"""Execute the registered CA-HMCD public-data replay protocol.

The runner consumes the frozen Stage-2 run registry. Each
workload-seed-algorithm tuple is checkpointed independently so a formal run can
resume without changing source assignments, stochastic seeds, or solver
budgets. Statistical inference is intentionally deferred; this module closes
execution, provenance, fairness auditing, and smoke-test reporting.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ca_hmcd_replay import ReplayEpisode, read_replay_episode
from ca_hmcd_simulation import (
    ALGORITHMS,
    EVALUATOR_VERSION,
    EXTERNAL_BASELINES,
    MODEL_VERSION,
    SERVICE_ENDPOINT_VERSION,
    attach_step_runtime_statistics,
    run_episode,
    summarise,
    write_csv,
)


RUNNER_VERSION = "registered-replay-runner-v1"
SHARD_SCHEMA_VERSION = "registered-replay-shard-v2"
FORMAL_CONTROL_ALGORITHMS = ("No-Pruning",)
FORMAL_EXTERNAL_ALGORITHMS = EXTERNAL_BASELINES
FORMAL_REFERENCE_ALGORITHMS = ("CA-HMCD",)
REPLAY_ALGORITHMS = (
    *ALGORITHMS,
    *FORMAL_CONTROL_ALGORITHMS,
    *FORMAL_EXTERNAL_ALGORITHMS,
)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _algorithm_slug(algorithm: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "-"
        for character in algorithm
    ).strip("-")


def _as_int(row: Mapping[str, object], key: str) -> int:
    return int(float(str(row[key])))


def _run_identity(row: Mapping[str, object], algorithm: str) -> Dict[str, object]:
    return {
        "workload_id": str(row["workload_id"]),
        "dataset": str(row["dataset"]),
        "replicate": _as_int(row, "replicate"),
        "seed": _as_int(row, "seed"),
        "algorithm": algorithm,
        "scenario": str(row["scenario"]),
        "task_load": _as_int(row, "task_load"),
        "horizon_steps": _as_int(row, "horizon_steps"),
        "resources_count": _as_int(row, "resources_count"),
        "top_k": _as_int(row, "top_k"),
        "node_limit": _as_int(row, "node_limit"),
        "independent_cluster": str(row["independent_cluster"]),
        "replay_path": str(row["replay_path"]),
    }


def _code_hashes() -> Dict[str, str]:
    root = Path(__file__).resolve().parent
    return {
        name: _sha256_file(root / name)
        for name in (
            "ca_hmcd_replay_runner.py",
            "ca_hmcd_replay.py",
            "ca_hmcd_simulation.py",
        )
    }


def _execution_fingerprint(
    row: Mapping[str, object],
    algorithm: str,
    replay_sha256: str,
    protocol_fingerprint: str,
    code_fingerprint: str,
) -> str:
    return _canonical_hash(
        {
            "identity": _run_identity(row, algorithm),
            "replay_sha256": replay_sha256,
            "protocol_fingerprint": protocol_fingerprint,
            "code_fingerprint": code_fingerprint,
            "runner_version": RUNNER_VERSION,
            "model_version": MODEL_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "service_endpoint_version": SERVICE_ENDPOINT_VERSION,
        }
    )


def _shard_path(
    output_dir: Path,
    row: Mapping[str, object],
    algorithm: str,
) -> Path:
    return (
        output_dir
        / "shards"
        / str(row["workload_id"])
        / f"seed-{_as_int(row, 'seed')}"
        / f"{_algorithm_slug(algorithm)}.json.gz"
    )


def _write_gzip_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    os.replace(temporary, path)


def _read_gzip_json(path: Path) -> Dict[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid replay shard payload: {path}")
    return payload


def _load_protocol_config(protocol_dir: Path) -> Dict[str, object]:
    path = protocol_dir / "protocol_config.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing protocol configuration: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Protocol configuration must be a JSON object")
    return payload


def load_registered_runs(protocol_dir: Path) -> List[Dict[str, str]]:
    path = protocol_dir / "experiment_run_registry.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing experiment run registry: {path}")
    rows = _read_csv(path)
    if not rows:
        raise ValueError("Experiment run registry is empty")
    required = {
        "workload_id",
        "dataset",
        "replicate",
        "seed",
        "paired_across_algorithms",
        "independent_cluster",
        "scenario",
        "task_load",
        "horizon_steps",
        "resources_count",
        "top_k",
        "node_limit",
        "replay_path",
    }
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"Run registry is missing columns: {sorted(missing)}")
    identities = [
        (row["workload_id"], _as_int(row, "seed"))
        for row in rows
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("Run registry contains duplicate workload-seed rows")
    if any(_as_int(row, "paired_across_algorithms") != 1 for row in rows):
        raise ValueError("Every registered run must be paired across algorithms")
    return rows


def _select_trial_workloads(
    rows: Sequence[Dict[str, str]],
) -> Tuple[str, ...]:
    selected: Dict[Tuple[str, int], str] = {}
    for row in sorted(rows, key=lambda item: str(item["workload_id"])):
        key = (str(row["dataset"]), _as_int(row, "task_load"))
        selected.setdefault(key, str(row["workload_id"]))
    return tuple(selected[key] for key in sorted(selected))


def select_registered_runs(
    rows: Sequence[Dict[str, str]],
    mode: str,
    workload_ids: Sequence[str] = (),
    max_replicates: Optional[int] = None,
) -> Tuple[List[Dict[str, str]], Tuple[str, ...]]:
    available = {str(row["workload_id"]) for row in rows}
    if workload_ids:
        unknown = sorted(set(workload_ids) - available)
        if unknown:
            raise ValueError(f"Unknown registered workloads: {unknown}")
        selected_workloads = tuple(dict.fromkeys(workload_ids))
    elif mode == "trial":
        selected_workloads = _select_trial_workloads(rows)
    else:
        selected_workloads = tuple(sorted(available))

    selected = [
        row for row in rows if str(row["workload_id"]) in selected_workloads
    ]
    if max_replicates is not None:
        if max_replicates < 1:
            raise ValueError("max_replicates must be positive")
        selected = [
            row for row in selected
            if _as_int(row, "replicate") <= max_replicates
        ]
    if not selected:
        raise ValueError("No registered runs remain after selection")
    return (
        sorted(
            selected,
            key=lambda row: (
                str(row["workload_id"]),
                _as_int(row, "replicate"),
            ),
        ),
        selected_workloads,
    )


def validate_registered_runs(
    protocol_dir: Path,
    all_rows: Sequence[Dict[str, str]],
    selected_rows: Sequence[Dict[str, str]],
    algorithms: Sequence[str],
    mode: str,
    protocol_config: Mapping[str, object],
) -> List[Dict[str, object]]:
    unknown_algorithms = sorted(set(algorithms) - set(REPLAY_ALGORITHMS))
    if unknown_algorithms:
        raise ValueError(f"Unsupported algorithms: {unknown_algorithms}")
    if not algorithms or len(algorithms) != len(set(algorithms)):
        raise ValueError("Algorithm list must be non-empty and unique")
    if mode == "formal":
        if set(algorithms) != set(ALGORITHMS):
            raise ValueError(
                "Formal mode requires the complete prespecified algorithm set"
            )
    elif mode == "formal-control":
        if tuple(algorithms) != FORMAL_CONTROL_ALGORITHMS:
            raise ValueError(
                "Formal-control mode requires the registered No-Pruning control"
            )
    elif mode == "formal-external":
        if tuple(algorithms) != FORMAL_EXTERNAL_ALGORITHMS:
            raise ValueError(
                "Formal-external mode requires the complete registered "
                "optimization and evolutionary baseline set"
            )
    elif mode == "formal-reference":
        if tuple(algorithms) != FORMAL_REFERENCE_ALGORITHMS:
            raise ValueError(
                "Formal-reference mode requires the registered CA-HMCD "
                "environment-matched reference"
            )
    if mode in {
        "formal",
        "formal-control",
        "formal-external",
        "formal-reference",
    }:
        if len(selected_rows) != len(all_rows):
            raise ValueError(
                f"{mode} mode must execute the complete frozen registry"
            )
        counts = Counter(str(row["workload_id"]) for row in selected_rows)
        if set(counts.values()) != {int(protocol_config["seeds_per_workload"])}:
            raise ValueError(
                f"{mode} workloads do not have the frozen seed count"
            )

    validation_rows: List[Dict[str, object]] = []
    replay_cache: Dict[Path, ReplayEpisode] = {}
    for workload_id in sorted({str(row["workload_id"]) for row in selected_rows}):
        workload_rows = [
            row for row in selected_rows if row["workload_id"] == workload_id
        ]
        exemplar = workload_rows[0]
        configuration = {
            (
                _as_int(row, "horizon_steps"),
                _as_int(row, "resources_count"),
                _as_int(row, "top_k"),
                _as_int(row, "node_limit"),
                str(row["dataset"]),
                str(row["scenario"]),
                _as_int(row, "task_load"),
                str(row["replay_path"]),
            )
            for row in workload_rows
        }
        if len(configuration) != 1:
            raise ValueError(f"Configuration drift within workload {workload_id}")
        replay_path = protocol_dir / str(exemplar["replay_path"])
        if not replay_path.is_file():
            raise FileNotFoundError(f"Missing registered replay: {replay_path}")
        episode = read_replay_episode(replay_path)
        replay_cache[replay_path] = episode
        checks = {
            "dataset": episode.dataset == str(exemplar["dataset"]),
            "sequence_id": episode.sequence_id == workload_id,
            "horizon_steps": episode.step_count
            == _as_int(exemplar, "horizon_steps"),
            "task_load": episode.task_count == _as_int(exemplar, "task_load"),
            "unique_seeds": len(
                {_as_int(row, "seed") for row in workload_rows}
            )
            == len(workload_rows),
        }
        validation_rows.append(
            {
                "workload_id": workload_id,
                "dataset": exemplar["dataset"],
                "selected_replicates": len(workload_rows),
                "replay_sha256": _sha256_file(replay_path),
                **{f"{key}_pass": int(value) for key, value in checks.items()},
                "passed": int(all(checks.values())),
            }
        )
    if not all(row["passed"] for row in validation_rows):
        failed = [
            str(row["workload_id"])
            for row in validation_rows
            if not row["passed"]
        ]
        raise RuntimeError(f"Registered replay validation failed: {failed}")
    return validation_rows


def _load_or_execute(
    protocol_dir: Path,
    output_dir: Path,
    row: Mapping[str, object],
    algorithm: str,
    replay_cache: Dict[Path, ReplayEpisode],
    replay_hash_cache: Dict[Path, str],
    protocol_fingerprint: str,
    code_fingerprint: str,
    resume: bool,
) -> Tuple[Dict[str, object], str]:
    shard_path = _shard_path(output_dir, row, algorithm)
    replay_path = protocol_dir / str(row["replay_path"])
    replay_sha256 = replay_hash_cache.get(replay_path)
    if replay_sha256 is None:
        replay_sha256 = _sha256_file(replay_path)
        replay_hash_cache[replay_path] = replay_sha256
    fingerprint = _execution_fingerprint(
        row,
        algorithm,
        replay_sha256,
        protocol_fingerprint,
        code_fingerprint,
    )
    if shard_path.is_file() and resume:
        payload = _read_gzip_json(shard_path)
        if payload.get("schema_version") != SHARD_SCHEMA_VERSION:
            raise ValueError(f"Unsupported shard schema: {shard_path}")
        if payload.get("execution_fingerprint") != fingerprint:
            raise ValueError(
                "Checkpoint does not match the current execution contract: "
                f"{shard_path}"
            )
        return payload, "resumed"

    episode = replay_cache.get(replay_path)
    if episode is None:
        episode = read_replay_episode(replay_path)
        replay_cache[replay_path] = episode
    started = time.perf_counter()
    identity = _run_identity(row, algorithm)
    metrics, step_rows = run_episode(
        algorithm,
        int(identity["seed"]),
        int(identity["horizon_steps"]),
        int(identity["resources_count"]),
        int(identity["task_load"]),
        str(identity["scenario"]),
        int(identity["top_k"]),
        node_limit_override=int(identity["node_limit"]),
        replay_episode=episode,
    )
    elapsed_seconds = time.perf_counter() - started
    common = {
        **identity,
        "runner_version": RUNNER_VERSION,
        "model_version": MODEL_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "service_endpoint_version": SERVICE_ENDPOINT_VERSION,
        "replay_sha256": replay_sha256,
        "protocol_fingerprint": protocol_fingerprint,
        "code_fingerprint": code_fingerprint,
    }
    episode_record = {
        **common,
        "execution_seconds": elapsed_seconds,
        **metrics,
    }
    enriched_steps = [{**common, **step_row} for step_row in step_rows]
    payload = {
        "schema_version": SHARD_SCHEMA_VERSION,
        "execution_fingerprint": fingerprint,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "episode": episode_record,
        "per_step": enriched_steps,
    }
    _write_gzip_json_atomic(shard_path, payload)
    return payload, "executed"


def audit_registered_execution(
    selected_rows: Sequence[Mapping[str, object]],
    episode_records: Sequence[Mapping[str, object]],
    step_records: Sequence[Mapping[str, object]],
    algorithms: Sequence[str],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    expected_algorithms = set(algorithms)
    expected_units = {
        (str(row["workload_id"]), _as_int(row, "seed"))
        for row in selected_rows
    }
    episode_index = Counter(
        (str(row["workload_id"]), int(row["seed"]), str(row["algorithm"]))
        for row in episode_records
    )
    step_groups: Dict[
        Tuple[str, int, int], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in step_records:
        step_groups[
            (
                str(row["workload_id"]),
                int(row["seed"]),
                int(row["step"]),
            )
        ].append(row)

    paired_rows: List[Dict[str, object]] = []
    for key in sorted(step_groups):
        workload_id, seed, step = key
        values = step_groups[key]
        observed_algorithms = {str(row["algorithm"]) for row in values}
        fingerprints = {str(row["state_fingerprint"]) for row in values}
        task_ids = {str(row["active_task_ids_json"]) for row in values}
        active_loads = {float(row["active_tasks"]) for row in values}
        model_versions = {str(row["model_version"]) for row in values}
        evaluator_versions = {str(row["evaluator_version"]) for row in values}
        endpoint_versions = {
            str(row["service_endpoint_version"]) for row in values
        }
        replay_hashes = {str(row["replay_sha256"]) for row in values}
        passed = (
            observed_algorithms == expected_algorithms
            and len(values) == len(algorithms)
            and len(fingerprints) == 1
            and len(task_ids) == 1
            and len(active_loads) == 1
            and model_versions == {MODEL_VERSION}
            and evaluator_versions == {EVALUATOR_VERSION}
            and endpoint_versions == {SERVICE_ENDPOINT_VERSION}
            and len(replay_hashes) == 1
        )
        paired_rows.append(
            {
                "workload_id": workload_id,
                "seed": seed,
                "step": step,
                "observed_algorithm_count": len(observed_algorithms),
                "missing_algorithms": ";".join(
                    sorted(expected_algorithms - observed_algorithms)
                ),
                "same_true_state": int(len(fingerprints) == 1),
                "same_active_task_ids": int(len(task_ids) == 1),
                "same_active_task_load": int(len(active_loads) == 1),
                "same_model_version": int(model_versions == {MODEL_VERSION}),
                "same_evaluator_version": int(
                    evaluator_versions == {EVALUATOR_VERSION}
                ),
                "same_service_endpoint_version": int(
                    endpoint_versions == {SERVICE_ENDPOINT_VERSION}
                ),
                "same_replay_hash": int(len(replay_hashes) == 1),
                "passed": int(passed),
            }
        )

    expected_episode_count = len(selected_rows) * len(algorithms)
    expected_step_count = sum(
        _as_int(row, "horizon_steps") for row in selected_rows
    ) * len(algorithms)
    expected_paired_groups = sum(
        _as_int(row, "horizon_steps") for row in selected_rows
    )
    summary_rows = [
        {
            "check": "registered run units are unique",
            "observed": len(expected_units),
            "expected": len(selected_rows),
            "passed": int(len(expected_units) == len(selected_rows)),
        },
        {
            "check": "episode result count is complete",
            "observed": len(episode_records),
            "expected": expected_episode_count,
            "passed": int(len(episode_records) == expected_episode_count),
        },
        {
            "check": "every episode tuple occurs once",
            "observed": max(episode_index.values(), default=0),
            "expected": 1,
            "passed": int(
                len(episode_index) == expected_episode_count
                and set(episode_index.values()) == {1}
            ),
        },
        {
            "check": "per-step result count is complete",
            "observed": len(step_records),
            "expected": expected_step_count,
            "passed": int(len(step_records) == expected_step_count),
        },
        {
            "check": "paired audit group count is complete",
            "observed": len(paired_rows),
            "expected": expected_paired_groups,
            "passed": int(len(paired_rows) == expected_paired_groups),
        },
        {
            "check": "all paired state audits pass",
            "observed": sum(int(row["passed"]) for row in paired_rows),
            "expected": len(paired_rows),
            "passed": int(all(row["passed"] for row in paired_rows)),
        },
        {
            "check": "all final allocations are feasible",
            "observed": min(
                (float(row["feasibility_rate"]) for row in episode_records),
                default=0.0,
            ),
            "expected": 1.0,
            "passed": int(
                all(
                    abs(float(row["feasibility_rate"]) - 1.0) <= 1e-12
                    for row in episode_records
                )
            ),
        },
    ]
    return paired_rows, summary_rows


def _coverage_rows(
    selected_rows: Sequence[Mapping[str, object]],
    episode_records: Sequence[Mapping[str, object]],
    algorithms: Sequence[str],
) -> List[Dict[str, object]]:
    completed = Counter(
        (str(row["workload_id"]), str(row["algorithm"]))
        for row in episode_records
    )
    selected_by_workload: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in selected_rows:
        selected_by_workload[str(row["workload_id"])].append(row)
    output = []
    for workload_id, rows in sorted(selected_by_workload.items()):
        expected_replicates = len(rows)
        algorithm_counts = {
            algorithm: completed[(workload_id, algorithm)]
            for algorithm in algorithms
        }
        output.append(
            {
                "workload_id": workload_id,
                "dataset": rows[0]["dataset"],
                "scenario": rows[0]["scenario"],
                "task_load": _as_int(rows[0], "task_load"),
                "expected_replicates": expected_replicates,
                "algorithms": ";".join(algorithms),
                "minimum_completed_replicates": min(algorithm_counts.values()),
                "maximum_completed_replicates": max(algorithm_counts.values()),
                "complete": int(
                    set(algorithm_counts.values()) == {expected_replicates}
                ),
            }
        )
    return output


def _build_result_report(
    output_dir: Path,
    protocol_dir: Path,
    mode: str,
    algorithms: Sequence[str],
    selected_rows: Sequence[Mapping[str, object]],
    run_status: Sequence[Mapping[str, object]],
    episode_records: Sequence[Mapping[str, object]],
    audit_summary: Sequence[Mapping[str, object]],
    duration_seconds: float,
) -> str:
    workload_count = len({str(row["workload_id"]) for row in selected_rows})
    executed = sum(row["status"] == "executed" for row in run_status)
    resumed = sum(row["status"] == "resumed" for row in run_status)
    fallback_runs = sum(
        float(row["fallback"]) > 0.0 for row in episode_records
    )
    node_limit_runs = sum(
        float(row["node_limit_hit"]) > 0.0 for row in episode_records
    )
    repair_runs = sum(
        float(row["repair_applied"]) > 0.0 for row in episode_records
    )
    passed = all(bool(row["passed"]) for row in audit_summary)
    qualification = (
        "This was a smoke test; estimates are diagnostic and are not formal "
        "inferential evidence."
        if mode == "trial"
        else "The complete frozen run registry was executed. Formal statistical "
        "inference remains a separate analysis stage."
    )
    return "\n".join(
        [
            "## Material Passport",
            "",
            "- Origin Skill: experiment-agent",
            "- Origin Mode: run",
            "- Origin Date: 2026-09-16",
            f"- Verification Status: {'VERIFIED' if passed else 'UNVERIFIED'}",
            "- Version Label: registered_replay_execution_v1",
            "",
            "## Experiment Result",
            "",
            "- **ID**: ca-hmcd-public-replay-stage3",
            "- **Type**: simulation",
            f"- **Status**: {'completed' if passed else 'audit_failed'}",
            f"- **Command**: `{' '.join(sys.argv)}`",
            f"- **Mode**: {mode}",
            f"- **Protocol directory**: `{protocol_dir}`",
            f"- **Output directory**: `{output_dir}`",
            f"- **Duration**: {duration_seconds:.3f} seconds",
            f"- **Workloads**: {workload_count}",
            f"- **Registered seed units**: {len(selected_rows)}",
            f"- **Algorithms**: {', '.join(algorithms)}",
            f"- **Algorithm runs executed**: {executed}",
            f"- **Algorithm runs resumed**: {resumed}",
            f"- **Runs with node-budget fallback**: {fallback_runs}",
            f"- **Runs with node-limit hits**: {node_limit_runs}",
            f"- **Runs requiring safety repair**: {repair_runs}",
            "",
            "### Output Summary",
            "",
            f"- Execution audit: {'PASS' if passed else 'FAIL'}.",
            f"- {qualification}",
            "- Public trajectories instantiate task-side motion or observation "
            "patterns; response resources and outcomes remain simulated.",
            "",
            "### Output Files",
            "",
            "- `registered_episode_results.csv`",
            "- `registered_per_step.csv`",
            "- `registered_summary.csv`",
            "- `registered_workload_summary.csv`",
            "- `registered_pairing_audit.csv`",
            "- `registered_execution_audit.csv`",
            "- `registered_workload_coverage.csv`",
            "- `registered_run_status.csv`",
            "- `execution_metadata.json`",
            "",
            "### Anomalies Detected",
            "",
            (
                f"- Node-budget fallback occurred in {fallback_runs} of "
                f"{len(episode_records)} algorithm runs. This is retained as "
                "a computational-boundary diagnostic."
                if fallback_runs
                else "- No node-budget fallback occurred."
            ),
            (
                f"- Safety repair was applied in {repair_runs} algorithm runs."
                if repair_runs
                else "- No true-state safety repair was required."
            ),
            (
                "- No protocol-integrity anomaly was detected."
                if passed
                else "- One or more execution audit checks failed."
            ),
            "",
        ]
    )


def run_registered_experiments(
    protocol_dir: Path,
    output_dir: Path,
    mode: str = "trial",
    algorithms: Sequence[str] = ALGORITHMS,
    workload_ids: Sequence[str] = (),
    max_replicates: Optional[int] = None,
    resume: bool = True,
) -> Dict[str, object]:
    if mode not in {
        "trial",
        "formal",
        "formal-control",
        "formal-external",
        "formal-reference",
    }:
        raise ValueError(
            "mode must be trial, formal, formal-control, formal-external, "
            "or formal-reference"
        )
    all_rows = load_registered_runs(protocol_dir)
    protocol_config = _load_protocol_config(protocol_dir)
    if mode == "trial" and max_replicates is None:
        max_replicates = 2
    selected_rows, selected_workloads = select_registered_runs(
        all_rows,
        mode,
        workload_ids,
        max_replicates,
    )
    validation_rows = validate_registered_runs(
        protocol_dir,
        all_rows,
        selected_rows,
        algorithms,
        mode,
        protocol_config,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "registered_input_validation.csv", validation_rows)

    started = time.perf_counter()
    replay_cache: Dict[Path, ReplayEpisode] = {}
    replay_hash_cache: Dict[Path, str] = {}
    code_hashes = _code_hashes()
    code_fingerprint = _canonical_hash(code_hashes)
    protocol_config_sha256 = _sha256_file(
        protocol_dir / "protocol_config.json"
    )
    run_registry_sha256 = _sha256_file(
        protocol_dir / "experiment_run_registry.csv"
    )
    protocol_fingerprint = _canonical_hash(
        {
            "protocol_config_sha256": protocol_config_sha256,
            "run_registry_sha256": run_registry_sha256,
        }
    )
    payloads: List[Dict[str, object]] = []
    run_status: List[Dict[str, object]] = []
    total = len(selected_rows) * len(algorithms)
    completed = 0
    for row in selected_rows:
        for algorithm in algorithms:
            run_started = time.perf_counter()
            try:
                payload, status = _load_or_execute(
                    protocol_dir,
                    output_dir,
                    row,
                    algorithm,
                    replay_cache,
                    replay_hash_cache,
                    protocol_fingerprint,
                    code_fingerprint,
                    resume,
                )
            except Exception as exc:
                failure = {
                    **_run_identity(row, algorithm),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
                write_csv(output_dir / "registered_failure.csv", [failure])
                raise
            payloads.append(payload)
            completed += 1
            run_status.append(
                {
                    **_run_identity(row, algorithm),
                    "status": status,
                    "wall_seconds": time.perf_counter() - run_started,
                    "shard_path": str(
                        _shard_path(output_dir, row, algorithm).relative_to(
                            output_dir
                        )
                    ).replace("\\", "/"),
                }
            )
            print(
                f"[registered-replay] {completed}/{total} "
                f"{row['workload_id']} seed={row['seed']} {algorithm} "
                f"{status}",
                flush=True,
            )

    episode_records = [
        dict(payload["episode"]) for payload in payloads
    ]
    step_records = [
        dict(row)
        for payload in payloads
        for row in payload["per_step"]
    ]
    paired_audit, audit_summary = audit_registered_execution(
        selected_rows,
        episode_records,
        step_records,
        algorithms,
    )
    coverage = _coverage_rows(selected_rows, episode_records, algorithms)
    summary = summarise(
        episode_records,
        ("dataset", "task_load", "algorithm"),
    )
    attach_step_runtime_statistics(
        summary,
        step_records,
        ("dataset", "task_load", "algorithm"),
    )
    workload_summary = summarise(
        episode_records,
        ("workload_id", "dataset", "task_load", "algorithm"),
    )
    attach_step_runtime_statistics(
        workload_summary,
        step_records,
        ("workload_id", "dataset", "task_load", "algorithm"),
    )
    duration_seconds = time.perf_counter() - started

    write_csv(output_dir / "registered_episode_results.csv", episode_records)
    write_csv(output_dir / "registered_per_step.csv", step_records)
    write_csv(output_dir / "registered_summary.csv", summary)
    write_csv(
        output_dir / "registered_workload_summary.csv",
        workload_summary,
    )
    write_csv(output_dir / "registered_pairing_audit.csv", paired_audit)
    write_csv(output_dir / "registered_execution_audit.csv", audit_summary)
    write_csv(output_dir / "registered_workload_coverage.csv", coverage)
    write_csv(output_dir / "registered_run_status.csv", run_status)
    metadata = {
        "runner_version": RUNNER_VERSION,
        "shard_schema_version": SHARD_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "protocol_dir": str(protocol_dir.resolve()),
        "protocol_config_sha256": protocol_config_sha256,
        "run_registry_sha256": run_registry_sha256,
        "protocol_fingerprint": protocol_fingerprint,
        "code_hashes": code_hashes,
        "code_fingerprint": code_fingerprint,
        "algorithms": list(algorithms),
        "selected_workloads": list(selected_workloads),
        "selected_registered_units": len(selected_rows),
        "expected_algorithm_runs": total,
        "duration_seconds": duration_seconds,
        "resume_enabled": resume,
        "model_version": MODEL_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "service_endpoint_version": SERVICE_ENDPOINT_VERSION,
        "python_version": sys.version,
        "platform": platform.platform(),
        "diagnostics": {
            "algorithm_runs_with_fallback": sum(
                float(row["fallback"]) > 0.0 for row in episode_records
            ),
            "algorithm_runs_with_node_limit_hit": sum(
                float(row["node_limit_hit"]) > 0.0
                for row in episode_records
            ),
            "algorithm_runs_with_safety_repair": sum(
                float(row["repair_applied"]) > 0.0
                for row in episode_records
            ),
        },
        "audit_pass": all(bool(row["passed"]) for row in audit_summary),
    }
    (output_dir / "execution_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = _build_result_report(
        output_dir,
        protocol_dir,
        mode,
        algorithms,
        selected_rows,
        run_status,
        episode_records,
        audit_summary,
        duration_seconds,
    )
    (output_dir / "experiment_result.md").write_text(
        report,
        encoding="utf-8",
    )
    if not metadata["audit_pass"]:
        raise RuntimeError("Registered replay execution audit failed")
    return {
        "episode_records": episode_records,
        "step_records": step_records,
        "summary": summary,
        "workload_summary": workload_summary,
        "pairing_audit": paired_audit,
        "audit_summary": audit_summary,
        "coverage": coverage,
        "run_status": run_status,
        "metadata": metadata,
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute the frozen CA-HMCD public replay registry"
    )
    parser.add_argument("--protocol-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=(
            "trial",
            "formal",
            "formal-control",
            "formal-external",
            "formal-reference",
        ),
        default="trial",
    )
    parser.add_argument(
        "--algorithms",
        nargs="+",
        default=list(ALGORITHMS),
        choices=REPLAY_ALGORITHMS,
    )
    parser.add_argument("--workload-id", action="append", default=[])
    parser.add_argument("--max-replicates", type=int)
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Recompute selected shards instead of using matching checkpoints",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    run_registered_experiments(
        args.protocol_dir,
        args.output_dir,
        mode=args.mode,
        algorithms=tuple(args.algorithms),
        workload_ids=tuple(args.workload_id),
        max_replicates=args.max_replicates,
        resume=not args.no_resume,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

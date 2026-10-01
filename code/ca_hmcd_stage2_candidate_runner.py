"""Run the pre-registered Stage 2 candidate-compression replay experiment."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import ca_hmcd_simulation as core
from ca_hmcd_framework import (
    FRAMEWORK_VERSION,
    CandidatePolicy,
    CandidatePool,
    retain_candidates,
)
from ca_hmcd_replay import ReplayEpisode, read_replay_episode


RUNNER_VERSION = "ca-hmcd-stage2-candidate-runner-v1"
SHARD_SCHEMA_VERSION = "ca-hmcd-stage2-candidate-shard-v1"
NEW_PROFILES = ("FixedK-6", "FixedK-24", "FixedK-48", "AdaptiveK")
FIXED_K = {
    "FixedK-6": 6,
    "FixedK-24": 24,
    "FixedK-48": 48,
}
_EPISODE_CACHE: Dict[str, ReplayEpisode] = {}


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def algorithm_slug(value: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "-"
        for character in value
    ).strip("-")


def as_int(row: Mapping[str, object], key: str) -> int:
    return int(float(str(row[key])))


def identity(row: Mapping[str, object], profile: str) -> Dict[str, object]:
    registered_k = FIXED_K.get(profile)
    return {
        "workload_id": str(row["workload_id"]),
        "dataset": str(row["dataset"]),
        "replicate": as_int(row, "replicate"),
        "seed": as_int(row, "seed"),
        "algorithm": profile,
        "candidate_policy": (
            "adaptive_k" if profile == "AdaptiveK" else "fixed_k"
        ),
        "registered_k": registered_k if registered_k is not None else "",
        "scenario": str(row["scenario"]),
        "task_load": as_int(row, "task_load"),
        "horizon_steps": as_int(row, "horizon_steps"),
        "resources_count": as_int(row, "resources_count"),
        "top_k": registered_k if registered_k is not None else 0,
        "node_limit": as_int(row, "node_limit"),
        "independent_cluster": str(row["independent_cluster"]),
        "replay_path": str(row["replay_path"]),
    }


def shard_path(
    output_dir: Path, row: Mapping[str, object], profile: str
) -> Path:
    return (
        output_dir
        / "shards"
        / str(row["workload_id"])
        / f"seed-{as_int(row, 'seed')}"
        / f"{algorithm_slug(profile)}.json.gz"
    )


def write_gzip_json_atomic(
    path: Path, payload: Mapping[str, object]
) -> None:
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


def read_gzip_json(path: Path) -> Dict[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid shard payload: {path}")
    return payload


def load_episode(path: Path) -> ReplayEpisode:
    key = str(path.resolve())
    episode = _EPISODE_CACHE.get(key)
    if episode is None:
        episode = read_replay_episode(path)
        _EPISODE_CACHE[key] = episode
    return episode


def copy_audit(
    source: core.CandidateGenerationAudit,
    target: core.CandidateGenerationAudit,
) -> None:
    for descriptor in fields(core.CandidateGenerationAudit):
        setattr(target, descriptor.name, getattr(source, descriptor.name))


def run_adaptive_episode(
    seed: int,
    steps: int,
    resources_count: int,
    tasks_count: int,
    scenario: str,
    node_limit: int,
    replay_episode: ReplayEpisode,
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    original_generate = core.generate_candidates
    call_metadata: List[Dict[str, object]] = []
    policy = CandidatePolicy.adaptive_k()

    def adaptive_generate(
        task_index: int,
        resources: Sequence[core.Resource],
        tasks: Sequence[core.Task],
        top_k: int = 8,
        alpha: float = core.ALPHA_COST,
        beta: float = core.BETA_TIME,
        use_synergy: bool = True,
        use_redundancy: bool = True,
        use_adaptation: bool = True,
        synergy_scale: float = 1.0,
        prune: bool = True,
        previous_combo: Optional[Tuple[int, ...]] = None,
        switch_lambda: float = core.LAMBDA_SWITCH,
        redundancy_lambda: float = core.LAMBDA_REDUNDANCY,
        audit: Optional[core.CandidateGenerationAudit] = None,
        apply_compatibility_screen: Optional[bool] = None,
        apply_dominance: Optional[bool] = None,
        apply_diversity: Optional[bool] = None,
    ) -> List[core.Candidate]:
        if audit is None:
            return original_generate(
                task_index,
                resources,
                tasks,
                top_k,
                alpha,
                beta,
                use_synergy,
                use_redundancy,
                use_adaptation,
                synergy_scale,
                prune,
                previous_combo,
                switch_lambda,
                redundancy_lambda,
                audit,
                apply_compatibility_screen,
                apply_dominance,
                apply_diversity,
            )
        complete_audit = core.CandidateGenerationAudit(
            task_index=task_index
        )
        complete = original_generate(
            task_index,
            resources,
            tasks,
            top_k=1,
            alpha=alpha,
            beta=beta,
            use_synergy=use_synergy,
            use_redundancy=use_redundancy,
            use_adaptation=use_adaptation,
            synergy_scale=synergy_scale,
            prune=False,
            previous_combo=previous_combo,
            switch_lambda=switch_lambda,
            redundancy_lambda=redundancy_lambda,
            audit=complete_audit,
            apply_compatibility_screen=False,
            apply_dominance=False,
            apply_diversity=False,
        )
        complete_pool = CandidatePool(
            by_task={task_index: complete},
            audits={task_index: complete_audit},
            policy=CandidatePolicy.full(),
        )
        retained = retain_candidates(
            complete_pool,
            len(resources),
            policy,
            previous={task_index: previous_combo} if previous_combo else {},
            switch_lambda=switch_lambda,
            active_task_count=sum(task.active for task in tasks),
        )
        copy_audit(retained.audits[task_index], audit)
        call_metadata.append(
            {
                "task_index": task_index,
                "task_active": int(tasks[task_index].active),
                **retained.retention_metadata[task_index],
            }
        )
        return retained.by_task[task_index]

    core.generate_candidates = adaptive_generate
    try:
        metrics, rows = core.run_episode(
            "CA-HMCD",
            seed,
            steps,
            resources_count,
            tasks_count,
            scenario,
            top_k=12,
            node_limit_override=node_limit,
            replay_episode=replay_episode,
        )
    finally:
        core.generate_candidates = original_generate

    expected_calls = steps * tasks_count
    if len(call_metadata) != expected_calls:
        raise RuntimeError(
            f"Adaptive audit expected {expected_calls} calls, "
            f"observed {len(call_metadata)}"
        )
    adaptive_fields = (
        "adaptive_k_mean",
        "adaptive_k_min",
        "adaptive_k_max",
        "adaptive_initial_k_mean",
        "adaptive_extension_rounds",
        "adaptive_extended_tasks",
        "adaptive_boundary_gap_mean",
    )
    for step, row in enumerate(rows):
        chunk = call_metadata[
            step * tasks_count : (step + 1) * tasks_count
        ]
        active = [item for item in chunk if item["task_active"]]
        targets = [float(item["selected_target_k"]) for item in active]
        initial = [float(item["initial_target_k"]) for item in active]
        gaps = [
            float(item["boundary_relative_gap_at_stop"])
            for item in active
            if item["boundary_relative_gap_at_stop"] is not None
        ]
        row.update(
            {
                "algorithm": "AdaptiveK",
                "candidate_policy": "adaptive_k",
                "registered_k": "",
                "adaptive_k_mean": (
                    statistics.fmean(targets) if targets else 0.0
                ),
                "adaptive_k_min": min(targets) if targets else 0.0,
                "adaptive_k_max": max(targets) if targets else 0.0,
                "adaptive_initial_k_mean": (
                    statistics.fmean(initial) if initial else 0.0
                ),
                "adaptive_extension_rounds": sum(
                    int(item["extension_rounds"]) for item in active
                ),
                "adaptive_extended_tasks": sum(
                    int(item["extension_rounds"]) > 0 for item in active
                ),
                "adaptive_boundary_gap_mean": (
                    statistics.fmean(gaps) if gaps else 0.0
                ),
                "adaptive_task_metadata_json": json.dumps(
                    active,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    output_metrics: Dict[str, object] = dict(metrics)
    output_metrics["algorithm"] = "AdaptiveK"
    output_metrics["candidate_policy"] = "adaptive_k"
    output_metrics["registered_k"] = ""
    for key in adaptive_fields:
        output_metrics[key] = statistics.fmean(
            float(row[key]) for row in rows
        )
    return output_metrics, rows


def run_fixed_episode(
    profile: str,
    seed: int,
    steps: int,
    resources_count: int,
    tasks_count: int,
    scenario: str,
    node_limit: int,
    replay_episode: ReplayEpisode,
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    top_k = FIXED_K[profile]
    original_repair = core.safety_repair

    def registered_repair(
        selected: Dict[int, Tuple[int, ...]],
        resources: Sequence[core.Resource],
        tasks: Sequence[core.Task],
        previous: Dict[int, Tuple[int, ...]],
        repair_top_k: int = 12,
        *args: object,
        **kwargs: object,
    ) -> Tuple[Dict[int, Tuple[int, ...]], int]:
        return original_repair(
            selected,
            resources,
            tasks,
            previous,
            12,
            *args,
            **kwargs,
        )

    core.safety_repair = registered_repair
    try:
        metrics, rows = core.run_episode(
            "CA-HMCD",
            seed,
            steps,
            resources_count,
            tasks_count,
            scenario,
            top_k=top_k,
            node_limit_override=node_limit,
            replay_episode=replay_episode,
        )
    finally:
        core.safety_repair = original_repair
    for row in rows:
        task_count = float(row["candidate_tasks_audited"])
        retained = float(row["candidate_after_diversity"])
        row.update(
            {
                "algorithm": profile,
                "candidate_policy": "fixed_k",
                "registered_k": top_k,
                "adaptive_k_mean": (
                    retained / task_count if task_count else 0.0
                ),
                "adaptive_k_min": 0.0,
                "adaptive_k_max": float(top_k),
                "adaptive_initial_k_mean": float(top_k),
                "adaptive_extension_rounds": 0.0,
                "adaptive_extended_tasks": 0.0,
                "adaptive_boundary_gap_mean": 0.0,
                "adaptive_task_metadata_json": "[]",
            }
        )
    output_metrics: Dict[str, object] = dict(metrics)
    output_metrics.update(
        {
            "algorithm": profile,
            "candidate_policy": "fixed_k",
            "registered_k": top_k,
            "adaptive_k_mean": statistics.fmean(
                float(row["adaptive_k_mean"]) for row in rows
            ),
            "adaptive_k_min": 0.0,
            "adaptive_k_max": float(top_k),
            "adaptive_initial_k_mean": float(top_k),
            "adaptive_extension_rounds": 0.0,
            "adaptive_extended_tasks": 0.0,
            "adaptive_boundary_gap_mean": 0.0,
        }
    )
    return output_metrics, rows


def worker_execute(job: Mapping[str, object]) -> Dict[str, object]:
    row = dict(job["row"])
    profile = str(job["profile"])
    output_dir = Path(str(job["output_dir"]))
    protocol_dir = Path(str(job["protocol_dir"]))
    target = shard_path(output_dir, row, profile)
    run_id = identity(row, profile)
    fingerprint = canonical_hash(
        {
            "identity": run_id,
            "replay_sha256": job["replay_sha256"],
            "protocol_fingerprint": job["protocol_fingerprint"],
            "code_fingerprint": job["code_fingerprint"],
            "runner_version": RUNNER_VERSION,
            "framework_version": FRAMEWORK_VERSION,
            "model_version": core.MODEL_VERSION,
            "evaluator_version": core.EVALUATOR_VERSION,
            "service_endpoint_version": core.SERVICE_ENDPOINT_VERSION,
        }
    )
    if bool(job["resume"]) and target.is_file():
        payload = read_gzip_json(target)
        if (
            payload.get("schema_version") != SHARD_SCHEMA_VERSION
            or payload.get("execution_fingerprint") != fingerprint
        ):
            raise ValueError(f"Checkpoint contract mismatch: {target}")
        return {
            **run_id,
            "status": "resumed",
            "shard_path": str(target),
            "wall_seconds": 0.0,
        }

    replay_path = protocol_dir / str(row["replay_path"])
    episode = load_episode(replay_path)
    started = time.perf_counter()
    if profile == "AdaptiveK":
        metrics, step_rows = run_adaptive_episode(
            int(run_id["seed"]),
            int(run_id["horizon_steps"]),
            int(run_id["resources_count"]),
            int(run_id["task_load"]),
            str(run_id["scenario"]),
            int(run_id["node_limit"]),
            episode,
        )
    else:
        metrics, step_rows = run_fixed_episode(
            profile,
            int(run_id["seed"]),
            int(run_id["horizon_steps"]),
            int(run_id["resources_count"]),
            int(run_id["task_load"]),
            str(run_id["scenario"]),
            int(run_id["node_limit"]),
            episode,
        )
    wall_seconds = time.perf_counter() - started
    common = {
        **run_id,
        "runner_version": RUNNER_VERSION,
        "framework_version": FRAMEWORK_VERSION,
        "model_version": core.MODEL_VERSION,
        "evaluator_version": core.EVALUATOR_VERSION,
        "service_endpoint_version": core.SERVICE_ENDPOINT_VERSION,
        "replay_sha256": str(job["replay_sha256"]),
        "protocol_fingerprint": str(job["protocol_fingerprint"]),
        "code_fingerprint": str(job["code_fingerprint"]),
    }
    payload = {
        "schema_version": SHARD_SCHEMA_VERSION,
        "execution_fingerprint": fingerprint,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "episode": {**common, "execution_seconds": wall_seconds, **metrics},
        "per_step": [{**common, **step_row} for step_row in step_rows],
    }
    write_gzip_json_atomic(target, payload)
    return {
        **run_id,
        "status": "executed",
        "shard_path": str(target),
        "wall_seconds": wall_seconds,
    }


def select_rows(
    rows: Sequence[Dict[str, str]],
    mode: str,
    max_replicates: Optional[int],
) -> List[Dict[str, str]]:
    if mode == "formal":
        return list(rows)
    selected_workloads: Dict[Tuple[str, int], str] = {}
    for row in sorted(rows, key=lambda item: str(item["workload_id"])):
        key = (str(row["dataset"]), as_int(row, "task_load"))
        selected_workloads.setdefault(key, str(row["workload_id"]))
    limit = 2 if max_replicates is None else max_replicates
    return [
        row
        for row in rows
        if str(row["workload_id"]) in selected_workloads.values()
        and as_int(row, "replicate") <= limit
    ]


def validate_registry(
    protocol_dir: Path, rows: Sequence[Mapping[str, object]]
) -> List[Dict[str, object]]:
    identities = [
        (str(row["workload_id"]), as_int(row, "seed")) for row in rows
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("The selected registry contains duplicate units")
    output: List[Dict[str, object]] = []
    for workload_id in sorted({str(row["workload_id"]) for row in rows}):
        workload_rows = [
            row for row in rows if str(row["workload_id"]) == workload_id
        ]
        exemplar = workload_rows[0]
        replay_path = protocol_dir / str(exemplar["replay_path"])
        episode = read_replay_episode(replay_path)
        checks = {
            "dataset": episode.dataset == str(exemplar["dataset"]),
            "sequence": episode.sequence_id == workload_id,
            "horizon": episode.step_count
            == as_int(exemplar, "horizon_steps"),
            "task_load": episode.task_count
            == as_int(exemplar, "task_load"),
        }
        output.append(
            {
                "workload_id": workload_id,
                "dataset": exemplar["dataset"],
                "selected_replicates": len(workload_rows),
                "replay_sha256": sha256_file(replay_path),
                **{f"{key}_pass": int(value) for key, value in checks.items()},
                "passed": int(all(checks.values())),
            }
        )
    if not all(bool(row["passed"]) for row in output):
        raise RuntimeError("One or more replay inputs failed validation")
    return output


def audit_execution(
    selected_rows: Sequence[Mapping[str, object]],
    episodes: Sequence[Mapping[str, object]],
    steps: Sequence[Mapping[str, object]],
    profiles: Sequence[str],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    expected_episode_count = len(selected_rows) * len(profiles)
    expected_step_count = sum(
        as_int(row, "horizon_steps") for row in selected_rows
    ) * len(profiles)
    grouped: Dict[Tuple[str, int, int], List[Mapping[str, object]]] = {}
    for row in steps:
        key = (
            str(row["workload_id"]),
            int(row["seed"]),
            int(row["step"]),
        )
        grouped.setdefault(key, []).append(row)
    pairing_rows: List[Dict[str, object]] = []
    for (workload_id, seed, step), values in sorted(grouped.items()):
        algorithms = {str(row["algorithm"]) for row in values}
        state = {str(row["state_fingerprint"]) for row in values}
        active_ids = {str(row["active_task_ids_json"]) for row in values}
        passed = (
            algorithms == set(profiles)
            and len(values) == len(profiles)
            and len(state) == 1
            and len(active_ids) == 1
        )
        pairing_rows.append(
            {
                "workload_id": workload_id,
                "seed": seed,
                "step": step,
                "same_profiles": int(algorithms == set(profiles)),
                "same_true_state": int(len(state) == 1),
                "same_active_task_ids": int(len(active_ids) == 1),
                "passed": int(passed),
            }
        )
    summary = [
        {
            "check": "episode result count",
            "observed": len(episodes),
            "expected": expected_episode_count,
            "passed": int(len(episodes) == expected_episode_count),
        },
        {
            "check": "per-step result count",
            "observed": len(steps),
            "expected": expected_step_count,
            "passed": int(len(steps) == expected_step_count),
        },
        {
            "check": "paired state groups",
            "observed": sum(int(row["passed"]) for row in pairing_rows),
            "expected": len(pairing_rows),
            "passed": int(all(bool(row["passed"]) for row in pairing_rows)),
        },
        {
            "check": "final feasibility",
            "observed": min(
                float(row["feasibility_rate"]) for row in episodes
            ),
            "expected": 1.0,
            "passed": int(
                all(
                    abs(float(row["feasibility_rate"]) - 1.0) <= 1e-12
                    for row in episodes
                )
            ),
        },
    ]
    return pairing_rows, summary


def run_stage2(
    protocol_dir: Path,
    output_dir: Path,
    registration_dir: Path,
    mode: str,
    profiles: Sequence[str],
    max_replicates: Optional[int],
    max_workers: int,
    resume: bool,
) -> Dict[str, object]:
    if mode == "formal" and tuple(profiles) != NEW_PROFILES:
        raise ValueError(
            "Formal mode requires the complete pre-registered new profile set"
        )
    unknown = set(profiles) - set(NEW_PROFILES)
    if unknown or len(profiles) != len(set(profiles)):
        raise ValueError(f"Invalid profile list: {sorted(unknown)}")
    registry = read_csv(protocol_dir / "experiment_run_registry.csv")
    selected = select_rows(registry, mode, max_replicates)
    if mode == "formal" and len(selected) != 1320:
        raise RuntimeError(
            f"Formal registry requires 1320 units; observed {len(selected)}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    validation = validate_registry(protocol_dir, selected)
    core.write_csv(output_dir / "stage2_input_validation.csv", validation)

    code_paths = {
        "ca_hmcd_stage2_candidate_runner.py": Path(__file__).resolve(),
        "ca_hmcd_framework.py": Path(__file__).resolve().parent
        / "ca_hmcd_framework.py",
        "ca_hmcd_simulation.py": Path(__file__).resolve().parent
        / "ca_hmcd_simulation.py",
        "ca_hmcd_replay.py": Path(__file__).resolve().parent
        / "ca_hmcd_replay.py",
        "stage2_candidate_protocol.md": registration_dir
        / "stage2_candidate_protocol.md",
        "stage2_preregistration.json": registration_dir
        / "stage2_preregistration.json",
    }
    code_hashes = {
        name: sha256_file(path) for name, path in code_paths.items()
    }
    code_fingerprint = canonical_hash(code_hashes)
    protocol_fingerprint = canonical_hash(
        {
            "protocol_config_sha256": sha256_file(
                protocol_dir / "protocol_config.json"
            ),
            "run_registry_sha256": sha256_file(
                protocol_dir / "experiment_run_registry.csv"
            ),
        }
    )
    replay_hashes = {
        str(row["workload_id"]): sha256_file(
            protocol_dir / str(row["replay_path"])
        )
        for row in selected
    }
    jobs = [
        {
            "row": row,
            "profile": profile,
            "output_dir": str(output_dir),
            "protocol_dir": str(protocol_dir),
            "replay_sha256": replay_hashes[str(row["workload_id"])],
            "protocol_fingerprint": protocol_fingerprint,
            "code_fingerprint": code_fingerprint,
            "resume": resume,
        }
        for row in selected
        for profile in profiles
    ]
    started = time.perf_counter()
    statuses: List[Dict[str, object]] = []
    print(
        f"[stage2] mode={mode} units={len(selected)} profiles={len(profiles)} "
        f"runs={len(jobs)} workers={max_workers}",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker_execute, job) for job in jobs]
        for completed, future in enumerate(as_completed(futures), start=1):
            statuses.append(future.result())
            if completed == 1 or completed % 50 == 0 or completed == len(jobs):
                elapsed = time.perf_counter() - started
                print(
                    f"[stage2] {completed}/{len(jobs)} complete "
                    f"elapsed={elapsed:.1f}s",
                    flush=True,
                )

    payloads = [
        read_gzip_json(Path(str(status["shard_path"])))
        for status in statuses
    ]
    episodes = [dict(payload["episode"]) for payload in payloads]
    steps = [
        dict(row) for payload in payloads for row in payload["per_step"]
    ]
    pairing, audit = audit_execution(selected, episodes, steps, profiles)
    summary = core.summarise(
        episodes, ("dataset", "task_load", "algorithm")
    )
    core.attach_step_runtime_statistics(
        summary, steps, ("dataset", "task_load", "algorithm")
    )
    for row in summary:
        selected_steps = [
            value
            for value in steps
            if value["dataset"] == row["dataset"]
            and value["task_load"] == row["task_load"]
            and value["algorithm"] == row["algorithm"]
        ]
        for key in (
            "adaptive_k_mean",
            "adaptive_initial_k_mean",
            "adaptive_extension_rounds",
            "adaptive_extended_tasks",
            "adaptive_boundary_gap_mean",
        ):
            row[f"{key}_mean"] = statistics.fmean(
                float(value[key]) for value in selected_steps
            )
    duration = time.perf_counter() - started
    core.write_csv(output_dir / "stage2_episode_results.csv", episodes)
    core.write_csv(output_dir / "stage2_per_step.csv", steps)
    core.write_csv(output_dir / "stage2_summary.csv", summary)
    core.write_csv(output_dir / "stage2_pairing_audit.csv", pairing)
    core.write_csv(output_dir / "stage2_execution_audit.csv", audit)
    core.write_csv(output_dir / "stage2_run_status.csv", statuses)
    metadata = {
        "runner_version": RUNNER_VERSION,
        "shard_schema_version": SHARD_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "profiles": list(profiles),
        "selected_registered_units": len(selected),
        "expected_runs": len(jobs),
        "duration_seconds": duration,
        "max_workers": max_workers,
        "resume_enabled": resume,
        "protocol_dir": str(protocol_dir.resolve()),
        "registration_dir": str(registration_dir.resolve()),
        "protocol_fingerprint": protocol_fingerprint,
        "code_hashes": code_hashes,
        "code_fingerprint": code_fingerprint,
        "framework_version": FRAMEWORK_VERSION,
        "model_version": core.MODEL_VERSION,
        "evaluator_version": core.EVALUATOR_VERSION,
        "service_endpoint_version": core.SERVICE_ENDPOINT_VERSION,
        "python_version": sys.version,
        "platform": platform.platform(),
        "diagnostics": {
            "fallback_runs": sum(
                float(row["fallback"]) > 0.0 for row in episodes
            ),
            "node_limit_runs": sum(
                float(row["node_limit_hit"]) > 0.0 for row in episodes
            ),
            "repair_runs": sum(
                float(row["repair_applied"]) > 0.0 for row in episodes
            ),
        },
        "audit_pass": all(bool(row["passed"]) for row in audit),
    }
    (output_dir / "stage2_execution_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not metadata["audit_pass"]:
        raise RuntimeError("Stage 2 execution audit failed")
    return {
        "episodes": episodes,
        "steps": steps,
        "summary": summary,
        "pairing": pairing,
        "audit": audit,
        "metadata": metadata,
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Stage 2 candidate-compression closure"
    )
    parser.add_argument("--protocol-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--registration-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("smoke", "formal"), default="smoke")
    parser.add_argument(
        "--profiles", nargs="+", choices=NEW_PROFILES, default=list(NEW_PROFILES)
    )
    parser.add_argument("--max-replicates", type=int)
    parser.add_argument("--max-workers", type=int, default=6)
    parser.add_argument("--no-resume", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    run_stage2(
        args.protocol_dir,
        args.output_dir,
        args.registration_dir,
        args.mode,
        tuple(args.profiles),
        args.max_replicates,
        args.max_workers,
        not args.no_resume,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

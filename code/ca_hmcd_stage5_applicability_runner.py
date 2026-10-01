"""Run the registered Stage 5 external-baseline applicability experiment."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy
import scipy

import ca_hmcd_simulation as core


RUNNER_VERSION = "ca-hmcd-stage5-applicability-runner-v1"
PROJECT_DIR = Path(__file__).resolve().parent
PROTOCOL_DIR = PROJECT_DIR / "ca_hmcd_stage5_applicability_protocol_20260918"
PROTOCOL_PATH = PROTOCOL_DIR / "stage5_preregistration.json"
PROTOCOL_TEXT_PATH = PROTOCOL_DIR / "stage5_protocol.md"
FORMAL_ENV_PYTHON = (
    PROJECT_DIR / ".venv-baselines" / "Scripts" / "python.exe"
)
SEEDS = tuple(range(950000, 950030))
STEPS = 12
TOP_K = 12
NODE_LIMIT = 12000
DEADLINES_MS = (50, 100, 200, 500)
SCENARIOS = (
    "airport_corridor",
    "energy_facility",
    "public_event",
    "urban_corridor",
    "industrial_zone",
)
METHODS = (
    "CA-HMCD",
    "HiGHS-MILP",
    "Genetic-Algorithm",
    "Greedy",
    "External-Auction",
)
METHOD_LABELS = {
    "CA-HMCD": "CA-HMCD-FixedK",
    "HiGHS-MILP": "CA-HMCD-Exact",
    "Genetic-Algorithm": "Genetic-Algorithm",
    "Greedy": "Greedy",
    "External-Auction": "External-Auction",
}
EXPECTED_FROZEN_HASHES = {
    "ca_hmcd_simulation.py": (
        "b6fae78029a59e65c6a49fe25cd11e4d2fa9d5c912ced55ad9b8a4b92c126ef1"
    ),
    "ca_hmcd_framework.py": (
        "f292a96a54a93a5be71a5487ecc9e0e9702e123e95700f958d6ee3b363c5a8aa"
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def protocol_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in (PROTOCOL_PATH, PROTOCOL_TEXT_PATH):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def code_hashes() -> Dict[str, str]:
    paths = {
        "ca_hmcd_stage5_applicability_runner.py": Path(__file__).resolve(),
        "ca_hmcd_simulation.py": PROJECT_DIR / "ca_hmcd_simulation.py",
        "ca_hmcd_framework.py": PROJECT_DIR / "ca_hmcd_framework.py",
        "stage5_preregistration.json": PROTOCOL_PATH,
        "stage5_protocol.md": PROTOCOL_TEXT_PATH,
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def registered_units() -> List[Dict[str, object]]:
    units: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        parameters = core.scenario_parameters(scenario)
        for seed in SEEDS:
            for algorithm in METHODS:
                units.append(
                    {
                        "unit_id": f"{scenario}__S{seed}__{algorithm}",
                        "scenario": scenario,
                        "seed": seed,
                        "algorithm": algorithm,
                        "method_label": METHOD_LABELS[algorithm],
                        "resources_count": int(parameters["resources"]),
                        "tasks_count": int(parameters["tasks"]),
                    }
                )
    return units


def validate_registration(require_formal_environment: bool) -> Dict[str, object]:
    registration = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    observed_hashes = {
        name: sha256_file(PROJECT_DIR / name)
        for name in EXPECTED_FROZEN_HASHES
    }
    expected_executable = str(FORMAL_ENV_PYTHON.resolve()).lower()
    checks = {
        "seeds": tuple(registration["frozen_contract"]["seeds"]) == SEEDS,
        "steps": registration["frozen_contract"]["steps"] == STEPS,
        "top_k": (
            registration["frozen_contract"]["top_k_for_compressed_methods"]
            == TOP_K
        ),
        "node_limit": (
            registration["frozen_contract"]["node_or_evaluation_budget"]
            == NODE_LIMIT
        ),
        "deadlines": (
            tuple(registration["frozen_contract"]["decision_deadlines_ms"])
            == DEADLINES_MS
        ),
        "episodes": (
            len(registered_units())
            == registration["design"]["registered_episodes"]
            == 750
        ),
        "frozen_hashes": observed_hashes == EXPECTED_FROZEN_HASHES,
        "model_version": (
            core.MODEL_VERSION
            == registration["frozen_contract"]["model_version"]
        ),
        "evaluator_version": (
            core.EVALUATOR_VERSION
            == registration["frozen_contract"]["external_evaluator_version"]
        ),
        "service_endpoint": (
            core.SERVICE_ENDPOINT_VERSION
            == registration["frozen_contract"]["service_endpoint_version"]
        ),
        "python_3_12": sys.version_info[:2] == (3, 12),
        "formal_executable": (
            str(Path(sys.executable).resolve()).lower() == expected_executable
        ),
    }
    mandatory = dict(checks)
    if not require_formal_environment:
        mandatory.pop("python_3_12")
        mandatory.pop("formal_executable")
    if not all(mandatory.values()):
        raise RuntimeError(f"Stage 5 registration mismatch: {checks}")
    return {
        "registration": registration,
        "checks": checks,
        "frozen_hashes": observed_hashes,
        "protocol_fingerprint": protocol_fingerprint(),
    }


def shard_path(output_dir: Path, unit: Mapping[str, object]) -> Path:
    algorithm_slug = str(unit["algorithm"]).replace(" ", "-").lower()
    return (
        output_dir
        / "shards"
        / str(unit["scenario"])
        / f"seed-{unit['seed']}"
        / f"{algorithm_slug}.json.gz"
    )


def write_gzip_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    os.replace(temporary, path)


def read_gzip_json(path: Path) -> Dict[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid Stage 5 shard: {path}")
    return value


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def execute_unit(unit: Mapping[str, object]) -> Dict[str, object]:
    started = time.perf_counter()
    metrics, rows = core.run_episode(
        algorithm=str(unit["algorithm"]),
        seed=int(unit["seed"]),
        steps=STEPS,
        resources_count=int(unit["resources_count"]),
        tasks_count=int(unit["tasks_count"]),
        scenario=str(unit["scenario"]),
        top_k=TOP_K,
        noise_level=0.0,
        synergy_scale=1.0,
        enable_safety_repair=True,
        node_limit_override=NODE_LIMIT,
        candidate_pool_mode="independent",
        force_all_tasks_active=True,
    )
    labelled_rows = [
        {
            **row,
            "method_label": unit["method_label"],
            "registered_candidate_profile": (
                "complete"
                if unit["algorithm"]
                in {"HiGHS-MILP", "Genetic-Algorithm"}
                else "K12"
            ),
        }
        for row in rows
    ]
    end_to_end = [
        float(row["end_to_end_runtime_ms"]) for row in labelled_rows
    ]
    candidate = [
        float(row["candidate_generation_ms"]) for row in labelled_rows
    ]
    solver = [float(row["solver_elapsed_ms"]) for row in labelled_rows]
    timing = {
        "end_to_end_runtime_ms_median": quantile(end_to_end, 0.5),
        "end_to_end_runtime_ms_p95": quantile(end_to_end, 0.95),
        "end_to_end_runtime_ms_max": max(end_to_end),
        "candidate_generation_ms_p95": quantile(candidate, 0.95),
        "solver_elapsed_ms_p95": quantile(solver, 0.95),
    }
    for deadline in DEADLINES_MS:
        timing[f"deadline_hit_rate_{deadline}ms"] = statistics.fmean(
            1.0 if value <= deadline else 0.0 for value in end_to_end
        )
    return {
        "episode": {
            **dict(unit),
            **metrics,
            **timing,
            "execution_seconds": time.perf_counter() - started,
        },
        "rows": labelled_rows,
        "unit_runtime_s": time.perf_counter() - started,
    }


def valid_existing_shard(
    path: Path,
    unit: Mapping[str, object],
    hashes: Mapping[str, str],
    fingerprint: str,
) -> bool:
    if not path.exists():
        return False
    try:
        payload = read_gzip_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        payload.get("schema") == "ca-hmcd-stage5-applicability-shard-v1"
        and payload.get("runner_version") == RUNNER_VERSION
        and payload.get("unit_id") == unit["unit_id"]
        and payload.get("code_hashes") == dict(hashes)
        and payload.get("protocol_fingerprint") == fingerprint
        and len(payload.get("result", {}).get("rows", [])) == STEPS
    )


def flatten_shards(
    output_dir: Path,
    units: Iterable[Mapping[str, object]],
    hashes: Mapping[str, str],
    fingerprint: str,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    episodes: List[Dict[str, object]] = []
    steps: List[Dict[str, object]] = []
    for unit in units:
        path = shard_path(output_dir, unit)
        if not valid_existing_shard(path, unit, hashes, fingerprint):
            continue
        payload = read_gzip_json(path)
        result = payload["result"]
        episode = dict(result["episode"])
        episode["shard_path"] = str(path.resolve())
        episode["shard_sha256"] = sha256_file(path)
        episodes.append(episode)
        steps.extend(dict(row) for row in result["rows"])
    return episodes, steps


def pairing_audit(
    rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[tuple[str, int, int], List[Mapping[str, object]]] = {}
    for row in rows:
        key = (
            str(row["scenario"]),
            int(row["seed"]),
            int(row["step"]),
        )
        grouped.setdefault(key, []).append(row)
    output: List[Dict[str, object]] = []
    expected = set(METHODS)
    for (scenario, seed, step), values in sorted(grouped.items()):
        methods = {str(row["algorithm"]) for row in values}
        fingerprints = {str(row["state_fingerprint"]) for row in values}
        task_ids = {str(row["active_task_ids_json"]) for row in values}
        loads = {float(row["active_tasks"]) for row in values}
        evaluators = {str(row["evaluator_version"]) for row in values}
        service_endpoints = {
            str(row["service_endpoint_version"]) for row in values
        }
        passed = (
            methods == expected
            and len(fingerprints) == 1
            and len(task_ids) == 1
            and len(loads) == 1
            and len(evaluators) == 1
            and len(service_endpoints) == 1
        )
        output.append(
            {
                "scenario": scenario,
                "seed": seed,
                "step": step,
                "expected_method_count": len(expected),
                "observed_method_count": len(methods),
                "missing_methods": ",".join(sorted(expected - methods)),
                "same_true_state": len(fingerprints) == 1,
                "same_active_task_ids": len(task_ids) == 1,
                "same_active_task_load": len(loads) == 1,
                "same_external_evaluator": len(evaluators) == 1,
                "same_service_endpoint": len(service_endpoints) == 1,
                "passed": passed,
            }
        )
    return output


def execute(
    output_dir: Path,
    *,
    mode: str,
    max_units: int | None,
    max_workers: int,
    resume: bool,
) -> Dict[str, object]:
    validation = validate_registration(mode == "formal")
    hashes = code_hashes()
    fingerprint = str(validation["protocol_fingerprint"])
    all_units = registered_units()
    selected = all_units if max_units is None else all_units[:max_units]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(PROTOCOL_DIR / "stage5_registered_units.csv", all_units)

    warmup_started = time.perf_counter()
    warmup = dict(all_units[0])
    warmup["unit_id"] = "warmup"
    execute_unit(warmup)
    warmup_s = time.perf_counter() - warmup_started

    pending: List[Dict[str, object]] = []
    skipped = 0
    for unit in selected:
        path = shard_path(output_dir, unit)
        if resume and valid_existing_shard(path, unit, hashes, fingerprint):
            skipped += 1
        else:
            pending.append(unit)

    started = time.perf_counter()
    completed = 0
    failures: List[Dict[str, object]] = []
    print(
        f"[stage5] selected={len(selected)}, pending={len(pending)}, "
        f"resumed={skipped}, workers={max_workers}",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(execute_unit, unit): unit for unit in pending
        }
        for future in as_completed(futures):
            unit = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failure = {
                    "unit_id": unit["unit_id"],
                    "scenario": unit["scenario"],
                    "algorithm": unit["algorithm"],
                    "seed": unit["seed"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                failures.append(failure)
                write_csv(output_dir / "stage5_failures.csv", failures)
                for pending_future in futures:
                    pending_future.cancel()
                raise RuntimeError(
                    f"Stage 5 unit failed without retry: {failure}"
                ) from exc
            payload = {
                "schema": "ca-hmcd-stage5-applicability-shard-v1",
                "runner_version": RUNNER_VERSION,
                "unit_id": unit["unit_id"],
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "protocol_fingerprint": fingerprint,
                "code_hashes": hashes,
                "result": result,
            }
            write_gzip_json_atomic(shard_path(output_dir, unit), payload)
            completed += 1
            processed = skipped + completed
            if (
                completed == 1
                or completed % 10 == 0
                or processed == len(selected)
            ):
                elapsed = time.perf_counter() - started
                rate = completed / max(elapsed, 1e-9)
                eta = (len(pending) - completed) / max(rate, 1e-9)
                print(
                    f"[stage5] {processed}/{len(selected)} complete; "
                    f"new={completed}, resumed={skipped}, "
                    f"elapsed={elapsed:.1f}s, eta={eta:.1f}s",
                    flush=True,
                )

    episodes, rows = flatten_shards(
        output_dir, selected, hashes, fingerprint
    )
    audits = pairing_audit(rows)
    expected_audits = len(SCENARIOS) * len(SEEDS) * STEPS
    audit_pass = (
        len(episodes) == len(selected)
        and len(rows) == len(selected) * STEPS
        and len(audits) == expected_audits
        and all(bool(row["passed"]) for row in audits)
    )
    if mode == "formal" and (
        len(selected) != len(all_units) or not audit_pass
    ):
        raise RuntimeError(
            "Formal Stage 5 execution is incomplete or failed pairing audit"
        )
    write_csv(output_dir / "stage5_episode_results.csv", episodes)
    write_csv(output_dir / "stage5_per_step.csv", rows)
    write_csv(output_dir / "stage5_pairing_audit.csv", audits)
    write_csv(
        output_dir / "stage5_cell_summary.csv",
        core.summarise(episodes, ("scenario", "algorithm", "method_label")),
    )
    output_names = (
        "stage5_episode_results.csv",
        "stage5_per_step.csv",
        "stage5_pairing_audit.csv",
        "stage5_cell_summary.csv",
    )
    metadata = {
        "schema": "ca-hmcd-stage5-applicability-execution-v1",
        "runner_version": RUNNER_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "selected_units": len(selected),
        "registered_units": len(all_units),
        "episode_results": len(episodes),
        "per_step_results": len(rows),
        "pairing_audits": len(audits),
        "expected_pairing_audits": expected_audits,
        "audit_pass": audit_pass,
        "new_units": completed,
        "resumed_units": skipped,
        "warmup_s": warmup_s,
        "formal_duration_s": time.perf_counter() - started,
        "max_workers": max_workers,
        "protocol_fingerprint": fingerprint,
        "code_hashes": hashes,
        "registration_validation": validation,
        "environment": {
            "python_executable": sys.executable,
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "cpu_count": os.cpu_count(),
        },
        "outputs": {
            name: sha256_file(output_dir / name) for name in output_names
        },
    }
    (output_dir / "stage5_execution_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "ca_hmcd_stage5_applicability_formal_20260918",
    )
    parser.add_argument(
        "--mode", choices=("smoke", "formal"), default="formal"
    )
    parser.add_argument("--max-units", type=int)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    result = execute(
        arguments.output_dir.resolve(),
        mode=arguments.mode,
        max_units=arguments.max_units,
        max_workers=arguments.max_workers,
        resume=not arguments.no_resume,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

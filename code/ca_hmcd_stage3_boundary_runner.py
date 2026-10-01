"""Execute and checkpoint the registered Stage 3 boundary experiment."""

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy
import scipy

from ca_hmcd_stage3_boundary import (
    BOUNDED_METHOD,
    ENGINE_VERSION,
    EXACT_METHOD,
    REGISTERED_BUDGETS_MS,
    REGISTERED_EXACT_TIME_LIMIT_MS,
    run_boundary_unit,
)


RUNNER_VERSION = "ca-hmcd-stage3-boundary-runner-v1"
RESOURCE_COUNTS = (10, 16, 24, 32)
TASK_COUNTS = (4, 8, 12, 16)
SEEDS = tuple(range(940000, 940030))
PROJECT_DIR = Path(__file__).resolve().parent
REGISTRATION_DIR = (
    PROJECT_DIR / "ca_hmcd_stage3_boundary_closure_20260917"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def registered_units() -> List[Dict[str, object]]:
    return [
        {
            "unit_id": f"R{resources_count}-T{tasks_count}-S{seed}",
            "resources_count": resources_count,
            "tasks_count": tasks_count,
            "seed": seed,
            "seed_offset": seed - SEEDS[0],
        }
        for resources_count in RESOURCE_COUNTS
        for tasks_count in TASK_COUNTS
        for seed in SEEDS
    ]


def shard_path(output_dir: Path, unit: Mapping[str, object]) -> Path:
    return (
        output_dir
        / "shards"
        / f"R{unit['resources_count']}-T{unit['tasks_count']}"
        / f"seed-{unit['seed']}.json.gz"
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
        raise ValueError(f"Invalid shard: {path}")
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


def code_hashes() -> Dict[str, str]:
    paths = {
        "ca_hmcd_stage3_boundary.py": (
            PROJECT_DIR / "ca_hmcd_stage3_boundary.py"
        ),
        "ca_hmcd_stage3_boundary_runner.py": (
            PROJECT_DIR / "ca_hmcd_stage3_boundary_runner.py"
        ),
        "ca_hmcd_simulation.py": PROJECT_DIR / "ca_hmcd_simulation.py",
        "ca_hmcd_framework.py": PROJECT_DIR / "ca_hmcd_framework.py",
        "stage3_preregistration.json": (
            REGISTRATION_DIR / "stage3_preregistration.json"
        ),
        "stage3_boundary_protocol.md": (
            REGISTRATION_DIR / "stage3_boundary_protocol.md"
        ),
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def validate_registration() -> Dict[str, object]:
    registration = json.loads(
        (
            REGISTRATION_DIR / "stage3_preregistration.json"
        ).read_text(encoding="utf-8")
    )
    design = registration["design"]
    checks = {
        "resources": tuple(design["resource_counts"]) == RESOURCE_COUNTS,
        "tasks": tuple(design["concurrent_task_counts"]) == TASK_COUNTS,
        "budgets": tuple(design["decision_budgets_ms"])
        == REGISTERED_BUDGETS_MS,
        "seeds": tuple(design["seeds"]) == SEEDS,
        "exact_time_limit": (
            registration["methods"]["CA-HMCD-Exact"][
                "solver_time_cap_ms"
            ]
            == REGISTERED_EXACT_TIME_LIMIT_MS
        ),
        "parent_gate_a_fail": (
            registration["parent_stage2_decision"]["decision"] == "FAIL"
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"Stage 3 registration mismatch: {checks}")
    return {"registration": registration, "checks": checks}


def valid_existing_shard(
    path: Path,
    unit: Mapping[str, object],
    hashes: Mapping[str, str],
) -> bool:
    if not path.exists():
        return False
    payload = read_gzip_json(path)
    return (
        payload.get("unit_id") == unit["unit_id"]
        and payload.get("code_hashes") == dict(hashes)
        and len(payload.get("result", {}).get("rows", [])) == 5
    )


def flatten_shards(
    output_dir: Path,
    units: Iterable[Mapping[str, object]],
    hashes: Mapping[str, str],
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    rows: List[Dict[str, object]] = []
    unit_rows: List[Dict[str, object]] = []
    for unit in units:
        path = shard_path(output_dir, unit)
        if not valid_existing_shard(path, unit, hashes):
            continue
        payload = read_gzip_json(path)
        result = payload["result"]
        unit_rows.append(
            {
                "unit_id": unit["unit_id"],
                "resources_count": unit["resources_count"],
                "tasks_count": unit["tasks_count"],
                "seed": unit["seed"],
                "state_fingerprint": result["state_fingerprint"],
                "unit_runtime_s": result["unit_runtime_s"],
                "shard_path": str(path.resolve()),
                "shard_sha256": sha256_file(path),
            }
        )
        rows.extend(dict(row) for row in result["rows"])
    return rows, unit_rows


def execute(
    output_dir: Path,
    mode: str,
    max_units: int | None,
    resume: bool,
) -> Dict[str, object]:
    validation = validate_registration()
    hashes = code_hashes()
    all_units = registered_units()
    selected = all_units if max_units is None else all_units[:max_units]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(REGISTRATION_DIR / "stage3_registered_units.csv", all_units)

    warmup_started = time.perf_counter()
    run_boundary_unit(
        seed=939999,
        resources_count=6,
        tasks_count=3,
        seed_offset=0,
        exact_time_limit_ms=1000,
        budgets_ms=(100,),
    )
    warmup_s = time.perf_counter() - warmup_started

    started = time.perf_counter()
    completed = 0
    skipped = 0
    for index, unit in enumerate(selected, start=1):
        path = shard_path(output_dir, unit)
        if resume and valid_existing_shard(path, unit, hashes):
            skipped += 1
            continue
        result = run_boundary_unit(
            seed=int(unit["seed"]),
            resources_count=int(unit["resources_count"]),
            tasks_count=int(unit["tasks_count"]),
            seed_offset=int(unit["seed_offset"]),
        )
        payload = {
            "schema": "ca-hmcd-stage3-boundary-shard-v1",
            "runner_version": RUNNER_VERSION,
            "engine_version": ENGINE_VERSION,
            "unit_id": unit["unit_id"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "code_hashes": hashes,
            "result": result,
        }
        write_gzip_json_atomic(path, payload)
        completed += 1
        if index == 1 or index % 5 == 0 or index == len(selected):
            elapsed = time.perf_counter() - started
            print(
                f"[stage3] {index}/{len(selected)} units; "
                f"new={completed}, skipped={skipped}, elapsed={elapsed:.1f}s",
                flush=True,
            )

    rows, unit_rows = flatten_shards(output_dir, selected, hashes)
    write_csv(output_dir / "stage3_boundary_runs.csv", rows)
    write_csv(output_dir / "stage3_unit_manifest.csv", unit_rows)
    expected_rows = len(selected) * 5
    exact_rows = [row for row in rows if row["method"] == EXACT_METHOD]
    bounded_rows = [row for row in rows if row["method"] == BOUNDED_METHOD]
    identity_count = len(
        {
            (
                row["resources_count"],
                row["tasks_count"],
                row["seed"],
                row["method"],
                row["registered_budget_ms"],
            )
            for row in rows
        }
    )
    state_counts: Dict[tuple[object, object, object], set[object]] = {}
    for row in rows:
        key = (row["resources_count"], row["tasks_count"], row["seed"])
        state_counts.setdefault(key, set()).add(row["state_fingerprint"])
    audit_checks = {
        "selected_unit_count": len(unit_rows) == len(selected),
        "total_run_count": len(rows) == expected_rows,
        "exact_run_count": len(exact_rows) == len(selected),
        "bounded_run_count": len(bounded_rows) == len(selected) * 4,
        "unique_run_identity": identity_count == len(rows),
        "common_state_within_unit": all(
            len(values) == 1 for values in state_counts.values()
        ),
        "all_final_allocations_feasible": all(
            float(row["final_feasibility"]) == 1.0 for row in rows
        ),
    }
    audit_pass = all(audit_checks.values())
    metadata: Dict[str, object] = {
        "schema": "ca-hmcd-stage3-boundary-execution-v1",
        "runner_version": RUNNER_VERSION,
        "engine_version": ENGINE_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "command": [sys.executable, *sys.argv],
        "working_directory": str(PROJECT_DIR),
        "output_directory": str(output_dir.resolve()),
        "execution": "single-process sequential",
        "warmup_excluded_seconds": warmup_s,
        "formal_elapsed_seconds": time.perf_counter() - started,
        "registered_units_total": len(all_units),
        "selected_registered_units": len(selected),
        "run_rows": len(rows),
        "methods": [EXACT_METHOD, BOUNDED_METHOD],
        "budgets_ms": list(REGISTERED_BUDGETS_MS),
        "code_hashes": hashes,
        "registration_checks": validation["checks"],
        "audit_checks": audit_checks,
        "audit_pass": audit_pass,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
            "openblas_num_threads": os.environ.get(
                "OPENBLAS_NUM_THREADS"
            ),
            "mkl_num_threads": os.environ.get("MKL_NUM_THREADS"),
        },
    }
    (output_dir / "stage3_execution_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not audit_pass:
        raise RuntimeError(f"Stage 3 execution audit failed: {audit_checks}")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR
        / "ca_hmcd_stage3_boundary_formal_20260917",
    )
    parser.add_argument(
        "--mode", choices=("smoke", "formal"), default="formal"
    )
    parser.add_argument("--max-units", type=int)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = execute(
        args.output_dir.resolve(),
        args.mode,
        args.max_units,
        args.resume,
    )
    print(
        json.dumps(
            {
                "audit_pass": metadata["audit_pass"],
                "selected_registered_units": metadata[
                    "selected_registered_units"
                ],
                "run_rows": metadata["run_rows"],
                "formal_elapsed_seconds": metadata[
                    "formal_elapsed_seconds"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

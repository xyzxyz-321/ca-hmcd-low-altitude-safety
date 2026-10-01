"""Run the registered Stage 4 value-model and engineering validation study."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import platform
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import ca_hmcd_simulation as core


RUNNER_VERSION = "ca-hmcd-stage4-value-engineering-runner-v1"
PROJECT_DIR = Path(__file__).resolve().parent
PROTOCOL_DIR = (
    PROJECT_DIR
    / "ca_hmcd_stage4_value_engineering_protocol_20260918"
)
PROTOCOL_PATH = PROTOCOL_DIR / "stage4_preregistration.json"
PROTOCOL_TEXT_PATH = PROTOCOL_DIR / "stage4_protocol.md"
SEEDS = tuple(range(950000, 950030))
STEPS = 12
TOP_K = 12
NODE_LIMIT = 12000
ENGINEERING_SCENARIOS = (
    "airport_corridor",
    "energy_facility",
    "public_event",
    "urban_corridor",
    "industrial_zone",
)
ENGINEERING_METHODS = (
    "CA-HMCD",
    "No-Compatibility",
    "No-Synergy",
    "No-Redundancy",
    "No-Stability",
    "Greedy",
    "External-Auction",
)
COMPLEMENTARITY_SCALES = (0.0, 0.5, 1.0, 1.5, 2.0)
STABILITY_LEVELS = (0.0, 0.05, 0.1, 0.2, 0.3)
REDUNDANCY_METHODS = (
    "CA-HMCD",
    "No-Redundancy",
    "No-Synergy",
    "Greedy",
)
REPAIR_LEVELS = (0.0, 0.05, 0.1, 0.2, 0.3)
REPAIR_METHODS = (
    "CA-HMCD-RepairOn",
    "CA-HMCD-RepairOff",
)
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
        "ca_hmcd_stage4_value_engineering_runner.py": Path(__file__).resolve(),
        "ca_hmcd_simulation.py": PROJECT_DIR / "ca_hmcd_simulation.py",
        "ca_hmcd_framework.py": PROJECT_DIR / "ca_hmcd_framework.py",
        "stage4_preregistration.json": PROTOCOL_PATH,
        "stage4_protocol.md": PROTOCOL_TEXT_PATH,
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def safe_slug(value: object) -> str:
    text = str(value).replace(".", "p")
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")


def make_unit(
    study: str,
    stratum: str,
    method: str,
    core_algorithm: str,
    seed: int,
    scenario: str,
    factor_name: str,
    factor_level: float | str,
    *,
    noise_level: float = 0.0,
    synergy_scale: float = 1.0,
    volatility_override: float | None = None,
    enable_safety_repair: bool = True,
) -> Dict[str, object]:
    params = core.scenario_parameters(scenario)
    unit_id = "__".join(
        safe_slug(part)
        for part in (study, stratum, method, seed)
    )
    return {
        "unit_id": unit_id,
        "study": study,
        "stratum": stratum,
        "method": method,
        "core_algorithm": core_algorithm,
        "seed": seed,
        "scenario": scenario,
        "factor_name": factor_name,
        "factor_level": factor_level,
        "resources_count": int(params["resources"]),
        "tasks_count": int(params["tasks"]),
        "noise_level": noise_level,
        "synergy_scale": synergy_scale,
        "volatility_override": volatility_override,
        "enable_safety_repair": enable_safety_repair,
    }


def registered_units() -> List[Dict[str, object]]:
    units: List[Dict[str, object]] = []
    for scenario in ENGINEERING_SCENARIOS:
        for method in ENGINEERING_METHODS:
            for seed in SEEDS:
                units.append(
                    make_unit(
                        "engineering",
                        scenario,
                        method,
                        method,
                        seed,
                        scenario,
                        "scenario",
                        scenario,
                    )
                )
    for scale in COMPLEMENTARITY_SCALES:
        stratum = f"scale-{scale:.2f}"
        for method in ("CA-HMCD", "No-Synergy"):
            for seed in SEEDS:
                units.append(
                    make_unit(
                        "complementarity_sweep",
                        stratum,
                        method,
                        method,
                        seed,
                        "public_event",
                        "synergy_scale",
                        scale,
                        synergy_scale=scale,
                    )
                )
    for volatility in STABILITY_LEVELS:
        stratum = f"volatility-{volatility:.2f}"
        for method in ("CA-HMCD", "No-Stability"):
            for seed in SEEDS:
                units.append(
                    make_unit(
                        "stability_sweep",
                        stratum,
                        method,
                        method,
                        seed,
                        "urban_corridor",
                        "volatility",
                        volatility,
                        volatility_override=volatility,
                    )
                )
    for method in REDUNDANCY_METHODS:
        for seed in SEEDS:
            units.append(
                make_unit(
                    "redundancy_stress",
                    "same-type-dense",
                    method,
                    method,
                    seed,
                    "redundancy_stress",
                    "stress_profile",
                    "same-type-dense",
                )
            )
    for noise in REPAIR_LEVELS:
        stratum = f"noise-{noise:.2f}"
        for method in REPAIR_METHODS:
            for seed in SEEDS:
                units.append(
                    make_unit(
                        "repair_disturbance",
                        stratum,
                        method,
                        "CA-HMCD",
                        seed,
                        "public_event",
                        "noise_level",
                        noise,
                        noise_level=noise,
                        enable_safety_repair=method.endswith("RepairOn"),
                    )
                )
    return units


def validate_registration() -> Dict[str, object]:
    registration = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    units = registered_units()
    observed_hashes = {
        name: sha256_file(PROJECT_DIR / name)
        for name in EXPECTED_FROZEN_HASHES
    }
    checks = {
        "seeds": tuple(registration["frozen_contract"]["seeds"]) == SEEDS,
        "steps": registration["frozen_contract"]["steps"] == STEPS,
        "top_k": registration["frozen_contract"]["top_k"] == TOP_K,
        "node_limit": (
            registration["frozen_contract"]["node_limit"] == NODE_LIMIT
        ),
        "candidate_pool": (
            registration["frozen_contract"]["candidate_pool_mode"] == "shared"
        ),
        "episode_count": (
            registration["design"]["episodes"]["total"] == len(units) == 2070
        ),
        "frozen_hashes": observed_hashes == EXPECTED_FROZEN_HASHES,
        "model_version": (
            core.MODEL_VERSION
            == registration["frozen_contract"]["value_model"]
        ),
        "evaluator_version": (
            core.EVALUATOR_VERSION
            == registration["frozen_contract"]["external_evaluator"]
        ),
        "service_endpoint": (
            core.SERVICE_ENDPOINT_VERSION
            == registration["frozen_contract"]["service_endpoint"]
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"Stage 4 registration mismatch: {checks}")
    return {
        "registration": registration,
        "checks": checks,
        "frozen_hashes": observed_hashes,
        "protocol_fingerprint": protocol_fingerprint(),
    }


def shard_path(output_dir: Path, unit: Mapping[str, object]) -> Path:
    return (
        output_dir
        / "shards"
        / str(unit["study"])
        / safe_slug(unit["stratum"])
        / safe_slug(unit["method"])
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


def write_csv(
    path: Path, rows: Sequence[Mapping[str, object]]
) -> None:
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
        payload.get("schema") == "ca-hmcd-stage4-value-engineering-shard-v1"
        and payload.get("runner_version") == RUNNER_VERSION
        and payload.get("unit_id") == unit["unit_id"]
        and payload.get("code_hashes") == dict(hashes)
        and payload.get("protocol_fingerprint") == fingerprint
        and len(payload.get("result", {}).get("rows", [])) == STEPS
    )


def execute_unit(unit: Mapping[str, object]) -> Dict[str, object]:
    started = time.perf_counter()
    metrics, rows = core.run_episode(
        algorithm=str(unit["core_algorithm"]),
        seed=int(unit["seed"]),
        steps=STEPS,
        resources_count=int(unit["resources_count"]),
        tasks_count=int(unit["tasks_count"]),
        scenario=str(unit["scenario"]),
        top_k=TOP_K,
        noise_level=float(unit["noise_level"]),
        synergy_scale=float(unit["synergy_scale"]),
        volatility_override=(
            None
            if unit["volatility_override"] is None
            else float(unit["volatility_override"])
        ),
        enable_safety_repair=bool(unit["enable_safety_repair"]),
        node_limit_override=NODE_LIMIT,
        candidate_pool_mode="shared",
        force_all_tasks_active=True,
    )
    labels = {
        "study": unit["study"],
        "stratum": unit["stratum"],
        "factor_name": unit["factor_name"],
        "factor_level": unit["factor_level"],
        "algorithm": unit["method"],
        "core_algorithm": unit["core_algorithm"],
        "repair_enabled": bool(unit["enable_safety_repair"]),
        "registered_resources": unit["resources_count"],
        "registered_tasks": unit["tasks_count"],
    }
    labelled_rows = [{**row, **labels} for row in rows]
    return {
        "episode": {
            **{key: unit[key] for key in unit},
            **metrics,
            "algorithm": unit["method"],
            "episode_runtime_s": time.perf_counter() - started,
        },
        "rows": labelled_rows,
        "unit_runtime_s": time.perf_counter() - started,
    }


def expected_methods(study: str) -> Sequence[str]:
    return {
        "engineering": ENGINEERING_METHODS,
        "complementarity_sweep": ("CA-HMCD", "No-Synergy"),
        "stability_sweep": ("CA-HMCD", "No-Stability"),
        "redundancy_stress": REDUNDANCY_METHODS,
        "repair_disturbance": REPAIR_METHODS,
    }[study]


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
    grouped: Dict[
        tuple[str, str, int, int], List[Mapping[str, object]]
    ] = {}
    for row in rows:
        key = (
            str(row["study"]),
            str(row["stratum"]),
            int(row["seed"]),
            int(row["step"]),
        )
        grouped.setdefault(key, []).append(row)
    audits: List[Dict[str, object]] = []
    for key, values in sorted(grouped.items()):
        study, stratum, seed, step = key
        methods = {str(row["algorithm"]) for row in values}
        expected = set(expected_methods(study))
        state_fingerprints = {
            str(row["state_fingerprint"]) for row in values
        }
        active_tasks = {float(row["active_tasks"]) for row in values}
        active_ids = {
            str(row["active_task_ids_json"]) for row in values
        }
        evaluators = {
            str(row["evaluator_version"]) for row in values
        }
        service_endpoints = {
            str(row["service_endpoint_version"]) for row in values
        }
        audits.append(
            {
                "study": study,
                "stratum": stratum,
                "seed": seed,
                "step": step,
                "expected_method_count": len(expected),
                "observed_method_count": len(methods),
                "missing_methods": ",".join(sorted(expected - methods)),
                "same_true_state": len(state_fingerprints) == 1,
                "same_active_task_load": len(active_tasks) == 1,
                "same_active_task_ids": len(active_ids) == 1,
                "same_external_evaluator": len(evaluators) == 1,
                "same_service_endpoint": len(service_endpoints) == 1,
                "passed": (
                    methods == expected
                    and len(state_fingerprints) == 1
                    and len(active_tasks) == 1
                    and len(active_ids) == 1
                    and len(evaluators) == 1
                    and len(service_endpoints) == 1
                ),
            }
        )
    return audits


def build_summary(
    episodes: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    return core.summarise(
        episodes,
        ("study", "stratum", "algorithm"),
    )


def execute(
    output_dir: Path,
    *,
    mode: str,
    max_units: int | None,
    max_workers: int,
    resume: bool,
) -> Dict[str, object]:
    validation = validate_registration()
    hashes = code_hashes()
    fingerprint = str(validation["protocol_fingerprint"])
    all_units = registered_units()
    selected = all_units if max_units is None else all_units[:max_units]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(PROTOCOL_DIR / "stage4_registered_units.csv", all_units)

    warmup_started = time.perf_counter()
    warmup_unit = make_unit(
        "warmup",
        "warmup",
        "CA-HMCD",
        "CA-HMCD",
        949999,
        "airport_corridor",
        "warmup",
        0,
    )
    warmup_unit["resources_count"] = 6
    warmup_unit["tasks_count"] = 3
    execute_unit(warmup_unit)
    warmup_s = time.perf_counter() - warmup_started

    pending: List[Dict[str, object]] = []
    skipped = 0
    for unit in selected:
        path = shard_path(output_dir, unit)
        if resume and valid_existing_shard(
            path, unit, hashes, fingerprint
        ):
            skipped += 1
        else:
            pending.append(unit)

    started = time.perf_counter()
    completed = 0
    failures: List[Dict[str, object]] = []
    print(
        f"[stage4] selected={len(selected)}, pending={len(pending)}, "
        f"resumed={skipped}, workers={max_workers}",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(execute_unit, unit): unit
            for unit in pending
        }
        for future in as_completed(futures):
            unit = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failure = {
                    "unit_id": unit["unit_id"],
                    "study": unit["study"],
                    "stratum": unit["stratum"],
                    "method": unit["method"],
                    "seed": unit["seed"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                failures.append(failure)
                write_csv(output_dir / "stage4_failures.csv", failures)
                for pending_future in futures:
                    pending_future.cancel()
                raise RuntimeError(
                    f"Stage 4 unit failed without retry: {failure}"
                ) from exc
            payload = {
                "schema": "ca-hmcd-stage4-value-engineering-shard-v1",
                "runner_version": RUNNER_VERSION,
                "unit_id": unit["unit_id"],
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "protocol_fingerprint": fingerprint,
                "code_hashes": hashes,
                "result": result,
            }
            write_gzip_json_atomic(
                shard_path(output_dir, unit), payload
            )
            completed += 1
            processed = skipped + completed
            if (
                completed == 1
                or completed % 25 == 0
                or processed == len(selected)
            ):
                elapsed = time.perf_counter() - started
                rate = completed / max(elapsed, 1e-9)
                remaining = (len(pending) - completed) / max(rate, 1e-9)
                print(
                    f"[stage4] {processed}/{len(selected)} complete; "
                    f"new={completed}, resumed={skipped}, "
                    f"elapsed={elapsed:.1f}s, eta={remaining:.1f}s",
                    flush=True,
                )

    episodes, rows = flatten_shards(
        output_dir, selected, hashes, fingerprint
    )
    audits = pairing_audit(rows)
    expected_audits = sum(
        len(SEEDS) * STEPS
        for study, strata in (
            ("engineering", len(ENGINEERING_SCENARIOS)),
            ("complementarity_sweep", len(COMPLEMENTARITY_SCALES)),
            ("stability_sweep", len(STABILITY_LEVELS)),
            ("redundancy_stress", 1),
            ("repair_disturbance", len(REPAIR_LEVELS)),
        )
        for _ in range(strata)
    )
    complete = len(episodes) == len(selected)
    audit_pass = (
        complete
        and len(rows) == len(selected) * STEPS
        and len(audits) == expected_audits
        and all(bool(row["passed"]) for row in audits)
    )
    if mode == "formal" and (
        len(selected) != len(all_units) or not audit_pass
    ):
        raise RuntimeError(
            "Formal Stage 4 execution is incomplete or failed pairing audit"
        )

    write_csv(output_dir / "stage4_episode_results.csv", episodes)
    write_csv(output_dir / "stage4_per_step.csv", rows)
    write_csv(output_dir / "stage4_pairing_audit.csv", audits)
    write_csv(output_dir / "stage4_cell_summary.csv", build_summary(episodes))
    metadata = {
        "schema": "ca-hmcd-stage4-value-engineering-execution-v1",
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
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": _package_version("numpy"),
            "scipy": _package_version("scipy"),
            "cpu_count": os.cpu_count(),
        },
        "outputs": {
            name: sha256_file(output_dir / name)
            for name in (
                "stage4_episode_results.csv",
                "stage4_per_step.csv",
                "stage4_pairing_audit.csv",
                "stage4_cell_summary.csv",
            )
        },
    }
    (output_dir / "stage4_execution_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata


def _package_version(name: str) -> str:
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return "unavailable"
    except Exception as exc:
        return f"unavailable:{type(exc).__name__}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            PROJECT_DIR
            / "ca_hmcd_stage4_value_engineering_formal_20260918"
        ),
    )
    parser.add_argument(
        "--mode", choices=("smoke", "formal"), default="formal"
    )
    parser.add_argument("--max-units", type=int)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=max(1, min(6, os.cpu_count() or 1)),
    )
    parser.add_argument(
        "--no-resume", action="store_true"
    )
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

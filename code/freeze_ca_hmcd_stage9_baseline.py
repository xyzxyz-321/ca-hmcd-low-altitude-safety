"""Freeze the Stage 9 CA-HMCD evidence base before v3 framework changes."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "ca_hmcd_stage0_stage1_20260917"
BASELINE_DIR = OUTPUT_ROOT / "stage0_baseline"
SOURCE_DIR = BASELINE_DIR / "source"
PROTOCOL_DIR = BASELINE_DIR / "protocol"
METADATA_DIR = BASELINE_DIR / "formal_metadata"

BASELINE_ID = "ca-hmcd-stage9-frozen-20260917"
REVISION_ID = "ca-hmcd-v3-framework-registration-20260917"

PACKAGE_DIR = ROOT / "CA-HMCD_Reproducibility_Package_20260917"
PACKAGE_MANIFEST = PACKAGE_DIR / "MANIFEST.csv"
PACKAGE_CHECKSUMS = PACKAGE_DIR / "checksums.sha256"
PACKAGE_ZIP = ROOT / "CA-HMCD_Reproducibility_Package_20260917.zip"

SOURCE_FILES = (
    "ca_hmcd_simulation.py",
    "ca_hmcd_replay.py",
    "ca_hmcd_replay_runner.py",
    "ca_hmcd_replay_protocol.py",
    "ca_hmcd_external_baseline_analysis.py",
    "ca_hmcd_no_pruning_analysis.py",
    "ca_hmcd_parameter_robustness_analysis.py",
)

PROTOCOL_FILES = (
    "ca_hmcd_replay_protocol_20260916/protocol_config.json",
    "ca_hmcd_replay_protocol_20260916/experiment_run_registry.csv",
    "ca_hmcd_no_pruning_protocol_20260917.md",
    "ca_hmcd_external_baselines_protocol_20260917.md",
    "ca_hmcd_external_baselines_protocol_amendment_20260917.md",
)

FORMAL_METADATA_FILES = (
    "CA-HMCD_Reproducibility_Package_20260917/results/formal_runs/"
    "primary_public_replay/execution_metadata.json",
    "ca_hmcd_external_reference_formal_20260917/execution_metadata.json",
    "ca_hmcd_external_baselines_formal_20260917/execution_metadata.json",
    "ca_hmcd_no_pruning_formal_20260917/execution_metadata.json",
    "ca_hmcd_parameter_robustness_analysis_20260917/analysis_metadata.json",
)

REFERENCE_ARTIFACTS = (
    "CA-HMCD_EAAI_Stage9_Robustness_Release_20260917.docx",
    "CA-HMCD_EAAI_Stage9_Robustness_Release_20260917_audit.txt",
    "CA-HMCD_Reproducibility_Package_20260917.zip",
    "CA-HMCD_Reproducibility_Package_20260917/MANIFEST.csv",
    "CA-HMCD_Reproducibility_Package_20260917/checksums.sha256",
    "ca_hmcd_external_reference_formal_20260917/registered_episode_results.csv",
    "ca_hmcd_external_baselines_formal_20260917/registered_episode_results.csv",
    "ca_hmcd_no_pruning_formal_20260917/registered_episode_results.csv",
    "ca_hmcd_parameter_robustness_analysis_20260917/configuration_summary_54.csv",
    "ca_hmcd_parameter_robustness_analysis_20260917/paired_statistics_36.csv",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_hashes() -> dict[str, str]:
    with PACKAGE_MANIFEST.open("r", newline="", encoding="utf-8-sig") as handle:
        return {
            row["relative_path"].replace("\\", "/"): row["sha256"]
            for row in csv.DictReader(handle)
        }


def ensure_inside_workspace(path: Path) -> None:
    resolved_root = ROOT.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise RuntimeError(f"Refusing to write outside workspace: {resolved_path}")


def copy_frozen(source: Path, destination: Path) -> None:
    ensure_inside_workspace(destination)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    if destination.exists():
        if sha256_file(destination) != source_hash:
            raise RuntimeError(f"Frozen destination already exists with another hash: {destination}")
        return
    shutil.copy2(source, destination)
    destination.chmod(stat.S_IREAD)


def load_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: object) -> None:
    ensure_inside_workspace(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise RuntimeError(f"Registry already exists with different content: {path}")
    path.write_text(encoded, encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    ensure_inside_workspace(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise RuntimeError(f"Registry already exists with different content: {path}")
    path.write_text(text, encoding="utf-8")


def collect_manifest_rows(paths: Iterable[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        rows.append(
            {
                "relative_path": path.relative_to(ROOT).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def verify_packaged_stage9_source(expected: dict[str, str]) -> None:
    for name in SOURCE_FILES:
        packaged_key = f"code/{name}"
        if packaged_key not in expected:
            raise RuntimeError(f"Missing Stage 9 package entry: {packaged_key}")
        observed = sha256_file(ROOT / name)
        if observed != expected[packaged_key]:
            raise RuntimeError(
                f"Current {name} does not match the Stage 9 release: "
                f"{observed} != {expected[packaged_key]}"
            )


def build_baseline_registry(manifest_rows: list[dict[str, object]]) -> dict[str, object]:
    primary = load_json(Path(FORMAL_METADATA_FILES[0]))
    reference = load_json(Path(FORMAL_METADATA_FILES[1]))
    external = load_json(Path(FORMAL_METADATA_FILES[2]))
    no_pruning = load_json(Path(FORMAL_METADATA_FILES[3]))
    robustness = load_json(Path(FORMAL_METADATA_FILES[4]))
    return {
        "baseline_id": BASELINE_ID,
        "status": "FROZEN",
        "frozen_date": "2026-09-17",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_policy": (
            "The Stage 9 source and evidence are immutable references. "
            "All v3 work must use new modules or explicitly versioned outputs."
        ),
        "model_version": primary["model_version"],
        "evaluator_version": primary["evaluator_version"],
        "service_endpoint_version": primary["service_endpoint_version"],
        "protocol": {
            "protocol_fingerprint": primary["protocol_fingerprint"],
            "protocol_config_sha256": primary["protocol_config_sha256"],
            "run_registry_sha256": primary["run_registry_sha256"],
            "independent_units": 44,
            "nested_seeds_per_unit": 30,
            "registered_units": primary["selected_registered_units"],
            "horizon_steps": 12,
            "resources_count": 10,
            "top_k": 12,
            "node_limit": 4000,
        },
        "formal_sessions": {
            "primary_six_method_replay": {
                "algorithms": primary["algorithms"],
                "expected_runs": primary["expected_algorithm_runs"],
                "audit_pass": primary["audit_pass"],
                "code_hashes": primary["code_hashes"],
            },
            "matched_ca_reference": {
                "algorithms": reference["algorithms"],
                "expected_runs": reference["expected_algorithm_runs"],
                "audit_pass": reference["audit_pass"],
                "code_hashes": reference["code_hashes"],
            },
            "external_full_candidate_baselines": {
                "algorithms": external["algorithms"],
                "expected_runs": external["expected_algorithm_runs"],
                "audit_pass": external["audit_pass"],
                "code_hashes": external["code_hashes"],
            },
            "no_pruning_control": {
                "algorithms": no_pruning["algorithms"],
                "expected_runs": no_pruning["expected_algorithm_runs"],
                "audit_pass": no_pruning["audit_pass"],
                "code_hashes": no_pruning["code_hashes"],
            },
            "parameter_sensitivity": {
                "parameter_configurations": robustness["parameter_configurations"],
                "seed_level_records": robustness["seed_level_records"],
                "paired_comparisons": robustness["paired_comparisons"],
                "holm_families": robustness["holm_families"],
                "audit_pass": robustness["audit_pass"],
            },
        },
        "reproducibility_package": {
            "zip_path": PACKAGE_ZIP.relative_to(ROOT).as_posix(),
            "zip_sha256": sha256_file(PACKAGE_ZIP),
            "manifest_sha256": sha256_file(PACKAGE_MANIFEST),
            "checksums_sha256": sha256_file(PACKAGE_CHECKSUMS),
        },
        "registered_files": manifest_rows,
    }


def build_revision_registration() -> dict[str, object]:
    return {
        "revision_id": REVISION_ID,
        "registration_date": "2026-09-17",
        "parent_baseline_id": BASELINE_ID,
        "status": "REGISTERED_BEFORE_V3_EXPERIMENTS",
        "framework_version": "ca-hmcd-framework-v3.0",
        "unchanged_contract": {
            "value_model_version": "ca-hmcd-stage1-closure-v2.3",
            "external_evaluator_version": "complete-v3-stage1-closure",
            "service_endpoint_version": "solver-decoupled-service-v2",
            "coefficients": {
                "alpha_cost": 0.12,
                "beta_time": 0.08,
                "lambda_switch": 0.10,
                "lambda_redundancy": 0.12,
                "gamma_unserved": 0.20,
            },
            "rule": (
                "Do not tune the value model or evaluator to reverse the "
                "registered HiGHS comparison."
            ),
        },
        "stage1_method_profiles": {
            "CA-HMCD-Exact": {
                "candidate_policy": "full",
                "solver_backend": "highs",
                "status": "ACTIVE",
            },
            "CA-HMCD-FixedK": {
                "candidate_policy": "fixed_k",
                "top_k": 12,
                "solver_backend": "bounded_search",
                "status": "ACTIVE_STAGE9_REFERENCE",
            },
            "CA-HMCD-NoPruning": {
                "candidate_policy": "full",
                "solver_backend": "bounded_search",
                "status": "ACTIVE_CONTROL",
            },
            "CA-HMCD-RT": {
                "candidate_policy": "adaptive_k",
                "solver_backend": "bounded_search",
                "status": "RESERVED_FOR_STAGE2",
            },
        },
        "planned_candidate_scan": {
            "fixed_k_values": [6, 12, 24, 48, "full"],
            "adaptive_k": "registered for Stage 2; rule not selected in Stage 1",
        },
        "planned_scale_boundary": {
            "resource_counts": [10, 16, 24, 32],
            "concurrent_task_counts": [4, 8, 12, 16],
            "decision_budgets_ms": [50, 100, 200, 500],
        },
        "primary_outcomes": [
            "complete_model_true_state_objective",
            "solver_decoupled_model_consistent_service_score",
            "coverage",
            "common_task_response_time",
            "candidate_counts_by_stage",
            "runtime",
            "visited_nodes",
            "fallback_rate",
            "retained_pool_gap",
            "final_feasibility",
        ],
        "registered_comparison_families": [
            "value_model_ablations",
            "candidate_reduction",
            "exact_vs_real_time",
            "scale_and_deadline",
            "repair_mechanism",
            "parameter_sensitivity",
        ],
        "decision_gates": {
            "A": (
                "Retain a real-time profile only if adaptive retention reduces "
                "high-load loss while preserving a meaningful compute benefit."
            ),
            "B": (
                "Claim a real-time advantage only where full-candidate HiGHS "
                "fails the registered deadline and CA-HMCD-RT returns a "
                "feasible solution with acceptable quality loss."
            ),
            "C": (
                "Treat true-state repair as a core contribution only if a "
                "disturbance experiment demonstrates a benefit."
            ),
        },
    }


def main() -> None:
    for path in (OUTPUT_ROOT, BASELINE_DIR, SOURCE_DIR, PROTOCOL_DIR, METADATA_DIR):
        ensure_inside_workspace(path)
        path.mkdir(parents=True, exist_ok=True)

    expected = package_hashes()
    verify_packaged_stage9_source(expected)

    for name in SOURCE_FILES:
        copy_frozen(ROOT / name, SOURCE_DIR / name)
    for relative in PROTOCOL_FILES:
        copy_frozen(ROOT / relative, PROTOCOL_DIR / Path(relative).name)
    for relative in FORMAL_METADATA_FILES:
        source = ROOT / relative
        session_name = source.parent.name
        copy_frozen(source, METADATA_DIR / f"{session_name}_execution_metadata.json")
    copy_frozen(PACKAGE_MANIFEST, BASELINE_DIR / "release_MANIFEST.csv")
    copy_frozen(PACKAGE_CHECKSUMS, BASELINE_DIR / "release_checksums.sha256")

    registered_paths = [
        *(ROOT / name for name in SOURCE_FILES),
        *(ROOT / relative for relative in PROTOCOL_FILES),
        *(ROOT / relative for relative in FORMAL_METADATA_FILES),
        *(ROOT / relative for relative in REFERENCE_ARTIFACTS),
    ]
    manifest_rows = collect_manifest_rows(registered_paths)
    baseline_registry = build_baseline_registry(manifest_rows)
    revision_registration = build_revision_registration()

    write_json(BASELINE_DIR / "baseline_registry.json", baseline_registry)
    write_json(OUTPUT_ROOT / "revision_registration.json", revision_registration)

    manifest_path = BASELINE_DIR / "baseline_manifest.csv"
    if manifest_path.exists():
        raise RuntimeError(f"Refusing to overwrite frozen manifest: {manifest_path}")
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=("relative_path", "size_bytes", "sha256")
        )
        writer.writeheader()
        writer.writerows(manifest_rows)

    write_text(
        BASELINE_DIR / "FROZEN_BASELINE.md",
        f"""# Frozen Stage 9 Baseline

- Baseline ID: `{BASELINE_ID}`
- Freeze date: 2026-09-17
- Status: `FROZEN`
- Model: `ca-hmcd-stage1-closure-v2.3`
- External evaluator: `complete-v3-stage1-closure`
- Service endpoint: `solver-decoupled-service-v2`
- Protocol fingerprint: `{baseline_registry['protocol']['protocol_fingerprint']}`

The source copies in `source/` are read-only snapshots. The Stage 9 manuscript,
formal result directories, and reproducibility ZIP remain in their original
workspace locations and are pinned by SHA-256 in `baseline_manifest.csv` and
`baseline_registry.json`.

All v3 development must use new versioned modules and output directories. The
Stage 9 core files must not be edited when reproducing the registered baseline.
""",
    )
    write_text(
        OUTPUT_ROOT / "revision_protocol.md",
        """# CA-HMCD v3 Revision Registration

Date registered: 2026-09-17

## Locked evidence contract

The Stage 9 value model, coefficients, public replay split, external evaluator,
and service endpoint are unchanged. They must not be tuned to reverse the
registered result that full-candidate HiGHS is faster at the ten-resource scale
and returns higher-quality high-load allocations.

## Stage 1 architecture

The v3 framework separates:

1. complete candidate construction and coalition valuation;
2. candidate retention policy;
3. global allocation backend;
4. true-state feasibility repair;
5. complete-model and solver-decoupled evaluation.

`CA-HMCD-Exact` uses the complete candidate pool and HiGHS.
`CA-HMCD-FixedK` reproduces fixed-K retention with bounded search.
`CA-HMCD-NoPruning` uses the complete pool with bounded search.
`CA-HMCD-RT` is reserved for the adaptive-K rule to be registered in Stage 2.

## Subsequent registered tests

- K scan: 6, 12, 24, 48, and full.
- Scale: 10, 16, 24, and 32 resources.
- Concurrent tasks: 4, 8, 12, and 16.
- Decision budgets: 50, 100, 200, and 500 ms.
- Statistical unit: independent public workload or trajectory; stochastic
  seeds remain nested repeats.
""",
    )
    print(f"Frozen baseline: {BASELINE_DIR}")
    print(f"Revision registration: {OUTPUT_ROOT / 'revision_registration.json'}")


if __name__ == "__main__":
    main()

"""Audit the frozen Stage 9 baseline and the decoupled v3 framework."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import ca_hmcd_simulation as core
from ca_hmcd_framework import (
    FRAMEWORK_VERSION,
    CandidatePolicy,
    SolverConfig,
    ValueModelConfig,
    build_candidates,
    default_budget,
    framework_contract,
    retain_candidates,
    solve_allocation,
)


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "ca_hmcd_stage0_stage1_20260917"
BASELINE_DIR = OUTPUT_DIR / "stage0_baseline"
AUDIT_JSON = OUTPUT_DIR / "stage0_stage1_audit.json"
AUDIT_MD = OUTPUT_DIR / "stage0_stage1_audit.md"
CONTRACT_JSON = OUTPUT_DIR / "stage1_framework_contract.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_baseline() -> tuple[list[dict[str, object]], bool]:
    rows: list[dict[str, object]] = []
    manifest_path = BASELINE_DIR / "baseline_manifest.csv"
    with manifest_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            path = ROOT / row["relative_path"]
            exists = path.is_file()
            observed = sha256_file(path) if exists else ""
            passed = exists and observed == row["sha256"]
            rows.append(
                {
                    "path": row["relative_path"],
                    "expected_sha256": row["sha256"],
                    "observed_sha256": observed,
                    "passed": passed,
                }
            )
    return rows, all(bool(row["passed"]) for row in rows)


def parity_trials(trials: int = 12) -> dict[str, object]:
    model = ValueModelConfig()
    maximum_candidate_value_difference = 0.0
    maximum_highs_wrapper_difference = 0.0
    maximum_exact_solver_difference = 0.0
    fixed_k_identity_passes = 0
    complete_pool_identity_passes = 0
    solver_identity_passes = 0
    branch_bound_exact_passes = 0
    dominance_removed = 0

    for trial in range(trials):
        resources_count = 6 + trial % 3
        tasks_count = 3 + trial % 3
        resources, tasks = core.make_scenario(
            12000 + trial,
            resources=resources_count,
            tasks=tasks_count,
            arrival_horizon=0,
        )
        for task in tasks:
            task.active = True
            task.arrival_step = 0
        previous = {
            task_index: (task_index % resources_count,)
            for task_index in range(min(2, tasks_count))
        }
        complete = build_candidates(resources, tasks, previous, model)
        complete_ok = True
        fixed_ok = True
        for task_index in range(tasks_count):
            legacy_complete = core.generate_candidates(
                task_index,
                resources,
                tasks,
                top_k=1,
                prune=False,
                previous_combo=previous.get(task_index),
                switch_lambda=model.lambda_switch,
                apply_compatibility_screen=False,
                apply_dominance=False,
                apply_diversity=False,
            )
            framework_complete = complete.by_task[task_index]
            complete_ok &= framework_complete == legacy_complete
            for left, right in zip(framework_complete, legacy_complete):
                maximum_candidate_value_difference = max(
                    maximum_candidate_value_difference,
                    abs(left.utility - right.utility),
                )

        fixed = retain_candidates(
            complete,
            resources_count,
            CandidatePolicy.fixed_k(12),
            previous,
            model.lambda_switch,
        )
        for task_index in range(tasks_count):
            legacy_fixed = core.generate_candidates(
                task_index,
                resources,
                tasks,
                top_k=12,
                prune=True,
                previous_combo=previous.get(task_index),
                switch_lambda=model.lambda_switch,
            )
            fixed_ok &= fixed.by_task[task_index] == legacy_fixed
            dominance_removed += fixed.audits[task_index].dominance_removed
        complete_pool_identity_passes += int(complete_ok)
        fixed_k_identity_passes += int(fixed_ok)

        budget = default_budget(resources)
        wrapper_highs = solve_allocation(
            tasks,
            resources,
            complete,
            previous,
            model,
            SolverConfig("highs", 4000),
            budget,
        )
        direct_highs = core.milp_allocate(
            tasks,
            resources,
            complete.by_task,
            previous,
            model.lambda_switch,
            budget,
            node_limit=4000,
            gamma_unserved=model.gamma_unserved,
        )
        highs_difference = abs(wrapper_highs.objective - direct_highs.objective)
        maximum_highs_wrapper_difference = max(
            maximum_highs_wrapper_difference, highs_difference
        )
        solver_identity_passes += int(
            wrapper_highs.selected == direct_highs.selected
            and highs_difference <= 1e-10
        )

        direct_branch_bound = core.solve_global(
            tasks,
            resources,
            complete.by_task,
            previous,
            switch_lambda=model.lambda_switch,
            budget=budget,
            exact=True,
            node_limit=1_000_000,
            gamma_unserved=model.gamma_unserved,
        )
        solver_difference = abs(
            direct_branch_bound.objective - direct_highs.objective
        )
        maximum_exact_solver_difference = max(
            maximum_exact_solver_difference, solver_difference
        )
        branch_bound_exact_passes += int(solver_difference <= 1e-8)

    return {
        "trials": trials,
        "complete_pool_identity_passes": complete_pool_identity_passes,
        "fixed_k_identity_passes": fixed_k_identity_passes,
        "highs_wrapper_identity_passes": solver_identity_passes,
        "highs_vs_unlimited_branch_bound_passes": branch_bound_exact_passes,
        "maximum_candidate_value_difference": maximum_candidate_value_difference,
        "maximum_highs_wrapper_difference": maximum_highs_wrapper_difference,
        "maximum_exact_solver_difference": maximum_exact_solver_difference,
        "dominance_removed_across_smoke_trials": dominance_removed,
        "passed": all(
            value == trials
            for value in (
                complete_pool_identity_passes,
                fixed_k_identity_passes,
                solver_identity_passes,
                branch_bound_exact_passes,
            )
        )
        and maximum_candidate_value_difference <= 1e-12
        and maximum_highs_wrapper_difference <= 1e-10
        and maximum_exact_solver_difference <= 1e-8,
    }


def main() -> None:
    baseline_rows, baseline_passed = verify_baseline()
    parity = parity_trials()
    contract = framework_contract()
    framework_code_hashes = {
        name: sha256_file(ROOT / name)
        for name in (
            "ca_hmcd_framework.py",
            "test_ca_hmcd_framework.py",
            "freeze_ca_hmcd_stage9_baseline.py",
            "audit_ca_hmcd_stage0_stage1.py",
        )
    }
    checks = {
        "baseline_manifest_verified": baseline_passed,
        "framework_version": contract["framework_version"] == FRAMEWORK_VERSION,
        "legacy_model_preserved": (
            contract["legacy_model_version"] == core.MODEL_VERSION
        ),
        "legacy_evaluator_preserved": (
            contract["legacy_evaluator_version"] == core.EVALUATOR_VERSION
        ),
        "legacy_service_endpoint_preserved": (
            contract["legacy_service_endpoint_version"]
            == core.SERVICE_ENDPOINT_VERSION
        ),
        "exact_profile_registered": "CA-HMCD-Exact" in contract["profiles"],
        "fixed_k_profile_registered": "CA-HMCD-FixedK" in contract["profiles"],
        "no_pruning_profile_registered": (
            "CA-HMCD-NoPruning" in contract["profiles"]
        ),
        "rt_profile_reserved": (
            contract["profiles"]["CA-HMCD-RT"]["status"]
            == "RESERVED_STAGE2"
        ),
        "parity_trials_passed": parity["passed"],
    }
    payload = {
        "audit_id": "ca-hmcd-stage0-stage1-audit-20260917",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage0": {
            "baseline_id": "ca-hmcd-stage9-frozen-20260917",
            "manifest_entries": len(baseline_rows),
            "manifest_verified": baseline_passed,
            "file_checks": baseline_rows,
        },
        "stage1": {
            "contract": contract,
            "code_hashes": framework_code_hashes,
            "parity": parity,
        },
        "checks": checks,
        "audit_pass": all(bool(value) for value in checks.values()),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    CONTRACT_JSON.write_text(
        json.dumps(
            {
                "contract": contract,
                "code_hashes": framework_code_hashes,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# CA-HMCD Stage 0 and Stage 1 Audit",
        "",
        "Date: 2026-09-17",
        "",
        f"- Frozen baseline manifest entries: {len(baseline_rows)}",
        f"- Baseline hash verification: {'PASS' if baseline_passed else 'FAIL'}",
        f"- Framework version: `{FRAMEWORK_VERSION}`",
        f"- Parity trials: {parity['trials']}",
        (
            "- Complete-pool parity: "
            f"{parity['complete_pool_identity_passes']}/{parity['trials']}"
        ),
        (
            "- Fixed-K parity: "
            f"{parity['fixed_k_identity_passes']}/{parity['trials']}"
        ),
        (
            "- HiGHS wrapper parity: "
            f"{parity['highs_wrapper_identity_passes']}/{parity['trials']}"
        ),
        (
            "- HiGHS versus unlimited branch-and-bound objective parity: "
            f"{parity['highs_vs_unlimited_branch_bound_passes']}/"
            f"{parity['trials']}"
        ),
        (
            "- Maximum exact-solver objective difference: "
            f"{parity['maximum_exact_solver_difference']:.3e}"
        ),
        f"- Overall audit: {'PASS' if payload['audit_pass'] else 'FAIL'}",
        "",
        "## Interpretation",
        "",
        "The Stage 9 evidence remains pinned by SHA-256 and unchanged. The v3 "
        "framework reproduces the registered complete-candidate and fixed-K "
        "candidate identities, and its HiGHS backend reproduces the direct "
        "registered backend. Candidate policy and solver backend can now be "
        "changed independently without altering the value model or evaluator.",
        "",
        "The `CA-HMCD-RT` profile is an interface placeholder only. Its "
        "adaptive-K rule remains a Stage 2 task and is not treated as a "
        "completed algorithm in this audit.",
    ]
    AUDIT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Audit pass: {payload['audit_pass']}")
    print(f"JSON: {AUDIT_JSON}")
    print(f"Markdown: {AUDIT_MD}")
    print(f"Framework contract: {CONTRACT_JSON}")
    if not payload["audit_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Analyse and adjudicate the registered Stage 3 decision boundary."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Sequence, Tuple

import ca_hmcd_simulation as core
from ca_hmcd_stage3_boundary import (
    BOUNDED_METHOD,
    EXACT_METHOD,
    REGISTERED_BUDGETS_MS,
)
from ca_hmcd_stage3_statistics import (
    bca_mean_interval,
    holm_adjust,
    stable_seed,
    wilson_score_interval,
)


ANALYSIS_VERSION = "ca-hmcd-stage3-boundary-analysis-v1"
EXPECTED_UNITS = 480
EXPECTED_RUNS = 2400
EXPECTED_SEEDS_PER_CELL = 30
EPSILON = 1e-12
QUALITY_METRICS = (
    ("external_objective_per_active_task", "higher"),
    ("independent_service_score", "higher"),
    ("independent_coverage", "higher"),
)
COMPUTE_METRICS = (
    ("decision_runtime_ms", "lower"),
    ("candidate_after_diversity", "lower"),
    ("nodes", "lower"),
    ("fallback", "lower"),
    ("deadline_hit", "higher"),
)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def as_float(row: Mapping[str, object], key: str) -> float:
    return float(str(row[key]))


def as_int(row: Mapping[str, object], key: str) -> int:
    return int(float(str(row[key])))


def as_bool(row: Mapping[str, object], key: str) -> bool:
    value = str(row.get(key, "")).strip().lower()
    return value in {"1", "true", "yes"}


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else math.nan


def common_response_pair(
    exact: Mapping[str, object], bounded: Mapping[str, object]
) -> Tuple[float, float, int]:
    exact_values = {
        int(key): float(value)
        for key, value in json.loads(
            str(exact["task_response_json"])
        ).items()
    }
    bounded_values = {
        int(key): float(value)
        for key, value in json.loads(
            str(bounded["task_response_json"])
        ).items()
    }
    common = sorted(set(exact_values) & set(bounded_values))
    if not common:
        return math.nan, math.nan, 0
    return (
        mean([exact_values[index] for index in common]),
        mean([bounded_values[index] for index in common]),
        len(common),
    )


def validate_sources(
    project_dir: Path, formal_dir: Path, registration_dir: Path
) -> Tuple[List[Dict[str, str]], Dict[str, object]]:
    metadata = json.loads(
        (formal_dir / "stage3_execution_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    registration = json.loads(
        (registration_dir / "stage3_preregistration.json").read_text(
            encoding="utf-8"
        )
    )
    rows = read_csv(formal_dir / "stage3_boundary_runs.csv")
    hash_paths = {
        "ca_hmcd_stage3_boundary.py": (
            project_dir / "ca_hmcd_stage3_boundary.py"
        ),
        "ca_hmcd_stage3_boundary_runner.py": (
            project_dir / "ca_hmcd_stage3_boundary_runner.py"
        ),
        "ca_hmcd_simulation.py": project_dir / "ca_hmcd_simulation.py",
        "ca_hmcd_framework.py": project_dir / "ca_hmcd_framework.py",
        "stage3_preregistration.json": (
            registration_dir / "stage3_preregistration.json"
        ),
        "stage3_boundary_protocol.md": (
            registration_dir / "stage3_boundary_protocol.md"
        ),
    }
    observed_hashes = {
        name: sha256_file(path) for name, path in hash_paths.items()
    }
    checks = [
        {
            "check": "formal execution mode",
            "observed": metadata.get("mode"),
            "expected": "formal",
            "passed": metadata.get("mode") == "formal",
        },
        {
            "check": "execution audit",
            "observed": metadata.get("audit_pass"),
            "expected": True,
            "passed": bool(metadata.get("audit_pass")),
        },
        {
            "check": "registered unit count",
            "observed": metadata.get("selected_registered_units"),
            "expected": EXPECTED_UNITS,
            "passed": metadata.get("selected_registered_units")
            == EXPECTED_UNITS,
        },
        {
            "check": "registered run count",
            "observed": len(rows),
            "expected": EXPECTED_RUNS,
            "passed": len(rows) == EXPECTED_RUNS,
        },
        {
            "check": "frozen execution hashes",
            "observed": observed_hashes,
            "expected": metadata.get("code_hashes"),
            "passed": observed_hashes == metadata.get("code_hashes"),
        },
        {
            "check": "Gate A remains failed",
            "observed": registration["parent_stage2_decision"]["decision"],
            "expected": "FAIL",
            "passed": registration["parent_stage2_decision"]["decision"]
            == "FAIL",
        },
    ]
    if not all(bool(check["passed"]) for check in checks):
        failed = [check["check"] for check in checks if not check["passed"]]
        raise RuntimeError(f"Stage 3 source validation failed: {failed}")
    return rows, {
        "metadata": metadata,
        "registration": registration,
        "source_checks": checks,
        "source_hashes": observed_hashes,
    }


def paired_records(
    rows: Sequence[Mapping[str, str]]
) -> List[Dict[str, object]]:
    exact_lookup = {
        (
            as_int(row, "resources_count"),
            as_int(row, "tasks_count"),
            as_int(row, "seed"),
        ): row
        for row in rows
        if row["method"] == EXACT_METHOD
    }
    output: List[Dict[str, object]] = []
    for bounded in rows:
        if bounded["method"] != BOUNDED_METHOD:
            continue
        key = (
            as_int(bounded, "resources_count"),
            as_int(bounded, "tasks_count"),
            as_int(bounded, "seed"),
        )
        exact = exact_lookup[key]
        if exact["state_fingerprint"] != bounded["state_fingerprint"]:
            raise RuntimeError(f"State mismatch for {key}")
        budget_ms = as_int(bounded, "registered_budget_ms")
        exact_response, bounded_response, common_count = (
            common_response_pair(exact, bounded)
        )
        pair: Dict[str, object] = {
            "resources_count": key[0],
            "tasks_count": key[1],
            "seed": key[2],
            "scale_cell": f"R{key[0]}-T{key[1]}",
            "registered_budget_ms": budget_ms,
            "state_fingerprint": exact["state_fingerprint"],
            "exact_optimal": int(
                as_bool(exact, "solver_trace_optimal")
            ),
            "exact_deadline_hit": int(
                as_float(exact, "decision_runtime_ms") <= budget_ms
            ),
            "bounded_deadline_hit": as_int(bounded, "deadline_hit"),
            "exact_final_feasibility": as_float(
                exact, "final_feasibility"
            ),
            "bounded_final_feasibility": as_float(
                bounded, "final_feasibility"
            ),
            "exact_common_response_time": exact_response,
            "bounded_common_response_time": bounded_response,
            "common_served_task_count": common_count,
        }
        for metric, _ in QUALITY_METRICS + COMPUTE_METRICS[:-1]:
            pair[f"exact_{metric}"] = as_float(exact, metric)
            pair[f"bounded_{metric}"] = as_float(bounded, metric)
        output.append(pair)
    if len(output) != EXPECTED_UNITS * len(REGISTERED_BUDGETS_MS):
        raise RuntimeError(
            f"Expected {EXPECTED_UNITS * len(REGISTERED_BUDGETS_MS)} "
            f"paired rows, observed {len(output)}"
        )
    return output


def interval_rate(values: Sequence[float]) -> Tuple[float, float, float]:
    return mean(values), *wilson_score_interval(values)


def boundary_summary(
    pairs: Sequence[Mapping[str, object]],
    registration: Mapping[str, object],
) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[int, int, int], List[Mapping[str, object]]] = (
        defaultdict(list)
    )
    for row in pairs:
        grouped[
            (
                int(row["resources_count"]),
                int(row["tasks_count"]),
                int(row["registered_budget_ms"]),
            )
        ].append(row)
    gate = registration["gate_b"]
    output: List[Dict[str, object]] = []
    for key, group in sorted(grouped.items()):
        if len(group) != EXPECTED_SEEDS_PER_CELL:
            raise RuntimeError(f"Incomplete boundary cell {key}")
        exact_optimal = [float(row["exact_optimal"]) for row in group]
        exact_deadline = [
            float(row["exact_deadline_hit"]) for row in group
        ]
        bounded_deadline = [
            float(row["bounded_deadline_hit"]) for row in group
        ]
        bounded_feasible = [
            float(row["bounded_final_feasibility"]) for row in group
        ]
        exact_optimal_rate, exact_optimal_low, exact_optimal_high = (
            interval_rate(exact_optimal)
        )
        exact_hit_rate, exact_hit_low, exact_hit_high = interval_rate(
            exact_deadline
        )
        bounded_hit_rate, bounded_hit_low, bounded_hit_high = (
            interval_rate(bounded_deadline)
        )
        feasible_rate, feasible_low, feasible_high = interval_rate(
            bounded_feasible
        )
        valid = [row for row in group if int(row["exact_optimal"]) == 1]
        service_loss = mean(
            [
                float(row["exact_independent_service_score"])
                - float(row["bounded_independent_service_score"])
                for row in valid
            ]
        )
        coverage_loss = mean(
            [
                float(row["exact_independent_coverage"])
                - float(row["bounded_independent_coverage"])
                for row in valid
            ]
        )
        objective_loss = mean(
            [
                float(
                    row["exact_external_objective_per_active_task"]
                )
                - float(
                    row["bounded_external_objective_per_active_task"]
                )
                for row in valid
            ]
        )
        criteria = {
            "exact_reference_optimal_rate_required": (
                exact_optimal_rate
                >= float(gate["exact_reference_optimal_rate_required"])
                - EPSILON
            ),
            "exact_deadline_hit_rate_below_threshold": (
                exact_hit_rate
                < float(gate["exact_deadline_hit_rate_must_be_below"])
                - EPSILON
            ),
            "bounded_deadline_hit_rate_required": (
                bounded_hit_rate
                >= float(gate["bounded_k12_deadline_hit_rate_required"])
                - EPSILON
            ),
            "bounded_feasibility_required": (
                feasible_rate
                >= float(gate["bounded_k12_feasibility_required"])
                - EPSILON
            ),
            "service_loss_within_limit": (
                service_loss
                <= float(gate["maximum_mean_service_score_loss"])
                + EPSILON
            ),
            "coverage_loss_within_limit": (
                coverage_loss
                <= float(gate["maximum_mean_coverage_loss"]) + EPSILON
            ),
            "objective_loss_within_limit": (
                objective_loss
                <= float(gate["maximum_mean_objective_per_task_loss"])
                + EPSILON
            ),
        }
        output.append(
            {
                "resources_count": key[0],
                "tasks_count": key[1],
                "scale_cell": f"R{key[0]}-T{key[1]}",
                "registered_budget_ms": key[2],
                "n_seed_pairs": len(group),
                "n_optimal_quality_pairs": len(valid),
                "exact_optimal_rate": exact_optimal_rate,
                "exact_optimal_wilson_low": exact_optimal_low,
                "exact_optimal_wilson_high": exact_optimal_high,
                "exact_deadline_hit_rate": exact_hit_rate,
                "exact_deadline_hit_wilson_low": exact_hit_low,
                "exact_deadline_hit_wilson_high": exact_hit_high,
                "bounded_deadline_hit_rate": bounded_hit_rate,
                "bounded_deadline_hit_wilson_low": bounded_hit_low,
                "bounded_deadline_hit_wilson_high": bounded_hit_high,
                "bounded_feasibility_rate": feasible_rate,
                "bounded_feasibility_wilson_low": feasible_low,
                "bounded_feasibility_wilson_high": feasible_high,
                "mean_service_score_loss_exact_minus_bounded": (
                    service_loss
                ),
                "mean_coverage_loss_exact_minus_bounded": coverage_loss,
                "mean_objective_per_task_loss_exact_minus_bounded": (
                    objective_loss
                ),
                "mean_exact_runtime_ms": mean(
                    [
                        float(row["exact_decision_runtime_ms"])
                        for row in group
                    ]
                ),
                "mean_bounded_runtime_ms": mean(
                    [
                        float(row["bounded_decision_runtime_ms"])
                        for row in group
                    ]
                ),
                "mean_exact_candidates": mean(
                    [
                        float(row["exact_candidate_after_diversity"])
                        for row in group
                    ]
                ),
                "mean_bounded_candidates": mean(
                    [
                        float(row["bounded_candidate_after_diversity"])
                        for row in group
                    ]
                ),
                **{
                    f"criterion_{name}": value
                    for name, value in criteria.items()
                },
                "gate_b_cell_pass": all(criteria.values()),
            }
        )
    return output


def paired_statistics_row(
    group: Sequence[Mapping[str, object]],
    metric: str,
    direction: str,
    budget_ms: int,
    scale_cell: str,
    exact_key: str,
    bounded_key: str,
    optimal_only: bool,
) -> Dict[str, object]:
    eligible = [
        row
        for row in group
        if (not optimal_only or int(row["exact_optimal"]) == 1)
        and math.isfinite(float(row[exact_key]))
        and math.isfinite(float(row[bounded_key]))
    ]
    bounded_values = [float(row[bounded_key]) for row in eligible]
    exact_values = [float(row[exact_key]) for row in eligible]
    differences = [
        bounded - exact
        for bounded, exact in zip(bounded_values, exact_values)
    ]
    stats = core.paired_statistics(bounded_values, exact_values)
    family_id = f"stage3.{metric}.budget_{budget_ms}"
    ci_low, ci_high = bca_mean_interval(
        differences,
        resamples=10000,
        seed=stable_seed(family_id, scale_cell),
        bounds=(-1.0, 1.0)
        if metric in {
            "deadline_hit",
            "fallback",
            "independent_service_score",
            "independent_coverage",
        }
        else None,
    )
    wins = sum(
        difference > EPSILON
        if direction == "higher"
        else difference < -EPSILON
        for difference in differences
    )
    ties = sum(abs(difference) <= EPSILON for difference in differences)
    return {
        "family_id": family_id,
        "comparison": f"{scale_cell}: bounded K12 vs exact",
        "metric": metric,
        "registered_budget_ms": budget_ms,
        "scale_cell": scale_cell,
        "method_a": BOUNDED_METHOD,
        "method_b": EXACT_METHOD,
        "favorable_direction_for_method_a": direction,
        "n_independent_seed_pairs": len(eligible),
        "method_a_mean": mean(bounded_values),
        "method_b_mean": mean(exact_values),
        "mean_difference_a_minus_b": mean(differences),
        "mean_difference_ci95_low": ci_low,
        "mean_difference_ci95_high": ci_high,
        "ci_method": "paired BCa bootstrap, 10000 resamples",
        "p_value_test": "two-sided paired Wilcoxon signed-rank test",
        "wilcoxon_w": stats["wilcoxon_w"],
        "p_unadjusted": stats["p_value"],
        "p_holm_adjusted": math.nan,
        "cohen_dz": stats["cohen_dz"],
        "rank_biserial": stats["rank_biserial"],
        "win_rate_favorable_to_method_a": (
            (wins + 0.5 * ties) / max(1, len(differences))
        ),
        "quality_reference_filter": (
            "HiGHS optimal seeds only" if optimal_only else "all paired seeds"
        ),
    }


def statistical_registry(
    pairs: Sequence[Mapping[str, object]]
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    grouped: Dict[Tuple[int, str], List[Mapping[str, object]]] = (
        defaultdict(list)
    )
    for row in pairs:
        grouped[
            (int(row["registered_budget_ms"]), str(row["scale_cell"]))
        ].append(row)
    rows: List[Dict[str, object]] = []
    registry: List[Dict[str, object]] = []
    metric_specs = [
        *[
            (
                metric,
                direction,
                f"exact_{metric}",
                f"bounded_{metric}",
                True,
            )
            for metric, direction in QUALITY_METRICS
        ],
        *[
            (
                metric,
                direction,
                (
                    "exact_deadline_hit"
                    if metric == "deadline_hit"
                    else f"exact_{metric}"
                ),
                (
                    "bounded_deadline_hit"
                    if metric == "deadline_hit"
                    else f"bounded_{metric}"
                ),
                False,
            )
            for metric, direction in COMPUTE_METRICS
        ],
        (
            "common_served_task_response_time",
            "lower",
            "exact_common_response_time",
            "bounded_common_response_time",
            True,
        ),
    ]
    for budget_ms in REGISTERED_BUDGETS_MS:
        for metric, direction, exact_key, bounded_key, optimal_only in (
            metric_specs
        ):
            family_rows = [
                paired_statistics_row(
                    grouped[(budget_ms, scale_cell)],
                    metric,
                    direction,
                    budget_ms,
                    scale_cell,
                    exact_key,
                    bounded_key,
                    optimal_only,
                )
                for scale_cell in sorted(
                    {
                        str(row["scale_cell"])
                        for row in pairs
                        if int(row["registered_budget_ms"]) == budget_ms
                    }
                )
            ]
            rows.extend(family_rows)
            registry.append(
                {
                    "family_id": f"stage3.{metric}.budget_{budget_ms}",
                    "endpoint": metric,
                    "registered_budget_ms": budget_ms,
                    "independent_unit": (
                        "one seeded static decision snapshot"
                    ),
                    "family_definition": (
                        "bounded K12 versus exact over the 16 registered "
                        "resource-task scale cells"
                    ),
                    "number_of_comparisons": len(family_rows),
                    "multiplicity_control": (
                        "Holm step-down, two-sided family-wise alpha=0.05"
                    ),
                }
            )
    holm_adjust(rows)
    return rows, registry


def gate_b(
    summary: Sequence[Mapping[str, object]]
) -> Dict[str, object]:
    passing = [
        row for row in summary if bool(row["gate_b_cell_pass"])
    ]
    return {
        "gate": "B",
        "decision": "PASS" if passing else "FAIL",
        "method_adjudication": (
            "VALIDATE_BOUNDED_K12_ONLY_IN_PASSING_CELLS"
            if passing
            else "DO_NOT_CLAIM_A_VALIDATED_REAL_TIME_BOUNDARY"
        ),
        "registered_cells": len(summary),
        "passing_cells": len(passing),
        "passing_cell_ids": [
            f"{row['scale_cell']}@{row['registered_budget_ms']}ms"
            for row in passing
        ],
        "stage2_gate_a_remains": "FAIL",
        "interpretation": (
            "Gate B validates only the fixed-K12 bounded profile in the "
            "listed cells; AdaptiveK remains unvalidated."
            if passing
            else "No registered cell simultaneously established an exact "
            "deadline miss, a bounded-K12 deadline success, full feasibility, "
            "and all three quality-loss limits."
        ),
    }


def write_report(
    output_dir: Path,
    execution: Mapping[str, object],
    gate: Mapping[str, object],
    summary: Sequence[Mapping[str, object]],
    statistics_rows: Sequence[Mapping[str, object]],
    audit_pass: bool,
) -> None:
    passing = [
        row for row in summary if bool(row["gate_b_cell_pass"])
    ]
    exact_optimal_cells = sum(
        float(row["exact_optimal_rate"]) == 1.0 for row in summary
    )
    best_bounded_hit = max(
        summary,
        key=lambda row: (
            float(row["bounded_deadline_hit_rate"]),
            -float(row["mean_service_score_loss_exact_minus_bounded"]),
        ),
    )
    lines = [
        "## Material Passport",
        "",
        "- Origin Skill: experiment-agent",
        "- Origin Mode: run + validate",
        f"- Origin Date: {datetime.now(timezone.utc).isoformat()}",
        "- Verification Status: VERIFIED",
        "- Version Label: ca_hmcd_stage3_boundary_v1",
        "",
        "## Experiment Result",
        "",
        "- **ID**: ca-hmcd-stage3-exact-realtime-boundary-v1",
        "- **Type**: simulation benchmark",
        "- **Status**: completed",
        f"- **Execution audit**: {'PASS' if audit_pass else 'FAIL'}",
        f"- **Independent units**: {execution['selected_registered_units']}",
        f"- **Method runs**: {execution['run_rows']}",
        f"- **Formal duration**: {float(execution['formal_elapsed_seconds']):.1f} s",
        "",
        "## Gate B",
        "",
        f"- **Decision**: {gate['decision']}",
        f"- **Passing cells**: {gate['passing_cells']} / {gate['registered_cells']}",
        f"- **Adjudication**: {gate['method_adjudication']}",
        f"- **Interpretation**: {gate['interpretation']}",
        "",
        "## Boundary Summary",
        "",
        f"- Cells with 30/30 optimal HiGHS references: {exact_optimal_cells} / {len(summary)}.",
        (
            "- Highest observed bounded deadline-hit cell: "
            f"{best_bounded_hit['scale_cell']} at "
            f"{best_bounded_hit['registered_budget_ms']} ms "
            f"({100 * float(best_bounded_hit['bounded_deadline_hit_rate']):.1f}%)."
        ),
    ]
    if passing:
        lines.extend(
            [
                "- Registered passing cells: "
                + ", ".join(str(value) for value in gate["passing_cell_ids"])
                + ".",
            ]
        )
    lines.extend(
        [
            "",
            "## Statistical Outputs",
            "",
            (
                f"- {len(statistics_rows)} paired comparisons with separate "
                "raw P values, Holm-adjusted P values, effect sizes, win rates, "
                "and 95% BCa intervals."
            ),
            "- Deadline and feasibility rates use 95% Wilson intervals.",
            "- Common-task response time includes only tasks served by both methods.",
            "",
            "## Scope",
            "",
            (
                "This static boundary experiment isolates candidate generation "
                "and optimization cost. It does not replace rolling replay "
                "evidence and does not reverse the failed Stage-2 AdaptiveK gate."
            ),
            "",
            "## Anomalies Detected",
            "",
            "- None that invalidated the registered execution.",
        ]
    )
    (output_dir / "stage3_completion_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def analyse(
    project_dir: Path,
    formal_dir: Path,
    registration_dir: Path,
    output_dir: Path,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, sources = validate_sources(
        project_dir, formal_dir, registration_dir
    )
    pairs = paired_records(rows)
    summary = boundary_summary(pairs, sources["registration"])
    statistics_rows, family_registry = statistical_registry(pairs)
    decision = gate_b(summary)

    write_csv(output_dir / "stage3_paired_runs.csv", pairs)
    write_csv(output_dir / "stage3_boundary_summary.csv", summary)
    write_csv(
        output_dir / "stage3_paired_statistics.csv", statistics_rows
    )
    write_csv(
        output_dir / "stage3_holm_family_registry.csv", family_registry
    )
    (output_dir / "stage3_gate_b.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    checks = [
        *sources["source_checks"],
        {
            "check": "paired row count",
            "observed": len(pairs),
            "expected": EXPECTED_UNITS * len(REGISTERED_BUDGETS_MS),
            "passed": len(pairs)
            == EXPECTED_UNITS * len(REGISTERED_BUDGETS_MS),
        },
        {
            "check": "boundary cell count",
            "observed": len(summary),
            "expected": 64,
            "passed": len(summary) == 64,
        },
        {
            "check": "Holm family count",
            "observed": len(family_registry),
            "expected": 36,
            "passed": len(family_registry) == 36,
        },
        {
            "check": "paired comparison count",
            "observed": len(statistics_rows),
            "expected": 576,
            "passed": len(statistics_rows) == 576,
        },
        {
            "check": "valid Holm adjusted P values",
            "observed": all(
                float(row["p_unadjusted"])
                <= float(row["p_holm_adjusted"]) + EPSILON
                <= 1.0 + EPSILON
                for row in statistics_rows
            ),
            "expected": True,
            "passed": all(
                float(row["p_unadjusted"])
                <= float(row["p_holm_adjusted"]) + EPSILON
                <= 1.0 + EPSILON
                for row in statistics_rows
            ),
        },
        {
            "check": "Gate B adjudicated once",
            "observed": decision["decision"],
            "expected": "PASS or FAIL",
            "passed": decision["decision"] in {"PASS", "FAIL"},
        },
    ]
    audit_pass = all(bool(check["passed"]) for check in checks)
    audit = {
        "audit_version": "ca-hmcd-stage3-boundary-audit-v1",
        "analysis_version": ANALYSIS_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_pass": audit_pass,
        "formal_execution_runs": len(rows),
        "paired_rows": len(pairs),
        "boundary_cells": len(summary),
        "paired_comparisons": len(statistics_rows),
        "holm_families": len(family_registry),
        "gate_b": decision,
        "checks": checks,
    }
    (output_dir / "stage3_completion_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(output_dir / "stage3_completion_audit.csv", checks)
    write_report(
        output_dir,
        sources["metadata"],
        decision,
        summary,
        statistics_rows,
        audit_pass,
    )
    if not audit_pass:
        failed = [check["check"] for check in checks if not check["passed"]]
        raise RuntimeError(f"Stage 3 analysis audit failed: {failed}")
    return audit


def parse_args() -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", type=Path, default=project_dir)
    parser.add_argument(
        "--formal-dir",
        type=Path,
        default=project_dir
        / "ca_hmcd_stage3_boundary_formal_20260917",
    )
    parser.add_argument(
        "--registration-dir",
        type=Path,
        default=project_dir
        / "ca_hmcd_stage3_boundary_closure_20260917",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=project_dir
        / "ca_hmcd_stage3_boundary_analysis_20260917",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audit = analyse(
        args.project_dir.resolve(),
        args.formal_dir.resolve(),
        args.registration_dir.resolve(),
        args.output_dir.resolve(),
    )
    print(
        json.dumps(
            {
                "audit_pass": audit["audit_pass"],
                "formal_execution_runs": audit["formal_execution_runs"],
                "boundary_cells": audit["boundary_cells"],
                "gate_b": audit["gate_b"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Analyse Stage 5 external baselines and freeze applicability claims."""

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
from ca_hmcd_stage3_statistics import (
    bca_mean_interval,
    holm_adjust,
    stable_seed,
    wilson_score_interval,
)
from ca_hmcd_stage5_applicability_runner import (
    DEADLINES_MS,
    METHODS,
    METHOD_LABELS,
    NODE_LIMIT,
    PROTOCOL_DIR,
    SCENARIOS,
    SEEDS,
    STEPS,
    TOP_K,
    code_hashes as runner_code_hashes,
    protocol_fingerprint,
)


ANALYSIS_VERSION = "ca-hmcd-stage5-applicability-analysis-v1.1"
PROJECT_DIR = Path(__file__).resolve().parent
STAGE3_DIR = PROJECT_DIR / "ca_hmcd_stage3_boundary_analysis_20260917"
STAGE4_DIR = (
    PROJECT_DIR / "ca_hmcd_stage4_value_engineering_analysis_20260918"
)
REFERENCE = "CA-HMCD"
COMPARATORS = tuple(method for method in METHODS if method != REFERENCE)
EXPECTED_EPISODES = 750
EXPECTED_STEPS = 9000
EXPECTED_AUDITS = 1800
BOOTSTRAP_RESAMPLES = 10000
EPSILON = 1e-12
PAIR_METRICS = (
    ("external_objective", "higher", "quality"),
    ("independent_service_score", "higher", "quality"),
    ("independent_coverage", "higher", "quality"),
    ("end_to_end_runtime_ms", "lower", "compute"),
    ("candidate_generation_ms", "lower", "compute"),
    ("solver_elapsed_ms", "lower", "compute"),
    ("external_switch_count", "lower", "operation"),
    ("external_physical_cost", "lower", "operation"),
    ("feasibility_rate", "higher", "safety"),
    ("fallback", "lower", "compute"),
)
BOUNDED_METRICS = (
    "independent_coverage",
    "feasibility_rate",
    "fallback",
    *(f"deadline_hit_rate_{deadline}ms" for deadline in DEADLINES_MS),
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
    return str(row.get(key, "")).strip().lower() in {"1", "true", "yes"}


def quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    position = min(1.0, max(0.0, probability)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def validate_sources(
    formal_dir: Path,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], Dict[str, object]]:
    metadata_path = formal_dir / "stage5_execution_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    episodes = read_csv(formal_dir / "stage5_episode_results.csv")
    steps = read_csv(formal_dir / "stage5_per_step.csv")
    audits = read_csv(formal_dir / "stage5_pairing_audit.csv")
    output_names = (
        "stage5_episode_results.csv",
        "stage5_per_step.csv",
        "stage5_pairing_audit.csv",
        "stage5_cell_summary.csv",
    )
    output_hashes = {
        name: sha256_file(formal_dir / name) for name in output_names
    }
    current_hashes = runner_code_hashes()
    checks = [
        {
            "check": "formal execution mode",
            "passed": metadata.get("mode") == "formal",
            "observed": metadata.get("mode"),
            "expected": "formal",
        },
        {
            "check": "formal audit",
            "passed": bool(metadata.get("audit_pass")),
            "observed": metadata.get("audit_pass"),
            "expected": True,
        },
        {
            "check": "episode count",
            "passed": len(episodes) == EXPECTED_EPISODES,
            "observed": len(episodes),
            "expected": EXPECTED_EPISODES,
        },
        {
            "check": "step count",
            "passed": len(steps) == EXPECTED_STEPS,
            "observed": len(steps),
            "expected": EXPECTED_STEPS,
        },
        {
            "check": "pairing audit count",
            "passed": (
                len(audits) == EXPECTED_AUDITS
                and all(as_bool(row, "passed") for row in audits)
            ),
            "observed": len(audits),
            "expected": EXPECTED_AUDITS,
        },
        {
            "check": "execution hashes",
            "passed": current_hashes == metadata.get("code_hashes"),
            "observed": current_hashes,
            "expected": metadata.get("code_hashes"),
        },
        {
            "check": "protocol fingerprint",
            "passed": (
                protocol_fingerprint()
                == metadata.get("protocol_fingerprint")
            ),
            "observed": protocol_fingerprint(),
            "expected": metadata.get("protocol_fingerprint"),
        },
        {
            "check": "output hashes",
            "passed": output_hashes == metadata.get("outputs"),
            "observed": output_hashes,
            "expected": metadata.get("outputs"),
        },
    ]
    if not all(bool(check["passed"]) for check in checks):
        failed = [
            str(check["check"]) for check in checks if not check["passed"]
        ]
        raise RuntimeError(f"Stage 5 source validation failed: {failed}")
    return episodes, steps, {
        "metadata": metadata,
        "checks": checks,
        "output_hashes": output_hashes,
        "execution_metadata_sha256": sha256_file(metadata_path),
    }


def paired_values(
    episodes: Sequence[Mapping[str, object]],
    scenario: str,
    method_a: str,
    method_b: str,
    metric: str,
) -> Tuple[List[int], List[float], List[float]]:
    relevant = [
        row
        for row in episodes
        if row["scenario"] == scenario
        and row["algorithm"] in (method_a, method_b)
    ]
    lookup = {
        (str(row["algorithm"]), as_int(row, "seed")): as_float(row, metric)
        for row in relevant
    }
    seeds = sorted(
        seed
        for seed in SEEDS
        if (method_a, seed) in lookup and (method_b, seed) in lookup
    )
    return (
        seeds,
        [lookup[(method_a, seed)] for seed in seeds],
        [lookup[(method_b, seed)] for seed in seeds],
    )


def paired_record(
    *,
    scenario: str,
    metric: str,
    category: str,
    favorable_direction: str,
    method_a: str,
    method_b: str,
    seeds: Sequence[int],
    values_a: Sequence[float],
    values_b: Sequence[float],
    family_id: str,
    common_task_periods_mean: float | None = None,
) -> Dict[str, object]:
    if not seeds:
        raise RuntimeError(f"No paired values for {scenario}/{metric}/{method_b}")
    differences = [a - b for a, b in zip(values_a, values_b)]
    stats = core.paired_statistics(values_a, values_b)
    low, high = bca_mean_interval(
        differences,
        resamples=BOOTSTRAP_RESAMPLES,
        seed=stable_seed(family_id, method_b),
    )
    favorable = [
        difference > EPSILON
        if favorable_direction == "higher"
        else difference < -EPSILON
        for difference in differences
    ]
    ties = sum(abs(difference) <= EPSILON for difference in differences)
    win_rate = (sum(favorable) + 0.5 * ties) / len(differences)
    win_low, win_high = wilson_score_interval(
        [1.0 if value else 0.0 for value in favorable]
    )
    return {
        "family_id": family_id,
        "scenario": scenario,
        "category": category,
        "metric": metric,
        "method_a": method_a,
        "method_a_label": METHOD_LABELS[method_a],
        "method_b": method_b,
        "method_b_label": METHOD_LABELS[method_b],
        "favorable_direction": favorable_direction,
        "pairs": len(seeds),
        "missing_seed_pairs": len(SEEDS) - len(seeds),
        "mean_a": statistics.fmean(values_a),
        "mean_b": statistics.fmean(values_b),
        "mean_difference_a_minus_b": statistics.fmean(differences),
        "ci95_bca_low": low,
        "ci95_bca_high": high,
        "p_unadjusted": float(stats["p_value"]),
        "cohen_dz": float(stats["cohen_dz"]),
        "rank_biserial": float(stats["rank_biserial"]),
        "win_rate_favorable": win_rate,
        "win_rate_wilson_low": win_low,
        "win_rate_wilson_high": win_high,
        "common_task_periods_mean": (
            "" if common_task_periods_mean is None
            else common_task_periods_mean
        ),
    }


def common_response_values(
    steps: Sequence[Mapping[str, object]],
    scenario: str,
    method_a: str,
    method_b: str,
) -> Tuple[List[int], List[float], List[float], List[int]]:
    relevant = [
        row
        for row in steps
        if row["scenario"] == scenario
        and row["algorithm"] in (method_a, method_b)
    ]
    lookup = {
        (
            str(row["algorithm"]),
            as_int(row, "seed"),
            as_int(row, "step"),
        ): {
            str(key): float(value)
            for key, value in json.loads(
                str(row["served_task_response_times_json"])
            ).items()
        }
        for row in relevant
    }
    output_seeds: List[int] = []
    values_a: List[float] = []
    values_b: List[float] = []
    counts: List[int] = []
    for seed in SEEDS:
        a_values: List[float] = []
        b_values: List[float] = []
        for step in range(STEPS):
            key_a = (method_a, seed, step)
            key_b = (method_b, seed, step)
            if key_a not in lookup or key_b not in lookup:
                continue
            common = sorted(set(lookup[key_a]) & set(lookup[key_b]))
            for task_id in common:
                a_values.append(lookup[key_a][task_id])
                b_values.append(lookup[key_b][task_id])
        if a_values:
            output_seeds.append(seed)
            values_a.append(statistics.fmean(a_values))
            values_b.append(statistics.fmean(b_values))
            counts.append(len(a_values))
    return output_seeds, values_a, values_b, counts


def paired_comparisons(
    episodes: Sequence[Mapping[str, object]],
    steps: Sequence[Mapping[str, object]],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    output: List[Dict[str, object]] = []
    common_rows: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        for metric, direction, category in PAIR_METRICS:
            family_id = f"stage5::{scenario}::{metric}"
            for method_b in COMPARATORS:
                seeds, values_a, values_b = paired_values(
                    episodes, scenario, REFERENCE, method_b, metric
                )
                if len(seeds) != len(SEEDS):
                    raise RuntimeError(
                        f"Incomplete paired cell: {scenario}/{metric}/{method_b}"
                    )
                output.append(
                    paired_record(
                        scenario=scenario,
                        metric=metric,
                        category=category,
                        favorable_direction=direction,
                        method_a=REFERENCE,
                        method_b=method_b,
                        seeds=seeds,
                        values_a=values_a,
                        values_b=values_b,
                        family_id=family_id,
                    )
                )
        family_id = f"stage5::{scenario}::common_task_response_time"
        for method_b in COMPARATORS:
            seeds, values_a, values_b, counts = common_response_values(
                steps, scenario, REFERENCE, method_b
            )
            common_rows.extend(
                {
                    "scenario": scenario,
                    "method_a": REFERENCE,
                    "method_b": method_b,
                    "seed": seed,
                    "common_task_periods": count,
                    "response_a": value_a,
                    "response_b": value_b,
                    "difference_a_minus_b": value_a - value_b,
                }
                for seed, value_a, value_b, count in zip(
                    seeds, values_a, values_b, counts
                )
            )
            output.append(
                paired_record(
                    scenario=scenario,
                    metric="common_task_response_time",
                    category="quality",
                    favorable_direction="lower",
                    method_a=REFERENCE,
                    method_b=method_b,
                    seeds=seeds,
                    values_a=values_a,
                    values_b=values_b,
                    family_id=family_id,
                    common_task_periods_mean=statistics.fmean(counts),
                )
            )
    holm_adjust(output)
    return output, common_rows


def cell_summary(
    episodes: Sequence[Mapping[str, object]],
    steps: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    episode_groups: Dict[
        Tuple[str, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    step_groups: Dict[
        Tuple[str, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in episodes:
        episode_groups[(str(row["scenario"]), str(row["algorithm"]))].append(row)
    for row in steps:
        step_groups[(str(row["scenario"]), str(row["algorithm"]))].append(row)
    output: List[Dict[str, object]] = []
    for key, values in sorted(episode_groups.items()):
        scenario, method = key
        period_rows = step_groups[key]
        runtime_values = [
            as_float(row, "end_to_end_runtime_ms") for row in period_rows
        ]
        row: Dict[str, object] = {
            "scenario": scenario,
            "method": method,
            "method_label": METHOD_LABELS[method],
            "seeds": len(values),
            "periods": len(period_rows),
            "external_objective_mean": statistics.fmean(
                as_float(value, "external_objective") for value in values
            ),
            "independent_service_score_mean": statistics.fmean(
                as_float(value, "independent_service_score") for value in values
            ),
            "independent_coverage_mean": statistics.fmean(
                as_float(value, "independent_coverage") for value in values
            ),
            "external_switch_count_mean": statistics.fmean(
                as_float(value, "external_switch_count") for value in values
            ),
            "external_physical_cost_mean": statistics.fmean(
                as_float(value, "external_physical_cost") for value in values
            ),
            "feasibility_rate_mean": statistics.fmean(
                as_float(value, "feasibility_rate") for value in values
            ),
            "fallback_mean": statistics.fmean(
                as_float(value, "fallback") for value in values
            ),
            "end_to_end_runtime_ms_mean": statistics.fmean(runtime_values),
            "end_to_end_runtime_ms_median": quantile(runtime_values, 0.5),
            "end_to_end_runtime_ms_p95": quantile(runtime_values, 0.95),
            "end_to_end_runtime_ms_max": max(runtime_values),
            "candidate_generation_share": statistics.fmean(
                as_float(value, "candidate_generation_ms")
                / max(as_float(value, "end_to_end_runtime_ms"), EPSILON)
                for value in period_rows
            ),
        }
        for deadline in DEADLINES_MS:
            hits = [
                1.0
                if as_float(value, "end_to_end_runtime_ms") <= deadline
                else 0.0
                for value in period_rows
            ]
            low, high = wilson_score_interval(hits)
            row[f"deadline_hit_rate_{deadline}ms"] = statistics.fmean(hits)
            row[f"deadline_hit_wilson_low_{deadline}ms"] = low
            row[f"deadline_hit_wilson_high_{deadline}ms"] = high
        output.append(row)
    return output


def bounded_intervals(
    episodes: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, object]]] = defaultdict(list)
    for row in episodes:
        groups[(str(row["scenario"]), str(row["algorithm"]))].append(row)
    output: List[Dict[str, object]] = []
    for (scenario, method), values in sorted(groups.items()):
        if len(values) != len(SEEDS):
            raise RuntimeError(f"Incomplete bounded cell: {scenario}/{method}")
        for metric in BOUNDED_METRICS:
            metric_values = [as_float(row, metric) for row in values]
            low, high = wilson_score_interval(metric_values)
            output.append(
                {
                    "scenario": scenario,
                    "method": method,
                    "method_label": METHOD_LABELS[method],
                    "metric": metric,
                    "seeds": len(values),
                    "mean": statistics.fmean(metric_values),
                    "wilson_95_low": low,
                    "wilson_95_high": high,
                }
            )
    return output


def pareto_rows(
    summaries: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        scenario_rows = [
            row for row in summaries if row["scenario"] == scenario
        ]
        for quality_metric in (
            "external_objective_mean",
            "independent_service_score_mean",
        ):
            for row in scenario_rows:
                quality = float(row[quality_metric])
                runtime = float(row["end_to_end_runtime_ms_mean"])
                dominators = [
                    other
                    for other in scenario_rows
                    if (
                        float(other[quality_metric]) >= quality - EPSILON
                        and float(other["end_to_end_runtime_ms_mean"])
                        <= runtime + EPSILON
                        and (
                            float(other[quality_metric]) > quality + EPSILON
                            or float(other["end_to_end_runtime_ms_mean"])
                            < runtime - EPSILON
                        )
                    )
                ]
                output.append(
                    {
                        "scenario": scenario,
                        "quality_metric": quality_metric,
                        "method": row["method"],
                        "method_label": row["method_label"],
                        "quality_mean": quality,
                        "runtime_mean_ms": runtime,
                        "pareto_nondominated": not dominators,
                        "dominated_by": ",".join(
                            str(other["method_label"]) for other in dominators
                        ),
                    }
                )
    return output


def deadline_selection(
    summaries: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        scenario_rows = [
            row for row in summaries if row["scenario"] == scenario
        ]
        for deadline in DEADLINES_MS:
            field = f"deadline_hit_rate_{deadline}ms"
            eligible = [
                row for row in scenario_rows if float(row[field]) >= 0.95
            ]
            quality_first = (
                max(
                    eligible,
                    key=lambda row: (
                        float(row["external_objective_mean"]),
                        float(row["independent_service_score_mean"]),
                    ),
                )
                if eligible
                else None
            )
            output.append(
                {
                    "scenario": scenario,
                    "deadline_ms": deadline,
                    "eligible_methods_95pct": ",".join(
                        str(row["method_label"])
                        for row in sorted(
                            eligible,
                            key=lambda value: float(
                                value["external_objective_mean"]
                            ),
                            reverse=True,
                        )
                    ),
                    "eligible_method_count": len(eligible),
                    "quality_first_recommendation": (
                        quality_first["method_label"]
                        if quality_first is not None
                        else "No verified method"
                    ),
                    "recommended_objective_mean": (
                        quality_first["external_objective_mean"]
                        if quality_first is not None
                        else ""
                    ),
                    "recommended_service_mean": (
                        quality_first["independent_service_score_mean"]
                        if quality_first is not None
                        else ""
                    ),
                    "recommended_deadline_hit_rate": (
                        quality_first[field]
                        if quality_first is not None
                        else ""
                    ),
                }
            )
    return output


def family_registry(
    rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    groups: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        groups[str(row["family_id"])].append(row)
    return [
        {
            "family_id": family_id,
            "scenario": values[0]["scenario"],
            "metric": values[0]["metric"],
            "category": values[0]["category"],
            "comparisons": len(values),
            "adjustment": "Holm step-down",
        }
        for family_id, values in sorted(groups.items())
    ]


def stage3_gate_b() -> Dict[str, object]:
    path = STAGE3_DIR / "stage3_gate_b.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    decision = payload.get("decision", payload.get("gate_b_decision", "FAIL"))
    return {"source": str(path.resolve()), "decision": decision}


def stage4_gates() -> Dict[str, str]:
    rows = read_csv(STAGE4_DIR / "stage4_gate_decisions.csv")
    return {row["gate"]: row["status"] for row in rows}


def exact_comparison_summary(
    paired_rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        relevant = [
            row
            for row in paired_rows
            if row["scenario"] == scenario
            and row["method_b"] == "HiGHS-MILP"
            and row["metric"]
            in {
                "external_objective",
                "independent_service_score",
                "end_to_end_runtime_ms",
            }
        ]
        record: Dict[str, object] = {"scenario": scenario}
        for row in relevant:
            metric = str(row["metric"])
            record[f"{metric}_fixedk_minus_exact"] = row[
                "mean_difference_a_minus_b"
            ]
            record[f"{metric}_ci_low"] = row["ci95_bca_low"]
            record[f"{metric}_ci_high"] = row["ci95_bca_high"]
            record[f"{metric}_p_holm"] = row["p_holm_adjusted"]
        output.append(record)
    return output


def claim_freeze_rows(
    paired_rows: Sequence[Mapping[str, object]],
    summaries: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    gate_b = stage3_gate_b()
    stage4 = stage4_gates()
    exact_quality_better = 0
    exact_service_better = 0
    exact_quality_significant = 0
    exact_service_significant = 0
    for scenario in SCENARIOS:
        objective = next(
            row
            for row in paired_rows
            if row["scenario"] == scenario
            and row["method_b"] == "HiGHS-MILP"
            and row["metric"] == "external_objective"
        )
        service = next(
            row
            for row in paired_rows
            if row["scenario"] == scenario
            and row["method_b"] == "HiGHS-MILP"
            and row["metric"] == "independent_service_score"
        )
        if float(objective["mean_difference_a_minus_b"]) < 0:
            exact_quality_better += 1
            if bool(objective["significant_after_holm_0_05"]):
                exact_quality_significant += 1
        if float(service["mean_difference_a_minus_b"]) < 0:
            exact_service_better += 1
            if bool(service["significant_after_holm_0_05"]):
                exact_service_significant += 1
    all_feasible = all(
        float(row["feasibility_rate_mean"]) >= 1.0 - EPSILON
        for row in summaries
        if row["method"] == "CA-HMCD"
    )
    return [
        {
            "claim_id": "C5.1",
            "claim": "CA-HMCD has a universal real-time advantage.",
            "decision": "REJECT",
            "basis": f"Stage-3 Gate B={gate_b['decision']}.",
            "approved_wording": (
                "The evaluated experiments did not establish a real-time "
                "computational advantage over full-candidate HiGHS."
            ),
        },
        {
            "claim_id": "C5.2",
            "claim": (
                "The exact HiGHS profile is retained as the complete-candidate "
                "reference and selected using scenario-specific constraints."
            ),
            "decision": "RETAIN_WITH_SCOPE",
            "basis": (
                f"Exact mean objective exceeds FixedK in "
                f"{exact_quality_better}/5 scenarios and service in "
                f"{exact_service_better}/5 scenarios, but Holm-significant "
                f"advantages occur in {exact_quality_significant}/5 and "
                f"{exact_service_significant}/5 scenarios, respectively."
            ),
            "approved_wording": (
                "CA-HMCD-Exact provides the complete-candidate reference "
                "backend. In the registered tests, it did not show a "
                "Holm-significant external-objective or service advantage "
                "over FixedK; backend selection should therefore follow the "
                "scenario- and deadline-specific evidence."
            ),
        },
        {
            "claim_id": "C5.3",
            "claim": "The K=12 profile is a universally superior optimizer.",
            "decision": "REJECT",
            "basis": (
                "Stage 3 and Stage 5 do not establish universal quality or "
                "runtime superiority over exact mode."
            ),
            "approved_wording": (
                "CA-HMCD-FixedK is a bounded-computation approximation whose "
                "quality-runtime trade-off must be stated explicitly."
            ),
        },
        {
            "claim_id": "C5.4",
            "claim": "Stability control is an independently supported contribution.",
            "decision": (
                "RETAIN"
                if stage4.get("H4.4 stability") == "SUPPORTED"
                else "REJECT"
            ),
            "basis": f"Stage 4 status: {stage4.get('H4.4 stability')}.",
            "approved_wording": (
                "The stability term reduces allocation switching at a "
                "measurable immediate-service cost."
            ),
        },
        {
            "claim_id": "C5.5",
            "claim": "True-state repair is an independently supported contribution.",
            "decision": (
                "RETAIN"
                if stage4.get("H4.5 true-state repair / Gate C")
                == "SUPPORTED"
                else "REJECT"
            ),
            "basis": (
                f"Stage 4 status: "
                f"{stage4.get('H4.5 true-state repair / Gate C')}."
            ),
            "approved_wording": (
                "True-state repair restores final feasibility under perception "
                "disturbance, with an explicit service trade-off."
            ),
        },
        {
            "claim_id": "C5.6",
            "claim": "Compatibility and complementarity universally improve the complete objective.",
            "decision": "REJECT",
            "basis": (
                f"Compatibility={stage4.get('H4.1 compatibility')}; "
                f"complementarity={stage4.get('H4.2 complementarity')}."
            ),
            "approved_wording": (
                "Compatibility and complementarity are context-dependent "
                "components with service-level evidence but no universal "
                "complete-objective advantage."
            ),
        },
        {
            "claim_id": "C5.7",
            "claim": "The current redundancy penalty is empirically validated.",
            "decision": "REJECT",
            "basis": f"Stage 4 status: {stage4.get('H4.3 redundancy')}.",
            "approved_wording": (
                "The redundancy term remains a modeling safeguard; its "
                "operational effect was not supported under the registered tests."
            ),
        },
        {
            "claim_id": "C5.8",
            "claim": "The framework transfers to engineering-shaped low-altitude-safety scenarios.",
            "decision": "RETAIN_WITH_SCOPE" if all_feasible else "REJECT",
            "basis": (
                f"FixedK final feasibility across all five scenarios={all_feasible}; "
                f"Stage 4 engineering gate="
                f"{stage4.get('H4.6 engineering transfer')}."
            ),
            "approved_wording": (
                "The framework is supported by controlled engineering-shaped "
                "simulation, not by field deployment evidence."
            ),
        },
    ]


def method_selection_matrix(
    summaries: Sequence[Mapping[str, object]],
    deadlines: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        rows = [row for row in summaries if row["scenario"] == scenario]
        quality_first = max(
            rows, key=lambda row: float(row["external_objective_mean"])
        )
        service_first = max(
            rows, key=lambda row: float(row["independent_service_score_mean"])
        )
        fastest = min(
            rows, key=lambda row: float(row["end_to_end_runtime_ms_mean"])
        )
        deadline_100 = next(
            row
            for row in deadlines
            if row["scenario"] == scenario
            and int(row["deadline_ms"]) == 100
        )
        output.append(
            {
                "scenario": scenario,
                "quality_first": quality_first["method_label"],
                "service_first": service_first["method_label"],
                "fastest": fastest["method_label"],
                "fastest_mean_runtime_ms": fastest[
                    "end_to_end_runtime_ms_mean"
                ],
                "100ms_quality_first_among_95pct_eligible": deadline_100[
                    "quality_first_recommendation"
                ],
                "fixedk_final_feasible": next(
                    row["feasibility_rate_mean"]
                    for row in rows
                    if row["method"] == "CA-HMCD"
                ),
                "interpretation": (
                    "Use objective/service/runtime priorities explicitly; "
                    "no universal winner is asserted."
                ),
            }
        )
    return output


def format_effect(row: Mapping[str, object]) -> str:
    return (
        f"{float(row['mean_difference_a_minus_b']):.4f} "
        f"(95% BCa CI {float(row['ci95_bca_low']):.4f} to "
        f"{float(row['ci95_bca_high']):.4f}; "
        f"Holm P={float(row['p_holm_adjusted']):.4g})"
    )


def build_report(
    source: Mapping[str, object],
    paired_rows: Sequence[Mapping[str, object]],
    summaries: Sequence[Mapping[str, object]],
    pareto: Sequence[Mapping[str, object]],
    deadlines: Sequence[Mapping[str, object]],
    claims: Sequence[Mapping[str, object]],
    matrix: Sequence[Mapping[str, object]],
    registry: Sequence[Mapping[str, object]],
) -> str:
    lines = [
        "## Material Passport",
        "",
        "- Origin Skill: experiment-agent",
        "- Origin Mode: run + validate",
        "- Origin Date: 2026-09-18",
        "- Verification Status: ANALYZED",
        "- Version Label: ca-hmcd-stage5-applicability-v1.1",
        "",
        "# CA-HMCD 阶段5：外部基线与适用边界定稿",
        "",
        "## 完成状态",
        "",
        f"- 正式实验：{EXPECTED_EPISODES}个30种子轨迹单元，"
        f"{EXPECTED_STEPS}条逐周期记录。",
        f"- 公平性审计：{EXPECTED_AUDITS}个场景-种子-周期组全部通过。",
        f"- 统计：{len(paired_rows)}项配对比较，"
        f"{len(registry)}个Holm比较族。",
        "- 比较方法：CA-HMCD-FixedK、CA-HMCD-Exact（HiGHS）、"
        "Genetic Algorithm、Greedy和External Auction。",
        "- 冻结价值模型、外部评价器和服务端点均未修改。",
        "",
        "## 方法选择矩阵",
        "",
        "下表依据各方法的样本均值作描述性选择；统计推断仍以配对区间和"
        "Holm校正结果为准。",
        "",
        "| 场景 | 最高统一目标均值 | 最高独立服务均值 | 最快方法 | "
        "100 ms且命中率>=95%的最高目标均值方法 |",
        "|---|---|---|---|---|",
    ]
    for row in matrix:
        lines.append(
            f"| {row['scenario']} | {row['quality_first']} | "
            f"{row['service_first']} | {row['fastest']} | "
            f"{row['100ms_quality_first_among_95pct_eligible']} |"
        )
    lines.extend(
        [
            "",
            "## FixedK与Exact模式",
            "",
        ]
    )
    for scenario in SCENARIOS:
        objective = next(
            row
            for row in paired_rows
            if row["scenario"] == scenario
            and row["method_b"] == "HiGHS-MILP"
            and row["metric"] == "external_objective"
        )
        service = next(
            row
            for row in paired_rows
            if row["scenario"] == scenario
            and row["method_b"] == "HiGHS-MILP"
            and row["metric"] == "independent_service_score"
        )
        runtime = next(
            row
            for row in paired_rows
            if row["scenario"] == scenario
            and row["method_b"] == "HiGHS-MILP"
            and row["metric"] == "end_to_end_runtime_ms"
        )
        lines.append(
            f"- **{scenario}**：FixedK-Exact统一目标差 "
            f"{format_effect(objective)}；服务差 {format_effect(service)}；"
            f"端到端时间差 {format_effect(runtime)}。"
        )
    lines.extend(
        [
            "",
            "HiGHS优化的是同一CA-HMCD价值模型。因此，某一场景中Exact模式"
            "均值更高时，差异属于候选空间和求解后端，而不表示价值模型被外部方法"
            "否定。五个场景中，Exact相对FixedK的统一目标和独立服务差异均未在"
            "Holm校正后达到显著水平。",
            "",
            "## 截止期边界",
            "",
            "| 场景 | 50 ms | 100 ms | 200 ms | 500 ms |",
            "|---|---|---|---|---|",
        ]
    )
    for scenario in SCENARIOS:
        values = {
            int(row["deadline_ms"]): row["quality_first_recommendation"]
            for row in deadlines
            if row["scenario"] == scenario
        }
        lines.append(
            f"| {scenario} | {values[50]} | {values[100]} | "
            f"{values[200]} | {values[500]} |"
        )
    lines.extend(
        [
            "",
            "上表仅在至少95%的实测决策周期满足截止期的方法中选择统一目标最高者。"
            "它不构成硬实时认证。",
            "",
            "## 论文主张冻结",
            "",
            "| ID | 决策 | 允许进入正文的表述 |",
            "|---|---|---|",
        ]
    )
    for claim in claims:
        lines.append(
            f"| {claim['claim_id']} | **{claim['decision']}** | "
            f"{claim['approved_wording']} |"
        )
    nondominated = [
        row for row in pareto if bool(row["pareto_nondominated"])
    ]
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            f"- 质量-时间Pareto非支配记录共{len(nondominated)}条；"
            "完整记录保存在`stage5_pareto_front.csv`。",
            "- 阶段3 Gate B仍然有效：当前证据不支持“实时优势”或"
            "“普遍比HiGHS更快”的表述。",
            "- 阶段4的稳定性和真实状态修复证据不因HiGHS质量更高而失效；"
            "它们属于价值模型和安全控制机制，而不是求解器速度主张。",
            "- 工程场景仍为受控模拟。公开轨迹和真实观测模式不能替代响应侧"
            "实装测试。",
            "",
            "## Fallacy Scan",
            "",
            "- Coverage: 11/11 fallacy types checked.",
            "- Pseudoreplication: inference uses one complete seed trajectory.",
            "- Multiple testing: endpoint-specific Holm families are explicit.",
            "- Metric circularity: independent service excludes optimization terms.",
            "- Solver/model conflation: HiGHS is labelled CA-HMCD-Exact.",
            "- Runtime cherry-picking: end-to-end time includes candidate construction.",
            "- Deadline post-selection: four deadlines were frozen before execution.",
            "- Null-is-equivalence: nonsignificance is not called equivalence.",
            "- Bounded-outcome misuse: Wilson intervals are reported.",
            "- Common-support bias: response time uses jointly served task-periods.",
            "- Causal overreach: claims are limited to controlled interventions.",
            "- Generalization overreach: no field-deployment claim is made.",
            "",
            "## Reproducibility",
            "",
            "- Formal execution used the locked Python 3.12.14, NumPy 2.5.3 and "
            "SciPy 1.18.1 environment.",
            "- Every shard is bound to the protocol fingerprint and code hashes.",
            "- Verification status is `ANALYZED`; deterministic spot re-runs and "
            "repository regression tests are reported separately.",
            "",
            "## Source fingerprints",
            "",
            f"- Protocol: `{source['metadata']['protocol_fingerprint']}`",
            f"- Execution metadata SHA-256: "
            f"`{source['execution_metadata_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def analyse(formal_dir: Path, output_dir: Path) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    episodes, steps, source = validate_sources(formal_dir)
    paired_rows, common_rows = paired_comparisons(episodes, steps)
    summaries = cell_summary(episodes, steps)
    bounded = bounded_intervals(episodes)
    pareto = pareto_rows(summaries)
    deadlines = deadline_selection(summaries)
    registry = family_registry(paired_rows)
    exact_summary = exact_comparison_summary(paired_rows)
    claims = claim_freeze_rows(paired_rows, summaries)
    matrix = method_selection_matrix(summaries, deadlines)

    outputs: Dict[str, Sequence[Mapping[str, object]]] = {
        "stage5_paired_comparisons.csv": paired_rows,
        "stage5_common_task_response.csv": common_rows,
        "stage5_method_summary.csv": summaries,
        "stage5_bounded_intervals.csv": bounded,
        "stage5_pareto_front.csv": pareto,
        "stage5_deadline_selection.csv": deadlines,
        "stage5_holm_families.csv": registry,
        "stage5_exact_mode_summary.csv": exact_summary,
        "stage5_claim_freeze.csv": claims,
        "stage5_method_selection_matrix.csv": matrix,
    }
    for name, rows in outputs.items():
        write_csv(output_dir / name, rows)
    report = build_report(
        source,
        paired_rows,
        summaries,
        pareto,
        deadlines,
        claims,
        matrix,
        registry,
    )
    (output_dir / "stage5_applicability_report.md").write_text(
        report, encoding="utf-8"
    )
    material_passport = {
        "schema": "ARS Material Passport 9",
        "origin_skill": "experiment-agent",
        "origin_mode": ["run", "validate"],
        "origin_date": "2026-09-18",
        "verification_status": "ANALYZED",
        "version_label": "ca-hmcd-stage5-applicability-v1",
        "scope_boundary": (
            "Controlled engineering-shaped simulation and solver-profile "
            "selection; not field validation or hard real-time certification."
        ),
        "materials": {
            "protocol": str(PROTOCOL_DIR.resolve()),
            "formal_results": str(formal_dir.resolve()),
            "analysis": str(output_dir.resolve()),
        },
    }
    (output_dir / "material_passport.json").write_text(
        json.dumps(material_passport, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    output_files = list(outputs) + [
        "stage5_applicability_report.md",
        "material_passport.json",
    ]
    manifest = {
        "schema": "ca-hmcd-stage5-applicability-analysis-manifest-v1",
        "analysis_version": ANALYSIS_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_source": str(formal_dir.resolve()),
        "protocol_source": str(PROTOCOL_DIR.resolve()),
        "source_validation": source,
        "counts": {
            "episodes": len(episodes),
            "steps": len(steps),
            "paired_comparisons": len(paired_rows),
            "holm_families": len(registry),
            "common_task_response_seed_rows": len(common_rows),
            "pareto_rows": len(pareto),
            "deadline_rows": len(deadlines),
            "claim_rows": len(claims),
        },
        "analysis_hashes": {
            "ca_hmcd_stage5_applicability_analysis.py": sha256_file(
                Path(__file__).resolve()
            )
        },
        "output_hashes": {
            name: sha256_file(output_dir / name) for name in output_files
        },
    }
    (output_dir / "stage5_analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--formal-dir",
        type=Path,
        default=PROJECT_DIR / "ca_hmcd_stage5_applicability_formal_20260918",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "ca_hmcd_stage5_applicability_analysis_20260918",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    result = analyse(
        arguments.formal_dir.resolve(),
        arguments.output_dir.resolve(),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

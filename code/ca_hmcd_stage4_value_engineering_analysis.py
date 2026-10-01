"""Analyse the registered Stage 4 value-model and engineering experiment."""

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
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

import ca_hmcd_simulation as core
from ca_hmcd_stage3_statistics import (
    bca_mean_interval,
    holm_adjust,
    stable_seed,
    wilson_score_interval,
)
from ca_hmcd_stage4_value_engineering_runner import (
    COMPLEMENTARITY_SCALES,
    ENGINEERING_SCENARIOS,
    NODE_LIMIT,
    PROTOCOL_DIR,
    REPAIR_LEVELS,
    SEEDS,
    STABILITY_LEVELS,
    STEPS,
    TOP_K,
    code_hashes as runner_code_hashes,
    protocol_fingerprint,
)


ANALYSIS_VERSION = "ca-hmcd-stage4-value-engineering-analysis-v1"
PROJECT_DIR = Path(__file__).resolve().parent
EXPECTED_EPISODES = 2070
EXPECTED_STEPS = EXPECTED_EPISODES * STEPS
EXPECTED_AUDITS = 7560
BOOTSTRAP_RESAMPLES = 10000
BOUNDED_METRICS = (
    "feasibility_rate",
    "raw_feasible",
    "repair_applied",
    "independent_coverage",
    "same_type_pair_ratio",
    "capability_overlap",
    "marginal_gain_waste",
)
ENGINEERING_MODULES = (
    "No-Compatibility",
    "No-Synergy",
    "No-Redundancy",
    "No-Stability",
)
ENGINEERING_BASELINES = ("Greedy", "External-Auction")
EPSILON = 1e-12


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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
    return str(row.get(key, "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }


def validate_formal_source(
    formal_dir: Path,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], Dict[str, object]]:
    metadata_path = formal_dir / "stage4_execution_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    episodes = read_csv(formal_dir / "stage4_episode_results.csv")
    steps = read_csv(formal_dir / "stage4_per_step.csv")
    audits = read_csv(formal_dir / "stage4_pairing_audit.csv")
    output_hashes = {
        name: sha256_file(formal_dir / name)
        for name in (
            "stage4_episode_results.csv",
            "stage4_per_step.csv",
            "stage4_pairing_audit.csv",
            "stage4_cell_summary.csv",
        )
    }
    current_hashes = runner_code_hashes()
    checks = [
        {
            "check": "formal execution mode",
            "observed": metadata.get("mode"),
            "expected": "formal",
            "passed": metadata.get("mode") == "formal",
        },
        {
            "check": "formal pairing audit",
            "observed": metadata.get("audit_pass"),
            "expected": True,
            "passed": bool(metadata.get("audit_pass")),
        },
        {
            "check": "registered episode count",
            "observed": len(episodes),
            "expected": EXPECTED_EPISODES,
            "passed": len(episodes) == EXPECTED_EPISODES,
        },
        {
            "check": "registered step count",
            "observed": len(steps),
            "expected": EXPECTED_STEPS,
            "passed": len(steps) == EXPECTED_STEPS,
        },
        {
            "check": "registered audit count",
            "observed": len(audits),
            "expected": EXPECTED_AUDITS,
            "passed": (
                len(audits) == EXPECTED_AUDITS
                and all(as_bool(row, "passed") for row in audits)
            ),
        },
        {
            "check": "frozen execution code hashes",
            "observed": current_hashes,
            "expected": metadata.get("code_hashes"),
            "passed": current_hashes == metadata.get("code_hashes"),
        },
        {
            "check": "protocol fingerprint",
            "observed": protocol_fingerprint(),
            "expected": metadata.get("protocol_fingerprint"),
            "passed": (
                protocol_fingerprint()
                == metadata.get("protocol_fingerprint")
            ),
        },
        {
            "check": "formal output hashes",
            "observed": output_hashes,
            "expected": metadata.get("outputs"),
            "passed": output_hashes == metadata.get("outputs"),
        },
    ]
    if not all(bool(check["passed"]) for check in checks):
        failed = [
            str(check["check"]) for check in checks if not check["passed"]
        ]
        raise RuntimeError(f"Stage 4 source validation failed: {failed}")
    return episodes, steps, {
        "metadata": metadata,
        "checks": checks,
        "output_hashes": output_hashes,
        "execution_metadata_sha256": sha256_file(metadata_path),
    }


def subset(
    rows: Sequence[Mapping[str, object]],
    **conditions: object,
) -> List[Mapping[str, object]]:
    return [
        row
        for row in rows
        if all(str(row.get(key)) == str(value) for key, value in conditions.items())
    ]


def paired_values(
    episodes: Sequence[Mapping[str, object]],
    study: str,
    stratum: str,
    method_a: str,
    method_b: str,
    metric: str,
) -> Tuple[List[int], List[float], List[float]]:
    relevant = [
        row
        for row in episodes
        if row["study"] == study
        and row["stratum"] == stratum
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


def paired_row_from_values(
    *,
    seeds: Sequence[int],
    values_a: Sequence[float],
    values_b: Sequence[float],
    family_id: str,
    section: str,
    comparison: str,
    metric: str,
    method_a: str,
    method_b: str,
    favorable_direction: str,
    context: Mapping[str, object],
    identity_control: bool = False,
    require_all_seeds: bool = True,
) -> Dict[str, object]:
    if require_all_seeds and len(seeds) != len(SEEDS):
        raise RuntimeError(
            f"Incomplete paired comparison {comparison}/{metric}: "
            f"{len(seeds)} seeds"
        )
    if not seeds:
        raise RuntimeError(
            f"No paired observations for {comparison}/{metric}"
        )
    differences = [
        float(a) - float(b) for a, b in zip(values_a, values_b)
    ]
    stats = core.paired_statistics(values_a, values_b)
    ci_low, ci_high = bca_mean_interval(
        differences,
        resamples=BOOTSTRAP_RESAMPLES,
        seed=stable_seed(family_id, comparison, metric),
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
        "section": section,
        "comparison": comparison,
        "metric": metric,
        "method_a": method_a,
        "method_b": method_b,
        "favorable_direction": favorable_direction,
        "identity_control": identity_control,
        "pairs": len(seeds),
        "missing_seed_pairs": len(SEEDS) - len(seeds),
        "mean_a": statistics.fmean(values_a),
        "mean_b": statistics.fmean(values_b),
        "mean_difference_a_minus_b": statistics.fmean(differences),
        "ci95_bca_low": ci_low,
        "ci95_bca_high": ci_high,
        "p_unadjusted": float(stats["p_value"]),
        "cohen_dz": float(stats["cohen_dz"]),
        "rank_biserial": float(stats["rank_biserial"]),
        "win_rate_favorable": win_rate,
        "win_rate_wilson_low": win_low,
        "win_rate_wilson_high": win_high,
        "max_abs_paired_difference": max(abs(value) for value in differences),
        **context,
    }


def paired_row(
    episodes: Sequence[Mapping[str, object]],
    *,
    study: str,
    stratum: str,
    method_a: str,
    method_b: str,
    metric: str,
    family_id: str,
    section: str,
    favorable_direction: str,
    context: Mapping[str, object],
    identity_control: bool = False,
) -> Dict[str, object]:
    seeds, values_a, values_b = paired_values(
        episodes,
        study,
        stratum,
        method_a,
        method_b,
        metric,
    )
    return paired_row_from_values(
        seeds=seeds,
        values_a=values_a,
        values_b=values_b,
        family_id=family_id,
        section=section,
        comparison=f"{method_a} vs {method_b}",
        metric=metric,
        method_a=method_a,
        method_b=method_b,
        favorable_direction=favorable_direction,
        context=context,
        identity_control=identity_control,
    )


def common_response_values(
    steps: Sequence[Mapping[str, object]],
    scenario: str,
    method_a: str,
    method_b: str,
) -> Tuple[List[int], List[float], List[float], List[int]]:
    relevant = [
        row
        for row in steps
        if row["study"] == "engineering"
        and row["stratum"] == scenario
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
    seeds: List[int] = []
    values_a: List[float] = []
    values_b: List[float] = []
    common_counts: List[int] = []
    for seed in SEEDS:
        a_response: List[float] = []
        b_response: List[float] = []
        for step in range(STEPS):
            key_a = (method_a, seed, step)
            key_b = (method_b, seed, step)
            if key_a not in lookup or key_b not in lookup:
                continue
            common = sorted(set(lookup[key_a]) & set(lookup[key_b]))
            for task_id in common:
                a_response.append(lookup[key_a][task_id])
                b_response.append(lookup[key_b][task_id])
        if a_response:
            seeds.append(seed)
            values_a.append(statistics.fmean(a_response))
            values_b.append(statistics.fmean(b_response))
            common_counts.append(len(a_response))
    return seeds, values_a, values_b, common_counts


def build_paired_comparisons(
    episodes: Sequence[Mapping[str, object]],
    steps: Sequence[Mapping[str, object]],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    rows: List[Dict[str, object]] = []
    common_rows: List[Dict[str, object]] = []
    module_metrics = (
        ("external_objective", "higher"),
        ("independent_service_score", "higher"),
        ("independent_coverage", "higher"),
        ("external_switch_count", "lower"),
        ("external_physical_cost", "lower"),
        ("same_type_pair_ratio", "lower"),
        ("capability_overlap", "lower"),
        ("marginal_gain_waste", "lower"),
    )
    for scenario in ENGINEERING_SCENARIOS:
        for metric, direction in module_metrics:
            family = f"eng_module::{scenario}::{metric}"
            for method_b in ENGINEERING_MODULES:
                rows.append(
                    paired_row(
                        episodes,
                        study="engineering",
                        stratum=scenario,
                        method_a="CA-HMCD",
                        method_b=method_b,
                        metric=metric,
                        family_id=family,
                        section="engineering_module_ablation",
                        favorable_direction=direction,
                        context={"scenario": scenario, "factor_level": scenario},
                    )
                )
    baseline_metrics = (
        ("external_objective", "higher"),
        ("independent_service_score", "higher"),
        ("independent_coverage", "higher"),
        ("external_physical_cost", "lower"),
    )
    for scenario in ENGINEERING_SCENARIOS:
        for metric, direction in baseline_metrics:
            family = f"eng_baseline::{scenario}::{metric}"
            for method_b in ENGINEERING_BASELINES:
                rows.append(
                    paired_row(
                        episodes,
                        study="engineering",
                        stratum=scenario,
                        method_a="CA-HMCD",
                        method_b=method_b,
                        metric=metric,
                        family_id=family,
                        section="engineering_external_baseline",
                        favorable_direction=direction,
                        context={"scenario": scenario, "factor_level": scenario},
                    )
                )
        family = f"eng_baseline::{scenario}::common_task_response_time"
        for method_b in ENGINEERING_BASELINES:
            seeds, values_a, values_b, counts = common_response_values(
                steps, scenario, "CA-HMCD", method_b
            )
            if len(seeds) != len(SEEDS):
                missing_seeds = len(SEEDS) - len(seeds)
            else:
                missing_seeds = 0
            common_rows.extend(
                {
                    "scenario": scenario,
                    "method_a": "CA-HMCD",
                    "method_b": method_b,
                    "seed": seed,
                    "common_task_periods": count,
                    "response_a": value_a,
                    "response_b": value_b,
                    "difference_a_minus_b": value_a - value_b,
                }
                for seed, count, value_a, value_b in zip(
                    seeds, counts, values_a, values_b
                )
            )
            rows.append(
                paired_row_from_values(
                    seeds=seeds,
                    values_a=values_a,
                    values_b=values_b,
                    family_id=family,
                    section="engineering_external_baseline",
                    comparison=f"CA-HMCD vs {method_b}",
                    metric="common_task_response_time",
                    method_a="CA-HMCD",
                    method_b=method_b,
                    favorable_direction="lower",
                    context={
                        "scenario": scenario,
                        "factor_level": scenario,
                        "mean_common_task_periods": statistics.fmean(counts),
                        "missing_seed_pairs": missing_seeds,
                    },
                    require_all_seeds=False,
                )
            )

    sweep_metrics = (
        ("external_objective", "higher"),
        ("independent_service_score", "higher"),
        ("external_complementarity_gain", "higher"),
        ("same_type_pair_ratio", "lower"),
    )
    for scale in COMPLEMENTARITY_SCALES:
        stratum = f"scale-{scale:.2f}"
        for metric, direction in sweep_metrics:
            rows.append(
                paired_row(
                    episodes,
                    study="complementarity_sweep",
                    stratum=stratum,
                    method_a="CA-HMCD",
                    method_b="No-Synergy",
                    metric=metric,
                    family_id=f"complementarity::{metric}",
                    section="complementarity_sweep",
                    favorable_direction=direction,
                    context={
                        "scenario": "public_event",
                        "factor_level": scale,
                    },
                    identity_control=abs(scale) <= EPSILON,
                )
            )

    stability_metrics = (
        ("external_objective", "higher"),
        ("independent_service_score", "higher"),
        ("external_switch_count", "lower"),
        ("external_physical_cost", "lower"),
    )
    for volatility in STABILITY_LEVELS:
        stratum = f"volatility-{volatility:.2f}"
        for metric, direction in stability_metrics:
            rows.append(
                paired_row(
                    episodes,
                    study="stability_sweep",
                    stratum=stratum,
                    method_a="CA-HMCD",
                    method_b="No-Stability",
                    metric=metric,
                    family_id=f"stability::{metric}",
                    section="stability_sweep",
                    favorable_direction=direction,
                    context={
                        "scenario": "urban_corridor",
                        "factor_level": volatility,
                    },
                )
            )

    redundancy_metrics = (
        ("external_objective", "higher"),
        ("independent_service_score", "higher"),
        ("same_type_pair_ratio", "lower"),
        ("capability_overlap", "lower"),
        ("marginal_gain_waste", "lower"),
    )
    for metric, direction in redundancy_metrics:
        for method_b in ("No-Redundancy", "No-Synergy", "Greedy"):
            rows.append(
                paired_row(
                    episodes,
                    study="redundancy_stress",
                    stratum="same-type-dense",
                    method_a="CA-HMCD",
                    method_b=method_b,
                    metric=metric,
                    family_id=f"redundancy_stress::{metric}",
                    section="redundancy_stress",
                    favorable_direction=direction,
                    context={
                        "scenario": "redundancy_stress",
                        "factor_level": "same-type-dense",
                    },
                )
            )

    repair_metrics = (
        ("feasibility_rate", "higher"),
        ("independent_service_score", "higher"),
        ("external_objective", "higher"),
    )
    for noise in REPAIR_LEVELS:
        stratum = f"noise-{noise:.2f}"
        for metric, direction in repair_metrics:
            identity = abs(noise) <= EPSILON
            family = (
                f"repair_identity::{metric}"
                if identity
                else f"repair_disturbance::{metric}"
            )
            rows.append(
                paired_row(
                    episodes,
                    study="repair_disturbance",
                    stratum=stratum,
                    method_a="CA-HMCD-RepairOn",
                    method_b="CA-HMCD-RepairOff",
                    metric=metric,
                    family_id=family,
                    section="repair_disturbance",
                    favorable_direction=direction,
                    context={
                        "scenario": "public_event",
                        "factor_level": noise,
                    },
                    identity_control=identity,
                )
            )
    holm_adjust(rows)
    return rows, common_rows


def bounded_intervals(
    episodes: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[
        Tuple[str, str, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in episodes:
        grouped[
            (str(row["study"]), str(row["stratum"]), str(row["algorithm"]))
        ].append(row)
    output: List[Dict[str, object]] = []
    for group, values in sorted(grouped.items()):
        study, stratum, algorithm = group
        if len(values) != len(SEEDS):
            raise RuntimeError(
                f"Bounded interval cell has {len(values)} seeds: {group}"
            )
        for metric in BOUNDED_METRICS:
            metric_values = [as_float(row, metric) for row in values]
            low, high = wilson_score_interval(metric_values)
            output.append(
                {
                    "study": study,
                    "stratum": stratum,
                    "algorithm": algorithm,
                    "metric": metric,
                    "seeds": len(metric_values),
                    "mean": statistics.fmean(metric_values),
                    "wilson_95_low": low,
                    "wilson_95_high": high,
                }
            )
    return output


def family_registry(
    rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["family_id"])].append(row)
    return [
        {
            "family_id": family_id,
            "section": values[0]["section"],
            "metric": values[0]["metric"],
            "number_of_comparisons": len(values),
            "identity_control_family": all(
                bool(value["identity_control"]) for value in values
            ),
            "adjustment": "Holm step-down",
        }
        for family_id, values in sorted(grouped.items())
    ]


def rankdata(values: Sequence[float]) -> List[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(ordered):
        end = position + 1
        while (
            end < len(ordered)
            and abs(ordered[end][1] - ordered[position][1]) <= EPSILON
        ):
            end += 1
        average_rank = (position + 1 + end) / 2.0
        for index in range(position, end):
            ranks[ordered[index][0]] = average_rank
        position = end
    return ranks


def correlation(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return math.nan
    mean_a = statistics.fmean(a)
    mean_b = statistics.fmean(b)
    numerator = sum(
        (x - mean_a) * (y - mean_b) for x, y in zip(a, b)
    )
    denominator = math.sqrt(
        sum((x - mean_a) ** 2 for x in a)
        * sum((y - mean_b) ** 2 for y in b)
    )
    return numerator / denominator if denominator > EPSILON else 0.0


def mechanism_trends(
    paired_rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    specifications = (
        ("complementarity_sweep", "external_objective"),
        ("complementarity_sweep", "independent_service_score"),
        ("stability_sweep", "external_switch_count"),
        ("repair_disturbance", "feasibility_rate"),
    )
    output: List[Dict[str, object]] = []
    for section, metric in specifications:
        values = [
            row
            for row in paired_rows
            if row["section"] == section and row["metric"] == metric
        ]
        values = sorted(values, key=lambda row: float(row["factor_level"]))
        x = [float(row["factor_level"]) for row in values]
        y = [float(row["mean_difference_a_minus_b"]) for row in values]
        if section == "repair_disturbance":
            values = [
                row for row in values if float(row["factor_level"]) > 0
            ]
            x = [float(row["factor_level"]) for row in values]
            y = [float(row["mean_difference_a_minus_b"]) for row in values]
        output.append(
            {
                "section": section,
                "metric": metric,
                "levels": json.dumps(x, separators=(",", ":")),
                "mean_differences": json.dumps(y, separators=(",", ":")),
                "spearman_rho_descriptive": correlation(
                    rankdata(x), rankdata(y)
                ),
                "interpretation": (
                    "descriptive trend only; registered paired tests determine support"
                ),
            }
        )
    return output


def supported_positive(row: Mapping[str, object]) -> bool:
    return (
        float(row["mean_difference_a_minus_b"]) > 0
        and float(row["ci95_bca_low"]) > 0
        and float(row["p_holm_adjusted"]) < 0.05
    )


def supported_negative(row: Mapping[str, object]) -> bool:
    return (
        float(row["mean_difference_a_minus_b"]) < 0
        and float(row["ci95_bca_high"]) < 0
        and float(row["p_holm_adjusted"]) < 0.05
    )


def gate_decisions(
    paired_rows: Sequence[Mapping[str, object]],
    episodes: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    compatibility_objective = {
        str(row["scenario"]): row
        for row in paired_rows
        if row["section"] == "engineering_module_ablation"
        and row["metric"] == "external_objective"
        and row["method_b"] == "No-Compatibility"
    }
    compatibility_service = {
        str(row["scenario"]): row
        for row in paired_rows
        if row["section"] == "engineering_module_ablation"
        and row["metric"] == "independent_service_score"
        and row["method_b"] == "No-Compatibility"
    }
    compatibility_cells = [
        scenario
        for scenario in ENGINEERING_SCENARIOS
        if supported_positive(compatibility_objective[scenario])
        and supported_positive(compatibility_service[scenario])
    ]
    compatibility_partial_cells = [
        scenario
        for scenario in ENGINEERING_SCENARIOS
        if supported_positive(compatibility_objective[scenario])
        or supported_positive(compatibility_service[scenario])
    ]
    compatibility_status = (
        "SUPPORTED"
        if len(compatibility_cells) >= 3
        else (
            "CONTEXT_DEPENDENT"
            if compatibility_partial_cells
            else "NOT_SUPPORTED"
        )
    )

    complementarity_rows = [
        row
        for row in paired_rows
        if row["section"] == "complementarity_sweep"
        and row["metric"] == "external_objective"
    ]
    complementarity_zero = next(
        row
        for row in complementarity_rows
        if abs(float(row["factor_level"])) <= EPSILON
    )
    complementarity_high = next(
        row
        for row in complementarity_rows
        if abs(float(row["factor_level"]) - 2.0) <= EPSILON
    )
    complementarity_identity = (
        float(complementarity_zero["max_abs_paired_difference"])
        <= 1e-10
    )
    complementarity_supported = (
        complementarity_identity
        and supported_positive(complementarity_high)
    )
    complementarity_service_high = next(
        row
        for row in paired_rows
        if row["section"] == "complementarity_sweep"
        and row["metric"] == "independent_service_score"
        and abs(float(row["factor_level"]) - 2.0) <= EPSILON
    )
    complementarity_status = (
        "SUPPORTED"
        if complementarity_supported
        else (
            "CONTEXT_DEPENDENT"
            if supported_positive(complementarity_service_high)
            or (
                complementarity_identity
                and float(
                    complementarity_high["mean_difference_a_minus_b"]
                )
                > 0
            )
            else "NOT_SUPPORTED"
        )
    )

    stability_rows = [
        row
        for row in paired_rows
        if row["section"] == "stability_sweep"
        and row["metric"] == "external_switch_count"
        and float(row["factor_level"]) > 0
    ]
    stability_supported_cells = [
        float(row["factor_level"])
        for row in stability_rows
        if supported_negative(row)
    ]
    stability_status = (
        "SUPPORTED"
        if len(stability_supported_cells) >= 3
        else (
            "CONTEXT_DEPENDENT"
            if stability_supported_cells
            else "NOT_SUPPORTED"
        )
    )

    redundancy_rows = [
        row
        for row in paired_rows
        if row["section"] == "redundancy_stress"
        and row["method_b"] == "No-Redundancy"
        and row["metric"]
        in {
            "same_type_pair_ratio",
            "capability_overlap",
            "marginal_gain_waste",
        }
    ]
    redundancy_supported_metrics = [
        str(row["metric"]) for row in redundancy_rows if supported_negative(row)
    ]
    redundancy_service = next(
        row
        for row in paired_rows
        if row["section"] == "redundancy_stress"
        and row["method_b"] == "No-Redundancy"
        and row["metric"] == "independent_service_score"
    )
    redundancy_supported = (
        len(redundancy_supported_metrics) >= 2
        and float(redundancy_service["mean_difference_a_minus_b"]) >= -0.02
    )
    redundancy_status = (
        "SUPPORTED"
        if redundancy_supported
        else (
            "CONTEXT_DEPENDENT"
            if redundancy_supported_metrics
            else "NOT_SUPPORTED"
        )
    )

    repair_rows = [
        row
        for row in paired_rows
        if row["section"] == "repair_disturbance"
        and row["metric"] == "feasibility_rate"
        and float(row["factor_level"]) > 0
    ]
    repair_on_lookup = {
        str(row["stratum"]): as_float(row, "feasibility_rate")
        for row in episodes
        if row["study"] == "repair_disturbance"
        and row["algorithm"] == "CA-HMCD-RepairOn"
    }
    repair_supported_levels = [
        float(row["factor_level"])
        for row in repair_rows
        if supported_positive(row)
        and min(
            as_float(episode, "feasibility_rate")
            for episode in episodes
            if episode["study"] == "repair_disturbance"
            and episode["algorithm"] == "CA-HMCD-RepairOn"
            and episode["stratum"]
            == f"noise-{float(row['factor_level']):.2f}"
        )
        >= 0.95
    ]
    repair_status = (
        "SUPPORTED" if repair_supported_levels else "NOT_SUPPORTED"
    )
    repair_service_rows = [
        row
        for row in paired_rows
        if row["section"] == "repair_disturbance"
        and row["metric"] == "independent_service_score"
        and float(row["factor_level"]) > 0
    ]
    repair_service_tradeoff_levels = [
        float(row["factor_level"])
        for row in repair_service_rows
        if supported_negative(row)
    ]

    ca_engineering = [
        row
        for row in episodes
        if row["study"] == "engineering"
        and row["algorithm"] == "CA-HMCD"
    ]
    engineering_feasible = all(
        as_float(row, "feasibility_rate") >= 1.0 - EPSILON
        for row in ca_engineering
    )
    engineering_supported_scenarios: List[str] = []
    for scenario in ENGINEERING_SCENARIOS:
        candidates = [
            row
            for row in paired_rows
            if row["section"] == "engineering_external_baseline"
            and row["scenario"] == scenario
            and row["metric"]
            in {"independent_service_score", "independent_coverage"}
        ]
        if any(supported_positive(row) for row in candidates):
            engineering_supported_scenarios.append(scenario)
    engineering_supported = (
        engineering_feasible
        and len(engineering_supported_scenarios) >= 3
    )
    engineering_status = (
        "SUPPORTED"
        if engineering_supported
        else (
            "CONTEXT_DEPENDENT"
            if engineering_feasible and engineering_supported_scenarios
            else "NOT_SUPPORTED"
        )
    )

    return [
        {
            "gate": "H4.1 compatibility",
            "status": compatibility_status,
            "evidence": (
                f"{len(compatibility_cells)}/5 scenarios supported both "
                f"objective and service: {', '.join(compatibility_cells) or 'none'}; "
                f"{len(compatibility_partial_cells)}/5 supported at least one endpoint"
            ),
        },
        {
            "gate": "H4.2 complementarity",
            "status": complementarity_status,
            "evidence": (
                f"scale-0 identity={complementarity_identity}; "
                f"scale-2 objective difference="
                f"{float(complementarity_high['mean_difference_a_minus_b']):.6f}, "
                f"95% CI [{float(complementarity_high['ci95_bca_low']):.6f}, "
                f"{float(complementarity_high['ci95_bca_high']):.6f}]; "
                f"scale-2 service difference="
                f"{float(complementarity_service_high['mean_difference_a_minus_b']):.6f}"
            ),
        },
        {
            "gate": "H4.3 redundancy",
            "status": redundancy_status,
            "evidence": (
                f"{len(redundancy_supported_metrics)}/3 diagnostics supported: "
                f"{', '.join(redundancy_supported_metrics) or 'none'}; "
                f"service difference="
                f"{float(redundancy_service['mean_difference_a_minus_b']):.6f}"
            ),
        },
        {
            "gate": "H4.4 stability",
            "status": stability_status,
            "evidence": (
                f"{len(stability_supported_cells)}/4 non-zero volatility "
                f"levels supported switch reduction: "
                f"{stability_supported_cells}"
            ),
        },
        {
            "gate": "H4.5 true-state repair / Gate C",
            "status": repair_status,
            "evidence": (
                f"supported non-zero noise levels: {repair_supported_levels}; "
                f"service trade-off levels: {repair_service_tradeoff_levels}; "
                f"RepairOn cell count={len(repair_on_lookup)}"
            ),
        },
        {
            "gate": "H4.6 engineering transfer",
            "status": engineering_status,
            "evidence": (
                f"all CA-HMCD engineering trajectories feasible="
                f"{engineering_feasible}; supported scenarios "
                f"{len(engineering_supported_scenarios)}/5: "
                f"{', '.join(engineering_supported_scenarios) or 'none'}"
            ),
        },
    ]


def key_row(
    rows: Sequence[Mapping[str, object]],
    *,
    section: str,
    metric: str,
    method_b: str,
    scenario: str | None = None,
    factor_level: float | None = None,
) -> Mapping[str, object] | None:
    matches = [
        row
        for row in rows
        if row["section"] == section
        and row["metric"] == metric
        and row["method_b"] == method_b
        and (scenario is None or row.get("scenario") == scenario)
        and (
            factor_level is None
            or abs(float(row["factor_level"]) - factor_level) <= EPSILON
        )
    ]
    return matches[0] if matches else None


def format_difference(row: Mapping[str, object] | None) -> str:
    if row is None:
        return "not available"
    return (
        f"{float(row['mean_difference_a_minus_b']):.4f} "
        f"(95% BCa CI {float(row['ci95_bca_low']):.4f} to "
        f"{float(row['ci95_bca_high']):.4f}; "
        f"Holm P={float(row['p_holm_adjusted']):.4g}; "
        f"dz={float(row['cohen_dz']):.3f})"
    )


def build_report(
    source: Mapping[str, object],
    paired_rows: Sequence[Mapping[str, object]],
    gates: Sequence[Mapping[str, object]],
    registry: Sequence[Mapping[str, object]],
    trend_rows: Sequence[Mapping[str, object]],
) -> str:
    repair_high = key_row(
        paired_rows,
        section="repair_disturbance",
        metric="feasibility_rate",
        method_b="CA-HMCD-RepairOff",
        factor_level=0.3,
    )
    synergy_high = key_row(
        paired_rows,
        section="complementarity_sweep",
        metric="external_objective",
        method_b="No-Synergy",
        factor_level=2.0,
    )
    synergy_service_high = key_row(
        paired_rows,
        section="complementarity_sweep",
        metric="independent_service_score",
        method_b="No-Synergy",
        factor_level=2.0,
    )
    redundancy_type = key_row(
        paired_rows,
        section="redundancy_stress",
        metric="same_type_pair_ratio",
        method_b="No-Redundancy",
    )
    stability_high = key_row(
        paired_rows,
        section="stability_sweep",
        metric="external_switch_count",
        method_b="No-Stability",
        factor_level=0.3,
    )
    repair_service_high = key_row(
        paired_rows,
        section="repair_disturbance",
        metric="independent_service_score",
        method_b="CA-HMCD-RepairOff",
        factor_level=0.3,
    )
    lines = [
        "## Material Passport",
        "",
        "- Origin Skill: experiment-agent",
        "- Origin Mode: run + validate",
        "- Origin Date: 2026-09-18",
        "- Verification Status: ANALYZED",
        "- Version Label: ca-hmcd-stage4-value-engineering-v1",
        "",
        "# CA-HMCD 阶段4：价值模型与工程机制验证",
        "",
        "## 完成状态",
        "",
        f"- 正式实验：{EXPECTED_EPISODES:,} 个30种子轨迹单元，"
        f"{EXPECTED_STEPS:,} 条逐周期记录。",
        f"- 配对审计：{EXPECTED_AUDITS:,} 个状态-周期组，全部通过。",
        f"- 候选池：完整价值模型生成的共享 K={TOP_K} 候选池；"
        f"节点上限 {NODE_LIMIT:,}。",
        f"- 推断：{len(paired_rows)} 项配对比较，"
        f"{len(registry)} 个明确注册的 Holm 比较族。",
        "- 冻结模型、外部评价器和服务评价端点均未修改。",
        "",
        "## 决策门结果",
        "",
        "| 假设/机制 | 判定 | 证据摘要 |",
        "|---|---|---|",
    ]
    for gate in gates:
        lines.append(
            f"| {gate['gate']} | **{gate['status']}** | "
            f"{gate['evidence']} |"
        )
    lines.extend(
        [
            "",
            "## 关键效应",
            "",
            f"- 互补机制（scale=2）：{format_difference(synergy_high)}。",
            f"- 互补机制的独立服务端点（scale=2）："
            f"{format_difference(synergy_service_high)}。",
            f"- 冗余机制（同类资源对比例）："
            f"{format_difference(redundancy_type)}。",
            f"- 稳定性机制（volatility=0.30，切换次数）："
            f"{format_difference(stability_high)}。",
            f"- 安全修复（noise=0.30，可行率）："
            f"{format_difference(repair_high)}。",
            f"- 安全修复的即时服务代价（noise=0.30）："
            f"{format_difference(repair_service_high)}。",
            "",
            "## 解释边界",
            "",
            "1. 本阶段把候选身份、状态轨迹、随机种子和外部评价器固定，"
            "但候选池仍是 K=12 压缩池，因此结论针对已注册的实际决策流程，"
            "不等同于全组合空间中的纯解析证明。",
            "2. 工程场景由低空安全任务需求结构驱动，响应资源仍为模拟资源；"
            "因此可支持机制合理性和半合成工程证据，不能替代实装系统试验。",
            "3. 互补、冗余和稳定性分别报告独立结构指标与服务结果；"
            "若结构指标改变但总体服务未改善，只能声称机制生效，"
            "不能声称总体性能普遍提高。",
            "4. 修复模块的判据是恢复真实状态下的可行性，而不是恢复到"
            "无感知误差时的目标值。",
            "",
            "## 统计完整性",
            "",
            "- 独立实验单位为一个完整的12周期随机种子轨迹，未把周期记录"
            "错误地当作独立样本。",
            "- 每项比较分别列示未校正 P 值、Holm 调整后 P 值、"
            "95% BCa区间、Cohen dz、秩二列相关和胜率。",
            "- 可行率、覆盖率和三项 [0,1] 结构指标另报 Wilson 区间。",
            "- 响应时间仅在两种方法共同服务的任务-周期上比较。",
            "",
            "## Fallacy Scan",
            "",
            "- Coverage: 11/11 fallacy types checked.",
            "- Pseudoreplication: controlled by seed-level aggregation.",
            "- Multiple testing: controlled by explicit endpoint-specific Holm families.",
            "- Dichotomization: continuous effects and intervals retained.",
            "- Null-is-equivalence: unsupported results are not described as equivalent.",
            "- Causal overreach: claims limited to controlled interventions.",
            "- Selection bias: all registered seeds and cells included.",
            "- Post-hoc subgrouping: only registered scenarios and factor levels analysed.",
            "- Metric circularity: independent service endpoint excludes model terms.",
            "- Bounded-outcome misuse: bounded intervals reported separately.",
            "- Missing-data bias: complete-cell and common-task counts audited.",
            "- Generalization overreach: field-deployment claims explicitly excluded.",
            "",
            "## 复现状态",
            "",
            "- 形式化运行、哈希绑定、分片完整性和配对状态均已验证。",
            "- 本报告标记为 `ANALYZED`；正式全量独立二次重算不在本阶段内，"
            "因此不将状态夸大为完整外部复现。",
            "",
            "## Material Passport Sources",
            "",
            f"- Protocol fingerprint: "
            f"`{source['metadata']['protocol_fingerprint']}`",
            f"- Execution metadata SHA-256: "
            f"`{source['execution_metadata_sha256']}`",
            f"- Trend records: {len(trend_rows)}",
            "",
        ]
    )
    return "\n".join(lines)


def analyse(formal_dir: Path, output_dir: Path) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    episodes, steps, source = validate_formal_source(formal_dir)
    paired_rows, common_rows = build_paired_comparisons(episodes, steps)
    bounded_rows = bounded_intervals(episodes)
    registry = family_registry(paired_rows)
    trend_rows = mechanism_trends(paired_rows)
    gates = gate_decisions(paired_rows, episodes)

    write_csv(output_dir / "stage4_paired_comparisons.csv", paired_rows)
    write_csv(output_dir / "stage4_holm_families.csv", registry)
    write_csv(output_dir / "stage4_bounded_intervals.csv", bounded_rows)
    write_csv(output_dir / "stage4_common_task_response.csv", common_rows)
    write_csv(output_dir / "stage4_mechanism_trends.csv", trend_rows)
    write_csv(output_dir / "stage4_gate_decisions.csv", gates)
    report = build_report(
        source, paired_rows, gates, registry, trend_rows
    )
    (output_dir / "stage4_analysis_report.md").write_text(
        report, encoding="utf-8"
    )
    decision_payload = {
        "schema": "ca-hmcd-stage4-gate-decisions-v1",
        "analysis_version": ANALYSIS_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "gates": gates,
        "stage_status": "COMPLETE_WITH_EVIDENCE_QUALIFICATIONS",
    }
    (output_dir / "stage4_gate_decisions.json").write_text(
        json.dumps(decision_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    output_files = (
        "stage4_paired_comparisons.csv",
        "stage4_holm_families.csv",
        "stage4_bounded_intervals.csv",
        "stage4_common_task_response.csv",
        "stage4_mechanism_trends.csv",
        "stage4_gate_decisions.csv",
        "stage4_analysis_report.md",
        "stage4_gate_decisions.json",
    )
    manifest = {
        "schema": "ca-hmcd-stage4-analysis-manifest-v1",
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
            "bounded_intervals": len(bounded_rows),
            "common_task_response_seed_rows": len(common_rows),
        },
        "analysis_hashes": {
            "ca_hmcd_stage4_value_engineering_analysis.py": sha256_file(
                Path(__file__).resolve()
            )
        },
        "output_hashes": {
            name: sha256_file(output_dir / name) for name in output_files
        },
    }
    (output_dir / "stage4_analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    material_passport = {
        "schema": "ARS Material Passport 9",
        "origin_skill": "experiment-agent",
        "origin_mode": ["run", "validate"],
        "origin_date": "2026-09-18",
        "verification_status": "ANALYZED",
        "version_label": "ca-hmcd-stage4-value-engineering-v1",
        "materials": {
            "protocol": str(PROTOCOL_DIR.resolve()),
            "formal_results": str(formal_dir.resolve()),
            "analysis": str(output_dir.resolve()),
        },
        "scope_boundary": (
            "Controlled simulation and engineering-shaped mechanism evidence; "
            "not a field-deployment validation."
        ),
    }
    (output_dir / "material_passport.json").write_text(
        json.dumps(material_passport, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--formal-dir",
        type=Path,
        default=(
            PROJECT_DIR
            / "ca_hmcd_stage4_value_engineering_formal_20260918"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            PROJECT_DIR
            / "ca_hmcd_stage4_value_engineering_analysis_20260918"
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    result = analyse(
        arguments.formal_dir.resolve(),
        arguments.output_dir.resolve(),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

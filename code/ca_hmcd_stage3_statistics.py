"""Phase-3 statistical upgrade for the CA-HMCD experiments.

This module preserves the random seed as the independent experimental unit,
adds bounded BCa bootstrap intervals, registers every Holm correction family,
and recomputes response time on task-periods served by both compared methods.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import random
import statistics
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from ca_hmcd_simulation import (
    ALGORITHMS,
    paired_statistics,
    run_episode,
    scenario_parameters,
    write_csv,
)


PRIMARY_SCENARIOS = ("balanced", "scarce", "volatile", "semisynthetic")
PRIMARY_BASELINES = tuple(algorithm for algorithm in ALGORITHMS if algorithm != "CA-HMCD")
ENGINEERING_SCENARIOS = (
    "airport_corridor",
    "energy_facility",
    "public_event",
    "urban_corridor",
    "industrial_zone",
)
BOUND_EPSILON = 1e-12
EVENT_RATE_METRICS = {
    "feasibility_rate",
    "raw_feasible",
    "repair_applied",
    "fallback",
    "node_limit_hit",
}


def stable_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("quantile requires at least one value")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    probability = min(1.0, max(0.0, probability))
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return (
        float(sorted_values[lower]) * (1.0 - fraction)
        + float(sorted_values[upper]) * fraction
    )


def bca_mean_interval(
    values: Sequence[float],
    confidence: float = 0.95,
    resamples: int = 10000,
    seed: int = 0,
    bounds: Tuple[float, float] | None = None,
) -> Tuple[float, float]:
    """Return a deterministic bias-corrected and accelerated interval for a mean."""
    data = [float(value) for value in values]
    if not data:
        raise ValueError("BCa interval requires at least one value")
    observed = statistics.fmean(data)
    if len(data) < 2 or max(data) - min(data) <= BOUND_EPSILON:
        value = observed
        if bounds is not None:
            value = min(bounds[1], max(bounds[0], value))
        return value, value

    rng = random.Random(seed)
    n = len(data)
    bootstrap = sorted(
        sum(data[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    )
    less = sum(value < observed for value in bootstrap)
    equal = sum(abs(value - observed) <= BOUND_EPSILON for value in bootstrap)
    probability_less = (less + 0.5 * equal) / resamples
    probability_less = min(
        1.0 - 0.5 / resamples,
        max(0.5 / resamples, probability_less),
    )
    normal = NormalDist()
    bias_correction = normal.inv_cdf(probability_less)

    total = sum(data)
    jackknife = [(total - value) / (n - 1) for value in data]
    jackknife_mean = statistics.fmean(jackknife)
    numerator = sum((jackknife_mean - value) ** 3 for value in jackknife)
    denominator = 6.0 * (
        sum((jackknife_mean - value) ** 2 for value in jackknife) ** 1.5
    )
    acceleration = numerator / denominator if denominator > BOUND_EPSILON else 0.0

    alpha = 1.0 - confidence

    def adjusted_probability(probability: float) -> float:
        z_alpha = normal.inv_cdf(probability)
        numerator_value = bias_correction + z_alpha
        denominator_value = 1.0 - acceleration * numerator_value
        if abs(denominator_value) <= BOUND_EPSILON:
            return probability
        adjusted_z = bias_correction + numerator_value / denominator_value
        return min(1.0, max(0.0, normal.cdf(adjusted_z)))

    low = quantile(bootstrap, adjusted_probability(alpha / 2.0))
    high = quantile(bootstrap, adjusted_probability(1.0 - alpha / 2.0))
    if bounds is not None:
        low = min(bounds[1], max(bounds[0], low))
        high = min(bounds[1], max(bounds[0], high))
    return low, high


def wilson_score_interval(
    values: Sequence[float],
    confidence: float = 0.95,
) -> Tuple[float, float]:
    """Wilson interval using one fractional event-rate contribution per seed."""
    data = [float(value) for value in values]
    if not data:
        raise ValueError("Wilson interval requires at least one value")
    if any(
        value < -BOUND_EPSILON or value > 1.0 + BOUND_EPSILON
        for value in data
    ):
        raise ValueError("Wilson event-rate inputs must be in [0, 1]")
    n = len(data)
    proportion = statistics.fmean(data)
    z_value = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    denominator = 1.0 + z_value * z_value / n
    center = (proportion + z_value * z_value / (2.0 * n)) / denominator
    half_width = (
        z_value
        * math.sqrt(
            proportion * (1.0 - proportion) / n
            + z_value * z_value / (4.0 * n * n)
        )
        / denominator
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)


def holm_adjust(rows: Sequence[MutableMapping[str, object]]) -> None:
    """Apply Holm step-down adjustment separately within each registered family."""
    families: Dict[str, List[MutableMapping[str, object]]] = defaultdict(list)
    for row in rows:
        families[str(row["family_id"])].append(row)
    for family_rows in families.values():
        ordered = sorted(family_rows, key=lambda row: float(row["p_unadjusted"]))
        running = 0.0
        family_size = len(ordered)
        for rank, row in enumerate(ordered):
            adjusted = min(
                1.0,
                (family_size - rank) * float(row["p_unadjusted"]),
            )
            running = max(running, adjusted)
            row["p_holm_adjusted"] = running
        for row in family_rows:
            row["holm_family_size"] = family_size
            row["significant_after_holm_0_05"] = (
                float(row["p_holm_adjusted"]) < 0.05
            )


def filter_records(
    records: Sequence[Mapping[str, object]], **conditions: object
) -> List[Mapping[str, object]]:
    return [
        record
        for record in records
        if all(record.get(key) == value for key, value in conditions.items())
    ]


def paired_row(
    a_records: Sequence[Mapping[str, object]],
    b_records: Sequence[Mapping[str, object]],
    metric: str,
    family_id: str,
    section: str,
    comparison_label: str,
    method_a: str,
    method_b: str,
    favorable_direction: str,
    bootstrap_resamples: int,
    context: Mapping[str, object] | None = None,
) -> Dict[str, object]:
    a_lookup = {int(record["seed"]): float(record[metric]) for record in a_records}
    b_lookup = {int(record["seed"]): float(record[metric]) for record in b_records}
    paired_seeds = sorted(set(a_lookup) & set(b_lookup))
    a_values = [a_lookup[seed] for seed in paired_seeds]
    b_values = [b_lookup[seed] for seed in paired_seeds]
    differences = [a - b for a, b in zip(a_values, b_values)]
    stats = paired_statistics(a_values, b_values)
    ci_low, ci_high = bca_mean_interval(
        differences,
        resamples=bootstrap_resamples,
        seed=stable_seed(family_id, comparison_label, metric),
    )
    wins = sum(
        (difference > BOUND_EPSILON)
        if favorable_direction == "higher"
        else (difference < -BOUND_EPSILON)
        for difference in differences
    )
    ties = sum(abs(difference) <= BOUND_EPSILON for difference in differences)
    row: Dict[str, object] = {
        "family_id": family_id,
        "section": section,
        "comparison": comparison_label,
        "metric": metric,
        "method_a": method_a,
        "method_b": method_b,
        "favorable_direction_for_method_a": favorable_direction,
        "n_independent_seed_pairs": len(paired_seeds),
        "method_a_mean": statistics.fmean(a_values) if a_values else math.nan,
        "method_b_mean": statistics.fmean(b_values) if b_values else math.nan,
        "mean_difference_a_minus_b": (
            statistics.fmean(differences) if differences else math.nan
        ),
        "mean_difference_ci95_low": ci_low,
        "mean_difference_ci95_high": ci_high,
        "ci_estimand": "mean paired difference at the seed level",
        "ci_method": f"BCa paired bootstrap, {bootstrap_resamples} resamples",
        "wilcoxon_w": stats["wilcoxon_w"],
        "p_value_test": "two-sided paired Wilcoxon signed-rank test",
        "p_unadjusted": stats["p_value"],
        "p_holm_adjusted": math.nan,
        "cohen_dz": stats["cohen_dz"],
        "rank_biserial": stats["rank_biserial"],
        "win_rate": (wins + 0.5 * ties) / max(1, len(differences)),
    }
    if context:
        row.update(context)
    return row


def register_family(
    registry: List[Dict[str, object]],
    family_id: str,
    section: str,
    endpoint: str,
    definition: str,
    rows: Sequence[Mapping[str, object]],
) -> None:
    registry.append(
        {
            "family_id": family_id,
            "section": section,
            "endpoint": endpoint,
            "independent_unit": "one complete seeded simulation trajectory",
            "family_definition": definition,
            "number_of_comparisons": len(rows),
            "members": "; ".join(str(row["comparison"]) for row in rows),
            "multiplicity_control": "Holm step-down, two-sided family-wise alpha=0.05",
        }
    )


def append_algorithm_family(
    output: List[Dict[str, object]],
    registry: List[Dict[str, object]],
    records: Sequence[Mapping[str, object]],
    metric: str,
    family_id: str,
    section: str,
    definition: str,
    baselines: Sequence[str],
    favorable_direction: str,
    bootstrap_resamples: int,
    context: Mapping[str, object] | None = None,
) -> None:
    family_rows: List[Dict[str, object]] = []
    for baseline in baselines:
        row = paired_row(
            filter_records(records, algorithm="CA-HMCD"),
            filter_records(records, algorithm=baseline),
            metric,
            family_id,
            section,
            f"CA-HMCD vs {baseline}",
            "CA-HMCD",
            baseline,
            favorable_direction,
            bootstrap_resamples,
            context,
        )
        family_rows.append(row)
    output.extend(family_rows)
    register_family(
        registry, family_id, section, metric, definition, family_rows
    )


def rerun_main_with_task_response(
    config: Mapping[str, object],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    episode_records: List[Dict[str, object]] = []
    task_response_records: List[Dict[str, object]] = []
    seeds = int(config["seeds"])
    steps = int(config["steps"])
    top_k = int(config["top_k"])
    base_seed = int(config["base_seed"])
    primary_node_limit = int(
        dict(config.get("node_budgets", {})).get("primary", 12000)
    )
    for scenario in PRIMARY_SCENARIOS:
        parameters = scenario_parameters(scenario)
        for algorithm in ALGORITHMS:
            for replication in range(seeds):
                seed = base_seed + replication
                metrics, rows = run_episode(
                    algorithm,
                    seed,
                    steps,
                    int(parameters["resources"]),
                    int(parameters["tasks"]),
                    scenario,
                    top_k,
                    node_limit_override=primary_node_limit,
                )
                episode_records.append(
                    {
                        "scenario": scenario,
                        "algorithm": algorithm,
                        "seed": seed,
                        **metrics,
                    }
                )
                for row in rows:
                    responses = json.loads(
                        str(row["served_task_response_times_json"])
                    )
                    for task_id, response in responses.items():
                        task_response_records.append(
                            {
                                "scenario": scenario,
                                "algorithm": algorithm,
                                "seed": seed,
                                "step": int(row["step"]),
                                "task_id": task_id,
                                "response_time": float(response),
                                "state_fingerprint": row["state_fingerprint"],
                            }
                        )
        print(f"[stage3] task-response replay: {scenario} complete", flush=True)
    return episode_records, task_response_records


def rerun_perception_noise(
    config: Mapping[str, object],
) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    seeds = int(config["seeds"])
    steps = max(8, int(config["steps"]) // 2)
    top_k = int(config["top_k"])
    base_seed = int(config["base_seed"])
    validation_node_limit = int(
        dict(config.get("node_budgets", {})).get("validation", 6000)
    )
    parameters = scenario_parameters("volatile")
    for noise in (0.0, 0.05, 0.10, 0.20, 0.30):
        for replication in range(seeds):
            seed = base_seed + replication
            metrics, _ = run_episode(
                "CA-HMCD",
                seed,
                steps,
                int(parameters["resources"]),
                int(parameters["tasks"]),
                "volatile",
                top_k,
                noise_level=noise,
                node_limit_override=validation_node_limit,
            )
            records.append(
                {
                    "noise_level": noise,
                    "algorithm": "CA-HMCD",
                    "seed": seed,
                    **metrics,
                }
            )
        print(f"[stage3] perception replay: noise={noise:g} complete", flush=True)
    return records


def replay_consistency(
    source_records: Sequence[Mapping[str, object]],
    replay_records: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    source_lookup = {
        (str(record["scenario"]), str(record["algorithm"]), int(record["seed"])): record
        for record in source_records
    }
    replay_lookup = {
        (str(record["scenario"]), str(record["algorithm"]), int(record["seed"])): record
        for record in replay_records
    }
    metrics = (
        "external_objective",
        "base_objective",
        "independent_service_score",
        "independent_coverage",
        "response_time",
        "same_type_pair_ratio",
        "capability_overlap",
        "marginal_gain_waste",
        "feasibility_rate",
        "fallback",
    )
    rows: List[Dict[str, object]] = []
    shared_keys = sorted(set(source_lookup) & set(replay_lookup))
    for metric in metrics:
        maximum = max(
            abs(
                float(source_lookup[key][metric])
                - float(replay_lookup[key][metric])
            )
            for key in shared_keys
        )
        rows.append(
            {
                "metric": metric,
                "compared_episode_count": len(shared_keys),
                "maximum_absolute_difference": maximum,
                "identical_within_1e_12": maximum <= 1e-12,
            }
        )
    return rows


def common_task_response_rows(
    task_records: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], List[Dict[str, object]]]:
    indexed: Dict[
        Tuple[str, str, int], Dict[Tuple[int, str], float]
    ] = defaultdict(dict)
    for record in task_records:
        indexed[
            (
                str(record["scenario"]),
                str(record["algorithm"]),
                int(record["seed"]),
            )
        ][(int(record["step"]), str(record["task_id"]))] = float(
            record["response_time"]
        )
    seeds = sorted({int(record["seed"]) for record in task_records})
    paired_episode_records: List[Dict[str, object]] = []
    summary_rows: List[Dict[str, object]] = []
    registry: List[Dict[str, object]] = []
    for scenario in PRIMARY_SCENARIOS:
        family_id = f"response.common_tasks.{scenario}"
        family_rows: List[Dict[str, object]] = []
        for baseline in PRIMARY_BASELINES:
            a_by_seed: Dict[int, float] = {}
            b_by_seed: Dict[int, float] = {}
            pooled_a: List[float] = []
            pooled_b: List[float] = []
            counts: List[int] = []
            for seed in seeds:
                a_values_by_task = indexed.get(
                    (scenario, "CA-HMCD", seed), {}
                )
                b_values_by_task = indexed.get((scenario, baseline, seed), {})
                common_keys = sorted(
                    set(a_values_by_task) & set(b_values_by_task)
                )
                if not common_keys:
                    continue
                a_values = [a_values_by_task[key] for key in common_keys]
                b_values = [b_values_by_task[key] for key in common_keys]
                a_mean = statistics.fmean(a_values)
                b_mean = statistics.fmean(b_values)
                a_by_seed[seed] = a_mean
                b_by_seed[seed] = b_mean
                pooled_a.extend(a_values)
                pooled_b.extend(b_values)
                counts.append(len(common_keys))
                paired_episode_records.append(
                    {
                        "scenario": scenario,
                        "baseline": baseline,
                        "seed": seed,
                        "common_task_period_count": len(common_keys),
                        "ca_hmcd_mean_response_time": a_mean,
                        "baseline_mean_response_time": b_mean,
                        "difference_ca_minus_baseline": a_mean - b_mean,
                    }
                )
            a_records = [
                {"seed": seed, "common_response_time": value}
                for seed, value in a_by_seed.items()
            ]
            b_records = [
                {"seed": seed, "common_response_time": value}
                for seed, value in b_by_seed.items()
            ]
            row = paired_row(
                a_records,
                b_records,
                "common_response_time",
                family_id,
                "main common-task response time",
                f"{scenario}: CA-HMCD vs {baseline}",
                "CA-HMCD",
                baseline,
                "lower",
                bootstrap_resamples,
                {
                    "scenario": scenario,
                    "common_task_periods_total": sum(counts),
                    "common_task_periods_median_per_seed": (
                        statistics.median(counts) if counts else 0
                    ),
                    "seeds_without_common_tasks": len(seeds) - len(counts),
                    "ca_hmcd_pooled_task_mean": (
                        statistics.fmean(pooled_a) if pooled_a else math.nan
                    ),
                    "baseline_pooled_task_mean": (
                        statistics.fmean(pooled_b) if pooled_b else math.nan
                    ),
                },
            )
            family_rows.append(row)
        summary_rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "main common-task response time",
            "response time among task-periods served by both methods",
            (
                f"Within {scenario}, CA-HMCD was compared with all five primary "
                "baselines; Holm adjustment covers those five contrasts."
            ),
            family_rows,
        )
    return summary_rows, registry, paired_episode_records


def append_standard_families(
    raw: Mapping[str, object],
    perception_records: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    rows: List[Dict[str, object]] = []
    registry: List[Dict[str, object]] = []

    primary_records = list(raw["episodes"])
    primary_metrics = (
        ("external_objective", "higher"),
        ("base_objective", "higher"),
        ("independent_service_score", "higher"),
        ("independent_coverage", "higher"),
        ("external_switch_count", "lower"),
    )
    for scenario in PRIMARY_SCENARIOS:
        subset = filter_records(primary_records, scenario=scenario)
        for metric, direction in primary_metrics:
            family_id = f"primary.{metric}.{scenario}"
            append_algorithm_family(
                rows,
                registry,
                subset,
                metric,
                family_id,
                "primary experiment",
                (
                    f"Within {scenario}, the prespecified CA-HMCD contrast "
                    "against each of the five primary baselines."
                ),
                PRIMARY_BASELINES,
                direction,
                bootstrap_resamples,
                {"scenario": scenario},
            )

    ablation_records = list(raw["ablation"])
    ablation_baselines = (
        "No-Compatibility",
        "No-Redundancy",
        "No-Stability",
        "No-Synergy",
    )
    for metric in ("external_objective", "base_objective", "independent_service_score"):
        append_algorithm_family(
            rows,
            registry,
            ablation_records,
            metric,
            f"ablation.{metric}.balanced",
            "module ablation",
            (
                "All four balanced-scenario module ablations for the same "
                f"{metric} endpoint."
            ),
            ablation_baselines,
            "higher",
            bootstrap_resamples,
            {"scenario": "balanced"},
        )

    mechanism_records = list(raw["mechanism"])
    mechanism_specs = (
        (
            "synergy_strength",
            "No-Synergy",
            "external_objective",
            "higher",
        ),
        (
            "synergy_strength",
            "No-Synergy",
            "independent_service_score",
            "higher",
        ),
        (
            "dynamic_volatility",
            "No-Stability",
            "external_objective",
            "higher",
        ),
        (
            "dynamic_volatility",
            "No-Stability",
            "external_switch_count",
            "lower",
        ),
    )
    for experiment, baseline, metric, direction in mechanism_specs:
        family_id = f"mechanism.{experiment}.{metric}"
        family_rows: List[Dict[str, object]] = []
        levels = sorted(
            {
                float(record["level"])
                for record in mechanism_records
                if record["experiment"] == experiment
            }
        )
        for level in levels:
            subset = filter_records(
                mechanism_records, experiment=experiment, level=level
            )
            family_rows.append(
                paired_row(
                    filter_records(subset, algorithm="CA-HMCD"),
                    filter_records(subset, algorithm=baseline),
                    metric,
                    family_id,
                    "mechanism validation",
                    f"{experiment}={level:g}: CA-HMCD vs {baseline}",
                    "CA-HMCD",
                    baseline,
                    direction,
                    bootstrap_resamples,
                    {"experiment": experiment, "level": level},
                )
            )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "mechanism validation",
            metric,
            (
                f"All five controlled {experiment} levels for the {metric} "
                "mechanism claim."
            ),
            family_rows,
        )

    for metric, direction in (
        ("external_objective", "higher"),
        ("independent_service_score", "higher"),
        ("feasibility_rate", "higher"),
    ):
        family_id = f"perception_noise.{metric}"
        zero_records = filter_records(perception_records, noise_level=0.0)
        family_rows = []
        for noise in (0.05, 0.10, 0.20, 0.30):
            family_rows.append(
                paired_row(
                    filter_records(perception_records, noise_level=noise),
                    zero_records,
                    metric,
                    family_id,
                    "perception-error robustness",
                    f"noise={noise:g} vs noise=0",
                    f"CA-HMCD at noise={noise:g}",
                    "CA-HMCD at noise=0",
                    direction,
                    bootstrap_resamples,
                    {"noise_level": noise},
                )
            )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "perception-error robustness",
            metric,
            (
                "All four nonzero perception-error levels compared with the "
                f"zero-error reference for {metric}."
            ),
            family_rows,
        )

    redundancy_records = list(raw["redundancy_stress"])
    redundancy_baselines = ("No-Redundancy", "No-Synergy", "Greedy")
    for metric, direction in (
        ("external_objective", "higher"),
        ("same_type_pair_ratio", "lower"),
        ("capability_overlap", "lower"),
        ("marginal_gain_waste", "lower"),
    ):
        append_algorithm_family(
            rows,
            registry,
            redundancy_records,
            metric,
            f"redundancy_stress.{metric}",
            "redundancy mechanism",
            (
                "All three redundancy-stress baselines for the same "
                f"{metric} endpoint."
            ),
            redundancy_baselines,
            direction,
            bootstrap_resamples,
            {"scenario": "redundancy_stress"},
        )

    scalability_records = list(raw["scalability"])
    scales = sorted(
        {
            (int(record["resources"]), int(record["tasks"]))
            for record in scalability_records
        }
    )
    for metric, direction in (
        ("external_objective", "higher"),
        ("independent_service_score", "higher"),
        ("end_to_end_runtime_ms", "lower"),
        ("fallback", "lower"),
    ):
        family_id = f"scalability.{metric}"
        family_rows = []
        for resources, tasks in scales:
            subset = filter_records(
                scalability_records, resources=resources, tasks=tasks
            )
            family_rows.append(
                paired_row(
                    filter_records(subset, algorithm="CA-HMCD"),
                    filter_records(subset, algorithm="Greedy"),
                    metric,
                    family_id,
                    "scalability",
                    f"{resources} resources/{tasks} tasks: CA-HMCD vs Greedy",
                    "CA-HMCD",
                    "Greedy",
                    direction,
                    bootstrap_resamples,
                    {"resources": resources, "tasks": tasks},
                )
            )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "scalability",
            metric,
            (
                "CA-HMCD versus Greedy across all seven scale configurations "
                f"for {metric}."
            ),
            family_rows,
        )

    engineering_records = list(raw["engineering_validation"])
    for scenario in ENGINEERING_SCENARIOS:
        subset = filter_records(engineering_records, scenario=scenario)
        append_algorithm_family(
            rows,
            registry,
            subset,
            "independent_service_score",
            f"engineering.independent_service_score.{scenario}",
            "engineering validation",
            (
                f"Within {scenario}, CA-HMCD against the three external "
                "engineering baselines."
            ),
            ("External-Auction", "Greedy", "Random"),
            "higher",
            bootstrap_resamples,
            {"scenario": scenario},
        )

    parameter_records = list(raw["parameter_robustness"])
    parameters = sorted({str(record["parameter"]) for record in parameter_records})
    for parameter in parameters:
        family_id = f"parameter_robustness.{parameter}.independent_service_score"
        parameter_subset = filter_records(parameter_records, parameter=parameter)
        levels = sorted({float(record["level"]) for record in parameter_subset})
        family_rows = []
        for level in levels:
            level_subset = filter_records(parameter_subset, level=level)
            for baseline in ("External-Auction", "Greedy"):
                family_rows.append(
                    paired_row(
                        filter_records(level_subset, algorithm="CA-HMCD"),
                        filter_records(level_subset, algorithm=baseline),
                        "independent_service_score",
                        family_id,
                        "parameter robustness",
                        f"{parameter}={level:g}: CA-HMCD vs {baseline}",
                        "CA-HMCD",
                        baseline,
                        "higher",
                        bootstrap_resamples,
                        {"parameter": parameter, "level": level},
                    )
                )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "parameter robustness",
            "independent_service_score",
            (
                f"All three {parameter} levels and both external baselines "
                "within one parameter-specific family."
            ),
            family_rows,
        )

    pool_records = list(raw["external_baseline_pool_fairness"])
    for pool_mode in ("independent", "shared"):
        family_id = f"candidate_pool.{pool_mode}.independent_service_score"
        family_rows = []
        for scenario in PRIMARY_SCENARIOS:
            subset = filter_records(
                pool_records,
                candidate_pool_mode=pool_mode,
                scenario=scenario,
            )
            for baseline in ("External-Auction", "Greedy"):
                family_rows.append(
                    paired_row(
                        filter_records(subset, algorithm="CA-HMCD"),
                        filter_records(subset, algorithm=baseline),
                        "independent_service_score",
                        family_id,
                        "candidate-pool fairness",
                        f"{scenario}: CA-HMCD vs {baseline}",
                        "CA-HMCD",
                        baseline,
                        "higher",
                        bootstrap_resamples,
                        {
                            "scenario": scenario,
                            "candidate_pool_mode": pool_mode,
                        },
                    )
                )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "candidate-pool fairness",
            "independent_service_score",
            (
                f"All four scenarios and two external baselines under the "
                f"{pool_mode} candidate-pool protocol."
            ),
            family_rows,
        )

    solver_records = list(raw["solver_fairness"])
    for scenario in ("balanced", "scarce"):
        family_id = f"solver_fairness.independent_service_score.{scenario}"
        subset = filter_records(solver_records, scenario=scenario)
        contrasts = (
            ("CA-HMCD", "CA-HMCD-GreedySolver"),
            ("No-Synergy", "No-Synergy-GreedySolver"),
        )
        family_rows = []
        for method_a, method_b in contrasts:
            family_rows.append(
                paired_row(
                    filter_records(subset, algorithm=method_a),
                    filter_records(subset, algorithm=method_b),
                    "independent_service_score",
                    family_id,
                    "solver fairness",
                    f"{method_a} vs {method_b}",
                    method_a,
                    method_b,
                    "higher",
                    bootstrap_resamples,
                    {"scenario": scenario},
                )
            )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "solver fairness",
            "independent_service_score",
            (
                f"The two model-matched branch-and-bound versus greedy-solver "
                f"contrasts in {scenario}."
            ),
            family_rows,
        )

    pool_mode_records = list(raw["external_baseline_pool_fairness"])
    for algorithm in ("CA-HMCD", "External-Auction", "Greedy"):
        family_id = (
            f"pool_mode_effect.external_fairness.{algorithm}."
            "independent_service_score"
        )
        family_rows = []
        for scenario in PRIMARY_SCENARIOS:
            algorithm_records = filter_records(
                pool_mode_records, scenario=scenario, algorithm=algorithm
            )
            family_rows.append(
                paired_row(
                    filter_records(
                        algorithm_records, candidate_pool_mode="independent"
                    ),
                    filter_records(algorithm_records, candidate_pool_mode="shared"),
                    "independent_service_score",
                    family_id,
                    "candidate-pool sensitivity",
                    f"{scenario}: independent vs shared pool",
                    f"{algorithm}, independent pool",
                    f"{algorithm}, shared pool",
                    "higher",
                    bootstrap_resamples,
                    {"scenario": scenario, "algorithm": algorithm},
                )
            )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "candidate-pool sensitivity",
            "independent_service_score",
            (
                f"Independent-minus-shared candidate-pool contrasts for "
                f"{algorithm} across all four primary scenarios."
            ),
            family_rows,
        )

    ablation_pool_records = list(raw["candidate_pool_sensitivity"])
    for algorithm in ("No-Synergy", "No-Stability"):
        family_id = (
            f"pool_mode_effect.ablation.{algorithm}.independent_service_score"
        )
        family_rows = []
        for scenario in sorted(
            {str(record["scenario"]) for record in ablation_pool_records}
        ):
            algorithm_records = filter_records(
                ablation_pool_records, scenario=scenario, algorithm=algorithm
            )
            family_rows.append(
                paired_row(
                    filter_records(
                        algorithm_records, candidate_pool_mode="independent"
                    ),
                    filter_records(algorithm_records, candidate_pool_mode="shared"),
                    "independent_service_score",
                    family_id,
                    "candidate-pool sensitivity",
                    f"{scenario}: independent vs shared pool",
                    f"{algorithm}, independent pool",
                    f"{algorithm}, shared pool",
                    "higher",
                    bootstrap_resamples,
                    {"scenario": scenario, "algorithm": algorithm},
                )
            )
        rows.extend(family_rows)
        register_family(
            registry,
            family_id,
            "candidate-pool sensitivity",
            "independent_service_score",
            (
                f"The available ablation pool-mode contrast for {algorithm}; "
                "this is a one-comparison family."
            ),
            family_rows,
        )

    pruning_records = list(raw["pruning_control"])
    append_algorithm_family(
        rows,
        registry,
        pruning_records,
        "external_objective",
        "pruning_control.external_objective",
        "pruning control",
        "The prespecified CA-HMCD versus No-Pruning control.",
        ("No-Pruning",),
        "higher",
        bootstrap_resamples,
        {"scenario": "balanced"},
    )
    return rows, registry


def bounded_interval_rows(
    datasets: Sequence[
        Tuple[
            str,
            Sequence[Mapping[str, object]],
            Sequence[str],
            Sequence[str],
        ]
    ],
    bootstrap_resamples: int,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for dataset_name, records, group_keys, metrics in datasets:
        groups: Dict[Tuple[object, ...], List[Mapping[str, object]]] = defaultdict(list)
        for record in records:
            groups[tuple(record[key] for key in group_keys)].append(record)
        for group, group_records in sorted(
            groups.items(), key=lambda item: tuple(str(value) for value in item[0])
        ):
            context = dict(zip(group_keys, group))
            for metric in metrics:
                values = [float(record[metric]) for record in group_records]
                if any(
                    value < -BOUND_EPSILON or value > 1.0 + BOUND_EPSILON
                    for value in values
                ):
                    raise ValueError(
                        f"{dataset_name}/{metric} contains values outside [0, 1]"
                    )
                if metric in EVENT_RATE_METRICS:
                    low, high = wilson_score_interval(values)
                    interval_method = (
                        "cluster-level Wilson score interval using one "
                        "seed-level event-rate contribution per independent seed"
                    )
                else:
                    low, high = bca_mean_interval(
                        values,
                        resamples=bootstrap_resamples,
                        seed=stable_seed(dataset_name, group, metric),
                        bounds=(0.0, 1.0),
                    )
                    interval_method = (
                        f"bounded BCa bootstrap on seed-level episode means, "
                        f"{bootstrap_resamples} resamples"
                    )
                row: Dict[str, object] = {
                    "dataset": dataset_name,
                    **context,
                    "metric": metric,
                    "n_independent_seeds": len(values),
                    "mean": statistics.fmean(values),
                    "ci95_low": low,
                    "ci95_high": high,
                    "interval_method": interval_method,
                }
                rows.append(row)
    return rows


def sample_size_rows(
    registry: Sequence[Mapping[str, object]],
    paired_rows: Sequence[Mapping[str, object]],
    alpha: float = 0.05,
) -> List[Dict[str, object]]:
    n_by_family: Dict[str, int] = {}
    for row in paired_rows:
        family_id = str(row["family_id"])
        n_value = int(row["n_independent_seed_pairs"])
        n_by_family[family_id] = min(n_value, n_by_family.get(family_id, n_value))
    normal = NormalDist()
    rows: List[Dict[str, object]] = []
    for family in registry:
        family_id = str(family["family_id"])
        family_size = int(family["number_of_comparisons"])
        n_value = n_by_family[family_id]
        alpha_per_comparison = alpha / max(1, family_size)
        for power in (0.80, 0.90):
            detectable_dz = (
                normal.inv_cdf(1.0 - alpha_per_comparison / 2.0)
                + normal.inv_cdf(power)
            ) / math.sqrt(n_value)
            rows.append(
                {
                    "family_id": family_id,
                    "family_size": family_size,
                    "minimum_independent_seed_pairs": n_value,
                    "familywise_alpha": alpha,
                    "conservative_bonferroni_alpha": alpha_per_comparison,
                    "target_power": power,
                    "approximate_minimum_detectable_paired_cohen_dz": detectable_dz,
                    "basis": (
                        "normal-approximation sensitivity calculation using the "
                        "Bonferroni first-step bound for the registered Holm family"
                    ),
                }
            )
    return rows


def optimality_interval_rows(
    raw: Mapping[str, object],
    bootstrap_resamples: int,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    configurations = (
        (
            "small_random_exact_optimum",
            list(raw["optimality"]),
            "optimal_objective",
            "ca_hmcd_objective",
            "optimal_hit",
        ),
        (
            "semisynthetic_exact_reference",
            list(raw["reference_solver"]),
            "reference_objective",
            "method_objective",
            None,
        ),
    )
    for validation, records, reference_key, method_key, hit_key in configurations:
        for top_k in sorted({int(record["top_k"]) for record in records}):
            subset = [record for record in records if int(record["top_k"]) == top_k]
            differences = [
                float(record[method_key]) - float(record[reference_key])
                for record in subset
            ]
            low, high = bca_mean_interval(
                differences,
                resamples=bootstrap_resamples,
                seed=stable_seed(validation, top_k, "objective_gap"),
            )
            row: Dict[str, object] = {
                "validation": validation,
                "top_k": top_k,
                "n_independent_seeds": len(subset),
                "mean_objective_difference_method_minus_exact": statistics.fmean(
                    differences
                ),
                "objective_difference_ci95_low": low,
                "objective_difference_ci95_high": high,
                "ci_method": (
                    f"BCa bootstrap on seed-level objective differences, "
                    f"{bootstrap_resamples} resamples"
                ),
                "inference_policy": (
                    "descriptive exact-solver audit; no superiority P value or "
                    "Holm correction because the exact optimum is a deterministic "
                    "reference rather than a stochastic competing method"
                ),
            }
            if hit_key is not None:
                hits = [float(bool(record[hit_key])) for record in subset]
                hit_low, hit_high = wilson_score_interval(hits)
                row.update(
                    {
                        "exact_hit_rate": statistics.fmean(hits),
                        "exact_hit_rate_ci95_low": hit_low,
                        "exact_hit_rate_ci95_high": hit_high,
                    }
                )
            rows.append(row)
    return rows


def round_numeric_rows(
    rows: Sequence[Mapping[str, object]], digits: int = 10
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for row in rows:
        rounded: Dict[str, object] = {}
        for key, value in row.items():
            if isinstance(value, float):
                rounded[key] = round(value, digits) if math.isfinite(value) else value
            else:
                rounded[key] = value
        output.append(rounded)
    return output


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_report(
    config: Mapping[str, object],
    paired_rows: Sequence[Mapping[str, object]],
    registry: Sequence[Mapping[str, object]],
    response_rows: Sequence[Mapping[str, object]],
    replay_audit: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
    bounded_resamples: int,
) -> str:
    primary_external = [
        row
        for row in paired_rows
        if row["section"] == "primary experiment"
        and row["metric"] == "independent_service_score"
        and row["method_b"] == "External-Auction"
    ]
    response_auction = [
        row for row in response_rows if row["method_b"] == "External-Auction"
    ]
    replay_pass = all(bool(row["identical_within_1e_12"]) for row in replay_audit)
    significant_count = sum(
        bool(row["significant_after_holm_0_05"]) for row in paired_rows
    )
    lines = [
        "# 第三阶段统计分析升级报告",
        "",
        "## 分析设计",
        "",
        f"- 独立实验单位为一个完整随机种子轨迹，正式比较通常为 n={config['seeds']}；"
        "同一种子内的时间步和任务仅用于形成该种子的汇总值，不作为独立样本。",
        "- 所有配对差值均定义为方法 A 减方法 B；连续和比例差值均报告种子层面的 "
        f"95% BCa 配对自助法区间（{bootstrap_resamples} 次重采样）。",
        "- 可行、修复、回退和节点上限事件率采用独立种子层面的 Wilson 得分区间；"
        "覆盖率、能力重叠度等连续 [0,1] 指标采用种子级均值的有界 BCa 区间"
        f"（{bounded_resamples} 次重采样），均不使用可能越界的普通正态区间。",
        "- 推断检验采用双侧配对 Wilcoxon 符号秩检验；效应量、胜率、未校正 P 值和 "
        "Holm 调整后 P 值分别列示。",
        f"- 共登记 {len(registry)} 个 Holm 比较族，形成 {len(paired_rows)} 个配对比较；"
        f"其中 {significant_count} 个在族内 Holm 校正后 P<0.05。",
        "",
        "## 主结果核查",
        "",
    ]
    for row in primary_external:
        lines.append(
            f"- {row['scenario']}: CA-HMCD 相对 External-Auction 的独立服务得分差为 "
            f"{float(row['mean_difference_a_minus_b']):.4f} "
            f"[95% CI {float(row['mean_difference_ci95_low']):.4f}, "
            f"{float(row['mean_difference_ci95_high']):.4f}]，"
            f"未校正 P={float(row['p_unadjusted']):.4g}，"
            f"Holm P={float(row['p_holm_adjusted']):.4g}，"
            f"dz={float(row['cohen_dz']):.3f}，"
            f"胜率={float(row['win_rate']):.1%}。"
        )
    lines.extend(["", "## 共同服务任务响应时间", ""])
    for row in response_auction:
        direction = (
            "更短"
            if float(row["mean_difference_a_minus_b"]) < 0
            else "更长"
        )
        lines.append(
            f"- {row['scenario']}: 在双方共同服务的 "
            f"{int(row['common_task_periods_total'])} 个任务-周期上，CA-HMCD 的响应时间"
            f"{direction}，配对差为 {float(row['mean_difference_a_minus_b']):.4f} "
            f"[95% CI {float(row['mean_difference_ci95_low']):.4f}, "
            f"{float(row['mean_difference_ci95_high']):.4f}]，"
            f"Holm P={float(row['p_holm_adjusted']):.4g}。"
        )
    lines.extend(
        [
            "",
            "## 复现与解释边界",
            "",
            f"- 主实验重放与第二阶段确定性指标一致：{'通过' if replay_pass else '未通过'}。",
            "- 共同任务响应时间是条件估计量，只回答“双方都服务时谁更快”，不能替代覆盖率；"
            "因此必须与独立覆盖率和服务得分同时报告。",
            "- 30 个种子的依据是协议预先固定的独立重复数。样本量表给出各 Holm 族在"
            "保守 Bonferroni 首步界下对配对标准化效应的近似检测灵敏度，而不是事后功效证明。",
            "- 第二阶段旧表中的 `p_holm` 列由本阶段注册表取代；论文中不得混用不同"
            "比较族产生的调整后 P 值。",
            "",
        ]
    )
    return "\n".join(lines)


def manuscript_statistics_text(
    config: Mapping[str, object],
    bootstrap_resamples: int,
    bounded_resamples: int,
) -> str:
    return "\n".join(
        [
            "# Statistical analysis",
            "",
            (
                "All inferential analyses treated one complete simulation trajectory "
                f"generated from an independent random seed as the experimental unit "
                f"(n = {config['seeds']} paired seeds per formal comparison unless "
                "otherwise stated). Decision periods and task-level observations within "
                "a seed were repeated measurements and were aggregated before inference. "
                "No completed seed was excluded from the episode-level analyses."
            ),
            "",
            (
                "Paired differences were defined as CA-HMCD minus the comparator and "
                "were tested using two-sided Wilcoxon signed-rank tests. For every "
                "prespecified contrast, we report the mean paired difference, its 95% "
                f"bias-corrected and accelerated bootstrap confidence interval "
                f"({bootstrap_resamples} seed-level resamples), the unadjusted P value, "
                "the Holm-adjusted P value, paired Cohen's dz, rank-biserial correlation, "
                "and the seed-level win rate, with ties contributing one half. Lower "
                "values were treated as favorable for response time, switching, runtime, "
                "fallback, and redundancy diagnostics; higher values were favorable for "
                "service and objective measures. The bootstrap interval estimates the "
                "mean paired difference, whereas the Wilcoxon P value tests a rank-based "
                "location null; their threshold decisions can therefore differ and were "
                "not forced to agree."
            ),
            "",
            (
                "Holm correction was applied separately to each prespecified inferential "
                "family defined by a common scientific question, endpoint, and experimental "
                "stratum. The complete family registry and all family members are provided "
                "in the accompanying source-data table. Unadjusted and adjusted P values "
                "are reported in separate columns and were not substituted for effect "
                "estimates or uncertainty intervals."
            ),
            "",
            (
                "For feasibility, repair, fallback, and node-limit event rates, uncertainty "
                "was quantified using Wilson score intervals with one seed-level event-rate "
                "contribution per independent seed. Other measures restricted to [0, 1], "
                "including coverage and redundancy diagnostics, used bounded BCa bootstrap "
                f"intervals on seed-level episode means ({bounded_resamples} resamples). "
                "Both procedures retained the parameter bounds and avoided treating "
                "within-seed decision periods as independent observations."
            ),
            "",
            (
                "Response-time comparisons were restricted to task-periods served by both "
                "methods under the same scenario, seed, and decision period. Task-level "
                "response times were first averaged within each seed, and inference was "
                "then performed across paired seeds. These conditional response-time results "
                "were interpreted jointly with coverage because they do not include tasks "
                "served by only one method."
            ),
            "",
            (
                f"The protocol fixed {config['seeds']} independent seeds before this "
                "statistical reanalysis. Design sensitivity was summarized using the "
                "normal-approximation minimum detectable paired standardized effect under "
                "the conservative Bonferroni first-step bound for each Holm family; this "
                "calculation was used as a sample-size rationale rather than as post hoc "
                "achieved power. Analyses were implemented in "
                f"Python {platform.python_version()} using the standard library."
            ),
            "",
        ]
    )


def sample_size_note(
    config: Mapping[str, object], sample_rows: Sequence[Mapping[str, object]]
) -> str:
    family_sizes = sorted({int(row["family_size"]) for row in sample_rows})
    normal = NormalDist()
    lines = [
        "# 样本量依据",
        "",
        f"- 正式实验固定使用 {config['seeds']} 个独立随机种子；种子是统计学 `n`。",
        f"- 每个种子包含 {config['steps']} 个主实验决策周期，这些周期是重复测量，"
        "不会把有效样本量从 30 扩大到 360。",
        "- 样本量未依据观察到的显著性或效应量追加，避免可选停止。",
        "- 检测灵敏度采用双侧正态近似，并以 Holm 族的 Bonferroni 首步 "
        "`alpha/m` 作为保守界；它用于说明 30 个种子能够稳定识别中等及更大的"
        "配对效应，不是事后功效。",
        "",
        "| 比较族大小 m | 80% 功效近似最小 dz | 90% 功效近似最小 dz |",
        "|---:|---:|---:|",
    ]
    for family_size in family_sizes:
        alpha_per = 0.05 / family_size
        values = []
        for power in (0.80, 0.90):
            values.append(
                (
                    normal.inv_cdf(1.0 - alpha_per / 2.0)
                    + normal.inv_cdf(power)
                )
                / math.sqrt(int(config["seeds"]))
            )
        lines.append(f"| {family_size} | {values[0]:.3f} | {values[1]:.3f} |")
    lines.extend(
        [
            "",
            "注：共同服务任务响应时间若某一种子没有共同服务任务，则该种子只从该"
            "条件响应时间对比中排除；专用结果表会逐项报告有效种子数和缺失数。",
            "",
        ]
    )
    return "\n".join(lines)


def family_registry_markdown(
    registry: Sequence[Mapping[str, object]],
) -> str:
    lines = [
        "# Holm 比较族注册表",
        "",
        "每一行是一个独立的族内校正范围。跨行不再次合并校正，因为各行对应不同"
        "预设科学问题、终点或实验分层。",
        "",
        "| Family ID | Section | Endpoint | m |",
        "|---|---|---|---:|",
    ]
    for family in registry:
        lines.append(
            f"| `{family['family_id']}` | {family['section']} | "
            f"{family['endpoint']} | {family['number_of_comparisons']} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_stage3(
    source_dir: Path,
    output_dir: Path,
    bootstrap_resamples: int = 10000,
    bounded_resamples: int = 5000,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = json.loads((source_dir / "raw_results.json").read_text(encoding="utf-8"))
    config = dict(raw["config"])

    replay_records, task_response_records = rerun_main_with_task_response(config)
    perception_records = rerun_perception_noise(config)
    replay_audit = replay_consistency(raw["episodes"], replay_records)
    if not all(bool(row["identical_within_1e_12"]) for row in replay_audit):
        raise RuntimeError("Stage-3 replay changed deterministic Stage-2 metrics")

    paired_rows, registry = append_standard_families(
        raw, perception_records, bootstrap_resamples
    )
    response_rows, response_registry, response_episode_records = (
        common_task_response_rows(task_response_records, bootstrap_resamples)
    )
    paired_rows.extend(response_rows)
    registry.extend(response_registry)
    holm_adjust(paired_rows)

    bounded_datasets = (
        (
            "primary",
            raw["episodes"],
            ("scenario", "algorithm"),
            (
                "independent_coverage",
                "feasibility_rate",
                "raw_feasible",
                "repair_applied",
                "fallback",
                "node_limit_hit",
                "same_type_pair_ratio",
                "capability_overlap",
                "marginal_gain_waste",
            ),
        ),
        (
            "perception_noise",
            perception_records,
            ("noise_level", "algorithm"),
            ("feasibility_rate", "raw_feasible", "repair_applied"),
        ),
        (
            "scalability",
            raw["scalability"],
            ("resources", "tasks", "algorithm"),
            ("independent_coverage", "feasibility_rate", "fallback", "node_limit_hit"),
        ),
        (
            "redundancy_stress",
            raw["redundancy_stress"],
            ("scenario", "algorithm"),
            (
                "feasibility_rate",
                "same_type_pair_ratio",
                "capability_overlap",
                "marginal_gain_waste",
            ),
        ),
        (
            "engineering_validation",
            raw["engineering_validation"],
            ("scenario", "algorithm"),
            ("independent_coverage", "feasibility_rate"),
        ),
    )
    bounded_rows = bounded_interval_rows(
        bounded_datasets, bounded_resamples
    )
    sample_rows = sample_size_rows(registry, paired_rows)
    optimality_rows = optimality_interval_rows(raw, bootstrap_resamples)

    main_columns = {
        "primary experiment",
        "main common-task response time",
        "module ablation",
        "redundancy mechanism",
        "engineering validation",
    }
    manuscript_rows = [
        row for row in paired_rows if str(row["section"]) in main_columns
    ]

    write_csv(
        output_dir / "all_paired_statistics.csv",
        round_numeric_rows(paired_rows),
    )
    write_csv(
        output_dir / "manuscript_main_paired_statistics.csv",
        round_numeric_rows(manuscript_rows),
    )
    write_csv(
        output_dir / "common_task_response_time.csv",
        round_numeric_rows(response_rows),
    )
    write_csv(
        output_dir / "common_task_response_by_seed.csv",
        round_numeric_rows(response_episode_records),
    )
    write_csv(
        output_dir / "bounded_metric_intervals.csv",
        round_numeric_rows(bounded_rows),
    )
    write_csv(output_dir / "holm_family_registry.csv", registry)
    write_csv(
        output_dir / "sample_size_basis.csv",
        round_numeric_rows(sample_rows),
    )
    write_csv(
        output_dir / "optimality_gap_intervals.csv",
        round_numeric_rows(optimality_rows),
    )
    write_csv(output_dir / "main_task_response_records.csv", task_response_records)
    write_csv(
        output_dir / "main_replay_episode_records.csv",
        round_numeric_rows(replay_records),
    )
    write_csv(
        output_dir / "perception_noise_episode_records.csv",
        round_numeric_rows(perception_records),
    )
    write_csv(
        output_dir / "replay_consistency_audit.csv",
        round_numeric_rows(replay_audit),
    )

    report = build_report(
        config,
        paired_rows,
        registry,
        response_rows,
        replay_audit,
        bootstrap_resamples,
        bounded_resamples,
    )
    (output_dir / "stage3_statistics_report.md").write_text(
        report, encoding="utf-8"
    )
    (output_dir / "statistical_analysis_manuscript_text.md").write_text(
        manuscript_statistics_text(
            config, bootstrap_resamples, bounded_resamples
        ),
        encoding="utf-8",
    )
    (output_dir / "sample_size_basis.md").write_text(
        sample_size_note(config, sample_rows), encoding="utf-8"
    )
    (output_dir / "holm_family_registry.md").write_text(
        family_registry_markdown(registry), encoding="utf-8"
    )
    stage3_config = {
        "run_date": "2026-09-15",
        "source_directory": str(source_dir.resolve()),
        "source_value_model_version": config.get("value_model_version"),
        "independent_unit": "complete seeded simulation trajectory",
        "seeds": config["seeds"],
        "steps": config["steps"],
        "paired_ci": {
            "method": "BCa paired bootstrap of seed-level mean differences",
            "resamples": bootstrap_resamples,
            "confidence": 0.95,
        },
        "bounded_ci": {
            "event_rate_method": (
                "cluster-level Wilson score interval with one seed-level "
                "event-rate contribution per independent seed"
            ),
            "continuous_bounded_method": (
                "bounded BCa bootstrap of seed-level episode means"
            ),
            "resamples": bounded_resamples,
            "confidence": 0.95,
        },
        "test": "two-sided paired Wilcoxon signed-rank",
        "multiplicity": "Holm step-down within registered families",
        "software": f"Python {platform.python_version()} standard library",
    }
    (output_dir / "stage3_config.json").write_text(
        json.dumps(stage3_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "paired_rows": paired_rows,
        "registry": registry,
        "response_rows": response_rows,
        "bounded_rows": bounded_rows,
        "sample_rows": sample_rows,
        "optimality_rows": optimality_rows,
        "replay_audit": replay_audit,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("ca_hmcd_stage2_experiment"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ca_hmcd_stage3_statistics"),
    )
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    parser.add_argument("--bounded-resamples", type=int, default=5000)
    args = parser.parse_args()
    result = run_stage3(
        args.source_dir,
        args.output_dir,
        args.bootstrap_resamples,
        args.bounded_resamples,
    )
    print(
        f"[stage3] complete: {len(result['paired_rows'])} paired comparisons, "
        f"{len(result['registry'])} Holm families, "
        f"{len(result['bounded_rows'])} bounded intervals",
        flush=True,
    )


if __name__ == "__main__":
    main()

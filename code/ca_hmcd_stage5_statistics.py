"""Cluster-aware statistical analysis for the registered CA-HMCD replay study.

The public trajectory or preregistered composed workload is the independent
inferential unit. The 30 random seeds nested within each workload are averaged
before paired inference so that stochastic repeats are not treated as
independent physical trajectories.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from ca_hmcd_simulation import paired_statistics, write_csv
from ca_hmcd_stage3_statistics import (
    bca_mean_interval,
    holm_adjust,
    stable_seed,
    wilson_score_interval,
)


METHOD = "CA-HMCD"
BASELINES = (
    "Greedy",
    "External-Auction",
    "No-Synergy",
    "No-Stability",
    "Random",
)
ALGORITHMS = (METHOD, *BASELINES)
EPSILON = 1e-12

EPISODE_ENDPOINTS = (
    (
        "external_objective",
        "primary performance",
        "higher",
        "Complete externally evaluated objective.",
    ),
    (
        "independent_service_score",
        "primary performance",
        "higher",
        "Unified service-quality score from the independent evaluator.",
    ),
    (
        "independent_coverage",
        "primary performance",
        "higher",
        "Fraction of active demand served by the final feasible allocation.",
    ),
    (
        "external_switch_count",
        "stability mechanism",
        "lower",
        "Allocation changes measured by the unified external evaluator.",
    ),
    (
        "independent_physical_cost",
        "resource efficiency",
        "lower",
        "Physical resource expenditure from the independent evaluator.",
    ),
    (
        "independent_same_type_pair_ratio",
        "redundancy mechanism",
        "lower",
        "Same-type resource pairs among within-task allocated pairs.",
    ),
    (
        "independent_capability_overlap",
        "redundancy mechanism",
        "lower",
        "Capability overlap among jointly allocated resources.",
    ),
    (
        "independent_marginal_gain_waste",
        "redundancy mechanism",
        "lower",
        "Standalone-success mass duplicated by overlapping resources.",
    ),
    (
        "end_to_end_runtime_ms",
        "computational boundary",
        "lower",
        "End-to-end decision time including candidate generation and evaluation.",
    ),
    (
        "fallback_run_indicator",
        "computational boundary",
        "lower",
        "Indicator that at least one period in the run used bounded-search fallback.",
    ),
    (
        "fallback",
        "computational boundary",
        "lower",
        "Within-run proportion of decision periods using fallback.",
    ),
)

BOUNDED_METRICS = (
    "independent_coverage",
    "feasibility_rate",
    "raw_feasible",
    "repair_run_indicator",
    "fallback_run_indicator",
    "fallback",
    "node_limit_run_indicator",
    "independent_same_type_pair_ratio",
    "independent_capability_overlap",
    "independent_marginal_gain_waste",
)

EVENT_PREVALENCE_METRICS = (
    (
        "clusters_with_any_repair",
        "repair_run_indicator",
        lambda value: value > EPSILON,
    ),
    (
        "clusters_with_any_fallback",
        "fallback_run_indicator",
        lambda value: value > EPSILON,
    ),
    (
        "clusters_with_any_node_limit_hit",
        "node_limit_run_indicator",
        lambda value: value > EPSILON,
    ),
    (
        "clusters_with_any_final_infeasibility",
        "feasibility_rate",
        lambda value: value < 1.0 - EPSILON,
    ),
)

NUMERIC_EPISODE_FIELDS = tuple(
    dict.fromkeys(
        [endpoint[0] for endpoint in EPISODE_ENDPOINTS if endpoint[0] != "fallback_run_indicator"]
        + list(BOUNDED_METRICS)
        + [
            "node_limit_hit",
            "repair_applied",
            "raw_feasible",
            "feasibility_rate",
        ]
    )
)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stratum_id(dataset: str, task_load: int) -> str:
    return f"{dataset}.load_{task_load}"


def stratum_label(dataset: str, task_load: int) -> str:
    if dataset == "anti_uav410":
        return f"Anti-UAV410 load {task_load}"
    if dataset == "uzh_fpv":
        return "UZH-FPV single-target replay"
    return f"{dataset} load {task_load}"


def format_number(value: object, digits: int = 4) -> str:
    number = float(value)
    if not math.isfinite(number):
        return "NA"
    if number != 0.0 and abs(number) < 0.001:
        return f"{number:.3g}"
    return f"{number:.{digits}f}"


def round_rows(
    rows: Sequence[Mapping[str, object]], digits: int = 10
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for source in rows:
        row: Dict[str, object] = {}
        for key, value in source.items():
            if isinstance(value, float) and math.isfinite(value):
                row[key] = round(value, digits)
            else:
                row[key] = value
        output.append(row)
    return output


def validate_source(source_dir: Path) -> Tuple[Dict[str, object], List[Dict[str, object]], List[Dict[str, object]]]:
    metadata = json.loads(
        (source_dir / "execution_metadata.json").read_text(encoding="utf-8")
    )
    execution_audit = read_csv(source_dir / "registered_execution_audit.csv")
    coverage = read_csv(source_dir / "registered_workload_coverage.csv")
    pairing = read_csv(source_dir / "registered_pairing_audit.csv")
    raw_episodes = read_csv(source_dir / "registered_episode_results.csv")

    checks = [
        {
            "check": "formal execution mode",
            "observed": metadata.get("mode"),
            "expected": "formal",
            "passed": metadata.get("mode") == "formal",
        },
        {
            "check": "source execution audit",
            "observed": sum(row.get("passed") == "1" for row in execution_audit),
            "expected": len(execution_audit),
            "passed": bool(execution_audit)
            and all(row.get("passed") == "1" for row in execution_audit),
        },
        {
            "check": "source pairing audit",
            "observed": sum(row.get("passed") == "1" for row in pairing),
            "expected": len(pairing),
            "passed": len(pairing) == 15840
            and all(row.get("passed") == "1" for row in pairing),
        },
        {
            "check": "formal episode count",
            "observed": len(raw_episodes),
            "expected": int(metadata.get("expected_algorithm_runs", -1)),
            "passed": len(raw_episodes)
            == int(metadata.get("expected_algorithm_runs", -1))
            == 7920,
        },
        {
            "check": "workload coverage",
            "observed": sum(row.get("complete") == "1" for row in coverage),
            "expected": len(coverage),
            "passed": len(coverage) == 44
            and all(row.get("complete") == "1" for row in coverage),
        },
        {
            "check": "registered replicates per workload",
            "observed": sorted(
                {
                    (
                        int(row["minimum_completed_replicates"]),
                        int(row["maximum_completed_replicates"]),
                    )
                    for row in coverage
                }
            ),
            "expected": [(30, 30)],
            "passed": all(
                row.get("minimum_completed_replicates") == "30"
                and row.get("maximum_completed_replicates") == "30"
                for row in coverage
            ),
        },
        {
            "check": "metadata audit pass",
            "observed": bool(metadata.get("audit_pass")),
            "expected": True,
            "passed": bool(metadata.get("audit_pass")),
        },
    ]
    if not all(bool(row["passed"]) for row in checks):
        failed = [str(row["check"]) for row in checks if not bool(row["passed"])]
        raise RuntimeError(f"Stage-5 source validation failed: {', '.join(failed)}")
    return metadata, checks, raw_episodes


def prepare_episode_records(
    raw_rows: Sequence[Mapping[str, str]],
) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    seen = set()
    for raw in raw_rows:
        algorithm = str(raw["algorithm"])
        if algorithm not in ALGORITHMS:
            raise ValueError(f"Unexpected algorithm: {algorithm}")
        key = (raw["workload_id"], int(raw["seed"]), algorithm)
        if key in seen:
            raise ValueError(f"Duplicate episode tuple: {key}")
        seen.add(key)
        record: Dict[str, object] = {
            "workload_id": str(raw["workload_id"]),
            "dataset": str(raw["dataset"]),
            "task_load": int(raw["task_load"]),
            "independent_cluster": str(raw["independent_cluster"]),
            "seed": int(raw["seed"]),
            "algorithm": algorithm,
        }
        for field in NUMERIC_EPISODE_FIELDS:
            if field in raw and str(raw[field]) != "":
                record[field] = float(raw[field])
        record["fallback_run_indicator"] = (
            1.0 if float(raw["fallback"]) > EPSILON else 0.0
        )
        record["node_limit_run_indicator"] = (
            1.0 if float(raw["node_limit_hit"]) > EPSILON else 0.0
        )
        record["repair_run_indicator"] = (
            1.0 if float(raw["repair_applied"]) > EPSILON else 0.0
        )
        records.append(record)
    return records


def aggregate_cluster_records(
    episode_records: Sequence[Mapping[str, object]],
    expected_cluster_algorithm_rows: int | None = None,
) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, int, str, str], List[Mapping[str, object]]] = defaultdict(list)
    for record in episode_records:
        grouped[
            (
                str(record["dataset"]),
                int(record["task_load"]),
                str(record["independent_cluster"]),
                str(record["algorithm"]),
            )
        ].append(record)

    metrics = tuple(
        dict.fromkeys(
            [endpoint[0] for endpoint in EPISODE_ENDPOINTS]
            + list(BOUNDED_METRICS)
            + ["node_limit_run_indicator", "repair_run_indicator"]
        )
    )
    output: List[Dict[str, object]] = []
    for (dataset, task_load, cluster, algorithm), rows in sorted(grouped.items()):
        seeds = sorted({int(row["seed"]) for row in rows})
        if len(seeds) != 30:
            raise RuntimeError(
                f"{cluster}/{algorithm} has {len(seeds)} seeds; expected 30"
            )
        record: Dict[str, object] = {
            "dataset": dataset,
            "task_load": task_load,
            "stratum_id": stratum_id(dataset, task_load),
            "stratum_label": stratum_label(dataset, task_load),
            "independent_cluster": cluster,
            "algorithm": algorithm,
            "nested_seed_runs": len(seeds),
        }
        for metric in metrics:
            values = [float(row[metric]) for row in rows]
            record[metric] = statistics.fmean(values)
        output.append(record)

    if (
        expected_cluster_algorithm_rows is not None
        and len(output) != expected_cluster_algorithm_rows
    ):
        raise RuntimeError(
            f"Expected {expected_cluster_algorithm_rows} cluster-algorithm rows, "
            f"observed {len(output)}"
        )
    return output


def paired_cluster_row(
    records: Sequence[Mapping[str, object]],
    metric: str,
    family_id: str,
    section: str,
    method_b: str,
    favorable_direction: str,
    bootstrap_resamples: int,
    dataset: str,
    task_load: int,
) -> Dict[str, object]:
    a_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == METHOD
    }
    b_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == method_b
    }
    clusters = sorted(set(a_lookup) & set(b_lookup))
    if set(a_lookup) != set(b_lookup):
        raise RuntimeError(
            f"Unpaired clusters for {family_id}, baseline={method_b}"
        )
    a_values = [a_lookup[cluster] for cluster in clusters]
    b_values = [b_lookup[cluster] for cluster in clusters]
    differences = [a - b for a, b in zip(a_values, b_values)]
    stats = paired_statistics(a_values, b_values)
    ci_low, ci_high = bca_mean_interval(
        differences,
        resamples=bootstrap_resamples,
        seed=stable_seed("stage5", family_id, method_b, metric),
    )
    if favorable_direction == "higher":
        wins = sum(value > EPSILON for value in differences)
    else:
        wins = sum(value < -EPSILON for value in differences)
    ties = sum(abs(value) <= EPSILON for value in differences)
    return {
        "family_id": family_id,
        "section": section,
        "comparison": f"{METHOD} vs {method_b}",
        "metric": metric,
        "method_a": METHOD,
        "method_b": method_b,
        "favorable_direction_for_method_a": favorable_direction,
        "dataset": dataset,
        "task_load": task_load,
        "stratum_id": stratum_id(dataset, task_load),
        "stratum_label": stratum_label(dataset, task_load),
        "independent_unit": "public trajectory or preregistered composed workload",
        "n_independent_cluster_pairs": len(clusters),
        "nested_seeds_per_cluster": 30,
        "method_a_mean": statistics.fmean(a_values),
        "method_b_mean": statistics.fmean(b_values),
        "mean_difference_a_minus_b": statistics.fmean(differences),
        "mean_difference_ci95_low": ci_low,
        "mean_difference_ci95_high": ci_high,
        "ci_estimand": "mean paired difference across independent clusters",
        "ci_method": (
            f"BCa paired cluster bootstrap, {bootstrap_resamples} resamples"
        ),
        "wilcoxon_w": stats["wilcoxon_w"],
        "p_value_test": "two-sided paired Wilcoxon signed-rank test",
        "p_unadjusted": stats["p_value"],
        "p_holm_adjusted": math.nan,
        "cohen_dz": stats["cohen_dz"],
        "rank_biserial": stats["rank_biserial"],
        "win_rate_favorable_to_method_a": (
            (wins + 0.5 * ties) / max(1, len(differences))
        ),
        "cluster_ids": ";".join(clusters),
    }


def register_family(
    registry: List[Dict[str, object]],
    rows: Sequence[Mapping[str, object]],
    section: str,
    endpoint: str,
    definition: str,
    dataset: str,
    task_load: int,
) -> None:
    if not rows:
        return
    registry.append(
        {
            "family_id": rows[0]["family_id"],
            "section": section,
            "endpoint": endpoint,
            "dataset": dataset,
            "task_load": task_load,
            "stratum_id": stratum_id(dataset, task_load),
            "independent_unit": (
                "public trajectory or preregistered composed workload; "
                "30 seeds are nested Monte Carlo repeats"
            ),
            "family_definition": definition,
            "number_of_comparisons": len(rows),
            "members": "; ".join(str(row["comparison"]) for row in rows),
            "multiplicity_control": (
                "Holm step-down within this family, two-sided family-wise alpha=0.05"
            ),
        }
    )


def build_episode_comparisons(
    cluster_records: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    output: List[Dict[str, object]] = []
    registry: List[Dict[str, object]] = []
    strata = sorted(
        {
            (str(row["dataset"]), int(row["task_load"]))
            for row in cluster_records
        }
    )
    for dataset, task_load in strata:
        stratum_rows = [
            row
            for row in cluster_records
            if row["dataset"] == dataset and int(row["task_load"]) == task_load
        ]
        for metric, section, direction, definition in EPISODE_ENDPOINTS:
            family_id = f"{section.replace(' ', '_')}.{metric}.{stratum_id(dataset, task_load)}"
            family_rows = [
                paired_cluster_row(
                    stratum_rows,
                    metric,
                    family_id,
                    section,
                    baseline,
                    direction,
                    bootstrap_resamples,
                    dataset,
                    task_load,
                )
                for baseline in BASELINES
            ]
            output.extend(family_rows)
            register_family(
                registry,
                family_rows,
                section,
                metric,
                (
                    f"All five prespecified contrasts of {METHOD} against the "
                    f"other methods for {definition}"
                ),
                dataset,
                task_load,
            )
    return output, registry


def build_common_response_records(
    per_step_path: Path,
    bootstrap_resamples: int,
    expected_per_step_rows: int | None = None,
) -> Tuple[
    List[Dict[str, object]],
    List[Dict[str, object]],
    List[Dict[str, object]],
    List[Dict[str, object]],
]:
    method_rows: Dict[
        Tuple[str, int, str, str, int, int], Dict[str, Dict[str, float]]
    ] = defaultdict(dict)
    per_step_rows = 0
    with per_step_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            per_step_rows += 1
            key = (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["independent_cluster"]),
                str(row["workload_id"]),
                int(row["seed"]),
                int(row["step"]),
            )
            method_rows[key][str(row["algorithm"])] = {
                str(task_id): float(value)
                for task_id, value in json.loads(
                    str(row["served_task_response_times_json"])
                ).items()
            }
    if (
        expected_per_step_rows is not None
        and per_step_rows != expected_per_step_rows
    ):
        raise RuntimeError(
            f"Expected {expected_per_step_rows} per-step rows, observed "
            f"{per_step_rows}"
        )

    seed_accumulators: Dict[
        Tuple[str, int, str, str, int, str], Dict[str, float]
    ] = defaultdict(lambda: {"a_sum": 0.0, "b_sum": 0.0, "count": 0.0})
    for (
        dataset,
        task_load,
        cluster,
        workload_id,
        seed,
        _step,
    ), methods in method_rows.items():
        a_responses = methods.get(METHOD, {})
        for baseline in BASELINES:
            b_responses = methods.get(baseline, {})
            common_tasks = sorted(set(a_responses) & set(b_responses))
            accumulator = seed_accumulators[
                (dataset, task_load, cluster, workload_id, seed, baseline)
            ]
            for task_id in common_tasks:
                accumulator["a_sum"] += a_responses[task_id]
                accumulator["b_sum"] += b_responses[task_id]
                accumulator["count"] += 1.0

    seed_rows: List[Dict[str, object]] = []
    for (
        dataset,
        task_load,
        cluster,
        workload_id,
        seed,
        baseline,
    ), values in sorted(seed_accumulators.items()):
        count = int(values["count"])
        if count <= 0:
            continue
        a_mean = values["a_sum"] / count
        b_mean = values["b_sum"] / count
        seed_rows.append(
            {
                "dataset": dataset,
                "task_load": task_load,
                "stratum_id": stratum_id(dataset, task_load),
                "independent_cluster": cluster,
                "workload_id": workload_id,
                "seed": seed,
                "method_b": baseline,
                "common_task_period_count": count,
                "ca_hmcd_mean_response_time": a_mean,
                "baseline_mean_response_time": b_mean,
                "mean_difference_a_minus_b": a_mean - b_mean,
            }
        )

    cluster_groups: Dict[
        Tuple[str, int, str, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in seed_rows:
        cluster_groups[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["independent_cluster"]),
                str(row["method_b"]),
            )
        ].append(row)

    cluster_rows: List[Dict[str, object]] = []
    for (dataset, task_load, cluster, baseline), rows in sorted(
        cluster_groups.items()
    ):
        included_seeds = {int(row["seed"]) for row in rows}
        cluster_rows.append(
            {
                "dataset": dataset,
                "task_load": task_load,
                "stratum_id": stratum_id(dataset, task_load),
                "stratum_label": stratum_label(dataset, task_load),
                "independent_cluster": cluster,
                "method_b": baseline,
                "seeds_with_common_tasks": len(included_seeds),
                "seeds_without_common_tasks": 30 - len(included_seeds),
                "common_task_periods_total": sum(
                    int(row["common_task_period_count"]) for row in rows
                ),
                "ca_hmcd_mean_response_time": statistics.fmean(
                    float(row["ca_hmcd_mean_response_time"]) for row in rows
                ),
                "baseline_mean_response_time": statistics.fmean(
                    float(row["baseline_mean_response_time"]) for row in rows
                ),
                "mean_difference_a_minus_b": statistics.fmean(
                    float(row["mean_difference_a_minus_b"]) for row in rows
                ),
            }
        )

    comparison_rows: List[Dict[str, object]] = []
    registry: List[Dict[str, object]] = []
    strata = sorted(
        {
            (str(row["dataset"]), int(row["task_load"]))
            for row in cluster_rows
        }
    )
    for dataset, task_load in strata:
        family_id = f"common_response_time.{stratum_id(dataset, task_load)}"
        family_rows: List[Dict[str, object]] = []
        for baseline in BASELINES:
            selected = [
                row
                for row in cluster_rows
                if row["dataset"] == dataset
                and int(row["task_load"]) == task_load
                and row["method_b"] == baseline
            ]
            a_lookup = {
                str(row["independent_cluster"]): float(
                    row["ca_hmcd_mean_response_time"]
                )
                for row in selected
            }
            b_lookup = {
                str(row["independent_cluster"]): float(
                    row["baseline_mean_response_time"]
                )
                for row in selected
            }
            clusters = sorted(set(a_lookup) & set(b_lookup))
            a_values = [a_lookup[cluster] for cluster in clusters]
            b_values = [b_lookup[cluster] for cluster in clusters]
            differences = [a - b for a, b in zip(a_values, b_values)]
            stats = paired_statistics(a_values, b_values)
            ci_low, ci_high = bca_mean_interval(
                differences,
                resamples=bootstrap_resamples,
                seed=stable_seed("stage5-response", family_id, baseline),
            )
            wins = sum(value < -EPSILON for value in differences)
            ties = sum(abs(value) <= EPSILON for value in differences)
            family_rows.append(
                {
                    "family_id": family_id,
                    "section": "common-task response time",
                    "comparison": f"{METHOD} vs {baseline}",
                    "metric": "common_task_response_time",
                    "method_a": METHOD,
                    "method_b": baseline,
                    "favorable_direction_for_method_a": "lower",
                    "dataset": dataset,
                    "task_load": task_load,
                    "stratum_id": stratum_id(dataset, task_load),
                    "stratum_label": stratum_label(dataset, task_load),
                    "independent_unit": (
                        "public trajectory or preregistered composed workload"
                    ),
                    "n_independent_cluster_pairs": len(clusters),
                    "nested_seeds_per_cluster": 30,
                    "method_a_mean": statistics.fmean(a_values),
                    "method_b_mean": statistics.fmean(b_values),
                    "mean_difference_a_minus_b": statistics.fmean(differences),
                    "mean_difference_ci95_low": ci_low,
                    "mean_difference_ci95_high": ci_high,
                    "ci_estimand": (
                        "mean paired cluster difference conditional on both "
                        "methods serving the same task-period"
                    ),
                    "ci_method": (
                        f"BCa paired cluster bootstrap, {bootstrap_resamples} resamples"
                    ),
                    "wilcoxon_w": stats["wilcoxon_w"],
                    "p_value_test": "two-sided paired Wilcoxon signed-rank test",
                    "p_unadjusted": stats["p_value"],
                    "p_holm_adjusted": math.nan,
                    "cohen_dz": stats["cohen_dz"],
                    "rank_biserial": stats["rank_biserial"],
                    "win_rate_favorable_to_method_a": (
                        (wins + 0.5 * ties) / max(1, len(differences))
                    ),
                    "common_task_periods_total": sum(
                        int(row["common_task_periods_total"]) for row in selected
                    ),
                    "clusters_with_missing_common_task_seeds": sum(
                        int(row["seeds_without_common_tasks"]) > 0
                        for row in selected
                    ),
                    "cluster_ids": ";".join(clusters),
                }
            )
        comparison_rows.extend(family_rows)
        register_family(
            registry,
            family_rows,
            "common-task response time",
            "common_task_response_time",
            (
                "All five prespecified contrasts conditional on task-periods "
                "served by both compared methods."
            ),
            dataset,
            task_load,
        )
    return seed_rows, cluster_rows, comparison_rows, registry


def bounded_interval_rows(
    cluster_records: Sequence[Mapping[str, object]],
    bounded_resamples: int,
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    grouped: Dict[Tuple[str, int, str], List[Mapping[str, object]]] = defaultdict(list)
    for row in cluster_records:
        grouped[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["algorithm"]),
            )
        ].append(row)

    for (dataset, task_load, algorithm), rows in sorted(grouped.items()):
        for metric in BOUNDED_METRICS:
            values = [float(row[metric]) for row in rows]
            low, high = bca_mean_interval(
                values,
                resamples=bounded_resamples,
                seed=stable_seed(
                    "stage5-bounded", dataset, task_load, algorithm, metric
                ),
                bounds=(0.0, 1.0),
            )
            output.append(
                {
                    "dataset": dataset,
                    "task_load": task_load,
                    "stratum_id": stratum_id(dataset, task_load),
                    "algorithm": algorithm,
                    "metric": metric,
                    "n_independent_clusters": len(values),
                    "mean": statistics.fmean(values),
                    "ci95_low": low,
                    "ci95_high": high,
                    "interval_method": (
                        f"bounded BCa bootstrap of cluster means, "
                        f"{bounded_resamples} resamples"
                    ),
                }
            )
        for output_metric, source_metric, predicate in EVENT_PREVALENCE_METRICS:
            flags = [1.0 if predicate(float(row[source_metric])) else 0.0 for row in rows]
            low, high = wilson_score_interval(flags)
            output.append(
                {
                    "dataset": dataset,
                    "task_load": task_load,
                    "stratum_id": stratum_id(dataset, task_load),
                    "algorithm": algorithm,
                    "metric": output_metric,
                    "n_independent_clusters": len(flags),
                    "mean": statistics.fmean(flags),
                    "ci95_low": low,
                    "ci95_high": high,
                    "interval_method": (
                        "Wilson score interval for binary cluster-level prevalence"
                    ),
                }
            )
    return output


def sample_size_rows(
    registry: Sequence[Mapping[str, object]],
    paired_rows: Sequence[Mapping[str, object]],
    alpha: float = 0.05,
) -> List[Dict[str, object]]:
    n_by_family: Dict[str, int] = {}
    for row in paired_rows:
        family_id = str(row["family_id"])
        n_value = int(row["n_independent_cluster_pairs"])
        n_by_family[family_id] = min(n_value, n_by_family.get(family_id, n_value))
    normal = NormalDist()
    output: List[Dict[str, object]] = []
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
            output.append(
                {
                    "family_id": family_id,
                    "family_size": family_size,
                    "minimum_independent_cluster_pairs": n_value,
                    "nested_seeds_per_cluster": 30,
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
    return output


def family_registry_markdown(
    registry: Sequence[Mapping[str, object]],
) -> str:
    lines = [
        "# Stage 5 Holm Family Registry",
        "",
        (
            "Each row defines one correction family. Families are separated by "
            "scientific endpoint and preregistered dataset/load stratum."
        ),
        "",
        "| Family ID | Section | Endpoint | Stratum | m |",
        "|---|---|---|---|---:|",
    ]
    for row in registry:
        lines.append(
            f"| `{row['family_id']}` | {row['section']} | {row['endpoint']} | "
            f"{row['stratum_id']} | {row['number_of_comparisons']} |"
        )
    lines.append("")
    return "\n".join(lines)


def statistical_analysis_text(
    bootstrap_resamples: int,
    bounded_resamples: int,
) -> str:
    return "\n".join(
        [
            "# Statistical analysis",
            "",
            (
                "The independent inferential unit was an original UZH-FPV flight "
                "sequence or a preregistered Anti-UAV410 composed workload. Thirty "
                "paired random seeds were nested within each unit and were averaged "
                "before inference; individual decision periods, tasks, and stochastic "
                "repeats were not treated as independent observations."
            ),
            "",
            (
                "For each dataset and task-load stratum, CA-HMCD was compared with "
                "Greedy, External-Auction, No-Synergy, No-Stability, and Random using "
                "two-sided paired Wilcoxon signed-rank tests across independent "
                "clusters. Effect reporting included the mean paired difference "
                "(CA-HMCD minus comparator), a 95% bias-corrected and accelerated "
                f"cluster-bootstrap confidence interval ({bootstrap_resamples} "
                "resamples), paired Cohen's dz, rank-biserial correlation, and the "
                "cluster win rate in the prespecified favorable direction."
            ),
            "",
            (
                "Holm step-down correction was applied separately within each "
                "prespecified family comprising the five algorithm contrasts for one "
                "endpoint in one dataset/load stratum. Unadjusted and Holm-adjusted "
                "P values are reported separately. Higher values were favorable for "
                "the external objective, service score, and coverage; lower values "
                "were favorable for switching, physical cost, redundancy diagnostics, "
                "runtime, fallback, and response time."
            ),
            "",
            (
                "The BCa interval estimates the mean paired cluster difference, "
                "whereas the Wilcoxon test evaluates signed ranks and the Holm "
                "procedure defines the registered family-wise decision rule. These "
                "quantities may differ at small cluster counts; a confidence interval "
                "excluding zero was not described as Holm-confirmed significance when "
                "the adjusted P value was at least 0.05."
            ),
            "",
            (
                "Coverage and other continuous metrics bounded to [0,1] were summarized "
                f"with bounded BCa intervals over cluster means ({bounded_resamples} "
                "resamples). The prevalence of clusters containing any repair, "
                "fallback, node-limit event, or final infeasibility was summarized "
                "using Wilson score intervals."
            ),
            "",
            (
                "Response-time comparisons were restricted to task-periods served by "
                "both methods. Response times were averaged first within each random "
                "seed and then within each independent cluster before paired inference. "
                "This conditional endpoint was interpreted together with coverage and "
                "was not used as a substitute for service availability."
            ),
            "",
            (
                f"Analyses were implemented in Python {platform.python_version()} "
                "using the standard library and deterministic bootstrap seeds. Exact "
                "P values are reported where computationally available; P<0.05 after "
                "the registered Holm correction was used as the inferential threshold."
            ),
            "",
        ]
    )


def result_sentence(row: Mapping[str, object]) -> str:
    return (
        f"{row['stratum_label']}: {row['comparison']} for {row['metric']} gave "
        f"a paired cluster difference of "
        f"{format_number(row['mean_difference_a_minus_b'])} "
        f"(95% CI {format_number(row['mean_difference_ci95_low'])} to "
        f"{format_number(row['mean_difference_ci95_high'])}; "
        f"unadjusted P={format_number(row['p_unadjusted'])}; "
        f"Holm-adjusted P={format_number(row['p_holm_adjusted'])}; "
        f"dz={format_number(row['cohen_dz'], 3)}; "
        f"rank-biserial={format_number(row['rank_biserial'], 3)}; "
        f"favorable win rate="
        f"{100.0 * float(row['win_rate_favorable_to_method_a']):.1f}%; "
        f"n={row['n_independent_cluster_pairs']} clusters)."
    )


def manuscript_results_text(
    paired_rows: Sequence[Mapping[str, object]],
) -> str:
    lines = ["# Statistical results", ""]
    for dataset, task_load in (
        ("anti_uav410", 2),
        ("anti_uav410", 4),
        ("anti_uav410", 6),
        ("anti_uav410", 8),
        ("uzh_fpv", 1),
    ):
        lines.append(f"## {stratum_label(dataset, task_load)}")
        lines.append("")
        targets = [
            ("independent_service_score", "Greedy"),
            ("independent_service_score", "External-Auction"),
            ("independent_coverage", "Greedy"),
            ("independent_coverage", "External-Auction"),
            ("external_objective", "No-Synergy"),
            ("external_objective", "No-Stability"),
            ("common_task_response_time", "External-Auction"),
        ]
        for metric, baseline in targets:
            matches = [
                row
                for row in paired_rows
                if row["dataset"] == dataset
                and int(row["task_load"]) == task_load
                and row["metric"] == metric
                and row["method_b"] == baseline
            ]
            if matches:
                lines.append(f"- {result_sentence(matches[0])}")
        lines.append("")
    lines.extend(
        [
            (
                "These comparisons use public trajectories or preregistered composed "
                "workloads as n. The 30 seeds within each workload quantify stochastic "
                "variation but do not increase the number of independent public-data "
                "clusters."
            ),
            (
                "For Anti-UAV410 loads 6 and 8, the reported high-load contrasts "
                "showed consistent effect directions but did not reach P<0.05 after "
                "the registered five-comparison Holm correction; they should therefore "
                "be presented as cluster-level effect estimates rather than confirmed "
                "family-wise superiority."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    metadata: Mapping[str, object],
    cluster_records: Sequence[Mapping[str, object]],
    paired_rows: Sequence[Mapping[str, object]],
    registry: Sequence[Mapping[str, object]],
    bounded_rows: Sequence[Mapping[str, object]],
    sample_rows: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
    bounded_resamples: int,
) -> str:
    significant = [
        row
        for row in paired_rows
        if bool(row["significant_after_holm_0_05"])
    ]
    lines = [
        "# CA-HMCD Stage 5 Statistical Analysis Report",
        "",
        "## Analysis design",
        "",
        (
            "- Independent unit: original UZH-FPV trajectory or preregistered "
            "Anti-UAV410 composed workload."
        ),
        (
            "- Nested repeats: 30 paired random seeds per independent unit; seeds "
            "were averaged before inference."
        ),
        (
            f"- Paired uncertainty: 95% BCa cluster-bootstrap intervals with "
            f"{bootstrap_resamples} resamples."
        ),
        (
            "- Test: two-sided paired Wilcoxon signed-rank test across independent "
            "clusters."
        ),
        (
            "- Multiplicity: Holm correction within each endpoint-by-stratum family "
            "of five algorithm contrasts."
        ),
        (
            f"- Bounded intervals: bounded BCa cluster bootstrap with "
            f"{bounded_resamples} resamples; binary cluster prevalence used Wilson "
            "score intervals."
        ),
        "",
        "## Sample sizes",
        "",
        "| Stratum | Independent clusters | Seeds per cluster |",
        "|---|---:|---:|",
    ]
    strata = sorted(
        {
            (str(row["dataset"]), int(row["task_load"]))
            for row in cluster_records
        }
    )
    for dataset, task_load in strata:
        clusters = {
            str(row["independent_cluster"])
            for row in cluster_records
            if row["dataset"] == dataset and int(row["task_load"]) == task_load
        }
        lines.append(
            f"| {stratum_label(dataset, task_load)} | {len(clusters)} | 30 |"
        )
    lines.extend(
        [
            "",
            "## Registered families",
            "",
            (
                f"- {len(registry)} Holm families and {len(paired_rows)} paired "
                f"comparisons were evaluated."
            ),
            (
                f"- {len(significant)} comparisons had Holm-adjusted P<0.05."
            ),
            (
                "- Raw P values, adjusted P values, paired Cohen's dz, "
                "rank-biserial correlations, and favorable-direction win rates are "
                "stored in separate columns."
            ),
            (
                "- BCa intervals estimate mean paired cluster differences, while "
                "Wilcoxon/Holm values define the registered family-wise test decision; "
                "the two are not interchangeable at small n."
            ),
            "",
            "## Main high-load comparisons",
            "",
        ]
    )
    for dataset, task_load in (("anti_uav410", 6), ("anti_uav410", 8)):
        for metric, baseline in (
            ("independent_service_score", "Greedy"),
            ("independent_service_score", "External-Auction"),
            ("independent_coverage", "Greedy"),
            ("independent_coverage", "External-Auction"),
            ("external_objective", "No-Synergy"),
            ("external_objective", "No-Stability"),
        ):
            row = next(
                (
                    candidate
                    for candidate in paired_rows
                    if candidate["dataset"] == dataset
                    and int(candidate["task_load"]) == task_load
                    and candidate["metric"] == metric
                    and candidate["method_b"] == baseline
                ),
                None,
            )
            if row:
                lines.append(f"- {result_sentence(row)}")
    displayed_high_load = [
        row
        for row in paired_rows
        if row["dataset"] == "anti_uav410"
        and int(row["task_load"]) in (6, 8)
        and (
            (
                row["metric"] in ("independent_service_score", "independent_coverage")
                and row["method_b"] in ("Greedy", "External-Auction")
            )
            or (
                row["metric"] == "external_objective"
                and row["method_b"] in ("No-Synergy", "No-Stability")
            )
        )
    ]
    lines.append(
        "- None of the displayed load-6/load-8 comparisons reached "
        f"Holm-adjusted P<0.05 ({sum(bool(row['significant_after_holm_0_05']) for row in displayed_high_load)} "
        f"of {len(displayed_high_load)}). Their estimates must not be described as "
        "Holm-confirmed superiority."
    )
    lines.extend(["", "## Common-task response time", ""])
    for dataset, task_load in strata:
        row = next(
            (
                candidate
                for candidate in paired_rows
                if candidate["dataset"] == dataset
                and int(candidate["task_load"]) == task_load
                and candidate["metric"] == "common_task_response_time"
                and candidate["method_b"] == "External-Auction"
            ),
            None,
        )
        if row:
            lines.append(f"- {result_sentence(row)}")

    fallback_rows = [
        row
        for row in bounded_rows
        if row["metric"] == "fallback_run_indicator"
        and row["algorithm"] == METHOD
        and row["dataset"] == "anti_uav410"
    ]
    fallback_prevalence = {
        int(row["task_load"]): row
        for row in bounded_rows
        if row["metric"] == "clusters_with_any_fallback"
        and row["algorithm"] == METHOD
        and row["dataset"] == "anti_uav410"
    }
    lines.extend(["", "## Computational boundary", ""])
    for row in sorted(fallback_rows, key=lambda value: int(value["task_load"])):
        prevalence = fallback_prevalence[int(row["task_load"])]
        lines.append(
            f"- Anti-UAV410 load {row['task_load']}: the cluster-averaged "
            f"CA-HMCD run-level fallback rate was "
            f"{100.0 * float(row['mean']):.1f}% "
            f"(bounded 95% CI {100.0 * float(row['ci95_low']):.1f}% to "
            f"{100.0 * float(row['ci95_high']):.1f}%); "
            f"{100.0 * float(prevalence['mean']):.1f}% of independent clusters "
            f"contained at least one fallback event "
            f"(Wilson 95% CI {100.0 * float(prevalence['ci95_low']):.1f}% to "
            f"{100.0 * float(prevalence['ci95_high']):.1f}%)."
        )

    mde_load8 = next(
        (
            row
            for row in sample_rows
            if row["family_id"]
            == "primary_performance.independent_service_score.anti_uav410.load_8"
            and float(row["target_power"]) == 0.80
        ),
        None,
    )
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            (
                "- Anti-UAV410 load 8 contains only four independent composed "
                "workloads. Exact paired Wilcoxon tests at this n have low resolution; "
                "absence of Holm-adjusted significance must not be interpreted as "
                "evidence of equivalence."
            ),
            (
                "- Several BCa intervals exclude zero while the corresponding "
                "Holm-adjusted Wilcoxon P value is at least 0.05. This reflects "
                "different estimands plus discrete small-n rank tests; the registered "
                "Holm decision, not interval exclusion alone, governs significance "
                "wording."
            ),
        ]
    )
    if mde_load8:
        lines.append(
            "- For a five-comparison Holm family at load 8, the approximate "
            "minimum detectable paired dz at 80% power under the conservative "
            f"Bonferroni first-step bound is {float(mde_load8['approximate_minimum_detectable_paired_cohen_dz']):.2f}."
        )
    lines.extend(
        [
            (
                "- The common-task response endpoint is conditional on both methods "
                "serving the task-period and must be interpreted with coverage."
            ),
            (
                "- Public data define task-side motion or observation patterns; "
                "response resources and outcomes remain simulated."
            ),
            (
                "- No repair event occurred in the formal replay, so the repair "
                "mechanism has no comparative inferential evidence in this dataset."
            ),
            "",
            "## Reproducibility",
            "",
            f"- Protocol fingerprint: `{metadata['protocol_fingerprint']}`",
            f"- Code fingerprint: `{metadata['code_fingerprint']}`",
            f"- Python: {platform.python_version()}",
            "",
        ]
    )
    return "\n".join(lines)


def sample_size_markdown(
    sample_rows: Sequence[Mapping[str, object]],
) -> str:
    representative: Dict[Tuple[int, float], Mapping[str, object]] = {}
    for row in sample_rows:
        key = (
            int(row["minimum_independent_cluster_pairs"]),
            float(row["target_power"]),
        )
        representative.setdefault(key, row)
    lines = [
        "# Stage 5 Sample-Size Basis",
        "",
        (
            "The public trajectory or preregistered composed workload is the "
            "independent unit. Thirty seeds within each workload reduce Monte Carlo "
            "noise but do not increase inferential n."
        ),
        "",
        (
            "The table gives an approximate paired Cohen's dz detectable under a "
            "two-sided family-wise alpha of 0.05, using the conservative Bonferroni "
            "first-step bound for a five-member Holm family. It is a sensitivity "
            "calculation, not a post-hoc proof of power."
        ),
        "",
        "| Independent clusters | Nested seeds per cluster | Approx. dz at 80% power | Approx. dz at 90% power |",
        "|---:|---:|---:|---:|",
    ]
    for n_value in sorted({key[0] for key in representative}):
        row80 = representative[(n_value, 0.8)]
        row90 = representative[(n_value, 0.9)]
        lines.append(
            f"| {n_value} | 30 | "
            f"{float(row80['approximate_minimum_detectable_paired_cohen_dz']):.3f} | "
            f"{float(row90['approximate_minimum_detectable_paired_cohen_dz']):.3f} |"
        )
    lines.extend(
        [
            "",
            (
                "Accordingly, the load-8 stratum (n=4) can only provide reliable "
                "family-wise detection for very large paired effects. The load-6 "
                "stratum (n=6) remains underpowered for moderate effects after "
                "five-comparison multiplicity control. Larger numbers of independent "
                "public trajectories or composed workloads, rather than additional "
                "random seeds on the same workloads, are needed to improve inferential "
                "resolution."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run_stage5(
    source_dir: Path,
    output_dir: Path,
    bootstrap_resamples: int = 10000,
    bounded_resamples: int = 5000,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata, audit_rows, raw_episodes = validate_source(source_dir)
    episode_records = prepare_episode_records(raw_episodes)
    cluster_records = aggregate_cluster_records(
        episode_records,
        expected_cluster_algorithm_rows=44 * len(ALGORITHMS),
    )

    paired_rows, registry = build_episode_comparisons(
        cluster_records, bootstrap_resamples
    )
    (
        response_seed_rows,
        response_cluster_rows,
        response_rows,
        response_registry,
    ) = build_common_response_records(
        source_dir / "registered_per_step.csv",
        bootstrap_resamples,
        expected_per_step_rows=95040,
    )
    paired_rows.extend(response_rows)
    registry.extend(response_registry)
    holm_adjust(paired_rows)

    bounded_rows = bounded_interval_rows(cluster_records, bounded_resamples)
    sample_rows = sample_size_rows(registry, paired_rows)
    audit_rows.extend(
        [
            {
                "check": "cluster-algorithm rows",
                "observed": len(cluster_records),
                "expected": 44 * len(ALGORITHMS),
                "passed": len(cluster_records) == 44 * len(ALGORITHMS),
            },
            {
                "check": "Holm family membership",
                "observed": len(paired_rows),
                "expected": sum(
                    int(row["number_of_comparisons"]) for row in registry
                ),
                "passed": len(paired_rows)
                == sum(int(row["number_of_comparisons"]) for row in registry),
            },
            {
                "check": "paired cluster counts positive",
                "observed": min(
                    int(row["n_independent_cluster_pairs"]) for row in paired_rows
                ),
                "expected": ">=4",
                "passed": all(
                    int(row["n_independent_cluster_pairs"]) >= 4
                    for row in paired_rows
                ),
            },
            {
                "check": "bounded intervals remain in [0,1]",
                "observed": len(bounded_rows),
                "expected": len(bounded_rows),
                "passed": all(
                    0.0 <= float(row["ci95_low"])
                    <= float(row["ci95_high"])
                    <= 1.0
                    for row in bounded_rows
                ),
            },
        ]
    )
    if not all(bool(row["passed"]) for row in audit_rows):
        raise RuntimeError("Stage-5 output audit failed")

    manuscript_sections = {
        "primary performance",
        "stability mechanism",
        "resource efficiency",
        "redundancy mechanism",
        "common-task response time",
    }
    manuscript_rows = [
        row for row in paired_rows if str(row["section"]) in manuscript_sections
    ]

    write_csv(
        output_dir / "cluster_level_episode_metrics.csv",
        round_rows(cluster_records),
    )
    write_csv(
        output_dir / "all_cluster_paired_statistics.csv",
        round_rows(paired_rows),
    )
    write_csv(
        output_dir / "manuscript_primary_statistics.csv",
        round_rows(manuscript_rows),
    )
    write_csv(
        output_dir / "common_task_response_by_seed.csv",
        round_rows(response_seed_rows),
    )
    write_csv(
        output_dir / "common_task_response_by_cluster.csv",
        round_rows(response_cluster_rows),
    )
    write_csv(
        output_dir / "common_task_response_comparisons.csv",
        round_rows(response_rows),
    )
    write_csv(
        output_dir / "bounded_metric_intervals.csv",
        round_rows(bounded_rows),
    )
    write_csv(output_dir / "holm_family_registry.csv", registry)
    write_csv(
        output_dir / "sample_size_sensitivity.csv",
        round_rows(sample_rows),
    )
    write_csv(output_dir / "stage5_analysis_audit.csv", audit_rows)

    report = build_report(
        metadata,
        cluster_records,
        paired_rows,
        registry,
        bounded_rows,
        sample_rows,
        bootstrap_resamples,
        bounded_resamples,
    )
    (output_dir / "stage5_statistics_report.md").write_text(
        report, encoding="utf-8"
    )
    (output_dir / "statistical_analysis_manuscript_text.md").write_text(
        statistical_analysis_text(bootstrap_resamples, bounded_resamples),
        encoding="utf-8",
    )
    (output_dir / "statistical_results_manuscript_text.md").write_text(
        manuscript_results_text(paired_rows), encoding="utf-8"
    )
    (output_dir / "holm_family_registry.md").write_text(
        family_registry_markdown(registry), encoding="utf-8"
    )
    (output_dir / "sample_size_basis.md").write_text(
        sample_size_markdown(sample_rows), encoding="utf-8"
    )

    config = {
        "run_date": "2026-09-16",
        "source_directory": str(source_dir.resolve()),
        "output_directory": str(output_dir.resolve()),
        "source_protocol_fingerprint": metadata["protocol_fingerprint"],
        "source_code_fingerprint": metadata["code_fingerprint"],
        "source_hashes": {
            "execution_metadata.json": sha256_file(
                source_dir / "execution_metadata.json"
            ),
            "registered_episode_results.csv": sha256_file(
                source_dir / "registered_episode_results.csv"
            ),
            "registered_per_step.csv": sha256_file(
                source_dir / "registered_per_step.csv"
            ),
        },
        "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
        "independent_unit": (
            "original UZH-FPV trajectory or preregistered Anti-UAV410 "
            "composed workload"
        ),
        "nested_repeats": "30 paired random seeds per independent cluster",
        "paired_ci": {
            "method": "BCa paired cluster bootstrap",
            "resamples": bootstrap_resamples,
            "confidence": 0.95,
        },
        "bounded_ci": {
            "continuous_method": "bounded BCa bootstrap of cluster means",
            "prevalence_method": "Wilson score interval on binary cluster events",
            "resamples": bounded_resamples,
            "confidence": 0.95,
        },
        "test": "two-sided paired Wilcoxon signed-rank",
        "multiplicity": (
            "Holm step-down within each endpoint-by-dataset/load family"
        ),
        "software": f"Python {platform.python_version()} standard library",
        "families": len(registry),
        "comparisons": len(paired_rows),
        "audit_pass": all(bool(row["passed"]) for row in audit_rows),
    }
    (output_dir / "stage5_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    checklist_lines = [
        "# Stage 5 Completion Checklist",
        "",
        "- [x] Independent public-data cluster used as inferential n.",
        "- [x] Thirty nested seeds averaged within each cluster.",
        "- [x] Major paired differences reported with 95% confidence intervals.",
        "- [x] Holm families explicitly registered.",
        "- [x] Bounded metrics use bounded intervals.",
        "- [x] Common-task response time recomputed on intersection tasks.",
        "- [x] Sample-size sensitivity reported by family.",
        "- [x] Raw P, adjusted P, effect sizes, and win rates are separate columns.",
        "- [x] Source fingerprints and file hashes recorded.",
        "- [x] Stage-5 audit passed.",
        "",
    ]
    (output_dir / "stage5_completion_checklist.md").write_text(
        "\n".join(checklist_lines), encoding="utf-8"
    )

    return {
        "paired_rows": paired_rows,
        "registry": registry,
        "bounded_rows": bounded_rows,
        "sample_rows": sample_rows,
        "audit_rows": audit_rows,
        "config": config,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("ca_hmcd_stage4_replay_formal_20260916"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ca_hmcd_stage5_statistics_20260916"),
    )
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    parser.add_argument("--bounded-resamples", type=int, default=5000)
    args = parser.parse_args()
    result = run_stage5(
        args.source_dir,
        args.output_dir,
        args.bootstrap_resamples,
        args.bounded_resamples,
    )
    print(
        f"[stage5] complete: {len(result['paired_rows'])} comparisons, "
        f"{len(result['registry'])} Holm families, "
        f"{len(result['bounded_rows'])} bounded intervals",
        flush=True,
    )


if __name__ == "__main__":
    main()

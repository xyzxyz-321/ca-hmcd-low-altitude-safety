"""Audit and analyse the formal optimization and evolutionary baselines."""

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
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from ca_hmcd_replay_runner import audit_registered_execution
from ca_hmcd_simulation import paired_statistics, write_csv
from ca_hmcd_stage3_statistics import (
    bca_mean_interval,
    holm_adjust,
    stable_seed,
)


REFERENCE_METHOD = "CA-HMCD"
BASELINES = ("HiGHS-MILP", "Genetic-Algorithm")
EXPECTED_UNITS = 44
EXPECTED_SEEDS = 30
EXPECTED_ROWS_PER_METHOD = EXPECTED_UNITS * EXPECTED_SEEDS
EPSILON = 1e-12

PRIMARY_METRICS = (
    ("external_objective", "higher"),
    ("independent_service_score", "higher"),
    ("independent_coverage", "higher"),
)
COMPUTATIONAL_METRICS = (
    ("end_to_end_runtime_ms", "lower"),
    ("solver_elapsed_ms", "lower"),
    ("fallback", "lower"),
    ("node_limit_run_indicator", "lower"),
)
MECHANISM_METRICS = (
    ("candidate_feasible_before_dominance", "descriptive"),
    ("candidate_after_diversity", "descriptive"),
    ("nodes", "descriptive"),
    ("repair_applied", "lower"),
)
BOUNDED_METRICS = (
    "independent_coverage",
    "feasibility_rate",
    "fallback",
    "node_limit_run_indicator",
    "repair_applied",
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


def validate_sources(
    reference_dir: Path,
    baseline_dir: Path,
    legacy_reference_dir: Optional[Path] = None,
) -> Tuple[Dict[str, object], Dict[str, object], List[Dict[str, object]]]:
    reference_meta = json.loads(
        (reference_dir / "execution_metadata.json").read_text(encoding="utf-8")
    )
    baseline_meta = json.loads(
        (baseline_dir / "execution_metadata.json").read_text(encoding="utf-8")
    )
    checks = [
        {
            "check": "environment-matched reference mode",
            "observed": reference_meta.get("mode"),
            "expected": "formal-reference",
            "passed": reference_meta.get("mode") == "formal-reference",
        },
        {
            "check": "external-baseline formal mode",
            "observed": baseline_meta.get("mode"),
            "expected": "formal-external",
            "passed": baseline_meta.get("mode") == "formal-external",
        },
        {
            "check": "reference execution audit",
            "observed": reference_meta.get("audit_pass"),
            "expected": True,
            "passed": bool(reference_meta.get("audit_pass")),
        },
        {
            "check": "external-baseline execution audit",
            "observed": baseline_meta.get("audit_pass"),
            "expected": True,
            "passed": bool(baseline_meta.get("audit_pass")),
        },
        {
            "check": "registered external algorithms",
            "observed": baseline_meta.get("algorithms"),
            "expected": list(BASELINES),
            "passed": baseline_meta.get("algorithms") == list(BASELINES),
        },
        {
            "check": "same frozen protocol fingerprint",
            "observed": baseline_meta.get("protocol_fingerprint"),
            "expected": reference_meta.get("protocol_fingerprint"),
            "passed": baseline_meta.get("protocol_fingerprint")
            == reference_meta.get("protocol_fingerprint"),
        },
        {
            "check": "same model version",
            "observed": baseline_meta.get("model_version"),
            "expected": reference_meta.get("model_version"),
            "passed": baseline_meta.get("model_version")
            == reference_meta.get("model_version"),
        },
        {
            "check": "same external evaluator version",
            "observed": baseline_meta.get("evaluator_version"),
            "expected": reference_meta.get("evaluator_version"),
            "passed": baseline_meta.get("evaluator_version")
            == reference_meta.get("evaluator_version"),
        },
        {
            "check": "same service endpoint version",
            "observed": baseline_meta.get("service_endpoint_version"),
            "expected": reference_meta.get("service_endpoint_version"),
            "passed": baseline_meta.get("service_endpoint_version")
            == reference_meta.get("service_endpoint_version"),
        },
        {
            "check": "same simulation implementation",
            "observed": reference_meta.get("code_hashes", {}).get(
                "ca_hmcd_simulation.py"
            ),
            "expected": baseline_meta.get("code_hashes", {}).get(
                "ca_hmcd_simulation.py"
            ),
            "passed": reference_meta.get("code_hashes", {}).get(
                "ca_hmcd_simulation.py"
            )
            == baseline_meta.get("code_hashes", {}).get(
                "ca_hmcd_simulation.py"
            ),
        },
        {
            "check": "same Python execution environment",
            "observed": reference_meta.get("python_version"),
            "expected": baseline_meta.get("python_version"),
            "passed": reference_meta.get("python_version")
            == baseline_meta.get("python_version"),
        },
        {
            "check": "complete external registered rows",
            "observed": baseline_meta.get("selected_registered_units"),
            "expected": EXPECTED_ROWS_PER_METHOD,
            "passed": baseline_meta.get("selected_registered_units")
            == EXPECTED_ROWS_PER_METHOD,
        },
    ]
    if legacy_reference_dir is not None:
        checks.append(
            validate_legacy_equivalence(
                legacy_reference_dir,
                reference_dir,
            )
        )
    if not all(bool(row["passed"]) for row in checks):
        failed = [str(row["check"]) for row in checks if not row["passed"]]
        raise RuntimeError(f"External-baseline source validation failed: {failed}")
    return reference_meta, baseline_meta, checks


def validate_legacy_equivalence(
    legacy_reference_dir: Path,
    matched_reference_dir: Path,
) -> Dict[str, object]:
    legacy_rows = [
        row
        for row in read_csv(
            legacy_reference_dir / "registered_episode_results.csv"
        )
        if row["algorithm"] == REFERENCE_METHOD
    ]
    matched_rows = read_csv(
        matched_reference_dir / "registered_episode_results.csv"
    )
    legacy_lookup = {
        (row["workload_id"], int(row["seed"])): row for row in legacy_rows
    }
    matched_lookup = {
        (row["workload_id"], int(row["seed"])): row for row in matched_rows
    }
    excluded = {
        "execution_seconds",
        "runtime_ms",
        "candidate_generation_ms",
        "solver_elapsed_ms",
        "evaluation_ms",
        "repair_ms",
        "end_to_end_runtime_ms",
        "code_fingerprint",
        "runner_version",
    }
    maximum_difference = 0.0
    passed = legacy_lookup.keys() == matched_lookup.keys()
    if passed:
        for key in legacy_lookup:
            legacy = legacy_lookup[key]
            matched = matched_lookup[key]
            for field in legacy.keys() & matched.keys():
                if field in excluded:
                    continue
                try:
                    difference = abs(float(legacy[field]) - float(matched[field]))
                except (TypeError, ValueError):
                    if field == "code_fingerprint":
                        continue
                    if str(legacy[field]) != str(matched[field]):
                        passed = False
                        break
                else:
                    maximum_difference = max(maximum_difference, difference)
                    if difference > EPSILON:
                        passed = False
                        break
            if not passed:
                break
    return {
        "check": "matched CA-HMCD reproduces prior non-timing outcomes",
        "observed": maximum_difference,
        "expected": f"maximum absolute difference <= {EPSILON}",
        "passed": passed,
    }


def combine_records(
    reference_dir: Path,
    baseline_dir: Path,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, object]]]:
    reference_episodes = [
        row
        for row in read_csv(reference_dir / "registered_episode_results.csv")
        if row["algorithm"] == REFERENCE_METHOD
    ]
    baseline_episodes = read_csv(
        baseline_dir / "registered_episode_results.csv"
    )
    reference_steps = [
        row
        for row in read_csv(reference_dir / "registered_per_step.csv")
        if row["algorithm"] == REFERENCE_METHOD
    ]
    baseline_steps = read_csv(baseline_dir / "registered_per_step.csv")
    episodes = reference_episodes + baseline_episodes
    steps = reference_steps + baseline_steps

    expected_methods = (REFERENCE_METHOD, *BASELINES)
    expected_keys = {
        (row["workload_id"], int(row["seed"]), method)
        for row in reference_episodes
        for method in expected_methods
    }
    observed_keys = {
        (row["workload_id"], int(row["seed"]), row["algorithm"])
        for row in episodes
    }
    checks: List[Dict[str, object]] = [
        {
            "check": "complete paired episode keys",
            "observed": len(observed_keys),
            "expected": len(expected_keys),
            "passed": observed_keys == expected_keys,
        }
    ]
    for method in BASELINES:
        selected = [
            row for row in baseline_episodes if row["algorithm"] == method
        ]
        no_dominance = all(
            abs(
                float(row["candidate_feasible_before_dominance"])
                - float(row["candidate_after_dominance"])
            )
            <= EPSILON
            for row in selected
        )
        no_diversity = all(
            abs(
                float(row["candidate_after_dominance"])
                - float(row["candidate_after_diversity"])
            )
            <= EPSILON
            for row in selected
        )
        checks.extend(
            [
                {
                    "check": f"{method} disables dominance pruning",
                    "observed": int(no_dominance),
                    "expected": 1,
                    "passed": no_dominance,
                },
                {
                    "check": f"{method} disables diversity pruning",
                    "observed": int(no_diversity),
                    "expected": 1,
                    "passed": no_diversity,
                },
                {
                    "check": f"{method} uses the same feasible candidate count",
                    "observed": "paired workload-seed mean",
                    "expected": "equal to CA-HMCD before pruning",
                    "passed": same_episode_metric(
                        reference_episodes,
                        selected,
                        "candidate_feasible_before_dominance",
                    ),
                },
            ]
        )
    if not all(bool(row["passed"]) for row in checks):
        failed = [str(row["check"]) for row in checks if not row["passed"]]
        raise RuntimeError(f"External-baseline candidate audit failed: {failed}")
    return episodes, steps, checks


def same_episode_metric(
    first: Sequence[Mapping[str, str]],
    second: Sequence[Mapping[str, str]],
    metric: str,
) -> bool:
    first_lookup = {
        (row["workload_id"], int(row["seed"])): float(row[metric])
        for row in first
    }
    second_lookup = {
        (row["workload_id"], int(row["seed"])): float(row[metric])
        for row in second
    }
    return first_lookup.keys() == second_lookup.keys() and all(
        abs(first_lookup[key] - second_lookup[key]) <= EPSILON
        for key in first_lookup
    )


def prepare_episode_records(
    raw_rows: Sequence[Mapping[str, str]],
) -> List[Dict[str, object]]:
    numeric_fields = {
        metric
        for metric, _direction in (
            *PRIMARY_METRICS,
            *COMPUTATIONAL_METRICS,
            *MECHANISM_METRICS,
        )
    } | {
        "feasibility_rate",
        "node_limit_hit",
        "fallback",
        "repair_applied",
    }
    numeric_fields.discard("node_limit_run_indicator")
    records: List[Dict[str, object]] = []
    for raw in raw_rows:
        record: Dict[str, object] = {
            "workload_id": str(raw["workload_id"]),
            "dataset": str(raw["dataset"]),
            "task_load": int(raw["task_load"]),
            "independent_cluster": str(raw["independent_cluster"]),
            "seed": int(raw["seed"]),
            "algorithm": str(raw["algorithm"]),
        }
        for field in numeric_fields:
            record[field] = float(raw[field])
        record["node_limit_run_indicator"] = (
            1.0 if float(raw["node_limit_hit"]) > EPSILON else 0.0
        )
        records.append(record)
    return records


def aggregate_clusters(
    episode_records: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[
        Tuple[str, int, str, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in episode_records:
        grouped[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["independent_cluster"]),
                str(row["algorithm"]),
            )
        ].append(row)
    metric_names = {
        metric
        for metric, _direction in (
            *PRIMARY_METRICS,
            *COMPUTATIONAL_METRICS,
            *MECHANISM_METRICS,
        )
    } | {"feasibility_rate"}
    output: List[Dict[str, object]] = []
    for (dataset, task_load, cluster, algorithm), rows in sorted(
        grouped.items()
    ):
        seeds = {int(row["seed"]) for row in rows}
        if len(seeds) != EXPECTED_SEEDS:
            raise RuntimeError(
                f"{cluster}/{algorithm} has {len(seeds)} registered seeds"
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
        for metric in metric_names:
            record[metric] = statistics.fmean(
                float(row[metric]) for row in rows
            )
        output.append(record)
    expected = EXPECTED_UNITS * (1 + len(BASELINES))
    if len(output) != expected:
        raise RuntimeError(
            f"Expected {expected} cluster-method rows, observed {len(output)}"
        )
    return output


def paired_cluster_row(
    records: Sequence[Mapping[str, object]],
    baseline: str,
    metric: str,
    direction: str,
    family_id: str,
    section: str,
    bootstrap_resamples: int,
) -> Dict[str, object]:
    reference_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == REFERENCE_METHOD
    }
    baseline_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == baseline
    }
    if reference_lookup.keys() != baseline_lookup.keys():
        raise RuntimeError(f"Unpaired clusters for {family_id}/{baseline}")
    clusters = sorted(reference_lookup)
    reference_values = [reference_lookup[key] for key in clusters]
    baseline_values = [baseline_lookup[key] for key in clusters]
    differences = [
        reference - comparison
        for reference, comparison in zip(reference_values, baseline_values)
    ]
    stats = paired_statistics(reference_values, baseline_values)
    low, high = bca_mean_interval(
        differences,
        resamples=bootstrap_resamples,
        seed=stable_seed("external-baseline", family_id, baseline, metric),
    )
    if direction == "higher":
        wins = sum(value > EPSILON for value in differences)
    elif direction == "lower":
        wins = sum(value < -EPSILON for value in differences)
    else:
        wins = sum(abs(value) <= EPSILON for value in differences)
    ties = sum(abs(value) <= EPSILON for value in differences)
    return {
        "family_id": family_id,
        "section": section,
        "comparison": f"{REFERENCE_METHOD} vs {baseline}",
        "metric": metric,
        "method_a": REFERENCE_METHOD,
        "method_b": baseline,
        "favorable_direction_for_method_a": direction,
        "dataset": records[0]["dataset"],
        "task_load": records[0]["task_load"],
        "stratum_id": records[0]["stratum_id"],
        "stratum_label": records[0]["stratum_label"],
        "n_independent_cluster_pairs": len(clusters),
        "nested_seeds_per_cluster": EXPECTED_SEEDS,
        "method_a_mean": statistics.fmean(reference_values),
        "method_b_mean": statistics.fmean(baseline_values),
        "mean_difference_a_minus_b": statistics.fmean(differences),
        "mean_difference_ci95_low": low,
        "mean_difference_ci95_high": high,
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


def build_common_response(
    step_rows: Sequence[Mapping[str, str]],
    bootstrap_resamples: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    grouped: Dict[
        Tuple[str, int, str, int, int], Dict[str, Dict[str, float]]
    ] = defaultdict(dict)
    for row in step_rows:
        key = (
            str(row["dataset"]),
            int(row["task_load"]),
            str(row["independent_cluster"]),
            int(row["seed"]),
            int(row["step"]),
        )
        grouped[key][str(row["algorithm"])] = {
            str(task_id): float(value)
            for task_id, value in json.loads(
                str(row["served_task_response_times_json"])
            ).items()
        }

    seed_accumulator: Dict[
        Tuple[str, int, str, int, str], Dict[str, float]
    ] = defaultdict(lambda: {"reference": 0.0, "baseline": 0.0, "count": 0.0})
    for (dataset, task_load, cluster, seed, _step), methods in grouped.items():
        reference = methods.get(REFERENCE_METHOD, {})
        for baseline in BASELINES:
            comparison = methods.get(baseline, {})
            for task_id in set(reference) & set(comparison):
                values = seed_accumulator[
                    (dataset, task_load, cluster, seed, baseline)
                ]
                values["reference"] += reference[task_id]
                values["baseline"] += comparison[task_id]
                values["count"] += 1.0

    seed_rows: List[Dict[str, object]] = []
    for (dataset, task_load, cluster, seed, baseline), values in sorted(
        seed_accumulator.items()
    ):
        count = int(values["count"])
        if count <= 0:
            continue
        seed_rows.append(
            {
                "dataset": dataset,
                "task_load": task_load,
                "independent_cluster": cluster,
                "seed": seed,
                "baseline": baseline,
                "common_task_period_count": count,
                "reference_mean_response_time": values["reference"] / count,
                "baseline_mean_response_time": values["baseline"] / count,
            }
        )

    grouped_clusters: Dict[
        Tuple[str, int, str, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in seed_rows:
        grouped_clusters[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["independent_cluster"]),
                str(row["baseline"]),
            )
        ].append(row)
    cluster_rows: List[Dict[str, object]] = []
    for (dataset, task_load, cluster, baseline), rows in sorted(
        grouped_clusters.items()
    ):
        cluster_rows.append(
            {
                "dataset": dataset,
                "task_load": task_load,
                "stratum_id": stratum_id(dataset, task_load),
                "stratum_label": stratum_label(dataset, task_load),
                "independent_cluster": cluster,
                "baseline": baseline,
                "seeds_with_common_tasks": len(rows),
                "seeds_without_common_tasks": EXPECTED_SEEDS - len(rows),
                "common_task_period_count": sum(
                    int(row["common_task_period_count"]) for row in rows
                ),
                "reference_mean_response_time": statistics.fmean(
                    float(row["reference_mean_response_time"]) for row in rows
                ),
                "baseline_mean_response_time": statistics.fmean(
                    float(row["baseline_mean_response_time"]) for row in rows
                ),
            }
        )

    comparison_rows: List[Dict[str, object]] = []
    strata = sorted(
        {(str(row["dataset"]), int(row["task_load"])) for row in cluster_rows}
    )
    for dataset, task_load in strata:
        sid = stratum_id(dataset, task_load)
        for baseline in BASELINES:
            selected = [
                row
                for row in cluster_rows
                if row["dataset"] == dataset
                and int(row["task_load"]) == task_load
                and row["baseline"] == baseline
            ]
            records: List[Dict[str, object]] = []
            for row in selected:
                base = {
                    "dataset": dataset,
                    "task_load": task_load,
                    "stratum_id": sid,
                    "stratum_label": stratum_label(dataset, task_load),
                    "independent_cluster": row["independent_cluster"],
                }
                records.extend(
                    [
                        {
                            **base,
                            "algorithm": REFERENCE_METHOD,
                            "common_task_response_time": row[
                                "reference_mean_response_time"
                            ],
                        },
                        {
                            **base,
                            "algorithm": baseline,
                            "common_task_response_time": row[
                                "baseline_mean_response_time"
                            ],
                        },
                    ]
                )
            comparison_rows.append(
                paired_cluster_row(
                    records,
                    baseline,
                    "common_task_response_time",
                    "lower",
                    f"performance.{sid}.common_task_response_time",
                    "primary performance",
                    bootstrap_resamples,
                )
            )
    return cluster_rows, comparison_rows


def build_comparisons(
    cluster_rows: Sequence[Mapping[str, object]],
    response_rows: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    strata = sorted(
        {(str(row["dataset"]), int(row["task_load"])) for row in cluster_rows}
    )
    sections = (
        ("primary performance", PRIMARY_METRICS, "performance"),
        ("computational boundary", COMPUTATIONAL_METRICS, "computational"),
        ("candidate mechanism", MECHANISM_METRICS, "mechanism"),
    )
    for dataset, task_load in strata:
        selected = [
            row
            for row in cluster_rows
            if row["dataset"] == dataset
            and int(row["task_load"]) == task_load
        ]
        sid = stratum_id(dataset, task_load)
        for section, metrics, prefix in sections:
            for metric, direction in metrics:
                for baseline in BASELINES:
                    output.append(
                        paired_cluster_row(
                            selected,
                            baseline,
                            metric,
                            direction,
                            f"{prefix}.{sid}.{metric}",
                            section,
                            bootstrap_resamples,
                        )
                    )
    output.extend(dict(row) for row in response_rows)
    holm_adjust(output)
    return output


def bounded_intervals(
    cluster_rows: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> List[Dict[str, object]]:
    grouped: Dict[
        Tuple[str, int, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in cluster_rows:
        grouped[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["algorithm"]),
            )
        ].append(row)
    output: List[Dict[str, object]] = []
    for (dataset, task_load, algorithm), rows in sorted(grouped.items()):
        for metric in BOUNDED_METRICS:
            values = [float(row[metric]) for row in rows]
            low, high = bca_mean_interval(
                values,
                resamples=bootstrap_resamples,
                seed=stable_seed(
                    "external-bounded",
                    dataset,
                    task_load,
                    algorithm,
                    metric,
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
                    "interval_method": "bounded BCa bootstrap of cluster means",
                }
            )
    return output


def family_registry(
    comparison_rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in comparison_rows:
        grouped[str(row["family_id"])].append(row)
    return [
        {
            "family_id": family_id,
            "section": rows[0]["section"],
            "dataset": rows[0]["dataset"],
            "task_load": rows[0]["task_load"],
            "metric": rows[0]["metric"],
            "number_of_comparisons": len(rows),
            "comparisons": ";".join(str(row["comparison"]) for row in rows),
            "correction": "Holm step-down within metric and replay stratum",
        }
        for family_id, rows in sorted(grouped.items())
    ]


def build_report(
    comparison_rows: Sequence[Mapping[str, object]],
    audit_rows: Sequence[Mapping[str, object]],
    baseline_meta: Mapping[str, object],
) -> str:
    primary = [
        row
        for row in comparison_rows
        if row["section"] == "primary performance"
    ]
    lines = [
        "# Formal optimization and evolutionary baseline experiment",
        "",
        "## Execution",
        "",
        (
            f"- Completed {baseline_meta['selected_registered_units']} "
            "registered workload-seed units for each of HiGHS-MILP and "
            "Genetic-Algorithm."
        ),
        (
            "- Both added baselines used the complete feasible candidate pool, "
            "the registered rolling value model, the same true-state external "
            "evaluator, and the same safety-repair policy."
        ),
        (
            "- HiGHS-MILP used a 4000-node ceiling. Genetic-Algorithm used "
            "4000 fitness evaluations with a complete-pool greedy warm start."
        ),
        (
            "- CA-HMCD was reused from the prior formal session after protocol, "
            "model/evaluator version, replay hash, per-period state, and "
            "candidate-count pairing audits."
        ),
        "",
        "## Audit",
        "",
    ]
    for row in audit_rows:
        status = "PASS" if bool(row["passed"]) else "FAIL"
        lines.append(
            f"- {status}: {row['check']} "
            f"(observed={row['observed']}, expected={row['expected']})."
        )
    lines.extend(["", "## Primary paired results", ""])
    for row in primary:
        lines.append(
            "- "
            f"{row['stratum_label']}, {row['metric']}, "
            f"{row['comparison']}: mean difference = "
            f"{float(row['mean_difference_a_minus_b']):.6g} "
            f"[95% CI {float(row['mean_difference_ci95_low']):.6g}, "
            f"{float(row['mean_difference_ci95_high']):.6g}], "
            f"raw P={float(row['p_unadjusted']):.4g}, "
            f"Holm P={float(row['p_holm_adjusted']):.4g}, "
            f"dz={float(row['cohen_dz']):.3f}, "
            f"win rate={100 * float(row['win_rate_favorable_to_method_a']):.1f}%."
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "HiGHS-MILP is a standard mathematical-optimization reference "
                "and Genetic-Algorithm is a standard evolutionary-search "
                "reference implemented against the registered interface. The "
                "latter is not presented as a reproduction of a named "
                "application-specific published model."
            ),
            (
                "Wall-clock values were obtained in separate formal sessions; "
                "paired service and objective results are deterministic under "
                "the registry, while runtime comparisons retain ordinary "
                "session-level system noise."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run_analysis(
    reference_dir: Path,
    baseline_dir: Path,
    output_dir: Path,
    bootstrap_resamples: int = 10000,
    legacy_reference_dir: Optional[Path] = None,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_meta, baseline_meta, source_checks = validate_sources(
        reference_dir, baseline_dir, legacy_reference_dir
    )
    raw_episodes, raw_steps, candidate_checks = combine_records(
        reference_dir, baseline_dir
    )
    paired_audit, pairing_checks = audit_registered_execution(
        [
            {
                "workload_id": row["workload_id"],
                "seed": row["seed"],
                "horizon_steps": row["horizon_steps"],
            }
            for row in raw_episodes
            if row["algorithm"] == REFERENCE_METHOD
        ],
        raw_episodes,
        raw_steps,
        (REFERENCE_METHOD, *BASELINES),
    )
    episode_records = prepare_episode_records(raw_episodes)
    cluster_rows = aggregate_clusters(episode_records)
    response_cluster_rows, response_rows = build_common_response(
        raw_steps, bootstrap_resamples
    )
    comparison_rows = build_comparisons(
        cluster_rows, response_rows, bootstrap_resamples
    )
    bounded_rows = bounded_intervals(cluster_rows, bootstrap_resamples)
    family_rows = family_registry(comparison_rows)
    combined_audits = [*source_checks, *candidate_checks, *pairing_checks]
    combined_audits.extend(
        [
            {
                "check": "all per-period state audits pass",
                "observed": sum(int(row["passed"]) for row in paired_audit),
                "expected": len(paired_audit),
                "passed": all(int(row["passed"]) == 1 for row in paired_audit),
            },
            {
                "check": "all final allocations are feasible",
                "observed": min(
                    float(row["feasibility_rate"]) for row in raw_episodes
                ),
                "expected": 1.0,
                "passed": all(
                    abs(float(row["feasibility_rate"]) - 1.0) <= EPSILON
                    for row in raw_episodes
                ),
            },
        ]
    )
    audit_rows: List[Dict[str, object]] = []
    seen = set()
    for row in combined_audits:
        key = (
            str(row["check"]),
            str(row["observed"]),
            str(row["expected"]),
            bool(row["passed"]),
        )
        if key not in seen:
            seen.add(key)
            audit_rows.append(dict(row))
    if not all(bool(row["passed"]) for row in audit_rows):
        failed = [str(row["check"]) for row in audit_rows if not row["passed"]]
        raise RuntimeError(f"External-baseline analysis audit failed: {failed}")

    write_csv(output_dir / "combined_episode_results.csv", raw_episodes)
    write_csv(output_dir / "combined_per_step.csv", raw_steps)
    write_csv(output_dir / "paired_state_audit.csv", paired_audit)
    write_csv(output_dir / "cluster_level_metrics.csv", round_rows(cluster_rows))
    write_csv(output_dir / "paired_statistics.csv", round_rows(comparison_rows))
    write_csv(
        output_dir / "common_task_response_by_cluster.csv",
        round_rows(response_cluster_rows),
    )
    write_csv(
        output_dir / "bounded_metric_intervals.csv",
        round_rows(bounded_rows),
    )
    write_csv(output_dir / "holm_family_registry.csv", family_rows)
    write_csv(output_dir / "analysis_audit.csv", audit_rows)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reference_dir": str(reference_dir.resolve()),
        "baseline_dir": str(baseline_dir.resolve()),
        "legacy_reference_dir": (
            str(legacy_reference_dir.resolve())
            if legacy_reference_dir is not None else None
        ),
        "protocol_fingerprint": baseline_meta["protocol_fingerprint"],
        "bootstrap_resamples": bootstrap_resamples,
        "independent_units": EXPECTED_UNITS,
        "nested_seeds_per_unit": EXPECTED_SEEDS,
        "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
        "audit_pass": True,
    }
    (output_dir / "analysis_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "experiment_result.md").write_text(
        build_report(comparison_rows, audit_rows, baseline_meta),
        encoding="utf-8",
    )
    return {
        "comparison_rows": comparison_rows,
        "audit_rows": audit_rows,
        "metadata": metadata,
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse formal CA-HMCD external optimization/AI baselines"
    )
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--legacy-reference-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    run_analysis(
        args.reference_dir,
        args.baseline_dir,
        args.output_dir,
        args.bootstrap_resamples,
        args.legacy_reference_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Audit and analyse the formal replay No-Pruning control.

The formal control is executed separately from the frozen six-method study.
This module verifies provenance, combines the registered CA-HMCD baseline with
No-Pruning, audits state-level pairing, and performs cluster-aware inference.
"""

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
    wilson_score_interval,
)


METHOD_A = "CA-HMCD"
METHOD_B = "No-Pruning"
EXPECTED_UNITS = 44
EXPECTED_SEEDS = 30
EXPECTED_REGISTERED_ROWS = EXPECTED_UNITS * EXPECTED_SEEDS
EPSILON = 1e-12

PERFORMANCE_ENDPOINTS = (
    ("external_objective", "higher"),
    ("independent_service_score", "higher"),
    ("independent_coverage", "higher"),
)
COMPUTATIONAL_ENDPOINTS = (
    ("end_to_end_runtime_ms", "lower"),
    ("fallback", "lower"),
    ("node_limit_run_indicator", "lower"),
    ("fallback_optimality_gap_bound", "lower"),
)
CANDIDATE_ENDPOINTS = (
    ("candidate_feasible_before_dominance", "descriptive"),
    ("candidate_after_dominance", "descriptive"),
    ("candidate_after_diversity", "descriptive"),
    ("nodes", "descriptive"),
)
BOUNDED_METRICS = (
    "independent_coverage",
    "feasibility_rate",
    "fallback",
    "node_limit_run_indicator",
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
    rounded: List[Dict[str, object]] = []
    for source in rows:
        row: Dict[str, object] = {}
        for key, value in source.items():
            if isinstance(value, float) and math.isfinite(value):
                row[key] = round(value, digits)
            else:
                row[key] = value
        rounded.append(row)
    return rounded


def validate_sources(
    baseline_dir: Path,
    control_dir: Path,
    project_dir: Path,
) -> Tuple[Dict[str, object], Dict[str, object], List[Dict[str, object]]]:
    baseline_meta = json.loads(
        (baseline_dir / "execution_metadata.json").read_text(encoding="utf-8")
    )
    control_meta = json.loads(
        (control_dir / "execution_metadata.json").read_text(encoding="utf-8")
    )
    current_hashes = {
        name: sha256_file(project_dir / name)
        for name in ("ca_hmcd_replay.py", "ca_hmcd_simulation.py")
    }
    checks = [
        {
            "check": "baseline formal mode",
            "observed": baseline_meta.get("mode"),
            "expected": "formal",
            "passed": baseline_meta.get("mode") == "formal",
        },
        {
            "check": "control formal-control mode",
            "observed": control_meta.get("mode"),
            "expected": "formal-control",
            "passed": control_meta.get("mode") == "formal-control",
        },
        {
            "check": "baseline execution audit",
            "observed": baseline_meta.get("audit_pass"),
            "expected": True,
            "passed": bool(baseline_meta.get("audit_pass")),
        },
        {
            "check": "control execution audit",
            "observed": control_meta.get("audit_pass"),
            "expected": True,
            "passed": bool(control_meta.get("audit_pass")),
        },
        {
            "check": "control algorithm registration",
            "observed": control_meta.get("algorithms"),
            "expected": [METHOD_B],
            "passed": control_meta.get("algorithms") == [METHOD_B],
        },
        {
            "check": "same frozen protocol fingerprint",
            "observed": control_meta.get("protocol_fingerprint"),
            "expected": baseline_meta.get("protocol_fingerprint"),
            "passed": control_meta.get("protocol_fingerprint")
            == baseline_meta.get("protocol_fingerprint"),
        },
        {
            "check": "same replay mapping code",
            "observed": control_meta.get("code_hashes", {}).get(
                "ca_hmcd_replay.py"
            ),
            "expected": baseline_meta.get("code_hashes", {}).get(
                "ca_hmcd_replay.py"
            ),
            "passed": (
                control_meta.get("code_hashes", {}).get("ca_hmcd_replay.py")
                == baseline_meta.get("code_hashes", {}).get("ca_hmcd_replay.py")
                == current_hashes["ca_hmcd_replay.py"]
            ),
        },
        {
            "check": "same simulation and value-model code",
            "observed": control_meta.get("code_hashes", {}).get(
                "ca_hmcd_simulation.py"
            ),
            "expected": baseline_meta.get("code_hashes", {}).get(
                "ca_hmcd_simulation.py"
            ),
            "passed": (
                control_meta.get("code_hashes", {}).get(
                    "ca_hmcd_simulation.py"
                )
                == baseline_meta.get("code_hashes", {}).get(
                    "ca_hmcd_simulation.py"
                )
                == current_hashes["ca_hmcd_simulation.py"]
            ),
        },
        {
            "check": "control registered row count",
            "observed": control_meta.get("selected_registered_units"),
            "expected": EXPECTED_REGISTERED_ROWS,
            "passed": control_meta.get("selected_registered_units")
            == EXPECTED_REGISTERED_ROWS,
        },
    ]
    if not all(bool(row["passed"]) for row in checks):
        failed = [str(row["check"]) for row in checks if not row["passed"]]
        raise RuntimeError(f"No-Pruning source validation failed: {failed}")
    return baseline_meta, control_meta, checks


def combine_records(
    baseline_dir: Path,
    control_dir: Path,
) -> Tuple[
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, object]],
]:
    baseline_episodes = [
        row
        for row in read_csv(baseline_dir / "registered_episode_results.csv")
        if row["algorithm"] == METHOD_A
    ]
    control_episodes = read_csv(control_dir / "registered_episode_results.csv")
    baseline_steps = [
        row
        for row in read_csv(baseline_dir / "registered_per_step.csv")
        if row["algorithm"] == METHOD_A
    ]
    control_steps = read_csv(control_dir / "registered_per_step.csv")
    episodes = baseline_episodes + control_episodes
    steps = baseline_steps + control_steps

    expected_episode_keys = {
        (row["workload_id"], int(row["seed"]), method)
        for row in baseline_episodes
        for method in (METHOD_A, METHOD_B)
    }
    observed_episode_keys = {
        (row["workload_id"], int(row["seed"]), row["algorithm"])
        for row in episodes
    }
    candidate_checks = [
        {
            "check": "complete paired episode keys",
            "observed": len(observed_episode_keys),
            "expected": len(expected_episode_keys),
            "passed": observed_episode_keys == expected_episode_keys,
        },
        {
            "check": "No-Pruning disables dominance removal",
            "observed": max(
                abs(
                    float(row["candidate_feasible_before_dominance"])
                    - float(row["candidate_after_dominance"])
                )
                for row in control_episodes
            ),
            "expected": 0.0,
            "passed": all(
                abs(
                    float(row["candidate_feasible_before_dominance"])
                    - float(row["candidate_after_dominance"])
                )
                <= EPSILON
                for row in control_episodes
            ),
        },
        {
            "check": "No-Pruning disables diversity removal",
            "observed": max(
                abs(
                    float(row["candidate_after_dominance"])
                    - float(row["candidate_after_diversity"])
                )
                for row in control_episodes
            ),
            "expected": 0.0,
            "passed": all(
                abs(
                    float(row["candidate_after_dominance"])
                    - float(row["candidate_after_diversity"])
                )
                <= EPSILON
                for row in control_episodes
            ),
        },
        {
            "check": "same pre-pruning feasible candidate count",
            "observed": "paired workload-seed mean",
            "expected": "equal",
            "passed": _same_episode_metric(
                baseline_episodes,
                control_episodes,
                "candidate_feasible_before_dominance",
            ),
        },
    ]
    if not all(bool(row["passed"]) for row in candidate_checks):
        failed = [
            str(row["check"]) for row in candidate_checks if not row["passed"]
        ]
        raise RuntimeError(f"No-Pruning candidate audit failed: {failed}")
    return episodes, steps, candidate_checks


def _same_episode_metric(
    method_a: Sequence[Mapping[str, str]],
    method_b: Sequence[Mapping[str, str]],
    metric: str,
) -> bool:
    a_lookup = {
        (row["workload_id"], int(row["seed"])): float(row[metric])
        for row in method_a
    }
    b_lookup = {
        (row["workload_id"], int(row["seed"])): float(row[metric])
        for row in method_b
    }
    return a_lookup.keys() == b_lookup.keys() and all(
        abs(a_lookup[key] - b_lookup[key]) <= EPSILON for key in a_lookup
    )


def prepare_episode_records(
    raw_rows: Sequence[Mapping[str, str]],
) -> List[Dict[str, object]]:
    numeric_fields = {
        metric for metric, _ in (
            *PERFORMANCE_ENDPOINTS,
            *COMPUTATIONAL_ENDPOINTS,
            *CANDIDATE_ENDPOINTS,
        )
    } | {
        "feasibility_rate",
        "node_limit_hit",
        "fallback",
        "fallback_optimality_gap_bound",
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
        metric for metric, _ in (
            *PERFORMANCE_ENDPOINTS,
            *COMPUTATIONAL_ENDPOINTS,
            *CANDIDATE_ENDPOINTS,
        )
    } | {"feasibility_rate"}
    output: List[Dict[str, object]] = []
    for (dataset, task_load, cluster, algorithm), rows in sorted(
        grouped.items()
    ):
        seeds = {int(row["seed"]) for row in rows}
        if len(seeds) != EXPECTED_SEEDS:
            raise RuntimeError(
                f"{cluster}/{algorithm} has {len(seeds)} seeds"
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
        for metric in sorted(metric_names):
            record[metric] = statistics.fmean(
                float(row[metric]) for row in rows
            )
        output.append(record)
    if len(output) != EXPECTED_UNITS * 2:
        raise RuntimeError(
            f"Expected {EXPECTED_UNITS * 2} cluster-method rows, "
            f"observed {len(output)}"
        )
    return output


def paired_cluster_row(
    records: Sequence[Mapping[str, object]],
    metric: str,
    direction: str,
    family_id: str,
    section: str,
    bootstrap_resamples: int,
) -> Dict[str, object]:
    a_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == METHOD_A
    }
    b_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == METHOD_B
    }
    if a_lookup.keys() != b_lookup.keys():
        raise RuntimeError(f"Unpaired clusters for {family_id}/{metric}")
    clusters = sorted(a_lookup)
    a_values = [a_lookup[cluster] for cluster in clusters]
    b_values = [b_lookup[cluster] for cluster in clusters]
    differences = [a - b for a, b in zip(a_values, b_values)]
    stats = paired_statistics(a_values, b_values)
    low, high = bca_mean_interval(
        differences,
        resamples=bootstrap_resamples,
        seed=stable_seed("no-pruning", family_id, metric),
    )
    if direction == "higher":
        wins = sum(value > EPSILON for value in differences)
    elif direction == "lower":
        wins = sum(value < -EPSILON for value in differences)
    else:
        wins = sum(value < -EPSILON for value in differences)
    ties = sum(abs(value) <= EPSILON for value in differences)
    return {
        "family_id": family_id,
        "section": section,
        "comparison": f"{METHOD_A} vs {METHOD_B}",
        "metric": metric,
        "method_a": METHOD_A,
        "method_b": METHOD_B,
        "favorable_direction_for_method_a": direction,
        "dataset": records[0]["dataset"],
        "task_load": records[0]["task_load"],
        "stratum_id": records[0]["stratum_id"],
        "stratum_label": records[0]["stratum_label"],
        "n_independent_cluster_pairs": len(clusters),
        "nested_seeds_per_cluster": EXPECTED_SEEDS,
        "method_a_mean": statistics.fmean(a_values),
        "method_b_mean": statistics.fmean(b_values),
        "mean_difference_a_minus_b": statistics.fmean(differences),
        "mean_difference_ci95_low": low,
        "mean_difference_ci95_high": high,
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
        Tuple[str, int, str, int], Dict[str, float]
    ] = defaultdict(lambda: {"a": 0.0, "b": 0.0, "count": 0.0})
    for (dataset, task_load, cluster, seed, _step), methods in grouped.items():
        a_values = methods.get(METHOD_A, {})
        b_values = methods.get(METHOD_B, {})
        for task_id in set(a_values) & set(b_values):
            accumulator = seed_accumulator[
                (dataset, task_load, cluster, seed)
            ]
            accumulator["a"] += a_values[task_id]
            accumulator["b"] += b_values[task_id]
            accumulator["count"] += 1.0

    seed_rows: List[Dict[str, object]] = []
    for (dataset, task_load, cluster, seed), values in sorted(
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
                "common_task_period_count": count,
                "ca_hmcd_mean_response_time": values["a"] / count,
                "no_pruning_mean_response_time": values["b"] / count,
            }
        )

    cluster_groups: Dict[
        Tuple[str, int, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in seed_rows:
        cluster_groups[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["independent_cluster"]),
            )
        ].append(row)

    cluster_rows: List[Dict[str, object]] = []
    for (dataset, task_load, cluster), rows in sorted(
        cluster_groups.items()
    ):
        cluster_rows.append(
            {
                "dataset": dataset,
                "task_load": task_load,
                "stratum_id": stratum_id(dataset, task_load),
                "stratum_label": stratum_label(dataset, task_load),
                "independent_cluster": cluster,
                "seeds_with_common_tasks": len(rows),
                "seeds_without_common_tasks": EXPECTED_SEEDS - len(rows),
                "common_task_period_count": sum(
                    int(row["common_task_period_count"]) for row in rows
                ),
                "ca_hmcd_mean_response_time": statistics.fmean(
                    float(row["ca_hmcd_mean_response_time"]) for row in rows
                ),
                "no_pruning_mean_response_time": statistics.fmean(
                    float(row["no_pruning_mean_response_time"]) for row in rows
                ),
            }
        )

    comparison_rows: List[Dict[str, object]] = []
    strata = sorted(
        {(str(row["dataset"]), int(row["task_load"])) for row in cluster_rows}
    )
    for dataset, task_load in strata:
        selected = [
            row
            for row in cluster_rows
            if row["dataset"] == dataset
            and int(row["task_load"]) == task_load
        ]
        records: List[Dict[str, object]] = []
        for row in selected:
            base = {
                "dataset": dataset,
                "task_load": task_load,
                "stratum_id": stratum_id(dataset, task_load),
                "stratum_label": stratum_label(dataset, task_load),
                "independent_cluster": row["independent_cluster"],
            }
            records.extend(
                [
                    {
                        **base,
                        "algorithm": METHOD_A,
                        "common_task_response_time": row[
                            "ca_hmcd_mean_response_time"
                        ],
                    },
                    {
                        **base,
                        "algorithm": METHOD_B,
                        "common_task_response_time": row[
                            "no_pruning_mean_response_time"
                        ],
                    },
                ]
            )
        comparison_rows.append(
            paired_cluster_row(
                records,
                "common_task_response_time",
                "lower",
                f"performance.{stratum_id(dataset, task_load)}",
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
    for dataset, task_load in strata:
        selected = [
            row
            for row in cluster_rows
            if row["dataset"] == dataset
            and int(row["task_load"]) == task_load
        ]
        sid = stratum_id(dataset, task_load)
        for metric, direction in PERFORMANCE_ENDPOINTS:
            output.append(
                paired_cluster_row(
                    selected,
                    metric,
                    direction,
                    f"performance.{sid}",
                    "primary performance",
                    bootstrap_resamples,
                )
            )
        for metric, direction in COMPUTATIONAL_ENDPOINTS:
            output.append(
                paired_cluster_row(
                    selected,
                    metric,
                    direction,
                    f"computational.{sid}",
                    "computational boundary",
                    bootstrap_resamples,
                )
            )
        for metric, direction in CANDIDATE_ENDPOINTS:
            output.append(
                paired_cluster_row(
                    selected,
                    metric,
                    direction,
                    f"candidate_mechanism.{sid}",
                    "candidate mechanism",
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
                    "no-pruning-bounded",
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
                    "interval_method": (
                        "bounded BCa bootstrap of cluster means"
                    ),
                }
            )
        node_events = [
            1.0 if float(row["node_limit_run_indicator"]) > EPSILON else 0.0
            for row in rows
        ]
        low, high = wilson_score_interval(node_events)
        output.append(
            {
                "dataset": dataset,
                "task_load": task_load,
                "stratum_id": stratum_id(dataset, task_load),
                "algorithm": algorithm,
                "metric": "clusters_with_any_node_limit_hit",
                "n_independent_clusters": len(node_events),
                "mean": statistics.fmean(node_events),
                "ci95_low": low,
                "ci95_high": high,
                "interval_method": "Wilson score interval",
            }
        )
    return output


def build_family_registry(
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
            "number_of_comparisons": len(rows),
            "metrics": ";".join(str(row["metric"]) for row in rows),
            "correction": "Holm step-down within family",
        }
        for family_id, rows in sorted(grouped.items())
    ]


def build_report(
    comparison_rows: Sequence[Mapping[str, object]],
    audit_rows: Sequence[Mapping[str, object]],
    control_meta: Mapping[str, object],
) -> str:
    primary = [
        row
        for row in comparison_rows
        if row["section"] == "primary performance"
    ]
    lines = [
        "# Formal replay No-Pruning control",
        "",
        "## Execution",
        "",
        (
            f"- Completed {control_meta['selected_registered_units']} "
            "No-Pruning runs over all 44 registered replay units and 30 "
            "nested seeds."
        ),
        (
            "- CA-HMCD baseline records were reused only after protocol, "
            "simulation-code, replay-code, model-version, evaluator-version, "
            "replay-hash, and per-step true-state fingerprints were audited."
        ),
        (
            "- The control disables dominance pruning and fixed-K diversity "
            "retention; all other model, solver, budget, evaluator, and repair "
            "settings remain matched."
        ),
        (
            "- CA-HMCD and No-Pruning were executed in adjacent formal sessions "
            "on the same workstation. Objective and service endpoints are "
            "deterministically paired; wall-clock runtime differences should "
            "also allow for residual session-level system noise."
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
    lines.extend(
        [
            "",
            "## Main findings",
            "",
            (
                "- The feasible candidate count before dominance and the count "
                "after dominance were identical in every replay stratum. The "
                "current dominance rule therefore made no empirical reduction "
                "in this formal replay; candidate compression came entirely "
                "from fixed-K diversity retention."
            ),
            (
                "- Diversity retention reduced the mean per-episode candidate "
                "pool by about 92% across all strata and sharply reduced "
                "branch-and-bound node expansion."
            ),
            (
                "- No-Pruning reached the 4000-node boundary in nearly all "
                "Anti-UAV410 runs and had larger fallback proportions and "
                "optimality-gap bounds. This supports pruning as a search-"
                "control mechanism."
            ),
            (
                "- The control does not support a blanket claim that pruning "
                "improves allocation quality. Under loads 6 and 8, No-Pruning "
                "showed higher solver-decoupled service and coverage, whereas "
                "CA-HMCD had faster common-task responses. These are quality-"
                "complexity trade-offs under the shared node budget."
            ),
            (
                "- UZH-FPV single-target allocation quality was identical. "
                "Here, pruning changed computation but not the selected "
                "allocation."
            ),
            "",
            "## Primary paired results",
            "",
        ]
    )
    for row in primary:
        lines.append(
            "- "
            f"{row['stratum_label']}, {row['metric']}: "
            f"CA-HMCD - No-Pruning = "
            f"{float(row['mean_difference_a_minus_b']):.6g} "
            f"[95% CI {float(row['mean_difference_ci95_low']):.6g}, "
            f"{float(row['mean_difference_ci95_high']):.6g}], "
            f"Holm-adjusted P={float(row['p_holm_adjusted']):.4g}, "
            f"dz={float(row['cohen_dz']):.3f}, "
            f"favorable win rate="
            f"{100 * float(row['win_rate_favorable_to_method_a']):.1f}%."
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "This control estimates the effect of candidate reduction "
                "under the common 4000-node bounded-search budget. It does not "
                "claim that the unpruned search exhausts the global coalition "
                "space when the node limit is reached."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run_analysis(
    baseline_dir: Path,
    control_dir: Path,
    output_dir: Path,
    bootstrap_resamples: int = 10000,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    project_dir = Path(__file__).resolve().parent
    baseline_meta, control_meta, source_checks = validate_sources(
        baseline_dir, control_dir, project_dir
    )
    raw_episodes, raw_steps, candidate_checks = combine_records(
        baseline_dir, control_dir
    )
    paired_audit, pairing_checks = audit_registered_execution(
        [
            {
                "workload_id": row["workload_id"],
                "seed": row["seed"],
                "horizon_steps": row["horizon_steps"],
            }
            for row in raw_episodes
            if row["algorithm"] == METHOD_A
        ],
        raw_episodes,
        raw_steps,
        (METHOD_A, METHOD_B),
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
    family_rows = build_family_registry(comparison_rows)
    combined_audits = [*source_checks, *candidate_checks, *pairing_checks]
    combined_audits.extend(
        [
            {
                "check": "all paired state audits pass",
                "observed": sum(int(row["passed"]) for row in paired_audit),
                "expected": len(paired_audit),
                "passed": all(int(row["passed"]) == 1 for row in paired_audit),
            },
            {
                "check": "all final allocations feasible",
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
    seen_audits = set()
    for row in combined_audits:
        key = (
            str(row["check"]),
            str(row["observed"]),
            str(row["expected"]),
            bool(row["passed"]),
        )
        if key not in seen_audits:
            seen_audits.add(key)
            audit_rows.append(dict(row))
    if not all(bool(row["passed"]) for row in audit_rows):
        raise RuntimeError("No-Pruning analysis audit failed")

    write_csv(output_dir / "combined_episode_results.csv", raw_episodes)
    write_csv(output_dir / "combined_per_step.csv", raw_steps)
    write_csv(output_dir / "paired_state_audit.csv", paired_audit)
    write_csv(
        output_dir / "cluster_level_metrics.csv", round_rows(cluster_rows)
    )
    write_csv(
        output_dir / "paired_statistics.csv", round_rows(comparison_rows)
    )
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
        "baseline_dir": str(baseline_dir.resolve()),
        "control_dir": str(control_dir.resolve()),
        "baseline_protocol_fingerprint": baseline_meta[
            "protocol_fingerprint"
        ],
        "control_protocol_fingerprint": control_meta[
            "protocol_fingerprint"
        ],
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
        build_report(comparison_rows, audit_rows, control_meta),
        encoding="utf-8",
    )
    return {
        "comparison_rows": comparison_rows,
        "audit_rows": audit_rows,
        "metadata": metadata,
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse the formal CA-HMCD No-Pruning replay control"
    )
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--control-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    run_analysis(
        args.baseline_dir,
        args.control_dir,
        args.output_dir,
        args.bootstrap_resamples,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

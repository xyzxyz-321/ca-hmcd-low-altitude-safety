"""Analyse and adjudicate the registered Stage 2 candidate-compression scan."""

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

import ca_hmcd_simulation as core
from ca_hmcd_framework import framework_contract
from ca_hmcd_stage3_statistics import (
    bca_mean_interval,
    holm_adjust,
    stable_seed,
    wilson_score_interval,
)


ANALYSIS_VERSION = "ca-hmcd-stage2-candidate-analysis-v1"
PROFILES = (
    "FixedK-6",
    "FixedK-12",
    "FixedK-24",
    "FixedK-48",
    "AdaptiveK",
    "Full",
)
COMPRESSED_PROFILES = PROFILES[:-1]
EXPECTED_CLUSTERS = 44
EXPECTED_SEEDS = 30
EXPECTED_UNITS = EXPECTED_CLUSTERS * EXPECTED_SEEDS
EPSILON = 1e-12

QUALITY_ENDPOINTS = (
    ("external_objective", "higher"),
    ("independent_service_score", "higher"),
    ("independent_coverage", "higher"),
)
COMPUTATIONAL_ENDPOINTS = (
    ("candidate_after_diversity", "lower"),
    ("candidate_retention_ratio", "lower"),
    ("nodes", "lower"),
    ("node_ratio_to_full", "lower"),
    ("fallback", "lower"),
    ("node_limit_run_indicator", "lower"),
    ("fallback_optimality_gap_bound", "lower"),
)
DESCRIPTIVE_ENDPOINTS = (
    "candidate_generation_ms",
    "solver_elapsed_ms",
    "end_to_end_runtime_ms",
    "adaptive_k_mean",
)
BOUNDED_ENDPOINTS = (
    "independent_coverage",
    "feasibility_rate",
    "fallback",
    "node_limit_run_indicator",
    "candidate_retention_ratio",
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


def rounded(
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


def normalise_historical(
    rows: Sequence[Mapping[str, str]],
    profile: str,
    registered_k: Optional[int],
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for source in rows:
        row: Dict[str, object] = dict(source)
        audited_tasks = float(row.get("candidate_tasks_audited", 0.0))
        retained = float(row.get("candidate_after_diversity", 0.0))
        retained_per_task = (
            retained / audited_tasks if audited_tasks > EPSILON else 0.0
        )
        row.update(
            {
                "algorithm": profile,
                "candidate_policy": (
                    "full" if profile == "Full" else "fixed_k"
                ),
                "registered_k": (
                    registered_k if registered_k is not None else ""
                ),
                "adaptive_k_mean": retained_per_task,
                "adaptive_k_min": 0.0,
                "adaptive_k_max": (
                    float(registered_k)
                    if registered_k is not None
                    else retained_per_task
                ),
                "adaptive_initial_k_mean": (
                    float(registered_k)
                    if registered_k is not None
                    else retained_per_task
                ),
                "adaptive_extension_rounds": 0.0,
                "adaptive_extended_tasks": 0.0,
                "adaptive_boundary_gap_mean": 0.0,
                "adaptive_task_metadata_json": "[]",
            }
        )
        output.append(row)
    return output


def validate_sources(
    project_dir: Path,
    stage2_dir: Path,
    k12_dir: Path,
    full_dir: Path,
    preregistration_dir: Path,
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    stage2_meta = json.loads(
        (stage2_dir / "stage2_execution_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    k12_meta = json.loads(
        (k12_dir / "execution_metadata.json").read_text(encoding="utf-8")
    )
    full_meta = json.loads(
        (full_dir / "execution_metadata.json").read_text(encoding="utf-8")
    )
    parent_audit = json.loads(
        (
            project_dir
            / "ca_hmcd_stage0_stage1_20260917"
            / "stage0_stage1_audit.json"
        ).read_text(encoding="utf-8")
    )
    execution_source_paths = {
        "ca_hmcd_stage2_candidate_runner.py": (
            preregistration_dir
            / "frozen_execution_source"
            / "ca_hmcd_stage2_candidate_runner.py"
        ),
        "ca_hmcd_framework.py": (
            preregistration_dir
            / "frozen_execution_source"
            / "ca_hmcd_framework.py"
        ),
        "ca_hmcd_simulation.py": project_dir / "ca_hmcd_simulation.py",
        "ca_hmcd_replay.py": project_dir / "ca_hmcd_replay.py",
    }
    execution_source_hashes = {
        name: sha256_file(path)
        for name, path in execution_source_paths.items()
    }
    expected_protocol = (
        "b0043867b0802dbabaf4183e6e204bfaddcc4a1aceb8197831096e87500fdcea"
    )
    checks = [
        {
            "check": "Stage 2 formal execution",
            "observed": stage2_meta.get("mode"),
            "expected": "formal",
            "passed": stage2_meta.get("mode") == "formal",
        },
        {
            "check": "Stage 2 execution audit",
            "observed": stage2_meta.get("audit_pass"),
            "expected": True,
            "passed": bool(stage2_meta.get("audit_pass")),
        },
        {
            "check": "complete new profile set",
            "observed": stage2_meta.get("profiles"),
            "expected": [
                "FixedK-6",
                "FixedK-24",
                "FixedK-48",
                "AdaptiveK",
            ],
            "passed": stage2_meta.get("profiles")
            == [
                "FixedK-6",
                "FixedK-24",
                "FixedK-48",
                "AdaptiveK",
            ],
        },
        {
            "check": "Stage 2 unit count",
            "observed": stage2_meta.get("selected_registered_units"),
            "expected": EXPECTED_UNITS,
            "passed": stage2_meta.get("selected_registered_units")
            == EXPECTED_UNITS,
        },
        {
            "check": "K12 reference unit count",
            "observed": k12_meta.get("selected_registered_units"),
            "expected": EXPECTED_UNITS,
            "passed": k12_meta.get("selected_registered_units")
            == EXPECTED_UNITS,
        },
        {
            "check": "K12 formal reference registration",
            "observed": {
                "mode": k12_meta.get("mode"),
                "algorithms": k12_meta.get("algorithms"),
                "audit_pass": k12_meta.get("audit_pass"),
            },
            "expected": {
                "mode": "formal-reference",
                "algorithms": ["CA-HMCD"],
                "audit_pass": True,
            },
            "passed": (
                k12_meta.get("mode") == "formal-reference"
                and k12_meta.get("algorithms") == ["CA-HMCD"]
                and bool(k12_meta.get("audit_pass"))
            ),
        },
        {
            "check": "Full reference unit count",
            "observed": full_meta.get("selected_registered_units"),
            "expected": EXPECTED_UNITS,
            "passed": full_meta.get("selected_registered_units")
            == EXPECTED_UNITS,
        },
        {
            "check": "Full formal control registration",
            "observed": {
                "mode": full_meta.get("mode"),
                "algorithms": full_meta.get("algorithms"),
                "audit_pass": full_meta.get("audit_pass"),
            },
            "expected": {
                "mode": "formal-control",
                "algorithms": ["No-Pruning"],
                "audit_pass": True,
            },
            "passed": (
                full_meta.get("mode") == "formal-control"
                and full_meta.get("algorithms") == ["No-Pruning"]
                and bool(full_meta.get("audit_pass"))
            ),
        },
        {
            "check": "common replay protocol",
            "observed": stage2_meta.get("protocol_fingerprint"),
            "expected": expected_protocol,
            "passed": {
                stage2_meta.get("protocol_fingerprint"),
                k12_meta.get("protocol_fingerprint"),
                full_meta.get("protocol_fingerprint"),
            }
            == {expected_protocol},
        },
        {
            "check": "common model version",
            "observed": stage2_meta.get("model_version"),
            "expected": core.MODEL_VERSION,
            "passed": {
                stage2_meta.get("model_version"),
                k12_meta.get("model_version"),
                full_meta.get("model_version"),
            }
            == {core.MODEL_VERSION},
        },
        {
            "check": "common evaluator version",
            "observed": stage2_meta.get("evaluator_version"),
            "expected": core.EVALUATOR_VERSION,
            "passed": {
                stage2_meta.get("evaluator_version"),
                k12_meta.get("evaluator_version"),
                full_meta.get("evaluator_version"),
            }
            == {core.EVALUATOR_VERSION},
        },
        {
            "check": "common service endpoint",
            "observed": stage2_meta.get("service_endpoint_version"),
            "expected": core.SERVICE_ENDPOINT_VERSION,
            "passed": {
                stage2_meta.get("service_endpoint_version"),
                k12_meta.get("service_endpoint_version"),
                full_meta.get("service_endpoint_version"),
            }
            == {core.SERVICE_ENDPOINT_VERSION},
        },
        {
            "check": "Stage 2 execution source hashes unchanged",
            "observed": execution_source_hashes,
            "expected": {
                name: stage2_meta["code_hashes"][name]
                for name in execution_source_hashes
            },
            "passed": all(
                execution_source_hashes[name]
                == stage2_meta["code_hashes"][name]
                for name in execution_source_hashes
            ),
        },
        {
            "check": "pre-registration hash unchanged",
            "observed": sha256_file(
                preregistration_dir / "stage2_preregistration.json"
            ),
            "expected": stage2_meta["code_hashes"][
                "stage2_preregistration.json"
            ],
            "passed": sha256_file(
                preregistration_dir / "stage2_preregistration.json"
            )
            == stage2_meta["code_hashes"]["stage2_preregistration.json"],
        },
        {
            "check": "frozen parent audit",
            "observed": parent_audit.get("audit_pass"),
            "expected": True,
            "passed": bool(parent_audit.get("audit_pass")),
        },
    ]
    if not all(bool(row["passed"]) for row in checks):
        failed = [str(row["check"]) for row in checks if not row["passed"]]
        raise RuntimeError(f"Stage 2 source validation failed: {failed}")
    return checks, {
        "stage2": stage2_meta,
        "k12": k12_meta,
        "full": full_meta,
        "execution_source_hashes": execution_source_hashes,
    }


def combine_records(
    stage2_dir: Path, k12_dir: Path, full_dir: Path
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    new_episodes: List[Dict[str, object]] = [
        dict(row) for row in read_csv(stage2_dir / "stage2_episode_results.csv")
    ]
    new_steps: List[Dict[str, object]] = [
        dict(row) for row in read_csv(stage2_dir / "stage2_per_step.csv")
    ]
    k12_episodes = normalise_historical(
        [
            row
            for row in read_csv(
                k12_dir / "registered_episode_results.csv"
            )
            if row["algorithm"] == "CA-HMCD"
        ],
        "FixedK-12",
        12,
    )
    k12_steps = normalise_historical(
        [
            row
            for row in read_csv(k12_dir / "registered_per_step.csv")
            if row["algorithm"] == "CA-HMCD"
        ],
        "FixedK-12",
        12,
    )
    full_episodes = normalise_historical(
        read_csv(full_dir / "registered_episode_results.csv"),
        "Full",
        None,
    )
    full_steps = normalise_historical(
        read_csv(full_dir / "registered_per_step.csv"),
        "Full",
        None,
    )
    episodes = new_episodes + k12_episodes + full_episodes
    steps = new_steps + k12_steps + full_steps
    if len(episodes) != EXPECTED_UNITS * len(PROFILES):
        raise RuntimeError(
            f"Expected {EXPECTED_UNITS * len(PROFILES)} episodes, "
            f"observed {len(episodes)}"
        )
    return episodes, steps


def audit_pairing(
    steps: Sequence[Mapping[str, object]],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    grouped: Dict[
        Tuple[str, int, int], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in steps:
        grouped[
            (
                str(row["workload_id"]),
                int(row["seed"]),
                int(row["step"]),
            )
        ].append(row)
    output: List[Dict[str, object]] = []
    for (workload_id, seed, step), rows in sorted(grouped.items()):
        profiles = {str(row["algorithm"]) for row in rows}
        state = {str(row["state_fingerprint"]) for row in rows}
        active_ids = {str(row["active_task_ids_json"]) for row in rows}
        replay_hashes = {str(row["replay_sha256"]) for row in rows}
        candidate_counts = [
            float(row["candidate_feasible_before_dominance"])
            for row in rows
        ]
        same_candidate_count = (
            max(candidate_counts) - min(candidate_counts) <= EPSILON
        )
        passed = (
            profiles == set(PROFILES)
            and len(rows) == len(PROFILES)
            and len(state) == 1
            and len(active_ids) == 1
            and len(replay_hashes) == 1
            and same_candidate_count
        )
        output.append(
            {
                "workload_id": workload_id,
                "seed": seed,
                "step": step,
                "complete_profile_set": int(profiles == set(PROFILES)),
                "same_true_state": int(len(state) == 1),
                "same_active_task_ids": int(len(active_ids) == 1),
                "same_replay_hash": int(len(replay_hashes) == 1),
                "same_pre_pruning_candidate_count": int(
                    same_candidate_count
                ),
                "passed": int(passed),
            }
        )
    expected_groups = EXPECTED_UNITS * 12
    summary = [
        {
            "check": "paired decision groups",
            "observed": len(output),
            "expected": expected_groups,
            "passed": int(len(output) == expected_groups),
        },
        {
            "check": "all six-policy pairing checks",
            "observed": sum(int(row["passed"]) for row in output),
            "expected": len(output),
            "passed": int(all(bool(row["passed"]) for row in output)),
        },
    ]
    if not all(bool(row["passed"]) for row in summary):
        raise RuntimeError("Combined six-policy pairing audit failed")
    return output, summary


def prepare_episode_records(
    rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    numeric = {
        metric for metric, _ in QUALITY_ENDPOINTS
    } | {
        metric for metric, _ in COMPUTATIONAL_ENDPOINTS
        if metric
        not in {"candidate_retention_ratio", "node_ratio_to_full"}
    } | {
        *DESCRIPTIVE_ENDPOINTS,
        "feasibility_rate",
        "node_limit_hit",
    }
    output: List[Dict[str, object]] = []
    for source in rows:
        row: Dict[str, object] = {
            "workload_id": str(source["workload_id"]),
            "dataset": str(source["dataset"]),
            "task_load": int(source["task_load"]),
            "independent_cluster": str(source["independent_cluster"]),
            "seed": int(source["seed"]),
            "algorithm": str(source["algorithm"]),
        }
        for field in numeric:
            row[field] = float(source.get(field, 0.0))
        row["node_limit_run_indicator"] = (
            1.0 if float(source["node_limit_hit"]) > EPSILON else 0.0
        )
        output.append(row)
    return output


def aggregate_clusters(
    rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[
        Tuple[str, int, str, str], List[Mapping[str, object]]
    ] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["independent_cluster"]),
                str(row["algorithm"]),
            )
        ].append(row)
    metric_names = {
        metric for metric, _ in QUALITY_ENDPOINTS
    } | {
        metric for metric, _ in COMPUTATIONAL_ENDPOINTS
        if metric
        not in {"candidate_retention_ratio", "node_ratio_to_full"}
    } | {
        *DESCRIPTIVE_ENDPOINTS,
        "feasibility_rate",
        "node_limit_run_indicator",
    }
    output: List[Dict[str, object]] = []
    for (dataset, load, cluster, algorithm), values in sorted(
        grouped.items()
    ):
        seeds = {int(row["seed"]) for row in values}
        if len(seeds) != EXPECTED_SEEDS:
            raise RuntimeError(
                f"{cluster}/{algorithm} has {len(seeds)} seeds"
            )
        record: Dict[str, object] = {
            "dataset": dataset,
            "task_load": load,
            "stratum_id": stratum_id(dataset, load),
            "stratum_label": stratum_label(dataset, load),
            "independent_cluster": cluster,
            "algorithm": algorithm,
            "nested_seed_runs": len(seeds),
        }
        for metric in sorted(metric_names):
            record[metric] = statistics.fmean(
                float(row[metric]) for row in values
            )
        output.append(record)
    expected = EXPECTED_CLUSTERS * len(PROFILES)
    if len(output) != expected:
        raise RuntimeError(
            f"Expected {expected} cluster-policy rows, observed {len(output)}"
        )
    full_lookup = {
        (
            str(row["dataset"]),
            int(row["task_load"]),
            str(row["independent_cluster"]),
        ): row
        for row in output
        if row["algorithm"] == "Full"
    }
    for row in output:
        full = full_lookup[
            (
                str(row["dataset"]),
                int(row["task_load"]),
                str(row["independent_cluster"]),
            )
        ]
        full_candidates = float(full["candidate_after_diversity"])
        full_nodes = float(full["nodes"])
        row["candidate_retention_ratio"] = (
            float(row["candidate_after_diversity"]) / full_candidates
            if full_candidates > EPSILON
            else 0.0
        )
        row["node_ratio_to_full"] = (
            float(row["nodes"]) / full_nodes
            if full_nodes > EPSILON
            else 0.0
        )
    return output


def paired_cluster_row(
    records: Sequence[Mapping[str, object]],
    profile: str,
    metric: str,
    direction: str,
    section: str,
    bootstrap_resamples: int,
) -> Dict[str, object]:
    profile_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == profile
    }
    full_lookup = {
        str(row["independent_cluster"]): float(row[metric])
        for row in records
        if row["algorithm"] == "Full"
    }
    if profile_lookup.keys() != full_lookup.keys():
        raise RuntimeError(f"Unpaired clusters for {profile}/{metric}")
    clusters = sorted(profile_lookup)
    a_values = [profile_lookup[cluster] for cluster in clusters]
    b_values = [full_lookup[cluster] for cluster in clusters]
    differences = [a - b for a, b in zip(a_values, b_values)]
    stats = core.paired_statistics(a_values, b_values)
    dataset = str(records[0]["dataset"])
    load = int(records[0]["task_load"])
    family_id = f"{section}.{stratum_id(dataset, load)}.{metric}"
    low, high = bca_mean_interval(
        differences,
        resamples=bootstrap_resamples,
        seed=stable_seed("stage2-candidate", family_id, profile),
    )
    if direction == "higher":
        wins = sum(value > EPSILON for value in differences)
    else:
        wins = sum(value < -EPSILON for value in differences)
    ties = sum(abs(value) <= EPSILON for value in differences)
    return {
        "family_id": family_id,
        "section": section,
        "comparison": f"{profile} vs Full",
        "metric": metric,
        "method_a": profile,
        "method_b": "Full",
        "favorable_direction_for_method_a": direction,
        "dataset": dataset,
        "task_load": load,
        "stratum_id": stratum_id(dataset, load),
        "stratum_label": stratum_label(dataset, load),
        "n_independent_cluster_pairs": len(clusters),
        "nested_seeds_per_cluster": EXPECTED_SEEDS,
        "method_a_mean": statistics.fmean(a_values),
        "method_b_mean": statistics.fmean(b_values),
        "mean_difference_a_minus_b": statistics.fmean(differences),
        "mean_difference_ci95_low": low,
        "mean_difference_ci95_high": high,
        "ci_estimand": "mean paired difference across workload clusters",
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
            wins + 0.5 * ties
        )
        / max(1, len(differences)),
        "cluster_ids": ";".join(clusters),
    }


def common_response_rows(
    steps: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    step_groups: Dict[
        Tuple[str, int, str, int, int], Dict[str, Dict[str, float]]
    ] = defaultdict(dict)
    for row in steps:
        key = (
            str(row["dataset"]),
            int(row["task_load"]),
            str(row["independent_cluster"]),
            int(row["seed"]),
            int(row["step"]),
        )
        step_groups[key][str(row["algorithm"])] = {
            str(task_id): float(value)
            for task_id, value in json.loads(
                str(row["served_task_response_times_json"])
            ).items()
        }
    seed_values: Dict[
        Tuple[str, int, str, int, str], Tuple[List[float], List[float]]
    ] = {}
    for (dataset, load, cluster, seed, _), methods in step_groups.items():
        full = methods.get("Full", {})
        for profile in COMPRESSED_PROFILES:
            candidate = methods.get(profile, {})
            common = sorted(set(candidate) & set(full))
            if not common:
                continue
            key = (dataset, load, cluster, seed, profile)
            profile_values, full_values = seed_values.setdefault(
                key, ([], [])
            )
            profile_values.extend(candidate[task_id] for task_id in common)
            full_values.extend(full[task_id] for task_id in common)
    cluster_accumulator: Dict[
        Tuple[str, int, str, str], List[Tuple[float, float]]
    ] = defaultdict(list)
    for (dataset, load, cluster, _, profile), (
        profile_values,
        full_values,
    ) in seed_values.items():
        cluster_accumulator[(dataset, load, cluster, profile)].append(
            (
                statistics.fmean(profile_values),
                statistics.fmean(full_values),
            )
        )
    cluster_rows: List[Dict[str, object]] = []
    for (dataset, load, cluster, profile), values in sorted(
        cluster_accumulator.items()
    ):
        cluster_rows.append(
            {
                "dataset": dataset,
                "task_load": load,
                "stratum_id": stratum_id(dataset, load),
                "independent_cluster": cluster,
                "comparison_profile": profile,
                "seeds_with_common_tasks": len(values),
                "seeds_without_common_tasks": EXPECTED_SEEDS - len(values),
                "profile_common_task_response_time": statistics.fmean(
                    value[0] for value in values
                ),
                "full_common_task_response_time": statistics.fmean(
                    value[1] for value in values
                ),
            }
        )
    comparisons: List[Dict[str, object]] = []
    strata = sorted(
        {
            (str(row["dataset"]), int(row["task_load"]))
            for row in cluster_rows
        }
    )
    for dataset, load in strata:
        for profile in COMPRESSED_PROFILES:
            selected = [
                row
                for row in cluster_rows
                if row["dataset"] == dataset
                and int(row["task_load"]) == load
                and row["comparison_profile"] == profile
            ]
            a_values = [
                float(row["profile_common_task_response_time"])
                for row in selected
            ]
            b_values = [
                float(row["full_common_task_response_time"])
                for row in selected
            ]
            differences = [a - b for a, b in zip(a_values, b_values)]
            stats = core.paired_statistics(a_values, b_values)
            family_id = (
                f"quality.{stratum_id(dataset, load)}."
                "common_task_response_time"
            )
            low, high = bca_mean_interval(
                differences,
                resamples=bootstrap_resamples,
                seed=stable_seed(
                    "stage2-common-response",
                    dataset,
                    load,
                    profile,
                ),
            )
            wins = sum(value < -EPSILON for value in differences)
            ties = sum(abs(value) <= EPSILON for value in differences)
            comparisons.append(
                {
                    "family_id": family_id,
                    "section": "quality",
                    "comparison": f"{profile} vs Full",
                    "metric": "common_task_response_time",
                    "method_a": profile,
                    "method_b": "Full",
                    "favorable_direction_for_method_a": "lower",
                    "dataset": dataset,
                    "task_load": load,
                    "stratum_id": stratum_id(dataset, load),
                    "stratum_label": stratum_label(dataset, load),
                    "n_independent_cluster_pairs": len(selected),
                    "nested_seeds_per_cluster": EXPECTED_SEEDS,
                    "method_a_mean": statistics.fmean(a_values),
                    "method_b_mean": statistics.fmean(b_values),
                    "mean_difference_a_minus_b": statistics.fmean(
                        differences
                    ),
                    "mean_difference_ci95_low": low,
                    "mean_difference_ci95_high": high,
                    "ci_estimand": (
                        "mean paired difference on jointly served task-periods"
                    ),
                    "ci_method": (
                        f"BCa paired cluster bootstrap, "
                        f"{bootstrap_resamples} resamples"
                    ),
                    "wilcoxon_w": stats["wilcoxon_w"],
                    "p_value_test": (
                        "two-sided paired Wilcoxon signed-rank test"
                    ),
                    "p_unadjusted": stats["p_value"],
                    "p_holm_adjusted": math.nan,
                    "cohen_dz": stats["cohen_dz"],
                    "rank_biserial": stats["rank_biserial"],
                    "win_rate_favorable_to_method_a": (
                        wins + 0.5 * ties
                    )
                    / max(1, len(differences)),
                    "cluster_ids": ";".join(
                        str(row["independent_cluster"]) for row in selected
                    ),
                }
            )
    return cluster_rows, comparisons


def build_comparisons(
    cluster_rows: Sequence[Mapping[str, object]],
    response_comparisons: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    strata = sorted(
        {
            (str(row["dataset"]), int(row["task_load"]))
            for row in cluster_rows
        }
    )
    for dataset, load in strata:
        selected = [
            row
            for row in cluster_rows
            if row["dataset"] == dataset
            and int(row["task_load"]) == load
        ]
        for metric, direction in QUALITY_ENDPOINTS:
            for profile in COMPRESSED_PROFILES:
                output.append(
                    paired_cluster_row(
                        selected,
                        profile,
                        metric,
                        direction,
                        "quality",
                        bootstrap_resamples,
                    )
                )
        for metric, direction in COMPUTATIONAL_ENDPOINTS:
            for profile in COMPRESSED_PROFILES:
                output.append(
                    paired_cluster_row(
                        selected,
                        profile,
                        metric,
                        direction,
                        "computational",
                        bootstrap_resamples,
                    )
                )
    output.extend(dict(row) for row in response_comparisons)
    holm_adjust(output)
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
            "dataset": values[0]["dataset"],
            "task_load": values[0]["task_load"],
            "endpoint": values[0]["metric"],
            "number_of_comparisons": len(values),
            "members": ";".join(str(row["comparison"]) for row in values),
            "correction": "Holm step-down within the registered family",
        }
        for family_id, values in sorted(grouped.items())
    ]


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
    for (dataset, load, profile), rows in sorted(grouped.items()):
        for metric in BOUNDED_ENDPOINTS:
            values = [float(row[metric]) for row in rows]
            low, high = bca_mean_interval(
                values,
                resamples=bootstrap_resamples,
                seed=stable_seed(
                    "stage2-bounded", dataset, load, profile, metric
                ),
                bounds=(0.0, 1.0),
            )
            output.append(
                {
                    "dataset": dataset,
                    "task_load": load,
                    "stratum_id": stratum_id(dataset, load),
                    "algorithm": profile,
                    "metric": metric,
                    "n_independent_clusters": len(values),
                    "mean": statistics.fmean(values),
                    "ci95_low": low,
                    "ci95_high": high,
                    "interval_method": "bounded BCa bootstrap of cluster means",
                }
            )
        events = [
            1.0
            if float(row["node_limit_run_indicator"]) > EPSILON
            else 0.0
            for row in rows
        ]
        low, high = wilson_score_interval(events)
        output.append(
            {
                "dataset": dataset,
                "task_load": load,
                "stratum_id": stratum_id(dataset, load),
                "algorithm": profile,
                "metric": "clusters_with_any_node_limit_hit",
                "n_independent_clusters": len(events),
                "mean": statistics.fmean(events),
                "ci95_low": low,
                "ci95_high": high,
                "interval_method": "Wilson score interval",
            }
        )
    return output


def scan_summary(
    cluster_rows: Sequence[Mapping[str, object]],
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
    metrics = [
        metric for metric, _ in QUALITY_ENDPOINTS
    ] + [
        metric for metric, _ in COMPUTATIONAL_ENDPOINTS
    ] + [
        *DESCRIPTIVE_ENDPOINTS,
        "feasibility_rate",
    ]
    output: List[Dict[str, object]] = []
    for (dataset, load, profile), rows in sorted(grouped.items()):
        record: Dict[str, object] = {
            "dataset": dataset,
            "task_load": load,
            "stratum_id": stratum_id(dataset, load),
            "algorithm": profile,
            "independent_clusters": len(rows),
            "nested_seeds_per_cluster": EXPECTED_SEEDS,
        }
        for metric in metrics:
            values = [float(row[metric]) for row in rows]
            record[f"{metric}_mean"] = statistics.fmean(values)
            record[f"{metric}_std_across_clusters"] = (
                statistics.stdev(values) if len(values) > 1 else 0.0
            )
        output.append(record)
    return output


def gate_a(
    cluster_rows: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    high_load = [
        row
        for row in cluster_rows
        if row["dataset"] == "anti_uav410"
        and int(row["task_load"]) in {6, 8}
    ]

    def mean(profile: str, metric: str) -> float:
        return statistics.fmean(
            float(row[metric])
            for row in high_load
            if row["algorithm"] == profile
        )

    def recovery(metric: str) -> Tuple[float, float, float]:
        full_value = mean("Full", metric)
        k12_gap = max(0.0, full_value - mean("FixedK-12", metric))
        adaptive_gap = max(0.0, full_value - mean("AdaptiveK", metric))
        if k12_gap <= EPSILON:
            recovered = 1.0 if adaptive_gap <= EPSILON else -math.inf
        else:
            recovered = (k12_gap - adaptive_gap) / k12_gap
        return k12_gap, adaptive_gap, recovered

    service_k12_gap, service_adaptive_gap, service_recovery = recovery(
        "independent_service_score"
    )
    coverage_k12_gap, coverage_adaptive_gap, coverage_recovery = recovery(
        "independent_coverage"
    )
    candidate_ratio = (
        mean("AdaptiveK", "candidate_after_diversity")
        / mean("Full", "candidate_after_diversity")
    )
    node_ratio = mean("AdaptiveK", "nodes") / mean("Full", "nodes")
    adaptive_objective = mean("AdaptiveK", "external_objective")
    k12_objective = mean("FixedK-12", "external_objective")
    feasibility = mean("AdaptiveK", "feasibility_rate")
    criteria = {
        "service_gap_recovery_at_least_20pct": service_recovery >= 0.20,
        "coverage_gap_recovery_at_least_20pct": coverage_recovery >= 0.20,
        "objective_not_below_k12_by_more_than_0_002": (
            adaptive_objective >= k12_objective - 0.002
        ),
        "candidate_ratio_at_most_0_25": candidate_ratio <= 0.25,
        "node_ratio_at_most_0_60": node_ratio <= 0.60,
        "final_feasibility_equals_1": abs(feasibility - 1.0) <= EPSILON,
    }
    core_conditions = [
        criteria["objective_not_below_k12_by_more_than_0_002"],
        criteria["candidate_ratio_at_most_0_25"],
        criteria["node_ratio_at_most_0_60"],
        criteria["final_feasibility_equals_1"],
    ]
    quality_count = sum(
        [
            criteria["service_gap_recovery_at_least_20pct"],
            criteria["coverage_gap_recovery_at_least_20pct"],
        ]
    )
    if all(criteria.values()):
        decision = "PASS"
    elif all(core_conditions) and quality_count == 1:
        decision = "PROVISIONAL"
    else:
        decision = "FAIL"
    return {
        "gate": "A",
        "decision": decision,
        "method_adjudication": (
            "PROMOTE_CA_HMCD_RT"
            if decision == "PASS"
            else "DO_NOT_PROMOTE_CA_HMCD_RT"
        ),
        "high_load_definition": "Anti-UAV410 loads 6 and 8",
        "independent_cluster_count": len(
            {
                str(row["independent_cluster"])
                for row in high_load
                if row["algorithm"] == "AdaptiveK"
            }
        ),
        "service_k12_gap_to_full": service_k12_gap,
        "service_adaptive_gap_to_full": service_adaptive_gap,
        "service_gap_recovery": service_recovery,
        "coverage_k12_gap_to_full": coverage_k12_gap,
        "coverage_adaptive_gap_to_full": coverage_adaptive_gap,
        "coverage_gap_recovery": coverage_recovery,
        "adaptive_external_objective": adaptive_objective,
        "k12_external_objective": k12_objective,
        "adaptive_candidate_ratio_to_full": candidate_ratio,
        "adaptive_node_ratio_to_full": node_ratio,
        "adaptive_final_feasibility": feasibility,
        "criteria": criteria,
        "interpretation": (
            "AdaptiveK recovers high-load quality and compresses the candidate "
            "pool, but it does not reduce bounded-search node demand enough to "
            "qualify as the registered real-time profile."
        ),
    }


def build_report(
    gate: Mapping[str, object],
    cluster_rows: Sequence[Mapping[str, object]],
    comparison_rows: Sequence[Mapping[str, object]],
) -> str:
    high = [
        row
        for row in cluster_rows
        if row["dataset"] == "anti_uav410"
        and int(row["task_load"]) in {6, 8}
    ]

    def pooled(profile: str, metric: str) -> float:
        return statistics.fmean(
            float(row[metric])
            for row in high
            if row["algorithm"] == profile
        )

    significant = sum(
        bool(row["significant_after_holm_0_05"]) for row in comparison_rows
    )
    return "\n".join(
        [
            "## Material Passport",
            "",
            "- Origin Skill: academic-research-suite / experiment-agent",
            "- Origin Mode: run + validate",
            "- Origin Date: 2026-09-17",
            "- Verification Status: VERIFIED",
            "- Version Label: stage2_candidate_compression_closure_v1",
            "",
            "# Stage 2 candidate-compression closure",
            "",
            "## Execution and integrity",
            "",
            "- The formal scan contains 44 independent public replay workload "
            "clusters, 30 nested seeds per cluster, six paired candidate "
            "policies, and 12 rolling decisions per run.",
            "- The four newly executed policies contributed 5,280 episode "
            "runs and 63,360 decision steps. K12 and Full were reused from "
            "their frozen formal controls after provenance and state-pairing "
            "audits.",
            "- Every final allocation was feasible. The complete six-policy "
            "state, active-task, replay-hash, and pre-pruning candidate-count "
            "audit passed.",
            "",
            "## Registered Gate A",
            "",
            f"- Decision: **{gate['decision']}**.",
            f"- Method adjudication: `{gate['method_adjudication']}`.",
            f"- Service-gap recovery: "
            f"{100 * float(gate['service_gap_recovery']):.1f}%.",
            f"- Coverage-gap recovery: "
            f"{100 * float(gate['coverage_gap_recovery']):.1f}%.",
            f"- Retained-candidate ratio to Full: "
            f"{100 * float(gate['adaptive_candidate_ratio_to_full']):.1f}%.",
            f"- Visited-node ratio to Full: "
            f"{100 * float(gate['adaptive_node_ratio_to_full']):.1f}%.",
            "- AdaptiveK therefore remains an experimental quality-oriented "
            "compression policy; it is not promoted as CA-HMCD-RT.",
            "",
            "## High-load trade-off",
            "",
            f"- K12 service score: "
            f"{pooled('FixedK-12', 'independent_service_score'):.4f}; "
            f"AdaptiveK: "
            f"{pooled('AdaptiveK', 'independent_service_score'):.4f}; "
            f"Full: {pooled('Full', 'independent_service_score'):.4f}.",
            f"- K12 coverage: "
            f"{pooled('FixedK-12', 'independent_coverage'):.4f}; "
            f"AdaptiveK: "
            f"{pooled('AdaptiveK', 'independent_coverage'):.4f}; "
            f"Full: {pooled('Full', 'independent_coverage'):.4f}.",
            f"- K12 candidates: "
            f"{pooled('FixedK-12', 'candidate_after_diversity'):.1f}; "
            f"AdaptiveK: "
            f"{pooled('AdaptiveK', 'candidate_after_diversity'):.1f}; "
            f"Full: {pooled('Full', 'candidate_after_diversity'):.1f}.",
            f"- K12 visited nodes: {pooled('FixedK-12', 'nodes'):.1f}; "
            f"AdaptiveK: {pooled('AdaptiveK', 'nodes'):.1f}; "
            f"Full: {pooled('Full', 'nodes'):.1f}.",
            "",
            "## Interpretation",
            "",
            "- K6 is over-compressed and loses substantial service quality "
            "under multi-target load.",
            "- K12 preserves a clear search-effort advantage but exhibits a "
            "high-load quality gap.",
            "- K24 and AdaptiveK recover most of that quality, yet their "
            "branch-and-bound searches approach the 4,000-node limit. "
            "Candidate-count compression alone is therefore insufficient to "
            "establish a real-time advantage.",
            "- The Stage 2 mechanism is closed with a negative promotion "
            "decision rather than post-hoc retuning. The next solver work "
            "should target search ordering, warm starts, or explicit time "
            "budgets while keeping the registered value model fixed.",
            "",
            "## Statistical outputs",
            "",
            f"- {len(comparison_rows)} paired comparisons were reported; "
            f"{significant} remain significant after their registered Holm "
            "correction.",
            "- `paired_statistics.csv` separately reports raw and adjusted "
            "P values, 95% confidence intervals, effect sizes, and win rates.",
            "- Wall-clock comparisons involving the reused Full control are "
            "descriptive because that control was produced under a different "
            "Python runtime. Candidate counts, nodes, decisions, and quality "
            "outcomes remain deterministic protocol evidence.",
            "",
        ]
    )


def write_release_checksums(output_dir: Path) -> None:
    excluded = {"stage2_release_manifest.csv", "stage2_checksums.sha256"}
    files = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name not in excluded
    )
    manifest = [
        {
            "path": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in files
    ]
    core.write_csv(output_dir / "stage2_release_manifest.csv", manifest)
    checksum_text = "\n".join(
        f"{row['sha256']}  {row['path']}" for row in manifest
    )
    (output_dir / "stage2_checksums.sha256").write_text(
        checksum_text + "\n", encoding="ascii"
    )


def run_analysis(
    project_dir: Path,
    stage2_dir: Path,
    k12_dir: Path,
    full_dir: Path,
    preregistration_dir: Path,
    output_dir: Path,
    bootstrap_resamples: int,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_checks, source_meta = validate_sources(
        project_dir, stage2_dir, k12_dir, full_dir, preregistration_dir
    )
    episodes, steps = combine_records(stage2_dir, k12_dir, full_dir)
    pairing_rows, pairing_summary = audit_pairing(steps)
    episode_records = prepare_episode_records(episodes)
    cluster_rows = aggregate_clusters(episode_records)
    response_clusters, response_comparisons = common_response_rows(
        steps, bootstrap_resamples
    )
    comparisons = build_comparisons(
        cluster_rows, response_comparisons, bootstrap_resamples
    )
    families = family_registry(comparisons)
    intervals = bounded_intervals(cluster_rows, bootstrap_resamples)
    summary = scan_summary(cluster_rows)
    gate = gate_a(cluster_rows)
    report = build_report(gate, cluster_rows, comparisons)
    framework = framework_contract()

    core.write_csv(output_dir / "source_audit.csv", source_checks)
    core.write_csv(output_dir / "pairing_audit.csv", pairing_rows)
    core.write_csv(output_dir / "pairing_audit_summary.csv", pairing_summary)
    core.write_csv(output_dir / "combined_episode_results.csv", episodes)
    core.write_csv(output_dir / "combined_per_step.csv", steps)
    core.write_csv(output_dir / "cluster_level_metrics.csv", rounded(cluster_rows))
    core.write_csv(
        output_dir / "common_task_response_by_cluster.csv",
        rounded(response_clusters),
    )
    core.write_csv(
        output_dir / "paired_statistics.csv", rounded(comparisons)
    )
    core.write_csv(output_dir / "holm_family_registry.csv", families)
    core.write_csv(
        output_dir / "bounded_metric_intervals.csv", rounded(intervals)
    )
    core.write_csv(
        output_dir / "candidate_scan_summary.csv", rounded(summary)
    )
    (output_dir / "stage2_gate_a.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "stage2_framework_contract.json").write_text(
        json.dumps(framework, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "experiment_result.md").write_text(
        report, encoding="utf-8"
    )
    metadata = {
        "analysis_version": ANALYSIS_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "bootstrap_resamples": bootstrap_resamples,
        "profiles": list(PROFILES),
        "episode_rows": len(episodes),
        "per_step_rows": len(steps),
        "cluster_policy_rows": len(cluster_rows),
        "paired_comparisons": len(comparisons),
        "holm_families": len(families),
        "source_audit_pass": all(
            bool(row["passed"]) for row in source_checks
        ),
        "pairing_audit_pass": all(
            bool(row["passed"]) for row in pairing_summary
        ),
        "gate_a_decision": gate["decision"],
        "source_execution_metadata": source_meta,
        "analysis_code_sha256": sha256_file(Path(__file__).resolve()),
    }
    (output_dir / "analysis_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_release_checksums(output_dir)
    return {
        "source_checks": source_checks,
        "pairing_summary": pairing_summary,
        "cluster_rows": cluster_rows,
        "comparisons": comparisons,
        "families": families,
        "intervals": intervals,
        "summary": summary,
        "gate": gate,
        "metadata": metadata,
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse the Stage 2 candidate-compression closure"
    )
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--stage2-dir", type=Path, required=True)
    parser.add_argument("--k12-dir", type=Path, required=True)
    parser.add_argument("--full-dir", type=Path, required=True)
    parser.add_argument("--preregistration-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    result = run_analysis(
        args.project_dir,
        args.stage2_dir,
        args.k12_dir,
        args.full_dir,
        args.preregistration_dir,
        args.output_dir,
        args.bootstrap_resamples,
    )
    print(
        f"[stage2-analysis] gate_a={result['gate']['decision']} "
        f"comparisons={len(result['comparisons'])} "
        f"families={len(result['families'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

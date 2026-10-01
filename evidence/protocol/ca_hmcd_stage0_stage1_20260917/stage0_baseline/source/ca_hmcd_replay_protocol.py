"""Build the preregistered public-data replay protocol for CA-HMCD.

The protocol keeps public source identity separate from stochastic algorithm
replication. UZH-FPV trajectories are independent motion-source clusters.
Anti-UAV410 test sequences are assigned exactly once to composed workloads.
Thirty random seeds are nested within each fixed workload and shared by every
algorithm during the later execution phase.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ca_hmcd_replay import (
    ANTI_UAV_ATTRIBUTE_NAMES,
    AntiUavMappingConfig,
    AntiUavTrackSpec,
    BlackbirdMappingConfig,
    REPLAY_MAPPING_VERSION,
    UzhFpvCleaningConfig,
    UzhFpvTrackSpec,
    build_anti_uav_episode,
    build_uzh_fpv_episode,
    read_replay_episode,
    write_replay_episode,
)
from ca_hmcd_simulation import write_csv


PROTOCOL_VERSION = "public-replay-protocol-v1"


@dataclass(frozen=True)
class ReplayProtocolConfig:
    horizon_steps: int = 12
    seeds_per_workload: int = 30
    base_seed: int = 730000
    selection_seed: int = 20260916
    resources_count: int = 10
    top_k: int = 12
    node_limit: int = 4000
    anti_width: int = 640
    anti_height: int = 512
    anti_window_frames: int = 240
    anti_event_padding_frames: int = 30
    anti_workload_plan: Tuple[Tuple[int, int], ...] = (
        (2, 10),
        (4, 8),
        (6, 6),
        (8, 4),
    )
    uzh_max_speed_mps: float = 25.0
    uzh_max_gap_s: float = 2.0
    uzh_max_bridge_points: int = 50
    uzh_motion_threshold_mps: float = 0.20
    uzh_motion_padding_s: float = 1.0


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _as_int(row: Mapping[str, str], key: str) -> int:
    return int(float(row.get(key, "0") or 0))


def _as_float(row: Mapping[str, str], key: str) -> float:
    return float(row.get(key, "0") or 0.0)


def classify_anti_source(row: Mapping[str, str]) -> str:
    if (
        _as_int(row, "visibility_events") > 0
        or _as_int(row, "out_of_view")
        or _as_int(row, "occlusion")
    ):
        return "visibility"
    if _as_int(row, "fast_motion"):
        return "fast_motion"
    if _as_int(row, "scale_variation") or _as_int(row, "tiny_size"):
        return "scale_small"
    return "nominal"


def _anti_window_mode(stratum: str) -> str:
    return {
        "visibility": "visibility",
        "fast_motion": "motion",
        "scale_small": "scale",
        "nominal": "motion",
    }[stratum]


def _uzh_environment(sequence_id: str) -> Tuple[str, str]:
    if sequence_id.startswith("indoor_forward"):
        return "indoor", "forward"
    if sequence_id.startswith("indoor_45"):
        return "indoor", "oblique_45"
    if sequence_id.startswith("outdoor_forward"):
        return "outdoor", "forward"
    if sequence_id.startswith("outdoor_45"):
        return "outdoor", "oblique_45"
    raise ValueError(f"Unknown UZH-FPV sequence family: {sequence_id}")


def _speed_thresholds(rows: Sequence[Mapping[str, str]]) -> Tuple[float, float]:
    values = sorted(_as_float(row, "median_cleaned_speed_mps") for row in rows)
    if len(values) < 3:
        return values[0], values[-1]
    low_index = max(0, len(values) // 3 - 1)
    high_index = max(low_index + 1, 2 * len(values) // 3 - 1)
    return values[low_index], values[high_index]


def _speed_stratum(value: float, thresholds: Tuple[float, float]) -> str:
    if value <= thresholds[0]:
        return "low"
    if value <= thresholds[1]:
        return "medium"
    return "high"


def _offset_pattern(load: int) -> Tuple[int, ...]:
    patterns = {
        1: (0,),
        2: (0, 2),
        4: (0, 1, 2, 3),
        6: (0, 0, 1, 2, 3, 4),
        8: (0, 0, 1, 1, 2, 2, 3, 4),
    }
    if load not in patterns:
        raise ValueError(f"No preregistered arrival pattern for load {load}")
    return patterns[load]


def _expanded_workload_plan(
    plan: Sequence[Tuple[int, int]],
) -> List[Dict[str, object]]:
    workloads: List[Dict[str, object]] = []
    for load, count in plan:
        if load not in (2, 4, 6, 8) or count < 0:
            raise ValueError("Anti-UAV workload plan must use loads 2, 4, 6 or 8")
        for replicate in range(count):
            workloads.append(
                {
                    "workload_id": f"AU-L{load:02d}-W{replicate + 1:02d}",
                    "load": load,
                    "capacity": load,
                    "sources": [],
                    "stratum_counts": {},
                }
            )
    return workloads


def _assign_anti_sources(
    sources: Sequence[Dict[str, object]],
    workloads: List[Dict[str, object]],
    seed: int,
) -> Dict[Tuple[int, str], int]:
    capacity = sum(int(workload["capacity"]) for workload in workloads)
    if len(sources) != capacity:
        raise ValueError(
            f"Anti-UAV workload plan has capacity {capacity}, but "
            f"{len(sources)} evaluation sources were supplied"
        )
    rng = random.Random(seed)
    grouped: Dict[str, List[Dict[str, object]]] = {
        stratum: [] for stratum in ("scale_small", "fast_motion", "visibility", "nominal")
    }
    for source in sources:
        grouped[str(source["stratum"])].append(source)
    for values in grouped.values():
        values.sort(key=lambda row: str(row["sequence_id"]))
        rng.shuffle(values)

    strata = ("scale_small", "fast_motion", "visibility", "nominal")
    workloads_by_load: Dict[int, List[Dict[str, object]]] = {}
    for workload in workloads:
        workloads_by_load.setdefault(int(workload["load"]), []).append(workload)
    load_capacities = {
        load: sum(int(workload["capacity"]) for workload in values)
        for load, values in workloads_by_load.items()
    }

    # Allocate integer stratum quotas to each load group while preserving both
    # the global source totals and every preregistered load capacity.
    quotas: Dict[Tuple[int, str], int] = {}
    row_remaining: Dict[int, int] = {}
    column_remaining = {stratum: len(grouped[stratum]) for stratum in strata}
    fractional_cells: List[Tuple[float, int, str]] = []
    for load, load_capacity in sorted(load_capacities.items()):
        allocated = 0
        for stratum in strata:
            expected = load_capacity * len(grouped[stratum]) / capacity
            base = math.floor(expected)
            quotas[(load, stratum)] = base
            allocated += base
            column_remaining[stratum] -= base
            fractional_cells.append((expected - base, load, stratum))
        row_remaining[load] = load_capacity - allocated

    for _, load, stratum in sorted(
        fractional_cells,
        key=lambda item: (-item[0], item[1], item[2]),
    ):
        if row_remaining[load] and column_remaining[stratum]:
            quotas[(load, stratum)] += 1
            row_remaining[load] -= 1
            column_remaining[stratum] -= 1
    while any(row_remaining.values()):
        candidates = [
            (load, stratum)
            for load in sorted(row_remaining)
            if row_remaining[load]
            for stratum in strata
            if column_remaining[stratum]
        ]
        if not candidates:
            raise RuntimeError("Unable to balance Anti-UAV strata by task load")
        load, stratum = min(
            candidates,
            key=lambda item: (
                quotas[item],
                -column_remaining[item[1]],
                item[0],
                item[1],
            ),
        )
        quotas[(load, stratum)] += 1
        row_remaining[load] -= 1
        column_remaining[stratum] -= 1
    if any(column_remaining.values()):
        raise RuntimeError("Anti-UAV stratum quotas do not preserve source totals")

    for load, load_workloads in sorted(workloads_by_load.items()):
        for stratum in strata:
            quota = quotas[(load, stratum)]
            selected_sources = grouped[stratum][:quota]
            del grouped[stratum][:quota]
            for source in selected_sources:
                candidates = [
                    workload
                    for workload in load_workloads
                    if len(workload["sources"]) < int(workload["capacity"])
                ]
                if not candidates:
                    raise RuntimeError(
                        f"Anti-UAV load-{load} assignment exhausted capacity"
                    )
                selected = min(
                    candidates,
                    key=lambda workload: (
                        int(workload["stratum_counts"].get(stratum, 0)),
                        len(workload["sources"]) / int(workload["capacity"]),
                        str(workload["workload_id"]),
                    ),
                )
                selected["sources"].append(source)
                counts = selected["stratum_counts"]
                counts[stratum] = int(counts.get(stratum, 0)) + 1

    if any(grouped[stratum] for stratum in strata):
        raise RuntimeError("Not all Anti-UAV evaluation sources were assigned")
    for workload in workloads:
        if len(workload["sources"]) != int(workload["capacity"]):
            raise RuntimeError(
                f"Workload {workload['workload_id']} was not filled to capacity"
            )
    return quotas


def _relative_path(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _write_protocol_report(
    output_dir: Path,
    config: ReplayProtocolConfig,
    source_rows: Sequence[Mapping[str, object]],
    workload_rows: Sequence[Mapping[str, object]],
    audit_rows: Sequence[Mapping[str, object]],
) -> None:
    anti_eval = [
        row
        for row in source_rows
        if row["dataset"] == "anti_uav410"
        and row["protocol_split"] == "evaluation"
    ]
    uzh_eval = [
        row for row in source_rows if row["dataset"] == "uzh_fpv"
    ]
    workload_summary: Dict[int, int] = {}
    for row in workload_rows:
        workload_summary[int(row["task_load"])] = (
            workload_summary.get(int(row["task_load"]), 0) + 1
        )
    lines = [
        "# CA-HMCD Public-Data Replay Protocol",
        "",
        "Date: 2026-09-16",
        f"Protocol version: `{PROTOCOL_VERSION}`",
        f"Replay mapping version: `{REPLAY_MAPPING_VERSION}`",
        "",
        "## Registered design",
        "",
        f"- Rolling horizon: {config.horizon_steps} decision periods.",
        f"- Paired random seeds: {config.seeds_per_workload} per fixed workload.",
        f"- Response resources: {config.resources_count} per run.",
        f"- Candidate retention limit: top-{config.top_k}.",
        f"- Solver node budget: {config.node_limit} per decision period.",
        f"- UZH-FPV source clusters: {len(uzh_eval)} measured trajectories.",
        f"- Anti-UAV410 evaluation sources: {len(anti_eval)} official test sequences.",
        "- Anti-UAV410 train is development-only; val is calibration-only; "
        "test is evaluation-only.",
        "- No Anti-UAV410 test source is reused across formal workloads.",
        "- Random seeds are nested within workloads and are not interpreted as "
        "independent physical flights or videos.",
        "",
        "## Workload registry",
        "",
        "| Task load | Workloads | Seeds per workload |",
        "|---:|---:|---:|",
    ]
    lines.extend(
        f"| {load} | {count} | {config.seeds_per_workload} |"
        for load, count in sorted(workload_summary.items())
    )
    lines.extend(
        [
            "",
            "## Independent units",
            "",
            "- UZH-FPV inference is clustered by original flight sequence.",
            "- Anti-UAV410 inference is clustered by preregistered composed workload; "
            "the manifest also retains every parent video identity.",
            "- Algorithms receive the same replay CSV, task arrivals, resources and "
            "seed for each paired comparison.",
            "",
            "## Audit",
            "",
        ]
    )
    lines.extend(
        f"- {'PASS' if row['passed'] else 'FAIL'}: {row['check']} "
        f"(observed={row['observed']}, expected={row['expected']})"
        for row in audit_rows
    )
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "Public data instantiate task-side motion and observation patterns. "
            "Protected-zone registration, multi-sequence concurrency, risk, response "
            "windows, response resources and allocation outcomes remain simulation "
            "constructs. This is a public-data-informed semi-synthetic replay protocol.",
        ]
    )
    (output_dir / "protocol_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def build_replay_protocol(
    uzh_manifest_path: Path,
    anti_manifest_path: Path,
    output_dir: Path,
    config: ReplayProtocolConfig = ReplayProtocolConfig(),
) -> Dict[str, object]:
    if config.horizon_steps < 2:
        raise ValueError("Replay horizon must contain at least two periods")
    if config.seeds_per_workload < 30:
        raise ValueError("Formal replay protocol requires at least 30 paired seeds")
    if config.resources_count < 1:
        raise ValueError("Replay protocol requires at least one response resource")
    if config.top_k < 1 or config.node_limit < 1:
        raise ValueError("Candidate and solver limits must be positive")
    uzh_manifest = _read_csv(uzh_manifest_path)
    anti_manifest = _read_csv(anti_manifest_path)
    if not uzh_manifest or not anti_manifest:
        raise ValueError("Quality manifests must not be empty")

    output_dir.mkdir(parents=True, exist_ok=True)
    replay_root = output_dir / "replays"
    uzh_replay_dir = replay_root / "uzh_fpv"
    anti_replay_dir = replay_root / "anti_uav410"
    uzh_replay_dir.mkdir(parents=True, exist_ok=True)
    anti_replay_dir.mkdir(parents=True, exist_ok=True)

    source_rows: List[Dict[str, object]] = []
    workload_rows: List[Dict[str, object]] = []
    task_rows: List[Dict[str, object]] = []
    scenario_rows: List[Dict[str, object]] = []
    replay_paths: List[Path] = []

    speed_thresholds = _speed_thresholds(uzh_manifest)
    uzh_cleaning = UzhFpvCleaningConfig(
        max_speed_mps=config.uzh_max_speed_mps,
        max_gap_s=config.uzh_max_gap_s,
        max_bridge_points=config.uzh_max_bridge_points,
        motion_speed_threshold_mps=config.uzh_motion_threshold_mps,
        motion_padding_s=config.uzh_motion_padding_s,
    )
    uzh_scenario_counts: Dict[str, int] = {}
    for row in sorted(uzh_manifest, key=lambda item: item["sequence_id"]):
        sequence_id = row["sequence_id"]
        environment, view = _uzh_environment(sequence_id)
        speed = _as_float(row, "median_cleaned_speed_mps")
        speed_stratum = _speed_stratum(speed, speed_thresholds)
        scenario = f"uzh_{environment}_{view}_{speed_stratum}"
        workload_id = f"UZH-{sequence_id}"
        replay_path = uzh_replay_dir / f"{workload_id}.csv"
        source_path = Path(row["source_path"])
        episode = build_uzh_fpv_episode(
            [UzhFpvTrackSpec(source_path, "UZH-01")],
            workload_id,
            step_count=config.horizon_steps,
            mapping=BlackbirdMappingConfig(),
            cleaning=uzh_cleaning,
        )
        write_replay_episode(replay_path, episode)
        replay_paths.append(replay_path)
        source_rows.append(
            {
                "dataset": "uzh_fpv",
                "sequence_id": sequence_id,
                "source_sha256": row["source_sha256"],
                "source_path": row["source_path"],
                "original_split": "public_ground_truth",
                "protocol_split": "external_evaluation",
                "stratum": scenario,
                "median_cleaned_speed_mps": speed,
                "cleaned_duration_s": _as_float(row, "cleaned_duration_s"),
                "cleaned_path_length_m": _as_float(
                    row, "cleaned_path_length_m"
                ),
                "assigned_workload_id": workload_id,
                "used_in_formal_workload": 1,
            }
        )
        workload_rows.append(
            {
                "workload_id": workload_id,
                "dataset": "uzh_fpv",
                "scenario": scenario,
                "task_load": 1,
                "source_sequence_count": 1,
                "horizon_steps": config.horizon_steps,
                "resources_count": config.resources_count,
                "top_k": config.top_k,
                "node_limit": config.node_limit,
                "independent_cluster": sequence_id,
                "replay_path": _relative_path(replay_path, output_dir),
            }
        )
        task_rows.append(
            {
                "workload_id": workload_id,
                "dataset": "uzh_fpv",
                "task_id": "UZH-01",
                "source_sequence_id": sequence_id,
                "source_sha256": row["source_sha256"],
                "source_stratum": scenario,
                "offset_steps": 0,
                "window_mode": "cleaned_full_motion",
                "replay_path": _relative_path(replay_path, output_dir),
            }
        )
        uzh_scenario_counts[scenario] = uzh_scenario_counts.get(scenario, 0) + 1

    for scenario, count in sorted(uzh_scenario_counts.items()):
        scenario_rows.append(
            {
                "scenario": scenario,
                "dataset": "uzh_fpv",
                "source_count": count,
                "task_load": 1,
                "evidence_role": "measured_3d_motion",
            }
        )

    anti_sources: List[Dict[str, object]] = []
    for row in anti_manifest:
        split = row["split"]
        protocol_split = {
            "train": "development",
            "val": "calibration",
            "test": "evaluation",
        }.get(split, "excluded")
        stratum = classify_anti_source(row)
        source = {
            "dataset": "anti_uav410",
            "sequence_id": row["sequence_id"],
            "source_sha256": row["source_sha256"],
            "source_path": row["source_path"],
            "original_split": split,
            "protocol_split": protocol_split,
            "stratum": stratum,
            "assigned_workload_id": "",
            "used_in_formal_workload": 0,
            "raw_frames": _as_int(row, "raw_frames"),
            "active_frames": _as_int(row, "active_frames"),
            "inactive_frames": _as_int(row, "inactive_frames"),
            "active_ratio": _as_float(row, "active_ratio"),
            "visibility_events": _as_int(row, "visibility_events"),
        }
        for attribute_name in ANTI_UAV_ATTRIBUTE_NAMES:
            source[attribute_name] = _as_int(row, attribute_name)
        source_rows.append(source)
        if protocol_split == "evaluation":
            anti_sources.append(source)

    workloads = _expanded_workload_plan(config.anti_workload_plan)
    anti_load_stratum_quotas = _assign_anti_sources(
        anti_sources,
        workloads,
        config.selection_seed,
    )
    anti_source_lookup = {
        str(row["sequence_id"]): row
        for row in source_rows
        if row["dataset"] == "anti_uav410"
    }
    anti_stratum_counts: Dict[str, int] = {}
    anti_load_stratum_counts: Dict[Tuple[int, str], int] = {
        key: 0 for key in anti_load_stratum_quotas
    }
    for workload in workloads:
        workload_id = str(workload["workload_id"])
        load = int(workload["load"])
        sources = sorted(
            workload["sources"],
            key=lambda row: (str(row["stratum"]), str(row["sequence_id"])),
        )
        offsets = _offset_pattern(load)
        tracks: List[AntiUavTrackSpec] = []
        parent_ids: List[str] = []
        for task_index, (source, offset) in enumerate(zip(sources, offsets), start=1):
            sequence_id = str(source["sequence_id"])
            stratum = str(source["stratum"])
            task_id = f"AU-{task_index:02d}"
            tracks.append(
                AntiUavTrackSpec(
                    Path(str(source["source_path"])),
                    task_id,
                    offset_steps=offset,
                    window_mode=_anti_window_mode(stratum),
                    window_frames=config.anti_window_frames,
                    event_padding_frames=config.anti_event_padding_frames,
                )
            )
            parent_ids.append(sequence_id)
            anti_source_lookup[sequence_id]["assigned_workload_id"] = workload_id
            anti_source_lookup[sequence_id]["used_in_formal_workload"] = 1
            task_rows.append(
                {
                    "workload_id": workload_id,
                    "dataset": "anti_uav410",
                    "task_id": task_id,
                    "source_sequence_id": sequence_id,
                    "source_sha256": source["source_sha256"],
                    "source_stratum": stratum,
                    "offset_steps": offset,
                    "window_mode": _anti_window_mode(stratum),
                    "replay_path": (
                        f"replays/anti_uav410/{workload_id}.csv"
                    ),
                }
            )
            anti_stratum_counts[stratum] = anti_stratum_counts.get(stratum, 0) + 1
            anti_load_stratum_counts[(load, stratum)] = (
                anti_load_stratum_counts.get((load, stratum), 0) + 1
            )
        replay_path = anti_replay_dir / f"{workload_id}.csv"
        episode = build_anti_uav_episode(
            tracks,
            workload_id,
            AntiUavMappingConfig(config.anti_width, config.anti_height),
            step_count=config.horizon_steps,
        )
        write_replay_episode(replay_path, episode)
        replay_paths.append(replay_path)
        workload_rows.append(
            {
                "workload_id": workload_id,
                "dataset": "anti_uav410",
                "scenario": f"anti_uav_load_{load}",
                "task_load": load,
                "source_sequence_count": len(parent_ids),
                "horizon_steps": config.horizon_steps,
                "resources_count": config.resources_count,
                "top_k": config.top_k,
                "node_limit": config.node_limit,
                "independent_cluster": workload_id,
                "parent_source_sequence_ids": ";".join(parent_ids),
                "replay_path": _relative_path(replay_path, output_dir),
            }
        )

    for stratum, count in sorted(anti_stratum_counts.items()):
        scenario_rows.append(
            {
                "scenario": f"anti_{stratum}",
                "dataset": "anti_uav410",
                "source_count": count,
                "task_load": "2/4/6/8",
                "evidence_role": "measured_image_plane_observation",
            }
        )
    for load, count in config.anti_workload_plan:
        scenario_rows.append(
            {
                "scenario": f"anti_uav_load_{load}",
                "dataset": "anti_uav410",
                "source_count": load * count,
                "workload_count": count,
                "task_load": load,
                "evidence_role": "semi_synthetic_concurrent_load",
            }
        )

    seed_rows: List[Dict[str, object]] = []
    for workload_index, workload in enumerate(
        sorted(workload_rows, key=lambda row: str(row["workload_id"]))
    ):
        for replicate in range(config.seeds_per_workload):
            seed_rows.append(
                {
                    "workload_id": workload["workload_id"],
                    "dataset": workload["dataset"],
                    "replicate": replicate + 1,
                    "seed": config.base_seed + workload_index * 100 + replicate,
                    "paired_across_algorithms": 1,
                    "independent_cluster": workload["independent_cluster"],
                    "scenario": workload["scenario"],
                    "task_load": workload["task_load"],
                    "horizon_steps": workload["horizon_steps"],
                    "resources_count": workload["resources_count"],
                    "top_k": workload["top_k"],
                    "node_limit": workload["node_limit"],
                    "replay_path": workload["replay_path"],
                }
            )

    replay_validation = [read_replay_episode(path) for path in replay_paths]
    replay_by_key = {
        (episode.dataset, episode.sequence_id): episode
        for episode in replay_validation
    }
    anti_used = [
        row
        for row in source_rows
        if row["dataset"] == "anti_uav410"
        and row["protocol_split"] == "evaluation"
        and int(row["used_in_formal_workload"]) == 1
    ]
    anti_used_ids = [str(row["sequence_id"]) for row in anti_used]
    uzh_used = [
        row
        for row in source_rows
        if row["dataset"] == "uzh_fpv"
        and int(row["used_in_formal_workload"]) == 1
    ]
    workload_seed_counts: Dict[str, int] = {}
    for row in seed_rows:
        workload_id = str(row["workload_id"])
        workload_seed_counts[workload_id] = workload_seed_counts.get(workload_id, 0) + 1

    def audit(
        check: str,
        observed: object,
        expected: object,
        passed: bool,
    ) -> Dict[str, object]:
        return {
            "check": check,
            "observed": observed,
            "expected": expected,
            "passed": int(passed),
        }

    anti_expected = sum(load * count for load, count in config.anti_workload_plan)
    audit_rows = [
        audit("UZH source trajectories assigned once", len(uzh_used), len(uzh_manifest),
              len(uzh_used) == len(uzh_manifest)),
        audit("Anti evaluation sources assigned once", len(anti_used_ids), anti_expected,
              len(anti_used_ids) == anti_expected and len(set(anti_used_ids)) == anti_expected),
        audit("No Anti train/val source in formal workload",
              sum(
                  int(row["used_in_formal_workload"])
                  for row in source_rows
                  if row["dataset"] == "anti_uav410"
                  and row["protocol_split"] != "evaluation"
              ),
              0,
              all(
                  not int(row["used_in_formal_workload"])
                  for row in source_rows
                  if row["dataset"] == "anti_uav410"
                  and row["protocol_split"] != "evaluation"
              )),
        audit("Every workload has preregistered seed count",
              min(workload_seed_counts.values()), config.seeds_per_workload,
              set(workload_seed_counts.values()) == {config.seeds_per_workload}),
        audit("All replay horizons are fixed",
              sorted({episode.step_count for episode in replay_validation}),
              [config.horizon_steps],
              {episode.step_count for episode in replay_validation}
              == {config.horizon_steps}),
        audit("All replay sequence IDs are unique",
              len({(episode.dataset, episode.sequence_id) for episode in replay_validation}),
              len(replay_validation),
              len({(episode.dataset, episode.sequence_id) for episode in replay_validation})
              == len(replay_validation)),
        audit("Minimum paired seeds", config.seeds_per_workload, ">=30",
              config.seeds_per_workload >= 30),
        audit("Response-resource count is fixed",
              sorted({int(row["resources_count"]) for row in workload_rows}),
              [config.resources_count],
              {int(row["resources_count"]) for row in workload_rows}
              == {config.resources_count}),
        audit("Candidate and solver budgets are fixed",
              sorted(
                  {
                      (int(row["top_k"]), int(row["node_limit"]))
                      for row in workload_rows
                  }
              ),
              [(config.top_k, config.node_limit)],
              {
                  (int(row["top_k"]), int(row["node_limit"]))
                  for row in workload_rows
              }
              == {(config.top_k, config.node_limit)}),
        audit("Anti strata are balanced within load groups",
              sorted(anti_load_stratum_counts.items()),
              sorted(anti_load_stratum_quotas.items()),
              anti_load_stratum_counts == anti_load_stratum_quotas),
        audit("Replay task counts match registered loads",
              sum(
                  replay_by_key[
                      (str(row["dataset"]), str(row["workload_id"]))
                  ].task_count
                  == int(row["task_load"])
                  for row in workload_rows
              ),
              len(workload_rows),
              all(
                  replay_by_key[
                      (str(row["dataset"]), str(row["workload_id"]))
                  ].task_count
                  == int(row["task_load"])
                  for row in workload_rows
              )),
        audit("Arrival offsets match preregistered patterns",
              len(task_rows),
              len(task_rows),
              all(
                  tuple(
                      int(task["offset_steps"])
                      for task in task_rows
                      if task["workload_id"] == workload["workload_id"]
                  )
                  == _offset_pattern(int(workload["task_load"]))
                  for workload in workload_rows
              )),
        audit("Run seeds are globally unique",
              len({int(row["seed"]) for row in seed_rows}),
              len(seed_rows),
              len({int(row["seed"]) for row in seed_rows}) == len(seed_rows)),
    ]
    if not all(bool(row["passed"]) for row in audit_rows):
        failed = [str(row["check"]) for row in audit_rows if not row["passed"]]
        raise RuntimeError(f"Replay protocol audit failed: {failed}")

    write_csv(output_dir / "source_registry.csv", source_rows)
    write_csv(output_dir / "scenario_registry.csv", scenario_rows)
    write_csv(output_dir / "workload_registry.csv", workload_rows)
    write_csv(output_dir / "workload_task_manifest.csv", task_rows)
    write_csv(output_dir / "seed_registry.csv", seed_rows)
    write_csv(output_dir / "experiment_run_registry.csv", seed_rows)
    write_csv(output_dir / "protocol_audit.csv", audit_rows)
    (output_dir / "protocol_config.json").write_text(
        json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "replay_mapping_version": REPLAY_MAPPING_VERSION,
                **asdict(config),
                "uzh_speed_thresholds_mps": {
                    "low_upper": speed_thresholds[0],
                    "medium_upper": speed_thresholds[1],
                },
                "anti_load_stratum_quotas": {
                    f"load_{load}:{stratum}": count
                    for (load, stratum), count in sorted(
                        anti_load_stratum_quotas.items()
                    )
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_protocol_report(
        output_dir,
        config,
        source_rows,
        workload_rows,
        audit_rows,
    )
    return {
        "sources": source_rows,
        "scenarios": scenario_rows,
        "workloads": workload_rows,
        "tasks": task_rows,
        "seeds": seed_rows,
        "audit": audit_rows,
        "replay_paths": replay_paths,
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the preregistered CA-HMCD public replay protocol"
    )
    parser.add_argument("--uzh-manifest", type=Path, required=True)
    parser.add_argument("--anti-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--horizon-steps", type=int, default=12)
    parser.add_argument("--seeds-per-workload", type=int, default=30)
    parser.add_argument("--base-seed", type=int, default=730000)
    parser.add_argument("--selection-seed", type=int, default=20260916)
    parser.add_argument("--resources-count", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--node-limit", type=int, default=4000)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    build_replay_protocol(
        args.uzh_manifest,
        args.anti_manifest,
        args.output_dir,
        ReplayProtocolConfig(
            horizon_steps=args.horizon_steps,
            seeds_per_workload=args.seeds_per_workload,
            base_seed=args.base_seed,
            selection_seed=args.selection_seed,
            resources_count=args.resources_count,
            top_k=args.top_k,
            node_limit=args.node_limit,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

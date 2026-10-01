"""Public-trajectory replay adapters for the CA-HMCD experiments.

The adapters deliberately separate measured task-side evidence from simulated
response resources. Blackbird supplies metric 3-D motion, whereas Anti-UAV410
supplies image-plane target observations. Both are converted to the normalized
task vector used by CA-HMCD:

    (scale, speed, spatial_level, density, risk, response_window)

Risk, response windows, protected zones, resource capabilities and response
effects remain explicit simulation assumptions. The resulting experiment is a
public-trajectory-informed semi-synthetic replay, not field validation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import re
import statistics
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ca_hmcd_simulation import (
    ALGORITHMS,
    Task,
    audit_paired_protocol,
    run_episode,
    summarise,
    write_csv,
)
from ca_hmcd_stage3_statistics import (
    bca_mean_interval,
    holm_adjust,
    paired_row,
    stable_seed,
    wilson_score_interval,
)


FeatureVector = Tuple[float, float, float, float, float, float]
REPLAY_MAPPING_VERSION = "public-trajectory-replay-v2"
ANTI_UAV_ATTRIBUTE_NAMES = (
    "thermal_crossover",
    "out_of_view",
    "scale_variation",
    "fast_motion",
    "occlusion",
    "dynamic_background_clutter",
    "tiny_size",
    "small_size",
    "medium_size",
    "normal_size",
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _feature(values: Iterable[float]) -> FeatureVector:
    result = tuple(clamp(float(value)) for value in values)
    if len(result) != 6:
        raise ValueError("A replay task feature vector must contain six values")
    return result  # type: ignore[return-value]


@dataclass(frozen=True)
class ReplayTaskState:
    task_id: str
    true_feature: FeatureVector
    observed_feature: FeatureVector
    true_active: bool
    observed_active: bool
    min_resources: int = 1
    max_resources: int = 3
    type_requirements: Tuple[Tuple[str, int], ...] = ()

    def to_task(self, observed: bool) -> Task:
        return Task(
            self.task_id,
            self.observed_feature if observed else self.true_feature,
            self.min_resources,
            self.max_resources,
            dict(self.type_requirements),
            arrival_step=0,
            active=self.observed_active if observed else self.true_active,
        )


@dataclass(frozen=True)
class ReplayStep:
    step: int
    source_time: float
    tasks: Tuple[ReplayTaskState, ...]


@dataclass(frozen=True)
class ReplayEpisode:
    dataset: str
    sequence_id: str
    steps: Tuple[ReplayStep, ...]
    mapping_version: str = REPLAY_MAPPING_VERSION
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def task_count(self) -> int:
        return len(self.steps[0].tasks) if self.steps else 0

    @property
    def task_ids(self) -> Tuple[str, ...]:
        return tuple(state.task_id for state in self.steps[0].tasks) if self.steps else ()

    def validate(self) -> None:
        if not self.dataset.strip():
            raise ValueError("Replay dataset name is required")
        if not self.sequence_id.strip():
            raise ValueError("Replay sequence_id is required")
        if not self.steps:
            raise ValueError("Replay episode must contain at least one step")
        expected_ids = self.task_ids
        if not expected_ids or len(expected_ids) != len(set(expected_ids)):
            raise ValueError("Replay task IDs must be non-empty and unique")
        for expected_step, replay_step in enumerate(self.steps):
            if replay_step.step != expected_step:
                raise ValueError("Replay steps must be contiguous and zero based")
            ids = tuple(state.task_id for state in replay_step.tasks)
            if ids != expected_ids:
                raise ValueError("Replay task order must remain stable across steps")
            for state in replay_step.tasks:
                if not 1 <= state.min_resources <= state.max_resources:
                    raise ValueError(f"Invalid coalition bounds for {state.task_id}")
                for vector in (state.true_feature, state.observed_feature):
                    if len(vector) != 6 or any(not 0.0 <= value <= 1.0 for value in vector):
                        raise ValueError(
                            f"Replay features for {state.task_id} must lie in [0, 1]"
                        )

    def true_tasks_at(self, step: int) -> List[Task]:
        return [state.to_task(observed=False) for state in self.steps[step].tasks]

    def observed_tasks_at(self, step: int) -> List[Task]:
        return [state.to_task(observed=True) for state in self.steps[step].tasks]

    def source_time_at(self, step: int) -> float:
        return self.steps[step].source_time


@dataclass(frozen=True)
class PoseSample:
    time_s: float
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class BoundingBoxSample:
    frame: int
    x: float
    y: float
    width: float
    height: float
    active: bool


@dataclass(frozen=True)
class BlackbirdTrackSpec:
    path: Path
    task_id: str
    offset_steps: int = 0
    start_time_s: Optional[float] = None
    end_time_s: Optional[float] = None


@dataclass(frozen=True)
class UzhFpvTrackSpec:
    path: Path
    task_id: str
    offset_steps: int = 0
    start_time_s: Optional[float] = None
    end_time_s: Optional[float] = None


@dataclass(frozen=True)
class AntiUavTrackSpec:
    path: Path
    task_id: str
    offset_steps: int = 0
    window_mode: str = "full"
    window_frames: Optional[int] = None
    event_padding_frames: int = 30


@dataclass(frozen=True)
class BlackbirdMappingConfig:
    protected_zone: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    protected_radius_m: float = 0.75
    distance_reference_m: float = 8.0
    speed_reference_mps: float = 7.0
    height_reference_m: float = 5.5
    density_radius_m: float = 4.0
    response_horizon_s: float = 8.0
    task_scale: float = 0.45
    risk_floor: float = 0.35
    min_response_window: float = 0.20


@dataclass(frozen=True)
class UzhFpvCleaningConfig:
    max_speed_mps: float = 25.0
    max_gap_s: float = 2.0
    max_bridge_points: int = 50
    motion_speed_threshold_mps: float = 0.20
    motion_padding_s: float = 1.0
    minimum_segment_points: int = 20


@dataclass(frozen=True)
class AntiUavMappingConfig:
    image_width: int
    image_height: int
    fps: Optional[float] = None
    protected_point: Tuple[float, float] = (0.5, 0.85)
    protected_radius: float = 0.08
    distance_reference: float = 0.75
    speed_reference_per_s: float = 0.30
    speed_reference_per_frame: float = 0.012
    apparent_size_reference: float = 0.12
    density_radius: float = 0.25
    response_horizon_s: float = 8.0
    response_horizon_frames: float = 200.0
    spatial_level: float = 0.50
    risk_floor: float = 0.35
    min_response_window: float = 0.20


def _canonical_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _as_float(value: str) -> float:
    return float(value.strip())


def _row_is_numeric(row: Sequence[str]) -> bool:
    if not row:
        return False
    try:
        for value in row:
            _as_float(value)
    except ValueError:
        return False
    return True


def _find_header_index(headers: Sequence[str], aliases: Sequence[str]) -> int:
    canonical = [_canonical_header(value) for value in headers]
    canonical_aliases = [_canonical_header(value) for value in aliases]
    for alias in canonical_aliases:
        if alias in canonical:
            return canonical.index(alias)
    for index, value in enumerate(canonical):
        if any(value.endswith(alias) for alias in canonical_aliases):
            return index
    raise ValueError(f"Could not locate any of {aliases!r} in CSV headers")


def _timestamp_factor(values: Sequence[float], unit: str) -> float:
    factors = {
        "s": 1.0,
        "ms": 1e-3,
        "us": 1e-6,
        "ns": 1e-9,
    }
    if unit != "auto":
        if unit not in factors:
            raise ValueError("timestamp_unit must be auto, s, ms, us or ns")
        return factors[unit]
    differences = sorted(
        right - left
        for left, right in zip(values, values[1:])
        if right > left
    )
    if not differences:
        return 1.0
    median = differences[len(differences) // 2]
    if median > 100_000:
        return 1e-9
    if median > 100:
        return 1e-6
    if median > 0.1:
        return 1e-3
    return 1.0


def read_blackbird_pose_csv(
    path: Path,
    timestamp_unit: str = "auto",
    column_indices: Optional[Tuple[int, int, int, int]] = None,
) -> List[PoseSample]:
    """Read Blackbird ``groundTruthPoses.csv`` or a header-bearing pose CSV.

    Official ``groundTruthPoses.csv`` files do not carry headers. For those
    files the default order is timestamp, x, y, z, followed by orientation.
    Header-bearing exports from the Blackbird ROS topic are detected by name.
    """

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.reader(handle) if row]
    if not rows:
        raise ValueError(f"Blackbird pose file is empty: {path}")
    has_header = not _row_is_numeric(rows[0])
    if has_header:
        headers = rows[0]
        data_rows = rows[1:]
        indices = (
            _find_header_index(
                headers,
                ("timestamp", "time", "time_s", "rosbagTimestamp", "header.stamp"),
            ),
            _find_header_index(headers, ("x", "position.x", "pose.position.x")),
            _find_header_index(headers, ("y", "position.y", "pose.position.y")),
            _find_header_index(headers, ("z", "position.z", "pose.position.z")),
        )
    else:
        data_rows = rows
        indices = column_indices or (0, 1, 2, 3)
    raw: List[Tuple[float, float, float, float]] = []
    for row in data_rows:
        if len(row) <= max(indices):
            continue
        try:
            values = tuple(_as_float(row[index]) for index in indices)
        except ValueError:
            continue
        if all(math.isfinite(value) for value in values):
            raw.append(values)  # type: ignore[arg-type]
    if len(raw) < 2:
        raise ValueError(f"Blackbird pose file needs at least two valid rows: {path}")
    factor = _timestamp_factor([row[0] for row in raw], timestamp_unit)
    first_time = raw[0][0] * factor
    samples = [
        PoseSample(row[0] * factor - first_time, row[1], row[2], row[3])
        for row in raw
    ]
    samples.sort(key=lambda sample: sample.time_s)
    return samples


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_uzh_fpv_rows(path: Path) -> Tuple[List[PoseSample], Dict[str, object]]:
    """Read raw Leica rows from a UZH-FPV ZIP archive or ``leica.txt``."""

    def read_rows(
        handle: io.TextIOBase,
    ) -> Tuple[List[PoseSample], int, int, Dict[str, int | float]]:
        parsed: List[Tuple[float, float, float, float, float]] = []
        candidate_rows = 0
        malformed_rows = 0
        for row in csv.reader(handle):
            if not row or row[0].strip() != "3":
                continue
            candidate_rows += 1
            if len(row) < 13:
                malformed_rows += 1
                continue
            try:
                host_timestamp = datetime.fromisoformat(row[6].strip()).timestamp()
                device_tick_ms = float(row[13])
                x, y, z = (float(value) for value in row[10:13])
            except (TypeError, ValueError):
                malformed_rows += 1
                continue
            if not all(
                math.isfinite(value)
                for value in (host_timestamp, device_tick_ms, x, y, z)
            ):
                malformed_rows += 1
                continue
            parsed.append((host_timestamp, device_tick_ms, x, y, z))
        if len(parsed) < 2:
            raise ValueError(f"UZH-FPV Leica file needs at least two valid rows: {path}")
        positive_device_deltas_ms = [
            right[1] - left[1]
            for left, right in zip(parsed, parsed[1:])
            if 0.0 < right[1] - left[1] <= 1000.0
        ]
        median_delta_s = (
            statistics.median(positive_device_deltas_ms) * 1e-3
            if positive_device_deltas_ms
            else 0.05
        )
        maximum_device_delta_s = max(0.25, 5.0 * median_delta_s)
        elapsed = 0.0
        samples: List[PoseSample] = []
        device_intervals = 0
        host_fallback_intervals = 0
        median_fallback_intervals = 0
        for index, record in enumerate(parsed):
            if index:
                previous = parsed[index - 1]
                device_delta_s = (record[1] - previous[1]) * 1e-3
                host_delta_s = record[0] - previous[0]
                if 0.0 < device_delta_s <= maximum_device_delta_s:
                    delta_s = device_delta_s
                    device_intervals += 1
                elif 0.0 < host_delta_s <= 2.0:
                    delta_s = host_delta_s
                    host_fallback_intervals += 1
                else:
                    delta_s = median_delta_s
                    median_fallback_intervals += 1
                elapsed += delta_s
            samples.append(PoseSample(elapsed, record[2], record[3], record[4]))
        return (
            samples,
            candidate_rows,
            malformed_rows,
            {
                "median_device_interval_s": median_delta_s,
                "device_clock_intervals": device_intervals,
                "host_clock_fallback_intervals": host_fallback_intervals,
                "median_interval_fallbacks": median_fallback_intervals,
            },
        )

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            entries = [
                name for name in archive.namelist() if name.lower().endswith("leica.txt")
            ]
            if len(entries) != 1:
                raise ValueError(
                    f"UZH-FPV archive must contain exactly one leica.txt: {path}"
                )
            with archive.open(entries[0]) as raw:
                with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as handle:
                    samples, candidate_rows, malformed_rows, timing_audit = read_rows(
                        handle
                    )
            entry_name = entries[0]
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            samples, candidate_rows, malformed_rows, timing_audit = read_rows(handle)
        entry_name = path.name
    return samples, {
        "source_path": str(path),
        "source_sha256": _sha256_file(path),
        "archive_entry": entry_name,
        "candidate_measurement_rows": candidate_rows,
        "parsed_points": len(samples),
        "malformed_rows": malformed_rows,
        "time_basis": "leica_device_clock_with_host_fallback",
        **timing_audit,
    }


def _pose_speed(left: PoseSample, right: PoseSample) -> float:
    delta_t = right.time_s - left.time_s
    if delta_t <= 0.0:
        return math.inf
    return _euclidean3(
        (left.x, left.y, left.z),
        (right.x, right.y, right.z),
    ) / delta_t


def _remove_duplicate_pose_times(
    samples: Sequence[PoseSample],
) -> Tuple[List[PoseSample], int]:
    output: List[PoseSample] = []
    removed = 0
    for sample in samples:
        if output and sample.time_s <= output[-1].time_s:
            removed += 1
            continue
        output.append(sample)
    return output, removed


def _remove_isolated_pose_spikes(
    samples: Sequence[PoseSample],
    maximum_speed: float,
) -> Tuple[List[PoseSample], int]:
    output = list(samples)
    removed = 0
    for _ in range(3):
        if len(output) < 3:
            break
        drop = set()
        for index in range(1, len(output) - 1):
            incoming = _pose_speed(output[index - 1], output[index])
            outgoing = _pose_speed(output[index], output[index + 1])
            bridge = _pose_speed(output[index - 1], output[index + 1])
            if (
                incoming > maximum_speed
                and outgoing > maximum_speed
                and bridge <= maximum_speed
            ):
                drop.add(index)
        if not drop:
            break
        output = [sample for index, sample in enumerate(output) if index not in drop]
        removed += len(drop)
    return output, removed


def _split_continuous_pose_segments(
    samples: Sequence[PoseSample],
    cleaning: UzhFpvCleaningConfig,
) -> Tuple[List[List[PoseSample]], int]:
    segments: List[List[PoseSample]] = []
    current: List[PoseSample] = []
    discontinuities = 0
    for sample in samples:
        if current:
            delta_t = sample.time_s - current[-1].time_s
            speed = _pose_speed(current[-1], sample)
            if delta_t > cleaning.max_gap_s or speed > cleaning.max_speed_mps:
                if current:
                    segments.append(current)
                current = []
                discontinuities += 1
        current.append(sample)
    if current:
        segments.append(current)
    return segments, discontinuities


def _longest_plausible_pose_chain(
    samples: Sequence[PoseSample],
    cleaning: UzhFpvCleaningConfig,
) -> Tuple[List[PoseSample], int, int]:
    """Keep the longest time-ordered chain satisfying physical transition limits."""

    if not samples:
        return [], 0, 0
    scores = [1] * len(samples)
    previous = [-1] * len(samples)
    discontinuities = sum(
        (
            right.time_s - left.time_s > cleaning.max_gap_s
            or _pose_speed(left, right) > cleaning.max_speed_mps
        )
        for left, right in zip(samples, samples[1:])
    )
    for right_index in range(len(samples)):
        start = max(0, right_index - cleaning.max_bridge_points - 1)
        for left_index in range(start, right_index):
            delta_t = samples[right_index].time_s - samples[left_index].time_s
            if delta_t <= 0.0 or delta_t > cleaning.max_gap_s:
                continue
            if _pose_speed(samples[left_index], samples[right_index]) > cleaning.max_speed_mps:
                continue
            candidate = scores[left_index] + 1
            if candidate > scores[right_index]:
                scores[right_index] = candidate
                previous[right_index] = left_index
    cursor = max(range(len(samples)), key=lambda index: scores[index])
    indices: List[int] = []
    while cursor >= 0:
        indices.append(cursor)
        cursor = previous[cursor]
    indices.reverse()
    chain = [samples[index] for index in indices]
    return chain, len(samples) - len(chain), discontinuities


def _segment_path_length(samples: Sequence[PoseSample]) -> float:
    return sum(
        _euclidean3(
            (left.x, left.y, left.z),
            (right.x, right.y, right.z),
        )
        for left, right in zip(samples, samples[1:])
    )


def _trim_static_pose_edges(
    samples: Sequence[PoseSample],
    cleaning: UzhFpvCleaningConfig,
) -> Tuple[List[PoseSample], int]:
    if len(samples) < 2:
        return list(samples), 0
    moving_pairs = [
        index
        for index, (left, right) in enumerate(zip(samples, samples[1:]))
        if _pose_speed(left, right) >= cleaning.motion_speed_threshold_mps
    ]
    if not moving_pairs:
        return list(samples), 0
    start_time = max(
        samples[0].time_s,
        samples[moving_pairs[0]].time_s - cleaning.motion_padding_s,
    )
    end_time = min(
        samples[-1].time_s,
        samples[moving_pairs[-1] + 1].time_s + cleaning.motion_padding_s,
    )
    trimmed = [sample for sample in samples if start_time <= sample.time_s <= end_time]
    return trimmed, len(samples) - len(trimmed)


def _localize_pose_samples(
    samples: Sequence[PoseSample],
) -> Tuple[List[PoseSample], Tuple[float, float, float]]:
    origin = (samples[0].x, samples[0].y, samples[0].z)
    first_time = samples[0].time_s
    return (
        [
            PoseSample(
                sample.time_s - first_time,
                sample.x - origin[0],
                sample.y - origin[1],
                sample.z - origin[2],
            )
            for sample in samples
        ],
        origin,
    )


def read_uzh_fpv_leica(
    path: Path,
    cleaning: UzhFpvCleaningConfig = UzhFpvCleaningConfig(),
) -> Tuple[List[PoseSample], Dict[str, object]]:
    """Parse and quality-control one UZH-FPV Leica trajectory."""

    if cleaning.max_speed_mps <= 0.0:
        raise ValueError("UZH-FPV maximum speed must be positive")
    if cleaning.max_gap_s <= 0.0:
        raise ValueError("UZH-FPV maximum gap must be positive")
    if cleaning.max_bridge_points < 0:
        raise ValueError("UZH-FPV max_bridge_points cannot be negative")
    if cleaning.motion_speed_threshold_mps < 0.0:
        raise ValueError("UZH-FPV motion threshold cannot be negative")
    raw_samples, audit = _parse_uzh_fpv_rows(path)
    deduplicated, duplicate_rows = _remove_duplicate_pose_times(raw_samples)
    despiked, isolated_spikes = _remove_isolated_pose_spikes(
        deduplicated,
        cleaning.max_speed_mps,
    )
    plausible, plausibility_removed, discontinuities = (
        _longest_plausible_pose_chain(despiked, cleaning)
    )
    if len(plausible) < cleaning.minimum_segment_points:
        raise ValueError(
            f"UZH-FPV trajectory has no plausible chain with at least "
            f"{cleaning.minimum_segment_points} points: {path}"
        )
    trimmed, static_points = _trim_static_pose_edges(plausible, cleaning)
    if len(trimmed) < cleaning.minimum_segment_points:
        raise ValueError(f"UZH-FPV cleaned trajectory is too short: {path}")
    localized, origin = _localize_pose_samples(trimmed)
    audit.update(
        {
            "duplicate_or_nonincreasing_time_rows_removed": duplicate_rows,
            "isolated_spikes_removed": isolated_spikes,
            "plausibility_chain_points_removed": plausibility_removed,
            "discontinuity_boundaries": discontinuities,
            "continuous_segments": discontinuities + 1,
            "eligible_segments": 1,
            "selected_segment_points": len(plausible),
            "static_edge_points_removed": static_points,
            "cleaned_points": len(localized),
            "cleaned_duration_s": localized[-1].time_s,
            "origin_x": origin[0],
            "origin_y": origin[1],
            "origin_z": origin[2],
            "max_speed_mps": cleaning.max_speed_mps,
            "max_gap_s": cleaning.max_gap_s,
            "max_bridge_points": cleaning.max_bridge_points,
            "motion_speed_threshold_mps": cleaning.motion_speed_threshold_mps,
            "motion_padding_s": cleaning.motion_padding_s,
            "removed_total": len(raw_samples) - len(localized),
            "quality_status": "PASS",
        }
    )
    return localized, audit


def _window_pose_samples(
    samples: Sequence[PoseSample],
    start_time_s: Optional[float],
    end_time_s: Optional[float],
) -> List[PoseSample]:
    start = samples[0].time_s if start_time_s is None else start_time_s
    end = samples[-1].time_s if end_time_s is None else end_time_s
    selected = [sample for sample in samples if start <= sample.time_s <= end]
    if len(selected) < 2:
        raise ValueError("The selected Blackbird time window contains fewer than two poses")
    return selected


def _nearest_resample_pose(
    samples: Sequence[PoseSample],
    count: int,
) -> List[PoseSample]:
    if count < 1:
        raise ValueError("Replay step count must be positive")
    if count == 1:
        return [samples[len(samples) // 2]]
    start = samples[0].time_s
    end = samples[-1].time_s
    targets = [start + (end - start) * index / (count - 1) for index in range(count)]
    output: List[PoseSample] = []
    cursor = 0
    for target in targets:
        while (
            cursor + 1 < len(samples)
            and abs(samples[cursor + 1].time_s - target)
            <= abs(samples[cursor].time_s - target)
        ):
            cursor += 1
        output.append(samples[cursor])
    return output


def _euclidean3(left: Tuple[float, float, float], right: Tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _euclidean2(left: Tuple[float, float], right: Tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _closing_speed(previous_distance: float, distance: float, delta_t: float) -> float:
    return max(0.0, previous_distance - distance) / max(delta_t, 1e-9)


def _response_window(
    distance: float,
    protected_radius: float,
    closing_speed: float,
    response_horizon_s: float,
    minimum: float,
) -> float:
    if distance <= protected_radius:
        return minimum
    if closing_speed <= 1e-9:
        return 1.0
    time_to_entry = (distance - protected_radius) / closing_speed
    return clamp(time_to_entry / max(response_horizon_s, 1e-9), minimum, 1.0)


def _build_metric_pose_episode(
    sampled_tracks: Mapping[str, Sequence[Optional[PoseSample]]],
    task_ids: Sequence[str],
    dataset: str,
    sequence_id: str,
    mapping: BlackbirdMappingConfig,
    metadata: Mapping[str, object],
) -> ReplayEpisode:
    previous_distances: Dict[str, float] = {}
    replay_steps: List[ReplayStep] = []
    step_count = len(next(iter(sampled_tracks.values())))
    for step in range(step_count):
        active_positions = {
            task_id: (sample.x, sample.y, sample.z)
            for task_id, values in sampled_tracks.items()
            if (sample := values[step]) is not None
        }
        states: List[ReplayTaskState] = []
        source_times: List[float] = []
        for task_id in task_ids:
            sample = sampled_tracks[task_id][step]
            if sample is None:
                inactive_feature = _feature(
                    (
                        mapping.task_scale,
                        0.0,
                        0.0,
                        0.0,
                        mapping.risk_floor,
                        1.0,
                    )
                )
                states.append(
                    ReplayTaskState(
                        task_id,
                        inactive_feature,
                        inactive_feature,
                        False,
                        False,
                    )
                )
                continue
            source_times.append(sample.time_s)
            position = (sample.x, sample.y, sample.z)
            previous_sample = (
                sampled_tracks[task_id][step - 1] if step > 0 else None
            )
            if previous_sample is None:
                speed = 0.0
                delta_t = 1.0
            else:
                delta_t = max(sample.time_s - previous_sample.time_s, 1e-9)
                speed = _euclidean3(
                    position,
                    (previous_sample.x, previous_sample.y, previous_sample.z),
                ) / delta_t
            distance = _euclidean3(position, mapping.protected_zone)
            previous_distance = previous_distances.get(task_id, distance)
            closing = _closing_speed(previous_distance, distance, delta_t)
            previous_distances[task_id] = distance
            neighbours = [
                max(
                    0.0,
                    1.0 - _euclidean3(position, other) / mapping.density_radius_m,
                )
                for other_id, other in active_positions.items()
                if other_id != task_id
            ]
            density = (
                sum(neighbours) / max(1, len(active_positions) - 1)
                if len(active_positions) > 1
                else 0.0
            )
            proximity = 1.0 - clamp(
                max(0.0, distance - mapping.protected_radius_m)
                / mapping.distance_reference_m
            )
            approach = clamp(closing / mapping.speed_reference_mps)
            risk = clamp(
                mapping.risk_floor
                + 0.35 * proximity
                + 0.20 * approach
                + 0.10 * density
            )
            window = _response_window(
                distance,
                mapping.protected_radius_m,
                closing,
                mapping.response_horizon_s,
                mapping.min_response_window,
            )
            feature = _feature(
                (
                    mapping.task_scale,
                    speed / mapping.speed_reference_mps,
                    abs(sample.z - mapping.protected_zone[2])
                    / mapping.height_reference_m,
                    density,
                    risk,
                    window,
                )
            )
            states.append(
                ReplayTaskState(task_id, feature, feature, True, True)
            )
        replay_steps.append(
            ReplayStep(
                step,
                sum(source_times) / len(source_times) if source_times else float(step),
                tuple(states),
            )
        )
    episode = ReplayEpisode(
        dataset,
        sequence_id,
        tuple(replay_steps),
        metadata=metadata,
    )
    episode.validate()
    return episode


def build_blackbird_episode(
    tracks: Sequence[BlackbirdTrackSpec],
    sequence_id: str,
    step_count: int = 12,
    mapping: BlackbirdMappingConfig = BlackbirdMappingConfig(),
    timestamp_unit: str = "auto",
    column_indices: Optional[Tuple[int, int, int, int]] = None,
) -> ReplayEpisode:
    if not tracks:
        raise ValueError("At least one Blackbird track is required")
    sampled_tracks: Dict[str, List[Optional[PoseSample]]] = {}
    for track in tracks:
        if track.task_id in sampled_tracks:
            raise ValueError(f"Duplicate task ID: {track.task_id}")
        samples = read_blackbird_pose_csv(
            track.path,
            timestamp_unit=timestamp_unit,
            column_indices=column_indices,
        )
        samples = _window_pose_samples(samples, track.start_time_s, track.end_time_s)
        active_count = step_count - track.offset_steps
        if active_count < 1:
            raise ValueError(f"Offset removes all replay steps for {track.task_id}")
        sampled = _nearest_resample_pose(samples, active_count)
        sampled_tracks[track.task_id] = (
            [None] * track.offset_steps + list(sampled)
        )[:step_count]
    return _build_metric_pose_episode(
        sampled_tracks,
        tuple(track.task_id for track in tracks),
        "blackbird",
        sequence_id,
        mapping,
        {
            "evidence_type": "measured_3d_motion_with_simulated_response_resources",
            "track_count": len(tracks),
            "source_files": [str(track.path) for track in tracks],
            "source_sha256": [_sha256_file(track.path) for track in tracks],
            "protected_zone": list(mapping.protected_zone),
            "pose_column_indices": list(column_indices or (0, 1, 2, 3)),
            "synthetic_dimensions": [
                "task_scale",
                "risk",
                "response_window",
                "resources",
            ],
            "measured_dimensions": ["position", "speed", "spatial_level"],
        },
    )


def _register_pose_endpoint(
    samples: Sequence[PoseSample],
    protected_zone: Tuple[float, float, float],
) -> List[PoseSample]:
    endpoint = (samples[-1].x, samples[-1].y, samples[-1].z)
    return [
        PoseSample(
            sample.time_s,
            sample.x - endpoint[0] + protected_zone[0],
            sample.y - endpoint[1] + protected_zone[1],
            sample.z - endpoint[2] + protected_zone[2],
        )
        for sample in samples
    ]


def build_uzh_fpv_episode(
    tracks: Sequence[UzhFpvTrackSpec],
    sequence_id: str,
    step_count: int = 12,
    mapping: BlackbirdMappingConfig = BlackbirdMappingConfig(),
    cleaning: UzhFpvCleaningConfig = UzhFpvCleaningConfig(),
) -> ReplayEpisode:
    if not tracks:
        raise ValueError("At least one UZH-FPV track is required")
    sampled_tracks: Dict[str, List[Optional[PoseSample]]] = {}
    track_audits: List[Dict[str, object]] = []
    for track in tracks:
        if track.task_id in sampled_tracks:
            raise ValueError(f"Duplicate task ID: {track.task_id}")
        samples, audit = read_uzh_fpv_leica(track.path, cleaning)
        samples = _window_pose_samples(samples, track.start_time_s, track.end_time_s)
        samples = _register_pose_endpoint(samples, mapping.protected_zone)
        active_count = step_count - track.offset_steps
        if active_count < 1:
            raise ValueError(f"Offset removes all replay steps for {track.task_id}")
        sampled = _nearest_resample_pose(samples, active_count)
        sampled_tracks[track.task_id] = (
            [None] * track.offset_steps + list(sampled)
        )[:step_count]
        track_audits.append(
            {
                "task_id": track.task_id,
                "offset_steps": track.offset_steps,
                "selected_start_time_s": track.start_time_s,
                "selected_end_time_s": track.end_time_s,
                **audit,
            }
        )
    return _build_metric_pose_episode(
        sampled_tracks,
        tuple(track.task_id for track in tracks),
        "uzh_fpv",
        sequence_id,
        mapping,
        {
            "evidence_type": (
                "measured_3d_position_traces_with_simulated_response_resources"
            ),
            "track_count": len(tracks),
            "source_files": [str(track.path) for track in tracks],
            "source_sha256": [str(row["source_sha256"]) for row in track_audits],
            "protected_zone": list(mapping.protected_zone),
            "time_basis": "leica_device_clock_with_host_fallback",
            "registration": "trajectory_endpoint_to_common_protected_zone",
            "cleaning_version": "uzh-fpv-leica-cleaning-v1",
            "track_quality_audits": track_audits,
            "synthetic_dimensions": [
                "task_scale",
                "risk",
                "response_window",
                "protected_zone_registration",
                "multi_sequence_concurrency",
                "resources",
            ],
            "measured_dimensions": ["position", "speed", "spatial_level"],
        },
    )


def read_anti_uav_annotations(path: Path) -> List[BoundingBoxSample]:
    """Read Anti-UAV410 TXT boxes or Anti-UAV JSON annotations."""

    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Anti-UAV JSON annotation must be an object")
        boxes = (
            payload.get("gt_rect")
            or payload.get("ground_truth")
            or payload.get("bboxes")
        )
        existence = payload.get("existence") or payload.get("exist")
        if not isinstance(boxes, list):
            raise ValueError("Anti-UAV JSON annotation has no supported box list")
        output: List[BoundingBoxSample] = []
        for frame, box in enumerate(boxes):
            active = bool(existence[frame]) if isinstance(existence, list) else True
            if not isinstance(box, list) or len(box) < 4:
                active = False
                values = (0.0, 0.0, 0.0, 0.0)
            else:
                values = tuple(float(value) for value in box[:4])
                active = active and values[2] > 0.0 and values[3] > 0.0
            output.append(BoundingBoxSample(frame, *values, active))
        return output

    output = []
    for frame, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines()):
        values = [value for value in re.split(r"[,\s]+", line.strip()) if value]
        if len(values) < 4:
            output.append(BoundingBoxSample(frame, 0.0, 0.0, 0.0, 0.0, False))
            continue
        try:
            x, y, width, height = (float(value) for value in values[:4])
        except ValueError:
            output.append(BoundingBoxSample(frame, 0.0, 0.0, 0.0, 0.0, False))
            continue
        active = all(math.isfinite(value) for value in (x, y, width, height))
        active = active and width > 0.0 and height > 0.0
        output.append(BoundingBoxSample(frame, x, y, width, height, active))
    if not output:
        raise ValueError(f"Anti-UAV annotation file is empty: {path}")
    return output


def read_anti_uav_attributes(path: Path) -> Dict[str, int]:
    attribute_path = path.parent / "att" / path.name
    if not attribute_path.is_file():
        return {name: 0 for name in ANTI_UAV_ATTRIBUTE_NAMES}
    values = [
        value
        for value in re.split(
            r"[,\s]+",
            attribute_path.read_text(encoding="utf-8-sig").strip(),
        )
        if value
    ]
    if len(values) < len(ANTI_UAV_ATTRIBUTE_NAMES):
        raise ValueError(f"Anti-UAV attribute file has fewer than 10 values: {attribute_path}")
    return {
        name: int(float(values[index]) != 0.0)
        for index, name in enumerate(ANTI_UAV_ATTRIBUTE_NAMES)
    }


def _inactive_runs(
    samples: Sequence[BoundingBoxSample],
) -> List[Tuple[int, int]]:
    runs: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for index, sample in enumerate(samples):
        if not sample.active and start is None:
            start = index
        if sample.active and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(samples) - 1))
    return runs


def _bounded_window(
    sample_count: int,
    center: int,
    requested_length: int,
) -> Tuple[int, int]:
    length = max(2, min(sample_count, requested_length))
    start = max(0, center - length // 2)
    end = min(sample_count, start + length)
    start = max(0, end - length)
    return start, end


def _anti_motion_scores(
    samples: Sequence[BoundingBoxSample],
    image_width: int,
    image_height: int,
) -> List[float]:
    diagonal = math.hypot(image_width, image_height)
    scores = [0.0]
    for left, right in zip(samples, samples[1:]):
        if not left.active or not right.active:
            scores.append(0.0)
            continue
        left_center = (left.x + 0.5 * left.width, left.y + 0.5 * left.height)
        right_center = (right.x + 0.5 * right.width, right.y + 0.5 * right.height)
        scores.append(_euclidean2(left_center, right_center) / diagonal)
    return scores


def _anti_scale_scores(
    samples: Sequence[BoundingBoxSample],
) -> List[float]:
    scores = [0.0]
    for left, right in zip(samples, samples[1:]):
        if not left.active or not right.active:
            scores.append(0.0)
            continue
        left_area = max(left.width * left.height, 1e-9)
        right_area = max(right.width * right.height, 1e-9)
        scores.append(abs(math.log(right_area / left_area)))
    return scores


def select_anti_uav_window(
    samples: Sequence[BoundingBoxSample],
    mode: str,
    image_width: int,
    image_height: int,
    window_frames: Optional[int] = None,
    event_padding_frames: int = 30,
) -> Tuple[List[BoundingBoxSample], Dict[str, object]]:
    if mode not in {"full", "auto", "visibility", "motion", "scale"}:
        raise ValueError(
            "Anti-UAV window mode must be full, auto, visibility, motion or scale"
        )
    if not samples:
        raise ValueError("Anti-UAV window selection needs at least one sample")
    if event_padding_frames < 0:
        raise ValueError("Anti-UAV event padding cannot be negative")
    requested = window_frames or min(len(samples), 240)
    selected_mode = mode
    event_center = len(samples) // 2
    visibility_runs = _inactive_runs(samples)
    if mode == "full":
        start, end = 0, len(samples)
    else:
        if mode == "auto":
            selected_mode = "visibility" if visibility_runs else "motion"
        if selected_mode == "visibility":
            if visibility_runs:
                longest = max(
                    visibility_runs,
                    key=lambda run: (run[1] - run[0] + 1, -run[0]),
                )
                event_center = (longest[0] + longest[1]) // 2
                minimum_length = (
                    longest[1] - longest[0] + 1 + 2 * event_padding_frames
                )
                start, end = _bounded_window(
                    len(samples),
                    event_center,
                    max(requested, minimum_length),
                )
            else:
                selected_mode = "motion"
        if selected_mode == "motion":
            scores = _anti_motion_scores(samples, image_width, image_height)
            event_center = max(range(len(scores)), key=scores.__getitem__)
            start, end = _bounded_window(len(samples), event_center, requested)
        elif selected_mode == "scale":
            scores = _anti_scale_scores(samples)
            event_center = max(range(len(scores)), key=scores.__getitem__)
            start, end = _bounded_window(len(samples), event_center, requested)
    selected = list(samples[start:end])
    return selected, {
        "requested_window_mode": mode,
        "selected_window_mode": selected_mode,
        "source_start_frame": selected[0].frame,
        "source_end_frame": selected[-1].frame,
        "source_window_frames": len(selected),
        "event_center_frame": samples[event_center].frame,
        "visibility_event_count": len(visibility_runs),
        "inactive_frames_in_window": sum(not sample.active for sample in selected),
    }


def _event_preserving_resample_boxes(
    samples: Sequence[BoundingBoxSample],
    count: int,
) -> List[BoundingBoxSample]:
    if count < 1:
        raise ValueError("Replay step count must be positive")
    if count == 1:
        return [samples[len(samples) // 2]]
    if len(samples) <= count:
        return [
            samples[round((len(samples) - 1) * index / (count - 1))]
            for index in range(count)
        ]
    required = {0, len(samples) - 1}
    runs = _inactive_runs(samples)
    for start, end in runs:
        required.update((start, (start + end) // 2, end))
        if start > 0:
            required.add(start - 1)
        if end + 1 < len(samples):
            required.add(end + 1)
    for index in range(1, len(samples)):
        if samples[index].active != samples[index - 1].active:
            required.update((index - 1, index))
    required_indices = sorted(required)
    if len(required_indices) > count:
        required_indices = [
            required_indices[
                round((len(required_indices) - 1) * index / (count - 1))
            ]
            for index in range(count)
        ]
    selected = set(required_indices)
    uniform = [
        round((len(samples) - 1) * index / (count - 1))
        for index in range(count)
    ]
    for index in uniform:
        if len(selected) >= count:
            break
        selected.add(index)
    if len(selected) < count:
        candidates = sorted(
            (
                min(abs(index - chosen) for chosen in selected),
                index,
            )
            for index in range(len(samples))
            if index not in selected
        )
        for _, index in reversed(candidates):
            selected.add(index)
            if len(selected) >= count:
                break
    return [samples[index] for index in sorted(selected)[:count]]


def build_anti_uav_episode(
    tracks: Sequence[AntiUavTrackSpec],
    sequence_id: str,
    mapping: AntiUavMappingConfig,
    step_count: int = 12,
) -> ReplayEpisode:
    if not tracks:
        raise ValueError("At least one Anti-UAV track is required")
    if mapping.image_width <= 0 or mapping.image_height <= 0:
        raise ValueError("Anti-UAV image dimensions must be positive")
    if mapping.fps is not None and mapping.fps <= 0:
        raise ValueError("Anti-UAV fps must be positive when provided")
    sampled_tracks: Dict[str, List[Optional[BoundingBoxSample]]] = {}
    track_windows: List[Dict[str, object]] = []
    for track in tracks:
        if track.task_id in sampled_tracks:
            raise ValueError(f"Duplicate task ID: {track.task_id}")
        samples = read_anti_uav_annotations(track.path)
        selected, window_audit = select_anti_uav_window(
            samples,
            track.window_mode,
            mapping.image_width,
            mapping.image_height,
            track.window_frames,
            track.event_padding_frames,
        )
        active_count = step_count - track.offset_steps
        if active_count < 1:
            raise ValueError(f"Offset removes all replay steps for {track.task_id}")
        sampled_tracks[track.task_id] = (
            [None] * track.offset_steps
            + _event_preserving_resample_boxes(selected, active_count)
        )[:step_count]
        attributes = read_anti_uav_attributes(track.path)
        track_windows.append(
            {
                "task_id": track.task_id,
                "source_path": str(track.path),
                "source_sha256": _sha256_file(track.path),
                "source_frames": len(samples),
                "source_active_frames": sum(sample.active for sample in samples),
                "source_inactive_frames": sum(not sample.active for sample in samples),
                "offset_steps": track.offset_steps,
                **window_audit,
                **attributes,
            }
        )

    task_ids = tuple(track.task_id for track in tracks)
    previous_distances: Dict[str, float] = {}
    replay_steps: List[ReplayStep] = []
    image_diagonal = math.hypot(mapping.image_width, mapping.image_height)
    time_basis = "seconds" if mapping.fps is not None else "frames"
    speed_reference = (
        mapping.speed_reference_per_s
        if mapping.fps is not None
        else mapping.speed_reference_per_frame
    )
    response_horizon = (
        mapping.response_horizon_s
        if mapping.fps is not None
        else mapping.response_horizon_frames
    )
    for step in range(step_count):
        geometry: Dict[str, Tuple[float, float, float, float]] = {}
        for task_id, values in sampled_tracks.items():
            box = values[step]
            if box is None or not box.active:
                continue
            center = (
                (box.x + 0.5 * box.width) / mapping.image_width,
                (box.y + 0.5 * box.height) / mapping.image_height,
            )
            apparent_size = math.sqrt(
                box.width * box.height
                / (mapping.image_width * mapping.image_height)
            )
            geometry[task_id] = (center[0], center[1], apparent_size, float(box.frame))

        states: List[ReplayTaskState] = []
        source_frames: List[float] = []
        for task_id in task_ids:
            box = sampled_tracks[task_id][step]
            if box is None or not box.active:
                inactive = _feature(
                    (
                        0.0,
                        0.0,
                        mapping.spatial_level,
                        0.0,
                        mapping.risk_floor,
                        1.0,
                    )
                )
                states.append(
                    ReplayTaskState(task_id, inactive, inactive, False, False)
                )
                continue
            source_frames.append(float(box.frame))
            center_x, center_y, apparent_size, _ = geometry[task_id]
            center = (center_x, center_y)
            previous_box = (
                sampled_tracks[task_id][step - 1] if step > 0 else None
            )
            if previous_box is None or not previous_box.active:
                motion_rate = 0.0
                delta_t = 1.0 / mapping.fps if mapping.fps is not None else 1.0
            else:
                previous_center = (
                    (previous_box.x + 0.5 * previous_box.width)
                    / mapping.image_width,
                    (previous_box.y + 0.5 * previous_box.height)
                    / mapping.image_height,
                )
                frame_delta = max(1, box.frame - previous_box.frame)
                delta_t = (
                    frame_delta / mapping.fps
                    if mapping.fps is not None
                    else float(frame_delta)
                )
                pixel_distance = math.hypot(
                    (center[0] - previous_center[0]) * mapping.image_width,
                    (center[1] - previous_center[1]) * mapping.image_height,
                )
                motion_rate = pixel_distance / image_diagonal / delta_t
            distance = _euclidean2(center, mapping.protected_point)
            previous_distance = previous_distances.get(task_id, distance)
            closing = _closing_speed(previous_distance, distance, delta_t)
            previous_distances[task_id] = distance
            neighbours = [
                max(
                    0.0,
                    1.0
                    - _euclidean2(center, (other[0], other[1]))
                    / mapping.density_radius,
                )
                for other_id, other in geometry.items()
                if other_id != task_id
            ]
            density = (
                sum(neighbours) / max(1, len(geometry) - 1)
                if len(geometry) > 1
                else 0.0
            )
            proximity = 1.0 - clamp(
                max(0.0, distance - mapping.protected_radius)
                / mapping.distance_reference
            )
            approach = clamp(closing / speed_reference)
            risk = clamp(
                mapping.risk_floor
                + 0.35 * proximity
                + 0.20 * approach
                + 0.10 * density
            )
            window = _response_window(
                distance,
                mapping.protected_radius,
                closing,
                response_horizon,
                mapping.min_response_window,
            )
            feature = _feature(
                (
                    apparent_size / mapping.apparent_size_reference,
                    motion_rate / speed_reference,
                    mapping.spatial_level,
                    density,
                    risk,
                    window,
                )
            )
            states.append(
                ReplayTaskState(task_id, feature, feature, True, True)
            )
        replay_steps.append(
            ReplayStep(
                step,
                (
                    (
                        sum(source_frames) / len(source_frames) / mapping.fps
                        if mapping.fps is not None
                        else sum(source_frames) / len(source_frames)
                    )
                    if source_frames
                    else float(step)
                ),
                tuple(states),
            )
        )
    episode = ReplayEpisode(
        "anti_uav410",
        sequence_id,
        tuple(replay_steps),
        metadata={
            "evidence_type": "measured_image_plane_tracks_with_simulated_response_resources",
            "track_count": len(tracks),
            "source_files": [str(track.path) for track in tracks],
            "source_sha256": [str(row["source_sha256"]) for row in track_windows],
            "image_width": mapping.image_width,
            "image_height": mapping.image_height,
            "fps": mapping.fps,
            "time_basis": time_basis,
            "motion_unit": (
                "normalized_image_diagonal_per_second"
                if mapping.fps is not None
                else "normalized_image_diagonal_per_frame"
            ),
            "event_window_version": "anti-uav-event-window-v1",
            "track_windows": track_windows,
            "protected_point": list(mapping.protected_point),
            "synthetic_dimensions": [
                "spatial_level",
                "risk",
                "response_window",
                "multi_sequence_concurrency",
                "resources",
            ],
            "measured_dimensions": ["apparent_size", "image_plane_speed", "visibility"],
        },
    )
    episode.validate()
    return episode


def apply_observation_disturbance(
    episode: ReplayEpisode,
    seed: int,
    latency_steps: int = 0,
    dropout_probability: float = 0.0,
    dropout_burst_steps: int = 1,
    feature_noise: float = 0.0,
) -> ReplayEpisode:
    """Create perceived-state delay, dropout and feature noise.

    True states remain unchanged, so the common external evaluator continues to
    score every method against the same clean public-data-derived trajectory.
    """

    if latency_steps < 0:
        raise ValueError("latency_steps must be non-negative")
    if not 0.0 <= dropout_probability <= 1.0:
        raise ValueError("dropout_probability must lie in [0, 1]")
    if dropout_burst_steps < 1:
        raise ValueError("dropout_burst_steps must be positive")
    if feature_noise < 0.0:
        raise ValueError("feature_noise must be non-negative")
    rng = random.Random(seed)
    burst_remaining = {task_id: 0 for task_id in episode.task_ids}
    disturbed_steps: List[ReplayStep] = []
    for step_index, replay_step in enumerate(episode.steps):
        source_index = step_index - latency_steps
        delayed = episode.steps[source_index] if source_index >= 0 else None
        delayed_lookup = (
            {state.task_id: state for state in delayed.tasks}
            if delayed is not None
            else {}
        )
        states: List[ReplayTaskState] = []
        for true_state in replay_step.tasks:
            observed_source = delayed_lookup.get(true_state.task_id)
            observed_active = bool(
                observed_source is not None and observed_source.true_active
            )
            observed_feature = (
                observed_source.true_feature
                if observed_source is not None
                else true_state.observed_feature
            )
            if observed_active and burst_remaining[true_state.task_id] == 0:
                if rng.random() < dropout_probability:
                    burst_remaining[true_state.task_id] = dropout_burst_steps
            if burst_remaining[true_state.task_id] > 0:
                observed_active = False
                burst_remaining[true_state.task_id] -= 1
            if feature_noise:
                observed_feature = _feature(
                    value + rng.gauss(0.0, feature_noise)
                    for value in observed_feature
                )
            states.append(
                replace(
                    true_state,
                    observed_feature=observed_feature,
                    observed_active=observed_active,
                )
            )
        disturbed_steps.append(
            ReplayStep(replay_step.step, replay_step.source_time, tuple(states))
        )
    metadata = dict(episode.metadata)
    metadata["observation_disturbance"] = {
        "seed": seed,
        "latency_steps": latency_steps,
        "dropout_probability": dropout_probability,
        "dropout_burst_steps": dropout_burst_steps,
        "feature_noise": feature_noise,
    }
    result = replace(episode, steps=tuple(disturbed_steps), metadata=metadata)
    result.validate()
    return result


def write_replay_episode(path: Path, episode: ReplayEpisode) -> None:
    episode.validate()
    rows: List[Dict[str, object]] = []
    names = ("s", "v", "h", "n", "rho", "tau")
    metadata_json = json.dumps(
        dict(episode.metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    for replay_step in episode.steps:
        for state in replay_step.tasks:
            row: Dict[str, object] = {
                "dataset": episode.dataset,
                "sequence_id": episode.sequence_id,
                "mapping_version": episode.mapping_version,
                "metadata_json": metadata_json,
                "step": replay_step.step,
                "source_time": replay_step.source_time,
                "task_id": state.task_id,
                "true_active": int(state.true_active),
                "observed_active": int(state.observed_active),
                "min_resources": state.min_resources,
                "max_resources": state.max_resources,
                "type_requirements_json": json.dumps(
                    dict(state.type_requirements), sort_keys=True, separators=(",", ":")
                ),
            }
            row.update(
                {
                    f"true_{name}": value
                    for name, value in zip(names, state.true_feature)
                }
            )
            row.update(
                {
                    f"observed_{name}": value
                    for name, value in zip(names, state.observed_feature)
                }
            )
            rows.append(row)
    write_csv(path, rows)


def read_replay_episode(path: Path) -> ReplayEpisode:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"Replay CSV is empty: {path}")
    names = ("s", "v", "h", "n", "rho", "tau")
    grouped: Dict[int, List[ReplayTaskState]] = {}
    source_times: Dict[int, float] = {}
    for row in rows:
        step = int(row["step"])
        state = ReplayTaskState(
            row["task_id"],
            _feature(float(row[f"true_{name}"]) for name in names),
            _feature(float(row[f"observed_{name}"]) for name in names),
            bool(int(row["true_active"])),
            bool(int(row["observed_active"])),
            int(row["min_resources"]),
            int(row["max_resources"]),
            tuple(
                sorted(
                    (
                        str(key),
                        int(value),
                    )
                    for key, value in json.loads(
                        row.get("type_requirements_json", "{}") or "{}"
                    ).items()
                )
            ),
        )
        grouped.setdefault(step, []).append(state)
        source_times[step] = float(row["source_time"])
    steps = tuple(
        ReplayStep(step, source_times[step], tuple(grouped[step]))
        for step in sorted(grouped)
    )
    metadata_text = rows[0].get("metadata_json", "{}") or "{}"
    episode = ReplayEpisode(
        rows[0]["dataset"],
        rows[0]["sequence_id"],
        steps,
        rows[0].get("mapping_version", REPLAY_MAPPING_VERSION),
        json.loads(metadata_text),
    )
    episode.validate()
    return episode


def _pose_manifest_metrics(samples: Sequence[PoseSample]) -> Dict[str, object]:
    speeds = [
        _pose_speed(left, right)
        for left, right in zip(samples, samples[1:])
        if right.time_s > left.time_s
    ]
    return {
        "cleaned_duration_s": samples[-1].time_s,
        "cleaned_path_length_m": _segment_path_length(samples),
        "median_cleaned_speed_mps": statistics.median(speeds) if speeds else 0.0,
        "maximum_cleaned_speed_mps": max(speeds) if speeds else 0.0,
        "x_extent_m": max(sample.x for sample in samples)
        - min(sample.x for sample in samples),
        "y_extent_m": max(sample.y for sample in samples)
        - min(sample.y for sample in samples),
        "z_extent_m": max(sample.z for sample in samples)
        - min(sample.z for sample in samples),
    }


def _anti_manifest_row(path: Path, split: str) -> Dict[str, object]:
    samples = read_anti_uav_annotations(path)
    attributes = read_anti_uav_attributes(path)
    selected, window_audit = select_anti_uav_window(
        samples,
        "auto",
        640,
        512,
        window_frames=min(len(samples), 240),
        event_padding_frames=30,
    )
    replay_samples = _event_preserving_resample_boxes(selected, 12)
    active_count = sum(sample.active for sample in samples)
    inactive_count = len(samples) - active_count
    right_edges = [sample.x + sample.width for sample in samples if sample.active]
    bottom_edges = [sample.y + sample.height for sample in samples if sample.active]
    return {
        "dataset": "anti_uav410",
        "split": split,
        "sequence_id": path.stem,
        "source_path": str(path),
        "source_sha256": _sha256_file(path),
        "mapping_version": REPLAY_MAPPING_VERSION,
        "raw_frames": len(samples),
        "active_frames": active_count,
        "inactive_frames": inactive_count,
        "active_ratio": active_count / len(samples),
        "visibility_events": len(_inactive_runs(samples)),
        "maximum_right_coordinate": max(right_edges) if right_edges else 0.0,
        "maximum_bottom_coordinate": max(bottom_edges) if bottom_edges else 0.0,
        "attribute_file_present": int((path.parent / "att" / path.name).is_file()),
        "auto_window_mode": window_audit["selected_window_mode"],
        "auto_window_start_frame": window_audit["source_start_frame"],
        "auto_window_end_frame": window_audit["source_end_frame"],
        "auto_window_source_frames": window_audit["source_window_frames"],
        "auto_window_inactive_frames": window_audit["inactive_frames_in_window"],
        "auto_replay_inactive_steps": sum(
            not sample.active for sample in replay_samples
        ),
        "event_window_status": "PASS",
        **attributes,
        "quality_status": "PASS",
    }


def audit_public_replay_data(
    uzh_dir: Path,
    anti_anno_dir: Path,
    output_dir: Path,
    cleaning: UzhFpvCleaningConfig = UzhFpvCleaningConfig(),
) -> Dict[str, object]:
    """Audit local public-data inputs without redistributing source records."""

    uzh_paths = sorted(uzh_dir.rglob("*.zip"))
    if not uzh_paths:
        raise ValueError(f"No UZH-FPV ZIP archives found under {uzh_dir}")
    uzh_rows: List[Dict[str, object]] = []
    for path in uzh_paths:
        samples, audit = read_uzh_fpv_leica(path, cleaning)
        replay = build_uzh_fpv_episode(
            [UzhFpvTrackSpec(path, path.stem)],
            f"quality-{path.stem}",
            step_count=12,
            cleaning=cleaning,
        )
        uzh_rows.append(
            {
                "dataset": "uzh_fpv",
                "sequence_id": path.stem,
                "mapping_version": REPLAY_MAPPING_VERSION,
                "cleaning_version": "uzh-fpv-leica-cleaning-v1",
                **audit,
                **_pose_manifest_metrics(samples),
                "replay_steps": replay.step_count,
                "replay_feature_validation": "PASS",
            }
        )

    anti_rows: List[Dict[str, object]] = []
    for split in ("train", "val", "test"):
        split_dir = anti_anno_dir / split
        if not split_dir.is_dir():
            continue
        anti_rows.extend(
            _anti_manifest_row(path, split)
            for path in sorted(split_dir.glob("*.txt"))
        )
    if not anti_rows:
        raise ValueError(
            f"No Anti-UAV410 annotation TXT files found under {anti_anno_dir}"
        )

    uzh_unique_hashes = len({str(row["source_sha256"]) for row in uzh_rows})
    anti_unique_hashes = len({str(row["source_sha256"]) for row in anti_rows})
    summary_rows = [
        {
            "dataset": "uzh_fpv",
            "source_sequences": len(uzh_rows),
            "unique_source_hashes": uzh_unique_hashes,
            "raw_samples": sum(int(row["parsed_points"]) for row in uzh_rows),
            "usable_samples": sum(int(row["cleaned_points"]) for row in uzh_rows),
            "inactive_samples": "",
            "malformed_samples": sum(int(row["malformed_rows"]) for row in uzh_rows),
            "quality_status": (
                "PASS"
                if uzh_unique_hashes == len(uzh_rows)
                and all(row["quality_status"] == "PASS" for row in uzh_rows)
                else "FAIL"
            ),
        },
        {
            "dataset": "anti_uav410",
            "source_sequences": len(anti_rows),
            "unique_source_hashes": anti_unique_hashes,
            "raw_samples": sum(int(row["raw_frames"]) for row in anti_rows),
            "usable_samples": sum(int(row["active_frames"]) for row in anti_rows),
            "inactive_samples": sum(int(row["inactive_frames"]) for row in anti_rows),
            "malformed_samples": 0,
            "quality_status": (
                "PASS"
                if anti_unique_hashes == len(anti_rows)
                and all(row["quality_status"] == "PASS" for row in anti_rows)
                else "FAIL"
            ),
        },
    ]
    warnings: List[str] = []
    if len(uzh_rows) < 16:
        warnings.append(
            f"UZH-FPV contains {len(uzh_rows)} source trajectories; "
            "the planned public ground-truth set contains 16."
        )
    if len(anti_rows) < 30:
        warnings.append(
            f"Anti-UAV410 contains only {len(anti_rows)} source sequences; "
            "formal stratified replay requires at least 30."
        )
    if any(int(row["attribute_file_present"]) == 0 for row in anti_rows):
        warnings.append("Some Anti-UAV410 sequences have no sequence-level attribute file.")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "uzh_fpv_quality_manifest.csv", uzh_rows)
    write_csv(output_dir / "anti_uav410_quality_manifest.csv", anti_rows)
    write_csv(output_dir / "data_quality_summary.csv", summary_rows)
    report_lines = [
        "# CA-HMCD Public-Data Quality Audit",
        "",
        f"Audit date: {datetime.now().date().isoformat()}",
        f"Replay mapping version: `{REPLAY_MAPPING_VERSION}`",
        "",
        "## Summary",
        "",
        "| Dataset | Sequences | Raw samples | Usable samples | Status |",
        "|---|---:|---:|---:|---|",
    ]
    report_lines.extend(
        (
            f"| {row['dataset']} | {row['source_sequences']} | "
            f"{row['raw_samples']} | {row['usable_samples']} | "
            f"{row['quality_status']} |"
        )
        for row in summary_rows
    )
    report_lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "UZH-FPV supplies measured three-dimensional position traces. "
            "Anti-UAV410 supplies measured image-plane boxes and visibility. "
            "Protected-zone registration, concurrent-task composition, risk, "
            "response windows, response resources and outcomes remain simulation "
            "constructs.",
            "",
            "## Warnings",
            "",
        ]
    )
    report_lines.extend(
        [f"- {warning}" for warning in warnings]
        or ["- None. Input completeness checks passed."]
    )
    (output_dir / "data_quality_report.md").write_text(
        "\n".join(report_lines) + "\n",
        encoding="utf-8",
    )
    return {
        "uzh_rows": uzh_rows,
        "anti_rows": anti_rows,
        "summary": summary_rows,
        "warnings": warnings,
    }


def _replay_statistical_package(
    episode_records: Sequence[Mapping[str, object]],
    per_step: Sequence[Mapping[str, object]],
    algorithms: Sequence[str],
    bootstrap_resamples: int,
) -> Dict[str, List[Dict[str, object]]]:
    paired_rows: List[Dict[str, object]] = []
    registry: List[Dict[str, object]] = []
    scenarios = sorted({str(row["scenario"]) for row in episode_records})
    for scenario in scenarios:
        scenario_records = [
            row for row in episode_records if str(row["scenario"]) == scenario
        ]
        baselines = [
            algorithm for algorithm in algorithms if algorithm != "CA-HMCD"
        ]
        for metric, suffix, direction in (
            ("independent_service_score", "service", "higher"),
            ("external_objective", "objective", "higher"),
        ):
            family_id = f"replay.{scenario}.{suffix}"
            family_rows: List[Dict[str, object]] = []
            for baseline in baselines:
                a_records = [
                    row
                    for row in scenario_records
                    if row["algorithm"] == "CA-HMCD"
                ]
                b_records = [
                    row
                    for row in scenario_records
                    if row["algorithm"] == baseline
                ]
                if not a_records or not b_records:
                    continue
                row = paired_row(
                    a_records,
                    b_records,
                    metric,
                    family_id,
                    "public-trajectory replay",
                    f"{scenario}: CA-HMCD vs {baseline}",
                    "CA-HMCD",
                    baseline,
                    direction,
                    bootstrap_resamples,
                    {"scenario": scenario},
                )
                row["independent_unit"] = "one complete public-source replay episode"
                row["ci_estimand"] = "mean paired difference at replay-episode level"
                family_rows.append(row)
            paired_rows.extend(family_rows)
            if family_rows:
                registry.append(
                    {
                        "family_id": family_id,
                        "section": "public-trajectory replay",
                        "endpoint": metric,
                        "independent_unit": "one complete public-source replay episode",
                        "family_definition": (
                            f"Within {scenario}, CA-HMCD is compared with every "
                            "prespecified replay baseline for this endpoint."
                        ),
                        "number_of_comparisons": len(family_rows),
                        "members": "; ".join(
                            str(row["comparison"]) for row in family_rows
                        ),
                        "multiplicity_control": (
                            "Holm step-down, two-sided family-wise alpha=0.05"
                        ),
                    }
                )

    response_index: Dict[
        Tuple[str, str, int], Dict[Tuple[int, str], float]
    ] = {}
    for row in per_step:
        key = (
            str(row["scenario"]),
            str(row["algorithm"]),
            int(row["seed"]),
        )
        response_index.setdefault(key, {})
        for task_id, response in json.loads(
            str(row["served_task_response_times_json"])
        ).items():
            response_index[key][(int(row["step"]), str(task_id))] = float(response)

    common_response_by_episode: List[Dict[str, object]] = []
    for scenario in scenarios:
        seeds = sorted(
            {
                int(row["seed"])
                for row in episode_records
                if str(row["scenario"]) == scenario
            }
        )
        family_id = f"replay.{scenario}.common_response"
        family_rows = []
        for baseline in (
            algorithm for algorithm in algorithms if algorithm != "CA-HMCD"
        ):
            a_records: List[Dict[str, object]] = []
            b_records: List[Dict[str, object]] = []
            for seed in seeds:
                a_values = response_index.get(
                    (scenario, "CA-HMCD", seed), {}
                )
                b_values = response_index.get((scenario, baseline, seed), {})
                common_keys = sorted(set(a_values) & set(b_values))
                if not common_keys:
                    continue
                a_mean = sum(a_values[key] for key in common_keys) / len(common_keys)
                b_mean = sum(b_values[key] for key in common_keys) / len(common_keys)
                a_records.append({"seed": seed, "common_response_time": a_mean})
                b_records.append({"seed": seed, "common_response_time": b_mean})
                common_response_by_episode.append(
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
            if not a_records:
                continue
            row = paired_row(
                a_records,
                b_records,
                "common_response_time",
                family_id,
                "public-trajectory common-task response",
                f"{scenario}: CA-HMCD vs {baseline}",
                "CA-HMCD",
                baseline,
                "lower",
                bootstrap_resamples,
                {
                    "scenario": scenario,
                    "episodes_with_common_tasks": len(a_records),
                    "episodes_without_common_tasks": len(seeds) - len(a_records),
                },
            )
            row["independent_unit"] = "one complete public-source replay episode"
            row["ci_estimand"] = "mean paired difference at replay-episode level"
            family_rows.append(row)
        paired_rows.extend(family_rows)
        if family_rows:
            registry.append(
                {
                    "family_id": family_id,
                    "section": "public-trajectory common-task response",
                    "endpoint": "response time on task-periods served by both methods",
                    "independent_unit": "one complete public-source replay episode",
                    "family_definition": (
                        f"Within {scenario}, common-task response time compares "
                        "CA-HMCD with every prespecified replay baseline."
                    ),
                    "number_of_comparisons": len(family_rows),
                    "members": "; ".join(
                        str(row["comparison"]) for row in family_rows
                    ),
                    "multiplicity_control": (
                        "Holm step-down, two-sided family-wise alpha=0.05"
                    ),
                }
            )

    holm_adjust(paired_rows)
    bounded_rows: List[Dict[str, object]] = []
    bounded_metrics = (
        "independent_coverage",
        "independent_feasibility",
        "repair_applied",
        "fallback",
        "same_type_pair_ratio",
        "capability_overlap",
        "marginal_gain_waste",
    )
    event_metrics = {
        "independent_feasibility",
        "repair_applied",
        "fallback",
    }
    for scenario in scenarios:
        for algorithm in algorithms:
            records = [
                row
                for row in episode_records
                if str(row["scenario"]) == scenario
                and str(row["algorithm"]) == algorithm
            ]
            for metric in bounded_metrics:
                values = [float(row[metric]) for row in records]
                if not values:
                    continue
                if metric in event_metrics:
                    low, high = wilson_score_interval(values)
                    method = "Wilson score interval on episode-level event rates"
                else:
                    low, high = bca_mean_interval(
                        values,
                        resamples=bootstrap_resamples,
                        seed=stable_seed("replay-bounded", scenario, algorithm, metric),
                        bounds=(0.0, 1.0),
                    )
                    method = (
                        f"bounded BCa bootstrap, {bootstrap_resamples} "
                        "episode-level resamples"
                    )
                bounded_rows.append(
                    {
                        "scenario": scenario,
                        "algorithm": algorithm,
                        "metric": metric,
                        "n_independent_episodes": len(values),
                        "mean": sum(values) / len(values),
                        "ci95_low": low,
                        "ci95_high": high,
                        "interval_method": method,
                    }
                )
    return {
        "paired_rows": paired_rows,
        "registry": registry,
        "common_response_by_episode": common_response_by_episode,
        "bounded_rows": bounded_rows,
    }


def run_replay_benchmark(
    episodes: Sequence[ReplayEpisode],
    output_dir: Path,
    algorithms: Sequence[str] = ALGORITHMS,
    resources_count: int = 10,
    top_k: int = 12,
    base_seed: int = 7000,
    node_limit: int = 4000,
    require_minimum_episodes: int = 30,
    bootstrap_resamples: int = 10000,
) -> Dict[str, object]:
    for episode in episodes:
        episode.validate()
    dataset_counts: Dict[str, int] = {}
    sequence_keys = set()
    for episode in episodes:
        dataset_counts[episode.dataset] = dataset_counts.get(episode.dataset, 0) + 1
        sequence_key = (episode.dataset, episode.sequence_id)
        if sequence_key in sequence_keys:
            raise ValueError(f"Duplicate replay sequence: {sequence_key}")
        sequence_keys.add(sequence_key)
    undersized = {
        dataset: count
        for dataset, count in dataset_counts.items()
        if count < require_minimum_episodes
    }
    if undersized:
        details = ", ".join(
            f"{dataset}={count}" for dataset, count in sorted(undersized.items())
        )
        raise ValueError(
            f"Each formal replay dataset requires at least "
            f"{require_minimum_episodes} independent episodes; received {details}"
        )
    step_counts = {episode.step_count for episode in episodes}
    if len(step_counts) != 1:
        raise ValueError("All replay episodes must use the same decision horizon")
    unknown = set(algorithms) - set(ALGORITHMS)
    if unknown:
        raise ValueError(f"Unsupported replay algorithms: {sorted(unknown)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    episode_records: List[Dict[str, object]] = []
    per_step: List[Dict[str, object]] = []
    for episode_index, episode in enumerate(episodes):
        seed = base_seed + episode_index
        scenario = f"{episode.dataset}_replay"
        for algorithm in algorithms:
            metrics, rows = run_episode(
                algorithm,
                seed,
                episode.step_count,
                resources_count,
                episode.task_count,
                scenario,
                top_k,
                node_limit_override=node_limit,
                replay_episode=episode,
            )
            episode_records.append(
                {
                    "scenario": scenario,
                    "dataset": episode.dataset,
                    "sequence_id": episode.sequence_id,
                    "algorithm": algorithm,
                    "seed": seed,
                    **metrics,
                }
            )
            per_step.extend(rows)

    summary = summarise(episode_records, ("scenario", "algorithm"))
    statistical_package = _replay_statistical_package(
        episode_records,
        per_step,
        algorithms,
        bootstrap_resamples,
    )
    audit = audit_paired_protocol(
        per_step,
        algorithms,
        expected_seeds=len(episodes),
        expected_steps=next(iter(step_counts)),
    )
    write_csv(output_dir / "replay_episode_results.csv", episode_records)
    write_csv(output_dir / "replay_per_step.csv", per_step)
    write_csv(output_dir / "replay_summary.csv", summary)
    write_csv(
        output_dir / "replay_statistical_tests.csv",
        statistical_package["paired_rows"],
    )
    write_csv(
        output_dir / "replay_holm_family_registry.csv",
        statistical_package["registry"],
    )
    write_csv(
        output_dir / "replay_common_task_response_by_episode.csv",
        statistical_package["common_response_by_episode"],
    )
    write_csv(
        output_dir / "replay_bounded_metric_intervals.csv",
        statistical_package["bounded_rows"],
    )
    write_csv(output_dir / "replay_protocol_audit.csv", audit)
    write_csv(
        output_dir / "replay_episode_manifest.csv",
        [
            {
                "dataset": episode.dataset,
                "sequence_id": episode.sequence_id,
                "mapping_version": episode.mapping_version,
                "steps": episode.step_count,
                "tasks": episode.task_count,
                "metadata_json": json.dumps(
                    dict(episode.metadata),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
            for episode in episodes
        ],
    )
    evidence_boundary = (
        "# Public-trajectory-informed replay evidence boundary\n\n"
        "Measured public trajectories instantiate task-side motion or observation "
        "patterns. Protected-zone definitions, risk, response windows, response "
        "resources, resource effectiveness, costs and coalition outcomes remain "
        "simulation constructs. These experiments support decision-level replay "
        "robustness and do not establish field effectiveness.\n"
    )
    (output_dir / "evidence_boundary.md").write_text(
        evidence_boundary, encoding="utf-8"
    )
    return {
        "episodes": episode_records,
        "per_step": per_step,
        "summary": summary,
        "tests": statistical_package["paired_rows"],
        "holm_families": statistical_package["registry"],
        "common_task_response": statistical_package[
            "common_response_by_episode"
        ],
        "bounded_intervals": statistical_package["bounded_rows"],
        "audit": audit,
    }


def _parse_track(
    value: str,
    cls: type[BlackbirdTrackSpec]
    | type[UzhFpvTrackSpec]
    | type[AntiUavTrackSpec],
):
    if "=" not in value:
        raise argparse.ArgumentTypeError("Track must use TASK_ID=PATH")
    task_id, raw_path = value.split("=", 1)
    if not task_id.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("Track must use TASK_ID=PATH")
    return cls(Path(raw_path), task_id.strip())


def _parse_vector(value: str, length: int) -> Tuple[float, ...]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != length:
        raise argparse.ArgumentTypeError(
            f"Expected {length} comma-separated numeric values"
        )
    try:
        return tuple(float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and run public-trajectory CA-HMCD replays"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    blackbird = subparsers.add_parser(
        "blackbird", help="Convert Blackbird groundTruthPoses.csv files"
    )
    blackbird.add_argument("--track", action="append", required=True)
    blackbird.add_argument("--sequence-id", required=True)
    blackbird.add_argument("--output", type=Path, required=True)
    blackbird.add_argument("--steps", type=int, default=12)
    blackbird.add_argument("--timestamp-unit", default="auto")
    blackbird.add_argument(
        "--columns",
        default="0,1,2,3",
        help="Headerless CSV indices for timestamp,x,y,z",
    )
    blackbird.add_argument("--protected-zone", default="0,0,0")

    uzh = subparsers.add_parser(
        "uzh-fpv", help="Convert UZH-FPV Leica ground-truth ZIP files"
    )
    uzh.add_argument("--track", action="append", required=True)
    uzh.add_argument("--sequence-id", required=True)
    uzh.add_argument("--output", type=Path, required=True)
    uzh.add_argument("--steps", type=int, default=12)
    uzh.add_argument("--protected-zone", default="0,0,0")
    uzh.add_argument("--max-speed-mps", type=float, default=25.0)
    uzh.add_argument("--max-gap-s", type=float, default=2.0)
    uzh.add_argument("--max-bridge-points", type=int, default=50)
    uzh.add_argument("--motion-threshold-mps", type=float, default=0.20)
    uzh.add_argument("--motion-padding-s", type=float, default=1.0)

    anti = subparsers.add_parser(
        "anti-uav", help="Convert Anti-UAV410 TXT or JSON annotations"
    )
    anti.add_argument("--track", action="append", required=True)
    anti.add_argument("--sequence-id", required=True)
    anti.add_argument("--output", type=Path, required=True)
    anti.add_argument("--steps", type=int, default=12)
    anti.add_argument("--width", type=int, required=True)
    anti.add_argument("--height", type=int, required=True)
    anti.add_argument(
        "--fps",
        type=float,
        help=(
            "Optional authoritative frame rate. When omitted, motion is "
            "normalized per frame."
        ),
    )
    anti.add_argument("--protected-point", default="0.5,0.85")
    anti.add_argument(
        "--window-mode",
        choices=("full", "auto", "visibility", "motion", "scale"),
        default="full",
    )
    anti.add_argument("--window-frames", type=int)
    anti.add_argument("--event-padding-frames", type=int, default=30)

    audit = subparsers.add_parser(
        "audit-data", help="Audit UZH-FPV and Anti-UAV410 local inputs"
    )
    audit.add_argument("--uzh-dir", type=Path, required=True)
    audit.add_argument("--anti-anno-dir", type=Path, required=True)
    audit.add_argument("--output-dir", type=Path, required=True)
    audit.add_argument("--max-speed-mps", type=float, default=25.0)
    audit.add_argument("--max-gap-s", type=float, default=2.0)
    audit.add_argument("--max-bridge-points", type=int, default=50)
    audit.add_argument("--motion-threshold-mps", type=float, default=0.20)
    audit.add_argument("--motion-padding-s", type=float, default=1.0)

    disturb = subparsers.add_parser(
        "disturb", help="Add delay, dropout and feature noise to observations"
    )
    disturb.add_argument("--input", type=Path, required=True)
    disturb.add_argument("--output", type=Path, required=True)
    disturb.add_argument("--seed", type=int, required=True)
    disturb.add_argument("--latency-steps", type=int, default=0)
    disturb.add_argument("--dropout-probability", type=float, default=0.0)
    disturb.add_argument("--dropout-burst-steps", type=int, default=1)
    disturb.add_argument("--feature-noise", type=float, default=0.0)

    benchmark = subparsers.add_parser(
        "benchmark", help="Run algorithms on prepared replay CSV files"
    )
    benchmark.add_argument("--replay", action="append", type=Path, required=True)
    benchmark.add_argument("--output-dir", type=Path, required=True)
    benchmark.add_argument("--resources", type=int, default=10)
    benchmark.add_argument("--top-k", type=int, default=12)
    benchmark.add_argument("--base-seed", type=int, default=7000)
    benchmark.add_argument("--node-limit", type=int, default=4000)
    benchmark.add_argument("--minimum-episodes", type=int, default=30)
    benchmark.add_argument("--bootstrap-resamples", type=int, default=10000)
    benchmark.add_argument(
        "--algorithms",
        nargs="+",
        default=list(ALGORITHMS),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    if args.command == "blackbird":
        tracks = [
            _parse_track(value, BlackbirdTrackSpec)
            for value in args.track
        ]
        zone = _parse_vector(args.protected_zone, 3)
        columns = tuple(
            int(value)
            for value in _parse_vector(args.columns, 4)
        )
        episode = build_blackbird_episode(
            tracks,
            args.sequence_id,
            args.steps,
            replace(
                BlackbirdMappingConfig(),
                protected_zone=(zone[0], zone[1], zone[2]),
            ),
            args.timestamp_unit,
            columns,  # type: ignore[arg-type]
        )
        write_replay_episode(args.output, episode)
        return 0
    if args.command == "uzh-fpv":
        tracks = [
            _parse_track(value, UzhFpvTrackSpec)
            for value in args.track
        ]
        zone = _parse_vector(args.protected_zone, 3)
        episode = build_uzh_fpv_episode(
            tracks,
            args.sequence_id,
            args.steps,
            replace(
                BlackbirdMappingConfig(),
                protected_zone=(zone[0], zone[1], zone[2]),
            ),
            UzhFpvCleaningConfig(
                max_speed_mps=args.max_speed_mps,
                max_gap_s=args.max_gap_s,
                max_bridge_points=args.max_bridge_points,
                motion_speed_threshold_mps=args.motion_threshold_mps,
                motion_padding_s=args.motion_padding_s,
            ),
        )
        write_replay_episode(args.output, episode)
        return 0
    if args.command == "anti-uav":
        tracks = []
        for value in args.track:
            track = _parse_track(value, AntiUavTrackSpec)
            tracks.append(
                replace(
                    track,
                    window_mode=args.window_mode,
                    window_frames=args.window_frames,
                    event_padding_frames=args.event_padding_frames,
                )
            )
        point = _parse_vector(args.protected_point, 2)
        episode = build_anti_uav_episode(
            tracks,
            args.sequence_id,
            AntiUavMappingConfig(
                args.width,
                args.height,
                args.fps,
                protected_point=(point[0], point[1]),
            ),
            args.steps,
        )
        write_replay_episode(args.output, episode)
        return 0
    if args.command == "audit-data":
        audit_public_replay_data(
            args.uzh_dir,
            args.anti_anno_dir,
            args.output_dir,
            UzhFpvCleaningConfig(
                max_speed_mps=args.max_speed_mps,
                max_gap_s=args.max_gap_s,
                max_bridge_points=args.max_bridge_points,
                motion_speed_threshold_mps=args.motion_threshold_mps,
                motion_padding_s=args.motion_padding_s,
            ),
        )
        return 0
    if args.command == "disturb":
        episode = read_replay_episode(args.input)
        disturbed = apply_observation_disturbance(
            episode,
            args.seed,
            args.latency_steps,
            args.dropout_probability,
            args.dropout_burst_steps,
            args.feature_noise,
        )
        write_replay_episode(args.output, disturbed)
        return 0
    if args.command == "benchmark":
        episodes = [read_replay_episode(path) for path in args.replay]
        run_replay_benchmark(
            episodes,
            args.output_dir,
            algorithms=args.algorithms,
            resources_count=args.resources,
            top_k=args.top_k,
            base_seed=args.base_seed,
            node_limit=args.node_limit,
            require_minimum_episodes=args.minimum_episodes,
            bootstrap_resamples=args.bootstrap_resamples,
        )
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

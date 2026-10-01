"""CA-HMCD 论文实验程序。

该程序把 111.docx 中的模型扩展为可复现实验：

* 异构资源、动态任务到达、目标状态变化和资源故障；
* 资源--任务适配度、时效因子、组合成功得分、互补增益和冗余软惩罚；
* 非支配候选组合剪枝，以及精确分支定界/贪婪/随机求解；
* CA-HMCD、Greedy、No-Synergy、No-Stability、Random 五种方法；
* 主实验、消融实验、规模实验，多随机种子统计；
* 输出论文可直接整理使用的 CSV 和 JSON 数据。

快速运行：
    python ca_hmcd_simulation.py --mode demo

生成论文实验数据（默认 5 个随机种子、20 个决策周期）：
    python ca_hmcd_simulation.py --mode paper --output-dir ca_hmcd_experiment
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import itertools
import json
import math
import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from ca_hmcd_replay import ReplayEpisode


RESOURCE_TYPES = ("Type-I", "Type-II", "Type-III", "Type-IV")
ALPHA_COST = .12
BETA_TIME = .08
LAMBDA_SWITCH = .10
LAMBDA_REDUNDANCY = .12
GAMMA_UNSERVED = .20
INDEPENDENT_SERVICE_WEIGHTS = {
    "risk_weighted_success": .55,
    "coverage": .20,
    "response_quality": .15,
    "cost_efficiency_coverage": .10,
}
MODEL_VERSION = "ca-hmcd-stage1-closure-v2.3"
EVALUATOR_VERSION = "complete-v3-stage1-closure"
SERVICE_ENDPOINT_VERSION = "solver-decoupled-service-v2"
EXTERNAL_BASELINE_VERSION = "highs-milp-ga-v1"


@dataclass
class Resource:
    resource_id: str
    resource_type: str
    # [R, v, c, e, q, h]：范围、速度、持续作用、消耗、成功能力、可用状态
    ability: Tuple[float, float, float, float, float, float]
    capacity: int = 1
    available: bool = True


@dataclass
class Task:
    task_id: str
    # [s, v, h, n, rho, tau]：规模、速度、高度、群集密度、风险、响应时间窗
    feature: Tuple[float, float, float, float, float, float]
    min_resources: int = 1
    max_resources: int = 3
    type_requirements: Dict[str, int] = field(default_factory=dict)
    arrival_step: int = 0
    active: bool = True


@dataclass(frozen=True)
class Candidate:
    task_index: int
    resource_indices: Tuple[int, ...]
    utility: float
    success_score: float
    physical_cost: float
    response_time: float
    complementarity_gain: float
    redundancy_penalty: float
    compatibility_score: float
    reachable: bool

    # Deprecated compatibility aliases. New experiments and manuscript text
    # should use the score/cost/penalty names above.
    @property
    def probability(self) -> float:
        return self.success_score

    @property
    def cost(self) -> float:
        return self.physical_cost

    @property
    def synergy(self) -> float:
        return self.complementarity_gain

    @property
    def redundancy(self) -> float:
        return self.redundancy_penalty

    @property
    def adaptation(self) -> float:
        return self.compatibility_score


@dataclass
class CandidateGenerationAudit:
    """Counts the three approximation stages without changing candidate values."""

    task_index: int
    total_resources: int = 0
    screened_resources: int = 0
    compatibility_screen_applied: bool = False
    enumerated_subsets: int = 0
    feasible_before_dominance: int = 0
    after_dominance: int = 0
    after_diversity: int = 0
    dominance_removed: int = 0
    diversity_removed: int = 0
    previous_candidate_feasible: bool = False
    previous_candidate_protected: bool = False


@dataclass
class AllocationResult:
    selected: Dict[int, Tuple[int, ...]]
    objective: float
    total_utility: float
    switch_penalty: float
    total_physical_cost: float
    covered_tasks: int
    mean_success_score: float
    mean_response_time: float
    switch_count: int
    solver: str
    nodes: int = 0
    runtime_ms: float = 0.0
    total_complementarity_gain: float = 0.0
    total_redundancy_penalty: float = 0.0
    mean_compatibility_score: float = 0.0
    # Unified external diagnostics, computed from the final allocation under the
    # complete model so that ablations remain comparable.
    allocated_pair_count: int = 0
    same_type_pair_ratio: float = 0.0
    capability_overlap: float = 0.0
    marginal_gain_waste: float = 0.0
    fallback: bool = False
    fallback_incumbent_objective: Optional[float] = None
    fallback_greedy_objective: Optional[float] = None
    fallback_selected_incumbent: bool = False
    node_limit_hit: bool = False
    search_upper_bound: Optional[float] = None
    fallback_optimality_gap_bound: Optional[float] = None
    fallback_gain_over_greedy: Optional[float] = None
    unexplored_frontier_nodes: int = 0

    # Deprecated compatibility aliases for existing analysis scripts.
    @property
    def total_cost(self) -> float:
        return self.total_physical_cost

    @property
    def mean_success_probability(self) -> float:
        return self.mean_success_score

    @property
    def total_synergy(self) -> float:
        return self.total_complementarity_gain

    @property
    def total_redundancy(self) -> float:
        return self.total_redundancy_penalty

    @property
    def mean_adaptation(self) -> float:
        return self.mean_compatibility_score


def clamp(x: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, x))


def scenario_parameters(name: str) -> Dict[str, float]:
    """论文场景：随机控制场景、半合成轨迹场景和机制压力场景。"""
    values = {
        "balanced": {"resources": 8, "tasks": 5, "failure_rate": .05, "volatility": .08, "arrival_horizon": 2},
        "scarce": {"resources": 8, "tasks": 8, "failure_rate": .10, "volatility": .10, "arrival_horizon": 4},
        "volatile": {"resources": 10, "tasks": 8, "failure_rate": .18, "volatility": .18, "arrival_horizon": 5},
        "semisynthetic": {"resources": 10, "tasks": 8, "failure_rate": .08, "volatility": .10, "arrival_horizon": 3},
        "redundancy_stress": {"resources": 12, "tasks": 6, "failure_rate": .03, "volatility": .04, "arrival_horizon": 1},
        "airport_corridor": {"resources": 10, "tasks": 6, "failure_rate": .07, "volatility": .10, "arrival_horizon": 2},
        "energy_facility": {"resources": 10, "tasks": 6, "failure_rate": .08, "volatility": .08, "arrival_horizon": 2},
        "public_event": {"resources": 12, "tasks": 8, "failure_rate": .10, "volatility": .14, "arrival_horizon": 3},
        "urban_corridor": {"resources": 10, "tasks": 7, "failure_rate": .12, "volatility": .16, "arrival_horizon": 3},
        "industrial_zone": {"resources": 12, "tasks": 7, "failure_rate": .09, "volatility": .11, "arrival_horizon": 2},
    }
    if name not in values:
        raise ValueError(f"未知场景 {name}，可选：{', '.join(values)}")
    return values[name].copy()


def make_scenario(seed: int = 7, resources: int = 8, tasks: int = 5,
                  arrival_horizon: int = 2) -> Tuple[List[Resource], List[Task]]:
    rng = random.Random(seed)
    rs: List[Resource] = []
    base_ability = {
        "Type-I": (0.55, 0.90, 0.45, 0.35, 0.88, 0.90),
        "Type-II": (0.90, 0.55, 0.80, 0.55, 0.72, 0.82),
        "Type-III": (0.45, 0.70, 0.35, 0.82, 0.76, 0.86),
        "Type-IV": (0.70, 0.82, 0.65, 0.48, 0.80, 0.88),
    }
    for i in range(resources):
        typ = RESOURCE_TYPES[i % len(RESOURCE_TYPES)]
        ability = tuple(clamp(v + rng.uniform(-.10, .10)) for v in base_ability[typ])
        # 面域资源允许同时服务两个任务，其他资源默认一次服务一个任务。
        capacity = 2 if typ == "Type-II" else 1
        rs.append(Resource(f"R{i + 1}", typ, ability, capacity=capacity, available=True))
    ts: List[Task] = []
    for j in range(tasks):
        feature = (rng.uniform(.2, .9), rng.uniform(.1, .95), rng.uniform(.1, .9),
                   rng.uniform(.1, .9), rng.uniform(.35, 1.0), rng.uniform(.35, .95))
        req = {"Type-I": 1} if j % 3 == 0 else ({"Type-II": 1} if j % 3 == 1 else {})
        arrival = rng.randint(0, max(0, arrival_horizon))
        ts.append(Task(f"T{j + 1}", feature, 1, 3, req, arrival, arrival == 0))
    return rs, ts


def make_semisynthetic_scenario(seed: int = 7, resources: int = 10, tasks: int = 8,
                                arrival_horizon: int = 3) -> Tuple[List[Resource], List[Task]]:
    """结构化半合成基准：任务特征来自相关轨迹，而非独立均匀抽样。

    该场景模拟公开调度/跟踪数据中常见的时间相关性：速度、规模、风险和群集密度
    由共享周期项驱动，资源能力则围绕类型中心带有固定个体差异。
    """
    rng = random.Random(seed)
    resources, _ = make_scenario(seed, resources, tasks, arrival_horizon)
    for i, resource in enumerate(resources):
        phase = 2.0 * math.pi * i / max(1, len(resources))
        values = list(resource.ability)
        values[1] = clamp(values[1] + .06 * math.sin(phase))
        values[4] = clamp(values[4] + .04 * math.cos(phase))
        resource.ability = tuple(values)  # type: ignore[assignment]
    structured_tasks: List[Task] = []
    for j in range(tasks):
        phase = 2.0 * math.pi * j / max(1, tasks)
        base = .5 + .25 * math.sin(phase + .4) + rng.uniform(-.04, .04)
        speed = clamp(.5 + .30 * math.cos(phase) + rng.uniform(-.04, .04))
        height = clamp(.5 + .22 * math.sin(phase * .7) + rng.uniform(-.04, .04))
        density = clamp(.45 + .25 * math.sin(phase + 1.2) + rng.uniform(-.04, .04))
        risk = clamp(.45 + .35 * (0.5 + .5 * math.cos(phase - .7)) + rng.uniform(-.03, .03))
        window = clamp(.65 - .20 * speed + rng.uniform(-.03, .03), .25, .95)
        req = {"Type-I": 1} if j % 3 == 0 else ({"Type-II": 1} if j % 3 == 1 else {})
        arrival = (j * 2 + seed) % (arrival_horizon + 1)
        structured_tasks.append(Task(f"S{j + 1}", (base, speed, height, density, risk, window),
                                     1, 3, req, arrival, arrival == 0))
    return resources, structured_tasks


def make_redundancy_stress_scenario(seed: int = 7, resources: int = 12, tasks: int = 6,
                                    arrival_horizon: int = 1) -> Tuple[List[Resource], List[Task]]:
    """冗余压力场景：同类资源密集、任务偏好相近，放大资源堆叠代价的作用。"""
    rng = random.Random(seed)
    rs, ts = make_scenario(seed, resources, tasks, arrival_horizon)
    base = {
        "Type-I": (0.62, 0.88, 0.48, 0.34, 0.88, 0.92),
        "Type-II": (0.78, 0.62, 0.76, 0.52, 0.76, 0.88),
    }
    for i, resource in enumerate(rs):
        typ = "Type-I" if i < int(resources * .67) else "Type-II"
        ability_values = list(base[typ])
        # 同一类型内设置两个能力簇：高速度/低持续和低速度/高持续。
        # 这使冗余代价不再与资源身份完全同质，能够检验其组合选择作用。
        if typ == "Type-I" and i % 2 == 1:
            ability_values[1] = .52
            ability_values[2] = .82
            ability_values[4] = .78
        ability = tuple(clamp(v + rng.uniform(-.025, .025)) for v in ability_values)
        resource.resource_type = typ
        resource.ability = ability  # type: ignore[assignment]
        resource.capacity = 1
    for j, task in enumerate(ts):
        s, v, h, n, _, window = task.feature
        task.feature = (clamp(.65 + rng.uniform(-.05, .05)), clamp(.55 + rng.uniform(-.05, .05)),
                        h, clamp(.35 + rng.uniform(-.05, .05)), clamp(.70 + rng.uniform(-.05, .05)),
                        clamp(window, .35, .8))
        task.type_requirements = {}
        task.min_resources = 2
        task.max_resources = 3
    return rs, ts


def make_engineering_scenario(seed: int, profile: str, resources: int = 10,
                              tasks: int = 6) -> Tuple[List[Resource], List[Task]]:
    """Create application-shaped low-altitude safety scenarios.

    These profiles are engineering validation cases, not measured field data.
    They preserve the four normalized response-resource classes while changing
    task demand structure and type requirements.
    """
    profiles = {
        "airport_corridor": {
            "patterns": [({"Type-I": 1}, 1, 2), ({"Type-IV": 1}, 1, 2),
                         ({"Type-I": 1, "Type-IV": 1}, 2, 3)],
            "base": (.58, .72, .62, .22, .82, .42),
        },
        "energy_facility": {
            "patterns": [({"Type-II": 1}, 1, 2), ({"Type-III": 1}, 1, 2),
                         ({"Type-II": 1, "Type-III": 1}, 2, 3)],
            "base": (.70, .52, .42, .38, .86, .56),
        },
        "public_event": {
            "patterns": [({"Type-II": 1}, 1, 3), ({"Type-IV": 1}, 1, 2),
                         ({"Type-II": 1, "Type-IV": 1}, 2, 3)],
            "base": (.62, .64, .55, .78, .88, .50),
        },
        "urban_corridor": {
            "patterns": [({"Type-I": 1}, 1, 2), ({"Type-IV": 1}, 1, 2),
                         ({}, 1, 3)],
            "base": (.48, .78, .58, .46, .74, .40),
        },
        "industrial_zone": {
            "patterns": [({"Type-II": 1}, 1, 2), ({"Type-III": 1}, 1, 2),
                         ({"Type-I": 1, "Type-II": 1}, 2, 3)],
            "base": (.66, .58, .48, .52, .80, .58),
        },
    }
    if profile not in profiles:
        raise ValueError(f"未知工程验证场景：{profile}")
    rng = random.Random(seed * 7919 + len(profile))
    resources_list, _ = make_scenario(seed + 37, resources, tasks, 0)
    for resource in resources_list:
        resource.available = True
    spec = profiles[profile]
    base_size, base_speed, base_height, base_density, base_risk, base_window = spec["base"]
    engineering_tasks: List[Task] = []
    for j in range(tasks):
        requirement, minimum, maximum = spec["patterns"][j % len(spec["patterns"])]
        phase = 2.0 * math.pi * j / max(1, tasks)
        feature = (
            clamp(base_size + .12 * math.sin(phase) + rng.uniform(-.05, .05)),
            clamp(base_speed + .12 * math.cos(phase) + rng.uniform(-.05, .05)),
            clamp(base_height + .10 * math.sin(phase + .5) + rng.uniform(-.04, .04)),
            clamp(base_density + .12 * math.cos(phase - .4) + rng.uniform(-.05, .05)),
            clamp(base_risk + .08 * math.sin(phase + .7) + rng.uniform(-.04, .04), .35, 1.0),
            clamp(base_window + .08 * math.cos(phase) + rng.uniform(-.04, .04), .25, .90),
        )
        arrival = (j + seed) % 3
        engineering_tasks.append(Task(
            f"{profile[:3].upper()}-{j + 1}", feature, minimum, maximum,
            dict(requirement), arrival, arrival == 0,
        ))
    return resources_list, engineering_tasks


def update_tasks(tasks: Sequence[Task], rng: random.Random, current_step: int = 0,
                 volatility: float = .08) -> None:
    for task in tasks:
        task.active = current_step >= task.arrival_step
        if not task.active:
            continue
        s, v, h, n, rho, window = task.feature
        v = clamp(v + rng.gauss(0.0, volatility))
        rho = clamp(rho + rng.gauss(0.0, volatility * .85))
        window = clamp(window + rng.gauss(0.0, volatility * .70), .2, 1.0)
        task.feature = (s, v, h, n, rho, window)


def update_resources(resources: Sequence[Resource], rng: random.Random, failure_rate: float = .05) -> None:
    for resource in resources:
        ability = list(resource.ability)
        ability[5] = clamp(ability[5] + rng.gauss(0.0, .05))
        resource.ability = tuple(ability)  # type: ignore[assignment]
        resource.available = ability[5] >= .30 and rng.random() >= failure_rate


def response_time(resource: Resource, task: Task) -> float:
    size, speed, _, _, _, _ = task.feature
    resource_speed = resource.ability[1]
    return clamp((.25 + .55 * size + .30 * speed) / (.45 + resource_speed), 0.0, 2.0)


def compatibility(resource: Resource, task: Task) -> float:
    """m_ij：四个能力维度的加权匹配。"""
    size, speed, height, density, _, _ = task.feature
    R, v, c, e, _, _ = resource.ability
    desired = {
        "Type-I": (.45 + .35 * size, speed, height, .25),
        "Type-II": (size, .45 + .35 * density, height, density),
        "Type-III": (.35 + .25 * size, speed, height, density),
        "Type-IV": (size, speed, height, .5 * density),
    }[resource.resource_type]
    values = (R, v, c, 1.0 - e)
    weights = (.28, .28, .20, .24)
    return clamp(sum(w * (1.0 - abs(a - clamp(b))) for a, b, w in zip(values, desired, weights)))


def pair_complementarity(r1: Resource, r2: Resource, task: Task, scale: float = 1.0) -> float:
    """Return a non-negative heterogeneous-capability complementarity score."""
    if r1.resource_type == r2.resource_type:
        return 0.0
    density = task.feature[3]
    type_bonus = .045 if {r1.resource_type, r2.resource_type} in (
        {"Type-I", "Type-IV"}, {"Type-II", "Type-III"}) else .025
    ability_distance = sum(abs(a - b) for a, b in zip(r1.ability[:4], r2.ability[:4])) / 4
    return scale * (type_bonus + .025 * ability_distance + .015 * density)


def pair_synergy(r1: Resource, r2: Resource, task: Task, scale: float = 1.0) -> float:
    """Deprecated alias for pair_complementarity()."""
    return pair_complementarity(r1, r2, task, scale)


def pair_redundancy(r1: Resource, r2: Resource) -> float:
    """Return an unweighted capability-overlap score for a resource pair."""
    overlap = 1.0 - sum(abs(a - b) for a, b in zip(r1.ability[:4], r2.ability[:4])) / 4
    return .045 * overlap if r1.resource_type == r2.resource_type else .012 * max(0.0, overlap)


def allocation_redundancy_diagnostics(selected: Dict[int, Tuple[int, ...]],
                                      resources: Sequence[Resource],
                                      tasks: Sequence[Task]) -> Tuple[int, float, float, float]:
    """Return post-hoc redundancy diagnostics for an allocation.

    Marginal-gain waste is the score mass counted repeatedly when standalone
    resource effects are summed instead of combined under the independent-score
    aggregation rule. It is normalized by total standalone score mass.
    """
    pair_count = 0
    same_type_pairs = 0
    overlaps: List[float] = []
    duplicated_probability = 0.0
    standalone_probability = 0.0
    for task_index, combo in selected.items():
        for left, right in itertools.combinations(combo, 2):
            pair_count += 1
            same_type_pairs += int(resources[left].resource_type == resources[right].resource_type)
            overlap = 1.0 - statistics.fmean(
                abs(a - b) for a, b in zip(resources[left].ability[:4], resources[right].ability[:4]))
            overlaps.append(clamp(overlap))
        standalone_scores = [
            _standalone_success_score(resources[resource_index], tasks[task_index])
            for resource_index in combo
        ]
        if standalone_scores:
            combined_failure = math.prod(1.0 - score for score in standalone_scores)
            combined_score = 1.0 - combined_failure
            standalone_probability += sum(standalone_scores)
            duplicated_probability += max(0.0, sum(standalone_scores) - combined_score)
    return (
        pair_count,
        same_type_pairs / pair_count if pair_count else 0.0,
        statistics.fmean(overlaps) if overlaps else 0.0,
        duplicated_probability / standalone_probability if standalone_probability > 1e-12 else 0.0,
    )


def _standalone_success_score(resource: Resource, task: Task,
                              use_adaptation: bool = True) -> float:
    """Return the normalized single-resource success endpoint."""
    match_score = compatibility(resource, task) if use_adaptation else .65
    quality = resource.ability[4] if resource.available else 0.0
    return clamp(match_score * quality * math.exp(-1.35 * response_time(resource, task)))


def evaluate_candidate(task_index: int, combo: Tuple[int, ...], resources: Sequence[Resource], tasks: Sequence[Task],
                       alpha: float = .12, beta: float = .08, use_synergy: bool = True,
                       use_redundancy: bool = True, use_adaptation: bool = True,
                       synergy_scale: float = 1.0,
                       redundancy_lambda: float = LAMBDA_REDUNDANCY) -> Candidate:
    task = tasks[task_index]
    standalone_scores: List[float] = []
    times: List[float] = []
    compatibility_values: List[float] = []
    physical_cost = 0.0
    for i in combo:
        resource = resources[i]
        match_score = compatibility(resource, task) if use_adaptation else .65
        compatibility_values.append(match_score)
        response = response_time(resource, task)
        standalone_scores.append(_standalone_success_score(resource, task, use_adaptation))
        times.append(response)
        physical_cost += .20 + .80 * resource.ability[3]
    raw_complementarity = (
        sum(pair_complementarity(resources[i], resources[k], task, synergy_scale)
            for i, k in itertools.combinations(combo, 2))
        if use_synergy else 0.0
    )
    raw_redundancy = (
        sum(pair_redundancy(resources[i], resources[k])
            for i, k in itertools.combinations(combo, 2))
        if use_redundancy else 0.0
    )
    independent_failure_score = math.prod(1.0 - score for score in standalone_scores)
    independent_success_score = 1.0 - independent_failure_score
    # Complementarity uses only the remaining headroom, keeping the score
    # bounded without treating an empirical pair bonus as additive probability.
    complementarity_gain = (
        (1.0 - independent_success_score) * (1.0 - math.exp(-raw_complementarity))
    )
    success_score = clamp(independent_success_score + complementarity_gain)
    redundancy_penalty = redundancy_lambda * raw_redundancy
    response = max(times) if times else 2.0
    utility = (
        task.feature[4] * success_score
        - alpha * physical_cost
        - beta * response
        - redundancy_penalty
    )
    return Candidate(task_index, combo, utility, success_score, physical_cost, response,
                     complementarity_gain, redundancy_penalty,
                     sum(compatibility_values) / len(compatibility_values),
                     bool(task.active) and all(t <= task.feature[5] for t in times))


def _switch_adjusted_candidate_value(candidate: Candidate,
                                     previous_combo: Optional[Tuple[int, ...]],
                                     switch_lambda: float) -> float:
    previous = set(previous_combo or ())
    current = set(candidate.resource_indices)
    return candidate.utility - switch_lambda * len(previous ^ current)


def _service_marginal_value(candidate: Candidate, task: Task,
                            previous_combo: Optional[Tuple[int, ...]],
                            switch_lambda: float,
                            gamma_unserved: float) -> float:
    """Return the exact objective gain over leaving this task unserved."""
    previous = set(previous_combo or ())
    current = set(candidate.resource_indices)
    switch_delta = len(previous ^ current) - len(previous)
    return (
        candidate.utility
        + gamma_unserved * task.feature[4]
        - switch_lambda * switch_delta
    )


def _dominates(a: Candidate, b: Candidate,
               previous_combo: Optional[Tuple[int, ...]] = None,
               switch_lambda: float = 0.0) -> bool:
    """Return whether a safely dominates b for the current task history.

    Resource-subset dominance is conservative with respect to shared capacity.
    The local objective comparison includes the exact candidate-level switching
    contribution, preventing a previous-period coalition from being removed
    merely because a smaller coalition has slightly higher static utility.
    """
    adjusted_a = _switch_adjusted_candidate_value(a, previous_combo, switch_lambda)
    adjusted_b = _switch_adjusted_candidate_value(b, previous_combo, switch_lambda)
    return (set(a.resource_indices).issubset(b.resource_indices) and
            adjusted_a >= adjusted_b - 1e-12 and
            a.success_score >= b.success_score - 1e-12 and
            a.physical_cost <= b.physical_cost + 1e-12 and
            a.response_time <= b.response_time + 1e-12 and
            (adjusted_a > adjusted_b + 1e-12 or
             a.success_score > b.success_score + 1e-12 or
             a.physical_cost < b.physical_cost - 1e-12 or
             a.response_time < b.response_time - 1e-12))


def nondominated_candidates(candidates: Sequence[Candidate],
                            previous_combo: Optional[Tuple[int, ...]] = None,
                            switch_lambda: float = 0.0) -> List[Candidate]:
    result: List[Candidate] = []
    for candidate in candidates:
        if previous_combo and candidate.resource_indices == previous_combo:
            result.append(candidate)
            continue
        if not any(
            _dominates(other, candidate, previous_combo, switch_lambda)
            for other in candidates if other is not candidate
        ):
            result.append(candidate)
    return result


def generate_candidates(task_index: int, resources: Sequence[Resource], tasks: Sequence[Task], top_k: int = 8,
                        alpha: float = .12, beta: float = .08, use_synergy: bool = True,
                        use_redundancy: bool = True, use_adaptation: bool = True,
                        synergy_scale: float = 1.0, prune: bool = True,
                        previous_combo: Optional[Tuple[int, ...]] = None,
                        switch_lambda: float = LAMBDA_SWITCH,
                        redundancy_lambda: float = LAMBDA_REDUNDANCY,
                        audit: Optional[CandidateGenerationAudit] = None,
                        apply_compatibility_screen: Optional[bool] = None,
                        apply_dominance: Optional[bool] = None,
                        apply_diversity: Optional[bool] = None) -> List[Candidate]:
    task = tasks[task_index]
    if not task.active:
        return []
    dominance_enabled = prune if apply_dominance is None else apply_dominance
    diversity_enabled = prune if apply_diversity is None else apply_diversity
    screen_enabled = prune if apply_compatibility_screen is None else apply_compatibility_screen
    if audit is not None:
        audit.total_resources = len(resources)
    all_candidates: List[Candidate] = []
    # Large-scale stress tests use a deterministic compatibility screen before
    # enumerating pairs/triples. Main-paper scenarios (at most 12 resources)
    # remain exhaustive, while the screen width tracks the retained pool size.
    resource_indices = list(range(len(resources)))
    if screen_enabled and len(resource_indices) > 16:
        screen_width = max(10, min(14, top_k + 2))
        resource_indices = sorted(resource_indices,
                                  key=lambda i: compatibility(resources[i], task),
                                  reverse=True)[:screen_width]
        if audit is not None:
            audit.compatibility_screen_applied = True
    if audit is not None:
        audit.screened_resources = len(resource_indices)
    for size in range(task.min_resources, min(task.max_resources, len(resources)) + 1):
        for combo in itertools.combinations(resource_indices, size):
            if audit is not None:
                audit.enumerated_subsets += 1
            if any(not resources[i].available for i in combo):
                continue
            type_counts = {typ: sum(resources[i].resource_type == typ for i in combo) for typ in RESOURCE_TYPES}
            if any(type_counts.get(typ, 0) < need for typ, need in task.type_requirements.items()):
                continue
            candidate = evaluate_candidate(task_index, combo, resources, tasks, alpha, beta,
                                           use_synergy, use_redundancy, use_adaptation,
                                           synergy_scale, redundancy_lambda)
            if candidate.reachable:
                all_candidates.append(candidate)
    if audit is not None:
        audit.feasible_before_dominance = len(all_candidates)
        audit.previous_candidate_feasible = any(
            candidate.resource_indices == previous_combo for candidate in all_candidates
        )
    if dominance_enabled:
        all_candidates = nondominated_candidates(
            all_candidates, previous_combo=previous_combo, switch_lambda=switch_lambda
        )
    if audit is not None:
        audit.after_dominance = len(all_candidates)
        audit.dominance_removed = audit.feasible_before_dominance - audit.after_dominance
        audit.previous_candidate_protected = bool(
            previous_combo
            and audit.previous_candidate_feasible
            and any(candidate.resource_indices == previous_combo for candidate in all_candidates)
        )
    all_candidates.sort(
        key=lambda c: (
            _switch_adjusted_candidate_value(c, previous_combo, switch_lambda),
            c.success_score,
            -c.physical_cost,
        ),
        reverse=True,
    )
    if not diversity_enabled or len(all_candidates) <= top_k:
        retained = list(all_candidates)
        if audit is not None:
            audit.after_diversity = len(retained)
            audit.diversity_removed = audit.after_dominance - audit.after_diversity
        return retained
    # 多样性剪枝：保留组合规模和资源身份代表，避免局部高效组合垄断稀缺资源。
    selected: List[Candidate] = []
    if previous_combo:
        selected.extend(c for c in all_candidates if c.resource_indices == previous_combo)
    by_size: Dict[int, List[Candidate]] = defaultdict(list)
    for candidate in all_candidates:
        by_size[len(candidate.resource_indices)].append(candidate)
    quota = max(1, top_k // max(1, len(by_size)))
    for size in sorted(by_size):
        for candidate in by_size[size][:quota]:
            if candidate not in selected and len(selected) < top_k:
                selected.append(candidate)
    for resource_index in range(len(resources)):
        representative = next((candidate for candidate in all_candidates
                               if resource_index in candidate.resource_indices), None)
        if representative is not None and representative not in selected and len(selected) < top_k:
            selected.append(representative)
    for candidate in all_candidates:
        if candidate not in selected and len(selected) < top_k:
            selected.append(candidate)
    if audit is not None:
        audit.after_diversity = len(selected)
        audit.diversity_removed = audit.after_dominance - audit.after_diversity
    return selected


def _switch_count(previous: Dict[int, Tuple[int, ...]], selected: Dict[int, Tuple[int, ...]], tasks: int) -> int:
    return sum(len(set(previous.get(j, ())) ^ set(selected.get(j, ()))) for j in range(tasks))


def unserved_penalty(tasks: Sequence[Task], selected: Dict[int, Tuple[int, ...]],
                     gamma: float = GAMMA_UNSERVED) -> float:
    """未服务高风险任务的软约束代价，避免零分配成为无代价选择。"""
    return gamma * sum(task.feature[4] for j, task in enumerate(tasks)
                       if task.active and j not in selected)


def _make_result(selected: Dict[int, Tuple[int, ...]], utility: float, physical_cost: float,
                 success_scores: Sequence[float], responses: Sequence[float], switch_count: int,
                 switch_lambda: float, solver: str, nodes: int, runtime_ms: float,
                 candidates_by_task: Optional[Dict[int, List[Candidate]]] = None) -> AllocationResult:
    selected_candidates: List[Candidate] = []
    if candidates_by_task:
        for task_index, combo in selected.items():
            selected_candidates.extend(c for c in candidates_by_task.get(task_index, [])
                                       if c.resource_indices == combo)
    return AllocationResult(selected, utility - switch_lambda * switch_count, utility,
                            switch_lambda * switch_count, physical_cost, len(selected),
                            sum(success_scores) / len(success_scores) if success_scores else 0.0,
                            sum(responses) / len(responses) if responses else 0.0,
                            switch_count, solver, nodes, runtime_ms,
                            sum(c.complementarity_gain for c in selected_candidates),
                            sum(c.redundancy_penalty for c in selected_candidates),
                            (sum(c.compatibility_score for c in selected_candidates) / len(selected_candidates)
                             if selected_candidates else 0.0))


def _is_fallback_solver(solver: str) -> bool:
    return solver in {
        "GreedyFallback",
        "IncumbentFallback",
        "MILPGreedyFallback",
        "MILPIncumbentFallback",
    }


def retained_pool_suffix_upper_bounds(
    tasks: Sequence[Task],
    order: Sequence[int],
    candidates: Dict[int, List[Candidate]],
    gamma_unserved: float,
) -> List[float]:
    """Return an optimistic suffix bound for the retained candidate pool.

    For each unexpanded task, the bound takes the better of no service (zero
    gain relative to the fixed all-unserved penalty) and the largest retained
    candidate utility plus the avoided unserved-task penalty. It deliberately
    ignores capacity, physical-budget, and switching penalties, all of which
    can only reduce the objective of a feasible completion.
    """
    suffix = [0.0] * (len(order) + 1)
    for pos in range(len(order) - 1, -1, -1):
        task_index = order[pos]
        risk_bonus = (
            gamma_unserved * tasks[task_index].feature[4]
            if tasks[task_index].active else 0.0
        )
        best_remaining_gain = max(
            [0.0]
            + [
                candidate.utility + risk_bonus
                for candidate in candidates.get(task_index, [])
            ]
        )
        suffix[pos] = suffix[pos + 1] + best_remaining_gain
    return suffix


def retained_pool_node_upper_bound(
    accumulated_utility: float,
    accumulated_service_bonus: float,
    total_risk_penalty: float,
    suffix_upper_bound: float,
) -> float:
    """Return the admissible objective bound at one retained-pool node."""
    return (
        accumulated_utility
        + accumulated_service_bonus
        - total_risk_penalty
        + suffix_upper_bound
    )


def solve_global(tasks: Sequence[Task], resources: Sequence[Resource], candidates: Dict[int, List[Candidate]],
                 previous: Dict[int, Tuple[int, ...]], switch_lambda: float = .10,
                 budget: Optional[float] = None, exact: bool = True, node_limit: int = 100000,
                 gamma_unserved: float = GAMMA_UNSERVED) -> AllocationResult:
    """候选组合上的 0-1 分配，节点受限时返回当前最好可行解。"""
    started = time.perf_counter()
    if budget is None:
        budget = sum((.20 + .80 * r.ability[3]) * r.capacity for r in resources) * .72
    if not exact:
        return greedy_allocate(tasks, resources, candidates, previous, switch_lambda, budget, "Greedy", started,
                               gamma_unserved=gamma_unserved)
    if node_limit < 0:
        raise ValueError("node_limit 必须为非负整数")
    order = sorted(range(len(tasks)), key=lambda j: tasks[j].feature[4], reverse=True)
    options = {j: list(candidates.get(j, [])) + [None] for j in order}
    upper = retained_pool_suffix_upper_bounds(
        tasks, order, candidates, gamma_unserved
    )
    total_risk_penalty = gamma_unserved * sum(task.feature[4] for task in tasks if task.active)
    greedy = greedy_allocate(
        tasks, resources, candidates, previous, switch_lambda, budget,
        "GreedyWarmStart", started, gamma_unserved=gamma_unserved
    )
    best = replace(
        greedy,
        solver="BranchBound",
        nodes=0,
        runtime_ms=0.0,
        fallback=False,
    )
    nodes = 0
    node_limit_hit = False
    frontier_upper_bound = -math.inf
    unexplored_frontier_nodes = 0

    def objective_upper_bound(pos: int, utility: float, service_bonus: float) -> float:
        return retained_pool_node_upper_bound(
            utility,
            service_bonus,
            total_risk_penalty,
            upper[pos],
        )

    def search(pos: int, usage: List[int], selected: Dict[int, Tuple[int, ...]], utility: float,
               service_bonus: float, physical_cost: float,
               success_scores: List[float], responses: List[float]) -> None:
        nonlocal best, nodes, node_limit_hit, frontier_upper_bound, unexplored_frontier_nodes
        bound = objective_upper_bound(pos, utility, service_bonus)
        if bound < best.objective - 1e-12:
            return
        if nodes >= node_limit:
            node_limit_hit = True
            frontier_upper_bound = max(frontier_upper_bound, bound)
            unexplored_frontier_nodes += 1
            return
        nodes += 1
        if pos == len(order):
            switches = _switch_count(previous, selected, len(tasks))
            evaluated_utility = utility - unserved_penalty(tasks, selected, gamma_unserved)
            result = _make_result(selected.copy(), evaluated_utility, physical_cost,
                                  success_scores, responses,
                                  switches, switch_lambda, "BranchBound", nodes,
                                  (time.perf_counter() - started) * 1000, candidates)
            if result.objective > best.objective + 1e-12:
                best = result
            return
        task_index = order[pos]
        for candidate in options[task_index]:
            if candidate is None:
                search(pos + 1, usage, selected, utility, service_bonus, physical_cost,
                       success_scores, responses)
                continue
            if physical_cost + candidate.physical_cost > budget:
                continue
            if any(usage[i] + 1 > resources[i].capacity for i in candidate.resource_indices):
                continue
            for i in candidate.resource_indices:
                usage[i] += 1
            selected[task_index] = candidate.resource_indices
            bonus = gamma_unserved * tasks[task_index].feature[4] if tasks[task_index].active else 0.0
            search(pos + 1, usage, selected, utility + candidate.utility, service_bonus + bonus,
                   physical_cost + candidate.physical_cost,
                   success_scores + [candidate.success_score],
                   responses + [candidate.response_time])
            selected.pop(task_index, None)
            for i in candidate.resource_indices:
                usage[i] -= 1

    search(0, [0] * len(resources), {}, 0.0, 0.0, 0.0, [], [])
    runtime_ms = (time.perf_counter() - started) * 1000
    if node_limit_hit:
        selected_incumbent = best.objective > greedy.objective + 1e-12
        search_upper_bound = max(
            best.objective,
            frontier_upper_bound if math.isfinite(frontier_upper_bound) else best.objective,
        )
        return replace(
            best,
            solver="IncumbentFallback" if selected_incumbent else "GreedyFallback",
            nodes=nodes,
            runtime_ms=runtime_ms,
            fallback=True,
            fallback_incumbent_objective=best.objective,
            fallback_greedy_objective=greedy.objective,
            fallback_selected_incumbent=selected_incumbent,
            node_limit_hit=True,
            search_upper_bound=search_upper_bound,
            fallback_optimality_gap_bound=max(0.0, search_upper_bound - best.objective),
            fallback_gain_over_greedy=max(0.0, best.objective - greedy.objective),
            unexplored_frontier_nodes=unexplored_frontier_nodes,
        )
    return replace(
        best,
        solver="BranchBound",
        nodes=nodes,
        runtime_ms=runtime_ms,
        node_limit_hit=False,
        search_upper_bound=best.objective,
        fallback_optimality_gap_bound=0.0,
        fallback_gain_over_greedy=0.0,
        unexplored_frontier_nodes=0,
    )


def milp_allocate(
    tasks: Sequence[Task],
    resources: Sequence[Resource],
    candidates: Dict[int, List[Candidate]],
    previous: Dict[int, Tuple[int, ...]],
    switch_lambda: float,
    budget: float,
    node_limit: int = 4000,
    gamma_unserved: float = GAMMA_UNSERVED,
) -> AllocationResult:
    """Solve the complete candidate-pool allocation with SciPy/HiGHS MILP."""
    started = time.perf_counter()
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_array
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "HiGHS-MILP requires a working NumPy/SciPy installation"
        ) from exc
    if node_limit < 1:
        raise ValueError("node_limit 必须为正整数")

    variables = [
        (task_index, candidate)
        for task_index in range(len(tasks))
        for candidate in candidates.get(task_index, [])
    ]
    if not variables:
        switches = _switch_count(previous, {}, len(tasks))
        utility = -unserved_penalty(tasks, {}, gamma_unserved)
        return _make_result(
            {}, utility, 0.0, [], [], switches, switch_lambda,
            "HiGHSMILP", 0, (time.perf_counter() - started) * 1000,
            candidates,
        )

    row_indices: List[int] = []
    column_indices: List[int] = []
    coefficients: List[float] = []
    resource_offset = len(tasks)
    budget_row = resource_offset + len(resources)
    increments: List[float] = []
    for column, (task_index, candidate) in enumerate(variables):
        increments.append(
            _service_marginal_value(
                candidate,
                tasks[task_index],
                previous.get(task_index),
                switch_lambda,
                gamma_unserved,
            )
        )
        row_indices.append(task_index)
        column_indices.append(column)
        coefficients.append(1.0)
        for resource_index in candidate.resource_indices:
            row_indices.append(resource_offset + resource_index)
            column_indices.append(column)
            coefficients.append(1.0)
        row_indices.append(budget_row)
        column_indices.append(column)
        coefficients.append(candidate.physical_cost)

    constraint_count = budget_row + 1
    matrix = coo_array(
        (
            np.asarray(coefficients, dtype=float),
            (
                np.asarray(row_indices, dtype=np.int32),
                np.asarray(column_indices, dtype=np.int32),
            ),
        ),
        shape=(constraint_count, len(variables)),
    ).tocsc()
    upper_bounds = np.asarray(
        [1.0] * len(tasks)
        + [float(resource.capacity) for resource in resources]
        + [float(budget)],
        dtype=float,
    )
    constraints = LinearConstraint(
        matrix,
        np.full(constraint_count, -np.inf, dtype=float),
        upper_bounds,
    )
    optimization = milp(
        c=-np.asarray(increments, dtype=float),
        integrality=np.ones(len(variables), dtype=np.int8),
        bounds=Bounds(
            np.zeros(len(variables), dtype=float),
            np.ones(len(variables), dtype=float),
        ),
        constraints=constraints,
        options={
            "node_limit": int(node_limit),
            "mip_rel_gap": 0.0,
            "presolve": True,
        },
    )

    selected: Dict[int, Tuple[int, ...]] = {}
    selected_candidates: List[Candidate] = []
    if optimization.x is not None:
        for value, (task_index, candidate) in zip(
            optimization.x, variables
        ):
            if value > 0.5:
                selected[task_index] = candidate.resource_indices
                selected_candidates.append(candidate)
    physical_cost = sum(candidate.physical_cost for candidate in selected_candidates)
    utility = (
        sum(candidate.utility for candidate in selected_candidates)
        - unserved_penalty(tasks, selected, gamma_unserved)
    )
    switches = _switch_count(previous, selected, len(tasks))
    incumbent = _make_result(
        selected,
        utility,
        physical_cost,
        [candidate.success_score for candidate in selected_candidates],
        [candidate.response_time for candidate in selected_candidates],
        switches,
        switch_lambda,
        "HiGHSMILP",
        int(getattr(optimization, "mip_node_count", 0) or 0),
        (time.perf_counter() - started) * 1000,
        candidates,
    )
    base_objective = (
        -unserved_penalty(tasks, {}, gamma_unserved)
        - switch_lambda * _switch_count(previous, {}, len(tasks))
    )
    if optimization.fun is not None:
        linearized_objective = base_objective - float(optimization.fun)
        if abs(linearized_objective - incumbent.objective) > 1e-7:
            raise RuntimeError(
                "HiGHS-MILP objective does not match the registered value model"
            )

    if int(optimization.status) == 0:
        return replace(
            incumbent,
            search_upper_bound=incumbent.objective,
            fallback_optimality_gap_bound=0.0,
            fallback_gain_over_greedy=0.0,
        )

    greedy = greedy_allocate(
        tasks,
        resources,
        candidates,
        previous,
        switch_lambda,
        budget,
        "MILPGreedyWarmStart",
        started,
        gamma_unserved=gamma_unserved,
    )
    use_incumbent = (
        optimization.x is not None
        and incumbent.objective > greedy.objective + 1e-12
    )
    chosen = incumbent if use_incumbent else greedy
    dual_bound = getattr(optimization, "mip_dual_bound", None)
    search_upper_bound = chosen.objective
    if dual_bound is not None and math.isfinite(float(dual_bound)):
        search_upper_bound = max(
            search_upper_bound,
            base_objective - float(dual_bound),
        )
    return replace(
        chosen,
        solver=(
            "MILPIncumbentFallback"
            if use_incumbent else "MILPGreedyFallback"
        ),
        nodes=int(getattr(optimization, "mip_node_count", 0) or 0),
        runtime_ms=(time.perf_counter() - started) * 1000,
        fallback=True,
        fallback_incumbent_objective=(
            incumbent.objective if optimization.x is not None else None
        ),
        fallback_greedy_objective=greedy.objective,
        fallback_selected_incumbent=use_incumbent,
        node_limit_hit=int(optimization.status) == 1,
        search_upper_bound=search_upper_bound,
        fallback_optimality_gap_bound=max(
            0.0, search_upper_bound - chosen.objective
        ),
        fallback_gain_over_greedy=max(
            0.0, chosen.objective - greedy.objective
        ),
    )


def genetic_allocate(
    tasks: Sequence[Task],
    resources: Sequence[Resource],
    candidates: Dict[int, List[Candidate]],
    previous: Dict[int, Tuple[int, ...]],
    switch_lambda: float,
    budget: float,
    rng: random.Random,
    evaluation_budget: int = 4000,
    population_size: int = 64,
    gamma_unserved: float = GAMMA_UNSERVED,
) -> AllocationResult:
    """Standard genetic search over the complete candidate pool."""
    started = time.perf_counter()
    if evaluation_budget < 1:
        raise ValueError("evaluation_budget 必须为正整数")
    population_size = max(4, min(population_size, evaluation_budget))
    task_count = len(tasks)

    def repair(genes: Sequence[int]) -> Tuple[int, ...]:
        ranked: List[Tuple[float, int, Candidate, int]] = []
        for task_index, gene in enumerate(genes):
            options = candidates.get(task_index, [])
            if gene < 0 or gene >= len(options):
                continue
            candidate = options[gene]
            marginal = _service_marginal_value(
                candidate,
                tasks[task_index],
                previous.get(task_index),
                switch_lambda,
                gamma_unserved,
            )
            ranked.append((marginal, task_index, candidate, gene))
        repaired = [-1] * task_count
        usage = [0] * len(resources)
        physical_cost = 0.0
        for marginal, task_index, candidate, gene in sorted(
            ranked,
            key=lambda item: (
                -item[0],
                item[1],
                item[2].resource_indices,
            ),
        ):
            if marginal <= 1e-12:
                continue
            if physical_cost + candidate.physical_cost > budget + 1e-12:
                continue
            if any(
                usage[index] + 1 > resources[index].capacity
                for index in candidate.resource_indices
            ):
                continue
            repaired[task_index] = gene
            physical_cost += candidate.physical_cost
            for index in candidate.resource_indices:
                usage[index] += 1
        return tuple(repaired)

    def decode(genes: Sequence[int]) -> Tuple[
        Dict[int, Tuple[int, ...]], List[Candidate]
    ]:
        selected: Dict[int, Tuple[int, ...]] = {}
        selected_candidates: List[Candidate] = []
        for task_index, gene in enumerate(genes):
            if gene < 0:
                continue
            candidate = candidates[task_index][gene]
            selected[task_index] = candidate.resource_indices
            selected_candidates.append(candidate)
        return selected, selected_candidates

    evaluations = 0

    def evaluate(genes: Sequence[int]) -> Tuple[float, Tuple[int, ...]]:
        nonlocal evaluations
        repaired = repair(genes)
        selected, selected_candidates = decode(repaired)
        utility = (
            sum(candidate.utility for candidate in selected_candidates)
            - unserved_penalty(tasks, selected, gamma_unserved)
        )
        objective = (
            utility
            - switch_lambda * _switch_count(previous, selected, task_count)
        )
        evaluations += 1
        return objective, repaired

    greedy = greedy_allocate(
        tasks,
        resources,
        candidates,
        previous,
        switch_lambda,
        budget,
        "GeneticGreedyWarmStart",
        started,
        gamma_unserved=gamma_unserved,
    )
    greedy_genes = [-1] * task_count
    for task_index, combo in greedy.selected.items():
        greedy_genes[task_index] = next(
            (
                index
                for index, candidate in enumerate(candidates[task_index])
                if candidate.resource_indices == combo
            ),
            -1,
        )
    previous_genes = [-1] * task_count
    for task_index, combo in previous.items():
        previous_genes[task_index] = next(
            (
                index
                for index, candidate in enumerate(
                    candidates.get(task_index, [])
                )
                if candidate.resource_indices == combo
            ),
            -1,
        )

    raw_population: List[Tuple[int, ...]] = [
        tuple(greedy_genes),
        tuple(previous_genes),
        tuple([-1] * task_count),
    ]
    while len(raw_population) < population_size:
        genes = []
        for task_index in range(task_count):
            option_count = len(candidates.get(task_index, []))
            if option_count == 0 or rng.random() < 0.25:
                genes.append(-1)
            else:
                rank_limit = min(option_count, 12)
                genes.append(rng.randrange(rank_limit))
        raw_population.append(tuple(genes))

    population: List[Tuple[float, Tuple[int, ...]]] = []
    for genes in raw_population:
        if evaluations >= evaluation_budget:
            break
        population.append(evaluate(genes))
    best = max(population, key=lambda item: item[0])

    def tournament() -> Tuple[int, ...]:
        contestants = rng.sample(population, k=min(3, len(population)))
        return max(contestants, key=lambda item: item[0])[1]

    mutation_rate = max(0.08, 1.0 / max(1, task_count))
    while evaluations < evaluation_budget:
        population.sort(key=lambda item: item[0], reverse=True)
        next_population = population[:2]
        while (
            len(next_population) < population_size
            and evaluations < evaluation_budget
        ):
            first = tournament()
            second = tournament()
            child = [
                first[index] if rng.random() < 0.5 else second[index]
                for index in range(task_count)
            ]
            for task_index in range(task_count):
                if rng.random() >= mutation_rate:
                    continue
                option_count = len(candidates.get(task_index, []))
                if option_count == 0 or rng.random() < 0.25:
                    child[task_index] = -1
                else:
                    child[task_index] = rng.randrange(option_count)
            evaluated = evaluate(child)
            next_population.append(evaluated)
            if evaluated[0] > best[0] + 1e-12:
                best = evaluated
        population = next_population

    selected, selected_candidates = decode(best[1])
    physical_cost = sum(candidate.physical_cost for candidate in selected_candidates)
    utility = (
        sum(candidate.utility for candidate in selected_candidates)
        - unserved_penalty(tasks, selected, gamma_unserved)
    )
    switches = _switch_count(previous, selected, task_count)
    result = _make_result(
        selected,
        utility,
        physical_cost,
        [candidate.success_score for candidate in selected_candidates],
        [candidate.response_time for candidate in selected_candidates],
        switches,
        switch_lambda,
        "GeneticAlgorithm",
        evaluations,
        (time.perf_counter() - started) * 1000,
        candidates,
    )
    if result.objective + 1e-9 < greedy.objective:
        raise RuntimeError("Genetic search lost its registered greedy warm start")
    return result


def greedy_allocate(tasks: Sequence[Task], resources: Sequence[Resource], candidates: Dict[int, List[Candidate]],
                   previous: Dict[int, Tuple[int, ...]], switch_lambda: float, budget: float,
                   solver_name: str = "Greedy", started: Optional[float] = None, nodes: int = 0,
                   gamma_unserved: float = GAMMA_UNSERVED) -> AllocationResult:
    started = started or time.perf_counter()
    usage = [0] * len(resources)
    selected: Dict[int, Tuple[int, ...]] = {}
    utility = 0.0
    physical_cost = 0.0
    success_scores: List[float] = []
    responses: List[float] = []
    ranked: List[Tuple[float, int, Candidate]] = []
    for j, values in candidates.items():
        for candidate in values:
            marginal = _service_marginal_value(
                candidate, tasks[j], previous.get(j), switch_lambda, gamma_unserved
            )
            ranked.append((marginal, j, candidate))
    for marginal, j, candidate in sorted(
        ranked,
        key=lambda item: (-item[0], item[1], item[2].resource_indices),
    ):
        if marginal <= 1e-12:
            continue
        if j in selected or physical_cost + candidate.physical_cost > budget:
            continue
        if any(usage[i] + 1 > resources[i].capacity for i in candidate.resource_indices):
            continue
        for i in candidate.resource_indices:
            usage[i] += 1
        selected[j] = candidate.resource_indices
        utility += candidate.utility
        physical_cost += candidate.physical_cost
        success_scores.append(candidate.success_score)
        responses.append(candidate.response_time)
    switches = _switch_count(previous, selected, len(tasks))
    utility -= unserved_penalty(tasks, selected, gamma_unserved)
    return _make_result(selected, utility, physical_cost, success_scores, responses, switches, switch_lambda,
                        solver_name, nodes, (time.perf_counter() - started) * 1000, candidates)


def auction_allocate(tasks: Sequence[Task], resources: Sequence[Resource],
                     candidates: Dict[int, List[Candidate]],
                     previous: Dict[int, Tuple[int, ...]], switch_lambda: float,
                     budget: float, alpha: float = ALPHA_COST,
                     beta: float = BETA_TIME, gamma_unserved: float = GAMMA_UNSERVED,
                     solver_name: str = "ExternalAuction") -> AllocationResult:
    """Task--resource auction baseline using standalone service bids.

    The baseline does not use coalition complementarity or redundancy penalty.
    It ranks feasible bids by risk-weighted standalone service benefit, cost,
    response time, and switching burden, then clears each resource capacity
    greedily. This gives an external, interpretable comparison point.
    """
    started = time.perf_counter()
    usage = [0] * len(resources)
    selected: Dict[int, Tuple[int, ...]] = {}
    utility = 0.0
    physical_cost = 0.0
    success_scores: List[float] = []
    responses: List[float] = []
    bids: List[Tuple[float, int, Candidate]] = []
    for task_index, values in candidates.items():
        task = tasks[task_index]
        for candidate in values:
            standalone = 1.0 - math.prod(
                1.0 - _standalone_success_score(resources[i], task)
                for i in candidate.resource_indices
            )
            switch_delta = len(
                set(previous.get(task_index, ())) ^ set(candidate.resource_indices)
            ) - len(previous.get(task_index, ()))
            scarcity_bonus = sum(
                1.0 / max(1, resources[i].capacity)
                for i in candidate.resource_indices
            )
            bid = (
                task.feature[4] * standalone
                - alpha * candidate.physical_cost
                - beta * candidate.response_time
                - switch_lambda * switch_delta
                + .005 * scarcity_bonus
            )
            marginal = bid + gamma_unserved * task.feature[4]
            bids.append((marginal, task_index, candidate))
    for marginal, task_index, candidate in sorted(
        bids, key=lambda item: (-item[0], item[1], item[2].resource_indices)
    ):
        if marginal <= 1e-12 or task_index in selected:
            continue
        if physical_cost + candidate.physical_cost > budget:
            continue
        if any(usage[i] + 1 > resources[i].capacity for i in candidate.resource_indices):
            continue
        for i in candidate.resource_indices:
            usage[i] += 1
        selected[task_index] = candidate.resource_indices
        task = tasks[task_index]
        standalone = 1.0 - math.prod(
            1.0 - _standalone_success_score(resources[i], task)
            for i in candidate.resource_indices
        )
        utility += task.feature[4] * standalone - alpha * candidate.physical_cost - beta * candidate.response_time
        physical_cost += candidate.physical_cost
        success_scores.append(standalone)
        responses.append(candidate.response_time)
    switches = _switch_count(previous, selected, len(tasks))
    utility -= unserved_penalty(tasks, selected, gamma_unserved)
    return _make_result(selected, utility, physical_cost, success_scores, responses,
                        switches, switch_lambda, solver_name, 0,
                        (time.perf_counter() - started) * 1000, candidates)


def random_allocate(tasks: Sequence[Task], resources: Sequence[Resource], candidates: Dict[int, List[Candidate]],
                    previous: Dict[int, Tuple[int, ...]], budget: float, rng: random.Random,
                    gamma_unserved: float = GAMMA_UNSERVED) -> AllocationResult:
    started = time.perf_counter()
    order = list(candidates)
    rng.shuffle(order)
    usage = [0] * len(resources)
    selected: Dict[int, Tuple[int, ...]] = {}
    utility = 0.0
    physical_cost = 0.0
    success_scores: List[float] = []
    responses: List[float] = []
    for j in order:
        options = [c for c in candidates[j] if physical_cost + c.physical_cost <= budget and
                   all(usage[i] + 1 <= resources[i].capacity for i in c.resource_indices)]
        if not options or rng.random() < .25:
            continue
        candidate = rng.choice(options)
        for i in candidate.resource_indices:
            usage[i] += 1
        selected[j] = candidate.resource_indices
        utility += candidate.utility
        physical_cost += candidate.physical_cost
        success_scores.append(candidate.success_score)
        responses.append(candidate.response_time)
    switches = _switch_count(previous, selected, len(tasks))
    utility -= unserved_penalty(tasks, selected, gamma_unserved)
    return _make_result(selected, utility, physical_cost, success_scores, responses, switches, 0.0,
                        "Random", 0, (time.perf_counter() - started) * 1000, candidates)


def evaluate_allocation(selected: Dict[int, Tuple[int, ...]], resources: Sequence[Resource],
                        tasks: Sequence[Task], previous: Dict[int, Tuple[int, ...]],
                        synergy_scale: float = 1.0,
                        budget: Optional[float] = None,
                        use_synergy: bool = True,
                        use_redundancy: bool = True,
                        use_adaptation: bool = True,
                        switch_lambda: float = LAMBDA_SWITCH,
                        gamma_unserved: float = GAMMA_UNSERVED,
                        alpha: float = ALPHA_COST,
                        beta: float = BETA_TIME,
                        redundancy_lambda: float = LAMBDA_REDUNDANCY) -> Tuple[AllocationResult, int]:
    """用统一完整模型对任意算法的决策进行外部评价。"""
    candidates: Dict[int, List[Candidate]] = {}
    utility = 0.0
    physical_cost = 0.0
    success_scores: List[float] = []
    responses: List[float] = []
    violations = 0
    valid_selected: Dict[int, Tuple[int, ...]] = {}
    usage = [0] * len(resources)
    for task_index, combo in selected.items():
        candidate = evaluate_candidate(
            task_index, combo, resources, tasks, alpha, beta,
            use_synergy, use_redundancy, use_adaptation, synergy_scale,
            redundancy_lambda
        )
        candidates[task_index] = [candidate]
        type_counts = {typ: sum(resources[i].resource_type == typ for i in combo)
                       for typ in RESOURCE_TYPES}
        task = tasks[task_index]
        invalid_structure = (
            len(set(combo)) != len(combo) or
            not task.min_resources <= len(combo) <= task.max_resources or
            any(type_counts.get(typ, 0) < need for typ, need in task.type_requirements.items())
        )
        if (invalid_structure or not candidate.reachable or
                any(not resources[i].available for i in combo) or
                any(usage[i] + 1 > resources[i].capacity for i in combo)):
            violations += 1
            continue
        valid_selected[task_index] = combo
        for i in combo:
            usage[i] += 1
        utility += candidate.utility
        physical_cost += candidate.physical_cost
        success_scores.append(candidate.success_score)
        responses.append(candidate.response_time)
    if budget is not None and physical_cost > budget + 1e-12:
        violations += 1
    utility -= unserved_penalty(tasks, valid_selected, gamma_unserved)
    switches = _switch_count(previous, valid_selected, len(tasks))
    result = _make_result(valid_selected, utility, physical_cost, success_scores, responses, switches,
                          switch_lambda, "ExternalEvaluation", 0, 0.0, candidates)
    (result.allocated_pair_count,
     result.same_type_pair_ratio,
     result.capability_overlap,
     result.marginal_gain_waste) = allocation_redundancy_diagnostics(
         valid_selected, resources, tasks)
    return result, violations


def evaluate_independent_allocation(selected: Dict[int, Tuple[int, ...]],
                                    resources: Sequence[Resource],
                                    tasks: Sequence[Task],
                                    budget: Optional[float] = None) -> Dict[str, object]:
    """Evaluate a final allocation with an endpoint-only engineering score.

    This evaluator is deliberately not the optimization objective. It uses
    standalone response success, risk-weighted task coverage, deadline quality,
    physical expenditure, and feasibility. It excludes complementarity,
    redundancy penalty, switching cost, candidate utility, and solver status.
    """
    active_tasks = [j for j, task in enumerate(tasks) if task.active]
    active_risk = sum(tasks[j].feature[4] for j in active_tasks)
    usage = [0] * len(resources)
    valid_selected: Dict[int, Tuple[int, ...]] = {}
    risk_success = 0.0
    response_quality: List[float] = []
    response_values: List[float] = []
    physical_cost = 0.0
    violations = 0
    for task_index, combo in selected.items():
        if task_index < 0 or task_index >= len(tasks):
            violations += 1
            continue
        task = tasks[task_index]
        invalid_indices = any(i < 0 or i >= len(resources) for i in combo)
        if invalid_indices:
            violations += 1
            continue
        type_counts = {
            typ: sum(resources[i].resource_type == typ for i in combo)
            for typ in RESOURCE_TYPES
        }
        invalid = (
            len(set(combo)) != len(combo)
            or not task.active
            or not task.min_resources <= len(combo) <= task.max_resources
            or any(type_counts.get(typ, 0) < need
                   for typ, need in task.type_requirements.items())
            or any(not resources[i].available for i in combo)
            or any(usage[i] + 1 > resources[i].capacity for i in combo)
        )
        if invalid:
            violations += 1
            continue
        for i in combo:
            usage[i] += 1
        standalone = [
            _standalone_success_score(resources[i], task)
            for i in combo
        ]
        success = 1.0 - math.prod(1.0 - value for value in standalone)
        response = max(response_time(resources[i], task) for i in combo)
        deadline = max(task.feature[5], 1e-9)
        tardiness = max(0.0, response - deadline) / deadline
        risk_success += task.feature[4] * success
        response_quality.append(clamp(1.0 - tardiness))
        response_values.append(response)
        physical_cost += sum(.20 + .80 * resources[i].ability[3] for i in combo)
        valid_selected[task_index] = combo
    if budget is not None and physical_cost > budget + 1e-12:
        violations += 1
    coverage = len(valid_selected) / max(1, len(active_tasks))
    risk_weighted_success = risk_success / max(active_risk, 1e-12)
    mean_response_quality = statistics.fmean(response_quality) if response_quality else 0.0
    response_mean = statistics.fmean(response_values) if response_values else 0.0
    cost_efficiency = clamp(
        1.0 - physical_cost / max(budget if budget is not None else physical_cost, 1e-9)
    )
    feasible = 1.0 if violations == 0 else 0.0
    service_score = feasible * clamp(
        INDEPENDENT_SERVICE_WEIGHTS["risk_weighted_success"] * risk_weighted_success
        + INDEPENDENT_SERVICE_WEIGHTS["coverage"] * coverage
        + INDEPENDENT_SERVICE_WEIGHTS["response_quality"] * mean_response_quality
        + INDEPENDENT_SERVICE_WEIGHTS["cost_efficiency_coverage"] * cost_efficiency * coverage
    )
    pair_count, same_type_ratio, overlap, waste = allocation_redundancy_diagnostics(
        valid_selected, resources, tasks
    )
    return {
        "independent_service_score": service_score,
        "independent_risk_weighted_success": risk_weighted_success,
        "independent_coverage": coverage,
        "independent_response_quality": mean_response_quality,
        "independent_response_time": response_mean,
        "independent_cost_efficiency": cost_efficiency,
        "independent_physical_cost": physical_cost,
        "independent_feasibility": feasible,
        "independent_violations": float(violations),
        "independent_allocated_pair_count": float(pair_count),
        "independent_same_type_pair_ratio": same_type_ratio,
        "independent_capability_overlap": overlap,
        "independent_marginal_gain_waste": waste,
        "independent_evaluator": SERVICE_ENDPOINT_VERSION,
        "independent_evaluator_weights": json.dumps(
            INDEPENDENT_SERVICE_WEIGHTS, sort_keys=True, separators=(",", ":")
        ),
        "independent_evaluator_reused_inputs": (
            "task risk; standalone success score; response time; physical cost"
        ),
        "independent_evaluator_excluded_terms": (
            "complementarity; redundancy penalty; switching penalty; "
            "candidate utility; solver state"
        ),
    }


def safety_repair(selected: Dict[int, Tuple[int, ...]], resources: Sequence[Resource], tasks: Sequence[Task],
                  previous: Dict[int, Tuple[int, ...]], top_k: int = 12,
                  budget: Optional[float] = None,
                  synergy_scale: float = 1.0,
                  use_synergy: bool = True,
                   use_redundancy: bool = True,
                   use_adaptation: bool = True,
                   switch_lambda: float = LAMBDA_SWITCH,
                   gamma_unserved: float = GAMMA_UNSERVED,
                   alpha: float = ALPHA_COST,
                   beta: float = BETA_TIME,
                   redundancy_lambda: float = LAMBDA_REDUNDANCY) -> Tuple[Dict[int, Tuple[int, ...]], int]:
    """按真实可达性移除失效组合，并用边际目标增益为未服务任务快速补位。"""
    repaired: Dict[int, Tuple[int, ...]] = {}
    usage = [0] * len(resources)
    removed = 0
    total_physical_cost = 0.0
    existing: List[Tuple[float, int, Tuple[int, ...], Candidate]] = []
    for task_index, combo in selected.items():
        candidate = evaluate_candidate(
            task_index, combo, resources, tasks, alpha, beta,
            use_synergy, use_redundancy, use_adaptation, synergy_scale,
            redundancy_lambda
        )
        marginal = _service_marginal_value(
            candidate, tasks[task_index], previous.get(task_index),
            switch_lambda, gamma_unserved
        )
        existing.append((marginal, task_index, combo, candidate))
    for _, task_index, combo, candidate in sorted(existing, key=lambda item: item[0], reverse=True):
        if (not candidate.reachable or any(not resources[i].available for i in combo) or
                any(usage[i] + 1 > resources[i].capacity for i in combo) or
                (budget is not None and total_physical_cost + candidate.physical_cost > budget)):
            removed += 1
            continue
        repaired[task_index] = combo
        total_physical_cost += candidate.physical_cost
        for i in combo:
            usage[i] += 1
    candidates = {j: generate_candidates(
                      j, resources, tasks, top_k, alpha, beta,
                      use_synergy, use_redundancy, use_adaptation, synergy_scale,
                      previous_combo=previous.get(j) if switch_lambda > 0 else None,
                      switch_lambda=switch_lambda,
                      redundancy_lambda=redundancy_lambda)
                  for j in range(len(tasks)) if j not in repaired and tasks[j].active}
    ranked: List[Tuple[float, int, Candidate]] = []
    for j, values in candidates.items():
        for candidate in values:
            marginal = _service_marginal_value(
                candidate, tasks[j], previous.get(j), switch_lambda, gamma_unserved
            )
            ranked.append((marginal, j, candidate))
    for marginal, j, candidate in sorted(ranked, key=lambda item: item[0], reverse=True):
        if marginal <= 0.0 or j in repaired:
            continue
        if (any(usage[i] + 1 > resources[i].capacity for i in candidate.resource_indices) or
                (budget is not None and total_physical_cost + candidate.physical_cost > budget)):
            continue
        repaired[j] = candidate.resource_indices
        total_physical_cost += candidate.physical_cost
        for i in candidate.resource_indices:
            usage[i] += 1
    return repaired, removed


def noisy_perception(resources: Sequence[Resource], tasks: Sequence[Task], noise: float,
                     rng: random.Random) -> Tuple[List[Resource], List[Task]]:
    """生成带测量误差的感知状态，真实状态保持不变并用于统一外部评价。"""
    perceived_resources = copy.deepcopy(list(resources))
    perceived_tasks = copy.deepcopy(list(tasks))
    if noise <= 0.0:
        return perceived_resources, perceived_tasks
    for resource in perceived_resources:
        values = list(resource.ability)
        for index in range(5):
            values[index] = clamp(values[index] + rng.gauss(0.0, noise))
        resource.ability = tuple(values)  # type: ignore[assignment]
    for task in perceived_tasks:
        values = list(task.feature)
        for index in range(6):
            values[index] = clamp(values[index] + rng.gauss(0.0, noise), .05 if index == 5 else 0.0, 1.0)
        task.feature = tuple(values)  # type: ignore[assignment]
    return perceived_resources, perceived_tasks


def state_fingerprint(resources: Sequence[Resource], tasks: Sequence[Task]) -> str:
    """Return a stable digest used to audit paired state trajectories."""
    payload = {
        "resources": [
            {
                "id": resource.resource_id,
                "type": resource.resource_type,
                "ability": [round(value, 12) for value in resource.ability],
                "capacity": resource.capacity,
                "available": resource.available,
            }
            for resource in resources
        ],
        "tasks": [
            {
                "id": task.task_id,
                "feature": [round(value, 12) for value in task.feature],
                "min": task.min_resources,
                "max": task.max_resources,
                "requirements": sorted(task.type_requirements.items()),
                "arrival": task.arrival_step,
                "active": task.active,
            }
            for task in tasks
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


ALGORITHMS = ("CA-HMCD", "Greedy", "External-Auction",
              "No-Synergy", "No-Stability", "Random")
EXTERNAL_BASELINES = ("HiGHS-MILP", "Genetic-Algorithm")
ABLATIONS = ("CA-HMCD", "No-Compatibility", "No-Synergy", "No-Redundancy", "No-Stability")
ALGORITHM_CONFIG = {
    # flags: complementarity, redundancy, compatibility, stability, pruning
    "CA-HMCD": ((True, True, True, True, True), "bb"),
    "Greedy": ((True, True, True, True, True), "greedy"),
    "No-Synergy": ((False, True, True, True, True), "bb"),
    "No-Compatibility": ((True, True, False, True, True), "bb"),
    "No-Redundancy": ((True, False, True, True, True), "bb"),
    "No-Stability": ((True, True, True, False, True), "bb"),
    "No-Pruning": ((True, True, True, True, False), "bb"),
    "HiGHS-MILP": ((True, True, True, True, False), "milp"),
    "Genetic-Algorithm": ((True, True, True, True, False), "ga"),
    "Random": ((True, True, True, False, True), "random"),
    "External-Auction": ((False, False, True, True, True), "auction"),
    # Orthogonal controls used to separate model and solver effects.
    "CA-HMCD-GreedySolver": ((True, True, True, True, True), "greedy"),
    "No-Synergy-GreedySolver": ((False, True, True, True, True), "greedy"),
}


def run_episode(algorithm: str, seed: int, steps: int, resources_count: int, tasks_count: int,
                scenario: str, top_k: int = 8, noise_level: float = 0.0,
                synergy_scale: float = 1.0, volatility_override: Optional[float] = None,
                failure_rate_override: Optional[float] = None,
                enable_safety_repair: bool = True,
                node_limit_override: Optional[int] = None,
                candidate_pool_mode: str = "independent",
                force_all_tasks_active: bool = False,
                model_parameters: Optional[Dict[str, float]] = None,
                replay_episode: Optional["ReplayEpisode"] = None) -> Tuple[Dict[str, float], List[Dict[str, object]]]:
    if algorithm not in ALGORITHM_CONFIG:
        raise ValueError(f"未知算法 {algorithm}，可选：{', '.join(ALGORITHM_CONFIG)}")
    if candidate_pool_mode not in ("independent", "shared"):
        raise ValueError("candidate_pool_mode 必须为 independent 或 shared")
    if replay_episode is not None:
        replay_episode.validate()
        if force_all_tasks_active:
            raise ValueError("公开轨迹回放不能强制所有任务持续活动")
        if steps > replay_episode.step_count:
            raise ValueError("请求的决策周期超过回放轨迹长度")
        if tasks_count not in (0, replay_episode.task_count):
            raise ValueError("tasks_count 必须与回放轨迹中的稳定任务数一致")
        params = {
            "volatility": 0.0,
            "failure_rate": (
                0.05 if failure_rate_override is None else failure_rate_override
            ),
        }
        resources, _ = make_scenario(seed, resources_count, 0, 0)
        tasks = replay_episode.true_tasks_at(0)
    else:
        params = scenario_parameters(scenario)
        if scenario == "semisynthetic":
            resources, tasks = make_semisynthetic_scenario(seed, resources_count, tasks_count,
                                                           int(params["arrival_horizon"]))
        elif scenario == "redundancy_stress":
            resources, tasks = make_redundancy_stress_scenario(seed, resources_count, tasks_count,
                                                               int(params["arrival_horizon"]))
        elif scenario in {"airport_corridor", "energy_facility", "public_event",
                          "urban_corridor", "industrial_zone"}:
            resources, tasks = make_engineering_scenario(
                seed, scenario, resources_count, tasks_count
            )
        else:
            resources, tasks = make_scenario(
                seed, resources_count, tasks_count, int(params["arrival_horizon"])
            )
        if force_all_tasks_active:
            for task in tasks:
                task.arrival_step = 0
                task.active = True
    dynamic_rng = random.Random(seed * 1009 + 17)
    decision_rng = random.Random(seed * 1013 + 29)
    perception_rng = random.Random(seed * 1019 + 31)
    previous: Dict[int, Tuple[int, ...]] = {}
    rows: List[Dict[str, object]] = []
    flags, solver_mode = ALGORITHM_CONFIG[algorithm]
    model_parameters = model_parameters or {}
    alpha_cost = float(model_parameters.get("alpha_cost", ALPHA_COST))
    beta_time = float(model_parameters.get("beta_time", BETA_TIME))
    lambda_switch = float(model_parameters.get("lambda_switch", LAMBDA_SWITCH))
    lambda_redundancy = float(model_parameters.get(
        "lambda_redundancy", LAMBDA_REDUNDANCY
    ))
    gamma_unserved = float(model_parameters.get("gamma_unserved", GAMMA_UNSERVED))
    for step in range(steps):
        step_started = time.perf_counter()
        volatility = params["volatility"] if volatility_override is None else volatility_override
        failure_rate = params["failure_rate"] if failure_rate_override is None else failure_rate_override
        if replay_episode is None:
            update_tasks(tasks, dynamic_rng, step, volatility)
            observed_tasks = tasks
        else:
            tasks = replay_episode.true_tasks_at(step)
            observed_tasks = replay_episode.observed_tasks_at(step)
        if step:
            update_resources(resources, dynamic_rng, failure_rate)
        perceived_resources, perceived_tasks = noisy_perception(
            resources, observed_tasks, noise_level, perception_rng
        )
        use_synergy, use_redundancy, use_adaptation, use_stability, use_pruning = flags
        candidate_limit = top_k if use_pruning else 10000
        candidate_started = time.perf_counter()
        if candidate_pool_mode == "shared":
            # Shared mode is retained as a sensitivity analysis. It is independent of
            # algorithm history and is generated with the complete model.
            pool_flags = (True, True, True, True, True)
            pool_limit = top_k
            pool_previous = None
        else:
            pool_flags = flags
            pool_limit = candidate_limit
            pool_previous = previous if use_stability else {}
        pool_synergy, pool_redundancy, pool_adaptation, _, pool_pruning = pool_flags
        pool_switch_lambda = lambda_switch if pool_previous else 0.0
        candidate_audits: Dict[int, CandidateGenerationAudit] = {}
        candidates: Dict[int, List[Candidate]] = {}
        for j in range(len(tasks)):
            audit = CandidateGenerationAudit(task_index=j)
            candidate_audits[j] = audit
            candidates[j] = generate_candidates(
                j,
                perceived_resources,
                perceived_tasks,
                pool_limit,
                alpha_cost,
                beta_time,
                pool_synergy,
                pool_redundancy,
                pool_adaptation,
                synergy_scale,
                prune=pool_pruning,
                previous_combo=(pool_previous or {}).get(j),
                switch_lambda=pool_switch_lambda,
                redundancy_lambda=lambda_redundancy,
                audit=audit,
            )
        if candidate_pool_mode == "shared" and (not (use_synergy and use_redundancy and use_adaptation)):
            # Re-score the shared complete-model pool under the ablation objective.
            candidates = {
                j: sorted(
                    [evaluate_candidate(j, c.resource_indices, perceived_resources, perceived_tasks,
                                         alpha_cost, beta_time, use_synergy, use_redundancy,
                                         use_adaptation, synergy_scale,
                                         lambda_redundancy) for c in pool],
                    key=lambda c: (
                        _switch_adjusted_candidate_value(
                            c, previous.get(j) if use_stability else None,
                            lambda_switch if use_stability else 0.0,
                        ),
                        c.success_score,
                        -c.physical_cost,
                    ),
                    reverse=True,
                )
                for j, pool in candidates.items()
            }
        candidate_generation_ms = (time.perf_counter() - candidate_started) * 1000
        active_candidate_audits = [
            audit
            for j, audit in candidate_audits.items()
            if perceived_tasks[j].active
        ]
        candidate_audit_totals = {
            "candidate_tasks_audited": len(active_candidate_audits),
            "candidate_resources_total": sum(
                audit.total_resources for audit in active_candidate_audits
            ),
            "candidate_resources_after_screen": sum(
                audit.screened_resources for audit in active_candidate_audits
            ),
            "candidate_screen_applied_tasks": sum(
                audit.compatibility_screen_applied for audit in active_candidate_audits
            ),
            "candidate_subsets_enumerated": sum(
                audit.enumerated_subsets for audit in active_candidate_audits
            ),
            "candidate_feasible_before_dominance": sum(
                audit.feasible_before_dominance for audit in active_candidate_audits
            ),
            "candidate_after_dominance": sum(
                audit.after_dominance for audit in active_candidate_audits
            ),
            "candidate_after_diversity": sum(
                audit.after_diversity for audit in active_candidate_audits
            ),
            "candidate_dominance_removed": sum(
                audit.dominance_removed for audit in active_candidate_audits
            ),
            "candidate_diversity_removed": sum(
                audit.diversity_removed for audit in active_candidate_audits
            ),
            "candidate_previous_feasible": sum(
                audit.previous_candidate_feasible for audit in active_candidate_audits
            ),
            "candidate_previous_protected": sum(
                audit.previous_candidate_protected for audit in active_candidate_audits
            ),
        }
        budget = sum((.20 + .80 * r.ability[3]) * r.capacity for r in resources) * .72
        solver_started = time.perf_counter()
        if solver_mode == "random":
            result = random_allocate(
                perceived_tasks, perceived_resources, candidates, previous,
                budget, decision_rng, gamma_unserved
            )
        elif solver_mode == "auction":
            result = auction_allocate(
                perceived_tasks, perceived_resources, candidates, previous,
                lambda_switch if use_stability else 0.0, budget,
                alpha_cost, beta_time, gamma_unserved
            )
        elif solver_mode == "milp":
            result = milp_allocate(
                perceived_tasks,
                perceived_resources,
                candidates,
                previous,
                lambda_switch if use_stability else 0.0,
                budget,
                node_limit=node_limit_override or 12000,
                gamma_unserved=gamma_unserved,
            )
        elif solver_mode == "ga":
            result = genetic_allocate(
                perceived_tasks,
                perceived_resources,
                candidates,
                previous,
                lambda_switch if use_stability else 0.0,
                budget,
                decision_rng,
                evaluation_budget=node_limit_override or 12000,
                gamma_unserved=gamma_unserved,
            )
        else:
            exact_solver = solver_mode == "bb"
            result = solve_global(perceived_tasks, perceived_resources, candidates, previous,
                                  switch_lambda=lambda_switch if use_stability else 0.0,
                                  budget=budget, exact=exact_solver,
                                  node_limit=node_limit_override or 12000,
                                  gamma_unserved=gamma_unserved)
        solver_elapsed_ms = (time.perf_counter() - solver_started) * 1000
        evaluation_started = time.perf_counter()
        # Every method is scored by the same complete external model. Ablation
        # flags affect decision-making only, never the reported evaluation.
        raw_external, raw_violations = evaluate_allocation(
            result.selected, resources, tasks, previous, synergy_scale, budget,
            use_synergy=True, use_redundancy=True, use_adaptation=True,
            switch_lambda=lambda_switch, gamma_unserved=gamma_unserved,
            alpha=alpha_cost, beta=beta_time,
            redundancy_lambda=lambda_redundancy)
        final_selected = result.selected
        removed_by_repair = 0
        repair_started = time.perf_counter()
        if enable_safety_repair and raw_violations:
            final_selected, removed_by_repair = safety_repair(
                result.selected, resources, tasks, previous, top_k, budget, synergy_scale,
                True, True, True, lambda_switch, gamma_unserved,
                alpha_cost, beta_time, lambda_redundancy)
        repair_elapsed_ms = (time.perf_counter() - repair_started) * 1000
        external, violations = evaluate_allocation(
            final_selected, resources, tasks, previous, synergy_scale, budget,
            use_synergy=True, use_redundancy=True, use_adaptation=True,
            switch_lambda=lambda_switch, gamma_unserved=gamma_unserved,
            alpha=alpha_cost, beta=beta_time,
            redundancy_lambda=lambda_redundancy)
        base_external, _ = evaluate_allocation(final_selected, resources, tasks, previous,
                                               synergy_scale, budget, use_synergy=False,
                                               use_redundancy=False, use_adaptation=True,
                                               switch_lambda=0.0,
                                               gamma_unserved=gamma_unserved,
                                               alpha=alpha_cost, beta=beta_time,
                                               redundancy_lambda=0.0)
        independent = evaluate_independent_allocation(
            final_selected, resources, tasks, budget
        )
        served_task_response_times = {
            tasks[task_index].task_id: max(
                response_time(resources[resource_index], tasks[task_index])
                for resource_index in resource_indices
            )
            for task_index, resource_indices in final_selected.items()
            if resource_indices
        }
        evaluation_elapsed_ms = (time.perf_counter() - evaluation_started) * 1000
        end_to_end_ms = (time.perf_counter() - step_started) * 1000
        previous = final_selected
        rows.append({
            "scenario": scenario, "algorithm": algorithm, "seed": seed, "step": step,
            "state_fingerprint": state_fingerprint(resources, tasks),
            "active_task_ids_json": json.dumps(
                [task.task_id for task in tasks if task.active],
                separators=(",", ":"),
            ),
            "objective": external.objective, "decision_objective": result.objective,
            "utility": external.total_utility,
            "success_score": external.mean_success_score,
            "physical_cost": external.total_physical_cost,
            "redundancy_penalty": external.total_redundancy_penalty,
            "compatibility_score": external.mean_compatibility_score,
            "complementarity_gain": external.total_complementarity_gain,
            # Deprecated aliases retained for existing plotting scripts.
            "success_probability": external.mean_success_score,
            "response_time": external.mean_response_time, "cost": external.total_physical_cost,
            "coverage": external.covered_tasks / max(1, sum(t.active for t in tasks)),
            "resource_utilization": sum(len(v) for v in external.selected.values()) /
                                    max(1, sum(r.capacity for r in resources)),
            "switch_count": external.switch_count, "runtime_ms": result.runtime_ms,
            "solver": result.solver, "nodes": result.nodes,
            "fallback": 1.0 if _is_fallback_solver(result.solver) else 0.0,
            "fallback_selected_incumbent": 1.0 if result.fallback_selected_incumbent else 0.0,
            "fallback_incumbent_objective": result.fallback_incumbent_objective,
            "fallback_greedy_objective": result.fallback_greedy_objective,
            "node_limit_hit": 1.0 if result.node_limit_hit else 0.0,
            "search_upper_bound": (
                result.search_upper_bound
                if result.search_upper_bound is not None else result.objective
            ),
            "fallback_optimality_gap_bound": (
                result.fallback_optimality_gap_bound
                if result.fallback_optimality_gap_bound is not None else 0.0
            ),
            "fallback_gain_over_greedy": (
                result.fallback_gain_over_greedy
                if result.fallback_gain_over_greedy is not None else 0.0
            ),
            "unexplored_frontier_nodes": result.unexplored_frontier_nodes,
            "active_tasks": sum(t.active for t in tasks),
            "observed_active_tasks": sum(t.active for t in perceived_tasks),
            "active_state_mismatches": sum(
                true_task.active != observed_task.active
                for true_task, observed_task in zip(tasks, perceived_tasks)
            ),
            "replay_dataset": (
                replay_episode.dataset if replay_episode is not None else ""
            ),
            "replay_sequence_id": (
                replay_episode.sequence_id if replay_episode is not None else ""
            ),
            "replay_mapping_version": (
                replay_episode.mapping_version if replay_episode is not None else ""
            ),
            "replay_source_time": (
                replay_episode.source_time_at(step)
                if replay_episode is not None
                else float(step)
            ),
            "synergy": external.total_complementarity_gain,
            "redundancy": external.total_redundancy_penalty,
            "adaptation": external.mean_compatibility_score,
            "constraint_violations": violations,
            "allocated_pair_count": external.allocated_pair_count,
            "same_type_pair_ratio": external.same_type_pair_ratio,
            "capability_overlap": external.capability_overlap,
            "marginal_gain_waste": external.marginal_gain_waste,
            "external_objective": external.objective,
            "external_utility": external.total_utility,
            "external_success_score": external.mean_success_score,
            "external_physical_cost": external.total_physical_cost,
            "external_redundancy_penalty": external.total_redundancy_penalty,
            "external_compatibility_score": external.mean_compatibility_score,
            "external_complementarity_gain": external.total_complementarity_gain,
            # Deprecated aliases retained for existing plotting scripts.
            "external_success_probability": external.mean_success_score,
            "external_synergy": external.total_complementarity_gain,
            "external_redundancy": external.total_redundancy_penalty,
            "external_switch_count": external.switch_count,
            "external_switch_penalty": external.switch_penalty,
            "external_evaluator": "complete",
            "raw_constraint_violations": raw_violations,
            "raw_feasible": 1.0 if raw_violations == 0 else 0.0,
            "raw_objective": raw_external.objective,
            "repair_applied": 1.0 if removed_by_repair else 0.0,
            "base_objective": base_external.objective,
            "base_utility": base_external.total_utility,
            "base_success_score": base_external.mean_success_score,
            "base_physical_cost": base_external.total_physical_cost,
            "base_success_probability": base_external.mean_success_score,
            "base_response_time": base_external.mean_response_time,
            "base_coverage": base_external.covered_tasks / max(1, sum(t.active for t in tasks)),
            "served_task_response_times_json": json.dumps(
                served_task_response_times, sort_keys=True, separators=(",", ":")
            ),
            **independent,
            **candidate_audit_totals,
            "candidate_generation_ms": candidate_generation_ms,
            "solver_elapsed_ms": solver_elapsed_ms,
            "evaluation_ms": evaluation_elapsed_ms,
            "repair_ms": repair_elapsed_ms,
            "end_to_end_runtime_ms": end_to_end_ms,
            "candidate_pool_mode": candidate_pool_mode,
            "noise_level": noise_level, "synergy_scale": synergy_scale,
            "volatility": volatility,
            "alpha_cost": alpha_cost,
            "beta_time": beta_time,
            "lambda_switch": lambda_switch,
            "lambda_redundancy": lambda_redundancy,
            "gamma_unserved": gamma_unserved,
            "model_version": MODEL_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "service_endpoint_version": SERVICE_ENDPOINT_VERSION,
        })
    mean_keys = (
        "objective", "utility", "success_score", "physical_cost", "redundancy_penalty",
        "compatibility_score", "complementarity_gain",
        "success_probability", "response_time", "cost",
        "coverage", "resource_utilization", "switch_count", "runtime_ms",
        "active_tasks", "observed_active_tasks", "active_state_mismatches",
        "synergy", "redundancy", "adaptation",
        "allocated_pair_count", "same_type_pair_ratio",
        "capability_overlap", "marginal_gain_waste",
        "external_objective", "external_utility", "external_success_score",
        "external_physical_cost", "external_redundancy_penalty",
        "external_compatibility_score", "external_complementarity_gain",
        "external_success_probability", "external_synergy",
        "external_redundancy", "external_switch_count",
        "external_switch_penalty",
        "decision_objective", "constraint_violations", "raw_constraint_violations",
        "raw_feasible", "raw_objective", "repair_applied", "fallback",
        "fallback_selected_incumbent", "node_limit_hit", "search_upper_bound",
        "fallback_optimality_gap_bound", "fallback_gain_over_greedy",
        "unexplored_frontier_nodes", "nodes",
        "base_objective", "base_utility", "base_success_score", "base_physical_cost",
        "base_success_probability", "base_response_time",
        "base_coverage", "independent_service_score",
        "independent_risk_weighted_success", "independent_coverage",
        "independent_response_quality", "independent_response_time",
        "independent_cost_efficiency", "independent_physical_cost",
        "independent_feasibility", "independent_violations",
        "independent_allocated_pair_count", "independent_same_type_pair_ratio",
        "independent_capability_overlap", "independent_marginal_gain_waste",
        "candidate_tasks_audited", "candidate_resources_total",
        "candidate_resources_after_screen", "candidate_screen_applied_tasks",
        "candidate_subsets_enumerated", "candidate_feasible_before_dominance",
        "candidate_after_dominance", "candidate_after_diversity",
        "candidate_dominance_removed", "candidate_diversity_removed",
        "candidate_previous_feasible", "candidate_previous_protected",
        "candidate_generation_ms", "solver_elapsed_ms", "evaluation_ms",
        "repair_ms", "end_to_end_runtime_ms",
    )
    metrics = {
        key: statistics.fmean(float(row[key]) for row in rows)
        for key in mean_keys
    }
    metrics["feasibility_rate"] = statistics.fmean(1.0 if row["constraint_violations"] == 0 else 0.0
                                                    for row in rows)
    return metrics, rows


def mean_std_ci(values: Sequence[float]) -> Tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, std, 1.96 * std / math.sqrt(len(values)) if len(values) > 1 else 0.0


def summarise(records: Sequence[Dict[str, object]], group_keys: Sequence[str]) -> List[Dict[str, object]]:
    metric_keys = (
        "objective", "utility", "success_score", "physical_cost", "redundancy_penalty",
        "compatibility_score", "complementarity_gain",
        "success_probability", "response_time", "cost", "coverage",
        "resource_utilization", "switch_count", "runtime_ms", "feasibility_rate",
        "active_tasks", "observed_active_tasks", "active_state_mismatches",
        "synergy", "redundancy", "adaptation", "decision_objective",
        "allocated_pair_count", "same_type_pair_ratio", "capability_overlap",
        "marginal_gain_waste", "external_objective", "external_utility",
        "external_success_score", "external_physical_cost",
        "external_redundancy_penalty", "external_compatibility_score",
        "external_complementarity_gain",
        "external_success_probability", "external_synergy", "external_redundancy",
        "external_switch_count", "external_switch_penalty",
        "constraint_violations", "raw_constraint_violations", "raw_feasible",
        "raw_objective", "repair_applied", "fallback", "fallback_selected_incumbent",
        "node_limit_hit", "search_upper_bound", "fallback_optimality_gap_bound",
        "fallback_gain_over_greedy", "unexplored_frontier_nodes",
        "nodes", "base_objective", "base_utility", "base_success_score",
        "base_physical_cost", "base_success_probability",
        "base_response_time", "base_coverage",
        "independent_service_score", "independent_risk_weighted_success",
        "independent_coverage", "independent_response_quality",
        "independent_response_time", "independent_cost_efficiency",
        "independent_physical_cost", "independent_feasibility",
        "independent_violations", "independent_allocated_pair_count",
        "independent_same_type_pair_ratio", "independent_capability_overlap",
        "independent_marginal_gain_waste",
        "candidate_tasks_audited", "candidate_resources_total",
        "candidate_resources_after_screen", "candidate_screen_applied_tasks",
        "candidate_subsets_enumerated", "candidate_feasible_before_dominance",
        "candidate_after_dominance", "candidate_after_diversity",
        "candidate_dominance_removed", "candidate_diversity_removed",
        "candidate_previous_feasible", "candidate_previous_protected",
        "candidate_generation_ms",
        "solver_elapsed_ms", "evaluation_ms", "repair_ms", "end_to_end_runtime_ms",
    )
    groups: Dict[Tuple[object, ...], List[Dict[str, object]]] = defaultdict(list)
    for record in records:
        groups[tuple(record[k] for k in group_keys)].append(record)
    output: List[Dict[str, object]] = []
    def metric_value(record: Dict[str, object], key: str) -> float:
        defaults = {
            "raw_constraint_violations": record.get("constraint_violations", 0.0),
            "raw_feasible": record.get("feasibility_rate", 1.0),
            "raw_objective": record.get("objective", 0.0),
            "repair_applied": 0.0,
            "fallback": 0.0,
            "fallback_selected_incumbent": 0.0,
            "node_limit_hit": 0.0,
            "search_upper_bound": record.get("objective", 0.0),
            "fallback_optimality_gap_bound": 0.0,
            "fallback_gain_over_greedy": 0.0,
            "unexplored_frontier_nodes": 0.0,
            "nodes": 0.0,
        }
        return float(record.get(key, defaults.get(key, 0.0)))
    for group, values in sorted(groups.items(), key=lambda item: tuple(str(x) for x in item[0])):
        row = dict(zip(group_keys, group))
        for key in metric_keys:
            mean, std, ci = mean_std_ci([metric_value(v, key) for v in values])
            row[f"{key}_mean"] = round(mean, 6)
            row[f"{key}_std"] = round(std, 6)
            row[f"{key}_ci95"] = round(ci, 6)
        runtime_values = sorted(float(metric_value(v, "runtime_ms")) for v in values)
        node_values = [float(metric_value(v, "nodes")) for v in values]
        row["runtime_ms_p95"] = round(runtime_values[min(len(runtime_values) - 1,
                                                         int(.95 * len(runtime_values)))], 6)
        row["nodes_mean"] = round(statistics.fmean(node_values), 6)
        row["nodes_max"] = round(max(node_values), 6)
        row["replications"] = len(values)
        output.append(row)
    return output


def attach_step_runtime_statistics(summary: Sequence[Dict[str, object]],
                                   step_records: Sequence[Dict[str, object]],
                                   group_keys: Sequence[str]) -> None:
    """Attach true per-step end-to-end tail latency to summary rows."""
    grouped: Dict[Tuple[object, ...], List[float]] = defaultdict(list)
    solver_grouped: Dict[Tuple[object, ...], List[float]] = defaultdict(list)
    for record in step_records:
        group = tuple(record[key] for key in group_keys)
        grouped[group].append(float(record["end_to_end_runtime_ms"]))
        solver_grouped[group].append(float(record["runtime_ms"]))
    for row in summary:
        group = tuple(row[key] for key in group_keys)
        values = sorted(grouped.get(group, []))
        solver_values = sorted(solver_grouped.get(group, []))
        if not values:
            row["end_to_end_runtime_ms_p95"] = 0.0
            row["end_to_end_runtime_ms_max"] = 0.0
            row["solver_runtime_ms_p95"] = 0.0
            continue
        percentile_index = max(0, math.ceil(.95 * len(values)) - 1)
        solver_percentile_index = max(0, math.ceil(.95 * len(solver_values)) - 1)
        row["end_to_end_runtime_ms_p95"] = round(values[percentile_index], 6)
        row["end_to_end_runtime_ms_max"] = round(values[-1], 6)
        row["solver_runtime_ms_p95"] = round(solver_values[solver_percentile_index], 6)


def _average_ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position + 1
        while end < len(order) and abs(values[order[end]] - values[order[position]]) < 1e-12:
            end += 1
        rank = (position + 1 + end) / 2.0
        for index in order[position:end]:
            ranks[index] = rank
        position = end
    return ranks


def paired_statistics(a: Sequence[float], b: Sequence[float]) -> Dict[str, float]:
    """配对 Wilcoxon 正态近似、配对效应量和胜率；差值定义为 a-b。"""
    all_differences = [x - y for x, y in zip(a, b)]
    differences = [difference for difference in all_differences if abs(difference) > 1e-12]
    if not differences:
        return {"mean_difference": 0.0, "wilcoxon_w": 0.0, "p_value": 1.0,
                "cohen_dz": 0.0, "rank_biserial": 0.0, "win_rate": .5, "pairs": len(a)}
    ranks = _average_ranks([abs(d) for d in differences])
    positive = sum(rank for rank, diff in zip(ranks, differences) if diff > 0)
    negative = sum(rank for rank, diff in zip(ranks, differences) if diff < 0)
    n = len(differences)
    mean_w = n * (n + 1) / 4.0
    if n <= 20:
        observed = abs(positive - mean_w)
        extreme = 0
        for signs in itertools.product((0, 1), repeat=n):
            simulated = sum(rank for rank, sign in zip(ranks, signs) if sign)
            extreme += abs(simulated - mean_w) >= observed - 1e-12
        p_value = extreme / (2 ** n)
    else:
        tie_counts: Dict[float, int] = defaultdict(int)
        for diff in differences:
            tie_counts[round(abs(diff), 12)] += 1
        variance = n * (n + 1) * (2 * n + 1) / 24.0
        variance -= sum(count ** 3 - count for count in tie_counts.values()) / 48.0
        correction = .5 if positive > mean_w else (-.5 if positive < mean_w else 0.0)
        z_value = (positive - mean_w - correction) / math.sqrt(max(variance, 1e-12))
        p_value = math.erfc(abs(z_value) / math.sqrt(2.0))
    std = statistics.stdev(all_differences) if len(all_differences) > 1 else 0.0
    mean_difference = statistics.fmean(all_differences) if all_differences else 0.0
    wins = sum(d > 0 for d in all_differences) + .5 * sum(abs(d) <= 1e-12 for d in all_differences)
    return {
        "mean_difference": mean_difference, "wilcoxon_w": min(positive, negative),
        "p_value": p_value, "cohen_dz": mean_difference / std if std > 1e-12 else 0.0,
        "rank_biserial": (positive - negative) / (positive + negative),
        "win_rate": wins / max(1, len(a)), "pairs": len(a),
    }


def paired_comparisons(records: Sequence[Dict[str, object]], metric: str = "base_objective") -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for scenario in sorted({str(r["scenario"]) for r in records}):
        scenario_rows = [r for r in records if r["scenario"] == scenario]
        lookup = {(r["algorithm"], r["seed"]): float(r[metric]) for r in scenario_rows}
        seeds = sorted({int(r["seed"]) for r in scenario_rows if r["algorithm"] == "CA-HMCD"})
        for baseline in sorted({str(r["algorithm"]) for r in scenario_rows} - {"CA-HMCD"}):
            paired_seeds = [seed for seed in seeds if (baseline, seed) in lookup]
            stats = paired_statistics([lookup[("CA-HMCD", seed)] for seed in paired_seeds],
                                      [lookup[(baseline, seed)] for seed in paired_seeds])
            rows.append({"scenario": scenario, "metric": metric, "method_a": "CA-HMCD",
                         "method_b": baseline, **{k: round(v, 8) for k, v in stats.items()}})
    # Holm step-down 校正，覆盖本表所有比较。
    for scenario in sorted({str(row["scenario"]) for row in rows}):
        indices = [i for i, row in enumerate(rows) if row["scenario"] == scenario]
        ordered = sorted(indices, key=lambda i: float(rows[i]["p_value"]))
        running = 0.0
        m = len(ordered)
        adjusted = {i: 1.0 for i in indices}
        for rank, index in enumerate(ordered):
            running = max(running, min(1.0, (m - rank) * float(rows[index]["p_value"])))
            adjusted[index] = running
        for index in indices:
            rows[index]["p_holm"] = round(adjusted[index], 8)
            rows[index]["significant_0_05"] = adjusted[index] < .05
    return rows


def optimality_experiment(seeds: int, base_seed: int, top_k: int) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for rep in range(seeds):
        seed = base_seed + rep
        resources, tasks = make_scenario(seed, 6, 4, 0)
        for task in tasks:
            task.active = True
            task.arrival_step = 0
            task.max_resources = 2
        budget = sum((.20 + .80 * r.ability[3]) * r.capacity for r in resources) * .72
        complete = {j: generate_candidates(j, resources, tasks, 10000, prune=False) for j in range(len(tasks))}
        optimum = solve_global(tasks, resources, complete, {}, LAMBDA_SWITCH, budget, True, 2000000)
        zero_baseline = -unserved_penalty(tasks, {}, GAMMA_UNSERVED)
        for candidate_k in sorted({4, 8, 12, top_k}):
            pruned = {j: generate_candidates(j, resources, tasks, candidate_k, prune=True)
                      for j in range(len(tasks))}
            approximate = solve_global(tasks, resources, pruned, {}, LAMBDA_SWITCH, budget, True, 500000)
            denominator = max(abs(optimum.objective), 1e-9)
            improvement_span = max(abs(optimum.objective - zero_baseline), 1e-9)
            gap = max(0.0, optimum.objective - approximate.objective)
            rows.append({"seed": seed, "top_k": candidate_k, "optimal_objective": optimum.objective,
                         "ca_hmcd_objective": approximate.objective, "absolute_gap": gap,
                         "optimality_gap_percent": 100.0 * gap / denominator,
                         "normalized_regret_percent": 100.0 * gap / improvement_span,
                         "zero_allocation_baseline": zero_baseline,
                         "optimal_hit": gap <= 1e-9, "optimal_solver": optimum.solver,
                         "optimal_nodes": optimum.nodes, "ca_hmcd_nodes": approximate.nodes,
                         "optimal_runtime_ms": optimum.runtime_ms,
                         "ca_hmcd_runtime_ms": approximate.runtime_ms})
    return rows


def parameter_provenance() -> List[Dict[str, object]]:
    return [
        {"parameter": "resource ability dimensions", "range": "[0,1]", "role": "normalized synthetic input",
         "basis": "type-specific controlled distributions", "validation": "noise robustness and sensitivity"},
        {"parameter": "task feature dimensions", "range": "[0,1]", "role": "normalized synthetic input",
         "basis": "controlled Monte Carlo sampling", "validation": "multi-scenario and 30-seed statistics"},
        {"parameter": "alpha_cost", "range": str(ALPHA_COST), "role": "physical-consumption penalty",
         "basis": "model assumption", "validation": "ablation/sensitivity required before deployment"},
        {"parameter": "beta_time", "range": str(BETA_TIME), "role": "response-time penalty",
         "basis": "model assumption", "validation": "ablation/sensitivity required before deployment"},
        {"parameter": "lambda_switch", "range": str(LAMBDA_SWITCH), "role": "reallocation penalty",
         "basis": "controlled stability coefficient", "validation": "dynamic-mechanism experiment"},
        {"parameter": "lambda_redundancy", "range": str(LAMBDA_REDUNDANCY),
         "role": "soft capability-overlap penalty",
         "basis": "separate from the physical expenditure budget",
         "validation": "redundancy ablation and stress experiment"},
        {"parameter": "gamma_unserved", "range": str(GAMMA_UNSERVED), "role": "unserved-risk slack penalty",
         "basis": "soft form of task service constraint", "validation": "coverage and sensitivity"},
        {"parameter": "synergy_scale", "range": "0.0-2.0", "role": "controlled complementarity strength",
         "basis": "mechanism-control variable", "validation": "monotonic mechanism experiment"},
        {"parameter": "perception_noise", "range": "0.0-0.30", "role": "measurement uncertainty",
         "basis": "zero-mean Gaussian perturbation", "validation": "robustness degradation curve"},
        {"parameter": "semisynthetic trajectories", "range": "8 tasks / 10 resources", "role": "structured benchmark",
         "basis": "trajectory-inspired synthetic correlations (not measured observations)",
         "validation": "exact reference solver and sensitivity analysis"},
        {"parameter": "redundancy-stress composition", "range": "67% Type-I / 33% Type-II", "role": "mechanism stress test",
         "basis": "same-type resource clusters with overlap", "validation": "No-Redundancy ablation"},
        {"parameter": "same_type_pair_ratio", "range": "[0,1]", "role": "post-hoc redundancy diagnostic",
         "basis": "same-type pairs / all within-task allocated pairs", "validation": "unified external evaluator"},
        {"parameter": "capability_overlap", "range": "[0,1]", "role": "post-hoc redundancy diagnostic",
         "basis": "one minus mean absolute difference over four ability dimensions",
         "validation": "unified external evaluator"},
        {"parameter": "marginal_gain_waste", "range": "[0,1]", "role": "post-hoc redundancy diagnostic",
         "basis": "duplicated standalone success-score mass / total standalone score mass",
         "validation": "unified external evaluator"},
    ]


def audit_paired_protocol(records: Sequence[Dict[str, object]],
                          expected_algorithms: Sequence[str],
                          expected_seeds: int,
                          expected_steps: int) -> List[Dict[str, object]]:
    """Audit common seeds, active loads, state trajectories, and evaluators."""
    rows: List[Dict[str, object]] = []
    grouped: Dict[Tuple[object, int, int], List[Dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[(record.get("scenario"), int(record["seed"]), int(record["step"]))].append(record)
    for (scenario, seed, step), values in sorted(grouped.items(), key=lambda item: str(item[0])):
        algorithms = {str(row["algorithm"]) for row in values}
        fingerprints = {str(row.get("state_fingerprint", "")) for row in values}
        active_loads = {float(row.get("active_tasks", -1.0)) for row in values}
        active_task_ids = {
            str(row.get("active_task_ids_json", ""))
            for row in values
        }
        evaluators = {str(row.get("evaluator_version", "")) for row in values}
        model_versions = {str(row.get("model_version", "")) for row in values}
        missing = sorted(set(expected_algorithms) - algorithms)
        rows.append({
            "scenario": scenario,
            "seed": seed,
            "step": step,
            "expected_algorithm_count": len(expected_algorithms),
            "observed_algorithm_count": len(algorithms),
            "missing_algorithms": ",".join(missing),
            "same_state": len(fingerprints) <= 1,
            "same_active_task_load": len(active_loads) <= 1,
            "same_active_task_ids": len(active_task_ids) <= 1,
            "same_evaluator_version": len(evaluators) <= 1,
            "same_model_version": len(model_versions) <= 1,
            "active_tasks": next(iter(active_loads)) if len(active_loads) == 1 else -1,
            "active_task_ids": next(iter(active_task_ids)) if len(active_task_ids) == 1 else "",
            "state_fingerprint_count": len(fingerprints),
            "evaluator_version_count": len(evaluators),
            "model_version_count": len(model_versions),
            "expected_seeds": expected_seeds,
            "expected_steps": expected_steps,
        })
    return rows


def reference_solver_experiment(seeds: int, base_seed: int, top_k: int) -> List[Dict[str, object]]:
    """小规模精确参考解：完整组合空间作为 Exact-Reference，测试剪枝后的近优性。"""
    rows: List[Dict[str, object]] = []
    for rep in range(seeds):
        seed = base_seed + rep
        resources, tasks = make_semisynthetic_scenario(seed, 6, 4, 0)
        for task in tasks:
            task.active = True
            task.arrival_step = 0
            task.max_resources = 2
        budget = sum((.20 + .80 * r.ability[3]) * r.capacity for r in resources) * .72
        complete = {j: generate_candidates(j, resources, tasks, 10000, prune=False)
                    for j in range(len(tasks))}
        started = time.perf_counter()
        reference = solve_global(tasks, resources, complete, {}, LAMBDA_SWITCH, budget, True, 2000000)
        reference_time = (time.perf_counter() - started) * 1000
        for candidate_k in sorted({4, 8, 12, top_k}):
            pruned = {j: generate_candidates(j, resources, tasks, candidate_k, prune=True)
                      for j in range(len(tasks))}
            started = time.perf_counter()
            proposed = solve_global(tasks, resources, pruned, {}, LAMBDA_SWITCH, budget, True, 500000)
            elapsed = (time.perf_counter() - started) * 1000
            gap = max(0.0, reference.objective - proposed.objective)
            rows.append({"benchmark": "semisynthetic_small", "seed": seed, "top_k": candidate_k,
                         "method": "CA-HMCD", "reference_objective": reference.objective,
                         "method_objective": proposed.objective, "absolute_gap": gap,
                         "normalized_regret_percent": 100.0 * gap / max(abs(reference.objective), 1e-9),
                         "reference_runtime_ms": reference_time, "method_runtime_ms": elapsed,
                         "reference_nodes": reference.nodes, "method_nodes": proposed.nodes,
                         "fallback": _is_fallback_solver(proposed.solver)})
    return rows


def build_analysis_report(summary: Sequence[Dict[str, object]], optimality: Sequence[Dict[str, object]],
                          robustness: Sequence[Dict[str, object]], mechanism: Sequence[Dict[str, object]],
                          tests: Sequence[Dict[str, object]], top_k: int, seeds: int, steps: int,
                          ablation_tests: Sequence[Dict[str, object]] = (),
                          reference_solver: Sequence[Dict[str, object]] = (),
                          redundancy_stress: Sequence[Dict[str, object]] = (),
                          solver_fairness: Sequence[Dict[str, object]] = (),
                          candidate_pool_sensitivity: Sequence[Dict[str, object]] = (),
                          pruning_control: Sequence[Dict[str, object]] = ()) -> str:
    lines = ["# CA-HMCD 仿真实验自动分析", "",
             f"实验采用 {seeds} 个配对随机种子、每次 {steps} 个决策周期，候选组合参数 K={top_k}。",
             "所有方法的 objective 均由完整 CA-HMCD 外部评价函数统一计算，因此可以横向比较。", "",
             "## 主实验", ""]
    lines[2] = ("Primary comparisons use external_objective: every final allocation is re-scored "
                "with compatibility, bounded complementarity, redundancy-penalty, "
                "physical-expenditure, switching and unserved-task terms enabled.")
    lines[3] = ("base_objective is retained as an orthogonal diagnostic that excludes "
                "complementarity, redundancy and switching terms.")
    for scenario in ("balanced", "scarce", "volatile", "semisynthetic", "redundancy_stress"):
        if not any(row["scenario"] == scenario for row in summary):
            continue
        values = [row for row in summary if row["scenario"] == scenario]
        values.sort(key=lambda row: float(row["external_objective_mean"]), reverse=True)
        lines.append(f"- {scenario}：" + " > ".join(
            f"{row['algorithm']} (external={float(row['external_objective_mean']):.4f})"
            for row in values))
    selected_optimality = [row for row in optimality if int(row["top_k"]) == top_k]
    mean_gap = statistics.fmean(float(row["optimality_gap_percent"]) for row in selected_optimality)
    hit_rate = statistics.fmean(1.0 if row["optimal_hit"] else 0.0 for row in selected_optimality)
    lines.extend(["", "## 小规模最优性", "",
                  f"- K={top_k} 的平均最优性差距为 {mean_gap:.3f}%，最优解命中率为 {hit_rate:.1%}。"])
    if reference_solver:
        selected_reference = [row for row in reference_solver if int(row["top_k"]) == top_k]
        lines.append(f"- 半合成精确参考解：K={top_k} 平均绝对差距 "
                     f"{statistics.fmean(float(row['absolute_gap']) for row in selected_reference):.4f}，"
                     f"平均运行时间 {statistics.fmean(float(row['method_runtime_ms']) for row in selected_reference):.3f} ms。")
    synergy_rows = [row for row in mechanism if row["experiment"] == "synergy_strength"]
    levels = sorted({float(row["level"]) for row in synergy_rows})
    lines.extend(["", "## 机制验证", ""])
    for level in (levels[0], levels[-1]):
        ca = next(row for row in synergy_rows if float(row["level"]) == level and row["algorithm"] == "CA-HMCD")
        no = next(row for row in synergy_rows if float(row["level"]) == level and row["algorithm"] == "No-Synergy")
        lines.append(f"- 协同强度 {level:.1f}：CA-HMCD 相对 No-Synergy 的统一目标增量为 "
                     f"{float(ca['objective_mean']) - float(no['objective_mean']):.4f}。")
    dynamic_rows = [row for row in mechanism if row["experiment"] == "dynamic_volatility"]
    high_level = max(float(row["level"]) for row in dynamic_rows)
    stable = next(row for row in dynamic_rows if float(row["level"]) == high_level and row["algorithm"] == "CA-HMCD")
    unstable = next(row for row in dynamic_rows if float(row["level"]) == high_level and row["algorithm"] == "No-Stability")
    reduction = 1.0 - float(stable["switch_count_mean"]) / max(float(unstable["switch_count_mean"]), 1e-9)
    lines.append(f"- 动态程度 {high_level:.2f}：稳定性机制使平均切换次数下降 {reduction:.1%}，"
                 f"统一外部目标差值为 "
                 f"{float(stable['external_objective_mean']) - float(unstable['external_objective_mean']):.4f}。")
    if ablation_tests:
        lines.extend(["", "## 消融检验", ""])
        for row in ablation_tests:
            supported = (float(row["mean_difference"]) > 0 and
                         row["significant_0_05"] in (True, "True"))
            conclusion = "贡献得到支持" if supported else "贡献未得到统计支持"
            lines.append(f"- {row['method_b']}：目标差值 {float(row['mean_difference']):.4f}，"
                         f"Holm p={float(row['p_holm']):.4g}，{conclusion}。")
    if redundancy_stress:
        lines.extend(["", "## 冗余压力场景", ""])
        for row in redundancy_stress:
            lines.append(
                f"- {row['algorithm']}：统一目标 {float(row['external_objective_mean']):.4f}，"
                f"同类资源对占比 {float(row['same_type_pair_ratio_mean']):.3f}，"
                f"能力重叠度 {float(row['capability_overlap_mean']):.3f}，"
                f"边际收益浪费 {float(row['marginal_gain_waste_mean']):.3f}。")
    lines.extend(["", "## 鲁棒性", ""])
    if solver_fairness:
        lines.extend(["", "## Solver fairness", "",
                      "Primary paired comparisons use base_objective, an operational score "
                      "without synergy, redundancy or switching terms."])
        for row in solver_fairness:
            lines.append(f"- {row['scenario']} / {row['algorithm']}: "
                         f"base score {float(row['base_objective_mean']):.4f}, "
                         f"end-to-end {float(row['end_to_end_runtime_ms_mean']):.3f} ms")
    if candidate_pool_sensitivity:
        lines.extend(["", "## Candidate-pool sensitivity", ""])
        for row in candidate_pool_sensitivity:
            lines.append(f"- {row['algorithm']} / {row['candidate_pool_mode']}: "
                         f"base score {float(row['base_objective_mean']):.4f}, "
                         f"coverage {float(row['base_coverage_mean']):.3f}")
    if pruning_control:
        lines.extend(["", "## Pruning control", ""])
        for row in pruning_control:
            lines.append(f"- {row['algorithm']}: base score {float(row['base_objective_mean']):.4f}, "
                         f"end-to-end {float(row['end_to_end_runtime_ms_mean']):.3f} ms")
    lines.extend(["", "## Robustness", ""])
    for row in robustness:
        absolute_degradation = float(row.get("objective_degradation_absolute",
                                             float(row["objective_degradation_percent"]) / 100.0))
        lines.append(f"- 噪声 {float(row['noise_level']):.0%}：目标绝对退化 "
                     f"{absolute_degradation:.4f}（相对 {float(row['objective_degradation_percent']):.1f}%），可行率 "
                     f"由修复前 {float(row['raw_feasible_mean']):.1%} 提升至 "
                     f"{float(row['feasibility_rate_mean']):.1%}，"
                     f"修复触发率 {float(row['repair_applied_mean']):.1%}。")
    significant = [row for row in tests if row["significant_0_05"] in (True, "True")]
    lines.extend(["", "## 配对统计", ""])
    if significant:
        for row in significant:
            direction = "优于" if float(row["mean_difference"]) > 0 else "低于"
            lines.append(f"- {row['scenario']}：CA-HMCD {direction} {row['method_b']}，"
                         f"Holm p={float(row['p_holm']):.4g}，Cohen dz={float(row['cohen_dz']):.3f}。")
    else:
        lines.append("- Holm 校正后未发现 0.05 水平的显著差异；不能据此宣称统计显著优越。")
    lines.extend(["", "## 解释边界", "",
                  "本报告验证的是合成控制场景下的算法机制和计算性质，不等同于真实部署有效性。",
                  "若噪声下可行率明显下降，应在论文中作为局限报告，并考虑鲁棒约束或安全修复模块。", ""])
    return "\n".join(lines)


def build_compliance_report(output_dir: Path, summary: Sequence[Dict[str, object]],
                            reference_solver: Sequence[Dict[str, object]],
                            redundancy_stress: Sequence[Dict[str, object]],
                            scalability: Sequence[Dict[str, object]],
                            seeds: int, steps: int) -> str:
    """逐项检查投稿前建议是否已经被实验数据覆盖。"""
    files = {
        "半合成结构化场景": ("semisynthetic", any(r["scenario"] == "semisynthetic" for r in summary)),
        "精确参考解": ("reference_solver.csv", bool(reference_solver)),
        "冗余压力场景": ("redundancy_stress.csv", bool(redundancy_stress)),
        "大规模回退率": ("scalability.csv", any(float(r.get("fallback_mean", 0.0)) > 0 for r in scalability)),
        "多随机种子": (f"{seeds} seeds", seeds >= 30),
        "动态周期": (f"{steps} steps", steps >= 10),
    }
    lines = ["# 投稿前要求符合性检查", "", f"主实验配置：{seeds} 个随机种子，{steps} 个决策周期。", ""]
    files["solver fairness control"] = ("solver_fairness.csv", (output_dir / "solver_fairness.csv").exists())
    files["candidate-pool sensitivity"] = ("candidate_pool_sensitivity.csv",
                                             (output_dir / "candidate_pool_sensitivity.csv").exists())
    files["external-baseline shared-pool control"] = (
        "external_baseline_pool_fairness.csv",
        (output_dir / "external_baseline_pool_fairness.csv").exists(),
    )
    files["pruning control"] = ("pruning_control.csv", (output_dir / "pruning_control.csv").exists())
    files["end-to-end runtime"] = ("per_step.csv", (output_dir / "per_step.csv").exists())
    scale_replications_ok = bool(scalability) and all(
        int(row.get("replications", 0)) >= 30 for row in scalability)
    scale_load_ok = bool(scalability) and all(
        abs(float(row.get("active_tasks_mean", -1.0)) - float(row["tasks"])) < 1e-9
        for row in scalability)
    unified_evaluator_ok = bool(summary) and all(
        "external_objective_mean" in row and "external_synergy_mean" in row and
        "external_redundancy_mean" in row and "external_switch_penalty_mean" in row
        for row in summary)
    redundancy_diagnostics_ok = bool(redundancy_stress) and all(
        "same_type_pair_ratio_mean" in row and "capability_overlap_mean" in row and
        "marginal_gain_waste_mean" in row for row in redundancy_stress)
    files["scale replications"] = (">=30 paired seeds per configuration", scale_replications_ok)
    files["fixed active task load"] = ("active_tasks == configured tasks", scale_load_ok)
    files["scale step-level runtime"] = (
        "scalability_per_step.csv", (output_dir / "scalability_per_step.csv").exists())
    fallback_audit_ok = bool(scalability) and all(
        "node_limit_hit_mean" in row and
        "search_upper_bound_mean" in row and
        "fallback_optimality_gap_bound_mean" in row and
        "fallback_gain_over_greedy_mean" in row and
        "unexplored_frontier_nodes_mean" in row
        for row in scalability
    )
    files["fallback incumbent audit"] = (
        "node-limit flag / frontier bound / retained-pool gap / gain over greedy",
        fallback_audit_ok,
    )
    candidate_stage_audit_ok = bool(summary) and all(
        "candidate_feasible_before_dominance_mean" in row
        and "candidate_after_dominance_mean" in row
        and "candidate_after_diversity_mean" in row
        and "candidate_screen_applied_tasks_mean" in row
        for row in summary
    )
    files["candidate-stage audit"] = (
        "screen / feasible / dominance / fixed-K counts",
        candidate_stage_audit_ok,
    )
    files["unified external evaluator"] = (
        "complete-model metrics for every method", unified_evaluator_ok)
    files["post-hoc redundancy diagnostics"] = (
        "type ratio / ability overlap / marginal waste", redundancy_diagnostics_ok)
    for requirement, (detail, achieved) in files.items():
        lines.append(f"- [{'x' if achieved else ' '}] {requirement}：{detail}")
    if not (seeds >= 30 and steps >= 10 and scale_replications_ok and scale_load_ok and
            unified_evaluator_ok and redundancy_diagnostics_ok and fallback_audit_ok
            and candidate_stage_audit_ok):
        lines.append("\n结论：当前运行配置未达到建议的 30 seeds/10 steps 正式统计门槛；代码支持该配置，需在投稿前运行。")
    else:
        lines.append("\n结论：形式上的投稿实验覆盖项已满足，仍需结合结果中的效应量和局限撰写论文。")
    lines.append("\n说明：半合成数据改善了结构有效性，但不能替代真实公开数据；冗余项若仅在压力场景轻微改善，应谨慎表述。")
    return "\n".join(lines) + "\n"


def build_stage2_report(config: Dict[str, object],
                        summary: Sequence[Dict[str, object]],
                        independent_summary: Sequence[Dict[str, object]],
                        baseline_tests: Sequence[Dict[str, object]],
                        protocol_audit: Sequence[Dict[str, object]],
                        engineering_summary: Sequence[Dict[str, object]],
                        robustness_summary: Sequence[Dict[str, object]]) -> str:
    """Write a concise audit trail for the six Phase-2 requirements."""
    audit_ok = all(
        bool(row["same_state"]) and bool(row["same_active_task_load"])
        and bool(row.get("same_active_task_ids", True))
        and bool(row["same_evaluator_version"])
        and bool(row.get("same_model_version", True))
        and not row["missing_algorithms"]
        for row in protocol_audit
    )
    lines = [
        "# 第二阶段实验重设计报告",
        "",
        f"- 运行日期：{config['run_date']}",
        f"- 代码版本：{config['code_version']}",
        f"- 正式种子：{config['seeds']} 个配对种子（{config['base_seed']}--"
        f"{int(config['base_seed']) + int(config['seeds']) - 1}）",
        f"- 每个 episode：{config['steps']} 个决策周期",
        f"- 候选池：每个任务保留 K={config['top_k']}，方法按各自决策配置生成",
        "",
        "## 六项完成情况",
        "",
        "1. **重新运行全部实验**：主实验、消融、机制、鲁棒性、最优性、规模、冗余压力、"
        "公平性、工程验证和参数稳健性均在新目录重新生成。",
        "2. **公平性控制**：所有方法使用配对种子；每周期输出真实状态指纹、活动任务数及具体任务 ID、"
        "模型与评价器版本和配置参数，审计结果见 `protocol_audit.csv`。",
        "3. **外部基线**：加入 `External-Auction`，只依据独立单资源成功端点、物理成本、"
        "响应时间和切换负担进行任务--资源竞价，不使用互补或冗余奖励。",
        "4. **求解器解耦的服务评价体系**：`independent_service_score` 不含决策效用、互补增益、冗余惩罚、"
        "切换惩罚或求解器状态；其端点指标同时报告风险加权成功、覆盖率、期限质量、成本效率和可行率。",
        "5. **工程验证**：加入 airport corridor、energy facility、public event、"
        "urban corridor 和 industrial zone 五类应用形状场景；这些场景是归一化工程验证，"
        "不宣称真实现场数据。",
        "6. **参数稳健性**：对成本、时间、切换、冗余、未服务风险和协同尺度执行单因素扫描，"
        "并报告相对基准的独立服务得分变化。",
        "",
        "## 审计结果",
        "",
        f"- 公平性协议通过：{'是' if audit_ok else '否'}。",
        f"- 审计记录数：{len(protocol_audit)} 个配对周期。",
        f"- 主实验方法数：{len(config['primary_algorithms'])}。",
        f"- 工程验证配置数：{len(engineering_summary)}。",
        f"- 参数稳健性配置数：{len(robustness_summary)}。",
        "",
        "## 评价口径",
        "",
        "主结果继续报告完整模型重评分 `external_objective`，同时报告求解器解耦端点 `independent_service_score`"
        "作为主要交叉检查，不把后者反向输入任何在线决策。",
        "外部基线和 CA-HMCD 使用相同的真实状态轨迹、资源容量、活动任务负载、物理预算和安全修复规则；"
        "基线差异仅来自决策器本身。",
        "",
        "## 解释边界",
        "",
        "工程验证用于检验场景结构变化下的方向一致性，不能替代真实传感器、响应设备或现场试验。",
        "参数稳健性反映当前归一化仿真范围内的敏感性，不等同于部署阈值认证。",
        "",
    ]
    if baseline_tests:
        lines.append("外部基线的配对统计已经写入 `external_baseline_tests.csv`；报告中应同时查看"
                     "完整模型目标和独立服务得分，避免只依赖单一综合指标。")
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_paper_experiment(output_dir: Path, seeds: int = 30, steps: int = 12, top_k: int = 12,
                         base_seed: int = 5000) -> Dict[str, object]:
    """生成主实验、消融、真最优解、机制、鲁棒性、统计和规模实验。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    episode_records: List[Dict[str, object]] = []
    per_step: List[Dict[str, object]] = []
    node_budgets = {
        "primary": 12000,
        "validation": 6000,
        "engineering": 4000,
        "parameter_robustness": 1500,
        "pruning_control": 6000,
        "scale_small": 2000,
        "scale_medium": 1000,
        "scale_large": 500,
    }
    # 主实验同时包含随机控制场景和结构化半合成场景。
    for scenario in ("balanced", "scarce", "volatile", "semisynthetic"):
        p = scenario_parameters(scenario)
        for algorithm in ALGORITHMS:
            for rep in range(seeds):
                seed = base_seed + rep
                metrics, rows = run_episode(algorithm, seed, steps, int(p["resources"]),
                                             int(p["tasks"]), scenario, top_k,
                                             node_limit_override=node_budgets["primary"])
                episode_records.append({"scenario": scenario, "algorithm": algorithm, "seed": seed, **metrics})
                per_step.extend(rows)
    summary = summarise(episode_records, ("scenario", "algorithm"))
    attach_step_runtime_statistics(summary, per_step, ("scenario", "algorithm"))
    statistical_tests = paired_comparisons(episode_records, metric="external_objective")
    base_statistical_tests = paired_comparisons(episode_records, metric="base_objective")
    independent_statistical_tests = paired_comparisons(
        episode_records, metric="independent_service_score"
    )
    print("[stage2] primary comparisons complete", flush=True)
    # 完整消融：公共方法复用主实验，另外计算三个模块消融。
    ablation_records = [r for r in episode_records if r["scenario"] == "balanced" and
                        r["algorithm"] in ("CA-HMCD", "No-Synergy", "No-Stability")]
    balanced_params = scenario_parameters("balanced")
    for algorithm in ("No-Compatibility", "No-Redundancy"):
        for rep in range(seeds):
            seed = base_seed + rep
            metrics, _ = run_episode(algorithm, seed, steps, int(balanced_params["resources"]),
                                     int(balanced_params["tasks"]), "balanced", top_k)
            ablation_records.append({"scenario": "balanced", "algorithm": algorithm,
                                     "seed": seed, **metrics})
    ablation = summarise(ablation_records, ("algorithm",))
    ablation_tests = paired_comparisons(ablation_records, metric="external_objective")
    base_ablation_tests = paired_comparisons(ablation_records, metric="base_objective")
    print("[stage2] ablation study complete", flush=True)
    # 机制控制实验：分别只改变协同强度和环境动态程度。
    mechanism_records: List[Dict[str, object]] = []
    validation_steps = max(8, steps // 2)
    # 主实验使用完整 seeds；机制、噪声和参考解属于验证套件，使用有界重复数避免
    # 与大规模组合搜索串行时超过可复现的运行预算。
    validation_seeds = seeds
    scarce_params = scenario_parameters("scarce")
    for scale in (0.0, .5, 1.0, 1.5, 2.0):
        for algorithm in ("CA-HMCD", "No-Synergy"):
            for rep in range(validation_seeds):
                seed = base_seed + rep
                metrics, _ = run_episode(algorithm, seed, validation_steps, int(scarce_params["resources"]),
                                         int(scarce_params["tasks"]), "scarce", top_k,
                                         synergy_scale=scale,
                                         node_limit_override=node_budgets["validation"])
                mechanism_records.append({"experiment": "synergy_strength", "level": scale,
                                          "algorithm": algorithm, "seed": seed, **metrics})
    volatile_params = scenario_parameters("volatile")
    for volatility in (0.0, .05, .10, .20, .30):
        for algorithm in ("CA-HMCD", "No-Stability"):
            for rep in range(validation_seeds):
                seed = base_seed + rep
                metrics, _ = run_episode(algorithm, seed, validation_steps, int(volatile_params["resources"]),
                                         int(volatile_params["tasks"]), "volatile", top_k,
                                         volatility_override=volatility,
                                         node_limit_override=node_budgets["validation"])
                mechanism_records.append({"experiment": "dynamic_volatility", "level": volatility,
                                          "algorithm": algorithm, "seed": seed, **metrics})
    mechanism = summarise(mechanism_records, ("experiment", "level", "algorithm"))
    print("[stage2] mechanism controls complete", flush=True)
    # 感知噪声鲁棒性，决策使用扰动状态，外部评分仍使用真实状态。
    robustness_records: List[Dict[str, object]] = []
    for noise in (0.0, .05, .10, .20, .30):
        for rep in range(validation_seeds):
            seed = base_seed + rep
            metrics, _ = run_episode("CA-HMCD", seed, validation_steps,
                                     int(volatile_params["resources"]), int(volatile_params["tasks"]),
                                     "volatile", top_k, noise_level=noise,
                                     node_limit_override=node_budgets["validation"])
            robustness_records.append({"noise_level": noise, "algorithm": "CA-HMCD",
                                       "seed": seed, **metrics})
    robustness = summarise(robustness_records, ("noise_level", "algorithm"))
    baseline_objective = next(float(r["objective_mean"]) for r in robustness if r["noise_level"] == 0.0)
    for row in robustness:
        denominator = max(abs(baseline_objective), 1e-9)
        row["objective_degradation_absolute"] = round(
            baseline_objective - float(row["objective_mean"]), 6)
        row["objective_degradation_percent"] = round(
            100.0 * (baseline_objective - float(row["objective_mean"])) / denominator, 6)
    print("[stage2] perception robustness complete", flush=True)
    optimality = optimality_experiment(validation_seeds, base_seed, top_k)
    # 规模实验采用 CA-HMCD 与 Greedy，任务数增大时自动触发分支定界上限后的快速修复。
    scalability_records: List[Dict[str, object]] = []
    scalability_per_step: List[Dict[str, object]] = []
    scale_steps = 3
    scale_reps = max(30, seeds)
    for resource_count, task_count in ((8, 5), (12, 8), (16, 10), (20, 12),
                                       (24, 16), (32, 20), (40, 30)):
        scale_node_limit = (
            node_budgets["scale_small"] if resource_count <= 20
            else node_budgets["scale_medium"] if resource_count <= 24
            else node_budgets["scale_large"]
        )
        for algorithm in ("CA-HMCD", "Greedy"):
            for rep in range(scale_reps):
                seed = base_seed + rep
                metrics, scale_rows = run_episode(
                    algorithm, seed, scale_steps, resource_count, task_count,
                    "volatile", top_k, node_limit_override=scale_node_limit,
                    force_all_tasks_active=True)
                scalability_records.append({"scenario": f"scale-{resource_count}x{task_count}",
                                            "resources": resource_count, "tasks": task_count,
                                            "algorithm": algorithm, "seed": seed, **metrics})
                scalability_per_step.extend(
                    {"resources": resource_count, "tasks": task_count, **row}
                    for row in scale_rows)
    scalability = summarise(scalability_records, ("resources", "tasks", "algorithm"))
    attach_step_runtime_statistics(
        scalability, scalability_per_step, ("resources", "tasks", "algorithm"))
    scalability_tests = paired_comparisons(
        scalability_records, metric="external_objective")
    print("[stage2] scalability study complete", flush=True)
    # 半合成精确参考解与 K-精度-时间权衡。
    reference_solver = reference_solver_experiment(seeds, base_seed, top_k)
    # 冗余压力场景的完整主算法比较，专门验证同类资源堆叠代价。
    redundancy_records: List[Dict[str, object]] = []
    redundancy_per_step: List[Dict[str, object]] = []
    stress_params = scenario_parameters("redundancy_stress")
    for algorithm in ("CA-HMCD", "No-Redundancy", "No-Synergy", "Greedy"):
        for rep in range(max(30, seeds)):
            seed = base_seed + rep
            metrics, stress_rows = run_episode(
                algorithm, seed, steps, int(stress_params["resources"]),
                int(stress_params["tasks"]), "redundancy_stress", top_k)
            redundancy_records.append({"scenario": "redundancy_stress", "algorithm": algorithm,
                                       "seed": seed, **metrics})
            redundancy_per_step.extend(stress_rows)
    redundancy_stress = summarise(redundancy_records, ("scenario", "algorithm"))
    attach_step_runtime_statistics(
        redundancy_stress, redundancy_per_step, ("scenario", "algorithm"))
    redundancy_tests = paired_comparisons(redundancy_records, metric="external_objective")
    redundancy_metric_tests: List[Dict[str, object]] = []
    for metric in ("same_type_pair_ratio", "capability_overlap", "marginal_gain_waste"):
        redundancy_metric_tests.extend(paired_comparisons(redundancy_records, metric=metric))
    print("[stage2] redundancy stress study complete", flush=True)
    # Orthogonal solver comparison: the model flags and candidate pool are held fixed.
    fairness_records: List[Dict[str, object]] = []
    fairness_algorithms = ("CA-HMCD", "CA-HMCD-GreedySolver",
                           "No-Synergy", "No-Synergy-GreedySolver")
    fairness_seeds = seeds
    for scenario in ("balanced", "scarce"):
        p = scenario_parameters(scenario)
        for algorithm in fairness_algorithms:
            for rep in range(fairness_seeds):
                seed = base_seed + rep
                metrics, _ = run_episode(algorithm, seed, steps, int(p["resources"]),
                                         int(p["tasks"]), scenario, top_k,
                                         candidate_pool_mode="independent")
                fairness_records.append({"scenario": scenario, "algorithm": algorithm,
                                         "seed": seed, **metrics})
    solver_fairness = summarise(fairness_records, ("scenario", "algorithm"))

    # Candidate-pool sensitivity: shared complete-model pools are retained only as a
    # diagnostic, while independent pools are used for all primary comparisons.
    pool_records: List[Dict[str, object]] = []
    for pool_mode in ("independent", "shared"):
        for algorithm in ("CA-HMCD", "No-Synergy", "No-Stability"):
            for rep in range(fairness_seeds):
                seed = base_seed + rep
                metrics, _ = run_episode(algorithm, seed, steps,
                                         int(balanced_params["resources"]),
                                         int(balanced_params["tasks"]), "balanced", top_k,
                                         candidate_pool_mode=pool_mode)
                pool_records.append({"scenario": "balanced", "algorithm": algorithm,
                                     "candidate_pool_mode": pool_mode,
                                     "seed": seed, **metrics})
    candidate_pool_sensitivity = summarise(pool_records,
                                           ("scenario", "algorithm", "candidate_pool_mode"))
    # External-baseline pool control. Independent-pool observations are reused
    # from the primary experiment; only the shared-pool arm is rerun.
    external_pool_records: List[Dict[str, object]] = [
        {
            **record,
            "candidate_pool_mode": "independent",
        }
        for record in episode_records
        if record["algorithm"] in ("CA-HMCD", "External-Auction", "Greedy")
    ]
    for scenario in ("balanced", "scarce", "volatile", "semisynthetic"):
        p = scenario_parameters(scenario)
        for algorithm in ("CA-HMCD", "External-Auction", "Greedy"):
            for rep in range(fairness_seeds):
                seed = base_seed + rep
                metrics, _ = run_episode(
                    algorithm,
                    seed,
                    steps,
                    int(p["resources"]),
                    int(p["tasks"]),
                    scenario,
                    top_k,
                    node_limit_override=node_budgets["primary"],
                    candidate_pool_mode="shared",
                )
                external_pool_records.append({
                    "scenario": scenario,
                    "candidate_pool_mode": "shared",
                    "algorithm": algorithm,
                    "seed": seed,
                    **metrics,
                })
    external_pool_summary = summarise(
        external_pool_records,
        ("scenario", "candidate_pool_mode", "algorithm"),
    )
    external_pool_tests: List[Dict[str, object]] = []
    for pool_mode in ("independent", "shared"):
        subset = [
            row for row in external_pool_records
            if row["candidate_pool_mode"] == pool_mode
        ]
        for row in paired_comparisons(
            subset, metric="independent_service_score"
        ):
            row["candidate_pool_mode"] = pool_mode
            external_pool_tests.append(row)
    external_pool_mode_effects: List[Dict[str, object]] = []
    for scenario in ("balanced", "scarce", "volatile", "semisynthetic"):
        for algorithm in ("CA-HMCD", "External-Auction", "Greedy"):
            lookup = {
                (str(row["candidate_pool_mode"]), int(row["seed"])): float(
                    row["independent_service_score"]
                )
                for row in external_pool_records
                if row["scenario"] == scenario and row["algorithm"] == algorithm
            }
            paired_seeds = [
                base_seed + rep
                for rep in range(fairness_seeds)
                if ("independent", base_seed + rep) in lookup
                and ("shared", base_seed + rep) in lookup
            ]
            stats = paired_statistics(
                [lookup[("independent", seed)] for seed in paired_seeds],
                [lookup[("shared", seed)] for seed in paired_seeds],
            )
            external_pool_mode_effects.append({
                "scenario": scenario,
                "algorithm": algorithm,
                "metric": "independent_service_score",
                "contrast": "independent-minus-shared",
                **{key: round(value, 8) for key, value in stats.items()},
            })
    pruning_records: List[Dict[str, object]] = []
    for algorithm in ("CA-HMCD", "No-Pruning"):
        for rep in range(fairness_seeds):
            seed = base_seed + rep
            metrics, _ = run_episode(algorithm, seed, steps,
                                     int(balanced_params["resources"]),
                                     int(balanced_params["tasks"]), "balanced", top_k,
                                     candidate_pool_mode="independent",
                                     node_limit_override=node_budgets["pruning_control"])
            pruning_records.append({"scenario": "balanced", "algorithm": algorithm,
                                    "seed": seed, **metrics})
    pruning_control = summarise(pruning_records, ("scenario", "algorithm"))
    protocol_audit = audit_paired_protocol(per_step, ALGORITHMS, seeds, steps)
    print("[stage2] fairness controls and protocol audit complete", flush=True)

    # Engineering-shaped validation profiles use the same paired seeds,
    # horizon, budget, repair operator and evaluation stack as the main study.
    engineering_records: List[Dict[str, object]] = []
    engineering_scenarios = (
        "airport_corridor", "energy_facility", "public_event",
        "urban_corridor", "industrial_zone",
    )
    for scenario in engineering_scenarios:
        p = scenario_parameters(scenario)
        for algorithm in ("CA-HMCD", "External-Auction", "Greedy", "Random"):
            for rep in range(seeds):
                seed = base_seed + rep
                metrics, _ = run_episode(
                    algorithm, seed, steps, int(p["resources"]), int(p["tasks"]),
                    scenario, top_k, force_all_tasks_active=True,
                    node_limit_override=node_budgets["engineering"],
                )
                engineering_records.append({
                    "scenario": scenario, "algorithm": algorithm,
                    "seed": seed, "application_validation": True, **metrics,
                })
    engineering_summary = summarise(engineering_records, ("scenario", "algorithm"))
    engineering_tests = paired_comparisons(
        engineering_records, metric="independent_service_score"
    )
    print("[stage2] engineering validation complete", flush=True)

    # One-factor parameter robustness on the same volatile trajectories.
    parameter_robustness_records: List[Dict[str, object]] = []
    robustness_grid = {
        "alpha_cost": (0.06, 0.12, 0.24),
        "beta_time": (0.04, 0.08, 0.16),
        "lambda_switch": (0.05, 0.10, 0.20),
        "lambda_redundancy": (0.06, 0.12, 0.24),
        "gamma_unserved": (0.10, 0.20, 0.40),
        "synergy_scale": (0.50, 1.00, 1.50),
    }
    robustness_scenario = "volatile"
    robustness_params = scenario_parameters(robustness_scenario)
    for parameter, levels in robustness_grid.items():
        for level in levels:
            overrides: Dict[str, float] = {}
            synergy_scale = 1.0
            if parameter == "synergy_scale":
                synergy_scale = level
            else:
                overrides[parameter] = level
            for algorithm in ("CA-HMCD", "External-Auction", "Greedy"):
                for rep in range(seeds):
                    seed = base_seed + rep
                    metrics, _ = run_episode(
                        algorithm, seed, steps,
                        int(robustness_params["resources"]),
                        int(robustness_params["tasks"]),
                        robustness_scenario, top_k,
                        synergy_scale=synergy_scale,
                        node_limit_override=node_budgets["parameter_robustness"],
                        model_parameters=overrides,
                    )
                    parameter_robustness_records.append({
                        "scenario": f"robustness-{parameter}-{level:g}",
                        "experiment": "parameter_robustness",
                        "parameter": parameter, "level": level,
                        "algorithm": algorithm, "seed": seed, **metrics,
                    })
    parameter_robustness = summarise(
        parameter_robustness_records,
        ("experiment", "parameter", "level", "algorithm")
    )
    parameter_robustness_tests = paired_comparisons(
        parameter_robustness_records, metric="independent_service_score"
    )
    baseline_records = [
        record for record in episode_records
        if record["algorithm"] in ("CA-HMCD", "External-Auction", "Greedy", "Random")
    ]
    external_baseline_tests = paired_comparisons(
        baseline_records, metric="independent_service_score"
    )
    print("[stage2] parameter robustness and external baseline tests complete", flush=True)
    write_csv(output_dir / "summary.csv", summary)
    write_csv(output_dir / "per_step.csv", per_step)
    write_csv(output_dir / "ablation.csv", ablation)
    write_csv(output_dir / "ablation_tests.csv", ablation_tests)
    write_csv(output_dir / "base_ablation_tests.csv", base_ablation_tests)
    write_csv(output_dir / "scalability.csv", scalability)
    write_csv(output_dir / "scalability_per_step.csv", scalability_per_step)
    write_csv(output_dir / "scalability_tests.csv", scalability_tests)
    write_csv(output_dir / "reference_solver.csv", reference_solver)
    write_csv(output_dir / "redundancy_stress.csv", redundancy_stress)
    write_csv(output_dir / "redundancy_per_step.csv", redundancy_per_step)
    write_csv(output_dir / "redundancy_tests.csv", redundancy_tests)
    write_csv(output_dir / "redundancy_metric_tests.csv", redundancy_metric_tests)
    write_csv(output_dir / "solver_fairness.csv", solver_fairness)
    write_csv(output_dir / "candidate_pool_sensitivity.csv", candidate_pool_sensitivity)
    write_csv(
        output_dir / "external_baseline_pool_fairness.csv",
        external_pool_summary,
    )
    write_csv(
        output_dir / "external_baseline_pool_fairness_tests.csv",
        external_pool_tests,
    )
    write_csv(
        output_dir / "external_baseline_pool_mode_effects.csv",
        external_pool_mode_effects,
    )
    write_csv(output_dir / "pruning_control.csv", pruning_control)
    write_csv(output_dir / "optimality.csv", optimality)
    write_csv(output_dir / "mechanism.csv", mechanism)
    write_csv(output_dir / "robustness.csv", robustness)
    write_csv(output_dir / "statistical_tests.csv", statistical_tests)
    write_csv(output_dir / "base_statistical_tests.csv", base_statistical_tests)
    write_csv(output_dir / "independent_statistical_tests.csv", independent_statistical_tests)
    write_csv(output_dir / "parameter_provenance.csv", parameter_provenance())
    write_csv(output_dir / "protocol_audit.csv", protocol_audit)
    write_csv(output_dir / "engineering_validation.csv", engineering_summary)
    write_csv(output_dir / "engineering_validation_tests.csv", engineering_tests)
    write_csv(output_dir / "parameter_robustness.csv", parameter_robustness)
    write_csv(output_dir / "parameter_robustness_tests.csv", parameter_robustness_tests)
    write_csv(output_dir / "external_baseline_tests.csv", external_baseline_tests)
    report = build_analysis_report(summary, optimality, robustness, mechanism,
                                   statistical_tests, top_k, seeds, steps, ablation_tests,
                                   reference_solver, redundancy_stress, solver_fairness,
                                   candidate_pool_sensitivity, pruning_control)
    (output_dir / "analysis_report.md").write_text(report, encoding="utf-8")
    compliance = build_compliance_report(output_dir, summary, reference_solver,
                                         redundancy_stress, scalability, seeds, steps)
    (output_dir / "compliance_report.md").write_text(compliance, encoding="utf-8")
    stage2_config = {
        "run_date": "2026-09-16",
        "code_version": "ca-hmcd-stage2-upgrade-v2.3",
        "seeds": seeds,
        "steps": steps,
        "top_k": top_k,
        "base_seed": base_seed,
        "primary_algorithms": list(ALGORITHMS),
    }
    stage2_report = build_stage2_report(
        stage2_config, summary, summary, external_baseline_tests,
        protocol_audit, engineering_summary, parameter_robustness,
    )
    (output_dir / "stage2_experiment_report.md").write_text(
        stage2_report, encoding="utf-8"
    )
    raw = {"config": {"seeds": seeds, "steps": steps, "top_k": top_k,
                      "base_seed": base_seed, "scalability_seeds": scale_reps,
                      "scalability_steps": scale_steps,
                      "scalability_all_tasks_active": True,
                      "value_model_version": MODEL_VERSION,
                      "dominance_rule": "history-aware-switch-adjusted",
                       "fallback_policy": "greedy-warm-start-best-incumbent-with-frontier-bound",
                       "external_evaluator": EVALUATOR_VERSION,
                       "independent_evaluator": SERVICE_ENDPOINT_VERSION,
                       "fairness_protocol": "paired-seeds-shared-state-fingerprint-shared-active-task-ids",
                       "external_baseline": "External-Auction",
                       "engineering_scenarios": list(engineering_scenarios),
                       "parameter_robustness_grid": robustness_grid,
                       "node_budgets": node_budgets,
                       "external_evaluator_terms": ["compatibility", "bounded complementarity",
                                                    "physical expenditure", "redundancy penalty",
                                                    "switching", "unserved-task penalty"]},
           "episodes": episode_records, "per_step": per_step, "ablation": ablation_records,
           "ablation_tests": ablation_tests, "base_ablation_tests": base_ablation_tests,
           "mechanism": mechanism_records, "robustness": robustness_records,
           "optimality": optimality, "reference_solver": reference_solver,
           "scalability": scalability_records, "scalability_per_step": scalability_per_step,
           "scalability_tests": scalability_tests,
           "redundancy_stress": redundancy_records,
           "redundancy_per_step": redundancy_per_step,
           "redundancy_tests": redundancy_tests,
           "redundancy_metric_tests": redundancy_metric_tests,
            "statistical_tests": statistical_tests,
            "base_statistical_tests": base_statistical_tests,
            "independent_statistical_tests": independent_statistical_tests}
    raw["solver_fairness"] = fairness_records
    raw["candidate_pool_sensitivity"] = pool_records
    raw["external_baseline_pool_fairness"] = external_pool_records
    raw["external_baseline_pool_fairness_tests"] = external_pool_tests
    raw["external_baseline_pool_mode_effects"] = external_pool_mode_effects
    raw["pruning_control"] = pruning_records
    raw["protocol_audit"] = protocol_audit
    raw["engineering_validation"] = engineering_records
    raw["engineering_validation_tests"] = engineering_tests
    raw["parameter_robustness"] = parameter_robustness_records
    raw["parameter_robustness_tests"] = parameter_robustness_tests
    raw["external_baseline_tests"] = external_baseline_tests
    (output_dir / "raw_results.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "ablation": ablation, "scalability": scalability,
            "optimality": optimality, "mechanism": mechanism, "robustness": robustness,
             "statistical_tests": statistical_tests,
             "base_statistical_tests": base_statistical_tests,
             "independent_statistical_tests": independent_statistical_tests,
             "ablation_tests": ablation_tests, "base_ablation_tests": base_ablation_tests,
            "reference_solver": reference_solver, "redundancy_stress": redundancy_stress,
            "redundancy_tests": redundancy_tests,
            "redundancy_metric_tests": redundancy_metric_tests,
            "scalability_tests": scalability_tests,
             "solver_fairness": solver_fairness,
             "candidate_pool_sensitivity": candidate_pool_sensitivity,
             "external_baseline_pool_fairness": external_pool_summary,
             "external_baseline_pool_fairness_tests": external_pool_tests,
             "external_baseline_pool_mode_effects": external_pool_mode_effects,
             "pruning_control": pruning_control,
             "protocol_audit": protocol_audit,
             "engineering_validation": engineering_summary,
             "engineering_validation_tests": engineering_tests,
             "parameter_robustness": parameter_robustness,
             "parameter_robustness_tests": parameter_robustness_tests,
             "external_baseline_tests": external_baseline_tests,
             "output_dir": str(output_dir.resolve())}


def run_simulation(seed: int = 7, steps: int = 12, top_k: int = 8,
                   resource_count: int = 8, task_count: int = 5) -> Dict[str, object]:
    """兼容旧接口：执行一个 CA-HMCD 均衡场景并返回逐周期结果。"""
    metrics, rows = run_episode("CA-HMCD", seed, steps, resource_count, task_count, "balanced", top_k)
    return {"seed": seed, "steps": steps, "metrics": metrics, "history": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="CA-HMCD 论文实验与动态协同分配仿真")
    parser.add_argument("--mode", choices=("demo", "paper"), default="paper")
    parser.add_argument("--seed", type=int, default=5000)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--top-k", type=int, default=12,
                        help="每个任务保留的候选组合数；小规模验证支持默认值 12")
    parser.add_argument("--resources", type=int, default=8)
    parser.add_argument("--tasks", type=int, default=5)
    parser.add_argument("--replications", type=int, default=30,
                        help="独立随机种子数；投稿实验建议设为 30")
    parser.add_argument("--output-dir", type=Path, default=Path("ca_hmcd_experiment"))
    parser.add_argument("--json", type=Path, default=None, help="demo 模式下保存 JSON")
    args = parser.parse_args()
    if args.mode == "demo":
        result = run_simulation(args.seed, args.steps, args.top_k, args.resources, args.tasks)
        print("CA-HMCD 单场景仿真完成")
        print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
        for row in result["history"]:
            print(f"t={row['step']:02d} 覆盖率={row['coverage']:.3f} 成功得分={row['success_score']:.3f} "
                  f"目标值={row['objective']:.4f} 切换数={row['switch_count']}")
        if args.json:
            args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"结果已保存：{args.json.resolve()}")
        return
    result = run_paper_experiment(args.output_dir, args.replications, args.steps, args.top_k, args.seed)
    print(f"论文实验完成，结果目录：{result['output_dir']}")
    print("主实验（均衡场景）核心指标：")
    for row in result["summary"]:
        if False and row["scenario"] == "balanced":
            print(f"  {row['algorithm']:12s} 目标值={row['objective_mean']:.4f}±{row['objective_std']:.4f} "
                  f"覆盖率={row['coverage_mean']:.3f} 成功得分={row['success_score_mean']:.3f} "
                  f"运行时间(ms)={row['runtime_ms_mean']:.3f}")
    print("Balanced-scene operational metrics (base_objective):")
    for row in result["summary"]:
        if row["scenario"] == "balanced":
            print(f"  {row['algorithm']:12s} base={row['base_objective_mean']:.4f}±{row['base_objective_std']:.4f} "
                  f"coverage={row['base_coverage_mean']:.3f} success_score={row['base_success_score_mean']:.3f} "
                  f"end_to_end_ms={row['end_to_end_runtime_ms_mean']:.3f}")
    print("Additional outputs: solver_fairness.csv and candidate_pool_sensitivity.csv")
    gaps = [float(row["optimality_gap_percent"]) for row in result["optimality"]
            if int(row["top_k"]) == args.top_k]
    hits = [row for row in result["optimality"] if int(row["top_k"]) == args.top_k]
    print(f"小规模最优性：平均差距={statistics.fmean(gaps):.3f}%，"
          f"最优命中率={statistics.fmean(1.0 if row['optimal_hit'] else 0.0 for row in hits):.1%}")
    print("已生成：summary、per_step、ablation、scalability、optimality、mechanism、robustness、"
          "reference_solver、redundancy_stress、statistical_tests、ablation_tests、"
          "parameter_provenance CSV、analysis_report.md、compliance_report.md 及 raw_results.json")


if __name__ == "__main__":
    main()

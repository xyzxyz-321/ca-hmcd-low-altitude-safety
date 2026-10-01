"""Versioned CA-HMCD framework with decoupled model, candidates, and solvers.

The Stage 9 implementation remains in ``ca_hmcd_simulation.py`` and is not
modified. This module composes its registered value model through explicit
candidate-policy, solver, repair, and evaluation interfaces.
"""

from __future__ import annotations

import time
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from typing import Dict, Literal, Mapping, Optional, Sequence, Tuple

import ca_hmcd_simulation as core


FRAMEWORK_VERSION = "ca-hmcd-framework-v3.1-stage2-closed"
CandidateMode = Literal["full", "fixed_k", "dominance_only", "adaptive_k"]
SolverBackend = Literal["highs", "bounded_search", "greedy"]


@dataclass(frozen=True)
class ValueModelConfig:
    """Registered coalition-value model independent of a solver backend."""

    alpha_cost: float = core.ALPHA_COST
    beta_time: float = core.BETA_TIME
    lambda_switch: float = core.LAMBDA_SWITCH
    lambda_redundancy: float = core.LAMBDA_REDUNDANCY
    gamma_unserved: float = core.GAMMA_UNSERVED
    synergy_scale: float = 1.0
    use_complementarity: bool = True
    use_redundancy: bool = True
    use_compatibility: bool = True
    use_stability: bool = True

    @property
    def effective_switch_lambda(self) -> float:
        return self.lambda_switch if self.use_stability else 0.0


@dataclass(frozen=True)
class AdaptiveKConfig:
    """Pre-registered Stage 2 adaptive candidate-retention rule."""

    min_k: int = 12
    max_k: int = 48
    load_intercept: float = 8.0
    load_slope: float = 2.0
    count_intercept: float = 4.0
    count_sqrt_scale: float = 1.0
    boundary_relative_gap: float = 0.02
    extension_step: int = 4
    apply_dominance: bool = True

    def __post_init__(self) -> None:
        if self.min_k < 1:
            raise ValueError("min_k must be >= 1")
        if self.max_k < self.min_k:
            raise ValueError("max_k must be >= min_k")
        if self.load_slope < 0.0 or self.count_sqrt_scale < 0.0:
            raise ValueError("adaptive K slopes must be non-negative")
        if not 0.0 <= self.boundary_relative_gap <= 1.0:
            raise ValueError("boundary_relative_gap must be in [0, 1]")
        if self.extension_step < 1:
            raise ValueError("extension_step must be >= 1")


@dataclass(frozen=True)
class CandidatePolicy:
    """Candidate management policy applied after complete enumeration."""

    mode: CandidateMode
    top_k: Optional[int] = None
    adaptive_config: Optional[AdaptiveKConfig] = None

    def __post_init__(self) -> None:
        if self.mode == "fixed_k":
            if self.top_k is None or self.top_k < 1:
                raise ValueError("fixed_k requires top_k >= 1")
            if self.adaptive_config is not None:
                raise ValueError("fixed_k does not accept adaptive_config")
        elif self.mode == "adaptive_k":
            if self.top_k is not None:
                raise ValueError("adaptive_k does not accept top_k")
            if self.adaptive_config is None:
                object.__setattr__(
                    self, "adaptive_config", AdaptiveKConfig()
                )
        elif self.top_k is not None or self.adaptive_config is not None:
            raise ValueError(
                f"{self.mode} does not accept top_k or adaptive_config"
            )

    @classmethod
    def full(cls) -> "CandidatePolicy":
        return cls("full")

    @classmethod
    def fixed_k(cls, top_k: int = 12) -> "CandidatePolicy":
        return cls("fixed_k", top_k)

    @classmethod
    def dominance_only(cls) -> "CandidatePolicy":
        return cls("dominance_only")

    @classmethod
    def adaptive_k(
        cls, config: Optional[AdaptiveKConfig] = None
    ) -> "CandidatePolicy":
        return cls("adaptive_k", adaptive_config=config or AdaptiveKConfig())


@dataclass(frozen=True)
class SolverConfig:
    backend: SolverBackend
    node_limit: int = 4000

    def __post_init__(self) -> None:
        if self.node_limit < 1:
            raise ValueError("node_limit must be >= 1")


@dataclass(frozen=True)
class MethodProfile:
    name: str
    candidate_policy: CandidatePolicy
    solver: SolverConfig
    status: str = "ACTIVE"
    note: str = ""


METHOD_PROFILES: Mapping[str, MethodProfile] = {
    "CA-HMCD-Exact": MethodProfile(
        "CA-HMCD-Exact",
        CandidatePolicy.full(),
        SolverConfig("highs", 4000),
        note="Complete candidate pool solved by SciPy/HiGHS.",
    ),
    "CA-HMCD-FixedK": MethodProfile(
        "CA-HMCD-FixedK",
        CandidatePolicy.fixed_k(12),
        SolverConfig("bounded_search", 4000),
        status="ACTIVE_STAGE9_REFERENCE",
        note="Stage 9 fixed-K retained-pool implementation.",
    ),
    "CA-HMCD-NoPruning": MethodProfile(
        "CA-HMCD-NoPruning",
        CandidatePolicy.full(),
        SolverConfig("bounded_search", 4000),
        status="ACTIVE_CONTROL",
        note="Complete candidate pool with the registered bounded search.",
    ),
    "CA-HMCD-RT": MethodProfile(
        "CA-HMCD-RT",
        CandidatePolicy.adaptive_k(),
        SolverConfig("bounded_search", 4000),
        status="EXPERIMENTAL_GATE_A_FAILED",
        note=(
            "Executable pre-registered adaptive-K policy retained for "
            "reproducibility. Stage 2 Gate A failed because high-load node "
            "demand remained too close to the Full control; do not present "
            "this profile as a validated real-time method."
        ),
    ),
}


@dataclass
class CandidatePool:
    by_task: Dict[int, list[core.Candidate]]
    audits: Dict[int, core.CandidateGenerationAudit]
    policy: CandidatePolicy
    generation_runtime_ms: float = 0.0
    retention_metadata: Dict[int, Dict[str, object]] = field(
        default_factory=dict
    )

    @property
    def total_candidates(self) -> int:
        return sum(len(values) for values in self.by_task.values())


@dataclass
class EvaluationBundle:
    complete_model: core.AllocationResult
    complete_model_violations: int
    service: Dict[str, object]


@dataclass
class DecisionOutcome:
    profile: MethodProfile
    complete_pool: CandidatePool
    retained_pool: CandidatePool
    proposed: core.AllocationResult
    final_selected: Dict[int, Tuple[int, ...]]
    repair_removed: int
    pre_repair: EvaluationBundle
    final: EvaluationBundle
    budget: float
    framework_version: str = FRAMEWORK_VERSION


def default_budget(resources: Sequence[core.Resource]) -> float:
    return sum(
        (0.20 + 0.80 * resource.ability[3]) * resource.capacity
        for resource in resources
    ) * 0.72


def build_candidates(
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    previous: Optional[Mapping[int, Tuple[int, ...]]] = None,
    model: Optional[ValueModelConfig] = None,
) -> CandidatePool:
    """Enumerate and value the complete feasible candidate pool."""
    started = time.perf_counter()
    previous = previous or {}
    model = model or ValueModelConfig()
    by_task: Dict[int, list[core.Candidate]] = {}
    audits: Dict[int, core.CandidateGenerationAudit] = {}
    for task_index in range(len(tasks)):
        audit = core.CandidateGenerationAudit(task_index=task_index)
        audits[task_index] = audit
        by_task[task_index] = core.generate_candidates(
            task_index,
            resources,
            tasks,
            top_k=1,
            alpha=model.alpha_cost,
            beta=model.beta_time,
            use_synergy=model.use_complementarity,
            use_redundancy=model.use_redundancy,
            use_adaptation=model.use_compatibility,
            synergy_scale=model.synergy_scale,
            prune=False,
            previous_combo=previous.get(task_index),
            switch_lambda=model.effective_switch_lambda,
            redundancy_lambda=model.lambda_redundancy,
            audit=audit,
            apply_compatibility_screen=False,
            apply_dominance=False,
            apply_diversity=False,
        )
    return CandidatePool(
        by_task=by_task,
        audits=audits,
        policy=CandidatePolicy.full(),
        generation_runtime_ms=(time.perf_counter() - started) * 1000,
        retention_metadata={},
    )


def evaluate_coalitions(
    pool: CandidatePool,
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    model: ValueModelConfig,
) -> CandidatePool:
    """Revalue an existing combination set without changing its identities."""
    started = time.perf_counter()
    rescored = {
        task_index: [
            core.evaluate_candidate(
                task_index,
                candidate.resource_indices,
                resources,
                tasks,
                alpha=model.alpha_cost,
                beta=model.beta_time,
                use_synergy=model.use_complementarity,
                use_redundancy=model.use_redundancy,
                use_adaptation=model.use_compatibility,
                synergy_scale=model.synergy_scale,
                redundancy_lambda=model.lambda_redundancy,
            )
            for candidate in candidates
        ]
        for task_index, candidates in pool.by_task.items()
    }
    return CandidatePool(
        by_task=rescored,
        audits={
            task_index: replace(audit)
            for task_index, audit in pool.audits.items()
        },
        policy=pool.policy,
        generation_runtime_ms=pool.generation_runtime_ms
        + (time.perf_counter() - started) * 1000,
        retention_metadata={
            task_index: dict(metadata)
            for task_index, metadata in pool.retention_metadata.items()
        },
    )


def _candidate_sort_key(
    candidate: core.Candidate,
    previous_combo: Optional[Tuple[int, ...]],
    switch_lambda: float,
) -> tuple[float, float, float]:
    return (
        core._switch_adjusted_candidate_value(  # noqa: SLF001
            candidate, previous_combo, switch_lambda
        ),
        candidate.success_score,
        -candidate.physical_cost,
    )


def _diversity_retention(
    candidates: Sequence[core.Candidate],
    resources_count: int,
    top_k: int,
    previous_combo: Optional[Tuple[int, ...]],
) -> list[core.Candidate]:
    selected: list[core.Candidate] = []
    if previous_combo:
        selected.extend(
            candidate
            for candidate in candidates
            if candidate.resource_indices == previous_combo
        )
    by_size: Dict[int, list[core.Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_size[len(candidate.resource_indices)].append(candidate)
    quota = max(1, top_k // max(1, len(by_size)))
    for size in sorted(by_size):
        for candidate in by_size[size][:quota]:
            if candidate not in selected and len(selected) < top_k:
                selected.append(candidate)
    for resource_index in range(resources_count):
        representative = next(
            (
                candidate
                for candidate in candidates
                if resource_index in candidate.resource_indices
            ),
            None,
        )
        if (
            representative is not None
            and representative not in selected
            and len(selected) < top_k
        ):
            selected.append(representative)
    for candidate in candidates:
        if candidate not in selected and len(selected) < top_k:
            selected.append(candidate)
    return selected


def _adaptive_k_target(
    candidates: Sequence[core.Candidate],
    active_task_count: int,
    config: AdaptiveKConfig,
    previous_combo: Optional[Tuple[int, ...]],
    switch_lambda: float,
) -> tuple[int, Dict[str, object]]:
    candidate_count = len(candidates)
    load_component = math.ceil(
        config.load_intercept + config.load_slope * active_task_count
    )
    count_component = math.ceil(
        config.count_intercept
        + config.count_sqrt_scale * math.sqrt(candidate_count)
    )
    initial_target = min(
        candidate_count,
        config.max_k,
        max(config.min_k, load_component, count_component),
    )
    target = initial_target
    extension_rounds = 0
    boundary_gap: Optional[float] = None
    if candidate_count and target < candidate_count:
        leading_value = _candidate_sort_key(
            candidates[0], previous_combo, switch_lambda
        )[0]
        denominator = max(abs(leading_value), 1e-9)
        while target < candidate_count and target < config.max_k:
            retained_value = _candidate_sort_key(
                candidates[target - 1], previous_combo, switch_lambda
            )[0]
            excluded_value = _candidate_sort_key(
                candidates[target], previous_combo, switch_lambda
            )[0]
            boundary_gap = (retained_value - excluded_value) / denominator
            if boundary_gap >= config.boundary_relative_gap:
                break
            target = min(
                candidate_count,
                config.max_k,
                target + config.extension_step,
            )
            extension_rounds += 1
    return target, {
        "active_task_count": active_task_count,
        "candidate_count": candidate_count,
        "load_component": load_component,
        "count_component": count_component,
        "initial_target_k": initial_target,
        "selected_target_k": target,
        "extension_rounds": extension_rounds,
        "boundary_relative_gap_at_stop": boundary_gap,
        "boundary_relative_gap_threshold": config.boundary_relative_gap,
    }


def retain_candidates(
    complete_pool: CandidatePool,
    resources_count: int,
    policy: CandidatePolicy,
    previous: Optional[Mapping[int, Tuple[int, ...]]] = None,
    switch_lambda: float = core.LAMBDA_SWITCH,
    active_task_count: Optional[int] = None,
) -> CandidatePool:
    """Apply registered dominance, fixed-K, or adaptive-K retention."""
    started = time.perf_counter()
    previous = previous or {}
    if active_task_count is None:
        active_task_count = sum(
            bool(candidates) for candidates in complete_pool.by_task.values()
        )
    retained_by_task: Dict[int, list[core.Candidate]] = {}
    retained_audits: Dict[int, core.CandidateGenerationAudit] = {}
    retention_metadata: Dict[int, Dict[str, object]] = {}
    for task_index, complete in complete_pool.by_task.items():
        previous_combo = previous.get(task_index)
        audit = replace(complete_pool.audits[task_index])
        audit.feasible_before_dominance = len(complete)
        audit.previous_candidate_feasible = any(
            candidate.resource_indices == previous_combo
            for candidate in complete
        )
        values = list(complete)
        if policy.mode in {"fixed_k", "dominance_only", "adaptive_k"}:
            values = core.nondominated_candidates(
                values,
                previous_combo=previous_combo,
                switch_lambda=switch_lambda,
            )
        audit.after_dominance = len(values)
        audit.dominance_removed = (
            audit.feasible_before_dominance - audit.after_dominance
        )
        audit.previous_candidate_protected = bool(
            previous_combo
            and audit.previous_candidate_feasible
            and any(
                candidate.resource_indices == previous_combo
                for candidate in values
            )
        )
        values.sort(
            key=lambda candidate: _candidate_sort_key(
                candidate, previous_combo, switch_lambda
            ),
            reverse=True,
        )
        target_k = len(values)
        metadata: Dict[str, object] = {
            "active_task_count": active_task_count,
            "candidate_count": len(values),
            "initial_target_k": len(values),
            "selected_target_k": len(values),
            "extension_rounds": 0,
            "boundary_relative_gap_at_stop": None,
        }
        if policy.mode == "fixed_k" and len(values) > int(policy.top_k):
            target_k = int(policy.top_k)
            values = _diversity_retention(
                values,
                resources_count,
                target_k,
                previous_combo,
            )
            metadata.update(
                {
                    "initial_target_k": target_k,
                    "selected_target_k": target_k,
                }
            )
        elif policy.mode == "adaptive_k":
            config = policy.adaptive_config
            if config is None:
                raise RuntimeError("adaptive_k is missing its registered config")
            target_k, metadata = _adaptive_k_target(
                values,
                active_task_count,
                config,
                previous_combo,
                switch_lambda,
            )
            if len(values) > target_k:
                values = _diversity_retention(
                    values,
                    resources_count,
                    target_k,
                    previous_combo,
                )
        audit.after_diversity = len(values)
        audit.diversity_removed = audit.after_dominance - audit.after_diversity
        retained_by_task[task_index] = values
        retained_audits[task_index] = audit
        retention_metadata[task_index] = {
            **metadata,
            "policy_mode": policy.mode,
            "complete_candidate_count": len(complete),
            "after_dominance": audit.after_dominance,
            "retained_candidate_count": audit.after_diversity,
        }
    return CandidatePool(
        by_task=retained_by_task,
        audits=retained_audits,
        policy=policy,
        generation_runtime_ms=complete_pool.generation_runtime_ms
        + (time.perf_counter() - started) * 1000,
        retention_metadata=retention_metadata,
    )


def solve_allocation(
    tasks: Sequence[core.Task],
    resources: Sequence[core.Resource],
    pool: CandidatePool,
    previous: Optional[Mapping[int, Tuple[int, ...]]] = None,
    model: Optional[ValueModelConfig] = None,
    solver: Optional[SolverConfig] = None,
    budget: Optional[float] = None,
) -> core.AllocationResult:
    """Solve one candidate pool using a selected interchangeable backend."""
    previous_dict = dict(previous or {})
    model = model or ValueModelConfig()
    solver = solver or SolverConfig("highs", 4000)
    budget = default_budget(resources) if budget is None else budget
    if solver.backend == "highs":
        return core.milp_allocate(
            tasks,
            resources,
            pool.by_task,
            previous_dict,
            model.effective_switch_lambda,
            budget,
            node_limit=solver.node_limit,
            gamma_unserved=model.gamma_unserved,
        )
    if solver.backend == "bounded_search":
        return core.solve_global(
            tasks,
            resources,
            pool.by_task,
            previous_dict,
            switch_lambda=model.effective_switch_lambda,
            budget=budget,
            exact=True,
            node_limit=solver.node_limit,
            gamma_unserved=model.gamma_unserved,
        )
    if solver.backend == "greedy":
        return core.greedy_allocate(
            tasks,
            resources,
            pool.by_task,
            previous_dict,
            model.effective_switch_lambda,
            budget,
            solver_name="FrameworkGreedy",
            gamma_unserved=model.gamma_unserved,
        )
    raise ValueError(f"Unsupported solver backend: {solver.backend}")


def evaluate_final_allocation(
    selected: Mapping[int, Tuple[int, ...]],
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    previous: Optional[Mapping[int, Tuple[int, ...]]] = None,
    model: Optional[ValueModelConfig] = None,
    budget: Optional[float] = None,
) -> EvaluationBundle:
    """Apply the unchanged Stage 9 complete and service evaluators."""
    previous_dict = dict(previous or {})
    model = model or ValueModelConfig()
    budget = default_budget(resources) if budget is None else budget
    complete, violations = core.evaluate_allocation(
        dict(selected),
        resources,
        tasks,
        previous_dict,
        synergy_scale=model.synergy_scale,
        budget=budget,
        use_synergy=True,
        use_redundancy=True,
        use_adaptation=True,
        switch_lambda=model.lambda_switch,
        gamma_unserved=model.gamma_unserved,
        alpha=model.alpha_cost,
        beta=model.beta_time,
        redundancy_lambda=model.lambda_redundancy,
    )
    service = core.evaluate_independent_allocation(
        dict(selected), resources, tasks, budget
    )
    return EvaluationBundle(complete, violations, service)


def repair_allocation(
    selected: Mapping[int, Tuple[int, ...]],
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    previous: Optional[Mapping[int, Tuple[int, ...]]] = None,
    model: Optional[ValueModelConfig] = None,
    budget: Optional[float] = None,
    repair_top_k: int = 12,
) -> tuple[Dict[int, Tuple[int, ...]], int]:
    """Apply the unchanged Stage 9 true-state repair operator."""
    previous_dict = dict(previous or {})
    model = model or ValueModelConfig()
    budget = default_budget(resources) if budget is None else budget
    return core.safety_repair(
        dict(selected),
        resources,
        tasks,
        previous_dict,
        top_k=repair_top_k,
        budget=budget,
        synergy_scale=model.synergy_scale,
        use_synergy=model.use_complementarity,
        use_redundancy=model.use_redundancy,
        use_adaptation=model.use_compatibility,
        switch_lambda=model.effective_switch_lambda,
        gamma_unserved=model.gamma_unserved,
        alpha=model.alpha_cost,
        beta=model.beta_time,
        redundancy_lambda=model.lambda_redundancy,
    )


def run_decision(
    perceived_resources: Sequence[core.Resource],
    perceived_tasks: Sequence[core.Task],
    true_resources: Sequence[core.Resource],
    true_tasks: Sequence[core.Task],
    previous: Optional[Mapping[int, Tuple[int, ...]]] = None,
    profile_name: str = "CA-HMCD-Exact",
    model: Optional[ValueModelConfig] = None,
    budget: Optional[float] = None,
    enable_repair: bool = True,
    repair_top_k: int = 12,
) -> DecisionOutcome:
    """Run one fully decoupled rolling decision and evaluation cycle."""
    if profile_name not in METHOD_PROFILES:
        raise ValueError(
            f"Unknown profile {profile_name}; choose from "
            f"{', '.join(METHOD_PROFILES)}"
        )
    profile = METHOD_PROFILES[profile_name]
    previous_dict = dict(previous or {})
    model = model or ValueModelConfig()
    budget = default_budget(true_resources) if budget is None else budget
    complete_pool = build_candidates(
        perceived_resources, perceived_tasks, previous_dict, model
    )
    retained_pool = retain_candidates(
        complete_pool,
        len(perceived_resources),
        profile.candidate_policy,
        previous_dict,
        model.effective_switch_lambda,
        active_task_count=sum(task.active for task in perceived_tasks),
    )
    proposed = solve_allocation(
        perceived_tasks,
        perceived_resources,
        retained_pool,
        previous_dict,
        model,
        profile.solver,
        budget,
    )
    pre_repair = evaluate_final_allocation(
        proposed.selected,
        true_resources,
        true_tasks,
        previous_dict,
        model,
        budget,
    )
    final_selected = dict(proposed.selected)
    repair_removed = 0
    if enable_repair and pre_repair.complete_model_violations:
        final_selected, repair_removed = repair_allocation(
            final_selected,
            true_resources,
            true_tasks,
            previous_dict,
            model,
            budget,
            repair_top_k,
        )
    final = evaluate_final_allocation(
        final_selected,
        true_resources,
        true_tasks,
        previous_dict,
        model,
        budget,
    )
    return DecisionOutcome(
        profile=profile,
        complete_pool=complete_pool,
        retained_pool=retained_pool,
        proposed=proposed,
        final_selected=final_selected,
        repair_removed=repair_removed,
        pre_repair=pre_repair,
        final=final,
        budget=budget,
    )


def framework_contract() -> dict[str, object]:
    return {
        "framework_version": FRAMEWORK_VERSION,
        "legacy_model_version": core.MODEL_VERSION,
        "legacy_evaluator_version": core.EVALUATOR_VERSION,
        "legacy_service_endpoint_version": core.SERVICE_ENDPOINT_VERSION,
        "profiles": {
            name: {
                "candidate_mode": profile.candidate_policy.mode,
                "top_k": profile.candidate_policy.top_k,
                "adaptive_config": (
                    asdict(profile.candidate_policy.adaptive_config)
                    if profile.candidate_policy.adaptive_config is not None
                    else None
                ),
                "solver_backend": profile.solver.backend,
                "node_limit": profile.solver.node_limit,
                "status": profile.status,
                "note": profile.note,
            }
            for name, profile in METHOD_PROFILES.items()
        },
    }

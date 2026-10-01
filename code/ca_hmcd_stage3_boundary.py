"""Stage 3 exact-versus-bounded decision-boundary experiment primitives."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, replace
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import ca_hmcd_simulation as core
from ca_hmcd_framework import (
    CandidatePolicy,
    CandidatePool,
    ValueModelConfig,
    build_candidates,
    default_budget,
)


ENGINE_VERSION = "ca-hmcd-stage3-boundary-engine-v1"
EXACT_METHOD = "CA-HMCD-Exact"
BOUNDED_METHOD = "CA-HMCD-BoundedK12-Candidate"
REGISTERED_BUDGETS_MS = (50, 100, 200, 500)
REGISTERED_EXACT_TIME_LIMIT_MS = 5000
REGISTERED_NODE_LIMIT = 100_000_000
REGISTERED_TOP_K = 12


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def state_fingerprint(
    resources: Sequence[core.Resource], tasks: Sequence[core.Task]
) -> str:
    return canonical_hash(
        {
            "resources": [asdict(resource) for resource in resources],
            "tasks": [asdict(task) for task in tasks],
        }
    )[:24]


def make_boundary_snapshot(
    seed: int, resources_count: int, tasks_count: int
) -> Tuple[List[core.Resource], List[core.Task]]:
    resources, tasks = core.make_scenario(
        seed=seed,
        resources=resources_count,
        tasks=tasks_count,
        arrival_horizon=0,
    )
    for task in tasks:
        task.arrival_step = 0
        task.active = True
    return resources, tasks


def _audit_totals(
    pool: CandidatePool, tasks: Sequence[core.Task]
) -> Dict[str, object]:
    audits = [
        audit
        for task_index, audit in pool.audits.items()
        if tasks[task_index].active
    ]
    return {
        "candidate_tasks_audited": len(audits),
        "candidate_resources_total": sum(
            audit.total_resources for audit in audits
        ),
        "candidate_resources_after_screen": sum(
            audit.screened_resources for audit in audits
        ),
        "candidate_screen_applied_tasks": sum(
            int(audit.compatibility_screen_applied) for audit in audits
        ),
        "candidate_subsets_enumerated": sum(
            audit.enumerated_subsets for audit in audits
        ),
        "candidate_feasible_before_dominance": sum(
            audit.feasible_before_dominance for audit in audits
        ),
        "candidate_after_dominance": sum(
            audit.after_dominance for audit in audits
        ),
        "candidate_after_diversity": sum(
            audit.after_diversity for audit in audits
        ),
        "candidate_dominance_removed": sum(
            audit.dominance_removed for audit in audits
        ),
        "candidate_diversity_removed": sum(
            audit.diversity_removed for audit in audits
        ),
    }


def build_fixed_k12_candidates(
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    model: Optional[ValueModelConfig] = None,
) -> CandidatePool:
    """Build the registered Stage-9 fixed-K12 candidate pool."""
    model = model or ValueModelConfig()
    started = time.perf_counter()
    by_task: Dict[int, List[core.Candidate]] = {}
    audits: Dict[int, core.CandidateGenerationAudit] = {}
    for task_index in range(len(tasks)):
        audit = core.CandidateGenerationAudit(task_index=task_index)
        audits[task_index] = audit
        by_task[task_index] = core.generate_candidates(
            task_index,
            resources,
            tasks,
            top_k=REGISTERED_TOP_K,
            alpha=model.alpha_cost,
            beta=model.beta_time,
            use_synergy=model.use_complementarity,
            use_redundancy=model.use_redundancy,
            use_adaptation=model.use_compatibility,
            synergy_scale=model.synergy_scale,
            prune=True,
            previous_combo=None,
            switch_lambda=model.effective_switch_lambda,
            redundancy_lambda=model.lambda_redundancy,
            audit=audit,
        )
    return CandidatePool(
        by_task=by_task,
        audits=audits,
        policy=CandidatePolicy.fixed_k(REGISTERED_TOP_K),
        generation_runtime_ms=(time.perf_counter() - started) * 1000.0,
        retention_metadata={},
    )


def highs_allocate_with_time_limit(
    tasks: Sequence[core.Task],
    resources: Sequence[core.Resource],
    candidates: Dict[int, List[core.Candidate]],
    previous: Dict[int, Tuple[int, ...]],
    switch_lambda: float,
    budget: float,
    time_limit_ms: int = REGISTERED_EXACT_TIME_LIMIT_MS,
    node_limit: int = REGISTERED_NODE_LIMIT,
    gamma_unserved: float = core.GAMMA_UNSERVED,
) -> Tuple[core.AllocationResult, Dict[str, object]]:
    """Run HiGHS with an explicit solver wall-clock cap and expose its status."""
    started = time.perf_counter()
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_array

    variables = [
        (task_index, candidate)
        for task_index in range(len(tasks))
        for candidate in candidates.get(task_index, [])
    ]
    if not variables:
        switches = core._switch_count(previous, {}, len(tasks))  # noqa: SLF001
        utility = -core.unserved_penalty(tasks, {}, gamma_unserved)
        result = core._make_result(  # noqa: SLF001
            {},
            utility,
            0.0,
            [],
            [],
            switches,
            switch_lambda,
            "HiGHSMILPDeadline",
            0,
            (time.perf_counter() - started) * 1000.0,
            candidates,
        )
        return result, {
            "status": 0,
            "message": "Empty candidate pool solved exactly.",
            "optimal": True,
            "time_limit_hit": False,
            "node_limit_hit": False,
            "mip_gap": 0.0,
            "mip_dual_bound": result.objective,
            "mip_node_count": 0,
        }

    row_indices: List[int] = []
    column_indices: List[int] = []
    coefficients: List[float] = []
    resource_offset = len(tasks)
    budget_row = resource_offset + len(resources)
    increments: List[float] = []
    for column, (task_index, candidate) in enumerate(variables):
        increments.append(
            core._service_marginal_value(  # noqa: SLF001
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
            "time_limit": max(0.001, float(time_limit_ms) / 1000.0),
            "mip_rel_gap": 0.0,
            "presolve": True,
        },
    )

    selected: Dict[int, Tuple[int, ...]] = {}
    selected_candidates: List[core.Candidate] = []
    if optimization.x is not None:
        for value, (task_index, candidate) in zip(
            optimization.x, variables
        ):
            if value > 0.5:
                selected[task_index] = candidate.resource_indices
                selected_candidates.append(candidate)
    physical_cost = sum(
        candidate.physical_cost for candidate in selected_candidates
    )
    utility = (
        sum(candidate.utility for candidate in selected_candidates)
        - core.unserved_penalty(tasks, selected, gamma_unserved)
    )
    switches = core._switch_count(previous, selected, len(tasks))  # noqa: SLF001
    incumbent = core._make_result(  # noqa: SLF001
        selected,
        utility,
        physical_cost,
        [candidate.success_score for candidate in selected_candidates],
        [candidate.response_time for candidate in selected_candidates],
        switches,
        switch_lambda,
        "HiGHSMILPDeadline",
        int(getattr(optimization, "mip_node_count", 0) or 0),
        (time.perf_counter() - started) * 1000.0,
        candidates,
    )
    base_objective = (
        -core.unserved_penalty(tasks, {}, gamma_unserved)
        - switch_lambda
        * core._switch_count(previous, {}, len(tasks))  # noqa: SLF001
    )
    if optimization.fun is not None and math.isfinite(float(optimization.fun)):
        linearized_objective = base_objective - float(optimization.fun)
        if abs(linearized_objective - incumbent.objective) > 1e-7:
            raise RuntimeError(
                "HiGHS objective does not match the frozen value model"
            )

    status = int(optimization.status)
    message = str(optimization.message)
    message_lower = message.lower()
    optimal = status == 0
    time_limit_hit = status == 1 and "time limit" in message_lower
    node_limit_hit = status == 1 and "node limit" in message_lower
    dual_bound = getattr(optimization, "mip_dual_bound", None)
    mip_gap = getattr(optimization, "mip_gap", None)
    metadata: Dict[str, object] = {
        "status": status,
        "message": message,
        "optimal": optimal,
        "time_limit_hit": time_limit_hit,
        "node_limit_hit": node_limit_hit,
        "mip_gap": (
            float(mip_gap)
            if mip_gap is not None and math.isfinite(float(mip_gap))
            else None
        ),
        "mip_dual_bound": (
            float(dual_bound)
            if dual_bound is not None and math.isfinite(float(dual_bound))
            else None
        ),
        "mip_node_count": int(
            getattr(optimization, "mip_node_count", 0) or 0
        ),
    }
    if optimal:
        return (
            replace(
                incumbent,
                search_upper_bound=incumbent.objective,
                fallback_optimality_gap_bound=0.0,
                fallback_gain_over_greedy=0.0,
            ),
            metadata,
        )

    greedy = core.greedy_allocate(
        tasks,
        resources,
        candidates,
        previous,
        switch_lambda,
        budget,
        "HiGHSDeadlineGreedyWarmStart",
        started,
        gamma_unserved=gamma_unserved,
    )
    use_incumbent = (
        optimization.x is not None
        and incumbent.objective > greedy.objective + 1e-12
    )
    chosen = incumbent if use_incumbent else greedy
    search_upper_bound = chosen.objective
    if dual_bound is not None and math.isfinite(float(dual_bound)):
        search_upper_bound = max(
            search_upper_bound, base_objective - float(dual_bound)
        )
    return (
        replace(
            chosen,
            solver=(
                "HiGHSDeadlineIncumbentFallback"
                if use_incumbent
                else "HiGHSDeadlineGreedyFallback"
            ),
            nodes=int(getattr(optimization, "mip_node_count", 0) or 0),
            runtime_ms=(time.perf_counter() - started) * 1000.0,
            fallback=True,
            fallback_incumbent_objective=(
                incumbent.objective if optimization.x is not None else None
            ),
            fallback_greedy_objective=greedy.objective,
            fallback_selected_incumbent=use_incumbent,
            node_limit_hit=node_limit_hit,
            search_upper_bound=search_upper_bound,
            fallback_optimality_gap_bound=max(
                0.0, search_upper_bound - chosen.objective
            ),
            fallback_gain_over_greedy=max(
                0.0, chosen.objective - greedy.objective
            ),
        ),
        metadata,
    )


def bounded_search_with_deadline(
    tasks: Sequence[core.Task],
    resources: Sequence[core.Resource],
    candidates: Dict[int, List[core.Candidate]],
    previous: Dict[int, Tuple[int, ...]],
    switch_lambda: float,
    budget: float,
    time_limit_ms: float,
    node_limit: int = REGISTERED_NODE_LIMIT,
    gamma_unserved: float = core.GAMMA_UNSERVED,
) -> Tuple[core.AllocationResult, Dict[str, object]]:
    """Interrupt retained-pool branch-and-bound on a monotonic wall clock."""
    started = time.perf_counter()
    deadline = started + max(0.0, float(time_limit_ms)) / 1000.0
    order = sorted(
        range(len(tasks)), key=lambda index: tasks[index].feature[4], reverse=True
    )
    options = {
        task_index: list(candidates.get(task_index, [])) + [None]
        for task_index in order
    }
    upper = core.retained_pool_suffix_upper_bounds(
        tasks, order, candidates, gamma_unserved
    )
    total_risk_penalty = gamma_unserved * sum(
        task.feature[4] for task in tasks if task.active
    )
    greedy = core.greedy_allocate(
        tasks,
        resources,
        candidates,
        previous,
        switch_lambda,
        budget,
        "BoundedK12GreedyWarmStart",
        started,
        gamma_unserved=gamma_unserved,
    )
    best = replace(
        greedy,
        solver="BoundedK12Search",
        nodes=0,
        runtime_ms=0.0,
        fallback=False,
    )
    nodes = 0
    time_limit_hit = time.perf_counter() >= deadline
    node_limit_hit = False
    frontier_upper_bound = -math.inf
    unexplored_frontier_nodes = 0

    def objective_upper_bound(
        position: int, utility: float, service_bonus: float
    ) -> float:
        return core.retained_pool_node_upper_bound(
            utility,
            service_bonus,
            total_risk_penalty,
            upper[position],
        )

    def search(
        position: int,
        usage: List[int],
        selected: Dict[int, Tuple[int, ...]],
        utility: float,
        service_bonus: float,
        physical_cost: float,
        success_scores: List[float],
        responses: List[float],
    ) -> None:
        nonlocal best, nodes, time_limit_hit, node_limit_hit
        nonlocal frontier_upper_bound, unexplored_frontier_nodes
        bound = objective_upper_bound(position, utility, service_bonus)
        if bound < best.objective - 1e-12:
            return
        if time.perf_counter() >= deadline:
            time_limit_hit = True
            frontier_upper_bound = max(frontier_upper_bound, bound)
            unexplored_frontier_nodes += 1
            return
        if nodes >= node_limit:
            node_limit_hit = True
            frontier_upper_bound = max(frontier_upper_bound, bound)
            unexplored_frontier_nodes += 1
            return
        nodes += 1
        if position == len(order):
            switches = core._switch_count(  # noqa: SLF001
                previous, selected, len(tasks)
            )
            evaluated_utility = utility - core.unserved_penalty(
                tasks, selected, gamma_unserved
            )
            result = core._make_result(  # noqa: SLF001
                selected.copy(),
                evaluated_utility,
                physical_cost,
                success_scores,
                responses,
                switches,
                switch_lambda,
                "BoundedK12Search",
                nodes,
                (time.perf_counter() - started) * 1000.0,
                candidates,
            )
            if result.objective > best.objective + 1e-12:
                best = result
            return
        task_index = order[position]
        for candidate in options[task_index]:
            if candidate is None:
                search(
                    position + 1,
                    usage,
                    selected,
                    utility,
                    service_bonus,
                    physical_cost,
                    success_scores,
                    responses,
                )
                continue
            if physical_cost + candidate.physical_cost > budget:
                continue
            if any(
                usage[index] + 1 > resources[index].capacity
                for index in candidate.resource_indices
            ):
                continue
            for index in candidate.resource_indices:
                usage[index] += 1
            selected[task_index] = candidate.resource_indices
            bonus = (
                gamma_unserved * tasks[task_index].feature[4]
                if tasks[task_index].active
                else 0.0
            )
            search(
                position + 1,
                usage,
                selected,
                utility + candidate.utility,
                service_bonus + bonus,
                physical_cost + candidate.physical_cost,
                success_scores + [candidate.success_score],
                responses + [candidate.response_time],
            )
            selected.pop(task_index, None)
            for index in candidate.resource_indices:
                usage[index] -= 1

    if not time_limit_hit:
        search(0, [0] * len(resources), {}, 0.0, 0.0, 0.0, [], [])
    runtime_ms = (time.perf_counter() - started) * 1000.0
    interrupted = time_limit_hit or node_limit_hit
    if interrupted:
        selected_incumbent = best.objective > greedy.objective + 1e-12
        search_upper_bound = max(
            best.objective,
            (
                frontier_upper_bound
                if math.isfinite(frontier_upper_bound)
                else best.objective
            ),
        )
        result = replace(
            best,
            solver=(
                "BoundedK12DeadlineIncumbent"
                if selected_incumbent
                else "BoundedK12DeadlineGreedy"
            ),
            nodes=nodes,
            runtime_ms=runtime_ms,
            fallback=True,
            fallback_incumbent_objective=best.objective,
            fallback_greedy_objective=greedy.objective,
            fallback_selected_incumbent=selected_incumbent,
            node_limit_hit=node_limit_hit,
            search_upper_bound=search_upper_bound,
            fallback_optimality_gap_bound=max(
                0.0, search_upper_bound - best.objective
            ),
            fallback_gain_over_greedy=max(
                0.0, best.objective - greedy.objective
            ),
            unexplored_frontier_nodes=unexplored_frontier_nodes,
        )
    else:
        result = replace(
            best,
            solver="BoundedK12Search",
            nodes=nodes,
            runtime_ms=runtime_ms,
            fallback=False,
            node_limit_hit=False,
            search_upper_bound=best.objective,
            fallback_optimality_gap_bound=0.0,
            fallback_gain_over_greedy=max(
                0.0, best.objective - greedy.objective
            ),
            unexplored_frontier_nodes=0,
        )
    return result, {
        "optimal_on_retained_pool": not interrupted,
        "time_limit_hit": time_limit_hit,
        "node_limit_hit": node_limit_hit,
        "requested_solver_time_limit_ms": float(time_limit_ms),
        "visited_nodes": nodes,
        "unexplored_frontier_nodes": unexplored_frontier_nodes,
    }


def _selected_task_responses(
    selected: Mapping[int, Tuple[int, ...]],
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
) -> Dict[str, float]:
    return {
        str(task_index): max(
            core.response_time(resources[index], tasks[task_index])
            for index in combo
        )
        for task_index, combo in selected.items()
        if combo
    }


def _record_result(
    method: str,
    seed: int,
    resources_count: int,
    tasks_count: int,
    fingerprint: str,
    pool: CandidatePool,
    result: core.AllocationResult,
    solver_trace: Mapping[str, object],
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    model: ValueModelConfig,
    physical_budget: float,
    candidate_generation_ms: float,
    solver_runtime_ms: float,
    decision_runtime_ms: float,
    registered_budget_ms: Optional[int],
) -> Dict[str, object]:
    complete, violations = core.evaluate_allocation(
        dict(result.selected),
        resources,
        tasks,
        {},
        model.synergy_scale,
        physical_budget,
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
        dict(result.selected), resources, tasks, physical_budget
    )
    active_task_count = sum(int(task.active) for task in tasks)
    row: Dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "method": method,
        "seed": seed,
        "resources_count": resources_count,
        "tasks_count": tasks_count,
        "scale_cell": f"R{resources_count}-T{tasks_count}",
        "state_fingerprint": fingerprint,
        "registered_budget_ms": (
            registered_budget_ms if registered_budget_ms is not None else ""
        ),
        "candidate_policy": pool.policy.mode,
        "candidate_generation_ms": candidate_generation_ms,
        "solver_runtime_ms": solver_runtime_ms,
        "decision_runtime_ms": decision_runtime_ms,
        "deadline_hit": (
            int(decision_runtime_ms <= registered_budget_ms)
            if registered_budget_ms is not None
            else ""
        ),
        "solver": result.solver,
        "nodes": result.nodes,
        "fallback": int(result.fallback),
        "search_upper_bound": result.search_upper_bound,
        "optimality_gap_bound": result.fallback_optimality_gap_bound,
        "selected_task_count": len(result.selected),
        "selected_json": json.dumps(
            {
                str(task_index): list(combo)
                for task_index, combo in sorted(result.selected.items())
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        "task_response_json": json.dumps(
            _selected_task_responses(result.selected, resources, tasks),
            sort_keys=True,
            separators=(",", ":"),
        ),
        "decision_objective": result.objective,
        "external_objective": complete.objective,
        "external_objective_per_active_task": (
            complete.objective / max(1, active_task_count)
        ),
        "external_violations": violations,
        "final_feasibility": float(
            violations == 0
            and float(service["independent_feasibility"]) == 1.0
        ),
        "independent_service_score": service[
            "independent_service_score"
        ],
        "independent_coverage": service["independent_coverage"],
        "independent_response_time": service[
            "independent_response_time"
        ],
        "independent_response_quality": service[
            "independent_response_quality"
        ],
        "independent_risk_weighted_success": service[
            "independent_risk_weighted_success"
        ],
        "independent_cost_efficiency": service[
            "independent_cost_efficiency"
        ],
        "physical_budget": physical_budget,
    }
    row.update(_audit_totals(pool, tasks))
    for key, value in solver_trace.items():
        row[f"solver_trace_{key}"] = value
    return row


def run_exact_mode(
    seed: int,
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    fingerprint: str,
    time_limit_ms: int = REGISTERED_EXACT_TIME_LIMIT_MS,
) -> Dict[str, object]:
    model = ValueModelConfig()
    physical_budget = default_budget(resources)
    decision_started = time.perf_counter()
    pool = build_candidates(resources, tasks, previous={}, model=model)
    candidate_generation_ms = (
        time.perf_counter() - decision_started
    ) * 1000.0
    solver_started = time.perf_counter()
    result, trace = highs_allocate_with_time_limit(
        tasks,
        resources,
        pool.by_task,
        {},
        model.effective_switch_lambda,
        physical_budget,
        time_limit_ms=time_limit_ms,
        node_limit=REGISTERED_NODE_LIMIT,
        gamma_unserved=model.gamma_unserved,
    )
    solver_runtime_ms = (time.perf_counter() - solver_started) * 1000.0
    decision_runtime_ms = (
        time.perf_counter() - decision_started
    ) * 1000.0
    return _record_result(
        EXACT_METHOD,
        seed,
        len(resources),
        len(tasks),
        fingerprint,
        pool,
        result,
        trace,
        resources,
        tasks,
        model,
        physical_budget,
        candidate_generation_ms,
        solver_runtime_ms,
        decision_runtime_ms,
        None,
    )


def run_bounded_mode(
    seed: int,
    resources: Sequence[core.Resource],
    tasks: Sequence[core.Task],
    fingerprint: str,
    registered_budget_ms: int,
) -> Dict[str, object]:
    model = ValueModelConfig()
    physical_budget = default_budget(resources)
    decision_started = time.perf_counter()
    pool = build_fixed_k12_candidates(resources, tasks, model)
    candidate_generation_ms = (
        time.perf_counter() - decision_started
    ) * 1000.0
    remaining_ms = max(
        0.0, float(registered_budget_ms) - candidate_generation_ms
    )
    solver_started = time.perf_counter()
    result, trace = bounded_search_with_deadline(
        tasks,
        resources,
        pool.by_task,
        {},
        model.effective_switch_lambda,
        physical_budget,
        time_limit_ms=remaining_ms,
        node_limit=REGISTERED_NODE_LIMIT,
        gamma_unserved=model.gamma_unserved,
    )
    solver_runtime_ms = (time.perf_counter() - solver_started) * 1000.0
    decision_runtime_ms = (
        time.perf_counter() - decision_started
    ) * 1000.0
    trace = {
        **trace,
        "remaining_budget_after_candidate_generation_ms": remaining_ms,
    }
    return _record_result(
        BOUNDED_METHOD,
        seed,
        len(resources),
        len(tasks),
        fingerprint,
        pool,
        result,
        trace,
        resources,
        tasks,
        model,
        physical_budget,
        candidate_generation_ms,
        solver_runtime_ms,
        decision_runtime_ms,
        registered_budget_ms,
    )


def rotated_budgets(seed_offset: int) -> Tuple[int, ...]:
    rotation = seed_offset % len(REGISTERED_BUDGETS_MS)
    return (
        REGISTERED_BUDGETS_MS[rotation:]
        + REGISTERED_BUDGETS_MS[:rotation]
    )


def run_boundary_unit(
    seed: int,
    resources_count: int,
    tasks_count: int,
    seed_offset: int,
    exact_time_limit_ms: int = REGISTERED_EXACT_TIME_LIMIT_MS,
    budgets_ms: Sequence[int] = REGISTERED_BUDGETS_MS,
) -> Dict[str, object]:
    """Run one registered snapshot with deterministic order balancing."""
    started = time.perf_counter()
    resources, tasks = make_boundary_snapshot(
        seed, resources_count, tasks_count
    )
    fingerprint = state_fingerprint(resources, tasks)
    rotated = [
        budget
        for budget in rotated_budgets(seed_offset)
        if budget in set(budgets_ms)
    ]

    rows: List[Dict[str, object]] = []

    def run_exact() -> None:
        rows.append(
            run_exact_mode(
                seed,
                resources,
                tasks,
                fingerprint,
                time_limit_ms=exact_time_limit_ms,
            )
        )

    def run_bounded() -> None:
        for budget_ms in rotated:
            rows.append(
                run_bounded_mode(
                    seed,
                    resources,
                    tasks,
                    fingerprint,
                    budget_ms,
                )
            )

    if seed_offset % 2 == 0:
        run_exact()
        run_bounded()
    else:
        run_bounded()
        run_exact()
    for position, row in enumerate(rows, start=1):
        row["execution_position_within_unit"] = position
    return {
        "engine_version": ENGINE_VERSION,
        "seed": seed,
        "seed_offset": seed_offset,
        "resources_count": resources_count,
        "tasks_count": tasks_count,
        "scale_cell": f"R{resources_count}-T{tasks_count}",
        "state_fingerprint": fingerprint,
        "unit_runtime_s": time.perf_counter() - started,
        "rows": rows,
    }

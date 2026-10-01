"""Executable audit for the CA-HMCD algorithm/model closure.

The audit checks the two lossless claims used in the manuscript and records
the heuristic approximation stages separately. It is intentionally small and
deterministic so that it can be rerun before manuscript submission.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Tuple

from ca_hmcd_simulation import (
    Candidate,
    CandidateGenerationAudit,
    GAMMA_UNSERVED,
    INDEPENDENT_SERVICE_WEIGHTS,
    LAMBDA_SWITCH,
    Resource,
    Task,
    _dominates,
    evaluate_candidate,
    evaluate_independent_allocation,
    generate_candidates,
    nondominated_candidates,
    retained_pool_node_upper_bound,
    retained_pool_suffix_upper_bounds,
    solve_global,
)


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "ca_hmcd_stage1_closure_20260916"


def make_candidate(
    task_index: int,
    resources: Tuple[int, ...],
    utility: float,
    success_score: float,
    physical_cost: float,
    response_time: float,
) -> Candidate:
    return Candidate(
        task_index=task_index,
        resource_indices=resources,
        utility=utility,
        success_score=success_score,
        physical_cost=physical_cost,
        response_time=response_time,
        complementarity_gain=0.0,
        redundancy_penalty=0.0,
        compatibility_score=1.0,
        reachable=True,
    )


def random_resources(count: int) -> List[Resource]:
    return [
        Resource(
            f"R{i}",
            ("Type-I", "Type-II", "Type-III", "Type-IV")[i % 4],
            (0.8, 0.8, 0.8, 0.1, 0.8, 1.0),
        )
        for i in range(count)
    ]


def audit_dominance_replacement(rng: random.Random, trials: int = 500) -> Dict[str, object]:
    maximum_objective_difference = 0.0
    for trial in range(trials):
        resource_count = rng.randint(4, 6)
        resources = random_resources(resource_count)
        tasks = [
            Task(f"T{j}", (0.1, 0.1, 0.1, 0.1, rng.uniform(0.2, 1.0), 1.0))
            for j in range(3)
        ]
        superset = tuple(sorted(rng.sample(range(resource_count), rng.randint(2, 3))))
        subset_size = rng.randint(1, len(superset) - 1)
        subset = tuple(sorted(rng.sample(superset, subset_size)))
        previous_combo = tuple(
            sorted(rng.sample(range(resource_count), rng.randint(0, 2)))
        )
        if previous_combo == superset:
            previous_combo = ()
        base_utility = rng.uniform(-0.1, 0.8)
        distance_subset = len(set(subset) ^ set(previous_combo))
        distance_superset = len(set(superset) ^ set(previous_combo))
        subset_utility = (
            base_utility
            + LAMBDA_SWITCH * (distance_subset - distance_superset)
            + rng.uniform(0.01, 0.2)
        )
        dominating = make_candidate(
            0,
            subset,
            subset_utility,
            rng.uniform(0.75, 1.0),
            rng.uniform(0.05, 0.2),
            rng.uniform(0.05, 0.2),
        )
        dominated = make_candidate(
            0,
            superset,
            base_utility,
            dominating.success_score - rng.uniform(0.0, 0.2),
            dominating.physical_cost + rng.uniform(0.0, 0.2),
            dominating.response_time + rng.uniform(0.0, 0.2),
        )
        if not _dominates(
            dominating, dominated, previous_combo or None, LAMBDA_SWITCH
        ):
            raise AssertionError(f"Constructed dominance pair failed at trial {trial}")

        candidates: Dict[int, List[Candidate]] = {
            0: [dominating, dominated]
        }
        for task_index in (1, 2):
            task_candidates = []
            for _ in range(2):
                combo = (rng.randrange(resource_count),)
                task_candidates.append(
                    make_candidate(
                        task_index,
                        combo,
                        rng.uniform(-0.1, 0.8),
                        rng.uniform(0.3, 0.9),
                        rng.uniform(0.05, 0.25),
                        rng.uniform(0.05, 0.4),
                    )
                )
            candidates[task_index] = task_candidates
        previous = {0: previous_combo} if previous_combo else {}
        complete = solve_global(
            tasks,
            resources,
            candidates,
            previous,
            switch_lambda=LAMBDA_SWITCH,
            budget=1.5,
            exact=True,
            node_limit=100000,
        )
        reduced_candidates = dict(candidates)
        reduced_candidates[0] = nondominated_candidates(
            candidates[0], previous_combo or None, LAMBDA_SWITCH
        )
        reduced = solve_global(
            tasks,
            resources,
            reduced_candidates,
            previous,
            switch_lambda=LAMBDA_SWITCH,
            budget=1.5,
            exact=True,
            node_limit=100000,
        )
        difference = abs(complete.objective - reduced.objective)
        maximum_objective_difference = max(maximum_objective_difference, difference)
        if difference > 1e-10:
            raise AssertionError(
                f"Dominance changed the optimum by {difference} at trial {trial}"
            )
    return {
        "trials": trials,
        "passed": trials,
        "maximum_absolute_objective_difference": maximum_objective_difference,
    }


def audit_frontier_bound(rng: random.Random, trials: int = 500) -> Dict[str, object]:
    minimum_slack = float("inf")
    for trial in range(trials):
        resource_count = rng.randint(3, 6)
        task_count = rng.randint(2, 4)
        resources = random_resources(resource_count)
        tasks = [
            Task(f"T{j}", (0.1, 0.1, 0.1, 0.1, rng.uniform(0.1, 1.0), 1.0))
            for j in range(task_count)
        ]
        candidates: Dict[int, List[Candidate]] = {}
        for task_index in range(task_count):
            values = []
            for _ in range(rng.randint(1, 3)):
                size = rng.randint(1, min(2, resource_count))
                combo = tuple(sorted(rng.sample(range(resource_count), size)))
                values.append(
                    make_candidate(
                        task_index,
                        combo,
                        rng.uniform(-0.25, 1.0),
                        rng.uniform(0.2, 0.95),
                        rng.uniform(0.05, 0.5),
                        rng.uniform(0.05, 0.8),
                    )
                )
            candidates[task_index] = values
        order = sorted(
            range(task_count), key=lambda j: tasks[j].feature[4], reverse=True
        )
        suffix = retained_pool_suffix_upper_bounds(
            tasks, order, candidates, GAMMA_UNSERVED
        )
        total_risk_penalty = GAMMA_UNSERVED * sum(
            task.feature[4] for task in tasks
        )
        root_bound = retained_pool_node_upper_bound(
            0.0, 0.0, total_risk_penalty, suffix[0]
        )
        exact = solve_global(
            tasks,
            resources,
            candidates,
            previous={},
            switch_lambda=LAMBDA_SWITCH,
            budget=rng.uniform(0.5, 1.5),
            exact=True,
            node_limit=100000,
            gamma_unserved=GAMMA_UNSERVED,
        )
        slack = root_bound - exact.objective
        minimum_slack = min(minimum_slack, slack)
        if slack < -1e-10:
            raise AssertionError(
                f"Root upper bound was violated by {-slack} at trial {trial}"
            )
    return {
        "trials": trials,
        "passed": trials,
        "minimum_upper_bound_slack": minimum_slack,
    }


def audit_value_model(rng: random.Random, trials: int = 500) -> Dict[str, object]:
    maximum_score = 0.0
    minimum_score = 1.0
    for trial in range(trials):
        resources = []
        for index in range(3):
            resources.append(
                Resource(
                    f"R{index}",
                    ("Type-I", "Type-II", "Type-IV")[index],
                    tuple(rng.uniform(0.05, 0.95) for _ in range(6)),
                )
            )
        task = Task(
            "T0",
            tuple(rng.uniform(0.1, 0.95) for _ in range(6)),
            min_resources=1,
            max_resources=3,
        )
        size = rng.randint(1, 3)
        combo = tuple(sorted(rng.sample(range(3), size)))
        coalition = evaluate_candidate(
            0,
            combo,
            resources,
            [task],
            synergy_scale=rng.uniform(0.0, 20.0),
        )
        maximum_score = max(maximum_score, coalition.success_score)
        minimum_score = min(minimum_score, coalition.success_score)
        expected_cost = sum(
            0.20 + 0.80 * resources[index].ability[3] for index in combo
        )
        if not -1e-12 <= coalition.success_score <= 1.0 + 1e-12:
            raise AssertionError(f"Coalition score left [0,1] at trial {trial}")
        if abs(coalition.physical_cost - expected_cost) > 1e-12:
            raise AssertionError(f"Physical cost mismatch at trial {trial}")
    return {
        "trials": trials,
        "passed": trials,
        "minimum_coalition_score": minimum_score,
        "maximum_coalition_score": maximum_score,
    }


def audit_approximation_accounting() -> Dict[str, object]:
    resources = random_resources(17)
    task = Task(
        "T0",
        (0.2, 0.1, 0.2, 0.2, 1.0, 1.0),
        min_resources=1,
        max_resources=1,
    )
    pruned_audit = CandidateGenerationAudit(task_index=0)
    pruned = generate_candidates(
        0, resources, [task], top_k=4, prune=True, audit=pruned_audit
    )
    exhaustive_audit = CandidateGenerationAudit(task_index=0)
    exhaustive = generate_candidates(
        0, resources, [task], top_k=4, prune=False, audit=exhaustive_audit
    )
    identities_hold = (
        pruned_audit.dominance_removed
        == pruned_audit.feasible_before_dominance - pruned_audit.after_dominance
        and pruned_audit.diversity_removed
        == pruned_audit.after_dominance - pruned_audit.after_diversity
        and exhaustive_audit.feasible_before_dominance
        == exhaustive_audit.after_dominance
        == exhaustive_audit.after_diversity
        == len(exhaustive)
    )
    if not identities_hold:
        raise AssertionError("Candidate-stage accounting identities failed")
    return {
        "passed": True,
        "pruned_retained_candidates": len(pruned),
        "exhaustive_candidates": len(exhaustive),
        "pruned_audit": vars(pruned_audit),
        "exhaustive_audit": vars(exhaustive_audit),
    }


def audit_service_endpoint() -> Dict[str, object]:
    resources = random_resources(1)
    task = Task(
        "T0",
        (0.2, 0.1, 0.2, 0.2, 1.0, 1.0),
        min_resources=1,
        max_resources=1,
    )
    valid = evaluate_independent_allocation(
        {0: (0,)}, resources, [task], budget=1.0
    )
    invalid = evaluate_independent_allocation(
        {0: (0, 0)}, resources, [task], budget=1.0
    )
    if abs(sum(INDEPENDENT_SERVICE_WEIGHTS.values()) - 1.0) > 1e-12:
        raise AssertionError("Service endpoint weights do not sum to one")
    if invalid["independent_service_score"] != 0.0:
        raise AssertionError("Infeasible allocation was not gated to zero")
    return {
        "passed": True,
        "weights": INDEPENDENT_SERVICE_WEIGHTS,
        "valid_service_score": valid["independent_service_score"],
        "invalid_service_score": invalid["independent_service_score"],
        "excluded_terms": valid["independent_evaluator_excluded_terms"],
    }


def write_report(results: Dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "stage1_closure_audit.json"
    report_path = OUTPUT_DIR / "stage1_algorithm_model_closure_report.md"
    json_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    dominance = results["dominance_replacement"]
    bound = results["retained_pool_upper_bound"]
    value = results["value_model"]
    report = f"""# CA-HMCD Stage-1 Algorithm and Model Closure Audit

Date: 2026-09-16

Overall status: **PASS**

## Executable checks

| Check | Trials | Result | Worst observed value |
|---|---:|---|---:|
| History-aware subset-dominance replacement | {dominance['trials']} | PASS | max objective difference = {dominance['maximum_absolute_objective_difference']:.3e} |
| Retained-pool branch-and-bound upper bound | {bound['trials']} | PASS | min bound slack = {bound['minimum_upper_bound_slack']:.3e} |
| Bounded coalition score and physical-cost identity | {value['trials']} | PASS | score range = [{value['minimum_coalition_score']:.6f}, {value['maximum_coalition_score']:.6f}] |
| Approximation-stage accounting | 1 deterministic case | PASS | screen, dominance, and diversity counts separated |
| Solver-decoupled service endpoint | 1 deterministic case | PASS | infeasible allocation score = 0 |

## Claim boundary

The executable audit supports lossless history-aware subset dominance and the
admissibility of the retained-pool search bound. It does not claim that the
compatibility pre-screen or fixed-K diversity retention is lossless. Those two
approximations are now recorded separately, and their empirical effect belongs
to the experimental validation stage.
"""
    report_path.write_text(report, encoding="utf-8")


def main() -> None:
    rng = random.Random(20260916)
    results: Dict[str, object] = {
        "audit_date": "2026-09-16",
        "model_version": "ca-hmcd-stage1-closure-v2.3",
        "overall_status": "PASS",
        "dominance_replacement": audit_dominance_replacement(rng),
        "retained_pool_upper_bound": audit_frontier_bound(rng),
        "value_model": audit_value_model(rng),
        "approximation_accounting": audit_approximation_accounting(),
        "service_endpoint": audit_service_endpoint(),
    }
    write_report(results)
    print(f"PASS: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

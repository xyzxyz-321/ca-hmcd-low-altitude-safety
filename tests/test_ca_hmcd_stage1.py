import unittest
import random

try:
    import scipy  # noqa: F401
except (ImportError, OSError):
    SCIPY_AVAILABLE = False
else:
    SCIPY_AVAILABLE = True

from ca_hmcd_simulation import (
    Candidate,
    CandidateGenerationAudit,
    INDEPENDENT_SERVICE_WEIGHTS,
    LAMBDA_SWITCH,
    Resource,
    Task,
    evaluate_independent_allocation,
    evaluate_candidate,
    generate_candidates,
    genetic_allocate,
    greedy_allocate,
    milp_allocate,
    nondominated_candidates,
    retained_pool_node_upper_bound,
    retained_pool_suffix_upper_bounds,
    solve_global,
)


def candidate(task_index, resources, utility, success_score, physical_cost):
    return Candidate(
        task_index=task_index,
        resource_indices=resources,
        utility=utility,
        success_score=success_score,
        physical_cost=physical_cost,
        response_time=0.1,
        complementarity_gain=0.0,
        redundancy_penalty=0.0,
        compatibility_score=1.0,
        reachable=True,
    )


class DominancePruningTests(unittest.TestCase):
    def test_previous_coalition_survives_switch_aware_pruning(self):
        smaller = candidate(0, (0,), 1.00, 0.90, 0.10)
        previous = candidate(0, (0, 1), 0.95, 0.80, 0.20)

        retained = nondominated_candidates(
            [smaller, previous],
            previous_combo=previous.resource_indices,
            switch_lambda=LAMBDA_SWITCH,
        )

        self.assertIn(previous, retained)

    def test_static_subset_dominance_still_prunes(self):
        better_subset = candidate(0, (0,), 1.00, 0.90, 0.10)
        worse_superset = candidate(0, (0, 1), 0.80, 0.80, 0.20)

        retained = nondominated_candidates(
            [better_subset, worse_superset],
            previous_combo=None,
            switch_lambda=0.0,
        )

        self.assertEqual(retained, [better_subset])

    def test_switch_adjusted_dominance_preserves_exact_objective(self):
        resources = [
            Resource("R0", "Type-I", (1, 1, 1, 0, 1, 1)),
            Resource("R1", "Type-II", (1, 1, 1, 0, 1, 1)),
        ]
        tasks = [Task("T0", (0, 0, 0, 0, 0.5, 1))]
        smaller = candidate(0, (0,), 1.00, 0.90, 0.10)
        previous = candidate(0, (0, 1), 0.95, 0.80, 0.20)
        previous_allocation = {0: previous.resource_indices}
        pruned = nondominated_candidates(
            [smaller, previous],
            previous_combo=previous.resource_indices,
            switch_lambda=LAMBDA_SWITCH,
        )

        complete_result = solve_global(
            tasks, resources, {0: [smaller, previous]}, previous_allocation,
            switch_lambda=LAMBDA_SWITCH, budget=1.0, node_limit=100,
        )
        pruned_result = solve_global(
            tasks, resources, {0: pruned}, previous_allocation,
            switch_lambda=LAMBDA_SWITCH, budget=1.0, node_limit=100,
        )

        self.assertEqual(complete_result.selected, {0: previous.resource_indices})
        self.assertEqual(pruned_result.selected, complete_result.selected)
        self.assertAlmostEqual(pruned_result.objective, complete_result.objective)

    def test_candidate_audit_separates_lossless_and_heuristic_reductions(self):
        resources = [
            Resource(f"R{i}", "Type-I", (0.8, 0.8, 0.8, 0.1, 0.8, 1.0))
            for i in range(4)
        ]
        task = Task(
            "T0",
            (0.2, 0.1, 0.2, 0.2, 1.0, 1.0),
            min_resources=1,
            max_resources=2,
        )
        audit = CandidateGenerationAudit(task_index=0)
        retained = generate_candidates(
            0,
            resources,
            [task],
            top_k=2,
            prune=True,
            switch_lambda=0.0,
            audit=audit,
        )

        self.assertEqual(audit.after_diversity, len(retained))
        self.assertGreaterEqual(
            audit.feasible_before_dominance, audit.after_dominance
        )
        self.assertGreaterEqual(audit.after_dominance, audit.after_diversity)
        self.assertEqual(
            audit.dominance_removed,
            audit.feasible_before_dominance - audit.after_dominance,
        )
        self.assertEqual(
            audit.diversity_removed,
            audit.after_dominance - audit.after_diversity,
        )

        exhaustive_audit = CandidateGenerationAudit(task_index=0)
        exhaustive = generate_candidates(
            0,
            resources,
            [task],
            top_k=2,
            prune=False,
            switch_lambda=0.0,
            audit=exhaustive_audit,
        )
        self.assertEqual(
            exhaustive_audit.feasible_before_dominance,
            exhaustive_audit.after_dominance,
        )
        self.assertEqual(
            exhaustive_audit.after_dominance,
            exhaustive_audit.after_diversity,
        )
        self.assertEqual(len(exhaustive), exhaustive_audit.after_diversity)

    def test_compatibility_screen_is_explicit_and_disableable(self):
        resources = [
            Resource(
                f"R{i}",
                "Type-I",
                (0.2 + 0.02 * i, 0.8, 0.8, 0.1, 0.8, 1.0),
            )
            for i in range(17)
        ]
        task = Task(
            "T0",
            (0.2, 0.1, 0.2, 0.2, 1.0, 1.0),
            min_resources=1,
            max_resources=1,
        )
        screened_audit = CandidateGenerationAudit(task_index=0)
        generate_candidates(
            0,
            resources,
            [task],
            top_k=4,
            prune=True,
            audit=screened_audit,
        )
        self.assertTrue(screened_audit.compatibility_screen_applied)
        self.assertLess(screened_audit.screened_resources, len(resources))

        exhaustive_audit = CandidateGenerationAudit(task_index=0)
        generate_candidates(
            0,
            resources,
            [task],
            top_k=4,
            prune=False,
            audit=exhaustive_audit,
        )
        self.assertFalse(exhaustive_audit.compatibility_screen_applied)
        self.assertEqual(exhaustive_audit.screened_resources, len(resources))


class FallbackTests(unittest.TestCase):
    def test_node_limit_returns_better_incumbent_instead_of_greedy(self):
        resources = [
            Resource("R0", "Type-I", (1, 1, 1, 0, 1, 1)),
            Resource("R1", "Type-II", (1, 1, 1, 0, 1, 1)),
            Resource("R2", "Type-III", (1, 1, 1, 0, 1, 1)),
        ]
        tasks = [
            Task("low-risk", (0, 0, 0, 0, 0.1, 1)),
            Task("high-risk-a", (0, 0, 0, 0, 1.0, 1)),
            Task("high-risk-b", (0, 0, 0, 0, 0.9, 1)),
        ]
        candidates = {
            0: [
                candidate(0, (0, 1, 2), 30.0, 1.0, 20.0),
                candidate(0, (0, 1), 10.0, 0.9, 0.1),
                candidate(0, (2,), 8.0, 0.8, 0.1),
            ],
            1: [candidate(1, (0,), 9.0, 0.9, 0.1)],
            2: [candidate(2, (1,), 9.0, 0.9, 0.1)],
        }

        result = solve_global(
            tasks,
            resources,
            candidates,
            previous={},
            switch_lambda=0.0,
            budget=10.0,
            exact=True,
            node_limit=4,
            gamma_unserved=0.0,
        )

        self.assertEqual(result.solver, "IncumbentFallback")
        self.assertTrue(result.fallback)
        self.assertTrue(result.fallback_selected_incumbent)
        self.assertGreater(result.fallback_incumbent_objective, result.fallback_greedy_objective)
        self.assertEqual(result.selected, {0: (2,), 1: (0,), 2: (1,)})
        self.assertTrue(result.node_limit_hit)
        self.assertGreaterEqual(result.search_upper_bound, result.objective)
        self.assertGreaterEqual(result.fallback_optimality_gap_bound, 0.0)
        self.assertAlmostEqual(
            result.fallback_gain_over_greedy,
            result.objective - result.fallback_greedy_objective,
        )
        self.assertGreater(result.unexplored_frontier_nodes, 0)

    def test_greedy_compares_service_with_the_unserved_history_baseline(self):
        resources = [Resource("R0", "Type-I", (1, 1, 1, 0, 1, 1))]
        tasks = [Task("T0", (0, 0, 0, 0, 0.1, 1))]
        keep_previous = candidate(0, (0,), -0.05, 0.5, 0.1)

        result = greedy_allocate(
            tasks,
            resources,
            {0: [keep_previous]},
            previous={0: (0,)},
            switch_lambda=0.10,
            budget=1.0,
            gamma_unserved=0.0,
        )

        self.assertEqual(result.selected, {0: (0,)})
        self.assertAlmostEqual(result.objective, -0.05)

    def test_retained_pool_frontier_bound_is_admissible(self):
        resources = [
            Resource("R0", "Type-I", (1, 1, 1, 0, 1, 1)),
            Resource("R1", "Type-II", (1, 1, 1, 0, 1, 1)),
        ]
        tasks = [
            Task("T0", (0, 0, 0, 0, 0.9, 1)),
            Task("T1", (0, 0, 0, 0, 0.7, 1)),
        ]
        candidates = {
            0: [
                candidate(0, (0,), 0.7, 0.9, 0.2),
                candidate(0, (1,), 0.5, 0.8, 0.2),
            ],
            1: [
                candidate(1, (0,), 0.6, 0.8, 0.2),
                candidate(1, (1,), 0.4, 0.7, 0.2),
            ],
        }
        gamma = 0.2
        order = [0, 1]
        suffix = retained_pool_suffix_upper_bounds(
            tasks, order, candidates, gamma
        )
        root_bound = retained_pool_node_upper_bound(
            0.0,
            0.0,
            gamma * sum(task.feature[4] for task in tasks),
            suffix[0],
        )
        exact = solve_global(
            tasks,
            resources,
            candidates,
            previous={},
            switch_lambda=0.1,
            budget=1.0,
            exact=True,
            node_limit=1000,
            gamma_unserved=gamma,
        )

        self.assertGreaterEqual(root_bound + 1e-12, exact.objective)


class ExternalBaselineTests(unittest.TestCase):
    def setUp(self):
        self.resources = [
            Resource("R0", "Type-I", (1, 1, 1, 0, 1, 1)),
            Resource("R1", "Type-II", (1, 1, 1, 0, 1, 1)),
        ]
        self.tasks = [
            Task("T0", (0, 0, 0, 0, 0.8, 1)),
            Task("T1", (0, 0, 0, 0, 0.7, 1)),
        ]
        self.candidates = {
            0: [
                candidate(0, (0,), 0.80, 0.9, 0.2),
                candidate(0, (1,), 0.60, 0.8, 0.2),
            ],
            1: [
                candidate(1, (0,), 0.70, 0.9, 0.2),
                candidate(1, (1,), 0.65, 0.8, 0.2),
            ],
        }

    @unittest.skipUnless(
        SCIPY_AVAILABLE,
        "HiGHS-MILP requires the locked SciPy environment",
    )
    def test_highs_milp_matches_complete_branch_and_bound(self):
        exact = solve_global(
            self.tasks,
            self.resources,
            self.candidates,
            previous={0: (1,)},
            switch_lambda=0.1,
            budget=1.0,
            node_limit=1000,
            gamma_unserved=0.2,
        )
        optimized = milp_allocate(
            self.tasks,
            self.resources,
            self.candidates,
            previous={0: (1,)},
            switch_lambda=0.1,
            budget=1.0,
            node_limit=1000,
            gamma_unserved=0.2,
        )

        self.assertEqual(optimized.solver, "HiGHSMILP")
        self.assertEqual(optimized.selected, exact.selected)
        self.assertAlmostEqual(optimized.objective, exact.objective)
        self.assertLessEqual(optimized.total_physical_cost, 1.0)

    def test_genetic_baseline_is_deterministic_and_keeps_greedy_warm_start(self):
        greedy = greedy_allocate(
            self.tasks,
            self.resources,
            self.candidates,
            previous={},
            switch_lambda=0.1,
            budget=1.0,
            gamma_unserved=0.2,
        )
        first = genetic_allocate(
            self.tasks,
            self.resources,
            self.candidates,
            previous={},
            switch_lambda=0.1,
            budget=1.0,
            rng=random.Random(17),
            evaluation_budget=200,
            population_size=20,
            gamma_unserved=0.2,
        )
        second = genetic_allocate(
            self.tasks,
            self.resources,
            self.candidates,
            previous={},
            switch_lambda=0.1,
            budget=1.0,
            rng=random.Random(17),
            evaluation_budget=200,
            population_size=20,
            gamma_unserved=0.2,
        )

        self.assertEqual(first.selected, second.selected)
        self.assertAlmostEqual(first.objective, second.objective)
        self.assertGreaterEqual(first.objective + 1e-12, greedy.objective)
        self.assertEqual(first.nodes, 200)
        usage = [0, 0]
        for combo in first.selected.values():
            for resource_index in combo:
                usage[resource_index] += 1
        self.assertTrue(all(value <= 1 for value in usage))


class ValueModelTests(unittest.TestCase):
    def test_redundancy_penalty_does_not_consume_physical_budget(self):
        resources = [
            Resource("R0", "Type-I", (0.9, 0.9, 0.9, 0.0, 1.0, 1.0)),
            Resource("R1", "Type-I", (0.9, 0.9, 0.9, 0.0, 1.0, 1.0)),
        ]
        task = Task(
            "T0",
            (0.2, 0.1, 0.2, 0.2, 1.0, 1.0),
            min_resources=2,
            max_resources=2,
        )
        coalition = evaluate_candidate(0, (0, 1), resources, [task])

        self.assertAlmostEqual(coalition.physical_cost, 0.4)
        self.assertGreater(coalition.redundancy_penalty, 0.0)
        self.assertLessEqual(coalition.success_score, 1.0)

        result = solve_global(
            [task],
            resources,
            {0: [coalition]},
            previous={},
            switch_lambda=0.0,
            budget=coalition.physical_cost,
            exact=True,
            node_limit=100,
        )

        self.assertEqual(result.selected, {0: (0, 1)})
        self.assertAlmostEqual(result.total_physical_cost, coalition.physical_cost)

    def test_complementarity_uses_only_remaining_score_headroom(self):
        resources = [
            Resource("R0", "Type-I", (0.9, 0.9, 0.8, 0.1, 0.95, 1.0)),
            Resource("R1", "Type-IV", (0.8, 0.9, 0.8, 0.1, 0.95, 1.0)),
        ]
        task = Task("T0", (0.2, 0.1, 0.2, 0.2, 1.0, 1.0), max_resources=2)
        coalition = evaluate_candidate(
            0, (0, 1), resources, [task], synergy_scale=20.0
        )

        self.assertGreater(coalition.complementarity_gain, 0.0)
        self.assertLessEqual(coalition.success_score, 1.0)
        self.assertGreaterEqual(
            1.0 - coalition.success_score,
            -1e-12,
        )

    def test_service_endpoint_is_bounded_and_feasibility_gated(self):
        self.assertAlmostEqual(sum(INDEPENDENT_SERVICE_WEIGHTS.values()), 1.0)
        resources = [
            Resource("R0", "Type-I", (0.9, 0.9, 0.9, 0.1, 0.9, 1.0))
        ]
        tasks = [
            Task(
                "T0",
                (0.2, 0.1, 0.2, 0.2, 1.0, 1.0),
                min_resources=1,
                max_resources=1,
            )
        ]
        valid = evaluate_independent_allocation(
            {0: (0,)}, resources, tasks, budget=1.0
        )
        invalid = evaluate_independent_allocation(
            {0: (0, 0)}, resources, tasks, budget=1.0
        )

        self.assertGreaterEqual(valid["independent_service_score"], 0.0)
        self.assertLessEqual(valid["independent_service_score"], 1.0)
        self.assertEqual(invalid["independent_feasibility"], 0.0)
        self.assertEqual(invalid["independent_service_score"], 0.0)


if __name__ == "__main__":
    unittest.main()

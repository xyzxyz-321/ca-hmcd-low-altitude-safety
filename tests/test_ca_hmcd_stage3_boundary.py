import json
import unittest
from pathlib import Path

import ca_hmcd_simulation as core
from ca_hmcd_framework import ValueModelConfig, default_budget
from ca_hmcd_stage3_boundary import (
    BOUNDED_METHOD,
    EXACT_METHOD,
    REGISTERED_BUDGETS_MS,
    build_fixed_k12_candidates,
    bounded_search_with_deadline,
    make_boundary_snapshot,
    rotated_budgets,
    run_boundary_unit,
)


class Stage3RegistrationTests(unittest.TestCase):
    def test_code_constants_match_frozen_registration(self):
        root = Path(__file__).resolve().parent
        registered = json.loads(
            (
                root
                / "ca_hmcd_stage3_boundary_closure_20260917"
                / "stage3_preregistration.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            tuple(registered["design"]["decision_budgets_ms"]),
            REGISTERED_BUDGETS_MS,
        )
        self.assertEqual(
            registered["parent_stage2_decision"]["decision"], "FAIL"
        )

    def test_budget_rotation_preserves_registered_set(self):
        for offset in range(8):
            self.assertEqual(
                set(rotated_budgets(offset)), set(REGISTERED_BUDGETS_MS)
            )


class Stage3BoundaryEngineTests(unittest.TestCase):
    def setUp(self):
        self.resources, self.tasks = make_boundary_snapshot(941111, 8, 5)
        self.model = ValueModelConfig()

    def test_fixed_k_pool_matches_registered_legacy_generator(self):
        pool = build_fixed_k12_candidates(
            self.resources, self.tasks, self.model
        )
        for task_index in range(len(self.tasks)):
            direct = core.generate_candidates(
                task_index,
                self.resources,
                self.tasks,
                top_k=12,
                alpha=self.model.alpha_cost,
                beta=self.model.beta_time,
                use_synergy=True,
                use_redundancy=True,
                use_adaptation=True,
                synergy_scale=1.0,
                prune=True,
                previous_combo=None,
                switch_lambda=self.model.lambda_switch,
                redundancy_lambda=self.model.lambda_redundancy,
            )
            self.assertEqual(pool.by_task[task_index], direct)

    def test_zero_time_search_returns_a_feasible_warm_start(self):
        pool = build_fixed_k12_candidates(
            self.resources, self.tasks, self.model
        )
        result, trace = bounded_search_with_deadline(
            self.tasks,
            self.resources,
            pool.by_task,
            {},
            self.model.lambda_switch,
            default_budget(self.resources),
            time_limit_ms=0.0,
        )
        external, violations = core.evaluate_allocation(
            result.selected,
            self.resources,
            self.tasks,
            {},
            budget=default_budget(self.resources),
        )
        self.assertEqual(violations, 0)
        self.assertTrue(trace["time_limit_hit"])
        self.assertTrue(result.fallback)
        self.assertAlmostEqual(result.objective, external.objective)

    def test_small_unit_is_paired_and_feasible(self):
        result = run_boundary_unit(
            seed=941112,
            resources_count=6,
            tasks_count=3,
            seed_offset=0,
            exact_time_limit_ms=1000,
            budgets_ms=(100,),
        )
        rows = result["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["method"] for row in rows},
            {EXACT_METHOD, BOUNDED_METHOD},
        )
        self.assertEqual(
            len({row["state_fingerprint"] for row in rows}), 1
        )
        self.assertTrue(
            all(float(row["final_feasibility"]) == 1.0 for row in rows)
        )


if __name__ == "__main__":
    unittest.main()

import unittest

try:
    import scipy  # noqa: F401
except (ImportError, OSError):
    SCIPY_AVAILABLE = False
else:
    SCIPY_AVAILABLE = True

import ca_hmcd_simulation as core
from ca_hmcd_framework import (
    METHOD_PROFILES,
    AdaptiveKConfig,
    CandidatePolicy,
    SolverConfig,
    ValueModelConfig,
    build_candidates,
    default_budget,
    retain_candidates,
    run_decision,
    solve_allocation,
)


class FrameworkCandidateTests(unittest.TestCase):
    def setUp(self):
        self.resources, self.tasks = core.make_scenario(
            seed=9217, resources=10, tasks=6, arrival_horizon=0
        )
        for task in self.tasks:
            task.active = True
            task.arrival_step = 0
        self.previous = {0: (0,), 2: (2, 3)}
        self.model = ValueModelConfig()

    def test_model_defaults_preserve_stage9_contract(self):
        self.assertEqual(self.model.alpha_cost, core.ALPHA_COST)
        self.assertEqual(self.model.beta_time, core.BETA_TIME)
        self.assertEqual(self.model.lambda_switch, core.LAMBDA_SWITCH)
        self.assertEqual(
            self.model.lambda_redundancy, core.LAMBDA_REDUNDANCY
        )
        self.assertEqual(self.model.gamma_unserved, core.GAMMA_UNSERVED)

    def test_complete_pool_matches_legacy_no_pruning_generation(self):
        framework = build_candidates(
            self.resources, self.tasks, self.previous, self.model
        )
        for task_index in range(len(self.tasks)):
            legacy = core.generate_candidates(
                task_index,
                self.resources,
                self.tasks,
                top_k=1,
                alpha=self.model.alpha_cost,
                beta=self.model.beta_time,
                use_synergy=True,
                use_redundancy=True,
                use_adaptation=True,
                synergy_scale=1.0,
                prune=False,
                previous_combo=self.previous.get(task_index),
                switch_lambda=self.model.lambda_switch,
                redundancy_lambda=self.model.lambda_redundancy,
                apply_compatibility_screen=False,
                apply_dominance=False,
                apply_diversity=False,
            )
            self.assertEqual(framework.by_task[task_index], legacy)

    def test_fixed_k_retention_matches_stage9_generation(self):
        complete = build_candidates(
            self.resources, self.tasks, self.previous, self.model
        )
        framework = retain_candidates(
            complete,
            len(self.resources),
            CandidatePolicy.fixed_k(12),
            self.previous,
            self.model.lambda_switch,
        )
        for task_index in range(len(self.tasks)):
            legacy = core.generate_candidates(
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
                previous_combo=self.previous.get(task_index),
                switch_lambda=self.model.lambda_switch,
                redundancy_lambda=self.model.lambda_redundancy,
            )
            self.assertEqual(framework.by_task[task_index], legacy)
            self.assertLessEqual(len(framework.by_task[task_index]), 12)

    def test_no_pruning_retains_every_complete_candidate(self):
        complete = build_candidates(
            self.resources, self.tasks, self.previous, self.model
        )
        retained = retain_candidates(
            complete,
            len(self.resources),
            CandidatePolicy.full(),
            self.previous,
            self.model.lambda_switch,
        )
        self.assertEqual(retained.by_task, complete.by_task)
        for audit in retained.audits.values():
            self.assertEqual(
                audit.feasible_before_dominance, audit.after_dominance
            )
            self.assertEqual(audit.after_dominance, audit.after_diversity)

    def test_adaptive_k_is_deterministic_bounded_and_audited(self):
        complete = build_candidates(
            self.resources, self.tasks, self.previous, self.model
        )
        policy = CandidatePolicy.adaptive_k(AdaptiveKConfig())
        first = retain_candidates(
            complete,
            len(self.resources),
            policy,
            self.previous,
            self.model.lambda_switch,
            active_task_count=6,
        )
        second = retain_candidates(
            complete,
            len(self.resources),
            policy,
            self.previous,
            self.model.lambda_switch,
            active_task_count=6,
        )
        self.assertEqual(first.by_task, second.by_task)
        self.assertEqual(first.retention_metadata, second.retention_metadata)
        for task_index, values in first.by_task.items():
            self.assertLessEqual(len(values), 48)
            metadata = first.retention_metadata[task_index]
            self.assertEqual(
                len(values), metadata["retained_candidate_count"]
            )
            self.assertGreaterEqual(metadata["selected_target_k"], 0)

    def test_adaptive_k_expands_or_preserves_pool_under_higher_load(self):
        complete = build_candidates(
            self.resources, self.tasks, self.previous, self.model
        )
        policy = CandidatePolicy.adaptive_k()
        low = retain_candidates(
            complete,
            len(self.resources),
            policy,
            self.previous,
            self.model.lambda_switch,
            active_task_count=2,
        )
        high = retain_candidates(
            complete,
            len(self.resources),
            policy,
            self.previous,
            self.model.lambda_switch,
            active_task_count=8,
        )
        self.assertGreaterEqual(
            high.total_candidates, low.total_candidates
        )


@unittest.skipUnless(SCIPY_AVAILABLE, "SciPy/HiGHS is required")
class FrameworkSolverTests(unittest.TestCase):
    def setUp(self):
        self.resources, self.tasks = core.make_scenario(
            seed=9317, resources=8, tasks=5, arrival_horizon=0
        )
        for task in self.tasks:
            task.active = True
            task.arrival_step = 0
        self.previous = {1: (1,), 3: (3, 4)}
        self.model = ValueModelConfig()

    def test_exact_profile_matches_registered_highs_backend(self):
        pool = build_candidates(
            self.resources, self.tasks, self.previous, self.model
        )
        budget = default_budget(self.resources)
        framework = solve_allocation(
            self.tasks,
            self.resources,
            pool,
            self.previous,
            self.model,
            SolverConfig("highs", 4000),
            budget,
        )
        direct = core.milp_allocate(
            self.tasks,
            self.resources,
            pool.by_task,
            self.previous,
            self.model.lambda_switch,
            budget,
            node_limit=4000,
            gamma_unserved=self.model.gamma_unserved,
        )
        self.assertEqual(framework.selected, direct.selected)
        self.assertAlmostEqual(framework.objective, direct.objective, places=10)

    def test_decision_pipeline_returns_feasible_true_state_allocation(self):
        outcome = run_decision(
            self.resources,
            self.tasks,
            self.resources,
            self.tasks,
            previous=self.previous,
            profile_name="CA-HMCD-Exact",
        )
        self.assertEqual(outcome.final.complete_model_violations, 0)
        self.assertEqual(outcome.final.service["independent_feasibility"], 1.0)
        self.assertEqual(outcome.profile.solver.backend, "highs")
        self.assertEqual(outcome.retained_pool.policy.mode, "full")

    def test_profiles_separate_candidate_and_solver_choices(self):
        exact = METHOD_PROFILES["CA-HMCD-Exact"]
        fixed = METHOD_PROFILES["CA-HMCD-FixedK"]
        no_pruning = METHOD_PROFILES["CA-HMCD-NoPruning"]
        self.assertEqual(exact.candidate_policy.mode, "full")
        self.assertEqual(exact.solver.backend, "highs")
        self.assertEqual(fixed.candidate_policy.mode, "fixed_k")
        self.assertEqual(fixed.solver.backend, "bounded_search")
        self.assertEqual(no_pruning.candidate_policy.mode, "full")
        self.assertEqual(no_pruning.solver.backend, "bounded_search")

    def test_rt_profile_executes_registered_adaptive_k(self):
        outcome = run_decision(
            self.resources,
            self.tasks,
            self.resources,
            self.tasks,
            previous=self.previous,
            profile_name="CA-HMCD-RT",
        )
        self.assertEqual(
            outcome.profile.status, "EXPERIMENTAL_GATE_A_FAILED"
        )
        self.assertEqual(outcome.retained_pool.policy.mode, "adaptive_k")
        self.assertEqual(outcome.final.complete_model_violations, 0)
        self.assertTrue(outcome.retained_pool.retention_metadata)


if __name__ == "__main__":
    unittest.main()

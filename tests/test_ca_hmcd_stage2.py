import unittest

from ca_hmcd_simulation import (
    EVALUATOR_VERSION,
    MODEL_VERSION,
    Resource,
    SERVICE_ENDPOINT_VERSION,
    Task,
    audit_paired_protocol,
    evaluate_independent_allocation,
    make_engineering_scenario,
    run_episode,
)


class IndependentEvaluatorTests(unittest.TestCase):
    def test_empty_allocation_has_zero_service_score(self):
        resources = [
            Resource("R0", "Type-I", (0.8, 0.8, 0.8, 0.2, 0.9, 1.0))
        ]
        tasks = [Task("T0", (0.4, 0.4, 0.4, 0.4, 0.8, 0.8))]

        result = evaluate_independent_allocation({}, resources, tasks, budget=1.0)

        self.assertEqual(result["independent_service_score"], 0.0)
        self.assertEqual(result["independent_coverage"], 0.0)
        self.assertEqual(result["independent_feasibility"], 1.0)


class FairnessProtocolTests(unittest.TestCase):
    def test_paired_algorithms_share_the_same_true_state_trajectory(self):
        _, ca_rows = run_episode(
            "CA-HMCD", 8123, 3, 8, 5, "balanced", 6,
            node_limit_override=1000,
        )
        _, auction_rows = run_episode(
            "External-Auction", 8123, 3, 8, 5, "balanced", 6,
            node_limit_override=1000,
        )

        self.assertEqual(
            [row["state_fingerprint"] for row in ca_rows],
            [row["state_fingerprint"] for row in auction_rows],
        )
        self.assertEqual(
            [row["active_tasks"] for row in ca_rows],
            [row["active_tasks"] for row in auction_rows],
        )
        self.assertEqual(
            [row["active_task_ids_json"] for row in ca_rows],
            [row["active_task_ids_json"] for row in auction_rows],
        )
        for row in ca_rows + auction_rows:
            self.assertEqual(row["model_version"], MODEL_VERSION)
            self.assertEqual(row["evaluator_version"], EVALUATOR_VERSION)
            self.assertEqual(
                row["service_endpoint_version"], SERVICE_ENDPOINT_VERSION
            )

        audit = audit_paired_protocol(
            ca_rows + auction_rows,
            ("CA-HMCD", "External-Auction"),
            expected_seeds=1,
            expected_steps=3,
        )
        self.assertTrue(all(row["same_state"] for row in audit))
        self.assertTrue(all(row["same_active_task_ids"] for row in audit))
        self.assertTrue(all(row["same_model_version"] for row in audit))
        self.assertTrue(all(row["same_evaluator_version"] for row in audit))

    def test_external_auction_is_feasible_after_true_state_repair(self):
        metrics, _ = run_episode(
            "External-Auction", 8124, 4, 10, 7, "volatile", 8,
            node_limit_override=1000,
        )

        self.assertEqual(metrics["feasibility_rate"], 1.0)
        self.assertEqual(metrics["independent_feasibility"], 1.0)

    def test_parameter_overrides_are_recorded(self):
        _, rows = run_episode(
            "CA-HMCD", 8125, 2, 8, 5, "balanced", 6,
            node_limit_override=1000,
            model_parameters={
                "alpha_cost": 0.24,
                "beta_time": 0.16,
                "lambda_switch": 0.20,
                "lambda_redundancy": 0.06,
                "gamma_unserved": 0.40,
            },
        )

        for row in rows:
            self.assertEqual(row["alpha_cost"], 0.24)
            self.assertEqual(row["beta_time"], 0.16)
            self.assertEqual(row["lambda_switch"], 0.20)
            self.assertEqual(row["lambda_redundancy"], 0.06)
            self.assertEqual(row["gamma_unserved"], 0.40)
            self.assertGreaterEqual(
                row["candidate_feasible_before_dominance"],
                row["candidate_after_dominance"],
            )
            self.assertGreaterEqual(
                row["candidate_after_dominance"],
                row["candidate_after_diversity"],
            )


class EngineeringScenarioTests(unittest.TestCase):
    def test_all_engineering_profiles_generate_requested_load(self):
        for profile in (
            "airport_corridor", "energy_facility", "public_event",
            "urban_corridor", "industrial_zone",
        ):
            resources, tasks = make_engineering_scenario(
                8126, profile, resources=10, tasks=6
            )
            self.assertEqual(len(resources), 10)
            self.assertEqual(len(tasks), 6)
            self.assertTrue(any(task.type_requirements for task in tasks))


if __name__ == "__main__":
    unittest.main()

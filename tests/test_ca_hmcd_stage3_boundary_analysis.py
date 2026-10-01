import unittest

from ca_hmcd_stage3_boundary_analysis import boundary_summary, gate_b


class Stage3GateBTests(unittest.TestCase):
    @staticmethod
    def pairs(bounded_hits=30, service_loss=0.02):
        rows = []
        for index in range(30):
            exact_service = 0.8
            rows.append(
                {
                    "resources_count": 24,
                    "tasks_count": 12,
                    "registered_budget_ms": 200,
                    "scale_cell": "R24-T12",
                    "exact_optimal": 1,
                    "exact_deadline_hit": 0,
                    "bounded_deadline_hit": int(index < bounded_hits),
                    "bounded_final_feasibility": 1.0,
                    "exact_independent_service_score": exact_service,
                    "bounded_independent_service_score": (
                        exact_service - service_loss
                    ),
                    "exact_independent_coverage": 0.9,
                    "bounded_independent_coverage": 0.87,
                    "exact_external_objective_per_active_task": 0.1,
                    "bounded_external_objective_per_active_task": 0.085,
                    "exact_decision_runtime_ms": 450.0,
                    "bounded_decision_runtime_ms": 150.0,
                    "exact_candidate_after_diversity": 1000.0,
                    "bounded_candidate_after_diversity": 144.0,
                }
            )
        return rows

    @staticmethod
    def registration():
        return {
            "gate_b": {
                "exact_reference_optimal_rate_required": 1.0,
                "exact_deadline_hit_rate_must_be_below": 0.95,
                "bounded_k12_deadline_hit_rate_required": 0.95,
                "bounded_k12_feasibility_required": 1.0,
                "maximum_mean_service_score_loss": 0.03,
                "maximum_mean_coverage_loss": 0.05,
                "maximum_mean_objective_per_task_loss": 0.02,
            }
        }

    def test_gate_passes_when_every_registered_condition_holds(self):
        summary = boundary_summary(self.pairs(), self.registration())
        self.assertTrue(summary[0]["gate_b_cell_pass"])
        self.assertEqual(gate_b(summary)["decision"], "PASS")

    def test_gate_fails_when_deadline_or_quality_condition_fails(self):
        summary = boundary_summary(
            self.pairs(bounded_hits=28, service_loss=0.04),
            self.registration(),
        )
        self.assertFalse(summary[0]["gate_b_cell_pass"])
        self.assertEqual(gate_b(summary)["decision"], "FAIL")


if __name__ == "__main__":
    unittest.main()

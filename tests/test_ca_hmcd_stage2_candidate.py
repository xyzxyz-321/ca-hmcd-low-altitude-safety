import json
import unittest
from dataclasses import asdict
from pathlib import Path

from ca_hmcd_framework import CandidatePolicy
from ca_hmcd_replay import read_replay_episode
from ca_hmcd_stage2_candidate_analysis import gate_a
from ca_hmcd_stage2_candidate_runner import run_adaptive_episode


class Stage2AdaptivePolicyTests(unittest.TestCase):
    def test_code_rule_matches_frozen_preregistration(self):
        root = Path(__file__).resolve().parent
        registered = json.loads(
            (
                root
                / "ca_hmcd_stage2_candidate_closure_20260917"
                / "stage2_preregistration.json"
            ).read_text(encoding="utf-8")
        )["adaptive_k"]
        implemented = asdict(
            CandidatePolicy.adaptive_k().adaptive_config
        )
        self.assertEqual(implemented["min_k"], registered["min_k"])
        self.assertEqual(implemented["max_k"], registered["max_k"])
        self.assertEqual(
            implemented["load_intercept"],
            registered["load_intercept"],
        )
        self.assertEqual(
            implemented["load_slope"], registered["load_slope"]
        )
        self.assertEqual(
            implemented["count_intercept"],
            registered["count_intercept"],
        )
        self.assertEqual(
            implemented["count_sqrt_scale"],
            registered["count_sqrt_scale"],
        )
        self.assertEqual(
            implemented["boundary_relative_gap"],
            registered["boundary_relative_gap"],
        )
        self.assertEqual(
            implemented["extension_step"],
            registered["extension_step"],
        )
        self.assertEqual(
            implemented["apply_dominance"],
            registered["dominance_first"],
        )

    def test_adaptive_replay_records_step_level_k_audit(self):
        root = Path(__file__).resolve().parent
        episode = read_replay_episode(
            root
            / "ca_hmcd_replay_protocol_20260916"
            / "replays"
            / "anti_uav410"
            / "AU-L02-W01.csv"
        )
        metrics, rows = run_adaptive_episode(
            seed=730000,
            steps=1,
            resources_count=10,
            tasks_count=episode.task_count,
            scenario="anti_uav_load_2",
            node_limit=4000,
            replay_episode=episode,
        )
        self.assertEqual(metrics["algorithm"], "AdaptiveK")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_policy"], "adaptive_k")
        self.assertLessEqual(rows[0]["adaptive_k_max"], 48)
        self.assertTrue(rows[0]["adaptive_task_metadata_json"])
        self.assertEqual(metrics["feasibility_rate"], 1.0)


class Stage2GateTests(unittest.TestCase):
    @staticmethod
    def rows(adaptive_nodes):
        output = []
        for load, cluster in ((6, "L6"), (8, "L8")):
            for algorithm, values in {
                "Full": (1.0, 1.0, 1.0, 1000.0, 1000.0),
                "FixedK-12": (0.8, 0.8, 0.8, 100.0, 400.0),
                "AdaptiveK": (
                    0.98,
                    0.98,
                    0.98,
                    200.0,
                    adaptive_nodes,
                ),
            }.items():
                service, coverage, objective, candidates, nodes = values
                output.append(
                    {
                        "dataset": "anti_uav410",
                        "task_load": load,
                        "independent_cluster": cluster,
                        "algorithm": algorithm,
                        "independent_service_score": service,
                        "independent_coverage": coverage,
                        "external_objective": objective,
                        "candidate_after_diversity": candidates,
                        "nodes": nodes,
                        "feasibility_rate": 1.0,
                    }
                )
        return output

    def test_gate_passes_when_all_registered_thresholds_hold(self):
        result = gate_a(self.rows(adaptive_nodes=500.0))
        self.assertEqual(result["decision"], "PASS")

    def test_gate_fails_when_node_reduction_is_insufficient(self):
        result = gate_a(self.rows(adaptive_nodes=900.0))
        self.assertEqual(result["decision"], "FAIL")
        self.assertFalse(result["criteria"]["node_ratio_at_most_0_60"])


if __name__ == "__main__":
    unittest.main()

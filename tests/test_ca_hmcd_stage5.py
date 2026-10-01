import csv
import json
import tempfile
import unittest
from pathlib import Path

from ca_hmcd_stage5_statistics import (
    aggregate_cluster_records,
    bounded_interval_rows,
    build_common_response_records,
    paired_cluster_row,
)


class ClusterAggregationTests(unittest.TestCase):
    def test_nested_seeds_are_averaged_before_inference(self):
        rows = []
        for algorithm, offset in (("CA-HMCD", 1.0), ("Greedy", 0.0)):
            for seed in range(30):
                rows.append(
                    {
                        "dataset": "anti_uav410",
                        "task_load": 2,
                        "independent_cluster": "W1",
                        "seed": seed,
                        "algorithm": algorithm,
                        "external_objective": offset + seed,
                        "independent_service_score": 0.5,
                        "independent_coverage": 0.8,
                        "external_switch_count": 1.0,
                        "independent_physical_cost": 1.0,
                        "independent_same_type_pair_ratio": 0.1,
                        "independent_capability_overlap": 0.2,
                        "independent_marginal_gain_waste": 0.3,
                        "end_to_end_runtime_ms": 10.0,
                        "fallback_run_indicator": 0.0,
                        "fallback": 0.0,
                        "feasibility_rate": 1.0,
                        "raw_feasible": 1.0,
                        "repair_run_indicator": 0.0,
                        "node_limit_run_indicator": 0.0,
                    }
                )

        aggregated = aggregate_cluster_records(rows)

        self.assertEqual(len(aggregated), 2)
        ca = next(row for row in aggregated if row["algorithm"] == "CA-HMCD")
        self.assertEqual(ca["nested_seed_runs"], 30)
        self.assertAlmostEqual(ca["external_objective"], 15.5)

    def test_paired_row_counts_clusters_not_seeds(self):
        records = []
        for cluster in ("W1", "W2", "W3", "W4"):
            records.extend(
                [
                    {
                        "independent_cluster": cluster,
                        "algorithm": "CA-HMCD",
                        "external_objective": 1.0,
                    },
                    {
                        "independent_cluster": cluster,
                        "algorithm": "Greedy",
                        "external_objective": 0.5,
                    },
                ]
            )

        row = paired_cluster_row(
            records,
            "external_objective",
            "family",
            "primary performance",
            "Greedy",
            "higher",
            100,
            "anti_uav410",
            8,
        )

        self.assertEqual(row["n_independent_cluster_pairs"], 4)
        self.assertAlmostEqual(row["mean_difference_a_minus_b"], 0.5)
        self.assertEqual(row["win_rate_favorable_to_method_a"], 1.0)


class BoundedIntervalTests(unittest.TestCase):
    def test_all_intervals_remain_in_unit_range(self):
        rows = []
        for algorithm in (
            "CA-HMCD",
            "Greedy",
            "External-Auction",
            "No-Synergy",
            "No-Stability",
            "Random",
        ):
            for index in range(4):
                rows.append(
                    {
                        "dataset": "anti_uav410",
                        "task_load": 8,
                        "algorithm": algorithm,
                        "independent_cluster": f"W{index}",
                        "independent_coverage": index / 3,
                        "feasibility_rate": 1.0,
                        "raw_feasible": 1.0,
                        "repair_run_indicator": 0.0,
                        "fallback_run_indicator": 1.0,
                        "fallback": 0.5,
                        "node_limit_run_indicator": 1.0,
                        "independent_same_type_pair_ratio": 0.2,
                        "independent_capability_overlap": 0.3,
                        "independent_marginal_gain_waste": 0.4,
                    }
                )

        intervals = bounded_interval_rows(rows, bounded_resamples=100)

        self.assertTrue(intervals)
        self.assertTrue(
            all(
                0.0 <= row["ci95_low"] <= row["ci95_high"] <= 1.0
                for row in intervals
            )
        )


class CommonResponseTests(unittest.TestCase):
    def test_only_jointly_served_tasks_enter_response_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "per_step.csv"
            fieldnames = [
                "dataset",
                "task_load",
                "independent_cluster",
                "workload_id",
                "seed",
                "step",
                "algorithm",
                "served_task_response_times_json",
            ]
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for seed in range(30):
                    rows = {
                        "CA-HMCD": {"T0": 0.4, "CA-only": 9.0},
                        "Greedy": {"T0": 0.7, "G-only": 0.1},
                        "External-Auction": {"T0": 0.7},
                        "No-Synergy": {"T0": 0.7},
                        "No-Stability": {"T0": 0.7},
                        "Random": {"T0": 0.7},
                    }
                    for algorithm, responses in rows.items():
                        writer.writerow(
                            {
                                "dataset": "anti_uav410",
                                "task_load": 2,
                                "independent_cluster": "W1",
                                "workload_id": "W1",
                                "seed": seed,
                                "step": 0,
                                "algorithm": algorithm,
                                "served_task_response_times_json": json.dumps(
                                    responses
                                ),
                            }
                        )

            seed_rows, cluster_rows, comparisons, registry = (
                build_common_response_records(path, bootstrap_resamples=100)
            )

            greedy_seed = next(
                row
                for row in seed_rows
                if row["method_b"] == "Greedy" and row["seed"] == 0
            )
            self.assertEqual(greedy_seed["common_task_period_count"], 1)
            self.assertAlmostEqual(
                greedy_seed["mean_difference_a_minus_b"], -0.3
            )
            greedy_cluster = next(
                row for row in cluster_rows if row["method_b"] == "Greedy"
            )
            self.assertAlmostEqual(
                greedy_cluster["mean_difference_a_minus_b"], -0.3
            )
            greedy_comparison = next(
                row for row in comparisons if row["method_b"] == "Greedy"
            )
            self.assertEqual(
                greedy_comparison["win_rate_favorable_to_method_a"], 1.0
            )
            self.assertEqual(len(registry), 1)


if __name__ == "__main__":
    unittest.main()

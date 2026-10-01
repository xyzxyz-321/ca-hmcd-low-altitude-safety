import unittest

from ca_hmcd_stage3_statistics import (
    PRIMARY_BASELINES,
    PRIMARY_SCENARIOS,
    bca_mean_interval,
    common_task_response_rows,
    holm_adjust,
    optimality_interval_rows,
    sample_size_rows,
    wilson_score_interval,
)


class BootstrapIntervalTests(unittest.TestCase):
    def test_bounded_bca_interval_stays_in_unit_interval(self):
        low, high = bca_mean_interval(
            [0.0, 0.0, 0.2, 0.7, 1.0],
            resamples=1000,
            seed=123,
            bounds=(0.0, 1.0),
        )

        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)
        self.assertLessEqual(low, high)

    def test_constant_interval_collapses_to_observed_value(self):
        low, high = bca_mean_interval(
            [0.4] * 30,
            resamples=100,
            seed=123,
            bounds=(0.0, 1.0),
        )

        self.assertEqual(low, 0.4)
        self.assertEqual(high, 0.4)

    def test_wilson_interval_is_non_degenerate_at_boundary(self):
        low, high = wilson_score_interval([1.0] * 30)

        self.assertLess(low, 1.0)
        self.assertEqual(high, 1.0)


class HolmFamilyTests(unittest.TestCase):
    def test_holm_is_separate_and_monotone_within_each_family(self):
        rows = [
            {"family_id": "A", "p_unadjusted": 0.01},
            {"family_id": "A", "p_unadjusted": 0.03},
            {"family_id": "A", "p_unadjusted": 0.04},
            {"family_id": "B", "p_unadjusted": 0.02},
        ]

        holm_adjust(rows)

        family_a = sorted(
            (row for row in rows if row["family_id"] == "A"),
            key=lambda row: row["p_unadjusted"],
        )
        self.assertEqual([row["holm_family_size"] for row in family_a], [3, 3, 3])
        self.assertEqual(
            [round(row["p_holm_adjusted"], 6) for row in family_a],
            [0.03, 0.06, 0.06],
        )
        family_b = next(row for row in rows if row["family_id"] == "B")
        self.assertEqual(family_b["p_holm_adjusted"], 0.02)


class CommonTaskResponseTests(unittest.TestCase):
    def test_response_time_uses_only_intersection_and_lower_is_a_win(self):
        records = []
        for scenario in PRIMARY_SCENARIOS:
            records.extend(
                [
                    {
                        "scenario": scenario,
                        "algorithm": "CA-HMCD",
                        "seed": 1,
                        "step": 0,
                        "task_id": "T0",
                        "response_time": 0.4,
                    },
                    {
                        "scenario": scenario,
                        "algorithm": "CA-HMCD",
                        "seed": 1,
                        "step": 0,
                        "task_id": "CA-only",
                        "response_time": 9.0,
                    },
                ]
            )
            for baseline in PRIMARY_BASELINES:
                records.extend(
                    [
                        {
                            "scenario": scenario,
                            "algorithm": baseline,
                            "seed": 1,
                            "step": 0,
                            "task_id": "T0",
                            "response_time": 0.7,
                        },
                        {
                            "scenario": scenario,
                            "algorithm": baseline,
                            "seed": 1,
                            "step": 0,
                            "task_id": "baseline-only",
                            "response_time": 0.1,
                        },
                    ]
                )

        rows, registry, by_seed = common_task_response_rows(
            records, bootstrap_resamples=100
        )
        holm_adjust(rows)

        self.assertEqual(len(rows), len(PRIMARY_SCENARIOS) * len(PRIMARY_BASELINES))
        self.assertEqual(len(registry), len(PRIMARY_SCENARIOS))
        self.assertTrue(all(row["common_task_periods_total"] == 1 for row in rows))
        self.assertTrue(
            all(
                abs(row["mean_difference_a_minus_b"] + 0.3) < 1e-12
                for row in rows
            )
        )
        self.assertTrue(all(row["win_rate"] == 1.0 for row in rows))
        self.assertTrue(all(record["common_task_period_count"] == 1 for record in by_seed))


class SampleSizeTests(unittest.TestCase):
    def test_detectable_effect_is_reported_for_each_power(self):
        registry = [
            {
                "family_id": "family",
                "number_of_comparisons": 5,
            }
        ]
        paired_rows = [
            {
                "family_id": "family",
                "n_independent_seed_pairs": 30,
            }
        ]

        rows = sample_size_rows(registry, paired_rows)

        self.assertEqual(len(rows), 2)
        self.assertLess(
            rows[0]["approximate_minimum_detectable_paired_cohen_dz"],
            rows[1]["approximate_minimum_detectable_paired_cohen_dz"],
        )


class OptimalityIntervalTests(unittest.TestCase):
    def test_exact_solver_audit_is_descriptive(self):
        raw = {
            "optimality": [
                {
                    "seed": seed,
                    "top_k": 4,
                    "optimal_objective": 1.0,
                    "ca_hmcd_objective": 1.0,
                    "optimal_hit": True,
                }
                for seed in range(3)
            ],
            "reference_solver": [
                {
                    "seed": seed,
                    "top_k": 4,
                    "reference_objective": 1.0,
                    "method_objective": 0.9,
                }
                for seed in range(3)
            ],
        }

        rows = optimality_interval_rows(raw, bootstrap_resamples=100)

        self.assertEqual(len(rows), 2)
        self.assertTrue(all("no superiority P value" in row["inference_policy"] for row in rows))
        exact = next(
            row for row in rows
            if row["validation"] == "small_random_exact_optimum"
        )
        self.assertEqual(exact["exact_hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

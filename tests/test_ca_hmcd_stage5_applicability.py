"""Regression tests for the registered Stage 5 applicability experiment."""

from __future__ import annotations

import csv
import gzip
import json
import unittest
from collections import Counter
from pathlib import Path

from ca_hmcd_stage5_applicability_runner import (
    METHODS,
    PROJECT_DIR,
    SCENARIOS,
    SEEDS,
    STEPS,
    execute_unit,
    registered_units,
    shard_path,
    validate_registration,
)


FORMAL_DIR = PROJECT_DIR / "ca_hmcd_stage5_applicability_formal_20260918"
ANALYSIS_DIR = PROJECT_DIR / "ca_hmcd_stage5_applicability_analysis_20260918"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class Stage5ApplicabilityTests(unittest.TestCase):
    def test_registered_design_counts(self):
        units = registered_units()
        self.assertEqual(len(units), 750)
        counts = Counter(str(unit["algorithm"]) for unit in units)
        self.assertEqual(counts, {method: 150 for method in METHODS})
        self.assertEqual({str(unit["scenario"]) for unit in units}, set(SCENARIOS))
        self.assertEqual({int(unit["seed"]) for unit in units}, set(SEEDS))

    def test_registration_and_frozen_hashes(self):
        validation = validate_registration(require_formal_environment=True)
        self.assertTrue(all(validation["checks"].values()))
        self.assertEqual(
            len(validation["registration"]["frozen_contract"]["seeds"]),
            30,
        )

    def test_formal_outputs_are_complete(self):
        metadata = json.loads(
            (FORMAL_DIR / "stage5_execution_metadata.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(metadata["audit_pass"])
        self.assertEqual(metadata["episode_results"], 750)
        self.assertEqual(metadata["per_step_results"], 750 * STEPS)
        self.assertEqual(metadata["pairing_audits"], 1800)
        audits = read_csv(FORMAL_DIR / "stage5_pairing_audit.csv")
        self.assertEqual(len(audits), 1800)
        self.assertTrue(
            all(row["passed"].strip().lower() == "true" for row in audits)
        )

    def test_analysis_outputs_and_claim_boundary(self):
        comparisons = read_csv(
            ANALYSIS_DIR / "stage5_paired_comparisons.csv"
        )
        claims = read_csv(ANALYSIS_DIR / "stage5_claim_freeze.csv")
        deadlines = read_csv(
            ANALYSIS_DIR / "stage5_deadline_selection.csv"
        )
        self.assertEqual(len(comparisons), 220)
        self.assertEqual(len(claims), 8)
        self.assertEqual(len(deadlines), 20)
        exact_rows = [
            row
            for row in comparisons
            if row["method_b"] == "HiGHS-MILP"
            and row["metric"]
            in {"external_objective", "independent_service_score"}
        ]
        self.assertEqual(len(exact_rows), 10)
        self.assertTrue(
            all(
                row["significant_after_holm_0_05"].lower() == "false"
                for row in exact_rows
            )
        )
        exact_claim = next(row for row in claims if row["claim_id"] == "C5.2")
        self.assertIn("did not show", exact_claim["approved_wording"])

    def test_deterministic_recalculation_for_all_methods(self):
        units = [
            unit
            for unit in registered_units()
            if unit["scenario"] == "airport_corridor"
            and int(unit["seed"]) == 950000
        ]
        self.assertEqual(len(units), len(METHODS))
        metrics = (
            "external_objective",
            "independent_service_score",
            "independent_coverage",
            "external_switch_count",
            "external_physical_cost",
            "feasibility_rate",
            "fallback",
        )
        for unit in units:
            with self.subTest(method=unit["algorithm"]):
                path = shard_path(FORMAL_DIR, unit)
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    original = json.load(handle)["result"]
                repeated = execute_unit(unit)
                for metric in metrics:
                    self.assertAlmostEqual(
                        float(original["episode"][metric]),
                        float(repeated["episode"][metric]),
                        places=12,
                    )
                self.assertEqual(
                    [row["state_fingerprint"] for row in original["rows"]],
                    [row["state_fingerprint"] for row in repeated["rows"]],
                )


if __name__ == "__main__":
    unittest.main()

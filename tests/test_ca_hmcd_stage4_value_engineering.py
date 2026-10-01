"""Regression tests for the registered Stage 4 experiment."""

from __future__ import annotations

import csv
import gzip
import json
import unittest
from collections import Counter
from pathlib import Path

from ca_hmcd_stage4_value_engineering_runner import (
    PROJECT_DIR,
    STEPS,
    execute_unit,
    registered_units,
    shard_path,
    validate_registration,
)


FORMAL_DIR = (
    PROJECT_DIR
    / "ca_hmcd_stage4_value_engineering_formal_20260918"
)
ANALYSIS_DIR = (
    PROJECT_DIR
    / "ca_hmcd_stage4_value_engineering_analysis_20260918"
)


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class Stage4ValueEngineeringTests(unittest.TestCase):
    def test_registered_design_counts(self):
        units = registered_units()
        self.assertEqual(len(units), 2070)
        counts = Counter(str(unit["study"]) for unit in units)
        self.assertEqual(
            counts,
            {
                "engineering": 1050,
                "complementarity_sweep": 300,
                "stability_sweep": 300,
                "redundancy_stress": 120,
                "repair_disturbance": 300,
            },
        )
        self.assertTrue(all(int(unit["seed"]) >= 950000 for unit in units))

    def test_registration_and_frozen_hashes(self):
        validation = validate_registration()
        self.assertTrue(all(validation["checks"].values()))
        self.assertEqual(
            len(validation["registration"]["frozen_contract"]["seeds"]),
            30,
        )

    def test_formal_outputs_are_complete(self):
        metadata = json.loads(
            (
                FORMAL_DIR / "stage4_execution_metadata.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(metadata["audit_pass"])
        self.assertEqual(metadata["episode_results"], 2070)
        self.assertEqual(metadata["per_step_results"], 2070 * STEPS)
        self.assertEqual(metadata["pairing_audits"], 7560)
        audits = read_csv(FORMAL_DIR / "stage4_pairing_audit.csv")
        self.assertEqual(len(audits), 7560)
        self.assertTrue(
            all(row["passed"].strip().lower() == "true" for row in audits)
        )

    def test_analysis_outputs_cover_all_gates(self):
        gates = read_csv(ANALYSIS_DIR / "stage4_gate_decisions.csv")
        self.assertEqual(len(gates), 6)
        self.assertEqual(
            {row["status"] for row in gates}
            - {"SUPPORTED", "CONTEXT_DEPENDENT", "NOT_SUPPORTED"},
            set(),
        )
        comparisons = read_csv(
            ANALYSIS_DIR / "stage4_paired_comparisons.csv"
        )
        self.assertEqual(len(comparisons), 280)
        self.assertTrue(
            all(int(row["pairs"]) > 0 for row in comparisons)
        )

    def test_stratified_deterministic_recalculation(self):
        units = registered_units()
        selected = (
            units[0],
            next(
                unit
                for unit in units
                if unit["study"] == "complementarity_sweep"
                and unit["stratum"] == "scale-2.00"
                and unit["method"] == "CA-HMCD"
            ),
            next(
                unit
                for unit in units
                if unit["study"] == "repair_disturbance"
                and unit["stratum"] == "noise-0.30"
                and unit["method"] == "CA-HMCD-RepairOn"
            ),
        )
        metrics = (
            "external_objective",
            "independent_service_score",
            "independent_coverage",
            "external_switch_count",
            "same_type_pair_ratio",
            "capability_overlap",
            "marginal_gain_waste",
            "feasibility_rate",
            "raw_feasible",
            "repair_applied",
        )
        for unit in selected:
            with self.subTest(unit=unit["unit_id"]):
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
                    [
                        row["state_fingerprint"]
                        for row in original["rows"]
                    ],
                    [
                        row["state_fingerprint"]
                        for row in repeated["rows"]
                    ],
                )


if __name__ == "__main__":
    unittest.main()

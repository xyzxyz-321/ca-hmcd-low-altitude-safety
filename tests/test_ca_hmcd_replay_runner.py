import csv
import json
import tempfile
import unittest
from pathlib import Path

try:
    import scipy  # noqa: F401
except (ImportError, OSError):
    SCIPY_AVAILABLE = False
else:
    SCIPY_AVAILABLE = True

from ca_hmcd_replay import (
    AntiUavMappingConfig,
    AntiUavTrackSpec,
    build_anti_uav_episode,
    write_replay_episode,
)
from ca_hmcd_replay_runner import run_registered_experiments


class RegisteredReplayRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _write_csv(path: Path, rows):
        fields = []
        for row in rows:
            for field in row:
                if field not in fields:
                    fields.append(field)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def _build_protocol(self) -> Path:
        protocol = self.root / "protocol"
        replay_dir = protocol / "replays" / "anti_uav410"
        replay_dir.mkdir(parents=True)
        track = self.root / "track.txt"
        track.write_text(
            "\n".join(
                f"{60 + frame * 2},100,{10 + frame % 4},8"
                for frame in range(80)
            ),
            encoding="utf-8",
        )
        episode = build_anti_uav_episode(
            [AntiUavTrackSpec(track, "AU-01")],
            "AU-TRIAL-01",
            AntiUavMappingConfig(640, 512),
            step_count=6,
        )
        replay_path = replay_dir / "AU-TRIAL-01.csv"
        write_replay_episode(replay_path, episode)
        rows = []
        for replicate, seed in ((1, 8100), (2, 8101)):
            rows.append(
                {
                    "workload_id": "AU-TRIAL-01",
                    "dataset": "anti_uav410",
                    "replicate": replicate,
                    "seed": seed,
                    "paired_across_algorithms": 1,
                    "independent_cluster": "AU-TRIAL-01",
                    "scenario": "anti_uav_load_1",
                    "task_load": 1,
                    "horizon_steps": 6,
                    "resources_count": 8,
                    "top_k": 6,
                    "node_limit": 1000,
                    "replay_path": "replays/anti_uav410/AU-TRIAL-01.csv",
                }
            )
        self._write_csv(protocol / "experiment_run_registry.csv", rows)
        (protocol / "protocol_config.json").write_text(
            json.dumps(
                {
                    "protocol_version": "test",
                    "seeds_per_workload": 2,
                }
            ),
            encoding="utf-8",
        )
        return protocol

    def test_runner_executes_audits_and_resumes_shards(self):
        protocol = self._build_protocol()
        output = self.root / "output"
        algorithms = ("CA-HMCD", "External-Auction")

        first = run_registered_experiments(
            protocol,
            output,
            mode="trial",
            algorithms=algorithms,
            workload_ids=("AU-TRIAL-01",),
            max_replicates=2,
        )

        self.assertEqual(len(first["episode_records"]), 4)
        self.assertEqual(len(first["step_records"]), 24)
        self.assertTrue(all(row["passed"] for row in first["audit_summary"]))
        self.assertEqual(
            {row["status"] for row in first["run_status"]},
            {"executed"},
        )
        self.assertTrue(
            all(row["same_true_state"] for row in first["pairing_audit"])
        )

        second = run_registered_experiments(
            protocol,
            output,
            mode="trial",
            algorithms=algorithms,
            workload_ids=("AU-TRIAL-01",),
            max_replicates=2,
        )
        self.assertEqual(
            {row["status"] for row in second["run_status"]},
            {"resumed"},
        )
        self.assertEqual(
            [
                (row["workload_id"], row["seed"], row["algorithm"])
                for row in first["episode_records"]
            ],
            [
                (row["workload_id"], row["seed"], row["algorithm"])
                for row in second["episode_records"]
            ],
        )
        for name in (
            "registered_episode_results.csv",
            "registered_per_step.csv",
            "registered_summary.csv",
            "registered_workload_summary.csv",
            "registered_pairing_audit.csv",
            "registered_execution_audit.csv",
            "registered_workload_coverage.csv",
            "registered_run_status.csv",
            "execution_metadata.json",
            "experiment_result.md",
        ):
            self.assertTrue((output / name).is_file(), name)

        replay_path = (
            protocol / "replays" / "anti_uav410" / "AU-TRIAL-01.csv"
        )
        replay_path.write_text(
            replay_path.read_text(encoding="utf-8-sig") + "\n",
            encoding="utf-8-sig",
        )
        with self.assertRaisesRegex(
            ValueError,
            "current execution contract",
        ):
            run_registered_experiments(
                protocol,
                output,
                mode="trial",
                algorithms=algorithms,
                workload_ids=("AU-TRIAL-01",),
                max_replicates=2,
            )

    def test_formal_mode_rejects_partial_algorithm_set(self):
        protocol = self._build_protocol()
        with self.assertRaisesRegex(
            ValueError,
            "complete prespecified algorithm set",
        ):
            run_registered_experiments(
                protocol,
                self.root / "formal",
                mode="formal",
                algorithms=("CA-HMCD", "External-Auction"),
            )

    def test_formal_control_executes_complete_no_pruning_registry(self):
        protocol = self._build_protocol()
        result = run_registered_experiments(
            protocol,
            self.root / "formal-control",
            mode="formal-control",
            algorithms=("No-Pruning",),
        )

        self.assertEqual(len(result["episode_records"]), 2)
        self.assertEqual(len(result["step_records"]), 12)
        self.assertTrue(result["metadata"]["audit_pass"])
        self.assertEqual(result["metadata"]["mode"], "formal-control")
        self.assertEqual(result["metadata"]["algorithms"], ["No-Pruning"])
        for row in result["episode_records"]:
            self.assertAlmostEqual(
                float(row["candidate_feasible_before_dominance"]),
                float(row["candidate_after_dominance"]),
            )
            self.assertAlmostEqual(
                float(row["candidate_after_dominance"]),
                float(row["candidate_after_diversity"]),
            )

    def test_formal_control_rejects_other_algorithms(self):
        protocol = self._build_protocol()
        with self.assertRaisesRegex(
            ValueError,
            "registered No-Pruning control",
        ):
            run_registered_experiments(
                protocol,
                self.root / "formal-control-invalid",
                mode="formal-control",
                algorithms=("CA-HMCD",),
            )

    @unittest.skipUnless(
        SCIPY_AVAILABLE,
        "formal external baselines require the locked SciPy environment",
    )
    def test_formal_external_executes_complete_registered_baseline_set(self):
        protocol = self._build_protocol()
        result = run_registered_experiments(
            protocol,
            self.root / "formal-external",
            mode="formal-external",
            algorithms=("HiGHS-MILP", "Genetic-Algorithm"),
        )

        self.assertEqual(len(result["episode_records"]), 4)
        self.assertEqual(len(result["step_records"]), 24)
        self.assertTrue(result["metadata"]["audit_pass"])
        self.assertEqual(result["metadata"]["mode"], "formal-external")
        self.assertEqual(
            result["metadata"]["algorithms"],
            ["HiGHS-MILP", "Genetic-Algorithm"],
        )
        for row in result["episode_records"]:
            self.assertAlmostEqual(float(row["feasibility_rate"]), 1.0)
            self.assertAlmostEqual(
                float(row["candidate_feasible_before_dominance"]),
                float(row["candidate_after_diversity"]),
            )

    def test_formal_external_rejects_partial_baseline_set(self):
        protocol = self._build_protocol()
        with self.assertRaisesRegex(
            ValueError,
            "complete registered optimization and evolutionary baseline set",
        ):
            run_registered_experiments(
                protocol,
                self.root / "formal-external-invalid",
                mode="formal-external",
                algorithms=("HiGHS-MILP",),
            )

    def test_formal_reference_executes_complete_registered_reference(self):
        protocol = self._build_protocol()
        result = run_registered_experiments(
            protocol,
            self.root / "formal-reference",
            mode="formal-reference",
            algorithms=("CA-HMCD",),
        )

        self.assertEqual(len(result["episode_records"]), 2)
        self.assertEqual(len(result["step_records"]), 12)
        self.assertTrue(result["metadata"]["audit_pass"])
        self.assertEqual(result["metadata"]["mode"], "formal-reference")
        self.assertEqual(result["metadata"]["algorithms"], ["CA-HMCD"])


if __name__ == "__main__":
    unittest.main()

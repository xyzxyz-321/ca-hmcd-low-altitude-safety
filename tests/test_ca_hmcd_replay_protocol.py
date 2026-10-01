import csv
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from ca_hmcd_replay import read_replay_episode
from ca_hmcd_replay_protocol import ReplayProtocolConfig, build_replay_protocol


class ReplayProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _write_csv(path: Path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = []
        for row in rows:
            for field in row:
                if field not in fields:
                    fields.append(field)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def _write_uzh_zip(self, sequence_id: str, lateral_rate: float) -> Path:
        path = self.root / "uzh" / f"{sequence_id}.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        start = datetime(2018, 10, 29, 12, 0, 0)
        lines = [
            '0,2018-10-29 11:59:00.000,Operator,RPG,883600,host,"MS60",version',
            "",
            "0,2018-10-29 12:00:00.000,Start!",
        ]
        for index in range(80):
            offset = index * 0.05
            stamp = (start + timedelta(seconds=offset)).isoformat(
                sep=" ", timespec="milliseconds"
            )
            x = index * 0.16
            y = lateral_rate * index
            z = 1.0 + 0.01 * index
            lines.append(
                f"3,{stamp},%R1Q,2082,{index % 10}:10000,1,{stamp},"
                f"%R1P,0,{index % 10}:0,{x},{y},{z},{index * 50},"
                f"{x},{y},{z},{index * 50}"
            )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{sequence_id}/leica.txt", "\n".join(lines))
        return path

    def _write_anti_track(self, split: str, sequence_id: str, mode: str) -> Path:
        path = self.root / "anti" / split / f"{sequence_id}.txt"
        attribute_dir = path.parent / "att"
        attribute_dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for frame in range(300):
            if mode == "visibility" and 120 <= frame < 145:
                lines.append("0,0,0,0")
            else:
                stride = 3 if mode == "fast" else 1
                size = 6 if mode == "small" else 14
                lines.append(f"{80 + stride * frame},120,{size},{size}")
        path.write_text("\n".join(lines), encoding="utf-8")
        attributes = {
            "nominal": "0,0,0,0,0,0,0,1,0,0",
            "visibility": "0,1,0,0,1,0,0,1,0,0",
            "fast": "0,0,0,1,0,0,0,1,0,0",
            "small": "0,0,1,0,0,0,1,0,0,0",
        }[mode]
        (attribute_dir / path.name).write_text(attributes, encoding="utf-8")
        return path

    def test_protocol_locks_sources_workloads_and_paired_seeds(self):
        uzh_rows = []
        for sequence_id, lateral_rate in (
            ("indoor_forward_1", 0.00),
            ("outdoor_45_1", 0.03),
        ):
            path = self._write_uzh_zip(sequence_id, lateral_rate)
            uzh_rows.append(
                {
                    "sequence_id": sequence_id,
                    "source_path": str(path),
                    "source_sha256": f"sha-{sequence_id}",
                    "median_cleaned_speed_mps": 3.2 + lateral_rate,
                    "cleaned_duration_s": 3.95,
                    "cleaned_path_length_m": 12.8,
                }
            )
        uzh_manifest = self.root / "uzh_manifest.csv"
        self._write_csv(uzh_manifest, uzh_rows)

        anti_rows = []
        test_modes = ("nominal", "visibility", "fast", "small", "nominal", "visibility")
        for index, mode in enumerate(test_modes, start=1):
            sequence_id = f"test-{index:02d}"
            path = self._write_anti_track("test", sequence_id, mode)
            flags = {
                "out_of_view": int(mode == "visibility"),
                "scale_variation": int(mode == "small"),
                "fast_motion": int(mode == "fast"),
                "occlusion": int(mode == "visibility"),
                "tiny_size": int(mode == "small"),
            }
            anti_rows.append(
                {
                    "split": "test",
                    "sequence_id": sequence_id,
                    "source_path": str(path),
                    "source_sha256": f"sha-{sequence_id}",
                    "raw_frames": 300,
                    "active_frames": 275 if mode == "visibility" else 300,
                    "inactive_frames": 25 if mode == "visibility" else 0,
                    "active_ratio": 275 / 300 if mode == "visibility" else 1.0,
                    "visibility_events": int(mode == "visibility"),
                    **flags,
                }
            )
        for split in ("train", "val"):
            sequence_id = f"{split}-excluded"
            path = self._write_anti_track(split, sequence_id, "nominal")
            anti_rows.append(
                {
                    "split": split,
                    "sequence_id": sequence_id,
                    "source_path": str(path),
                    "source_sha256": f"sha-{sequence_id}",
                    "raw_frames": 300,
                    "active_frames": 300,
                    "inactive_frames": 0,
                    "active_ratio": 1.0,
                    "visibility_events": 0,
                }
            )
        anti_manifest = self.root / "anti_manifest.csv"
        self._write_csv(anti_manifest, anti_rows)

        output = self.root / "protocol"
        result = build_replay_protocol(
            uzh_manifest,
            anti_manifest,
            output,
            ReplayProtocolConfig(
                anti_workload_plan=((2, 1), (4, 1)),
                seeds_per_workload=30,
                resources_count=10,
                top_k=12,
                node_limit=4000,
            ),
        )

        self.assertEqual(len(result["workloads"]), 4)
        self.assertEqual(len(result["tasks"]), 8)
        self.assertEqual(len(result["seeds"]), 120)
        self.assertTrue(all(row["passed"] for row in result["audit"]))
        self.assertEqual(
            len(
                {
                    row["sequence_id"]
                    for row in result["sources"]
                    if row["dataset"] == "anti_uav410"
                    and row["used_in_formal_workload"]
                }
            ),
            6,
        )
        self.assertTrue(
            all(
                not row["used_in_formal_workload"]
                for row in result["sources"]
                if row["dataset"] == "anti_uav410"
                and row["protocol_split"] != "evaluation"
            )
        )
        self.assertEqual(
            {
                (
                    row["resources_count"],
                    row["top_k"],
                    row["node_limit"],
                )
                for row in result["workloads"]
            },
            {(10, 12, 4000)},
        )
        self.assertEqual(len(result["replay_paths"]), 4)
        self.assertTrue(
            all(read_replay_episode(path).step_count == 12
                for path in result["replay_paths"])
        )
        for name in (
            "source_registry.csv",
            "scenario_registry.csv",
            "workload_registry.csv",
            "workload_task_manifest.csv",
            "seed_registry.csv",
            "experiment_run_registry.csv",
            "protocol_audit.csv",
            "protocol_config.json",
            "protocol_report.md",
        ):
            self.assertTrue((output / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()

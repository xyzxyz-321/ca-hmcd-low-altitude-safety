import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from ca_hmcd_replay import (
    AntiUavMappingConfig,
    AntiUavTrackSpec,
    BlackbirdMappingConfig,
    BlackbirdTrackSpec,
    UzhFpvCleaningConfig,
    UzhFpvTrackSpec,
    apply_observation_disturbance,
    audit_public_replay_data,
    build_anti_uav_episode,
    build_blackbird_episode,
    build_uzh_fpv_episode,
    main,
    read_anti_uav_attributes,
    read_replay_episode,
    read_uzh_fpv_leica,
    run_replay_benchmark,
    write_replay_episode,
)
from ca_hmcd_simulation import audit_paired_protocol, run_episode


class ReplayAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_uzh_zip(self, path: Path, sequence: str = "flight") -> Path:
        start = datetime(2018, 10, 29, 12, 0, 0)
        lines = [
            '0,2018-10-29 11:59:00.000,Operator,RPG,883600,host,"MS60",version',
            "",
            "0,2018-10-29 12:00:00.000,Start!",
        ]
        points = []
        for index in range(10):
            points.append((index * 0.05, 0.0, 0.0, 0.0))
        for index in range(40):
            x = index * 0.20
            if index == 20:
                x += 100.0
            points.append(((index + 10) * 0.05, x, 0.1 * index, 1.0))
        for index in range(10):
            points.append(((index + 50) * 0.05, 7.8, 3.9, 1.0))
        for index, (offset, x, y, z) in enumerate(points):
            stamp = (start + timedelta(seconds=offset)).isoformat(
                sep=" ", timespec="milliseconds"
            )
            lines.append(
                f"3,{stamp},%R1Q,2082,{index % 10}:10000,1,{stamp},"
                f"%R1P,0,{index % 10}:0,{x},{y},{z},{index * 50},"
                f"{x},{y},{z},{index * 50}"
            )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{sequence}/leica.txt", "\n".join(lines))
        return path

    def test_blackbird_headerless_pose_csv_builds_replay(self):
        left = self.root / "left.csv"
        right = self.root / "right.csv"
        left.write_text(
            "\n".join(
                f"{index * 100000000},{0.25 * index},0,{1.0 + 0.05 * index},1,0,0,0"
                for index in range(20)
            ),
            encoding="utf-8",
        )
        right.write_text(
            "\n".join(
                f"{index * 100000000},{2.0 - 0.15 * index},1,{1.5},1,0,0,0"
                for index in range(20)
            ),
            encoding="utf-8",
        )

        episode = build_blackbird_episode(
            [
                BlackbirdTrackSpec(left, "BB-1"),
                BlackbirdTrackSpec(right, "BB-2", offset_steps=2),
            ],
            "blackbird-smoke",
            step_count=8,
            mapping=BlackbirdMappingConfig(protected_zone=(0.0, 0.0, 0.0)),
            timestamp_unit="ns",
        )

        self.assertEqual(episode.dataset, "blackbird")
        self.assertEqual(episode.step_count, 8)
        self.assertEqual(episode.task_count, 2)
        self.assertFalse(episode.steps[0].tasks[1].true_active)
        self.assertTrue(episode.steps[3].tasks[1].true_active)
        self.assertTrue(
            all(
                0.0 <= value <= 1.0
                for step in episode.steps
                for task in step.tasks
                for value in task.true_feature
            )
        )

    def test_anti_uav_txt_and_json_build_replay(self):
        txt_path = self.root / "track-a.txt"
        txt_path.write_text(
            "\n".join(
                f"{100 + index * 4},{80 + index * 3},{20 + index},{12 + index * 0.5}"
                for index in range(18)
            ),
            encoding="utf-8",
        )
        json_path = self.root / "track-b.json"
        json_path.write_text(
            json.dumps(
                {
                    "gt_rect": [
                        [300 - index * 5, 180 + index * 2, 18, 10]
                        for index in range(18)
                    ],
                    "existence": [1] * 12 + [0] * 2 + [1] * 4,
                }
            ),
            encoding="utf-8",
        )

        episode = build_anti_uav_episode(
            [
                AntiUavTrackSpec(txt_path, "AU-1"),
                AntiUavTrackSpec(json_path, "AU-2"),
            ],
            "anti-uav-smoke",
            AntiUavMappingConfig(640, 512, 25.0),
            step_count=9,
        )

        self.assertEqual(episode.dataset, "anti_uav410")
        self.assertEqual(episode.task_count, 2)
        self.assertTrue(any(not step.tasks[1].true_active for step in episode.steps))
        self.assertGreater(
            max(step.tasks[0].true_feature[1] for step in episode.steps),
            0.0,
        )

    def test_uzh_fpv_zip_is_cleaned_and_builds_replay(self):
        archive = self.write_uzh_zip(self.root / "uzh.zip", "uzh")
        samples, audit = read_uzh_fpv_leica(
            archive,
            UzhFpvCleaningConfig(
                max_speed_mps=25.0,
                max_gap_s=0.75,
                motion_speed_threshold_mps=0.20,
                motion_padding_s=0.10,
            ),
        )

        self.assertEqual(audit["quality_status"], "PASS")
        self.assertGreaterEqual(audit["isolated_spikes_removed"], 1)
        self.assertLess(audit["cleaned_points"], audit["parsed_points"])
        self.assertAlmostEqual(samples[0].x, 0.0)
        self.assertAlmostEqual(samples[0].y, 0.0)
        self.assertAlmostEqual(samples[0].z, 0.0)

        episode = build_uzh_fpv_episode(
            [UzhFpvTrackSpec(archive, "UZH-1")],
            "uzh-smoke",
            step_count=8,
            cleaning=UzhFpvCleaningConfig(motion_padding_s=0.10),
        )
        self.assertEqual(episode.dataset, "uzh_fpv")
        self.assertEqual(episode.step_count, 8)
        self.assertEqual(
            episode.metadata["registration"],
            "trajectory_endpoint_to_common_protected_zone",
        )
        final = episode.steps[-1].tasks[0]
        self.assertTrue(final.true_active)

    def test_anti_uav_frame_basis_preserves_visibility_event_and_attributes(self):
        split = self.root / "annos" / "test"
        att = split / "att"
        att.mkdir(parents=True)
        path = split / "visibility.txt"
        lines = []
        for frame in range(120):
            if 50 <= frame <= 60:
                lines.append("0,0,0,0")
            else:
                lines.append(f"{100 + frame},120,12,8")
        path.write_text("\n".join(lines), encoding="utf-8")
        (att / path.name).write_text(
            "0,1,0,1,1,0,0,1,0,0",
            encoding="utf-8",
        )
        attributes = read_anti_uav_attributes(path)
        self.assertEqual(attributes["out_of_view"], 1)
        self.assertEqual(attributes["occlusion"], 1)

        episode = build_anti_uav_episode(
            [
                AntiUavTrackSpec(
                    path,
                    "AU-1",
                    window_mode="visibility",
                    window_frames=50,
                    event_padding_frames=10,
                )
            ],
            "anti-event",
            AntiUavMappingConfig(640, 512),
            step_count=12,
        )

        self.assertEqual(episode.metadata["time_basis"], "frames")
        self.assertIsNone(episode.metadata["fps"])
        self.assertTrue(
            any(not step.tasks[0].true_active for step in episode.steps)
        )
        window = episode.metadata["track_windows"][0]
        self.assertGreater(window["inactive_frames_in_window"], 0)
        self.assertEqual(window["out_of_view"], 1)

    def test_data_quality_audit_writes_manifests(self):
        uzh_dir = self.root / "uzh"
        uzh_dir.mkdir()
        self.write_uzh_zip(uzh_dir / "flight.zip")
        anti_root = self.root / "anti"
        split = anti_root / "train"
        att = split / "att"
        att.mkdir(parents=True)
        annotation = split / "track.txt"
        annotation.write_text(
            "\n".join(
                "0,0,0,0" if 10 <= index < 15 else f"{index},20,8,8"
                for index in range(40)
            ),
            encoding="utf-8",
        )
        (att / annotation.name).write_text(
            "0,1,0,0,1,0,0,1,0,0",
            encoding="utf-8",
        )
        output = self.root / "audit"

        result = audit_public_replay_data(uzh_dir, anti_root, output)

        self.assertEqual(len(result["uzh_rows"]), 1)
        self.assertEqual(len(result["anti_rows"]), 1)
        for name in (
            "uzh_fpv_quality_manifest.csv",
            "anti_uav410_quality_manifest.csv",
            "data_quality_summary.csv",
            "data_quality_report.md",
        ):
            self.assertTrue((output / name).is_file(), name)

    def test_replay_round_trip_and_observation_disturbance(self):
        path = self.root / "track.txt"
        path.write_text(
            "\n".join(f"{10 + index},20,8,8" for index in range(20)),
            encoding="utf-8",
        )
        episode = build_anti_uav_episode(
            [AntiUavTrackSpec(path, "AU-1")],
            "round-trip",
            AntiUavMappingConfig(320, 256, 20.0),
            step_count=6,
        )
        disturbed = apply_observation_disturbance(
            episode,
            seed=17,
            latency_steps=1,
            dropout_probability=1.0,
            dropout_burst_steps=2,
            feature_noise=0.01,
        )
        output = self.root / "replay.csv"
        write_replay_episode(output, disturbed)
        restored = read_replay_episode(output)

        self.assertEqual(restored.task_ids, episode.task_ids)
        self.assertEqual(restored.step_count, episode.step_count)
        self.assertTrue(restored.steps[0].tasks[0].true_active)
        self.assertFalse(restored.steps[0].tasks[0].observed_active)

    def test_replay_is_paired_across_algorithms(self):
        first = self.root / "first.csv"
        second = self.root / "second.csv"
        first.write_text(
            "\n".join(
                f"{index * 0.1},{index * 0.08},0.2,1.0,1,0,0,0"
                for index in range(30)
            ),
            encoding="utf-8",
        )
        second.write_text(
            "\n".join(
                f"{index * 0.1},{2.0 - index * 0.04},1.0,1.4,1,0,0,0"
                for index in range(30)
            ),
            encoding="utf-8",
        )
        episode = build_blackbird_episode(
            [
                BlackbirdTrackSpec(first, "BB-1"),
                BlackbirdTrackSpec(second, "BB-2"),
            ],
            "paired",
            step_count=5,
            timestamp_unit="s",
        )

        _, ca_rows = run_episode(
            "CA-HMCD",
            9100,
            episode.step_count,
            8,
            episode.task_count,
            "blackbird_replay",
            6,
            node_limit_override=1000,
            replay_episode=episode,
        )
        _, auction_rows = run_episode(
            "External-Auction",
            9100,
            episode.step_count,
            8,
            episode.task_count,
            "blackbird_replay",
            6,
            node_limit_override=1000,
            replay_episode=episode,
        )

        self.assertEqual(
            [row["state_fingerprint"] for row in ca_rows],
            [row["state_fingerprint"] for row in auction_rows],
        )
        self.assertTrue(all(row["replay_dataset"] == "blackbird" for row in ca_rows))
        audit = audit_paired_protocol(
            ca_rows + auction_rows,
            ("CA-HMCD", "External-Auction"),
            expected_seeds=1,
            expected_steps=episode.step_count,
        )
        self.assertTrue(all(row["same_state"] for row in audit))
        self.assertTrue(all(row["same_active_task_ids"] for row in audit))

    def test_formal_minimum_is_enforced_per_dataset(self):
        path = self.root / "track.txt"
        path.write_text(
            "\n".join(f"{10 + index},20,8,8" for index in range(20)),
            encoding="utf-8",
        )
        anti = build_anti_uav_episode(
            [AntiUavTrackSpec(path, "AU-1")],
            "anti-one",
            AntiUavMappingConfig(320, 256, 20.0),
            step_count=4,
        )
        pose = self.root / "pose.csv"
        pose.write_text(
            "\n".join(
                f"{index * 0.1},{index * 0.05},0,1,1,0,0,0"
                for index in range(20)
            ),
            encoding="utf-8",
        )
        blackbird = build_blackbird_episode(
            [BlackbirdTrackSpec(pose, "BB-1")],
            "blackbird-one",
            step_count=4,
            timestamp_unit="s",
        )

        with self.assertRaisesRegex(ValueError, "Each formal replay dataset"):
            run_replay_benchmark(
                [anti, blackbird],
                self.root / "result",
                algorithms=("CA-HMCD", "External-Auction"),
                require_minimum_episodes=2,
            )

    def test_cli_prepare_disturb_and_benchmark(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text(
            "\n".join(f"{80 + index * 2},90,18,12" for index in range(24)),
            encoding="utf-8",
        )
        second.write_text(
            "\n".join(f"{400 - index * 3},140,16,10" for index in range(24)),
            encoding="utf-8",
        )
        replay = self.root / "anti.csv"
        disturbed = self.root / "anti-disturbed.csv"
        results = self.root / "results"

        self.assertEqual(
            main(
                [
                    "anti-uav",
                    "--track",
                    f"AU-1={first}",
                    "--track",
                    f"AU-2={second}",
                    "--sequence-id",
                    "cli-smoke",
                    "--width",
                    "640",
                    "--height",
                    "512",
                    "--fps",
                    "25",
                    "--steps",
                    "5",
                    "--output",
                    str(replay),
                ]
            ),
            0,
        )
        self.assertEqual(
            main(
                [
                    "disturb",
                    "--input",
                    str(replay),
                    "--output",
                    str(disturbed),
                    "--seed",
                    "7000",
                    "--latency-steps",
                    "1",
                    "--dropout-probability",
                    "0.1",
                ]
            ),
            0,
        )
        self.assertEqual(
            main(
                [
                    "benchmark",
                    "--replay",
                    str(disturbed),
                    "--output-dir",
                    str(results),
                    "--minimum-episodes",
                    "1",
                    "--algorithms",
                    "CA-HMCD",
                    "External-Auction",
                    "--resources",
                    "8",
                    "--top-k",
                    "6",
                    "--node-limit",
                    "1000",
                ]
            ),
            0,
        )
        for name in (
            "replay_episode_results.csv",
            "replay_per_step.csv",
            "replay_summary.csv",
            "replay_statistical_tests.csv",
            "replay_holm_family_registry.csv",
            "replay_common_task_response_by_episode.csv",
            "replay_bounded_metric_intervals.csv",
            "replay_protocol_audit.csv",
            "replay_episode_manifest.csv",
            "evidence_boundary.md",
        ):
            self.assertTrue((results / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()

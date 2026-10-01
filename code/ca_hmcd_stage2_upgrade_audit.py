"""Post-run integrity audit for the upgraded Stage-2 experiment package."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Sequence

from ca_hmcd_simulation import (
    EVALUATOR_VERSION,
    MODEL_VERSION,
    SERVICE_ENDPOINT_VERSION,
)


PRIMARY_SCENARIOS = ("balanced", "scarce", "volatile", "semisynthetic")
PRIMARY_ALGORITHMS = (
    "CA-HMCD",
    "Greedy",
    "External-Auction",
    "No-Synergy",
    "No-Stability",
    "Random",
)
ENGINEERING_SCENARIOS = (
    "airport_corridor",
    "energy_facility",
    "public_event",
    "urban_corridor",
    "industrial_zone",
)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def truth(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def assert_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, observed {actual!r}")


def assert_all(condition: Sequence[bool], message: str) -> None:
    if not condition or not all(condition):
        raise AssertionError(message)


def audit(output_dir: Path, seeds: int, steps: int) -> Dict[str, object]:
    expected_files = (
        "summary.csv",
        "per_step.csv",
        "ablation.csv",
        "mechanism.csv",
        "robustness.csv",
        "scalability.csv",
        "scalability_per_step.csv",
        "reference_solver.csv",
        "redundancy_stress.csv",
        "redundancy_per_step.csv",
        "solver_fairness.csv",
        "candidate_pool_sensitivity.csv",
        "external_baseline_pool_fairness.csv",
        "external_baseline_pool_fairness_tests.csv",
        "external_baseline_pool_mode_effects.csv",
        "pruning_control.csv",
        "protocol_audit.csv",
        "engineering_validation.csv",
        "parameter_robustness.csv",
        "raw_results.json",
        "stage2_experiment_report.md",
        "compliance_report.md",
    )
    missing = [name for name in expected_files if not (output_dir / name).exists()]
    if missing:
        raise AssertionError(f"Missing output files: {', '.join(missing)}")

    summary = read_csv(output_dir / "summary.csv")
    per_step = read_csv(output_dir / "per_step.csv")
    protocol = read_csv(output_dir / "protocol_audit.csv")
    scalability = read_csv(output_dir / "scalability.csv")
    scalability_steps = read_csv(output_dir / "scalability_per_step.csv")
    engineering = read_csv(output_dir / "engineering_validation.csv")
    parameters = read_csv(output_dir / "parameter_robustness.csv")
    redundancy = read_csv(output_dir / "redundancy_stress.csv")
    reference = read_csv(output_dir / "reference_solver.csv")
    pruning = read_csv(output_dir / "pruning_control.csv")
    external_pool = read_csv(output_dir / "external_baseline_pool_fairness.csv")

    assert_equal(
        len(summary),
        len(PRIMARY_SCENARIOS) * len(PRIMARY_ALGORITHMS),
        "Primary summary row count",
    )
    assert_equal(
        len(per_step),
        len(PRIMARY_SCENARIOS) * len(PRIMARY_ALGORITHMS) * seeds * steps,
        "Primary per-step row count",
    )
    assert_all(
        [int(float(row["replications"])) == seeds for row in summary],
        "Primary summary does not use the frozen seed count",
    )
    assert_equal(
        {
            (row["scenario"], row["algorithm"])
            for row in summary
        },
        {
            (scenario, algorithm)
            for scenario in PRIMARY_SCENARIOS
            for algorithm in PRIMARY_ALGORITHMS
        },
        "Primary scenario-method coverage",
    )

    assert_equal(
        {row["model_version"] for row in per_step},
        {MODEL_VERSION},
        "Primary model version",
    )
    assert_equal(
        {row["evaluator_version"] for row in per_step},
        {EVALUATOR_VERSION},
        "Primary complete-model evaluator version",
    )
    assert_equal(
        {row["service_endpoint_version"] for row in per_step},
        {SERVICE_ENDPOINT_VERSION},
        "Primary service endpoint version",
    )
    assert_all(
        [
            int(float(row["candidate_dominance_removed"]))
            == int(float(row["candidate_feasible_before_dominance"]))
            - int(float(row["candidate_after_dominance"]))
            for row in per_step
        ],
        "Dominance-stage candidate accounting failed",
    )
    assert_all(
        [
            int(float(row["candidate_diversity_removed"]))
            == int(float(row["candidate_after_dominance"]))
            - int(float(row["candidate_after_diversity"]))
            for row in per_step
        ],
        "Fixed-K candidate accounting failed",
    )
    assert_all(
        [
            int(float(row["candidate_screen_applied_tasks"])) == 0
            for row in per_step
        ],
        "Compatibility screening was unexpectedly active in a primary scenario",
    )

    assert_equal(
        len(protocol),
        len(PRIMARY_SCENARIOS) * seeds * steps,
        "Protocol-audit row count",
    )
    assert_all(
        [
            not row["missing_algorithms"]
            and truth(row["same_state"])
            and truth(row["same_active_task_load"])
            and truth(row["same_active_task_ids"])
            and truth(row["same_model_version"])
            and truth(row["same_evaluator_version"])
            for row in protocol
        ],
        "Paired fairness protocol failed",
    )

    assert_equal(len(scalability), 14, "Scale summary row count")
    assert_all(
        [int(float(row["replications"])) >= 30 for row in scalability],
        "A scale configuration has fewer than 30 seeds",
    )
    assert_all(
        [
            abs(float(row["active_tasks_mean"]) - float(row["tasks"])) < 1e-9
            for row in scalability
        ],
        "Scale task load was not fixed to all active tasks",
    )
    assert_equal(
        len(scalability_steps),
        7 * 2 * max(30, seeds) * 3,
        "Scale per-step row count",
    )

    assert_equal(
        len(engineering),
        len(ENGINEERING_SCENARIOS) * 4,
        "Engineering summary row count",
    )
    assert_all(
        [int(float(row["replications"])) == seeds for row in engineering],
        "Engineering validation seed count",
    )
    assert_equal(len(parameters), 6 * 3 * 3, "Parameter-grid row count")
    assert_all(
        [int(float(row["replications"])) == seeds for row in parameters],
        "Parameter robustness seed count",
    )
    assert_equal(len(redundancy), 4, "Redundancy-stress summary row count")
    assert_all(
        [int(float(row["replications"])) >= 30 for row in redundancy],
        "Redundancy-stress seed count",
    )
    assert_equal(len(pruning), 2, "Pruning-control summary row count")
    assert_all(
        [int(float(row["replications"])) == seeds for row in pruning],
        "Pruning-control seed count",
    )

    expected_reference_rows = seeds * 3
    assert_equal(len(reference), expected_reference_rows, "Exact-reference row count")
    assert_equal(
        {int(float(row["top_k"])) for row in reference},
        {4, 8, 12},
        "Exact-reference K levels",
    )
    assert_equal(len(external_pool), 4 * 2 * 3, "External pool-control rows")
    assert_all(
        [int(float(row["replications"])) == seeds for row in external_pool],
        "External pool-control seed count",
    )

    config = json.loads(
        (output_dir / "raw_results.json").read_text(encoding="utf-8")
    )["config"]
    assert_equal(config["value_model_version"], MODEL_VERSION, "Raw model version")
    assert_equal(
        config["external_evaluator"], EVALUATOR_VERSION, "Raw evaluator version"
    )
    assert_equal(
        config["independent_evaluator"],
        SERVICE_ENDPOINT_VERSION,
        "Raw service endpoint version",
    )

    return {
        "audit_date": "2026-09-16",
        "status": "PASS",
        "model_version": MODEL_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "service_endpoint_version": SERVICE_ENDPOINT_VERSION,
        "primary_episode_count": len(summary) * seeds,
        "primary_per_step_count": len(per_step),
        "protocol_units": len(protocol),
        "scale_configurations": len(scalability),
        "engineering_configurations": len(engineering),
        "parameter_configurations": len(parameters),
        "reference_runs": len(reference),
        "external_pool_configurations": len(external_pool),
        "primary_screen_activations": sum(
            int(float(row["candidate_screen_applied_tasks"])) for row in per_step
        ),
    }


def write_report(output_dir: Path, result: Dict[str, object]) -> None:
    (output_dir / "stage2_upgrade_integrity_audit.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report = f"""# 第二阶段实验升级完整性审计

审计日期：2026-09-16

结论：**PASS**

- 模型版本：`{result['model_version']}`
- 完整模型评价器：`{result['evaluator_version']}`
- 服务端点：`{result['service_endpoint_version']}`
- 主实验 episode：{result['primary_episode_count']}
- 主实验逐周期记录：{result['primary_per_step_count']}
- 配对公平性审计单元：{result['protocol_units']}
- 规模配置：{result['scale_configurations']}
- 工程验证配置：{result['engineering_configurations']}
- 参数稳健性配置：{result['parameter_configurations']}
- 精确参考运行：{result['reference_runs']}
- 双候选池外部基线配置：{result['external_pool_configurations']}
- 主实验资源预筛触发次数：{result['primary_screen_activations']}

审计确认了 30 个配对种子、具体活动任务 ID、真实状态指纹、模型和评价器
版本、候选阶段计数恒等式、规模实验固定全活动负载以及所有正式配置的完整性。
"""
    (output_dir / "stage2_upgrade_integrity_audit.md").write_text(
        report, encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ca_hmcd_stage2_upgrade_20260916"),
    )
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--steps", type=int, default=12)
    args = parser.parse_args()
    result = audit(args.output_dir, args.seeds, args.steps)
    write_report(args.output_dir, result)
    print(f"PASS: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()

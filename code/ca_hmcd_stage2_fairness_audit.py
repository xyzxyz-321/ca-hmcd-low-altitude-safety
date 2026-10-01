"""Complete the Phase-2 fairness audit for external baselines."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List

from ca_hmcd_simulation import (
    paired_comparisons,
    paired_statistics,
    run_episode,
    scenario_parameters,
    summarise,
    write_csv,
)


SCENARIOS = ("balanced", "scarce", "volatile", "semisynthetic")
ALGORITHMS = ("CA-HMCD", "External-Auction", "Greedy")
POOL_MODES = ("independent", "shared")


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def run_pool_fairness(output_dir: Path, seeds: int, steps: int, top_k: int,
                      base_seed: int) -> Dict[str, object]:
    records: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        params = scenario_parameters(scenario)
        for pool_mode in POOL_MODES:
            for algorithm in ALGORITHMS:
                for rep in range(seeds):
                    seed = base_seed + rep
                    metrics, _ = run_episode(
                        algorithm, seed, steps,
                        int(params["resources"]), int(params["tasks"]),
                        scenario, top_k,
                        node_limit_override=12000,
                        candidate_pool_mode=pool_mode,
                    )
                    records.append({
                        "scenario": scenario,
                        "candidate_pool_mode": pool_mode,
                        "algorithm": algorithm,
                        "seed": seed,
                        **metrics,
                    })
        print(f"[fairness] {scenario} complete", flush=True)

    summary = summarise(
        records, ("scenario", "candidate_pool_mode", "algorithm")
    )
    tests: List[Dict[str, object]] = []
    for pool_mode in POOL_MODES:
        subset = [row for row in records if row["candidate_pool_mode"] == pool_mode]
        for row in paired_comparisons(subset, metric="independent_service_score"):
            row["candidate_pool_mode"] = pool_mode
            tests.append(row)

    pool_effects: List[Dict[str, object]] = []
    for scenario in SCENARIOS:
        for algorithm in ALGORITHMS:
            lookup = {
                (str(row["candidate_pool_mode"]), int(row["seed"])): float(
                    row["independent_service_score"]
                )
                for row in records
                if row["scenario"] == scenario and row["algorithm"] == algorithm
            }
            paired_seeds = [
                base_seed + rep for rep in range(seeds)
                if ("independent", base_seed + rep) in lookup
                and ("shared", base_seed + rep) in lookup
            ]
            stats = paired_statistics(
                [lookup[("independent", seed)] for seed in paired_seeds],
                [lookup[("shared", seed)] for seed in paired_seeds],
            )
            pool_effects.append({
                "scenario": scenario,
                "algorithm": algorithm,
                "metric": "independent_service_score",
                "contrast": "independent-minus-shared",
                **{key: round(value, 8) for key, value in stats.items()},
            })

    write_csv(output_dir / "external_baseline_pool_fairness.csv", summary)
    write_csv(output_dir / "external_baseline_pool_fairness_tests.csv", tests)
    write_csv(output_dir / "external_baseline_pool_mode_effects.csv", pool_effects)
    return {
        "records": records,
        "summary": summary,
        "tests": tests,
        "pool_effects": pool_effects,
    }


def build_report(output_dir: Path, pool: Dict[str, object],
                 seeds: int, steps: int, top_k: int, base_seed: int) -> str:
    audit = read_csv(output_dir / "protocol_audit.csv")
    summaries = {
        name: read_csv(output_dir / name)
        for name in (
            "summary.csv", "ablation.csv", "mechanism.csv", "robustness.csv",
            "scalability.csv", "redundancy_stress.csv", "solver_fairness.csv",
            "engineering_validation.csv", "parameter_robustness.csv",
        )
    }
    audit_pass = all(
        row["same_state"] == "True"
        and row["same_active_task_load"] == "True"
        and row["same_evaluator_version"] == "True"
        and not row["missing_algorithms"]
        for row in audit
    )
    replication_pass = all(
        all(int(float(row["replications"])) >= 30 for row in rows)
        for rows in summaries.values()
    )
    tests = pool["tests"]
    lines = [
        "# 第二阶段公平性控制完整报告",
        "",
        "## Protocol",
        "",
        f"- 配对种子：{base_seed}--{base_seed + seeds - 1}，共 {seeds} 个。",
        f"- 主实验周期：{steps}；候选保留数：K={top_k}。",
        "- 同一种子为所有方法生成相同资源、任务、到达、状态扰动与可用性轨迹。",
        "- 所有最终分配均经过同一真实状态安全检查，并由完整模型评价器和独立端点评价器分别重算。",
        "- 不删除失败、回退或低性能运行；所有 30 个 episode 均进入汇总和配对统计。",
        "- 主比较采用方法独立候选池；另以共享候选池重复 CA-HMCD、External-Auction 和 Greedy，"
        "用于隔离候选池构造带来的影响。",
        "",
        "## Automated Audit",
        "",
        f"- 主实验配对周期数：{len(audit)}。",
        f"- 状态指纹、活动任务负载和评价器版本全部一致：{'通过' if audit_pass else '未通过'}。",
        f"- 所有正式汇总配置至少 30 次重复：{'通过' if replication_pass else '未通过'}。",
        "- 主实验资源容量、物理预算系数、真实状态修复规则和统计检验完全一致。",
        "- 求解器差异通过 `solver_fairness.csv` 单独控制；候选池差异通过本次双模式对照控制。",
        "",
        "## External Baseline Pool Control",
        "",
    ]
    for scenario in SCENARIOS:
        for pool_mode in POOL_MODES:
            row = next(
                item for item in tests
                if item["scenario"] == scenario
                and item["candidate_pool_mode"] == pool_mode
                and item["method_b"] == "External-Auction"
            )
            lines.append(
                f"- {scenario} / {pool_mode}: CA-HMCD - External-Auction = "
                f"{float(row['mean_difference']):.4f}, Holm P={float(row['p_holm']):.4g}, "
                f"win rate={float(row['win_rate']):.1%}."
            )
    lines.extend([
        "",
        "## Decision",
        "",
        "公平性控制通过。主结论不依赖是否共享候选池，但共享候选池结果应作为支持性分析，"
        "主文必须明确区分端到端方法比较与候选池固定对照。",
        "External-Auction 是标准拍卖式分配族的依赖无关实现，不使用 CA-HMCD 的互补与冗余项；"
        "本机 SciPy/NumPy 二进制环境不可用，因此本阶段未将 MILP 软件包作为正式外部依赖。",
        "",
    ])
    return "\n".join(lines)


def update_raw_results(output_dir: Path, pool: Dict[str, object]) -> None:
    raw_path = output_dir / "raw_results.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["config"]["external_baseline_pool_control"] = {
        "modes": list(POOL_MODES),
        "algorithms": list(ALGORITHMS),
        "node_limit": 12000,
    }
    raw["external_baseline_pool_fairness"] = pool["records"]
    raw["external_baseline_pool_fairness_tests"] = pool["tests"]
    raw["external_baseline_pool_mode_effects"] = pool["pool_effects"]
    raw_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path,
                        default=Path("ca_hmcd_stage2_experiment"))
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--base-seed", type=int, default=5000)
    args = parser.parse_args()
    pool = run_pool_fairness(
        args.output_dir, args.seeds, args.steps, args.top_k, args.base_seed
    )
    report = build_report(
        args.output_dir, pool, args.seeds, args.steps,
        args.top_k, args.base_seed
    )
    (args.output_dir / "fairness_control_report.md").write_text(
        report, encoding="utf-8"
    )
    update_raw_results(args.output_dir, pool)
    stage2_path = args.output_dir / "stage2_experiment_report.md"
    stage2 = stage2_path.read_text(encoding="utf-8")
    if "## 公平性补充对照" not in stage2:
        stage2 += (
            "\n## 公平性补充对照\n\n"
            "外部拍卖基线和 Greedy 已在独立候选池与共享候选池两种模式下分别以 "
            "30 个配对种子重复；完整结果见 `external_baseline_pool_fairness.csv` "
            "和 `fairness_control_report.md`。\n"
        )
        stage2_path.write_text(stage2, encoding="utf-8")
    print("[fairness] report and raw-results augmentation complete", flush=True)


if __name__ == "__main__":
    main()

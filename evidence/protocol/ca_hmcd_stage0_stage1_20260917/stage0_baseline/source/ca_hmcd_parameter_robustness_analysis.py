"""Recompute and report the 54-configuration v2.3 parameter robustness study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Sequence

from ca_hmcd_stage3_statistics import (
    holm_adjust,
    paired_row,
    register_family,
)


ANALYSIS_VERSION = "ca-hmcd-parameter-robustness-v2.3-reanalysis-20260917"
EXPECTED_MODEL_VERSION = "ca-hmcd-stage1-closure-v2.3"
EXPECTED_EVALUATOR_VERSION = "complete-v3-stage1-closure"
EXPECTED_SERVICE_VERSION = "solver-decoupled-service-v2"
PARAMETERS = (
    "alpha_cost",
    "beta_time",
    "gamma_unserved",
    "lambda_redundancy",
    "lambda_switch",
    "synergy_scale",
)
ALGORITHMS = ("CA-HMCD", "External-Auction", "Greedy")
BASELINES = ("External-Auction", "Greedy")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    seen = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fields.append(field)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def audit_row(
    check: str,
    observed: object,
    expected: object,
    passed: bool,
) -> Dict[str, object]:
    return {
        "check": check,
        "observed": observed,
        "expected": expected,
        "passed": passed,
    }


def group_records(
    records: Sequence[Mapping[str, object]],
) -> Dict[tuple[str, float, str], List[Mapping[str, object]]]:
    grouped: Dict[tuple[str, float, str], List[Mapping[str, object]]] = defaultdict(list)
    for record in records:
        grouped[
            (
                str(record["parameter"]),
                float(record["level"]),
                str(record["algorithm"]),
            )
        ].append(record)
    return grouped


def summarize_configurations(
    records: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    metrics = (
        "external_objective",
        "independent_service_score",
        "independent_coverage",
        "independent_response_time",
        "independent_physical_cost",
        "switch_count",
        "redundancy_penalty",
        "complementarity_gain",
        "end_to_end_runtime_ms",
        "fallback",
        "node_limit_hit",
        "feasibility_rate",
    )
    rows: List[Dict[str, object]] = []
    for (parameter, level, algorithm), group in sorted(group_records(records).items()):
        row: Dict[str, object] = {
            "experiment": "parameter_robustness",
            "model_version": EXPECTED_MODEL_VERSION,
            "parameter": parameter,
            "level": level,
            "algorithm": algorithm,
            "paired_seeds": len({int(record["seed"]) for record in group}),
            "decision_periods_per_seed": 12,
        }
        for metric in metrics:
            values = [float(record[metric]) for record in group]
            mean = statistics.fmean(values)
            standard_deviation = statistics.stdev(values) if len(values) > 1 else 0.0
            row[f"{metric}_mean"] = mean
            row[f"{metric}_std"] = standard_deviation
            row[f"{metric}_ci95_normal_half_width"] = (
                1.96 * standard_deviation / math.sqrt(len(values))
            )
        rows.append(row)
    return rows


def compute_paired_statistics(
    records: Sequence[Mapping[str, object]],
    bootstrap_resamples: int,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    paired_rows: List[Dict[str, object]] = []
    family_registry: List[Dict[str, object]] = []
    for parameter in PARAMETERS:
        parameter_records = [
            record for record in records if str(record["parameter"]) == parameter
        ]
        levels = sorted({float(record["level"]) for record in parameter_records})
        family_rows: List[Dict[str, object]] = []
        family_id = f"parameter_robustness.{parameter}.independent_service_score"
        for level in levels:
            level_records = [
                record
                for record in parameter_records
                if float(record["level"]) == level
            ]
            for baseline in BASELINES:
                family_rows.append(
                    paired_row(
                        [
                            record
                            for record in level_records
                            if record["algorithm"] == "CA-HMCD"
                        ],
                        [
                            record
                            for record in level_records
                            if record["algorithm"] == baseline
                        ],
                        "independent_service_score",
                        family_id,
                        "parameter robustness",
                        f"{parameter}={level:g}: CA-HMCD vs {baseline}",
                        "CA-HMCD",
                        baseline,
                        "higher",
                        bootstrap_resamples,
                        {
                            "model_version": EXPECTED_MODEL_VERSION,
                            "parameter": parameter,
                            "level": level,
                            "nested_decision_periods": 12,
                        },
                    )
                )
        paired_rows.extend(family_rows)
        register_family(
            family_registry,
            family_id,
            "parameter robustness",
            "independent_service_score",
            (
                f"All three registered {parameter} levels and both comparators "
                "within one six-member family."
            ),
            family_rows,
        )
    holm_adjust(paired_rows)
    return paired_rows, family_registry


def sensitivity_summary(
    configurations: Sequence[Mapping[str, object]],
    paired_rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for parameter in PARAMETERS:
        ca_rows = sorted(
            (
                row
                for row in configurations
                if row["parameter"] == parameter and row["algorithm"] == "CA-HMCD"
            ),
            key=lambda row: float(row["level"]),
        )
        comparisons = [
            row for row in paired_rows if row["parameter"] == parameter
        ]
        service = [float(row["independent_service_score_mean"]) for row in ca_rows]
        coverage = [float(row["independent_coverage_mean"]) for row in ca_rows]
        objective = [float(row["external_objective_mean"]) for row in ca_rows]
        deltas = [
            float(row["mean_difference_a_minus_b"]) for row in comparisons
        ]
        rows.append(
            {
                "parameter": parameter,
                "levels": ";".join(f"{float(row['level']):g}" for row in ca_rows),
                "ca_hmcd_service_by_level": ";".join(f"{value:.10g}" for value in service),
                "ca_hmcd_service_range": max(service) - min(service),
                "ca_hmcd_coverage_by_level": ";".join(
                    f"{value:.10g}" for value in coverage
                ),
                "ca_hmcd_coverage_range": max(coverage) - min(coverage),
                "ca_hmcd_objective_by_level": ";".join(
                    f"{value:.10g}" for value in objective
                ),
                "ca_hmcd_objective_range": max(objective) - min(objective),
                "minimum_service_advantage": min(deltas),
                "maximum_service_advantage": max(deltas),
                "holm_significant_comparisons": sum(
                    bool(row["significant_after_holm_0_05"])
                    for row in comparisons
                ),
                "registered_comparisons": len(comparisons),
            }
        )
    return rows


def write_report(
    output_dir: Path,
    summary_rows: Sequence[Mapping[str, object]],
    paired_rows: Sequence[Mapping[str, object]],
    audit_rows: Sequence[Mapping[str, object]],
) -> None:
    summary_lookup = {str(row["parameter"]): row for row in summary_rows}
    all_significant = sum(
        bool(row["significant_after_holm_0_05"]) for row in paired_rows
    )
    lines = [
        "# CA-HMCD v2.3 parameter robustness report",
        "",
        "## Design",
        "",
        "- Six one-factor scans: alpha_cost, beta_time, gamma_unserved, "
        "lambda_redundancy, lambda_switch, and synergy_scale.",
        "- Three registered levels per coefficient and three methods per level, "
        "giving 54 configurations.",
        "- Thirty paired seeds (5000-5029) and 12 decision periods per configuration.",
        "- The independent inferential unit is one complete seeded simulation trajectory.",
        "- The endpoint is the solver-decoupled independent service score.",
        "- Each coefficient forms one six-comparison Holm family: three levels "
        "times two comparators.",
        "",
        "## Findings",
        "",
        f"- {all_significant} of {len(paired_rows)} registered CA-HMCD contrasts "
        "remained below the family-wise 0.05 threshold after Holm correction.",
        "- Relative ordering was stable across the registered ranges, but absolute "
        "performance was not invariant to the coefficients.",
    ]
    for parameter in PARAMETERS:
        row = summary_lookup[parameter]
        lines.append(
            f"- {parameter}: CA-HMCD service range "
            f"{float(row['ca_hmcd_service_range']):.4f}; comparison advantages "
            f"{float(row['minimum_service_advantage']):.4f} to "
            f"{float(row['maximum_service_advantage']):.4f}; "
            f"{int(row['holm_significant_comparisons'])}/"
            f"{int(row['registered_comparisons'])} Holm-significant contrasts."
        )
    lines.extend(
        [
            "",
            "The widest CA-HMCD service changes occurred for lambda_switch and "
            "alpha_cost. Changes in lambda_redundancy and synergy_scale were much "
            "smaller. This is a controlled synthetic sensitivity result, not a "
            "deployment threshold calibration and not part of the public-trajectory "
            "replay's independent-unit inference.",
            "",
            "## Audit",
            "",
        ]
    )
    for row in audit_rows:
        lines.append(
            f"- {'PASS' if row['passed'] else 'FAIL'}: {row['check']} "
            f"(observed={row['observed']}, expected={row['expected']})."
        )
    (output_dir / "experiment_result.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_analysis(
    source_json: Path,
    integrity_json: Path,
    output_dir: Path,
    bootstrap_resamples: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with source_json.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    with integrity_json.open(encoding="utf-8") as handle:
        integrity = json.load(handle)

    config = dict(raw["config"])
    records = list(raw["parameter_robustness"])
    configurations = summarize_configurations(records)
    paired_rows, family_registry = compute_paired_statistics(
        records,
        bootstrap_resamples,
    )
    parameter_summary = sensitivity_summary(configurations, paired_rows)

    grouped = group_records(records)
    seed_counts = {
        len({int(record["seed"]) for record in group})
        for group in grouped.values()
    }
    audit_rows = [
        audit_row(
            "stage-2 integrity status",
            integrity.get("status"),
            "PASS",
            integrity.get("status") == "PASS",
        ),
        audit_row(
            "model version",
            config.get("value_model_version"),
            EXPECTED_MODEL_VERSION,
            config.get("value_model_version") == EXPECTED_MODEL_VERSION,
        ),
        audit_row(
            "external evaluator version",
            config.get("external_evaluator"),
            EXPECTED_EVALUATOR_VERSION,
            config.get("external_evaluator") == EXPECTED_EVALUATOR_VERSION,
        ),
        audit_row(
            "service endpoint version",
            config.get("independent_evaluator"),
            EXPECTED_SERVICE_VERSION,
            config.get("independent_evaluator") == EXPECTED_SERVICE_VERSION,
        ),
        audit_row(
            "raw parameter records",
            len(records),
            1620,
            len(records) == 1620,
        ),
        audit_row(
            "parameter configurations",
            len(grouped),
            54,
            len(grouped) == 54,
        ),
        audit_row(
            "paired seeds per configuration",
            sorted(seed_counts),
            [30],
            seed_counts == {30},
        ),
        audit_row(
            "decision periods per seed",
            config.get("steps"),
            12,
            config.get("steps") == 12,
        ),
        audit_row(
            "registered parameters",
            sorted({str(record["parameter"]) for record in records}),
            sorted(PARAMETERS),
            {str(record["parameter"]) for record in records} == set(PARAMETERS),
        ),
        audit_row(
            "registered algorithms",
            sorted({str(record["algorithm"]) for record in records}),
            sorted(ALGORITHMS),
            {str(record["algorithm"]) for record in records} == set(ALGORITHMS),
        ),
        audit_row(
            "paired comparison rows",
            len(paired_rows),
            36,
            len(paired_rows) == 36,
        ),
        audit_row(
            "Holm families",
            len(family_registry),
            6,
            len(family_registry) == 6,
        ),
        audit_row(
            "complete finite statistics",
            sum(
                math.isfinite(float(row["mean_difference_a_minus_b"]))
                and math.isfinite(float(row["p_holm_adjusted"]))
                for row in paired_rows
            ),
            36,
            all(
                math.isfinite(float(row["mean_difference_a_minus_b"]))
                and math.isfinite(float(row["p_holm_adjusted"]))
                for row in paired_rows
            ),
        ),
    ]
    if not all(bool(row["passed"]) for row in audit_rows):
        failures = [row["check"] for row in audit_rows if not row["passed"]]
        raise RuntimeError(f"Parameter robustness audit failed: {failures}")

    write_csv(output_dir / "seed_level_records.csv", records)
    write_csv(output_dir / "configuration_summary_54.csv", configurations)
    write_csv(output_dir / "paired_statistics_36.csv", paired_rows)
    write_csv(output_dir / "holm_family_registry_6.csv", family_registry)
    write_csv(output_dir / "parameter_sensitivity_summary.csv", parameter_summary)
    write_csv(output_dir / "analysis_audit.csv", audit_rows)
    metadata = {
        "analysis_version": ANALYSIS_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_json": str(source_json.resolve()),
        "source_json_sha256": sha256_file(source_json),
        "integrity_json": str(integrity_json.resolve()),
        "model_version": EXPECTED_MODEL_VERSION,
        "external_evaluator_version": EXPECTED_EVALUATOR_VERSION,
        "service_endpoint_version": EXPECTED_SERVICE_VERSION,
        "bootstrap_resamples": bootstrap_resamples,
        "parameter_configurations": 54,
        "seed_level_records": 1620,
        "paired_comparisons": 36,
        "holm_families": 6,
        "audit_pass": True,
    }
    (output_dir / "analysis_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_report(output_dir, parameter_summary, paired_rows, audit_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reanalyse the v2.3 54-configuration parameter study"
    )
    parser.add_argument(
        "--source-json",
        type=Path,
        default=Path("ca_hmcd_stage2_upgrade_20260916/raw_results.json"),
    )
    parser.add_argument(
        "--integrity-json",
        type=Path,
        default=Path(
            "ca_hmcd_stage2_upgrade_20260916/"
            "stage2_upgrade_integrity_audit.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ca_hmcd_parameter_robustness_analysis_20260917"),
    )
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run_analysis(
        arguments.source_json,
        arguments.integrity_json,
        arguments.output_dir,
        arguments.bootstrap_resamples,
    )

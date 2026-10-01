from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ca_hmcd_stage6_unified_statistics_20260918"

BASE = (
    ROOT
    / "CA-HMCD_Reproducibility_Package_20260917"
    / "results"
    / "statistical_registry"
    / "complete_statistical_registry_516.csv"
)
STAGE3 = ROOT / "ca_hmcd_stage3_boundary_analysis_20260917" / "stage3_paired_statistics.csv"
STAGE4 = (
    ROOT
    / "ca_hmcd_stage4_value_engineering_analysis_20260918"
    / "stage4_paired_comparisons.csv"
)
STAGE5 = (
    ROOT
    / "ca_hmcd_stage5_applicability_analysis_20260918"
    / "stage5_paired_comparisons.csv"
)

REGISTRY = OUT / "complete_statistical_registry_1592.csv"
FAMILIES = OUT / "complete_holm_family_registry_316.csv"
CLAIMS = OUT / "claim_evidence_matrix.csv"
SUMMARY = OUT / "evidence_layer_summary.csv"
REPORT = OUT / "stage6_unified_statistics_report.md"
MANIFEST = OUT / "stage6_analysis_manifest.json"
SUPPLEMENT = (
    ROOT / "CA-HMCD_Supplementary_Table_S11_Unified_Statistical_Registry_20260918.csv"
)

STANDARD_COLUMNS = [
    "record_id",
    "evidence_layer",
    "evidence_block",
    "source_file",
    "source_sha256",
    "source_row",
    "family_id",
    "comparison",
    "metric",
    "scenario",
    "stratum",
    "method_a",
    "method_b",
    "favorable_direction",
    "n_independent_units",
    "nested_repeats",
    "mean_a",
    "mean_b",
    "mean_difference_a_minus_b",
    "ci95_low",
    "ci95_high",
    "ci_method",
    "p_unadjusted",
    "p_holm_adjusted",
    "cohen_dz",
    "rank_biserial",
    "win_rate_favorable",
    "win_rate_interval_low",
    "win_rate_interval_high",
    "common_task_support",
    "holm_family_size",
    "significant_after_holm_0_05",
    "verification_status",
    "interpretation_limit",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text(row: pd.Series, *names: str) -> str:
    for name in names:
        if name in row and pd.notna(row[name]):
            return str(row[name])
    return ""


def number(row: pd.Series, *names: str):
    value = text(row, *names)
    if value == "":
        return ""
    try:
        return float(value)
    except ValueError:
        return value


def boolean(row: pd.Series, *names: str):
    value = text(row, *names).strip().lower()
    if value == "":
        return ""
    return value in {"true", "1", "yes"}


def normalize_base(path: Path) -> list[dict]:
    source = pd.read_csv(path, low_memory=False)
    records = []
    for index, row in source.iterrows():
        records.append(
            {
                "record_id": text(row, "registry_record_id"),
                "evidence_layer": "public-data replay and registered supplements",
                "evidence_block": text(row, "evidence_block"),
                "source_file": text(row, "source_file"),
                "source_sha256": text(row, "source_file_sha256"),
                "source_row": index + 2,
                "family_id": text(row, "registry_family_id"),
                "comparison": text(row, "comparison_normalized", "source__comparison"),
                "metric": text(row, "metric_normalized", "source__metric"),
                "scenario": text(
                    row,
                    "source__dataset",
                    "source__parameter",
                    "source__section",
                ),
                "stratum": text(
                    row,
                    "source__stratum_label",
                    "source__level",
                    "source__task_load",
                ),
                "method_a": text(row, "method_a_normalized", "source__method_a"),
                "method_b": text(row, "method_b_normalized", "source__method_b"),
                "favorable_direction": text(
                    row, "source__favorable_direction_for_method_a"
                ),
                "n_independent_units": number(
                    row,
                    "n_independent_units_normalized",
                    "source__n_independent_cluster_pairs",
                    "source__n_independent_seed_pairs",
                ),
                "nested_repeats": number(
                    row,
                    "nested_repeats_normalized",
                    "source__nested_seeds_per_cluster",
                    "source__nested_decision_periods",
                ),
                "mean_a": number(row, "source__method_a_mean"),
                "mean_b": number(row, "source__method_b_mean"),
                "mean_difference_a_minus_b": number(
                    row,
                    "mean_difference_normalized",
                    "source__mean_difference_a_minus_b",
                ),
                "ci95_low": number(
                    row, "ci95_low_normalized", "source__mean_difference_ci95_low"
                ),
                "ci95_high": number(
                    row, "ci95_high_normalized", "source__mean_difference_ci95_high"
                ),
                "ci_method": text(row, "source__ci_method"),
                "p_unadjusted": number(
                    row, "p_unadjusted_normalized", "source__p_unadjusted"
                ),
                "p_holm_adjusted": number(
                    row, "p_holm_adjusted_normalized", "source__p_holm_adjusted"
                ),
                "cohen_dz": number(
                    row, "cohen_dz_normalized", "source__cohen_dz"
                ),
                "rank_biserial": number(
                    row, "rank_biserial_normalized", "source__rank_biserial"
                ),
                "win_rate_favorable": number(
                    row,
                    "win_rate_favorable_normalized",
                    "source__win_rate_favorable_to_method_a",
                    "source__win_rate",
                ),
                "win_rate_interval_low": "",
                "win_rate_interval_high": "",
                "common_task_support": number(row, "source__common_task_periods_total"),
                "holm_family_size": number(row, "source__holm_family_size"),
                "significant_after_holm_0_05": boolean(
                    row,
                    "significant_after_holm_0_05_normalized",
                    "source__significant_after_holm_0_05",
                ),
                "verification_status": "VERIFIED",
                "interpretation_limit": (
                    "Inference remains within the evidence block and registered "
                    "workload; timing is implementation- and workstation-specific."
                ),
            }
        )
    return records


def normalize_stage3(path: Path) -> list[dict]:
    source = pd.read_csv(path)
    source_hash = sha256(path)
    records = []
    for index, row in source.iterrows():
        records.append(
            {
                "record_id": f"stage3_boundary::{index + 1:04d}",
                "evidence_layer": "static scale-deadline boundary",
                "evidence_block": "stage3_exact_fixedk_boundary",
                "source_file": str(path.relative_to(ROOT)),
                "source_sha256": source_hash,
                "source_row": index + 2,
                "family_id": text(row, "family_id"),
                "comparison": text(row, "comparison"),
                "metric": text(row, "metric"),
                "scenario": text(row, "scale_cell"),
                "stratum": f"{text(row, 'registered_budget_ms')} ms",
                "method_a": text(row, "method_a"),
                "method_b": text(row, "method_b"),
                "favorable_direction": text(
                    row, "favorable_direction_for_method_a"
                ),
                "n_independent_units": number(row, "n_independent_seed_pairs"),
                "nested_repeats": "",
                "mean_a": number(row, "method_a_mean"),
                "mean_b": number(row, "method_b_mean"),
                "mean_difference_a_minus_b": number(
                    row, "mean_difference_a_minus_b"
                ),
                "ci95_low": number(row, "mean_difference_ci95_low"),
                "ci95_high": number(row, "mean_difference_ci95_high"),
                "ci_method": text(row, "ci_method"),
                "p_unadjusted": number(row, "p_unadjusted"),
                "p_holm_adjusted": number(row, "p_holm_adjusted"),
                "cohen_dz": number(row, "cohen_dz"),
                "rank_biserial": number(row, "rank_biserial"),
                "win_rate_favorable": number(
                    row, "win_rate_favorable_to_method_a"
                ),
                "win_rate_interval_low": "",
                "win_rate_interval_high": "",
                "common_task_support": "",
                "holm_family_size": number(row, "holm_family_size"),
                "significant_after_holm_0_05": boolean(
                    row, "significant_after_holm_0_05"
                ),
                "verification_status": "VERIFIED",
                "interpretation_limit": (
                    "Static scale-deadline experiment; Gate B failed in all 64 "
                    "registered cells and does not establish a real-time advantage."
                ),
            }
        )
    return records


def normalize_stage4(path: Path) -> list[dict]:
    source = pd.read_csv(path)
    source_hash = sha256(path)
    records = []
    for index, row in source.iterrows():
        records.append(
            {
                "record_id": f"stage4_mechanism::{index + 1:04d}",
                "evidence_layer": "controlled mechanism and disturbance validation",
                "evidence_block": text(row, "section"),
                "source_file": str(path.relative_to(ROOT)),
                "source_sha256": source_hash,
                "source_row": index + 2,
                "family_id": f"stage4::{text(row, 'family_id')}",
                "comparison": text(row, "comparison"),
                "metric": text(row, "metric"),
                "scenario": text(row, "scenario"),
                "stratum": text(row, "factor_level"),
                "method_a": text(row, "method_a"),
                "method_b": text(row, "method_b"),
                "favorable_direction": text(row, "favorable_direction"),
                "n_independent_units": number(row, "pairs"),
                "nested_repeats": 12,
                "mean_a": number(row, "mean_a"),
                "mean_b": number(row, "mean_b"),
                "mean_difference_a_minus_b": number(
                    row, "mean_difference_a_minus_b"
                ),
                "ci95_low": number(row, "ci95_bca_low"),
                "ci95_high": number(row, "ci95_bca_high"),
                "ci_method": "paired BCa bootstrap, 10000 resamples",
                "p_unadjusted": number(row, "p_unadjusted"),
                "p_holm_adjusted": number(row, "p_holm_adjusted"),
                "cohen_dz": number(row, "cohen_dz"),
                "rank_biserial": number(row, "rank_biserial"),
                "win_rate_favorable": number(row, "win_rate_favorable"),
                "win_rate_interval_low": number(row, "win_rate_wilson_low"),
                "win_rate_interval_high": number(row, "win_rate_wilson_high"),
                "common_task_support": number(row, "mean_common_task_periods"),
                "holm_family_size": number(row, "holm_family_size"),
                "significant_after_holm_0_05": boolean(
                    row, "significant_after_holm_0_05"
                ),
                "verification_status": "ANALYZED",
                "interpretation_limit": (
                    "Controlled engineering-shaped simulation; mechanisms are "
                    "interpreted separately from field-deployment performance."
                ),
            }
        )
    return records


def normalize_stage5(path: Path) -> list[dict]:
    source = pd.read_csv(path)
    source_hash = sha256(path)
    records = []
    for index, row in source.iterrows():
        records.append(
            {
                "record_id": f"stage5_applicability::{index + 1:04d}",
                "evidence_layer": "engineering-scenario solver applicability",
                "evidence_block": f"stage5_{text(row, 'category')}",
                "source_file": str(path.relative_to(ROOT)),
                "source_sha256": source_hash,
                "source_row": index + 2,
                "family_id": text(row, "family_id"),
                "comparison": (
                    f"{text(row, 'method_a_label')} vs "
                    f"{text(row, 'method_b_label')}"
                ),
                "metric": text(row, "metric"),
                "scenario": text(row, "scenario"),
                "stratum": text(row, "category"),
                "method_a": text(row, "method_a_label", "method_a"),
                "method_b": text(row, "method_b_label", "method_b"),
                "favorable_direction": text(row, "favorable_direction"),
                "n_independent_units": number(row, "pairs"),
                "nested_repeats": 12,
                "mean_a": number(row, "mean_a"),
                "mean_b": number(row, "mean_b"),
                "mean_difference_a_minus_b": number(
                    row, "mean_difference_a_minus_b"
                ),
                "ci95_low": number(row, "ci95_bca_low"),
                "ci95_high": number(row, "ci95_bca_high"),
                "ci_method": "paired BCa bootstrap, 10000 resamples",
                "p_unadjusted": number(row, "p_unadjusted"),
                "p_holm_adjusted": number(row, "p_holm_adjusted"),
                "cohen_dz": number(row, "cohen_dz"),
                "rank_biserial": number(row, "rank_biserial"),
                "win_rate_favorable": number(row, "win_rate_favorable"),
                "win_rate_interval_low": number(row, "win_rate_wilson_low"),
                "win_rate_interval_high": number(row, "win_rate_wilson_high"),
                "common_task_support": number(row, "common_task_periods_mean"),
                "holm_family_size": number(row, "holm_family_size"),
                "significant_after_holm_0_05": boolean(
                    row, "significant_after_holm_0_05"
                ),
                "verification_status": "ANALYZED",
                "interpretation_limit": (
                    "Scenario- and deadline-specific controlled simulation; "
                    "recommendations are descriptive and not hard-real-time certification."
                ),
            }
        )
    return records


def claim_rows() -> list[dict]:
    return [
        {
            "claim_id": "C6.1",
            "claim": "CA-HMCD is an interpretable rolling coalition-allocation framework, not a universally superior solver.",
            "decision": "RETAIN",
            "primary_evidence": "problem formulation, algorithm trace, public replay, Stages 3-5",
            "allowed_wording": "The contribution is an auditable allocation and safety-control framework with measured operating boundaries.",
        },
        {
            "claim_id": "C6.2",
            "claim": "Fixed-K candidate retention provides a validated real-time advantage.",
            "decision": "REJECT",
            "primary_evidence": "Stage 3 Gate B: 0/64 cells passed",
            "allowed_wording": "The evaluated implementation did not establish a real-time advantage over exact HiGHS.",
        },
        {
            "claim_id": "C6.3",
            "claim": "Conservative subset dominance is theoretically lossless under its conditions.",
            "decision": "RETAIN_WITH_SCOPE",
            "primary_evidence": "Proposition 1 and formal no-pruning replay",
            "allowed_wording": "The rule is lossless under the stated subset conditions, but removed no candidates in the formal replay.",
        },
        {
            "claim_id": "C6.4",
            "claim": "Complementarity improves all outcomes.",
            "decision": "REJECT",
            "primary_evidence": "Stage 4 complementarity sweep",
            "allowed_wording": "Complementarity produced a monotone service-level gain in the registered public-event sweep, while the complete-objective effect remained nonsignificant.",
        },
        {
            "claim_id": "C6.5",
            "claim": "The stability term reduces switching.",
            "decision": "RETAIN",
            "primary_evidence": "Stage 4 volatility sweep",
            "allowed_wording": "Stability reduced switching at every nonzero volatility level, with an immediate service trade-off.",
        },
        {
            "claim_id": "C6.6",
            "claim": "The redundancy penalty has demonstrated operational value.",
            "decision": "REJECT",
            "primary_evidence": "Stage 4 redundancy stress and 54-configuration sensitivity",
            "allowed_wording": "The redundancy term remains a modeling safeguard; none of the three registered structural diagnostics was supported.",
        },
        {
            "claim_id": "C6.7",
            "claim": "True-state repair restores final feasibility under registered perception disturbance.",
            "decision": "RETAIN_WITH_SCOPE",
            "primary_evidence": "Stage 4 repair disturbance",
            "allowed_wording": "Repair restored feasibility in the controlled disturbance experiment, at a measurable immediate service cost.",
        },
        {
            "claim_id": "C6.8",
            "claim": "One method is best across all engineering scenarios and deadlines.",
            "decision": "REJECT",
            "primary_evidence": "Stage 5 solver applicability",
            "allowed_wording": "Method selection is scenario-, objective-, and deadline-specific; no universal winner was observed.",
        },
        {
            "claim_id": "C6.9",
            "claim": "The evidence establishes field deployment performance.",
            "decision": "REJECT",
            "primary_evidence": "all evidence layers",
            "allowed_wording": "The study is a controlled simulation with public-data-informed task motion and observation patterns.",
        },
    ]


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = (
        normalize_base(BASE)
        + normalize_stage3(STAGE3)
        + normalize_stage4(STAGE4)
        + normalize_stage5(STAGE5)
    )
    registry = pd.DataFrame(records, columns=STANDARD_COLUMNS)
    registry.to_csv(REGISTRY, index=False, encoding="utf-8-sig")
    registry.to_csv(SUPPLEMENT, index=False, encoding="utf-8-sig")

    family_rows = []
    for family_id, group in registry.groupby("family_id", sort=True, dropna=False):
        declared = {
            int(float(value))
            for value in group["holm_family_size"]
            if str(value).strip() not in {"", "nan"}
        }
        family_rows.append(
            {
                "family_id": family_id,
                "evidence_layer": "; ".join(sorted(set(group["evidence_layer"]))),
                "evidence_block": "; ".join(sorted(set(group["evidence_block"]))),
                "metric": "; ".join(sorted(set(group["metric"]))),
                "scenario": "; ".join(sorted(set(group["scenario"]))),
                "registered_comparisons": len(group),
                "declared_family_sizes": ";".join(map(str, sorted(declared))),
                "significant_comparisons": int(
                    pd.to_numeric(
                        group["significant_after_holm_0_05"], errors="coerce"
                    ).fillna(0).astype(bool).sum()
                ),
            }
        )
    families = pd.DataFrame(family_rows)
    families.to_csv(FAMILIES, index=False, encoding="utf-8-sig")

    summary = (
        registry.groupby(["evidence_layer", "evidence_block"], dropna=False)
        .agg(
            comparisons=("record_id", "count"),
            holm_families=("family_id", "nunique"),
            significant_comparisons=("significant_after_holm_0_05", "sum"),
        )
        .reset_index()
    )
    summary.to_csv(SUMMARY, index=False, encoding="utf-8-sig")
    pd.DataFrame(claim_rows()).to_csv(CLAIMS, index=False, encoding="utf-8-sig")

    checks = {
        "registry_rows_1592": bool(len(registry) == 1592),
        "holm_families_316": bool(registry["family_id"].nunique() == 316),
        "record_ids_unique": bool(registry["record_id"].is_unique),
        "all_family_ids_present": bool(
            registry["family_id"].astype(str).str.len().gt(0).all()
        ),
        "all_metrics_present": bool(
            registry["metric"].astype(str).str.len().gt(0).all()
        ),
        "holm_p_in_unit_interval": bool(pd.to_numeric(
            registry["p_holm_adjusted"], errors="coerce"
        ).dropna().between(0, 1).all()),
        "unadjusted_p_in_unit_interval": bool(pd.to_numeric(
            registry["p_unadjusted"], errors="coerce"
        ).dropna().between(0, 1).all()),
        "ci_order_valid": bool((
            pd.to_numeric(registry["ci95_low"], errors="coerce")
            <= pd.to_numeric(registry["ci95_high"], errors="coerce")
        ).fillna(True).all()),
        "supplement_written": bool(SUPPLEMENT.exists()),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise RuntimeError(f"Stage 6 registry validation failed: {failed}")

    manifest = {
        "date": "2026-09-18",
        "verification_status": "ANALYZED",
        "registry_rows": len(registry),
        "holm_families": int(registry["family_id"].nunique()),
        "sources": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for path in (BASE, STAGE3, STAGE4, STAGE5)
        ],
        "outputs": [
            str(path.relative_to(ROOT))
            for path in (REGISTRY, FAMILIES, CLAIMS, SUMMARY, SUPPLEMENT)
        ],
        "checks": checks,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    layer_table = "\n".join(
        f"| {row.evidence_layer} | {int(row.comparisons)} | {int(row.holm_families)} |"
        for row in (
            registry.groupby("evidence_layer")
            .agg(comparisons=("record_id", "count"), holm_families=("family_id", "nunique"))
            .reset_index()
            .itertuples()
        )
    )
    REPORT.write_text(
        f"""# Stage 6 unified statistical analysis

Date: 2026-09-18

## Completion

- Unified registry: **{len(registry):,} paired comparisons**.
- Prespecified Holm families: **{registry['family_id'].nunique():,}**.
- Every principal contrast retains the mean paired difference, 95% interval,
  unadjusted P value, Holm-adjusted P value, Cohen's dz, rank-biserial
  correlation, and favorable win rate when available.
- Bounded outcomes retain Wilson intervals in their original source tables.
- Common-task response time is limited to jointly served task-periods.

## Evidence layers

| Evidence layer | Comparisons | Holm families |
|---|---:|---:|
{layer_table}

## Statistical interpretation

The four evidence layers are registered and reported separately. Public-data
replay addresses task-side transportability, controlled mechanism experiments
address causal module contrasts within simulation, the static boundary study
addresses candidate-construction and deadline behavior, and the engineering
scenario study addresses solver/backend applicability. Results are not pooled
across layers.

The unified analysis supports stability and controlled true-state repair,
supports context-dependent complementarity at the service endpoint, and does
not support an operational redundancy effect. Stage 3 remains a failed real-time
gate (0/64 passing cells). Stage 5 finds no universal method: CA-HMCD-FixedK,
CA-HMCD-Exact, the genetic algorithm, and External-Auction are selected under
different scenario, quality, and deadline criteria.

## Reproducibility

The complete normalized registry is `{REGISTRY.name}`. The manuscript-facing
copy is `{SUPPLEMENT.name}`. Source hashes and validation checks are recorded in
`{MANIFEST.name}`.
""",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "registry_rows": len(registry),
                "holm_families": int(registry["family_id"].nunique()),
                "output": str(REGISTRY),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    build()

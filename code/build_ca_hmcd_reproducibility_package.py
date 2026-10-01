from __future__ import annotations

import csv
import hashlib
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "CA-HMCD_Reproducibility_Package_20260917"
PACKAGE_DIR = (ROOT / PACKAGE_NAME).resolve()
ZIP_PATH = (ROOT / f"{PACKAGE_NAME}.zip").resolve()

STAT_BLOCKS = (
    {
        "block": "primary_public_replay",
        "comparison_file": ROOT
        / "ca_hmcd_stage5_statistics_20260916"
        / "all_cluster_paired_statistics.csv",
        "family_file": ROOT
        / "ca_hmcd_stage5_statistics_20260916"
        / "holm_family_registry.csv",
        "expected_comparisons": 300,
        "expected_families": 60,
        "independent_unit": (
            "public trajectory or preregistered composed workload; "
            "30 stochastic seeds nested within each unit"
        ),
    },
    {
        "block": "no_pruning_control",
        "comparison_file": ROOT
        / "ca_hmcd_no_pruning_analysis_20260917"
        / "paired_statistics.csv",
        "family_file": ROOT
        / "ca_hmcd_no_pruning_analysis_20260917"
        / "holm_family_registry.csv",
        "expected_comparisons": 60,
        "expected_families": 15,
        "independent_unit": (
            "public trajectory or preregistered composed workload; "
            "30 stochastic seeds nested within each unit"
        ),
    },
    {
        "block": "external_full_candidate_baselines",
        "comparison_file": ROOT
        / "ca_hmcd_external_baselines_analysis_20260917"
        / "paired_statistics.csv",
        "family_file": ROOT
        / "ca_hmcd_external_baselines_analysis_20260917"
        / "holm_family_registry.csv",
        "expected_comparisons": 120,
        "expected_families": 60,
        "independent_unit": (
            "public trajectory or preregistered composed workload; "
            "30 stochastic seeds nested within each unit"
        ),
    },
    {
        "block": "parameter_robustness",
        "comparison_file": ROOT
        / "ca_hmcd_parameter_robustness_analysis_20260917"
        / "paired_statistics_36.csv",
        "family_file": ROOT
        / "ca_hmcd_parameter_robustness_analysis_20260917"
        / "holm_family_registry_6.csv",
        "expected_comparisons": 36,
        "expected_families": 6,
        "independent_unit": "one complete seeded 12-period simulation trajectory",
    },
)


def ensure_within_root(path: Path) -> None:
    path.resolve().relative_to(ROOT)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def normalize_comparison(
    block: Mapping[str, object],
    source_row: Mapping[str, str],
    row_index: int,
) -> Dict[str, object]:
    original_family = str(source_row.get("family_id", ""))
    registry_family = f"{block['block']}::{original_family}"
    n_independent = (
        source_row.get("n_independent_cluster_pairs")
        or source_row.get("n_independent_seed_pairs")
        or ""
    )
    nested_repeats = (
        source_row.get("nested_seeds_per_cluster")
        or source_row.get("nested_decision_periods")
        or ""
    )
    win_rate = (
        source_row.get("win_rate_favorable_to_method_a")
        or source_row.get("win_rate")
        or ""
    )
    normalized: Dict[str, object] = {
        "registry_record_id": f"{block['block']}::{row_index:04d}",
        "evidence_block": block["block"],
        "registry_family_id": registry_family,
        "original_family_id": original_family,
        "source_file": str(Path(block["comparison_file"]).relative_to(ROOT)),
        "source_file_sha256": sha256_file(Path(block["comparison_file"])),
        "independent_unit_normalized": block["independent_unit"],
        "n_independent_units_normalized": n_independent,
        "nested_repeats_normalized": nested_repeats,
        "comparison_normalized": source_row.get("comparison", ""),
        "metric_normalized": source_row.get("metric", ""),
        "method_a_normalized": source_row.get("method_a", ""),
        "method_b_normalized": source_row.get("method_b", ""),
        "mean_difference_normalized": source_row.get(
            "mean_difference_a_minus_b", ""
        ),
        "ci95_low_normalized": source_row.get("mean_difference_ci95_low", ""),
        "ci95_high_normalized": source_row.get("mean_difference_ci95_high", ""),
        "p_unadjusted_normalized": source_row.get("p_unadjusted", ""),
        "p_holm_adjusted_normalized": source_row.get("p_holm_adjusted", ""),
        "cohen_dz_normalized": source_row.get("cohen_dz", ""),
        "rank_biserial_normalized": source_row.get("rank_biserial", ""),
        "win_rate_favorable_normalized": win_rate,
        "significant_after_holm_0_05_normalized": source_row.get(
            "significant_after_holm_0_05", ""
        ),
    }
    normalized.update({f"source__{key}": value for key, value in source_row.items()})
    return normalized


def normalize_family(
    block: Mapping[str, object],
    source_row: Mapping[str, str],
    row_index: int,
) -> Dict[str, object]:
    original_family = str(source_row.get("family_id", ""))
    members = (
        source_row.get("members")
        or source_row.get("comparisons")
        or source_row.get("metrics")
        or ""
    )
    correction = (
        source_row.get("multiplicity_control")
        or source_row.get("correction")
        or ""
    )
    normalized: Dict[str, object] = {
        "registry_family_record_id": f"{block['block']}::family::{row_index:03d}",
        "evidence_block": block["block"],
        "registry_family_id": f"{block['block']}::{original_family}",
        "original_family_id": original_family,
        "source_file": str(Path(block["family_file"]).relative_to(ROOT)),
        "source_file_sha256": sha256_file(Path(block["family_file"])),
        "independent_unit_normalized": (
            source_row.get("independent_unit") or block["independent_unit"]
        ),
        "family_size_normalized": source_row.get("number_of_comparisons", ""),
        "family_members_normalized": members,
        "multiplicity_control_normalized": correction,
    }
    normalized.update({f"source__{key}": value for key, value in source_row.items()})
    return normalized


def build_statistical_registry(output_dir: Path) -> Dict[str, object]:
    comparison_rows: List[Dict[str, object]] = []
    family_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    block_summary: List[Dict[str, object]] = []

    for block in STAT_BLOCKS:
        source_comparisons = read_csv(Path(block["comparison_file"]))
        source_families = read_csv(Path(block["family_file"]))
        comparison_rows.extend(
            normalize_comparison(block, row, index)
            for index, row in enumerate(source_comparisons, start=1)
        )
        family_rows.extend(
            normalize_family(block, row, index)
            for index, row in enumerate(source_families, start=1)
        )
        comparisons_pass = (
            len(source_comparisons) == block["expected_comparisons"]
        )
        families_pass = len(source_families) == block["expected_families"]
        audit_rows.extend(
            [
                {
                    "check": f"{block['block']}: comparison count",
                    "observed": len(source_comparisons),
                    "expected": block["expected_comparisons"],
                    "passed": comparisons_pass,
                },
                {
                    "check": f"{block['block']}: Holm family count",
                    "observed": len(source_families),
                    "expected": block["expected_families"],
                    "passed": families_pass,
                },
            ]
        )
        block_summary.append(
            {
                "evidence_block": block["block"],
                "comparisons": len(source_comparisons),
                "holm_families": len(source_families),
                "independent_unit": block["independent_unit"],
            }
        )

    family_size_sum = sum(
        int(row["family_size_normalized"]) for row in family_rows
    )
    complete_p_values = sum(
        bool(row["p_unadjusted_normalized"])
        and bool(row["p_holm_adjusted_normalized"])
        for row in comparison_rows
    )
    unique_comparison_ids = len(
        {row["registry_record_id"] for row in comparison_rows}
    )
    unique_family_ids = len(
        {row["registry_family_id"] for row in family_rows}
    )
    audit_rows.extend(
        [
            {
                "check": "complete registry comparison count",
                "observed": len(comparison_rows),
                "expected": 516,
                "passed": len(comparison_rows) == 516,
            },
            {
                "check": "complete registry Holm family count",
                "observed": len(family_rows),
                "expected": 141,
                "passed": len(family_rows) == 141,
            },
            {
                "check": "sum of declared family sizes",
                "observed": family_size_sum,
                "expected": 516,
                "passed": family_size_sum == 516,
            },
            {
                "check": "comparison IDs are unique",
                "observed": unique_comparison_ids,
                "expected": 516,
                "passed": unique_comparison_ids == 516,
            },
            {
                "check": "family IDs are unique",
                "observed": unique_family_ids,
                "expected": 141,
                "passed": unique_family_ids == 141,
            },
            {
                "check": "unadjusted and Holm P values are complete",
                "observed": complete_p_values,
                "expected": 516,
                "passed": complete_p_values == 516,
            },
        ]
    )
    if not all(bool(row["passed"]) for row in audit_rows):
        failed = [row["check"] for row in audit_rows if not row["passed"]]
        raise RuntimeError(f"Statistical registry audit failed: {failed}")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "complete_statistical_registry_516.csv", comparison_rows)
    write_csv(output_dir / "complete_holm_family_registry_141.csv", family_rows)
    write_csv(output_dir / "statistical_registry_audit.csv", audit_rows)
    write_csv(output_dir / "statistical_registry_block_summary.csv", block_summary)
    summary = {
        "registry_version": "ca-hmcd-complete-statistical-registry-20260917",
        "created_date": date.today().isoformat(),
        "comparisons": len(comparison_rows),
        "holm_families": len(family_rows),
        "evidence_blocks": block_summary,
        "audit_pass": True,
        "reporting_fields": [
            "mean paired difference",
            "95% confidence interval",
            "unadjusted P value",
            "Holm-adjusted P value",
            "paired Cohen's dz",
            "rank-biserial correlation",
            "favorable-direction win rate",
        ],
    }
    (output_dir / "statistical_registry_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_lines = [
        "# Complete statistical registry",
        "",
        "- Comparisons: 516",
        "- Prespecified Holm families: 141",
        "- Evidence blocks: primary public replay, no-pruning control, "
        "external full-candidate baselines, and parameter robustness",
        "- Audit status: PASS",
        "",
        "The registry preserves every source column and adds normalized fields for "
        "the evidence block, independent unit, effect estimate, confidence interval, "
        "unadjusted P value, Holm-adjusted P value, effect sizes, and win rate.",
        "",
        "The 54-configuration parameter study contributes 36 paired comparisons in "
        "six Holm families. The public replay, no-pruning, and external-baseline "
        "blocks use the public trajectory or preregistered workload as the "
        "independent unit; the parameter study uses a complete seeded trajectory.",
    ]
    (output_dir / "README.md").write_text(
        "\n".join(report_lines) + "\n",
        encoding="utf-8",
    )
    return summary


def copy_file(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_selected_directory(
    source_dir: Path,
    destination_dir: Path,
    excluded_names: Iterable[str] = (),
    excluded_parts: Iterable[str] = (),
) -> None:
    excluded = set(excluded_names)
    excluded_path_parts = set(excluded_parts)
    for source in source_dir.rglob("*"):
        relative = source.relative_to(source_dir)
        if (
            not source.is_file()
            or source.name in excluded
            or any(part in excluded_path_parts for part in relative.parts)
        ):
            continue
        destination = destination_dir / relative
        copy_file(source, destination)


def write_package_documents(package_dir: Path) -> None:
    readme = """# CA-HMCD reproducibility package

Version: 2026-09-17 release candidate

## Scope

This package supports the CA-HMCD controlled low-altitude-safety simulation
study. It contains the frozen public-data replay protocol, derived replay data,
episode-level formal outputs, the registered 54-configuration parameter
sensitivity source, figure source data, analysis scripts, tests, the revised
manuscript, and a complete statistical registry.

The statistical registry contains 516 comparisons organized into 141
prespecified Holm families. The parameter-robustness component contains 54
parameter-method configurations, 1,620 seeded trajectories, 36 paired
comparisons, and six Holm families.

## Evidence boundary

UZH-FPV and Anti-UAV410 supplied task-side motion or observation patterns.
Response resources, protected zones, workload composition, risk mapping,
response windows, costs, and outcomes remained simulated. The original public
datasets are not redistributed; obtain them from their official sources under
their original licences. This package includes source identifiers, hashes,
quality-control registries, split assignments, and derived replay files.

## Start here

- `REPRODUCE.md`: environment and exact commands.
- `MANIFEST.csv`: file-level SHA-256 hashes, sizes, and roles.
- `results/statistical_registry/`: all 516 statistical comparisons and 141
  Holm family definitions.
- `results/parameter_robustness/`: all 54 configuration summaries, seed-level
  records, inferential results, and audit files.
- `AUTHOR_INPUT_NEEDED.md`: metadata and legal fields required before public
  DOI release.

## Integrity

Every file except the self-referential manifest/checksum files is listed in
`MANIFEST.csv` and `checksums.sha256`. The registry and manuscript builders
perform internal count and completeness audits.
"""
    reproduce = """# Reproduction instructions

All commands are run from the package root. Python 3.12 is recommended.

## 1. Environment

```powershell
python -m venv .venv
& .\\.venv\\Scripts\\python.exe -m pip install -r environment\\requirements-all.txt
```

## 2. Fast integrity and analysis checks

```powershell
$env:PYTHONPATH = (Resolve-Path code)
& .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_ca_hmcd*.py"
& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_parameter_robustness_analysis.py `
  --source-json results\\parameter_robustness\\source\\raw_results.json `
  --integrity-json results\\parameter_robustness\\source\\stage2_upgrade_integrity_audit.json `
  --output-dir reproduced\\parameter_robustness `
  --bootstrap-resamples 10000
```

## 3. Recompute formal public replay statistics from supplied episode outputs

```powershell
& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_stage5_statistics.py `
  --source-dir results\\formal_runs\\primary_public_replay `
  --output-dir reproduced\\primary_statistics `
  --bootstrap-resamples 10000 `
  --bounded-resamples 5000

& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_no_pruning_analysis.py `
  --baseline-dir results\\formal_runs\\primary_public_replay `
  --control-dir results\\formal_runs\\no_pruning_control `
  --output-dir reproduced\\no_pruning `
  --bootstrap-resamples 10000

& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_external_baseline_analysis.py `
  --reference-dir results\\formal_runs\\external_matched_ca_reference `
  --baseline-dir results\\formal_runs\\external_full_candidate_baselines `
  --legacy-reference-dir results\\formal_runs\\primary_public_replay `
  --output-dir reproduced\\external_baselines `
  --bootstrap-resamples 10000
```

## 4. Full formal replay regeneration

The following commands regenerate the episode and per-period outputs from the
frozen protocol. They are substantially slower than the analysis-only route.

```powershell
& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\\public_replay `
  --output-dir reproduced\\formal_primary `
  --mode formal

& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\\public_replay `
  --output-dir reproduced\\formal_no_pruning `
  --mode formal-control `
  --algorithms No-Pruning

& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\\public_replay `
  --output-dir reproduced\\formal_external `
  --mode formal-external `
  --algorithms HiGHS-MILP Genetic-Algorithm

& .\\.venv\\Scripts\\python.exe code\\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\\public_replay `
  --output-dir reproduced\\formal_reference `
  --mode formal-reference `
  --algorithms CA-HMCD
```

The formal runner recreates `registered_per_step.csv`; the release package
omits those large, derivable files while retaining episode outputs, protocol
data, audits, and source hashes.

## 5. Rebuild the manuscript

```powershell
& .\\.venv\\Scripts\\python.exe code\\build_stage9_robustness_release_manuscript.py
```

The manuscript builder expects the repository layout used by the authors. The
archived manuscript and supplementary tables are included for inspection even
when the builder is not run.
"""
    author_input = """# AUTHOR_INPUT_NEEDED before public release

The files are technically ready for repository upload, but the following
author-controlled fields cannot be inferred:

1. Final creator list, affiliations, ORCID identifiers, and corresponding
   author contact.
2. Software licence for original code.
3. Licence for author-generated derived data and documentation.
4. Confirmation that redistributed derived replay files comply with the
   original UZH-FPV and Anti-UAV410 terms.
5. Repository choice and public DOI or accession.
6. Replacement of both `AUTHOR_INPUT_NEEDED` placeholders in the manuscript.
7. Optional funding identifiers and related manuscript/preprint identifier.

Recommended route: create a versioned Zenodo or OSF record, upload the ZIP,
select the verified licences, publish the record, and insert the resulting DOI
in the Data availability statement and citation metadata.
"""
    release_notes = """# Release notes

## 2026-09-17

- Reanalysed the current v2.3 parameter study as 54 parameter-method
  configurations with 30 paired seeds and 12 periods per configuration.
- Reported 36 paired service contrasts in six prespecified Holm families.
- Added a unified 516-comparison statistical registry and 141-family Holm
  registry.
- Added episode-level outputs for the primary replay, no-pruning control,
  external full-candidate baselines, and matched CA-HMCD reference.
- Added source hashes, protocol registries, figure source data, tests,
  environment files, and Stage 9 manuscript materials.
- Did not redistribute the third-party UZH-FPV or Anti-UAV410 source datasets.
"""
    environment = """# Environment

The formal external-baseline and matched-reference runs used:

- Python 3.12.14
- NumPy 2.5.3
- SciPy 1.18.1
- HiGHS 1.12.0 through `scipy.optimize.milp`

The DOCX builder additionally used:

- pandas 3.0.1
- python-docx 1.2.0

Create a clean environment from `requirements-all.txt`. Timing values remain
machine-dependent and should be interpreted only within a matched environment.
"""
    citation_template = """cff-version: 1.2.0
message: "Please cite this software and reproducibility package."
title: "CA-HMCD reproducibility package"
type: software
version: "2026.09.17"
date-released: "2026-09-17"
authors:
  - family-names: "AUTHOR_INPUT_NEEDED"
    given-names: "AUTHOR_INPUT_NEEDED"
repository-code: "AUTHOR_INPUT_NEEDED"
doi: "AUTHOR_INPUT_NEEDED"
"""
    license_note = """A public release requires author selection of licences.

Do not treat this placeholder as a licence grant. The authors should select:

- a software licence for original code; and
- a compatible data/documentation licence for author-generated derived files.

Third-party UZH-FPV and Anti-UAV410 materials remain under their original
terms and are not relicensed by this package.
"""

    files = {
        "README.md": readme,
        "REPRODUCE.md": reproduce,
        "AUTHOR_INPUT_NEEDED.md": author_input,
        "RELEASE_NOTES.md": release_notes,
        "environment/ENVIRONMENT.md": environment,
        "environment/requirements-experiments.txt": (
            "numpy==2.5.3\nscipy==1.18.1\n"
        ),
        "environment/requirements-documents.txt": (
            "pandas==3.0.1\npython-docx==1.2.0\n"
        ),
        "environment/requirements-all.txt": (
            "numpy==2.5.3\nscipy==1.18.1\npandas==3.0.1\npython-docx==1.2.0\n"
        ),
        "CITATION.cff.template": citation_template,
        "LICENSE_TO_BE_SELECTED.txt": license_note,
    }
    for relative, content in files.items():
        destination = package_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def copy_package_inputs(package_dir: Path) -> None:
    code_files = [
        "ca_hmcd_simulation.py",
        "ca_hmcd_replay.py",
        "ca_hmcd_replay_protocol.py",
        "ca_hmcd_replay_runner.py",
        "ca_hmcd_stage3_statistics.py",
        "ca_hmcd_stage5_statistics.py",
        "ca_hmcd_no_pruning_analysis.py",
        "ca_hmcd_external_baseline_analysis.py",
        "ca_hmcd_parameter_robustness_analysis.py",
        "build_stage4_restructured_manuscript.py",
        "build_stage7_revised_manuscript.py",
        "build_stage8_external_baselines_manuscript.py",
        "build_stage9_robustness_release_manuscript.py",
        "build_ca_hmcd_reproducibility_package.py",
    ]
    test_files = [
        "test_ca_hmcd_replay.py",
        "test_ca_hmcd_replay_protocol.py",
        "test_ca_hmcd_replay_runner.py",
        "test_ca_hmcd_stage1.py",
        "test_ca_hmcd_stage2.py",
        "test_ca_hmcd_stage3.py",
        "test_ca_hmcd_stage5.py",
    ]
    protocol_files = [
        "ca_hmcd_public_replay_protocol.md",
        "ca_hmcd_no_pruning_protocol_20260917.md",
        "ca_hmcd_external_baselines_protocol_20260917.md",
        "ca_hmcd_external_baselines_protocol_amendment_20260917.md",
        "ca_hmcd_stage2_upgrade_protocol_20260916.md",
        "ca_hmcd_stage2_replay_protocol_report_20260916.md",
    ]
    manuscript_files = [
        "CA-HMCD_EAAI_Stage9_Robustness_Release_20260917.docx",
        "CA-HMCD_EAAI_Stage9_Robustness_Release_20260917_audit.txt",
        "CA-HMCD_EAAI_Stage9_Robustness_Release_20260917_QA.pdf",
        "CA-HMCD_Supplementary_Table_S8_External_Baselines_20260917.csv",
        "CA-HMCD_Supplementary_Table_S9_Parameter_Configurations_20260917.csv",
        "CA-HMCD_Supplementary_Table_S10_Parameter_Paired_Statistics_20260917.csv",
    ]
    for filename in code_files:
        copy_file(ROOT / filename, package_dir / "code" / filename)
    for filename in test_files:
        copy_file(ROOT / filename, package_dir / "tests" / filename)
    for filename in protocol_files:
        copy_file(ROOT / filename, package_dir / "protocol" / filename)
    for filename in manuscript_files:
        copy_file(ROOT / filename, package_dir / "manuscript" / filename)
    copy_file(
        ROOT / "CA-HMCD_54_Parameter_Robustness_Report_20260917.md",
        package_dir
        / "results"
        / "parameter_robustness"
        / "parameter_robustness_summary_zh.md",
    )

    copy_selected_directory(
        ROOT / "ca_hmcd_replay_protocol_20260916",
        package_dir / "protocol" / "public_replay",
    )
    copy_selected_directory(
        ROOT / "figures" / "stage6_replay",
        package_dir / "figures" / "stage6_replay",
    )

    analysis_sources = {
        "primary_public_replay": ROOT / "ca_hmcd_stage5_statistics_20260916",
        "no_pruning_control": ROOT / "ca_hmcd_no_pruning_analysis_20260917",
        "external_full_candidate_baselines": (
            ROOT / "ca_hmcd_external_baselines_analysis_20260917"
        ),
        "parameter_robustness": (
            ROOT / "ca_hmcd_parameter_robustness_analysis_20260917"
        ),
    }
    large_analysis_exclusions = {
        "combined_per_step.csv",
        "combined_episode_results.csv",
    }
    for name, source in analysis_sources.items():
        copy_selected_directory(
            source,
            package_dir / "results" / name,
            excluded_names=large_analysis_exclusions,
        )

    formal_sources = {
        "primary_public_replay": ROOT / "ca_hmcd_stage4_replay_formal_20260916",
        "no_pruning_control": ROOT / "ca_hmcd_no_pruning_formal_20260917",
        "external_full_candidate_baselines": (
            ROOT / "ca_hmcd_external_baselines_formal_20260917"
        ),
        "external_matched_ca_reference": (
            ROOT / "ca_hmcd_external_reference_formal_20260917"
        ),
    }
    formal_exclusions = {"registered_per_step.csv"}
    for name, source in formal_sources.items():
        copy_selected_directory(
            source,
            package_dir / "results" / "formal_runs" / name,
            excluded_names=formal_exclusions,
            excluded_parts={"shards"},
        )

    parameter_source = package_dir / "results" / "parameter_robustness" / "source"
    copy_file(
        ROOT / "ca_hmcd_stage2_upgrade_20260916" / "raw_results.json",
        parameter_source / "raw_results.json",
    )
    copy_file(
        ROOT
        / "ca_hmcd_stage2_upgrade_20260916"
        / "stage2_upgrade_integrity_audit.json",
        parameter_source / "stage2_upgrade_integrity_audit.json",
    )
    copy_file(
        ROOT / "ca_hmcd_stage2_upgrade_20260916" / "parameter_provenance.csv",
        parameter_source / "parameter_provenance.csv",
    )


def build_manifest(package_dir: Path) -> None:
    rows: List[Dict[str, object]] = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or path.name in {"MANIFEST.csv", "checksums.sha256"}:
            continue
        relative = path.relative_to(package_dir).as_posix()
        top_level = relative.split("/", 1)[0]
        rows.append(
            {
                "relative_path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "role": top_level,
            }
        )
    write_csv(package_dir / "MANIFEST.csv", rows)
    checksum_lines = [f"{row['sha256']}  {row['relative_path']}" for row in rows]
    (package_dir / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )


def build_zip(package_dir: Path, zip_path: Path) -> None:
    ensure_within_root(zip_path)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    arcname=(Path(PACKAGE_NAME) / path.relative_to(package_dir)),
                )


def build() -> None:
    ensure_within_root(PACKAGE_DIR)
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    PACKAGE_DIR.mkdir(parents=True)

    write_package_documents(PACKAGE_DIR)
    copy_package_inputs(PACKAGE_DIR)
    registry_summary = build_statistical_registry(
        PACKAGE_DIR / "results" / "statistical_registry"
    )
    build_manifest(PACKAGE_DIR)
    build_zip(PACKAGE_DIR, ZIP_PATH)

    output = {
        "package_directory": str(PACKAGE_DIR),
        "zip_path": str(ZIP_PATH),
        "zip_size_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256_file(ZIP_PATH),
        "manifest_files": len(read_csv(PACKAGE_DIR / "MANIFEST.csv")),
        "registry_comparisons": registry_summary["comparisons"],
        "registry_holm_families": registry_summary["holm_families"],
    }
    (ROOT / f"{PACKAGE_NAME}_build_summary.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    build()

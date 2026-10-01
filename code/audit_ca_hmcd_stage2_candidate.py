"""Final integrity audit for the Stage 2 candidate-compression closure."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from ca_hmcd_framework import framework_contract


AUDIT_VERSION = "ca-hmcd-stage2-candidate-audit-v1"


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest(analysis_dir: Path) -> bool:
    rows = read_csv(analysis_dir / "stage2_release_manifest.csv")
    return bool(rows) and all(
        (analysis_dir / row["path"]).is_file()
        and int(row["size_bytes"]) == (analysis_dir / row["path"]).stat().st_size
        and row["sha256"] == sha256_file(analysis_dir / row["path"])
        for row in rows
    )


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_audit(
    project_dir: Path,
    formal_dir: Path,
    analysis_dir: Path,
    closure_dir: Path,
) -> Dict[str, object]:
    formal = json.loads(
        (formal_dir / "stage2_execution_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    analysis = json.loads(
        (analysis_dir / "analysis_metadata.json").read_text(encoding="utf-8")
    )
    gate = json.loads(
        (analysis_dir / "stage2_gate_a.json").read_text(encoding="utf-8")
    )
    parent = json.loads(
        (
            project_dir
            / "ca_hmcd_stage0_stage1_20260917"
            / "stage0_stage1_audit.json"
        ).read_text(encoding="utf-8")
    )
    families = read_csv(analysis_dir / "holm_family_registry.csv")
    paired = read_csv(analysis_dir / "paired_statistics.csv")
    bounded = read_csv(analysis_dir / "bounded_metric_intervals.csv")
    final_framework = framework_contract()
    stage9_expected = {
        row["path"]: row["expected_sha256"]
        for row in parent["stage0"]["file_checks"]
        if row["path"]
        in {
            "ca_hmcd_simulation.py",
            "ca_hmcd_replay.py",
            "ca_hmcd_replay_runner.py",
        }
    }
    frozen_source_expected = formal["code_hashes"]
    frozen_source_observed = {
        "ca_hmcd_stage2_candidate_runner.py": sha256_file(
            closure_dir
            / "frozen_execution_source"
            / "ca_hmcd_stage2_candidate_runner.py"
        ),
        "ca_hmcd_framework.py": sha256_file(
            closure_dir
            / "frozen_execution_source"
            / "ca_hmcd_framework.py"
        ),
    }
    checks = [
        {
            "check": "formal execution audit",
            "observed": formal.get("audit_pass"),
            "expected": True,
            "passed": bool(formal.get("audit_pass")),
        },
        {
            "check": "formal run count",
            "observed": formal.get("expected_runs"),
            "expected": 5280,
            "passed": formal.get("expected_runs") == 5280,
        },
        {
            "check": "analysis source audit",
            "observed": analysis.get("source_audit_pass"),
            "expected": True,
            "passed": bool(analysis.get("source_audit_pass")),
        },
        {
            "check": "six-policy pairing audit",
            "observed": analysis.get("pairing_audit_pass"),
            "expected": True,
            "passed": bool(analysis.get("pairing_audit_pass")),
        },
        {
            "check": "registered comparison count",
            "observed": len(paired),
            "expected": 275,
            "passed": len(paired) == 275,
        },
        {
            "check": "registered Holm families",
            "observed": len(families),
            "expected": 55,
            "passed": len(families) == 55
            and all(int(row["number_of_comparisons"]) == 5 for row in families),
        },
        {
            "check": "Holm adjusted P values valid",
            "observed": "all adjusted P in [raw P, 1]",
            "expected": True,
            "passed": all(
                float(row["p_unadjusted"])
                <= float(row["p_holm_adjusted"])
                <= 1.0
                for row in paired
            ),
        },
        {
            "check": "bounded intervals valid",
            "observed": len(bounded),
            "expected": 180,
            "passed": len(bounded) == 180
            and all(
                0.0
                <= float(row["ci95_low"])
                <= float(row["ci95_high"])
                <= 1.0
                for row in bounded
            ),
        },
        {
            "check": "release manifest verified",
            "observed": verify_manifest(analysis_dir),
            "expected": True,
            "passed": verify_manifest(analysis_dir),
        },
        {
            "check": "Stage 9 core remains frozen",
            "observed": {
                name: sha256_file(project_dir / name)
                for name in stage9_expected
            },
            "expected": stage9_expected,
            "passed": all(
                sha256_file(project_dir / name) == expected
                for name, expected in stage9_expected.items()
            ),
        },
        {
            "check": "formal Stage 2 execution source frozen",
            "observed": frozen_source_observed,
            "expected": {
                name: frozen_source_expected[name]
                for name in frozen_source_observed
            },
            "passed": all(
                observed == frozen_source_expected[name]
                for name, observed in frozen_source_observed.items()
            ),
        },
        {
            "check": "Gate A adjudicated once",
            "observed": gate.get("decision"),
            "expected": "PASS, PROVISIONAL, or FAIL",
            "passed": gate.get("decision") in {"PASS", "PROVISIONAL", "FAIL"},
        },
        {
            "check": "framework records failed RT promotion",
            "observed": final_framework["profiles"]["CA-HMCD-RT"]["status"],
            "expected": "EXPERIMENTAL_GATE_A_FAILED",
            "passed": (
                gate.get("decision") == "FAIL"
                and final_framework["profiles"]["CA-HMCD-RT"]["status"]
                == "EXPERIMENTAL_GATE_A_FAILED"
            ),
        },
    ]
    passed = all(bool(row["passed"]) for row in checks)
    payload = {
        "audit_version": AUDIT_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_pass": passed,
        "formal_execution_runs": formal.get("expected_runs"),
        "combined_episode_rows": analysis.get("episode_rows"),
        "combined_per_step_rows": analysis.get("per_step_rows"),
        "paired_comparisons": len(paired),
        "holm_families": len(families),
        "gate_a": gate,
        "checks": checks,
    }
    closure_dir.mkdir(parents=True, exist_ok=True)
    (closure_dir / "stage2_completion_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (closure_dir / "stage2_final_framework_contract.json").write_text(
        json.dumps(final_framework, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(closure_dir / "stage2_completion_audit.csv", checks)
    report = "\n".join(
        [
            "# Stage 2 completion audit",
            "",
            f"- Audit: {'PASS' if passed else 'FAIL'}",
            f"- Formal new-policy runs: {formal.get('expected_runs')}",
            f"- Combined episode rows: {analysis.get('episode_rows')}",
            f"- Combined decision rows: {analysis.get('per_step_rows')}",
            f"- Paired comparisons: {len(paired)}",
            f"- Holm families: {len(families)}",
            f"- Gate A: {gate.get('decision')}",
            f"- Adjudication: {gate.get('method_adjudication')}",
            "",
            "Stage 2 is complete even though Gate A failed: the registered "
            "adaptive policy was implemented and evaluated without post-hoc "
            "retuning, and the negative promotion decision is preserved.",
            "",
        ]
    )
    (closure_dir / "stage2_completion_report.md").write_text(
        report, encoding="utf-8"
    )
    if not passed:
        failed = [str(row["check"]) for row in checks if not row["passed"]]
        raise RuntimeError(f"Stage 2 completion audit failed: {failed}")
    return payload


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit the Stage 2 candidate-compression closure"
    )
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--formal-dir", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--closure-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_cli().parse_args(argv)
    result = run_audit(
        args.project_dir,
        args.formal_dir,
        args.analysis_dir,
        args.closure_dir,
    )
    print(
        f"[stage2-audit] pass={result['audit_pass']} "
        f"gate_a={result['gate_a']['decision']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

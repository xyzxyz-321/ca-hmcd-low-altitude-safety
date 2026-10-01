"""Verify hashes and unified registry counts in a staged release tree."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "evidence/analysis/ca_hmcd_stage6_unified_statistics_20260918"


def digest(file: Path) -> str:
    h = hashlib.sha256()
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def count_csv(file: Path) -> int:
    with file.open("r", encoding="utf-8-sig", newline="") as stream:
        return sum(1 for _ in csv.DictReader(stream))


with (ROOT / "MANIFEST.csv").open("r", encoding="utf-8", newline="") as stream:
    manifest = list(csv.DictReader(stream))
for row in manifest:
    file = ROOT / row["path"]
    assert file.is_file(), f"Missing file: {file}"
    assert file.stat().st_size == int(row["size_bytes"]), f"Size mismatch: {file}"
    assert digest(file) == row["sha256"], f"SHA-256 mismatch: {file}"

comparisons = count_csv(REGISTRY / "complete_statistical_registry_1592.csv")
families = count_csv(REGISTRY / "complete_holm_family_registry_316.csv")
assert comparisons == 1592, comparisons
assert families == 316, families
print(f"PASS: {len(manifest)} hashed files; {comparisons} comparisons; {families} Holm families")

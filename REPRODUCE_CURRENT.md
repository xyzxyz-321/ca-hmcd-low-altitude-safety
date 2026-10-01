# Current 2026-09-18 evidence layers

The earlier replay instructions are in `REPRODUCE.md`. The current manuscript
uses `evidence/analysis/`, `evidence/protocol/`, and
`evidence/formal_aggregates/` (Stage 2-5); the complete 1,592-row registry,
316-family register, hashes and validation checks are in
`evidence/analysis/ca_hmcd_stage6_unified_statistics_20260918/`.

First verify the archive content and unified registry without external data
or third-party Python packages:

```powershell
python verify_release.py
```

The historical full regression suite was executed in the author's locked
workspace, but it is **not** a turnkey test of this staged subset. Several
tests reference the original author-directory layout and per-run checkpoint
shards omitted here. Running those tests additionally requires installing
`environment/requirements-all.txt` (including SciPy) and adapting the
expected artifact paths. A partial syntax check can be run from the package
root with:

```powershell
python -m compileall -q code tests
```

Formal aggregate CSVs are supplied; per-run checkpoint shards and the
duplicated Stage-2 analysis-period file are omitted. Recomputing entire formal
workloads requires running the individual frozen stage protocols; this archive
has not yet been independently validated as a one-command full regeneration.
Historical manuscript builders may depend on the authors' original workspace
layout; the updated manuscript and supplementary registry are included.

# Reproduction instructions

All commands are run from the package root. Python 3.12 is recommended.

## 1. Environment

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r environment\requirements-all.txt
```

## 2. Fast integrity and analysis checks

```powershell
$env:PYTHONPATH = (Resolve-Path code)
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ca_hmcd*.py"
& .\.venv\Scripts\python.exe code\ca_hmcd_parameter_robustness_analysis.py `
  --source-json results\parameter_robustness\source\raw_results.json `
  --integrity-json results\parameter_robustness\source\stage2_upgrade_integrity_audit.json `
  --output-dir reproduced\parameter_robustness `
  --bootstrap-resamples 10000
```

## 3. Recompute formal public replay statistics from supplied episode outputs

```powershell
& .\.venv\Scripts\python.exe code\ca_hmcd_stage5_statistics.py `
  --source-dir results\formal_runs\primary_public_replay `
  --output-dir reproduced\primary_statistics `
  --bootstrap-resamples 10000 `
  --bounded-resamples 5000

& .\.venv\Scripts\python.exe code\ca_hmcd_no_pruning_analysis.py `
  --baseline-dir results\formal_runs\primary_public_replay `
  --control-dir results\formal_runs\no_pruning_control `
  --output-dir reproduced\no_pruning `
  --bootstrap-resamples 10000

& .\.venv\Scripts\python.exe code\ca_hmcd_external_baseline_analysis.py `
  --reference-dir results\formal_runs\external_matched_ca_reference `
  --baseline-dir results\formal_runs\external_full_candidate_baselines `
  --legacy-reference-dir results\formal_runs\primary_public_replay `
  --output-dir reproduced\external_baselines `
  --bootstrap-resamples 10000
```

## 4. Full formal replay regeneration

The following commands regenerate the episode and per-period outputs from the
frozen protocol. They are substantially slower than the analysis-only route.

```powershell
& .\.venv\Scripts\python.exe code\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\public_replay `
  --output-dir reproduced\formal_primary `
  --mode formal

& .\.venv\Scripts\python.exe code\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\public_replay `
  --output-dir reproduced\formal_no_pruning `
  --mode formal-control `
  --algorithms No-Pruning

& .\.venv\Scripts\python.exe code\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\public_replay `
  --output-dir reproduced\formal_external `
  --mode formal-external `
  --algorithms HiGHS-MILP Genetic-Algorithm

& .\.venv\Scripts\python.exe code\ca_hmcd_replay_runner.py `
  --protocol-dir protocol\public_replay `
  --output-dir reproduced\formal_reference `
  --mode formal-reference `
  --algorithms CA-HMCD
```

The formal runner recreates `registered_per_step.csv`; the release package
omits those large, derivable files while retaining episode outputs, protocol
data, audits, and source hashes.

## 5. Rebuild the manuscript

```powershell
& .\.venv\Scripts\python.exe code\build_stage9_robustness_release_manuscript.py
```

The manuscript builder expects the repository layout used by the authors. The
archived manuscript and supplementary tables are included for inspection even
when the builder is not run.

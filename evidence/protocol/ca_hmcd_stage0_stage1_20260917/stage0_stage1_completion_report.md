## Material Passport

- **Material ID:** `ca-hmcd-stage0-stage1-completion-20260917`
- **Material type:** experiment and implementation result
- **Status:** `VERIFIED`
- **Date:** 2026-09-17
- **Parent baseline:** `ca-hmcd-stage9-frozen-20260917`
- **Framework version:** `ca-hmcd-framework-v3.0`
- **Data access level:** workspace-local registered evidence
- **Verification environment:** Python 3.12.14, NumPy 2.5.3, SciPy 1.18.1

# Stage 0 and Stage 1 Completion Report

## Stage 0: Frozen Baseline

The Stage 9 manuscript, source code, replay protocol, formal-run metadata,
No-Pruning control, external-baseline evidence, parameter-sensitivity evidence,
and reproducibility package have been pinned by SHA-256.

The frozen source copies are read-only. The active Stage 9 core files remain
unchanged and match the release package:

- `ca_hmcd_simulation.py`
- `ca_hmcd_replay.py`
- `ca_hmcd_replay_runner.py`
- `ca_hmcd_replay_protocol.py`
- external-baseline, No-Pruning, and parameter-analysis scripts

The frozen contract retains:

- 44 independent public-data units;
- 30 nested stochastic seeds per unit;
- 12 rolling periods;
- 10 response resources;
- fixed `K = 12`;
- 4,000-node registered search budget;
- value model `ca-hmcd-stage1-closure-v2.3`;
- evaluator `complete-v3-stage1-closure`;
- service endpoint `solver-decoupled-service-v2`.

The revision registration explicitly prohibits tuning the value model or
external evaluator to reverse the registered HiGHS result.

## Stage 1: Model and Solver Decoupling

The new `ca_hmcd_framework.py` separates five responsibilities:

1. complete candidate enumeration and coalition valuation;
2. candidate retention policy;
3. global solver backend;
4. true-state repair;
5. complete-model and solver-decoupled evaluation.

The following profiles are registered:

| Profile | Candidate policy | Solver | Status |
|---|---|---|---|
| CA-HMCD-Exact | complete pool | HiGHS | active |
| CA-HMCD-FixedK | dominance plus fixed `K=12` | bounded search | Stage 9 reference |
| CA-HMCD-NoPruning | complete pool | bounded search | active control |
| CA-HMCD-RT | adaptive K | bounded search | reserved for Stage 2 |

`CA-HMCD-RT` is deliberately non-executable until the adaptive-K rule is
registered and implemented. It cannot silently run the fixed-K placeholder
under a new method label.

## Verification Results

- Frozen manifest entries verified: **27/27**
- Complete candidate-pool parity trials: **12/12**
- Fixed-K candidate identity parity trials: **12/12**
- Framework HiGHS versus direct HiGHS parity: **12/12**
- HiGHS versus unlimited branch-and-bound objective parity: **12/12**
- Maximum exact-solver objective difference: **5.551e-17**
- Full CA-HMCD test suite: **54/54 passed**
- Overall Stage 0/1 audit: **PASS**

## Claim Boundary

These stages establish provenance and software architecture. They do not
establish an advantage for a real-time approximation method. Adaptive K, the
fixed-K scan, quality-complexity analysis, and the decision gate for retaining
`CA-HMCD-RT` remain Stage 2 work.

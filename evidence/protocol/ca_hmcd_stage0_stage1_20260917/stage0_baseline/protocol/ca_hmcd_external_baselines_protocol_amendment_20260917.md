# External-Baseline Protocol Amendment: Environment-Matched Reference

## Material Passport

- **Artifact ID**: `CA-HMCD-EXTBASE-AMEND-20260917`
- **Date**: 2026-09-17
- **Parent protocol**: `CA-HMCD-EXTBASE-20260917`
- **Reason**: execution-environment comparability

## Amendment

The original protocol planned to reuse CA-HMCD records from the prior formal
replay. Those records remain valid for deterministic objective and service
comparisons because the replay registry, model version, evaluator version, and
per-period states match. However, the prior formal run used Python 3.14 alpha,
whereas HiGHS-MILP and Genetic-Algorithm use the locked Python 3.12
environment. Cross-environment wall-clock comparisons would therefore be
confounded.

Before using formal runtime results, CA-HMCD will be rerun over the complete
1,320-unit registry in the same Python 3.12 environment used by the two new
baselines. No model, candidate, solver, seed, state, budget, evaluator, or
repair setting is changed.

The following checks are required:

1. The matched CA-HMCD run completes all 1,320 units and passes every registered
   state and feasibility audit.
2. Its `ca_hmcd_simulation.py` hash equals the external-baseline formal run.
3. Its protocol fingerprint, model/evaluator versions, replay hashes, and
   per-period state fingerprints equal those of the external-baseline run.
4. All non-timing CA-HMCD episode outcomes equal the prior formal CA-HMCD
   records within `1e-12`.
5. Only the environment-matched run is used for runtime comparisons.

This amendment does not change the compared methods or any statistical
endpoint. It prevents an execution-environment difference from being
misinterpreted as an algorithmic runtime effect.

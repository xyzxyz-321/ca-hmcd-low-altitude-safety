# CA-HMCD Stage 0 and Stage 1 Audit

Date: 2026-09-17

- Frozen baseline manifest entries: 27
- Baseline hash verification: PASS
- Framework version: `ca-hmcd-framework-v3.0`
- Parity trials: 12
- Complete-pool parity: 12/12
- Fixed-K parity: 12/12
- HiGHS wrapper parity: 12/12
- HiGHS versus unlimited branch-and-bound objective parity: 12/12
- Maximum exact-solver objective difference: 5.551e-17
- Overall audit: PASS

## Interpretation

The Stage 9 evidence remains pinned by SHA-256 and unchanged. The v3 framework reproduces the registered complete-candidate and fixed-K candidate identities, and its HiGHS backend reproduces the direct registered backend. Candidate policy and solver backend can now be changed independently without altering the value model or evaluator.

The `CA-HMCD-RT` profile is an interface placeholder only. Its adaptive-K rule remains a Stage 2 task and is not treated as a completed algorithm in this audit.

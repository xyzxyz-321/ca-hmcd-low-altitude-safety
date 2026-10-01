# CA-HMCD v3 Revision Registration

Date registered: 2026-09-17

## Locked evidence contract

The Stage 9 value model, coefficients, public replay split, external evaluator,
and service endpoint are unchanged. They must not be tuned to reverse the
registered result that full-candidate HiGHS is faster at the ten-resource scale
and returns higher-quality high-load allocations.

## Stage 1 architecture

The v3 framework separates:

1. complete candidate construction and coalition valuation;
2. candidate retention policy;
3. global allocation backend;
4. true-state feasibility repair;
5. complete-model and solver-decoupled evaluation.

`CA-HMCD-Exact` uses the complete candidate pool and HiGHS.
`CA-HMCD-FixedK` reproduces fixed-K retention with bounded search.
`CA-HMCD-NoPruning` uses the complete pool with bounded search.
`CA-HMCD-RT` is reserved for the adaptive-K rule to be registered in Stage 2.

## Subsequent registered tests

- K scan: 6, 12, 24, 48, and full.
- Scale: 10, 16, 24, and 32 resources.
- Concurrent tasks: 4, 8, 12, and 16.
- Decision budgets: 50, 100, 200, and 500 ms.
- Statistical unit: independent public workload or trajectory; stochastic
  seeds remain nested repeats.

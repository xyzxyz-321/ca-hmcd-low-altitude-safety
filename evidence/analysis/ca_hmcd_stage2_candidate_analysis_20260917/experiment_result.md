## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run + validate
- Origin Date: 2026-09-17
- Verification Status: VERIFIED
- Version Label: stage2_candidate_compression_closure_v1

# Stage 2 candidate-compression closure

## Execution and integrity

- The formal scan contains 44 independent public replay workload clusters, 30 nested seeds per cluster, six paired candidate policies, and 12 rolling decisions per run.
- The four newly executed policies contributed 5,280 episode runs and 63,360 decision steps. K12 and Full were reused from their frozen formal controls after provenance and state-pairing audits.
- Every final allocation was feasible. The complete six-policy state, active-task, replay-hash, and pre-pruning candidate-count audit passed.

## Registered Gate A

- Decision: **FAIL**.
- Method adjudication: `DO_NOT_PROMOTE_CA_HMCD_RT`.
- Service-gap recovery: 93.7%.
- Coverage-gap recovery: 100.0%.
- Retained-candidate ratio to Full: 17.0%.
- Visited-node ratio to Full: 96.2%.
- AdaptiveK therefore remains an experimental quality-oriented compression policy; it is not promoted as CA-HMCD-RT.

## High-load trade-off

- K12 service score: 0.5174; AdaptiveK: 0.5371; Full: 0.5384.
- K12 coverage: 0.8306; AdaptiveK: 0.8896; Full: 0.8825.
- K12 candidates: 57.2; AdaptiveK: 120.0; Full: 704.8.
- K12 visited nodes: 2367.2; AdaptiveK: 3847.0; Full: 4000.0.

## Interpretation

- K6 is over-compressed and loses substantial service quality under multi-target load.
- K12 preserves a clear search-effort advantage but exhibits a high-load quality gap.
- K24 and AdaptiveK recover most of that quality, yet their branch-and-bound searches approach the 4,000-node limit. Candidate-count compression alone is therefore insufficient to establish a real-time advantage.
- The Stage 2 mechanism is closed with a negative promotion decision rather than post-hoc retuning. The next solver work should target search ordering, warm starts, or explicit time budgets while keeping the registered value model fixed.

## Statistical outputs

- 275 paired comparisons were reported; 91 remain significant after their registered Holm correction.
- `paired_statistics.csv` separately reports raw and adjusted P values, 95% confidence intervals, effect sizes, and win rates.
- Wall-clock comparisons involving the reused Full control are descriptive because that control was produced under a different Python runtime. Candidate counts, nodes, decisions, and quality outcomes remain deterministic protocol evidence.

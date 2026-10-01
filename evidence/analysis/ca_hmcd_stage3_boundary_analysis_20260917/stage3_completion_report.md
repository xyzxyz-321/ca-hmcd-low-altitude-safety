## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: run + validate
- Origin Date: 2026-09-17T14:40:05.490986+00:00
- Verification Status: VERIFIED
- Version Label: ca_hmcd_stage3_boundary_v1

## Experiment Result

- **ID**: ca-hmcd-stage3-exact-realtime-boundary-v1
- **Type**: simulation benchmark
- **Status**: completed
- **Execution audit**: PASS
- **Independent units**: 480
- **Method runs**: 2400
- **Formal duration**: 2081.9 s

## Gate B

- **Decision**: FAIL
- **Passing cells**: 0 / 64
- **Adjudication**: DO_NOT_CLAIM_A_VALIDATED_REAL_TIME_BOUNDARY
- **Interpretation**: No registered cell simultaneously established an exact deadline miss, a bounded-K12 deadline success, full feasibility, and all three quality-loss limits.

## Boundary Summary

- Cells with 30/30 optimal HiGHS references: 64 / 64.
- Highest observed bounded deadline-hit cell: R10-T4 at 200 ms (100.0%).
- Exact deadline-miss criterion passed in 47 / 64 cells.
- Bounded-K12 deadline-hit criterion passed in only 2 / 64 cells; in both cells, exact HiGHS also met the deadline.
- Service, coverage, and objective-loss criteria passed in 13, 11, and 28 cells, respectively.

### Representative boundary cells

| Cell | Exact hit | Bounded hit | Service loss | Coverage loss | Objective/task loss | Interpretation |
|---|---:|---:|---:|---:|---:|---|
| R10-T4 @ 200 ms | 100.0% | 100.0% | 0.0009 | 0.0000 | 0.0003 | Both modes meet the deadline |
| R16-T4 @ 100 ms | 23.3% | 3.3% | 0.0101 | 0.0167 | 0.0043 | Quality retained, but bounded K12 is slower |
| R24-T4 @ 200 ms | 0.0% | 40.0% | 0.0769 | 0.1500 | 0.0225 | Faster in some seeds, but deadline and quality gates fail |
| R32-T4 @ 500 ms | 0.0% | 80.0% | 0.1604 | 0.2750 | 0.0536 | Closest separation, but all three quality limits fail |

Candidate construction accounts for 74% to 100% of bounded-mode decision time.
The present fixed-K policy still enumerates and compares an intermediate pool
before retaining at most 12 candidates per task. This cost prevents a validated
real-time boundary and, at 10 to 16 resources, can exceed complete enumeration
plus HiGHS.

## Statistical Outputs

- 576 paired comparisons with separate raw P values, Holm-adjusted P values, effect sizes, win rates, and 95% BCa intervals.
- Deadline and feasibility rates use 95% Wilson intervals.
- Common-task response time includes only tasks served by both methods.

## Scope

This static boundary experiment isolates candidate generation and optimization cost. It does not replace rolling replay evidence and does not reverse the failed Stage-2 AdaptiveK gate.

The present evidence supports keeping exact HiGHS as the default where its
observed deadline is acceptable. Fixed K12 remains an experimental control and
must not be described as a validated real-time solver. A future real-time
attempt requires an interruptible candidate constructor, indexed or
incremental nondominance maintenance, and cheap pre-valuation bounds.

## Anomalies Detected

- None that invalidated the registered execution.

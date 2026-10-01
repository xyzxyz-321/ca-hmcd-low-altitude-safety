# CA-HMCD Stage 2 candidate-compression protocol

Date registered: 2026-09-17

## Objective

Close the candidate-compression mechanism without changing the registered
coalition-value model, true-state evaluator, service endpoint, safety repair,
or the 4,000-node bounded-search budget.

## Frozen parent

- Parent revision: `ca-hmcd-v3-framework-registration-20260917`
- Parent framework: `ca-hmcd-framework-v3.0`
- Parent framework SHA-256:
  `c68e976233354da362ff65a8e7a84bea42453e570d14fe87d08f8b72b1d64b7e`
- Stage 9 reference implementation SHA-256:
  `b6fae78029a59e65c6a49fe25cd11e4d2fa9d5c912ced55ad9b8a4b92c126ef1`
- Public replay protocol fingerprint:
  `b0043867b0802dbabaf4183e6e204bfaddcc4a1aceb8197831096e87500fdcea`

## Candidate policies

All policies start from the same complete feasible candidate set and use the
same switch-adjusted ordering, previous-coalition protection, and diversity
retention operator.

The fixed scan is `K = {6, 12, 24, 48, Full}`. The registered Stage 9
`K = 12` results and the formal No-Pruning results are reused after provenance
and pairing audits. New replay runs are required for `K = 6, 24, 48` and the
adaptive policy.

The adaptive policy is fixed before outcome inspection:

1. Apply the registered dominance operator.
2. Let `L` be the number of active perceived tasks and `N_j` the post-dominance
   candidate count for task `j`.
3. Compute
   `K_load = ceil(8 + 2 L)` and `K_count = ceil(4 + sqrt(N_j))`.
4. Set
   `K_j = min(N_j, 48, max(12, K_load, K_count))`.
5. Sort candidates by switch-adjusted value, success, and physical cost.
6. If the relative value gap between ranks `K_j` and `K_j + 1` is below 0.02,
   expand `K_j` by four and repeat, capped at 48 and `N_j`.
7. Apply the unchanged diversity-retention operator at the resulting `K_j`.

The relative boundary gap is
`(v[K_j] - v[K_j+1]) / max(abs(v[1]), 1e-9)` using one-based ranks.

## Formal replay design

- Public workloads: the 44 frozen Anti-UAV410 and UZH-FPV replay units.
- Nested stochastic replicates: 30 registered seeds per workload.
- Horizon: 12 rolling decisions.
- Response resources: 10 simulated heterogeneous resources.
- Solver: registered bounded branch-and-bound search.
- Node budget: 4,000 for every policy.
- Pairing: identical replay path, seed, state trajectory, value model,
  evaluator, and service endpoint for every candidate policy.
- Independent inferential unit: workload trajectory; the 30 seeds are averaged
  within each workload before paired inference.

## Outcomes

Primary quality outcomes are true-state external objective, independent
service score, independent coverage, and response time on task-periods served
by both compared policies.

Computational outcomes are retained candidate count, retained/full candidate
ratio, visited nodes, fallback rate, node-limit rate, certified fallback gap,
candidate-generation time, solver time, and end-to-end time. Wall-clock
comparisons involving reused historical outputs are descriptive because the
No-Pruning control was produced under a different Python runtime.

Mechanism outcomes include the adaptive target `K`, boundary-gap extensions,
previous-coalition protection, and final feasibility.

## Statistical registration

For each dataset-load stratum and endpoint, the five compressed policies
`K6`, `K12`, `K24`, `K48`, and `AdaptiveK` are compared with `Full` in one
Holm family. Reports separate the unadjusted P value, Holm-adjusted P value,
Cohen's dz, rank-biserial effect, win rate, and the paired mean difference with
a 95% BCa cluster-bootstrap interval. Bounded outcomes use bounded BCa
intervals; binary event rates additionally use Wilson intervals.

## Gate A

High load is defined as Anti-UAV410 loads 6 and 8. `CA-HMCD-RT` is retained as
the real-time candidate policy only if all conditions hold:

1. Its mean service-score gap to Full is at least 20% smaller than the K12 gap.
2. Its mean coverage gap to Full is at least 20% smaller than the K12 gap.
3. Its mean external objective is not lower than K12 by more than 0.002.
4. Its retained candidate count is at most 25% of Full and its visited-node
   count is at most 60% of Full.
5. Its final feasibility rate is 1.0.

If the computational and feasibility conditions hold but only one of the two
service-recovery conditions holds, Gate A is `PROVISIONAL`. Otherwise it is
`FAIL`. The rule is evaluated once after the formal outputs are frozen.


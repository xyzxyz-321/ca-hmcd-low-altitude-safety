# Stage 6 unified statistical analysis

Date: 2026-09-18

## Completion

- Unified registry: **1,592 paired comparisons**.
- Prespecified Holm families: **316**.
- Every principal contrast retains the mean paired difference, 95% interval,
  unadjusted P value, Holm-adjusted P value, Cohen's dz, rank-biserial
  correlation, and favorable win rate when available.
- Bounded outcomes retain Wilson intervals in their original source tables.
- Common-task response time is limited to jointly served task-periods.

## Evidence layers

| Evidence layer | Comparisons | Holm families |
|---|---:|---:|
| controlled mechanism and disturbance validation | 280 | 84 |
| engineering-scenario solver applicability | 220 | 55 |
| public-data replay and registered supplements | 516 | 141 |
| static scale-deadline boundary | 576 | 36 |

## Statistical interpretation

The four evidence layers are registered and reported separately. Public-data
replay addresses task-side transportability, controlled mechanism experiments
address causal module contrasts within simulation, the static boundary study
addresses candidate-construction and deadline behavior, and the engineering
scenario study addresses solver/backend applicability. Results are not pooled
across layers.

The unified analysis supports stability and controlled true-state repair,
supports context-dependent complementarity at the service endpoint, and does
not support an operational redundancy effect. Stage 3 remains a failed real-time
gate (0/64 passing cells). Stage 5 finds no universal method: CA-HMCD-FixedK,
CA-HMCD-Exact, the genetic algorithm, and External-Auction are selected under
different scenario, quality, and deadline criteria.

## Reproducibility

The complete normalized registry is `complete_statistical_registry_1592.csv`. The manuscript-facing
copy is `CA-HMCD_Supplementary_Table_S11_Unified_Statistical_Registry_20260918.csv`. Source hashes and validation checks are recorded in
`stage6_analysis_manifest.json`.

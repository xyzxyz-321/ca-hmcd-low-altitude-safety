# CA-HMCD Stage 4 Protocol

## Scope

Stage 4 validates the registered value-model terms and the true-state safety
repair mechanism under controlled, engineering-shaped low-altitude-safety
scenarios. It is a mechanism-validation stage, not a field-deployment study.

The mechanism arms use a shared candidate pool generated once from the complete
model for each seed and decision period. The same candidate identities,
random-seed trajectory, external evaluator, and node limit are therefore used
across the compared decision models. Greedy and External-Auction are included
as engineering baselines under the same shared pool. This design isolates
value-model effects from candidate-pool construction and solver changes.

## Formal cells

| Study | Strata | Methods | Seeds | Episodes |
|---|---:|---:|---:|---:|
| Engineering transfer and module ablations | 5 | 7 | 30 | 1,050 |
| Complementarity scale sweep | 5 | 2 | 30 | 300 |
| Stability-volatility sweep | 5 | 2 | 30 | 300 |
| Redundancy stress | 1 | 4 | 30 | 120 |
| Perception disturbance and repair | 5 | 2 | 30 | 300 |
| **Total** |  |  |  | **2,070** |

Every episode has 12 decision periods, uses the same 30 registered seeds in
each cell, and forces all configured tasks active when the scenario is
synthetic. The registered coefficients are unchanged from Stage 0/1:
`alpha_cost=0.12`, `beta_time=0.08`, `gamma_unserved=0.20`,
`lambda_redundancy=0.12`, and `lambda_switch=0.10`.

## Statistical families

The analysis creates an explicit Holm registry. Engineering module comparisons
are corrected within each scenario and endpoint across the four ablations.
Engineering baseline comparisons are corrected within each scenario and
endpoint across Greedy and External-Auction. Sweep comparisons are corrected
across registered levels for each endpoint. Repair comparisons exclude the
zero-noise identity control from the inferential family.

The seed trajectory is the independent unit. Primary paired intervals are
deterministic 95% BCa intervals for mean paired differences. Episode-level
bounded quantities additionally receive Wilson score intervals. Unadjusted P,
Holm-adjusted P, effect size, and win rate are retained as separate columns.

## Decision boundaries

The result report distinguishes `SUPPORTED`, `CONTEXT_DEPENDENT`,
`NOT_SUPPORTED`, and `INCONCLUSIVE`. A failed mechanism rule is not repaired by
post-hoc coefficient tuning. In particular, the report may conclude that a
registered term is mathematically present but not operationally supported by
the current coefficient and scenario range.

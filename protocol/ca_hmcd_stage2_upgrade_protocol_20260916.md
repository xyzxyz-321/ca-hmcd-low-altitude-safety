# CA-HMCD Stage-2 Experimental Design Upgrade

Protocol freeze date: 2026-09-16

Model version: `ca-hmcd-stage1-closure-v2.3`

Complete-model evaluator: `complete-v3-stage1-closure`

Service endpoint: `solver-decoupled-service-v2`

## 1. Replication and workload control

- Use 30 paired seeds, 5000--5029, for every formal comparison.
- Use 12 decision periods for the main, ablation, engineering, and parameter
  experiments.
- Use identical true state trajectories for every method under a paired seed.
- Audit both the number and exact IDs of active tasks per period.
- Use 30 seeds and a fixed all-active task load for every scale configuration.

## 2. Fairness controls

- Record true-state fingerprints, active task IDs, model version, evaluator
  version, candidate-pool mode, coefficients, and node budget provenance.
- Compare full methods with method-specific candidate pools.
- Repeat CA-HMCD, External-Auction, and Greedy under a shared complete-model
  candidate pool.
- Separate model effects from solver effects through matched greedy-solver
  controls.
- Retain all failed, repaired, fallback, and low-performing episodes.

## 3. Baselines

- `External-Auction`: market-style external baseline without complementarity
  or redundancy terms.
- `Greedy`: same value model without branch-and-bound.
- `Random`: feasible random-allocation control.
- `Exact-Reference`: complete enumeration on small structured instances.
- `No-Pruning`: exhaustive candidate construction on the balanced control.

## 4. Evaluation hierarchy

- Primary service endpoint: feasibility-gated, solver-decoupled service score.
- Secondary model endpoint: complete-model true-state objective.
- Diagnostic base objective: independent aggregation without complementarity,
  redundancy, or switching.
- Report coverage, common-task response time, physical expenditure,
  feasibility, repair, fallback, runtime, and node counts.
- Report same-type pair ratio, capability overlap, and marginal-gain waste
  independently.

## 5. Engineering validation

Use five normalized engineering-shaped profiles:

- airport corridor;
- energy facility;
- public event;
- urban corridor; and
- industrial zone.

These profiles test structural transfer only and are not represented as field
measurements or deployment validation.

## 6. Robustness and approximation audit

- Scan physical-cost, response-time, switching, redundancy, unserved-risk,
  and complementarity coefficients at low, nominal, and high levels.
- Vary perception noise and dynamic volatility.
- Record resources retained by the compatibility screen, enumerated subsets,
  feasible candidates, dominance removals, fixed-\(K\) removals, frontier
  bounds, retained-pool gaps, fallback gains, and unexpanded nodes.
- Estimate pre-screen and fixed-\(K\) effects only from explicit controls; do
  not attribute the lossless dominance guarantee to either heuristic stage.

## 7. Output directory

Formal rerun outputs are written to:

`ca_hmcd_stage2_upgrade_20260916`

The earlier `ca_hmcd_stage2_experiment` directory is retained as historical
evidence for the pre-closure model and must not be mixed with the upgraded
results.

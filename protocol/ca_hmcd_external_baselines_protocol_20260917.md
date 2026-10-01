# Formal External-Baseline Replay Protocol

## Material Passport

- **Artifact ID**: `CA-HMCD-EXTBASE-20260917`
- **Type**: preregistered code-experiment protocol
- **Status**: frozen before the formal run
- **Date**: 2026-09-17
- **Input registry**: `ca_hmcd_replay_protocol_20260916`
- **Primary comparator**: CA-HMCD from
  `ca_hmcd_stage4_replay_formal_20260916`

## Purpose

This experiment adds two solver-independent baselines requested during
pre-submission review:

1. **HiGHS-MILP**, a standard binary mixed-integer optimization baseline.
2. **Genetic-Algorithm**, a standard evolutionary-search baseline.

Both methods optimize the registered CA-HMCD rolling objective but do not use
CA-HMCD's compatibility screen, dominance pruning, or Top-K diversity
pruning. They therefore provide an optimization reference and an evolutionary
AI reference on the complete feasible candidate pool.

## Frozen Workload

- 44 independent replay workloads.
- 30 paired resource-realization seeds per workload.
- 1,320 registered workload-seed units per method.
- 12 rolling decision periods per unit.
- 10 response resources.
- Task loads of 1, 2, 4, 6, and 8, as frozen in the public replay registry.
- Identical replay files, state fingerprints, task activity, resource
  realizations, model parameters, budget, and true-state safety repair policy
  across compared methods.

## Candidate Pool

For both added baselines:

- all feasible coalitions satisfying activity, availability, type, reachability,
  and response-window conditions are enumerated;
- the candidate cap is 10,000, above the maximum feasible pool for the frozen
  10-resource setting;
- compatibility pre-screening is disabled;
- dominance pruning is disabled;
- Top-K diversity pruning is disabled.

The candidate-generation audit must satisfy, for every formal run,
`feasible_before_dominance = after_dominance = after_diversity`.

## HiGHS-MILP

One binary variable is created for each task-candidate pair. The model enforces:

- at most one coalition per task;
- per-resource capacity;
- the shared physical-cost budget.

The objective exactly linearizes candidate utility, avoided unserved-task
penalty, and the resource-identity switching cost relative to an unserved
decision. The selected solution is independently re-evaluated by the existing
objective implementation. A mismatch larger than `1e-7` aborts the run.

- Interface: `scipy.optimize.milp`
- SciPy: 1.18.1
- HiGHS: 1.12.0
- Integrality tolerance: solver default
- Relative MIP gap target: 0
- Node ceiling: 4,000 per decision period
- Presolve: enabled
- Limit handling: retain the better of the available MILP incumbent and the
  complete-pool greedy warm start; report the dual-bound gap when available.

## Genetic Algorithm

The chromosome contains one categorical gene per task: no service or one
complete-pool candidate index. Capacity and budget feasibility are restored by
a deterministic marginal-value repair. The fitness is the same rolling
decision objective used by HiGHS-MILP.

- Population size: 64.
- Evaluation ceiling: 4,000 fitness evaluations per decision period.
- Initialization: complete-pool greedy solution, feasible previous-period
  allocation, all-unserved solution, and seeded random candidates.
- Parent selection: tournament selection with size 3.
- Crossover: uniform crossover with probability 0.5 per gene.
- Mutation probability: `max(0.08, 1 / number_of_tasks)` per gene.
- Elitism: best two individuals retained per generation.
- Randomness: the registered algorithm-specific decision seed.
- Safeguard: the final solution must not be worse than its complete-pool greedy
  warm start.

## Fairness And Evaluation

The two baselines use the same perceived state for decision-making and the same
true state for post-hoc evaluation. The unified external evaluator recomputes:

- external objective and utility;
- service score and coverage;
- response time on served and commonly served tasks;
- physical cost and resource utilization;
- complementarity and redundancy;
- same-type pair ratio, capability overlap, and marginal-gain waste;
- switching and stability;
- raw feasibility, repair incidence, and final feasibility;
- candidate-generation, solver, evaluation, repair, and end-to-end runtime.

The numerical node/evaluation ceilings are solver-specific work budgets and are
not treated as equivalent elementary operations. Wall-clock runtime and budget
exhaustion are reported separately.

## Statistical Plan

The primary contrasts are CA-HMCD minus each added baseline. Registered seeds
are first averaged within each independent replay workload. Inference is then
performed on paired workload-level differences.

- 95% paired cluster bootstrap confidence intervals.
- Paired Wilcoxon signed-rank tests.
- Separate raw and Holm-adjusted P values.
- Standardized paired effect size and rank-biserial effect.
- Paired win rate with a bounded-data confidence interval.
- Holm families are declared separately for each primary metric and load
  stratum, with the two external-baseline contrasts in each family.

The formal execution must stop if replay validation, state pairing, candidate
pool equality, or final-feasibility audits fail.

# Formal Replay No-Pruning Control Protocol

Date frozen: 2026-09-17

## Objective

Estimate the effect of the CA-HMCD candidate-reduction mechanism under the
formal public-data replay protocol. The comparison is CA-HMCD versus
No-Pruning. No-Pruning keeps the same value model, stability term, branch-and-
bound solver, node limit, true-state safety repair, external evaluator,
resource realization, replay state, and random seed, while disabling both
dominance pruning and fixed-`K` diversity retention.

This is a bounded-search control, not an exhaustive optimality claim. Both
methods retain the frozen branch-and-bound node limit of 4000.

## Frozen execution contract

- Source protocol: `ca_hmcd_replay_protocol_20260916`
- Independent units: 44 public trajectories or preregistered composed
  workloads
- Nested stochastic repeats: 30 seeds per independent unit
- Horizon: 12 decision periods
- Resources: 10
- CA-HMCD retained candidates per task: `K = 12`
- No-Pruning candidate cap: 10000, which exceeds the complete feasible
  coalition count for the registered ten-resource setting
- Formal control runs: 1320
- Pairing keys: workload, seed, and decision period

## Outcomes

Primary performance family within each replay stratum:

- complete external objective;
- solver-decoupled service score;
- final coverage;
- response time on task-periods served by both methods.

Computational family within each replay stratum:

- end-to-end decision time;
- fallback-period proportion;
- node-limit-run indicator;
- fallback optimality-gap bound.

Candidate-mechanism family within each replay stratum:

- feasible candidates before dominance;
- candidates after dominance;
- candidates after diversity retention;
- branch-and-bound nodes.

Feasibility is audited as a required invariant and reported with bounded
cluster-level intervals.

## Statistical plan

The independent inferential unit is the public trajectory or preregistered
composed workload. The 30 seeds are averaged within each independent unit
before paired inference. For each stratum and endpoint, report the paired mean
difference (CA-HMCD minus No-Pruning), a deterministic 95% BCa paired-cluster
bootstrap interval, a two-sided paired Wilcoxon signed-rank test, paired
Cohen's `d_z`, rank-biserial correlation, and the favorable win rate. Holm
step-down correction is applied separately to the explicitly listed
performance, computational, and candidate-mechanism families within each
stratum. Bounded proportions receive bounded BCa intervals; binary
cluster-prevalence outcomes receive Wilson intervals.

The direction of the performance difference is not assumed. Lower runtime,
fallback, optimality-gap bound, and response time are favorable; higher
objective, service score, coverage, and feasibility are favorable. Candidate
counts and node counts are descriptive mechanism outcomes.

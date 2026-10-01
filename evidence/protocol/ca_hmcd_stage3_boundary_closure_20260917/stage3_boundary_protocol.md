# Stage 3 protocol: exact-mode and real-time-mode boundary

## Purpose

This stage evaluates the computational boundary between the complete-candidate
HiGHS mode and a fixed-K interruptible mode. Stage 2 Gate A failed; therefore,
the adaptive-K profile is excluded from real-time claims. The tested real-time
candidate is the existing fixed `K=12` policy coupled to a wall-clock
interruptible retained-pool search.

## Registered design

- Scales: 10, 16, 24, and 32 resources crossed with 4, 8, 12, and 16
  simultaneously active tasks.
- Replication: the same 30 seeds are used in every scale cell.
- Deadlines: 50, 100, 200, and 500 ms.
- Independent unit: one deterministic static decision snapshot.
- Total execution: 480 exact runs and 1,920 bounded-K12 runs.
- Execution is sequential after one excluded warm-up to avoid contention from
  parallel workers.

The static design isolates candidate-generation and optimization scaling. It
does not replace the rolling replay experiments used for service and
engineering validation.

## Compared modes

`CA-HMCD-Exact` enumerates the complete feasible candidate pool and calls
SciPy/HiGHS. HiGHS is capped at 5,000 ms per solve to keep the experiment
bounded. A run is used as an exact quality reference only when HiGHS reports an
optimal solution.

`CA-HMCD-BoundedK12-Candidate` uses the registered Stage-9 fixed-K candidate
policy and an interruptible branch-and-bound search initialized by a feasible
greedy allocation. Candidate generation consumes the registered deadline; the
search receives only the remaining time.

## Timing boundary

Decision time begins immediately before candidate generation and ends when the
allocation solver returns. It includes candidate enumeration, screening,
dominance filtering, diversity retention, matrix construction, and
optimization/search. It excludes state acquisition, the experiment-only
external evaluator, file output, and statistics. A deadline is met only when
the measured decision time is no greater than the registered limit; no timing
tolerance is added.

## Gate B

A scale-deadline cell passes only when all 30 exact references are optimal, the
exact deadline-hit rate is below 95%, the bounded-K12 hit rate is at least 95%,
bounded-K12 feasibility is 100%, and its mean losses do not exceed 0.03 in
service score, 0.05 in coverage, or 0.02 in complete-model objective per active
task. At least one passing cell is required for Gate B to pass.

A passing result validates only the fixed-K12 bounded mode in the reported
cells. It does not validate adaptive K, and it does not imply that bounded
search is preferable where complete-pool HiGHS meets the same deadline.

## Statistical reporting

Each main paired difference is reported with a 95% paired BCa interval, raw and
Holm-adjusted P values, Cohen's dz, rank-biserial correlation, and paired win
rate. Deadline and feasibility proportions receive Wilson intervals. Holm
families are defined separately for each endpoint and deadline over the 16
registered scale cells.

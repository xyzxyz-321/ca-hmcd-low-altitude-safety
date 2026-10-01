# CA-HMCD Stage 5 Protocol

## Purpose

Stage 5 closes the external-baseline question and freezes the applicability
boundary of the proposed framework. It does not seek a universally favorable
ranking.

The central distinction is between the value model and its solver profile.
`HiGHS-MILP` optimizes the same frozen CA-HMCD value model over the complete
candidate space and is therefore reported as **CA-HMCD-Exact (HiGHS
backend)**. The existing `CA-HMCD` implementation is reported as
**CA-HMCD-FixedK**, using K=12 candidate compression and bounded
branch-and-bound. Genetic Algorithm, Greedy, and External Auction remain
external algorithmic baselines.

## Formal design

- Five engineering-shaped low-altitude-safety scenarios
- Five methods
- Thirty paired seeds per scenario
- Twelve decision periods per seed
- 750 episodes and 9,000 period-level observations
- One locked Python 3.12/SciPy environment
- One frozen model, external evaluator, and independent service endpoint

Candidate policies are part of each registered method profile. This is an
end-to-end comparison, so candidate construction time is included in the
reported runtime.

## Inference and decision output

Every primary contrast reports the paired mean difference, 95% BCa interval,
raw P value, Holm-adjusted P value, Cohen dz, rank-biserial correlation, and
win rate. Response time is restricted to jointly served task-periods.

The final output is not a league table. It is a method-selection matrix:

1. exact quality when sufficient computation is available;
2. bounded-computation operation when a quality trade-off is acceptable;
3. lightweight fallback when latency dominates;
4. stability and true-state feasibility safeguards established in Stage 4;
5. unsupported claims that must be removed from the manuscript.

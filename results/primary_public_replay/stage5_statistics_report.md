# CA-HMCD Stage 5 Statistical Analysis Report

## Analysis design

- Independent unit: original UZH-FPV trajectory or preregistered Anti-UAV410 composed workload.
- Nested repeats: 30 paired random seeds per independent unit; seeds were averaged before inference.
- Paired uncertainty: 95% BCa cluster-bootstrap intervals with 10000 resamples.
- Test: two-sided paired Wilcoxon signed-rank test across independent clusters.
- Multiplicity: Holm correction within each endpoint-by-stratum family of five algorithm contrasts.
- Bounded intervals: bounded BCa cluster bootstrap with 5000 resamples; binary cluster prevalence used Wilson score intervals.

## Sample sizes

| Stratum | Independent clusters | Seeds per cluster |
|---|---:|---:|
| Anti-UAV410 load 2 | 10 | 30 |
| Anti-UAV410 load 4 | 8 | 30 |
| Anti-UAV410 load 6 | 6 | 30 |
| Anti-UAV410 load 8 | 4 | 30 |
| UZH-FPV single-target replay | 16 | 30 |

## Registered families

- 60 Holm families and 300 paired comparisons were evaluated.
- 101 comparisons had Holm-adjusted P<0.05.
- Raw P values, adjusted P values, paired Cohen's dz, rank-biserial correlations, and favorable-direction win rates are stored in separate columns.
- BCa intervals estimate mean paired cluster differences, while Wilcoxon/Holm values define the registered family-wise test decision; the two are not interchangeable at small n.

## Main high-load comparisons

- Anti-UAV410 load 6: CA-HMCD vs Greedy for independent_service_score gave a paired cluster difference of 0.0135 (95% CI 0.0093 to 0.0206; unadjusted P=0.0312; Holm-adjusted P=0.1562; dz=1.745; rank-biserial=1.000; favorable win rate=100.0%; n=6 clusters).
- Anti-UAV410 load 6: CA-HMCD vs External-Auction for independent_service_score gave a paired cluster difference of 0.0112 (95% CI 0.0066 to 0.0186; unadjusted P=0.0312; Holm-adjusted P=0.1562; dz=1.407; rank-biserial=1.000; favorable win rate=100.0%; n=6 clusters).
- Anti-UAV410 load 6: CA-HMCD vs Greedy for independent_coverage gave a paired cluster difference of 0.0536 (95% CI 0.0397 to 0.0751; unadjusted P=0.0312; Holm-adjusted P=0.1562; dz=2.242; rank-biserial=1.000; favorable win rate=100.0%; n=6 clusters).
- Anti-UAV410 load 6: CA-HMCD vs External-Auction for independent_coverage gave a paired cluster difference of 0.0515 (95% CI 0.0390 to 0.0804; unadjusted P=0.0312; Holm-adjusted P=0.1562; dz=2.037; rank-biserial=1.000; favorable win rate=100.0%; n=6 clusters).
- Anti-UAV410 load 6: CA-HMCD vs No-Synergy for external_objective gave a paired cluster difference of -0.0015 (95% CI -0.0043 to 0.000777; unadjusted P=0.4375; Holm-adjusted P=0.4375; dz=-0.449; rank-biserial=-0.429; favorable win rate=33.3%; n=6 clusters).
- Anti-UAV410 load 6: CA-HMCD vs No-Stability for external_objective gave a paired cluster difference of 0.4268 (95% CI 0.3594 to 0.4585; unadjusted P=0.0312; Holm-adjusted P=0.1562; dz=7.025; rank-biserial=1.000; favorable win rate=100.0%; n=6 clusters).
- Anti-UAV410 load 8: CA-HMCD vs Greedy for independent_service_score gave a paired cluster difference of 0.0217 (95% CI 0.0144 to 0.0289; unadjusted P=0.1250; Holm-adjusted P=0.6250; dz=2.470; rank-biserial=1.000; favorable win rate=100.0%; n=4 clusters).
- Anti-UAV410 load 8: CA-HMCD vs External-Auction for independent_service_score gave a paired cluster difference of 0.0215 (95% CI 0.0151 to 0.0301; unadjusted P=0.1250; Holm-adjusted P=0.6250; dz=2.555; rank-biserial=1.000; favorable win rate=100.0%; n=4 clusters).
- Anti-UAV410 load 8: CA-HMCD vs Greedy for independent_coverage gave a paired cluster difference of 0.0722 (95% CI 0.0491 to 0.0953; unadjusted P=0.1250; Holm-adjusted P=0.6250; dz=2.648; rank-biserial=1.000; favorable win rate=100.0%; n=4 clusters).
- Anti-UAV410 load 8: CA-HMCD vs External-Auction for independent_coverage gave a paired cluster difference of 0.0720 (95% CI 0.0526 to 0.0983; unadjusted P=0.1250; Holm-adjusted P=0.6250; dz=2.825; rank-biserial=1.000; favorable win rate=100.0%; n=4 clusters).
- Anti-UAV410 load 8: CA-HMCD vs No-Synergy for external_objective gave a paired cluster difference of -0.0024 (95% CI -0.0036 to -0.0016; unadjusted P=0.1250; Holm-adjusted P=0.6250; dz=-1.938; rank-biserial=-1.000; favorable win rate=0.0%; n=4 clusters).
- Anti-UAV410 load 8: CA-HMCD vs No-Stability for external_objective gave a paired cluster difference of 0.4993 (95% CI 0.4758 to 0.5370; unadjusted P=0.1250; Holm-adjusted P=0.6250; dz=13.824; rank-biserial=1.000; favorable win rate=100.0%; n=4 clusters).
- None of the displayed load-6/load-8 comparisons reached Holm-adjusted P<0.05 (0 of 12). Their estimates must not be described as Holm-confirmed superiority.

## Common-task response time

- Anti-UAV410 load 2: CA-HMCD vs External-Auction for common_task_response_time gave a paired cluster difference of -0.0013 (95% CI -0.0022 to -0.000546; unadjusted P=0.0078; Holm-adjusted P=0.0234; dz=-0.880; rank-biserial=-0.956; favorable win rate=85.0%; n=10 clusters).
- Anti-UAV410 load 4: CA-HMCD vs External-Auction for common_task_response_time gave a paired cluster difference of -0.0051 (95% CI -0.0078 to -0.0033; unadjusted P=0.0078; Holm-adjusted P=0.0391; dz=-1.516; rank-biserial=-1.000; favorable win rate=100.0%; n=8 clusters).
- Anti-UAV410 load 6: CA-HMCD vs External-Auction for common_task_response_time gave a paired cluster difference of -0.0058 (95% CI -0.0077 to -0.0038; unadjusted P=0.0312; Holm-adjusted P=0.1562; dz=-2.156; rank-biserial=-1.000; favorable win rate=100.0%; n=6 clusters).
- Anti-UAV410 load 8: CA-HMCD vs External-Auction for common_task_response_time gave a paired cluster difference of -0.00086 (95% CI -0.0024 to 0.000137; unadjusted P=0.6250; Holm-adjusted P=1.0000; dz=-0.551; rank-biserial=-0.400; favorable win rate=75.0%; n=4 clusters).
- UZH-FPV single-target replay: CA-HMCD vs External-Auction for common_task_response_time gave a paired cluster difference of -1.9e-05 (95% CI -7.03e-05 to 2.33e-05; unadjusted P=0.6875; Holm-adjusted P=1.0000; dz=-0.191; rank-biserial=-0.214; favorable win rate=53.1%; n=16 clusters).

## Computational boundary

- Anti-UAV410 load 2: the cluster-averaged CA-HMCD run-level fallback rate was 0.0% (bounded 95% CI 0.0% to 0.0%); 0.0% of independent clusters contained at least one fallback event (Wilson 95% CI 0.0% to 27.8%).
- Anti-UAV410 load 4: the cluster-averaged CA-HMCD run-level fallback rate was 0.0% (bounded 95% CI 0.0% to 0.0%); 0.0% of independent clusters contained at least one fallback event (Wilson 95% CI 0.0% to 32.4%).
- Anti-UAV410 load 6: the cluster-averaged CA-HMCD run-level fallback rate was 76.7% (bounded 95% CI 62.2% to 87.8%); 100.0% of independent clusters contained at least one fallback event (Wilson 95% CI 61.0% to 100.0%).
- Anti-UAV410 load 8: the cluster-averaged CA-HMCD run-level fallback rate was 100.0% (bounded 95% CI 100.0% to 100.0%); 100.0% of independent clusters contained at least one fallback event (Wilson 95% CI 51.0% to 100.0%).

## Interpretation limits

- Anti-UAV410 load 8 contains only four independent composed workloads. Exact paired Wilcoxon tests at this n have low resolution; absence of Holm-adjusted significance must not be interpreted as evidence of equivalence.
- Several BCa intervals exclude zero while the corresponding Holm-adjusted Wilcoxon P value is at least 0.05. This reflects different estimands plus discrete small-n rank tests; the registered Holm decision, not interval exclusion alone, governs significance wording.
- For a five-comparison Holm family at load 8, the approximate minimum detectable paired dz at 80% power under the conservative Bonferroni first-step bound is 1.71.
- The common-task response endpoint is conditional on both methods serving the task-period and must be interpreted with coverage.
- Public data define task-side motion or observation patterns; response resources and outcomes remain simulated.
- No repair event occurred in the formal replay, so the repair mechanism has no comparative inferential evidence in this dataset.

## Reproducibility

- Protocol fingerprint: `b0043867b0802dbabaf4183e6e204bfaddcc4a1aceb8197831096e87500fdcea`
- Code fingerprint: `e45abc5d899cb97f7513c79fb3e0090092fc5a18bbac729f9cc552fd7aa085a1`
- Python: 3.14.0a1

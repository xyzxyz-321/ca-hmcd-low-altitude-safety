# Statistical analysis

The independent inferential unit was an original UZH-FPV flight sequence or a preregistered Anti-UAV410 composed workload. Thirty paired random seeds were nested within each unit and were averaged before inference; individual decision periods, tasks, and stochastic repeats were not treated as independent observations.

For each dataset and task-load stratum, CA-HMCD was compared with Greedy, External-Auction, No-Synergy, No-Stability, and Random using two-sided paired Wilcoxon signed-rank tests across independent clusters. Effect reporting included the mean paired difference (CA-HMCD minus comparator), a 95% bias-corrected and accelerated cluster-bootstrap confidence interval (10000 resamples), paired Cohen's dz, rank-biserial correlation, and the cluster win rate in the prespecified favorable direction.

Holm step-down correction was applied separately within each prespecified family comprising the five algorithm contrasts for one endpoint in one dataset/load stratum. Unadjusted and Holm-adjusted P values are reported separately. Higher values were favorable for the external objective, service score, and coverage; lower values were favorable for switching, physical cost, redundancy diagnostics, runtime, fallback, and response time.

The BCa interval estimates the mean paired cluster difference, whereas the Wilcoxon test evaluates signed ranks and the Holm procedure defines the registered family-wise decision rule. These quantities may differ at small cluster counts; a confidence interval excluding zero was not described as Holm-confirmed significance when the adjusted P value was at least 0.05.

Coverage and other continuous metrics bounded to [0,1] were summarized with bounded BCa intervals over cluster means (5000 resamples). The prevalence of clusters containing any repair, fallback, node-limit event, or final infeasibility was summarized using Wilson score intervals.

Response-time comparisons were restricted to task-periods served by both methods. Response times were averaged first within each random seed and then within each independent cluster before paired inference. This conditional endpoint was interpreted together with coverage and was not used as a substitute for service availability.

Analyses were implemented in Python 3.14.0a1 using the standard library and deterministic bootstrap seeds. Exact P values are reported where computationally available; P<0.05 after the registered Holm correction was used as the inferential threshold.

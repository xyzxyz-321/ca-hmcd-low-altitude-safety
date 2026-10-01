# Stage 5 Sample-Size Basis

The public trajectory or preregistered composed workload is the independent unit. Thirty seeds within each workload reduce Monte Carlo noise but do not increase inferential n.

The table gives an approximate paired Cohen's dz detectable under a two-sided family-wise alpha of 0.05, using the conservative Bonferroni first-step bound for a five-member Holm family. It is a sensitivity calculation, not a post-hoc proof of power.

| Independent clusters | Nested seeds per cluster | Approx. dz at 80% power | Approx. dz at 90% power |
|---:|---:|---:|---:|
| 4 | 30 | 1.709 | 1.929 |
| 6 | 30 | 1.395 | 1.575 |
| 8 | 30 | 1.208 | 1.364 |
| 10 | 30 | 1.081 | 1.220 |
| 16 | 30 | 0.854 | 0.964 |

Accordingly, the load-8 stratum (n=4) can only provide reliable family-wise detection for very large paired effects. The load-6 stratum (n=6) remains underpowered for moderate effects after five-comparison multiplicity control. Larger numbers of independent public trajectories or composed workloads, rather than additional random seeds on the same workloads, are needed to improve inferential resolution.

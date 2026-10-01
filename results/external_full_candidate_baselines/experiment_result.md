# Formal optimization and evolutionary baseline experiment

## Execution

- Completed 1320 registered workload-seed units for each of HiGHS-MILP and Genetic-Algorithm.
- Both added baselines used the complete feasible candidate pool, the registered rolling value model, the same true-state external evaluator, and the same safety-repair policy.
- HiGHS-MILP used a 4000-node ceiling. Genetic-Algorithm used 4000 fitness evaluations with a complete-pool greedy warm start.
- CA-HMCD was reused from the prior formal session after protocol, model/evaluator version, replay hash, per-period state, and candidate-count pairing audits.

## Audit

- PASS: environment-matched reference mode (observed=formal-reference, expected=formal-reference).
- PASS: external-baseline formal mode (observed=formal-external, expected=formal-external).
- PASS: reference execution audit (observed=True, expected=True).
- PASS: external-baseline execution audit (observed=True, expected=True).
- PASS: registered external algorithms (observed=['HiGHS-MILP', 'Genetic-Algorithm'], expected=['HiGHS-MILP', 'Genetic-Algorithm']).
- PASS: same frozen protocol fingerprint (observed=b0043867b0802dbabaf4183e6e204bfaddcc4a1aceb8197831096e87500fdcea, expected=b0043867b0802dbabaf4183e6e204bfaddcc4a1aceb8197831096e87500fdcea).
- PASS: same model version (observed=ca-hmcd-stage1-closure-v2.3, expected=ca-hmcd-stage1-closure-v2.3).
- PASS: same external evaluator version (observed=complete-v3-stage1-closure, expected=complete-v3-stage1-closure).
- PASS: same service endpoint version (observed=solver-decoupled-service-v2, expected=solver-decoupled-service-v2).
- PASS: same simulation implementation (observed=b6fae78029a59e65c6a49fe25cd11e4d2fa9d5c912ced55ad9b8a4b92c126ef1, expected=b6fae78029a59e65c6a49fe25cd11e4d2fa9d5c912ced55ad9b8a4b92c126ef1).
- PASS: same Python execution environment (observed=3.12.14 (main, Aug 25 2026, 14:01:42) [MSC v.1944 64 bit (AMD64)], expected=3.12.14 (main, Aug 25 2026, 14:01:42) [MSC v.1944 64 bit (AMD64)]).
- PASS: complete external registered rows (observed=1320, expected=1320).
- PASS: matched CA-HMCD reproduces prior non-timing outcomes (observed=0.0, expected=maximum absolute difference <= 1e-12).
- PASS: complete paired episode keys (observed=3960, expected=3960).
- PASS: HiGHS-MILP disables dominance pruning (observed=1, expected=1).
- PASS: HiGHS-MILP disables diversity pruning (observed=1, expected=1).
- PASS: HiGHS-MILP uses the same feasible candidate count (observed=paired workload-seed mean, expected=equal to CA-HMCD before pruning).
- PASS: Genetic-Algorithm disables dominance pruning (observed=1, expected=1).
- PASS: Genetic-Algorithm disables diversity pruning (observed=1, expected=1).
- PASS: Genetic-Algorithm uses the same feasible candidate count (observed=paired workload-seed mean, expected=equal to CA-HMCD before pruning).
- PASS: registered run units are unique (observed=1320, expected=1320).
- PASS: episode result count is complete (observed=3960, expected=3960).
- PASS: every episode tuple occurs once (observed=1, expected=1).
- PASS: per-step result count is complete (observed=47520, expected=47520).
- PASS: paired audit group count is complete (observed=15840, expected=15840).
- PASS: all paired state audits pass (observed=15840, expected=15840).
- PASS: all final allocations are feasible (observed=1.0, expected=1.0).
- PASS: all per-period state audits pass (observed=15840, expected=15840).

## Primary paired results

- Anti-UAV410 load 2, external_objective, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- Anti-UAV410 load 2, external_objective, CA-HMCD vs Genetic-Algorithm: mean difference = -0.000278052 [95% CI -0.00100363, 0.000129883], raw P=0.5625, Holm P=1, dz=-0.307, win rate=50.0%.
- Anti-UAV410 load 2, independent_service_score, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- Anti-UAV410 load 2, independent_service_score, CA-HMCD vs Genetic-Algorithm: mean difference = -0.000134389 [95% CI -0.000787295, 0.000258543], raw P=1, Holm P=1, dz=-0.159, win rate=60.0%.
- Anti-UAV410 load 2, independent_coverage, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- Anti-UAV410 load 2, independent_coverage, CA-HMCD vs Genetic-Algorithm: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- Anti-UAV410 load 4, external_objective, CA-HMCD vs HiGHS-MILP: mean difference = -0.000241116 [95% CI -0.000828762, 0.000115801], raw P=0.5625, Holm P=0.5625, dz=-0.341, win rate=37.5%.
- Anti-UAV410 load 4, external_objective, CA-HMCD vs Genetic-Algorithm: mean difference = 0.0021603 [95% CI 0.000256386, 0.0055796], raw P=0.1953, Holm P=0.3906, dz=0.565, win rate=75.0%.
- Anti-UAV410 load 4, independent_service_score, CA-HMCD vs HiGHS-MILP: mean difference = -0.000790797 [95% CI -0.00133342, -0.000360487], raw P=0.03125, Holm P=0.0625, dz=-1.065, win rate=12.5%.
- Anti-UAV410 load 4, independent_service_score, CA-HMCD vs Genetic-Algorithm: mean difference = -0.000783236 [95% CI -0.00139122, 0.000333458], raw P=0.1484, Holm P=0.1484, dz=-0.642, win rate=12.5%.
- Anti-UAV410 load 4, independent_coverage, CA-HMCD vs HiGHS-MILP: mean difference = -0.0020544 [95% CI -0.00350116, -0.00078125], raw P=0.03125, Holm P=0.0625, dz=-0.993, win rate=12.5%.
- Anti-UAV410 load 4, independent_coverage, CA-HMCD vs Genetic-Algorithm: mean difference = -0.000347222 [95% CI -0.0020544, 0.00150463], raw P=0.8438, Holm P=0.8438, dz=-0.127, win rate=50.0%.
- Anti-UAV410 load 6, external_objective, CA-HMCD vs HiGHS-MILP: mean difference = -0.0290312 [95% CI -0.0495707, -0.0176386], raw P=0.03125, Holm P=0.0625, dz=-1.429, win rate=0.0%.
- Anti-UAV410 load 6, external_objective, CA-HMCD vs Genetic-Algorithm: mean difference = -0.0246039 [95% CI -0.0424125, -0.0130647], raw P=0.03125, Holm P=0.0625, dz=-1.300, win rate=0.0%.
- Anti-UAV410 load 6, independent_service_score, CA-HMCD vs HiGHS-MILP: mean difference = -0.0168268 [95% CI -0.0230128, -0.0114666], raw P=0.03125, Holm P=0.0625, dz=-2.121, win rate=0.0%.
- Anti-UAV410 load 6, independent_service_score, CA-HMCD vs Genetic-Algorithm: mean difference = -0.0166276 [95% CI -0.0235592, -0.00886816], raw P=0.03125, Holm P=0.0625, dz=-1.663, win rate=0.0%.
- Anti-UAV410 load 6, independent_coverage, CA-HMCD vs HiGHS-MILP: mean difference = -0.0452238 [95% CI -0.061088, -0.0311265], raw P=0.03125, Holm P=0.0625, dz=-2.214, win rate=0.0%.
- Anti-UAV410 load 6, independent_coverage, CA-HMCD vs Genetic-Algorithm: mean difference = -0.0398534 [95% CI -0.0581944, -0.0227778], raw P=0.03125, Holm P=0.0625, dz=-1.653, win rate=0.0%.
- Anti-UAV410 load 8, external_objective, CA-HMCD vs HiGHS-MILP: mean difference = -0.0634669 [95% CI -0.0845194, -0.0424144], raw P=0.125, Holm P=0.25, dz=-2.386, win rate=0.0%.
- Anti-UAV410 load 8, external_objective, CA-HMCD vs Genetic-Algorithm: mean difference = -0.0477616 [95% CI -0.0681, -0.0372586], raw P=0.125, Holm P=0.25, dz=-2.398, win rate=0.0%.
- Anti-UAV410 load 8, independent_service_score, CA-HMCD vs HiGHS-MILP: mean difference = -0.0279571 [95% CI -0.0339092, -0.0234356], raw P=0.125, Holm P=0.25, dz=-4.884, win rate=0.0%.
- Anti-UAV410 load 8, independent_service_score, CA-HMCD vs Genetic-Algorithm: mean difference = -0.0254071 [95% CI -0.0301413, -0.022491], raw P=0.125, Holm P=0.25, dz=-5.428, win rate=0.0%.
- Anti-UAV410 load 8, independent_coverage, CA-HMCD vs HiGHS-MILP: mean difference = -0.0861491 [95% CI -0.103483, -0.0722636], raw P=0.125, Holm P=0.25, dz=-5.066, win rate=0.0%.
- Anti-UAV410 load 8, independent_coverage, CA-HMCD vs Genetic-Algorithm: mean difference = -0.078626 [95% CI -0.0920246, -0.0686756], raw P=0.125, Holm P=0.25, dz=-6.131, win rate=0.0%.
- UZH-FPV single-target replay, external_objective, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- UZH-FPV single-target replay, external_objective, CA-HMCD vs Genetic-Algorithm: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- UZH-FPV single-target replay, independent_service_score, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- UZH-FPV single-target replay, independent_service_score, CA-HMCD vs Genetic-Algorithm: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- UZH-FPV single-target replay, independent_coverage, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- UZH-FPV single-target replay, independent_coverage, CA-HMCD vs Genetic-Algorithm: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- Anti-UAV410 load 2, common_task_response_time, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- Anti-UAV410 load 2, common_task_response_time, CA-HMCD vs Genetic-Algorithm: mean difference = -0.000225599 [95% CI -0.000950337, 1.03741e-05], raw P=0.6875, Holm P=1, dz=-0.358, win rate=50.0%.
- Anti-UAV410 load 4, common_task_response_time, CA-HMCD vs HiGHS-MILP: mean difference = -2.63451e-05 [95% CI -7.29407e-05, -1.03805e-06], raw P=0.25, Holm P=0.25, dz=-0.505, win rate=68.8%.
- Anti-UAV410 load 4, common_task_response_time, CA-HMCD vs Genetic-Algorithm: mean difference = -0.00124418 [95% CI -0.00199481, -0.000600343], raw P=0.007812, Holm P=0.01562, dz=-1.150, win rate=100.0%.
- Anti-UAV410 load 6, common_task_response_time, CA-HMCD vs HiGHS-MILP: mean difference = -0.00441996 [95% CI -0.006285, -0.00187633], raw P=0.03125, Holm P=0.0625, dz=-1.507, win rate=100.0%.
- Anti-UAV410 load 6, common_task_response_time, CA-HMCD vs Genetic-Algorithm: mean difference = -0.00769963 [95% CI -0.0100513, -0.0028684], raw P=0.0625, Holm P=0.0625, dz=-1.763, win rate=83.3%.
- Anti-UAV410 load 8, common_task_response_time, CA-HMCD vs HiGHS-MILP: mean difference = -0.00562643 [95% CI -0.00777885, -0.00425258], raw P=0.125, Holm P=0.25, dz=-2.669, win rate=100.0%.
- Anti-UAV410 load 8, common_task_response_time, CA-HMCD vs Genetic-Algorithm: mean difference = -0.00831814 [95% CI -0.0117134, -0.00615992], raw P=0.125, Holm P=0.25, dz=-2.409, win rate=100.0%.
- UZH-FPV single-target replay, common_task_response_time, CA-HMCD vs HiGHS-MILP: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.
- UZH-FPV single-target replay, common_task_response_time, CA-HMCD vs Genetic-Algorithm: mean difference = 0 [95% CI 0, 0], raw P=1, Holm P=1, dz=0.000, win rate=50.0%.

## Interpretation boundary

HiGHS-MILP is a standard mathematical-optimization reference and Genetic-Algorithm is a standard evolutionary-search reference implemented against the registered interface. The latter is not presented as a reproduction of a named application-specific published model.
Wall-clock values were obtained in separate formal sessions; paired service and objective results are deterministic under the registry, while runtime comparisons retain ordinary session-level system noise.

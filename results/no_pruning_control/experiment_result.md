# Formal replay No-Pruning control

## Execution

- Completed 1320 No-Pruning runs over all 44 registered replay units and 30 nested seeds.
- CA-HMCD baseline records were reused only after protocol, simulation-code, replay-code, model-version, evaluator-version, replay-hash, and per-step true-state fingerprints were audited.
- The control disables dominance pruning and fixed-K diversity retention; all other model, solver, budget, evaluator, and repair settings remain matched.
- CA-HMCD and No-Pruning were executed in adjacent formal sessions on the same workstation. Objective and service endpoints are deterministically paired; wall-clock runtime differences should also allow for residual session-level system noise.

## Audit

- PASS: baseline formal mode (observed=formal, expected=formal).
- PASS: control formal-control mode (observed=formal-control, expected=formal-control).
- PASS: baseline execution audit (observed=True, expected=True).
- PASS: control execution audit (observed=True, expected=True).
- PASS: control algorithm registration (observed=['No-Pruning'], expected=['No-Pruning']).
- PASS: same frozen protocol fingerprint (observed=b0043867b0802dbabaf4183e6e204bfaddcc4a1aceb8197831096e87500fdcea, expected=b0043867b0802dbabaf4183e6e204bfaddcc4a1aceb8197831096e87500fdcea).
- PASS: same replay mapping code (observed=41052a5d71bd770ff16e804399c322523a3a2db2ef39d5a41f0957b35e2922d5, expected=41052a5d71bd770ff16e804399c322523a3a2db2ef39d5a41f0957b35e2922d5).
- PASS: same simulation and value-model code (observed=74f4dc965e6d1c591b369f803eb0d9c4cf9de13d286c47f98086cce0a75baef2, expected=74f4dc965e6d1c591b369f803eb0d9c4cf9de13d286c47f98086cce0a75baef2).
- PASS: control registered row count (observed=1320, expected=1320).
- PASS: complete paired episode keys (observed=2640, expected=2640).
- PASS: No-Pruning disables dominance removal (observed=0.0, expected=0.0).
- PASS: No-Pruning disables diversity removal (observed=0.0, expected=0.0).
- PASS: same pre-pruning feasible candidate count (observed=paired workload-seed mean, expected=equal).
- PASS: registered run units are unique (observed=1320, expected=1320).
- PASS: episode result count is complete (observed=2640, expected=2640).
- PASS: every episode tuple occurs once (observed=1, expected=1).
- PASS: per-step result count is complete (observed=31680, expected=31680).
- PASS: paired audit group count is complete (observed=15840, expected=15840).
- PASS: all paired state audits pass (observed=15840, expected=15840).
- PASS: all final allocations are feasible (observed=1.0, expected=1.0).
- PASS: all final allocations feasible (observed=1.0, expected=1.0).

## Main findings

- The feasible candidate count before dominance and the count after dominance were identical in every replay stratum. The current dominance rule therefore made no empirical reduction in this formal replay; candidate compression came entirely from fixed-K diversity retention.
- Diversity retention reduced the mean per-episode candidate pool by about 92% across all strata and sharply reduced branch-and-bound node expansion.
- No-Pruning reached the 4000-node boundary in nearly all Anti-UAV410 runs and had larger fallback proportions and optimality-gap bounds. This supports pruning as a search-control mechanism.
- The control does not support a blanket claim that pruning improves allocation quality. Under loads 6 and 8, No-Pruning showed higher solver-decoupled service and coverage, whereas CA-HMCD had faster common-task responses. These are quality-complexity trade-offs under the shared node budget.
- UZH-FPV single-target allocation quality was identical. Here, pruning changed computation but not the selected allocation.

## Primary paired results

- Anti-UAV410 load 2, external_objective: CA-HMCD - No-Pruning = -0.000447591 [95% CI -0.00161015, 0], Holm-adjusted P=1, dz=-0.394, favorable win rate=40.0%.
- Anti-UAV410 load 2, independent_service_score: CA-HMCD - No-Pruning = -0.000313903 [95% CI -0.00150867, 0.00012653], Holm-adjusted P=1, dz=-0.255, favorable win rate=50.0%.
- Anti-UAV410 load 2, independent_coverage: CA-HMCD - No-Pruning = 0 [95% CI 0, 0], Holm-adjusted P=1, dz=0.000, favorable win rate=50.0%.
- Anti-UAV410 load 4, external_objective: CA-HMCD - No-Pruning = 0.00883473 [95% CI 0.00407394, 0.0144286], Holm-adjusted P=0.07031, dz=1.088, favorable win rate=87.5%.
- Anti-UAV410 load 4, independent_service_score: CA-HMCD - No-Pruning = -0.00260616 [95% CI -0.00665504, -0.00101271], Holm-adjusted P=0.07812, dz=-0.686, favorable win rate=25.0%.
- Anti-UAV410 load 4, independent_coverage: CA-HMCD - No-Pruning = 0.00083912 [95% CI -0.00135995, 0.00483218], Holm-adjusted P=0.8438, dz=0.185, favorable win rate=62.5%.
- Anti-UAV410 load 6, external_objective: CA-HMCD - No-Pruning = -0.00549954 [95% CI -0.0156674, 0.0101714], Holm-adjusted P=0.5625, dz=-0.326, favorable win rate=33.3%.
- Anti-UAV410 load 6, independent_service_score: CA-HMCD - No-Pruning = -0.018066 [95% CI -0.0256065, -0.0103242], Holm-adjusted P=0.125, dz=-1.708, favorable win rate=0.0%.
- Anti-UAV410 load 6, independent_coverage: CA-HMCD - No-Pruning = -0.0377392 [95% CI -0.0550772, -0.0206559], Holm-adjusted P=0.125, dz=-1.568, favorable win rate=0.0%.
- Anti-UAV410 load 8, external_objective: CA-HMCD - No-Pruning = -0.0241932 [95% CI -0.0301057, -0.0136516], Holm-adjusted P=0.5, dz=-2.365, favorable win rate=0.0%.
- Anti-UAV410 load 8, independent_service_score: CA-HMCD - No-Pruning = -0.0255479 [95% CI -0.0280508, -0.0223543], Holm-adjusted P=0.5, dz=-8.215, favorable win rate=0.0%.
- Anti-UAV410 load 8, independent_coverage: CA-HMCD - No-Pruning = -0.0732068 [95% CI -0.0806143, -0.0682622], Holm-adjusted P=0.5, dz=-10.391, favorable win rate=0.0%.
- UZH-FPV single-target replay, external_objective: CA-HMCD - No-Pruning = 0 [95% CI 0, 0], Holm-adjusted P=1, dz=0.000, favorable win rate=50.0%.
- UZH-FPV single-target replay, independent_service_score: CA-HMCD - No-Pruning = 0 [95% CI 0, 0], Holm-adjusted P=1, dz=0.000, favorable win rate=50.0%.
- UZH-FPV single-target replay, independent_coverage: CA-HMCD - No-Pruning = 0 [95% CI 0, 0], Holm-adjusted P=1, dz=0.000, favorable win rate=50.0%.
- Anti-UAV410 load 2, common_task_response_time: CA-HMCD - No-Pruning = -0.000329063 [95% CI -0.000906642, 0], Holm-adjusted P=1, dz=-0.471, favorable win rate=60.0%.
- Anti-UAV410 load 4, common_task_response_time: CA-HMCD - No-Pruning = -0.00315614 [95% CI -0.00820027, -0.00122579], Holm-adjusted P=0.0625, dz=-0.682, favorable win rate=87.5%.
- Anti-UAV410 load 6, common_task_response_time: CA-HMCD - No-Pruning = -0.0121844 [95% CI -0.0175787, -0.00737434], Holm-adjusted P=0.125, dz=-1.694, favorable win rate=100.0%.
- Anti-UAV410 load 8, common_task_response_time: CA-HMCD - No-Pruning = -0.012842 [95% CI -0.0167663, -0.00891762], Holm-adjusted P=0.5, dz=-2.765, favorable win rate=100.0%.
- UZH-FPV single-target replay, common_task_response_time: CA-HMCD - No-Pruning = 0 [95% CI 0, 0], Holm-adjusted P=1, dz=0.000, favorable win rate=50.0%.

## Interpretation boundary

This control estimates the effect of candidate reduction under the common 4000-node bounded-search budget. It does not claim that the unpruned search exhausts the global coalition space when the node limit is reached.

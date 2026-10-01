# CA-HMCD v2.3 parameter robustness report

## Design

- Six one-factor scans: alpha_cost, beta_time, gamma_unserved, lambda_redundancy, lambda_switch, and synergy_scale.
- Three registered levels per coefficient and three methods per level, giving 54 configurations.
- Thirty paired seeds (5000-5029) and 12 decision periods per configuration.
- The independent inferential unit is one complete seeded simulation trajectory.
- The endpoint is the solver-decoupled independent service score.
- Each coefficient forms one six-comparison Holm family: three levels times two comparators.

## Findings

- 36 of 36 registered CA-HMCD contrasts remained below the family-wise 0.05 threshold after Holm correction.
- Relative ordering was stable across the registered ranges, but absolute performance was not invariant to the coefficients.
- alpha_cost: CA-HMCD service range 0.0531; comparison advantages 0.0083 to 0.0276; 6/6 Holm-significant contrasts.
- beta_time: CA-HMCD service range 0.0234; comparison advantages 0.0153 to 0.0203; 6/6 Holm-significant contrasts.
- gamma_unserved: CA-HMCD service range 0.0392; comparison advantages 0.0144 to 0.0229; 6/6 Holm-significant contrasts.
- lambda_redundancy: CA-HMCD service range 0.0004; comparison advantages 0.0162 to 0.0181; 6/6 Holm-significant contrasts.
- lambda_switch: CA-HMCD service range 0.0806; comparison advantages 0.0043 to 0.0352; 6/6 Holm-significant contrasts.
- synergy_scale: CA-HMCD service range 0.0015; comparison advantages 0.0162 to 0.0191; 6/6 Holm-significant contrasts.

The widest CA-HMCD service changes occurred for lambda_switch and alpha_cost. Changes in lambda_redundancy and synergy_scale were much smaller. This is a controlled synthetic sensitivity result, not a deployment threshold calibration and not part of the public-trajectory replay's independent-unit inference.

## Audit

- PASS: stage-2 integrity status (observed=PASS, expected=PASS).
- PASS: model version (observed=ca-hmcd-stage1-closure-v2.3, expected=ca-hmcd-stage1-closure-v2.3).
- PASS: external evaluator version (observed=complete-v3-stage1-closure, expected=complete-v3-stage1-closure).
- PASS: service endpoint version (observed=solver-decoupled-service-v2, expected=solver-decoupled-service-v2).
- PASS: raw parameter records (observed=1620, expected=1620).
- PASS: parameter configurations (observed=54, expected=54).
- PASS: paired seeds per configuration (observed=[30], expected=[30]).
- PASS: decision periods per seed (observed=12, expected=12).
- PASS: registered parameters (observed=['alpha_cost', 'beta_time', 'gamma_unserved', 'lambda_redundancy', 'lambda_switch', 'synergy_scale'], expected=['alpha_cost', 'beta_time', 'gamma_unserved', 'lambda_redundancy', 'lambda_switch', 'synergy_scale']).
- PASS: registered algorithms (observed=['CA-HMCD', 'External-Auction', 'Greedy'], expected=['CA-HMCD', 'External-Auction', 'Greedy']).
- PASS: paired comparison rows (observed=36, expected=36).
- PASS: Holm families (observed=6, expected=6).
- PASS: complete finite statistics (observed=36, expected=36).

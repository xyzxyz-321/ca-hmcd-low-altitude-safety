# CA-HMCD Stage 4 Completion Report

Date: 18 September 2026

## Completion

Stage 4 is complete as a registered controlled-simulation experiment.

- 2,070 complete 30-seed episode units
- 24,840 decision-period records
- 7,560 paired state-audit groups, all passed
- 280 paired comparisons
- 84 explicitly separated Holm correction families
- 483 bounded-outcome intervals
- 290 seed-level common-task response-time records
- Frozen model, evaluator, and service-endpoint hashes unchanged
- 72/72 repository regression tests passed in the locked Python 3.12
  baseline environment

## Evidence decisions

| Mechanism | Decision | Manuscript-safe interpretation |
|---|---|---|
| Compatibility | Context-dependent | It improves the independent service endpoint in three engineering scenarios, but no scenario jointly supports service and the complete external objective after correction. |
| Complementarity | Context-dependent | The service advantage grows monotonically with complementarity scale, reaching +0.0049 at scale 2, but the complete-objective interval crosses zero and the scale-0 identity control is not exact under bounded search. |
| Redundancy | Not supported | Removing the redundancy term does not materially change same-type pair ratio, capability overlap, marginal-gain waste, or service under the registered stress design. |
| Stability | Supported | At volatility 0.30, CA-HMCD reduces mean switching by 3.46 per episode relative to No-Stability, with a 95% BCa interval from -3.85 to -3.05. Immediate service is lower, so this is a stability-performance trade-off. |
| True-state repair | Supported as a feasibility safeguard | At perception noise 0.30, repair raises final feasibility by 0.794 (95% BCa interval 0.744 to 0.844), while independent service falls by 0.024. Gate C passes only for the feasibility-safeguard claim. |
| Engineering transfer | Supported with scenario qualification | Final CA-HMCD allocations are feasible in all five scenarios; service or coverage gains over an external baseline are supported in airport, public-event, urban, and industrial scenarios, but not in the energy-facility scenario. |

## Required manuscript changes

1. Present stability and true-state repair as the two independently supported
   mechanisms. State their service/cost trade-offs in the same paragraph as
   the benefits.
2. Describe compatibility and complementarity as context-dependent design
   components. Do not claim universal aggregate objective improvement.
3. Remove any statement that the current redundancy coefficient has been
   empirically validated. The term may remain as a modeling safeguard, but its
   operational contribution is unverified.
4. Describe engineering evidence as controlled, engineering-shaped simulation.
   The five scenarios are not field trials.
5. Report the energy-facility scenario as a boundary case rather than omitting
   it.
6. Explain that the scale-0 complementarity identity control was not exact
   because bounded search and candidate ordering can select different tied
   incumbents. A pure value-term proof would require a separate complete-pool,
   exact-solver diagnostic.

## Artifact map

- Protocol: `ca_hmcd_stage4_value_engineering_protocol_20260918`
- Formal results: `ca_hmcd_stage4_value_engineering_formal_20260918`
- Statistical analysis: `ca_hmcd_stage4_value_engineering_analysis_20260918`
- Main report: `stage4_analysis_report.md`
- Full paired registry: `stage4_paired_comparisons.csv`
- Holm registry: `stage4_holm_families.csv`
- Gate decisions: `stage4_gate_decisions.csv`
- Reproducibility manifest: `stage4_analysis_manifest.json`

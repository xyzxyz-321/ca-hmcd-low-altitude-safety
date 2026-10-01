# CA-HMCD Stage 6-7 completion report

Date: 2026-09-18

## Stage 6: unified statistics and figures

- Unified 1,592 paired comparisons from the public replay, no-pruning control,
  external baselines, parameter sensitivity, static boundary experiment,
  controlled mechanism experiments, and engineering applicability experiment.
- Registered 316 separate Holm comparison families.
- Preserved mean paired differences, 95% intervals, unadjusted P values,
  Holm-adjusted P values, paired Cohen's dz, rank-biserial correlations, and
  favorable win rates when available.
- Preserved Wilson intervals for bounded outcomes and common-task-only response
  time comparisons in the source tables.
- Replaced Figures 4-7 with the formal Stage 4-5 results and retained the
  unchanged public-data replay as Figure 3.
- Figure exports include editable PDF/SVG, 300 dpi PNG, 600 dpi TIFF, source
  data, captions, alignment audits, PDF text audits, and collision audits.

## Stage 7: manuscript restructuring

- Rebuilt the manuscript in the requested eight-section order:
  Introduction; Related Work; Problem Formulation; CA-HMCD Method;
  Experimental Protocol; Results; Discussion; Conclusion.
- Reframed the contribution as an interpretable value, allocation, and
  true-state safety-control framework with solver-specific operating
  boundaries.
- Removed claims of universal quality, speed, redundancy benefit, real-time
  superiority, and field effectiveness.
- Integrated the formal no-pruning result, controlled repair validation,
  exact-versus-fixed-K boundary, five-scenario method-selection matrix, and
  54-configuration sensitivity study.
- Retained 59 references and explicit data/code availability placeholders for
  author-supplied repository DOI, licence, and creator metadata.

## Principal conclusions

- Complementarity is context-dependent: the registered service endpoint
  improved, while the complete-objective effect remained nonsignificant.
- Stability reduces switching at an immediate service cost.
- The registered redundancy mechanism is not empirically supported.
- True-state repair restores feasibility under controlled perception
  disturbance with a measurable service cost.
- No method is universally best across scenario, objective, and deadline.
- The Stage 3 real-time gate failed in all 64 registered cells.

## Verification

- Manuscript audit: PASS.
- Figure alignment, PDF text, and collision audits: PASS.
- Word-to-PDF visual QA: PASS; final manuscript renders as 19 pages.
- Regression tests: 77/77 PASS in the locked Python 3.12.14, NumPy 2.5.3,
  SciPy 1.18.1 environment.
- Frozen core hashes remain unchanged.

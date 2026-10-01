# Stage 6 figure contract

## Shared evidence boundary

The figures use the frozen Stage 4 public-data-informed replay outputs and the
Stage 5 cluster-aware statistical analysis. The independent unit is an original
UZH-FPV trajectory or a preregistered Anti-UAV410 composed workload. The 30
random seeds are nested repetitions and are not plotted or counted as
independent public-data samples.

## Figure 3

- Results-level question: How does CA-HMCD compare with Greedy and
  External-Auction as concurrent task load increases?
- Figure-level claim: CA-HMCD trades a small low-load service disadvantage for
  higher coverage and service estimates at larger Anti-UAV410 loads, although
  the load-6 and load-8 families remain under-resolved after Holm correction.
- Panels: independent service, coverage, external objective, and common-task
  response time.
- Boundary: confidence intervals estimate mean paired cluster differences;
  filled markers alone denote Holm-adjusted P < 0.05.

## Figure 4

- Results-level question: Which modeled components account for the full
  objective?
- Figure-level claim: The stability term consistently protects the complete
  objective by reducing switching and physical expenditure, whereas the
  complementarity term has a load-dependent and non-universal aggregate effect.
- Panels: objective difference from No-Synergy, objective difference from
  No-Stability, switch-count difference, and physical-cost difference.

## Figure 5

- Results-level question: Does CA-HMCD alter coalition structure as load rises?
- Figure-level claim: CA-HMCD generally reduces same-type pairing, capability
  overlap, and marginal-gain waste relative to Greedy and External-Auction, but
  these structural differences do not imply a universal objective advantage.
- Panels: the three independent redundancy diagnostics and external objective.

## Figure 6

- Results-level question: What is the public-data evidence base, and what does
  its audit establish?
- Figure-level claim: The registered replay provides complete paired execution
  and final feasibility over 44 independent units, while the UZH-FPV
  single-target stratum supports motion-replay compatibility rather than
  superiority.
- Panels: independent-unit design, formal audit outcomes, and UZH-FPV paired
  differences.

## Figure 7

- Results-level question: Where does bounded search become the limiting factor?
- Figure-level claim: Runtime and node use rise with Anti-UAV410 task load, and
  fallback becomes common at loads 6 and 8; the retained incumbent can improve
  on the greedy warm start but carries a non-zero bound gap.
- Panels: runtime, visited nodes, run-level fallback prevalence, and fallback
  incumbent diagnostics.

## Export and integrity contract

- Backend: Python/matplotlib only.
- Final width: 180 mm; white background; editable PDF/SVG text.
- Uncertainty: Stage 5 paired cluster BCa intervals or bounded BCa/Wilson
  intervals, as specified in each caption.
- Statistical decisions: Holm-adjusted P values, never confidence-interval
  exclusion alone.
- Required outputs: PNG, SVG, PDF, TIFF, source-data CSV, alignment JSON/SVG,
  PDF text audit, and rendered collision audit.

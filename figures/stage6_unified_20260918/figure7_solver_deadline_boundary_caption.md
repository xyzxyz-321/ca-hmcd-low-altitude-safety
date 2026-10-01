# Figure 7 caption

**Figure 7. Solver and deadline evidence defines scenario-specific operating
boundaries rather than a universal winner.** **a**, Mean end-to-end runtime for
five methods in five controlled engineering scenarios; External-Auction is
fastest in every scenario, whereas the genetic algorithm is consistently
slowest. **b**, Paired complete-objective difference between CA-HMCD-FixedK and
CA-HMCD-Exact. None of the five scenario contrasts was Holm-significant.
**c**, Quality-first method among those meeting each registered deadline in at
least 95% of periods. The selected method changes with scenario and deadline,
and no method qualifies for the public-event scenario at 50 ms. **d**, Stage 3
real-time gate diagnostics. Although all 64 cells had optimal exact references,
no cell simultaneously met the exact-miss, FixedK-hit, feasibility, and three
quality-loss criteria. The evaluated implementation therefore does not
establish a real-time advantage over full-candidate HiGHS. Runtime results are
workstation- and implementation-specific and do not constitute hard-real-time
certification.

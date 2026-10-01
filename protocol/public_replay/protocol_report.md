# CA-HMCD Public-Data Replay Protocol

Date: 2026-09-16
Protocol version: `public-replay-protocol-v1`
Replay mapping version: `public-trajectory-replay-v2`

## Registered design

- Rolling horizon: 12 decision periods.
- Paired random seeds: 30 per fixed workload.
- Response resources: 10 per run.
- Candidate retention limit: top-12.
- Solver node budget: 4000 per decision period.
- UZH-FPV source clusters: 16 measured trajectories.
- Anti-UAV410 evaluation sources: 120 official test sequences.
- Anti-UAV410 train is development-only; val is calibration-only; test is evaluation-only.
- No Anti-UAV410 test source is reused across formal workloads.
- Random seeds are nested within workloads and are not interpreted as independent physical flights or videos.

## Workload registry

| Task load | Workloads | Seeds per workload |
|---:|---:|---:|
| 1 | 16 | 30 |
| 2 | 10 | 30 |
| 4 | 8 | 30 |
| 6 | 6 | 30 |
| 8 | 4 | 30 |

## Independent units

- UZH-FPV inference is clustered by original flight sequence.
- Anti-UAV410 inference is clustered by preregistered composed workload; the manifest also retains every parent video identity.
- Algorithms receive the same replay CSV, task arrivals, resources and seed for each paired comparison.

## Audit

- PASS: UZH source trajectories assigned once (observed=16, expected=16)
- PASS: Anti evaluation sources assigned once (observed=120, expected=120)
- PASS: No Anti train/val source in formal workload (observed=0, expected=0)
- PASS: Every workload has preregistered seed count (observed=30, expected=30)
- PASS: All replay horizons are fixed (observed=[12], expected=[12])
- PASS: All replay sequence IDs are unique (observed=44, expected=44)
- PASS: Minimum paired seeds (observed=30, expected=>=30)
- PASS: Response-resource count is fixed (observed=[10], expected=[10])
- PASS: Candidate and solver budgets are fixed (observed=[(12, 4000)], expected=[(12, 4000)])
- PASS: Anti strata are balanced within load groups (observed=[((2, 'fast_motion'), 3), ((2, 'nominal'), 8), ((2, 'scale_small'), 2), ((2, 'visibility'), 7), ((4, 'fast_motion'), 5), ((4, 'nominal'), 12), ((4, 'scale_small'), 4), ((4, 'visibility'), 11), ((6, 'fast_motion'), 6), ((6, 'nominal'), 14), ((6, 'scale_small'), 4), ((6, 'visibility'), 12), ((8, 'fast_motion'), 5), ((8, 'nominal'), 12), ((8, 'scale_small'), 3), ((8, 'visibility'), 12)], expected=[((2, 'fast_motion'), 3), ((2, 'nominal'), 8), ((2, 'scale_small'), 2), ((2, 'visibility'), 7), ((4, 'fast_motion'), 5), ((4, 'nominal'), 12), ((4, 'scale_small'), 4), ((4, 'visibility'), 11), ((6, 'fast_motion'), 6), ((6, 'nominal'), 14), ((6, 'scale_small'), 4), ((6, 'visibility'), 12), ((8, 'fast_motion'), 5), ((8, 'nominal'), 12), ((8, 'scale_small'), 3), ((8, 'visibility'), 12)])
- PASS: Replay task counts match registered loads (observed=44, expected=44)
- PASS: Arrival offsets match preregistered patterns (observed=136, expected=136)
- PASS: Run seeds are globally unique (observed=1320, expected=1320)

## Evidence boundary

Public data instantiate task-side motion and observation patterns. Protected-zone registration, multi-sequence concurrency, risk, response windows, response resources and allocation outcomes remain simulation constructs. This is a public-data-informed semi-synthetic replay protocol.

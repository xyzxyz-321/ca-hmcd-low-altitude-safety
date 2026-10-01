# CA-HMCD Public-Trajectory Replay Protocol

Protocol version: `public-trajectory-replay-v2`

## Evidence boundary

UZH-FPV and Blackbird provide measured task-side motion, whereas Anti-UAV410
provides measured image-plane observation patterns. The protected zone, risk
score, response-time window, heterogeneous response resources, resource
effectiveness, physical expenditure and coalition outcomes remain simulation
constructs. The resulting evidence is therefore a public-data-informed
semi-synthetic replay and not field validation.

## UZH-FPV adapter

The adapter reads the single `leica.txt` entry directly from each public
ground-truth ZIP archive. It uses the Leica device clock as the primary time
base and the host response time only when the device clock resets or becomes
invalid. The parser extracts measured `x`, `y`, and `z` positions and applies a
fully recorded quality-control pipeline:

1. remove duplicate or non-increasing timestamps;
2. remove isolated position spikes;
3. retain the longest physically plausible time-ordered chain using the
   declared speed, time-gap and bridge limits;
4. trim stationary acquisition before and after the moving trajectory;
5. translate the selected trajectory to a local coordinate frame; and
6. register its endpoint to the declared protected zone.

Endpoint registration and concurrent composition are simulation operations;
the underlying relative three-dimensional motion remains measured evidence.
The adapter derives normalized speed, spatial level, density, risk and response
window features and records the source SHA-256, cleaning parameters, removed
point counts and coordinate origin in replay metadata.

Example:

```powershell
python ca_hmcd_replay.py uzh-fpv `
  --track UZH-1=<EXTERNAL_DATA_ROOT>\UZH-FPV\gt\indoor_forward_3.zip `
  --sequence-id uzh-fpv-001 `
  --protected-zone 0,0,0 `
  --max-speed-mps 25 `
  --max-gap-s 2 `
  --max-bridge-points 50 `
  --steps 12 `
  --output replay\uzh-fpv-001.csv
```

## Blackbird adapter

Use one official `groundTruthPoses.csv` file per replay task. The official file
is headerless, so the adapter default assumes

`timestamp, x, y, z, quaternion...`

as columns `0,1,2,3`. This assumption is recorded in the replay metadata and can
be changed with `--columns timestamp_index,x_index,y_index,z_index`. The adapter
also detects header-bearing pose exports. It resamples each flight into a fixed
decision horizon and derives:

- normalized three-dimensional speed;
- spatial level relative to a declared protected zone;
- cross-track density for composed concurrent flights;
- a transparent proximity-and-approach risk score; and
- a normalized time-to-entry response window.

Multiple independent flights can be composed with fixed step offsets. Such
concurrency must be reported as synthetic composition. Source flights must not
cross calibration and test splits.

Example:

```powershell
python ca_hmcd_replay.py blackbird `
  --track BB-1=<EXTERNAL_DATA_ROOT>\blackbird\clover\groundTruthPoses.csv `
  --track BB-2=<EXTERNAL_DATA_ROOT>\blackbird\mouse\groundTruthPoses.csv `
  --sequence-id blackbird-001 `
  --columns 0,1,2,3 `
  --protected-zone 0,0,0 `
  --steps 12 `
  --output replay\blackbird-001.csv
```

## Anti-UAV410 adapter

The adapter accepts the benchmark TXT format `x,y,width,height` and JSON files
containing `gt_rect` plus an optional `existence` or `exist` vector. It also
loads the ten sequence-level attributes stored under each split's `att`
directory. It derives:

- apparent target size;
- frame-normalized image-plane motion by default;
- visibility/activity;
- density when independent tracks are composed; and
- simulated risk and response windows relative to a declared image-plane
  protected point.

Anti-UAV410 is primarily a single-object tracking benchmark. Combining
independent videos creates semi-synthetic concurrent tasks and must not be
described as native multi-target field data.

No frame rate is assumed. When `--fps` is omitted, speed is measured as
normalized image-diagonal displacement per source frame. An authoritative frame
rate may be supplied explicitly to obtain a per-second rate. Event windows can
be selected with `--window-mode visibility`, `motion`, `scale`, or `auto`.
State-transition-preserving resampling retains disappearance and reappearance
events when a source window is compressed to the rolling decision horizon.

Example:

```powershell
python ca_hmcd_replay.py anti-uav `
  --track AU-1=<EXTERNAL_DATA_ROOT>\AntiUAV410\annos\test\sequence-1.txt `
  --track AU-2=<EXTERNAL_DATA_ROOT>\AntiUAV410\annos\test\sequence-2.txt `
  --sequence-id anti-uav-001 `
  --width 640 --height 512 `
  --window-mode auto --window-frames 240 `
  --protected-point 0.5,0.85 `
  --steps 12 `
  --output replay\anti-uav-001.csv
```

## Data quality audit

The audit command parses every source file, verifies unique SHA-256 values,
runs the UZH-FPV cleaning and replay conversion, validates Anti-UAV event
windows, and writes source-level manifests without redistributing third-party
records.

```powershell
python ca_hmcd_replay.py audit-data `
  --uzh-dir <EXTERNAL_DATA_ROOT>\UZH-FPV\gt `
  --anti-anno-dir <EXTERNAL_DATA_ROOT>\Anti-UAV410\annos `
  --output-dir ca_hmcd_data_quality_20260916
```

Outputs:

- `uzh_fpv_quality_manifest.csv`;
- `anti_uav410_quality_manifest.csv`;
- `data_quality_summary.csv`; and
- `data_quality_report.md`.

## Observation disturbances

Delay, dropout bursts and normalized feature noise modify only the perceived
state. The true public-data-derived state remains unchanged for external
evaluation.

```powershell
python ca_hmcd_replay.py disturb `
  --input replay\anti-uav-001.csv `
  --output replay\anti-uav-001-disturbed.csv `
  --seed 7000 `
  --latency-steps 1 `
  --dropout-probability 0.10 `
  --dropout-burst-steps 2 `
  --feature-noise 0.03
```

## Formal benchmark

Use at least 30 paired stochastic replications for every fixed workload. These
seeds quantify algorithmic and simulated-resource variability; they are nested
within the workload and must not be described as independent public flights or
videos. A source flight or video may occur in only one formal workload and one
data split. Every method receives the same replay, resource realization,
active-task IDs and seed.

The registered Stage-2 design uses all 16 UZH-FPV trajectories as separate
motion-source clusters. Anti-UAV410 train is development-only, val is
calibration-only, and all 120 test sequences are assigned exactly once to 28
composed workloads: ten two-task, eight four-task, six six-task and four
eight-task workloads. Observation strata are proportionally balanced within
each load group to avoid confounding task load with visibility, motion or scale
difficulty. All workloads use 12 decision periods, 10 response resources,
top-12 candidate retention and a 4,000-node solver budget.

The complete locked design is stored in
`ca_hmcd_replay_protocol_20260916/experiment_run_registry.csv`. Statistical
inference must cluster UZH-FPV observations by original trajectory and
Anti-UAV410 observations by composed workload while retaining parent-video
identity for audit.

```powershell
python ca_hmcd_replay.py benchmark `
  --replay replay\episode-001.csv `
  --replay replay\episode-002.csv `
  --output-dir ca_hmcd_stage3_public_replay_20260916 `
  --resources 10 `
  --top-k 12
```

The benchmark writes episode results, per-step records, statistical comparisons,
Holm family registrations, 95% paired BCa confidence intervals, bounded-metric
intervals, common-task response-time comparisons, paired-state audits and an
evidence-boundary statement.

## Registered Stage-2 protocol execution

The frozen Stage-2 registry must be executed with
`ca_hmcd_replay_runner.py`, rather than the legacy one-seed-per-replay
benchmark interface. The registered runner expands every workload-seed row
across the six prespecified algorithms, writes one atomic gzip checkpoint per
algorithm run, and reconstructs aggregate CSV outputs after interruption.
Each checkpoint is bound to the protocol, run registry, replay CSV and core
source-code SHA-256 fingerprints. A mismatched checkpoint is rejected.

Trial execution:

```powershell
python ca_hmcd_replay_runner.py `
  --protocol-dir ca_hmcd_replay_protocol_20260916 `
  --output-dir ca_hmcd_stage3_code_closure_trial_20260916 `
  --mode trial
```

Formal execution:

```powershell
python ca_hmcd_replay_runner.py `
  --protocol-dir ca_hmcd_replay_protocol_20260916 `
  --output-dir ca_hmcd_stage3_replay_formal_20260916 `
  --mode formal
```

Formal mode requires the complete 1,320-row registry and all six prespecified
algorithms. It rejects workload filters, reduced replication counts and partial
algorithm sets. The runner performs execution and fairness audits only;
cluster-aware inferential statistics remain a separate analysis stage.

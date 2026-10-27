# Engine track progress (Ansh) — overnight build

Started 2026-10-27. Source of truth for the milestones is `docs/TASKS_ANSH.md`.
Commit message per milestone: `A<n>: <summary>`.

## Environment (recorded in A0)

| Tool | Version |
|---|---|
| Python | 3.12.10 |
| uv | 0.11.23 |
| node | v24.16.0 (not used by the engine) |
| git | 2.54.0.windows.1 |
| OS | Windows 11 (Git Bash + PowerShell) |

## Milestones

- [x] A0 Pre-flight
- [x] A1 Scaffold
- [x] A2 Physics and vehicle priors
- [x] A3 Truth simulator
- [x] A4 Calibration and E1 (BMW i3 part BLOCKED: dataset not downloaded)
- [x] A5 Forecast and E2
- [x] A6 Trust engine and E5 (Neo4j live test BLOCKED: no credentials)
- [~] A7 Routing and chargers (PARTIAL: real data fetched, tests NOT written; OCM blocked, no API key)

> **RUN STOPPED EARLY: usage limit reached.** Done and committed: A0-A6. A7 is partial. NOT started:
> A8 (dispatch, E3, E4), A9 (API + export script), A10 (scripts/run_all.py, README/CLAUDE.md commands),
> A11 (docs/MORNING_REPORT_ENGINE.md, final AI_USAGE.md entry). Resume with: "Read docs/TASKS_ANSH.md and
> docs/PROGRESS_ENGINE.md; finish A7 (tests/test_routing.py is only a placeholder), then A8-A11."
- [ ] A8 Dispatch, E3 and E4
- [ ] A9 API per docs/API_CONTRACT.md
- [ ] A10 Quality pass
- [ ] A11 Morning report

## Notes per milestone

### A0 Pre-flight (done)
- Versions recorded above. No `.env` exists yet (so Neo4j and Sarvam are unconfigured tonight).
- Layout fixed: root had `PROMPTS.md`, `TASKS_ANSH.md` and the plan PDF; `verity-starter/` and
  `verity-engine/` held nested copies. Everything unique is now at the CLAUDE.md locations
  (`docs/PROMPTS.md`, `docs/PLAN.md` + `docs/PLAN.pdf`, `docs/API_CONTRACT.md`, `.env.example`,
  `.gitignore`); the three duplicates were byte-identical (checked with `diff -q`); both shell
  folders are gone.
- `git init -b main` done (identity already configured). Nothing is ever pushed.
- CLAUDE.md updated for the engine/frontend split (see DECISIONS.md).
- Plan content: `docs/PLAN.md` is a placeholder, so the PDF was text-extracted to read it.

### A1 Scaffold (done)
- `pyproject.toml` (uv, hatchling, Python >= 3.11, only the CLAUDE.md dependency list; pytest in the
  dev group). `engine/verity` and `api` install editable, so `import verity` / `import api` work
  from anywhere.
- Docstring stubs for every module in the CLAUDE.md layout (`physics`, `vehicles`, `routing`,
  `calibrate`, `forecast`, `dispatch`, `trust/*`, `api/main.py`); they are filled in A2-A9.
- `engine/verity/schemas.py`: pydantic v2 models for `verity.trip.v1`, charger record and report.
  Timestamps must be timezone-aware, a trip needs >= 2 GPS points and >= 2 SoC readings, and
  `is_simulated` is mandatory so nothing can be forgotten to be labelled.
- `tests/test_schemas.py`: 18 tests (valid sample, alias round-trip, 13 malformed variants, charger,
  report). `uv run pytest` -> 18 passed.
- `README.md`, `docs/AI_USAGE.md`, `docs/DECISIONS.md`, `data/trips/README.md` created.
- NOT created, by instruction: `logger/`, `web/`, `design/`.

### A2 Physics and vehicle priors (done)
- `physics.py`: `VehicleParams`, `Segments`, `energy_terms()` returning joules for each of
  rolling/grade/aero/inertia/regen/aux/base; helpers to convert to SoC points, per term and
  cumulative. Everything broadcasts, so N Monte Carlo runs evaluate as one (N, S) array.
- Energy attribution is exact: terms sum to the integral of P_total (tested against an explicit scalar
  re-implementation of the CLAUDE.md formulas).
- `vehicles.py`: three generic classes (small e-car ~25 kWh, compact e-SUV ~45 kWh, e-scooter ~3 kWh).
  Every number is commented "approximate - verify". Calibrated parameters (CdA mult, Crr mult,
  eta_drive, aux mult, usable fraction, k) live in an unconstrained vector with a Gaussian prior, so
  priors stay inside physical bounds.
- `tests/test_physics.py`: 19 tests. Sanity: zero distance -> zero energy, more payload -> more energy,
  AC on -> more aux energy, regen <= eta_regen x braking energy and <= the cap. Plus hand calculation
  on flat ground, energy/km rising with speed above 30 km/h, grade/wind direction, bounds, class
  plausibility. A real bug found by the tests and fixed: summing energy to (N,) then dividing by an
  (N, 1) capacity gave an (N, N) matrix that would have mixed Monte Carlo runs.
- `uv run pytest` -> 37 passed.

### A3 Truth simulator (done)
- `engine/verity/sim.py`: 1 s truth physics, deliberately richer than `physics.py`: AR(1) wind gusts,
  per-trip driver aggressiveness, state-of-health + capacity fade + I^2R loss growing with age,
  stop-and-go / arterial / highway blocks, rough-surface blocks, AC compressor duty cycling,
  occupant/cargo mass errors, power-dependent drive efficiency.
- Exports only what a logger / lookup service would see: `data/sim/<name>/trips/` (verity.trip.v1, 10 s
  GPS, whole-percent SoC every ~10 min), `context/` (100 m elevation profile with 0.8 m noise + a
  noisy weather forecast), and hidden `truth/` (never read by planning code). Seeded: same seed ->
  byte-identical files (tested).
- Official dataset: `uv run python -m verity.sim --name main --seed 20261027` -> 126 SIMULATED trips,
  9 vehicles (3 per class), 14 trips each, 8.2 MB in `data/sim/main/`. Per-trip numbers of `main` were
  deliberately NOT inspected (held-out discipline); plausibility was checked on a throwaway `dev`
  dataset (seed 777): median Wh/km ~125 (small e-car), ~145 (e-SUV), ~22 (scooter).
- `tests/test_sim.py`: 8 tests (reproducible, seed-sensitive, contract + labels, no truth leakage,
  phone-log realism, physical plausibility, forced SoH, manifest). Bug found and fixed by the
  plausibility test: a grade computed with `np.gradient` given spacing instead of coordinates produced
  834 Wh/km; replaced by the exact analytic terrain slope.
- `uv run pytest` -> 45 passed.

### A4 Calibration and E1 (done; BMW i3 part blocked)
- Split FIXED FIRST and committed alone (`b4f02b7`): `data/splits/sim_main_split.json`, 90 train / 36
  test trips (4 of 14 per vehicle), ids only, `save_split` refuses to overwrite.
- `trips.py` (trip log + context -> physics segments; planner view only), `calibrate.py` (Laplace
  Bayesian calibration + empirical-Bayes fleet pooling, method explained in its docstring and in
  DECISIONS.md), `splits.py`.
- Two real problems found and fixed on the way: GPS jitter at standstill inflated distance by 4 %
  (planner now trusts Doppler speed when parked; simulator congestion also made less extreme, `main`
  regenerated with the same ids), and uv runs Python 3.13.14 (not the 3.12.10 of the bare `python`).
- Settings were chosen on the DEV simulator seed only (rho_rel = 0.06; pooling variance floor 0.05 -> 0.30
  after the pooled prior was overconfident on dev). `main` held-out split evaluated ONCE with frozen settings.
- E1 on the held-out split (SIMULATED, 9 vehicles, 36 test trips). MAE in SoC points / share inside the 90 % band:

| method | n=1 | n=2 | n=3 | n=5 | n=10 |
|---|---|---|---|---|---|
| Verity physics, class prior | 3.26 / 0.74 | 2.20 / 0.79 | 2.27 / 0.81 | 2.46 / 0.79 | 2.20 / 0.78 |
| Verity physics, fleet-pooled prior | 3.56 / 0.68 | 2.42 / 0.79 | 2.54 / 0.81 | 2.48 / 0.79 | 2.34 / 0.72 |
| Nominal-spec model (no learning) | 5.74 / 0.56 | 5.74 / 0.68 | 5.74 / 0.77 | 5.74 / 0.71 | 5.74 / 0.81 |
| Flat "minus 25%" rule | 8.30 / 0.61 | 8.30 / 0.76 | 8.30 / 0.78 | 8.30 / 0.80 | 8.30 / 0.86 |
| Plain regression (no physics) | 6.56 / 0.07 | 6.98 / 0.06 | 25.2 / 0.05 | 39.7 / 0.18 | 6.66 / 0.50 |

- Honest reading: physics + Bayes beats every baseline on error at every n and is useful from 1-2
  trips. BUT its "90 %" band covers only 72-81 %: overconfident on held-out vehicles (dev said 86-94 %).
  Fleet pooling did NOT help (two peer vehicles per class is too few). Not re-tuned on the test split.
- Files: `results/e1_learning_curve_simulated.png|json` (title says "(simulated)").
- BLOCKED: BMW i3 real-data E1 (needs `data/raw/bmw_i3/`; the loader is deliberately not guessed,
  see `engine/verity/datasets/bmw_i3.py`). Exactly what to do is in the morning report.
- `uv run pytest` -> 58 passed.

### A5 Forecast and E2 (done)
- `forecast.py`: `PlannedRoute` (1 km segments with road class, grade, forecast temperature/wind),
  `simulate_runs` (N=2000 default, seeded, vectorised) with shared trip-level factors sampled once per
  run (calibration draw incl. driver k and usable capacity, aux multiplier, payload error, wind error,
  day-to-day capacity error, traffic regime) plus small independent segment noise; `shared=False` is the
  E2 strawman. Stop-and-go energy in congestion-prone stretches is modelled explicitly. `live_update`
  conditions the runs on a new mid-trip SoC reading (importance weights, noise widened until >= 40 runs
  still count). `round_prob` rounds to the nearest 5 % (golden rule 5).
- `physics.Segments` now accepts (runs, segments) arrays; `vehicles.to_params` accepts per-segment draws.
- `sim.planner_view` exposes only what a planner could know; the `main` dataset stayed byte-identical.
- Compute: ~110 ms per 2000-run forecast of a 60-180 km route on this laptop.
- `tests/test_forecast.py`: 9 tests (reproducible; shared spread > 3x independent with equal per-segment
  marginals; confidence monotone; terms sum; payload/heat/speed lever directions; stop-and-go only in
  jams; interpolation; live update direction; 5 % rounding; route cutting).
- E2 (SIMULATED, 540 new truth-simulator trips on the 9 `main` vehicles calibrated on train-split trips
  only; planning constants chosen on DEV). Success = arrive with >= 10 % SoC:

| | shared factors (Verity) | independent segments (strawman) |
|---|---|---|
| ECE (calibration error) | 0.061 | 0.117 |
| Brier score | 0.107 | 0.122 |
| 90 % arrival band covers truth | 0.92 | 0.28 |
| mean 90 % band width (SoC points) | 17.9 | 2.6 |
| trips with forecast >= 0.9: share that arrived | 0.965 (n=142, mean forecast 0.972) | 0.879 (n=272, mean forecast 0.996) |

- Honest gaps: Verity's mid-range bins sit slightly below the diagonal (e.g. ~0.85 predicted vs ~0.76
  observed), and the median energy forecast is biased low by 2.6 % / 3.0 % / 8.7 % for small e-car /
  e-SUV / e-scooter. Files: `results/e2_calibration_simulated.png|json`.
- `uv run pytest` -> 67 passed.

### A6 Trust engine and E5 (done; live Neo4j blocked)
- `trust/priors.py`: Beta priors by operator / site type / region; every value cites the PLAN s3 row it
  came from and is marked "approximate - verify" (national 0.82 Parliament data, BPCL 0.40, highway/DC
  fast 0.60, Delhi IEEFA audit 0.16 with weight 1 because of its small sample). Strength-weighted,
  total clipped to 2-8 pseudo-reports.
- `trust/source_trust.py`: Dawid-Skene in numpy with a per-report latent state (an intermittent charger
  is not a noisy reporter), Beta priors per source and charger, 30-day time decay, per-source weights,
  and fixed-parameter usage-trace pseudo-source.
- `trust/rings.py`: fake-report ring detection on the co-reporting graph (shared chargers, Jaccard, label
  agreement, burst timing, connected components).
- `trust/reports_sim.py`: SIMULATED worlds: noisy honest users, lying operator feed, fake ring, usage traces.
- `trust/engine.py`: `TrustEngine.estimate()` -> per charger p_works, 10-90 % interval, n_reports in 60
  days, days since last, trust level (high/medium/low/unknown).
- `trust/graph.py`: `ChargerGraph` interface; `InMemoryGraph` (default) and `Neo4jGraph` with the CLAUDE.md
  schema (constraints for all 7 labels, REPORTED / RUN_BY / AT / LEG per battery band / ALTERNATE /
  WORKS_WITH, co-reporting query, prefix-scoped test-data delete). `scripts/neo4j_smoke_test.py` skips
  cleanly without `.env` credentials (run: "SKIPPED ... exit 0").
- BLOCKED: the Neo4j implementation has only been tested through an injected fake driver (query shape
  and parsing); the Cypher has never run against a real server. Needs AuraDB credentials in `.env`.
- Tests: `test_trust.py` (13) and `test_graph.py` (13). Settings (source prior Beta(4,1)) chosen on
  simulator seeds 0-7; E5 on seeds 100-119.
- E5 (SIMULATED; 20 worlds per scenario, 120 chargers, 100 honest users; mean absolute error of the recovered
  reliability, lower is better):

| scenario | avg star rating | Dawid-Skene | + usage | + ring detection |
|---|---|---|---|---|
| clean | 0.154 | 0.168 | 0.156 | 0.156 |
| lying operator feed | 0.163 | 0.171 | 0.160 | 0.160 |
| ring of 5 | 0.213 | 0.171 | 0.160 | 0.159 |
| ring of 12 | 0.245 | 0.177 | 0.160 | 0.156 |
| ring of 12 + lying feed | 0.254 | 0.180 | 0.165 | 0.160 |
| evasive ring of 12 (10 days) | 0.245 | 0.177 | 0.160 | 0.160 |

  On the chargers the ring attacks (ring of 12): average rating 0.543 vs 0.130 with the full engine.
  Ring detection: precision 1.0 / recall 1.0 for the burst ring (all 20 worlds each); recall 0.0 for the
  evasive ring (never flagged; Dawid-Skene + usage still limits the damage: 0.146 on attacked chargers).
- Honest reading: no gain over average rating in a clean world (both sit on a ~0.15 floor because each
  report is one noisy sample of an intermittent charger); large gain under attack; ring detection is
  evadable by an adversary who spreads reports over days. Files: `results/e5_trust_recovery_simulated.*`.
- `uv run pytest` -> 93 passed.

## Blocked items

(none yet)

### A7 Routing and chargers (PARTIAL, committed as-is)
- `engine/verity/routing.py`: `CachedClient` (on-disk cache, persisted request counter with a hard 300 limit,
  exponential back-off, API keys never cached), OSRM route, Open-Meteo elevation + forecast, `plan_segments`
  (1 km segments: road class from router speed, grade, forecast temperature/headwind), labelled synthetic
  fallback, `load_planned_route`. Run: `uv run python -m verity.routing`.
- REAL data fetched and saved to `data/processed/route_noida_agra.json`: 190.77 km, 149.5 min, 191 segments
  (167 highway / 24 arterial), elevation 157-204 m, forecast for 2026-10-28 14:00 IST 28.6-29.8 C, wind
  0.4-1.2 m/s. Passes 2.4 km from Jewar and ends at Agra (consistent with the Yamuna Expressway; the router
  chose it, it was not forced). 4 requests.
- `engine/verity/chargers.py` + `scripts/import_chargers.py`: OSM Overpass import (195 OSM records, 54 merged
  within 50 m -> 141 chargers in `data/processed/chargers_ncr.json`, 1 request; 5 requests in total so far).
- BLOCKED: Open Charge Map needs a free API key (`OCM_API_KEY` in .env; not created tonight). Neo4j load skipped
  (no credentials).
- KNOWN WEAKNESSES of the import: only 1 of 141 chargers has a tagged connector power (OSM rarely tags kW; untagged
  connectors are dropped, not guessed), all site types are "unknown" (keyword heuristic matched nothing), 90 have
  no operator. Dispatch needs connector power for charge times: A8 must treat missing power as unknown.
- TODO for A7: write `tests/test_routing.py` (cache replay, request budget, back-off via httpx.MockTransport,
  fallback labelling, segment building, charger parsing/merging); it currently holds only a docstring.

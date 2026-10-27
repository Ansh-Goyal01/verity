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
- [ ] A5 Forecast and E2
- [ ] A6 Trust engine and E5
- [ ] A7 Routing and chargers
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

## Blocked items

(none yet)

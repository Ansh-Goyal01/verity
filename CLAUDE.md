# Verity — project brief for Claude Code

Verity tells an EV driver, with an honest and tested confidence level, whether they will reach
their destination, and plans charging around chargers that actually work. Every trip gets a
dispatch release: GO / NO-GO, a plan with reachable alternate chargers, and a live **Bingo Line**
(the battery % below which the driver must divert). Built for hackFront India 2026, theme T6,
problem statement T6.1 "Will I Make It". Team: Low Cortisol.

- Full plan, research and judging criteria: `docs/PLAN.md` (read it when a task needs context).
- Prompt playbook for building: `docs/PROMPTS.md`.
- Interface between the engine and the frontend pages: `docs/API_CONTRACT.md` (never changed
  silently; proposals go in `docs/API_CONTRACT_CHANGES.md`).
- **Prototype deadline: 31 Oct 2026, 11:59 PM IST. Internal target: submit by 6 PM.**

## Golden rules (judges reward honesty; never break these)

1. **Never invent data or results.** No made-up numbers in code, UI, docs or charts.
2. **Label simulation.** Simulated data lives in `data/sim/`, has `"is_simulated": true`, and every
   chart built from it says "(simulated)" in its title.
3. **Held-out trips are sacred.** The test split in `data/splits/` is fixed once and never used
   for tuning or inspection while building.
4. **Physics first.** The energy model is physics-based and explainable. ML appears only as a
   baseline to beat in experiment E1.
5. **Every probability shown in the UI comes from the model**, rounded to the nearest 5%, with
   its sample size or evidence where relevant ("based on 3 reports in 60 days").
6. **Refuse rather than guess.** If evidence is too thin, the UI says so instead of promising.
7. **Secrets only in `.env`** (never committed). `.env.example` lists the keys with no values.
8. **Log AI tool use** in `docs/AI_USAGE.md` (the hackathon requires disclosure).

## Repo layout

```
CLAUDE.md  README.md  .env.example  .gitignore  pyproject.toml
docs/        PLAN.md (+ PLAN.pdf), PROMPTS.md, API_CONTRACT.md, AI_USAGE.md, DECISIONS.md
engine/verity/
  physics.py      energy model (pure functions, SI units)
  vehicles.py     vehicle-model priors (mass, battery, CdA, Crr, efficiency)
  routing.py      route, elevation, weather fetch + on-disk cache
  calibrate.py    Bayesian calibration, fleet pooling
  forecast.py     Monte Carlo with shared trip-level factors; live updates
  dispatch.py     candidate plans, backup evaluation, Bingo Line, levers
  trust/          priors.py, source_trust.py (Dawid–Skene), rings.py, engine.py, reports_sim.py, graph.py (in-memory + Neo4j), graph_build.py (backend choice + sync)
  sim.py          truth simulator (richer than the planner; SIMULATED data)
  trips.py        trip log + context -> physics segments; splits.py fixed held-out split
  chargers.py     OSM/OCM parsing, 50 m merge, near-route lookup; voice.py Sarvam client
  schemas.py      pydantic models for every data contract below
api/            main.py (FastAPI app), service.py (engine composition), models.py (mirrors the contract)
scripts/        import_chargers.py, neo4j_smoke_test.py, graph_insights.py, export_api_examples.py, run_all.py
logger/         frontend teammate's: static trip logger, plain HTML/CSS/JS, no build step
web/            frontend teammate's: fleet console + driver view, plain HTML/CSS/JS (js/api.js)
design/         frontend teammate's: design pages, plain HTML/CSS/JS
                (engine work never creates, edits or deletes anything in logger/, web/, design/)
experiments/    e1_learning_curve.py ... e5_trust_recovery.py -> results/
data/           raw/ (gitignored) trips/ (real logs) sim/ splits/ cache/ (gitignored)
tests/
```

## Tech stack

- Python 3.11+, managed with `uv`. Core libs: numpy, scipy, pandas, pydantic, fastapi, uvicorn,
  httpx, neo4j (official driver), pytest, matplotlib. Ask before adding any other dependency.
- Frontend: plain HTML/CSS/JS with no build tools, built by the frontend teammate. It reads data
  only through the HTTP endpoints in `docs/API_CONTRACT.md`.
- Graph: Neo4j AuraDB Free (credentials in `.env`), Aura Graph Analytics for algorithms.
- Voice: Sarvam API — Saaras v3 (speech-to-text), Bulbul v3 (text-to-speech), Sarvam Translate.
- Routes / elevation / weather: open services (OSRM-compatible router, open elevation data,
  Open-Meteo). Public endpoints are rate-limited: always cache responses in `data/cache/`.
  Verify each service's current usage limits before relying on it.

## The energy model (physics.py)

Per route segment i with speed v, acceleration a, grade angle θ, length d:

- F_roll = Crr · s_surface · m · g · cos θ   (s_surface from OSM surface tag, ≥ 1 on rough roads)
- F_grade = m · g · sin θ
- F_aero = ½ · ρ_air(T) · CdA · (v + v_headwind)²
- F_inertia = m · (1 + δ) · a   (δ = rotational inertia factor, prior ≈ 0.05, calibrated)
- P_wheel = (F_roll + F_grade + F_aero + F_inertia) · v
- P_batt = P_wheel / η_drive if P_wheel ≥ 0, else P_wheel · η_regen (capped at max regen power)
- P_total = P_batt + P_aux(T_ambient, ac_on) + P_base
- ΔSoC% = 100 · Σ(P_total · Δt) / E_usable

Mass m = curb mass + passengers × assumed body mass + cargo. Treat the per-passenger mass as a
named, documented assumption. Every term must be reportable separately: the UI breakdown
("AC −11%, climb −6%") is just these terms summed per trip.

**Calibrated parameters θ:** CdA multiplier, Crr multiplier, η_drive, AC/aux coefficient,
E_usable (true usable capacity), driver aggressiveness k. Priors per vehicle model; fleet pooling
= hierarchical (model-level → vehicle-level). Output posterior mean AND uncertainty.

## Uncertainty (forecast.py)

- **Shared trip-level factors** (sampled ONCE per simulated run): driver k, aux multiplier,
  payload error, wind, usable-capacity error, traffic regime.
- **Segment noise** (sampled per segment): small and independent.
- Never sample everything independently per segment: it makes long trips falsely confident.
  Experiment E2 tests exactly this.
- Default N = 2000 runs, configurable; seed all randomness; vectorise with numpy.

## Dispatch (dispatch.py)

- Neo4j proposes candidate charging chains (k-shortest paths). **The graph never decides alone**:
  each candidate is scored by the Monte Carlo with backup plans (divert to an alternate if the
  planned charger fails or SoC falls below the Bingo Line).
- Bingo SoC at route position x = the lowest SoC from which P(reach the planned charger OR a
  reachable alternate, with reserve) ≥ the driver's target confidence.
- Return: GO / NO-GO, success probability, plan, alternates, Bingo Line per km, point of no
  return, and levers (cruise speed, extra charging minutes, other vehicle) that raise confidence.

## Data contracts (implement in schemas.py; bump version on change)

Trip log `verity.trip.v1` (produced by logger/, consumed by engine):
```json
{
  "schema": "verity.trip.v1",
  "trip_id": "uuid",
  "vehicle": {"make": "", "model": "", "variant": "", "nominal_kwh": 0, "type": "car|2w|3w"},
  "driver_id": "anon-01",
  "passengers": 1, "cargo_kg": 0, "ac_on": true,
  "started_at": "ISO-8601", "ended_at": "ISO-8601",
  "points": [{"t": "ISO-8601", "lat": 0.0, "lon": 0.0, "acc_m": 0.0, "speed_mps": null}],
  "soc": [{"t": "ISO-8601", "pct": 0, "source": "tap|voice"}],
  "notes": "",
  "is_simulated": false
}
```

Charger record: `id, name, lat, lon, operator, site_type, connectors[{type, kw}], source_ids[]`.
Report: `charger_id, source_id, status (working|broken|blocked|occupied|no_power), t, evidence`.

## Neo4j graph schema (trust/graph.py)

Nodes: `Charger, Operator, Site, HardwareModel, VehicleModel, Source, RoadSegment`.
Relationships: `(Source)-[:REPORTED {status, t}]->(Charger)`, `(Charger)-[:RUN_BY]->(Operator)`,
`(Charger)-[:AT]->(Site)`, `(Charger)-[:IS]->(HardwareModel)`,
`(VehicleModel)-[:WORKS_WITH {success_rate, n}]->(HardwareModel)`,
`(Charger)-[:LEG {soc_band, p_reach, minutes}]->(Charger)`,
`(RoadSegment)-[:ALTERNATE {p_reach}]->(Charger)`.
LEG is stored per battery band, never as one fixed probability.

## Commands (keep this section updated as commands are created)

- Setup: `uv sync`
- Tests: `uv run pytest`
- API: `uv run uvicorn api.main:app --reload` (check: http://localhost:8000/api/health; `neo4j` says `connected`
  only when Neo4j is the graph in use; `VERITY_GRAPH=memory` forces in-memory)
- Everything from a clean state: `uv run python scripts/run_all.py` (add `--fast` for a plumbing check)
- Experiments: `uv run python experiments/e1_learning_curve.py` ... `e5_trust_recovery.py` -> `results/`
- Data: `uv run python -m verity.sim`, `-m verity.splits` (written once), `-m verity.routing`,
  `uv run python scripts/import_chargers.py`, `scripts/export_api_examples.py`, `scripts/neo4j_smoke_test.py`, `scripts/graph_insights.py`

## How to work in this repo

- Git: commit directly to main. Never create branches. Commit locally; I push myself.
- Use plan mode for any change touching more than two files; wait for approval before editing.
- Small, reviewable steps. Run `uv run pytest` after every change and fix failures before moving on.
- Type hints everywhere; pure functions in `engine/`; no network calls inside the physics or
  forecast code (fetch and cache in `routing.py`, pass data in).
- Physics sanity tests must always pass: zero distance → zero energy; more payload → more
  energy; AC on → more aux energy; regen never exceeds available braking energy.
- When a decision is made (library, assumption, threshold), add one line to `docs/DECISIONS.md`.
- Keep this file under 200 lines; put detail in `docs/`.

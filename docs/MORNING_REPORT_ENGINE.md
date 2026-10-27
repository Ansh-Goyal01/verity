# Morning report: engine track (Ansh)

Written 2026-10-27 night. Everything below is local and committed in git; **nothing was pushed**, no GitHub
command was run, no account was created, no paid API and no Sarvam call was made. Network use: **5 requests in
total** (OSRM route, 2 Open-Meteo elevation, 1 Open-Meteo forecast, 1 OpenStreetMap Overpass), all cached in
`data/cache/`, limit 300.

**One-line status:** A0-A11 are done. From a clean state, `uv run python scripts/run_all.py` finishes with
**ALL STEPS OK in ~20 min** (163 tests passing, E1-E5 regenerated). Three things could not be done tonight and need
you (credentials / dataset): see "What is blocked".

**Start the API in one command** (from `D:\hackfront-verity`): `uv run uvicorn api.main:app --reload`, then open
<http://localhost:8000/api/health> (expect `{"status":"ok","version":"0.1.0","neo4j":"in_memory","is_simulated":true}`).

## What was built, per milestone

| | Built |
|---|---|
| A0 | Repo layout fixed (files were nested in `verity-starter/` and `verity-engine/`; nothing lost, duplicates were byte-identical), `git init`, CLAUDE.md updated for the engine/frontend split. `logger/`, `web/`, `design/` were never created. |
| A1 | uv project, package stubs, pydantic contracts (`verity.trip.v1`, charger record, report), schema tests. |
| A2 | Physics energy model (exact per-term breakdown, vectorised for Monte Carlo) and three generic vehicle classes, every number marked "approximate - verify". |
| A3 | Truth simulator richer than the planner (gusts, driver variability, battery ageing + I^2R loss, stop-and-go, AC duty cycling, payload error, power-dependent efficiency). 126 SIMULATED trips in `data/sim/main`, seeded and byte-reproducible. |
| A4 | Held-out split fixed FIRST (committed alone), Laplace Bayesian calibration + empirical-Bayes fleet pooling, E1. |
| A5 | Monte Carlo forecast with shared trip-level factors, live update from a mid-trip SoC reading, E2. |
| A6 | Trust engine: PLAN-s3-sourced priors, Dawid-Skene (per-report latent state), usage traces, fake-ring detection, simulated adversarial reports, in-memory graph + Neo4j implementation of one interface, E5. |
| A7 | Cached routing client (300-request hard limit, back-off), REAL Noida-Agra route (190.8 km) with elevation and forecast, REAL OpenStreetMap chargers (141 after a 50 m merge), labelled synthetic fallback. |
| A8 | Dispatch: Yen candidate chains over a per-battery-band leg graph, each scored by Monte Carlo with backups; Bingo Line per km; point of no return; levers; refusal; E3, E4. |
| A9 | FastAPI exactly per `docs/API_CONTRACT.md`, demo scenario + replay, voice endpoints (503 without a key), CORS for her 4 origins, 14 real example responses in `docs/api_examples/`. |
| A10 | `scripts/run_all.py`, README, CLAUDE.md Commands. |
| A11 | This report, `docs/AI_USAGE.md`. |

Design decisions (one line each) are in `docs/DECISIONS.md`; progress, bugs found and fixed per milestone in
`docs/PROGRESS_ENGINE.md`.

## Tests

**163 passed, 0 failed** (`uv run pytest`, ~90 s): schemas 18, physics 19, simulator 8, calibration 8, splits 5,
forecast 9, trust 13, graph 13, routing 14, dispatch 18, API 20, voice 18. The tests caught real bugs while
building (an (N, N) broadcasting error that would have mixed Monte Carlo runs, a wrong grade formula giving
834 Wh/km, GPS jitter inflating distance 4 %, "one hundred" parsed as 1, and others, all listed in the progress file).

## Experiment results (all SIMULATED; each chart title says so)

Held-out discipline: settings were chosen on a separate DEV simulator seed; the `main` split (36 test trips) was
evaluated once per run with frozen settings.

### E1: learning curve on held-out trips (9 vehicles, 36 test trips). MAE in SoC points / share inside the "90 %" band

| method | n=1 | n=2 | n=3 | n=5 | n=10 |
|---|---|---|---|---|---|
| Verity physics, class prior | 3.26 / 0.74 | 2.20 / 0.79 | 2.27 / 0.81 | 2.46 / 0.79 | 2.20 / 0.78 |
| Verity physics, fleet-pooled prior | 3.56 / 0.68 | 2.42 / 0.79 | 2.54 / 0.81 | 2.48 / 0.79 | 2.34 / 0.72 |
| Nominal-spec model (no learning) | 5.74 / 0.56 | 5.74 / 0.68 | 5.74 / 0.77 | 5.74 / 0.71 | 5.74 / 0.81 |
| Flat "minus 25%" rule | 8.30 / 0.61 | 8.30 / 0.76 | 8.30 / 0.78 | 8.30 / 0.80 | 8.30 / 0.86 |
| Plain regression (no physics) | 6.56 / 0.07 | 6.98 / 0.06 | 25.23 / 0.05 | 39.69 / 0.18 | 6.66 / 0.50 |

Physics + Bayes beats every baseline on error at every n and is useful from 1-2 trips. **But its "90 %" band covers
only 72-81 %: it is overconfident on held-out vehicles** (dev suggested 86-94 %); I did not re-tune on the test split.
Fleet pooling did not help with two peer vehicles per class. The BMW i3 part is BLOCKED (see below).

### E2: when Verity says X %, do X % arrive? (540 new truth-simulator trips; success = arrive with >= 10 % SoC)

| | shared trip-level factors (Verity) | independent segments (strawman) |
|---|---|---|
| calibration error (ECE) | 0.0613 | 0.1166 |
| Brier score | 0.1066 | 0.1216 |
| 90 % arrival band covers the truth | 0.9185 | 0.2759 |
| mean 90 % band width (SoC points) | 17.9 | 2.6 |
| trips forecast >= 0.9 that arrived | 0.965 (n=142, mean forecast 0.972) | 0.879 (n=272, mean forecast 0.996) |

Honest gaps: Verity's mid-range bins sit slightly below the diagonal (e.g. ~0.85 predicted vs ~0.76 observed), and the
median energy forecast is biased low by 2.6 % / 3.0 % / 8.7 % (small e-car / e-SUV / e-scooter).
One 2000-run forecast takes ~104 ms.

### E3: reliability-blind vs trust-aware planning (200 simulated 190 km trips per scenario)

| chargers down | first stop changed | stranded: blind | stranded: trust-aware | stranded only when blind / only when aware | minutes lost by being aware | plan ms blind / aware |
|---|---|---|---|---|---|---|
| 20 % (~ national non-operational share, PLAN s3) | 70 % | 17.5 % | 4.0 % | 27 / 0 | -0.3 | 266 / 251 |
| 40 % (~ DC fast-charger downtime, PLAN s3) | 75 % | 19.0 % | 6.0 % | 27 / 1 | 0.6 | 273 / 254 |

### E4: fixed shortest-path plan vs dispatch policy (800 simulated trips: 200 per scenario; Wilson 95 % CI)

| chargers down | stranded: fixed plan, no backups | stranded: dispatch (trust-aware + alternates) | only fixed / only dispatch | minutes (not stranded) fixed / dispatch |
|---|---|---|---|---|
| 0 % (control: forecast error only) | 12.0 % [8.2, 17.2] | 5.0 % [2.7, 9.0] | 14 / 0 | 207.1 / 207.0 |
| 10 % | 18.0 % [13.3, 23.9] | 3.5 % [1.7, 7.0] | 29 / 0 | 209.9 / 210.1 |
| 20 % | 27.0 % [21.3, 33.5] | 4.0 % [2.0, 7.7] | 46 / 0 | 214.8 / 215.4 |
| 40 % | 33.0 % [26.9, 39.8] | 6.0 % [3.5, 10.2] | 55 / 1 | 213.8 / 215.6 |

Compute: a full dispatch (plan + Bingo Line + point of no return + levers) took **3.0 s on average** on the real
route with 2000 runs (E4 timing); through the API demo (1500 runs, levers incl. other vehicles) it takes **~5-9 s**
(see `compute_ms` in `docs/api_examples/dispatch_*.json`). The contract's example shows 850 ms; we are slower.

### E5: recovering charger reliability from noisy and fake reports (20 worlds per scenario; error, lower is better)

| scenario | avg star rating | Dawid-Skene | + usage | + ring detection |
|---|---|---|---|---|
| clean | 0.154 | 0.168 | 0.156 | 0.156 |
| lying operator feed | 0.163 | 0.171 | 0.160 | 0.160 |
| ring of 5 | 0.213 | 0.171 | 0.160 | 0.159 |
| ring of 12 | 0.245 | 0.177 | 0.160 | 0.156 |
| ring of 12 + lying feed | 0.254 | 0.180 | 0.165 | 0.160 |
| evasive ring of 12 (10 days) | 0.245 | 0.177 | 0.160 | 0.160 |

On the chargers a 12-account ring attacks: average rating 0.543 vs 0.130 with the full engine. Ring detection:
precision 1.0 / recall 1.0 for the burst ring (all 20 worlds); **recall 0.0 for the evasive ring** (never flagged;
Dawid-Skene + usage still limit the damage: 0.146 on attacked chargers). In a **clean world there is no gain** over
average rating (both sit on a ~0.15 floor because each report is one noisy sample of an intermittent charger).

### The demo (engine output for the scenario inputs; real route, simulated fleet and reports)

| vehicle (start SoC) | decision | confidence | what the engine did |
|---|---|---|---|
| V07, aged battery (82 %) | NO_GO | 85 % | two stops (km 34, 126); levers: cruise at 80 km/h (-> 90 %, +8 min), use V12 instead (-> 90 %, -14 min) |
| V12, healthy (62 %) | GO | 90 % | two stops (km 34 for ~1 min, km 126 for 26 min); the km-126 charger is well evidenced |
| V19 (40 %) | INSUFFICIENT_EVIDENCE | 90 % raw plan, ~70 % pessimistic | plan depends on a charger with no reports and one with a single 41-day-old report: refuses and offers "ask the community to verify" levers |

The scenario inputs (state of health 0.78 / 0.98 / 0.95, start SoC, and which charger has how many reports) were
chosen to illustrate the three decision types; every decision, probability, plan and message is computed.
`docs/api_examples/replay.json` (V19's plan) ends in a DIVERT when the first charger is reported broken.

## What is blocked, and exactly what you must do

1. **Neo4j (live).** The Neo4j implementation of the graph interface exists and is tested only through a fake driver;
   its Cypher has never run on a real server. Create an AuraDB Free instance, put `NEO4J_URI`, `NEO4J_USERNAME`,
   `NEO4J_PASSWORD` in `.env`, then run `uv run python scripts/neo4j_smoke_test.py` (expect "Neo4j smoke test: OK"; it
   inserts three `TEST-` chargers and deletes only those) and `uv run python scripts/import_chargers.py` (loads the 141
   chargers). If the Cypher has a typo, fix it in `engine/verity/trust/graph.py`.
2. **BMW i3 dataset (real-data E1).** Download "Battery and Heating Data in Real Driving Cycles" from IEEE DataPort
   (free login) into `data/raw/bmw_i3/`, then run Prompt 4 of `docs/PROMPTS.md` (inspect the files, write
   `engine/verity/datasets/bmw_i3.py`, fix the 70/30 split). I did not guess the file format. When reporting, say it is
   German driving with heater load.
3. **Sarvam voice.** Put `SARVAM_API_KEY` in `.env`. The client in `engine/verity/voice.py` is mock-tested but its
   endpoint paths, header name and model ids are **unverified**: check them against the current Sarvam docs, then try one
   TTS and one STT call.
4. **Open Charge Map.** Free key from openchargemap.org -> `OCM_API_KEY` in `.env`, then re-run
   `scripts/import_chargers.py` (merges OCM with OSM; today only OSM is used).
5. **Real data.** No real Indian trips or charger audits exist in the repo yet: E1-E5 are simulated. Real logger exports
   go in `data/trips/` (`"is_simulated": false`, anonymised driver ids).

## Connecting your local repo to GitHub without clobbering your teammate's uploads

My commits never touch `logger/`, `web/` or `design/`, so her files and mine are disjoint. The only possible clashes are
root files she may also have uploaded (`README.md`, `.gitignore`). From `D:\hackfront-verity` (I did not run any of this):

```powershell
git remote add origin https://github.com/<your-user>/<repo>.git
git fetch origin
git log --oneline origin/main                          # see her uploads first
git pull origin main --allow-unrelated-histories --no-rebase   # PULL FIRST; resolve any README.md/.gitignore conflict by keeping both
uv run pytest                                          # sanity check after the merge
git push -u origin main
```

Safer variant: `git push -u origin main:engine` and merge via a pull request. Never `git push --force`. LF/CRLF
warnings from git are harmless. Her GitHub Pages origin is already allowed by the API's CORS; a page served over
HTTPS calling `http://localhost:8000` may be blocked by the browser (private-network rules); I could not test that,
so if it happens, open her pages locally (Live Server) or host the API.

## Proposed contract changes (full text in `docs/API_CONTRACT_CHANGES.md`; the contract itself is untouched)

1. `days_since_last` may be `null` (exactly when `n_reports` is 0).
2. `connectors` may be `[]` (OSM rarely tags kW: 1 of 141 chargers has it); the engine then assumes 25 kW and says so in `risk_sources`.
3. The contract's `VITE_API_URL` no longer fits a plain-JS frontend: use a constant or `?api=` in `web/js/api.js`.

## Known weaknesses (please read before quoting any number)

- **All trip and report data are simulated**, and by the same author as the planner: results show the method works on a
  harsher simulator, not that it works on Indian roads. E1 in particular must be repeated on real trips.
- **E1 overconfidence:** the nominal 90 % band covers 72-81 % on held-out vehicles. Needs a trip-level discrepancy term.
- **Fleet pooling gave no gain** (two peer vehicles per class); the CdA / Crr / efficiency split in the breakdown is weakly
  identified from trip totals (the total energy is well predicted; its attribution among terms is not).
- **E5 is optimistic by construction:** usage traces are generated from the same assumed bounce rates the engine uses,
  and ring detection looks for exactly the burst signature the simulated ring has (the evasive ring beats it).
- **Charger data are thin:** only 4 OSM chargers lie within 3 km of the expressway (km 22, 34, 126, 175: a 92 km gap), 90
  of 141 have no operator, 1 has a tagged power, site types are all "unknown". The demo therefore runs on that real
  sparse network plus simulated reports. Charger failures are drawn independently (no operator-level correlation).
- **Dispatch quirks:** V12's plan has a ~1-minute stop at km 34 (a safety top-up: without it the plan misses the target);
  a full dispatch is 3-9 s, not the contract example's 0.85 s; planning constants (speeds per road class, 5 % arrival
  reserve, 80 % charge cap, 6-min stop overhead, 25 kW assumed power) are assumptions marked "approximate - verify".
- **E3/E4 sizes:** 200 trips per scenario (800 for E4), not thousands, because of tonight's runtime; confidence
  intervals are in `results/e4_*.json`. The 0 % row of E4 shows 5-12 % stranding from forecast error alone.
- **Live update** reweights the stored Monte Carlo runs; it does not re-plan or re-score alternates after a diversion.
- **Voice and Neo4j are untested against real services** (see above). `uv run` uses Python 3.13 here (3.12 on `python`);
  the project requires >= 3.11.

## Top 5 next steps

1. **Log real trips** with the logger (even scooter trips) into `data/trips/`, fix a real held-out split, and run E1 on
   them; also do the BMW i3 download + Prompt 4 for the second real test set.
2. **Fix E1 overconfidence** (trip-level discrepancy random effect, re-check coverage on a fresh split) before any
   "[X]% inside our 90% band" goes on a slide; fill the abstract only from these real numbers.
3. **Ground the trust engine in the field:** do the charger audit near Noida, load it into Neo4j (credentials), and check
   the graph queries on a live instance; then replace the simulated demo reports with real ones.
4. **Wire the frontend to the API** (swap mocks for `docs/api_examples/`, then the live endpoints), and make dispatch
   faster (cache the leg graph, fewer Bingo evaluation points) toward the sub-second target.
5. **Go live on voice and strengthen E4:** verify the Sarvam calls, then raise E4 to thousands of trips with
   operator-correlated failures and a real corridor charger list from operator apps.

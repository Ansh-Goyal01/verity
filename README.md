# Verity

Verity estimates whether an EV driver can reach their destination, reports model uncertainty,
and plans charging around estimated charger reliability. Every trip gets a dispatch release:
GO / NO-GO, a plan with reachable alternate chargers, a live **Bingo Line** (the battery % below which the
driver must divert), the point of no return, and levers that raise confidence. Built for hackFront India
2026 (theme T6, problem T6.1 "Will I Make It"). Team: Low Cortisol.

This repo holds the **engine** (Python) and the **HTTP API** the frontend pages call. The frontend
(`logger/`, `web/`, `design/`) is plain HTML/CSS/JS built separately; the two sides meet only at
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) (change proposals: `docs/API_CONTRACT_CHANGES.md`).

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python >= 3.11 and everything in `pyproject.toml`).

```powershell
uv sync
uv run pytest
```

Secrets go in `.env` (copy `.env.example`; never commit `.env`). All keys are optional: without them the
engine runs fully offline and the Neo4j / Sarvam / Open Charge Map parts say so and skip.

| Key | Used for |
|---|---|
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | the Neo4j charger graph: when set and reachable, the API reads chargers, reports and dispatch chains through it; otherwise the in-memory graph gives the same answers. Also `scripts/neo4j_smoke_test.py`, `scripts/import_chargers.py`, `scripts/graph_insights.py` |
| `SARVAM_API_KEY` | voice: `/api/voice/tts` and `/api/voice/stt` (503 without it) |
| `OCM_API_KEY` | Open Charge Map as a second charger source (skipped without it) |

## Run the API

```powershell
uv run uvicorn api.main:app --reload
```

**Check it is reachable:** open <http://localhost:8000/api/health> in a browser. You should see
`{"status":"ok","version":"0.1.0","neo4j":"connected","is_simulated":true}` when Neo4j credentials are in `.env` and
the database answers, or `"neo4j":"in_memory"` otherwise (no credentials, or the connection failed: the engine then
runs on the in-memory graph; `VERITY_GRAPH=memory` forces that). Interactive docs are at
<http://localhost:8000/docs>. From a page: `fetch("http://localhost:8000/api/health").then(r => r.json())`.

Connect the plain-JS frontend by opening `web/console.html?api=http://127.0.0.1:8000`
from a local static server. Without `?api=`, the page uses its labelled mocks. The API must be running.

CORS allows exactly these origins: `null` (a page opened by double-clicking, `file://`),
`http://localhost:5500` and `http://127.0.0.1:5500` (VS Code Live Server) and
`https://ansh-goyal01.github.io` (GitHub Pages). Anything else is refused by the browser.

Every endpoint in the contract is served and validated against it (`api/models.py`, `tests/test_api.py`).
Real engine responses for the demo scenario are saved in [`docs/api_examples/`](docs/api_examples/)
(regenerate with `uv run python scripts/export_api_examples.py`), so the frontend can swap its mocks for them.

The demo runs 1500 Monte Carlo runs per forecast (`VERITY_N_RUNS` changes it).
For measured dispatch timings and machine details, see [the final numbers pack](docs/RESULTS_FOR_DECK.md).
Historical Aura timings in engine progress notes are not measurements on this machine.

## Tests and experiments

```powershell
uv run pytest                                   # all tests
uv run python scripts/run_all.py                # all tests + all experiments from a clean state (~20 min)
uv run python scripts/run_all.py --fast         # the same steps with tiny sizes (plumbing check only)
```

| Experiment | Command | Output (in `results/`) |
|---|---|---|
| E1 learning curve on held-out trips | `uv run python experiments/e1_learning_curve.py` | `e1_learning_curve_simulated.*` |
| E2 calibration of arrival probability | `uv run python experiments/e2_calibration.py` | `e2_calibration_simulated.*` |
| E3 reliability-blind vs trust-aware planning | `uv run python experiments/e3_reliability_planning.py` | `e3_reliability_planning_simulated.*` |
| E4 fixed shortest path vs dispatch policy | `uv run python experiments/e4_dispatch_vs_fixed.py` | `e4_dispatch_vs_fixed_simulated.*` |
| E5 trust recovery from noisy and fake reports | `uv run python experiments/e5_trust_recovery.py` | `e5_trust_recovery_simulated.*` |

Every chart title says "(simulated)". Other scripts: `uv run python -m verity.sim` (simulated trips),
`uv run python -m verity.splits` (the fixed held-out split: written once, never rewritten),
`uv run python -m verity.routing` (cached Noida-Agra route), `uv run python scripts/import_chargers.py`
(OpenStreetMap chargers), `uv run python scripts/neo4j_smoke_test.py`,
`uv run python scripts/graph_insights.py` (route coverage gaps and fake-report ring candidates, from the graph).

## Data: what is real and what is simulated

- **Real:** the Noida-Agra route, elevation and weather forecast (OSRM, Open-Meteo; cached in `data/cache/`,
  processed copy in `data/processed/`) and the OpenStreetMap / Open Charge Map charger location directory.
- **Simulated (labelled `is_simulated: true`):** all trips (`data/sim/`), the demo fleet, the charger reports,
  and E1-sim through E5. There is no real Indian trip validation, field audit, driver interview or fleet pilot.
- **Needs a download (the raw files are not in git):** the BMW i3 dataset in `data/raw/bmw_i3/`. Its loader, notes
  (`docs/BMW_I3_NOTES.md`) and fixed 70/30 split (`data/splits/bmw_i3_split.json`) are done; E1 on it is not.
  BMW evaluation: **NOT DONE (dataset not on this machine)**. Sarvam live verification is blocked by a missing key.
  The committed charger directory already includes OCM; refreshing it needs a key.
  Neo4j credentials are optional; current status is in [FINAL_REPORT.md](docs/FINAL_REPORT.md).

## Layout

- `engine/verity/` physics, vehicle priors, simulator, calibration, forecast, dispatch, trust engine, routing, voice
- `api/` FastAPI app (`main.py`), engine service (`service.py`), contract models (`models.py`)
- `experiments/` E1-E5; `scripts/` import, smoke test, API examples, `run_all.py`
- `docs/` plan, API contract, decisions, progress, AI usage; `results/` charts and JSON; `data/` see above

Golden rules (see `CLAUDE.md`): never invent data, label every simulation, never touch the held-out split while
building, physics first, probabilities come from the model rounded to 5 %, refuse rather than guess.

## Graph probability semantics

For an offline run in PowerShell, set `$env:VERITY_GRAPH = "memory"` before starting uvicorn.
This skips Neo4j connections even if credentials exist; `/api/health` reports `in_memory`.
The existing `tests/test_api_graph.py` checks the memory backend (R6a reused).

Graph `ALTERNATE.p_works` is the trust model's probability that the charger works, not an energy
reach probability. Sync sets `p_works` and explicitly removes obsolete `p_reach` on each ALTERNATE.
`LEG.p_reach` remains a genuine Monte Carlo energy-reach frequency at the battery-band midpoint:
`mean(midpoint - sampled_leg_energy >= arrival_reserve)`, using the destination/stop reserve as appropriate.
It is conditional on the forecast and does not include charger reliability or guarantee a whole trip.
The API's per-vehicle alternate `p_reach` is also unchanged. Live Aura migration is not verified on
this machine because Neo4j credentials are absent; fake-driver tests verify the Cypher write/read/filter.

## Evidence status and limitations

See [EVIDENCE_STATUS.md](docs/EVIDENCE_STATUS.md) for REAL / SIMULATED / NOT DONE provenance.
There is **no real Indian validation data**. The three demo vehicles are constructed scenarios whose
inputs were selected from a sweep to show three outcomes (DECISIONS A13); charger reports are simulated.
Almost no directory charger has tagged power, so dispatch assumes 25 kW (approximate - verify).
Only K_CANDIDATES=6 chains ranked by cost are scored; denser networks can crowd out better plans.
Same-site backups are treated as independent (optional R9 not performed), so common power/gate/closure
failures can overstate success. BMW i3 data describes German driving with heater/AC load, not Indian
conditions; its real-data experiment has not run here. The voice client is mock-tested only: no Sarvam
key, live endpoint verification or TTS/STT calls in this run. The plan cache is unbounded, HTTP endpoints
are not rate-limited, and the Neo4j driver has no socket read timeout.

R4a's training-only E1 widening still undercovers individual-subset forecasts. Its mixture coverage is
a different quantity and must be labelled as such. This experiment-only correction does not change live
engine forecasts. Trips share vehicles, so trip-level Wilson intervals are descriptive, not adjusted
for clustering. E5 usage traces share model assumptions, and its ten-day evasive ring is missed.

# Verity

Verity tells an EV driver, with an honest and tested confidence level, whether they will reach their
destination, and plans charging around chargers that actually work. Every trip gets a dispatch release:
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

CORS allows exactly these origins: `null` (a page opened by double-clicking, `file://`),
`http://localhost:5500` and `http://127.0.0.1:5500` (VS Code Live Server) and
`https://ansh-goyal01.github.io` (GitHub Pages). Anything else is refused by the browser.

Every endpoint in the contract is served and validated against it (`api/models.py`, `tests/test_api.py`).
Real engine responses for the demo scenario are saved in [`docs/api_examples/`](docs/api_examples/)
(regenerate with `uv run python scripts/export_api_examples.py`), so the frontend can swap its mocks for them.

A full dispatch (plan, Bingo Line, levers) takes a few seconds on a laptop: the demo runs 1500 Monte Carlo runs
per forecast (`VERITY_N_RUNS` changes it): about 2.5 s on the in-memory graph and about 5 s through Neo4j (up to ~10 s when AuraDB is slow). The
first start takes ~3 s to calibrate the demo fleet, plus the graph sync when Neo4j is used.

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
  processed copy in `data/processed/`) and the OpenStreetMap charger directory.
- **Simulated (labelled `is_simulated: true`):** all trips (`data/sim/`), the demo fleet, the charger reports,
  and every experiment. No real EV trips or charger audits are in this repo yet; real logs go in `data/trips/`.
- **Needs a download (the raw files are not in git):** the BMW i3 dataset in `data/raw/bmw_i3/`. Its loader, notes
  (`docs/BMW_I3_NOTES.md`) and fixed 70/30 split (`data/splits/bmw_i3_split.json`) are done; E1 on it is not.
  **Blocked until you add them:** a Sarvam key and an Open Charge Map key.
  Neo4j credentials are optional: without them the in-memory graph is used. See `docs/MORNING_REPORT_ENGINE.md`.

## Layout

- `engine/verity/` physics, vehicle priors, simulator, calibration, forecast, dispatch, trust engine, routing, voice
- `api/` FastAPI app (`main.py`), engine service (`service.py`), contract models (`models.py`)
- `experiments/` E1-E5; `scripts/` import, smoke test, API examples, `run_all.py`
- `docs/` plan, API contract, decisions, progress, AI usage; `results/` charts and JSON; `data/` see above

Golden rules (see `CLAUDE.md`): never invent data, label every simulation, never touch the held-out split while
building, physics first, probabilities come from the model rounded to 5 %, refuse rather than guess.

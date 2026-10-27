# Verity

Verity tells an EV driver, with an honest and tested confidence level, whether they will reach
their destination, and plans charging around chargers that actually work. Built for hackFront
India 2026 (theme T6, problem T6.1 "Will I Make It"). Team: Low Cortisol.

This repo holds the **engine** (Python) and the **HTTP API** that the frontend pages call. The
frontend (`logger/`, `web/`, `design/`) is plain HTML/CSS/JS built separately; the two sides meet
only at [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md).

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run pytest
```

Secrets go in `.env` (copy `.env.example`; never commit `.env`).

## Layout

- `engine/verity/` physics, vehicle priors, calibration, forecast, dispatch, trust engine, schemas
- `api/` FastAPI app serving the engine
- `experiments/` E1-E5 scripts that write charts and JSON to `results/`
- `docs/` plan, decisions, progress and the API contract
- `data/` raw (gitignored), real trips, simulated data, splits, cache (gitignored)

Golden rules (see `CLAUDE.md`): never invent data, label every simulation, never touch the held-out
split while building, refuse rather than guess.

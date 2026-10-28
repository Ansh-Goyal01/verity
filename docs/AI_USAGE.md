# AI tool use (hackathon disclosure)

| Date | Tool | What it was used for |
|---|---|---|
| 2026-10-27 | Claude Code (model: Claude Sonnet 5.5) | Overnight autonomous engine build driven by `docs/TASKS_ANSH.md`, milestones A0-A11: repo layout fix and scaffold; physics energy model and generic vehicle priors; truth simulator; Bayesian calibration and E1; Monte Carlo forecast and E2; trust engine (priors, Dawid-Skene, ring detection), graph interface with in-memory and Neo4j implementations, and E5; routing, real route fetch and OpenStreetMap charger import; dispatch (candidate chains, Bingo Line, levers, refusal), E3 and E4; FastAPI service exactly per `docs/API_CONTRACT.md`, voice client (mock-tested only), API examples export; test suite, `scripts/run_all.py`, README, this disclosure and the morning report. |
| 2026-10-27 | Third-party services (no AI) | Open-Meteo (elevation, weather forecast), an OSRM-compatible public router, OpenStreetMap via Overpass: fetched once, cached in `data/cache/`, request count logged. Open Charge Map, Sarvam and Neo4j were NOT called (no keys/credentials tonight). |
| 2026-10-27 | Python libraries | numpy, scipy, pandas, pydantic, FastAPI, uvicorn, httpx, neo4j driver, matplotlib, pytest (as listed in `pyproject.toml`); `pypdf` was run once, outside the project, to read the plan PDF. |
| 2026-10-28 | Claude Code (model: Claude Sonnet 5.5) | A12, Neo4j made load-bearing: trust and dispatch now read chargers, reports and plan legs through the `ChargerGraph` interface backed by Neo4j (in-memory fallback), batched idempotent sync, parity/fallback/ring/coverage tests, `scripts/graph_insights.py`, docs. A code-reviewer and a security-reviewer subagent reviewed the diff. |
| 2026-10-28 | Third-party services (no AI) | Neo4j AuraDB Free was called for the first time (live parity tests, API runs, insights script). It receives the charger directory, the SIMULATED demo reports and short-lived plan edges; no personal data. Credentials stay in `.env` and are never logged or committed. |

How the AI work was checked: every module has tests (`uv run pytest`), every experiment is a script that
writes its own numbers and charts, and the tests caught and fixed real bugs during the build (listed in
`docs/PROGRESS_ENGINE.md`). No number in the docs or charts was typed by hand; every one is produced by a
script in this repo. All trip, report and fleet data in this repo is SIMULATED and labelled; the route and
charger locations are real public data.

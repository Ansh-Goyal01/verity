# AI tool use (hackathon disclosure)

| Date | Tool | What it was used for |
|---|---|---|
| 2026-10-27 | Claude Code (model: Claude Sonnet 5.5) | Overnight autonomous engine build driven by `docs/TASKS_ANSH.md`, milestones A0-A11: repo layout fix and scaffold; physics energy model and generic vehicle priors; truth simulator; Bayesian calibration and E1; Monte Carlo forecast and E2; trust engine (priors, Dawid-Skene, ring detection), graph interface with in-memory and Neo4j implementations, and E5; routing, real route fetch and OpenStreetMap charger import; dispatch (candidate chains, Bingo Line, levers, refusal), E3 and E4; FastAPI service exactly per `docs/API_CONTRACT.md`, voice client (mock-tested only), API examples export; test suite, `scripts/run_all.py`, README, this disclosure and the morning report. |
| 2026-10-27 | Third-party services (no AI) | Open-Meteo (elevation, weather forecast), an OSRM-compatible public router, OpenStreetMap via Overpass: fetched once, cached in `data/cache/`, request count logged. Open Charge Map, Sarvam and Neo4j were NOT called (no keys/credentials tonight). |
| 2026-10-27 | Python libraries | numpy, scipy, pandas, pydantic, FastAPI, uvicorn, httpx, neo4j driver, matplotlib, pytest (as listed in `pyproject.toml`); `pypdf` was run once, outside the project, to read the plan PDF. |
| 2026-10-28 | Claude Code (model: Claude Sonnet 5.5) | A12, Neo4j made load-bearing: trust and dispatch now read chargers, reports and plan legs through the `ChargerGraph` interface backed by Neo4j (in-memory fallback), batched idempotent sync, parity/fallback/ring/coverage tests, `scripts/graph_insights.py`, docs. A code-reviewer and a security-reviewer subagent reviewed the diff. |
| 2026-10-28 | Claude Code (model: Claude Sonnet 5.5) | BMW i3 real-data groundwork (no fitting): read and profiled the TU Munich dataset files with throwaway scripts, wrote `docs/BMW_I3_NOTES.md`, the `bmw_i3` loader with tests on a synthetic fixture, and the fixed 70/30 split by trip id. The raw data stays out of git. |
| 2026-10-28 | Third-party services (no AI) | Neo4j AuraDB Free was called for the first time (live parity tests, API runs, insights script). It receives the charger directory, the SIMULATED demo reports and short-lived plan edges; no personal data. Credentials stay in `.env` and are never logged or committed. |

How the AI work was checked: every module has tests (`uv run pytest`), every experiment is a script that
writes its own numbers and charts, and the tests caught and fixed real bugs during the build (listed in
`docs/PROGRESS_ENGINE.md`). No number in the docs or charts was typed by hand; every one is produced by a
script in this repo. All trip, report and fleet data in this repo is SIMULATED and labelled; the route and
charger locations are real public data.

| 2026-10-29 | Codex CLI | R0 takeover: read repository instructions and handoff, verified main/HEAD and baseline, wrote finish plan. No subagents or external services used. |
| 2026-10-29 | Codex CLI | R1: fixed live-update test setup and checked seeds 0?11 in a scratch script; full pytest. |
| 2026-10-29 | Codex CLI | R2: two permitted web fixes; Node syntax and scratch VM against local uvicorn. No external service calls. |
| 2026-10-29 | Codex CLI | R6: graph ALTERNATE probability rename and stale-property removal, migration/query test, memory switch verification and docs. No live Neo4j calls (credentials absent). |
| 2026-10-29 to 30 | Codex CLI | R4a: preserved original E1 artifacts, added experiment-only training-LOO discrepancy variance, 20 training subset draws/k, unique-trip and per-subset 50/80/90% coverage/Wilson reporting, frozen audit/evaluation reuse and three statistical bookkeeping tests. Ran one final held-out simulated evaluation; no paid service calls. |
| 2026-10-30 | Codex CLI | R7: evidence classification, honesty/limitations pass, historical status notices and wording audit. No external calls. |

## Finish-track disclosure additions

The rows below append to the historical record; they do not claim the old services were called on this machine.
The two chat/frontend rows were supplied by the takeover instructions. Teammate tool names remain unconfirmed.
A13's prior commit history records the work, but does not identify its assistant tool; confirm that attribution
with the teammate rather than inventing a Claude Code run. A12 and BMW groundwork tool attribution already
appears in the original rows above. Current finish-track work uses Codex CLI only, with no subagents.

| Date | Tool | What it was used for |
|---|---|---|
| 2026-10-26 to 28 | Claude (claude.ai chat, by Ansh) | Research of the problem statements, literature and market sources; the build plan and the frontend brief; the Claude Code task files and prompts; debugging of setup problems (Neo4j connection, TLS, credentials). |
| 2026-10-26 to 28 | AI assistants used by the frontend teammate: [TEAMMATE: add tool names] | Trip logger, fleet console, driver view and design system, generated from docs brief and tested and uploaded by her. |
| 2026-10-28 | Historical A13 work; assistant tool not recorded in commits (team confirmation required) | Pinned simulated demo scenario inputs, scenario sweeps, regression tests, examples and E3/E4 reruns. See 9eb15b1, 2e07e70 and DECISIONS A13. No claim that Codex performed this historical work. |
| Historical import, before takeover | Open Charge Map (non-AI service) | Directory import in commit 31592f9, merged with OSM. Source status is fetched. No OCM request in this finish run; no key on this machine. |
| 2026-10-28 (historical) | IEEE DataPort BMW i3 dataset (non-AI source) | Download/profile documented in BMW_I3_NOTES.md and commit 7dc5f10; raw files absent on takeover machine. Loader and fixed split reused. No dataset download in this finish run. |
| 2026-10-30 | Codex CLI | R8 disclosure and BMW citation/download instructions; preserved existing rows and explicitly marked missing teammate/A13 tool attribution. No Claude Code, live Neo4j, Sarvam, OCM or dataset-download calls in this run. |

Historical documentation includes numbers supplied by earlier scripts; finish reports are generated from saved
artifacts by a reporting script. REAL source geography does not establish real operational reliability.
| 2026-10-30 | Codex CLI | R5: documented blocker and manual steps; no external or paid call, no raw-data/test-trip read. |
| 2026-10-30 | Codex CLI | R4b: documented blocker and manual steps; no external or paid call, no raw-data/test-trip read. |
| 2026-10-30 | Codex CLI | R10: full run_all (E1 frozen evaluation verification, full E2-E5), fresh local API exports, script-measured demo seed ranges/directory counts/uncached timing, generated final/deck reports, full pytest. Neo4j smoke skipped; no external services or paid calls. |
| 2026-10-30 | Codex CLI | R11: final scope/split/raw-data audit, full pytest and local wrap-up commit. Optional R9 skipped. No external services or push. |

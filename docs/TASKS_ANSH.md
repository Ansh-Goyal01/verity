# Engine track (Ansh) — overnight autonomous build

You are building the Verity engine overnight while I sleep. Work through the milestones below in
order, without asking me anything. I will read your report in the morning.

## Context

- Read CLAUDE.md first and follow it strictly, especially the Golden rules.
- Read docs/PLAN.md (or the plan PDF if the .md is missing) for the full plan, docs/PROMPTS.md
  for detailed specs referenced below, and docs/API_CONTRACT.md for the API you must serve.
- This is a Windows machine (PowerShell). Prefer cross-platform Python scripts over shell scripts.
- **Work is split across two people.** A teammate builds `logger/`, `web/` and `design/` as plain
  HTML/CSS/JS pages (no build tools) and uploads them straight to the GitHub repo. Her pages read
  data only through her `web/js/api.js`, which calls the endpoints in docs/API_CONTRACT.md. You own
  everything else. Never create, edit or delete anything in `logger/`, `web/` or `design/`, not
  even placeholder files: her uploads must land without conflicts.

## How to work

1. Create docs/PROGRESS_ENGINE.md with the milestone checklist below. Before each milestone
   re-read it and CLAUDE.md; after each, tick it, note what was built, and commit with the message
   "A<n>: <summary>".
2. Never stop to ask questions. If something is ambiguous, choose the simplest option consistent
   with CLAUDE.md, add one line to docs/DECISIONS.md, and continue.
3. If a milestone is blocked (missing credentials, data, network or a tool), write the reason in
   PROGRESS_ENGINE.md, build everything around the blocker that you can (interfaces, mocks,
   tests), and move on.
4. Run `uv run pytest` after every milestone and never leave failing tests. Run every script you
   write.
5. Ending your turn while milestones remain undone and unblocked is a failure. Keep going until
   every milestone is done or blocked, then write the morning report (A11).

## Hard limits

- Work only inside this folder. Never delete anything outside it.
- Never print, edit or commit .env values. Never commit .env.
- Git stays local only: commit freely, but never run git push, git remote add, or any gh/GitHub
  command, and never enable GitHub Pages. I will push everything myself tomorrow.
- Do not create accounts or call paid APIs. Do not call the Sarvam API tonight: build its client
  and test it against a mock.
- Free public APIs (routing, elevation, weather, OpenStreetMap, Open Charge Map): cache every
  response in data/cache/, keep total requests under 300, back off on errors, and fall back to
  clearly labelled synthetic data if they fail.
- Do not invent facts. Vehicle priors are generic classes (e.g. "compact e-SUV, ~45 kWh") with
  every number commented "approximate - verify"; never present them as official specs.
- All generated data is labelled simulated per CLAUDE.md. Never write a result anywhere that a
  script did not actually produce.
- Stick to the dependencies in CLAUDE.md. Add a new one only if essential, and log why in
  DECISIONS.md.
- Never change docs/API_CONTRACT.md. If the engine truly needs a different shape, keep serving the
  contract shape and record the proposed change in docs/API_CONTRACT_CHANGES.md.

## Milestones

**A0 Pre-flight.** Record python, uv, node and git versions in PROGRESS_ENGINE.md. If files sit in
a verity-starter/ subfolder or the wrong place, fix the layout to match CLAUDE.md
(docs/PROMPTS.md, docs/PLAN.*) without losing anything; delete verity-starter/ only once empty.
Run git init if needed. Then update CLAUDE.md to match the split: in the repo layout, `logger/`,
`web/` and `design/` are plain HTML/CSS/JS owned by the frontend teammate; remove Vite, React and
the `npm run dev` command from CLAUDE.md, and add one line pointing to docs/API_CONTRACT.md as the
interface between the engine and her pages.

**A1 Scaffold.** Prompt 1 of docs/PROMPTS.md, except: do NOT create `logger/`, `web/` or
`design/` at all.

**A2 Physics and vehicle priors.** Prompt 3 of docs/PROMPTS.md, plus engine/verity/vehicles.py with
three generic classes (small e-car, compact e-SUV, e-scooter) including parameter uncertainty.

**A3 Truth simulator.** engine/verity/sim.py generating trips deliberately RICHER than the planning
model (wind gusts, driver variability, battery ageing, stop-and-go traffic, AC duty cycles,
payload variation). Output verity.trip.v1-compatible JSON plus hidden ground-truth parameters into
data/sim/. Seeded and reproducible.

**A4 Calibration and E1.** Bayesian calibration (explain the method in DECISIONS.md) on simulated
trips. Fix the held-out split FIRST. Learning curve on 1, 2, 3, 5 and 10 trips against the three
baselines in Prompt 6. If data/raw/bmw_i3/ exists, also do Prompt 4 and run E1 on it; otherwise
mark that part blocked. Save results/e1_*.png and results/e1_*.json, titled "(simulated)" where
applicable.

**A5 Forecast and E2.** Monte Carlo with shared trip-level factors per CLAUDE.md, plus a live update
from a new mid-trip SoC reading. E2: calibration chart comparing independent-segment vs
shared-factor sampling on truth-simulator trips.

**A6 Trust engine and E5.** Priors by operator and site type (each value commented with its source
from docs/PLAN.md section 3 and marked approximate), Dawid-Skene source trust in numpy,
usage-trace evidence, simulated adversarial reports including a coordinated fake-report ring and a
lying operator feed, and ring detection. E5: recovery error vs average rating. Implement an
in-memory graph behind an interface, and a Neo4j implementation of the same interface in
trust/graph.py, with a smoke-test script that skips cleanly when .env credentials are missing.

**A7 Routing and chargers.** Fetch and cache one real route (Noida to Agra via the Yamuna
Expressway) with elevation and a weather forecast, split into segments for physics.py. Import
real NCR chargers per Prompt 7. Labelled synthetic fallback if the network fails.

**A8 Dispatch, E3 and E4.** Candidate charging chains (k-shortest paths on the in-memory graph),
each scored by Monte Carlo with backup plans; Bingo Line per km; point of no return; levers
(cruise speed, extra charging minutes, other vehicle); refusal when evidence is too thin.
E3: reliability-blind vs trust-aware planner. E4: fixed shortest-path plan vs dispatch policy
(stranding rates). Report compute time per plan.

**A9 API, exactly per docs/API_CONTRACT.md.** FastAPI in api/main.py serving every endpoint in the
contract, backed by the real engine, including the demo scenario (one outstation booking, three
vehicles: aged battery, healthy, depends on a stale charger) and the replay. Voice endpoints proxy
to a Sarvam client that returns 503 without a key. Enable CORS for the origins her pages use: "null"
(pages opened by double-clicking, file://), http://localhost:5500, http://127.0.0.1:5500 (VS Code
Live Server) and https://ansh-goyal01.github.io (GitHub Pages). Add a /api/health check page note
to the README so she can confirm the API is reachable. Write tests that validate every response against the contract shapes.
Then write scripts/export_api_examples.py that calls each endpoint and saves real responses to
docs/api_examples/*.json, so the frontend can swap its mocks for real engine output tomorrow.

**A10 Quality pass.** One command (scripts/run_all.py) that runs all tests and all experiments from
a clean state; fix anything broken; update README and the Commands section of CLAUDE.md.

**A11 Morning report.** docs/MORNING_REPORT_ENGINE.md with: what was built per milestone; test
count and status; each experiment's actual numbers (labelled simulated where they are); what is
blocked and exactly what I must do (credentials, dataset download); how to start the API in one
command; how to connect my local repo to GitHub without clobbering the teammate's uploads (pull
first, then push); any proposed contract changes; known weaknesses; top 5 next steps. Add tonight's work to
docs/AI_USAGE.md. Final commit.

Start with A0 now.

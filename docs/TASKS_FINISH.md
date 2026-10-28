# Finish track — close every remaining item, with NO real Indian data

You are finishing Verity for the hackFront India submission. Work through the milestones R0-R11 in order,
without asking me anything. I will read your report later.

## Situation (read this first, it changes what claims are allowed)

- Prototype deadline: Sat 31 Oct 2026, 11:59 PM IST (internal target: submit by 6 PM). Today is Wed 28 Oct.
- We will NOT have any real Indian data: no real Indian trips, no charger audit, no driver interviews.
  Do not plan for them and do not write anything that implies them.
- The only REAL measured evidence we can have is: (1) the public BMW i3 trips (TU Munich, Germany, heater
  load, DOI 10.21227/6jr9-5235), and (2) real route, elevation, weather and charger-LOCATION data
  (Open-Meteo, a public router, OpenStreetMap, Open Charge Map). Everything else (trips for the simulator
  experiments, charger reports, the demo fleet and its scenario inputs) is SIMULATED and must say so.
- Allowed phrasing: "validated on public real data from Germany", "simulated", "scenario". Forbidden
  phrasing: "accurate on Indian roads", "validated in India", "audited chargers", "drivers told us".
- Read first: CLAUDE.md, docs/MORNING_REPORT_ENGINE.md, docs/DECISIONS.md, docs/PROGRESS_ENGINE.md,
  docs/API_CONTRACT.md, and docs/BMW_I3_NOTES.md if it exists.

## How to work

1. Create docs/PROGRESS_FINISH.md with the milestone checklist. Before each milestone re-read it and
   CLAUDE.md; after each, tick it, note what was built, and commit with "R<n>: <summary>".
2. Never stop to ask questions. If something is ambiguous, choose the simplest option consistent with
   CLAUDE.md, add one line to docs/DECISIONS.md, and continue.
3. If a milestone is blocked (missing data, key, network), write the exact reason and the exact manual
   step in PROGRESS_FINISH.md, build everything around the blocker that you can, and move on.
4. Run `uv run pytest` after every milestone. Never leave failing tests.
5. Ending your turn while milestones remain undone and unblocked is a failure.

## Hard limits

- Commit directly to `main`. Never create branches. Local commits only: never git push, never add a
  remote, never use gh. I push myself.
- Never print, edit or commit .env values. Never commit raw data (data/raw/ is gitignored).
- Do not invent data or results. Every number in a report comes from a script that produced it. Label
  every result REAL or SIMULATED, with n and a confidence interval where one exists.
- Held-out splits are written once and never changed. Tune only on training or dev data. Evaluate a
  held-out split once per final run. Report the result whatever it is; never re-tune to improve it.
- Do not change the energy model, trust engine, thresholds or experiments EXCEPT where a milestone below
  explicitly says to. Scenario inputs are not to be re-tuned without telling me.
- `logger/`, `design/` and `web/` belong to the frontend teammate. The only edits you may make there are
  the two named in R2.
- Free public APIs: cache every response in data/cache/ and keep total requests under 300. The only paid or
  keyed calls allowed: one TTS call and one STT call to Sarvam in R5. Neo4j is allowed.
- If a Neo4j or network call hangs or fails because of the network, skip it with a clear note. Do not loop.
- Stick to the dependencies in CLAUDE.md; log any addition and why in docs/DECISIONS.md.

## Milestones

**R0 Pre-flight.** Confirm you are on main; `git branch` must list only main (delete any fully merged local
branch with `git branch -d`; if one is not fully merged, tell me and leave it). `git status` must be clean
or contain only files you can explain. Add one line under "How to work in this repo" in CLAUDE.md:
"Git: commit directly to main. Never create branches. Commit locally; I push myself." Record python and uv
versions and the baseline `uv run pytest` result in PROGRESS_FINISH.md.

**R1 Fix a fragile test.** In tests/test_forecast.py, `test_live_update_reacts_to_a_battery_reading` sets
`start = float(np.median(r.total_used)) + 10.0 + 12.0` (median arrival 22 %), which saturates all three
confidences near 1.0, so the strict ordering assertion passes or fails with the platform (it fails on a clean
Linux install for 8 of 10 seeds). Change it to `start = float(np.median(r.total_used)) + 10.0` (median
arrival AT the 10 % reserve) with a comment explaining why, keep every assertion, and check that it passes
for seeds 0-11 (loop once in a scratch script, do not add a slow test). Run the full suite.

**R2 Two tiny frontend edits (the only edits allowed in web/).** Both must be idempotent.
(a) web/js/api.js line 1 is `const API_URL = ""; // Empty means mock mode.` Replace it with
`const API_URL = (typeof location !== "undefined" && new URLSearchParams(location.search).get("api")) || ""; // Empty means mock mode. Open a page as console.html?api=http://127.0.0.1:8000 to use the real engine.`
(b) In web/js/console.js the planned-stop tooltip prints
`${stop.evidence.n_reports} reports, last ${stop.evidence.days_since_last} days ago`. When
`days_since_last` is null (an unreported charger, which V12's km-119 stop is) it prints "last null days
ago". Make it print `last report unknown` in that case, exactly like the charger tooltip already does.
Run `node --check` on both files, add the `?api=` usage to web/README.md, and verify with the real API:
start `uv run uvicorn api.main:app`, then in a scratch Node script load web/js/api.js with a fake
`location` of `?api=http://127.0.0.1:8000` and call getScenario and getDispatch for V12.

**R3 BMW i3 loader and fixed split (needs the dataset).** Check `data/raw/bmw_i3/`. If it is empty or
missing, mark R3 and R4b BLOCKED with the manual steps (download "Measurement Data.zip" from IEEE DataPort,
DOI 10.21227/6jr9-5235, unzip into data/raw/bmw_i3/) and continue with R4a. If an earlier run already
wrote a real loader and data/splits/bmw_i3_split.json, verify and reuse them; NEVER re-split. Otherwise:
read readme.txt first; write docs/BMW_I3_NOTES.md (formats, columns, units, sampling rate, number of trips,
how SoC is defined, which signals can drive our physics model, data-quality problems); replace the stub in
engine/verity/datasets/bmw_i3.py with a real loader mapping each trip to a common format (time, speed,
elevation, ambient temperature, battery voltage, current, SoC, heating or auxiliary power); test it with a
tiny fixture in tests/fixtures/ that mimics the real column layout (never commit raw data); create ONE fixed
train/test split with a set seed (30 % of trips held out, by trip id, using engine/verity/splits.py) saved
to data/splits/bmw_i3_split.json; add a decision note. Fit nothing yet.

**R4 E1: make the 90 % band honest, then add the real-data result.**
(4a, simulated). Today E1's nominal 90 % band covers only 72-81 % of held-out simulated trips (overconfident).
First copy the current results to results/e1_learning_curve_simulated_BEFORE_fix.json (and .png) and keep
them. Then add a trip-level model-discrepancy variance to the predictive distribution (unmodelled traffic,
driver, wind, battery effects shared across a trip), estimated from the leave-one-trip-out residuals of the
TRAINING trips, not hand-set. A split-conformal widening is acceptable if you explain why. Choose any
hyperparameter on the dev seed / training trips only. Evaluate the main held-out split ONCE. Report MAE in
SoC points for 1, 2, 3, 5, 10 calibration trips (mean over at least 20 random training subsets) against the
same baselines as before, and the share of held-out outcomes inside the nominal 50 / 80 / 90 % bands with
Wilson 95 % intervals. If coverage is still below nominal, say so; do not tune on the held-out split.
(4b, REAL; only if R3 is done). Vehicle parameters come from the dataset readme and the authors' paper
(https://www.mdpi.com/2032-6653/11/2/41): fetch them, do not use memory, and comment every number
"approximate - verify". Predict each held-out trip's SoC change two ways: (A) planner path: route geometry
and elevation, mean ambient temperature and cabin heating state only, no measured speed trace, through the
same forecast machinery, giving a predictive distribution; (B) oracle path: the measured speed trace and
measured auxiliary power, which isolates the physics model. Calibrate on k = 1, 2, 3, 5, 10 TRAINING trips
(at least 20 random subsets per k) and evaluate held-out trips once. Metrics: MAE in SoC points, coverage of
the nominal 50 / 80 / 90 % bands (Wilson CI), against the baselines (nominal-spec consumption, flat minus-25 %
rule, plain regression on trip features). If the dataset's SoC is unusable or too coarse, use integrated
battery energy as ground truth, convert with the documented usable capacity, and say so. Save
results/e1_bmw_i3_real.json and .png, titled "(REAL data: BMW i3, Germany, heater load; not Indian
conditions)". If (A) is not feasible in the time, deliver (B) and state that (A) is not done.

**R5 Sarvam voice: verify, do not assume.** Needs SARVAM_API_KEY in .env; if it is missing, mark BLOCKED and
continue. Read engine/verity/voice.py. Fetch the current Sarvam API docs and verify endpoint paths, auth
header name, request fields and model ids for text-to-speech and speech-to-text. Fix anything wrong. Make
exactly ONE TTS call (a short Hindi sentence) and ONE STT call on that audio. Stop and report if either
returns an auth error. Record what you verified in docs/DECISIONS.md. Never print the key.

**R6 Housekeeping.** (a) Add the env switch `VERITY_GRAPH=memory` that forces the in-memory graph; /api/health
must then report "in_memory"; document it in the README and add a test. (b) Rename the property `p_reach`
to `p_works` on ALTERNATE edges only (it stores the alternate's P(works), not an energy-reach probability):
code, Neo4j sync, tests, docs, and make the sync `REMOVE` the old property so no stale value remains in
Aura. Check whether LEG's `p_reach` is a genuine reach probability; leave it if so, and say which it is.

**R7 Honesty pass for the no-real-data situation.**
- Write docs/EVIDENCE_STATUS.md: a table with three groups, REAL (BMW i3 trips, route, elevation, weather,
  charger locations, published reliability figures used as priors with their sources), SIMULATED (E1-sim to
  E5, charger reports, the demo fleet, the scenario inputs, the Monte Carlo worlds) and NOT DONE (real
  Indian trips, charger field audit, driver interviews, fleet pilot, live OBD or telematics feed).
- Add an "Evidence status and limitations" section to README.md that links to it. Limitations to include:
  the three demo vehicles are constructed scenarios whose inputs were chosen from a sweep to show three
  outcomes (A13 in DECISIONS.md); charger reports are simulated; almost no charger has a tagged power rating
  so 25 kW is assumed; the planner scores only K_CANDIDATES = 6 chains ranked by cost, so a denser network
  can crowd out better plans; backups at one site are treated as independent unless R9 is done; BMW i3 results
  are German driving with heater load; the voice client is verified only as far as R5 says; the plan cache is
  unbounded and endpoints are not rate-limited; there is no socket read timeout on the Neo4j driver.
- docs/PLAN.md is a placeholder pointing to PLAN.pdf. Replace its content with a short status note: the plan
  was written before the build; the field audit, driver interviews and Indian trip logging in it were not
  completed; see docs/EVIDENCE_STATUS.md. Keep the link to PLAN.pdf.
- grep README.md, docs/ (except PLAN.pdf, PROMPTS.md, TASKS_*.md, MORNING_REPORT_ENGINE.md) and web/ for
  wording that implies real Indian data, audits, interviews or "validated in India", fix it, and list every
  change in PROGRESS_FINISH.md.

**R8 Disclosure and citations.** Append to docs/AI_USAGE.md (do not rewrite existing rows):
| 2026-10-26 to 28 | Claude (claude.ai chat, by Ansh) | Research of the problem statements, literature and market sources; the build plan and the frontend brief; the Claude Code task files and prompts; debugging of setup problems (Neo4j connection, TLS, credentials). |
| 2026-10-26 to 28 | AI assistants used by the frontend teammate: [TEAMMATE: add tool names] | Trip logger, fleet console, driver view and design system, generated from docs brief and tested and uploaded by her. |
plus rows for every Claude Code task since 2026-10-28 (A12, A13, R0-R11 as done) and for third-party services
actually called (Open Charge Map, Neo4j Aura, Sarvam only if R5 made calls, the BMW i3 dataset download).
Add the dataset citation to README.md: Matthias Steinstraeter, Johannes Buberger, Dimitar Trifonov,
"Battery and Heating Data in Real Driving Cycles", IEEE Dataport, October 19, 2020, doi:10.21227/6jr9-5235,
and say that the raw data is not in the repo and how to download it.

**R9 OPTIONAL: site-level failure correlation. Do this only after R0-R8 and R10's first full run are committed.**
Chargers within about 150 m are usually one site and fail together (power, gate, closure); today the
backup logic treats the two Tata Power stations at km 125.6 and 125.7 as independent. Group them into a Site
(the graph already has Site nodes) and treat a backup at the same Site as the planned stop as NOT independent.
This is an engine change: re-run pytest, E3, E4 and tests/test_demo_scenario.py. If any demo decision changes
or any test or experiment gets worse, `git revert` this change, record what happened in PROGRESS_FINISH.md
and do NOT re-tune scenario inputs. If everything holds, document it in DECISIONS.md.

**R10 Final run and numbers pack.** (a) `uv run python scripts/run_all.py` from a clean state (about 20 min);
fix anything broken. (b) `uv run python scripts/export_api_examples.py`; confirm the three demo decisions
(V07 NO_GO, V12 GO, V19 INSUFFICIENT_EVIDENCE) and that V12's replay ends in DIVERT. (c) `uv run pytest` fully
green. (d) Write docs/FINAL_REPORT.md (what was built, test count, blocked items and exact manual steps,
known weaknesses) and add one line at the top of docs/MORNING_REPORT_ENGINE.md saying it is superseded and
its demo table predates the charger rebuild. (e) Write docs/RESULTS_FOR_DECK.md: every number the deck or
abstract may quote, as a table (Claim | Value | n and 95 % CI | REAL or SIMULATED | source file), covering E1
before/after the fix, E1 on BMW i3 if done, E2, E3, E4 with intervals, E5 including the missed 10-day ring,
demo decisions and their confidence ranges, charger-directory counts (total, near-route, the largest gap
between corridor chargers and how many chargers have tagged power), compute time per dispatch for both
graph backends (state the machine), and test counts. End it with a "Do not claim" list.

**R11 Wrap-up.** Final commit on main (local). Print: what is done, what is blocked, the exact manual steps
left for me, and the three numbers I should be most careful about.

Start with R0 now.

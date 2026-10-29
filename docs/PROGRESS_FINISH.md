# Finish track progress — Codex CLI, Windows

Authority: TASKS_FINISH.md with the user's takeover overrides. Starting HEAD: b372fe5 on main;
working tree clean. No applicable AGENTS.md found. Branch creation/deletion, pushing and gh prohibited.

## Environment and baseline

- Python (uv run): 3.12.10; uv: 0.13.6 (329bf9448, Windows x86_64).
- User setup baseline: 239 passed, 11 skipped, 1 warning in 75.42 seconds.
- Independently verified baseline: 239 passed, 11 skipped, 1 warning in 97.06 seconds.
  Warning: Starlette TestClient/httpx deprecation; skips: BMW raw data and live Neo4j.
- Key-presence booleans: SARVAM_API_KEY=false, NEO4J_URI=false, NEO4J_USERNAME=false,
  NEO4J_PASSWORD=false, OCM_API_KEY=false. No .env created or modified.
- Git author name/email missing; requested the correct identity from the user. Commits pending identity.

## Checklist and implementation plan

Plan recorded before implementation (plan mode unavailable). Work in this order, full pytest per milestone:

- [x] R0: preflight, git rule, this plan. Post-milestone pytest: 239 passed, 11 skipped, 1 warning in 140.75 s; local commit pending identity.
- [x] R1: move live-update test to the reserve boundary; scratch-check seeds 0–11, retain assertions.
- [x] R2: inspect then apply only missing API switch and planned-stop null tooltip changes;
  Node syntax checks and scratch integration with a real local API. Document usage in root README
  because the override permits only two web file edits (web/README.md excluded).
- [x] R3: SKIPPED by override; existing loader and fixed seed-31 49/21 split reused, never regenerated.
- [x] R6: verify existing memory switch; rename only graph ALTERNATE probability to p_works,
  explicitly remove stale Neo4j property, test migration/query semantics, explain LEG probability.
- [x] R4a: preserve before artifacts; implement training-only leave-one-trip-out discrepancy,
  at least 20 training subsets/k; aggregate repeated predictions per held-out trip for Wilson intervals;
  save frozen settings/training audit before opening held-out observations; evaluate once, report undercoverage.
- [x] R7: evidence table, all specified limitations, plan status note, wording audit and change list.
- [x] R8: append accurate disclosure and service history; dataset citation/download instructions.
- [ ] R5: BLOCKED — SARVAM_API_KEY absent. No paid calls. Add key locally to .env manually, then
  verify current official endpoint/header/fields/model IDs and make at most one TTS and one STT call;
  stop on authentication failure. Never log the key.
- [ ] R4b: BLOCKED — `uv run python -m verity.datasets.bmw_i3` reports missing/empty raw folder.
  Open https://doi.org/10.21227/6jr9-5235, sign into IEEE DataPort if requested, download Measurement Data.zip,
  extract its 70 TripXnn.csv files and Overview.xlsx directly into data/raw/bmw_i3/ (no nested folder).
  Run the loader check and require loader_ready, n_trips=70. Do not regenerate the split or inspect test trips.
  Then fetch the authors' paper, freeze training-only exclusions, run the final test evaluation exactly once.
  BMW results must say NOT DONE (dataset not on this machine).
- [x] R10: full clean run_all, fresh API export, verify decisions/replay, full tests; script-generated
  final report and deck numbers with provenance, intervals and explicit missing measurements.
- [ ] R9: SKIPPED (optional); independence limitation retained.
- [x] R11: final local commit, clean-tree/split/scope review, user review/push commands.

No energy/trust/threshold changes or scenario retuning; only explicitly permitted experiment/graph edits.
R4a will use a saved evaluation artifact for R10's E1 step to avoid evaluating held-out trips twice.
All raw data stays ignored. No new dependencies planned. Decisions and actual AI/service use appended.

## R1 verification

All original assertions passed for seeds 0-11 in a scratch script. Full pytest: 239 passed, 11 skipped, 1 warning in 84.16 s.

## R2 verification

Only web/js/api.js and web/js/console.js changed under web/. Replacement edits are idempotent. Node v26.13.0 syntax checks passed. Scratch Node VM checked mock mode and fetched scenario/V12 dispatch from real uvicorn: GO, confidence 0.95, is_simulated=true. Full pytest: 239 passed, 11 skipped, 1 warning in 80.75 s. Root README documents ?api=; no web/README.md edit because the override forbids extra web files.

## R6 verification

R6a reused: existing test_api_graph memory-backend test passes; README includes PowerShell usage. ALTERNATE stores p_works; writes REMOVE a.p_reach and reads/coverage filters use p_works. Added fake-driver and memory migration-semantic check. LEG remains a band-midpoint Monte Carlo energy reach frequency, conditional on the model (not charger reliability). No live Aura verification: credentials absent. Full pytest: 240 passed, 11 skipped, 1 warning in 65.94 s.

## R4a verification

Before-fix JSON/PNG preserved byte-for-byte from the starting tree. All fits/variance estimates frozen before opening the 36 held-out SIMULATED trips. Final evaluation performed once; R10 reuses the fingerprinted artifact. At least 20 subset draws/k; at k=10 only one distinct subset exists. Full pytest: 243 passed, 11 skipped, 1 warning in 69.38 s. Scratch statistical checks are now three focused tests.

| Method | k | MAE pp | 90% mixture coverage (Wilson 95%) | Mean per-subset coverage |
|---|---|---|---|---|
| verity_class_prior | 1 | 2.831 | 0.8889 [0.7468, 0.9559], n=36 | 0.8125 |
| verity_class_prior | 2 | 2.437 | 0.8889 [0.7468, 0.9559], n=36 | 0.8306 |
| verity_class_prior | 3 | 2.35 | 0.8889 [0.7468, 0.9559], n=36 | 0.8556 |
| verity_class_prior | 5 | 2.321 | 0.8889 [0.7468, 0.9559], n=36 | 0.8375 |
| verity_class_prior | 10 | 2.207 | 0.8889 [0.7468, 0.9559], n=36 | 0.8764 |
| verity_pooled | 1 | 3.115 | 0.9167 [0.7817, 0.9713], n=36 | 0.8042 |
| verity_pooled | 2 | 2.63 | 0.8889 [0.7468, 0.9559], n=36 | 0.8333 |
| verity_pooled | 3 | 2.557 | 0.8889 [0.7468, 0.9559], n=36 | 0.8500 |
| verity_pooled | 5 | 2.463 | 0.8889 [0.7468, 0.9559], n=36 | 0.8431 |
| verity_pooled | 10 | 2.326 | 0.8889 [0.7468, 0.9559], n=36 | 0.8722 |

Remaining undercoverage is explicit: most 90% mixture bands are below nominal. Mixture coverage and mean subset coverage describe different forecasts; neither treats the repeated subset predictions as independent samples. Wilson intervals are not adjusted for shared vehicles. No held-out retuning. Correction is experiment-only, not a production forecast fix.

## R7 wording audit and verification

- README opening now calls confidence a model estimate, not a tested guarantee.
- README identifies both OSM and OCM locations, removes obsolete key/import blockers, links final status,
  replaces old machine timings with the numbers-pack link, and states no Indian validation/interviews/pilot.
- README adds every required limitation, scenario-sweep disclosure, experiment-only correction caveat,
  remaining undercoverage, and same-site independence.
- PLAN.md now says its proposed fieldwork was not completed; PLAN.pdf link retained.
- EVIDENCE_STATUS.md classifies external source data, simulated evidence and NOT DONE work, with historical
  prior-source provenance explicitly unverified and current BMW/Sarvam/Neo4j blockers.
- PROGRESS_ENGINE.md and BMW_I3_NOTES.md have historical/current-availability notices; old logs preserved.
- Wording scan of README/docs/web (task/prompts/morning-report exclusions applied) found no affirmative
  claim of Indian validation/interviews/audits. API_CONTRACT audit enum is a schema option; no contract edit.
  web/js/mock-data.js has a simulated audit source enum; documented in evidence status and left untouched
  under the two-file web override. No logger/ or design/ edits.
- Full pytest: 243 passed, 11 skipped, 1 warning in 55.98 s. BEFORE_fix and both splits match starting git
  content after Windows checkout newline normalization; git diff on data/splits is empty.

## R8 verification

Appended required historical chat/frontend disclosure, historical OCM/BMW service provenance and current Codex-only work. Existing A12/Aura/BMW rows preserved. A13 assistant attribution and frontend tool names require team confirmation; no identity invented. README now includes the user-supplied dataset citation and exact raw-file placement/check steps. Full pytest: 243 passed, 11 skipped, 1 warning in 52.26 s. No new dependency.

## R5 reviewed: BLOCKED

SARVAM_API_KEY absent; no endpoint verification or TTS/STT calls. Manual step: add your key locally to .env, verify current official endpoints/header/request fields/models, make at most one TTS and one STT call, stop on auth failure. Key values must never be printed.

Full pytest: ================= 243 passed, 11 skipped, 1 warning in 58.17s =================

## R4b reviewed: BLOCKED

BMW loader check reports raw folder missing/empty. NOT DONE (dataset not on this machine). Download Measurement Data.zip from https://doi.org/10.21227/6jr9-5235 and place 70 TripXnn.csv files plus Overview.xlsx directly in data/raw/bmw_i3/. Require loader_ready and n_trips=70. Preserve split; fetch paper and freeze training-only exclusions before exactly one final test load.

Full pytest: ============ 243 passed, 11 skipped, 1 warning in 62.23s (0:01:02) ============

## Git identity resolved

The user supplied Anushka Saxena / saxenaanushka33@gmail.com. Both were configured with git config --local only. Nine preserved milestone trees committed in order directly on main (R0 through R4b, with blockers documented). No branches created/deleted, no push, no gh. Earlier pending-identity notes describe the state when those snapshots were prepared.

## R10 completed

Full clean-output run_all: ALL STEPS OK (E1 verified frozen final evaluation; E2-E5 executed full sizes; Neo4j smoke explicitly SKIPPED). Fresh API examples exported again. V07 NO_GO, V12 GO, V19 INSUFFICIENT_EVIDENCE and V12 replay DIVERT asserted by finish_report.py. Script-generated final/deck reports and durable measurements/validation artifacts saved.

Final post-report pytest: 243 passed, 11 skipped, 1 warning in 53.06s. Source log: results/logs/pytest_final.log (ignored); durable summary results/finish_validation.json. Full timings and confidence seed ranges recorded in results/finish_measurements.json. No current-machine Neo4j timing or live Sarvam claim.

R9 SKIPPED: optional site-correlation engine change deferred to prioritize required validation and handoff; same-site independence remains disclosed. No optional engine change was attempted.

## R11 wrap-up

Final scope/split/raw-data audit passed; only the two permitted web edits. No branch operation, push or gh. R0/R1/R2/R6/R4a/R7/R8/R5/R4b/R10 have separate local milestone commits; R5/R4b commits document blockers. Final full pytest: 243 passed, 11 skipped, 1 warning in 52.39s. The wrap-up commit completes R11.

Review: `git status --short`, `git log --oneline b372fe5..HEAD`, `git diff --stat b372fe5..HEAD`, `git show HEAD`. After your review, you may run `git push origin main` yourself. No push performed by Codex.

## Fleet Console follow-up plan (user-authorized frontend scope extension)

Before edits: inspect current JS/CSS, contract/schema, live memory API and available browser/network tools.
Confirmed V12 dispatch omits starting_battery_pct; scenario supplies SoC. All seven real API breakdown
terms sum with scenario start to arrival. Plan: use scenario SoC, validate breakdown/start/arrival without
inventing missing values; fit all labels/negative cumulative totals inside an accessible chart; keep
OpenStreetMap attribution/provider, expose persistent tile-failure notice with explicit route-only/retry
controls. Add focused Node regression checks with live API data and missing/invalid data cases; full pytest.
Record actual Codex use and diagnostics; local commit only. No models, thresholds, demo inputs, results,
held-out splits, logger or design changes. Browser tooling is unavailable; visual/network verification
will be explicitly manual. Direct tile probes both returned ConnectError (including outside sandbox).

### Fleet Console follow-up outcome

- Code defect confirmed against the live local memory API and DispatchResponse schema: nonexistent
  starting_battery_pct caused NaN bars; scenario soc_pct is the correct starting value. All seven live
  V12 breakdown rows were available and reconciled to arrival, so no missing value was invented.
- Waterfall now validates input, reports unavailable/invalid data, retains signed/zero terms, uses a
  scale containing intermediate totals, wraps horizontal labels and scrolls within narrow cards.
  A reconciliation warning reports inconsistent API values unchanged. Caption identifies attribution
  rather than a chronological battery trace; simulated charts are labelled.
- OSM provider remains unchanged. Tile connection probes failed with ConnectError both in the sandbox
  and outside it, with no HTTP status. Cannot conclude an OSM provider rejection. No browser debugging
  endpoint or installed Playwright/Puppeteer was available; actual browser error remains unverified.
  Map failures now persist through partial tile success; explicit route-only/retry buttons preserve
  route/charger overlays and OSM attribution. No automatic provider switching or repeated request loop.
- Checks: node --check web/js/console.js; 5/5 Node DOM/Leaflet-harness tests, including live local memory
  API contract checks (VERITY_TEST_API=http://127.0.0.1:8000 node --test tests/frontend_console.test.cjs).
  Without that environment variable the four offline checks run and the live check explicitly skips.
  Full uv run pytest: 243 passed, 11 skipped, 1 warning in 86.03 s; git diff --check clean.
- Models, thresholds, scenario inputs, saved results, fixed splits, logger and design unchanged.
  No paid service call or experiment/held-out evaluation. No dependency added.
- Manual browser verification: hard-refresh the real-API console; select V07 and V12; check every label
  and value in dark/light themes and at narrow/desktop widths (horizontal chart scroll is intentional).
  Check route-only view keeps route/markers/attribution, then Retry street tiles. In DevTools Network,
  inspect the failing tile.openstreetmap.org request's HTTP status or net::ERR_* and Console errors;
  this distinguishes local DNS/TLS/proxy/extension filtering from an HTTP provider response. Successful
  local /api requests and visible overlays alone do not verify basemap connectivity. No visual claim
  is made from the DOM/Leaflet test harness.

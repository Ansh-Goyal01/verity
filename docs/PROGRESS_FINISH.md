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
- [ ] R10: full clean run_all, fresh API export, verify decisions/replay, full tests; script-generated
  final report and deck numbers with provenance, intervals and explicit missing measurements.
- [ ] R9: optional; assess after R10 committed. Drop first if time is short; record independence limitation.
- [ ] R11: final local commit, clean-tree/split/scope review, user review/push commands.

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

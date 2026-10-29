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
- [ ] R1: move live-update test to the reserve boundary; scratch-check seeds 0–11, retain assertions.
- [ ] R2: inspect then apply only missing API switch and planned-stop null tooltip changes;
  Node syntax checks and scratch integration with a real local API. Document usage in root README
  because the override permits only two web file edits (web/README.md excluded).
- [x] R3: SKIPPED by override; existing loader and fixed seed-31 49/21 split reused, never regenerated.
- [ ] R6: verify existing memory switch; rename only graph ALTERNATE probability to p_works,
  explicitly remove stale Neo4j property, test migration/query semantics, explain LEG probability.
- [ ] R4a: preserve before artifacts; implement training-only leave-one-trip-out discrepancy,
  at least 20 training subsets/k; aggregate repeated predictions per held-out trip for Wilson intervals;
  save frozen settings/training audit before opening held-out observations; evaluate once, report undercoverage.
- [ ] R7: evidence table, all specified limitations, plan status note, wording audit and change list.
- [ ] R8: append accurate disclosure and service history; dataset citation/download instructions.
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

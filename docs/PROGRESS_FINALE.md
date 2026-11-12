# Finale build progress (F0–F12)

Authority: docs/TASKS_FINALE.md. Commit directly to main, local only; one commit per milestone ("F<n>: ...").
Scope of this session (user instruction 2026-11-12): F0, F1 and F2 only, then stop. F3+ not started.

## Checklist

- [x] F0 Pre-flight
- [x] F1 Collection kit for the teammate
- [x] F2 Vehicle specs
- [x] F3 Enrichment
- [x] F4 E1-India (real, public, self-reported)
- [ ] F5 Charger evidence
- [ ] F6 Voice of customer from app reviews
- [ ] F7 Sarvam voice loop
- [ ] F8 Engine upgrades
- [ ] F9 Range Passport
- [ ] F10 Driver demo scenario
- [ ] F11 One command and documents
- [ ] F12 Wrap-up

## F0 Pre-flight (2026-11-12)

- `git pull --no-edit origin main`: already up to date at 68fb724.
- `git branch` listed main plus two local branches, `feat/neo4j-load-bearing` and
  `fix/demo-scenario-pinned-chargers`. `git branch --merged main` confirmed both were fully merged, so they
  were removed with the safe `git branch -d` (which refuses unmerged branches). `git branch` now lists only
  main. The remote-tracking ref `origin/claude/mission-control-frontend` is not a local branch and was left alone.
- Baseline `uv run pytest`: **244 passed, 10 skipped, 1 warning** (154 s). All 10 skips are the live Neo4j
  parity tests in tests/test_graph_parity.py: "Neo4j unavailable (ServiceUnavailable)". Credentials are
  present in .env, but the Aura instance did not answer (an AuraDB Free instance pauses when idle).
  Manual step: resume the instance in the Aura console, then re-run `uv run python scripts/neo4j_smoke_test.py`.
- Untracked folder `verity/` at the repo root is an older standalone clone of this repository (its own .git,
  newest commit 7a4b995, which is already in main's history). It was not modified, staged or deleted;
  every commit in this track stages explicit paths only. The owner may delete it or keep it.

### Why R4b (BMW i3) and R5 (Sarvam) were blocked, and what unblocks them

| Item | Why it was blocked (docs/PROGRESS_FINISH.md) | What unblocks it | State on this machine today |
|---|---|---|---|
| R4b BMW i3 evaluation | `uv run python -m verity.datasets.bmw_i3` reported the raw folder data/raw/bmw_i3/ missing or empty; the dataset is downloaded by hand from IEEE DataPort (DOI 10.21227/6jr9-5235) after sign-in. | Put the 70 TripXnn.csv files and Overview.xlsx directly in data/raw/bmw_i3/; the loader check must say `loader_ready`, `n_trips=70`. Then freeze training-only exclusions and run the held-out evaluation exactly once, keeping the fixed split. | **Raw data now present**: loader check returns `loader_ready`, `n_trips=70` (72 files in the folder). The held-out evaluation itself is still NOT DONE; it belongs to F9 in this track. |
| R5 Sarvam verification | `SARVAM_API_KEY` absent from .env, so no endpoint, auth header, field or model-id verification and no TTS/STT call was made. | Add the key to .env locally; verify the current official endpoints, header, request fields and model ids from Sarvam's docs; make at most one TTS and one STT call; stop on authentication failure; never log the key. | **Key now present** in .env (presence checked only; value never printed). No call made yet; verification belongs to F7. |

## F1 Collection kit (2026-11-12)

Built: data/corpus/ with header-only templates trips_india.csv, charger_evidence.csv,
app_status_snapshots.csv, app_reviews.csv (no review text); data/private/ gitignored for
app_reviews_text.csv (review_id, text). Column specs, allowed values and ranges in engine/verity/corpus.py
(single source of truth for later pipelines); scripts/validate_corpus.py prints per-row messages using
spreadsheet row numbers and exits 1 on any ERROR. docs/DATA_COLLECTION_GUIDE.md for the teammate.
Seeded six trips (T0001-T0006) from the task text, all verified=no; each note says what the human must check
or fill (e.g. T0004 start SoC, T0002 route, T0001 one-way question). Validator on the committed corpus:
0 errors, 6 rows, 0 usable in results (none verified yet). tests/test_corpus.py: 22 tests.

## F2 Vehicle specs (2026-11-12)

Built data/corpus/vehicles_india.csv (13 variants, every value with its own source URL, every row
"approximate - verify") and its spec in engine/verity/corpus.py (validated by scripts/validate_corpus.py).
Sources actually read: MG India product pages (official) and Wikipedia (secondary). Tata's EV site failed with
an expired TLS certificate; Mahindra's site showed no specs. Manual follow-up: confirm values from the
manufacturer brochures (e.g. Mahindra XEV 9e brochure PDF linked on its page), add the Nexon EV 45 kWh range,
mass and power, and kerb mass for Tiago EV, MG ZS EV and MG Comet EV. Gross vs usable kWh and drag figures
were not on any fetched page. Each model maps to an engine prior via verity.vehicles.classify (pinned by test);
every make/model in trips_india.csv has a spec row (pinned by test). No engine change.

## F3 Enrichment (2026-11-12)

Built engine/verity/enrich.py and scripts/enrich_trips.py (`uv run python scripts/enrich_trips.py`):
geocode (Nominatim, cached, 1 req/s), OSRM route with a >15 % distance-mismatch flag, Open-Meteo elevation
profile, Open-Meteo archive weather at the route midpoint, 1 km physics segments; a provenance string on
every value. Real output data/processed/trips_india_enriched.json: n_selected = 0 (no row is verified=yes
yet), so it is empty by design and re-runs as rows are verified. Live plumbing check on draft rows
(results/dev/, not a result) worked end to end and already flags T0001: reported 302 km vs 126 km routed
Kolkata to Midnapore (140 % mismatch), consistent with the open question in its note. T0001 has no trip or
publication date, so its weather is "missing" until the teammate adds one. Network note: geocoders were
briefly unreachable over TLS from this machine, then recovered; see DECISIONS. tests/test_enrich.py: 7 tests.

## F4 E1-India (2026-11-12)

Built engine/verity/india_eval.py and experiments/e1_india_real.py (`uv run python experiments/e1_india_real.py`):
source-grouped fixed 30 % split (data/splits/india_trips_split.json, rule frozen, zero assignments so far),
predictive distributions through simulate_runs for SoC used / Wh/km / range, reporting-noise model,
per-variant Bayesian calibration (>=3 training records), baselines (claimed linear, flat -25 %, training-only
regression), MAE + 50/80/90 % coverage with Wilson CIs, PRELIMINARY below held-out n=15. Real run:
**NOT_RUN, n=0** (no row verified yet); results/e1_india_real.json and .png say so. Additive engine change:
ForecastRuns.energy_kwh. tests/test_india_eval.py: 14 tests (synthetic in-test records only).
Note for the teammate: E1-India needs verified rows from several different source pages; rows from one page
always share a side of the split, so ~30 % of pages (not rows) become the held-out set.

# Finale build progress (F0–F12)

Authority: docs/TASKS_FINALE.md. Commit directly to main, local only; one commit per milestone ("F<n>: ...").
Sessions (user instructions 2026-11-12): F0-F2, then F3-F4, then F5-F7, each followed by a stop.

## Checklist

- [x] F0 Pre-flight
- [x] F1 Collection kit for the teammate
- [x] F2 Vehicle specs
- [x] F3 Enrichment
- [x] F4 E1-India (real, public, self-reported)
- [x] F5 Charger evidence
- [x] F6 Voice of customer from app reviews
- [x] F7 Sarvam voice loop
- [x] F8 Engine upgrades
- [x] F9 Range Passport
- [x] F10 Driver demo scenario
- [x] F11 One command and documents
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

## F5 Charger evidence (2026-11-12)

Built engine/verity/evidence.py (pure: matching, dated items, one report per row, rated kW, analyses) and
experiments/e3_real_evidence.py (`uv run python experiments/e3_real_evidence.py`). New human-confirmation file
data/corpus/charger_match_overrides.csv (header only; validated like the other corpus files) resolves the review
list data/processed/charger_evidence_review.csv. One Source per channel (`public:google_review`, ...), REPORTED
edges is_simulated=false in memory and in Neo4j when it answers; per-channel sensitivity/specificity from the
Dawid-Skene engine (rings off for channels). Analyses with n and Wilson CIs: working rate by operator and site
type, app 'available' within +-48 h of a dated failure, operator-app rated kW, E3-real.
Real run today: **0 usable evidence rows, 0 snapshots, 0 reports; E3-real NOT_RUN (n=0)**. The CSVs are still
header-only; Neo4j was not contacted (nothing to write). results/e3_real_evidence.json says so.
tests/test_evidence.py: 10 tests (synthetic in-test rows only).

## F6 Voice of customer (2026-11-12)

Built engine/verity/voc.py (Asensio categories + claim types, prompt, JSON schema, strict label parser, keyword
baseline, accuracy with Wilson CI, Cohen's kappa, category shares with CIs) and experiments/voc_app_reviews.py
(`uv run python experiments/voc_app_reviews.py`; `--method keyword` = no paid calls; `--smoke` = one call on a
fictional sentence). SarvamClient gained verified chat_json (POST /v1/chat/completions, sarvam-105b, Bearer +
api-subscription-key) and translate (POST /translate, mayura:v1) plus a paid-call log (path, model, status, ms).
Method choice (logged in DECISIONS): Sarvam's own text model on the original Hindi / Hinglish / English text.
Paid calls this session: 5 chat calls, all on the fictional SMOKE_TEXT (timeout, two HTTP 400 that revealed the
live reasoning_effort values, one empty 200, one success in 22.9 s); no review text was sent.
Real run: **NOT_RUN** (app_reviews.csv has 0 rows; data/private/app_reviews_text.csv not on this machine).
tests/test_voc.py: 7 tests (mock transport only).

## F7 Sarvam voice loop (2026-11-12) - R5 unblocked

Verified endpoints, header, fields and model ids from docs.sarvam.ai and live calls (DECISIONS F7); fixed the TTS
request field (target_language_code -> language_code). Built engine/verity/voice_loop.py (battery % incl. Hindi
number words via Sarvam Translate; spoken charger note -> {charger match, status} as a driver_voice report;
fixed Hindi/English decision templates) and scripts/voice_check.py (`uv run python scripts/voice_check.py`).
Live check (results/voice_check.json, SIMULATED speech: our own TTS read back by STT): run 1 = 4 calls, all 200;
it exposed two problems (romanized Hindi numbers, so no battery %; a false charger match on the word "sector"),
both fixed. Run 2 = 5 calls, all 200 (TTS ~1.3 s, STT 0.8 s, translate 1.2 s): transcript "Battery bayaalis
pratishat hai Sector baasath wala charger band hai", translation "The battery is forty-two percent, the sixty-two
sector charger is off.", battery 42 % (via translation), status broken. Run 2 still matched "Battery Smart" on the
word "battery"; fixed and the record recomputed offline (no new call): charger unmatched (no directory charger is
named Sector 62), so no report is created, which is the honest outcome. Hindi and English decision audio
generated from the saved V12 demo output (GO, 95 %, Bingo 11 %; demo on the SIMULATED fleet); WAVs in results/dev/voice/
(gitignored). No real driver voice exists yet, so no driver_voice report is is_simulated=false.
Paid Sarvam calls F7: 9 (TTS 6, STT 2, translate 1). tests/test_voice_loop.py: 13 tests (mock transport).
Not done (outside F7's text): API endpoints for driver voice reports (an API_CONTRACT change; propose in F8/F10).

## F8 Engine upgrades (2026-11-13)

- (a) Objective: plans minimise expected LOST EARNING MINUTES (driving, detours, failed stops, queue wait from
  'occupied' evidence, heat-derated charging, 240-min stranding penalty x P(stranded)) under the confidence target;
  minutes -> rupees at an input rate. Demo default Rs 60/hour, labelled EXAMPLE, from CIS India 2024 (median net
  taxi earnings INR 5,000 / median 84 h week, surveys 2021-22 incl. Delhi NCR; "per week" is our reading). DECISIONS F8a.
- (b) POST /api/ride-check (docs/API_CONTRACT.md "Contract v2 additions", additive): engine/verity/ridecheck.py
  (pure) + api/ride.py. Ride + after-drop leg in ONE Monte Carlo, primary charger + 2 backups, decision GO / NO_GO /
  INSUFFICIENT_EVIDENCE, reason, minutes and rupees per part, every option with why it was (not) chosen, ride
  Bingo Line, levers (AC off, verify a stale charger). Demo route Noida Sector 62 -> Sector 18 -> IGI T3 at
  2026-11-13 19:00 IST fetched once (OSRM route + table, Open-Meteo elevation + forecast; 16 requests total in the
  cache log) and committed as data/processed/ride_demo_noida_t3.json (41.5 km, 48.6 min, 23.5 degC). On it today,
  with no evidence on the airport chargers, the ride check answers INSUFFICIENT_EVIDENCE with "verify" levers.
- (c) Sites: chargers within 150 m form one Site; beta-factor common-cause failures (beta 0.5, assumption) in the
  dispatch Monte Carlo, its Bingo Line and the ride check (sites.py). Aerocity: Hotel Pullman + Aloft = one Site.
- (d) Candidates: + 3 reliability-ranked + 2 spatially distinct chains. results/f8_candidate_diversity_simulated.json
  (SIMULATED, 100 trips per rate, same seeds): stranded 2 -> 3 (10 % down), 3 -> 2 (20 %), 6 -> 5 (40 %); plan changed
  on 6-9 % of trips; planning +6-16 % time. **No measurable decision-quality gain at this n** (kept: it costs little
  and guards cases the cheapest-6 set misses; differences are within noise).
- (e) Neo4j: client-side deadline (35 s) per query on a daemon thread -> Neo4jStalled (OSError) -> mirror fallback;
  the stalled driver is closed off-thread. Tested with a fake driver that never answers.
- (f) Latency: results/f8_ride_check_latency.json: 30 ride checks, in-memory graph, 1,500 runs: **p50 1.52 s, p95
  1.69 s, max 3.06 s** (target p95 < 5 s: met), Windows 11, 12 logical CPUs.
- Re-runs after the changes (SIMULATED): E3 and E4 conclusions unchanged (trust-aware/dispatch strand far less than
  blind/fixed), numbers moved because the Monte Carlo now draws Site-correlated failures and plans differ: E4 at
  40 % chargers down: fixed 37.5 % [31.1, 44.4] vs dispatch 3.5 % [1.7, 7.0] (before F8: 33 % vs 6 %). E3/E4 planning
  time per plan rose from ~0.1 s to ~0.3 s and a full dispatch on the real route from 1.6 s to 3.3-3.5 s
  (measured standalone too: 3.35 s mean, 4.05 s max): more candidates are scored. The E3/E4 timings in their JSON were
  taken while other experiments ran in parallel. Demo tests: the three fleet decisions (V07 NO_GO, V12 GO + DIVERT,
  V19 INSUFFICIENT_EVIDENCE) are unchanged, no re-pin needed.
- E3/E4's simulated world now places chargers at their km (lat 28 + km/111) instead of one shared point (needed for
  Sites); disclosed in DECISIONS F8c.
- Tests: tests/test_f8_engine.py (13), tests/test_ridecheck_api.py (7). Full suite: 350 passed, 10 skipped (Neo4j
  parity, Aura paused).

## F9 Range Passport (2026-11-13)

Built engine/verity/passport.py (pure: calibration posterior -> usable capacity with 80 % / 95 % credible intervals;
Monte Carlo range 100 -> 0 % and Wh/km on two real Indian reference routes, AC on, 2 people; JSON certificate schema
`verity.range_passport.v1` + markdown), engine/verity/datasets/bmw_passport.py (BMW i3 trip -> logger-like observation
with whole-percent dashboard SoC, never using current/voltage/heater; ground truth = integrated measured V x I per
100 BMS SoC points; i3 prior = small_ecar class with the cited 21.6 kWh nominal / 18.8 kWh usable / 1,195 kg curb),
scripts/range_passport.py (`uv run python scripts/range_passport.py`), tests/test_passport.py (7 tests).
Outputs: results/range_passport_demo_V07.{json,md}, results/range_passport_bmw_i3.{json,md},
results/f9_passport_validation.json.

- **REAL validation (BMW i3, Germany, TRAINING-split trips only, 40 usable of 49, 728 km):** ground truth
  22.70 kWh per 100 SoC points (61.7 Ah per 100 points). Passport with all 40 trips: 19.39 kWh (80 % CI 17.97-20.53,
  95 % 17.19-20.99): **-14.5 %, truth outside the 95 % interval**. Learning curve (20 random subsets per k):
  median |error| 17.3 % (k=1), 16.9 % (3), 15.4 % (5), 14.7 % (10), 14.9 % (20); 95 % coverage 0.0 at every k.
  The estimate is biased low and overconfident on real data. Two facts behind it, both disclosed: the model's
  usable-fraction bound caps capacity at 1.05 x nominal = 22.68 kWh (the truth sits at that ceiling because the BMS
  counts more energy per 100 points than the nominal 21.6 kWh), and only battery and mass are i3-specific (CdA, Crr,
  efficiencies and climate load stay at the class prior). Not tuned after seeing it (that would fit to the truth).
- **SIMULATED validation (6 simulated cars, first k training trips vs simulator truth):** median |error| 4.3 % (k=1),
  3.2 % (3), 3.0 % (5), 3.1 % (10); 80 % coverage 0.83 (k=1) then 1.0. Good on data from our own simulator, which
  shares the planner's physics family: this is the optimistic case.
- Demo certificate V07 (SIMULATED, 6 trips): 37.08 kWh vs simulator truth 32.87 kWh (+12.8 %), truth outside the
  80 % interval.
- Each certificate also states the DATA-only anchor "km per 100 SoC points on the logged trips" (BMW 142.2, V07
  166.4). The BMW passport's Indian-route ranges (177 km highway, 280 km evening urban, p50) are model outputs that
  have NOT been validated against measured Indian driving; given the capacity bias the model likely under-predicts
  this car's consumption (its predicted 69-108 Wh/km is below EV Database's 163 Wh/km "real range" estimate).
- Held-out BMW test trips were not opened.

## F10 Driver demo scenario (2026-11-13)

Second demo: 19:00 IST, an EV cab driver in Noida Sector 62 at 45 % battery (fleet vehicle V12, SIMULATED calibration)
gets a ride Sector 18 -> IGI Airport Terminal 3. `GET /api/demo/driver` serves the scenario; its `request` goes
unchanged to `POST /api/ride-check`; `uv run python scripts/driver_demo.py` saves results/driver_demo_simulated.json.

- REAL: route (OSRM, 41.5 km, pickup at km 11.7), elevation and the forecast for 2026-11-13 19:00 (23.5 degC), the
  8 directory chargers within 8 km of the drop and their road legs (OSRM table). The Tesla Supercharger at Aerocity is
  excluded for a non-Tesla car.
- SIMULATED (flagged, `is_simulated: true`, "0 real, 14 SIMULATED" reports in data_notes): the vehicle and the reports
  pinned in api/service.py DRIVER_PINS: CH-0089 (OSM node, nearest to the drop, 2.3 km) broken 1 and 2 days ago and
  working 9 and 16 days ago; CH-0056 Radisson Blu Plaza Delhi Airport working 7 times in 12 days; CH-0073 Hotel Pullman
  (same Site as Aloft) working twice and occupied once. There is no real evidence for these chargers yet
  (charger_evidence.csv is header-only), so none is used.
- Engine answer today: **GO, confidence shown 100 % (rounded from >= 97.5 %; target 90 %)**; after the drop it picks
  Radisson Blu (4.5 km, trust high), with Aloft and JW Marriott as backups; it avoids the nearer CH-0089 ("Avoided: a
  failure was reported 1 day ago"). Expected 75 lost minutes = Rs 80 at the EXAMPLE rate (charging 58.2 min, driving
  6.6, failed stops 4.4, backup driving 0.6, stranding risk 5.3, queue 0). Without the simulated reports (F8 run) the
  same ride is INSUFFICIENT_EVIDENCE.
- Pins are guarded (charger id + source id + within 8 km of the drop); a directory change stops the service instead of
  attaching the reports elsewhere. Driver-demo report sources are separate ids (ride-demo-user-*) so they do not
  share reliability with the fleet demo's sources.
- The original fleet demo is unchanged: V07 NO_GO, V12 GO (+ DIVERT in its replay), V19 INSUFFICIENT_EVIDENCE; no re-pin.
- Tests: tests/test_driver_demo.py (7) pins the decision, the chosen and avoided chargers, the Site, the simulated
  flags and the pin guards. Full suite: 357 passed, 10 skipped (Neo4j parity, Aura paused).

## F11 One command and documents (2026-11-13)

- `uv run python scripts/run_real_data.py` (new): validate corpus -> F3 enrich -> F4 E1-India -> F5 charger evidence
  -> F6 app reviews -> F9 Range Passport, each in its own process (logs results/logs/real_<step>.log), summary with the
  corpus sizes in results/real_data_summary.json. `--no-paid` (F6 keyword baseline), `--only`. Corpus errors don't
  stop later steps but make the exit code 1. Run today: ALL STEPS OK; every REAL result NOT_RUN at n=0; outputs were
  identical to the committed ones apart from timestamps and runtimes. check_file moved into verity.corpus.
  tests/test_run_real_data.py (7).
- Docs: RESULTS_FOR_DECK.md finale section on top (every quotable number with n, CI, REAL/SIMULATED, file; superseded
  29 Oct rows listed), EVIDENCE_STATUS.md rewritten for the current state, docs/SOURCES.md (new, every URL grouped),
  AI_USAGE.md, README.md (real-data track, ride-check endpoints, corrected stale BMW/Sarvam/limits text), CLAUDE.md
  commands.
- E1 guard found broken and fixed without re-evaluation (DECISIONS F11 E1 guard): F9's model_prior() moved from
  vehicles.py to passport.py (vehicles.py restored to R4a bytes) and the five fingerprinted files re-checked-out with
  CRLF. E1 now verifies its frozen evaluation and replots.
- `uv run pytest`: 364 passed, 10 skipped (live Neo4j). `uv run python scripts/run_all.py`: tests, E1-E5 and
  api_examples OK; **neo4j_smoke FAILED** (Aura host does not resolve; see EVIDENCE_STATUS), so the summary says
  SOME STEPS FAILED. E2-E5 numbers unchanged versus F8; only timings moved. API examples re-exported: fleet decisions
  unchanged (V07 NO_GO, V12 GO, V19 INSUFFICIENT_EVIDENCE); V07's rounded confidence 0.85 -> 0.80 versus the 29 Oct
  export (F8 Sites); chargers.json now shows the F10 driver-demo SIMULATED reports (response is_simulated=true).

# Evidence, results and sources

What is measured, what is simulated, what is still missing, and where every number comes from.
Live status: `GET /api/evidence/ledger`. Results below were hand-maintained on 2026-11-13; the BMW held-out run,
E5b and E6 are in the README tables and the ledger.

## Results: quote these first

Hand-maintained from the result files named in each row (re-run: `uv run python scripts/run_real_data.py` for REAL,
`uv run python scripts/run_all.py` for SIMULATED). Machine: Windows 11 (10.0.26300), 12 logical CPUs, Python 3.13.
95 % intervals are Wilson unless stated. Every URL is in the Sources section below.

| Claim | Value | n and 95% CI | REAL or SIMULATED | Source file |
|---|---|---|---|---|
| Hand-collected corpus size | trips 6 rows (0 verified), charger evidence 0, app snapshots 0, app reviews 0, vehicle specs 13 | counts on 2026-11-13 | REAL (empty so far) | results/real_data_summary.json |
| E1-India (SoC / Wh/km / range on public Indian trips) | class-prior planner: MAE 25.9 Wh/km (16 consumption), 30.9 km (5 range tests); 90 % band covers 20/21. OLS regression 25.8 Wh/km, covers 14/21 | held-out n=21 (11 source pages), FINAL; 2026-11-21 | REAL (self-reported, India) | results/e1_india_real.json |
| E7 honest bands (WP5) | conformal k = 0.95 on 71 training trips (out of sample) -> applied 1.0, releases unchanged; held-out 90 % coverage: physics 19/21 (Wilson 71-97 %), physics + learned correction 19/21, pure ML regression 14/21 (45-83 %) | calibration n=71 (40 pages), held-out n=21; MAE seed-sensitive (8 context scenarios per trip: 26-30 Wh/km) | REAL (self-reported, India) | results/e7_honest_bands.json, data/processed/conformal.json |
| Charger evidence, channel trust, E3-real | NOT_RUN: 0 usable evidence rows, 0 snapshots, 0 reports | n=0 | REAL | results/e3_real_evidence.json |
| Voice of customer (app reviews) | NOT_RUN: 0 reviews | n=0 | REAL | results/voc_app_reviews.json |
| Range Passport capacity, BMW i3 (all 40 usable TRAINING trips, 728 km) | 19.39 kWh per 100 SoC points vs measured 22.70 (V x I): **-14.5 %** | 80 % CrI 17.97-20.53, 95 % CrI 17.19-20.99; truth OUTSIDE the 95 % interval | REAL (Germany, not India) | results/range_passport_bmw_i3.json, results/f9_passport_validation.json |
| Range Passport learning curve, BMW i3 | median abs error 17.3 % (k=1), 16.9 % (3), 15.4 % (5), 14.7 % (10), 14.9 % (20); mean error always negative | 20 random subsets per k from 40 trips (not independent); 95 % coverage 0.0 at every k | REAL (Germany) | results/f9_passport_validation.json |
| BMW i3 observed efficiency (data only, no model) | 142.2 km per 100 SoC points | 40 trips, 512 SoC points, -3.3 to 30.8 degC | REAL (Germany) | results/range_passport_bmw_i3.json |
| Range Passport, simulated cars | median abs error 4.3 % (k=1), 3.2 % (3), 3.0 % (5), 3.1 % (10); 80 % coverage 0.83 then 1.0 | 6 cars; no CI at n=6 | SIMULATED (optimistic: same physics family) | results/f9_passport_validation.json |
| Range Passport demo V07 | 37.08 kWh vs simulator truth 32.87 (+12.8 %), truth outside the 80 % CrI | 6 trips | SIMULATED | results/range_passport_demo_V07.json |
| BMW i3 Indian-route range (model output) | Noida-Agra p50 177 km (p10 147, p90 211); Noida-IGI evening p50 280 km | Monte Carlo, NOT validated; likely optimistic given the -14.5 % capacity bias | SIMULATED on REAL routes | results/range_passport_bmw_i3.json |
| E3 trust-aware vs reliability-blind, 40 % chargers down | stranded 3.5 % vs 16.0 % (only-blind 26, only-aware 1) | 7/200 [0.017, 0.070] vs 32/200 [0.116, 0.217] | SIMULATED | results/e3_reliability_planning_simulated.json |
| E3, 20 % chargers down | stranded 2.5 % vs 9.5 % | 5/200 [0.011, 0.057] vs 19/200 [0.062, 0.144] | SIMULATED | results/e3_reliability_planning_simulated.json |
| E4 dispatch vs fixed shortest path, 40 % chargers down | stranded 3.5 % vs 37.5 % (only-fixed 68, only-dispatch 0); minutes 218.9 vs 217.0 when both arrive | 7/200 [0.017, 0.070] vs 75/200 [0.311, 0.444] | SIMULATED | results/e4_dispatch_vs_fixed_simulated.json |
| E4, 0 / 10 / 20 % chargers down | dispatch 3.5 / 3.0 / 2.5 % vs fixed 9.0 / 14.5 / 20.5 % | 200 trips each; CIs in the JSON | SIMULATED | results/e4_dispatch_vs_fixed_simulated.json |
| Candidate diversity (F8d) | stranded 2 -> 3, 3 -> 2, 6 -> 5 at 10 / 20 / 40 % down; plan changed on 6-9 % of trips; +6-16 % planning time | 100 trips per rate; e.g. 40 %: [0.028, 0.125] vs [0.022, 0.112]: **no measurable gain** | SIMULATED | results/f8_candidate_diversity_simulated.json |
| Ride-check latency | p50 1.52 s, p95 1.69 s, max 3.06 s (target p95 < 5 s) | 30 calls, 1,500 MC runs, in-memory graph; timings real on this laptop | SIMULATED vehicle | results/f8_ride_check_latency.json |
| Driver demo (Noida 19:00, 45 %, ride to IGI T3) | GO, shown 95%+ (target 90 %); after the drop: Radisson Blu (4.5 km, 7 reports), avoids the nearer CH-0089 (failure 1 day ago); 75 expected lost minutes = Rs 80 at the EXAMPLE rate | one scenario, 1,500 MC runs; pinned by tests | SIMULATED vehicle and reports on a REAL route, forecast, directory | results/driver_demo_simulated.json |
| Example earning rate | Rs 60/hour, labelled EXAMPLE | from CIS India 2024 survey medians ("per week" is our reading) | REAL source, our derivation | api/ride.py, docs/EVIDENCE.md (Sources) |
| Fleet demo decisions | V07 NO_GO (rounded 0.80), V12 GO (0.95), V19 INSUFFICIENT_EVIDENCE (0.90) | one export, 1,500 MC runs; decisions pinned by tests | SIMULATED | docs/api_examples/dispatch_V*.json |
| Sarvam voice loop | Hindi note -> transcript -> "battery 42 %", status broken; TTS ~1.3 s, STT 0.8 s, translate 1.2 s | one round trip (n=1) of our own TTS, not a real driver | SIMULATED speech, REAL API | results/voice_check.json |


## Do not claim

- Validated in India, accurate on Indian roads, audited chargers, drivers told us, completed interviews or fleet pilot.
- That the BMW-based Range Passport is accurate: it is biased low (-14.5 % on training trips, -10.5 % on the held-out split).
- Nominal 90% now guarantees 90%: single-subset bands still under-cover; mixture coverage is a different quantity.
- R4a changed production forecast uncertainty, or repeated subset predictions are independent held-out trips.
- Same-site independence in reality; directory records guarantee access/working status; assumed 25 kW is measured power.
- All attacks detected: E5 misses the ten-day evasive ring.
- Sub-second dispatch or model/MC confidence proves real-world safety.
- "90 % guaranteed" or that conformal k = 0.95 proves every release band is calibrated: it holds on average over public reports like the 71 training trips, whose unknown context widens the predictive.
- That the learned correction is in the product or beats physics: it is an experiment (n=21 held-out, overlapping intervals).

## Status and limitations
Status as of 2026-11-18. REAL means an external recorded source, not a Verity field
validation. Numbers with their n and intervals are in the Results section above; every URL is in the Sources section below. Re-run everything REAL from the current CSVs with `uv run python scripts/run_real_data.py`
(writes results/real_data_summary.json with the corpus sizes).

| Group | Evidence | Status and provenance |
|---|---|---|
| REAL | BMW i3 trips, Germany (TU Munich, DOI [10.21227/6jr9-5235](https://doi.org/10.21227/6jr9-5235)) | Raw data present on the build machine (data/raw/bmw_i3/, gitignored; loader_ready, 70 trips). Used in F9 Range Passport validation on TRAINING-split trips only (40 usable): capacity estimate biased low (-14.5 %) and the truth falls outside its 95 % interval. German driving with heater load, not Indian conditions. The held-out BMW test trips have not been opened. |
| REAL | Route geometry, elevation, weather | Cached OSRM-compatible router and Open-Meteo responses: Noida -> Agra snapshot (data/processed/route_noida_agra.json) and the ride-check bundle Noida Sector 62 -> Sector 18 -> IGI T3 (data/processed/ride_demo_noida_t3.json, forecast for 2026-11-13 19:00). Routing is not a measured driven trip; weather is a forecast. |
| REAL | Charger locations | OpenStreetMap and Open Charge Map directory, data/processed/chargers_ncr.json (207 chargers). Locations and metadata only; no field audit, no observed operational status. Sites (chargers within 150 m) are computed from these locations. |
| REAL | Sarvam voice APIs | Endpoints, header, fields and model ids verified from docs.sarvam.ai plus live calls (F7: 9 paid calls; F6: 5 chat calls on a fictional sentence). The speech used was our own TTS read back by STT, so the voice loop's accuracy on real drivers is NOT measured. |
| REAL (not yet filled) | Hand-collected corpus (data/corpus/) | Kit, validator and pipelines F3-F6 built. On 2026-11-13: trips_india.csv 6 seed rows, 0 verified; charger_evidence.csv 0 rows; app_status_snapshots.csv 0; app_reviews.csv 0; no private review text on this machine. So E1-India, charger-evidence analyses, E3-real and voice of customer are all NOT_RUN (n=0). vehicles_india.csv: 13 variants, every value cited, "approximate - verify". |
| REAL (example) | Earning rate Rs 60/hour | From CIS India 2024 survey figures (median net weekly earnings / hours; "per week" is our reading). Labelled EXAMPLE wherever rupees are shown. |
| REAL (weak priors) | Published charger-reliability figures | Source claims in engine/verity/trust/priors.py and PLAN.pdf section 3 (Parliament counts, MMCM on BPCL, Yo Mobility, IEEFA Delhi). Approximate/unverified; weak priors, not Verity measurements. Do not quote them as verified facts. |
| SIMULATED | E1-E5, F8 candidate diversity, F9 simulated validation | Simulator-generated trips, reliability worlds, failures, attacks, usage traces. The simulator shares the planner's physics family, so it is the optimistic case. No Indian operational validation. |
| SIMULATED | Demo fleet (V07, V12, V19) and the driver demo (F10) | Vehicles and every charger report are simulated and flagged is_simulated=true; the driver demo runs on a REAL route, forecast and charger directory. Decisions are computed, not hand-entered, but they are not validation. |
| SIMULATED | Monte Carlo confidence | Draws from model assumptions. Displayed confidence is a model estimate rounded to 5 %, not a measured safety guarantee. |
| NOT DONE | Real Indian trip validation | Needs verified rows in trips_india.csv from several source pages (the split is by source page). |
| NOT DONE | Charger field audit, driver interviews, fleet pilot, live OBD/telematics | None. |
| NOT DONE | Real driver voice reports | No real person's voice note has been processed; no driver_voice report is is_simulated=false. |
| BLOCKED | Live Neo4j on this machine | Credentials are in .env, but on 2026-11-13 the instance host in NEO4J_URI did not resolve in DNS (`getaddrinfo failed`) while neo4j.io itself resolved: the AuraDB Free instance is paused or no longer exists. The 10 live parity tests skip; run_all's neo4j_smoke step fails. Manual step: open the Aura console; resume the instance, or create a new free instance and put its URI, username and password in .env; then `uv run python scripts/neo4j_smoke_test.py`. Everything falls back to the in-memory graph (with a 35 s client-side deadline per query). |

### Known limits

- Range Passport on real data (BMW i3) is biased low by about 15 % and over-confident (95 % coverage 0.0 at every k),
  partly because the model caps usable capacity at 1.05 x nominal and only battery and mass are vehicle-specific.
  Its Indian-route ranges are model outputs, not measurements.
- Almost no directory charger has tagged connector power, so dispatch assumes 25 kW (approximate - verify) unless an
  operator-app snapshot gives a rated kW.
- Site common-cause failures use a beta factor of 0.5 (assumption, DECISIONS F8c), not a measured value.
- Candidate diversity (F8d) showed no measurable decision-quality gain at n=100 trips per scenario (SIMULATED).
- Live updates reweight stored worlds; they do not replan after diversion. E5 misses the ten-day evasive ring.
- The API's in-process caches (plans, ride bundles) are unbounded and the endpoints have no rate limiting: fine for a
  demo, not for a public deployment.
- web/js/mock-data.js includes an `audit` source value in simulated mock records; it is an illustrative UI fixture,
  not a claim that anyone audited chargers (engine work never edits web/).

### Open Charge Map check-ins (W1.2, S7) - BLOCKED on the first real fetch

- `scripts/fetch_ocm_checkins.py` + `verity/ocm.py` are built and tested on a SYNTHETIC point (`tests/test_ocm.py`). An `OCM_API_KEY` is in `.env`, but the one run (2026-11-17) ended with `ConnectError: [WinError 10054] connection forcibly closed` from `api.openchargemap.io` after the client's three retries. Not retried in a loop. Result: n = 0 real check-ins, `results/ocm_checkins.json` NOT written.
- The field names the parser reads (`UserComments`, `CheckinStatusType.IsPositive`, `DateCreated`) are from memory: the official API docs page was not readable (navigation only) and `/v3/referencedata/` returned HTTP 403. Mapping: positive -> `working`, negative -> `broken`; anything unclear is skipped and counted, never guessed. **Manual step:** re-run `uv run python scripts/fetch_ocm_checkins.py` from another network, open `data/cache/ocm_checkins_raw.json`, confirm those three fields exist, then report the n it prints (whatever it is: coverage of Indian chargers may be thin).
- The reports are not yet fed to the graph or the ledger: S10 (W1.5) decides how they enter `GET /api/evidence/ledger`.

- **Update 2026-11-17 (S10): the fetch now works and the answer is n = 0.** After the network cleared, `scripts/fetch_ocm_checkins.py` fetched 78 points from the Delhi NCR box (`results/ocm_checkins.json`: `n_pois_fetched 78`, `n_checkins 0`). None of the 78 points has a `UserComments` key at all (raw cache `data/cache/ocm_checkins_raw.json`; the request asked for `includecomments=true` and `verbose=false`, which omits empty fields), so OCM holds no user check-ins for this box and there is nothing to feed the ledger or the graph. `mapping_verified` stays `false`: with zero examples the comment field names still cannot be checked, and the negative-status-to-`broken` caveat in DECISIONS (S7) is untouched. A wider box or another country code would be a new, separate fetch.

### BMW i3 held-out evaluation (W1.4, S10) - DONE, opened once

- `scripts/bmw_heldout.py --final` opened the held-out split exactly once (19 usable trips, 398.8 km; freeze logged in DECISIONS before the run). Frozen v2 prior: capacity error -10.5 % (truth inside the 95 % interval, outside the 80 %); F9's prior -13.0 %. Per-trip SoC drop: mean relative error -0.1 %, median absolute error 0.95 points; the parameter-only bands cover 26 % / 47 % of trips (80 % / 95 %). The capacity claim is NOT validated on real driving; the Range Passport stays in Lab. `results/bmw_i3_heldout.json` exists, so a second `--final` run refuses.
- German driving with heater or A/C; not Indian conditions. Unchanged by the held-out run: F9's training-trip result (-14.5 %).

### Real-data corpus (W1.1) - still EMPTY on 2026-11-17

- `data/corpus/`: 0 usable charger-evidence rows, 0 app-status snapshots, 0 app reviews, 6 trip rows of which 0 usable (13 vehicle rows). `scripts/run_real_data.py --no-paid` re-ran: every step OK, E1-India / E3-real / reviews NOT_RUN for lack of rows. The corpus freeze is Thursday 19 Nov; the ledger (`GET /api/evidence/ledger`) and `driver_real` (WAITING_FOR_DATA) update by themselves when the rows arrive and `run_real_data.py` is re-run.

### Venue voice measurement (guide W3.5) - NOT DONE

The capture endpoint (`POST /api/lab/voice-sample`, off unless `VERITY_LAB_MODE=1`) and `scripts/voice_real_eval.py` exist and are tested on
synthetic data with a fake Sarvam client. No real speaker has been recorded, so there is no `results/voice_real.json` and the ledger
says `NOT_RUN`. Manual steps at the venue: run the API with `VERITY_LAB_MODE=1`, record 30+ utterances from 10+ people (Hindi, Hinglish,
English) through a capture page (the frontend's), ask each for consent, give each an anonymous id, then run the script. Under the target the
file is marked preliminary. The v3-vs-v4 and mode comparison needs `compare=true` on each capture (4 extra paid Sarvam calls per utterance).


## Sources

Grouped by purpose. Collected on 2026-11-13 by searching the code, corpus CSVs and docs for URLs (`engine/`, `api/`,
`scripts/`, `experiments/`, `data/corpus/`, `docs/`). "Read on" dates come from docs/DECISIONS.md and
the finale build log (git history). A source listed here is not proof that its figure is correct: corpus rows stay
`verified=no` until a human opens the link and checks every number (docs/DATA.md).

### 1. Indian trip reports (data/corpus/trips_india.csv seeds T0001-T0006, all verified=no)

| Row(s) | Source | Used for |
|---|---|---|
| T0001 | https://www.team-bhp.com/news/bhubaneswar-kolkata-back-my-xev-9e-key-highlights-trip-stats | Mahindra XEV 9E, Kolkata -> Midnapore, 302 km, SoC 83 -> 22 (open question: one way or with local driving; routed 126 km in F3) |
| T0002 | https://www.rushlane.com/tata-nexon-electric-range-battery-road-trip-12388665.html | Tata Nexon EV 30.2 kWh, 109.7 km, SoC 100 -> 52 (route to be filled) |
| T0003 | https://www.team-bhp.com/news/tata-nexon-ev-max-highway-drive-efficiency-max-range-more | Nexon EV Max, 130 km highway, 174 Wh/km (consumption) |
| T0004 | https://www.cardekho.com/india-car-news/5-things-we-learnt-by-draining-the-tata-nexon-ev-max-battery-29982.htm | Nexon EV Max drain test, 293.3 km to 1 % (range_test; start SoC to be filled) |
| T0005, T0006 | https://www.autocarindia.com/auto-features/tata-nexon-ev-range-real-world-mileage-test-45kwh-battery-433577 | Nexon EV 45 kWh, city 7.9 km/kWh and highway 7.67 km/kWh (consumption) |

### 2. Vehicle specifications (data/corpus/vehicles_india.csv, every row "approximate - verify")

| Source | Kind | Models |
|---|---|---|
| https://www.mgmotor.co.in/vehicles/mgzsev-electric-car-in-india | Manufacturer page | MG ZS EV |
| https://www.mgmotor.co.in/vehicles/comet-ev-electric-car-in-india | Manufacturer page | MG Comet EV |
| https://en.wikipedia.org/wiki/Tata_Nexon | Secondary (Tata's site failed with an expired TLS certificate on 2026-11-12) | Nexon EV variants |
| https://en.wikipedia.org/wiki/Tata_Tiago | Secondary | Tiago EV |
| https://en.wikipedia.org/wiki/Tata_Curvv | Secondary | Curvv EV |
| https://en.wikipedia.org/wiki/Mahindra_XEV_9e | Secondary (Mahindra's site showed no specs) | XEV 9e |
| https://en.wikipedia.org/wiki/Mahindra_BE_6 | Secondary | BE 6 |
| https://ev-database.org/car/1004/BMW-i3-60-Ah | Secondary | BMW i3 60 Ah prior (nominal / usable kWh, curb mass) and the "real range" consumption figure quoted in F9 |

### 3. Open datasets, papers and published figures

| Source | Used for |
|---|---|
| https://doi.org/10.21227/6jr9-5235 (IEEE DataPort: https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles) | BMW i3 real driving data (TU Munich), F9 REAL validation; raw files in data/raw/bmw_i3/ (gitignored) |
| https://www.mdpi.com/2032-6653/11/2/41 | Paper describing the BMW i3 dataset (signals, flags) |
| Asensio et al., Nature Sustainability 2020 (cited in the finale task list, since removed; no URL recorded in this repo) | The 8 review categories used in F6 (engine/verity/voc.py) |
| https://cis-india.org/raw/workers-experiences-in-app-based-taxi-and-delivery-sectors-key-initial-findings-from-multi-city-quantitative-surveys | EXAMPLE earning rate Rs 60/hour for the ride check (F8a; api/ride.py) |

Published reliability figures behind the charger-trust priors (engine/verity/trust/priors.py: Parliament operational
counts, MMCM on BPCL, Yo Mobility DC downtime, IEEFA Delhi audit) are cited by name in that file and in PLAN.pdf
section 3; the file says they were found by web search on 26 Oct 2026 and must be re-checked. They are weak priors,
not Verity measurements.

### 4. Open services called by the engine (every response cached in data/cache/, gitignored)

| Endpoint | Used by | Purpose |
|---|---|---|
| https://router.project-osrm.org/route/v1/driving/{coords} | engine/verity/routing.py (also via enrich.py) | Route geometry and distance (demo routes, F3, ride-check bundle) |
| https://router.project-osrm.org/table/v1/driving/{coords} | routing.py | Drop -> charger road legs (F8 / F10 bundle) |
| https://api.open-meteo.com/v1/elevation | routing.py (also via enrich.py) | Elevation profile |
| https://api.open-meteo.com/v1/forecast | routing.py | Forecast weather for demo departures |
| https://archive-api.open-meteo.com/v1/archive | enrich.py | Historical weather on a trip's date or month (F3) |
| https://nominatim.openstreetmap.org/search | enrich.py | Geocoding (at most 1 request/second, project User-Agent) |
| https://overpass-api.de/api/interpreter | scripts/import_chargers.py | OpenStreetMap charger directory |
| https://api.openchargemap.io/v3/poi/ | scripts/import_chargers.py | Open Charge Map charger directory |

### 5. Paid / keyed APIs

| Endpoint | Used by | Notes |
|---|---|---|
| https://api.sarvam.ai (`/speech-to-text`, `/text-to-speech`, `/translate`, `/v1/chat/completions`) | engine/verity/voice.py, voice_loop.py, experiments/voc_app_reviews.py | Fields, header and model ids verified against docs.sarvam.ai on 2026-11-12 (DECISIONS F7). Every paid call is logged (path, model, status, ms) |
| Neo4j AuraDB Free (URI in .env, never committed) | engine/verity/trust/graph.py | Charger-truth graph; falls back to the in-memory graph on timeout |

### 6. Hand-collected evidence (filled by the teammate; none yet)

`charger_evidence.csv` (Google Maps reviews, PlugShare, operator apps, X posts, forums, news), `app_status_snapshots.csv`
(Tata EZ Charge, Statiq, Jio-bp pulse, ChargeZone) and `app_reviews.csv` carry a `source_url` or app name on every
row. They had 0 rows on 2026-11-13. After uploads, run `uv run python scripts/run_real_data.py`; the URLs live in
those files.

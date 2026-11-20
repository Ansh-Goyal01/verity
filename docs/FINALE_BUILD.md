# Verity — Claude Code Build Guide (Finale)

Nov 20, 2026 · @Ansh

## How to use this guide

This guide turns Verity from a single-route demo into a product a judge can use for their own trip, in ten work packages (WPs), for the hackFront India finale (21–22 Nov 2026). Export it to Markdown, save it as `docs/FINALE_BUILD.md` in the repo, and start each Claude Code session with:

> Read `CLAUDE.md`, then `docs/FINALE_BUILD.md`. Do WP\<n> only. Before writing code, list the files you will change and any decision you need from me. Run `uv run pytest` before you finish, then commit.

One WP per session keeps context small and every commit testable.

### Where the code stands (checked 20 Nov)

- `api/service.py` `EngineService` loads ONE route at startup (`load_planned_route()` → `data/processed/route_noida_agra.json`) and `_check_endpoints` rejects any request more than `ENDPOINT_TOLERANCE_KM = 30` km from Noida or Agra (HTTP 422 `unsupported_route`). `_read_view` computes `near` for that one route.
- `ride_check` already fetches new routes on demand through `fetch_bundle` and `CachedClient`, limited to the Delhi NCR area by `_check_area`. Reuse that pattern.
- The fleet is 3 simulated vehicles (`DEMO_FLEET`, `FLEET_META`); `data/corpus/vehicles_india.csv` has 13 real Indian EVs that the API never uses.
- `dispatch.py` refuses with `INSUFFICIENT_EVIDENCE` when `trips_calibrated < MIN_CALIBRATED_TRIPS` (= 1), and when a GO depends on stale or unknown chargers under a pessimistic reading.
- Routing, elevation and weather go through `engine/verity/routing.py` (`OSRM_URL`, Open-Meteo); `CachedClient` caches every response and enforces `MAX_REQUESTS = 300`.
- Charger directory: `scripts/import_chargers.py` with two `BBOXES` (NCR and the Yamuna Expressway), 207 chargers in `data/processed/chargers_ncr.json`.
- Sarvam: STT `saaras:v4`, TTS `bulbul:v3`, chat `sarvam-105b` (`engine/verity/voice.py`). The spoken ride-check resolves places only from `data/gazetteer/ncr_places.json`.
- Neo4j: `docs/EVIDENCE.md` records the AuraDB instance as unreachable since 13 Nov; the in-memory twin answers the same queries.

### Non-negotiable rules (in addition to CLAUDE.md)

1. **The golden rules in `CLAUDE.md` win** over anything here. Never invent data, numbers, coordinates or sources. Coordinates for demo places come from the geocoder's response, never from memory.
2. **Rule 4 is amended** (Ansh updates CLAUDE.md): ML is allowed as a learned correction on top of physics, trained and scored on real data only, always reported against physics alone.
3. **`is_simulated` must be true exactly when any input is simulated.** A plan on a real route, a real car and only real reports must say `false`. That first fully real release is a milestone, so never mark it falsely either way.
4. **Held-out data stays sealed.** Never open the BMW held-out split (`tests/test_bmw_heldout_lock.py` guards it). The India held-out split in `data/splits/` is scored once per experiment run, never used for tuning.
5. **Every network call goes through `CachedClient`.** Public routers and Open-Meteo are rate-limited; cache everything.
6. **API changes are additive.** Existing endpoints and fields keep working; every new endpoint or field is logged in the Changelog section of `docs/API_CONTRACT.md` and every judgement call in `docs/DECISIONS.md`.
7. **Ask Ansh before adding a dependency** (expected: `scikit-learn` for WP5; nothing else).
8. **Do not edit `web/`, `logger/` or `design/`** unless Ansh asks in that session: he owns the frontend.
9. **Log AI tool use** in `docs/AI_USAGE.md` (the hackathon requires disclosure).
10. **Stop and ask** at every decision marked "Ansh decides" below.

## Work packages and order

WP1–WP5 and WP10 must land before the venue (Fri 20 Nov); WP6–WP9 are built on site. If time runs short, cut from the bottom of the on-site list, never the foundations.

| Order | WP | What it unlocks | Depends on | When | Rough size |
| --- | --- | --- | --- | --- | --- |
| 1 | WP1 Any-route planning | A judge plans their own trip | — | Fri morning | Large |
| 2 | WP2 Any Indian EV | 13 real cars instead of 3 simulated | WP1 | Fri morning | Medium |
| 3 | WP3 India-wide chargers | Chargers exist on the new routes | WP1 | Fri morning | Medium |
| 4 | WP4 Trip options | "How do you want to go?": fastest, fewest stops, safest | WP1–WP3 | Fri afternoon | Medium |
| 5 | WP5 Honest bands + learned correction | Tested coverage on real data; the ML story | real trip rows (teammate) | Fri afternoon | Medium |
| 6 | WP10 Demo resilience | Works offline and deployed | WP1–WP4 | Fri evening | Small |
| 7 | WP6 Fleet booking acceptance | The fleet product | WP2, WP4 | Venue H0–H6 | Small |
| 8 | WP7 Sarvam voice planner | Hindi voice for any trip; WhatsApp stretch | WP1, WP2 | Venue H0–H6 | Medium |
| 9 | WP8 Neo4j showpieces | Live ring attack, grid shared cause, critical chargers | WP3 | Venue H6–H12 | Medium |
| 10 | WP9 Verity Assured | Priced cover on a GO; paid reports | WP4 | Venue H6–H12 | Small |

Feature freeze at venue H16: after that, only bug fixes, tests and docs.

## WP1 Any-route planning

**Goal:** `POST /api/dispatch` plans any origin and destination in India that has road access, with the Noida–Agra demo kept byte-for-byte as it is today.

### Build

1. **Per-request route.** In `EngineService.dispatch`, replace `_check_endpoints` with a route resolver:
   - If origin and destination are within `ENDPOINT_TOLERANCE_KM` of the demo booking, use the cached demo route exactly as now (keeps existing tests, plan ids and the audio cache valid).
   - Otherwise build a snapshot with `build_route_snapshot(CachedClient(), departure, origin, destination)` and convert it with `snapshot_to_route`. Keep processed snapshots in an `LruCache` keyed by rounded coordinates and departure hour, like `_ride_cache`.
2. **Fix hard-coded names.** `build_route_snapshot` writes `"Noida"` and `"Agra"` into every snapshot. Take origin and destination names as arguments; the request gains optional `origin.name` and `destination.name` (additive).
3. **Per-request charger corridor.** Compute `near = chargers_near_route(view.chargers, poly, poly_km, CORRIDOR_KM)` from the request's own polyline instead of the startup route. `_read_view` keeps computing the demo route's `near` for the existing endpoints.
4. **Graph road network.** `populate_road_network` is built once for the demo route. Read `graph_build.py` and `dispatch.graph_chains` to see what is route-scoped. For a new route, either write its road segments under a route-scoped id prefix and sweep them like plan legs, or skip the road network for that plan and record which in `docs/DECISIONS.md`. Never let one route's segments leak into another's plan.
5. **Spoken answer.** `spoken_answers(... destination=BOOKING[...])` must use the request's destination name.
6. **No synthetic geometry for real users.** `build_route_snapshot` falls back to a synthetic straight-line route when the network fails. For a non-demo request, return HTTP 503 `route_unavailable` ("Couldn't fetch this route right now") instead of planning on synthetic geometry.
7. **Request budget.** `MAX_REQUESTS = 300` caps live calls. Read how `requests_made()` counts (persistent or per process). Make the cap configurable with `VERITY_MAX_REQUESTS` and tell Ansh the cost of one new route (1 router call + about 1 elevation call per 100 km + 1 weather call).
8. **Real-mode honesty.** Simulated demo reports are pinned to Noida–Agra chargers. For a non-demo route, exclude simulated reports from trust, so a plan on a real route with a real car and real reports returns `is_simulated: false` (see rule 3). Add `data_sources` to the response: route, elevation, weather, vehicle, charger reports, each `real` or `simulated` with its source.
9. **Geocoder.** Add `GET /api/geocode?q=<text>&limit=5` returning `{name, lat, lon, kind}` for India only (bounded to India's bounding box, results outside dropped). Use Photon (komoot) for autocomplete, behind `CachedClient`. Read Photon's usage terms first and record them in `docs/DECISIONS.md`. Do not use Nominatim for autocomplete: its usage policy forbids it.
10. **Distance limit.** Ansh decides the maximum trip length (suggested 700 km); longer requests get 422 `route_too_long` with that message.

### Done when

- Connaught Place → Jaipur and Pune → Mumbai return a release with a real polyline, `route.distance_km` within 10% of the router's distance, and the destination name in the speech.
- Noida → Agra demo responses are unchanged (existing tests green, same plan ids).
- With the network off, a new route returns 503 `route_unavailable`, never a synthetic plan.
- `GET /api/geocode?q=jaipur` returns Jaipur with coordinates from Photon.

## WP2 Any Indian EV

**Goal:** a driver picks any of the 13 cars in `data/corpus/vehicles_india.csv` and gets a release; the 3 simulated demo vehicles stay available and labelled.

### Build

1. **`GET /api/vehicles`** lists every row of `vehicles_india.csv` (`vehicle_id`, make, model, variant, battery kWh, claimed range, the source-quality flag such as "approximate - verify") plus V07, V12, V19 with `is_simulated: true`. Read the CSV through `verity.corpus`, as `experiments/e1_india_real.py` does.
2. **Vehicle context for a real car.** Prior from `classify(vehicle_type, battery_nominal_kwh)`, with curb mass and battery size from the CSV row (see how `india_eval._curb_mass` and `prior_posterior` already do this). Build a `VehicleContext` with that posterior.
3. **Calibrate from public trips, training split only.** If verified training-split rows in `trips_india.csv` exist for that make and model, calibrate with them (`india_eval.calibrated_posterior` shows how) and set `trips_calibrated` to that count. Never use held-out rows. Recompute when the corpus changes (on startup is enough).
4. **Cars with zero trips (Ansh decides).** Today `MIN_CALIBRATED_TRIPS = 1` makes every such car refuse. Options:
   - **A (keep):** refuse, as now.
   - **B (recommended):** decide from the class prior and its wider spread; add `calibration: {"trips": 0, "basis": "class prior", "label": "First trip in this car: prior only"}`; refuse only when the shown confidence is within 10 points of the target ("too close to call without your car's history"). Record the choice and its reasoning in `docs/DECISIONS.md`.
5. **Response.** Add `vehicle: {id, name, battery_kwh, trips_calibrated, calibration_basis, source_count, is_simulated}` to the release. `dispatch(..., other_vehicles=...)` uses the demo fleet for its "another vehicle" lever: for a real car with no fleet, pass none.
6. **Unknown id** keeps returning 404 `unknown_vehicle`, now listing all valid ids.

### Done when

- `GET /api/vehicles` returns 16 entries (13 real, 3 simulated), each with its source quality.
- A Nexon EV Max trip on Delhi–Jaipur returns a release whose `vehicle.calibration_basis` says either the public-trip count or "class prior".
- A test proves no held-out India row is ever used for calibration.

## WP3 India-wide charger directory

**Goal:** chargers exist along every demo corridor, and a plan on a corridor with few reports gives a useful answer instead of refusing every time.

### Build

1. **Import.** Extend `scripts/import_chargers.py`: either an all-India Overpass query (`area["ISO3166-1"="IN"]` with `amenity=charging_station`) or one bounding box per demo corridor. Corridors to cover: Delhi NCR (existing), Noida–Agra (existing), Delhi–Jaipur, Delhi–Chandigarh, Delhi–Dehradun, Pune–Mumbai, Bengaluru–Mysuru. Derive corridor boxes from the WP1 route polylines (box around the route plus a margin), never from remembered coordinates. Keep the 50 m merge and the Open Charge Map join.
2. **Output.** Write `data/processed/chargers_india.json`; keep `chargers_ncr.json` for existing tests. `load_chargers()` reads the India file when present`, or the file named by a new VERITY_CHARGERS setting`.
3. **Graph load.** Neo4j AuraDB Free has node and relationship limits: count what the import creates and check it against the current Aura Free limits before loading; report the numbers in `docs/DECISIONS.md`.
4. **The refusal problem.** On a new corridor most chargers have no reports. `dispatch` then reads them pessimistically (`p_lo`) and a GO usually flips to `INSUFFICIENT_EVIDENCE`. That's honest, but a demo where every judge's trip refuses is a failure. Fix it with real evidence and better statistics, never with fake reports:
   - **Operator pooling (recommended).** A charger with no reports borrows strength from real reports on chargers of the same operator (hierarchical Beta prior per operator, shrunk toward the global prior with the evidence count). Read `trust/priors.py` and `trust/engine.py` first; keep the existing behaviour for chargers that have their own reports.
   - **Teammate's evidence.** Real dated reports in `charger_evidence.csv` for the demo corridors flow in through the existing corpus pipeline; make sure they reach the trust engine for the new chargers.
   - **A useful refusal.** When the answer is `INSUFFICIENT_EVIDENCE`, still return the best plan, the chargers it depends on and what would change the answer ("Verify these 2 chargers and this becomes a GO at 90%").
5. **Response.** Every stop and backup carries `trust: {level, n_reports, days_since_last, basis}` where `basis` is `own reports`, `operator pool` or `prior only`.

### Done when

- `GET /api/chargers?bbox=...` returns chargers along Delhi–Jaipur and Pune–Mumbai.
- A Delhi–Jaipur plan names its stops' trust basis, and a refusal lists the chargers to verify.
- Graph counts are within the Aura Free limits, with the numbers recorded.

## WP4 Trip options

**Goal:** one request returns up to three distinct plans (fastest, fewest stops, safest), shaped by the driver's preferences, each a full release.

### Build

1. **`POST /api/plan`** (new, additive). Request: everything `DispatchRequest` has, plus `preferences`:
   - `objectives`: any of `fastest`, `fewest_stops`, `safest` (default all three)
   - `preferred_operators`, `avoid_operators`: operator names from the directory
   - `arrival_buffer_pct`: minimum battery on arrival (default the engine's current reserve)
   - `max_stops`: optional cap
2. **Reuse the dispatch internals, don't fork them.** `_choose` already proposes candidate chains (`propose_paths`, `diverse_paths`, `graph_chains`) and evaluates them (`evaluate_plan` → `PlanEval` with `confidence`, `mean_minutes`, `expected_lost_minutes`). Refactor so the evaluated candidates are returned, then pick per objective:
   - **safest:** highest confidence
   - **fastest:** lowest `mean_minutes` among plans meeting the target (if none meets it, the best and say so)
   - **fewest\_stops:** fewest stops among plans meeting the target, ties broken by time
3. **Preferences.** Operator filters act on the candidate nodes before proposing chains. `arrival_buffer_pct` changes the arrival floor used in evaluation: find where the reserve lives in `_build_ctx` and `Ctx` and thread it through. Record anything you can't support honestly in the response under `unsupported_preferences`.
4. **Deduplicate.** If two objectives choose the same stops, return one option carrying both labels.
5. **Each option is a full release.** Same fields as `/api/dispatch`, its own `plan_id` stored in `self.plans`, so `/api/forecast/update`, `/api/demo/replay/{plan_id}` and `/api/graph/why/{plan_id}` work on any chosen option.
6. **Speed.** Evaluate candidates once and reuse; with 1,500 Monte Carlo runs the target is under 6 s for a 300 km trip on Ansh's laptop. Report the measured time in the response (`compute_ms`).

### Done when

- Delhi–Jaipur returns 2–3 options with different stops or times, each with confidence, arrival band, time and stop count.
- `avoid_operators` really removes that operator's chargers from every option.
- Picking an option and calling `/api/forecast/update` on its `plan_id` works.

## WP5 Honest bands and a learned correction

**Goal:** fix the overconfidence the README already shows (on real BMW trips the 95% band covered 47% of trips) with conformal calibration on real data, add a learned correction on top of physics, and report both on real held-out trips.

### Why it matters

The pitch rests on "our probability is tested". Today the only real test says our bands are too narrow. Fixing it in the open is the strongest critical-thinking story we have, and it is also where the ML belongs.

### Build

1. **Calibration set (training data only).** Real trips the engine can predict: verified India training-split rows (`trips_india.csv` through `india_eval`), BMW i3 *training* trips (never held-out), and VED if usable (step 4). For each trip, the engine's predictive samples of battery drop in percentage points (`india_eval.predictive_samples` shows the path).
2. **Split conformal.** Score each calibration trip with a normalised residual `|observed − median| / spread`, where spread is the predictive interquartile range. The inflation factor `k` is the conformal quantile of these scores at the 90% level (the `ceil((n+1)×0.9)/n` empirical quantile). Save `k`, `n`, the sources and the date to `data/processed/conformal.json`.
3. **Apply it.** Widen the Monte Carlo's battery outcomes around their median by `k` before computing confidence, arrival bands and the Bingo Line. Add `band_method: {name: "split conformal", n, k, sources}` to every release. If `k` is not available, keep today's bands and say `band_method: "model only"`.
4. **VED (time-box to 2 hours).** The Vehicle Energy Dataset (github.com/gsoh/VED, University of Michigan; check its licence first and record it) includes 27 PHEV/EVs with energy and auxiliary-power signals. Check whether its EV trips give battery energy or SoC per trip and enough vehicle data for our physics model. If yes: use EV trips for the learned correction and the calibration set. If not usable in 2 hours, drop it and say so in `docs/DECISIONS.md`.
5. **Learned correction (needs Ansh's OK to add `scikit-learn`).** A small gradient-boosted model (`HistGradientBoostingRegressor`) predicting the physics model's residual from trip features already used in `india_eval._features` (distance, speed band, temperature, AC, passengers, battery size). Train on the calibration set only. The prediction becomes physics + correction; conformal step 2 is then run on the corrected predictions.
6. **Experiment E7.** New `experiments/e7_honest_bands.py`. On the India held-out split, scored once per run: mean absolute error and 50/80/90% coverage for (a) physics only, (b) physics + conformal, (c) physics + correction + conformal, (d) pure ML regression (the existing baseline). Write `results/e7_honest_bands.json` and `.png`, labelled `REAL` with n, and `PRELIMINARY` when held-out n < 15, exactly like E1-India.
7. **Wording.** Conformal coverage is a guarantee only on average over trips like the calibration set (exchangeability). Every place that shows it says that in one short line. Never write "90% guaranteed" (it's on the do-not-claim list).

### Done when

- `results/e7_honest_bands.json` exists with real n and all four methods.
- Releases carry `band_method` with the real n behind it.
- `scripts/export_readme_tables.py` includes E7, and the README test passes.
- A test proves BMW held-out trips and India held-out rows never enter the calibration set.

## WP6 Fleet booking acceptance

**Goal:** a fleet dispatcher enters a booking and sees which car can take it, ranked, with a release ready to send to the driver.

### Build

1. **Demo fleet file.** `data/fleet/demo_fleet.json`: 5–6 cars, each a real model id from WP2 with a label, current battery %, current location and trips calibrated. These states are scenario inputs chosen by us, so the file says `"is_simulated": true` and the UI shows it. Keep V07, V12, V19 as they are.
2. **`POST /api/fleet/booking`.** Request: `{origin, destination, departure_time, passengers, ac_on, target_confidence, vehicles?: [{vehicle_id, soc_pct, location?}]}` (defaults to the demo fleet). For each car, plan from its location to the pickup and then the trip (or the trip only if no location), using the WP4 safest option.
3. **Rank.** GO first, then by confidence, then by total minutes including charging. Each row: `vehicle`, `decision`, `confidence_display`, `arrival_soc`, `charging_minutes`, `plan_id`, `why_not` (for NO-GO and refusals, the engine's reason). Mark one `recommended`.
4. **Speed.** Rank with a reduced Monte Carlo (for example 400 runs, a setting), then re-run the recommended car at the full count so its release matches `/api/dispatch`. Report both counts.
5. **Send to driver.** `GET /api/release/{plan_id}/share` returns a short text summary, the release link, and Hindi and English audio from the existing TTS path (cached audio when Sarvam is down, marked `cached: true`). WhatsApp delivery is WP7.

### Done when

- A Delhi–Jaipur booking against the demo fleet returns every car ranked, with at least one GO and one NO-GO or refusal with its reason.
- The recommended car's `plan_id` opens in the release and live-drive views.
- The response time is reported and is under 15 s for 6 cars.

## WP7 Sarvam voice planner and WhatsApp

**Goal:** a driver says a trip in Hindi, Hinglish or English ("Kal subah 8 baje Jaipur jaana hai, Nexon mein 70% hai, AC chalega"), hears it read back, confirms, and hears the answer in their language.

### Build

1. **`POST /api/voice/plan`**, following the existing `/api/voice/ride-check` design in `engine/verity/ride_voice.py`: rules first, the language model only fills gaps, nothing runs on an unconfirmed parse, and the model never writes a number in the answer.
2. **Slots:** origin (default: the phone's location if sent), destination, battery %, car, departure time, AC, passengers.
   - **Places:** today they resolve only from `data/gazetteer/ncr_places.json`. Resolve through the WP1 geocoder instead (India only, biased to the phone's location). Several close matches: ask ("Jaipur, Rajasthan?").
   - **Battery:** reuse `soc_from_transcript` (it already keeps "Sector 18" from becoming 18%).
   - **Car:** match make and model words against the WP2 vehicle list; unknown car: ask.
   - **Time:** add rules for common Hindi and English phrases ("kal subah 8 baje", "abhi", "tomorrow 6 pm"), in IST. Anything unparsed: ask; never guess.
3. **Language model** (`sarvam-105b`): only when rules miss a slot, strict JSON schema, the existing 4 s timeout. The repo's smoke test once recorded 22.9 s for one call, so the rules path must handle the demo sentence alone.
4. **Answer:** plan with WP4 (safest option by default), then speak from fixed templates (`spoken_answers`, extended for the new fields) through Bulbul. Hindi templates must be checked by Ansh (a native speaker) before the demo.
5. **Cached fallback:** pre-record the demo sentence's answer into `data/audio_cache/` and serve it marked `cached: true` when Sarvam is down.
6. **WhatsApp (stretch, Ansh decides).** Option A: Twilio's WhatsApp sandbox with a webhook on the deployed API: an inbound voice note goes through STT, the same flow runs, and the reply is text, an audio note and a PNG "release card" (decision, stops, Bingo Line, drawn with matplotlib). It needs a Twilio account and a public HTTPS URL. Option B: skip WhatsApp and demo the same flow from the web voice page. Don't start A before WP6 is done.

### Done when

- The demo sentence, spoken, produces the right slots, a read-back in Hindi, and a Hindi answer within 10 s with Sarvam up.
- With Sarvam down, the cached answer plays, marked cached.
- An ambiguous place or an unknown car produces a question, never a guess.

## WP8 Neo4j showpieces

**Goal:** three things the Neo4j judges can see the graph doing that a table couldn't: a fake-report attack caught live, chargers that share a likely grid cause of failure, and the chargers a corridor can't lose.

**First:** confirm Neo4j is back (`uv run python scripts/neo4j_smoke_test.py` passes). Every graph response keeps returning the Cypher it ran and `query_ms`.

### 8a. Live fake-report ring attack

1. `POST /api/demo/ring-attack` (demo-only, behind a `VERITY_DEMO=1` setting): adds N simulated sources that all report one named charger `broken` within minutes, plus the overlapping history they need to match the ring rule (read `rings.detect_rings`, `graph.report_rings` and `AGREEMENT_CYPHER` for the thresholds: shared reports, Jaccard, agreement, time window). Every injected report is `is_simulated: true` and tagged for cleanup.
2. Response: the ring as found by the graph (members, shared reports, Jaccard), the charger's trust before and after (it should barely move), and the Cypher.
3. `POST /api/demo/ring-attack/reset` removes everything the attack added (`prune_simulated_reports`).
4. **Honest limit, stated in the response:** a slow ring spread over ten days evades detection in experiment E5.

### 8b. Graph neighbourhood for the UI

`GET /api/graph/neighborhood/{charger_id}?depth=2` returns nodes and edges (charger, site, operator, reports, sources, rings, plans) in a shape a browser graph library can draw directly, plus the Cypher. Ansh picks the library for the frontend.

### 8c. Likely shared grid cause

1. Import `power=substation` from OpenStreetMap for the demo corridors (same Overpass path as WP3, through `CachedClient`).
2. Link each charger to its nearest substation within a distance Ansh sets (suggested 5 km) as `(:Charger)-[:NEAREST_SUBSTATION {distance_km}]->(:Substation)`. This is proximity only: India's feeder maps aren't public, so never call it the feeding substation.
3. Add it to `shared_causes` as its own, weakest tier. In dispatch, a backup that shares the primary's nearest substation is not counted as independent. Record the rule in `docs/DECISIONS.md`.
4. Script `scripts/g1_grid_shared_cause.py` → `results/g1_grid_shared_cause.json`: on the demo corridors, how many primary–backup pairs share a nearest substation, with counts and the distance threshold used.

### 8d. Chargers a corridor can't lose

Script `scripts/g2_critical_chargers.py` → `results/g2_critical_chargers.json`: for each demo corridor and a mid-size car's usable range, the chargers whose removal opens a gap longer than that range (use `coverage_gaps` and the road-segment graph). If Aura Graph Analytics is available on the instance, use it and include the call; otherwise compute it in Python and say so.

### Done when

- The ring attack is flagged on screen and the charger's trust barely moves; reset restores the graph.
- `results/g1_*.json` and `results/g2_*.json` exist with real counts and their thresholds.

## WP9 Verity Assured and paid reports

**Goal:** show that a tested probability can carry a price: a stranding cover on GO releases, and rewards for verified charger reports that fraud can't farm. Both are illustrative business features, labelled as such everywhere.

### 9a. Cover price on a release

1. **Stranding probability.** Use the Monte Carlo's share of runs that end stranded, if `evaluate_plan` tracks it separately from "diverted but arrived". If it doesn't, add that count. Only if that's impossible, use 1 − confidence and label it "upper bound".
2. **Inputs, never invented.** `config/assured.json` (Ansh fills it): `tow_cost_rs` with a `source_url`, `margin` (a multiplier), `min_confidence` (suggested 0.95). If `tow_cost_rs` has no source, show the formula without rupees.
3. **Formula:** price = stranding probability × tow cost × margin, rounded to the nearest ₹5.
4. **Response field** on releases: `assured: {eligible, p_strand, p_strand_basis, tow_cost_rs, tow_cost_source, margin, price_rs, is_illustrative: true, note}`. Eligible only for GO at or above `min_confidence`. Note text: "Illustrative. A real cover would be sold through a licensed insurance or roadside partner."
5. When `band_method` (WP5) is "model only", set eligible to false with the reason "bands not yet calibrated on real data".

### 9b. Rewards for verified reports

1. `POST /api/reports` responses gain `reward: {eligible, amount_rs, withheld_reason, is_illustrative: true}`, with the amount from `config/assured.json` (`report_reward_rs`).
2. Withhold the reward when the source belongs to a flagged ring, when the report contradicts strong recent evidence from trusted sources, or when the same source exceeds a daily cap. Reuse the trust engine's source weights; don't build a second fraud system.
3. Show it in the ring-attack demo (WP8a): the ring's reports earn nothing.

### Done when

- A GO release at 95%+ shows a cover price with every input and its source; a NO-GO shows none.
- A ring member's report shows `reward.eligible: false` with the reason.

## WP10 Demo resilience

**Goal:** the demo works with venue Wi-Fi down, Sarvam down or Neo4j down, and a public HTTPS URL answers when it's up.

### Build

1. **Pre-cached demo routes.** `scripts/precache_demo_routes.py` takes a list of place names, resolves each through the WP1 geocoder, and builds and saves processed snapshots to `data/processed/routes/<slug>.json` (committed; they're small). Corridors: Noida–Agra (existing), Delhi–Jaipur, Delhi–Chandigarh, Delhi–Dehradun, Pune–Mumbai, Bengaluru–Mysuru. The route resolver checks these files before the network. Ansh confirms each start and end point on a map before committing.
2. **Offline mode.** `VERITY_OFFLINE=1` serves only cached routes and geocoder results; anything else returns 503 `offline` with the list of cached trips, so the UI can offer them.
3. **Geocoder cache.** Pre-warm the geocoder cache for the demo place names and common prefixes ("jai", "chan", "agra").
4. **Deploy.** Follow `docs/DEPLOY.md`: build the `Dockerfile` (never built yet, expect a fix), set secrets in the host dashboard, set `VERITY_CORS_ORIGINS` to the frontend's URL, check `/api/health`. Add `scripts/warmup.sh` that calls `/api/health` and one cached plan, to run two minutes before the pitch.
5. **Health page.** `/api/health` adds: offline mode, cached route count, conformal `k` and n (WP5), charger count, graph backend.

### Done when

- With the network unplugged, every pre-cached corridor plans and the UI offers the cached list for anything else.
- The deployed URL's `/api/health` shows the expected graph backend and `sarvam_key_present: true`.

## API contract changes

All changes are additive (contract v3): every v1 and v2 endpoint and field keeps its shape. Log each row below in the Changelog section of `docs/API_CONTRACT.md` with the date, the reason, and the old and new shape, in the same commit as the code. Give Ansh the new response shapes so the frontend can follow.

| WP | Endpoint | Change |
| --- | --- | --- |
| WP1 | `POST /api/dispatch` | Any route; optional `origin.name`, `destination.name`; new `data_sources`; new errors 503 `route_unavailable`, 422 `route_too_long`; `unsupported_route` retired |
| WP1 | `GET /api/geocode` | New: `q`, `limit` → `[{name, lat, lon, kind}]`, India only |
| WP2 | `GET /api/vehicles` | New: 13 real cars + 3 simulated |
| WP2 | Releases | New `vehicle` and `calibration` blocks |
| WP3 | Releases | Each stop and backup gains `trust.basis` |
| WP4 | `POST /api/plan` | New: `preferences` in, `options[]` out, each a full release with its own `plan_id` |
| WP5 | Releases, `/api/health` | New `band_method`; health shows conformal `k` and n |
| WP6 | `POST /api/fleet/booking`, `GET /api/release/{plan_id}/share` | New |
| WP7 | `POST /api/voice/plan` | New, same read-back and confirm pattern as `/api/voice/ride-check` |
| WP8 | `POST /api/demo/ring-attack`, `.../reset`, `GET /api/graph/neighborhood/{charger_id}` | New; ring attack only with `VERITY_DEMO=1` |
| WP9 | Releases, `POST /api/reports` | New `assured` and `reward` blocks, `is_illustrative: true` |
| WP10 | All | 503 `offline` with the cached trip list when `VERITY_OFFLINE=1` |

## Testing and acceptance checklist

A WP is done only when its own "Done when" items pass and every box below that applies is ticked. Run `uv run pytest` before every commit; the existing suite (60+ test files, including the offline demo test) must stay green.

### Tests to add

- [ ] **WP1:** any-route dispatch with recorded router, elevation and weather responses as fixtures (no live network in tests); demo-route responses unchanged; network-off → 503 `route_unavailable`; geocoder results outside India dropped.
- [ ] **WP1:** a real route with a real car and only real reports returns `is_simulated: false`; add any simulated input and it flips to `true`.
- [ ] **WP2:** `/api/vehicles` count and fields; calibration never reads India held-out rows.
- [ ] **WP3:** trust `basis` is `own reports` whenever a charger has reports; operator pooling never overrides a charger's own evidence.
- [ ] **WP4:** options are distinct or merged with both labels; `avoid_operators` holds in every option; each option's `plan_id` works with `/api/forecast/update`.
- [ ] **WP5:** calibration set contains no BMW held-out and no India held-out trip; `k` is reproducible from `conformal.json`'s inputs.
- [ ] **WP6:** ranking order rules; recommended car re-run at the full Monte Carlo count.
- [ ] **WP7:** the demo sentence parses by rules alone; an ambiguous place produces a question; the answer contains only engine numbers.
- [ ] **WP8:** ring attack is detected and reset leaves no injected node behind; nearest-substation edges never claim to be feeders.
- [ ] **WP9:** `assured` only on eligible GO releases; no rupee figure without a sourced input; ring members get no reward.
- [ ] **WP10:** offline mode serves every pre-cached corridor.

### Before the demo (Ansh runs these)

- [ ] `uv run pytest` green.
- [ ] `uv run python scripts/run_real_data.py`, then `uv run python scripts/export_readme_tables.py`; README numbers match `results/`.
- [ ] `docs/API_CONTRACT.md` changelog, `docs/DECISIONS.md` and `docs/AI_USAGE.md` are up to date.
- [ ] `docs/EVIDENCE.md` updated with every new REAL, SIMULATED and NOT DONE item, and the do-not-claim list re-read.
- [ ] No secrets in git (`.env` ignored; `git log -p | grep -i key` finds nothing sensitive).
- [ ] Deployed `/api/health` checked; `scripts/warmup.sh` run two minutes before the pitch.

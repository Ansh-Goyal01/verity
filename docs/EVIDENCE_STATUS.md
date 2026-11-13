# Evidence status and limitations

Status after the finale build (F0-F11, 2026-11-13). REAL means an external recorded source, not a Verity field
validation. Numbers with their n and intervals are in [RESULTS_FOR_DECK.md](RESULTS_FOR_DECK.md); every URL is in
[SOURCES.md](SOURCES.md). Re-run everything REAL from the current CSVs with `uv run python scripts/run_real_data.py`
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

## Known limits

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

Older progress documents (PROGRESS_ENGINE, PROGRESS_FINISH, FINAL_REPORT) are historical; this file and
RESULTS_FOR_DECK.md describe the current state.

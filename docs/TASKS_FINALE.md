# Finale build — real public data + the upgrades that make Verity win

We are in the hackFront India national finale (22-hour hackathon, 21–22 Nov 2026, IIIT Delhi), ~20 teams on
our problem statement, 160 teams overall. Work through F0–F12 in order without asking me anything.

## Situation and strategy (read first)

- The team cannot travel, log trips or interview people. Real data will come from PUBLIC desk sources,
  collected by hand by my teammate (new to coding) into CSV files, plus open datasets. You build the kit she
  uses, the pipelines that turn her rows into evidence, and the engine upgrades.
- Desk sources: (1) Indian EV trip reports by owners and magazines (Team-BHP, Autocar India, CarDekho,
  CarWale, evo India, Rushlane, Cartoq, YouTube range tests); (2) dated public evidence about specific
  chargers (Google Maps reviews, PlugShare check-ins, operator-app status snapshots, posts to operators on X);
  (3) charging-app store reviews; (4) open datasets (BMW i3 TU Munich, DOI 10.21227/6jr9-5235).
- Method precedent for (2) and (3): Asensio et al., Nature Sustainability 2020 (127,257 reviews at 12,720 US
  stations, 8 categories: functionality, availability, cost, location, dealership, user interaction,
  service time, range anxiety) and the group's cross-lingual follow-up (72 languages).
- Product direction: decisions in rupees and minutes, a driver's "can I take this ride?" check, a voice loop
  in Hindi (Sarvam), a charger-truth graph fed by real evidence (Neo4j), and a used-EV Range Passport.
- Read first: CLAUDE.md, docs/FINAL_REPORT.md, docs/PROGRESS_FINISH.md, docs/EVIDENCE_STATUS.md,
  docs/DECISIONS.md, docs/API_CONTRACT.md, docs/RESULTS_FOR_DECK.md.

## How to work

1. Create docs/PROGRESS_FINALE.md with the F0–F12 checklist. After each milestone tick it, note what was
   built, and commit "F<n>: <summary>".
2. Never stop to ask questions; choose the simplest option consistent with CLAUDE.md and log it in
   docs/DECISIONS.md. If blocked (missing data, key, network), record the exact reason and manual step,
   build everything around it, and continue.
3. `uv run pytest` after every milestone; never leave failing tests.
4. Every pipeline must work on PARTIAL data: the CSVs fill up over the week, so everything re-runs from one
   command (F11) and reports n honestly.
5. Ending your turn while milestones remain undone and unblocked is a failure.

## Hard limits

- Commit directly to main; never create branches; local commits only; never git push, add remotes or use gh.
- Never print, edit or commit .env values. Never commit raw data or private text (see F1 for what is private).
- No scraping of Google Maps, the Play Store, X, Team-BHP or any site whose terms forbid automated access.
  Humans collect those by hand. You may call official/free APIs (OSRM-compatible routers, Nominatim at most
  1 request/second with a proper User-Agent, Open-Meteo forecast and historical archive, Open-Elevation or
  equivalent, Open Charge Map, Neo4j, Sarvam) and must cache every response in data/cache/.
- Do not invent data or results. Vehicle specs and any parameter must come from a cited source (comment
  "approximate - verify"). Every result is labelled REAL or SIMULATED with n and a 95% CI where possible.
- Rows with verified != yes are excluded from every result. Held-out splits are written once and never
  changed; tune only on training data; evaluate held-out sets once per final run and report whatever comes out.
- Keep the simulated demo reports separate from real evidence (is_simulated flag everywhere).
- Ask before nothing, but log every engine change in DECISIONS.md. If an engine change flips a demo decision,
  report it; re-pinning scenario inputs is allowed only with disclosure in DECISIONS.md.

## Milestones

**F0 Pre-flight.** `git pull --no-edit origin main`; `git branch` must list only main; record baseline
`uv run pytest`. Read docs/PROGRESS_FINISH.md and write down WHY R4b (BMW i3) and R5 (Sarvam) were blocked
and what would unblock each.

**F1 Collection kit for my teammate (do this first; she starts as soon as it is committed).**
Create data/corpus/ with header-only CSV templates and one docs file:
- `trips_india.csv`: record_id, record_type (soc_trip | consumption | range_test), source_url, source_kind
  (owner_forum | magazine_test | owner_story | video), source_published_date, trip_date_or_month, make, model,
  variant, battery_kwh_nominal, origin, destination, route_notes, one_way_or_round, distance_km,
  start_soc_pct, end_soc_pct, energy_wh_per_km, avg_speed_kmh, speed_band (city | mixed | highway_80_100 |
  highway_100_plus), ac (on | off | unknown), passengers, drive_mode, regen_mode, ambient_temp_c, notes
  (one-line paraphrase), entered_by, verified (yes | no), verified_by.
- `charger_evidence.csv`: evidence_id, charger_name, operator, landmark_or_address, lat, lon, place_url,
  source_kind (google_review | plugshare | operator_app | x_post | forum | news), evidence_date,
  evidence_time, status (working | broken | partially_working | queue | no_power | access_blocked |
  payment_failed | unknown), connector, rated_kw, queue_count, source_url, paraphrase (max 15 words, no
  names), collected_by, verified.
- `app_status_snapshots.csv`: snapshot_id, app (tata_ez_charge | statiq | jiobp_pulse | chargezone |
  other), charger_name, charger_ref_in_app, datetime_ist, status_shown (available | busy | offline |
  faulty | unknown), connectors_available, rated_kw, collected_by.
- `app_reviews.csv` (committed, NO review text): review_id, app, review_date, stars, language (en | hi |
  hinglish | other), paraphrase (max 15 words), human_category (one of the 8 Asensio categories or other),
  human_claim (charger_not_working | wrong_status | payment | app_bug | queue | other), collected_by.
  The full review text goes in `data/private/app_reviews_text.csv` (review_id, text) — add data/private/ to
  .gitignore.
- `docs/DATA_COLLECTION_GUIDE.md`, written for a beginner: what each file is for and why it matters to the
  judges; where to find sources (list the sites above, and for chargers: Google Maps reviews, PlugShare,
  the four operator apps, X); one fully worked example row per file; the rules (copy by hand, no scraping,
  always a link, no reviewer names, paraphrase in at most 15 words, mark verified=yes only after opening the
  link and checking every number); targets (40–60 trips, 40–60 chargers in Delhi NCR and on the Noida–Agra
  corridor, app snapshots 2–3 times a day for 6 days on the same chargers, 200–300 app reviews with 50 of
  them human-labelled); how to work in Google Sheets with the exact header row, export as CSV, and upload to
  data/corpus/ through the GitHub website without renaming the file.
- `scripts/validate_corpus.py`: checks every CSV (required fields, allowed values, numeric ranges, URLs
  present, dates parse, start_soc > end_soc) and prints friendly, specific messages per row.
- Seed trips_india.csv with these rows, all verified=no (a human must open each link and confirm):
  (a) Mahindra XEV 9E (79 kWh), Kolkata to Midnapore, 302 km, SoC 83 -> 22, AC on, regen L1, avg 65–70 km/h,
  early summer; source https://www.team-bhp.com/news/bhubaneswar-kolkata-back-my-xev-9e-key-highlights-trip-stats
  (note: check whether 302 km is one way or includes local driving).
  (b) Tata Nexon EV (30.2 kWh), 27 Dec 2020, 109.7 km, SoC 100 -> 52, family on board; source
  https://www.rushlane.com/tata-nexon-electric-range-battery-road-trip-12388665.html (route unknown: fill in).
  (c) Tata Nexon EV Max, 130 km highway drive, 174 Wh/km overall; first 65 km Eco mode at 90–110 km/h at
  153 Wh/km; source https://www.team-bhp.com/news/tata-nexon-ev-max-highway-drive-efficiency-max-range-more
  (record_type consumption).
  (d) Tata Nexon EV Max drain test, 293.3 km ending at 1%; source
  https://www.cardekho.com/india-car-news/5-things-we-learnt-by-draining-the-tata-nexon-ev-max-battery-29982.htm
  (record_type range_test; fill start SoC from the article).
  (e) Tata Nexon EV 45 kWh real-world test: city 7.9 km/kWh, highway 7.67 km/kWh, two people; source
  https://www.autocarindia.com/auto-features/tata-nexon-ev-range-real-world-mileage-test-45kwh-battery-433577
  (two consumption rows: city and highway).
Commit F1 immediately so I can push it.

**F2 Vehicle specs.** `data/corpus/vehicles_india.csv`: make, model, variant, battery gross/usable kWh,
curb mass, claimed range and test cycle, drag or efficiency hints, and a source URL per value, for every
model in the corpus (start with Nexon EV variants, XEV 9E, Tiago EV, MG ZS EV, MG Comet, Curvv EV, BE 6).
Fetch from manufacturer pages or reputable outlets; never from memory; comment "approximate - verify".
Map each model to the engine's priors.

**F3 Enrichment.** For verified trips: geocode origin/destination (Nominatim, cached, 1 req/s), route
(distance cross-checked against the reported distance; flag >15% mismatch), elevation profile, and
historical weather for the trip date or month at the route midpoint (Open-Meteo archive). Write
data/processed/trips_india_enriched.json with a provenance field per value.

**F4 E1-India (real, public, self-reported).** Fix a held-out split of 30% grouped by source (no source in
both sets), written once. For each held-out record predict, through the normal planner path (route,
elevation, weather, speed band, AC, passengers, vehicle prior): SoC used (soc_trip), Wh/km (consumption)
or full range (range_test), as a predictive distribution. Metrics: MAE and coverage of 50/80/90% bands with
Wilson CIs, versus baselines (claimed range scaled linearly; flat minus-25% rule; plain regression on trip
features). Where one model has at least 3 training records, show per-model calibration gain. Model the
reporting noise (integer SoC, self-report). Save results/e1_india_real.json and .png titled "(REAL: public
self-reported Indian trips, n=…)". If held-out n < 15, label the result PRELIMINARY.

**F5 Charger evidence.** Ingest charger_evidence.csv and app_status_snapshots.csv; match each row to the
charger directory (place_url or within 100 m plus name similarity; unmatched rows go to a review list);
create Source nodes per channel (google_review, plugshare, operator_app, x_post, forum, news) and dated
REPORTED edges with is_simulated=false, in Neo4j and in-memory. Learn per-channel trust with the existing
Dawid–Skene engine. Update rated_kw where the operator app shows it. Analyses (only if the data supports
them, always with n): working rate by operator and site type; how often the operator app showed available
within 48 h of a dated failure report; E3-real: for real NCR and corridor trips, how often trust-aware
planning on real evidence changes the charger choice versus reliability-blind. Save results/e3_real_evidence.json.

**F6 Voice of customer from app reviews.** Classify every review (text from data/private/) into the 8
Asensio categories plus the claim type. Use Sarvam's text models for Hindi and Hinglish if its current API
offers them (fetch the docs; never assume), otherwise translate with Sarvam Translate and classify; log the
choice. Measure agreement with the human labels (accuracy and Cohen's kappa on the labelled subset). Report
category shares with CIs. Save results/voc_app_reviews.json. Commit no review text.

**F7 Sarvam voice loop.** Unblock R5: verify endpoints, auth header, fields and model ids from the current
docs; then (a) speech-to-text that extracts a battery % from Hindi, Hinglish or English speech; (b) a voice
note like "Sector 62 wala charger band hai" becomes a structured charger report (charger match + status),
stored with source_kind=driver_voice and is_simulated=false only if it came from a real person; (c)
text-to-speech of decisions from fixed templates in Hindi and English. Keep paid calls minimal and logged.

**F8 Engine upgrades.**
(a) Rupee and minute objective: plans minimise expected lost earning minutes = driving + detour + expected
charging time (rated kW derated for heat) + expected waiting (queue evidence) + stranding penalty, under
the confidence target. The earning rate (Rs/hour) is an input; the demo default must cite a published
figure and be labelled "example".
(b) New endpoint POST /api/ride-check: vehicle, current SoC, current location, pickup, drop, earning rate ->
GO / NO-GO / INSUFFICIENT_EVIDENCE for the ride AND the after-drop plan to a trusted charger, with minutes
and rupees, the reason, levers and the Bingo Line. Document it in docs/API_CONTRACT.md as v2.
(c) Site correlation: chargers within ~150 m form one Site; a backup at the same Site is not independent.
(d) Candidate diversity: candidates are no longer only the 6 cheapest by cost; add reliability-ranked and
spatially distinct chains; measure decision quality and time.
(e) Neo4j driver timeouts so a stalled connection falls back instead of hanging.
(f) Latency: ride-check p95 under 5 s on a laptop with the in-memory graph; measure and report.
Re-run E3, E4 and the demo tests after each sub-change.

**F9 Range Passport.** From k logged trips of one vehicle, estimate usable capacity and real-world range in
Indian conditions with a credible interval. If the BMW i3 raw data is present, validate the capacity
estimate against capacity from integrated measured current (the ground truth). Output a JSON certificate and
a markdown certificate. Label validation REAL (BMW i3) or SIMULATED.

**F10 Driver demo scenario.** Build a second demo: an EV cab driver in Noida in the evening with a given SoC
gets an airport ride request; ride-check decides; after the drop it avoids a charger with recent failure
evidence and picks a trusted one. Use real chargers and real evidence where they exist; anything simulated is
flagged. Add tests that pin its decision. Keep the original fleet demo test green or re-pin it with
disclosure.

**F11 One command and documents.** `uv run python scripts/run_real_data.py` re-validates the corpus and
re-runs F3–F6 and F9 from the current CSVs. Update docs/RESULTS_FOR_DECK.md (every quotable number with n,
CI, REAL/SIMULATED, source file), docs/EVIDENCE_STATUS.md, docs/SOURCES.md (every URL used, grouped),
docs/AI_USAGE.md, README.md and CLAUDE.md commands. Full `uv run pytest` green; run scripts/run_all.py.

**F12 Wrap-up.** Print: what is done, what is blocked with exact manual steps, the corpus sizes reached,
and the five numbers I should be most careful about.

Start with F0 now.

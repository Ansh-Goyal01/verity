# Sources (every URL the engine, data or results use)

Grouped by purpose. Collected on 2026-11-13 by searching the code, corpus CSVs and docs for URLs (`engine/`, `api/`,
`scripts/`, `experiments/`, `data/corpus/`, `docs/`). "Read on" dates come from docs/DECISIONS.md and
docs/PROGRESS_FINALE.md. A source listed here is not proof that its figure is correct: corpus rows stay
`verified=no` until a human opens the link and checks every number (docs/DATA_COLLECTION_GUIDE.md).

## 1. Indian trip reports (data/corpus/trips_india.csv seeds T0001-T0006, all verified=no)

| Row(s) | Source | Used for |
|---|---|---|
| T0001 | https://www.team-bhp.com/news/bhubaneswar-kolkata-back-my-xev-9e-key-highlights-trip-stats | Mahindra XEV 9E, Kolkata -> Midnapore, 302 km, SoC 83 -> 22 (open question: one way or with local driving; routed 126 km in F3) |
| T0002 | https://www.rushlane.com/tata-nexon-electric-range-battery-road-trip-12388665.html | Tata Nexon EV 30.2 kWh, 109.7 km, SoC 100 -> 52 (route to be filled) |
| T0003 | https://www.team-bhp.com/news/tata-nexon-ev-max-highway-drive-efficiency-max-range-more | Nexon EV Max, 130 km highway, 174 Wh/km (consumption) |
| T0004 | https://www.cardekho.com/india-car-news/5-things-we-learnt-by-draining-the-tata-nexon-ev-max-battery-29982.htm | Nexon EV Max drain test, 293.3 km to 1 % (range_test; start SoC to be filled) |
| T0005, T0006 | https://www.autocarindia.com/auto-features/tata-nexon-ev-range-real-world-mileage-test-45kwh-battery-433577 | Nexon EV 45 kWh, city 7.9 km/kWh and highway 7.67 km/kWh (consumption) |

## 2. Vehicle specifications (data/corpus/vehicles_india.csv, every row "approximate - verify")

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

## 3. Open datasets, papers and published figures

| Source | Used for |
|---|---|
| https://doi.org/10.21227/6jr9-5235 (IEEE DataPort: https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles) | BMW i3 real driving data (TU Munich), F9 REAL validation; raw files in data/raw/bmw_i3/ (gitignored) |
| https://www.mdpi.com/2032-6653/11/2/41 | Paper describing the BMW i3 dataset (signals, flags) |
| Asensio et al., Nature Sustainability 2020 (cited in docs/TASKS_FINALE.md; no URL recorded in this repo) | The 8 review categories used in F6 (engine/verity/voc.py) |
| https://cis-india.org/raw/workers-experiences-in-app-based-taxi-and-delivery-sectors-key-initial-findings-from-multi-city-quantitative-surveys | EXAMPLE earning rate Rs 60/hour for the ride check (F8a; api/ride.py) |

Published reliability figures behind the charger-trust priors (engine/verity/trust/priors.py: Parliament operational
counts, MMCM on BPCL, Yo Mobility DC downtime, IEEFA Delhi audit) are cited by name in that file and in PLAN.pdf
section 3; the file says they were found by web search on 26 Oct 2026 and must be re-checked. They are weak priors,
not Verity measurements.

## 4. Open services called by the engine (every response cached in data/cache/, gitignored)

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

## 5. Paid / keyed APIs

| Endpoint | Used by | Notes |
|---|---|---|
| https://api.sarvam.ai (`/speech-to-text`, `/text-to-speech`, `/translate`, `/v1/chat/completions`) | engine/verity/voice.py, voice_loop.py, experiments/voc_app_reviews.py | Fields, header and model ids verified against docs.sarvam.ai on 2026-11-12 (DECISIONS F7). Every paid call is logged (path, model, status, ms) |
| Neo4j AuraDB Free (URI in .env, never committed) | engine/verity/trust/graph.py | Charger-truth graph; falls back to the in-memory graph on timeout |

## 6. Hand-collected evidence (filled by the teammate; none yet)

`charger_evidence.csv` (Google Maps reviews, PlugShare, operator apps, X posts, forums, news), `app_status_snapshots.csv`
(Tata EZ Charge, Statiq, Jio-bp pulse, ChargeZone) and `app_reviews.csv` carry a `source_url` or app name on every
row. They had 0 rows on 2026-11-13. After uploads, run `uv run python scripts/run_real_data.py`; the URLs live in
those files.

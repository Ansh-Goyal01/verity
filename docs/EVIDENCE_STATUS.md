# Evidence status and limitations

Status for the finish track. REAL means an external recorded source, not a Verity field validation.
No real Indian validation data exists. Results must be read with their sample sizes and intervals.

| Group | Evidence | Status and provenance |
|---|---|---|
| REAL | Public BMW i3 trips, Germany, heater/AC load | External dataset and existing loader/fixed split: [notes](BMW_I3_NOTES.md), DOI [10.21227/6jr9-5235](https://doi.org/10.21227/6jr9-5235). Raw files absent here; evaluation NOT DONE (dataset not on this machine). No Indian applicability established. |
| REAL | Route geometry | Cached OSRM-compatible public-router snapshot in data/processed/route_noida_agra.json; routing is not a measured driven trip. |
| REAL | Elevation and weather | Cached Open-Meteo lookup/forecast in the route snapshot. Weather is a dated forecast, not current measurements or proof of driving conditions. |
| REAL | Charger locations | OpenStreetMap and Open Charge Map directory, data/processed/chargers_ncr.json. Location and source metadata only; no field audit or observed operational status. |
| REAL | Published reliability figures used as priors | Historical source claims documented in engine/verity/trust/priors.py and [PLAN.pdf](PLAN.pdf), section 3: Parliament operational counts, MMCM BPCL report, Yo Mobility DC downtime, IEEFA Delhi study. These source claims remain approximate/unverified in this finish run; they are weak prior assumptions, not Verity measurements. Do not quote their numbers as verified facts. |
| SIMULATED | E1 simulated learning curve | verity.sim trips; fixed sim_main split, training-only discrepancy widening in R4a. Unique-trip Wilson intervals are descriptive because trips share vehicles. This experiment-only correction does not widen live engine forecasts. |
| SIMULATED | E2, E3, E4, E5 | Simulator-generated trips, reliability worlds, failures, attacks and usage traces. No Indian operational validation. |
| SIMULATED | Charger reports | All demo report evidence, source reliability and reports used in experiments; no actual community audit. |
| SIMULATED | Demo fleet and scenario inputs | Constructed scenarios; inputs selected by a sweep to illustrate three outcomes (A13 in DECISIONS.md). Decisions are computed, not hand-entered, but are not independent validation. |
| SIMULATED | Monte Carlo worlds | Draws from model assumptions, not observed journeys. Displayed confidence is a model estimate rounded to 5%, not a measured safety guarantee. |
| NOT DONE | Real Indian trips | No validation set. |
| NOT DONE | Charger field audit | No Verity audit or ground-truth operational survey. |
| NOT DONE | Driver interviews | No completed interviews. |
| NOT DONE | Fleet pilot | No deployed fleet outcome evidence. |
| NOT DONE | Live OBD/telematics feed | No vehicle integration or measured live battery feed. |
| NOT DONE | BMW real E1 on this machine | Dataset absent; see download steps in README. |
| NOT DONE | Live Sarvam verification | SARVAM_API_KEY absent; client mock-tested only, endpoint/model assumptions still unverified. No TTS/STT calls made. |
| NOT DONE | Live Neo4j verification on this machine | Credentials absent; historical A12 live results are separate from current fake-driver/offline checks. |

Known limits: almost no directory charger has tagged connector power, so dispatch assumes 25 kW
(approximate - verify). Only K_CANDIDATES=6 cost-ranked chains are scored, so a dense network can
crowd out a better chain. Same-site backups remain independent (optional R9 not performed);
common gate/power/operator failures may invalidate that assumption. Site nodes currently represent
types, not a verified physical-site directory. BMW driving is German with heater/AC load, not Indian
conditions. The plan cache is unbounded, endpoints lack rate limiting, and the Neo4j driver has no
socket read timeout. Live updates reweight stored worlds; they do not replan after diversion.
E5 usage traces share the assumed bounce model, and the ten-day evasive ring is missed.

web/js/mock-data.js includes an `audit` source value in simulated mock records. The records are
labelled simulated; that enum is an illustrative UI fixture, not a claim that anyone audited chargers.
The web edit restriction leaves that file untouched.

Use [RESULTS_FOR_DECK.md](RESULTS_FOR_DECK.md) for final measured numbers and
[FINAL_REPORT.md](FINAL_REPORT.md) for execution status; older progress documents are historical.

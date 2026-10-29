# Final finish-track report

Full clean-output run_all: **ALL STEPS OK**. Final pytest: **243 passed, 11 skipped, 1 warning in 53.06s**.

R0, R1, R2, R6, R4a, R7, R8 completed; R3 reused, never regenerated. R5/R4b blocked. R10 full validation, fresh exports and reports completed as recorded below. R9 skipped (optional site-correlation change deferred; limitation retained). Local milestone commits created on main using the user-provided repository-only identity; see PROGRESS_FINISH.md for final R11 status. No push.

| Run step | OK | Seconds | Detail |
|---|---|---|---|
| tests | True | 57.8 | ====================== 243 passed, 11 skipped in 56.61s ======================= |
| E1 | True | 3.2 | Plain regression (no physics)          n=1: MAE 7.02 cov 0.81  n=2: MAE 7.77 cov 0.83  n=3: MAE 18.48 cov 0.89  n=5: MAE 31.20 cov 0.97  n=10: MAE 6.66 cov 0.50 |
| E2 | True | 115.8 | median-forecast relative energy error by class: {'small_ecar': -0.0264, 'compact_esuv': -0.0303, 'e_scooter': -0.0869} |
| E3 | True | 109.5 | 40% chargers down: first stop changed 75% / stranded blind 19.0% vs trust-aware 6.0% (only-blind 27, only-aware 1) / minutes lost 0.6 / plan ms 113.0/101.0 |
| E4 | True | 190.0 | 40% chargers down: stranded fixed 33.0% [0.269, 0.398] vs dispatch 6.0% [0.035, 0.102] (only-fixed 55, only-dispatch 1) / minutes 213.8 vs 215.6 |
| E5 | True | 16.1 |                            ring detection: {'precision': None, 'recall': 0.0, 'worlds_with_any_flag': 0} |
| neo4j_smoke | True | 0.4 | SKIPPED: NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD are not all set in .env (copy .env.example to .env and fill them in) |
| api_examples | True | 13.0 | wrote 14 examples to C:\Users\Anushka Saxena\OneDrive\Desktop\Verity\docs\api_examples |

E1 final held-out evaluation ran exactly once in R4a; R10 verifies the code/training/settings fingerprint and replots its immutable saved evaluation without reopening held-out observations. E2-E5 run at full sizes. Neo4j smoke SKIPPED is not live success. Examples were freshly exported again after run_all.

- V07: NO_GO, rounded model confidence 0.85 (SIMULATED fleet/reports).
- V12: GO, rounded model confidence 0.95 (SIMULATED fleet/reports).
- V19: INSUFFICIENT_EVIDENCE, rounded model confidence 0.9 (SIMULATED fleet/reports).
- V12 replay ends in DIVERT.

## Blockers and exact manual steps

- BMW: **NOT DONE (dataset not on this machine)**. Open https://doi.org/10.21227/6jr9-5235, log in if requested, download Measurement Data.zip; place 70 TripXnn.csv files and Overview.xlsx directly in data/raw/bmw_i3/. Run `uv run python -m verity.datasets.bmw_i3`; require loader_ready, n_trips=70. Preserve the split; fetch https://www.mdpi.com/2032-6653/11/2/41, freeze training-only flag exclusions and documented parameters, then load test with final_evaluation=True exactly once. Label eventual REAL BMW Germany heater/AC data, not Indian conditions. No BMW test trips opened here.
- Sarvam: add your SARVAM_API_KEY locally to .env, verify current official endpoint/header/request/model details, then make at most one TTS and one STT call; stop on auth failure. Key absent, no calls or live verification here.
- Neo4j: provide credentials locally, run existing smoke/parity checks once and benchmark there. Current graph migration has fake-driver coverage only.
- Disclosure: teammate must fill frontend tool names and confirm historical A13 assistant attribution.
- Git: repository-only author identity was supplied by the user and configured locally. Review the local commits before pushing yourself.

## Three claims requiring most caution

1. E1 nominal 90% single-subset bands still under-cover. Mixture coverage is different; the correction is experiment-only.
2. E4 stranding reductions are SIMULATED small-sample outcomes with independent failures, not Indian field validation.
3. Demo confidence/GO/DIVERT outcomes use sweep-selected scenarios; same-site independence, assumed power and six-chain shortlist limit interpretation.

See [EVIDENCE_STATUS.md](EVIDENCE_STATUS.md) and README for all system/evidence limits; [RESULTS_FOR_DECK.md](RESULTS_FOR_DECK.md) contains measured numbers, n/intervals, machine and provenance. No energy/trust/threshold/demo-input changes or new dependencies.


## Do not claim

- Validated in India, accurate on Indian roads, audited chargers, drivers told us, completed interviews or fleet pilot.
- BMW validation done here; Sarvam/Aura live verified here; current-machine Neo4j timing.
- Nominal 90% now guarantees 90%: single-subset bands still under-cover; mixture coverage is a different quantity.
- R4a changed production forecast uncertainty, or repeated subset predictions are independent held-out trips.
- Same-site independence in reality; directory records guarantee access/working status; assumed 25 kW is measured power.
- All attacks detected: E5 misses the ten-day evasive ring.
- Sub-second dispatch or model/MC confidence proves real-world safety.

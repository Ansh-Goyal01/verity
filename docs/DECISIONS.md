# Decisions

One line per decision (library, assumption, threshold). Newest at the bottom.

- 2026-10-27 (A0): Repo layout fixed to match CLAUDE.md. Files had been unpacked into
  `verity-starter/verity-starter/` and `verity-engine/verity-engine/`; unique files were moved to
  the root and `docs/`, three byte-identical duplicates were removed, and the empty shell folders
  were deleted with `rmdir` (which refuses non-empty folders).
- 2026-10-27 (A0): The plan PDF is stored as `docs/PLAN.pdf` (renamed from a long filename
  containing an em dash, to keep tooling safe). `docs/PLAN.md` was only a "replace me" placeholder,
  so it is kept with a pointer to the PDF rather than overwritten.
- 2026-10-27 (A0): The PDF was read with `pypdf` through an ephemeral `uv run --with pypdf
  --no-project`; pypdf is not a project dependency.
- 2026-10-27 (A0): CLAUDE.md says to use plan mode and wait for approval for multi-file changes.
  The overnight run is explicitly "never stop to ask questions", so that rule is waived for this
  run only; each milestone is instead planned in `docs/PROGRESS_ENGINE.md` and tested.
- 2026-10-27 (A0): Commits use the task file's `A<n>: <summary>` format instead of the
  conventional-commit types, and carry no co-author trailer (user rule: attribution disabled).
- 2026-10-27 (A0): `logger/`, `web/`, `design/` belong to the frontend teammate. The engine never
  creates, edits or deletes anything there; CLAUDE.md now says so and no longer mentions Vite,
  React or `npm run dev`.
- 2026-10-27 (design): Driver aggressiveness k scales the inertia/dynamics term (F_inertia = k*m*(1+d)*a).
  A 10 s GPS trace under-resolves real accelerations, so k absorbs sub-sampled dynamics + driver style. In
  forecast.py k also scales planned acceleration activity (shared trip-level factor).
- 2026-10-27 (design): Planning code never sees hidden truth. The simulator writes three kinds of files:
  data/sim/trips/ (verity.trip.v1, what a logger would export), data/sim/context/ (elevation profile and
  weather forecast a planner could look up), data/sim/truth/ (hidden ground-truth parameters, only read
  by E1/E2 scoring code and tests). Simulated SoC readings use source "tap" (trip-level is_simulated=true
  carries the label; the v1 contract is not widened).
- 2026-10-27 (design): Held-out discipline. A separate DEV simulator seed is used for all debugging and
  tuning; the official held-out split is fixed in A4 before any fitting and evaluated once per E1 run.
- 2026-10-27 (design): No networkx; k-shortest paths (Yen) and Dawid-Skene are written in numpy/stdlib to
  stay inside the CLAUDE.md dependency list. python-multipart is NOT added; the STT multipart body is
  parsed with the stdlib email parser if needed (decided in A9).
- 2026-10-27 (design): Calibration = Laplace approximation (MAP via scipy least_squares in log-space,
  Gauss-Newton Hessian + prior precision). Fleet pooling = empirical-Bayes update of the class-level prior
  from other vehicles' posteriors. Predictive band = posterior draws through physics + residual sigma.
- 2026-10-27 (design): Bingo SoC at km x = lowest SoC with P(reach planned charger OR a reachable working
  alternate, with reserve) >= target, computed per run as 1 - prod(1 - p_works) over reachable chargers.
  Point of no return = first km where P(return to last charger/origin behind) drops below target.
- 2026-10-27 (design): INSUFFICIENT_EVIDENCE when confidence < target AND the shortfall is driven by thin
  evidence (vehicle posterior wide because of few trips, or a plan-critical charger with no reports);
  NO_GO when confidence < target with adequate evidence.
- 2026-10-27 (A1): Build backend is hatchling (uv default-compatible) with wheel packages `engine/verity`
  and `api`, giving editable imports for both; pytest lives in the uv dev dependency group.
- 2026-10-27 (A1): `schemas.py` uses `extra="ignore"` so the teammate's logger can add metadata without
  the engine rejecting trips, requires tz-aware timestamps (the browser logger emits ISO strings with a
  zone), and makes `is_simulated` mandatory. The JSON field `schema` is exposed as `schema_id` in Python
  (to avoid shadowing a pydantic method) and `to_json_dict()` restores the contract name.
- 2026-10-27 (A2): "passengers" in a trip log counts every occupant including the driver; mass =
  curb + passengers x PASSENGER_MASS_KG (70 kg, assumption, approximate - verify) + cargo. The e-scooter
  curb mass excludes the rider, who is counted through passengers.
- 2026-10-27 (A2): Energy-term attribution is by operating mode: driving steps split into rolling /
  grade / aero / inertia at p_i / eta_drive; regeneration steps are credited wholly to one negative
  "regen" term. Terms therefore sum exactly to the battery energy (the UI breakdown cannot drift).
- 2026-10-27 (A2): Climate load = aux_w_per_k x |T - 22 C| when AC is on (heating and cooling share one
  term, as the plan says). 22 C neutral point and all coefficients are approximate - verify.
- 2026-10-27 (A3): Simulated dataset sizes: 9 vehicles (3 per class) x 14 trips, GPS every 10 s. Trips are
  shortened/start SoC re-picked so every simulated trip ends with 8-45 % left (a trip log never records a
  stranding). Elevation is a smooth synthetic profile; real Indian terrain is NOT represented.
- 2026-10-27 (A3): Truth-side invented structure (sinusoidal terrain, regime mix, I^2R loss form, efficiency
  knee, 1.15 compressor overhead, 0.0004/trip capacity fade) is simulation machinery to make the truth
  differ from the planner. It is not a claim about real vehicles and appears in no UI or result as fact.
- 2026-10-27 (A4): Calibration method = Laplace approximation (MAP by Levenberg-Marquardt on whitened
  residuals in the unconstrained parameter space; posterior N(u_MAP, (J^T J)^-1) with the prior included).
  Chosen over MCMC because the model is smooth, fits take ~0.1-1 s (fleet experiments and the live API
  stay fast) and the covariance is explicit. Known weakness: skewed posteriors at 1-2 trips.
- 2026-10-27 (A4): Likelihood uses one observation per trip (SoC drop first->last reading): variance =
  1/6 pp^2 (rounding of two whole-percent readings) + (rho_rel x observed drop)^2; rho_rel = 0.06 is a
  relative model-discrepancy allowance chosen on the DEV seed only. It under-covered on held-out vehicles
  (72-81 % inside the nominal 90 % band) and is reported as a known weakness, not re-tuned.
- 2026-10-27 (A4): Fleet pooling = empirical Bayes in the eigenbasis of the average data information; the
  between-vehicle variance is floored at 0.30 x class prior variance (0.05 was overconfident on dev). It did
  not beat the plain class prior with only 2 peer vehicles; both curves are reported.
- 2026-10-27 (A4): E1 baselines: nominal-spec = physics at class prior medians; flat rule = claimed range
  x 0.75; regression = numpy lstsq on 5 trip features. Baseline bands = +/-1.645 x RMS training error
  (floor 0.5 pp). Test trips use the realised speed trace, so E1 measures energy-model + calibration error,
  not traffic forecasting (that is E2/E4).
- 2026-10-27 (A4): Planner treats a segment as stationary when the reported GPS speed is < 0.5 m/s, because
  10 s GPS jitter otherwise adds phantom distance while parked (+4 % distance on a congested trip).
- 2026-10-27 (A4): `uv run` uses Python 3.13.14 here, whereas the bare `python` is 3.12.10 (A0 table);
  the project only requires >= 3.11.
- 2026-10-27 (A5): Forecast shared factors and their spreads (assumptions, approximate - verify): wind error
  sd 1.5 m/s, payload error sd 12 kg, aux multiplier lognormal sd 0.20, day-to-day capacity error 0.02,
  traffic lognormal sd 0.12, segment noise 0.04; the calibration posterior (incl. driver k, usable
  capacity) is also drawn once per run. Planned speeds per road class (car 26/14/6.5 m/s, scooter
  11/8/4), stop-and-go peak and cycles per km are planning assumptions fixed on the DEV seed only.
- 2026-10-27 (A5): Arrival reserve = 10 % SoC (assumption, approximate - verify; a driver-facing setting
  in the API later). E2 success = arrival SoC >= reserve in the truth run.
- 2026-10-27 (A5): Live update = importance weighting of the stored runs by the new SoC reading (sigma 0.8
  pp, widened until >= 40 effective runs), exploiting that trip-level factors are shared so one reading is
  informative about the rest of the trip.
- 2026-10-27 (A5): E2 start SoC = reserve + the forecast's median energy use + U(-10, +14) pp, so predicted
  probabilities spread over 0..1 (a calibration chart needs outcomes on both sides of the threshold).
- 2026-10-27 (A6): Dawid-Skene variant with a per-report latent state (z_r ~ Bernoulli(r_charger)) instead of one
  latent truth per charger, because chargers are intermittent; sens/spec per source with Beta(4,1) priors
  (Beta(8,2) held a lying feed's learned spec too high), chargers start at the operator/site/region prior.
- 2026-10-27 (A6): Source labels collapse to binary: working and occupied -> working; broken, blocked,
  no_power -> not working. Reports decay with a 30-day half-life; ring members keep 5 % weight.
- 2026-10-27 (A6): Usage traces enter as a fixed pseudo-source (P(session | works) = 0.95, P(bounce | broken)
  = 0.70; assumptions, approximate - verify). The simulator generates traces from the same numbers, so E5's
  usage benefit is optimistic by construction.
- 2026-10-27 (A6): Prior evidence sources and strengths are listed in trust/priors.py; the Feb-2024 "ever
  non-functional" figure is deliberately unused (it is not P(works now)). Region "delhi" = NCT bounding box.
- 2026-10-27 (A6): Neo4j: Site nodes are keyed by site type (no site directory yet); connectors are stored as a
  JSON string property; the LEG soc_band is a "lo-hi" string; only ids starting with TEST- can be deleted.
  The Neo4j class takes an injectable driver so its query shapes can be tested without a server.
- 2026-10-27 (A6): No networkx / python-dotenv: ring detection and the .env parser are written in numpy/stdlib.
- 2026-10-27 (A8): Dispatch = graph proposes, Monte Carlo decides. LEG edges are stored per 10-point battery band
  in `InMemoryGraph` (p_reach per band, never one number); Yen's k=6 shortest paths over drive + stop + charge
  minutes (+ a 45-min expected-failure penalty x (1 - p_works) for the trust-aware planner) propose chains; every
  chain plus the no-stop plan is scored by the vectorised Monte Carlo with backups (planned charger -> reachable
  working alternate within 60 km ahead -> carry on if the next stop is reachable -> stranded).
- 2026-10-27 (A8): Planning assumptions, all approximate - verify: 5 % SoC on arrival at a charger, 10 % at the
  destination, charge only up to 80 %, +3 pp margin on the 90th-percentile leg energy, 6 min per stop, 20 min
  wasted on a broken charger, 3 alternates max, charger power assumed 25 kW when unlisted, vehicle max charge power
  by class (30 / 50 / 3 kW), average-power factor 0.85, heat slows charging up to 20 % above 30 C.
- 2026-10-27 (A8): GO / NO_GO follow the DISPLAYED (5 %-rounded) confidence so the UI never shows "90 % / NO_GO";
  2.5 points is below the Monte Carlo's own resolution. When nothing meets the target the most reliable plan is
  chosen comparing confidence at 2-point resolution (ties -> fewer minutes), so noise cannot add pointless stops.
- 2026-10-27 (A8): INSUFFICIENT_EVIDENCE = vehicle with no calibrated trips, OR a plan that meets the target only
  when chargers with stale/unknown evidence work (re-scored with those chargers at the 10th percentile of their
  Beta posterior). The "verify charger X" lever re-scores with the 90th percentile and is offered when it would
  restore the target.
- 2026-10-27 (A8): Bingo Line per segment end from the same runs (sub-sample of 1000): lowest SoC with
  P(1 - prod(1 - p_works) over reachable candidates) >= target, candidates = next planned stop, chargers within
  60 km ahead, and the destination. Point of no return = first km where returning to the last charger/origin is
  below target. Breakdown from the central 40-60 % of runs, rounded so start + sum(terms) = arrival p50 exactly.
- 2026-10-27 (A8): E3/E4 world: truth energy from verity.sim, 12 simulated chargers per 190 km trip, reliabilities
  from a failure-rate scenario (20 % ~ PLAN s3 national non-operational share, 40 % ~ DC fast-charger downtime;
  both approximate - verify), reports from 20 noisy users -> trust engine. 200 trips per scenario (not thousands:
  runtime), Wilson 95 % intervals. The 0 % row in E4 is the control (forecast error only).
- 2026-10-27 (A9): python-multipart is NOT added: the STT multipart body is parsed with the stdlib email parser
  (`verity.voice.parse_multipart`, tested). Sarvam endpoint paths, header and model ids are UNVERIFIED (mock-tested).
- 2026-10-27 (A9): Real route, real OSM chargers (only 4 within 3 km of the expressway route, 92 km gap between the
  second and third) and a SIMULATED fleet/reports. The demo does not pad the corridor with invented chargers. The
  vehicles' SoH (0.78 / 0.98 / 0.95), start SoC (82 / 62 / 40 %) and the report scenarios (well evidenced, thin,
  stale, none) are scenario inputs chosen to illustrate the three decision types; every decision, probability,
  plan and message is computed by the engine. Vehicle health notes are generated from the calibrated posterior.
- 2026-10-27 (A9): API uses 1500 Monte Carlo runs per forecast (CLAUDE.md default is 2000) to keep a full dispatch
  (plan + Bingo + levers, 4 plan evaluations) near 5 s; `VERITY_N_RUNS` overrides. Plans are cached in memory,
  keyed by a hash of the request (identical request -> identical plan_id and response).
- 2026-10-27 (A10): `scripts/run_all.py` deletes only its own named outputs, never data/, the cache or the fixed
  split, and runs every step as a subprocess with a log in results/logs/. `--fast` shrinks the experiments for a
  plumbing check and says its numbers are not results.
- 2026-10-28 (A12): The graph in use is Neo4j only when credentials exist AND `verify_connectivity()` and the sync
  succeed; otherwise an in-memory mirror holding the same data (`VERITY_GRAPH=auto|memory|neo4j`, default auto; the
  tests force memory via `tests/conftest.py` so the suite never depends on the network). `/api/health` reports the
  backend actually in use. If a Neo4j query fails mid-request the request is re-run on the mirror and health flips to
  `in_memory` for good (no automatic reconnect; restart to retry). Failure reasons log the exception CLASS only.
- 2026-10-28 (A12): What reads through the graph: chargers and reports (-> trust -> near-route chargers) on every
  dispatch, and the LEG chains of each plan. Dispatch backups (`alts_for`) stay geometry on the graph-read chargers:
  moving them onto ALTERNATE edges would change behaviour for no gain. Chargers and reports are re-read per dispatch
  (2 queries) so a graph edited outside the API is picked up; `/api/chargers` uses the view from the last dispatch.
- 2026-10-28 (A12): Idempotent sync with MERGE. A report's identity is (source, charger, time); simulated reports
  absent from the current scenario are pruned; the derived road network (segments + ALTERNATE edges) is deleted and
  rebuilt on every sync. `Report.is_simulated` (default false) is stored on REPORTED edges and on Source nodes (a
  source is simulated only if all its reports are). The prune only ever touches simulated reports.
- 2026-10-28 (A12): Both backends return chargers by id, reports by (time, source, charger) and legs by (from, to,
  band). Cypher gives no row order and the Dawid-Skene EM sums floats in report order, so without this the two graphs
  could disagree in the last digits; with it `tests/test_graph_parity.py` compares whole responses exactly.
- 2026-10-28 (A12): LEG edges carry a per-dispatch `scope` (uuid) and are written in one batch, read in one query and
  dropped in a `finally`; START/END are per-scope `RoadSegment` nodes (`<scope>:START`). This also fixes `add_leg`,
  which matched `(:Charger)` on both ends and would have dropped every START/END leg on a real server. Scoped LEGs
  older than 1 h (a crashed request) are swept at startup.
- 2026-10-28 (A12): ALTERNATE `p_reach` stores the alternate's P(works) from the trust model, plus `km_ahead`; whether
  a vehicle has the energy to get there is vehicle-specific and stays with the Monte Carlo. A charger counts as
  coverage only if it has been reported on (trust level not `unknown`) and P(works) >= 0.5 (approximate - verify): an
  unreported charger is not coverage (golden rule 6). Road segments are 10 km slices; the window is the planner's 60 km.
- 2026-10-28 (A12): Ring query = two sources filing the same verdict on >= 4 common chargers within 72 h (approximate -
  verify), grouped into rings, and cross-checked against the trust engine's detector, which is what actually
  down-weights sources. Without the window slow rings show up, and so do honest repeat reporters; E5 already showed a
  10-day evasive ring is missed by the windowed view. The demo has no ring; a live test plants one (TEST- data).
- 2026-10-28 (A12): Measured against the 15 s rule (default 1500 runs, AuraDB Free, this laptop, 9 runs): in-memory
  2.4-2.7 s per dispatch, Neo4j 4.7-5.8 s (14 queries = 2 reads + 4 re-plans x write/read/drop; 2.3-2.9 s of it inside
  Neo4j). A later live run (500 runs) saw 4.9-7.1 s inside Neo4j and 8.0-10.2 s per dispatch; an A/B of the
  query-timeout wrapper on one connection (8 pings, 5 reads each way) showed the wrapper costs nothing, so that
  spread is AuraDB/network variance. Typical and worst observed are under 15 s, so the graph is still read per
  request. BUT one cold start right after the full test suite measured 12-16 s per dispatch at 1500 runs and did not
  reproduce in 6 reruns (cause unknown): on a bad day the 15 s line can be crossed. Reading chargers and reports
  once at startup would save only 2 of the 14 queries; the LEG round trips dominate. For a hard bound: return the
  legs from the write and drop once per dispatch (14 -> ~7 queries), or keep LEGs in memory (`graph=None`) and use
  Neo4j for chargers, reports and the two queries.
- 2026-10-28 (A12, after review): The fallback triggers only on Neo4j, driver and OS errors (`BACKEND_FAILURES`); a bug
  in the engine code run on the graph surfaces as itself and leaves Neo4j on, instead of switching it off for everyone.
  A stale pooled connection gets one retry (`ServiceUnavailable` / `SessionExpired`; every statement is idempotent).
  No automatic reconnect after a fallback (restart to retry).
- 2026-10-28 (A12, after review): Timeouts: connect and pool acquisition 10 s, 30 s server-side per query. Driver 6.4
  has no socket read timeout, so a connection that stalls silently mid-query can still hold a worker thread: NOT
  mitigated.
- 2026-10-28 (A12, after review): Leg cleanup is best-effort (logged by class name; it never fails a plan);
  `sweep_stale_legs` runs at startup and every 10 min and removes scoped legs older than 1 h. A leg whose endpoint is
  not in the graph is a `KeyError`, not a silent drop. A scope may not contain ':' (waypoint ids are `<scope>:START`).
- 2026-10-28 (A12, after review): The AuraDB is shared with the live tests. The engine ignores `TEST-` chargers and
  their reports when it reads; the live tests plant theirs at (0, 0), far from any route, and delete them in `finally`;
  the prune only touches this scenario's simulated reports and never `TEST-` sources. Live tests and a running API
  share one database and one road network, so run them one at a time. A charger removed from the directory is NOT
  pruned from the graph (the parity test would flag the drift).
- 2026-10-28 (A12, after review): `EngineService.chargers / trust / near` are read-only properties over one
  `GraphView` that is swapped in a single assignment. A repeated identical request returns the stored plan
  (idempotency per the contract) without re-reading the graph, so a report filed after the first plan only shows in a
  different request. `/api/health` reports the backend in use as of the last request; a link that died since is
  noticed at the next request. Not fixed, pre-existing: `self.plans` grows without bound and no endpoint is rate
  limited, which matters more now that each new plan costs Neo4j round trips.
- 2026-10-28 (A13): The rebuilt charger directory (207 chargers: OpenStreetMap + Open Charge Map, `ocm_status`
  fetched, 78 OCM records) flipped the demo: V07 NO_GO -> INSUFFICIENT_EVIDENCE, V19 INSUFFICIENT_EVIDENCE -> NO_GO,
  V12 stayed GO. Diagnosed by varying scenario inputs only. The corridor went from 4 to 10 chargers: two real OCM
  stations (CH-0006 at km 118.6, CH-0004 at km 125.6, 110 m from OSM's CH-0003) and three more chargers at km 17-22.
  Ids are numbered by the import and were renumbered (old CH-0002/0001/0004/0006 are now CH-0003/0002/0015/0020, same
  coordinates). V07 now stops at km 119 with two backups next door: raw confidence 0.85 -> 0.90, so only the
  pessimistic reading of its unreported chargers refuses it. V19: the planner shortlists `K_CANDIDATES = 6` chains
  ranked by its cost model, and the denser corridor fills them with 3-stop chains (0.82-0.85), so the 2-stop chain
  that scored 0.90 is never offered.
- 2026-10-28 (A13): Scenario input change 1 of 3: the simulated reports are bound to NAMED chargers
  (`DEMO_REPORTS` of `ScenarioPin`, api/service.py) instead of km bands (first charger in 120-130 / 170-180 / 30-40 /
  15-25 km). CH-0003 (osm:node/13952066560, km 125.7) well evidenced: 10 reports, 9 working + 1 broken, as before.
  CH-0002 (osm:node/9529343817, km 175.0) thinly evidenced. CH-0015 (osm:node/13955464891, km 33.9) stale: 1 working
  report 41 days old, as before. CH-0020 (osm:node/7554422920, km 21.6) no reports, as before. Each pin also carries
  its stable OSM id and expected km: a pin whose charger left the corridor, no longer has that id (renumbered) or moved
  more than 1 km raises `ValueError` at startup (the old code dropped a band with no charger without a word).
- 2026-10-28 (A13): Change 2 of 3: the thinly evidenced charger's record is 1 working + 2 broken (12, 21, 30 days
  old), was 3 working. With 3 working reports its p_works (0.89) kept 3-stop chains cheap enough to crowd out the
  2-stop chain; the mixed record raises their planner cost (failure penalty 45 min x (1 - p_works)), so 2-stop chains
  enter the shortlist and V19 reaches 0.90 again. It is still thin (3 reports).
- 2026-10-28 (A13): Change 3 of 3: V07 starts at 70 % (was 82 %). At 82 % its best chains score 0.887-0.896 and the
  decision rests on the pessimistic reading; at 70 % raw confidence is 0.813-0.837. 74 % was rejected (0.863 at most,
  0.012 from the decision line). Unchanged: V12 62 %, V19 40 %, states of health 0.78 / 0.98 / 0.95, calibration trip
  counts, the simulated trips in `data/sim/demo`, the route, target confidence 0.9.
- 2026-10-28 (A13): Measured outcomes (1500 runs, 8 seeds, raw confidence; confidences are shown to the nearest 5 %,
  so the decision line is 0.875): V07 at 70 % NO_GO 0.813-0.837; V12 at 62 % GO 0.900-0.943; V19 at 40 %
  INSUFFICIENT_EVIDENCE 0.895-0.905; every seed gave the same decision. V12's replay (the 85th-percentile energy run,
  its planned charger CH-0006 reported broken) ends in a DIVERT to CH-0003; driven through `execute_trip` with CH-0006
  down, that run is not stranded (final SoC 17.8 %). V07's replay says SLOW_DOWN (no alternate reachable with
  confidence); `docs/api_examples/replay.json` is now V12's plan, regenerated with `VERITY_GRAPH=memory`.
- 2026-10-28 (A13): DISCLOSURE: these inputs were chosen to illustrate three outcomes and were found by trying
  scenarios against the engine (seven report scenarios, SoC sweeps from 40 to 82 %). The engine, trust model,
  thresholds (`usable_alternate`, confidence rules), experiments and held-out split were not changed to get them. The
  outcomes sit near planner-shortlist boundaries, so a new corridor charger can still flip them: the pins stop silent
  re-targeting, and `tests/test_demo_scenario.py` asserts the three decisions and the DIVERT replay on the committed
  directory, so a data change fails the build instead of the demo. If it does, re-pin and record it here; do not
  loosen the test or touch the engine. Also: `tests/test_routing.py` no longer pins `ocm_status` to BLOCKED (a data
  state); it checks that the label tells the truth about the data.
- 2026-10-28 (BMW i3): The download has no `readme.txt` (only `Overview.xlsx` and `readin.m`) and unpacked as `Measurement Data/`;
  it was renamed to `data/raw/bmw_i3/` (gitignored, never committed). It holds 70 trips (A01-A32 summer, B01-B38 winter) at exactly
  0.1 s, not the 72 the DataPort summary mentions. Everything known about the files, including 8 data-quality problems, is in
  `docs/BMW_I3_NOTES.md`; nothing was fitted.
- 2026-10-28 (BMW i3): The loader (`engine/verity/datasets/bmw_i3.py`) maps each trip to time, speed (m/s), elevation, ambient
  temperature, battery voltage, current, SoC and heating power (W) and **cleans nothing**: no row is dropped, no value repaired, the
  source sign is kept (discharge negative). Problems become flags (`soc_missing_tail`, `soc_jump`, `charging_session`, `heating_spike`,
  `soc_copy_of:<id>`) whose thresholds are read off the files ("approximate - verify"); the caller decides what is unusable, the same
  way for train and test. Heating power is the CAN channel (in all 70 trips; the LIN twin is in 50). `AirCon Power` and the Overview
  metadata (dates, payload notes) are not in the common format yet. Columns are matched on the raw header line and selected by position
  (TripB38's header says `Velocity [km/h]]]`); a missing or ambiguous signal, a non-numeric value or a non-increasing time axis raises.
  No dependency was added: `Overview.xlsx` is not read by the loader (`openpyxl` stays out).
- 2026-10-28 (BMW i3): Known defects are reported, not repaired: TripB07's SoC is a copy of TripB03's (a fixed table found by comparing
  all trips before the split, so the flag does not depend on which trips are loaded together), TripB14's last sample has SoC 0.0,
  TripB02 has a 40 kW heating peak, A-trip heating power is unreliable (use `AirCon Power`), and TripB04 and TripB09 are charging
  sessions.
- 2026-10-28 (BMW i3): ONE fixed split, `data/splits/bmw_i3_split.json`, seed 31 (`SPLIT_SEED`), 30 % held out by trip id and
  stratified by season so both survive in the test set: A 22 train / 10 test, B 27 / 11, 49 / 21 in all. All 70 ids are in it
  (unusable trips are filtered by flag later, equally on both sides). All 70 trips were profiled in aggregate (no model) BEFORE the
  split was written; the split itself depends on the ids only. Never re-split: `tests/test_bmw_i3.py` regenerates it from its recorded
  seed and fails if it differs, and `load_split_part("test")` raises unless `final_evaluation=True` (only the final E1 run passes it).

- 2026-10-29 (R0, Codex CLI): Follow takeover overrides: main only, local commits, no branch deletion, no push/gh; write the multi-file plan in PROGRESS_FINISH.md because plan mode is unavailable. Baseline independently verified. Missing Git identity requested; prepare milestone snapshots while awaiting it.

- 2026-10-29 (R1): Median arrival is exactly the 10% reserve in the live-update test; the extra 12 points saturated confidence. All assertions retained, seeds 0?11 checked outside permanent tests. No forecast implementation change.

- 2026-10-29 (R2): Only missing API query switch and planned-stop null-age tooltip changed. Document ?api= in root README, since the two-file web override supersedes TASKS_FINISH web/README request. Both substitutions are idempotent. Local HTTP/Node verification passed; no paid calls.

- 2026-10-29 (R6): Correct historical A12 naming: ALTERNATE.p_works is charger reliability; sync SETs it and REMOVE a.p_reach. Read and coverage filters migrated together; HTTP contract unchanged. LEG.p_reach is mean(band_midpoint - sampled_leg_energy >= required_arrival_reserve), a genuine conditional energy-reach frequency; retain it. Existing memory backend reused. Live migration not tested: no credentials.

- 2026-10-29 (R4a, BEFORE final evaluation): Experiment-only extra whole-trip Gaussian variance estimated separately for class and pooled methods from 90 training leave-one-trip-out residuals: max(0, mean(((observed - median_predictive)^2 - predictive_variance) / max(abs(median_predictive), 1)^2)). The 1-point floor prevents division near zero; no held-out choice. Existing model/likelihood/rho unchanged. Freeze all fits and 20 seeded training subset draws per k before test loading; k=10 repeats the only full-training subset. MAE averages subset point predictions. Coverage uses their predictive mixture once per unique trip with Wilson n=36, plus per-subset Wilson and mean coverage; within-vehicle dependence makes these descriptive, not cluster-adjusted. No claim of exact Gaussian calibration. Save immutable evaluation and audit; R10 verifies fingerprint/replots instead of evaluating test twice.

- 2026-10-30 (R4a outcome): Final frozen simulated evaluation completed; remaining undercoverage retained and reported. No retuning. R10 E1 step verifies immutable evaluation and training/code fingerprint, regenerating public JSON/chart without re-opening held-out trips. BEFORE_fix artifacts preserved.

- 2026-10-30 (R7): Evidence table separates external recorded geography/locations from simulated operational evidence and NOT DONE Indian validation. Preserve historical logs with status notices; mark original plan uncompleted fieldwork. Do not treat imported directory locations or published weak-prior claims as a Verity field audit. Restricted web mock audit enum remains an explicitly simulated fixture; document it instead of exceeding the two-file web override.

- 2026-10-30 (R8): Append required historical chat/frontend disclosures as user-provided provenance; leave teammate tool names visibly pending. A13 assistant attribution is not in its commits, so mark unknown rather than invent Claude Code use. Historical OCM/Aura/BMW service history distinguished from this Codex-only offline run. Add BMW citation and manual placement/loader-check instructions; no new dependencies.

- 2026-10-30 (R5): BLOCKED. SARVAM_API_KEY absent; no endpoint verification or TTS/STT calls. Manual step: add your key locally to .env, verify current official endpoints/header/request fields/models, make at most one TTS and one STT call, stop on auth failure. Key values must never be printed.

- 2026-10-30 (R4b): BLOCKED. BMW loader check reports raw folder missing/empty. NOT DONE (dataset not on this machine). Download Measurement Data.zip from https://doi.org/10.21227/6jr9-5235 and place 70 TripXnn.csv files plus Overview.xlsx directly in data/raw/bmw_i3/. Require loader_ready and n_trips=70. Preserve split; fetch paper and freeze training-only exclusions before exactly one final test load.

- 2026-10-30 (R10): Full clean-output runner reuses the frozen E1 evaluation to respect one held-out evaluation; all other experiments execute full configured sizes. Generate final/deck reports from artifacts with scripts/finish_report.py; include both coverage definitions, missing live-backend timing, and machine metadata. Optional R9 deferred: same-site independence remains a limitation, and its prerequisite committed R10 run is blocked by missing author identity.

- 2026-10-30 (Git): User supplied repository-only author identity; configured via git config --local. Committed preserved per-milestone index trees in authorized order without modifying working files. Temporary commit helpers removed after successful commits. No push.

- 2026-10-30 (R11): Final audit confirms main, only two authorized web files, untouched fixed splits/physics/forecast/trust scoring/demo inputs, no tracked raw data or .env. No optional R9 engine change, no branches/push/gh. All milestone commits use user-provided repository-local identity.

- 2026-10-30 (Fleet Console follow-up): User explicitly extends frontend edit scope. DispatchResponse has no starting_battery_pct; use selected scenario vehicle soc_pct, not an inferred default. Validate all provided breakdown terms/start/arrival and disclose absence or reconciliation mismatch; keep zero terms and show signed percentage-point deltas. Wrap labels and scroll the waterfall at readable width; include negative cumulative attribution totals without implying a trip timeline. Retain the OSM tile provider and permanent source attribution; expose route-only/retry controls and preserve failure notices after mixed tile outcomes. Two direct tile probes failed with ConnectError, including outside sandbox; no HTTP rejection status or browser DevTools evidence available, so provider blame remains unconfirmed. No model/threshold/demo/result/split changes.

- 2026-10-30 (Tile recovery): Replace lifetime tile-failure counters with per-image pending/failed/loaded state from Leaflet events. Only tile rectangles intersecting the current map viewport affect the notice; unload removes stale state, and move/zoom/resize recompute it. A loaded tile clears its own failure, not another visible failure; pending tiles show loading. Preserve retry, route-only late-event isolation, attribution, provider and model/data behavior. Browser DevTools unavailable; no assertion about the user current visible tile failures.

- 2026-10-30 (Mission Control frontend): User asked for a full frontend redesign. Added web/index.html (six-tab Mission Control) on top of the existing plain HTML/CSS/JS, with no build step and no new dependencies. Offline mode now prefers the saved real engine outputs (docs/api_examples, bundled by scripts/export_web_snapshot.py into web/js/snapshot.js) over hand-written mocks; ?source=mock keeps the old mocks. Evidence charts read the saved results JSON unchanged. Charts withheld for INSUFFICIENT_EVIDENCE plans; levers still shown. Replay moves the vehicle marker only to reported km values and never interpolates battery. console.js, driver.js and their tests are unchanged; console.html and driver.html also load the snapshot. Kept the STYLE.md palette and stamps and added a condensed display face (IBM Plex Sans Condensed).
- 2026-10-30 (Product site): User asked for a premium, animated site that beats competitors' websites. Added web/home.html with css/home.css and js/home.js; the root index.html now redirects there, and Mission Control stays at web/index.html. Vendored Motion 12.43.0 (MIT, released 2026-07-28) as web/vendor/motion-12.43.0.js instead of loading a CDN, so judging works offline (jsDelivr and unpkg were also blocked in the build sandbox). The user's request for a premium look relaxes STYLE.md's no-gradients rule on the landing page only. Mission Control keeps the ops style but gains spring tab entrances. Compare section uses category-level, sourced descriptions (docs/COMPETITORS.md), not checkmark grids, to avoid unverifiable claims about competitors. Hero readout dropped a 'headroom' cell because the saved V12 Bingo Line exceeds expected battery between km 34 and 65 (engine behaviour, flagged to the team, not changed).

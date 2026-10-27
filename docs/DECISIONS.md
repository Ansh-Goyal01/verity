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

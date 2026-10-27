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

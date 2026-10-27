# Claude Code prompt playbook

Paste these into Claude Code in order. After each one: review the diff, run the tests, commit.
For prompts marked **[PLAN]**, press Shift+Tab until plan mode is on before sending, read the
plan, then approve it.

---

## Monday night (26 Oct)

### Prompt 0 — orientation (no code)

```
Read CLAUDE.md and docs/PLAN.md. In 10 lines or fewer, tell me what we are building, which parts
are must-ship by 31 Oct, and anything in the plan that is ambiguous or contradictory. Do not
write any code yet.
```

### Prompt 1 — scaffold the repo [PLAN]

```
Scaffold the repository exactly as described in the "Repo layout" section of CLAUDE.md.
- pyproject.toml managed by uv, Python 3.11+, with the core libraries listed in CLAUDE.md
- engine/verity as an importable package with empty modules and docstrings stating each
  module's job
- engine/verity/schemas.py with pydantic models for the trip log (verity.trip.v1), charger
  record and report, matching CLAUDE.md exactly
- tests/test_schemas.py that validates a sample trip JSON and rejects a malformed one
- docs/AI_USAGE.md (start it: "Claude Code used for scaffolding, 26 Oct") and docs/DECISIONS.md
- README.md with setup and run commands
- .gitignore and .env.example already exist; do not overwrite them, only add to .gitignore if
  something is missing
Then run uv sync and uv run pytest and show me the result.
```

### Prompt 2 — the trip logger (highest priority tonight)

```
Build logger/index.html: a single static file (HTML, CSS, vanilla JS, no build step, no external
dependencies) that teammates open on their phones to log real EV trips. Mobile-first, large
touch targets, works in bright sunlight (high contrast).

Features:
1. Setup screen: vehicle make, model, variant, nominal battery kWh, type (car / 2W / 3W),
   driver id, passengers, cargo kg, AC on/off. Remember the last vehicle on this device.
2. Start trip: record GPS with navigator.geolocation.watchPosition (high accuracy), keep points
   with timestamp, lat, lon, accuracy and speed; drop points with accuracy worse than 50 m.
3. Battery % entry: a big number pad to enter SoC at start, during the trip and at the end.
   Optional: a mic button using the browser's speech recognition if available, falling back to
   the keypad silently. Remind the driver every 10 minutes (vibration if supported).
4. Keep the screen awake during a trip using the Screen Wake Lock API where supported.
5. Autosave the trip in progress to localStorage so a page reload loses nothing.
6. End trip: validate against verity.trip.v1 (same fields as CLAUDE.md), then let the user
   download the JSON and share it via the Web Share API if available.
7. A trips list showing saved trips with distance, duration and SoC used, and a delete button
   with confirmation.
8. A visible note: "Only enter battery % when safely stopped or let a passenger enter it."

Also write logger/README.md explaining how to host it on GitHub Pages (phones only allow GPS on
HTTPS pages) and how teammates should name and share exported files.
Test the validation logic with a small sample in the browser console and tell me how you tested.
```

After this: push to GitHub, enable GitHub Pages for the logger, send the link to the team.

### Prompt 3 — physics core [PLAN]

```
Implement engine/verity/physics.py following "The energy model" section of CLAUDE.md exactly.
- Dataclasses for VehicleParams and Segment; SI units throughout; vectorised with numpy
- A function returning total energy AND the separate contribution of each term (rolling,
  grade, aero, inertia, regen, aux, base) so the UI can show a breakdown
- Air density as a function of temperature
- Per-passenger mass as a named constant with a comment saying it is an assumption
Write tests/test_physics.py with the sanity tests listed in CLAUDE.md, plus: on flat ground at
constant speed the result matches a hand calculation; higher speed above 30 km/h uses more
energy per km. Run the tests and show me the output.
```

### Prompt 4 — BMW i3 dataset loader (after you download the data)

Download the "Battery and Heating Data in Real Driving Cycles" dataset from IEEE DataPort
(free login) into `data/raw/bmw_i3/` first.

```
Inspect the files in data/raw/bmw_i3/. Report: file format, columns, units, sampling rate,
number of trips, and any data quality problems. Then write engine/verity/datasets/bmw_i3.py
that loads each trip into a common internal format (time, speed, elevation, ambient temperature,
battery voltage, current, SoC, heating power).
Create a FIXED train/test split with a set random seed: hold out 30% of trips as test and save
the split to data/splits/bmw_i3_split.json. Do not fit or tune anything yet.
Add a short note to docs/DECISIONS.md about the split.
```

### Prompt 5 — Neo4j smoke test

Create a free AuraDB instance first and put its URI, username and password in `.env`.

```
Write engine/verity/trust/graph.py with a small Neo4j client that reads credentials from .env.
Add a function that creates uniqueness constraints for the node labels in CLAUDE.md, and a
script scripts/neo4j_smoke_test.py that inserts three clearly fake sample chargers (named
"TEST-1" to "TEST-3"), one operator, one site and two REPORTED relationships, queries them
back, prints them, and then deletes the test data. Never print the password.
```

---

## Tuesday (27 Oct)

### Prompt 6 — calibration v0 + first learning curve [PLAN]

```
Implement engine/verity/calibrate.py: Bayesian calibration of the parameters listed in CLAUDE.md
from one or more trips, using the physics model. Start simple and explain the method you choose
(for example a Laplace approximation or a small MCMC) and why, in docs/DECISIONS.md.
Then write experiments/e1_learning_curve.py using ONLY the training split of the BMW i3 data:
calibrate on 1, 2, 3, 5 and 10 trips, and evaluate on the held-out test split. Compare against
three baselines: the nominal-spec model, a flat "minus 25%" rule, and a plain data-driven
regression baseline (numpy least squares on trip features, no physics). Report mean absolute error in SoC percentage points and the share of test
trips inside the 90% predictive band. Save a chart to results/e1_learning_curve.png.
State clearly in the output that this is German driving data with heater load.
```

### Prompt 7 — charger directory import

```
Write scripts/import_chargers.py that builds a charger list for the Delhi NCR region from
OpenStreetMap (charging stations via the Overpass API) and Open Charge Map, merges duplicates
within 50 m, records which source each came from, and saves data/processed/chargers_ncr.json
following the charger record contract. Cache all API responses in data/cache/. Report how many
chargers came from each source and how many were merged. Then load them into Neo4j.
```

---

## Habits that keep the build clean

- Commit after every prompt with a clear message.
- If Claude Code proposes a new dependency, ask why before accepting.
- When it gets something wrong about the project, tell it to "remember" the correction so it is
  added to CLAUDE.md.
- Add a line to docs/AI_USAGE.md at the end of each day.

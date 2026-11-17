# Verity API contract v1

The single source of truth between the engine (Ansh) and the frontend (teammate).
Both sides build against this file. Neither side changes it silently: any change goes in
`docs/API_CONTRACT_CHANGES.md` with the date, the reason, and the old and new shape.

- Base URL: `http://localhost:8000` (frontend reads it from `VITE_API_URL`; mock mode if unset).
- All responses are JSON. All percentages are numbers from 0 to 100. All probabilities are
  numbers from 0 to 1, already rounded to the nearest 0.05 by the engine.
- Every response carries `"is_simulated": true|false`. The UI must show a visible
  "Simulated data" badge whenever it is true.
- **All example values below are illustrative and internally consistent. They are not results
  and must never be displayed as results.**
- Errors: HTTP 4xx/5xx with `{"error": "code", "message": "human readable"}`.

---

## GET /api/health

```json
{"status": "ok", "version": "0.1.0", "neo4j": "connected|in_memory", "is_simulated": true}
```

## GET /api/demo/scenario

The fleet-console demo: one booking and the vehicles that could take it.

```json
{
  "booking": {
    "booking_id": "BK-001",
    "origin": {"name": "Noida", "lat": 28.5355, "lon": 77.3910},
    "destination": {"name": "Agra", "lat": 27.1767, "lon": 78.0081},
    "departure_time": "2026-10-28T14:00:00+05:30",
    "passengers": 4,
    "ac_on": true,
    "target_confidence": 0.9
  },
  "vehicles": [
    {
      "vehicle_id": "V07",
      "label": "Vehicle 7",
      "model_class": "compact_esuv",
      "soc_pct": 82,
      "usable_kwh_estimate": {"p10": 33.0, "p50": 35.5, "p90": 37.5},
      "health_note": "Aged battery: usable capacity estimated below nominal",
      "trips_calibrated": 6
    }
  ],
  "is_simulated": true
}
```

## POST /api/dispatch

Request:
```json
{
  "vehicle_id": "V12",
  "origin": {"lat": 28.5355, "lon": 77.3910},
  "destination": {"lat": 27.1767, "lon": 78.0081},
  "departure_time": "2026-10-28T14:00:00+05:30",
  "start_soc_pct": 60,
  "passengers": 4,
  "ac_on": true,
  "target_confidence": 0.9
}
```

Response (`decision` is one of `GO`, `NO_GO`, `INSUFFICIENT_EVIDENCE`):
```json
{
  "plan_id": "PL-7f3a",
  "vehicle_id": "V12",
  "decision": "GO",
  "confidence": 0.9,
  "arrival_soc": {"p10": 8, "p50": 14, "p90": 19},
  "route": {
    "distance_km": 231.4,
    "duration_min": 205,
    "polyline": [[28.5355, 77.3910], [27.1767, 78.0081]]
  },
  "stops": [
    {
      "charger_id": "CH-0142",
      "name": "Example charger name",
      "lat": 27.9, "lon": 77.7,
      "km_along_route": 120.5,
      "arrive_soc_p50": 22,
      "charge_minutes": 22,
      "p_works": 0.9,
      "evidence": {"n_reports": 7, "days_since_last": 3, "trust_level": "high"}
    }
  ],
  "alternates": [
    {
      "for_stop_index": 0,
      "charger_id": "CH-0188",
      "name": "Example alternate",
      "lat": 27.95, "lon": 77.65,
      "p_reach": 0.95,
      "p_works": 0.8
    }
  ],
  "bingo_line": [
    {"km": 0, "bingo_soc_pct": 45, "expected_soc_pct": 60, "expected_soc_p10": 58, "expected_soc_p90": 61}
  ],
  "point_of_no_return_km": 104.0,
  "breakdown": [
    {"term": "rolling", "label": "Rolling resistance", "soc_pct": -20},
    {"term": "aero", "label": "Air drag", "soc_pct": -32},
    {"term": "grade", "label": "Climbs", "soc_pct": -3},
    {"term": "inertia", "label": "Stop-and-go", "soc_pct": -8},
    {"term": "regen", "label": "Regenerative braking", "soc_pct": 5},
    {"term": "aux", "label": "AC and electronics", "soc_pct": -14},
    {"term": "charging", "label": "Charging stops", "soc_pct": 26}
  ],
  "levers": [
    {"lever_id": "cruise_90", "label": "Cruise at 90 instead of 100 km/h", "new_confidence": 0.95, "time_cost_min": 12}
  ],
  "risk_sources": [
    {"kind": "unknown", "label": "Charger CH-0142 has few recent reports"},
    {"kind": "unpredictable", "label": "Traffic on the expressway exit is highly variable"}
  ],
  "refusal_reason": null,
  "compute_ms": 850,
  "is_simulated": true
}
```

Rules the UI relies on:
- Starting SoC plus the sum of `breakdown` terms (including the `charging` term) equals
  `arrival_soc.p50` within rounding. In the example: 60 - 72 + 26 = 14.
- `bingo_line` has one point per km or per segment, ordered by `km`.
- When `decision` is `INSUFFICIENT_EVIDENCE`, `refusal_reason` is a human-readable string and
  `levers` may suggest what would make the trip plannable.
- `risk_sources[].kind`: `unknown` = we lack data; `unpredictable` = the world is variable.

## POST /api/forecast/update

Live update during a trip (driver reads out a new battery %).

Request:
```json
{"plan_id": "PL-7f3a", "t": "2026-10-28T14:55:00+05:30", "lat": 28.10, "lon": 77.60, "soc_pct": 36}
```

Response:
```json
{
  "plan_id": "PL-7f3a",
  "km_along_route": 64.2,
  "confidence": 0.8,
  "arrival_soc": {"p10": 3, "p50": 9, "p90": 14},
  "bingo_soc_pct_here": 30,
  "action": "CONTINUE",
  "divert_to": null,
  "message": "Battery is draining faster than planned; still above the Bingo Line.",
  "is_simulated": true
}
```

`action` is one of `CONTINUE`, `DIVERT`, `SLOW_DOWN`. When `DIVERT`, `divert_to` holds a charger
object with `charger_id, name, lat, lon, p_works`.

## GET /api/chargers?min_lat=&min_lon=&max_lat=&max_lon=

```json
{
  "chargers": [
    {
      "charger_id": "CH-0142",
      "name": "Example charger name",
      "lat": 27.9, "lon": 77.7,
      "operator": "Example network",
      "site_type": "highway",
      "connectors": [{"type": "CCS2", "kw": 60}],
      "p_works": 0.9,
      "trust_level": "high",
      "n_reports": 7,
      "days_since_last": 3,
      "source": "osm|ocm|audit|simulated"
    }
  ],
  "is_simulated": true
}
```

`trust_level` is one of `high`, `medium`, `low`, `unknown`.

## GET /api/demo/replay/{plan_id}

A scripted trip replay for the driver view: ordered events the UI plays back.

```json
{
  "plan_id": "PL-7f3a",
  "events": [
    {"t_offset_s": 0, "type": "soc_reading", "km": 0, "soc_pct": 60},
    {"t_offset_s": 1800, "type": "soc_reading", "km": 48, "soc_pct": 44},
    {"t_offset_s": 2400, "type": "charger_report", "charger_id": "CH-0142", "status": "broken", "source_trust": "high"},
    {"t_offset_s": 2410, "type": "forecast", "action": "DIVERT", "message": "Planned charger reported broken. Divert to the alternate ahead.", "divert_to": {"charger_id": "CH-0188", "name": "Example alternate", "lat": 27.95, "lon": 77.65, "p_works": 0.8}}
  ],
  "is_simulated": true
}
```

## POST /api/voice/tts and POST /api/voice/stt

Proxied to Sarvam by the backend so the API key never reaches the browser.

- `/api/voice/tts` request `{"text": "...", "language": "hi-IN"}` → `{"audio_base64": "...", "format": "wav"}`
- `/api/voice/stt` request: multipart audio file + `language` → `{"text": "...", "soc_pct": 42}`
  (`soc_pct` is null if no battery number was recognised)
- Without a key both return HTTP 503 `{"error": "voice_unavailable", ...}`; the UI then falls back
  to browser speech synthesis and the keypad.

---

# Contract v2 additions (F8, 2026-11-13)

v2 is additive: every v1 endpoint and field above is unchanged. Change log: `docs/API_CONTRACT_CHANGES.md`.

## POST /api/ride-check

A cab driver's "can I take this ride?" check: the ride (current position -> pickup -> drop) AND the after-drop plan
to a trusted charger, in minutes and rupees. Request (defaults in brackets):

```json
{"vehicle_id": "V12", "soc_pct": 45,
 "current": {"lat": 28.6280, "lon": 77.3649}, "pickup": {"lat": 28.5708, "lon": 77.3261},
 "drop": {"lat": 28.5562, "lon": 77.0999},
 "earning_rs_per_hour": null, "passengers": 1, "ac_on": true, "target_confidence": 0.9,
 "departure_time": "2026-11-13T19:00:00+05:30"}
```

`earning_rs_per_hour: null` (or absent) uses the cited EXAMPLE rate (`earning_rate.is_example: true`, with its
source); the UI must label it "example". `departure_time` (default: now) picks the weather forecast hour.

Response (illustrative values, not results):

```json
{"vehicle_id": "V12", "decision": "GO", "confidence": 0.95,
 "reason": "95% confidence of completing the ride and reaching a working charger after the drop (target 90%).",
 "ride": {"distance_km": 41.5, "pickup_km": 11.7, "duration_min": 48, "p_complete": 1.0,
          "drop_soc": {"p10": 34, "p50": 35, "p90": 36}},
 "after_drop": {
   "charger": {"charger_id": "CH-0056", "name": "...", "lat": 28.54, "lon": 77.12, "site_id": "CH-0056",
               "road_km": 4.5, "drive_min": 7, "p_works": 0.9,
               "evidence": {"n_reports": 6, "days_since_last": 1, "trust_level": "high"},
               "recent_failure_days": null, "confidence_as_primary": 0.95, "expected_lost_min": 80,
               "expected_lost_rs": 80, "chosen": true, "note": "Chosen: fewest expected lost minutes at the target confidence."},
   "backups": [{"charger_id": "CH-0071", "name": "...", "same_site_as_primary": false}],
   "expected_lost_min": 80, "expected_lost_rs": 80,
   "minutes": [{"part": "drive", "label": "Drive to the charger", "minutes": 6.6},
               {"part": "backup_drive", "label": "...", "minutes": 0.4},
               {"part": "failed_stops", "label": "...", "minutes": 1.5},
               {"part": "waiting", "label": "...", "minutes": 2.0},
               {"part": "charging", "label": "Charging to 80 %", "minutes": 55.0},
               {"part": "stranding", "label": "...", "minutes": 4.8}]},
 "considered": [{"...same fields as after_drop.charger...": "", "chosen": false,
                 "note": "Avoided: a failure was reported 1 day ago."}],
 "bingo_line": [{"km": 0.0, "bingo_soc_pct": 17, "expected_soc_pct": 45}],
 "levers": [{"lever_id": "ac_off", "label": "Ride with the AC off", "new_confidence": 0.95, "time_cost_min": 0,
             "rupee_cost": 0}],
 "earning_rs_per_hour": 60.0, "compute_ms": 1500,
 "route": {"polyline": [[28.628, 77.365]], "source": "osrm"},
 "earning_rate": {"rs_per_hour": 60.0, "is_example": true, "source": "Example: CIS India (2024) ..."},
 "data_notes": ["Route: osrm (real router); weather: forecast for 2026-11-13T19:00.", "..."],
 "is_simulated": true}
```

Rules the UI can rely on:
- `decision` is GO when P(complete the ride AND reach a working charger after the drop, keeping 5 %) >= target
  (displayed value, 5 % steps); NO_GO otherwise (also when no charger is known near the drop: then
  `after_drop` is null); INSUFFICIENT_EVIDENCE when the vehicle has no calibrated trips or the GO only holds if
  chargers with stale or missing reports work. `reason` always explains the decision in one sentence.
- `after_drop.minutes` parts sum to `expected_lost_min` (+-1 for rounding); rupees = minutes x rate / 60, to the
  nearest 10. `stranding` = 240 min penalty x P(stranded) (an assumption, documented in DECISIONS).
- `bingo_line[i].bingo_soc_pct` = lowest battery % at that ride km from which some after-drop charger is still
  reachable and working with probability >= target; below it, the driver should charge or decline.
- `considered` lists every after-drop option with why it was or was not chosen. Chargers within ~150 m share a
  `site_id`; a backup at the same Site is not independent (`same_site_as_primary`).
- `data_notes` lists what is real, simulated or assumed for this answer; show them under the decision.
- Errors: 404 `unknown_vehicle`; 422 `unsupported_area` (outside the charger directory: Delhi NCR and the
  Noida-Agra corridor); 422 `invalid_request`.

## GET /api/demo/driver (v2, F10)

The second demo's scenario: an EV cab driver in Noida in the evening gets an airport ride. POST `request` unchanged to
`/api/ride-check` to get the decision.

```json
{"story": "19:00, Noida Sector 62: a cab driver at 45 % battery gets a ride request ...",
 "request": {"vehicle_id": "V12", "soc_pct": 45, "current": {"lat": 28.628, "lon": 77.3649},
             "pickup": {"lat": 28.5708, "lon": 77.3261}, "drop": {"lat": 28.5562, "lon": 77.0999},
             "earning_rs_per_hour": null, "passengers": 1, "ac_on": true, "target_confidence": 0.9,
             "departure_time": "2026-11-13T19:00:00+05:30"},
 "pinned_chargers": [{"charger_id": "CH-0089", "name": "...", "role": "nearest to the drop, failures reported ...",
                      "simulated_reports": 4}],
 "is_simulated": true}
```

Route, weather and charger positions are real; the vehicle and every pinned report are SIMULATED (show the badge).

# Contract v3 additions (finale build W, 2026-11-16)

Additive only: no existing field changed meaning.

## `confidence_display` on POST /api/dispatch and POST /api/ride-check (W0.2)

`confidence_display` is the confidence as the screen and the voice show it: `"95%+"` when the rounded
confidence is 0.95 or more, `"under 5%"` when it is 0.05 or less, otherwise the rounded percent (`"80%"`).
The Monte Carlo cannot tell 95 % from certainty, so no screen shows "100%" or "0%". `confidence` (the 5 %-step
number) is unchanged; show `confidence_display` wherever a person reads the number.

```json
{"decision": "GO", "confidence": 0.95, "confidence_display": "95%+", "...": "..."}
```

## `reason` on POST /api/dispatch (W0.6)

One plain sentence naming the charger the decision rests on, built from the plan's own Monte Carlo; the voice
speaks the same sentence (en-IN) or its Hindi template. `INSUFFICIENT_EVIDENCE` repeats `refusal_reason`.

```json
{"decision": "GO", "confidence_display": "95%+",
 "reason": "GO because backup Charger X (95%+ likely to work, 10 reports) is reachable 90% of the time."}
```

Other forms: "NO_GO because the best plan relies on planned stop X (...) and reaches only 80% confidence (target
90%).", and, for plans without a charging stop, "GO because the destination is reached with reserve, without
charging, in 95%+ of runs." / "NO_GO because no charging plan reaches the 90% target; the best reaches 70%."

## `graph` and `sarvam_key_present` on GET /api/health (W0.7)

```json
{"status": "ok", "version": "0.1.0", "neo4j": "in_memory", "is_simulated": true,
 "graph": {"backend": "memory", "nodes": 288, "relationships": 302},
 "sarvam_key_present": true}
```

`graph.backend` is the graph that trust and dispatch actually read (`"neo4j"` or `"memory"`; the older `neo4j` field
says the same thing as `connected` / `in_memory`). `nodes` and `relationships` count what that backend holds: for
Neo4j the whole database, for memory what it would have written. They are re-read at most every 30 s, so polling
health is cheap. `sarvam_key_present` says only whether voice can work on this server; the key is never returned.
Real values are in `docs/api_examples/health.json`.

## HTTP 429 on the voice endpoints (W0.7)

`POST /api/voice/tts`, `POST /api/voice/stt` and `POST /api/voice/ride-check` (both stages) share one budget per client address (default 20 calls per minute,
`VERITY_VOICE_RATE_PER_MIN`; an IPv6 client is counted per /64), because every call is a paid Sarvam call. Over it:

```json
HTTP 429  Retry-After: 40
{"error": "rate_limited", "message": "Too many voice requests from this address; try again in 40 s."}
```

`Retry-After` is whole seconds, and the server lists it in `Access-Control-Expose-Headers`, so `fetch()` on another
origin can read it. Handle the 429 like `voice_unavailable`: fall back to browser speech and the keypad.
Every other endpoint is unlimited.

## Server settings (W0.7, not part of any response)

`VERITY_CORS_ORIGINS` (comma-separated; REPLACES the built-in origins when set), `VERITY_VOICE_RATE_PER_MIN`,
`VERITY_TRUST_PROXY=1` (only behind exactly one proxy that appends `X-Forwarded-For`). Environment first, then
`.env`. A plan not requested for a while may be evicted (the server keeps the 300 most recently used plans): a replay
or live update for an evicted plan returns the existing 404 `unknown_plan`; call `POST /api/dispatch` again.

## `n_runs` on dispatch, forecast update and ride-check (W0.3)

Every probability in these responses comes from a Monte Carlo of `n_runs` runs, and the response says how many
(`"n_runs": 1500`). A forecast update reports the runs of the plan it reweights. Show this number, or the count
"19 of 20 simulated evenings" derived from it, instead of typing "1,500" or "2,000" into a page: the landing page
and the README disagreed on it (F4). The number is the server's `VERITY_N_RUNS` (default 1500).

## `speech` on POST /api/dispatch and `cached` on POST /api/voice/tts (W0.5)

`speech` maps each demo language (`"en-IN"`, `"hi-IN"`) to the exact sentence to speak for the decision: a fixed
template filled with the engine's numbers, spoken as words ("95 percent or more", never "100"), followed by the
reason sentence. **Speak this string; do not build your own.** The server's audio cache is keyed on these exact
strings, which is what lets the demo talk with the wifi off. `speech` is `{}` when the engine cannot say it honestly
(a GO without its Bingo Line).

```json
{"decision": "NO_GO", "speech": {"en-IN": "Do not start yet. The chance of reaching Agra is only 80 percent. NO_GO because the best plan relies on planned stop Tata (85 percent likely to work, 1 report) and reaches only 80 percent confidence (target 90 percent).", "hi-IN": "..."}}
```

`POST /api/voice/tts` now returns `{"audio_base64": "...", "format": "wav", "cached": false}`. When live
text-to-speech fails (no key, Sarvam unreachable, or Sarvam answers an error) the server returns the pre-generated clip
of that exact sentence and language with `"cached": true`; a sentence that was never generated still gets the 503
`voice_unavailable`. Speech-to-text has no offline fallback (arbitrary speech cannot be pre-recorded): it returns the
same 503, and the UI falls back to the keypad. The clips are made by `scripts/build_audio_cache.py` (paid Sarvam calls,
only for sentences not yet cached) into `data/audio_cache/`, and `tests/test_offline_demo.py` runs the whole demo with
every non-local connection refused.

`docs/api_examples/` now also holds `ride_check.json` and `driver_demo.json` (real responses for the pinned airport
ride), and `results/snapshot.json` bundles every example and result the screens show, with `sources` naming the file
behind each section; `tests/test_snapshot.py` fails when the bundle or an example differs from the engine or its file.

## `speech` on POST /api/ride-check, and `POST /api/voice/ride-check` (W3.2)

`POST /api/ride-check` now also returns `speech`: language (`"en-IN"`, `"hi-IN"`) -> the exact sentence to speak for the
answer, a FIXED template filled with the response's own numbers (confidence as `confidence_display` says it, minutes,
rupees, the charger to use after the drop). `{}` when the engine cannot say it honestly. A language model never writes it.
Speak this string; the audio cache is keyed on it.

`POST /api/voice/ride-check` is the driver's voice loop in **two calls**, so a misheard word can never start a ride-check.

**Call 1, hear (multipart/form-data).** Field `audio` (or `file`): one recording, under 30 s and 5 MB. Optional `language`
(`unknown`, the default, lets speech-to-text detect it, or a code such as `hi-IN`) and `speak` (`0` = no audio back).
Response (`stage` is `"confirm"` or `"clarify"`; this call NEVER runs the ride-check):

```
{"stage": "confirm", "transcript": "...", "language_code": "hi-IN", "reply_language": "en-IN" | "hi-IN",
 "slots": {"soc_pct": 45, "pickup": {"id", "name", "lat", "lon"}, "drop": {...}, "missing": [], "unresolved": [],
           "parsed_by": "rules" | "llm" | "rules+llm"},
 "needs_confirmation": true,
 "prompt": "45 percent battery, Sector 18 to IGI Airport Terminal 3. Is that right?",
 "prompt_audio": {"audio_base64": "...", "format": "wav", "cached": false} | null,
 "timings_ms": {"hear": 0, "parse": 0, "llm": null, "decide": null, "speak": 0, "total": 0}, "is_simulated": true}
```

`stage: "clarify"` means something was not understood: `missing` lists `soc_pct` / `pickup` / `drop`, `unresolved` lists
a place name that was heard but is not in the gazetteer (never guessed), and `prompt` asks about exactly that. Show or
speak the `prompt` and record again. `prompt_audio` is `null` when it could not be synthesised: speak `prompt` with browser
speech. The reply is in the language that was heard (`hi-IN` or English).

**Call 2, decide (application/json).** After the driver says yes or no to the read-back:

```
{"confirmed": true, "soc_pct": 45, "pickup_id": "noida-sector-18", "drop_id": "igi-airport-t3", "language": "en-IN",
 "vehicle_id": null, "current": null, "speak": true}
```

Use the values from call 1's `slots`. `vehicle_id` and `current` default to the demo driver's. `confirmed: false` runs
nothing and returns `{"stage": "not_confirmed", "ride_check": null, "speech": null, "audio": null}`. `confirmed: true`
runs the ordinary ride-check and returns `{"stage": "decided", "ride_check": <the POST /api/ride-check response>,
"speech": "<the sentence>", "audio": {...} | null, "timings_ms": {...}}`. This call works with no Sarvam key at all: `audio`
is then the pre-generated clip (`cached: true`) of the pinned ride's sentence, or `null` (speak `speech` with browser speech).

**Errors.** 503 `voice_unavailable` on call 1 when there is no key or Sarvam cannot be reached (speech-to-text has no
offline path: fall back to the keypad); 413 `payload_too_large` over 5 MB of audio (16 KB for call 2); 422
`invalid_request` for a missing recording, an unknown or identical `pickup_id` / `drop_id`, a battery outside 0-100, or a
malformed `language`; 422 `unsupported_ride` on call 2 when no checked route is committed for that pickup and drop (today
only Sector 18 to IGI Airport T3: the engine would otherwise fetch a route from the network, so the voice demo refuses);
429 as above. `timings_ms` is milliseconds
per stage, `null` for a stage the call did not run; `hear` + `parse` + `llm` + `speak` are what the driver waits for.

## Graph audit and live reports (contract v4, W2.6)

All three are additive. Errors use the usual `{"error", "message"}` shape.

**`GET /api/graph/why/{plan_id}`** - the path behind a released plan: the plan, its stops (`STOPS_AT {order}`), backups
(`BACKUP_FOR {stop, p_reach, p_works}`) and chargers it passed over (`AVOIDED {stop, reason}`; `reason` is `no_reports` or
`low_p_works`: inside a stop's backup window, not relied on, and would not count as coverage), each charger's physical `Site`
and `Operator`, its latest 5 reports, their `Source` and `Channel`. Response: `{plan_id, nodes: [{id, label, props}],
edges: [{from, to, type, props}], cypher, query_ms, backend, executed, is_simulated}`. Node ids are `<Label>:<id>`.
404 `unknown_plan` for a plan the graph does not hold (plans are kept for the newest 300 and 24 h).

**`GET /api/graph/charger/{id}`** - one charger's neighbourhood: operator, site and the other chargers there, latest 5 reports
with channels, aliases, the latest 5 plans that used or avoided it. Response as above plus `charger_id` and `trust`
(`{p_works (5 % steps or null), trust_level, n_reports, days_since_last}`). 404 `unknown_charger`.

**`POST /api/reports`** - body `{charger_id, status (working|broken|blocked|occupied|no_power), consent (must be true),
channel ("driver_voice"), note? (<= 200 chars)}`. Writes one `REPORTED` edge (`is_simulated: false`, source
`driver:driver_voice`, channel `driver_voice`) and answers `{charger_id, status, source_id, t, trust_before, trust_after,
backend, is_simulated: false}`; trust is evaluated at the engine's own clock, the same one dispatch uses. 422 `consent_required`, 404 `unknown_charger`, 429 `rate_limited` (own budget, not the voice one) or `already_reported` (one report per address per charger per 10 minutes; `Retry-After` says when).

## Spoken charger report (contract v5, W3.3)

**`POST /api/voice/report`** (call 1 of 2; voice rate limit) - multipart `audio` (+ `language`, `speak`) or JSON
`{transcript (<= 300 chars), language ("unknown"|"en-IN"|"hi-IN"), speak}`. Never writes. Answers `{stage ("confirm"|"clarify"),
transcript, language_code, reply_language, status (or null), charger ({charger_id, name, score} only when exactly one charger
fits every distinctive spoken word, else null), candidates (the best few), needs_confirmation, prompt (the read-back or the
question), prompt_audio, timings_ms, is_simulated: false}`.

**`POST /api/voice/report/confirm`** (call 2 of 2; voice + report rate limits and the per-address per-charger cooldown) - JSON
`{confirmed, charger_id, status, consent, language, transcript?, from_real_person (default true), speak}`. Writes only when
`confirmed` and `consent` are true (422 `consent_required`, 404 `unknown_charger`, 429 `already_reported`). Answers `{stage
("recorded"|"not_recorded"), reply_language, report (the `POST /api/reports` answer, with trust_before / trust_after, or null),
plans_checked (<= 2), plans [{plan_id, vehicle_id, decision_before, decision_after, confidence_before, confidence_after,
moved, changed ("decision"|"stops"|"backups")}], speech, audio, timings_ms, is_simulated}`. `from_real_person=false` stores a
simulated report under source `driver:driver_voice_scripted`. The audio is never stored.

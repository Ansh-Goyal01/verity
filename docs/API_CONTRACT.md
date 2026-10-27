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

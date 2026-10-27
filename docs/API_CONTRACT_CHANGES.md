# API contract change proposals

`docs/API_CONTRACT.md` is **unchanged**. The engine serves exactly its shapes (the response models in
`api/models.py` forbid extra fields, and `tests/test_api.py` validates every endpoint). The items below are
places where real engine output is looser than the contract's *example*, or where the contract text no longer
fits the project. Nothing here is applied unless both sides agree.

## Proposed changes

### 1. `days_since_last` may be `null`  (2026-10-27)
- **Reason:** a charger with no reports in the window has no "days since last report". Inventing a number
  would break golden rule 1; the UI should show "no reports".
- **Old:** `"days_since_last": 3` (integer) in `stops[].evidence` and in `chargers[]`.
- **New:** integer **or `null`**; `null` exactly when `n_reports` is `0`. The engine already sends this.

### 2. `connectors` may be an empty list  (2026-10-27)
- **Reason:** OpenStreetMap rarely tags connector power (1 of 141 chargers in the committed directory has it),
  and the engine does not guess kW. A connector with unknown power is omitted.
- **Old:** `"connectors": [{"type": "CCS2", "kw": 60}]` (example always non-empty).
- **New:** same item shape, but the list can be `[]`. When a stop's charger has no listed power the engine
  assumes 25 kW and says so in `risk_sources` ("Charging power at X is not listed; assumed 25 kW").

### 3. Base URL configuration  (2026-10-27)
- **Reason:** the contract header says the frontend reads the base URL "from `VITE_API_URL`", but the frontend
  is plain HTML/CSS/JS with no build step.
- **Old:** `VITE_API_URL` environment variable, mock mode if unset.
- **New (suggestion, frontend-side only):** a constant (or `?api=` query parameter) in `web/js/api.js`; mock
  mode when it is empty. No engine change.

## Behaviour notes (no shape change)

- **Route coverage:** `POST /api/dispatch` serves the cached Noida -> Agra route only. Origin and destination must
  be within 30 km of Noida (28.5355, 77.3910) and Agra (27.1767, 78.0081), otherwise HTTP 422
  `{"error": "unsupported_route"}`. The weather/elevation snapshot is for departure 2026-10-28 14:00 IST; other
  departure times reuse it.
- **Vehicles:** only the demo fleet `V07`, `V12`, `V19` exists (HTTP 404 `unknown_vehicle` otherwise).
- **`plan_id`:** `PL-` plus 4 or more hex characters (longer only on a hash collision). Posting the identical
  request returns the identical plan. Plans live in memory and are lost when the server restarts.
- **`/api/forecast/update`:** `t` is accepted but not used; the position comes from `lat`/`lon` snapped to the
  route (HTTP 422 `off_route` if more than 15 km away).
- **Voice STT:** the multipart recording field is named `audio` (`file` is also accepted) plus a `language`
  text field. Both voice endpoints return HTTP 503 `voice_unavailable` when no `SARVAM_API_KEY` is set.
- **Error codes used:** `invalid_request`, `invalid_bbox`, `unsupported_route`, `unknown_vehicle`, `unknown_plan`,
  `off_route`, `voice_unavailable`, `not_found`, `method_not_allowed`, `internal_error`.
- **`decision`:** `GO` / `NO_GO` follow the *displayed* (5 %-rounded) confidence; `INSUFFICIENT_EVIDENCE` is
  returned when the vehicle has no calibrated trips, or when the plan only meets the target if chargers with
  stale or missing reports work. `refusal_reason` is non-null exactly then.
- **Everything is simulated except the route and charger directory:** the demo fleet, its trips and the charger
  reports are simulated (`is_simulated: true` on every response). The route, elevation, weather forecast and
  charger locations are real data (OpenStreetMap, Open-Meteo, OSRM).

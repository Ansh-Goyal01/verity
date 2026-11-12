"""F3 enrichment of verified public Indian trips: geocode, route, elevation and historical weather.

Every enriched value carries a provenance string (where it came from, or why it is missing). Network
responses go through ``routing.CachedClient`` (disk cache in data/cache/, persistent request budget).
Nominatim is called at most once per second (its usage policy). A failed fetch leaves the value empty
with provenance ``failed: <reason>``; real records never fall back to a synthetic route.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Callable, Sequence
from datetime import date, datetime

import numpy as np

from verity.corpus import Row
from verity.routing import (
    ELEVATION_CHUNK,
    CachedClient,
    NetworkUnavailable,
    RequestBudgetExceeded,
    RouteData,
    fetch_elevation,
    fetch_route,
    plan_segments,
    position_at,
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NOMINATIM_MIN_INTERVAL_S = 1.0  # Nominatim usage policy: at most 1 request per second
DISTANCE_MISMATCH_FLAG = 0.15  # flag when reported and routed distance differ by more than 15 %
DAYTIME_HOURS = range(8, 21)  # 08:00-20:00 IST: hours averaged for trip weather (assumption)
SCHEMA = "verity.trips_india_enriched.v1"

FETCH_ERRORS = (NetworkUnavailable, RequestBudgetExceeded, KeyError, IndexError, TypeError, ValueError)


def value(v: object, provenance: str) -> dict:
    return {"value": v, "provenance": provenance}


def missing(reason: str) -> dict:
    return {"value": None, "provenance": f"missing: {reason}"}


def failed(exc: Exception) -> dict:
    return {"value": None, "provenance": f"failed: {type(exc).__name__}"}


# ---------------------------------------------------------------- pure helpers
def _month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def weather_window(row: Row) -> tuple[date, date, str] | None:
    """(start, end, provenance) of the dates whose weather stands for the trip, or None."""
    trip = row.get("trip_date_or_month", "")
    if len(trip) == 10:
        day = datetime.strptime(trip, "%Y-%m-%d").date()
        return day, day, "trip date"
    if len(trip) == 7:
        month = datetime.strptime(trip, "%Y-%m").date()
        return (*_month(month.year, month.month), "trip month")
    published = row.get("source_published_date", "")
    if len(published) == 10:
        day = datetime.strptime(published, "%Y-%m-%d").date()
        return (*_month(day.year, day.month), "proxy: publication month (trip date not reported)")
    return None


def aggregate_weather(payload: dict) -> dict:
    """Daytime mean temperature, mean wind speed and vector-mean wind direction from an archive response."""
    hourly = payload["hourly"]
    keep = [i for i, t in enumerate(hourly["time"])
            if int(t[11:13]) in DAYTIME_HOURS and hourly["temperature_2m"][i] is not None]
    if not keep:
        raise ValueError("no daytime hours with data")
    temp = np.array([hourly["temperature_2m"][i] for i in keep], dtype=float)
    speed = np.array([hourly["wind_speed_10m"][i] for i in keep], dtype=float)
    rad = np.radians([hourly["wind_direction_10m"][i] for i in keep])
    direction = math.degrees(math.atan2(np.mean(speed * np.sin(rad)), np.mean(speed * np.cos(rad)))) % 360.0
    return {"temperature_c": round(float(temp.mean()), 1), "wind_speed_mps": round(float(speed.mean()), 2),
            "wind_from_deg": round(direction, 0), "n_hours": len(keep)}


def distance_check(reported_km: float | None, routed_km: float, one_way_or_round: str) -> dict:
    """Compare the reported distance with the routed one (doubled for round trips)."""
    if reported_km is None:
        return {"expected_km": None, "mismatch_frac": None, "flag": False, "note": "no reported distance"}
    factor = 2.0 if one_way_or_round == "round_trip" else 1.0
    expected = routed_km * factor
    frac = abs(reported_km - expected) / expected
    note = "one way unconfirmed; compared with one-way route" if one_way_or_round in ("", "unknown") else ""
    return {"expected_km": round(expected, 1), "mismatch_frac": round(frac, 3),
            "flag": frac > DISTANCE_MISMATCH_FLAG, "note": note}


def match_vehicle(row: Row, vehicles: Sequence[Row]) -> dict:
    """Pick the vehicles_india.csv variant: same make+model, then the same battery size if reported."""
    key = (row["make"].lower(), row["model"].lower())
    same = [v for v in vehicles if (v["make"].lower(), v["model"].lower()) == key]
    kwh = row.get("battery_kwh_nominal", "")
    if kwh:
        same = [v for v in same if math.isclose(float(v["battery_nominal_kwh"]), float(kwh))]
    if len(same) == 1:
        return value(same[0]["vehicle_id"], "vehicles_india.csv match on make, model" + (", battery" if kwh else ""))
    return missing("no unique vehicles_india.csv variant" if same else "model not in vehicles_india.csv")


def _num(text: str) -> float | None:
    return float(text) if text else None


def reported_fields(row: Row) -> dict:
    """The self-reported values, unchanged, each labelled as reported (or missing)."""
    out = {}
    for name in ("distance_km", "start_soc_pct", "end_soc_pct", "energy_wh_per_km", "avg_speed_kmh",
                 "passengers", "ambient_temp_c", "battery_kwh_nominal"):
        out[name] = value(_num(row[name]), "reported") if row.get(name) else missing("not reported")
    for name in ("speed_band", "ac", "one_way_or_round", "drive_mode", "regen_mode", "trip_date_or_month"):
        out[name] = value(row[name], "reported") if row.get(name) else missing("not reported")
    return out


# ---------------------------------------------------------------- network steps
def geocode(client: CachedClient, place: str, sleep: Callable[[float], None]) -> dict:
    try:
        payload, cached = client.get_json(NOMINATIM_URL, {"q": place, "format": "jsonv2", "limit": 1,
                                                          "countrycodes": "in"})
    except FETCH_ERRORS as exc:
        return failed(exc)
    if not cached:
        sleep(NOMINATIM_MIN_INTERVAL_S)
    if not payload:
        return missing(f"Nominatim found no match for '{place}'")
    hit = payload[0]
    return value({"lat": float(hit["lat"]), "lon": float(hit["lon"]), "display_name": hit.get("display_name", "")},
                 "nominatim (OpenStreetMap), first match, countrycodes=in")


def elevation_profile(client: CachedClient, route: RouteData) -> tuple[np.ndarray, np.ndarray]:
    """At most ELEVATION_CHUNK evenly spaced samples, so one request per trip."""
    n = int(min(ELEVATION_CHUNK, max(2, math.ceil(route.distance_km) + 1)))
    s = np.linspace(0.0, route.cum_m[-1], n)
    lat, lon = position_at(route, s)
    return s, fetch_elevation(client, lat, lon)


def archive_weather(client: CachedClient, lat: float, lon: float, start: date, end: date) -> dict:
    payload, _ = client.get_json(ARCHIVE_URL, {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": start.isoformat(),
        "end_date": end.isoformat(), "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms", "timezone": "Asia/Kolkata"})
    return aggregate_weather(payload)


def weather_block(client: CachedClient, row: Row, lat: float | None, lon: float | None) -> dict:
    if row.get("ambient_temp_c"):
        return value({"temperature_c": float(row["ambient_temp_c"])}, "reported ambient temperature")
    if lat is None or lon is None:
        return missing("no route location for weather")
    window = weather_window(row)
    if window is None:
        return missing("no trip or publication date")
    start, end, label = window
    try:
        data = archive_weather(client, lat, lon, start, end)
    except FETCH_ERRORS as exc:
        return failed(exc)
    return value({**data, "start": start.isoformat(), "end": end.isoformat()},
                 f"open-meteo archive (ERA5 reanalysis), route midpoint, {label}, daytime mean")


def _segments(client: CachedClient, route: RouteData, weather: dict) -> tuple[dict, dict]:
    try:
        s, elevation = elevation_profile(client, route)
    except FETCH_ERRORS as exc:
        return failed(exc), missing("no elevation profile")
    w = weather["value"] or {}
    wx = [{"temperature_c": w.get("temperature_c", math.nan), "wind_speed_mps": w.get("wind_speed_mps", 0.0),
           "wind_from_deg": w.get("wind_from_deg", 0.0)}]
    _, detail = plan_segments(route, s, elevation, wx, np.array([route.cum_m[-1] / 2.0]))
    elev = value({"n_samples": len(s), "min_m": round(float(elevation.min()), 1),
                  "max_m": round(float(elevation.max()), 1)}, "open-meteo elevation API (Copernicus DEM ~90 m)")
    kept = {k: detail[k] for k in ("km_end", "road_class", "grade_pct", "headwind_mps")}
    kept["temp_c"] = [None if math.isnan(t) else t for t in detail["temp_c"]]
    seg = value(kept, "1 km segments from route + elevation + weather (temp_c null when weather missing)")
    return elev, seg


def route_block(client: CachedClient, row: Row, origin: dict, destination: dict) -> dict:
    """Route, distance check, weather, elevation and 1 km segments for a trip with both ends geocoded."""
    a, b = origin["value"], destination["value"]
    try:
        route = fetch_route(client, (a["lat"], a["lon"]), (b["lat"], b["lon"]))
    except FETCH_ERRORS as exc:
        return {"route": failed(exc), "weather": weather_block(client, row, None, None)}
    mid_lat, mid_lon = (float(v[0]) for v in position_at(route, np.array([route.cum_m[-1] / 2.0])))
    weather = weather_block(client, row, mid_lat, mid_lon)
    elevation, segments = _segments(client, route, weather)
    return {
        "route": value({"distance_km": round(route.distance_km, 2), "duration_min": round(route.duration_s / 60, 1),
                        "midpoint": {"lat": round(mid_lat, 5), "lon": round(mid_lon, 5)}},
                       "OSRM public demo router, fastest route between geocoded ends"),
        "distance_check": distance_check(_num(row.get("distance_km", "")), route.distance_km,
                                         row.get("one_way_or_round", "")),
        "weather": weather, "elevation": elevation, "segments": segments,
    }


def enrich_record(client: CachedClient, row: Row, vehicles: Sequence[Row],
                  sleep: Callable[[float], None]) -> dict:
    record = {"record_id": row["record_id"], "record_type": row["record_type"], "source_url": row["source_url"],
              "source_kind": row["source_kind"], "make": row["make"], "model": row["model"],
              "variant": row.get("variant", ""), "is_simulated": False,
              "vehicle_id": match_vehicle(row, vehicles), "reported": reported_fields(row)}
    if not (row.get("origin") and row.get("destination")):
        no_place = missing("origin/destination not reported")
        return {**record, "origin": no_place, "destination": no_place, "route": missing("no origin/destination"),
                "weather": weather_block(client, row, None, None)}
    origin = geocode(client, row["origin"], sleep)
    destination = geocode(client, row["destination"], sleep)
    record = {**record, "origin": origin, "destination": destination}
    if origin["value"] is None or destination["value"] is None:
        return {**record, "route": missing("an end could not be geocoded"),
                "weather": weather_block(client, row, None, None)}
    return {**record, **route_block(client, row, origin, destination)}

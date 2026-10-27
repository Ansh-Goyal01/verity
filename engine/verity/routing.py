"""Route, elevation and weather fetch with an on-disk cache in data/cache/.

All network access in the engine lives here; physics and forecast code receive plain arrays.

Rules (docs/TASKS_ANSH.md hard limits): every response is cached on disk and replayed afterwards; a
persisted request counter refuses to go past ``MAX_REQUESTS`` in total; transient failures back off
exponentially; and if the network is unavailable the caller can fall back to a clearly labelled
SYNTHETIC route (``synthetic_route``). Secrets are never written into cache records.

Services (open, keyless): an OSRM-compatible router (public demo server), Open-Meteo elevation and
forecast. Public endpoints are rate limited and their usage policies change: verify them before
relying on them in production.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np

from .forecast import PlannedRoute
from .paths import CACHE_DIR, PROCESSED_DIR
from .trips import bearing_deg, haversine_m

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
MAX_REQUESTS = 300
USER_AGENT = "verity-hackfront-prototype/0.1 (EV trip planner research; cached, low volume)"
ELEVATION_CHUNK = 100  # Open-Meteo accepts up to 100 coordinates per elevation request
ROUTE_FILE = PROCESSED_DIR / "route_noida_agra.json"

NOIDA = (28.5355, 77.3910)  # as in docs/API_CONTRACT.md
AGRA = (27.1767, 78.0081)

# Road-class thresholds on the router's per-edge speed (m/s): highway >= 20, arterial >= 10, else
# congestion-prone. Based on the router's own default speeds, approximate - verify.
HIGHWAY_MPS, ARTERIAL_MPS = 20.0, 10.0


class RequestBudgetExceeded(RuntimeError):
    pass


class NetworkUnavailable(RuntimeError):
    pass


class CachedClient:
    """HTTP client that replays cached responses, counts real requests, and backs off on errors."""

    def __init__(self, cache_dir: Path = CACHE_DIR, max_requests: int = MAX_REQUESTS, timeout: float = 45.0,
                 retries: int = 3, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cache_dir = Path(cache_dir)
        self.max_requests = max_requests
        self.retries = retries
        self._sleep = sleep
        self._client = client or httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT},
                                              follow_redirects=True)

    # -- request log (persisted so the 300-request limit holds across runs) --
    @property
    def _log_path(self) -> Path:
        return self.cache_dir / "request_log.json"

    def requests_made(self) -> int:
        if self._log_path.exists():
            return int(json.loads(self._log_path.read_text(encoding="utf-8")).get("count", 0))
        return 0

    def _count(self, url: str, status: int | str) -> None:
        log = json.loads(self._log_path.read_text(encoding="utf-8")) if self._log_path.exists() else {"count": 0, "log": []}
        log["count"] += 1
        log["log"].append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "url": url, "status": status})
        self._log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")

    @staticmethod
    def _key(method: str, url: str, params: dict | None, data: dict | None) -> str:
        blob = json.dumps([method, url, params or {}, data or {}], sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]

    def request_json(self, method: str, url: str, *, params: dict | None = None, data: dict | None = None,
                     headers: dict | None = None) -> tuple[Any, bool]:
        """(parsed JSON, served_from_cache). ``headers`` (e.g. an API key) are never cached or logged."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{self._key(method, url, params, data)}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"], True
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if self.requests_made() >= self.max_requests:
                raise RequestBudgetExceeded(f"{self.max_requests} requests already made (hard limit)")
            try:
                resp = self._client.request(method, url, params=params, data=data, headers=headers)
                self._count(url, resp.status_code)
                if resp.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                payload = resp.json()
                record = {"request": {"method": method, "url": url, "params": params, "data": data},
                          "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "response": payload}
                path.write_text(json.dumps(record), encoding="utf-8")
                return payload, False
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                last = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (429, 502, 503, 504):
                    break  # a real client error (400/404...) will not improve by retrying
                if attempt < self.retries:
                    self._sleep(min(2.0**attempt, 20.0))
        raise NetworkUnavailable(f"{url}: {type(last).__name__}: {last}")

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> tuple[Any, bool]:
        return self.request_json("GET", url, params=params, headers=headers)

    def post_json(self, url: str, data: dict) -> tuple[Any, bool]:
        return self.request_json("POST", url, data=data)


# --- route -------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class RouteData:
    coords: np.ndarray  # (n, 2) lat, lon
    cum_m: np.ndarray  # (n,) distance from the start
    edge_speed_mps: np.ndarray  # (n - 1,) router speed on each edge
    duration_s: float
    source: str  # "osrm" | "synthetic_fallback"

    @property
    def distance_km(self) -> float:
        return float(self.cum_m[-1] / 1000.0)


def fetch_route(client: CachedClient, origin: tuple[float, float] = NOIDA, destination: tuple[float, float] = AGRA,
                via: list[tuple[float, float]] | None = None) -> RouteData:
    points = [origin, *(via or []), destination]
    coords = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in points)
    payload, _ = client.get_json(OSRM_URL.format(coords=coords),
                                 {"overview": "full", "geometries": "geojson", "steps": "false",
                                  "annotations": "speed,distance"})
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise NetworkUnavailable(f"router returned {payload.get('code')}")
    route = payload["routes"][0]
    ll = np.array([[lat, lon] for lon, lat in route["geometry"]["coordinates"]], dtype=float)
    ann = {k: np.concatenate([np.asarray(leg["annotation"][k], dtype=float) for leg in route["legs"]])
           for k in ("distance", "speed")}
    if len(ann["distance"]) == len(ll) - 1:
        cum = np.concatenate([[0.0], np.cumsum(ann["distance"])])
        speed = ann["speed"]
    else:  # annotations do not line up with the geometry: recompute distances, spread speeds by position
        cum = np.concatenate([[0.0], np.cumsum(haversine_m(ll[:-1, 0], ll[:-1, 1], ll[1:, 0], ll[1:, 1]))])
        speed = np.interp(np.arange(len(ll) - 1), np.linspace(0, len(ll) - 2, max(len(ann["speed"]), 1)),
                          ann["speed"] if len(ann["speed"]) else np.full(1, 20.0))
    return RouteData(ll, cum, speed, float(route["duration"]), "osrm")


def position_at(route: RouteData, s_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(lat, lon) at distances ``s_m`` metres along the route."""
    return np.interp(s_m, route.cum_m, route.coords[:, 0]), np.interp(s_m, route.cum_m, route.coords[:, 1])


def fetch_elevation(client: CachedClient, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    out: list[float] = []
    for i in range(0, len(lat), ELEVATION_CHUNK):
        sl = slice(i, i + ELEVATION_CHUNK)
        payload, _ = client.get_json(ELEVATION_URL, {"latitude": ",".join(f"{v:.5f}" for v in lat[sl]),
                                                      "longitude": ",".join(f"{v:.5f}" for v in lon[sl])})
        out.extend(float(z) for z in payload["elevation"])
    return np.array(out)


def fetch_weather(client: CachedClient, lat: np.ndarray, lon: np.ndarray, departure: datetime) -> list[dict]:
    """Forecast at the departure hour for each point (one request for all points)."""
    payload, _ = client.get_json(FORECAST_URL, {
        "latitude": ",".join(f"{v:.4f}" for v in lat), "longitude": ",".join(f"{v:.4f}" for v in lon),
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms",
        "timezone": "Asia/Kolkata", "forecast_days": 5})
    items = payload if isinstance(payload, list) else [payload]
    hour = departure.strftime("%Y-%m-%dT%H:00")
    out = []
    for item in items:
        times = item["hourly"]["time"]
        idx = times.index(hour) if hour in times else min(range(len(times)), key=lambda k: abs(k - len(times) // 2))
        out.append({"temperature_c": float(item["hourly"]["temperature_2m"][idx]),
                    "wind_speed_mps": float(item["hourly"]["wind_speed_10m"][idx]),
                    "wind_from_deg": float(item["hourly"]["wind_direction_10m"][idx]), "hour": times[idx]})
    return out


# --- segments for physics -------------------------------------------------------------------------------


def plan_segments(route: RouteData, sample_s_m: np.ndarray, elevation_m: np.ndarray, weather: list[dict],
                  weather_s_m: np.ndarray, segment_km: float = 1.0,
                  vtype: str = "car") -> tuple[PlannedRoute, dict]:
    """Cut the route into ``segment_km`` segments: road class, grade, forecast temperature and headwind.

    ``elevation_m`` was sampled at ``sample_s_m`` metres along the route; ``weather`` at ``weather_s_m``.
    """
    total = route.cum_m[-1]
    n = max(1, int(math.ceil(total / (segment_km * 1000.0))))
    edges = np.minimum(np.arange(n + 1) * segment_km * 1000.0, total)
    length = np.diff(edges)
    mid = 0.5 * (edges[:-1] + edges[1:])
    edge_len = np.diff(route.cum_m)
    edge_mid = 0.5 * (route.cum_m[:-1] + route.cum_m[1:])
    seg_of_edge = np.clip(np.searchsorted(edges, edge_mid, side="right") - 1, 0, n - 1)
    weight = np.bincount(seg_of_edge, edge_len, n)
    weighted_speed = np.bincount(seg_of_edge, edge_len * route.edge_speed_mps, n)
    speed = np.where(weight > 0, weighted_speed / np.maximum(weight, 1e-9), HIGHWAY_MPS)
    road_class = np.where(speed >= HIGHWAY_MPS, 0, np.where(speed >= ARTERIAL_MPS, 1, 2))
    lat_e, lon_e = position_at(route, edges)
    z = np.interp(edges, sample_s_m, elevation_m)
    grade = np.arctan(np.diff(z) / np.maximum(length, 1.0))
    brg = bearing_deg(lat_e[:-1], lon_e[:-1], lat_e[1:], lon_e[1:])
    nearest = np.argmin(np.abs(mid[:, None] - weather_s_m[None, :]), axis=1)
    temp = np.array([weather[i]["temperature_c"] for i in nearest])
    wind = np.array([weather[i]["wind_speed_mps"] * math.cos(math.radians(weather[i]["wind_from_deg"] - b))
                     for i, b in zip(nearest, brg)])
    planned = PlannedRoute(vtype, length, road_class.astype(int), grade, temp, wind)
    detail = {"km_end": (np.cumsum(length) / 1000.0).round(3).tolist(), "road_class": road_class.tolist(),
              "elevation_m": [round(float(v), 1) for v in z[1:]], "grade_pct": (np.tan(grade) * 100.0).round(2).tolist(),
              "temp_c": temp.round(1).tolist(), "headwind_mps": wind.round(2).tolist(),
              "lat": lat_e[1:].round(5).tolist(), "lon": lon_e[1:].round(5).tolist()}
    return planned, detail


def synthetic_route(origin: tuple[float, float] = NOIDA, destination: tuple[float, float] = AGRA) -> RouteData:
    """Straight-line SYNTHETIC fallback (clearly labelled), used only when the network is unavailable."""
    n = 400
    lat = np.linspace(origin[0], destination[0], n)
    lon = np.linspace(origin[1], destination[1], n)
    cum = np.concatenate([[0.0], np.cumsum(haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:]))])
    return RouteData(np.column_stack([lat, lon]), cum, np.full(n - 1, 25.0), float(cum[-1] / 25.0), "synthetic_fallback")


def build_route_snapshot(client: CachedClient, departure: datetime, origin: tuple[float, float] = NOIDA,
                         destination: tuple[float, float] = AGRA, via: list[tuple[float, float]] | None = None,
                         segment_km: float = 1.0) -> dict:
    """Fetch (or replay from cache) route + elevation + weather and return the processed snapshot dict."""
    try:
        route = fetch_route(client, origin, destination, via)
        s_samples = np.arange(0.0, route.cum_m[-1] + segment_km * 1000.0, segment_km * 1000.0)
        s_samples = np.minimum(s_samples, route.cum_m[-1])
        lat_s, lon_s = position_at(route, s_samples)
        elevation = fetch_elevation(client, lat_s, lon_s)
        w_s = np.linspace(0.0, route.cum_m[-1], 5)
        w_lat, w_lon = position_at(route, w_s)
        weather = fetch_weather(client, w_lat, w_lon, departure)
        simulated, elev_src = False, "open-meteo elevation (Copernicus DEM ~90 m)"
    except (NetworkUnavailable, RequestBudgetExceeded) as exc:
        route, simulated = synthetic_route(origin, destination), True
        s_samples = np.linspace(0.0, route.cum_m[-1], int(route.cum_m[-1] / (segment_km * 1000.0)) + 1)
        elevation = 200.0 + 20.0 * np.sin(s_samples / 15000.0)
        w_s = np.linspace(0.0, route.cum_m[-1], 5)
        weather = [{"temperature_c": 30.0, "wind_speed_mps": 2.0, "wind_from_deg": 270.0, "hour": "synthetic"}] * 5
        elev_src = f"SYNTHETIC fallback ({type(exc).__name__})"
    planned, detail = plan_segments(route, s_samples, elevation, weather, w_s, segment_km)
    thin = np.unique(np.append(np.arange(0, len(route.coords), max(1, len(route.coords) // 1200)), len(route.coords) - 1))
    return {
        "is_simulated": simulated, "source": route.source, "elevation_source": elev_src,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": {"name": "Noida", "lat": origin[0], "lon": origin[1]},
        "destination": {"name": "Agra", "lat": destination[0], "lon": destination[1]},
        "departure_time": departure.isoformat(), "distance_km": round(route.distance_km, 2),
        "duration_min": round(route.duration_s / 60.0, 1),
        "polyline": route.coords[thin].round(5).tolist(), "polyline_km": (route.cum_m[thin] / 1000.0).round(3).tolist(),
        "weather_points": [{**w, "km": round(float(s) / 1000.0, 1)} for w, s in zip(weather, w_s)],
        "segments": detail, "requests_made_total": client.requests_made(),
    }


def load_planned_route(path: Path = ROUTE_FILE, vtype: str = "car") -> tuple[PlannedRoute, dict]:
    """Load the processed snapshot (no network) as a ``PlannedRoute`` plus its metadata."""
    snap = json.loads(Path(path).read_text(encoding="utf-8"))
    seg = snap["segments"]
    km_end = np.array(seg["km_end"])
    length = np.diff(np.concatenate([[0.0], km_end])) * 1000.0
    planned = PlannedRoute(vtype, length, np.array(seg["road_class"], dtype=int),
                           np.arctan(np.array(seg["grade_pct"]) / 100.0), np.array(seg["temp_c"]),
                           np.array(seg["headwind_mps"]))
    return planned, snap


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch (cached) the Noida->Agra route, elevation and forecast")
    parser.add_argument("--departure", default="2026-10-28T14:00:00+05:30")
    args = parser.parse_args()
    client = CachedClient()
    snap = build_route_snapshot(client, datetime.fromisoformat(args.departure))
    ROUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROUTE_FILE.write_text(json.dumps(snap, separators=(",", ":")), encoding="utf-8")
    cls = np.bincount(snap["segments"]["road_class"], minlength=3)
    print(f"route source={snap['source']} simulated={snap['is_simulated']} {snap['distance_km']} km "
          f"{snap['duration_min']} min; segments by class (highway/arterial/jam-prone): {cls.tolist()}; "
          f"requests made in total: {snap['requests_made_total']} (limit {MAX_REQUESTS}) -> {ROUTE_FILE}")


if __name__ == "__main__":
    main()

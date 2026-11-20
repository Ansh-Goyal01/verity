"""Which route a dispatch request plans on (WP1, docs/FINALE_BUILD.md).

* Both ends within ``ENDPOINT_TOLERANCE_KM`` of the demo booking: the committed Noida-Agra snapshot, exactly as before
  (same plan ids, same speech, same audio cache).
* Anything else: a REAL route fetched through ``CachedClient`` (router, elevation, forecast; cached on disk), kept in
  a bounded in-process cache. A network failure is HTTP 503 ``route_unavailable``: a real user never gets a plan on
  the synthetic straight-line fallback. Trips longer than ``max_trip_km`` are HTTP 422 ``route_too_long``; the
  straight-line distance is checked first, so an obviously long request costs no network call.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from verity.forecast import PlannedRoute
from verity.routing import CachedClient, build_route_snapshot, ist_hour, snapshot_to_route
from verity.trips import haversine_m

from .limits import LruCache, RateLimiter, positive_int

ENDPOINT_TOLERANCE_KM = 30.0  # the demo route serves requests that start/end this close to Noida and Agra
DEFAULT_MAX_TRIP_KM = 700.0  # Ansh's choice, 2026-11-20 (docs/DECISIONS.md WP1)
MIN_TRIP_KM = 1.0
DEFAULT_NEW_ROUTES_PER_HOUR = 20  # server-wide; ~5 budget calls each, so at most ~100 lifetime calls an hour


def new_routes_setting() -> int:
    return positive_int(os.environ.get("VERITY_NEW_ROUTES_PER_HOUR"), DEFAULT_NEW_ROUTES_PER_HOUR,
                        "VERITY_NEW_ROUTES_PER_HOUR")
ROUTE_CACHE_SIZE = 64  # processed snapshots of non-demo routes held in memory (raw responses are on disk anyway)
COORD_DP = 3  # ~100 m: requests this close share one route


class RouteError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True, eq=False)
class ResolvedRoute:
    route: PlannedRoute
    snap: dict
    poly: np.ndarray
    poly_km: np.ndarray
    is_demo: bool


def max_trip_km_setting() -> float:
    raw = os.environ.get("VERITY_MAX_TRIP_KM", "").strip()
    value = float(raw) if raw else DEFAULT_MAX_TRIP_KM
    if value <= 0:
        raise ValueError("VERITY_MAX_TRIP_KM must be positive")
    return value


def parse_departure(text: str) -> datetime | None:
    """The request's ISO-8601 departure time, or None when it is not one."""
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def straight_km(a: dict, b: dict) -> float:
    return float(haversine_m(np.array([a["lat"]]), np.array([a["lon"]]), np.array([b["lat"]]),
                             np.array([b["lon"]]))[0]) / 1000.0


def _too_long(km: float, limit: float) -> RouteError:
    return RouteError(422, "route_too_long", f"This trip is about {km:.0f} km; Verity plans trips up to "
                      f"{limit:.0f} km. Split it into shorter legs.")


class RouteResolver:
    def __init__(self, demo_route: PlannedRoute, demo_snap: dict, booking: dict,
                 client_factory: Callable[[], CachedClient] = CachedClient, max_trip_km: float | None = None,
                 new_routes_per_hour: int | None = None) -> None:
        self.demo = ResolvedRoute(demo_route, demo_snap, np.array(demo_snap["polyline"]),
                                  np.array(demo_snap["polyline_km"]), True)
        self.booking = booking
        self.client_factory = client_factory
        self.max_trip_km = max_trip_km if max_trip_km is not None else max_trip_km_setting()
        self._cache: LruCache[tuple, ResolvedRoute] = LruCache(ROUTE_CACHE_SIZE)
        self.new_routes = RateLimiter(new_routes_per_hour or new_routes_setting(), 3600.0)

    def is_demo(self, origin: dict, destination: dict) -> bool:
        return (straight_km(origin, self.booking["origin"]) <= ENDPOINT_TOLERANCE_KM
                and straight_km(destination, self.booking["destination"]) <= ENDPOINT_TOLERANCE_KM)

    def resolve(self, origin: dict, destination: dict, departure_time: str) -> ResolvedRoute:
        if self.is_demo(origin, destination):
            return self.demo
        crow = straight_km(origin, destination)
        if crow > self.max_trip_km:  # road distance is never shorter than the straight line
            raise _too_long(crow, self.max_trip_km)
        if crow < MIN_TRIP_KM:  # also keeps a zero-length route out of the segment maths
            raise RouteError(422, "route_too_short", f"Origin and destination are under {MIN_TRIP_KM:g} km apart.")
        departure = parse_departure(departure_time)
        if departure is None:
            raise RouteError(422, "invalid_request", "departure_time must be an ISO-8601 date and time, "
                             "for example 2026-11-21T08:00:00+05:30.")
        # Names are not part of the route (the response speaks the request's own name), so they stay out of the key;
        # the hour is the IST forecast hour, so 08:00+05:30 and 02:30Z share a route and 08:00Z does not.
        key = (round(origin["lat"], COORD_DP), round(origin["lon"], COORD_DP), round(destination["lat"], COORD_DP),
               round(destination["lon"], COORD_DP), ist_hour(departure))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        retry_after = self.new_routes.hit("new-route")
        if retry_after is not None:  # protects the lifetime request budget from a loop of distinct routes
            raise RouteError(429, "rate_limited", f"Too many new routes this hour; try again in "
                             f"{max(1, int(retry_after // 60))} min, or pick a route already planned.")
        resolved = self._fetch(origin, destination, departure)
        self._cache[key] = resolved
        return resolved

    def _fetch(self, origin: dict, destination: dict, departure: datetime) -> ResolvedRoute:
        try:
            snap = build_route_snapshot(self.client_factory(), departure, (origin["lat"], origin["lon"]),
                                        (destination["lat"], destination["lon"]),
                                        origin_name="Origin", destination_name="Destination")
        except (OSError, ValueError, KeyError, IndexError, ArithmeticError) as exc:  # unreadable cache, odd response
            raise RouteError(503, "route_unavailable", "Couldn't fetch this route right now.") from exc
        if snap["is_simulated"]:  # the network or the request budget failed: never plan a real user on fake geometry
            raise RouteError(503, "route_unavailable", "Couldn't fetch this route right now. Try again shortly.")
        if snap["distance_km"] > self.max_trip_km:
            raise _too_long(snap["distance_km"], self.max_trip_km)
        return ResolvedRoute(snapshot_to_route(snap), snap, np.array(snap["polyline"]), np.array(snap["polyline_km"]),
                             False)

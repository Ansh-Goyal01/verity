"""Shared set-up for the graph-analysis scripts G1 and G2 (WP8c, 8d): the demo corridors, resolved for real.

Corridor ends are Photon's first result for each place name (cached), never typed coordinates; Noida-Agra is the
committed demo route. Routes come through the service's own RouteResolver (cached router, elevation, forecast), and the
chargers near each route are the ones the planner itself would consider (``EngineService._route_inputs``).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np

from api.service import BOOKING, EngineService
from verity.catalog import vehicle_context
from verity.chargers import RouteCharger
from verity.dispatch import _build_ctx
from verity.forecast import RESERVE_PCT

CORRIDORS = (("Delhi-Jaipur", "Connaught Place", "Jaipur"), ("Delhi-Chandigarh", "Connaught Place", "Chandigarh"),
             ("Delhi-Dehradun", "Connaught Place", "Dehradun"), ("Pune-Mumbai", "Pune", "Mumbai"),
             ("Bengaluru-Mysuru", "Bengaluru", "Mysuru"))
DEPARTURE = "2026-11-22T08:00:00+05:30"
SEED = 8


@dataclass(frozen=True)
class Corridor:
    name: str
    ends: tuple[dict, dict]
    resolved: object  # api.route_resolver.ResolvedRoute
    near: list[RouteCharger]
    trust: dict


def place(svc: EngineService, query: str) -> dict:
    first = svc.geocode(query, 1)["results"][0]
    if not first["name"].casefold().startswith(query.casefold().split()[0]):
        raise SystemExit(f"Photon's first result for {query!r} is {first['name']!r}; not that place")
    return {"name": first["name"], "lat": first["lat"], "lon": first["lon"]}


def corridors(svc: EngineService) -> list[Corridor]:
    view = svc._view
    demo = svc.resolver.demo
    near, trust, _ = svc._route_inputs(view, demo)
    out = [Corridor("Noida-Agra", (BOOKING["origin"], BOOKING["destination"]), demo, near, trust)]
    for name, a, b in CORRIDORS:
        ends = (place(svc, a), place(svc, b))
        resolved = svc._resolve_route({"origin": ends[0], "destination": ends[1], "departure_time": DEPARTURE})
        near, trust, _ = svc._route_inputs(view, resolved)
        out.append(Corridor(name, ends, resolved, near, trust))
    return out


def mid_size_car(svc: EngineService):
    """The catalog car with the median battery size (the lower middle one for an even count)."""
    cars = sorted(svc.catalog.values(), key=lambda c: (c.battery_kwh, c.vehicle_id))
    median = statistics.median_low([c.battery_kwh for c in cars])
    return next(c for c in cars if c.battery_kwh == median)


def range_km(svc: EngineService, car, corridor: Corridor) -> float:
    """The car's p50 range on this corridor from the engine's own Monte Carlo: 100 % down to the arrival reserve, at
    the median battery use per km over the route (a model output, not a measurement)."""
    ctx = _build_ctx(vehicle_context(car, 1, True), corridor.resolved.route, 100.0, 0.9, np.random.default_rng(SEED),
                     svc.cfg)
    per_km = float(np.median(ctx.runs.total_used)) / float(corridor.resolved.poly_km[-1])
    return (100.0 - RESERVE_PCT) / per_km

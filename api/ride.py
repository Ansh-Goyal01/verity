"""Ride check plumbing for the API (F8b): routes, after-drop chargers, evidence signals.

The engine (verity.ridecheck) is pure; this module finds the ride route (a committed bundle in data/processed/, else
the cached router via verity.routing, else a labelled SYNTHETIC fallback), the chargers near the drop, and the
queue / failure evidence from the graph's reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from verity.chargers import RouteCharger, chargers_near_point
from verity.dispatch import Node, make_nodes
from verity.paths import PROCESSED_DIR
from verity.ridecheck import AfterDropOption
from verity.routing import CachedClient, build_ride_bundle
from verity.schemas import ChargerRecord, Report
from verity.trust.engine import ChargerTrust

# Example earning rate (an INPUT, never a result): CIS India, "Workers' experiences in app-based taxi and delivery
# sectors" (15 Feb 2024; surveys 2021-22, 1,048 workers in Delhi NCR, Mumbai, Lucknow, Guwahati): taxi workers'
# median net earnings INR 5,000 and median 84 working hours a week -> 5000 / 84 = ~60 INR per hour. The page puts
# the INR 5,000 next to weekly hours; "per week" is our reading of it (approximate - verify).
EXAMPLE_EARNING_RS_PER_HOUR = 60.0
EARNING_RATE_SOURCE = ("Example: CIS India (2024) median net taxi-worker earnings INR 5,000 over a median 84-hour "
                       "week (surveys 2021-22, incl. Delhi NCR) = about INR 60/hour; approximate - verify. "
                       "https://cis-india.org/raw/workers-experiences-in-app-based-taxi-and-delivery-sectors-key-"
                       "initial-findings-from-multi-city-quantitative-surveys")
AFTER_DROP_RADIUS_KM = 8.0  # chargers within this straight-line distance of the drop are after-drop options
MAX_AFTER_DROP = 8
QUEUE_WAIT_MIN = 20.0  # wait when a charger is found occupied (assumption, approximate - verify)
QUEUE_WINDOW_DAYS = 30.0
FAILURE_STATUSES = ("broken", "no_power")
COORD_TOL_DEG = 2e-4  # a request matches a committed bundle when every point is within ~20 m
RIDE_BUNDLE_FILES = (PROCESSED_DIR / "ride_demo_noida_t3.json",)
NETWORK_ONLY_FOR = ("tesla",)  # chargers reserved for one brand's cars (approximate - verify)


@dataclass(frozen=True)
class Signals:
    queue_wait_min: dict[str, float]
    recent_failure_days: dict[str, float]


def load_bundles(paths: tuple[Path, ...] = RIDE_BUNDLE_FILES) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths if p.exists()]


def _same(a: dict, b: tuple[float, float]) -> bool:
    return abs(a["lat"] - b[0]) <= COORD_TOL_DEG and abs(a["lon"] - b[1]) <= COORD_TOL_DEG


def match_bundle(bundles: list[dict], cur: tuple[float, float], pick: tuple[float, float],
                 drop: tuple[float, float]) -> dict | None:
    return next((b for b in bundles if _same(b["origin"], cur) and _same(b["pickup"], pick)
                 and _same(b["destination"], drop)), None)


def compatible(c: ChargerRecord, make: str = "") -> bool:
    """Brand-reserved networks (e.g. Tesla Superchargers in India) are not options for other makes."""
    text = f"{c.operator} {c.name}".lower()
    return not any(brand in text and brand != make.lower() for brand in NETWORK_ONLY_FOR)


def candidates_near_drop(chargers: list[ChargerRecord], drop: tuple[float, float], make: str = "") -> list[ChargerRecord]:
    usable = [c for c in chargers if compatible(c, make)]
    return [c for c, _ in chargers_near_point(usable, drop[0], drop[1], AFTER_DROP_RADIUS_KM, MAX_AFTER_DROP)]


def fetch_bundle(departure: datetime, cur: tuple[float, float], pick: tuple[float, float], drop: tuple[float, float],
                 candidates: list[ChargerRecord], client: CachedClient | None = None) -> dict:
    """Route + after-drop legs through the cached router (network only on a cache miss)."""
    return build_ride_bundle(client or CachedClient(), departure, cur, pick, drop,
                             [(c.id, list(c.source_ids), c.lat, c.lon) for c in candidates])


def report_signals(reports: tuple[Report, ...], now: datetime) -> Signals:
    """Queue wait = QUEUE_WAIT_MIN x the share of 'occupied' among reports in the last 30 days (0 with no reports);
    recent failure = days since the latest broken / no_power report."""
    recent: dict[str, list[str]] = {}
    failure: dict[str, float] = {}
    for r in reports:
        age = (now - r.t).total_seconds() / 86400.0
        if 0.0 <= age <= QUEUE_WINDOW_DAYS:
            recent.setdefault(r.charger_id, []).append(r.status)
        if r.status in FAILURE_STATUSES and age >= 0.0:
            failure[r.charger_id] = min(age, failure.get(r.charger_id, float("inf")))
    wait = {cid: QUEUE_WAIT_MIN * sts.count("occupied") / len(sts) for cid, sts in recent.items()}
    return Signals(wait, failure)


def options_from_bundle(bundle: dict, chargers: list[ChargerRecord], trust: dict[str, ChargerTrust],
                        signals: Signals, make: str = "") -> tuple[list[AfterDropOption], list[str]]:
    """After-drop options from the bundle's legs. A leg whose charger id now belongs to another charger (directory
    renumbered) is skipped and noted, never attached to the wrong charger."""
    by_id = {c.id: c for c in chargers}
    legs, notes = [], []
    for leg in bundle.get("after_drop_legs", []):
        c = by_id.get(leg["charger_id"])
        if c is None or not set(leg["source_ids"]) & set(c.source_ids):
            notes.append(f"Skipped {leg['charger_id']}: the charger directory changed since the route was fetched.")
            continue
        if compatible(c, make):
            legs.append((c, leg))
    nodes: list[Node] = make_nodes([RouteCharger(c, 0.0, 0.0) for c, _ in legs], trust,
                                   queue_wait_min=signals.queue_wait_min)
    by_node = {n.id: n for n in nodes}
    opts = [AfterDropOption(by_node[c.id], float(leg["km"]), float(leg["minutes"]),
                            signals.recent_failure_days.get(c.id)) for c, leg in legs]
    if any("SYNTHETIC" in leg.get("source", "") for _, leg in legs):
        notes.append("Road legs from the drop to chargers are a SYNTHETIC fallback (straight line x road factor).")
    return opts, notes


def data_notes(bundle: dict, opts: list[AfterDropOption], signals: Signals, n_real: int, n_sim: int) -> list[str]:
    hour = (bundle.get("weather_points") or [{}])[-1].get("hour", "unknown")
    notes = [f"Route: {bundle.get('source')} ({'SIMULATED fallback' if bundle.get('is_simulated') else 'real router'}); "
             f"weather: forecast for {hour}.",
             "After-drop legs are taken as flat urban driving (grade 0); approximate - verify.",
             f"Charger reports used near the drop: {n_real} real, {n_sim} SIMULATED."]
    if any(o.node.kw is None for o in opts):
        notes.append("Charging power is not listed for some chargers; 25 kW is assumed for them.")
    if not any(o.node.id in signals.queue_wait_min for o in opts):
        notes.append("No queue evidence for these chargers: waiting is counted as 0 minutes.")
    return notes


def thin_polyline(poly: list, max_points: int = 400) -> list:
    if len(poly) <= max_points:
        return poly
    idx = np.unique(np.append(np.linspace(0, len(poly) - 1, max_points).astype(int), len(poly) - 1))
    return [poly[int(i)] for i in idx]

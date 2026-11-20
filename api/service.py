"""Engine service behind the API: demo fleet, real route and chargers, trust, stored plans, replay.

What is real and what is simulated (every response says ``is_simulated: true``):
* REAL: the Noida-Agra route, elevation and weather forecast (data/processed/route_noida_agra.json) and
  the OpenStreetMap charger directory (data/processed/chargers_ncr.json).
* SIMULATED: the three demo vehicles and their trips (data/sim/demo, calibrated through the real
  pipeline), and the charger REPORTS (demo inputs below). The reports show a well-evidenced charger, a thinly
  evidenced one, a stale one and an unreported one, and are attached to NAMED chargers (DEMO_REPORTS), not to
  km bands. The reports and the vehicles' starting charge were CHOSEN to illustrate three outcomes (V07
  NO_GO, V12 GO with a DIVERT in its replay, V19 INSUFFICIENT_EVIDENCE); they were found by trying inputs
  against the engine, never by changing it (docs/DECISIONS.md A13). Every decision, probability, plan and
  message that comes out is computed by the engine from them.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import numpy as np

from verity import __version__
from verity.calibrate import calibrate
from verity.chargers import RouteCharger, chargers_near_route, load_chargers, source_of
from verity.dispatch import ALT_AHEAD_KM, PlanState, VehicleContext, dispatch, live_update_plan
from verity.forecast import ForecastConfig, confidence_display, round_prob
from verity.paths import SIM_DIR
from verity.catalog import load_catalog, vehicle_context
from verity.geocode import geocode, geocode_client, normalise_query
from verity.routing import CachedClient, NetworkUnavailable, RequestBudgetExceeded, load_planned_route
from verity.schemas import ChargerRecord, Report
from verity.sim import generate_dataset, load_manifest
from verity.trips import haversine_m, load_sim_observation
from verity.trust.engine import ChargerTrust, TrustEngine
from verity.trust.graph import TEST_PREFIX, ChargerGraph, GraphCounts
from verity.trust.graph_build import (
    PLAN_KEEP,
    PLAN_MAX_AGE_S,
    GraphHandle,
    open_graph,
    plan_record,
    populate_road_network,
)
from verity.trust.clock import rebase_real_reports
from verity.trust.reports_sim import NOW
from verity.ridecheck import RideInput, ride_check
from verity.routing import snapshot_to_route
from verity.vehicles import get_prior
from verity.ride_voice import spoken_ride_answer
from verity.voice_loop import SPEECH_LANGUAGES, spoken_answers

from .data_sources import DEMO_FLEET_LABEL, any_simulated, build_data_sources
from .graph_endpoints import GraphEndpoints
from .limits import LruCache
from .route_resolver import ENDPOINT_TOLERANCE_KM, ResolvedRoute, RouteError, RouteResolver, parse_departure
from .models import DispatchRequest, RideCheckRequest
from .ride import (
    AFTER_DROP_RADIUS_KM,
    EARNING_RATE_SOURCE,
    EXAMPLE_EARNING_RS_PER_HOUR,
    candidates_near_drop,
    data_notes,
    fetch_bundle,
    load_bundles,
    match_bundle,
    options_from_bundle,
    report_signals,
    thin_polyline,
)

log = logging.getLogger(__name__)
AREA_MARGIN_DEG = 0.1  # ride checks serve points inside the charger directory's area plus this margin

DEMO_SEED = 1028
SWEEP_EVERY_S = 600.0  # how often to remove plan legs that a crashed or failed-cleanup request left behind
PLAN_CACHE_SIZE = 300  # stored plans (W0.7): identical requests share one id, so this is distinct plans in play
RIDE_CACHE_SIZE = 300  # fetched ride routes outside the committed bundles
HEALTH_COUNTS_TTL_S = 30.0  # /api/health is polled; a Neo4j count is a ~200 ms round trip, so reuse it for a while
DEMO_FLEET = (("V07", "compact_esuv", 0.78), ("V12", "compact_esuv", 0.98), ("V19", "compact_esuv", 0.95))
# (label, start SoC %, number of trips used for calibration). Scenario inputs, not results.
# V07 starts at 70 % (was 82 %): at 82 % the denser corridor lifts its raw confidence to the target and the
# pessimistic reading refuses it instead; see docs/DECISIONS.md A13.
FLEET_META = {"V07": ("Vehicle 7", 70, 6), "V12": ("Vehicle 12", 62, 8), "V19": ("Vehicle 19", 40, 4)}
BOOKING = {"booking_id": "BK-001", "origin": {"name": "Noida", "lat": 28.5355, "lon": 77.3910},
           "destination": {"name": "Agra", "lat": 27.1767, "lon": 78.0081},
           "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}
CORRIDOR_KM = 3.0  # chargers within this distance of the route are planning candidates
# ENDPOINT_TOLERANCE_KM (imported above) now lives in route_resolver.py; still importable from here.
PIN_KM_TOLERANCE = 1.0  # a pinned charger must still sit this close (km) to where the scenario expects it


@dataclass(frozen=True)
class ScenarioPin:
    """Simulated reports bound to ONE named charger. Charger ids are numbered by the import, so a directory rebuild
    renumbers them: ``source_id`` (the stable OSM/OCM id) and ``km`` guard against attaching the reports to a
    different charger. ``reports`` are (age in days, status)."""

    charger_id: str
    source_id: str
    km: float
    role: str
    reports: tuple[tuple[int, str], ...]


# Demo report scenarios, bound to chargers of data/processed/chargers_ncr.json (207 chargers, OSM + Open Charge Map).
# SIMULATED inputs, chosen to illustrate three outcomes (docs/DECISIONS.md A13).
DEMO_REPORTS: tuple[ScenarioPin, ...] = (
    ScenarioPin("CH-0003", "osm:node/13952066560", 125.7, "well evidenced",
                tuple((a, "working") for a in (1, 2, 3, 4, 6, 8, 10, 12, 15)) + ((5, "broken"),)),
    ScenarioPin("CH-0002", "osm:node/9529343817", 175.0, "thinly evidenced",
                ((12, "working"), (21, "broken"), (30, "broken"))),
    ScenarioPin("CH-0015", "osm:node/13955464891", 33.9, "stale", ((41, "working"),)),
    ScenarioPin("CH-0020", "osm:node/7554422920", 21.6, "no reports", ()),
)


def demo_reports_for(near: Sequence[RouteCharger], pins: Sequence[ScenarioPin] = DEMO_REPORTS) -> list[Report]:
    """The demo's simulated reports, on the pinned chargers only. A pin that no longer matches the directory raises:
    the demo must stop, not silently attach its reports elsewhere (or nowhere) and flip the decisions."""
    by_id = {rc.charger.id: rc for rc in near}
    out: list[Report] = []
    for pin in pins:
        label = f"demo scenario pin {pin.charger_id} ({pin.role}, {pin.source_id}, km {pin.km:g})"
        rc = by_id.get(pin.charger_id)
        if rc is None:
            raise ValueError(f"{label}: no such charger within {CORRIDOR_KM:g} km of the route; the charger directory "
                             "changed, so re-pin the scenario (docs/DECISIONS.md A13)")
        if pin.source_id not in rc.charger.source_ids:
            raise ValueError(f"{label}: that id now belongs to a different charger (its source ids are "
                             f"{list(rc.charger.source_ids)}); the directory was renumbered, so re-pin the scenario")
        if abs(rc.km_along - pin.km) > PIN_KM_TOLERANCE:
            raise ValueError(f"{label}: the charger now sits at km {rc.km_along:.1f}; re-pin the scenario")
        for i, (age, status) in enumerate(pin.reports):
            out.append(Report(charger_id=pin.charger_id, source_id=f"demo-user-{i}", status=status,
                              t=NOW - timedelta(days=age, hours=2 * i), evidence="SIMULATED demo report",
                              is_simulated=True))
    return out


# --- second demo (F10): an EV cab driver in Noida in the evening gets an airport ride ----------------------------------
# The route, weather, chargers and their positions are REAL (committed bundle data/processed/ride_demo_noida_t3.json,
# OSM / Open Charge Map directory). The vehicle (fleet V12) and every charger REPORT below are SIMULATED scenario inputs,
# chosen to illustrate one outcome (a GO that avoids the nearest charger because of recent failure reports and picks a
# trusted one); they were found by trying inputs against the engine, never by changing it (docs/DECISIONS.md F10).
DRIVER_DEMO = {"vehicle_id": "V12", "soc_pct": 45, "current": {"lat": 28.6280, "lon": 77.3649},
               "pickup": {"lat": 28.5708, "lon": 77.3261}, "drop": {"lat": 28.5562, "lon": 77.0999},
               "earning_rs_per_hour": None, "passengers": 1, "ac_on": True, "target_confidence": 0.9,
               "departure_time": "2026-11-13T19:00:00+05:30"}
DRIVER_STORY = ("19:00, Noida Sector 62: a cab driver at 45 % battery gets a ride request from Sector 18 to IGI Airport "
                "Terminal 3. Can they take it, and where should they charge after the drop?")


@dataclass(frozen=True)
class PointPin:
    """Simulated reports on one named charger near the driver demo's drop (ids are guarded like ScenarioPin)."""

    charger_id: str
    source_id: str
    role: str
    reports: tuple[tuple[int, str], ...]


DRIVER_PINS: tuple[PointPin, ...] = (
    PointPin("CH-0089", "osm:node/12973875562", "nearest to the drop, failures reported in the last days",
             ((1, "broken"), (2, "broken"), (9, "working"), (16, "working"))),
    PointPin("CH-0056", "ocm:306802", "well evidenced, working", tuple((a, "working") for a in (1, 2, 3, 5, 7, 9, 12))),
    PointPin("CH-0073", "ocm:307107", "same Site as Aloft, found occupied once", ((2, "working"), (3, "occupied"),
                                                                                 (6, "working"))),
)


def driver_demo_reports(chargers: Sequence[ChargerRecord], pins: Sequence[PointPin] = DRIVER_PINS) -> list[Report]:
    """The driver demo's SIMULATED reports. A pin that no longer matches the directory raises (re-pin, never guess)."""
    by_id = {c.id: c for c in chargers}
    drop = (DRIVER_DEMO["drop"]["lat"], DRIVER_DEMO["drop"]["lon"])
    out: list[Report] = []
    for pin in pins:
        c = by_id.get(pin.charger_id)
        label = f"driver demo pin {pin.charger_id} ({pin.role}, {pin.source_id})"
        if c is None or pin.source_id not in c.source_ids:
            raise ValueError(f"{label}: the charger directory changed; re-pin the driver demo (docs/DECISIONS.md F10)")
        far = haversine_m(np.array([c.lat]), np.array([c.lon]), np.array([drop[0]]), np.array([drop[1]]))[0] / 1000.0
        if far > AFTER_DROP_RADIUS_KM:
            raise ValueError(f"{label}: now {far:.1f} km from the drop; re-pin the driver demo")
        for i, (age, status) in enumerate(pin.reports):
            out.append(Report(charger_id=pin.charger_id, source_id=f"ride-demo-user-{pin.charger_id}-{i}", status=status,
                              t=NOW - timedelta(days=age, hours=i), evidence="SIMULATED driver-demo report",
                              is_simulated=True))
    return out


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class FleetVehicle:
    ctx: VehicleContext
    label: str
    soc_pct: int
    usable_p: tuple[float, float, float]
    note: str


@dataclass(frozen=True)
class PickedVehicle:
    """The vehicle a dispatch plans with, and how the release describes it (WP2)."""

    ctx: VehicleContext
    others: list
    vehicle: dict
    calibration: dict
    source: str


@dataclass
class StoredPlan:
    state: PlanState
    response: dict
    request: dict
    poly: np.ndarray | None = None  # the plan's own route (WP1); None = the demo route
    poly_km: np.ndarray | None = None


def plan_key(req: dict) -> str:
    """sha1 of the canonical request (the plan id prefix and the Monte Carlo seed). A trip end's ``name`` is left out
    when absent, so every request made before names existed keeps its plan id (and its cached audio)."""
    body = {k: req[k] for k in sorted(req)}
    for end in ("origin", "destination"):
        if isinstance(body.get(end), dict) and body[end].get("name") is None:
            body[end] = {k: v for k, v in body[end].items() if k != "name"}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()


REPLAN_LIMIT = 2  # plans re-planned after one report: each is a full Monte Carlo (about 2.4 s in memory)


def plan_charger_ids(response: dict) -> set[str]:
    """Every charger a plan relies on: its stops and the alternates backing them."""
    return {s["charger_id"] for s in response["stops"]} | {a["charger_id"] for a in response["alternates"]}


def _stop_ids(response: dict) -> list[str]:
    return [s["charger_id"] for s in response["stops"]]


def _backups(response: dict) -> list[tuple[int, str]]:
    return sorted((a["for_stop_index"], a["charger_id"]) for a in response["alternates"])


def plan_change(plan_id: str, before: dict, after: dict) -> dict:
    """What a new report did to one plan. ``moved`` = the decision or the chargers it relies on changed; a shifted
    confidence alone is shown (both displays) but is not a move."""
    changed = [name for name, differs in (("decision", before["decision"] != after["decision"]),
                                          ("stops", _stop_ids(before) != _stop_ids(after)),
                                          ("backups", _backups(before) != _backups(after))) if differs]
    return {"plan_id": plan_id, "vehicle_id": after["vehicle_id"], "decision_before": before["decision"],
            "decision_after": after["decision"], "confidence_before": before["confidence_display"],
            "confidence_after": after["confidence_display"], "moved": bool(changed), "changed": changed}


@dataclass(frozen=True)
class GraphView:
    """What trust and dispatch read from the graph: chargers, per-charger trust, and the chargers near the route."""

    chargers: list[ChargerRecord]
    trust: dict[str, ChargerTrust]
    near: list[RouteCharger]
    reports: tuple[Report, ...] = ()


def demo_dispatch_request(vehicle_id: str) -> dict:
    """The demo booking as the ``POST /api/dispatch`` body for one demo vehicle: what the examples, the audio cache and
    the offline test all send, so they all hit the same plan (the request hash is the plan id and the seed)."""
    return {"vehicle_id": vehicle_id, "origin": {"lat": BOOKING["origin"]["lat"], "lon": BOOKING["origin"]["lon"]},
            "destination": {"lat": BOOKING["destination"]["lat"], "lon": BOOKING["destination"]["lon"]},
            "departure_time": BOOKING["departure_time"], "passengers": BOOKING["passengers"], "ac_on": BOOKING["ac_on"],
            "target_confidence": BOOKING["target_confidence"], "start_soc_pct": FLEET_META[vehicle_id][1]}


def canonical_request(model: type, req: dict) -> dict:
    """The request exactly as the API hands it over (validated, defaults filled, 45 -> 45.0), so the seed hashed from
    it is the same for a script and an HTTP call (F8: the driver demo's saved 100 % vs live 95 % was this)."""
    return model.model_validate(req).model_dump()


class EngineService(GraphEndpoints):
    def __init__(self, n_runs: int | None = None, graph_backend: str | None = None,
                 clock: Callable[[], datetime] | None = None,
                 route_client: Callable[[], CachedClient] | None = None,
                 geocoder_client: Callable[[], CachedClient] | None = None) -> None:
        # One client each for the life of the service (functools.cache of a zero-argument factory): httpx clients are
        # thread-safe and CachedClient serialises its own cache, so per-request clients would only leak connections.
        self.geocoder_client: Callable[[], CachedClient] = geocoder_client or functools.cache(geocode_client)
        # Real reports are aged by this clock, simulated ones by reports_sim.NOW (trust/clock.py); injectable for tests.
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self.cfg = ForecastConfig(n_runs=n_runs or int(os.environ.get("VERITY_N_RUNS", "1500")))
        self.route, self.snap = load_planned_route()
        self.poly = np.array(self.snap["polyline"])
        self.poly_km = np.array(self.snap["polyline_km"])
        # Any other route is fetched per request (WP1); the client factory is injectable so tests replay recordings.
        self.resolver = RouteResolver(self.route, self.snap, BOOKING, route_client or functools.cache(CachedClient))
        directory, self.directory_meta = load_chargers()
        self.demo_reports = demo_reports_for(chargers_near_route(directory, self.poly, self.poly_km, CORRIDOR_KM))
        self.driver_demo_reports = driver_demo_reports(directory)
        # The graph (Neo4j when it works, in-memory otherwise) is what trust and dispatch read; the directory file
        # only seeds it. VERITY_GRAPH=auto|memory|neo4j overrides the default (auto).
        self.graph: GraphHandle = open_graph(directory, self.demo_reports + self.driver_demo_reports,
                                             prefer=graph_backend or os.environ.get("VERITY_GRAPH", "auto"))
        self._adopt(self.graph.call(self._read_view))
        self.graph.populate(lambda g: populate_road_network(g, self.near, self.trust, float(self.poly_km[-1]),
                                                            ALT_AHEAD_KM))
        self.fleet = self._build_fleet()
        self.catalog = load_catalog()  # the real Indian cars (WP2): vehicles_india.csv, calibrated on training trips only
        self.plans: LruCache[str, StoredPlan] = LruCache(PLAN_CACHE_SIZE)
        self.ride_bundles = load_bundles()  # committed ride routes (no network); others are fetched and cached
        self._ride_cache: LruCache[tuple, dict] = LruCache(RIDE_CACHE_SIZE)
        self._counts_cache: tuple[str, float, GraphCounts] | None = None  # (backend, taken at, counts)
        self.last_graph_timing: dict = {}
        self._last_sweep = time.monotonic()  # open_graph already swept at startup
        self._replan_slot = threading.Semaphore(1)  # one report-triggered re-plan at a time (each is a full Monte Carlo)

    # -- setup ----------------------------------------------------------------------------------------------
    def _build_fleet(self) -> dict[str, FleetVehicle]:
        if not (SIM_DIR / "demo" / "manifest.json").exists():
            generate_dataset("demo", DEMO_SEED, fleet=DEMO_FLEET, trips_per_vehicle=8)
        manifest, fleet = load_manifest("demo"), {}
        prior = get_prior("compact_esuv")
        prior_usable = prior.nominal_kwh * 0.93
        for veh in manifest["vehicles"]:
            label, soc, n_trips = FLEET_META[veh["vehicle_id"]]
            obs = [load_sim_observation("demo", t) for t in veh["trip_ids"][:n_trips]]
            post = calibrate(prior.u_mean, prior.u_cov, obs, prior.base, prior.nominal_kwh)
            q = post.usable_kwh_quantiles()
            ratio = float(q[1]) / prior_usable
            note = (f"Usable capacity estimated at {q[1]:.1f} kWh, {abs(1 - ratio):.0%} "
                    f"{'below' if ratio < 1 else 'above'} the class typical ({prior_usable:.1f} kWh)")
            if ratio < 0.85:
                note = "Aged battery: " + note[0].lower() + note[1:]
            ctx = VehicleContext(veh["vehicle_id"], "compact_esuv", post, BOOKING["passengers"], True, n_trips)
            fleet[veh["vehicle_id"]] = FleetVehicle(ctx, label, soc, (float(q[0]), float(q[1]), float(q[2])), note)
        return fleet

    # -- endpoints ----------------------------------------------------------------------------------------------
    def health(self) -> dict:
        """``neo4j`` says "connected" only while Neo4j is the graph that trust and dispatch actually read; ``graph``
        names that backend and counts what it holds (W0.7). The counts are taken at most every HEALTH_COUNTS_TTL_S."""
        backend, counts = self._graph_counts()  # one read of the backend: both labels below come from it
        return {"status": "ok", "version": __version__, "neo4j": "connected" if backend == "neo4j" else "in_memory",
                "is_simulated": True,
                "graph": {"backend": backend, "nodes": counts.nodes, "relationships": counts.relationships}}

    def _graph_counts(self) -> tuple[str, GraphCounts]:
        """(backend the counts came from, counts). A Neo4j failure here falls back to the mirror like any other
        request does, so the backend is read AFTER the call."""
        cached = self._counts_cache
        if cached is not None and cached[0] == self.graph.backend and time.monotonic() - cached[1] < HEALTH_COUNTS_TTL_S:
            return cached[0], cached[2]
        counts = self.graph.call(lambda g: g.counts())
        backend = self.graph.backend
        self._counts_cache = (backend, time.monotonic(), counts)
        return backend, counts

    def close(self) -> None:
        self.graph.close()

    def _read_view(self, graph: ChargerGraph) -> GraphView:
        """Chargers and reports come out of the graph; trust and the near-route list are computed from them."""
        # The live tests plant clearly fake TEST- chargers in the shared database: planning never sees them.
        chargers = [c for c in graph.chargers() if not c.id.startswith(TEST_PREFIX)]
        known = {c.id for c in chargers}
        reports = rebase_real_reports([r for r in graph.reports() if r.charger_id in known], self.clock())
        trust = TrustEngine(chargers).estimate(reports, NOW).chargers
        return GraphView(chargers, trust, chargers_near_route(chargers, self.poly, self.poly_km, CORRIDOR_KM),
                         tuple(reports))

    def _adopt(self, view: GraphView) -> None:
        self._view = view  # one assignment: a concurrent request sees the old view or the new one, never a mix

    @property
    def chargers(self) -> list[ChargerRecord]:
        return self._view.chargers

    @property
    def trust(self) -> dict[str, ChargerTrust]:
        return self._view.trust

    @property
    def near(self) -> list[RouteCharger]:
        return self._view.near

    def _sweep_if_due(self, graph: ChargerGraph) -> None:
        if time.monotonic() - self._last_sweep >= SWEEP_EVERY_S:
            self._last_sweep = time.monotonic()
            graph.sweep_stale_legs()
            graph.sweep_plans(PLAN_KEEP, PLAN_MAX_AGE_S)
            self.graph.mirror.sweep_plans(PLAN_KEEP, PLAN_MAX_AGE_S)  # the mirror holds every plan too

    def _record_plan(self, plan_id: str, response: dict, near: Sequence[RouteCharger],
                     trust: dict[str, ChargerTrust]) -> None:
        """Write the released plan into the graph (STOPS_AT / BACKUP_FOR / AVOIDED). A failure here must never fail the
        dispatch: the plan is already computed and cached, only its audit trail would be missing."""
        try:
            rec = plan_record(plan_id, response, near, trust, ALT_AHEAD_KM, time.time())
            self.graph.mirror.add_plan(rec)
            if self.graph.backend == "neo4j":  # not ``populate``: a rejected audit write must not switch Neo4j off for everyone
                self.graph.neo4j.add_plan(rec)
        except Exception as exc:  # noqa: BLE001 - logged by class name only
            log.warning("could not record plan %s in the graph (%s)", plan_id, type(exc).__name__)

    def _graph_queries(self) -> tuple[int, float]:
        neo = self.graph.neo4j
        stats = neo.query_stats() if neo is not None else None
        return (stats.queries, stats.seconds) if stats else (0, 0.0)

    def demo_scenario(self) -> dict:
        vehicles = [{"vehicle_id": vid, "label": fv.label, "model_class": fv.ctx.class_key, "soc_pct": fv.soc_pct,
                     "usable_kwh_estimate": {"p10": round(fv.usable_p[0], 1), "p50": round(fv.usable_p[1], 1),
                                             "p90": round(fv.usable_p[2], 1)},
                     "health_note": fv.note, "trips_calibrated": fv.ctx.trips_calibrated}
                    for vid, fv in self.fleet.items()]
        return {"booking": BOOKING, "vehicles": vehicles, "is_simulated": True}

    def geocode(self, q: str, limit: int = 5, near: tuple[float, float] | None = None) -> dict:
        """India-only place search (WP1) through Photon, cached; its own request budget (engine/verity/geocode.py)."""
        try:
            normalise_query(q)
        except ValueError as exc:  # only the query check is the caller's fault
            raise ApiError(422, "invalid_request", str(exc)) from exc
        try:
            places = geocode(self.geocoder_client(), q, limit, near)
        except (NetworkUnavailable, RequestBudgetExceeded, OSError, ValueError, KeyError) as exc:
            log.warning("geocoder unavailable (%s)", type(exc).__name__)
            raise ApiError(503, "geocoder_unavailable", "Place search is unavailable right now; try again shortly.") from exc
        return {"results": [p.as_dict() for p in places], "source": "Photon (komoot), OpenStreetMap data"}

    def _vehicle_for(self, req: dict) -> PickedVehicle:
        """The demo fleet's simulated vehicle, or a real car from vehicles_india.csv (WP2); 404 lists every id."""
        vid, pax, ac = req["vehicle_id"], req["passengers"], req["ac_on"]
        fv = self.fleet.get(vid)
        if fv is not None:
            n = fv.ctx.trips_calibrated
            others = [(replace(o.ctx, passengers=pax, ac_on=ac), float(o.soc_pct))
                      for other, o in self.fleet.items() if other != vid]
            return PickedVehicle(
                replace(fv.ctx, passengers=pax, ac_on=ac), others,
                {"id": vid, "name": fv.label, "battery_kwh": round(float(fv.ctx.post.nominal_kwh), 1),
                 "trips_calibrated": n, "calibration_basis": "simulated trips", "source_count": 0, "is_simulated": True},
                {"trips": n, "basis": "simulated trips", "label": f"Calibrated on {n} simulated trips"},
                DEMO_FLEET_LABEL)
        car = self.catalog.get(vid)
        if car is None:
            raise ApiError(404, "unknown_vehicle", f"Unknown vehicle {vid!r}; known: {sorted(self.fleet) + list(self.catalog)}")
        return PickedVehicle(
            vehicle_context(car, pax, ac), [],  # a single real car has no fleet to offer as "another vehicle"
            {"id": vid, "name": car.name, "battery_kwh": car.battery_kwh, "trips_calibrated": car.trips_calibrated,
             "calibration_basis": car.calibration_basis, "source_count": car.source_count, "is_simulated": False},
            {"trips": car.trips_calibrated, "basis": car.calibration_basis, "label": car.calibration_label},
            f"vehicles_india.csv spec row ({car.source_count} cited sources, {car.source_quality}"
            f"{', approximate - verify' if car.approximate else ''}; kerb mass: {car.curb_mass_basis})")

    def vehicles(self) -> dict:
        """GET /api/vehicles (WP2): every real car, then the simulated demo fleet."""
        real = [{"vehicle_id": c.vehicle_id, "make": c.make, "model": c.model, "variant": c.variant,
                 "battery_kwh": c.battery_kwh, "claimed_range_km": c.claimed_range_km,
                 "range_test_cycle": c.range_test_cycle, "source_quality": c.source_quality,
                 "verify_note": "approximate - verify" if c.approximate else None,
                 "trips_calibrated": c.trips_calibrated, "calibration_basis": c.calibration_basis,
                 "curb_mass_basis": c.curb_mass_basis, "is_simulated": False} for c in self.catalog.values()]
        demo = [{"vehicle_id": vid, "make": "Demo fleet", "model": fv.label, "variant": fv.ctx.class_key,
                 "battery_kwh": round(float(fv.ctx.post.nominal_kwh), 1), "claimed_range_km": None,
                 "range_test_cycle": None, "source_quality": "simulated", "verify_note": None,
                 "trips_calibrated": fv.ctx.trips_calibrated, "calibration_basis": "simulated trips",
                 "curb_mass_basis": "class typical", "is_simulated": True} for vid, fv in self.fleet.items()]
        return {"vehicles": real + demo}

    def _resolve_route(self, req: dict) -> ResolvedRoute:
        try:
            return self.resolver.resolve(req["origin"], req["destination"], req["departure_time"])
        except RouteError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc

    def _route_inputs(self, view: GraphView, resolved: ResolvedRoute) -> tuple[list[RouteCharger], dict, list[Report]]:
        """(chargers near THIS route, trust, the reports on them). Off the demo route the simulated demo reports are
        left out of trust, so a plan built only from real inputs can honestly say ``is_simulated: false``."""
        if resolved.is_demo:
            near, trust, reports = view.near, view.trust, list(view.reports)
        else:
            near = chargers_near_route(view.chargers, resolved.poly, resolved.poly_km, CORRIDOR_KM)
            reports = [r for r in view.reports if not r.is_simulated]
            trust = TrustEngine(view.chargers).estimate(reports, NOW).chargers
        ids = {rc.charger.id for rc in near}
        return near, trust, [r for r in reports if r.charger_id in ids]

    def _speech_destination(self, req: dict, resolved: ResolvedRoute) -> str | None:
        name = req["destination"].get("name")
        if name:
            return name
        return BOOKING["destination"]["name"] if resolved.is_demo else None  # no name: say nothing, never guess one

    def _plan_id_for(self, key: str, req: dict) -> str:
        """The shortest ``PL-`` prefix of the request hash (4 hex characters or more) that is free or already this
        request's. After an eviction a different request can take an evicted plan's id: roughly 1 in 65 000 per new
        distinct request, and only once more than PLAN_CACHE_SIZE plans have been made (docs/DECISIONS.md W0.7)."""
        for n in range(4, 41):
            plan_id = f"PL-{key[:n]}"
            stored = self.plans.get(plan_id)
            if stored is None or stored.request == req:
                return plan_id
        raise AssertionError("a 40-character hash prefix collided with a different request")

    def dispatch(self, req: dict, *, refresh: bool = False) -> dict:
        """The plan for a request. ``refresh`` recomputes against the graph as it is now and replaces the cached plan only
        when the new one is ready (the old one keeps being served until then)."""
        req = canonical_request(DispatchRequest, req)
        picked = self._vehicle_for(req)
        resolved = self._resolve_route(req)
        key = plan_key(req)
        plan_id = self._plan_id_for(key, req)
        stored = self.plans.get(plan_id)  # one atomic read: the plan may be evicted between a check and a second read
        if stored is not None and not refresh:
            return stored.response
        ctx, others = picked.ctx, picked.others
        t0, (q0, s0) = time.perf_counter(), self._graph_queries()

        def plan_on(g: ChargerGraph) -> tuple[GraphView, tuple, object]:
            self._sweep_if_due(g)
            view = self._read_view(g)  # per dispatch, so a graph edited outside the API is picked up
            inputs = self._route_inputs(view, resolved)
            return view, inputs, dispatch(ctx, resolved.route, inputs[0], inputs[1], start_soc=req["start_soc_pct"],
                                          target_confidence=req["target_confidence"], seed=int(key[:8], 16),
                                          cfg=self.cfg, other_vehicles=others, graph=g)

        view, (near, trust, near_reports), result = self.graph.call(plan_on)
        self._adopt(view)
        q1, s1 = self._graph_queries()
        self.last_graph_timing = {"backend": self.graph.backend, "queries": max(q1 - q0, 0),
                                  "graph_s": max(s1 - s0, 0.0), "total_s": time.perf_counter() - t0}
        log.info("dispatch %s: %d graph queries, %.2f s in the graph of %.2f s total (%s)", plan_id,
                 self.last_graph_timing["queries"], self.last_graph_timing["graph_s"],
                 self.last_graph_timing["total_s"], self.graph.backend)
        destination = self._speech_destination(req, resolved)
        n_sim = sum(r.is_simulated for r in near_reports)
        sources = build_data_sources(resolved.snap, parse_departure(req["departure_time"]) or datetime.min,
                                     vehicle_simulated=picked.vehicle["is_simulated"], vehicle_source=picked.source,
                                     vehicle_note=("Spec sheet only: this car's real range is not measured yet "
                                                   "(class prior, no trip history)."
                                                   if picked.vehicle["calibration_basis"] == "class prior" else None),
                                     n_real_reports=len(near_reports) - n_sim, n_simulated_reports=n_sim)
        response = {
            "plan_id": plan_id, "vehicle_id": result.vehicle_id, "decision": result.decision,
            "confidence": result.confidence, "confidence_display": confidence_display(result.confidence),
            "reason": result.reason,
            "speech": {} if destination is None else spoken_answers(
                result.decision, destination=destination, confidence=result.confidence,
                bingo=result.bingo_line[0]["bingo_soc_pct"] if result.bingo_line else None,
                reason_facts=result.reason_facts),
            "arrival_soc": result.arrival_soc,
            "route": {"distance_km": result.distance_km, "duration_min": result.duration_min,
                      "polyline": resolved.snap["polyline"]},
            "stops": [{k: v for k, v in s.items() if k != "power_assumed"} for s in result.stops],
            "alternates": result.alternates, "bingo_line": result.bingo_line,
            "point_of_no_return_km": result.point_of_no_return_km, "breakdown": result.breakdown,
            "levers": result.levers, "risk_sources": result.risk_sources, "refusal_reason": result.refusal_reason,
            "compute_ms": result.compute_ms, "n_runs": self.cfg.n_runs, "data_sources": sources,
            "vehicle": picked.vehicle, "calibration": picked.calibration,
            "is_simulated": any_simulated(sources),
        }
        self.plans[plan_id] = StoredPlan(result.state, response, dict(req), resolved.poly, resolved.poly_km)
        self._record_plan(plan_id, response, near, trust)
        return response

    # -- did a new report move a plan? (W3.3) -----------------------------------------------------------------------
    def replan_after_report(self, charger_id: str, *, trust_changed: bool = True,
                            limit: int = REPLAN_LIMIT) -> tuple[list[dict], int]:
        """(changes, pending): re-plan the newest cached plans that use ``charger_id`` (stop or backup) against the graph as it
        is now, and say for each whether the answer moved since it was made. ``pending`` counts the plans that use the charger
        but were not re-checked (over ``limit``, the shown trust did not change, or another re-plan was running): they keep
        their old answer until asked for again. The old plan is served until its replacement is ready. Same request, same seed,
        but the clock and other reports have moved too, so a change is "since it was made", not "caused by this report"."""
        uses = [(pid, st) for pid, st in reversed(self.plans.snapshot()) if charger_id in plan_charger_ids(st.response)]
        if not uses:
            return [], 0
        if not trust_changed or not self._replan_slot.acquire(blocking=False):
            return [], len(uses)
        try:
            out = [plan_change(pid, st.response, self.dispatch(st.request, refresh=True)) for pid, st in uses[:limit]]
        finally:
            self._replan_slot.release()
        return out, len(uses) - len(out)

    # -- ride check (F8b) --------------------------------------------------------------------------------------------
    def _check_area(self, *points: tuple[float, float]) -> None:
        lats, lons = [c.lat for c in self.chargers], [c.lon for c in self.chargers]
        for lat, lon in points:
            if not (min(lats) - AREA_MARGIN_DEG <= lat <= max(lats) + AREA_MARGIN_DEG
                    and min(lons) - AREA_MARGIN_DEG <= lon <= max(lons) + AREA_MARGIN_DEG):
                raise ApiError(422, "unsupported_area", "This prototype checks rides inside Delhi NCR and the Noida-Agra "
                               "corridor only (the area of its charger directory).")

    def _ride_bundle(self, req: dict, cur: tuple, pick: tuple, drop: tuple, view: GraphView) -> dict:
        bundle = match_bundle(self.ride_bundles, cur, pick, drop)
        if bundle is not None:
            return bundle
        key = tuple(round(v, 4) for p in (cur, pick, drop) for v in p)
        cached = self._ride_cache.get(key)
        if cached is not None:
            return cached
        departure = datetime.fromisoformat(req["departure_time"]) if req.get("departure_time") else datetime.now(
            timezone.utc)
        bundle = fetch_bundle(departure, cur, pick, drop, candidates_near_drop(view.chargers, drop))
        self._ride_cache[key] = bundle
        return bundle

    def ride_check(self, req: dict) -> dict:
        """GO / NO_GO / INSUFFICIENT_EVIDENCE for a ride plus the after-drop plan to a trusted charger (F8b)."""
        req = canonical_request(RideCheckRequest, req)
        fv = self.fleet.get(req["vehicle_id"])
        if fv is None:
            raise ApiError(404, "unknown_vehicle", f"Unknown vehicle {req['vehicle_id']!r}; known: {sorted(self.fleet)}")
        cur, pick, drop = ((req[k]["lat"], req[k]["lon"]) for k in ("current", "pickup", "drop"))
        self._check_area(cur, pick, drop)
        key = hashlib.sha1(json.dumps(req, sort_keys=True, default=str).encode()).hexdigest()
        view = self.graph.call(self._read_view)
        self._adopt(view)
        bundle = self._ride_bundle(req, cur, pick, drop, view)
        signals = report_signals(view.reports, NOW)
        opts, notes = options_from_bundle(bundle, view.chargers, view.trust, signals)
        rate = req.get("earning_rs_per_hour")
        is_example = rate is None
        rate = EXAMPLE_EARNING_RS_PER_HOUR if is_example else float(rate)
        ctx = replace(fv.ctx, passengers=req["passengers"], ac_on=req["ac_on"])
        inp = RideInput(ctx, snapshot_to_route(bundle), float(bundle["pickup_km"]), float(req["soc_pct"]),
                        float(req["target_confidence"]), rate, tuple(opts))
        result = ride_check(inp, seed=int(key[:8], 16), cfg=self.cfg)
        ids = {o.node.id for o in opts}
        near = [r for r in view.reports if r.charger_id in ids]
        n_sim = sum(r.is_simulated for r in near)
        answer = {"vehicle_id": req["vehicle_id"], **result, "n_runs": self.cfg.n_runs,
                "route": {"polyline": thin_polyline(bundle["polyline"]), "source": bundle["source"]},
                "earning_rate": {"rs_per_hour": rate, "is_example": is_example,
                                 "source": EARNING_RATE_SOURCE if is_example else "Entered by the driver"},
                "data_notes": data_notes(bundle, opts, signals, len(near) - n_sim, n_sim) + notes,
                "is_simulated": True}
        # The sentence to speak per language (W3.2): a fixed template filled with this answer's numbers, or none.
        answer["speech"] = {lang: text for lang in SPEECH_LANGUAGES if (text := spoken_ride_answer(answer, lang))}
        return answer

    def driver_demo(self) -> dict:
        """The second demo's request and what is pinned (F10); the decision itself comes from POST /api/ride-check."""
        names = {c.id: c.name for c in self.chargers}
        return {"story": DRIVER_STORY, "request": DRIVER_DEMO,
                "pinned_chargers": [{"charger_id": p.charger_id, "name": names.get(p.charger_id, p.charger_id),
                                     "role": p.role, "simulated_reports": len(p.reports)} for p in DRIVER_PINS],
                "is_simulated": True}

    def _plan(self, plan_id: str) -> StoredPlan:
        stored = self.plans.get(plan_id)
        if stored is None:
            raise ApiError(404, "unknown_plan", f"No plan {plan_id!r}; call POST /api/dispatch first")
        return stored

    def forecast_update(self, req: dict) -> dict:
        sp = self._plan(req["plan_id"])
        poly = self.poly if sp.poly is None else sp.poly  # each plan locates the driver on its own route (WP1)
        poly_km = self.poly_km if sp.poly_km is None else sp.poly_km
        d = haversine_m(np.full(len(poly), req["lat"]), np.full(len(poly), req["lon"]), poly[:, 0], poly[:, 1])
        i = int(np.argmin(d))
        if d[i] > 15_000.0:
            raise ApiError(422, "off_route", "The position is more than 15 km from the planned route.")
        upd = live_update_plan(sp.state, float(poly_km[i]), float(req["soc_pct"]))
        return {"plan_id": req["plan_id"], **upd, "n_runs": sp.state.ctx.runs.n_runs, "is_simulated": True}

    def chargers_in_bbox(self, min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> dict:
        out = []
        view = self._view  # read once: a dispatch may swap the view while this request is running
        for c in view.chargers:
            if not (min_lat <= c.lat <= max_lat and min_lon <= c.lon <= max_lon):
                continue
            t: ChargerTrust = view.trust[c.id]
            out.append({
                "charger_id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon, "operator": c.operator,
                "site_type": c.site_type, "connectors": [{"type": k.type, "kw": k.kw} for k in c.connectors],
                "p_works": round_prob(t.p_works), "trust_level": t.trust_level, "n_reports": t.n_reports,
                "days_since_last": None if t.days_since_last is None else int(round(t.days_since_last)),
                "source": source_of(c)})
        return {"chargers": out, "is_simulated": True}

    def replay(self, plan_id: str) -> dict:
        """A scripted trip for the driver view: a trip that drains faster than planned (85th-percentile energy
        run) and a trusted source reporting the first planned charger down. SoC readings come from that run
        of the engine's own Monte Carlo; the forecast event is the engine's live update."""
        sp = self._plan(plan_id)
        state = sp.state
        ctx, runs = state.ctx, state.ctx.runs
        idx = int(np.argsort(runs.total_used)[int(0.85 * runs.n_runs)])
        trace = state.ev.trace[idx]

        def soc_at(km: float) -> int:
            if km <= 0:
                return int(round(ctx.start_soc))
            return int(np.clip(round(float(trace[min(int(np.searchsorted(runs.km_end, km)), len(trace) - 1)])), 0, 100))

        first = state.stops[0] if state.stops else None
        cutoff = max(first.node.km - 12.0, 1.0) if first else float(runs.km_end[-1])
        kms = [0.0, *[float(k) for k in np.arange(25.0, cutoff, 25.0)], float(cutoff)]
        events: list[dict] = [{"t_offset_s": int(round(ctx.minutes_at(k) * 60)), "type": "soc_reading", "km": round(k, 1),
                               "soc_pct": soc_at(k)} for k in kms]
        t_end, soc_end = events[-1]["t_offset_s"], events[-1]["soc_pct"]
        broken: set[str] = set()
        if first is not None:
            broken = {first.node.id}
            events.append({"t_offset_s": t_end + 10, "type": "charger_report", "charger_id": first.node.id,
                           "status": "broken", "source_trust": "high"})  # scripted: a trusted source reports it down
        upd = live_update_plan(state, kms[-1], float(soc_end), broken=broken)
        events.append({"t_offset_s": t_end + 20, "type": "forecast", "action": upd["action"], "message": upd["message"],
                       "divert_to": upd["divert_to"]})
        return {"plan_id": plan_id, "events": events, "is_simulated": True}

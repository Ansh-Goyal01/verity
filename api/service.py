"""Engine service behind the API: demo fleet, real route and chargers, trust, stored plans, replay.

What is real and what is simulated (every response says ``is_simulated: true``):
* REAL: the Noida-Agra route, elevation and weather forecast (data/processed/route_noida_agra.json) and
  the OpenStreetMap charger directory (data/processed/chargers_ncr.json).
* SIMULATED: the three demo vehicles and their trips (data/sim/demo, calibrated through the real
  pipeline), and the charger REPORTS (demo inputs below). The reports are scenario inputs chosen to show
  a well-evidenced charger, a thinly evidenced one, a stale one and an unreported one; every decision,
  probability, plan and message that comes out is computed by the engine from them.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import timedelta

import numpy as np

from verity import __version__
from verity.calibrate import calibrate
from verity.chargers import chargers_near_route, load_chargers, source_of
from verity.dispatch import PlanState, VehicleContext, dispatch, live_update_plan
from verity.forecast import ForecastConfig, round_prob
from verity.paths import SIM_DIR
from verity.routing import load_planned_route
from verity.schemas import ChargerRecord, Report
from verity.sim import generate_dataset, load_manifest
from verity.trips import haversine_m, load_sim_observation
from verity.trust.engine import ChargerTrust, TrustEngine
from verity.trust.graph import Neo4jGraph
from verity.trust.reports_sim import NOW
from verity.vehicles import get_prior

DEMO_SEED = 1028
DEMO_FLEET = (("V07", "compact_esuv", 0.78), ("V12", "compact_esuv", 0.98), ("V19", "compact_esuv", 0.95))
# (label, start SoC %, number of trips used for calibration). Scenario inputs, not results.
FLEET_META = {"V07": ("Vehicle 7", 82, 6), "V12": ("Vehicle 12", 62, 8), "V19": ("Vehicle 19", 40, 4)}
BOOKING = {"booking_id": "BK-001", "origin": {"name": "Noida", "lat": 28.5355, "lon": 77.3910},
           "destination": {"name": "Agra", "lat": 27.1767, "lon": 78.0081},
           "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}
CORRIDOR_KM = 3.0  # chargers within this distance of the route are planning candidates
ENDPOINT_TOLERANCE_KM = 30.0  # the demo route only serves requests that start/end near Noida and Agra
# Demo report scenarios by position along the route: (km from, km to, [(report ages in days, status)]).
DEMO_REPORTS = (
    (120.0, 130.0, "well evidenced", [(a, "working") for a in (1, 2, 3, 4, 6, 8, 10, 12, 15)] + [(5, "broken")]),
    (170.0, 180.0, "thinly evidenced", [(12, "working"), (21, "working"), (30, "working")]),
    (30.0, 40.0, "stale", [(41, "working")]),
    (15.0, 25.0, "no reports", []),
)


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


@dataclass
class StoredPlan:
    state: PlanState
    response: dict
    request: dict


class EngineService:
    def __init__(self, n_runs: int | None = None) -> None:
        self.cfg = ForecastConfig(n_runs=n_runs or int(os.environ.get("VERITY_N_RUNS", "1500")))
        self.route, self.snap = load_planned_route()
        self.poly = np.array(self.snap["polyline"])
        self.poly_km = np.array(self.snap["polyline_km"])
        self.chargers, self.directory_meta = load_chargers()
        self.near = chargers_near_route(self.chargers, self.poly, self.poly_km, CORRIDOR_KM)
        self.demo_reports = self._demo_reports()
        self.trust = TrustEngine(self.chargers).estimate(self.demo_reports, NOW).chargers
        self.fleet = self._build_fleet()
        self.plans: dict[str, StoredPlan] = {}
        self._neo4j: str | None = None

    # -- setup ----------------------------------------------------------------------------------------------
    def _demo_reports(self) -> list[Report]:
        out: list[Report] = []
        for lo, hi, _, reports in DEMO_REPORTS:
            target = next((rc for rc in self.near if lo <= rc.km_along <= hi), None)
            if target is None:
                continue
            for i, (age, status) in enumerate(reports):
                out.append(Report(charger_id=target.charger.id, source_id=f"demo-user-{i}", status=status,
                                  t=NOW - timedelta(days=age, hours=2 * i), evidence="SIMULATED demo report"))
        return out

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
        if self._neo4j is None:
            self._neo4j = "in_memory"
            graph = Neo4jGraph.from_env()
            if graph is not None:
                try:
                    graph._driver.verify_connectivity()
                    self._neo4j = "connected"
                except Exception:  # noqa: BLE001 - any failure means we stay in memory
                    pass
                finally:
                    graph.close()
        return {"status": "ok", "version": __version__, "neo4j": self._neo4j, "is_simulated": True}

    def demo_scenario(self) -> dict:
        vehicles = [{"vehicle_id": vid, "label": fv.label, "model_class": fv.ctx.class_key, "soc_pct": fv.soc_pct,
                     "usable_kwh_estimate": {"p10": round(fv.usable_p[0], 1), "p50": round(fv.usable_p[1], 1),
                                             "p90": round(fv.usable_p[2], 1)},
                     "health_note": fv.note, "trips_calibrated": fv.ctx.trips_calibrated}
                    for vid, fv in self.fleet.items()]
        return {"booking": BOOKING, "vehicles": vehicles, "is_simulated": True}

    def _check_endpoints(self, origin: dict, destination: dict) -> None:
        def far(p: dict, ref: dict) -> bool:
            d = haversine_m(np.array([p["lat"]]), np.array([p["lon"]]), np.array([ref["lat"]]), np.array([ref["lon"]]))[0]
            return d / 1000.0 > ENDPOINT_TOLERANCE_KM
        if far(origin, BOOKING["origin"]) or far(destination, BOOKING["destination"]):
            raise ApiError(422, "unsupported_route", "This prototype serves the cached Noida to Agra route only "
                           f"(origin and destination must be within {ENDPOINT_TOLERANCE_KM:.0f} km of them).")

    def dispatch(self, req: dict) -> dict:
        fv = self.fleet.get(req["vehicle_id"])
        if fv is None:
            raise ApiError(404, "unknown_vehicle", f"Unknown vehicle {req['vehicle_id']!r}; known: {sorted(self.fleet)}")
        self._check_endpoints(req["origin"], req["destination"])
        key = hashlib.sha1(json.dumps({k: req[k] for k in sorted(req)}, sort_keys=True).encode()).hexdigest()
        plan_id = next(f"PL-{key[:n]}" for n in range(4, 41) if f"PL-{key[:n]}" not in self.plans
                       or self.plans[f"PL-{key[:n]}"].request == req)
        if plan_id in self.plans:
            return self.plans[plan_id].response
        ctx = replace(fv.ctx, passengers=req["passengers"], ac_on=req["ac_on"])
        others = [(replace(o.ctx, passengers=req["passengers"], ac_on=req["ac_on"]), float(o.soc_pct))
                  for vid, o in self.fleet.items() if vid != req["vehicle_id"]]
        result = dispatch(ctx, self.route, self.near, self.trust, start_soc=req["start_soc_pct"],
                          target_confidence=req["target_confidence"], seed=int(key[:8], 16), cfg=self.cfg,
                          other_vehicles=others)
        response = {
            "plan_id": plan_id, "vehicle_id": result.vehicle_id, "decision": result.decision,
            "confidence": result.confidence, "arrival_soc": result.arrival_soc,
            "route": {"distance_km": result.distance_km, "duration_min": result.duration_min,
                      "polyline": self.snap["polyline"]},
            "stops": [{k: v for k, v in s.items() if k != "power_assumed"} for s in result.stops],
            "alternates": result.alternates, "bingo_line": result.bingo_line,
            "point_of_no_return_km": result.point_of_no_return_km, "breakdown": result.breakdown,
            "levers": result.levers, "risk_sources": result.risk_sources, "refusal_reason": result.refusal_reason,
            "compute_ms": result.compute_ms, "is_simulated": True,
        }
        self.plans[plan_id] = StoredPlan(result.state, response, dict(req))
        return response

    def _plan(self, plan_id: str) -> StoredPlan:
        if plan_id not in self.plans:
            raise ApiError(404, "unknown_plan", f"No plan {plan_id!r}; call POST /api/dispatch first")
        return self.plans[plan_id]

    def forecast_update(self, req: dict) -> dict:
        sp = self._plan(req["plan_id"])
        d = haversine_m(np.full(len(self.poly), req["lat"]), np.full(len(self.poly), req["lon"]),
                        self.poly[:, 0], self.poly[:, 1])
        i = int(np.argmin(d))
        if d[i] > 15_000.0:
            raise ApiError(422, "off_route", "The position is more than 15 km from the planned route.")
        upd = live_update_plan(sp.state, float(self.poly_km[i]), float(req["soc_pct"]))
        return {"plan_id": req["plan_id"], **upd, "is_simulated": True}

    def chargers_in_bbox(self, min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> dict:
        out = []
        for c in self.chargers:
            if not (min_lat <= c.lat <= max_lat and min_lon <= c.lon <= max_lon):
                continue
            t: ChargerTrust = self.trust[c.id]
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

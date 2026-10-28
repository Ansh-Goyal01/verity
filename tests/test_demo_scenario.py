"""The demo scenario's three outcomes, on the COMMITTED charger directory and the real engine.

V07 (aged battery) -> NO_GO, V12 -> GO and its replay ends in a DIVERT that still gets the trip home,
V19 -> INSUFFICIENT_EVIDENCE. The simulated reports are pinned to named chargers (api/service.py DEMO_REPORTS).
If a change to data/processed/chargers_ncr.json breaks one of these, the demo story is broken: re-pin the
scenario and record it in docs/DECISIONS.md. Do not loosen these assertions or change the engine to pass them.
"""

from __future__ import annotations

import numpy as np
import pytest

from api.service import DEMO_REPORTS, PIN_KM_TOLERANCE, EngineService, ScenarioPin, demo_reports_for
from verity.chargers import RouteCharger
from verity.dispatch import execute_trip
from verity.schemas import ChargerRecord

BOOKING = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
           "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}


@pytest.fixture(scope="module")
def svc():
    service = EngineService(graph_backend="memory")  # default run count, like the API
    yield service
    service.close()


@pytest.fixture(scope="module")
def plans(svc: EngineService) -> dict[str, dict]:
    return {vid: svc.dispatch({**BOOKING, "vehicle_id": vid, "start_soc_pct": fv.soc_pct}) for vid, fv in svc.fleet.items()}


# --- the pins ---------------------------------------------------------------------------------------------------


def test_every_pin_resolves_to_the_charger_it_was_chosen_for(svc: EngineService) -> None:
    near = {rc.charger.id: rc for rc in svc.near}
    for pin in DEMO_REPORTS:
        assert pin.charger_id in near, pin
        assert pin.source_id in near[pin.charger_id].charger.source_ids, pin
        assert abs(near[pin.charger_id].km_along - pin.km) <= PIN_KM_TOLERANCE, pin


def test_reports_sit_on_the_pinned_chargers_and_nowhere_else(svc: EngineService) -> None:
    assert {r.charger_id for r in svc.demo_reports} == {p.charger_id for p in DEMO_REPORTS if p.reports}
    assert len(svc.demo_reports) == sum(len(p.reports) for p in DEMO_REPORTS)
    assert all(r.is_simulated for r in svc.demo_reports)


def charger(cid: str, source_id: str) -> RouteCharger:
    return RouteCharger(ChargerRecord(id=cid, name=cid, lat=0.0, lon=0.0, source_ids=[source_id]), km_along=100.0, offset_km=0.1)


PIN = ScenarioPin("CH-0001", "osm:node/1", 100.0, "test", ((1, "working"), (2, "broken")))


def test_a_pin_attaches_its_reports_to_its_charger() -> None:
    reports = demo_reports_for([charger("CH-0001", "osm:node/1")], [PIN])
    assert [(r.charger_id, r.status, r.is_simulated) for r in reports] == [("CH-0001", "working", True), ("CH-0001", "broken", True)]


@pytest.mark.parametrize(("near", "message"), [
    ([], "no such charger"),  # the charger left the corridor: the old km-band code silently dropped the reports
    ([charger("CH-0001", "osm:node/999")], "different charger"),  # the directory was renumbered
    ([RouteCharger(charger("CH-0001", "osm:node/1").charger, km_along=140.0, offset_km=0.1)], "km 140"),  # it moved
])
def test_a_pin_that_no_longer_matches_the_directory_stops_the_service(near: list[RouteCharger], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        demo_reports_for(near, [PIN])


# --- the three outcomes ---------------------------------------------------------------------------------------------


def test_v07_aged_battery_is_no_go_with_a_confidence_below_target(plans: dict) -> None:
    d = plans["V07"]
    assert d["decision"] == "NO_GO" and d["confidence"] < 0.9 and d["refusal_reason"] is None


def test_v12_is_go_at_the_target_confidence(plans: dict) -> None:
    d = plans["V12"]
    assert d["decision"] == "GO" and d["confidence"] >= 0.9 and d["refusal_reason"] is None


def test_v19_refuses_because_its_plan_rests_on_chargers_without_current_evidence(plans: dict) -> None:
    d = plans["V19"]
    assert d["decision"] == "INSUFFICIENT_EVIDENCE" and d["confidence"] >= 0.9
    assert "stale or missing" in d["refusal_reason"] and "verify" in d["refusal_reason"].lower()


# --- the GO vehicle's replay --------------------------------------------------------------------------------------


def test_the_go_vehicles_replay_ends_in_a_divert_and_the_trip_still_succeeds(svc: EngineService, plans: dict) -> None:
    plan = plans["V12"]
    events = svc.replay(plan["plan_id"])["events"]
    planned = plan["stops"][0]["charger_id"]
    reported = next(e for e in events if e["type"] == "charger_report")
    last = events[-1]
    assert (reported["charger_id"], reported["status"]) == (planned, "broken")
    assert last["type"] == "forecast" and last["action"] == "DIVERT"

    # the alternate it diverts to is one the plan already listed as reachable, and is not the broken charger
    divert = last["divert_to"]["charger_id"]
    reachable = {a["charger_id"]: a["p_reach"] for a in plan["alternates"] if a["for_stop_index"] == 0}
    assert divert != planned and reachable.get(divert, 0.0) >= 0.5

    # drive the replay's own drained run (the same 85th-percentile run it scripts) with the planned charger down
    state = svc.plans[plan["plan_id"]].state
    ctx, runs = state.ctx, state.ctx.runs
    idx = int(np.argsort(runs.total_used)[int(0.85 * runs.n_runs)])
    works = {n.id: True for n in state.nodes} | {planned: False}
    outcome = execute_trip(state.stops, lambda km: float(runs.used_at_km(km)[idx]), ctx.start_soc, works,
                           ctx.minutes_at(ctx.total_km), ctx.min_per_pp, ctx.total_km, use_alts=True)
    assert not outcome.stranded and outcome.stops_used >= 1

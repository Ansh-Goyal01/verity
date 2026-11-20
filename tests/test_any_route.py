"""WP1 any-route planning, offline: the Connaught Place -> Jaipur responses were recorded once, live, on 2026-11-20
(coordinates from Photon's answer for "connaught place" and "jaipur"), and are replayed here through CachedClient's
own disk cache. A transport that raises makes sure no test ever reaches the network."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
import pytest

from api.data_sources import any_simulated, build_data_sources
from api.route_resolver import RouteError, RouteResolver
from api.service import ApiError, EngineService, demo_dispatch_request, plan_key
from verity.routing import CachedClient

FIXTURES = Path(__file__).parent / "fixtures" / "route_cp_jaipur"
CP = {"lat": 28.63177, "lon": 77.21938, "name": "Connaught Place, New Delhi"}  # Photon's first result, 2026-11-20
JAIPUR = {"lat": 26.915458, "lon": 75.818982, "name": "Jaipur"}  # Photon's first result, 2026-11-20
DEPARTURE = "2026-11-21T08:00:00+05:30"
ROUTER_KM = 298.42  # the recorded OSRM route's length
NOWHERE = {"origin": {"lat": 0.0, "lon": 0.0}, "destination": {"lat": 0.0, "lon": 0.0}}  # a booking nothing is near


class Offline:
    """An httpx transport handler that refuses every request and counts the attempts."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise httpx.ConnectError("offline test", request=request)


def replay_factory(cache_dir: Path, transport: Offline):
    return lambda: CachedClient(cache_dir=cache_dir, max_requests=1000, retries=0, sleep=lambda _s: None,
                                client=httpx.Client(transport=httpx.MockTransport(transport)))


def trip(**extra) -> dict:
    return {"vehicle_id": "V12", "origin": CP, "destination": JAIPUR, "departure_time": DEPARTURE,
            "start_soc_pct": 90, "passengers": 1, "ac_on": True, "target_confidence": 0.9, **extra}


@pytest.fixture(scope="module")
def offline() -> Offline:
    return Offline()


@pytest.fixture(scope="module")
def svc(tmp_path_factory: pytest.TempPathFactory, offline: Offline) -> EngineService:
    cache = tmp_path_factory.mktemp("route_cache")
    for f in FIXTURES.glob("*.json"):
        shutil.copy2(f, cache / f.name)
    return EngineService(n_runs=300, graph_backend="memory", route_client=replay_factory(cache, offline))


@pytest.fixture(scope="module")
def jaipur(svc: EngineService) -> dict:
    return svc.dispatch(trip())


def test_a_new_route_plans_on_the_real_recorded_route(jaipur: dict, offline: Offline) -> None:
    assert offline.calls == 0  # everything came from the recorded responses
    poly = np.array(jaipur["route"]["polyline"])
    assert len(poly) > 100
    assert poly[0] == pytest.approx([CP["lat"], CP["lon"]], abs=0.01)
    assert poly[-1] == pytest.approx([JAIPUR["lat"], JAIPUR["lon"]], abs=0.01)
    assert abs(jaipur["route"]["distance_km"] - ROUTER_KM) / ROUTER_KM < 0.10
    assert jaipur["decision"] in {"GO", "NO_GO", "INSUFFICIENT_EVIDENCE"}


def test_data_sources_say_what_is_real_and_is_simulated_follows_them(jaipur: dict) -> None:
    ds = jaipur["data_sources"]
    assert {k: v["status"] for k, v in ds.items()} == {
        "route": "real", "elevation": "real", "weather": "real", "vehicle": "simulated", "charger_reports": "real"}
    assert ds["weather"]["note"] is None  # the recording holds the 08:00 departure hour
    assert jaipur["is_simulated"] is True  # only because the demo fleet vehicle is simulated (real cars arrive in WP2)


def test_the_destination_name_is_spoken_when_the_template_names_a_place(jaipur: dict) -> None:
    assert jaipur["speech"], "a named destination must be spoken"
    if jaipur["decision"] != "INSUFFICIENT_EVIDENCE":  # that template names no place
        assert "Jaipur" in jaipur["speech"]["en-IN"]
        assert "Jaipur" in jaipur["speech"]["hi-IN"]


def test_without_a_destination_name_nothing_is_spoken_rather_than_guessing(svc: EngineService) -> None:
    unnamed = svc.dispatch(trip(destination={"lat": JAIPUR["lat"], "lon": JAIPUR["lon"]}))
    assert unnamed["speech"] == {}


def test_simulated_reports_never_inform_trust_off_the_demo_route(svc: EngineService) -> None:
    resolved = svc.resolver.resolve(CP, JAIPUR, DEPARTURE)
    view = svc._view
    assert any(r.is_simulated for r in view.reports)  # the demo's simulated reports are in the graph
    near, _trust, reports = svc._route_inputs(view, resolved)
    assert near and not any(r.is_simulated for r in reports)


def test_forecast_update_works_on_a_new_route_plan(svc: EngineService, jaipur: dict) -> None:
    lat, lon = jaipur["route"]["polyline"][len(jaipur["route"]["polyline"]) // 3]
    upd = svc.forecast_update({"plan_id": jaipur["plan_id"], "t": DEPARTURE, "lat": lat, "lon": lon, "soc_pct": 60})
    assert upd["plan_id"] == jaipur["plan_id"]


def test_with_the_network_off_a_new_route_is_refused_never_planned_on_fake_geometry(
        tmp_path: Path, svc: EngineService) -> None:
    dead = Offline()
    resolver = RouteResolver(svc.route, svc.snap, NOWHERE, replay_factory(tmp_path, dead))
    with pytest.raises(RouteError) as err:
        resolver.resolve(CP, JAIPUR, DEPARTURE)
    assert (err.value.status, err.value.code) == (503, "route_unavailable")
    assert dead.calls >= 1


def test_a_trip_over_the_limit_is_refused_before_any_network_call(svc: EngineService, offline: Offline) -> None:
    before = offline.calls
    with pytest.raises(ApiError) as err:
        svc.dispatch(trip(destination={"lat": 12.97, "lon": 77.59}))
    assert (err.value.status, err.value.code) == (422, "route_too_long")
    assert offline.calls == before


def test_a_bad_departure_time_off_the_demo_route_is_a_422(svc: EngineService) -> None:
    with pytest.raises(ApiError) as err:
        svc.dispatch(trip(departure_time="tomorrow morning"))
    assert (err.value.status, err.value.code) == (422, "invalid_request")


def test_an_absent_name_leaves_the_plan_id_exactly_as_before_names_existed() -> None:
    before = demo_dispatch_request("V07")
    old = hashlib.sha1(json.dumps({k: before[k] for k in sorted(before)}, sort_keys=True).encode()).hexdigest()
    req = {**before, "origin": {**before["origin"], "name": None},
           "destination": {**before["destination"], "name": None}}
    assert plan_key(req) == old
    assert plan_key({**req, "destination": {**req["destination"], "name": "Agra"}}) != old


def test_is_simulated_is_false_only_when_every_input_is_real() -> None:
    snap = {"is_simulated": False, "source": "osrm", "elevation_source": "open-meteo",
            "weather_points": [{"hour": "2026-11-21T08:00"}]}
    dep = datetime.fromisoformat(DEPARTURE)
    base = {"vehicle_simulated": False, "vehicle_source": "a real car's spec row", "n_real_reports": 3,
            "n_simulated_reports": 0}
    assert any_simulated(build_data_sources(snap, dep, **base)) is False
    for change in ({"vehicle_simulated": True}, {"n_simulated_reports": 1}):
        assert any_simulated(build_data_sources(snap, dep, **{**base, **change})) is True
    assert any_simulated(build_data_sources({**snap, "is_simulated": True}, dep, **base)) is True


# --- review fixes (S-WP1 code review) -------------------------------------------------------------------------------


def test_the_forecast_hour_is_the_ist_hour_whatever_offset_the_request_uses() -> None:
    from verity.routing import ist_hour

    assert ist_hour(datetime.fromisoformat("2026-11-21T02:30:00+00:00")) == "2026-11-21T08:00"
    assert ist_hour(datetime.fromisoformat("2026-11-21T08:10:00+05:30")) == "2026-11-21T08:00"
    assert ist_hour(datetime.fromisoformat("2026-11-21T08:10:00")) == "2026-11-21T08:00"  # naive = IST


def test_origin_and_destination_too_close_is_a_422_without_network(svc: EngineService, offline: Offline) -> None:
    before = offline.calls
    with pytest.raises(ApiError) as err:
        svc.dispatch(trip(destination={**CP, "lat": CP["lat"] + 0.001}))
    assert (err.value.status, err.value.code) == (422, "route_too_short")
    assert offline.calls == before


def test_new_routes_are_capped_but_cached_routes_are_free(tmp_path: Path, svc: EngineService) -> None:
    for f in FIXTURES.glob("*.json"):
        shutil.copy2(f, tmp_path / f.name)
    dead = Offline()
    resolver = RouteResolver(svc.route, svc.snap, NOWHERE, replay_factory(tmp_path, dead), new_routes_per_hour=1)
    first = resolver.resolve(CP, JAIPUR, DEPARTURE)
    assert resolver.resolve({**CP, "name": "CP"}, JAIPUR, DEPARTURE) is first  # names are not part of the route
    with pytest.raises(RouteError) as err:
        resolver.resolve(CP, {**JAIPUR, "lat": 26.8}, DEPARTURE)
    assert (err.value.status, err.value.code) == (429, "rate_limited")
    assert dead.calls == 0


def test_concurrent_cache_misses_fetch_once_and_count_once(tmp_path: Path) -> None:
    import threading

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"ok": True})

    def worker() -> None:
        CachedClient(cache_dir=tmp_path, max_requests=50,
                     client=httpx.Client(transport=httpx.MockTransport(handler))).get_json("https://example.test/x")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert CachedClient(cache_dir=tmp_path).requests_made() == 1

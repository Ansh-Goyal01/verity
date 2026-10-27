"""Routing cache/budget/back-off (offline, via httpx.MockTransport), segment building and charger parsing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest

from verity.chargers import (
    CHARGERS_FILE,
    assign_ids,
    chargers_near_route,
    load_chargers,
    merge_duplicates,
    parse_ocm_poi,
    parse_osm_element,
    source_of,
)
from verity.routing import (
    ROUTE_FILE,
    CachedClient,
    NetworkUnavailable,
    RequestBudgetExceeded,
    build_route_snapshot,
    fetch_route,
    load_planned_route,
    plan_segments,
    synthetic_route,
)
from verity.schemas import ChargerRecord

IST = timezone(timedelta(hours=5, minutes=30))


def make_client(tmp_path: Path, handler, **kw) -> tuple[CachedClient, list[float]]:
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CachedClient(tmp_path, client=client, sleep=sleeps.append, **kw), sleeps


def ok_handler(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "url": str(request.url)})
    return handler


# --- cache, budget, back-off -------------------------------------------------------------------------


def test_second_identical_request_is_served_from_disk_without_a_network_call(tmp_path: Path) -> None:
    calls: list = []
    client, _ = make_client(tmp_path, ok_handler(calls))
    first, cached1 = client.get_json("https://example.test/a", {"x": 1})
    second, cached2 = client.get_json("https://example.test/a", {"x": 1})
    assert first == second and (cached1, cached2) == (False, True) and len(calls) == 1
    client.get_json("https://example.test/a", {"x": 2})  # different params -> a real request
    assert len(calls) == 2 and client.requests_made() == 2


def test_request_budget_is_a_hard_limit_that_survives_a_new_client(tmp_path: Path) -> None:
    calls: list = []
    client, _ = make_client(tmp_path, ok_handler(calls), max_requests=2)
    client.get_json("https://example.test/1"); client.get_json("https://example.test/2")
    fresh, _ = make_client(tmp_path, ok_handler(calls), max_requests=2)  # counter is persisted on disk
    with pytest.raises(RequestBudgetExceeded):
        fresh.get_json("https://example.test/3")
    assert fresh.get_json("https://example.test/1")[1] is True  # cached responses stay free


def test_transient_errors_back_off_exponentially_then_succeed(tmp_path: Path) -> None:
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        return httpx.Response(code, json={"ok": code == 200})

    client, sleeps = make_client(tmp_path, handler)
    payload, _ = client.get_json("https://example.test/flaky")
    assert payload == {"ok": True} and sleeps == [1.0, 2.0] and client.requests_made() == 3


def test_client_errors_are_not_retried_and_dead_networks_raise(tmp_path: Path) -> None:
    calls: list = []

    def not_found(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, json={})

    client, sleeps = make_client(tmp_path, not_found)
    with pytest.raises(NetworkUnavailable):
        client.get_json("https://example.test/missing")
    assert len(calls) == 1 and sleeps == []

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    dead, dead_sleeps = make_client(tmp_path / "dead", down, retries=2)
    with pytest.raises(NetworkUnavailable):
        dead.get_json("https://example.test/x")
    assert dead_sleeps == [1.0, 2.0]


def test_api_keys_in_headers_are_never_written_to_the_cache(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, ok_handler([]))
    client.get_json("https://example.test/keyed", headers={"X-API-Key": "SECRET-VALUE-123"})
    assert all("SECRET-VALUE-123" not in p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))


# --- route, fallback, segments ---------------------------------------------------------------------------


OSRM = {"code": "Ok", "routes": [{"duration": 600, "geometry": {"coordinates": [[77.39, 28.53], [77.40, 28.54], [77.41, 28.55]]},
                                  "legs": [{"annotation": {"distance": [1500.0, 1500.0], "speed": [25.0, 8.0]}}]}]}


def test_fetch_route_parses_geometry_distance_and_speeds(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, lambda req: httpx.Response(200, json=OSRM))
    route = fetch_route(client)
    assert route.source == "osrm" and route.coords.shape == (3, 2) and route.distance_km == pytest.approx(3.0)
    assert list(route.edge_speed_mps) == [25.0, 8.0]


def test_network_failure_falls_back_to_a_clearly_labelled_synthetic_route(tmp_path: Path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client, _ = make_client(tmp_path, down)
    snap = build_route_snapshot(client, datetime(2026, 10, 28, 14, tzinfo=IST))
    assert snap["is_simulated"] is True and snap["source"] == "synthetic_fallback"
    assert "SYNTHETIC" in snap["elevation_source"] and snap["distance_km"] > 100


def test_plan_segments_cuts_one_km_segments_with_class_grade_and_weather() -> None:
    route = synthetic_route()
    weather = [{"temperature_c": 30.0, "wind_speed_mps": 4.0, "wind_from_deg": 0.0}] * 3
    s_m = np.linspace(0.0, route.cum_m[-1], 50)
    planned, detail = plan_segments(route, s_m, np.linspace(200.0, 250.0, 50), weather,
                                    np.linspace(0.0, route.cum_m[-1], 3))
    assert planned.n == int(np.ceil(route.distance_km)) and planned.distance_km == pytest.approx(route.distance_km)
    assert set(planned.road_class) == {0} and np.all(planned.grade_rad > 0)  # steady climb, 25 m/s edges
    assert detail["km_end"][-1] == pytest.approx(route.distance_km, abs=0.01)
    assert np.all(np.abs(planned.headwind_mps) <= 4.0 + 1e-9)


def test_committed_real_route_snapshot_loads_and_is_labelled_real() -> None:
    if not ROUTE_FILE.exists():
        pytest.skip("route snapshot not generated")
    planned, snap = load_planned_route()
    assert snap["is_simulated"] is False and snap["source"] == "osrm"
    assert planned.distance_km == pytest.approx(snap["distance_km"], rel=0.01)
    assert set(planned.road_class) <= {0, 1, 2} and 150 < planned.distance_km < 250


# --- chargers ------------------------------------------------------------------------------------------------


def test_osm_element_parsing_keeps_tagged_power_and_drops_untagged_connectors() -> None:
    el = {"type": "node", "id": 7, "lat": 28.5, "lon": 77.4,
          "tags": {"name": "Plaza Charger", "operator": "Some Network", "socket:ccs": "1",
                   "socket:ccs:output": "60 kW", "socket:type2": "2"}}
    rec, dropped = parse_osm_element(el)
    assert rec.id == "osm:node/7" and rec.source_ids == ["osm:node/7"] and dropped == 1
    assert [(c.type, c.kw) for c in rec.connectors] == [("CCS2", 60.0)]
    way = {"type": "way", "id": 9, "center": {"lat": 28.6, "lon": 77.2}, "tags": {}}
    assert parse_osm_element(way)[0].lat == 28.6
    assert parse_osm_element({"type": "node", "id": 1, "tags": {}})[0] is None


def test_ocm_poi_parsing() -> None:
    poi = {"ID": 5, "AddressInfo": {"Title": "T", "Latitude": 28.4, "Longitude": 77.3},
           "OperatorInfo": {"Title": "Op"}, "Connections": [{"PowerKW": 30, "ConnectionType": {"Title": "CCS (Type 2)"}}, {"PowerKW": None}]}
    rec = parse_ocm_poi(poi)
    assert rec.id == "ocm:5" and len(rec.connectors) == 1 and source_of(rec) == "ocm"


def test_duplicates_within_fifty_metres_merge_and_keep_every_source() -> None:
    a = ChargerRecord(id="osm:node/1", name="A", lat=28.5000, lon=77.4000, operator="", source_ids=["osm:node/1"])
    b = ChargerRecord(id="ocm:2", name="B", lat=28.50020, lon=77.4000, operator="Op", source_ids=["ocm:2"],
                      connectors=[{"type": "CCS2", "kw": 50}])  # ~22 m away
    far = ChargerRecord(id="osm:node/3", name="C", lat=28.6, lon=77.4, source_ids=["osm:node/3"])
    merged, n_merged = merge_duplicates([a, b, far])
    assert n_merged == 1 and len(merged) == 2
    both = next(r for r in merged if len(r.source_ids) == 2)
    assert sorted(both.source_ids) == ["ocm:2", "osm:node/1"] and both.operator == "Op" and both.connectors
    ids = [r.id for r in assign_ids(merged)]
    assert ids == ["CH-0001", "CH-0002"]


def test_chargers_near_route_reports_position_along_the_route() -> None:
    poly = np.column_stack([np.linspace(28.0, 27.0, 101), np.full(101, 77.5)])
    km = np.linspace(0, 111.0, 101)
    near = ChargerRecord(id="n", name="n", lat=27.5, lon=77.51)
    far = ChargerRecord(id="f", name="f", lat=27.5, lon=78.5)
    hits = chargers_near_route([far, near], poly, km, max_offset_km=3.0)
    assert [h.charger.id for h in hits] == ["n"] and hits[0].km_along == pytest.approx(55.5, abs=1.5)


def test_committed_charger_directory_validates_and_is_labelled() -> None:
    if not CHARGERS_FILE.exists():
        pytest.skip("charger directory not generated")
    chargers, meta = load_chargers()
    assert len(chargers) > 50 and meta["is_simulated"] is False
    assert all(c.id.startswith("CH-") and c.source_ids for c in chargers)
    assert json.loads(CHARGERS_FILE.read_text(encoding="utf-8"))["sources"]["ocm_status"].startswith("BLOCKED")

import json
from datetime import datetime
from functools import partial
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from api.main import app, get_service
from api.route_resolver import RouteResolver
from api.service import EngineService
from test_any_route import CP, DEPARTURE, FIXTURES, JAIPUR, Offline, replay_factory, trip
from verity.routing import build_route_snapshot


def test_offline_mode_plans_packaged_routes_and_lists_them_on_a_cache_miss(tmp_path, monkeypatch):
    transport = Offline()
    source = replay_factory(FIXTURES, transport)()
    snap = build_route_snapshot(source, datetime.fromisoformat(DEPARTURE),
                                (CP["lat"], CP["lon"]), (JAIPUR["lat"], JAIPUR["lon"]),
                                origin_name="Connaught Place", destination_name="Jaipur")
    assert not snap["is_simulated"]
    (tmp_path / "delhi-jaipur.json").write_text(json.dumps(snap), encoding="utf-8")

    monkeypatch.setenv("VERITY_OFFLINE", "1")
    monkeypatch.setattr("api.service.RouteResolver", partial(RouteResolver, routes_dir=tmp_path))
    monkeypatch.setattr("api.service.load_geocodes", lambda: {"jai": [{**JAIPUR, "kind": "city"}]})

    def no_router():
        pytest.fail("The offline route resolver tried to open a router client.")

    service = EngineService(n_runs=120, graph_backend="neo4j", route_client=no_router,
                            geocoder_client=replay_factory(Path(tmp_path / "missing"), transport))
    monkeypatch.setitem(app.dependency_overrides, get_service, lambda: service)
    client = TestClient(app)
    try:
        health = client.get("/api/health").json()
        assert health["offline"] is True and health["cached_route_count"] == 2
        assert health["charger_count"] > 0 and health["graph"]["backend"] == "memory"
        plan = client.post("/api/plan", json=trip())
        assert plan.status_code == 200, plan.text
        assert client.get("/api/geocode", params={"q": "Jai"}).json()["results"][0]["name"] == "Jaipur"
        misses = (
            client.get("/api/geocode", params={"q": "uncached place"}),
            client.post("/api/plan", json=trip(destination={"lat": 28.0, "lon": 77.5})),
        )
        for miss in misses:
            assert miss.status_code == 503
            assert miss.json()["code"] == "offline"
            assert {row["slug"] for row in miss.json()["cached_trips"]} == {"noida-agra", "delhi-jaipur"}
        assert transport.calls == 0
    finally:
        service.close()

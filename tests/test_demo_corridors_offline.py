"""WP10: with VERITY_OFFLINE=1 every committed pre-cached corridor plans without any network call."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.demo_cache import ROUTES_DIR
from api.main import app, get_service
from api.service import EngineService

DEPARTURE = "2026-11-22T02:00:00+05:30"
SLUGS = sorted(path.stem for path in ROUTES_DIR.glob("*.json"))
EXPECTED = {"delhi-jaipur", "delhi-chandigarh", "delhi-dehradun", "pune-mumbai", "bengaluru-mysuru"}


def no_network() -> None:
    pytest.fail("Offline mode tried to open a network client.")


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("VERITY_OFFLINE", "1")
    service = EngineService(n_runs=120, graph_backend="memory", route_client=no_network, geocoder_client=no_network)
    app.dependency_overrides[get_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_service, None)
        service.close()
        mp.undo()


def test_every_expected_corridor_is_committed() -> None:
    assert EXPECTED <= set(SLUGS)


@pytest.mark.parametrize("slug", SLUGS)
def test_a_cached_corridor_plans_offline_from_its_own_endpoints(client: TestClient, slug: str) -> None:
    snap = json.loads(Path(ROUTES_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    assert snap["is_simulated"] is False
    body = {"vehicle_id": "V12", "origin": snap["origin"], "destination": snap["destination"],
            "departure_time": DEPARTURE, "start_soc_pct": 90, "passengers": 1, "ac_on": True,
            "target_confidence": 0.9}
    response = client.post("/api/plan", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["options"]


def test_an_uncached_trip_is_refused_with_the_cached_list(client: TestClient) -> None:
    body = {"vehicle_id": "V12", "origin": {"lat": 13.08, "lon": 80.27}, "destination": {"lat": 11.0, "lon": 76.96},
            "departure_time": DEPARTURE, "start_soc_pct": 90, "passengers": 1, "ac_on": True,
            "target_confidence": 0.9}
    response = client.post("/api/plan", json=body)
    assert response.status_code == 503
    assert response.json()["code"] == "offline"
    assert EXPECTED <= {row["slug"] for row in response.json()["cached_trips"]}

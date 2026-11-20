"""WP1 geocoder: India only, its own cache and budget, honest failure. Payloads here are small hand-made Photon-shaped
test inputs (not data shown to users)."""

from __future__ import annotations

import httpx
import pytest

from verity.geocode import (
    DEFAULT_GEOCODE_BUDGET,
    GEOCODE_BUDGET_ENV,
    GEOCODE_CACHE_DIR,
    geocode,
    geocode_client,
    normalise_query,
    places_from_photon,
)
from verity.paths import CACHE_DIR
from verity.routing import CachedClient, NetworkUnavailable


def feature(name: str, cc: str, lon: float, lat: float, kind: str = "city", **props) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name, "countrycode": cc, "osm_value": kind, **props}}


PAYLOAD = {"type": "FeatureCollection", "features": [
    feature("Jaipur", "IN", 75.8, 26.9, state="Rajasthan"),
    feature("Jaipur", "PK", 70.0, 30.0),  # outside India: dropped
    feature("", "IN", 75.0, 26.0),  # no name: dropped
    feature("Jaipur", "in", 86.1, 23.5, kind="administrative", state="West Bengal"),
]}


def test_only_named_indian_points_are_kept_with_the_contract_shape() -> None:
    places = places_from_photon(PAYLOAD, 10)
    assert [p.as_dict() for p in places] == [
        {"name": "Jaipur, Rajasthan", "lat": 26.9, "lon": 75.8, "kind": "city"},
        {"name": "Jaipur, West Bengal", "lat": 23.5, "lon": 86.1, "kind": "administrative"}]
    assert len(places_from_photon(PAYLOAD, 1)) == 1


def test_queries_are_normalised_and_bounded() -> None:
    assert normalise_query("  Jaipur   City ") == "jaipur city"
    for bad in ("j", "x" * 101):
        with pytest.raises(ValueError):
            normalise_query(bad)


def test_the_geocoder_has_its_own_cache_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GEOCODE_BUDGET_ENV, raising=False)
    client = geocode_client()
    assert client.cache_dir == GEOCODE_CACHE_DIR != CACHE_DIR  # so its request_log.json is separate too
    assert client.max_requests == DEFAULT_GEOCODE_BUDGET
    monkeypatch.setenv(GEOCODE_BUDGET_ENV, "7")
    assert geocode_client().max_requests == 7


def test_geocode_asks_photon_for_india_and_caches(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PAYLOAD)

    client = CachedClient(cache_dir=tmp_path, max_requests=5, client=httpx.Client(transport=httpx.MockTransport(handler)))
    first = geocode(client, "Jaipur", 5)
    again = geocode(client, "  jaipur ", 5)
    assert first == again and len(seen) == 1  # the second call is served from the cache
    assert seen[0].url.params["countrycode"] == "IN" and seen[0].url.params["q"] == "jaipur"


def test_a_dead_network_raises_rather_than_returning_nothing(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = CachedClient(cache_dir=tmp_path, max_requests=5, retries=0, sleep=lambda _s: None,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(NetworkUnavailable):
        geocode(client, "jaipur")

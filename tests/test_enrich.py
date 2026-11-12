"""F3 enrichment: pure helpers plus an offline end-to-end run through httpx.MockTransport (no network)."""

from __future__ import annotations

import csv
import json

import httpx

import scripts.enrich_trips as cli
from verity.corpus import TRIPS, VEHICLES
from verity.enrich import aggregate_weather, distance_check, match_vehicle, weather_window
from verity.routing import CachedClient

VEHICLE = {"vehicle_id": "x-ev-40", "make": "Tata", "model": "Nexon EV Max", "battery_nominal_kwh": "40.5"}


def test_weather_window_prefers_trip_date_then_month_then_publication_proxy():
    assert weather_window({"trip_date_or_month": "2024-05-07"})[2] == "trip date"
    start, end, label = weather_window({"trip_date_or_month": "2024-02"})
    assert (start.isoformat(), end.isoformat(), label) == ("2024-02-01", "2024-02-29", "trip month")
    assert weather_window({"source_published_date": "2023-06-15"})[2].startswith("proxy: publication month")
    assert weather_window({}) is None


def test_aggregate_weather_uses_daytime_hours_and_vector_mean_direction():
    times = [f"2024-05-01T{h:02d}:00" for h in range(24)]
    payload = {"hourly": {"time": times, "temperature_2m": [20.0 if 8 <= h <= 20 else 0.0 for h in range(24)],
                          "wind_speed_10m": [2.0] * 24,
                          "wind_direction_10m": [350.0 if h % 2 else 10.0 for h in range(24)]}}
    out = aggregate_weather(payload)
    assert out["temperature_c"] == 20.0 and out["n_hours"] == 13
    direction = out["wind_from_deg"]
    assert min(direction, 360.0 - direction) < 5.0  # 350 and 10 average to north, not ~180


def test_distance_check_flags_over_15_percent_and_doubles_round_trips():
    assert distance_check(110.0, 100.0, "one_way")["flag"] is False
    assert distance_check(120.0, 100.0, "one_way")["flag"] is True
    assert distance_check(200.0, 100.0, "round_trip")["flag"] is False
    assert "unconfirmed" in distance_check(100.0, 100.0, "unknown")["note"]


def test_match_vehicle_needs_a_unique_variant():
    other = dict(VEHICLE, vehicle_id="x-ev-30", battery_nominal_kwh="30.2")
    assert match_vehicle({"make": "Tata", "model": "Nexon EV Max"}, [VEHICLE])["value"] == "x-ev-40"
    assert match_vehicle({"make": "Tata", "model": "Nexon EV Max"}, [VEHICLE, other])["value"] is None
    assert match_vehicle({"make": "Tata", "model": "Nexon EV Max", "battery_kwh_nominal": "30.2"},
                         [VEHICLE, other])["value"] == "x-ev-30"


# ---------------------------------------------------------------- offline end-to-end
def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "nominatim" in url:
        lat, lon = (28.5, 77.4) if "Noida" in url and "Greater" not in url else (28.5, 77.9)
        return httpx.Response(200, json=[{"lat": str(lat), "lon": str(lon), "display_name": "x"}])
    if "router" in url:
        coords = [[77.4 + 0.005 * i, 28.5] for i in range(101)]  # ~49 km due east
        return httpx.Response(200, json={"code": "Ok", "routes": [{
            "geometry": {"coordinates": coords}, "duration": 3600.0,
            "legs": [{"annotation": {"distance": [489.0] * 100, "speed": [25.0] * 100}}]}]})
    if "elevation" in url:
        n = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json={"elevation": [200.0 + i for i in range(n)]})
    if "archive" in url:
        times = [f"2024-04-01T{h:02d}:00" for h in range(24)]
        return httpx.Response(200, json={"hourly": {"time": times, "temperature_2m": [35.0] * 24,
                                                    "wind_speed_10m": [3.0] * 24, "wind_direction_10m": [270.0] * 24}})
    return httpx.Response(404)


def _write(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in header})


def _corpus(tmp_path):
    base = dict(record_type="soc_trip", source_url="https://example.org/t", source_kind="magazine_test",
                source_published_date="2024-05-01", make="Tata", model="Nexon EV Max", origin="Noida",
                destination="Greater Noida", one_way_or_round="one_way", distance_km="50", start_soc_pct="90",
                end_soc_pct="70", trip_date_or_month="2024-04", entered_by="t", verified="yes", verified_by="c")
    trips = [dict(base, record_id="T1"), dict(base, record_id="T2", verified="no", verified_by=""),
             dict(base, record_id="T3", record_type="consumption", origin="", destination="", distance_km="",
                  start_soc_pct="", end_soc_pct="", energy_wh_per_km="150")]
    _write(tmp_path / TRIPS.filename, TRIPS.header, trips)
    vehicle = dict(VEHICLE, variant="40.5", vehicle_type="car", battery_basis="unstated",
                   battery_source_url="https://example.org/v", source_quality="secondary",
                   engine_prior_class="compact_esuv", notes="approximate - verify")
    _write(tmp_path / VEHICLES.filename, VEHICLES.header, [vehicle])


def _client(tmp_path, handler=_handler, **kw) -> CachedClient:
    return CachedClient(tmp_path / "cache", client=httpx.Client(transport=httpx.MockTransport(handler)),
                        sleep=lambda s: None, **kw)


def test_run_enriches_only_verified_rows_with_provenance(tmp_path):
    _corpus(tmp_path)
    sleeps: list[float] = []
    doc = cli.run(tmp_path, tmp_path / "out.json", False, client=_client(tmp_path), sleep=sleeps.append)
    assert [r["record_id"] for r in doc["records"]] == ["T1", "T3"]  # T2 is a draft
    t1, t3 = doc["records"]
    assert t1["route"]["provenance"].startswith("OSRM") and t1["distance_check"]["flag"] is False
    assert t1["weather"]["value"]["temperature_c"] == 35.0 and "trip month" in t1["weather"]["provenance"]
    assert len(t1["segments"]["value"]["km_end"]) == 49 and t1["is_simulated"] is False
    assert t1["vehicle_id"]["value"] == "x-ev-40"
    assert t3["route"]["value"] is None and t3["route"]["provenance"].startswith("missing")
    assert sleeps == [1.0, 1.0]  # Nominatim: one second after each real request
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["summary"]["n_routed"] == 1


def test_cached_rerun_makes_no_requests_and_no_waits(tmp_path):
    _corpus(tmp_path)
    client = _client(tmp_path)
    cli.run(tmp_path, tmp_path / "a.json", False, client=client, sleep=lambda s: None)
    before, sleeps = client.requests_made(), []
    cli.run(tmp_path, tmp_path / "b.json", False, client=client, sleep=sleeps.append)
    assert client.requests_made() == before and sleeps == []


def test_network_failure_is_recorded_never_replaced_by_a_synthetic_route(tmp_path):
    _corpus(tmp_path)
    client = _client(tmp_path, handler=lambda r: httpx.Response(503), retries=0)
    doc = cli.run(tmp_path, tmp_path / "out.json", False, client=client, sleep=lambda s: None)
    assert doc["records"][0]["origin"] == {"value": None, "provenance": "failed: NetworkUnavailable"}
    assert "synthetic" not in json.dumps(doc).lower()

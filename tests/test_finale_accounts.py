import os
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from api.main import app, get_service
from scripts.migrate import migrate
from test_auth import issue  # noqa: F401 - shares the ephemeral Supabase JWKS fixture


@pytest.fixture(scope="module")
def database():
    url = os.environ.get("VERITY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a fresh local VERITY_TEST_DATABASE_URL with a verity_test_* database")
    cfg = conninfo_to_dict(url)
    assert cfg.get("host") in ("127.0.0.1", "localhost", "::1")
    assert cfg.get("dbname", "").startswith("verity_test_")
    with psycopg.connect(url, autocommit=True) as db:
        db.execute("CREATE SCHEMA auth")
        db.execute("CREATE TABLE auth.users (id uuid PRIMARY KEY)")
        db.execute("CREATE ROLE anon NOLOGIN")
        db.execute("CREATE ROLE authenticated NOLOGIN")
        assert migrate(db) == ["001_accounts.sql"]
        assert migrate(db) == []
    return url


@pytest.fixture
def account_client(database, issue, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database)

    def make_account(role):
        key = uuid4()
        with psycopg.connect(database) as db:
            db.execute("INSERT INTO auth.users (id) VALUES (%s)", (key,))
        return {"Authorization": "Bearer " + issue(sub=str(key), app_metadata={"role": role})}

    def dispatch(req, *, private_vehicle):
        assert req["vehicle_id"] == private_vehicle.vehicle_id
        return {"plan_id": uuid4().hex, "decision": "GO", "stops": [], "bingo_line": [],
                "reason": "Fixture decision"}

    service = SimpleNamespace(dispatch=dispatch, chargers=[SimpleNamespace(id="known-charger")])
    monkeypatch.setitem(app.dependency_overrides, get_service, lambda: service)
    return TestClient(app), make_account


def test_database_roles_have_no_access(database):
    with psycopg.connect(database) as db:
        tables = db.execute("""
            SELECT relrowsecurity FROM pg_class JOIN pg_namespace n ON n.oid = relnamespace
            WHERE nspname = 'verity_private' AND relkind = 'r'
        """).fetchall()
        assert tables == [(True,)] * 8
        for role in ("anon", "authenticated"):
            assert db.execute("SELECT has_schema_privilege(%s, 'verity_private', 'USAGE')", (role,)).fetchone() == (False,)


def test_signed_in_journey_and_cross_user_isolation(account_client, database, monkeypatch):
    client, make_account = account_client
    first, other = make_account("owner"), make_account("owner")
    sample = client.get("/api/sample-trip").json()
    car_body = {"model_id": sample["model_id"], "nickname": "Private garage", "battery_health_pct": 90}
    created = client.post("/api/me/cars", json=car_body, headers=first)
    assert created.status_code == 201, created.text
    car = created.json()
    assert client.get("/api/me", headers=first).json()["cars"] == [car]
    trip_body = {**sample["request"], "car_id": car["id"]}
    created = client.post("/api/trips", json=trip_body, headers=first)
    assert created.status_code == 201, created.text
    path = "/api/trips/" + created.json()["id"]
    assert client.post(path + "/save", headers=first).json()["saved"]
    assert client.post(path + "/arrival", json={"arrival_pct": 19}, headers=first).json()["arrival_pct"] == 19

    graph_calls = []
    monkeypatch.setattr("api.routes_trips.graph_query", lambda *args: graph_calls.append(args))
    report_body = {"charger_id": "known-charger", "status": "working"}
    keyed = {**first, "Idempotency-Key": "518d4e17-9642-4c44-bec8-4b9f5c89d114"}
    report = client.post("/api/reports/quick", json=report_body, headers=keyed)
    assert client.post("/api/reports/quick", json=report_body, headers=keyed).json()["id"] == report.json()["id"]
    assert client.post("/api/reports/quick", json={**report_body, "status":"not_working"}, headers=keyed).status_code == 409
    assert report.status_code == 201, report.text
    assert client.post("/api/reports/quick", json=report_body, headers=first).json()["id"] == report.json()["id"]
    assert len(graph_calls) == 1

    for private in (path, "/api/me/cars/" + car["id"], "/api/reports/" + report.json()["id"]):
        assert client.get(private, headers=other).status_code == 404
    assert client.post(path + "/share", headers=other).status_code == 404
    token = client.post(path + "/share", headers=first).json()["token"]
    public_path = "/api/share/" + token
    public = client.get(public_path)
    assert public.json() == {"decision": "GO", "stops": [], "bingo_line": []}
    assert public.headers["cache-control"] == "no-store"

    expires = client.post("/api/trips", json=trip_body, headers=first).json()["id"]
    with psycopg.connect(database) as db:
        for trip_id in [expires, created.json()["id"]]:
            db.execute("UPDATE verity_private.trips SET created_at = now() - interval '91 days' WHERE id = %s",
                       (trip_id,))
        db.execute("UPDATE verity_private.share_links SET expires_at = now() - interval '1 second'")
    # Fresh client and database connections continue to see the saved trip.
    restarted = TestClient(app)
    assert restarted.get(path, headers=first).json()["saved"]
    assert restarted.get("/api/trips/" + expires, headers=first).status_code == 404
    assert restarted.get(public_path).status_code == 404
    assert restarted.delete("/api/me/cars/" + car["id"], headers=first).status_code == 204
    assert restarted.get(path, headers=first).json()["car_id"] is None


def test_operator_approval_and_charger_scope(account_client, monkeypatch):
    client, make_account = account_client
    cpo, admin = make_account("cpo"), make_account("admin")
    calls = []

    def query(name, *args):
        calls.append((name, *args))
        if name == "cpo_chargers":
            assert args == ("my-operator",)
            return [{"charger_id": "mine"}]
        if name in {"cpo_open_reports", "critical_chargers"}:
            return [{"charger_id": "mine"}, {"charger_id": "another-operators-charger"}]
        return []

    monkeypatch.setattr("api.routes_cpo.graph_query", query)
    claim = client.post("/api/cpo/claims", headers=cpo, json={"operator_id": "my-operator"}).json()
    approve = f"/api/admin/cpo-claims/{claim['id']}/approve"
    assert client.get("/api/admin/cpo-claims", headers=cpo).status_code == 403
    assert claim["id"] in [row["id"] for row in client.get("/api/admin/cpo-claims", headers=admin).json()]
    assert client.get("/api/cpo/chargers", headers=cpo).json()["chargers"] == []
    assert not calls
    assert client.post(approve, headers=cpo).status_code == 403
    assert client.post(approve, headers=admin).status_code == 200
    assert claim["id"] not in [row["id"] for row in client.get("/api/admin/cpo-claims", headers=admin).json()]
    assert client.get("/api/cpo/chargers", headers=cpo).json()["open_reports"] == [{"charger_id": "mine"}]
    assert client.get("/api/cpo/critical-chargers?corridor_id=sample&usable_range_km=150",
                      headers=cpo).json() == [{"charger_id": "mine"}]
    assert client.post("/api/cpo/chargers/another-operators-charger/fixed", headers=cpo).status_code == 404
    assert not any(call[0] == "record_fix" for call in calls)
    assert client.post("/api/cpo/chargers/mine/fixed", headers=cpo).status_code == 200


def test_manager_checks_own_fleet(account_client):
    client, make_account = account_client
    manager, other, owner = make_account("fleet_manager"), make_account("fleet_manager"), make_account("owner")
    sample = client.get("/api/sample-trip").json()
    body = {"model_id": sample["model_id"], "nickname": "Fleet vehicle", "battery_health_pct": 90,
            "start_soc_pct": 60}
    created = client.post("/api/fleet/vehicles", json=body, headers=manager)
    assert created.status_code == 201, created.text
    assert client.get("/api/fleet/vehicles", headers=other).json() == []
    assert client.get("/api/fleet/vehicles", headers=owner).status_code == 403
    vid = created.json()["id"]
    assert client.put("/api/fleet/vehicles/" + vid, json=body, headers=other).status_code == 404
    booking = {k: v for k, v in sample["request"].items() if k != "start_soc_pct"}
    checked = client.post("/api/fleet/booking/check", json=booking, headers=manager)
    assert checked.json() == [{"vehicle_id": vid, "accepted": True, "decision": "GO", "reason": "Fixture decision"}]


def test_real_operator_graph_recovers_reports_and_repairs(account_client, monkeypatch):
    from api.service import EngineService

    client, make_account = account_client
    operator, admin, driver = (make_account(role) for role in ("cpo", "admin", "driver"))
    service = EngineService(graph_backend="memory")
    charger = next(c for c in service.chargers if c.operator)
    monkeypatch.setitem(app.dependency_overrides, get_service, lambda: service)
    monkeypatch.setattr("api.main.get_service", lambda: service)
    claim = client.post("/api/cpo/claims", headers=operator, json={"operator_id": charger.operator}).json()
    assert client.post(f"/api/admin/cpo-claims/{claim['id']}/approve", headers=admin).is_success
    assert client.post("/api/reports/quick", headers=driver,
                       json={"charger_id": charger.id, "status": "not_working"}).json()["synced_at"]
    rows = client.get("/api/cpo/chargers", headers=operator).json()
    assert all(r["operator_id"] == charger.operator for r in rows["chargers"])
    assert any(r["charger_id"] == charger.id and not r["is_simulated"] for r in rows["open_reports"])
    keyed = {**operator, "Idempotency-Key": "0feb659c-82c3-4a72-963b-f3aa0ddf733d"}
    endpoint = f"/api/cpo/chargers/{charger.id}/fixed"
    fixed = client.post(endpoint, headers=keyed).json()["fixed_at"]
    assert client.post(endpoint, headers=keyed).json()["fixed_at"] == fixed
    service = EngineService(graph_backend="memory")
    fresh = client.get("/api/cpo/chargers", headers=operator).json()
    assert not any(r["charger_id"] == charger.id for r in fresh["open_reports"])
    assert any(r.evidence == "operator_repair" for r in service.graph.mirror.reports(charger.id))
    for path in ("suspected-rings", "shared-grid-risk", "critical-chargers?corridor_id=noida-agra&usable_range_km=90"):
        assert isinstance(client.get("/api/cpo/" + path, headers=operator).json(), list)

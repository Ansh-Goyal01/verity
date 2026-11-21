"""WP8a live fake-report ring attack: the graph finds the ring, trust barely moves, reset leaves nothing behind.
In-memory graph (no network); every injected report is SIMULATED."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main
from api.demo_ring import ATTACK_PREFIX, ring_attack, ring_reset, slow_ring_limit
from api.service import ApiError, EngineService
from test_auth import issue  # noqa: F401 - shares the ephemeral Supabase JWKS fixture


@pytest.fixture(scope="module")
def svc() -> EngineService:
    return EngineService(n_runs=200, graph_backend="memory")


@pytest.fixture(scope="module")
def attacked(svc: EngineService) -> dict:
    return ring_attack(svc)


def test_the_graph_finds_every_attacking_source(attacked: dict) -> None:
    ring = attacked["ring"]
    assert ring["found"] and ring["attack_sources_caught"] == attacked["n_sources"] == 6
    assert ring["shared"] >= 4 and ring["jaccard_min"] >= 0.5 and "MATCH (s:Source)-[r:REPORTED]" in ring["cypher"]
    assert attacked["reports_added"] == 6 * 5 and attacked["is_simulated"] is True


def test_trust_barely_moves_but_would_without_ring_detection(attacked: dict) -> None:
    before, after = attacked["trust_before"]["p_works"], attacked["trust_after"]["p_works"]
    naive = attacked["trust_without_ring_detection"]["p_works"]
    assert abs(after - before) <= 0.05 < abs(naive - before)


def test_the_honest_limit_is_read_from_the_e5_file(attacked: dict) -> None:
    limit = slow_ring_limit()
    assert attacked["limit"] == limit and limit["source"].endswith("e5_trust_recovery_simulated.json")
    assert limit["recall"] is not None and "10 days" in limit["text"]


def test_reset_removes_every_injected_report(svc: EngineService, attacked: dict) -> None:
    out = ring_reset(svc)
    assert out["reports_removed"] == attacked["reports_added"] and out["attack_reports_left"] == 0
    assert not any(r.source_id.startswith(ATTACK_PREFIX) for r in svc.graph.mirror.reports())
    assert not any(s.startswith(ATTACK_PREFIX) for s in svc.graph.mirror.ring_ids())


def test_unknown_target_is_404(svc: EngineService) -> None:
    with pytest.raises(ApiError) as err:
        ring_attack(svc, "CH-NOPE")
    assert err.value.status == 404


def bearer(issue, role: str) -> dict:
    return {"Authorization": "Bearer " + issue(app_metadata={"role": role})}


def test_the_demo_endpoints_are_off_unless_verity_demo(svc: EngineService, monkeypatch: pytest.MonkeyPatch,
                                                       issue) -> None:
    main.app.dependency_overrides[main.get_service] = lambda: svc
    try:
        client = TestClient(main.app)
        admin = bearer(issue, "admin")
        monkeypatch.delenv("VERITY_DEMO", raising=False)
        assert client.post("/api/demo/ring-attack", json={}, headers=admin).status_code == 403
        monkeypatch.setenv("VERITY_DEMO", "1")
        hit = client.post("/api/demo/ring-attack", json={"n_sources": 4}, headers=admin)
        assert hit.status_code == 200 and hit.json()["ring"]["found"]
        assert client.post("/api/demo/ring-attack/reset", headers=admin).json()["attack_reports_left"] == 0
        hood = client.get("/api/graph/neighborhood/CH-0003", params={"depth": 2})
        assert hood.status_code == 200 and hood.json()["depth"] == 2
        assert client.get("/api/graph/neighborhood/CH-NOPE").status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_ring_attack_endpoints_need_an_admin_sign_in(svc: EngineService, monkeypatch: pytest.MonkeyPatch, issue) -> None:
    monkeypatch.setenv("VERITY_DEMO", "1")
    main.app.dependency_overrides[main.get_service] = lambda: svc
    try:
        client = TestClient(main.app)
        for path in ("/api/demo/ring-attack", "/api/demo/ring-attack/reset"):
            assert client.post(path, json={}).status_code == 401  # no token
            for role in ("owner", "driver", "fleet_manager", "cpo"):
                assert client.post(path, json={}, headers=bearer(issue, role)).status_code == 403
        admin = bearer(issue, "admin")
        assert client.post("/api/demo/ring-attack", json={"n_sources": 4}, headers=admin).status_code == 200
        reset = client.post("/api/demo/ring-attack/reset", headers=admin)
        assert reset.status_code == 200 and reset.json()["attack_reports_left"] == 0
        assert not any(r.source_id.startswith(ATTACK_PREFIX) for r in svc.graph.mirror.reports())
    finally:
        main.app.dependency_overrides.clear()

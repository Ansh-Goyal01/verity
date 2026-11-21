from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.auth import User
from api.middleware import install


@pytest.fixture
def secured():
    app = FastAPI()
    install(app, ["https://web.example", "*"], True, True, 2)

    @app.get("/boom")
    def boom():
        raise RuntimeError("private server detail")

    @app.post("/api/voice/stt")
    def voice():
        return {"ok": True}

    @app.post("/api/trips")
    def trip():
        return {"ok": True}

    return TestClient(app)


def test_headers_and_plain_errors(secured, caplog):
    for endpoint, status in [("/missing", 404), ("/boom", 500)]:
        response = secured.get(endpoint, headers={"Origin": "https://web.example"})
        assert response.status_code == status
        assert set(response.json()) == {"error", "id"}
        assert response.json()["id"] == response.headers["x-request-id"]
        assert response.headers["strict-transport-security"] == "max-age=31536000"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["access-control-allow-origin"] == "https://web.example"
        assert "private server detail" not in response.text
    assert response.json()["id"] in caplog.text
    assert "private server detail" not in caplog.text


def test_body_limits_include_chunked_and_content_length(secured):
    assert secured.post("/api/trips", content=b"x" * 5_000_000).status_code == 413
    chunk = b"x" * 35_000
    response = secured.post("/api/trips", content=iter([chunk, chunk]))
    assert response.status_code == 413
    assert set(response.json()) == {"error", "id"}
    assert secured.post("/api/voice/stt", files={"audio": ("data.wav", b"x" * 1_048_576)}).status_code == 413


def test_cors_rejects_unconfigured_origin(secured):
    response = secured.options("/api/trips", headers={"Origin": "https://other.example",
                                                     "Access-Control-Request-Method": "POST"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_account_budget_follows_user_across_addresses(secured, monkeypatch):
    monkeypatch.setattr("api.middleware.verify_access_token", lambda token: User(token, "test@example.com", "owner"))
    for address in ["192.0.2.1", "192.0.2.2"]:
        assert secured.post("/api/voice/stt", headers={"Authorization": "Bearer user-a",
                                                       "X-Forwarded-For": address}).status_code == 200
    response = secured.post("/api/voice/stt", headers={"Authorization": "Bearer user-a",
                                                      "X-Forwarded-For": "192.0.2.3"})
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


def test_planning_budget_applies_without_credentials(secured):
    for _ in range(30):
        assert secured.post("/api/trips").status_code == 200
    assert secured.post("/api/trips").status_code == 429

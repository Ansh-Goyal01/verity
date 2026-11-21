import json
import time
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest

from api.auth import ROLES, User, current_user, require_role, signing_keys
from scripts.seed_demo_accounts import SeedError, seed


PROJECT = "https://demo.supabase.co"


@pytest.fixture
def issue(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", PROJECT)
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="demo", alg="ES256", use="sig")
    signing_keys.cache_clear()

    def fetch(client):
        assert client.uri == PROJECT + "/auth/v1/.well-known/jwks.json"
        return {"keys": [jwk]}

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fetch)

    def sign(**overrides):
        claims = dict(sub=str(uuid4()), email="example@example.com", exp=int(time.time()) + 300,
                      iss=PROJECT + "/auth/v1", aud="authenticated", role="authenticated")
        claims.update(overrides)
        return jwt.encode(claims, private, algorithm="ES256", headers={"kid": "demo"})

    yield sign
    signing_keys.cache_clear()


app = FastAPI()


@app.get("/identity")
def identity(user: User = Depends(current_user)):
    return user


@app.get("/admin")
def admin(user: User = Depends(require_role("admin"))):
    return user


client = TestClient(app)


def get(token, path="/identity"):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def test_credentials_required():
    response = client.get("/identity")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("claims", [
    {"exp": 1}, {"aud": "anon"}, {"iss": "https://other.supabase.co/auth/v1"},
    {"sub": "invalid"}, {"email": ""}, {"role": "service_role"}, {"is_anonymous": True},
    {"app_metadata": None},
])
def test_invalid_session(issue, claims):
    assert get(issue(**claims)).status_code == 401


def test_tampered_token(issue):
    header, _, signature = issue().split(".")
    payload = issue(app_metadata={"role": "admin"}).split(".")[1]
    assert get(".".join([header, payload, signature]), "/admin").status_code == 401


def test_unsigned_and_shared_secret_tokens_are_rejected(issue):
    for alg, key in [("none", None), ("HS256", b"x" * 32)]:
        assert get(jwt.encode({}, key, algorithm=alg, headers={"kid": "demo"})).status_code == 401


def test_only_server_metadata_grants_access(issue):
    owner = issue(user_metadata={"role": "admin"})
    assert get(owner).json()["role"] == "owner"
    assert get(owner, "/admin").status_code == 403
    assert get(issue(app_metadata={"role": "unknown"})).status_code == 403
    assert get(issue(app_metadata={"role": "admin"}), "/admin").status_code == 200


def test_jwks_outage_fails_closed(issue, monkeypatch):
    def offline(*args):
        raise jwt.PyJWKClientConnectionError("private upstream detail")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", offline)
    response = get(issue())
    assert response.status_code == 503
    assert "upstream" not in response.text


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", PROJECT)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-admin")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-public")
    for role in ROLES:
        monkeypatch.setenv(f"VERITY_DEMO_{role.upper()}_PASSWORD", uuid4().hex)


def test_seed_checks_all_roles_and_can_resume(demo_env, issue):
    accounts = {}

    def respond(request):
        body = json.loads(request.content)
        email = body["email"]
        if request.url.path.endswith("/admin/users"):
            assert request.headers["apikey"] == "test-admin"
            if email in accounts:
                return httpx.Response(422, json={"error_code": "email_exists"})
            accounts[email] = dict(id=str(uuid4()), email=email, app_metadata=body["app_metadata"])
            return httpx.Response(201)
        assert request.url.path == "/auth/v1/token"
        assert request.url.params["grant_type"] == "password"
        assert request.headers["apikey"] == "test-public"
        assert "Authorization" not in request.headers
        user = accounts[email]
        token = issue(sub=user["id"], email=email, app_metadata=user["app_metadata"])
        return httpx.Response(200, json={"user": user, "access_token": token})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        seed("demo", http)
        seed("demo", http)
    assert {a["app_metadata"]["role"] for a in accounts.values()} == set(ROLES)


def test_seed_refuses_other_project_before_contact(demo_env):
    with httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected request"))) as http:
        with pytest.raises(SeedError, match="project reference"):
            seed("other", http)

"""Authentication dependencies for routes that require a Supabase user session."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
import os
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt


ROLES = ("owner", "driver", "fleet_manager", "cpo", "admin")
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    id: str
    email: str
    role: str


def unauthorized() -> HTTPException:
    return HTTPException(401, "A valid sign-in is required.", headers={"WWW-Authenticate": "Bearer"})


@lru_cache(maxsize=1)
def signing_keys(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", timeout=3, lifespan=300)


def verify_access_token(token: str) -> User:
    if len(token) > 16384:
        raise unauthorized()
    project = os.environ.get("SUPABASE_URL", "").rstrip("/")
    parts = urlsplit(project)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.path or parts.query or parts.fragment:
        raise HTTPException(503, "Sign-in verification is unavailable.")
    issuer = f"{project}/auth/v1"
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") not in ("ES256", "RS256") or not isinstance(kid, str) or not kid:
            raise unauthorized()
        key = signing_keys(issuer).get_signing_key(kid)
        if key.algorithm_name != header["alg"]:
            raise unauthorized()
        claims = jwt.decode(token, key, algorithms=[key.algorithm_name], issuer=issuer,
                            audience="authenticated", options={"require": ["exp", "iss", "aud", "sub"]})
        user_id = str(UUID(claims["sub"]))
    except (jwt.PyJWKClientConnectionError, jwt.PyJWKSetError):
        raise HTTPException(503, "Sign-in verification is unavailable.") from None
    except (jwt.PyJWTError, ValueError, TypeError, OverflowError, RecursionError):
        raise unauthorized() from None

    email = claims.get("email")
    metadata = claims.get("app_metadata", {})
    if (claims.get("role") != "authenticated" or claims.get("is_anonymous", False) is not False
            or not isinstance(email, str) or not email.strip() or not isinstance(metadata, dict)):
        raise unauthorized()
    role = metadata.get("role", "owner")
    if role not in ROLES:
        raise HTTPException(403, "Account role is unavailable.")
    return User(user_id, email, role)


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> User:
    if credentials is None:
        raise unauthorized()
    return verify_access_token(credentials.credentials)


def require_role(*roles: str) -> Callable[..., User]:
    if not roles or any(role not in ROLES for role in roles):
        raise ValueError("Expected one or more supported account roles.")

    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Your account does not have access.")
        return user

    return check

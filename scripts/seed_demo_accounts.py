"""Create CX-1 accounts in a dedicated hosted Supabase demo project and check sign-in."""

import argparse
import os
import re

from fastapi import HTTPException
import httpx

from api.auth import ROLES, verify_access_token


class SeedError(Exception):
    """Operator-facing message safe to print."""


def seed(project_ref: str, client: httpx.Client) -> None:
    project = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not re.fullmatch(r"[a-z0-9-]+", project_ref) or project != f"https://{project_ref}.supabase.co":
        raise SeedError("The demo project reference must match SUPABASE_URL.")
    admin_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    public_key = os.environ.get("SUPABASE_ANON_KEY")
    passwords = {role: os.environ.get(f"VERITY_DEMO_{role.upper()}_PASSWORD", "") for role in ROLES}
    if not admin_key or not public_key or admin_key == public_key:
        raise SeedError("Set distinct service and public API keys for the demo project.")
    if any(len(pw) < 16 for pw in passwords.values()) or len(set(passwords.values())) != len(ROLES):
        raise SeedError("Set a distinct password of at least 16 characters for each demo role.")

    auth_url = f"{project}/auth/v1"
    admin_headers = {"apikey": admin_key, "Authorization": f"Bearer {admin_key}"}
    for role, password in passwords.items():
        email = f"verity-demo-{role}@example.com"
        credentials = {"email": email, "password": password}
        created = client.post(f"{auth_url}/admin/users", headers=admin_headers, json={
            **credentials, "email_confirm": True, "app_metadata": {"role": role, "verity_demo": True},
        })
        if not created.is_success:
            duplicate = (created.status_code in (400, 409, 422) and
                         created.json().get("error_code") in ("email_exists", "user_already_exists"))
            if not duplicate:
                raise SeedError(f"{role} creation failed (HTTP {created.status_code}).")

        login = client.post(f"{auth_url}/token", params={"grant_type": "password"},
                            headers={"apikey": public_key}, json=credentials)
        if not login.is_success:
            raise SeedError(f"{role} password sign-in failed (HTTP {login.status_code}).")
        session = login.json()
        account = session["user"]
        user = verify_access_token(session["access_token"])
        if (account["app_metadata"].get("verity_demo") is not True or user.role != role
                or user.email != email or user.id != account["id"]):
            raise SeedError(f"{role} identity or demo metadata did not match.")
        print(f"{role} sign-in verified.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-project-ref", required=True, help="Reference of the isolated demo project")
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            seed(args.demo_project_ref, client)
    except SeedError as exc:
        print(str(exc))
        return 1
    except (httpx.HTTPError, HTTPException, ValueError, TypeError, KeyError, AttributeError):
        print("Demo setup failed during the Supabase request or identity check.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Check Supabase password sign-in and saved-trip persistence on the deployed API."""

import argparse
import os
from urllib.parse import urlsplit
from uuid import UUID

import httpx


def https_origin(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Provide a bare HTTPS origin.")
    return value.rstrip("/")


def verify_public(client: httpx.Client, api_url: str, frontend: str) -> None:
    health = client.get(f"{api_url}/api/health")
    health.raise_for_status()
    if health.json()["status"] != "ok":
        raise ValueError("API health is not OK.")
    docs = client.get(f"{api_url}/openapi.json")
    if docs.status_code != 404 or not docs.json().get("id"):
        raise ValueError("Expected production error handling and disabled API docs.")
    url = f"{api_url}/api/health"
    for origin, allowed in [(frontend, True), ("https://cors-check.invalid", False)]:
        response = client.options(url, headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        if allowed:
            if response.status_code != 200 or response.headers.get("access-control-allow-origin") != origin:
                raise ValueError("The frontend origin did not pass CORS preflight.")
        elif response.headers.get("access-control-allow-origin"):
            raise ValueError("CORS allowed an unrelated origin.")
    print("Production error handling, health and frontend CORS verified.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("api_url", type=https_origin)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--create-saved-trip", action="store_true", help="Create a car and saved trip in the demo account")
    action.add_argument("--verify-trip", type=UUID, help="Verify the saved trip after a deployment restart")
    action.add_argument("--public-only", action="store_true", help="Check public production behavior without signing in")
    parser.add_argument("--frontend-origin", type=https_origin, help="Allowed frontend origin (default: API origin)")
    args = parser.parse_args()
    try:
        if args.public_only:
            with httpx.Client(timeout=60, follow_redirects=False) as client:
                verify_public(client, args.api_url, args.frontend_origin or args.api_url)
            return 0
        supabase = https_origin(os.environ["SUPABASE_URL"])
        public_key = os.environ["SUPABASE_ANON_KEY"]
        email = os.environ["VERITY_VERIFY_EMAIL"]
        password = os.environ["VERITY_VERIFY_PASSWORD"]

        def request(client: httpx.Client, method: str, url: str, **kwargs: object) -> dict:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        with httpx.Client(timeout=60, follow_redirects=False) as client:
            health = request(client, "GET", f"{args.api_url}/api/health")
            session = request(client, "POST", f"{supabase}/auth/v1/token", params={"grant_type": "password"},
                              headers={"apikey": public_key}, json={"email": email, "password": password})
            auth = {"Authorization": "Bearer " + session["access_token"]}

            def call(method: str, path: str, **kwargs: object) -> dict:
                return request(client, method, f"{args.api_url}/api{path}", headers=auth, **kwargs)

            profile = call("GET", "/me")["profile"]
            if profile["id"] != session["user"]["id"]:
                raise ValueError("The signed-in account does not match its database profile.")
            if args.create_saved_trip:
                sample = call("GET", "/sample-trip")
                car = call("POST", "/me/cars", json={"model_id": sample["model_id"], "nickname": "Deployment check",
                                                    "battery_health_pct": 100})
                trip = call("POST", "/trips", json={**sample["request"], "car_id": car["id"]})
                saved = call("POST", f"/trips/{trip['id']}/save")
                if saved["saved"] is not True:
                    raise ValueError("The verification trip was not saved.")
                print("Saved verification trip ID", trip["id"])
            elif args.verify_trip:
                trip = call("GET", f"/trips/{args.verify_trip}")
                if trip["id"] != str(args.verify_trip) or trip["saved"] is not True or not trip["plan"]:
                    raise ValueError("The saved verification trip did not match.")
                print("Saved trip is present with its stored plan.")
        print(f"Sign-in and profile verified. Graph backend {health['graph']['backend']}. "
              f"Sarvam key present {health['sarvam_key_present']}.")
    except httpx.HTTPStatusError as exc:
        parser.exit(1, f"Deployment check got HTTP {exc.response.status_code}.\n")
    except KeyError as exc:
        required = {"SUPABASE_URL", "SUPABASE_ANON_KEY", "VERITY_VERIFY_EMAIL", "VERITY_VERIFY_PASSWORD"}
        if exc.args[0] in required:
            parser.exit(1, f"Deployment sign-in check requires {exc.args[0]}.\n")
        parser.exit(1, "The deployed API response did not contain an expected field.\n")
    except (httpx.HTTPError, TypeError, ValueError):
        parser.exit(1, "Deployment check failed. Check the HTTPS origins, environment and API configuration.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

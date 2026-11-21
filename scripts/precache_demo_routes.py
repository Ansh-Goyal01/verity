"""Fetch real route snapshots and public Photon suggestions for map review."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from api.demo_cache import CORRIDORS, ROUTES_DIR, WARM_QUERIES, trip_info
from verity.geocode import geocode, geocode_client, normalise_query
from verity.routing import CachedClient, IST, NetworkUnavailable, RequestBudgetExceeded, build_route_snapshot


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare(departure: datetime, output: Path, corridors: list[tuple[str, str, str]],
            places_client: CachedClient, routes_client: CachedClient) -> list[dict]:
    queries = {normalise_query(query) for _, a, b in corridors for query in (a, b)}
    queries.update(map(normalise_query, WARM_QUERIES))
    places = {query: [place.as_dict() for place in geocode(places_client, query, 10)] for query in sorted(queries)}
    review = []
    for slug, start, end in corridors:
        a, b = (places[normalise_query(name)] for name in (start, end))
        if not a or not b:
            raise ValueError(f"The geocoder found no endpoint for {slug}.")
        snap = build_route_snapshot(routes_client, departure, (a[0]["lat"], a[0]["lon"]),
                                    (b[0]["lat"], b[0]["lon"]), origin_name=a[0]["name"],
                                    destination_name=b[0]["name"])
        if snap["is_simulated"]:
            raise ValueError(f"Real route, weather or elevation data was unavailable for {slug}.")
        write_json(output / "routes" / f"{slug}.json", snap)
        review.append(trip_info(slug, snap))
    write_json(output / "demo_geocodes.json", places)
    return review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--departure", type=datetime.fromisoformat,
                        help="Forecast departure in ISO 8601, default current IST hour")
    parser.add_argument("--output", type=Path, default=ROUTES_DIR.parent)
    parser.add_argument("--route", action="append", nargs=3, metavar=("SLUG", "ORIGIN", "DESTINATION"),
                        help="Override the default corridors, repeating for each route")
    args = parser.parse_args()
    corridors = args.route or list(CORRIDORS)
    if any(not slug.isascii() or not slug.replace("-", "").isalnum() for slug, _, _ in corridors):
        parser.error("Slugs must contain only ASCII letters, digits and dashes.")
    departure = args.departure or datetime.now(IST).replace(minute=0, second=0, microsecond=0)
    try:
        review = prepare(departure, args.output, corridors, geocode_client(timeout=15, retries=1),
                         CachedClient(timeout=25, retries=1))
    except (NetworkUnavailable, RequestBudgetExceeded, OSError, ValueError) as exc:
        message = str(exc) if type(exc) is ValueError else f"Route preparation failed ({type(exc).__name__})."
        parser.exit(1, message + "\n")
    print(json.dumps(review, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Write data/fleet/demo_fleet.json (WP6): real catalog car models at two Delhi depots with SIMULATED battery levels.

Depot coordinates are Photon's first result for each query (cached through CachedClient), never typed in. Battery %
values are scenario inputs, chosen by trying inputs against the engine, never by changing it (docs/DECISIONS.md WP6).
Run: uv run python scripts/build_demo_fleet.py
"""

from __future__ import annotations

import json
from datetime import date

from verity.geocode import geocode, geocode_client
from verity.paths import DATA_DIR

# "Okhla Industrial Area, New Delhi" returned "MCD New Moti Nagar" first (the wrong place), so the queries are the
# shortest wording whose first result names the place; main() refuses a first result that does not.
DEPOTS = {"okhla": "Okhla Phase 1", "dwarka": "Dwarka Sector 21"}
# (fleet_id, catalog vehicle_id, label, battery % now, depot): scenario inputs, SIMULATED.
CARS = (
    ("F1", "tata-curvv-ev-55", "Curvv 55 (Okhla)", 90, "okhla"),
    ("F2", "mahindra-be-6-79", "BE 6 79 (Okhla)", 85, "okhla"),
    ("F3", "tata-tiago-ev-24", "Tiago 24 (Okhla)", 80, "okhla"),
    ("F4", "mg-zs-ev-50.3", "ZS EV (Dwarka)", 75, "dwarka"),
    ("F5", "tata-nexon-ev-45", "Nexon 45 (Dwarka)", 60, "dwarka"),
    ("F6", "mg-comet-ev-17.4", "Comet (Dwarka)", 95, "dwarka"),
)
NOTE = ("SIMULATED scenario: the car MODELS are real rows of data/corpus/vehicles_india.csv, but their battery % and "
        "depots were chosen by us for the demo. Depot coordinates are Photon's first result for 'query'. "
        "trips_calibrated is read from the catalog, never from this file.")


def main() -> None:
    client, today = geocode_client(), date.today().isoformat()
    depots = {}
    for key, query in DEPOTS.items():
        first = geocode(client, query, 1)[0]
        if query.split()[0].lower() not in first.name.lower():
            raise SystemExit(f"Photon's first result for {query!r} is {first.name!r}: not that place; change the query")
        depots[key] = {"name": first.name, "query": query, "lat": first.lat, "lon": first.lon,
                       "source": f"Photon (komoot) first result for the query, {today}"}
    fleet = {"schema": "verity.fleet.v1", "is_simulated": True, "note": NOTE, "depots": depots,
             "vehicles": [{"fleet_id": f, "vehicle_id": v, "label": label, "soc_pct": soc, "depot": d}
                          for f, v, label, soc, d in CARS]}
    out = DATA_DIR / "fleet" / "demo_fleet.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fleet, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for key, depot in depots.items():
        print(f"{key}: {depot['name']} ({depot['lat']:.5f}, {depot['lon']:.5f})")


if __name__ == "__main__":
    main()

"""G2 (WP8d): the chargers a corridor can't lose -> results/g2_critical_chargers.json.

For each demo corridor and a mid-size car's p50 range there (the engine's own Monte Carlo), a charger is CRITICAL when
removing it opens a gap (start -> chargers along the route -> end) longer than that range while the full set has none.
Simplification, stated in the output: a gap is passable when shorter than the full-battery range (100 % to the arrival
reserve), i.e. every stop is assumed to charge back to full. Computed in Python on the planner's own candidate chargers;
Aura Graph Analytics is not used.

    uv run python scripts/g2_critical_chargers.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _corridors import corridors, mid_size_car, range_km  # noqa: E402

from api.service import EngineService  # noqa: E402
from verity.coverage import critical  # noqa: E402
from verity.paths import RESULTS_DIR  # noqa: E402

OUT = RESULTS_DIR / "g2_critical_chargers.json"


def longest_gap(kms: list[float], length: float) -> dict:
    """Where the longest stretch without a candidate charger is (km from the start)."""
    points = [0.0, *sorted(kms), length]
    i = max(range(len(points) - 1), key=lambda k: points[k + 1] - points[k])
    return {"from_km": round(points[i], 1), "to_km": round(points[i + 1], 1)}


def main() -> int:
    svc = EngineService(graph_backend="memory")
    small = min(svc.catalog.values(), key=lambda c: (c.battery_kwh, c.vehicle_id))
    cars = {"mid_size": (mid_size_car(svc), "median battery size of the catalog"),
            "small": (small, "smallest battery of the catalog: where the gaps start to bind")}
    rows = []
    for c in corridors(svc):
        length = float(c.resolved.poly_km[-1])
        row = {"corridor": c.name, "ends": [e.get("name") for e in c.ends], "distance_km": round(length, 1),
               "candidate_chargers": len(c.near), "route_source": c.resolved.snap.get("source"),
               "demo_route": c.resolved.is_demo, "by_car": {}}
        for key, (car, _) in cars.items():
            reach = range_km(svc, car, c)
            full, crit = critical(c.near, length, reach)
            row["longest_gap_at"] = longest_gap([rc.km_along for rc in c.near], length)
            row["by_car"][key] = {"range_km_p50": round(reach, 1), "longest_gap_km": round(full, 1),
                                  "passable_with_every_charger": full <= reach, "critical": crit}
            print(f"{c.name} [{key}]: {len(c.near)} chargers, range {reach:.0f} km, longest gap {full:.0f} km, "
                  f"{len(crit)} critical")
        rows.append(row)
    out = {"experiment": "G2 chargers a corridor can't lose", "is_simulated": False,
           "label": "REAL routes and charger positions; the range is a MODEL output (Monte Carlo p50)",
           "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "cars": {key: {"vehicle_id": car.vehicle_id, "name": car.name, "battery_kwh": car.battery_kwh, "why": why,
                          "calibration_basis": car.calibration_basis} for key, (car, why) in cars.items()},
           "n_runs": svc.cfg.n_runs,
           "method": "critical = removing it opens a gap longer than the range while the full set has none",
           "simplification": "every stop is assumed to charge back to 100 %; the reserve is kept at each gap's end",
           "aura_graph_analytics": "not used (computed in Python on the planner's candidate chargers)",
           "coverage_note": "gaps are gaps in OUR directory (OpenStreetMap + Open Charge Map, chargers within the planner's "
                            "corridor of the route), not proof that no charger exists on the ground",
           "corridors": rows}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""G1 (WP8c): how often a backup shares its primary's nearest substation -> results/g1_grid_shared_cause.json.

Per demo corridor: (a) among the planner's candidate chargers, the pairs within ALT_AHEAD_KM of each other along the
route (the pairs that could be primary and backup) that have the SAME nearest OpenStreetMap substation within 5 km;
(b) in a mid-size car's actual release (90 % start, target 0.9), the stop-backup pairs that share one. Proximity only:
the nearest substation is not known to feed a charger. Charger and substation positions are REAL (OpenStreetMap /
Open Charge Map); what a shared substation does to a plan rests on an assumed beta (sites.BETA_SUBSTATION).

    uv run python scripts/import_substations.py   # first, once
    uv run python scripts/g1_grid_shared_cause.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _corridors import DEPARTURE, corridors, mid_size_car  # noqa: E402

from api.service import BOOKING, EngineService  # noqa: E402
from verity.dispatch import ALT_AHEAD_KM  # noqa: E402
from verity.paths import RESULTS_DIR  # noqa: E402
from verity.sites import BETA_SUBSTATION  # noqa: E402
from verity.substations import SUBSTATION_RADIUS_KM, load_substations  # noqa: E402

OUT = RESULTS_DIR / "g1_grid_shared_cause.json"
START_SOC = 90.0


def candidate_pairs(near, sub_of: dict) -> tuple[int, int]:
    """(pairs within ALT_AHEAD_KM along the route, of which share a nearest substation)."""
    near = sorted(near, key=lambda rc: rc.km_along)
    total = shared = 0
    for i, a in enumerate(near):
        for b in near[i + 1:]:
            if b.km_along - a.km_along > ALT_AHEAD_KM:
                break
            total += 1
            sa, sb = sub_of.get(a.charger.id), sub_of.get(b.charger.id)
            shared += sa is not None and sa == sb
    return total, shared


def release_pairs(release: dict, sub_of: dict) -> tuple[int, int]:
    stops = release["stops"]
    pairs = [(stops[a["for_stop_index"]]["charger_id"], a["charger_id"]) for a in release["alternates"]
             if 0 <= a["for_stop_index"] < len(stops)]
    shared = sum(1 for p, b in pairs if sub_of.get(p) is not None and sub_of.get(p) == sub_of.get(b))
    return len(pairs), shared


def main() -> int:
    if not load_substations():
        raise SystemExit("no data/processed/substations.json: run scripts/import_substations.py first")
    svc = EngineService(graph_backend="memory")
    sub_of = {cid: sid for cid, (sid, _) in svc.substation_links.items()}
    car = mid_size_car(svc)
    rows = []
    for c in corridors(svc):
        linked = sum(rc.charger.id in sub_of for rc in c.near)
        total, shared = candidate_pairs(c.near, sub_of)
        ends = (BOOKING["origin"], BOOKING["destination"]) if c.resolved.is_demo else c.ends
        release = svc.dispatch({"vehicle_id": car.vehicle_id, "origin": ends[0], "destination": ends[1],
                                "departure_time": BOOKING["departure_time"] if c.resolved.is_demo else DEPARTURE,
                                "start_soc_pct": START_SOC, "passengers": 1, "ac_on": True, "target_confidence": 0.9})
        r_total, r_shared = release_pairs(release, sub_of)
        rows.append({"corridor": c.name, "candidate_chargers": len(c.near), "linked_to_a_substation": linked,
                     "candidate_pairs_within_alt_km": total, "candidate_pairs_sharing_substation": shared,
                     "release": {"plan_id": release["plan_id"], "decision": release["decision"],
                                 "stop_backup_pairs": r_total, "sharing_substation": r_shared}})
        print(f"{c.name}: {linked}/{len(c.near)} linked; {shared}/{total} candidate pairs share; "
              f"release {r_shared}/{r_total} stop-backup pairs share")
    out = {"experiment": "G1 likely shared grid cause", "is_simulated": False,
           "label": "REAL charger and substation positions (OpenStreetMap); the effect on plans rests on an ASSUMED beta",
           "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "link_radius_km": SUBSTATION_RADIUS_KM, "alt_ahead_km": ALT_AHEAD_KM, "beta_substation": BETA_SUBSTATION,
           "car": {"vehicle_id": car.vehicle_id, "name": car.name, "start_soc_pct": START_SOC},
           "n_runs": svc.cfg.n_runs,
           "note": "nearest substation within the radius, by straight-line distance; NOT known to feed the charger",
           "corridors": rows}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

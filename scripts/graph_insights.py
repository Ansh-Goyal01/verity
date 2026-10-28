"""Two questions only a graph answers, on the demo graph: where is the route uncovered, and who files fake reports.

* Coverage gaps: road segments (10 km slices of the cached Noida-Agra route) with no USABLE alternate charger
  ahead within the planner's backup window. "Usable" = reported on at least once and more likely than not to work
  (``usable_alternate`` in trust/graph_build.py), so an unreported charger is not counted as coverage.
* Ring candidates: sources that file the same verdict on the same chargers within a time window. They are
  cross-checked against the trust engine's own ring detector (the one that actually down-weights sources).

Runs on whichever backend works (Neo4j when .env credentials work, otherwise in memory) and says which. Reports
and chargers are the demo's: real OSM chargers, SIMULATED reports. Everything printed is measured from the graph.
Never prints credentials.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore")

from api.service import EngineService  # noqa: E402
from verity.dispatch import ALT_AHEAD_KM  # noqa: E402
from verity.trust.engine import TrustEngine  # noqa: E402
from verity.trust.graph import RING_WINDOW_HOURS, RoadSegmentRec  # noqa: E402
from verity.trust.graph_build import MIN_ALT_P_WORKS, usable_alternate  # noqa: E402
from verity.trust.reports_sim import NOW  # noqa: E402


def merge_ranges(gaps: list[RoadSegmentRec]) -> list[tuple[float, float]]:
    """Adjacent gap segments as km ranges."""
    ranges: list[tuple[float, float]] = []
    for g in sorted(gaps, key=lambda s: s.km_from):
        if ranges and abs(ranges[-1][1] - g.km_from) < 1e-9:
            ranges[-1] = (ranges[-1][0], g.km_to)
        else:
            ranges.append((g.km_from, g.km_to))
    return ranges


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-p", type=float, default=0.0, help="also count alternates below this P(works) as gaps")
    ap.add_argument("--min-shared", type=int, default=4, help="chargers two sources must both report on")
    ap.add_argument("--window-hours", type=float, default=RING_WINDOW_HOURS, help="reports further apart do not pair up")
    args = ap.parse_args(argv)

    svc = EngineService(n_runs=100)  # the forecast is not used here; only the graph and the trust estimates
    try:
        # One read through the handle, so a Neo4j failure part-way falls back instead of crashing the script.
        reports, gaps, rings, loose = svc.graph.call(lambda g: (
            g.reports(), g.coverage_gaps(min_p=args.min_p),
            g.report_rings(min_shared=args.min_shared, window_hours=args.window_hours),
            g.report_rings(min_shared=args.min_shared, window_hours=None)))
        print(f"graph backend: {'Neo4j' if svc.graph.backend == 'neo4j' else 'in-memory'} ({svc.graph.reason})")
        print(f"chargers {len(svc.chargers)} | near the route {len(svc.near)} | reports {len(reports)} "
              "(SIMULATED demo reports)")

        usable = [rc for rc in svc.near
                  if usable_alternate(svc.trust[rc.charger.id].p_works, svc.trust[rc.charger.id].trust_level)]
        print(f"\nCoverage gaps (no usable alternate within {ALT_AHEAD_KM:.0f} km ahead; usable = reported on and "
              f"P(works) >= {MIN_ALT_P_WORKS:.0%}): {len(usable)} of {len(svc.near)} near-route chargers count")
        for lo, hi in merge_ranges(gaps):
            print(f"  km {lo:.0f} - {hi:.0f}  ({hi - lo:.0f} km)")
        print(f"  {len(gaps)} road segments uncovered" if gaps else "  none: every road segment has a usable alternate")

        print(f"\nRing candidates (same verdict on >= {args.min_shared} common chargers within {args.window_hours:g} h):")
        for ring in rings:
            print(f"  {len(ring.sources)} sources, up to {ring.shared} chargers in common: {', '.join(ring.sources)}")
        if not rings:
            targets: dict[str, set[str]] = defaultdict(set)
            for r in reports:
                targets[r.source_id].add(r.charger_id)
            busiest = max((len(t) for t in targets.values()), default=0)
            print(f"  none among {len(targets)} sources (the busiest reported on {busiest} charger(s))")
        flagged = TrustEngine(svc.chargers).estimate([r for r in reports if r.charger_id in svc.trust], NOW).flagged_sources
        print(f"  trust engine's own ring detector flags: {list(flagged) or 'nothing'}")
        if len(loose) != len(rings):
            print(f"  without the time window {len(loose)} candidate ring(s) appear: slow rings are only visible that way, "
                  "at the price of flagging honest repeat reporters (experiment E5 found a 10-day evasive ring was missed).")
        return 0
    finally:
        svc.close()


if __name__ == "__main__":
    sys.exit(main())

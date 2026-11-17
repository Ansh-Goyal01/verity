"""W2.4: find report rings in the graph in use and write ``ring_id`` back onto the Source nodes (an offline batch).

Pure Cypher computes the Jaccard agreement edges (``AGREEMENT_CYPHER``) and Python's union-find groups them into
communities; no Aura Graph Analytics session is used (its current client was not verified). The last batch replaces
the previous ring ids. Nothing here calls a paid service.

    uv run python scripts/graph_rings.py [--min-shared 4] [--min-jaccard 0.5] [--min-agree 0.9] [--min-size 3]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

from api.service import EngineService  # noqa: E402
from verity.trust.graph import communities, ring_id_of  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-shared", type=int, default=4)
    parser.add_argument("--min-jaccard", type=float, default=0.5)
    parser.add_argument("--min-agree", type=float, default=0.9)
    parser.add_argument("--min-size", type=int, default=3)
    args = parser.parse_args()
    svc = EngineService(n_runs=200)
    try:
        def batch(graph):
            edges = graph.agreement_edges(args.min_shared, args.min_jaccard, args.min_agree)
            rings = communities(edges, args.min_size)
            graph.write_ring_ids(rings)
            return edges, rings

        edges, rings = svc.graph.call(batch)
        mirror_edges, mirror_rings = batch(svc.graph.mirror) if svc.graph.backend == "neo4j" else (edges, rings)
        print(f"graph in use: {svc.graph.backend}; {len(edges)} agreement edges, {len(rings)} rings")
        for ring in rings:
            print(f"  {ring_id_of(ring)}: {len(ring.sources)} sources, up to {ring.shared} shared chargers")
        print(f"in-memory mirror agrees: {(mirror_edges, mirror_rings) == (edges, rings)}")
    finally:
        svc.close()


if __name__ == "__main__":
    main()

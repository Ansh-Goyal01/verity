"""W2.3: how long does the Cypher candidate-chain query take on the live Neo4j, against Yen's algorithm in Python?

Times every ``Neo4jGraph.chains`` call made inside real dispatches of the three demo vehicles (the query family is
the one that matters: all the planner's legs are written to Neo4j first), and the Python path on the in-memory
graph for the same requests. Needs a reachable Neo4j; writes ``results/chains_latency.json``. No paid calls.

    uv run python scripts/bench_chains.py [--runs 8]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import verity.dispatch as dispatch_module  # noqa: E402
from api.service import EngineService  # noqa: E402
from verity.trust.graph import MAX_CHAIN_LEGS, Neo4jGraph, chains_cypher  # noqa: E402

DEMO_SOC = {"V07": 70, "V12": 62, "V19": 40}
BASE = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def summary(ms: list[float]) -> dict:
    return {"n": len(ms), "p50_ms": round(statistics.median(ms), 1), "p95_ms": round(percentile(ms, 0.95), 1),
            "max_ms": round(max(ms), 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=8, help="dispatches per demo vehicle")
    args = parser.parse_args()

    chain_ms: list[float] = []
    real_chains = Neo4jGraph.chains

    def timed(self, *a, **kw):
        t0 = time.perf_counter()
        try:
            return real_chains(self, *a, **kw)
        finally:
            chain_ms.append(1000.0 * (time.perf_counter() - t0))

    yen_ms: list[float] = []
    real_diverse = dispatch_module.diverse_paths

    def timed_yen(*a, **kw):
        t0 = time.perf_counter()
        try:
            return real_diverse(*a, **kw)
        finally:
            yen_ms.append(1000.0 * (time.perf_counter() - t0))

    neo4j = EngineService(n_runs=500, graph_backend="neo4j")
    memory = EngineService(n_runs=500, graph_backend="memory")
    try:
        if neo4j.graph.backend != "neo4j":
            raise SystemExit("Neo4j is not reachable: nothing to measure")
        Neo4jGraph.chains = timed  # type: ignore[method-assign]
        dispatch_module.diverse_paths = timed_yen  # type: ignore[assignment]
        for vehicle, soc in DEMO_SOC.items():
            for _ in range(args.runs):
                neo4j.dispatch({**BASE, "vehicle_id": vehicle, "start_soc_pct": soc}, refresh=True)
        neo4j_calls = list(chain_ms)
        yen_ms.clear()
        for vehicle, soc in DEMO_SOC.items():
            for _ in range(args.runs):
                memory.dispatch({**BASE, "vehicle_id": vehicle, "start_soc_pct": soc}, refresh=True)
    finally:
        Neo4jGraph.chains = real_chains  # type: ignore[method-assign]
        dispatch_module.diverse_paths = real_diverse  # type: ignore[assignment]
        neo4j.close()
        memory.close()

    out = {
        "experiment": "W2.3 candidate chains: Cypher on Neo4j vs Yen's algorithm in Python",
        "is_simulated": False,
        "note": "Real queries on the live graph, demo routes only; the demo vehicles' chargers and reports are simulated.",
        "dispatches_per_vehicle": args.runs, "vehicles": sorted(DEMO_SOC), "max_legs": MAX_CHAIN_LEGS,
        "neo4j_cypher_chains": summary(neo4j_calls),
        "python_yen_diverse_paths": summary(yen_ms),
        "cypher": chains_cypher(MAX_CHAIN_LEGS),
        "reading": "One dispatch makes three chain queries (cheapest, reliability-weighted, spatially distinct).",
    }
    path = ROOT / "results" / "chains_latency.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("neo4j_cypher_chains", "python_yen_diverse_paths")}, indent=1))


if __name__ == "__main__":
    main()

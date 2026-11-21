"""A live fake-report ring attack for the demo (WP8a), and its reset. Off unless the server sets ``VERITY_DEMO=1``.

N simulated sources all report one named charger ``broken`` within minutes, plus the same 4 nearby chargers, so the
attack has the overlap the ring rule looks for (>= 4 shared chargers, Jaccard >= 0.5, label agreement >= 0.9, within
12 h: ``trust/rings.py``). The graph then finds the ring itself (``AGREEMENT_CYPHER``, run inside Neo4j when it is the
backend), and the trust engine, which down-weights ring members, barely moves the charger. The response also shows what
the same reports would have done with ring detection off. Every injected report is ``is_simulated: true``.

Honest limit, quoted from the saved experiment: a slower ring spread over ten days is not detected (E5).
"""

from __future__ import annotations

import json
import time
from datetime import timedelta

import numpy as np

from verity.paths import RESULTS_DIR
from verity.schemas import Report
from verity.trips import haversine_m
from verity.trust.engine import TrustEngine
from verity.trust.graph import AGREEMENT_CYPHER, TEST_PREFIX, ChargerGraph, communities, report_key
from verity.trust.reports_sim import NOW

from .graph_endpoints import trust_block

ATTACK_PREFIX = "ring-attack-"
DEFAULT_TARGET = "CH-0003"  # the demo's well-evidenced pin on Noida-Agra (service.DEMO_REPORTS)
CO_TARGETS = 4  # other chargers every ring member also reports, so the ring has the overlap the rule needs
E5_FILE = RESULTS_DIR / "e5_trust_recovery_simulated.json"
E5_SLOW = "evasive ring of 12 (10 days)"


def _nearest(chargers, target, k: int) -> list[str]:
    others = [c for c in chargers if c.id != target.id and not c.id.startswith(TEST_PREFIX)]
    d = haversine_m(np.full(len(others), target.lat), np.full(len(others), target.lon),
                    np.array([c.lat for c in others]), np.array([c.lon for c in others]))
    return [others[i].id for i in np.argsort(d)[:k]]


def attack_reports(target_id: str, co_targets: list[str], n_sources: int) -> list[Report]:
    out = []
    for i in range(n_sources):
        for j, cid in enumerate([target_id, *co_targets]):
            out.append(Report(charger_id=cid, source_id=f"{ATTACK_PREFIX}{i + 1}", status="broken",
                              t=NOW - timedelta(minutes=3 * i + j), evidence="SIMULATED ring attack (WP8a demo)",
                              is_simulated=True))
    return out


def slow_ring_limit() -> dict:
    """The E5 line for a ring spread over ten days, from the saved result file (never typed in)."""
    try:
        scenario = json.loads(E5_FILE.read_text(encoding="utf-8"))["scenarios"][E5_SLOW]
    except (OSError, KeyError, ValueError):
        return {"text": "E5 result file missing: the slow-ring limit is not quoted.", "recall": None, "source": None}
    recall = scenario["ring_detection"]["recall"]
    return {"text": f"A ring of 12 spreading its reports over 10 days was caught with recall {recall:.0%} in E5 "
                    "(SIMULATED): slow, patient rings evade this rule.", "recall": recall,
            "source": f"results/{E5_FILE.name}"}


def _rings_in_graph(g: ChargerGraph) -> tuple[list, list, float]:
    t = time.perf_counter()
    edges = g.agreement_edges()
    return edges, communities(edges), 1000 * (time.perf_counter() - t)


def ring_attack(svc, charger_id: str | None = None, n_sources: int = 6) -> dict:
    from .service import ApiError  # local: service imports the API modules

    view = svc._view
    target_id = charger_id or DEFAULT_TARGET
    target = next((c for c in view.chargers if c.id == target_id), None)
    if target is None:
        raise ApiError(404, "unknown_charger", f"No charger {target_id!r}")
    co = _nearest(view.chargers, target, CO_TARGETS)
    reps = attack_reports(target_id, co, n_sources)
    before = view.trust.get(target_id)
    svc.graph.populate(lambda g: g.add_reports(reps))
    after_view = svc.graph.call(svc._read_view)
    svc._adopt(after_view)
    naive = TrustEngine(after_view.chargers, pool_operators=True, use_rings=False).estimate(
        list(after_view.reports), NOW).chargers.get(target_id)
    edges, rings, query_ms = svc.graph.call(_rings_in_graph)
    svc.graph.populate(lambda g: g.write_ring_ids(rings))  # derived data, shown by the neighbourhood view (8b)
    attackers = {r.source_id for r in reps}
    caught = [r for r in rings if attackers & set(r.sources)]
    members = sorted({s for r in caught for s in r.sources})
    jac = [e.jaccard for e in edges if e.a in attackers and e.b in attackers]
    changes, pending = svc.replan_after_report(target_id)
    return {"charger_id": target_id, "charger_name": target.name, "co_targets": co, "n_sources": n_sources,
            "reports_added": len(reps),
            "ring": {"found": bool(caught), "members": members, "attack_sources_caught": len(attackers & set(members)),
                     "shared": max((r.shared for r in caught), default=0),
                     "jaccard_min": round(min(jac), 3) if jac else None, "cypher": AGREEMENT_CYPHER,
                     "query_ms": round(query_ms, 1), "backend": svc.graph.backend,
                     "executed": svc.graph.backend == "neo4j"},
            "trust_before": trust_block(before), "trust_after": trust_block(after_view.trust.get(target_id)),
            "trust_without_ring_detection": trust_block(naive),
            "plans": {"changes": changes, "pending": pending},
            "limit": slow_ring_limit(), "is_simulated": True}


def ring_reset(svc) -> dict:
    """Remove every report the attack added (and, on Neo4j, the sources left without a report)."""
    def count(g: ChargerGraph) -> tuple[int, int]:
        reps = [r for r in g.reports() if r.source_id.startswith(ATTACK_PREFIX)]
        return len(reps), len({r.source_id for r in reps})

    def prune(g: ChargerGraph) -> None:
        g.prune_simulated_reports({report_key(r) for r in g.reports() if not r.source_id.startswith(ATTACK_PREFIX)})

    before = svc.graph.call(count)
    svc.graph.populate(prune)
    _, rings, _ = svc.graph.call(_rings_in_graph)
    svc.graph.populate(lambda g: g.write_ring_ids(rings))
    svc._adopt(svc.graph.call(svc._read_view))
    after = svc.graph.call(count)
    return {"reports_removed": before[0] - after[0], "attack_reports_left": after[0], "attack_sources_left": after[1],
            "backend": svc.graph.backend, "is_simulated": True}

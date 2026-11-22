"""The audit endpoints behind the graph (W2.6): why a plan was released, one charger's neighbourhood, and a live report.

``GraphEndpoints`` is a mixin for ``EngineService`` (it uses ``graph``, ``chargers``, ``trust``, ``_read_view`` and
``_adopt``). Every graph response carries the Cypher that produced it and the query time; on the in-memory backend
``executed`` is false and the Cypher is the query Neo4j would have run (golden rule 1: say what really happened).
"""

from __future__ import annotations

from datetime import datetime

from verity.forecast import round_prob
from verity.schemas import Report
from verity.trust.engine import ChargerTrust, TrustEngine
from verity.trust.graph import TEST_PREFIX, ChargerGraph
from verity.trust.clock import rebase_real_reports
from verity.trust.graph_types import AuditView
from verity.trust.reports_sim import NOW

from .assured import contradicts_strong_evidence, load_config as load_assured_config, reward_block

REPORT_CHANNELS = {"driver_voice": "driver:driver_voice"}  # channel -> source id (one source per channel, as for public evidence)
SCRIPTED_SOURCE_ID = "driver:driver_voice_scripted"  # a demo voice note: its own source, so it never lends a real driver's reputation
MAX_NOTE_CHARS = 200


def view_response(view: AuditView) -> dict:
    simulated = any(n["props"].get("is_simulated") for n in view.nodes) or any(
        e["props"].get("is_simulated") for e in view.edges)
    return {"nodes": list(view.nodes), "edges": list(view.edges), "cypher": view.cypher, "query_ms": view.query_ms,
            "backend": view.backend, "executed": view.executed, "is_simulated": simulated}


def trust_block(t: ChargerTrust | None) -> dict:
    """Probabilities rounded to 5 % (golden rule 5); a charger nobody has reported on says so."""
    if t is None:
        return {"p_works": None, "trust_level": "unknown", "n_reports": 0, "days_since_last": None}
    return {"p_works": round_prob(t.p_works), "trust_level": t.trust_level, "n_reports": t.n_reports,
            "days_since_last": None if t.days_since_last is None else int(round(t.days_since_last))}


def _api_error(status: int, code: str, message: str) -> Exception:
    from .service import ApiError  # late: service imports this module

    return ApiError(status, code, message)


class GraphEndpoints:
    def graph_why(self, plan_id: str) -> dict:
        view = self.graph.call(lambda g: g.why(plan_id))
        stored = self.plans.get(plan_id)
        if view is None and stored is not None:  # still served from the plan cache but swept (or never written) in the graph
            self._record_plan(plan_id, stored.response, self._view)
            view = self.graph.call(lambda g: g.why(plan_id))
        if view is None:
            raise _api_error(404, "unknown_plan", f"No plan {plan_id!r} in the graph (plans are kept for 24 h and the newest 300, "
                                   "and may live on another instance); plan again with POST /api/plan or /api/dispatch")
        return {"plan_id": plan_id, **view_response(view)}

    def graph_charger(self, charger_id: str) -> dict:
        view = self.graph.call(lambda g: g.charger_neighbourhood(charger_id))
        if view is None:
            raise _api_error(404, "unknown_charger", f"No charger {charger_id!r}")
        return {"charger_id": charger_id, "trust": trust_block(self.trust.get(charger_id)), **view_response(view)}

    def graph_neighbourhood(self, charger_id: str, depth: int = 2) -> dict:
        """WP8b: nodes + edges a browser graph library can draw. Depth 1 is the charger audit view (site, operator,
        same-site chargers, reports and their sources, plans, aliases); depth 2 adds each source's ring and the
        charger's NEAREST substation (proximity only) with the other chargers that share it."""
        def read(g: ChargerGraph) -> tuple:
            view = g.charger_neighbourhood(charger_id)
            if view is None or depth < 2:
                return view, {}, {}
            return view, g.ring_ids(), g.substation_links()

        view, rings, links = self.graph.call(read)
        if view is None:
            raise _api_error(404, "unknown_charger", f"No charger {charger_id!r}")
        out = view_response(view)
        nodes = {n["id"]: n for n in out["nodes"]}
        edges = list(out["edges"])
        queries = [view.cypher]
        if depth >= 2:
            for nid, node in list(nodes.items()):
                ring = rings.get(nid.split(":", 1)[1]) if node["label"] == "Source" else None
                if ring:
                    nodes.setdefault(f"Ring:{ring}", {"id": f"Ring:{ring}", "label": "Ring", "props": {}})
                    edges.append({"from": nid, "to": f"Ring:{ring}", "type": "MEMBER_OF", "props": {}})
            link = links.get(charger_id)
            if link is not None:
                sid = link[0]
                nodes[f"Substation:{sid}"] = {"id": f"Substation:{sid}", "label": "Substation",
                                              "props": {"note": "nearest substation; not known to feed it"}}
                for cid, (other, d) in sorted(links.items()):
                    if other != sid:
                        continue
                    nodes.setdefault(f"Charger:{cid}", {"id": f"Charger:{cid}", "label": "Charger", "props": {}})
                    edges.append({"from": f"Charger:{cid}", "to": f"Substation:{sid}", "type": "NEAREST_SUBSTATION",
                                  "props": {"distance_km": d}})
            queries += ["MATCH (s:Source) WHERE s.ring_id IS NOT NULL RETURN s.id, s.ring_id",
                        "MATCH (c:Charger)-[r:NEAREST_SUBSTATION]->(s:Substation) RETURN c.id, s.id, r.distance_km"]
        return {**out, "charger_id": charger_id, "depth": depth,
                "nodes": sorted(nodes.values(), key=lambda n: n["id"]), "edges": edges, "cypher": "\n;\n".join(queries)}

    def add_report(self, req: dict, now: datetime | None = None, *, is_simulated: bool = False,
                   reporter: str | None = None) -> dict:
        """One REPORTED edge, with the charger's trust before and after. Trust is evaluated the way dispatch does it:
        simulated reports on the frozen simulation clock, real ones aged by real time (``trust/clock.py``).
        ``is_simulated`` is true only for a scripted report (a demo voice note), never for a person."""
        if not req.get("consent"):
            raise _api_error(422, "consent_required", "A report is stored with the charger's history: consent must be true.")
        cid = req["charger_id"]
        if cid not in {c.id for c in self.chargers}:
            raise _api_error(404, "unknown_charger", f"No charger {cid!r}")
        when = now or (NOW if is_simulated else self.clock())  # simulated reports live on the simulation clock
        rep = Report(charger_id=cid, source_id=SCRIPTED_SOURCE_ID if is_simulated else REPORT_CHANNELS[req["channel"]], status=req["status"], t=when,
                     evidence=(req.get("note") or "")[:MAX_NOTE_CHARS] or "driver report", is_simulated=is_simulated)
        as_of = NOW  # the engine's clock (real reports are rebased onto it), so the numbers here match the plan

        def trust_now(g: ChargerGraph) -> ChargerTrust | None:
            known = [c for c in g.chargers() if not c.id.startswith(TEST_PREFIX)]
            ids = {c.id for c in known}
            reports = rebase_real_reports([r for r in g.reports() if r.charger_id in ids], self.clock())
            return TrustEngine(known).estimate(reports, as_of).chargers.get(cid)

        before = self.graph.call(trust_now)
        self.graph.populate(lambda g: g.add_report(rep))
        after = self.graph.call(trust_now)
        self._adopt(self.graph.call(self._read_view))
        trust_before = trust_block(before)
        return {"charger_id": cid, "status": rep.status, "source_id": rep.source_id, "t": when.isoformat(),
                "trust_before": trust_before, "trust_after": trust_block(after),
                "backend": self.graph.backend, "is_simulated": is_simulated,
                "reward": self._report_reward(rep, trust_before, reporter, when)}

    def _report_reward(self, rep: Report, trust_before: dict, reporter: str | None, when: datetime) -> dict:
        """WP9: the trust engine's own signals decide the reward (ring membership, contradiction); no second fraud system.
        The daily cap counts every report from one reporter key (the caller's address), per UTC day, in memory."""
        cfg = load_assured_config()
        over_cap = False
        if reporter is not None:
            counts = self.__dict__.setdefault("_report_counts", {})
            day = when.date().isoformat()
            for stale in [k for k in counts if k[1] != day]:
                del counts[stale]
            over_cap = counts.get((reporter, day), 0) >= cfg["report_daily_cap"]
            counts[(reporter, day)] = counts.get((reporter, day), 0) + 1
        return reward_block(is_simulated=rep.is_simulated, in_ring=rep.source_id in self.graph.call(lambda g: g.ring_ids()),
                            contradicts=contradicts_strong_evidence(trust_before, rep.status), over_cap=over_cap, cfg=cfg)

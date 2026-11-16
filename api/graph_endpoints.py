"""The audit endpoints behind the graph (W2.6): why a plan was released, one charger's neighbourhood, and a live report.

``GraphEndpoints`` is a mixin for ``EngineService`` (it uses ``graph``, ``chargers``, ``trust``, ``_read_view`` and
``_adopt``). Every graph response carries the Cypher that produced it and the query time; on the in-memory backend
``executed`` is false and the Cypher is the query Neo4j would have run (golden rule 1: say what really happened).
"""

from __future__ import annotations

from datetime import datetime, timezone

from verity.forecast import round_prob
from verity.schemas import Report
from verity.trust.engine import ChargerTrust, TrustEngine
from verity.trust.graph import TEST_PREFIX, ChargerGraph
from verity.trust.graph_types import AuditView
from verity.trust.reports_sim import NOW

REPORT_CHANNELS = {"driver_voice": "driver:driver_voice"}  # channel -> source id (one source per channel, as for public evidence)
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
            raise _api_error(404, "unknown_plan", f"No plan {plan_id!r} in the graph; call POST /api/dispatch first")
        return {"plan_id": plan_id, **view_response(view)}

    def graph_charger(self, charger_id: str) -> dict:
        view = self.graph.call(lambda g: g.charger_neighbourhood(charger_id))
        if view is None:
            raise _api_error(404, "unknown_charger", f"No charger {charger_id!r}")
        return {"charger_id": charger_id, "trust": trust_block(self.trust.get(charger_id)), **view_response(view)}

    def add_report(self, req: dict, now: datetime | None = None) -> dict:
        """One REPORTED edge from a person, with the charger's trust before and after, evaluated at the engine's clock
        (the same one dispatch uses; a report newer than it counts as age 0). The clock itself is S7's to fix."""
        if not req.get("consent"):
            raise _api_error(422, "consent_required", "A report is stored with the charger's history: consent must be true.")
        cid = req["charger_id"]
        if cid not in {c.id for c in self.chargers}:
            raise _api_error(404, "unknown_charger", f"No charger {cid!r}")
        when = now or datetime.now(timezone.utc)
        rep = Report(charger_id=cid, source_id=REPORT_CHANNELS[req["channel"]], status=req["status"], t=when,
                     evidence=(req.get("note") or "")[:MAX_NOTE_CHARS] or "driver report", is_simulated=False)
        as_of = NOW  # the clock dispatch uses, so the numbers here match the plan; a newer report's age is clamped to 0

        def trust_now(g: ChargerGraph) -> ChargerTrust | None:
            known = [c for c in g.chargers() if not c.id.startswith(TEST_PREFIX)]
            ids = {c.id for c in known}
            return TrustEngine(known).estimate([r for r in g.reports() if r.charger_id in ids], as_of).chargers.get(cid)

        before = self.graph.call(trust_now)
        self.graph.populate(lambda g: g.add_report(rep))
        after = self.graph.call(trust_now)
        self._adopt(self.graph.call(self._read_view))
        return {"charger_id": cid, "status": rep.status, "source_id": rep.source_id, "t": when.isoformat(),
                "trust_before": trust_block(before), "trust_after": trust_block(after),
                "backend": self.graph.backend, "is_simulated": False}

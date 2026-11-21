"""Account reports and operator views of the same charger graph the planner uses."""

from collections import defaultdict
from datetime import timezone
from hashlib import sha256
from typing import TYPE_CHECKING

from fastapi import HTTPException

from verity.coverage import critical
from verity.schemas import Report
from verity.trust.graph import communities, report_key

from .db import transaction

if TYPE_CHECKING:
    from .service import EngineService

REPAIR = "operator_repair"


def account_report(user_id, charger_id, status, when, *, repair=False) -> Report:
    # Sources appear in public graph views. Never put an account ID in those views.
    identity = sha256(str(user_id).encode()).hexdigest()
    return Report(charger_id=charger_id, source_id=("feed:repair-" if repair else "user-") + identity,
                  t=when.astimezone(timezone.utc), status="broken" if status == "not_working" else status,
                  evidence=REPAIR if repair else "account report", is_simulated=False)


def restore_reports(svc: "EngineService") -> None:
    """Replay committed account observations into a fresh graph or a serverless memory mirror."""
    with transaction() as db:
        reports = db.execute("SELECT user_id, charger_id, status, created_at FROM verity_private.reports").fetchall()
        repairs = db.execute("SELECT user_id, target, at FROM verity_private.audit_log WHERE action = %s",
                             (REPAIR,)).fetchall()
    saved = [account_report(r["user_id"], r["charger_id"], r["status"], r["created_at"]) for r in reports]
    saved.extend(account_report(r["user_id"], r["target"], "working", r["at"], repair=True) for r in repairs)
    known = {c.id for c in svc.chargers}
    present = {report_key(r) for r in svc.graph.mirror.reports()}
    missing = [r for r in saved if r.charger_id in known and report_key(r) not in present]
    if missing:
        svc.graph.populate(lambda g: g.add_reports(missing))
        svc._adopt(svc.graph.call(svc._read_view))


class OperatorGraph:
    def __init__(self, svc: "EngineService"):
        self.svc = svc

    def cpo_chargers(self, operator):
        return [{"charger_id": c.id, "name": c.name, "operator_id": c.operator, "lat": c.lat, "lon": c.lon}
                for c in self.svc.chargers if c.operator == operator]

    def cpo_open_reports(self, operator):
        ids = {r["charger_id"] for r in self.cpo_chargers(operator)}
        if not ids:
            return []
        restore_reports(self.svc)
        reports = [r for r in self.svc.graph.call(lambda g: g.reports()) if r.charger_id in ids]
        repaired = {}
        for r in reports:
            if r.evidence == REPAIR and r.source_id.startswith("feed:repair-") and not r.is_simulated:
                repaired[r.charger_id] = max(r.t, repaired.get(r.charger_id, r.t))
        return [{"charger_id": r.charger_id, "status": r.status, "reported_at": r.t,
                 "is_simulated": r.is_simulated} for r in sorted(reports, key=lambda r: r.t, reverse=True)
                if r.status in {"broken", "blocked", "no_power"}
                and (r.charger_id not in repaired or r.t > repaired[r.charger_id])]

    def suspected_rings(self, charger_ids):
        restore_reports(self.svc)
        allowed = set(charger_ids)

        def read(graph):
            by_source = defaultdict(list)
            for report in graph.reports():
                if report.charger_id in allowed:
                    by_source[report.source_id].append(report)
            rows = []
            for ring in communities(graph.agreement_edges()):
                affected = [r for source in ring.sources for r in by_source.get(source, ())]
                if affected:
                    rows.append({"sources_in_ring": len(ring.sources),
                                 "charger_ids": sorted({r.charger_id for r in affected}),
                                 "includes_simulated_reports": any(r.is_simulated for r in affected)})
            return rows

        return self.svc.graph.call(read)

    def shared_grid_risk(self, operator):
        ids = {r["charger_id"] for r in self.cpo_chargers(operator)}
        stations = defaultdict(list)
        for cid, (station, distance) in self.svc.graph.call(lambda g: g.substation_links()).items():
            if cid in ids:
                stations[station].append({"charger_id": cid, "distance_km": round(distance, 2)})
        return [{"nearest_substation": station, "chargers": sorted(rows, key=lambda r: r["charger_id"])}
                for station, rows in sorted(stations.items()) if len(rows) > 1]

    def critical_chargers(self, corridor_id, usable_range_km):
        routes = {"noida-agra": self.svc.resolver.demo, **self.svc.resolver.packaged}
        route = routes.get(corridor_id.strip().lower())
        if route is None:
            raise HTTPException(422, "Select an available corridor: " + ", ".join(routes))
        near, _, _ = self.svc._route_inputs(self.svc._view, route)
        _, rows = critical(near, float(route.poly_km[-1]), usable_range_km)
        return rows

    def _apply(self, report):
        self.svc.graph.populate(lambda g: g.add_report(report))
        self.svc._adopt(self.svc.graph.call(self.svc._read_view))

    def record_user_report(self, user_id, charger_id, status, when):
        # The caller already committed the database row, including this stable timestamp.
        self._apply(account_report(user_id, charger_id, status, when))

    def record_fix(self, charger_id, user_id, when):
        # Commit before acknowledging even when the graph is running in memory.
        with transaction() as db:
            db.execute("INSERT INTO verity_private.audit_log (user_id, action, target, at) VALUES (%s, %s, %s, %s)",
                       (user_id, REPAIR, charger_id, when))
        self._apply(account_report(user_id, charger_id, "working", when, repair=True))

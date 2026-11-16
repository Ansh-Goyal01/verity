"""Sites, plans, channels, aliases, name search and audit views for both graph backends (W2.1, W2.5, W2.6).

``graph.py`` holds chargers, reports, legs and road segments; this module holds the rest of the schema as two mixins
with the same methods: ``MemoryStructure`` (dictionaries, the offline fallback) and ``Neo4jStructure`` (Cypher).
The mixins use only what their host class provides: ``_chargers`` / ``_reports`` (memory) or ``_run`` /
``chargers()`` (Neo4j).

Graph views are built so the two backends cannot disagree: each backend only FETCHES rows of one fixed shape, and
one pure function (``why_view`` / ``charger_view``) turns rows into nodes and edges.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from ..names import rank_hits, spoken_tokens, text_tokens
from ..places import normalise
from ..sites import assign_sites
from .channels import channel_of
from .graph_types import (
    AliasRec,
    AuditView,
    NameHit,
    PlanAvoidedRec,
    PlanBackupRec,
    PlanRec,
    PlanStopRec,
    SiteRec,
)

TEST_PREFIX = "TEST-"
REPORTS_PER_CHARGER = 5  # latest reports shown per charger in a view
PLANS_PER_CHARGER = 5  # latest plans shown per charger in a view
NAME_CANDIDATES = 50  # candidates fetched per name search, before the Python ranking
MAX_QUERY_WORDS = 12  # a long utterance must not exceed Lucene's clause limit (that error would switch Neo4j off)
FULLTEXT_INDEX = "charger_names"

_REPORTS_SUBQUERY = """\
CALL {
  WITH c
  OPTIONAL MATCH (src:Source)-[rep:REPORTED]->(c)
  OPTIONAL MATCH (src)-[:VIA]->(ch:Channel)
  WITH src, rep, ch ORDER BY rep.ts DESC, src.id LIMIT $per_charger
  RETURN collect(CASE WHEN src IS NULL THEN null ELSE {source: src.id, channel: ch.id, status: rep.status,
                                                       t: rep.t, is_simulated: coalesce(rep.is_simulated, false)} END) AS reports
}"""

WHY_CYPHER = f"""\
MATCH (p:Plan {{id: $plan}})
OPTIONAL MATCH (p)-[r:STOPS_AT|BACKUP_FOR|AVOIDED]->(c:Charger)
OPTIONAL MATCH (c)-[:AT]->(s:Site)
OPTIONAL MATCH (c)-[:RUN_BY]->(o:Operator)
{_REPORTS_SUBQUERY}
RETURN properties(p) AS plan, type(r) AS rel, properties(r) AS rel_props,
       c {{.id, .name, .lat, .lon, .operator, .site_type}} AS charger, s.id AS site, o.id AS operator, reports"""

CHARGER_CYPHER = f"""\
MATCH (c:Charger {{id: $id}})
OPTIONAL MATCH (c)-[:AT]->(s:Site)
OPTIONAL MATCH (c)-[:RUN_BY]->(o:Operator)
CALL {{ WITH c OPTIONAL MATCH (c)-[:AT]->(:Site)<-[:AT]-(n:Charger) WHERE n <> c RETURN collect(n.id) AS same_site }}
{_REPORTS_SUBQUERY}
CALL {{ WITH c OPTIONAL MATCH (a:Alias)-[:NAMES]->(c) RETURN collect(a.text) AS aliases }}
CALL {{
  WITH c
  OPTIONAL MATCH (p:Plan)-[r:STOPS_AT|BACKUP_FOR|AVOIDED]->(c)
  WITH p, r ORDER BY p.created_ts DESC, p.id LIMIT $plans
  RETURN collect(CASE WHEN p IS NULL THEN null ELSE {{plan: p.id, decision: p.decision, vehicle_id: p.vehicle_id,
                                                     rel: type(r), props: properties(r)}} END) AS plans
}}
RETURN c {{.id, .name, .lat, .lon, .operator, .site_type}} AS charger, s.id AS site, o.id AS operator,
       same_site, reports, aliases, plans"""

SEARCH_CYPHER = (
    "CALL db.index.fulltext.queryNodes('charger_names', $q, {limit: $limit}) YIELD node, score "
    "UNWIND CASE WHEN node:Charger THEN [node] ELSE [(node)-[:NAMES]->(c:Charger) | c] END AS ch "  # an alias names EVERY charger it points at
    "RETURN ch.id AS id, ch.name AS name, coalesce(node.name, node.text) AS matched, score "
    "ORDER BY score DESC, id")


# -- pure helpers --------------------------------------------------------------------------------------------------
def build_site_recs(chargers: Sequence[Any]) -> list[SiteRec]:
    """150 m clusters over the directory (test chargers excluded). Type = commonest member type, ties alphabetical."""
    real = [c for c in chargers if not c.id.startswith(TEST_PREFIX)]
    site_of = assign_sites([(c.id, c.lat, c.lon) for c in real])
    members: dict[str, list[Any]] = defaultdict(list)
    for c in real:
        members[site_of[c.id]].append(c)
    out = []
    for sid, cs in members.items():
        known = Counter(c.site_type for c in cs if c.site_type and c.site_type != "unknown")
        site_type = min(known, key=lambda k: (-known[k], k)) if known else "unknown"
        out.append(SiteRec(sid, site_type, tuple(sorted(c.id for c in cs))))
    return sorted(out, key=lambda s: s.site_id)


def lucene_query(spoken: str) -> str:
    """The distinctive words, each escaped, OR-ed: the index only retrieves; ``rank_hits`` decides."""
    def esc(word: str) -> str:
        return "".join("\\" + ch if ch in '+-&|!(){}[]^"~*?:\\/' else ch for ch in word)
    return " OR ".join(esc(t) for t in spoken_tokens(spoken)[:MAX_QUERY_WORDS])


def alias_id(text: str) -> str:
    return normalise(text)


def _node(label: str, ident: str, **props: Any) -> dict:
    return {"id": f"{label}:{ident}", "label": label, "props": props}


def _edge(a: str, b: str, kind: str, **props: Any) -> dict:
    return {"from": a, "to": b, "type": kind, "props": props}


def _add_charger(nodes: dict, edges: dict, ch: dict, site: str | None, operator: str | None, reports: list[dict]) -> str:
    node = _node("Charger", ch["id"], name=ch["name"], lat=ch["lat"], lon=ch["lon"], operator=ch["operator"] or "",
                 site_type=ch["site_type"] if ch["site_type"] is not None else "unknown")
    cid = node["id"]
    nodes[cid] = node
    if site is not None:
        nodes[f"Site:{site}"] = _node("Site", site)
        edges[(cid, f"Site:{site}", "AT", "")] = _edge(cid, f"Site:{site}", "AT")
    if operator:
        nodes[f"Operator:{operator}"] = _node("Operator", operator)
        edges[(cid, f"Operator:{operator}", "RUN_BY", "")] = _edge(cid, f"Operator:{operator}", "RUN_BY")
    for rep in (r for r in reports if r):
        sid = f"Source:{rep['source']}"
        nodes[sid] = _node("Source", rep["source"], is_simulated=rep["is_simulated"])
        edges[(sid, cid, "REPORTED", rep["t"])] = _edge(sid, cid, "REPORTED", status=rep["status"], t=rep["t"],
                                                        is_simulated=rep["is_simulated"])
        if rep["channel"]:
            nodes[f"Channel:{rep['channel']}"] = _node("Channel", rep["channel"])
            edges[(sid, f"Channel:{rep['channel']}", "VIA", "")] = _edge(sid, f"Channel:{rep['channel']}", "VIA")
    return cid


def _finish(nodes: dict, edges: dict, cypher: str, started: float, backend: str, executed: bool) -> AuditView:
    return AuditView(tuple(sorted(nodes.values(), key=lambda n: n["id"])),
                     tuple(sorted(edges.values(), key=lambda e: (e["from"], e["to"], e["type"], str(e["props"])))),
                     cypher, round((time.perf_counter() - started) * 1000.0, 2), backend, executed)


def why_view(rows: Sequence[dict], started: float, backend: str, executed: bool) -> AuditView | None:
    """plan -> stops, backups, avoided chargers -> reports -> sources -> channel, plus each charger's site and operator."""
    if not rows:
        return None
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    plan = rows[0]["plan"]
    pid = f"Plan:{plan['id']}"
    nodes[pid] = _node("Plan", plan["id"], vehicle_id=plan.get("vehicle_id"), decision=plan.get("decision"),
                       confidence=plan.get("confidence"), n_runs=plan.get("n_runs"),
                       avoided_total=plan.get("avoided_total"), is_simulated=plan.get("is_simulated"))
    for row in rows:
        if row["charger"] is None:
            continue
        cid = _add_charger(nodes, edges, row["charger"], row["site"], row["operator"], row["reports"])
        props = dict(row["rel_props"] or {})
        edges[(pid, cid, row["rel"], str(sorted(props.items())))] = _edge(pid, cid, row["rel"], **props)
    return _finish(nodes, edges, WHY_CYPHER, started, backend, executed)


def charger_view(row: dict | None, started: float, backend: str, executed: bool) -> AuditView | None:
    """One charger's neighbourhood: operator, site and its other chargers, latest reports with channels, aliases, plans."""
    if row is None:
        return None
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    cid = _add_charger(nodes, edges, row["charger"], row["site"], row["operator"], row["reports"])
    for other in row["same_site"]:
        nodes[f"Charger:{other}"] = _node("Charger", other)
        edges[(f"Charger:{other}", f"Site:{row['site']}", "AT", "")] = _edge(f"Charger:{other}", f"Site:{row['site']}", "AT")
    for text in row["aliases"]:
        aid = f"Alias:{alias_id(text)}"
        nodes[aid] = _node("Alias", alias_id(text), text=text)
        edges[(aid, cid, "NAMES", "")] = _edge(aid, cid, "NAMES")
    for p in (p for p in row["plans"] if p):
        pid = f"Plan:{p['plan']}"
        nodes[pid] = _node("Plan", p["plan"], decision=p["decision"], vehicle_id=p["vehicle_id"])
        edges[(pid, cid, p["rel"], str(sorted(p["props"].items())))] = _edge(pid, cid, p["rel"], **p["props"])
    return _finish(nodes, edges, CHARGER_CYPHER, started, backend, executed)


def _plan_rels(plan: PlanRec) -> list[tuple[str, str, dict]]:
    """(relationship type, charger id, properties) for every edge a plan has."""
    return ([("STOPS_AT", s.charger_id, {"order": s.order}) for s in plan.stops]
            + [("BACKUP_FOR", b.charger_id, {"stop": b.stop, "p_reach": b.p_reach, "p_works": b.p_works}) for b in plan.backups]
            + [("AVOIDED", a.charger_id, {"stop": a.stop, "reason": a.reason}) for a in plan.avoided])


# -- in-memory ---------------------------------------------------------------------------------------------------
class MemoryStructure:
    """Mixin for ``InMemoryGraph`` (needs ``_chargers`` and ``_reports``)."""

    _chargers: dict
    _reports: dict

    def _init_structure(self) -> None:
        self._plans: dict[str, PlanRec] = {}
        self._aliases: dict[tuple[str, str], AliasRec] = {}

    def sync_sites(self) -> None:
        return None  # sites are derived from the chargers on every read

    def sites(self) -> list[SiteRec]:
        return build_site_recs(list(self._chargers.values()))

    def add_plan(self, plan: PlanRec) -> None:
        self._plans[plan.plan_id] = plan

    def plan(self, plan_id: str) -> PlanRec | None:
        return self._plans.get(plan_id)

    def plan_ids(self) -> list[str]:
        return sorted(self._plans)

    def sweep_plans(self, keep: int, max_age_s: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        newest = sorted(list(self._plans.values()), key=lambda p: (-p.created_ts, p.plan_id))
        for i, p in enumerate(newest):
            if i >= keep or now - p.created_ts > max_age_s:
                self._plans.pop(p.plan_id, None)

    def add_aliases(self, aliases: Sequence[AliasRec]) -> None:
        for a in aliases:
            if a.charger_id not in self._chargers:
                raise KeyError(f"unknown charger {a.charger_id!r}")
        for a in aliases:
            self._aliases[(alias_id(a.text), a.charger_id)] = a

    def aliases(self) -> list[AliasRec]:
        return sorted(self._aliases.values(), key=lambda a: (alias_id(a.text), a.charger_id))

    def search_names(self, spoken: str, limit: int = 3) -> list[NameHit]:
        wanted = set(spoken_tokens(spoken))
        if not wanted:
            return []
        cands = [(c.id, c.name, c.name) for c in list(self._chargers.values()) if wanted & text_tokens(c.name)]
        cands += [(a.charger_id, self._chargers[a.charger_id].name, a.text) for a in list(self._aliases.values())
                  if wanted & text_tokens(a.text)]
        return rank_hits(spoken, cands)[:limit]

    def _reports_of(self, charger_id: str) -> list[dict]:
        mine = [r for r in list(self._reports.values()) if r.charger_id == charger_id]
        mine = sorted(mine, key=lambda r: (-r.t.timestamp(), r.source_id))[:REPORTS_PER_CHARGER]
        return [{"source": r.source_id, "channel": channel_of(r.source_id), "status": r.status, "t": r.t.isoformat(),
                 "is_simulated": r.is_simulated} for r in mine]

    def _charger_row(self, charger_id: str, sites: Sequence[SiteRec]) -> tuple[dict, str | None, str | None]:
        c = self._chargers[charger_id]
        site = next((s.site_id for s in sites if charger_id in s.charger_ids), None)
        row = {"id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon, "operator": c.operator, "site_type": c.site_type}
        return row, site, (c.operator or None)

    def why(self, plan_id: str) -> AuditView | None:
        started = time.perf_counter()
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        head = {"id": plan.plan_id, "vehicle_id": plan.vehicle_id, "decision": plan.decision, "confidence": plan.confidence,
                "n_runs": plan.n_runs, "avoided_total": plan.avoided_total, "is_simulated": plan.is_simulated}
        rows, sites = [], self.sites()  # sites once per call: rebuilding them per charger is O(n^2) each time
        for rel, cid, props in _plan_rels(plan):
            if cid in self._chargers:
                ch, site, operator = self._charger_row(cid, sites)
                rows.append({"plan": head, "rel": rel, "rel_props": props, "charger": ch, "site": site,
                             "operator": operator, "reports": self._reports_of(cid)})
        empty = {"plan": head, "rel": None, "rel_props": None, "charger": None, "site": None, "operator": None, "reports": []}
        return why_view(rows or [empty], started, "memory", False)

    def charger_neighbourhood(self, charger_id: str) -> AuditView | None:
        started = time.perf_counter()
        if charger_id not in self._chargers:
            return None
        sites = self.sites()
        ch, site, operator = self._charger_row(charger_id, sites)
        same = next((s.charger_ids for s in sites if charger_id in s.charger_ids), ())
        plans = []
        for p in sorted(list(self._plans.values()), key=lambda p: (-p.created_ts, p.plan_id)):
            plans += [{"plan": p.plan_id, "decision": p.decision, "vehicle_id": p.vehicle_id, "rel": rel, "props": props}
                      for rel, cid, props in _plan_rels(p) if cid == charger_id]
        row = {"charger": ch, "site": site, "operator": operator, "same_site": [i for i in same if i != charger_id],
               "reports": self._reports_of(charger_id), "plans": plans[:PLANS_PER_CHARGER],
               "aliases": sorted({a.text for a in list(self._aliases.values()) if a.charger_id == charger_id})}
        return charger_view(row, started, "memory", False)

    def structure_counts(self) -> tuple[int, int]:
        """(nodes, relationships) for Site, SiteType, Channel, Plan and Alias, the way the Neo4j writes create them."""
        sites = self.sites()
        sources = {r.source_id for r in list(self._reports.values())}
        plans = list(self._plans.values())
        aliases = list(self._aliases.values())
        nodes = (len(sites) + len({s.site_type for s in sites}) + len({channel_of(s) for s in sources}) + len(plans)
                 + len({alias_id(a.text) for a in aliases}))
        rels = (sum(len(s.charger_ids) for s in sites) + len(sites) + len(sources)
                + sum(len(p.stops) + len(p.backups) + len(p.avoided) for p in plans) + len(aliases))
        return nodes, rels


# -- Neo4j -------------------------------------------------------------------------------------------------------
class Neo4jStructure:
    """Mixin for ``Neo4jGraph`` (needs ``_run`` and ``chargers()``)."""

    def ensure_name_index(self) -> None:
        self._run(f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS FOR (n:Charger|Alias) ON EACH [n.name, n.text]")

    def await_indexes(self) -> None:
        self._run("CALL db.awaitIndexes(30)")

    def sync_sites(self) -> None:
        """Rebuild the physical Sites (derived data, like the road network): drop the old ones, write the 150 m clusters."""
        sites = build_site_recs(self.chargers())
        self._run("MATCH (s:Site) DETACH DELETE s")
        self._run("MATCH (t:SiteType) WHERE NOT (t)--() DELETE t")
        rows = [{"id": s.site_id, "site_type": s.site_type, "chargers": list(s.charger_ids)} for s in sites]
        if rows:
            self._run("UNWIND $rows AS row MERGE (s:Site {id: row.id}) SET s.site_type = row.site_type "
                      "MERGE (t:SiteType {id: row.site_type}) MERGE (s)-[:OF_TYPE]->(t) "
                      "WITH s, row UNWIND row.chargers AS cid MATCH (c:Charger {id: cid}) MERGE (c)-[:AT]->(s)", rows=rows)

    def sites(self) -> list[SiteRec]:
        rows = self._run("MATCH (s:Site) OPTIONAL MATCH (c:Charger)-[:AT]->(s) WITH s, c ORDER BY c.id "
                         "RETURN s.id AS id, s.site_type AS site_type, collect(c.id) AS chargers ORDER BY id")
        return [SiteRec(r["id"], r["site_type"], tuple(r["chargers"])) for r in rows]

    def add_plan(self, plan: PlanRec) -> None:
        self._run(
            "MERGE (p:Plan {id: $id}) SET p.vehicle_id = $vehicle_id, p.decision = $decision, p.confidence = $confidence, "
            "p.n_runs = $n_runs, p.created_ts = $ts, p.is_simulated = $sim, p.avoided_total = $avoided_total "
            "WITH p OPTIONAL MATCH (p)-[old:STOPS_AT|BACKUP_FOR|AVOIDED]->() DELETE old "
            "WITH DISTINCT p "
            "CALL { WITH p UNWIND $stops AS s MATCH (c:Charger {id: s.cid}) MERGE (p)-[:STOPS_AT {order: s.order}]->(c) "
            "RETURN count(*) AS a } "
            "CALL { WITH p UNWIND $backups AS b MATCH (c:Charger {id: b.cid}) MERGE (p)-[r:BACKUP_FOR {stop: b.stop}]->(c) "
            "SET r.p_reach = b.p_reach, r.p_works = b.p_works RETURN count(*) AS b2 } "
            "CALL { WITH p UNWIND $avoided AS v MATCH (c:Charger {id: v.cid}) MERGE (p)-[r:AVOIDED {stop: v.stop}]->(c) "
            "SET r.reason = v.reason RETURN count(*) AS c2 } RETURN p.id AS id",
            id=plan.plan_id, vehicle_id=plan.vehicle_id, decision=plan.decision, confidence=plan.confidence,
            n_runs=plan.n_runs, ts=plan.created_ts, sim=plan.is_simulated, avoided_total=plan.avoided_total,
            stops=[{"order": s.order, "cid": s.charger_id} for s in plan.stops],
            backups=[{"stop": b.stop, "cid": b.charger_id, "p_reach": b.p_reach, "p_works": b.p_works} for b in plan.backups],
            avoided=[{"stop": a.stop, "cid": a.charger_id, "reason": a.reason} for a in plan.avoided])

    def plan(self, plan_id: str) -> PlanRec | None:
        head = self._run("MATCH (p:Plan {id: $id}) RETURN properties(p) AS p", id=plan_id)
        if not head:
            return None
        p = head[0]["p"]

        def rels(kind: str) -> list[dict]:
            return self._run(f"MATCH (:Plan {{id: $id}})-[r:{kind}]->(c:Charger) RETURN properties(r) AS r, c.id AS cid", id=plan_id)

        return PlanRec(
            plan_id, p["vehicle_id"], p["decision"], p["confidence"], p["n_runs"], p["created_ts"],
            tuple(sorted(PlanStopRec(r["r"]["order"], r["cid"]) for r in rels("STOPS_AT"))),
            tuple(sorted(PlanBackupRec(r["r"]["stop"], r["cid"], r["r"]["p_reach"], r["r"]["p_works"]) for r in rels("BACKUP_FOR"))),
            tuple(sorted(PlanAvoidedRec(r["r"]["stop"], r["cid"], r["r"]["reason"]) for r in rels("AVOIDED"))),
            p.get("avoided_total", 0), p.get("is_simulated", True))

    def plan_ids(self) -> list[str]:
        return [r["id"] for r in self._run("MATCH (p:Plan) RETURN p.id AS id ORDER BY id")]

    def sweep_plans(self, keep: int, max_age_s: float, now: float | None = None) -> None:
        cutoff = (time.time() if now is None else now) - max_age_s
        self._run("MATCH (p:Plan) WHERE p.created_ts < $cutoff DETACH DELETE p", cutoff=cutoff)
        self._run("MATCH (p:Plan) WITH p ORDER BY p.created_ts DESC, p.id SKIP $keep DETACH DELETE p", keep=keep)

    def add_aliases(self, aliases: Sequence[AliasRec]) -> None:
        rows = [{"id": alias_id(a.text), "text": a.text, "cid": a.charger_id, "origin": a.origin} for a in aliases]
        if not rows:
            return
        out = self._run("UNWIND $rows AS row MATCH (c:Charger {id: row.cid}) MERGE (a:Alias {id: row.id}) "
                        "SET a.text = row.text MERGE (a)-[n:NAMES]->(c) SET n.origin = row.origin RETURN count(n) AS n",
                        rows=rows)
        if (out[0]["n"] if out else 0) != len(rows):
            raise KeyError("an alias refers to a charger that is not in the graph")
        self.await_indexes()

    def aliases(self) -> list[AliasRec]:
        rows = self._run("MATCH (a:Alias)-[n:NAMES]->(c:Charger) RETURN a.text AS text, c.id AS cid, n.origin AS origin")
        return sorted((AliasRec(r["text"], r["cid"], r["origin"]) for r in rows),
                      key=lambda a: (alias_id(a.text), a.charger_id))

    def search_names(self, spoken: str, limit: int = 3) -> list[NameHit]:
        q = lucene_query(spoken)
        if not q:
            return []
        rows = self._run(SEARCH_CYPHER, q=q, limit=NAME_CANDIDATES)
        return rank_hits(spoken, ((r["id"], r["name"], r["matched"]) for r in rows))[:limit]

    def why(self, plan_id: str) -> AuditView | None:
        started = time.perf_counter()
        rows = self._run(WHY_CYPHER, plan=plan_id, per_charger=REPORTS_PER_CHARGER)
        return why_view(rows, started, "neo4j", True)

    def charger_neighbourhood(self, charger_id: str) -> AuditView | None:
        started = time.perf_counter()
        rows = self._run(CHARGER_CYPHER, id=charger_id, per_charger=REPORTS_PER_CHARGER, plans=PLANS_PER_CHARGER)
        return charger_view(rows[0] if rows else None, started, "neo4j", True)

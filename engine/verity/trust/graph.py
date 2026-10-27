"""Charger graph behind one interface: an in-memory implementation and a Neo4j implementation.

Schema (CLAUDE.md): nodes Charger, Operator, Site, HardwareModel, VehicleModel, Source, RoadSegment;
relationships (Source)-[:REPORTED {status, t}]->(Charger), (Charger)-[:RUN_BY]->(Operator),
(Charger)-[:AT]->(Site), (Charger)-[:IS]->(HardwareModel),
(VehicleModel)-[:WORKS_WITH {success_rate, n}]->(HardwareModel),
(Charger)-[:LEG {soc_band, p_reach, minutes}]->(Charger), (RoadSegment)-[:ALTERNATE {p_reach}]->(Charger).
A LEG is stored per battery band, never as one fixed probability.

The planner only talks to ``ChargerGraph``; ``InMemoryGraph`` is the default (offline, deterministic) and
``Neo4jGraph`` stores the same data in AuraDB when credentials exist. Site nodes are keyed by site type
for now because there is no site directory yet. Credentials come from ``.env`` and are never printed.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..paths import REPO_ROOT
from ..schemas import ChargerRecord, Report

NODE_LABELS = ("Charger", "Operator", "Site", "HardwareModel", "VehicleModel", "Source", "RoadSegment")
TEST_PREFIX = "TEST-"


@dataclass(frozen=True)
class Leg:
    """Travel between two chargers for one battery band (SoC percent at departure)."""

    from_id: str
    to_id: str
    soc_lo: int
    soc_hi: int
    p_reach: float
    minutes: float

    @property
    def soc_band(self) -> str:
        return f"{self.soc_lo}-{self.soc_hi}"


class ChargerGraph(Protocol):
    def add_charger(self, rec: ChargerRecord) -> None: ...
    def add_report(self, rep: Report) -> None: ...
    def chargers(self) -> list[ChargerRecord]: ...
    def reports(self, charger_id: str | None = None) -> list[Report]: ...
    def add_leg(self, leg: Leg) -> None: ...
    def legs_from(self, charger_id: str) -> list[Leg]: ...
    def add_alternate(self, segment_id: str, charger_id: str, p_reach: float) -> None: ...
    def alternates(self, segment_id: str) -> list[tuple[str, float]]: ...
    def add_works_with(self, vehicle_model: str, hardware_model: str, success_rate: float, n: int) -> None: ...
    def works_with(self, vehicle_model: str, hardware_model: str) -> tuple[float, int] | None: ...
    def co_reporting_pairs(self, min_shared: int = 4) -> list[tuple[str, str, int]]: ...


class InMemoryGraph:
    """Dictionary-backed graph with the same behaviour as the Neo4j one (default for tests and the API)."""

    def __init__(self) -> None:
        self._chargers: dict[str, ChargerRecord] = {}
        self._reports: list[Report] = []
        self._legs: dict[str, dict[tuple[str, str], Leg]] = defaultdict(dict)
        self._alternates: dict[str, dict[str, float]] = defaultdict(dict)
        self._works_with: dict[tuple[str, str], tuple[float, int]] = {}

    def add_charger(self, rec: ChargerRecord) -> None:
        self._chargers[rec.id] = rec

    def add_report(self, rep: Report) -> None:
        if rep.charger_id not in self._chargers:
            raise KeyError(f"unknown charger {rep.charger_id!r}")
        self._reports.append(rep)

    def chargers(self) -> list[ChargerRecord]:
        return list(self._chargers.values())

    def reports(self, charger_id: str | None = None) -> list[Report]:
        return [r for r in self._reports if charger_id is None or r.charger_id == charger_id]

    def add_leg(self, leg: Leg) -> None:
        self._legs[leg.from_id][(leg.to_id, leg.soc_band)] = leg  # one LEG per (pair, band): upsert

    def legs_from(self, charger_id: str) -> list[Leg]:
        return list(self._legs[charger_id].values())

    def add_alternate(self, segment_id: str, charger_id: str, p_reach: float) -> None:
        self._alternates[segment_id][charger_id] = p_reach

    def alternates(self, segment_id: str) -> list[tuple[str, float]]:
        return sorted(self._alternates[segment_id].items(), key=lambda kv: -kv[1])

    def add_works_with(self, vehicle_model: str, hardware_model: str, success_rate: float, n: int) -> None:
        self._works_with[(vehicle_model, hardware_model)] = (success_rate, n)

    def works_with(self, vehicle_model: str, hardware_model: str) -> tuple[float, int] | None:
        return self._works_with.get((vehicle_model, hardware_model))

    def co_reporting_pairs(self, min_shared: int = 4) -> list[tuple[str, str, int]]:
        by_source: dict[str, set[str]] = defaultdict(set)
        for r in self._reports:
            by_source[r.source_id].add(r.charger_id)
        ids = sorted(by_source)
        pairs = [(a, b, len(by_source[a] & by_source[b])) for i, a in enumerate(ids) for b in ids[i + 1:]]
        return sorted((p for p in pairs if p[2] >= min_shared), key=lambda p: (-p[2], p[0], p[1]))


def load_env(path: Path | None = None) -> dict[str, str]:
    """Minimal ``.env`` parser (KEY=VALUE lines). Values are returned, never printed or logged."""
    path = path or REPO_ROOT / ".env"
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


class Neo4jGraph:
    """Neo4j implementation of ``ChargerGraph``. Pass ``driver`` to inject a fake in tests."""

    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None, *,
                 driver: Any = None, database: str | None = None) -> None:
        if driver is None:
            from neo4j import GraphDatabase  # imported lazily: only needed when credentials exist

            driver = GraphDatabase.driver(uri, auth=(user, password))
        self._driver = driver
        self._database = database

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> Neo4jGraph | None:
        """A connected graph, or ``None`` when NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD are not all set."""
        env = {**load_env(env_path), **{k: v for k, v in os.environ.items() if k.startswith("NEO4J_")}}
        uri, user, password = env.get("NEO4J_URI"), env.get("NEO4J_USERNAME"), env.get("NEO4J_PASSWORD")
        return cls(uri, user, password) if uri and user and password else None

    def close(self) -> None:
        self._driver.close()

    def _run(self, query: str, **params: Any) -> list[dict]:
        with self._driver.session(database=self._database) as session:
            return [record.data() for record in session.run(query, **params)]

    def ensure_constraints(self) -> None:
        for label in NODE_LABELS:
            self._run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")

    def add_charger(self, rec: ChargerRecord) -> None:
        self._run(
            "MERGE (c:Charger {id:$id}) SET c.name=$name, c.lat=$lat, c.lon=$lon, c.source_ids=$source_ids, "
            "c.connectors=$connectors, c.operator=$operator, c.site_type=$site_type "
            "FOREACH (_ IN CASE WHEN $operator <> '' THEN [1] ELSE [] END | "
            "MERGE (o:Operator {id:$operator}) MERGE (c)-[:RUN_BY]->(o)) "
            "FOREACH (_ IN CASE WHEN $site_type <> '' THEN [1] ELSE [] END | "
            "MERGE (s:Site {id:$site_type}) MERGE (c)-[:AT]->(s))",
            id=rec.id, name=rec.name, lat=rec.lat, lon=rec.lon, source_ids=rec.source_ids,
            connectors=json.dumps([c.model_dump() for c in rec.connectors]), operator=rec.operator,
            site_type=rec.site_type,
        )

    def add_report(self, rep: Report) -> None:
        self._run(
            "MATCH (c:Charger {id:$charger_id}) MERGE (s:Source {id:$source_id}) "
            "CREATE (s)-[:REPORTED {status:$status, t:$t, evidence:$evidence}]->(c)",
            charger_id=rep.charger_id, source_id=rep.source_id, status=rep.status, t=rep.t.isoformat(),
            evidence=rep.evidence,
        )

    def chargers(self) -> list[ChargerRecord]:
        rows = self._run("MATCH (c:Charger) RETURN c.id AS id, c.name AS name, c.lat AS lat, c.lon AS lon, "
                         "c.operator AS operator, c.site_type AS site_type, c.connectors AS connectors, "
                         "c.source_ids AS source_ids")
        return [ChargerRecord(id=r["id"], name=r["name"], lat=r["lat"], lon=r["lon"], operator=r["operator"] or "",
                              site_type=r["site_type"] or "unknown", connectors=json.loads(r["connectors"] or "[]"),
                              source_ids=r["source_ids"] or []) for r in rows]

    def reports(self, charger_id: str | None = None) -> list[Report]:
        rows = self._run("MATCH (s:Source)-[r:REPORTED]->(c:Charger) WHERE $cid IS NULL OR c.id = $cid "
                         "RETURN c.id AS charger_id, s.id AS source_id, r.status AS status, r.t AS t, "
                         "r.evidence AS evidence", cid=charger_id)
        return [Report(**row) for row in rows]

    def add_leg(self, leg: Leg) -> None:
        self._run("MATCH (a:Charger {id:$a}), (b:Charger {id:$b}) MERGE (a)-[l:LEG {soc_band:$band}]->(b) "
                  "SET l.p_reach=$p, l.minutes=$m", a=leg.from_id, b=leg.to_id, band=leg.soc_band,
                  p=leg.p_reach, m=leg.minutes)

    def legs_from(self, charger_id: str) -> list[Leg]:
        rows = self._run("MATCH (a:Charger {id:$a})-[l:LEG]->(b:Charger) RETURN a.id AS a, b.id AS b, "
                         "l.soc_band AS band, l.p_reach AS p, l.minutes AS m", a=charger_id)
        legs = []
        for r in rows:
            lo, hi = (int(x) for x in r["band"].split("-"))
            legs.append(Leg(r["a"], r["b"], lo, hi, r["p"], r["m"]))
        return legs

    def add_alternate(self, segment_id: str, charger_id: str, p_reach: float) -> None:
        self._run("MATCH (c:Charger {id:$cid}) MERGE (rs:RoadSegment {id:$seg}) MERGE (rs)-[a:ALTERNATE]->(c) "
                  "SET a.p_reach=$p", cid=charger_id, seg=segment_id, p=p_reach)

    def alternates(self, segment_id: str) -> list[tuple[str, float]]:
        rows = self._run("MATCH (rs:RoadSegment {id:$seg})-[a:ALTERNATE]->(c:Charger) "
                         "RETURN c.id AS id, a.p_reach AS p ORDER BY p DESC", seg=segment_id)
        return [(r["id"], r["p"]) for r in rows]

    def add_works_with(self, vehicle_model: str, hardware_model: str, success_rate: float, n: int) -> None:
        self._run("MERGE (v:VehicleModel {id:$v}) MERGE (h:HardwareModel {id:$h}) "
                  "MERGE (v)-[w:WORKS_WITH]->(h) SET w.success_rate=$rate, w.n=$n",
                  v=vehicle_model, h=hardware_model, rate=success_rate, n=n)

    def works_with(self, vehicle_model: str, hardware_model: str) -> tuple[float, int] | None:
        rows = self._run("MATCH (:VehicleModel {id:$v})-[w:WORKS_WITH]->(:HardwareModel {id:$h}) "
                         "RETURN w.success_rate AS rate, w.n AS n", v=vehicle_model, h=hardware_model)
        return (rows[0]["rate"], rows[0]["n"]) if rows else None

    def co_reporting_pairs(self, min_shared: int = 4) -> list[tuple[str, str, int]]:
        rows = self._run("MATCH (a:Source)-[:REPORTED]->(c:Charger)<-[:REPORTED]-(b:Source) WHERE a.id < b.id "
                         "WITH a.id AS a, b.id AS b, count(DISTINCT c) AS shared WHERE shared >= $k "
                         "RETURN a, b, shared ORDER BY shared DESC", k=min_shared)
        return [(r["a"], r["b"], r["shared"]) for r in rows]

    def delete_test_data(self, prefix: str = TEST_PREFIX) -> None:
        """Delete only nodes whose id starts with ``prefix`` (the smoke test's clearly fake data)."""
        if not prefix:
            raise ValueError("refusing to delete without a prefix")
        self._run("MATCH (n) WHERE n.id STARTS WITH $prefix DETACH DELETE n", prefix=prefix)

"""Graph interface: in-memory behaviour, and the Neo4j implementation driven through an injected fake driver.

No Neo4j server is available in CI or overnight, so the Cypher is only checked for shape here; it still
has to be exercised once against a real AuraDB instance (scripts/neo4j_smoke_test.py).
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from verity.paths import REPO_ROOT
from verity.schemas import ChargerRecord, Report
from verity.trust.graph import NODE_LABELS, InMemoryGraph, Leg, Neo4jGraph, load_env

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 27, 10, 0, tzinfo=IST)
PROTOCOL_METHODS = ("add_charger", "add_report", "chargers", "reports", "add_leg", "legs_from", "add_alternate",
                    "alternates", "add_works_with", "works_with", "co_reporting_pairs")


def charger(i: int, op: str = "OpA") -> ChargerRecord:
    return ChargerRecord(id=f"C{i}", name=f"c{i}", lat=28.5, lon=77.4, operator=op, site_type="mall",
                         connectors=[{"type": "CCS2", "kw": 60}])


def report(cid: str, sid: str, status: str = "working", minutes: int = 0) -> Report:
    return Report(charger_id=cid, source_id=sid, status=status, t=T0 + timedelta(minutes=minutes))


# --- in-memory ---------------------------------------------------------------------------------------


def test_in_memory_graph_stores_chargers_reports_and_rejects_unknown_chargers() -> None:
    g = InMemoryGraph()
    g.add_charger(charger(1))
    g.add_report(report("C1", "s1"))
    assert [c.id for c in g.chargers()] == ["C1"] and len(g.reports("C1")) == 1 and g.reports("C2") == []
    with pytest.raises(KeyError):
        g.add_report(report("C9", "s1"))


def test_legs_are_stored_per_battery_band_and_upserted() -> None:
    g = InMemoryGraph()
    g.add_leg(Leg("A", "B", 20, 40, 0.30, 80))
    g.add_leg(Leg("A", "B", 40, 60, 0.80, 80))
    g.add_leg(Leg("A", "B", 40, 60, 0.85, 82))  # same band: replaces, does not duplicate
    legs = sorted(g.legs_from("A"), key=lambda leg: leg.soc_lo)
    assert [(l.soc_band, l.p_reach) for l in legs] == [("20-40", 0.30), ("40-60", 0.85)]


def test_alternates_and_works_with_round_trip() -> None:
    g = InMemoryGraph()
    g.add_alternate("seg-1", "C1", 0.7)
    g.add_alternate("seg-1", "C2", 0.9)
    assert g.alternates("seg-1") == [("C2", 0.9), ("C1", 0.7)] and g.alternates("none") == []
    g.add_works_with("model-x", "hw-y", 0.6, 12)
    assert g.works_with("model-x", "hw-y") == (0.6, 12) and g.works_with("model-x", "hw-z") is None


def test_co_reporting_pairs_count_shared_chargers() -> None:
    g = InMemoryGraph()
    for i in range(6):
        g.add_charger(charger(i))
    for cid in ("C0", "C1", "C2", "C3", "C4"):
        g.add_report(report(cid, "ring-1")); g.add_report(report(cid, "ring-2"))
    g.add_report(report("C5", "lone"))
    assert g.co_reporting_pairs(min_shared=4) == [("ring-1", "ring-2", 5)]
    assert g.co_reporting_pairs(min_shared=6) == []


# --- Neo4j through a fake driver ---------------------------------------------------------------------------


class FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


class FakeDriver:
    def __init__(self, canned: list[list[dict]] | None = None) -> None:
        self.log: list[tuple[str, dict]] = []
        self.canned = list(canned or [])
        self.closed = False

    def session(self, database: str | None = None) -> "FakeDriver":
        return self

    def __enter__(self) -> "FakeDriver":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, query: str, **params: object) -> list[FakeRecord]:
        self.log.append((query, params))
        return [FakeRecord(r) for r in (self.canned.pop(0) if self.canned else [])]

    def close(self) -> None:
        self.closed = True


def test_neo4j_graph_implements_the_whole_interface() -> None:
    assert all(callable(getattr(Neo4jGraph, m, None)) for m in PROTOCOL_METHODS)
    assert all(callable(getattr(InMemoryGraph, m, None)) for m in PROTOCOL_METHODS)


def test_constraints_cover_every_node_label_from_claude_md() -> None:
    drv = FakeDriver()
    Neo4jGraph(driver=drv).ensure_constraints()
    text = " ".join(q for q, _ in drv.log)
    assert len(drv.log) == len(NODE_LABELS) == 7
    assert all(f"(n:{label})" in text for label in NODE_LABELS) and "IF NOT EXISTS" in text


def test_add_charger_and_report_write_the_documented_relationships() -> None:
    drv = FakeDriver()
    g = Neo4jGraph(driver=drv)
    g.add_charger(charger(1))
    g.add_report(report("C1", "s1", "broken"))
    (q_charger, p_charger), (q_report, p_report) = drv.log
    assert "RUN_BY" in q_charger and "[:AT]" in q_charger and "MERGE (c:Charger" in q_charger
    assert p_charger["operator"] == "OpA" and json.loads(p_charger["connectors"]) == [{"type": "CCS2", "kw": 60.0}]
    assert "REPORTED" in q_report and p_report["status"] == "broken" and p_report["t"].startswith("2026-10-27T10:00")


def test_reports_and_legs_are_parsed_back_from_rows() -> None:
    drv = FakeDriver([
        [{"charger_id": "C1", "source_id": "s1", "status": "working", "t": T0.isoformat(), "evidence": None}],
        [{"a": "A", "b": "B", "band": "40-60", "p": 0.8, "m": 75.0}],
    ])
    g = Neo4jGraph(driver=drv)
    assert g.reports("C1")[0].status == "working"
    assert g.legs_from("A") == [Leg("A", "B", 40, 60, 0.8, 75.0)]


def test_leg_band_and_co_reporting_use_the_expected_cypher() -> None:
    drv = FakeDriver()
    g = Neo4jGraph(driver=drv)
    g.add_leg(Leg("A", "B", 40, 60, 0.8, 75.0))
    g.co_reporting_pairs(min_shared=4)
    assert "LEG {soc_band:$band}" in drv.log[0][0] and drv.log[0][1]["band"] == "40-60"
    assert "count(DISTINCT c)" in drv.log[1][0] and drv.log[1][1]["k"] == 4


def test_delete_test_data_is_prefix_scoped_and_refuses_an_empty_prefix() -> None:
    drv = FakeDriver()
    g = Neo4jGraph(driver=drv)
    g.delete_test_data()
    assert "STARTS WITH" in drv.log[0][0] and drv.log[0][1]["prefix"] == "TEST-"
    with pytest.raises(ValueError):
        g.delete_test_data("")


# --- credentials and the smoke-test script ---------------------------------------------------------------


def test_from_env_returns_none_without_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("# comment\nNEO4J_URI=\nNEO4J_USERNAME=\nNEO4J_PASSWORD=\n", encoding="utf-8")
    assert Neo4jGraph.from_env(env) is None and Neo4jGraph.from_env(tmp_path / "missing.env") is None


def test_load_env_parses_values_without_echoing_them(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    env = tmp_path / ".env"
    env.write_text('A=1\n# c\nB = "two"\nbad line\n', encoding="utf-8")
    assert load_env(env) == {"A": "1", "B": "two"}
    assert capsys.readouterr().out == ""


def test_smoke_script_skips_cleanly_when_credentials_are_missing(monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture) -> None:
    spec = importlib.util.spec_from_file_location("neo4j_smoke_test", REPO_ROOT / "scripts" / "neo4j_smoke_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.Neo4jGraph, "from_env", classmethod(lambda cls, env_path=None: None))
    assert module.main() == 0
    assert "SKIPPED" in capsys.readouterr().out

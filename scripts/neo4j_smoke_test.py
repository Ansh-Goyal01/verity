"""Neo4j smoke test: insert clearly fake chargers, query them back, delete them. Never prints secrets.

Reads NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD from .env. Without them it prints SKIPPED and exits 0,
so it is safe in scripts/run_all.py. Only nodes whose id starts with ``TEST-`` are ever deleted.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from verity.schemas import ChargerRecord, Report
from verity.trust.graph import TEST_PREFIX, Neo4jGraph

IST = timezone(timedelta(hours=5, minutes=30))


def main() -> int:
    graph = Neo4jGraph.from_env()
    if graph is None:
        print("SKIPPED: NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD are not all set in .env "
              "(copy .env.example to .env and fill them in)")
        return 0
    try:
        graph.ensure_constraints()
        graph.delete_test_data()  # clear leftovers of an interrupted earlier run
        for i in (1, 2, 3):
            graph.add_charger(ChargerRecord(id=f"{TEST_PREFIX}{i}", name=f"{TEST_PREFIX}{i} (fake charger)",
                                            lat=28.5 + i / 100, lon=77.4, operator=f"{TEST_PREFIX}OP",
                                            site_type=f"{TEST_PREFIX}SITE", source_ids=["smoke-test"]))
        now = datetime.now(IST)
        graph.add_report(Report(charger_id=f"{TEST_PREFIX}1", source_id=f"{TEST_PREFIX}SRC-1", status="working", t=now))
        graph.add_report(Report(charger_id=f"{TEST_PREFIX}2", source_id=f"{TEST_PREFIX}SRC-1", status="broken", t=now))
        found = [c for c in graph.chargers() if c.id.startswith(TEST_PREFIX)]
        reports = [r for r in graph.reports() if r.charger_id.startswith(TEST_PREFIX)]
        print(f"inserted and read back {len(found)} test chargers and {len(reports)} test reports:")
        for c in sorted(found, key=lambda c: c.id):
            print(f"  {c.id}  operator={c.operator}  site={c.site_type}")
        for r in reports:
            print(f"  {r.source_id} -> {r.charger_id}: {r.status}")
        ok = len(found) == 3 and len(reports) == 2
        graph.delete_test_data()
        left = [c for c in graph.chargers() if c.id.startswith(TEST_PREFIX)]
        print(f"test data deleted ({len(left)} left)")
        print("Neo4j smoke test:", "OK" if ok and not left else "FAILED")
        return 0 if ok and not left else 1
    except Exception as exc:  # report the class and message; the driver never echoes the password
        print(f"Neo4j smoke test FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        graph.close()


if __name__ == "__main__":
    sys.exit(main())

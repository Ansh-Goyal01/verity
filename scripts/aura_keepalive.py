"""Aura keep-alive: one tiny write so the AuraDB Free instance is not paused for inactivity.

Creates a throwaway ``_KeepAlive`` node and deletes it in the same query, so the graph is left unchanged
(no node or relationship counts move). Credentials come from .env via ``Neo4jGraph.from_env``; nothing
secret is printed. Appends one line per run to ``data/cache/aura_keepalive.log`` (gitignored).

Scheduled daily by Windows Task Scheduler (task ``VerityAuraKeepAlive``) until 2026-11-23. Exits without
contacting Aura once that date has passed, so a forgotten schedule does nothing.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from verity.trust.graph import Neo4jGraph

IST = timezone(timedelta(hours=5, minutes=30))
LAST_DAY = date(2026, 11, 23)
LOG_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "aura_keepalive.log"
QUERY = "CREATE (k:_KeepAlive {t: datetime()}) WITH k DELETE k RETURN 1 AS ok"


def _log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


def main() -> int:
    now = datetime.now(IST)
    stamp = now.isoformat(timespec="seconds")
    if now.date() > LAST_DAY:
        _log(f"{stamp} SKIPPED: past {LAST_DAY}, keep-alive no longer needed")
        return 0
    graph = Neo4jGraph.from_env()
    if graph is None:
        _log(f"{stamp} SKIPPED: NEO4J_* not set in .env")
        return 1
    try:
        rows = graph._run(QUERY)
        ok = bool(rows) and rows[0].get("ok") == 1
        _log(f"{stamp} {'OK' if ok else 'FAILED: unexpected result'}")
        return 0 if ok else 1
    except Exception as exc:  # the driver never echoes the password
        _log(f"{stamp} FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        graph.close()


if __name__ == "__main__":
    sys.exit(main())

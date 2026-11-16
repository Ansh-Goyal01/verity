"""Value types for the graph's structure beyond chargers and reports (W2.1, W2.5, W2.6).

Kept apart from ``graph.py`` so the two backends and the API can share them without importing each other.
Everything here is immutable data; nothing touches a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

# Why a charger was passed over as a backup (one rule, shared with the coverage-gap query: graph_build.avoided_reason).
AVOIDED_NO_REPORTS = "no_reports"
AVOIDED_LOW_P_WORKS = "low_p_works"


class SiteRec(NamedTuple):
    """One physical place: chargers within 150 m of each other (single linkage, ``sites.assign_sites``)."""

    site_id: str  # the smallest member charger id
    site_type: str  # the commonest type among members (ties: alphabetical); "unknown" when none is known
    charger_ids: tuple[str, ...]


class PlanStopRec(NamedTuple):
    order: int
    charger_id: str


class PlanBackupRec(NamedTuple):
    stop: int  # index of the planned stop this charger backs up
    charger_id: str
    p_reach: float
    p_works: float


class PlanAvoidedRec(NamedTuple):
    stop: int  # the stop whose backup window the charger lay in
    charger_id: str
    reason: str  # AVOIDED_NO_REPORTS | AVOIDED_LOW_P_WORKS


@dataclass(frozen=True)
class PlanRec:
    """A released plan as the graph stores it: (Plan)-[:STOPS_AT|BACKUP_FOR|AVOIDED]->(Charger)."""

    plan_id: str
    vehicle_id: str
    decision: str
    confidence: float
    n_runs: int
    created_ts: float  # epoch seconds: the sweep keeps the newest plans and drops the old ones
    stops: tuple[PlanStopRec, ...] = ()
    backups: tuple[PlanBackupRec, ...] = ()
    avoided: tuple[PlanAvoidedRec, ...] = ()
    avoided_total: int = 0  # how many were passed over before the cap on what is stored
    is_simulated: bool = True

    def __post_init__(self) -> None:
        # A plan's edges are a set: one canonical order, so a plan read back from Neo4j equals the one that was written.
        for name in ("stops", "backups", "avoided"):
            object.__setattr__(self, name, tuple(sorted(getattr(self, name))))


class AliasRec(NamedTuple):
    """A name a driver might say for one charger. ``origin`` says where it came from, so nothing is invented."""

    text: str
    charger_id: str
    origin: str  # "operator" | "corpus_landmark" | "alias_file"


class NameHit(NamedTuple):
    charger_id: str
    name: str  # the charger's own directory name
    matched: str  # the text that matched: the name itself or one alias
    score: float  # 0..1, share of the spoken words (generic ones ignored) found in ``matched``


@dataclass(frozen=True)
class AuditView:
    """A sub-graph for the UI plus the query that produced it. ``executed`` is False when the in-memory twin
    answered: ``cypher`` is then the query Neo4j WOULD have run, and ``query_ms`` times the Python twin."""

    nodes: tuple[dict, ...]
    edges: tuple[dict, ...]
    cypher: str
    query_ms: float
    backend: str
    executed: bool

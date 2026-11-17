"""Agreement edges between report sources (W2.4): the pure definition the graph's Cypher query must reproduce.

Two sources are joined when they report on many of the SAME chargers (Jaccard overlap of the chargers each one
reports on), say the same thing about them, and (optionally) post close together in time. Communities of at least
``min_size`` joined sources are flagged. This is the numpy detector's rule (``rings.detect_rings``) written over
(source, charger) verdicts, so the in-memory graph, Neo4j and the experiment agree by construction.

A source's verdict on a charger is the majority of its own reports there: working / occupied = 1, anything else = 0
(``source_trust.LABEL_OF``), ties count as working. No network, no database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import NamedTuple

POSITIVE_STATUSES = ("working", "occupied")  # same mapping as source_trust.LABEL_OF


class AgreementEdge(NamedTuple):
    a: str  # source ids, a < b
    b: str
    shared: int  # chargers both report on
    jaccard: float  # shared / (chargers of a + chargers of b - shared)


def verdicts_of(reports: Iterable[tuple[str, str, str]]) -> dict[tuple[str, str], bool]:
    """(source, charger, status) rows -> (source, charger) -> majority verdict (True = working)."""
    votes: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for source, charger, status in reports:
        votes[(source, charger)][0] += 1 if status in POSITIVE_STATUSES else 0
        votes[(source, charger)][1] += 1
    return {key: pos / total >= 0.5 for key, (pos, total) in votes.items()}


def agreement_edges(verdicts: Mapping[tuple[str, str], bool], *, min_shared: int = 4, min_jaccard: float = 0.5,
                    min_agree: float = 0.9) -> list[AgreementEdge]:
    """Edges where the sources share >= ``min_shared`` chargers, the Jaccard overlap is >= ``min_jaccard`` and they
    give the same verdict on >= ``min_agree`` of the shared chargers. Sorted (-shared, a, b)."""
    by_source: dict[str, dict[str, bool]] = defaultdict(dict)
    for (source, charger), verdict in verdicts.items():
        by_source[source][charger] = verdict
    ids = sorted(by_source)
    edges = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            common = by_source[a].keys() & by_source[b].keys()
            if len(common) < min_shared:
                continue
            jaccard = len(common) / (len(by_source[a]) + len(by_source[b]) - len(common))
            same = sum(by_source[a][c] == by_source[b][c] for c in common)
            if jaccard >= min_jaccard and same >= min_agree * len(common):
                edges.append(AgreementEdge(a, b, len(common), jaccard))
    return sorted(edges, key=lambda e: (-e.shared, e.a, e.b))

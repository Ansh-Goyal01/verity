"""Fake-report ring detection: accounts that report the same chargers, the same way, in a tight burst.

Why this signature: honest users scatter over many chargers and over many days; a coordinated ring
(e.g. accounts posting "broken" on a rival network) shares most of its target set (high Jaccard), agrees
on the label, and posts within hours of each other. A pair of sources becomes an edge only when ALL of:
>= ``min_shared`` common chargers, Jaccard >= ``min_jaccard``, label agreement >= ``min_agree`` on the
shared chargers, and mean report-time gap <= ``max_hours`` on them. Connected components of at least
``min_size`` sources are flagged. This is the same co-reporting graph that a Neo4j community-detection
call would run on (see ``graph.co_reporting_pairs``); here it is plain numpy so it works offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from .source_trust import ReportSet


@dataclass(frozen=True)
class RingResult:
    groups: tuple[tuple[int, ...], ...]  # source indices per detected ring
    flagged: tuple[int, ...]

    def flagged_ids(self, source_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(source_ids[i] for i in self.flagged)


def detect_rings(
    rs: ReportSet,
    *,
    min_shared: int = 4,
    min_jaccard: float = 0.5,
    min_agree: float = 0.9,
    max_hours: float = 12.0,
    min_size: int = 3,
    ignore: Iterable[int] = (),
) -> RingResult:
    n_s, n_c = len(rs.source_ids), len(rs.charger_ids)
    keep = ~np.isin(rs.source, list(ignore))
    src, ch = rs.source[keep], rs.charger[keep]
    count = np.zeros((n_s, n_c))
    label_sum = np.zeros((n_s, n_c))
    age_sum = np.zeros((n_s, n_c))
    np.add.at(count, (src, ch), 1.0)
    np.add.at(label_sum, (src, ch), rs.label[keep].astype(float))
    np.add.at(age_sum, (src, ch), rs.age_days[keep])
    present = count > 0
    safe = np.maximum(count, 1.0)
    positive = present & (label_sum / safe >= 0.5)
    negative = present & ~positive
    age_mean = age_sum / safe

    pf = present.astype(float)
    shared = pf @ pf.T
    same = positive.astype(float) @ positive.T.astype(float) + negative.astype(float) @ negative.T.astype(float)
    size = pf.sum(axis=1)
    jaccard = shared / np.maximum(size[:, None] + size[None, :] - shared, 1.0)
    candidate = (shared >= min_shared) & (jaccard >= min_jaccard) & (same >= min_agree * shared)
    candidate = np.triu(candidate, k=1)

    parent = list(range(n_s))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in zip(*np.nonzero(candidate)):
        both = present[i] & present[j]
        gap_hours = 24.0 * float(np.abs(age_mean[i, both] - age_mean[j, both]).mean())
        if gap_hours <= max_hours:
            parent[find(int(i))] = find(int(j))

    comps: dict[int, list[int]] = {}
    for s in range(n_s):
        if size[s] > 0:
            comps.setdefault(find(s), []).append(s)
    groups = tuple(tuple(c) for c in comps.values() if len(c) >= min_size)
    return RingResult(groups, tuple(sorted(s for g in groups for s in g)))

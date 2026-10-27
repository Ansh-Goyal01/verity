"""Starting charger-reliability priors by operator, site type and region, with their sources.

Every number comes from the evidence table in docs/PLAN.md section 3 (the PDF plan, pages 4-6). That
table says its sources were found by web search on 26 Oct 2026 and must be re-checked before any figure
goes on a slide, so ALL values here are APPROXIMATE - verify. They are only starting points (Beta
priors) that real reports and usage traces quickly outweigh; they are never shown as facts.

How they combine: each applicable piece of evidence is a (p_works, strength) pair, where ``strength`` is
a pseudo-count of how much it should count (small sample or indirect = small). The prior is the
strength-weighted mean with total strength clipped to [2, 8], i.e. at most ~8 reports' worth of belief.
Not used, and why: "~12,100 of 25,000 chargers non-functional at some point in Feb 2024" (Business
Standard) measures 'ever down', not the chance of working right now.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_STRENGTH, MAX_STRENGTH = 2.0, 8.0


@dataclass(frozen=True)
class PriorEvidence:
    p_works: float
    strength: float
    source: str


@dataclass(frozen=True)
class BetaPrior:
    a: float
    b: float
    evidence: tuple[PriorEvidence, ...]

    @property
    def mean(self) -> float:
        return self.a / (self.a + self.b)


# Chargers row: "Parliament data: 27,737 stations installed, 22,753 operational (Mar 2026)" -> 0.82.
NATIONAL = PriorEvidence(0.82, 2.0, "PLAN s3 Chargers: Parliament data, 22,753 of 27,737 operational (Mar 2026); approximate - verify")

# Chargers row: "BPCL's network reportedly 60% non-operational" (MMCM) -> 0.40.
OPERATOR_PRIORS: dict[str, PriorEvidence] = {
    "bpcl": PriorEvidence(0.40, 3.0, "PLAN s3 Chargers: BPCL network reportedly 60% non-operational (MMCM); approximate - verify"),
}

# Chargers row: "Indian DC fast-charger downtime near 40%" (Yo Mobility) -> 0.60. ASSUMPTION: highway sites
# are DC fast-charger sites.
SITE_PRIORS: dict[str, PriorEvidence] = {
    "highway": PriorEvidence(0.60, 2.0, "PLAN s3 Chargers: DC fast-charger downtime near 40% (Yo Mobility); assumes highway = DC fast; approximate - verify"),
}

# Chargers row: "2024 IEEFA audit: about 84% of chargers examined in Delhi non-functional (small sample)"
# -> 0.16, deliberately weak (strength 1) because the sample was small.
REGION_PRIORS: dict[str, PriorEvidence] = {
    "delhi": PriorEvidence(0.16, 1.0, "PLAN s3 Chargers: IEEFA 2024 audit, ~84% of examined Delhi chargers non-functional (small sample); approximate - verify"),
}

# Rough bounding box of the NCT of Delhi (approximate - verify). Noida / Greater Noida / Gurugram are outside it.
DELHI_BBOX = (28.40, 76.84, 28.88, 77.35)  # min_lat, min_lon, max_lat, max_lon


def normalise_operator(name: str | None) -> str:
    low = (name or "").strip().lower()
    if "bpcl" in low or "bharat petroleum" in low:
        return "bpcl"
    return low


def region_of(lat: float, lon: float) -> str | None:
    min_lat, min_lon, max_lat, max_lon = DELHI_BBOX
    return "delhi" if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon else None


def prior_for(operator: str | None, site_type: str | None, region: str | None = None) -> BetaPrior:
    """Beta prior on P(charger works) for this operator / site type / region."""
    evidence = [NATIONAL]
    if (op := OPERATOR_PRIORS.get(normalise_operator(operator))) is not None:
        evidence.append(op)
    if (site := SITE_PRIORS.get((site_type or "").strip().lower())) is not None:
        evidence.append(site)
    if (reg := REGION_PRIORS.get(region or "")) is not None:
        evidence.append(reg)
    total = sum(e.strength for e in evidence)
    p = sum(e.strength * e.p_works for e in evidence) / total
    strength = min(max(total, MIN_STRENGTH), MAX_STRENGTH)
    return BetaPrior(p * strength, (1.0 - p) * strength, tuple(evidence))

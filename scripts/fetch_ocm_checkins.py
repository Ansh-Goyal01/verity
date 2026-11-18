"""Open Charge Map check-ins for Delhi NCR and the Noida-Agra corridor -> charger reports (W1.2).

Official API only (``GET /v3/poi`` with ``includecomments=true``). The key (``OCM_API_KEY`` in .env) travels in a header and
is never cached or logged. No key -> the script says BLOCKED and writes nothing it did not read. The raw response is cached in
``data/cache/`` (gitignored) so the field names can be checked against what OCM really returned: the mapping in
``verity/ocm.py`` is UNVERIFIED until then. Output ``results/ocm_checkins.json`` reports n whatever it is, with a count of
every check-in that was skipped and why. Coverage of Indian chargers may be thin; that is a finding, not a failure.

Usage: ``uv run python scripts/fetch_ocm_checkins.py``
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from import_chargers import BBOXES, OCM_URL
from verity.chargers import CHARGERS_FILE, load_chargers
from verity.ocm import OCM_CHECKIN_SOURCE, checkins_from_pois
from verity.routing import CACHE_DIR, CachedClient, NetworkUnavailable, RequestBudgetExceeded
from verity.trust.graph import load_env

ROOT = Path(__file__).resolve().parent.parent
RESULT = ROOT / "results" / "ocm_checkins.json"
RAW_CACHE = Path(CACHE_DIR) / "ocm_checkins_raw.json"
MAX_RESULTS = 2000


def fetch_pois(client: CachedClient, key: str) -> list[dict]:
    pois: list[dict] = []
    for s, w, n, e in BBOXES:
        payload, _ = client.get_json(OCM_URL, {"output": "json", "countrycode": "IN", "maxresults": MAX_RESULTS, "includecomments": "true",
                                               "boundingbox": f"({s},{w}),({n},{e})", "verbose": "false"},  # no "compact": it may strip the nested check-in objects
                                     headers={"X-API-Key": key})
        pois.extend(payload)
        if len(payload) >= MAX_RESULTS:
            print(f"WARNING: box ({s},{w},{n},{e}) returned {len(payload)} points = the limit; the list may be truncated.")
    return pois


def write_result(status: str, reports: list, skipped: dict, n_pois: int | None, warnings: list[str]) -> None:
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps({
        "source": OCM_CHECKIN_SOURCE, "status": status, "n_pois_fetched": n_pois, "n_checkins": len(reports),
        "skipped": skipped, "warnings": warnings, "mapping_verified": False, "is_simulated": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "reports": [json.loads(r.model_dump_json()) for r in reports]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    key = load_env().get("OCM_API_KEY", "")
    if not key:
        print("BLOCKED: Open Charge Map needs a free API key (OCM_API_KEY in .env); nothing fetched, nothing written.")
        return 2
    directory, _ = load_chargers(CHARGERS_FILE)
    by_ocm = {s: c.id for c in directory for s in c.source_ids if s.startswith("ocm:")}
    try:
        pois = fetch_pois(CachedClient(), key)
    except (NetworkUnavailable, RequestBudgetExceeded) as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
    RAW_CACHE.write_text(json.dumps(pois), encoding="utf-8")
    reports, skipped = checkins_from_pois(pois, by_ocm)
    warnings = []
    n_comments = sum(len(p.get("UserComments") or []) for p in pois)
    if n_comments and not reports and set(skipped) <= {"not_a_checkin", "poi_not_in_directory"}:
        warnings.append(f"{n_comments} comments came back but none has a CheckinStatusType: the field names or the query are "
                        "probably wrong, so this is NOT evidence that coverage is thin. Open the raw cache file and check.")
    for w in warnings:
        print("WARNING:", w)
    write_result("fetched", reports, dict(skipped), len(pois), warnings)
    print(f"{len(pois)} points fetched, {len(reports)} check-ins mapped to directory chargers; skipped: {dict(skipped)}")
    print("Check the field names against", RAW_CACHE, "before trusting the mapping (docs/EVIDENCE.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

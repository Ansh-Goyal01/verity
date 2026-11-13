"""One command for the REAL-data track: re-validate the corpus, then re-run F3-F6 and F9 from the current CSVs.

    uv run python scripts/run_real_data.py              # everything
    uv run python scripts/run_real_data.py --no-paid    # F6 uses the keyword baseline: no Sarvam calls
    uv run python scripts/run_real_data.py --only validate,F4

Steps (each in its own process, output in results/logs/real_<step>.log):
  validate  scripts/validate_corpus.py --errors     corpus check; rows with errors are left out of every result
  F3        scripts/enrich_trips.py                 verified trips -> route, elevation, weather (cached APIs)
  F4        experiments/e1_india_real.py            E1-India on the fixed source-grouped held-out split
  F5        experiments/e3_real_evidence.py         charger evidence, channel trust, E3-real
  F6        experiments/voc_app_reviews.py          app-review classification (paid Sarvam calls only for new text)
  F9        scripts/range_passport.py               Range Passport certificates and validation

Every pipeline works on partial data and reports its n honestly (NOT_RUN / PRELIMINARY / OK). Corpus errors do not
stop the later steps (they skip bad rows), but they make the exit code 1 so they are not missed. The summary with the
corpus sizes and each step's status is written to results/real_data_summary.json.

Held-out rule: F4 evaluates the held-out set on every run. Quote E1-India numbers from the final run only and never
change the planner after looking at them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from verity.corpus import ERROR, PRIVATE_REVIEW_TEXT, SPECS, check_file, read_csv
from verity.paths import CORPUS_DIR, PRIVATE_DIR

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"
LOGS = RESULTS / "logs"
SUMMARY = RESULTS / "real_data_summary.json"
PY = sys.executable


def _load(rel: str) -> dict:
    try:
        return json.loads((REPO / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _headline_f3(d: dict) -> dict:
    return {"n_selected": d.get("n_selected"), **(d.get("summary") or {})}


def _headline_f4(d: dict) -> dict:
    return {"status": d.get("status"), "label": d.get("label"), "reason": d.get("reason"), "counts": d.get("counts")}


def _headline_f5(d: dict) -> dict:
    e3 = d.get("e3_real") or {}
    return {"inputs": d.get("inputs"), "n_reports": d.get("n_reports"),
            "e3_real": {"status": e3.get("status"), "n_trips": e3.get("n_trips")}}


def _headline_f6(d: dict) -> dict:
    return {"status": d.get("status"), "method": d.get("method"), "n_review_rows": d.get("n_review_rows"),
            "n_with_private_text": d.get("n_with_private_text"), "n_human_labelled": d.get("n_human_labelled"),
            "paid_calls": len(d.get("paid_calls") or [])}


def _headline_f9(d: dict) -> dict:
    bmw = d.get("bmw_i3") or {}
    return {"bmw_i3_trips_usable": bmw.get("trips_usable"), "bmw_i3_label": bmw.get("label"),
            "demo_V07": (d.get("demo_V07") or {}).get("summary")}


# name -> (command tail, files it must produce, result file read for the headline, headline extractor)
Step = tuple[list[str], list[str], str | None, Callable[[dict], dict] | None]
STEPS: dict[str, Step] = {
    "validate": (["scripts/validate_corpus.py", "--errors"], [], None, None),
    "F3": (["scripts/enrich_trips.py"], ["data/processed/trips_india_enriched.json"],
           "data/processed/trips_india_enriched.json", _headline_f3),
    "F4": (["experiments/e1_india_real.py"], ["results/e1_india_real.json", "results/e1_india_real.png"],
           "results/e1_india_real.json", _headline_f4),
    "F5": (["experiments/e3_real_evidence.py"], ["results/e3_real_evidence.json"],
           "results/e3_real_evidence.json", _headline_f5),
    "F6": (["experiments/voc_app_reviews.py"], ["results/voc_app_reviews.json"],
           "results/voc_app_reviews.json", _headline_f6),
    "F9": (["scripts/range_passport.py"], ["results/f9_passport_validation.json"],
           "results/f9_passport_validation.json", _headline_f9),
}


def corpus_sizes(corpus: Path = CORPUS_DIR) -> dict[str, dict[str, int]]:
    """Rows, usable rows (verified=yes, no error) and errors per corpus file. Counts only; no text."""
    sizes = {}
    for spec in SPECS:
        issues, n_rows, n_usable = check_file(spec, corpus)
        sizes[spec.filename] = {"rows": n_rows, "usable": n_usable, "errors": sum(i.level == ERROR for i in issues)}
    return sizes


def private_review_texts(private: Path = PRIVATE_DIR) -> int:
    """How many review texts exist in data/private/ (counted, never copied into the summary)."""
    path = private / PRIVATE_REVIEW_TEXT
    if not path.exists():
        return 0
    return sum(1 for r in read_csv(path)[1] if (r.get("text") or "").strip())


def step_command(name: str, no_paid: bool) -> list[str]:
    tail = list(STEPS[name][0])
    if name == "F6" and no_paid:
        tail += ["--method", "keyword"]
    return [PY, *tail]


def run_step(name: str, no_paid: bool) -> dict:
    _, produced, result_file, headline = STEPS[name]
    t0 = time.time()
    proc = subprocess.run(step_command(name, no_paid), cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    seconds = round(time.time() - t0, 1)
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / f"real_{name}.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    missing = [p for p in produced if not (REPO / p).exists()]
    last = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:] or [""]
    return {"step": name, "ok": proc.returncode == 0 and not missing, "returncode": proc.returncode,
            "seconds": seconds, "missing_outputs": missing, "last_line": last[0][:200],
            "headline": headline(_load(result_file)) if headline and result_file else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-validate the corpus and re-run F3-F6 and F9 from the CSVs")
    parser.add_argument("--only", default="", help="comma-separated steps: " + ",".join(STEPS))
    parser.add_argument("--no-paid", action="store_true", help="F6 uses the keyword baseline (no Sarvam calls)")
    args = parser.parse_args(argv)
    selected = [s.strip() for s in args.only.split(",") if s.strip()] or list(STEPS)
    unknown = [s for s in selected if s not in STEPS]
    if unknown:
        print(f"unknown step(s): {unknown}; choose from {list(STEPS)}")
        return 2

    sizes = corpus_sizes()
    n_texts = private_review_texts()
    print("Corpus (rows / usable in results / errors):")
    for name, s in sizes.items():
        print(f"  {name:<30} {s['rows']:>4} / {s['usable']:>4} / {s['errors']:>3}")
    print(f"  {'private review texts':<30} {n_texts:>4}")

    steps = []
    for name in selected:
        print(f"[{name}] running ...", flush=True)
        res = run_step(name, args.no_paid)
        steps.append(res)
        print(f"[{name}] {'OK  ' if res['ok'] else 'FAIL'} {res['seconds']:>6.1f} s  {res['last_line']}", flush=True)
        if not res["ok"]:
            print(f"        see results/logs/real_{name}.log"
                  + (f"; missing outputs: {res['missing_outputs']}" if res["missing_outputs"] else ""))

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "is_simulated": False,
           "note": "Corpus sizes and step statuses for the REAL-data track; the numbers live in each step's result file.",
           "corpus": sizes, "private_review_texts": n_texts, "no_paid": args.no_paid,
           "all_ok": all(s["ok"] for s in steps), "steps": steps}
    RESULTS.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(out, indent=1), encoding="utf-8")
    verdict = "ALL STEPS OK" if out["all_ok"] else "SOME STEPS FAILED (corpus errors count as a failure)"
    print(f"\n{verdict} -> {SUMMARY.relative_to(REPO)}")
    return 0 if out["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

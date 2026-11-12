"""F6: voice of customer from charging-app reviews (REAL, hand-collected) -> results/voc_app_reviews.json.

    uv run python experiments/voc_app_reviews.py                      # Sarvam chat model, cached, capped
    uv run python experiments/voc_app_reviews.py --method keyword     # no paid calls (baseline only)
    uv run python experiments/voc_app_reviews.py --smoke              # ONE paid call on a fictional sentence

Joins data/corpus/app_reviews.csv (paraphrase + human labels, committed) with data/private/app_reviews_text.csv
(full text, gitignored) on review_id. Methods: "sarvam-chat" classifies the original text (English, Hindi or
Hinglish) with Sarvam's chat model (sarvam-105b, Structured Outputs), ten reviews per call; "translate-keyword"
translates non-English text with Sarvam Translate (mayura:v1, auto source) and applies the keyword rules;
"keyword" applies the keyword rules to the original text. The keyword rules are always run as the baseline.
Model labels are cached in data/private/ (the prompts contain review text). No review text is ever written to
results/: only review ids, labels, counts, accuracy, Cohen's kappa and Wilson 95 % CIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from verity import voc
from verity.corpus import APP_REVIEWS, PRIVATE_REVIEW_TEXT, read_csv, usable_rows
from verity.paths import CORPUS_DIR, PRIVATE_DIR, RESULTS_DIR
from verity.voice import CHAT_MODEL, TRANSLATE_MODEL, SarvamClient, VoiceUnavailable

OUTPUT = RESULTS_DIR / "voc_app_reviews.json"
CACHE = PRIVATE_DIR / "voc_labels_cache.json"
BATCH = 10
MAX_CALLS = 40
MIN_OK_LABELLED = 50
SMOKE_TEXT = "Charger band tha par app pe available dikha raha tha, paise bhi kat gaye."  # fictional, written by us
LABEL = "REAL: hand-collected public app-store reviews (text kept private)"
Labels = dict[str, tuple[str, str]]


def load_reviews(corpus: Path, private: Path) -> tuple[list[dict], dict[str, str]]:
    path = corpus / APP_REVIEWS.filename
    rows = usable_rows(APP_REVIEWS, read_csv(path)[1]) if path.exists() else []
    text_path = private / PRIVATE_REVIEW_TEXT
    texts = ({r["review_id"].strip(): (r.get("text") or "") for r in read_csv(text_path)[1]}
             if text_path.exists() else {})
    return rows, {k: v for k, v in texts.items() if v.strip()}


def _key(text: str, method: str) -> str:
    return hashlib.sha256(f"{voc.PROMPT_VERSION}|{method}|{text}".encode()).hexdigest()


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _chat_with_retry(client: SarvamClient, system: str, user: str, keys: list[str]) -> Labels | None:
    """One retry: on 2026-11-12 one 200 response came back with empty content (reasoning used the budget)."""
    for _ in range(2):
        try:
            return voc.parse_labels(client.chat_json(system, user, voc.SCHEMA), keys)
        except VoiceUnavailable:
            continue
    return None


def classify_chat(todo: list[tuple[str, str]], client: SarvamClient, cache: dict, max_calls: int) -> Labels:
    out: Labels = {}
    for start in range(0, len(todo), BATCH):
        if len(client.calls) >= max_calls:
            break
        batch = todo[start:start + BATCH]
        keys = [f"r{i + 1}" for i in range(len(batch))]
        system, user = voc.build_prompt(list(zip(keys, (t for _, t in batch))))
        labels = _chat_with_retry(client, system, user, keys)
        if labels is None:
            continue  # logged in client.calls; the batch stays unclassified (reported, never guessed)
        for k, (rid, text) in zip(keys, batch):
            if k in labels:
                out[rid] = labels[k]
                cache[_key(text, "sarvam-chat")] = list(labels[k])
    return out


def classify_translate(todo: list[tuple[str, str]], langs: dict[str, str], client: SarvamClient, cache: dict,
                       max_calls: int) -> Labels:
    out: Labels = {}
    for rid, text in todo:
        english = text
        if langs.get(rid) != "en":
            if len(client.calls) >= max_calls:
                continue
            try:
                english = client.translate(text, "en-IN")
            except VoiceUnavailable:
                continue
        out[rid] = voc.keyword_classify(english)
        cache[_key(text, "translate-keyword")] = list(out[rid])
    return out


def run(corpus: Path = CORPUS_DIR, private: Path = PRIVATE_DIR, method: str = "sarvam-chat",
        client_factory: Callable[[], SarvamClient | None] = SarvamClient.from_env, max_calls: int = MAX_CALLS,
        cache_path: Path = CACHE) -> dict:
    rows, texts = load_reviews(corpus, private)
    joined = [r for r in rows if r["review_id"] in texts]
    base = {"experiment": "F6 voice of customer (app reviews)", "is_simulated": False, "label": LABEL,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "method": method,
            "model": {"sarvam-chat": CHAT_MODEL, "translate-keyword": TRANSLATE_MODEL}.get(method, "keyword rules"),
            "n_review_rows": len(rows), "n_with_private_text": len(joined), "paid_calls": []}
    if not joined:
        return {**base, "status": "NOT_RUN", "why": "no review rows joined with data/private/app_reviews_text.csv yet"}
    cache = _load_cache(cache_path)
    model: Labels = {}
    todo = []
    for r in joined:
        rid, text = r["review_id"], texts[r["review_id"]]
        if method == "keyword":
            model[rid] = voc.keyword_classify(text)
        elif hit := cache.get(_key(text, method)):
            model[rid] = (hit[0], hit[1])
        else:
            todo.append((rid, text))
    client = client_factory() if todo else None
    if todo and client is None:
        base["warning"] = "SARVAM_API_KEY missing: uncached reviews were not classified"
    elif todo and client is not None:
        langs = {r["review_id"]: r["language"] for r in joined}
        model.update(classify_chat(todo, client, cache, max_calls) if method == "sarvam-chat"
                     else classify_translate(todo, langs, client, cache, max_calls))
        base["paid_calls"] = client.calls
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return {**base, **summarise(joined, texts, model)}


def _agree(pred: Labels, idx: int, rows: list[dict], col: str) -> dict:
    return voc.agreement([pred[r["review_id"]][idx] for r in rows], [r[col] for r in rows])


def summarise(joined: list[dict], texts: dict[str, str], model: Labels) -> dict:
    baseline = {r["review_id"]: voc.keyword_classify(texts[r["review_id"]]) for r in joined}
    labelled = [r for r in joined if r.get("human_category") and r.get("human_claim")]
    both = [r for r in labelled if r["review_id"] in model]
    by_lang = {lang: _agree(model, 0, [r for r in both if r["language"] == lang], "human_category")
               for lang in sorted({r["language"] for r in both})}
    return {
        "status": "PRELIMINARY" if len(both) < MIN_OK_LABELLED else "OK",
        "n_classified": len(model), "n_unclassified": len(joined) - len(model), "n_human_labelled": len(labelled),
        "category_shares_model": voc.shares([c for c, _ in model.values()]),
        "claim_shares_model": voc.shares([c for _, c in model.values()], voc.CLAIMS),
        "category_shares_human": voc.shares([r["human_category"] for r in labelled]),
        "agreement_model_vs_human": {"category": _agree(model, 0, both, "human_category"),
                                     "claim": _agree(model, 1, both, "human_claim"),
                                     "category_by_language": by_lang},
        "agreement_keyword_baseline_vs_human": {"category": _agree(baseline, 0, labelled, "human_category"),
                                                "claim": _agree(baseline, 1, labelled, "human_claim")},
        "labels": [{"review_id": rid, "category": c, "claim": k} for rid, (c, k) in sorted(model.items())],
        "notes": ["Shares describe the reviews collected by hand, not a random sample of all reviews.",
                  f"PRELIMINARY below {MIN_OK_LABELLED} human-labelled reviews with a model label."],
    }


def smoke(client: SarvamClient) -> dict:
    """One paid call on a fictional sentence: proves the chat endpoint, auth and schema work."""
    system, user = voc.build_prompt([("r1", SMOKE_TEXT)])
    try:
        labels = voc.parse_labels(client.chat_json(system, user, voc.SCHEMA), ["r1"])
        return {"ok": bool(labels), "labels": labels.get("r1"), "calls": client.calls}
    except VoiceUnavailable as exc:
        return {"ok": False, "error": str(exc), "calls": client.calls}


def main() -> int:
    parser = argparse.ArgumentParser(description="F6 voice of customer")
    parser.add_argument("--method", choices=("sarvam-chat", "translate-keyword", "keyword"), default="sarvam-chat")
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        client = SarvamClient.from_env()
        out = smoke(client) if client else {"ok": False, "error": "SARVAM_API_KEY missing"}
        print(json.dumps({"sentence": "fictional (SMOKE_TEXT in this file)", **out}, ensure_ascii=False))
        return 0 if out.get("ok") else 1
    result = run(method=args.method, max_calls=args.max_calls)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"{LABEL}: {result['status']} | rows {result['n_review_rows']}, with text {result['n_with_private_text']}, "
          f"classified {result.get('n_classified', 0)}, paid calls {len(result['paid_calls'])} -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""F6 voice of customer: prompt/parse, kappa, shares, and the run against a MOCK Sarvam transport only."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx

import experiments.voc_app_reviews as vr
from verity import voc
from verity.corpus import APP_REVIEWS
from verity.voice import SarvamClient

KEY = "TEST-KEY-DO-NOT-LEAK"


def test_cohen_kappa_and_agreement() -> None:
    assert voc.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert voc.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    assert voc.cohen_kappa(["a", "a"], ["a", "a"]) is None and voc.cohen_kappa([], []) is None
    out = voc.agreement(["a", "b", "b"], ["a", "b", "a"])
    assert out["n"] == 3 and out["accuracy"] == round(2 / 3, 4) and out["accuracy_ci95"][0] < 2 / 3


def test_shares_cover_every_category_with_ci() -> None:
    out = voc.shares(["functionality", "functionality", "cost"])
    by = {s["label"]: s for s in out["shares"]}
    assert out["n"] == 3 and by["functionality"]["k"] == 2 and by["dealership"]["share"] == 0.0
    assert len(out["shares"]) == 9 and by["cost"]["ci95"] is not None


def test_parse_labels_drops_unknown_values_and_keys() -> None:
    obj = {"labels": [{"key": "r1", "category": "cost", "claim": "payment"},
                      {"key": "[r2]", "category": "made_up", "claim": "other"},
                      {"key": "r9", "category": "cost", "claim": "other"}, "junk"]}
    assert voc.parse_labels(obj, ["r1", "r2"]) == {"r1": ("cost", "payment")}
    assert voc.parse_labels("not json", ["r1"]) == {}


def test_prompt_lists_all_categories_and_keys_only() -> None:
    system, user = voc.build_prompt([("r1", "Charger  band\nhai"), ("r2", "app crashed")])
    assert all(c in system for c in voc.CATEGORY_DEFS) and user == "[r1] Charger band hai\n[r2] app crashed"


def test_keyword_baseline_reads_english_and_hinglish() -> None:
    assert voc.keyword_classify("Charger band hai, kharab") == ("functionality", "charger_not_working")
    assert voc.keyword_classify("Payment failed, money deducted in the app") == ("user_interaction", "payment")
    assert voc.keyword_classify("nice") == ("other", "other")


def _corpus(tmp: Path) -> tuple[Path, Path]:
    corpus, private = tmp / "corpus", tmp / "private"
    corpus.mkdir()
    private.mkdir()
    rows = [{"review_id": f"R{i}", "app": "statiq", "review_date": "2026-11-01", "stars": "1", "language": lang,
             "paraphrase": "", "human_category": cat, "human_claim": claim, "collected_by": "t"}
            for i, (lang, cat, claim) in enumerate([("en", "functionality", "charger_not_working"),
                                                    ("hinglish", "user_interaction", "payment"), ("hi", "", "")])]
    with (corpus / APP_REVIEWS.filename).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, APP_REVIEWS.header)
        writer.writeheader()
        writer.writerows(rows)
    with (private / "app_reviews_text.csv").open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["review_id", "text"], ["R0", "charger not working"],
                                  ["R1", "paise kat gaye app me"], ["R2", "चार्जर बंद है"]])
    return corpus, private


def _mock_client(seen: dict) -> SarvamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        seen["model"] = json.loads(request.read())["model"]
        labels = [{"key": "r1", "category": "functionality", "claim": "charger_not_working"},
                  {"key": "r2", "category": "cost", "claim": "payment"},
                  {"key": "r3", "category": "functionality", "claim": "charger_not_working"}]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"labels": labels})}}]})

    return SarvamClient(KEY, httpx.Client(base_url="https://api.sarvam.ai", transport=httpx.MockTransport(handler)))


def test_run_classifies_once_caches_and_writes_no_text(tmp_path: Path) -> None:
    corpus, private = _corpus(tmp_path)
    seen: dict = {}
    cache = private / "cache.json"
    out = vr.run(corpus, private, client_factory=lambda: _mock_client(seen), cache_path=cache)
    assert seen == {"auth": f"Bearer {KEY}", "path": "/v1/chat/completions", "model": "sarvam-105b"}
    assert out["n_classified"] == 3 and len(out["paid_calls"]) == 1 and out["status"] == "PRELIMINARY"
    agree = out["agreement_model_vs_human"]
    assert agree["category"]["n"] == 2 and agree["category"]["accuracy"] == 0.5
    dumped = json.dumps(out, ensure_ascii=False)
    assert "paise" not in dumped and "चार्जर" not in dumped and KEY not in dumped
    again = vr.run(corpus, private, client_factory=lambda: None, cache_path=cache)  # all cached: no client needed
    assert again["n_classified"] == 3 and again["paid_calls"] == [] and "warning" not in again


def test_run_without_text_or_key_is_honest(tmp_path: Path) -> None:
    out = vr.run(tmp_path, tmp_path, client_factory=lambda: None, cache_path=tmp_path / "c.json")
    assert out["status"] == "NOT_RUN" and out["paid_calls"] == []
    corpus, private = _corpus(tmp_path)
    out = vr.run(corpus, private, client_factory=lambda: None, cache_path=private / "c.json")
    assert out["n_classified"] == 0 and "SARVAM_API_KEY" in out["warning"]
    kw = vr.run(corpus, private, method="keyword", client_factory=lambda: None, cache_path=private / "c.json")
    assert kw["n_classified"] == 3 and kw["paid_calls"] == []

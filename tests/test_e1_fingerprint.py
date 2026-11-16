"""The E1 guard (W0.4, F10): the frozen held-out evaluation still matches the code and TRAIN data it was made from,
on every checkout. Nothing here opens a held-out trip: the fingerprint reads training files only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.e1_learning_curve import DEFAULT_RHO_REL, audit_sha256, normalised_bytes, protocol_fingerprint
from verity.paths import RESULTS_DIR
from verity.splits import load_eval_dataset

EVALUATION = RESULTS_DIR / "e1_learning_curve_simulated_evaluation.json"
RESULT = RESULTS_DIR / "e1_learning_curve_simulated.json"
AUDIT = RESULTS_DIR / "e1_training_audit_simulated.json"
RESAMPLES = 20  # experiments/e1_learning_curve.py: the default, and the minimum R4a allows


def test_line_endings_do_not_change_what_is_hashed(tmp_path: Path) -> None:
    lf, crlf = tmp_path / "lf.json", tmp_path / "crlf.json"
    lf.write_bytes(b'{\n "a": 1\n}\n')
    crlf.write_bytes(b'{\r\n "a": 1\r\n}\r\n')
    assert normalised_bytes(lf) == normalised_bytes(crlf) == b'{\n "a": 1\n}\n'
    assert audit_sha256(lf) == audit_sha256(crlf)


def test_a_real_change_still_changes_the_hash(tmp_path: Path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(b'{"a": 1}\r\n')
    b.write_bytes(b'{"a": 2}\r\n')
    assert audit_sha256(a) != audit_sha256(b)


@pytest.mark.skipif(not (EVALUATION.exists() and AUDIT.exists()), reason="the frozen E1 evaluation is not in this checkout")
def test_the_frozen_evaluation_matches_this_checkout() -> None:
    saved = json.loads(EVALUATION.read_text(encoding="utf-8"))
    _, split = load_eval_dataset("main")
    expected = protocol_fingerprint("main", split, DEFAULT_RHO_REL, RESAMPLES, saved["seed"], saved["n_draws"])
    assert saved["fingerprint"] == expected, "E1 code, settings or training data changed since the frozen evaluation"
    assert saved["training_audit_sha256"] == audit_sha256(AUDIT)


@pytest.mark.skipif(not (EVALUATION.exists() and RESULT.exists()), reason="the frozen E1 evaluation is not in this checkout")
def test_the_reported_result_is_the_frozen_evaluation() -> None:
    frozen = json.loads(EVALUATION.read_text(encoding="utf-8"))
    reported = json.loads(RESULT.read_text(encoding="utf-8"))
    for key in ("fingerprint", "training_audit_sha256", "methods", "seed", "n_draws", "test_trips"):
        assert reported[key] == frozen[key], key

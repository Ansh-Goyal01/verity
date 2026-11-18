"""The venue voice kit (W3.5): POST /api/lab/voice-sample, the sample store, and the summary behind results/voice_real.json.

No Sarvam call is made: a fake client returns scripted transcripts. Every sample here is synthetic test data."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.limits import RateLimiter
from api.main import app, get_service, get_voice_client, get_voice_limiter
from api.service import EngineService
from verity.names import decide
from verity.report_voice import name_query
from verity.voice import VoiceModels, VoiceUnavailable
from verity.voice_lab import COMPARE_CONFIGS, MIN_SAMPLES, MIN_SPEAKERS, SampleStore, summarise

AUDIO = b"RIFF....synthetic, not a recording"


@dataclass
class FakeVoice:
    """A dataclass like SarvamClient, so ``dataclasses.replace`` can swap its models for the comparison calls."""

    transcript: str = "my battery is 37 percent"
    by_config: dict = field(default_factory=dict)  # (model, mode) -> transcript, or an exception to raise
    models: VoiceModels = field(default_factory=VoiceModels)
    seen: list = field(default_factory=list)

    def stt_full(self, audio: bytes, filename: str, content_type: str, language: str = "unknown", mode: str | None = None) -> dict:
        key = (self.models.stt_model, mode or self.models.stt_mode)
        self.seen.append(key)
        result = self.by_config.get(key, self.transcript)
        if isinstance(result, Exception):
            raise result
        return {"transcript": result, "language_code": "en-IN"}


@pytest.fixture(scope="module")
def svc():
    service = EngineService(n_runs=200, graph_backend="memory")
    yield service
    service.close()


@pytest.fixture()
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, svc: EngineService):
    monkeypatch.setenv("VERITY_LAB_MODE", "1")
    monkeypatch.setenv("VERITY_LAB_TOKEN", TOKEN)
    monkeypatch.setenv("VERITY_LAB_DIR", str(tmp_path))
    voice = FakeVoice()
    app.dependency_overrides[get_service] = lambda: svc
    app.dependency_overrides[get_voice_client] = lambda: voice
    app.dependency_overrides[get_voice_limiter] = lambda: RateLimiter(1000, 60.0)
    yield TestClient(app), voice, tmp_path / "voice_samples.jsonl"
    app.dependency_overrides.clear()


TOKEN = "venue-secret"


def post(client: TestClient, token: str | None = TOKEN, **over):
    form = {"consent": "true", "speaker_id": "s01", "spoken_language": "english", "device": "pixel-7",
            "language": "en-IN", "expected_soc_pct": "37", **over}
    form = {k: v for k, v in form.items() if v is not None}
    headers = {"X-Lab-Token": token} if token is not None else {}
    return client.post("/api/lab/voice-sample", data=form, files={"audio": ("clip.wav", AUDIO, "audio/wav")}, headers=headers)


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_the_lab_is_off_unless_the_operator_switches_it_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, svc) -> None:
    monkeypatch.delenv("VERITY_LAB_MODE", raising=False)
    monkeypatch.setenv("VERITY_LAB_TOKEN", TOKEN)
    monkeypatch.setenv("VERITY_LAB_DIR", str(tmp_path))
    app.dependency_overrides[get_service] = lambda: svc
    app.dependency_overrides[get_voice_client] = lambda: FakeVoice()
    app.dependency_overrides[get_voice_limiter] = lambda: RateLimiter(1000, 60.0)
    try:
        r = post(TestClient(app))
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 403 and r.json()["error"] == "lab_disabled"
    assert not list(tmp_path.iterdir())


def test_the_lab_needs_a_configured_token_and_every_call_must_carry_it(lab, monkeypatch: pytest.MonkeyPatch) -> None:
    client, voice, path = lab
    for token in (None, "wrong", ""):
        r = post(client, token=token)
        assert r.status_code == 403 and r.json()["error"] == "lab_token_invalid"
    monkeypatch.delenv("VERITY_LAB_TOKEN")  # mode on but no token configured: the lab stays off
    assert post(client).json()["error"] == "lab_disabled"
    assert voice.seen == [] and not path.exists()


def test_a_comparison_request_is_charged_five_hits_of_the_voice_budget(lab) -> None:
    client, voice, path = lab
    limiter = RateLimiter(5, 60.0)  # exactly one comparison's worth; ONE instance, so its count carries across requests
    app.dependency_overrides[get_voice_limiter] = lambda: limiter
    assert post(client, compare="true").status_code == 200
    again = post(client)
    assert again.status_code == 429 and len(lines(path)) == 1


def test_a_comparison_that_would_overrun_the_budget_is_refused_before_any_paid_call(lab) -> None:
    client, voice, path = lab
    limiter = RateLimiter(3, 60.0)
    app.dependency_overrides[get_voice_limiter] = lambda: limiter
    r = post(client, compare="true")
    assert r.status_code == 429 and voice.seen == [] and not path.exists()


def test_without_consent_nothing_is_stored_and_nothing_is_sent_to_sarvam(lab) -> None:
    client, voice, path = lab
    r = post(client, consent="false")
    assert r.status_code == 422 and r.json()["error"] == "consent_required"
    assert not path.exists() and voice.seen == []


def test_a_correct_battery_sample_is_stored_without_audio_or_identity(lab) -> None:
    client, _, path = lab
    r = post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["transcript"], body["parsed_soc_pct"], body["expected_soc_pct"], body["battery_correct"]) == (
        "my battery is 37 percent", 37, 37, True)
    stored = lines(path)
    assert len(stored) == 1 and stored[0]["sample_id"] == body["sample_id"]
    assert stored[0]["consent"] is True and stored[0]["is_simulated"] is False
    assert stored[0]["schema"] == "verity.voice_sample.v1"
    text = path.read_text(encoding="utf-8")
    assert "RIFF" not in text and "testclient" not in text  # no audio, no address
    assert set(stored[0]) <= {"schema", "sample_id", "recorded_at", "speaker_id", "spoken_language", "device", "language_detected",
                              "stt_model", "stt_mode", "stt_ms", "transcript", "expected_soc_pct", "parsed_soc_pct",
                              "battery_correct", "expected_charger_id", "matched_charger_id", "charger_kind", "charger_correct",
                              "comparisons", "consent", "is_simulated"}


def test_a_misheard_number_is_stored_as_wrong_not_dropped(lab) -> None:
    client, voice, path = lab
    voice.transcript = "my battery is 87 percent"
    r = post(client)
    assert r.status_code == 200 and r.json()["battery_correct"] is False
    assert lines(path)[0]["parsed_soc_pct"] == 87


def test_a_charger_sample_is_scored_through_the_name_index(lab, svc: EngineService) -> None:
    client, voice, path = lab
    target = next((c for c in svc.chargers if (d := decide(svc.graph.call(lambda g: g.search_names(name_query(c.name))))).kind == "match"
                   and d.hits[0].charger_id == c.id), None)
    if target is None:
        pytest.skip("no charger in the directory has a name the index resolves uniquely")
    voice.transcript = f"{target.name} band hai"
    r = post(client, expected_soc_pct=None, expected_charger_id=target.id)
    assert r.status_code == 200, r.text
    assert (r.json()["matched_charger_id"], r.json()["charger_correct"]) == (target.id, True)
    voice.transcript = "kuch aur"
    wrong = post(client, expected_soc_pct=None, expected_charger_id=target.id).json()
    assert wrong["charger_correct"] is False and wrong["matched_charger_id"] is None


@pytest.mark.parametrize("over, code", [
    ({"speaker_id": "Ansh Goyal"}, "invalid_request"),      # a name (with a space) is refused: ids are anonymous labels
    ({"speaker_id": None}, "invalid_request"),
    ({"spoken_language": "klingon"}, "invalid_request"),
    ({"device": "x" * 41}, "invalid_request"),
    ({"expected_soc_pct": "101"}, "invalid_request"),
    ({"expected_soc_pct": "abc"}, "invalid_request"),
    ({"expected_soc_pct": "²"}, "invalid_request"),          # a Unicode digit: str.isdigit() says yes, int() raises
    ({"expected_soc_pct": None}, "nothing_to_score"),
    ({"expected_soc_pct": None, "expected_charger_id": "NO-SUCH-CHARGER"}, "unknown_charger"),
    ({"language": "english"}, "invalid_request"),
])
def test_bad_fields_are_refused_before_any_paid_call(lab, over: dict, code: str) -> None:
    client, voice, path = lab
    r = post(client, **over)
    assert r.status_code in (404, 422) and r.json()["error"] == code, r.text
    assert voice.seen == [] and not path.exists()


def test_a_missing_recording_is_refused(lab) -> None:
    client, _, _ = lab
    r = client.post("/api/lab/voice-sample", data={"consent": "true", "speaker_id": "s01", "spoken_language": "english",
                                                   "device": "d", "expected_soc_pct": "37"}, headers={"X-Lab-Token": TOKEN})
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"


def test_without_a_sarvam_key_the_lab_says_so(lab) -> None:
    client, _, path = lab
    app.dependency_overrides[get_voice_client] = lambda: None
    r = post(client)
    assert r.status_code == 503 and r.json()["error"] == "voice_unavailable" and not path.exists()


def test_compare_replays_the_same_audio_through_every_config_and_keeps_failures(lab) -> None:
    client, voice, path = lab
    voice.by_config = {("saaras:v3", "transcribe"): "my battery is eighty seven percent",   # a wrong number: scored wrong
                       ("saaras:v4", "codemix"): VoiceUnavailable("voice service error: ReadTimeout")}
    r = post(client, compare="true")
    assert r.status_code == 200, r.text
    comparisons = r.json()["comparisons"]
    assert [(c["model"], c["mode"]) for c in comparisons] == list(COMPARE_CONFIGS)
    by = {(c["model"], c["mode"]): c for c in comparisons}
    assert by[("saaras:v4", "transcribe")]["correct"] is True and by[("saaras:v3", "transcribe")]["correct"] is False
    assert by[("saaras:v4", "codemix")]["error"] and by[("saaras:v4", "codemix")]["correct"] is None  # kept, not dropped
    assert voice.seen.count(("saaras:v3", "codemix")) == 1
    assert len(lines(path)) == 1


def test_an_oversized_upload_is_refused(lab) -> None:
    client, voice, path = lab
    big = client.post("/api/lab/voice-sample", data={"consent": "true"}, files={"audio": ("c.wav", b"0" * 5_200_000, "audio/wav")},
                      headers={"X-Lab-Token": TOKEN})
    assert big.status_code == 413 and voice.seen == [] and not path.exists()


# --- the store and the summary ---------------------------------------------------------------------------------


def sample(speaker: str, language: str = "hindi", *, battery: bool | None = True, charger: bool | None = None,
           ms: int = 500, comparisons: list | None = None) -> dict:
    return {"schema": "verity.voice_sample.v1", "sample_id": f"{speaker}-{language}-{ms}", "speaker_id": speaker,
            "spoken_language": language, "stt_ms": ms, "stt_model": "saaras:v4", "stt_mode": "transcribe",
            "battery_correct": battery, "charger_correct": charger, "comparisons": comparisons or [],
            "consent": True, "is_simulated": False}


def test_summary_counts_intervals_and_latency_by_language() -> None:
    samples = [sample("a", "hindi", ms=400), sample("b", "hindi", battery=False, ms=800),
               sample("c", "english", ms=300), sample("d", "english", battery=None, charger=True, ms=200)]
    s = summarise(samples)
    assert (s["n_samples"], s["n_speakers"]) == (4, 4)
    assert s["battery"]["scored"] == 3 and s["battery"]["correct"] == 2
    assert s["battery"]["wilson95"][0] < 2 / 3 < s["battery"]["wilson95"][1]
    assert (s["charger_match"]["scored"], s["charger_match"]["correct"]) == (1, 1)
    assert s["by_language"]["hindi"]["battery"]["scored"] == 2 and s["by_language"]["hindi"]["n_speakers"] == 2
    assert s["stt_latency_ms"] == {"n": 4, "p50": 350.0, "p95": pytest.approx(740.0)}
    assert s["is_simulated"] is False and s["schema"] == "verity.voice_real.v1"


def test_a_small_sample_is_marked_preliminary_and_says_why() -> None:
    s = summarise([sample("a")])
    assert s["preliminary"] is True and str(MIN_SAMPLES) in s["preliminary_reason"] and str(MIN_SPEAKERS) in s["preliminary_reason"]


def test_enough_samples_and_speakers_clear_the_preliminary_flag() -> None:
    samples = [sample(f"s{i % MIN_SPEAKERS}", ms=300 + i) for i in range(MIN_SAMPLES)]
    s = summarise(samples)
    assert s["n_speakers"] == MIN_SPEAKERS and s["preliminary"] is False


def test_the_comparison_table_ranks_configs_and_counts_failures() -> None:
    def cmp(model: str, mode: str, correct: bool | None, ms: int = 100) -> dict:
        return {"model": model, "mode": mode, "correct": correct, "ms": ms, "error": None if correct is not None else "timeout"}

    samples = [sample("a", comparisons=[cmp("saaras:v3", "transcribe", False), cmp("saaras:v4", "transcribe", True)]),
               sample("b", comparisons=[cmp("saaras:v3", "transcribe", False), cmp("saaras:v4", "transcribe", None)])]
    table = {(r["model"], r["mode"]): r for r in summarise(samples)["model_comparison"]}
    assert (table[("saaras:v4", "transcribe")]["scored"], table[("saaras:v4", "transcribe")]["correct"],
            table[("saaras:v4", "transcribe")]["errors"]) == (1, 1, 1)
    assert table[("saaras:v3", "transcribe")]["correct"] == 0
    assert summarise(samples)["model_comparison"][0]["model"] == "saaras:v4"  # best accuracy first


def test_the_store_appends_reads_back_and_reports_damaged_lines(tmp_path: Path) -> None:
    store = SampleStore(tmp_path / "s.jsonl")
    store.append(sample("a"))
    store.append(sample("b"))
    with (tmp_path / "s.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    samples, skipped = store.read()
    assert [s["speaker_id"] for s in samples] == ["a", "b"] and skipped == 1
    assert SampleStore(tmp_path / "missing.jsonl").read() == ([], 0)


def test_a_transcript_with_a_unicode_line_separator_stays_one_sample(tmp_path: Path) -> None:
    store = SampleStore(tmp_path / "s.jsonl")
    store.append({**sample("a"), "transcript": "line one line two\u0085end"})
    samples, skipped = store.read()
    assert skipped == 0 and samples[0]["transcript"] == "line one line two\u0085end"


def test_simulated_unconsented_or_foreign_rows_are_skipped_not_counted(tmp_path: Path) -> None:
    store = SampleStore(tmp_path / "s.jsonl")
    store.append(sample("real"))
    store.append({**sample("sim"), "is_simulated": True})
    store.append({**sample("nocon"), "consent": False})
    store.append({**sample("old"), "schema": "something.else"})
    samples, skipped = store.read()
    assert [s["speaker_id"] for s in samples] == ["real"] and skipped == 3


def test_a_config_that_mostly_fails_cannot_top_the_comparison_table() -> None:
    def cmp(model: str, correct: bool | None) -> dict:
        return {"model": model, "mode": "transcribe", "correct": correct, "ms": 100, "error": None if correct is not None else "timeout"}

    flaky = [cmp("flaky", None)] * 9 + [cmp("flaky", True)]                  # 1 right, 9 failed: 100% of the one that answered
    steady = [cmp("steady", True)] * 28 + [cmp("steady", False)] * 2         # 28 of 30
    samples = [sample(f"s{i}", comparisons=[steady[i]] + ([flaky[i]] if i < len(flaky) else [])) for i in range(len(steady))]
    table = summarise(samples)["model_comparison"]
    assert [r["model"] for r in table][0] == "steady"
    flaky_row = next(r for r in table if r["model"] == "flaky")
    assert flaky_row["accuracy"] == 1.0 and flaky_row["success_over_all_calls"] == 0.1 and flaky_row["errors"] == 9


# --- the script and the ledger ---------------------------------------------------------------------------------


def run_script(*args: str) -> int:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "voice_real_eval.py"
    spec = importlib.util.spec_from_file_location("voice_real_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(list(args))


def test_the_script_writes_nothing_when_there_are_no_samples(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    out = tmp_path / "voice_real.json"
    assert run_script("--samples", str(tmp_path / "none.jsonl"), "--out", str(out)) == 1
    assert not out.exists() and "nothing written" in capsys.readouterr().err


def test_the_script_writes_a_preliminary_summary_the_ledger_reports_as_preliminary(tmp_path: Path) -> None:
    from api.ledger import build_ledger

    store = SampleStore(tmp_path / "s.jsonl")
    for speaker in ("a", "b"):
        store.append(sample(speaker))
    assert run_script("--samples", str(tmp_path / "s.jsonl"), "--out", str(tmp_path / "voice_real.json")) == 0
    written = json.loads((tmp_path / "voice_real.json").read_text(encoding="utf-8"))
    assert written["n_samples"] == 2 and written["preliminary"] is True
    notes = build_ledger(tmp_path)["real"]["voice_notes"]
    assert (notes["count"], notes["speakers"], notes["status"]) == (2, 2, "PRELIMINARY")


def test_the_ledger_says_measured_only_when_the_target_is_met(tmp_path: Path) -> None:
    from api.ledger import build_ledger

    (tmp_path / "voice_real.json").write_text(json.dumps(summarise(
        [sample(f"s{i % MIN_SPEAKERS}") for i in range(MIN_SAMPLES)])), encoding="utf-8")
    assert build_ledger(tmp_path)["real"]["voice_notes"]["status"] == "MEASURED"
    assert build_ledger(tmp_path / "empty")["real"]["voice_notes"] == {
        "count": 0, "speakers": 0, "status": "NOT_RUN", "note": "venue measurement on real speakers (guide W3.5) has not happened"}

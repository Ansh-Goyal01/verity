# Finale build W — session plan (from the Opus 5.5 winning-build guide)

Authority: `docs/Verity — winning build guide for Opus 5.5 (hackFront India finale).md` (called **the guide**).
This file only splits the guide into sessions small enough to run one per fresh context. Where this file
and the guide disagree, the guide wins, except for the boundary rules below.

## The continue protocol (every session follows this exactly)

The user starts each session with `/clear`, then: **`continue docs/TASKS_W.md`**

1. Read `CLAUDE.md` (auto-loaded), this file, and `docs/PROGRESS_W.md`. Nothing else yet.
2. Take the **first session whose box is unticked** in `docs/PROGRESS_W.md`. Do only that session.
3. Read only the guide sections and files listed under that session. Use `Grep`/partial `Read` on large
   files (`dispatch.py`, `DECISIONS.md`, `RESULTS_FOR_DECK.md`); never read them whole.
4. Plan mode is pre-approved by this file for the listed scope (CLAUDE.md asks for plan mode on >2-file
   changes; this plan is that approval). Anything outside the listed scope still needs a plan and approval.
5. Test first where the session says so. `uv run pytest` green before every commit.
6. One commit per milestone, message `W<n>.<m>: summary` (e.g. `W0.1: ...`), ending with the
   co-author line. Commit to main, local only; Ansh pushes. Stage named files only, never `git add .`
   (the stray `verity/` clone in the repo root must never be committed).
7. When the session is done: tick it in `docs/PROGRESS_W.md`, write a handoff note of at most 15 lines
   (what changed, numbers that moved, anything blocked + the manual step), commit that with the last
   milestone, print a short status, and **stop**. Do not start the next session.
8. If blocked (no key, no network, no data rows): build everything around the block, record the exact
   manual step in the handoff note and in `docs/EVIDENCE_STATUS.md`, and finish the rest of the session.
9. If a change flips a pinned demo decision: stop and report it (guide §2 rule 6). Never tune to an outcome.

## Boundary rules (override the guide where they conflict)

- **No edits in `web/`, `logger/` or `design/`** (CLAUDE.md, guide §9). `scripts/export_web_snapshot.py`
  writes `web/js/snapshot.js`, so W0.3 does **not** run it. Instead the engine side writes the same bundle
  to `results/snapshot.json` (plus the consistency test), and the handoff note tells Ansh to run
  `export_web_snapshot.py` himself. Anything the guide calls a "page" (e.g. `/lab/voice`) is delivered as
  an API endpoint + script; the page is Ansh's.
- **Held-out data:** the test split is opened only in S10 (W1.4), exactly once, after the freeze is logged.
- **Paid calls:** Sarvam calls only where a session says so; every call goes through the existing paid-call log.
- **API changes are additive**, written into `docs/API_CONTRACT.md` + `docs/API_CONTRACT_CHANGES.md`, and
  logged in `docs/DECISIONS.md`.

## Sessions

Each session lists: guide sections to read · main files · milestones · acceptance · human prerequisite.

### S1 — Bingo Line agrees with the plan (W0.1) + deadline line
- Read: guide §3 (F1, F2), §5 W0.1. Code: `engine/verity/dispatch.py` around `alts_for` (~l.300),
  `bingo_line` (~l.424), `evaluate_plan`, `live_update_plan` (~l.729); `tests/test_dispatch.py`.
- First: a **failing test** that reproduces F1 on V12 (expected SoC below the Bingo Line on a GO plan) and
  confirms or refutes the F2 cause. Then the fix; then the precedence rule for `live_update_plan` in DECISIONS.md.
- Invariant tests: the 3 demo vehicles + 50 seeded simulated trips → `expected_soc_pct >= bingo_soc_pct`
  at every km on GO plans; the plan's own median trajectory fed to `live_update_plan` → `CONTINUE`.
- Re-export `docs/api_examples/` (`scripts/export_api_examples.py`), re-run E3 and E4, and report any changed pinned decision.
- Also: CLAUDE.md deadline line → "Finale: 21 November 2026, 07:30 IST" (guide §2). Commit that as `W0.0`.
- Human: none.

### S2 — No certainty on screen + a reason for every release (W0.2, W0.6)
- Read: guide §3 (F8, F11), §5 W0.2, W0.6. Code: `engine/verity/ridecheck.py`, `voice_loop.py`
  (`DECISION_TEMPLATES`), the dispatch response builder in `api/service.py` / `api/models.py`, `tests/test_driver_demo.py`.
- `confidence_display` ("95%+", "under 5%", else the rounded %) on dispatch and ride-check responses; reason
  strings and voice templates use it. Find why the driver demo gives 1.0 saved vs 0.95 live; seed it; pin the value.
- `reason` sentence naming the charger that carries the decision (template in W0.6), reused by speech.
- Acceptance: grep of `results/`, `docs/api_examples/` and the templates finds no "100%".
- Human: none.

### S3 — Green on every machine + minimum hardening (W0.4, W0.7)
- Read: guide §3 (F10, F14 CORS part), §5 W0.4, W0.7. Code: `tests/test_bmw_i3.py`, the E1 fingerprint
  helper (grep `fingerprint` in `experiments/` and `engine/`), `api/main.py`, `api/service.py` caches.
- `.gitattributes`; CRLF test builds its bytes in the test body; E1 fingerprint on newline-normalised content
  (DECISIONS.md entry; do **not** re-run the held-out evaluation); `.github/workflows/ci.yml` (Ubuntu + Windows).
- LRU-bounded plan and ride caches; CORS origins from env `VERITY_CORS_ORIGINS` (add to `.env.example`);
  per-IP rate limit on the voice endpoints (no new dependency without asking; a small in-process limiter is fine);
  `/api/health` adds backend, node/rel counts, Sarvam key present (bool).
- Human: none. (The CI badge goes into the README in S11.)

### S4 — One source of truth + works with wifi off (W0.3, W0.5)
- Read: guide §3 (F4, F9), §5 W0.3, W0.5. Code: `scripts/export_web_snapshot.py` (read only, to mirror its
  bundle), `scripts/export_api_examples.py`, the voice synthesis path in `voice.py` / `voice_loop.py`.
- `scripts/export_snapshot.py` → `results/snapshot.json` (adds ride-check, driver demo, `real_data_summary.json`);
  test that every snapshot value equals the result file it came from; `n_runs` in every forecast/dispatch/ride response.
- Cached audio for the demo's spoken answers, committed under `data/audio_cache/`, served only when Sarvam is
  unreachable, flagged `cached: true`. Generating it costs Sarvam calls: allowed here (a handful).
- Acceptance: a test that runs the demo's API calls with network access disabled (monkeypatched) and all succeed.
- Human: Sarvam credits confirmed. Afterwards Ansh runs `export_web_snapshot.py` for the frontend.

### S5 — Sarvam client up to date + spoken ride-check (W3.1, W3.2)
- Read: guide §8 W3.1, W3.2, W3.7. Code: `engine/verity/voice.py`, `voice_loop.py`, `ridecheck.py`, `api/main.py`.
- Verify the current Sarvam API reference first (model ids, STT modes, request fields) and log what was read.
  Model ids configurable via env; keep the paid-call log.
- `POST /api/voice/ride-check`: hear → rule parse → LLM fallback (strict schema, 4 s timeout) → confirm step
  (response carries `needs_confirmation` + read-back text; a second call with `confirmed=true` decides) →
  `ride_check` → speak. Per-stage timings in the response. Place parsing uses a small committed NCR gazetteer
  for now; S6 swaps in the full-text index.
- Tests with recorded/fake Sarvam responses (no paid calls in pytest). A few real calls to measure latency.
- Human: Sarvam credits; Ansh speaks to it in Hindi/Hinglish afterwards and lists misparses.

### S6 — Real structure in the graph + names + graph endpoints (W2.1, W2.5, W2.6)
- Read: guide §3 (F6), §7 intro, W2.1, W2.5, W2.6, W2.7. Code: `engine/verity/trust/graph.py`,
  `graph_build.py`, `engine/verity/sites.py`, `tests/test_graph_parity.py`.
- Physical `Site` nodes (150 m clusters), `SiteType`; `Plan` STOPS_AT / BACKUP_FOR / AVOIDED; `Channel`
  via VIA; `Alias` NAMES + full-text index (in-memory equivalent for the fallback; never invent a charger).
- `GET /api/graph/why/{plan_id}`, `GET /api/graph/charger/{id}`, `POST /api/reports` (trust before/after).
  Every graph response carries `cypher` and `query_ms`.
- Parity tests memory vs Neo4j (skipped cleanly when Neo4j is unreachable). Swap S5's place parser to the index.
- Human: **Aura instance alive** (guide W2.0) for the Neo4j half; the in-memory half is built regardless.

### S7 — Spoken report writes to the graph + Open Charge Map check-ins (W3.3, W1.2)
- Read: guide §8 W3.3, §6 W1.2, §3 (the `NOW` note). Code: `voice_loop.py` (`parse_status`),
  `engine/verity/trust/reports_sim.py` (`NOW`), `api/service.py` recency, `engine/verity/evidence.py`.
- `POST /api/voice/report`: status + charger (index) → `POST /api/reports` path; consent flag required;
  real person → `driver_voice`, `is_simulated=false`; reply states trust before/after and whether the plan moved.
- Fix the `NOW` clock issue (real November reports must not be in the engine's future); DECISIONS.md entry.
- `scripts/fetch_ocm_checkins.py`: official API, cached in `data/cache/`, mapped to `Report` with source
  `public:ocm_checkin`; report n whatever it is.
- Human: `OCM_API_KEY` in `.env`.

### S8 — Graph decides independence + proposes chains (W2.2, W2.3, E6)
- Read: guide §7 W2.2, W2.3. Code: `sites.py` common-cause grouping, `dispatch.py` `_adjacency` and
  `k_shortest_paths` (~l.228), `trust/graph.py`.
- Shared site/operator/hardware query feeds common-cause groups (operator beta labelled as an assumption).
- Cypher candidate chains with a parity test against `k_shortest_paths` on the demo routes; Python stays the
  fallback; p95 on Aura to `results/` when reachable.
- `experiments/e6_operator_correlation.py` → `results/` (simulated, labelled). Publish either way.
- Human: Aura alive for the Cypher half.

### S9 — Report rings as graph analytics (W2.4, E5b)
- Read: guide §7 W2.4. Code: `engine/verity/trust/rings.py` (`group_rings`), `experiments/e5_trust_recovery.py`.
- Jaccard agreement edges + communities (pure-Cypher/union-find fallback first; Aura Graph Analytics only if
  its current client is verified and works); `ring_id` written back to `Source`.
- `experiments/e5b_ring_overlap.py` on the 20 simulated worlds: precision/recall, honest repeat reporters flagged.
- Human: none required (Aura optional).

### S10 — Real data in, ledger out (W1.3, W1.4, W1.5, W3.6) — run on or after Thu 19 Nov corpus freeze
- Read: guide §6 (all), §8 W3.6. Code: `scripts/run_real_data.py`, `results/real_data_summary.json`,
  `experiments/e1_india_real.py`, `e3_real_evidence.py`, `voc_app_reviews.py`, the BMW i3 loader + `docs/BMW_I3_NOTES.md`.
- `uv run python scripts/run_real_data.py`; `GET /api/evidence/ledger`; `driver_real` scenario if ≥3 real
  dated reports near the drop (W1.3).
- W1.4: fix the two bias causes on training trips only → freeze → DECISIONS.md → open held-out **once** with
  `final_evaluation=True` → publish whatever comes out.
- Re-run S4's snapshot export so the new real numbers are in `results/snapshot.json`.
- Human: corpus rows verified and uploaded to `data/corpus/` (guide W1.1 minimums).

### S11 — Front door, disclosure, deploy (W5.1, W5.2, W5.3) + Done-means audit
- Read: guide §10 W5.1–W5.3, §11 "Done means". Code: `README.md`, `docs/AI_USAGE.md`, `pyproject.toml`.
- README moved to `docs/ENGINE_NOTES.md` unchanged; new one-screen README (CI badge, ledger table generated
  from `results/real_data_summary.json`, Neo4j and Sarvam in four lines each). GIF/QR: leave marked slots for Ansh.
- `Dockerfile` (+ `.dockerignore`), secrets from env only; deploy notes for a non-sleeping HTTPS host and the
  hotspot + tunnel fallback (research current free tiers; record what was checked).
- `docs/AI_USAGE.md`: one row per W session. Update CLAUDE.md "Commands" with every new command.
- Walk the guide's "Done means" checklist; tick what is verifiable, list what is not and why.
- Human: pick the host; the GIF and QR after deploy.

### S12 — Venue kit for voice measurement + extra languages (W3.5, W3.4) — pre-build only if organisers allow
- Read: guide §8 W3.4, W3.5, §12 first question. Code: `voice.py`, `voice_loop.py`.
- `POST /api/lab/voice-sample` (consent required; stores transcript, parsed, expected, language, device; no names)
  + `scripts/voice_real_eval.py` → `results/voice_real.json` (Wilson intervals by language, STT p50/p95,
  v3 vs v4 and two modes on the same audio).
- Templates in three more Bulbul languages (Ansh picks them), committed and marked machine-translated.
- Human: organisers' answer on pre-built code; the three languages; at the venue, 30+ utterances from 10+ speakers.

## Human tasks that block sessions (do these early)

| Needed by | Task |
|---|---|
| S4, S5, S7, S12 | Sarvam credits confirmed |
| S6, S8 (Neo4j halves) | New AuraDB Free instance in `.env`; `uv run python scripts/neo4j_smoke_test.py` passes |
| S7 | Open Charge Map API key as `OCM_API_KEY` in `.env` |
| S10 | Teammate's corpus (guide W1.1): 30 trips / 10 pages, 40 charger rows / 15 chargers, 100 reviews / 50 labelled |
| S11 | Hosting choice |
| S12 | Organisers' rule on pre-built code; three extra languages |

# Finale build W — progress

Authority: `docs/TASKS_W.md` (sessions) and the Opus 5.5 winning-build guide in `docs/`.
Start every session with `/clear`, then `continue docs/TASKS_W.md`. One session per context.

## Sessions

- [x] S1 Bingo Line agrees with the plan (W0.1) + deadline line (W0.0)
- [x] S2 No certainty on screen + reason sentence (W0.2, W0.6)
- [x] S3 Green on every machine + hardening (W0.4, W0.7)
- [x] S4 One source of truth + offline (W0.3, W0.5)
- [x] S5 Sarvam client + spoken ride-check (W3.1, W3.2)
- [ ] S6 Graph structure + names + graph endpoints (W2.1, W2.5, W2.6)
- [ ] S7 Spoken report into the graph + OCM check-ins (W3.3, W1.2)
- [ ] S8 Graph independence + Cypher chains + E6 (W2.2, W2.3)
- [ ] S9 Rings as graph analytics + E5b (W2.4)
- [ ] S10 Real data in, ledger out (W1.3, W1.4, W1.5, W3.6) — after corpus freeze
- [ ] S11 README, disclosure, deploy + Done-means audit (W5.1–W5.3)
- [ ] S12 Venue voice kit + extra languages (W3.5, W3.4)

## Baseline (before S1)

- HEAD `9302586` (F12). Guide reports 362 passed / 1 failed (CRLF, Linux only) / 1 skipped on Linux.
- Stray nested clone `verity/` in the repo root (older R-series history of the GitHub remote). Untracked;
  never stage it. Ansh can delete it or move it out of the repo.
- 2026-11-16: new Aura instance live (username is `neo4j`, not the instance id). Smoke test OK.
  `tests/test_graph_parity.py`: 9 passed, 1 failed -
  `test_simulated_reports_and_sources_are_tagged_and_syncing_again_changes_nothing`: re-sync with only
  `demo_reports` drops REPORTED 28 -> 14 and Source 24 -> 10 (likely the service's own reports, e.g. the
  driver-demo scenario, being removed by the replace-style sync). Probably never run live before (skips when
  Neo4j is unreachable). **S6: diagnose first, decide sync-vs-test, log in DECISIONS.md.** Until fixed, the
  full `uv run pytest` is red whenever Neo4j is reachable. `VERITY_GRAPH=memory` does NOT skip these tests (checked). Sessions S1-S5
  run the suite through the Bash tool with an empty URI, which skips the 10 parity tests (checked):
  `NEO4J_URI= uv run pytest`. Not PowerShell: `$env:X=''` deletes the variable there, so .env would be read
  again. Report that the parity tests were skipped this way.

## Handoff notes (newest last, at most 15 lines per session)

### S1 (2026-11-16) — done
- W0.0 `56ddaf2`: CLAUDE.md deadline line -> finale 21 Nov 2026, 07:30 IST.
- Aura keep-alive `5c2bd30`: `scripts/aura_keepalive.py`, Windows task `VerityAuraKeepAlive` 10:00 + 22:00 IST until 23 Nov
  (write-and-delete, log `data/cache/aura_keepalive.log`). Only runs while the laptop is on; first two runs OK.
- W0.1: F2 confirmed by a failing test first. Bingo Line + live update now use the plan's own policy
  (`policy_ahead`: next stop, its backups, later stops + backups, destination) and one threshold (`meets_target`,
  displayed confidence). Live-update precedence in DECISIONS.md: confidence meeting target -> CONTINUE.
- New tests: F2 repro, 50 seeded trips (30 GO: expected >= line at every km, on-plan median -> CONTINUE), V12
  invariants, precedence. Suite: 369 passed, 10 skipped (parity tests skipped with `NEO4J_URI=`).
- Numbers that moved: V12 line at km 34 72 -> 46 (expected 50); forecast_update.json SLOW_DOWN -> CONTINUE
  (line 63 -> 35 at km 57.9, confidence 1.0). No pinned decision changed (V07 NO_GO 80 %, V12 GO 95 %, V19 IE).
  E3/E4 outcomes identical; only timing fields changed.
- Manual step for Ansh: the frontend snapshot (`web/js/snapshot.js`) still holds the old V12 line; regenerate it
  with `uv run python scripts/export_web_snapshot.py` when convenient (S4 adds the engine-side snapshot).

### S2 (2026-11-16) — done
- W0.2 `696edd5`: `confidence_display` ("95%+" / "under 5%" / rounded %) on dispatch + ride-check (contract v3);
  ride-check reasons and both voice templates use it ("95 percent or more", "95 प्रतिशत या उससे अधिक").
- F8 cause found: not machine noise. `driver_demo.py` hashed `soc_pct` 45 (int), the API 45.0, so the seeds differed.
  Requests are now canonicalised through their pydantic model before hashing (API seeds unchanged). Pinned 0.95 / "95%+".
- W0.6: dispatch `reason` names the charger that carries the decision (Ansh's rule: most depended-on, falling back to
  most-used when no single failure costs > 4 points); `PlanEval.charged_at` records where each run charged (no new
  random draws). Spoken via `render_decision(..., reason_facts=...)` in en-IN and hi-IN.
- Numbers: V12 reason = backup "EV charging station (OpenStreetMap)", 95%+ works, 10 reports, reach 90%. Pinned decisions
  unchanged (V07 NO_GO 80%, V12 GO 95%+, V19 IE 90%). No "100%" in results/*.json or docs/api_examples/ (results/logs/tests.log
  has pytest's own "[100%]" progress marker only). Suite: 379 passed, 10 skipped (parity skipped with `NEO4J_URI=`).
- Manual for Ansh: check the Hindi reason templates in `voice_loop.py` (`REASON_TEMPLATES["hi-IN"]`); regenerate
  `web/js/snapshot.js` (`scripts/export_web_snapshot.py`) and show `confidence_display` + `reason` in the frontend.

### S3 (2026-11-16) — done
- W0.4 `49ee00e`: `.gitattributes` (LF text; BMW CSV fixtures `-text`, now stored as CRLF); the CRLF test builds its own bytes (old
  test fails on LF fixtures, new passes on both); E1 fingerprint + audit hash hash newline-normalised bytes. One-time re-stamp of
  those two hash fields only, after the old hashes were re-verified; every E1 number identical, no held-out trip opened
  (`tests/test_e1_fingerprint.py` guards it on every OS). `.github/workflows/ci.yml`: Windows only (Ansh's decision, later the same day), Python 3.13, no secrets.
- W0.7 (next commit): LRU(300) plan + ride caches (`api/limits.py`, atomic `get`); CORS from `VERITY_CORS_ORIGINS`; voice rate limit 20/min per
  address shared by tts+stt (429 + `Retry-After`, exposed through CORS; IPv6 per /64); `/api/health` adds `graph{backend,nodes,relationships}` +
  `sarvam_key_present`. `counts()` on both graph backends: memory 288/302 = live Aura 288/302 (by hand, not pinned). Contract v3 + CHANGES.
- Suite: 442 passed, 10 skipped (parity, `NEO4J_URI=`). No pinned decision moved (V07 NO_GO 80%, V12 GO 95%+, V19 IE 90%). Code review run
  (`code-reviewer`): no CRITICAL/HIGH; the MEDIUMs were fixed, limits logged in DECISIONS.md. Only `docs/api_examples/health.json` re-exported.
- Manual for Ansh: (1) push, then watch the FIRST CI run (Windows): it has never run; (2) confirm the
  20/min voice default (`VERITY_VOICE_RATE_PER_MIN`); (3) a hosted deploy must set `VERITY_TRUST_PROXY=1` and `VERITY_CORS_ORIGINS` (S11);
  (4) `SarvamClient` reads `.env` only, so an env-var-only deploy shows `sarvam_key_present: false` (S5/S11).
- Seen, not fixed: with live Aura, dispatch V12 `compute_ms` ~10 s vs ~2.4 s in memory (network writes; S8). Run `export_api_examples.py` with `VERITY_GRAPH=memory`.

### S4 (2026-11-16) — done
- W0.3 `01fc5be`: `n_runs` on dispatch / forecast-update / ride-check. `scripts/export_snapshot.py` -> `results/snapshot.json` (API examples incl. new
  `ride_check.json` + `driver_demo.json`, E1-E5, run_all, `real_data_summary.json`; `sources` names each file; no timestamp). `export_web_snapshot.py` now wraps
  the same bundle. `tests/test_snapshot.py` fails if the snapshot, a section, or a saved example differs from its file / the live engine (F4's V07 0.85-vs-0.80).
- W0.5 `613c95d`: dispatch returns `speech` {en-IN, hi-IN} (the exact sentence to speak). `data/audio_cache/`: 6 Sarvam TTS clips, 6 paid calls (all 200, 0.9-3.9 s),
  served with `cached: true` only when live TTS fails. `tests/test_offline_demo.py`: all non-loopback connections refused, Neo4j unreachable -> memory, the demo's calls
  make ZERO connection attempts. Found + fixed: the spoken reason said "target 90%" (now in words).
- Suite: 495 passed, 11 skipped (10 parity with `NEO4J_URI=`, 1 = `web/js/snapshot.js` stale). No pinned decision moved. Paid-call budget used: 6 TTS.
- Manual for Ansh: (1) `uv run python scripts/export_web_snapshot.py` (refreshes web/js/snapshot.js: F4's stale numbers, plus rideCheck / driverDemo / real_data / n_runs);
  (2) frontend: speak `speech[lang]` through `/api/voice/tts`, show `n_runs`, drop hardcoded 1,500/2,000; (3) F9's CDNs (Leaflet, fonts, tiles) are in `web/` and still there;
  (4) listen to the 6 clips in `data/audio_cache/` (Hindi quality is yours to judge). Any template/number change -> `scripts/build_audio_cache.py` (paid) or the offline test fails.
- Seen: the V12 GO answer is 14.5 s spoken (decision + Bingo + reason): too long for the 3-second goal; S5's spoken ride-check must be short. STT has no offline path.

### S5 (2026-11-16) — done
- W3.1 `1899947`: `VoiceModels` + `SARVAM_*` settings, key read from the environment too, per-call chat timeout, `docs/SARVAM_API_NOTES.md` (pages read AND not read).
  W3.2 `2a0cd0a` (places, slot parsing, 4 s model fallback that only fills gaps, fixed-template answer) + `2e38573` (the endpoint): `POST /api/voice/ride-check`, two calls:
  audio -> read-back or a question (never decides); JSON `confirmed` -> the ordinary ride-check, spoken. `api/speech.py` is the one "live TTS, else the cached clip" path (also `/api/voice/tts`).
- Measured, not assumed (`scripts/bench_voice_ride_check.py`, 22 paid calls, SYNTHETIC audio, n=2 per cell, `results/voice_ride_check_latency.json`): `saaras:v3` transcribe 0/4 correct
  (writes number words), codemix 2/4, `saaras:v4` 4/4 (digits), all ~0.5-0.6 s median -> **default STT is now `saaras:v4`**. Chat fallback ~0.2 s on all four configs (the old 22.9 s did not reproduce); default unchanged.
- Cache: the pinned ride's read-back + answer, EN + HI (4 paid TTS, `scripts/build_audio_cache.py`); `tests/test_offline_demo.py` covers them and call 2 with Sarvam unreachable.
- Suite: 598 passed, 11 skipped (10 parity with `NEO4J_URI=`, 1 = `web/js/snapshot.js` stale). Engine untouched, so no pinned decision moved. Paid calls in S5: 4 TTS + 22 bench = 26 (S4: 6).
- Code review (`code-reviewer`): no CRITICAL/HIGH; 4 of 7 MEDIUMs fixed (bounded body + 413, `fullmatch` language, same-place in call 1, model-written names not spoken) and
  call 2 now REFUSES rides with no committed route (422 `unsupported_ride`; only 18 -> IGI T3 has one, so "Sector 62 -> airport" is read back and then refused: S6 grows the gazetteer + routes).
  Left as logged limits in DECISIONS: one rate-limit hit buys up to 3 paid calls; a bare number in the transcript is taken as the battery.
- NOT measured: the 3-second goal end to end (per-stage `timings_ms` are in every response; the TTS step and the Monte Carlo were not benched), real microphone speech, noise, accents.
- Manual for Ansh: (1) speak Hindi/Hinglish to it and list the misparses (the frontend does: multipart `audio` -> show `prompt` -> JSON `confirmed`); (2) check the Hindi read-back/answer sentences (`ride_voice.py`);
  (3) `web/js/snapshot.js` is still stale (`scripts/export_web_snapshot.py`); (4) CONTRACT: `docs/API_CONTRACT.md` last section.
- Seen, not fixed: `get_voice_client` builds a new `SarvamClient` (a new HTTP connection pool, a new TLS handshake) on every request; one shared client would cut that from every voice call (S11 / when the 3 s goal is measured).
- S6 (W2.1, W2.5, W2.6) not started.

### S6 (2026-11-17) — IN PROGRESS (not ticked): diagnosis + design done, schema code not written yet
- Done + committed `0d50187`: the failing live parity test was a wrong TEST (re-synced one report set, the service syncs two); sync unchanged. All 10 parity tests pass live on Aura (run WITHOUT `NEO4J_URI=`).
- Written, unused so far: `trust/channels.py` (`channel_of(source_id)`: public:X -> X, driver:X -> X, feed: -> operator_feed, user-/demo-user-/ride-demo-user-/ring- -> app_user, else other) and `trust/graph_types.py` (SiteRec, PlanRec + stop/backup/avoided recs, AliasRec, NameHit, GraphView with `executed` flag: memory twin returns the Cypher it WOULD run).
- Design (follow it, do not re-derive): (1) `trust/graph_structure.py` mixins keep graph.py < 800 lines. Sites: `sync_sites()` rebuilds Site (150 m, `assign_sites` over all non-TEST chargers) + SiteType + AT + OF_TYPE and deletes the legacy type-keyed Site nodes; remove Site/AT from `_CHARGER_UPSERT`; update `counts()` (memory) and tests/test_graph.py counts + `NODE_LABELS == 7` assertion. VIA: add Channel to `_REPORT_UPSERT`. Plans: `add_plan` (replace by id), `sweep_plans(keep=300, max_age 24 h)` called from `_sweep_if_due`; AVOIDED = route chargers inside a stop's `ALT_AHEAD_KM` window that `usable_alternate` refuses (reuse ONE rule: split into `avoided_reason`), capped at 30 per plan with `avoided_total` kept.
- Names: directory has 77/207 chargers called "EV charging station (OpenStreetMap)" and NO name containing "sector"; `charger_evidence.csv` has no rows. So aliases = operator names + optional `data/gazetteer/charger_aliases.json` + corpus landmarks (none yet). Neo4j full-text only RETRIEVES the top candidates; the ask-or-answer decision is a backend-independent Python score (share of non-generic spoken words found), so memory and Neo4j agree. "Sector 62 wala charger" must come back as "Which one?" with no guess until verified aliases exist.
- Endpoints: both backends fetch rows, ONE pure function builds nodes/edges (parity by construction). `POST /api/reports`: consent required, channel `driver_voice`, `is_simulated=false`, source `driver:driver_voice`, rate-limited; trust before/after computed at `max(NOW, report time)` (interim; S7 fixes the clock and must also invalidate the plan cache).
- DEVIATION to confirm with Ansh: I plan NOT to merge charger names into the S5 ride-place parser (names like "Subway", "Tata" would hit ordinary words in a pinned, tested flow; and no routes exist for them). The charger index serves S7's spoken report instead. Log in DECISIONS when done.

# Finale build W — progress

Authority: `docs/TASKS_W.md` (sessions) and the Opus 5.5 winning-build guide in `docs/`.
Start every session with `/clear`, then `continue docs/TASKS_W.md`. One session per context.

## Sessions

- [x] S1 Bingo Line agrees with the plan (W0.1) + deadline line (W0.0)
- [x] S2 No certainty on screen + reason sentence (W0.2, W0.6)
- [x] S3 Green on every machine + hardening (W0.4, W0.7)
- [x] S4 One source of truth + offline (W0.3, W0.5)
- [ ] S5 Sarvam client + spoken ride-check (W3.1, W3.2)
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

### S5 (2026-11-16) — IN PROGRESS: stopped at the usage limit; box NOT ticked; S6 not started
- Done + committed: W3.1 (`VoiceModels` + `SARVAM_*` settings, key read from the environment as well as `.env`, per-call chat timeout, `docs/SARVAM_API_NOTES.md`
  incl. the pages NOT read) and the W3.2 building blocks: `places.py` + `data/gazetteer/ncr_places.json` (3 places, coordinates copied from DRIVER_DEMO),
  `ride_voice.py` (rule slots, 4 s LLM fallback that only fills gaps, read-back / clarify, fixed-template spoken ride answer), `speech` on `POST /api/ride-check`,
  and the endpoint's models in `api/models.py`. Suite: 574 passed, 11 skipped, run with `--ignore=tests/test_voice_ride_api.py`.
- Resume here: (1) `tests/test_voice_ride_api.py` is written, UNTRACKED and RED: it is the spec for `POST /api/voice/ride-check`. Still to write: `api/speech.py`
  (`synthesise`: live TTS, else the cached clip; also use it in `/api/voice/tts`), `api/voice_ride.py` (`hear_stage`, `decide_stage`, per-stage timings), the endpoint
  in `api/main.py` (multipart audio -> stage confirm/clarify; JSON `confirmed` -> decided/not_confirmed; both behind `limit_voice`). Then commit that test file.
  (2) NO real STT/chat call has been made yet: write a bench on synthetic TTS audio (saaras v3 vs v4, modes, `sarvam-105b` reasoning none/omit vs `sarvam-105b-conversations`)
  -> `results/voice_ride_check_latency.json`; pick the defaults by measurement and log them in DECISIONS (W3.1). (3) Add the pinned ride's read-back + answer sentences
  to `scripts/build_audio_cache.py` (4 more paid TTS) so `tests/test_offline_demo.py` covers them. (4) Contract v3 + CHANGES + DECISIONS + CLAUDE.md for the endpoint; tick S5.
- S6 (W2.1, W2.5, W2.6) not started. Paid calls this session: 6 TTS (audio cache) only. Ansh's Hindi/Hinglish misparse test comes after the endpoint exists.

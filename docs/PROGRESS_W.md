# Finale build W — progress

Authority: `docs/TASKS_W.md` (sessions) and the Opus 5.5 winning-build guide in `docs/`.
Start every session with `/clear`, then `continue docs/TASKS_W.md`. One session per context.

## Sessions

- [x] S1 Bingo Line agrees with the plan (W0.1) + deadline line (W0.0)
- [x] S2 No certainty on screen + reason sentence (W0.2, W0.6)
- [x] S3 Green on every machine + hardening (W0.4, W0.7)
- [x] S4 One source of truth + offline (W0.3, W0.5)
- [x] S5 Sarvam client + spoken ride-check (W3.1, W3.2)
- [x] S6 Graph structure + names + graph endpoints (W2.1, W2.5, W2.6)
- [x] S7 Spoken report into the graph + OCM check-ins (W3.3, W1.2)
- [x] S8 Graph independence + Cypher chains + E6 (W2.2, W2.3)
- [x] S9 Rings as graph analytics + E5b (W2.4)
- [x] S10 Real data in, ledger out (W1.3, W1.4, W1.5, W3.6) — BUILT AROUND an empty corpus: W1.4 DONE, W1.5 DONE, W1.3/W3.6 wait for the 19 Nov rows (re-run S10 steps then)
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

### S6 (2026-11-17) — done
- Parity test fixed first (`0d50187`): it was the TEST (re-synced one report set, the service syncs two); the sync is right. All 10 original parity tests pass LIVE on Aura (run without `NEO4J_URI=`).
- W2.1: `Site` = physical 150 m cluster (185 sites) + `SiteType`/`OF_TYPE`; `Plan` STOPS_AT/BACKUP_FOR/AVOIDED written on every dispatch (newest 300, 24 h); `Source-[:VIA]->Channel` (`trust/channels.py`). Memory and Aura agree: health counts 479/519, sites, aliases, plan, audit views (all live-tested).
- W2.5: `charger_names` full-text index + in-memory twin; the index only retrieves, `names.decide` answers or asks (match only when ONE charger has every distinctive word, else top 3, else none). Aliases = operators + `data/gazetteer/charger_aliases.json` (EMPTY: add verified entries only). Honest result: "Sector 62 wala charger" -> nothing (no directory name says "sector"; 77/207 are "EV charging station (OpenStreetMap)"; the corpus has no rows).
- W2.6: `GET /api/graph/why/{plan_id}`, `GET /api/graph/charger/{id}`, `POST /api/reports` (consent, cooldown, own rate limit, trust before/after); every graph response has `cypher`, `query_ms`, `backend`, `executed`. Contract v4. Aura timings seen: why ~370 ms, charger ~620 ms.
- Review (`code-reviewer`): 2 HIGH + 7 MEDIUM fixed or logged in DECISIONS (alias `head()` bug, report stacking, clock, thread-safety, backend flip on a rejected plan write ...). Suite WITH live Aura (all parity tests, no `NEO4J_URI=`): 667 passed, 1 skipped (`web/js/snapshot.js` stale). Offline (`NEO4J_URI=`) it skips the parity tests as before.
- DEVIATION (Ansh to overrule): the S5 ride-place parser is NOT swapped to the charger index (charger names like "Subway"/"Tata" would match ordinary words in a pinned flow; no routes exist for them). Logged in DECISIONS.
- Manual for Ansh: (1) the frontend can now draw `/api/graph/why/{plan_id}` (nodes/edges/cypher) and show the backend pill; (2) add verified spoken names to `data/gazetteer/charger_aliases.json` (text, charger_id, source) when you visit/verify chargers; (3) confirm the 10-minute per-charger report cooldown; (4) `export_web_snapshot.py` is still pending from S4/S5.
- For S7: `names.decide(graph.search_names(spoken))` is the resolver for the spoken report; `service.add_report` is the write path (plan cache is NOT invalidated: S7 must recompute "did the plan move"); fix the `NOW` clock (trust uses it); live reports are never expired.

### S7 (2026-11-17) - done (OCM half blocked on the network)
- W3.3 `d46dd33` + review fixes: `POST /api/voice/report` (hear, read back, NEVER writes; status by rule, charger by `names.decide` over the name index) and `/report/confirm` (needs `confirmed` + `consent`; writes through the `POST /api/reports` path; re-plans up to 2 cached plans that use the charger; reply = trust before/after + whether the plan changed since it was made). Contract v5. Audio never stored; transcript stored as the note.
- `NOW` fixed (`trust/clock.py`): simulated reports stay on the frozen 27 Oct clock (pinned decisions unchanged: V07 NO_GO 80 %, V12 GO 95 %+, V19 IE 90 %); real reports age by real time. Before, a real November report stayed age 0 for ever.
- Review (`code-reviewer`): 1 HIGH + 6 MEDIUM, all fixed or logged in DECISIONS. HIGH: scripted (`from_real_person=false`) notes are now refused unless the server sets `VERITY_ALLOW_SCRIPTED_REPORTS=1` (anyone could plant a never-aging simulated report). Re-plan no longer pops the plan; pending/failed re-checks are said, never "did not change".
- W1.2: `verity/ocm.py` + `scripts/fetch_ocm_checkins.py` + tests (SYNTHETIC point). **BLOCKED:** the one fetch got `ConnectError 10054` from api.openchargemap.io (key IS in .env); n = 0 real check-ins, `results/ocm_checkins.json` not written. Field names are from memory (docs unreadable, reference data 403): `mapping_verified: false`. Manual: re-run from another network, check `data/cache/ocm_checkins_raw.json`, report the n. Details: `docs/EVIDENCE_STATUS.md`.
- Suite offline (`NEO4J_URI=`): 688 passed, 21 skipped (live-Aura tests + stale `web/js/snapshot.js`); the live parity tests were NOT re-run this session (graph code untouched apart from `add_report` arguments). No paid Sarvam calls in S7; the new reply sentences are not in the offline audio cache (live TTS, else text).
- Manual for Ansh: (1) the frontend: audio -> `/api/voice/report` -> show `prompt` + candidates -> consent tick -> `/report/confirm` -> speak `speech`; (2) listen to the Hindi sentences in `report_voice.py` (machine-written); (3) real Hindi speech writes charger names in Devanagari, which will not match Latin directory names (not measured; needs transliteration or verified aliases in `data/gazetteer/charger_aliases.json`); (4) decide whether to set `VERITY_ALLOW_SCRIPTED_REPORTS=1` for the demo; (5) `export_web_snapshot.py` still pending.

### S8 (2026-11-17) - done
- W2.2: chargers of one operator share an operator-wide common cause (`BETA_OPERATOR = 0.2`, an ASSUMPTION; Site wins when both apply). The graph answers "who shares a Site/operator/hardware" in one query (`SHARED_CAUSES_CYPHER`, memory twin, live parity OK); hardware is always null (no field in the directory). `link_operators=False` keeps E3/E4 on the Site-only model: E3 re-run identical except timings. One existing fixture (`test_dispatch.charger`) set to operator "" (it declared one placeholder operator for six co-located chargers).
- W2.3: `chains_cypher` + `dispatch.graph_chains`; Yen stays the fallback. Live parity on V07/V12/V19: same chains. Latency on Aura (216 queries): Cypher p50 121 ms / p95 517 ms vs Yen 1.5 / 5.2 ms per proposal -> `results/chains_latency.json`. The graph is the visible path, not the fast one.
- E6 (300 trips x 6 scenarios, simulated): plan changes 26-28 % but the control changes 26 % too (noise); stranding differences inside the 95 % intervals; both planners over-claim (95 % vs 90 % realised in the harshest case). Published as is.
- Pinned decisions unchanged (V07 NO_GO 80 %, V12 GO 95 %+, V19 IE 90 %; dispatch examples byte-equal bar compute_ms). Ride-check secondary numbers moved (confidence_as_primary 0.90 -> 0.95 on one option); the decision did not. Examples + snapshot re-exported.
- Neo4j was unreachable for a while (Bolt port timed out, same network blocked OCM); it came back and every live test ran on Aura. Docker was started once and closed again, not used.

### S9 (2026-11-17) - done
- W2.4: `AGREEMENT_CYPHER` (Jaccard agreement edges between sources), `graph.communities`, `Source.ring_id` written back by `scripts/graph_rings.py`; pure definition in `trust/ring_edges.py`; memory communities == numpy detector rings; live Cypher parity OK. Aura Graph Analytics NOT used (client not verified); pure Cypher + union-find is the guide's fallback.
- E5b: E5's burst detector misses the 10-day evasive ring (recall 0); overlap with no window finds all of it but flags 19-28 % of honest repeat reporters with E5's thresholds; thresholds tuned on dev seeds (min_shared 16) flag none, but that is fitted to simulated sizes (upper bound). The simulator gained `WorldConfig.n_repeat` (drawn last: E5 worlds unchanged).

### S10 (2026-11-17) - done around an EMPTY corpus
- W1.4 DONE: i3 prior v2 frozen (cited CdA 0.69 m2; per-vehicle reference capacity x1.25 because the global 1.05 bound is fingerprinted by E1 and was left alone), logged and committed BEFORE the held-out split was opened once (`--final`, lock file). Held-out: 19 trips, 399 km: capacity error -10.5 % (truth inside the 95 % CI, outside 80 %; F9's prior -13.0 %); per-trip SoC drop mean error -0.1 %, median 0.95 points, but parameter-only bands cover 26 % / 47 %. Capacity claim NOT validated: Range Passport stays in Lab. Training-trip record: -14.5 % -> -12.1 %.
- W1.5 DONE: `GET /api/evidence/ledger` (contract v6). W1.3: `driver_real` is a status (WAITING_FOR_DATA, 0 real reports). W3.6 not run (0 review rows). `run_real_data.py --no-paid` re-run, all steps OK, all real rows 0. OCM re-fetched once the network cleared: 78 points, 0 check-ins (no `UserComments` key at all): n = 0, mapping still unverified.
- Manual for Ansh: (1) the corpus rows (guide W1.1) by 19 Nov, then `uv run python scripts/run_real_data.py` and `scripts/export_snapshot.py`; (2) `export_web_snapshot.py` for the frontend (still pending from S4-S7); (3) decide whether `BETA_OPERATOR = 0.2` and the i3 ceiling scale 1.25 are acceptable assumptions; (4) a first CI run (Windows) is still unseen.

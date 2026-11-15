# Verity — winning build guide for Opus 5.5 (hackFront India finale)

Nov 14, 2026 · @Ansh

## 1. Verdict

Verity has a finalist-grade engine and a demo that currently hides it. On a clean Linux clone, 362 of 364 tests pass, the ride-check answers in 0.45 s, and the REAL / SIMULATED discipline is rare for a student team. Four things stand between that and the prizes.

1. **No real Indian number exists.** Every REAL result in the repo is NOT\_RUN at n=0. The problem statement is in the "Measurement and data" category and asks you to "tell an Indian driver the truth, with a confidence level attached".
2. **Neo4j is switched off and nothing misses it.** The Aura host no longer resolves, and the README says the in-memory graph "gives the same answers". A track judge who asks "what breaks if I delete Neo4j?" gets the answer "nothing".
3. **The best story is not on screen.** The cab-driver ride-check in rupees and the Hindi voice loop exist in the engine and API. The frontend never calls them, and its saved data is from 30 October.
4. **The signature feature contradicts itself.** The Bingo Line sits above the expected battery for 32 km of a GO plan, and a saved API example says "100% confidence" and "slow down, below the Bingo Line" in the same response.

All four are fixable before 21 November. The engine does not need more features; it needs fewer, each one real, visible and consistent.

| Prize | What decides it | Where Verity is today | What this guide changes |
| --- | --- | --- | --- |
| Overall | Five published criteria: problem and track alignment, innovation and effectiveness, technical implementation and code quality, functionality and prototype validation, critical thinking and practical implementation | Strong on implementation and critical thinking; weak on validation (n=0) and on a demo a judge can follow in 3 minutes | One hero demo, at least two real measured numbers, the Bingo Line fixed, everything deployed and offline-safe |
| Best Use of Neo4j | Track tagline: "Build where the value sits in the relationships" | Passive mirror of a Python dict; instance dead; Site nodes are site types, not places | Graph proposes charging chains, decides which backups are independent, finds report rings, and renders every decision as an auditable path |
| Best Use of Sarvam | Track tagline: "Build for India in India's languages" | One round trip of our own synthetic speech; voice button hidden on a secondary page | Voice-first driver app in Hindi and Hinglish plus more languages, with accuracy measured on real speakers at the venue |

Sources for the criteria and track wording: [Where U Elevate listing](https://whereuelevate.com/drills/hackfrontindia-2026) and [hackfrontindia.tech](https://hackfrontindia.tech/). Neither page publishes weights or track rubrics, and the five criteria are listed for the prototype submission, so the finale's may differ. A [finalist team's README](https://github.com/eres0045/hackfront-fortec) says the organisers' tip is that half the score comes from technical implementation and a working prototype; treat that as second-hand.

## 2. Brief for Opus 5.5

You are finishing Verity for the hackFront India national finale (IIIT Delhi, 21–22 November 2026). Work through sections 5 to 10 in the order given in section 11. Everything in section 3 was observed by running this repository on 14 November 2026; anything marked "verify" was not.

**Read first, in this order:** `CLAUDE.md`, `docs/EVIDENCE_STATUS.md`, `docs/RESULTS_FOR_DECK.md` (top table only), `docs/API_CONTRACT.md`, `docs/DECISIONS.md`, `docs/PROGRESS_FINALE.md` (F8 to F12).

**The goal in one sentence:** a judge picks up a phone, speaks one sentence in Hindi, and within three seconds sees and hears a GO or NO-GO they can audit down to the individual charger reports, with at least two numbers on screen that were measured on real Indian data.

### Rules that never bend

The eight golden rules in `CLAUDE.md` stay in force. Add these.

1. Never display or speak "100%" or "0%". The engine rounds to 5%; the display ceiling is "95%+" and the floor is "under 5%".
2. A feature that is not in the three-minute demo (section 4) does not get a place in the navigation. It may live under a "Lab" link.
3. Every graph-backed panel can show the Cypher that produced it. Every spoken sentence is built from a fixed template filled with engine numbers; a language model never writes a number.
4. No scraping. Real evidence comes from the hand-collected CSVs, official APIs and consenting people at the venue.
5. API changes are additive, written into `docs/API_CONTRACT.md`, and logged in `docs/DECISIONS.md`.
6. If a change flips a pinned demo decision, stop and report it. Re-pinning inputs is allowed only with a `DECISIONS.md` entry. Never tune the engine to produce a demo outcome.
7. Verify every external API detail against current vendor docs before coding (Sarvam model ids and fields, Neo4j and Aura features). Model ids in this guide are pointers, not specifications.
8. `uv run pytest` green after every milestone. One commit per milestone, message `W<n>.<m>: summary`. Local commits only; Ansh pushes.

### One `CLAUDE.md` line to update

`CLAUDE.md` still says "Prototype deadline: 31 Oct 2026". Change it to the finale: 21 November 2026, 07:30 IST. Its rule that engine work never touches `web/`, `logger/` or `design/` stays exactly as written.

### If time runs out

Cut from the bottom of this list, never from the top: Bingo Line fix → Neo4j instance alive → spoken ride-check endpoint → evidence path endpoint → real-data pipelines re-run → voice report writes to the graph → venue voice measurement → Cypher candidate chains → ring analytics → extra languages.

## 3. What running the repo showed

Thirteen findings, each observed on commit `9302586` (13 November) on a Linux clone with the in-memory graph. Severity is about the finale, not about code quality in general.

| # | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| F1 | The Bingo Line contradicts the plan it belongs to | `docs/api_examples/dispatch_V12.json`: decision GO at 0.95, yet from km 34 to km 65 the Bingo Line is 72% falling to 60% while expected battery is 50% falling to 38%. `docs/api_examples/forecast_update.json`: confidence 1.0, arrival p50 23%, action `SLOW_DOWN`, message "Battery is below the Bingo Line". | Critical |
| F2 | Likely cause of F1 | `engine/verity/dispatch.py` line 438: `bingo_line()` counts a charger only if it is within `ALT_AHEAD_KM` = 60 km of the car, while the plan evaluation counts the planned stop's backups wherever they are. The line drops from 60% to 31% between km 65 and km 67, exactly when the backups enter the 60 km window. This is my reading of the code; confirm with a test before changing it. | Critical |
| F3 | The frontend never uses the driver story | No reference to `/api/ride-check`, `/api/demo/driver` or the Range Passport anywhere in `web/` | Critical |
| F4 | The frontend shows stale numbers | `web/js/snapshot.js` was last committed on 30 October. It shows E4 stranding 33% vs 6%; `results/e4_dispatch_vs_fixed_simulated.json` says 37.5% vs 3.5%. It shows V07 confidence 0.85 (now 0.80) and "243 tests passed" (now 364). The landing page says 2,000 runs per forecast; the README says the demo runs 1,500. | High |
| F5 | No real Indian data in any result | `data/corpus/`: `trips_india.csv` has 6 rows, none verified; the charger evidence, app snapshot and app review files are header-only | Critical |
| F6 | Neo4j is offline and replaceable | `docs/EVIDENCE_STATUS.md`: the Aura host does not resolve. `engine/verity/trust/graph.py` holds storage queries plus one pairwise co-report query; the k-shortest-path search is Python (`dispatch.py` line 228). `Site` nodes are keyed by site type (line 323), so the 150 m physical Sites the Monte Carlo uses exist only in `sites.py`. | Critical for the track |
| F7 | Sarvam is verified once, on synthetic speech | `results/voice_check.json`: n=1, our own TTS read back by STT. The spoken charger name did not match, so no report was created. STT uses `saaras:v3`; Sarvam's model page now lists Saaras v4 as recommended. The microphone button exists only in `web/driver.html` and only with `?api=`. | Critical for the track |
| F8 | "100% confidence" appears in a headline result | `results/driver_demo_simulated.json`: confidence 1.0, reason text "100% confidence of completing the ride". My live call with the same request returned 0.95, so the headline number is not stable across machines or runs. | High |
| F9 | The demo depends on three CDNs | Leaflet from unpkg, fonts from Google, tiles from OpenStreetMap. With those blocked, the map panel reads "The map library could not load" and all type falls back to system fonts. | High at a venue |
| F10 | One test fails off Windows | `tests/test_bmw_i3.py::test_a_non_increasing_time_axis_fails_loudly` asserts CRLF bytes in a fixture: 1 failed, 362 passed, 1 skipped. There is no `.gitattributes`. `PROGRESS_FINALE.md` also notes the E1 guard needs a CRLF checkout. | Medium |
| F11 | The hero vehicle's release looks inconsistent | V12 is GO at 95% with a planned stop that has 0 reports and trust "unknown", while V19 is refused for unverified chargers. The likely reason, a backup with 95% works and 90% reach, is not stated. The Levers panel reads "The engine returned no levers" and the waterfall has three zero bars. | Medium |
| F13 | Range Passport fails its only real check | From `docs/RESULTS_FOR_DECK.md`: −14.5% on BMW i3 training trips, truth outside the 95% interval, coverage 0.0 at every k | Keep out of the demo |
| F14 | The repository front door is written for its builders | `README.md` is 196 lines of milestone codes (R4a, F8c, A13). CORS allows only `localhost:5500` and one GitHub Pages origin, so nothing deployed elsewhere can call the API. | Medium |

One thing to check that I did not: `engine/verity/trust/reports_sim.py` fixes `NOW` at 27 October 2026, and `api/service.py` uses it for report recency. Real evidence dated in November would be in the future relative to that clock.

## 4. The thesis and the one demo

**Thesis:** Verity turns drivers' voices into a charger-truth graph, and that graph into a go or no-go a driver can stake an evening's earnings on.

Each clause is one prize. The voice is Sarvam, the graph is Neo4j, and the calibrated decision is the problem statement. Neither sponsor technology is a bolt-on in this framing: voice is the cheapest sensor an Indian fleet has, and the graph is where a spoken report becomes trust.

**Hero user:** an EV cab driver in Noida Sector 62 at 19:00 with 45% battery who gets a ride from Sector 18 to IGI Terminal 3. This scenario already exists as `GET /api/demo/driver` and is pinned by `tests/test_driver_demo.py`. The Noida–Agra fleet scenario becomes the second act, for the dispatcher view.

### The three-minute demo

1. **Ask.** On a phone, in Hindi: "Battery 45 percent hai, Sector 18 se airport ka ride mila hai." The app shows the transcript, stamps GO, and speaks the answer with rupees, minutes and the charger to use after the drop.
2. **Trust the number.** The chance is stated as a count, "19 of 20 simulated evenings", next to the percentage.
3. **Audit it.** Tap "Why not the nearest charger?" The evidence path opens: two broken reports in the last two days, who filed them, how reliable those sources have been. One tap shows the Cypher.
4. **Change it.** A judge says "Radisson wala charger band hai" into the phone. A new report edge appears in the graph on the big screen. The app says what one unverified voice does to the trust score and what a second independent one would do. A scripted ring of five colluding accounts then fails to move it.
5. **Refuse.** Remove the evidence and ask again. The stamp reads INSUFFICIENT EVIDENCE and lists which charger to verify.
6. **Prove.** One screen of numbers: public Indian trips (n), real voice notes recorded at the venue (n), calibration on held-out trips, and one failure the team publishes.

Beat 4 must show whatever the trust engine really does. Run it before scripting it, then write the script around the observed behaviour.

### Why this is not another route planner

`docs/COMPETITORS.md` already makes the case against A Better Routeplanner, Chargetrip, Statiq and PlugShare. Keep its five claims and add two that the finale build creates: the answer is spoken in the driver's language and priced in rupees, and every answer is a path through evidence that anyone can inspect. Re-check each competitor claim before the pitch; that file is from October and says it could not open the vendor sites.

## 5. Workstream 0: fix first

Nothing in sections 6 to 10 starts until W0.1 to W0.4 are committed. These are the items a sharp judge can use to dismiss the whole project.

### W0.1 Make the Bingo Line agree with the plan (F1, F2)

- **Definition to implement:** the Bingo Line at km x is the lowest battery from which the plan's own policy still succeeds with probability at or above the target. "The plan's own policy" means the next planned stop, then that stop's backups, then the destination, with the same failure model `evaluate_plan` uses.
- **Change:** in `bingo_line()`, build the candidate set from the plan's alternates for the next stop (the existing alts\_for helper) instead of from the `km + ALT_AHEAD_KM` window. Apply the same set in `live_update_plan` where it computes `ahead`.
- **Resolve the message conflict:** `live_update_plan` must never return `SLOW_DOWN` or `DIVERT` for "below the Bingo Line" when the reweighted confidence is at or above target. Decide the precedence, write it in `DECISIONS.md`, and test it.
- **New invariant tests** in `tests/test_dispatch.py`: for every GO plan among the three demo vehicles and 50 seeded simulated trips, `expected_soc_pct >= bingo_soc_pct` at every km; and feeding the plan's own median battery at each km into `live_update_plan` returns `CONTINUE`.
- **Then:** re-export `docs/api_examples/`, re-run E3 and E4, and report any pinned decision that changed.

### W0.2 Remove certainty from the display (F8)

- Add `confidence_display` to dispatch and ride-check responses: `"95%+"` for values of 0.95 or more, `"under 5%"` for 0.05 or less, otherwise the rounded percent. Reason strings in `ridecheck.py` and the voice templates in `voice_loop.py` use it.
- Find why the same ride returns 1.0 in the saved result and 0.95 live, seed it, and pin the rounded value in `tests/test_driver_demo.py`.

### W0.3 One source of truth for every number on screen (F4)

- Regenerate with `scripts/export_api_examples.py` and `scripts/export_web_snapshot.py`. Add ride-check, the driver demo and `results/real_data_summary.json` to the snapshot.
- Add a test that fails when a snapshot value differs from the result file it came from.
- Every response reports the `n_runs` it used, so no consumer has to hardcode it.

### W0.4 Green on every machine (F10)

- Add `.gitattributes` with `* text=auto eol=lf` and mark the BMW fixtures `-text`.
- Rewrite the failing test to build its CRLF bytes in the test body.
- Make the E1 fingerprint hash line-ending-normalised content. This changes the guard, so log it in `DECISIONS.md` and do not re-run the held-out evaluation.
- Add `.github/workflows/ci.yml` running `uv run pytest` on Ubuntu and Windows, and put the badge at the top of the README.

### W0.5 Work with the wifi off (F9)

- Pre-generate and commit the demo's spoken answers as audio files, served only when Sarvam is unreachable and flagged `cached: true` in the response.
- Keep the in-memory graph and the snapshot path working as the no-network fallback.
- Acceptance: every API call the demo makes succeeds with the laptop in airplane mode, using the snapshot, cached audio and a local graph.

### W0.6 Explain the release (F11)

- The engine writes one plain sentence per decision naming the charger that carries it: "GO because backup {name} ({p\_works} likely to work, {n} reports) is reachable {p\_reach} of the time." It is returned in the response as `reason` and reused in speech.

### W0.7 Minimum hardening

- Bound the plan and ride caches with an LRU of a few hundred entries. Read allowed CORS origins from an environment variable. Add a per-IP rate limit on the two voice endpoints, which cost money.
- `/api/health` reports the graph backend, node and relationship counts, and the Sarvam key's presence (never its value).

## 6. Workstream 1: real evidence without fieldwork

Two real Indian numbers on the first screen are worth more than every simulated experiment in the repo. Six routes can produce them without travel; the pipelines for most already exist and are waiting for rows.

| Route | Who | What it yields | Pipeline status |
| --- | --- | --- | --- |
| R1 Hand-collected public trips | Teammate | E1-India: prediction error and band coverage on public Indian owner and magazine trips | Built (`experiments/e1_india_real.py`); 0 verified rows |
| R2 Hand-collected charger evidence | Teammate | Real reports on the airport chargers the driver demo uses; per-channel trust; E3-real | Built (`experiments/e3_real_evidence.py`); 0 rows |
| R3 Open Charge Map check-ins | Opus | Dated user check-ins with a status, through the official API | To build |
| R4 App reviews | Teammate + Opus | Hindi, Hinglish and English reviews classified by Sarvam, agreement with human labels | Built (`experiments/voc_app_reviews.py`); 0 rows |
| R5 Voice notes at the venue | Team, on 21 Nov | Battery and charger extraction accuracy on real speakers | To build (section 8) |
| R6 BMW i3 held-out trips | Opus | One honest held-out number for the energy model on real driving | Loader built; test split unopened |

### W1.1 Minimum corpus by 19 November (human work, start today)

- **Trips:** 30 verified rows from at least 10 different source pages. The split is by page, so this gives roughly 9 held-out rows. The result will carry the PRELIMINARY label (under 15 held-out); it is still a real Indian number.
- **Charger evidence:** 40 rows over at least 15 chargers, weighted to the chargers within 8 km of IGI Terminal 3 that the driver demo ranks (`CH-0089`, `CH-0056`, `CH-0073` and their neighbours). The aim is to run beat 3 of the demo on real reports.
- **App reviews:** 100 rows with 50 human-labelled.
- After each upload: `uv run python scripts/run_real_data.py`, then regenerate the snapshot (W0.3).

### W1.2 Open Charge Map check-ins (R3)

- The official `GET /poi` endpoint accepts `includecomments=true` and returns user comments and check-ins with a status type. The repo already has an `OCM_API_KEY` slot and permits official APIs.
- Fetch once for Delhi NCR and the corridor, cache in `data/cache/`, and map each check-in to a `Report` with source `public:ocm_checkin` and `is_simulated=false`.
- Report n whatever it is. Coverage of Indian chargers may be thin; I did not measure it.

### W1.3 Switch the driver demo to real reports when they exist

- When R2 or R3 gives at least three dated reports on a charger near the drop, build a second scenario `driver_real` whose reports are all real. If its decision differs from the simulated one, show the real one.
- Check the `NOW` constant first (end of section 3). Real November reports must not be dated after the engine's clock.

### W1.4 BMW i3 held-out evaluation (R6)

- On training trips only, remove the two known causes of the −14.5% bias that `PROGRESS_FINALE.md` F9 names: the usable-capacity ceiling at 1.05 × nominal, and class priors standing in for i3 values.
- Freeze, log in `DECISIONS.md`, then open the held-out split exactly once with `final_evaluation=True` and publish what comes out.
- If coverage is still poor, Range Passport stays in Lab and the pitch says so.

### W1.5 The evidence ledger

Expose the counts in `results/real_data_summary.json` through `GET /api/evidence/ledger`: how many real trips, real charger reports, real voice notes and simulated runs stand behind the current answers. Counts update without a code change.

## 7. Workstream 2: Neo4j

The test to pass is one question from a Neo4j developer advocate: "If I delete Neo4j, what stops working?" Today the honest answer is "nothing" (F6). After this workstream the answer is four visible things: candidate charging chains, independence of backups, report-ring detection, and the audit path behind every decision.

The in-memory graph stays as a demo-day safety net, but the pitch and the UI lead with what the graph does, and the health pill shows which backend is live.

### W2.0 Bring the graph back (human, today)

- Create a new AuraDB Free instance, put its URI and credentials in `.env`, run `scripts/neo4j_smoke_test.py` and `uv run pytest tests/test_graph_parity.py`. The free tier allows 200,000 nodes and 400,000 relationships ([Neo4j limits](https://neo4j.com/docs/aura/quotas-and-limits/)); Verity needs a few thousand.
- The last instance stopped resolving, most likely paused or removed after sitting idle. Add a scheduled keep-alive query until 23 November.
- Install a local Neo4j on the demo laptop with the same loader. Campus networks often block the Bolt port; this is my assumption about IIIT Delhi, not a checked fact. Decide which one is primary after testing on venue wifi.

### W2.1 Put the real structure in the graph

- `Site` becomes a physical place: one node per 150 m cluster from `sites.py`. The current site-type value moves to a `SiteType` label or a property.
- Add `(:Plan {id})-[:STOPS_AT {order}]->(:Charger)`, `(:Plan)-[:BACKUP_FOR {stop}]->(:Charger)` and `(:Plan)-[:AVOIDED {reason}]->(:Charger)`, written when a plan is released and swept like plan legs are today.
- Add `(:Source)-[:VIA]->(:Channel)` for `driver_voice`, `google_review`, `ocm_checkin` and the others in `evidence.py`.
- Add `(:Alias {text})-[:NAMES]->(:Charger)` for spoken names (W2.5).

### W2.2 The graph decides which backups are independent

Today a backup is treated as correlated with the primary only when it is within 150 m, with an assumed beta of 0.5. A backup run by the same operator, or built on the same hardware, can fail for the same reason.

```cypher
MATCH (p:Charger {id: $primary})
MATCH (b:Charger) WHERE b.id IN $backups
OPTIONAL MATCH (p)-[:AT]->(s:Site)<-[:AT]-(b)
OPTIONAL MATCH (p)-[:RUN_BY]->(o:Operator)<-[:RUN_BY]-(b)
OPTIONAL MATCH (p)-[:IS]->(h:HardwareModel)<-[:IS]-(b)
RETURN b.id AS backup, s.id AS shared_site,
       o.id AS shared_operator, h.id AS shared_hardware
```

- Feed the result into the common-cause grouping in `sites.py`, which today groups only by 150 m site. The operator-level beta is an assumption: label it, and estimate it from same-operator same-day failures if R2 or R3 gives enough rows.
- Experiment E6: how often does the plan change, and does stranding fall, when operator-correlated outages are simulated? Publish the result either way.

### W2.3 The graph proposes the charging chains

`CLAUDE.md` says "Neo4j proposes candidate charging chains". In the code, Neo4j stores legs and Python runs Yen's algorithm. Make the sentence true.

```cypher
MATCH p = (:RoadSegment {id: $start})-[legs:LEG*1..5]->(:RoadSegment {id: $end})
WHERE all(l IN legs WHERE l.scope = $scope)
WITH p, reduce(c = 0.0, l IN legs | c + l.minutes) AS cost
RETURN [n IN nodes(p) | n.id] AS chain, cost
ORDER BY cost LIMIT $k
```

- This is a sketch. Match it to how `_adjacency` builds edge costs and battery bands, then add a parity test: same top-k chains as `k_shortest_paths` on the demo routes.
- Keep the Python path as the fallback. Record the query's p95 on Aura in `results/`.

### W2.4 Report rings as graph analytics

- Build agreement edges between sources that file the same verdict on the same chargers, weighted by Jaccard overlap of the chargers they report on. Find communities over those edges.
- Aura Graph Analytics is available on the free tier with one session of up to 2 GB for up to 4 hours, per the same limits page. Run it as an offline batch that writes `ring_id` back to `Source` nodes. Check the current Python client before coding. A pure-Cypher Jaccard plus the existing `group_rings` union-find is the fallback; the graph is small enough.
- Experiment E5b on the existing 20 simulated worlds: does overlap without a time window catch the ten-day evasive ring that E5 misses, and at what cost in honest repeat reporters flagged? Report precision and recall whatever they are. "The attack our first detector missed, and what happened when we tried again" is a strong critical-thinking slide in both outcomes.

### W2.5 Spoken names resolve through a full-text index

F7's voice note failed because "Sector 62 wala charger" matched no directory name.

```cypher
CREATE FULLTEXT INDEX charger_names IF NOT EXISTS
FOR (n:Charger|Alias) ON EACH [n.name, n.text];

CALL db.index.fulltext.queryNodes('charger_names', $spoken)
YIELD node, score
RETURN node, score ORDER BY score DESC LIMIT 3
```

- Aliases come from the `landmark_or_address` column of the evidence CSV, operator names, and romanised and Devanagari spellings of the same place. Never invent a charger or a coordinate.
- Below a score threshold the app asks "Which one?" and shows the top three. It does not guess.

### W2.6 Every decision is a path

- `GET /api/graph/why/{plan_id}` returns nodes, edges and the Cypher text for: plan → stops, backups and avoided chargers → reports → sources → channel and ring flag, plus shared site and operator.
- `GET /api/graph/charger/{id}` returns one charger's neighbourhood.
- `POST /api/reports` (used by voice in section 8) writes one `REPORTED` edge and returns the before and after trust for that charger.

### W2.7 What the track judge sees

- Every graph-backed response carries the Cypher that produced it and the query time in milliseconds.
- `/api/health` reports the graph backend, node and relationship counts, and the last query time.
- A report written through `POST /api/reports` appears in the next `GET /api/graph/charger/{id}` response.
- Track line for the pitch: "A backup behind the same operator is not a backup. Five accounts that always agree are not five witnesses. A decision is a path you can audit."

## 8. Workstream 3: Sarvam

The track rewards products that could not exist without Indian-language AI. Verity qualifies only if voice is the main way in and out of the driver app, and only if the team can say how well it works on real speakers. Today it is a hidden button and one synthetic round trip (F7).

### W3.1 Bring the client up to date

- Sarvam's [model page](https://docs.sarvam.ai/api/getting-started/models) lists Saaras v4 as the recommended speech-to-text model, with transcribe, translate, verbatim, translit and codemix output modes across 23 languages. `voice.py` pins `saaras:v3`.
- The same page lists Bulbul v3 as the default text-to-speech model (11 languages, 30+ speakers) and a newer Bulbul v4 Flash.
- Read the current API reference, make the model ids configurable, and keep the paid-call log that `SarvamClient` already writes.

### W3.2 Spoken ride-check

New endpoint `POST /api/voice/ride-check` takes audio and returns the transcript, detected language, parsed slots, the ride-check result and the spoken reply.

1. **Hear.** One speech-to-text call. Try the translate mode first: it returns English in a single call and removes the separate translation call the current loop makes (1.2 s in `results/voice_check.json`).
2. **Parse by rule.** Battery percent with the existing `soc_from_transcript`. Pickup and drop through the full-text index (W2.5) over a small gazetteer of NCR places.
3. **Fall back to the language model only when rules fail,** with `chat_json`, a strict schema and a 4-second timeout. The repo's own smoke test recorded one `sarvam-105b` call at 22.9 s, so it cannot sit in the main path. The model page also lists `sarvam-105b-conversations` without reasoning; test whether it is fast enough.
4. **Confirm.** The app reads back "45%, Sector 18 to Airport T3. Sahi hai?" and waits for a tap or "haan". Nothing runs on an unconfirmed parse.
5. **Decide.** Call the existing `ride_check`. The language model never sees or writes the decision.
6. **Speak.** Fill the fixed template in the language the driver used and synthesise it. Extend `DECISION_TEMPLATES` with rupees, minutes and the after-drop charger.

### W3.3 Spoken charger report writes to the graph

- "Radisson wala charger band hai" becomes a status (existing `parse_status`) plus a charger (W2.5), then `POST /api/reports` (W2.6).
- A report from a real person is stored with `source_kind=driver_voice` and `is_simulated=false`. A scripted one stays simulated. Ask consent on screen before recording; store the transcript and the parsed fields, not the audio, unless the speaker agrees.
- The reply states what changed: the charger's trust before and after, and whether the plan moved.

### W3.4 More than Hindi

- The app replies in the language the speech-to-text call detected.
- Translate the decision templates once into three more languages that Bulbul supports, commit them, and mark each "machine-translated, not yet reviewed by a native speaker" in the UI until someone at the venue reviews it. Pick the three with Ansh.
- Test how each voice reads "45%", "₹80" and charger names before the demo. Write numbers the way the voice reads them correctly.

### W3.5 Measure it on real people (R5)

This is the one real Indian measurement the team can collect in an afternoon without leaving the hall.

- A minimal capture page or script at `/lab/voice` shows a prompt card ("Say your battery is 37 percent"), records one utterance, and stores the transcript, parsed value, expected value, language and device. No names.
- Target: at least 30 utterances from at least 10 people in Hindi, Hinglish and English, in hall noise.
- `scripts/voice_real_eval.py` writes `results/voice_real.json`: battery-extraction accuracy and charger-match accuracy with Wilson intervals, by language, plus speech-to-text latency p50 and p95.
- Replay the same audio through v3 and v4 and through two output modes. Report the comparison, and use the best one in the demo.

### W3.6 Reviews in three languages (R4)

When the review rows arrive, run `experiments/voc_app_reviews.py` with Sarvam and with the keyword baseline. Write both accuracies and Cohen's kappa to the results file, so the ledger endpoint can serve them.

### W3.7 What the track judge sees

- The voice endpoints return the transcript in the script it was spoken in, the detected language, and per-stage timings: hear, parse, decide, speak.
- Target: under 3 seconds from end of speech to start of reply on the rule path. Measured, not promised.
- Track line for the pitch: "The driver's hands are on the wheel. Asking, hearing the answer and reporting a dead charger all happen by voice, in the language the driver spoke."

## 9. Frontend

Out of scope. Ansh owns the frontend and will direct it himself. Opus 5.5 does not create, edit or delete anything in `web/`, `logger/` or `design/`, as `CLAUDE.md` already says. The boundary is `docs/API_CONTRACT.md`: everything in sections 5 to 8 is delivered as an endpoint or a response field, and nothing in this guide prescribes how it is shown.

## 10. Workstream 5: repository, deployment, pitch

### W5.1 A front door for judges (F14)

Move the current `README.md` to `docs/ENGINE_NOTES.md` unchanged. The new README fits one screen before the fold:

1. One sentence (the thesis from section 4) and a 20-second GIF of beats 1 to 3.
2. A link to the live driver app and a QR code.
3. "What is real, what is simulated": the evidence ledger as a table, generated from `results/real_data_summary.json`.
4. "How Neo4j is used" and "How Sarvam is used": four lines each, every line naming a file and a screen where a judge can see it.
5. Three commands to run it locally, and the CI badge.
6. Results with n and intervals, then known limits, then a link to `docs/AI_USAGE.md`.

No milestone codes (R4a, F8c) above the fold.

### W5.2 Disclosure

The league's rules require AI tool use to be disclosed at submission. Add rows to `docs/AI_USAGE.md` for this analysis and for every Opus 5.5 session, and resolve the "teammate tool names remain unconfirmed" note that file still carries.

### W5.3 Deploy once, reach it from any phone

- One container for the FastAPI service. Secrets come from the host's environment, never the image. Read allowed origins from an environment variable so whichever frontend Ansh ships can call it.
- The phone's microphone only works on an HTTPS origin, so judges' phones need a public HTTPS URL; a laptop's `localhost` will not do. Pick a host that does not sleep between requests, or wake it before the pitch. I did not compare hosts; check current free tiers.
- Second route in case the venue blocks the host: the laptop serves the app over a phone hotspot through an HTTPS tunnel.
- Print the QR code. Put it on the first and last slide.

### W5.4 Demo kit

- Two charged phones with the app installed, one hotspot, the laptop with local Neo4j and the offline bundle.
- A 90-second screen recording of the six beats, made on 20 November, labelled as a recording if it is ever played.
- A printed one-page run sheet of the beats with the exact sentences to speak.

### W5.5 The pitch

| Time | Say | Show |
| --- | --- | --- |
| 0:00 | "A cab driver at 45% gets an airport ride. Every app tells him a range. None tells him whether to take it." | The phone, idle |
| 0:20 | Beats 1 and 2: ask in Hindi, hear the answer, read the twenty dots | Phone mirrored to the projector |
| 1:10 | Beat 3: why not the nearest charger | Evidence path, then the Cypher drawer |
| 1:40 | Beat 4: a judge reports a broken charger by voice | Graph edge appears; trust before and after |
| 2:15 | Beat 5: refusal | INSUFFICIENT EVIDENCE stamp |
| 2:30 | Beat 6: the real numbers and one published failure | The measured results |
| 2:50 | The thesis sentence, then the QR code | Last slide |

Adjust the timings once the organisers confirm the slot length.

### W5.6 Hard questions, answered in one breath

| Question | Answer |
| --- | --- |
| "Isn't this all simulated?" | Name the real n for trips, charger reports and voice notes on the ledger, then say what is still simulated and why. |
| "What does Neo4j add over a dictionary?" | The four things in section 7, each visible on screen: chains, independence, rings, audit path. |
| "Why not A Better Routeplanner or Google Maps?" | They give a range and a route. Verity gives a calibrated chance, refuses when it lacks evidence, and treats charger reports as possibly false. |
| "How do you know 90% means 90%?" | E2 calibration on held-out trips, with the interval; and E1-India coverage once it has run. |
| "What about people who lie about chargers?" | E5 and E5b, including the ring the first detector missed. |
| "Who pays?" | Fleet operators, per vehicle. A wrong "go" costs them a stranded cab and a lost shift. Do not quote a market size the team has not sourced. |
| "What failed?" | Range Passport on real BMW data, with the number. Saying this unprompted is the strongest critical-thinking evidence the team has. |

## 11. Sequence, human tasks, acceptance

Five working days remain before the finale. The order below puts correctness first, then the hero demo, then the two tracks, then proof. It assumes pre-finale building is allowed; section 12 says what to hold back if it is not.

| Day | Opus 5.5 | Ansh and teammate |
| --- | --- | --- |
| Sun 15 Nov (evening) | W0.1 Bingo Line, W0.2 certainty, W0.4 cross-platform | Update the deadline line in `CLAUDE.md`. Create the Aura instance. Confirm Sarvam credits and get an Open Charge Map key. Email the organisers the questions in section 12. Teammate starts collecting trips and charger evidence. |
| Mon 16 | W0.3 snapshot, W0.5, W0.6, W0.7. | Keep collecting. |
| Tue 17 | W3.1 client update and W3.2 spoken ride-check endpoint. | Speak to it in Hindi and Hinglish; list every misparse. |
| Wed 18 | W2.1 schema, W2.5 full-text names, W2.6 graph endpoints, W3.3 spoken report into the graph. | Test Aura from a campus network. Pick the three extra languages. |
| Thu 19 | W2.2 independence and W2.3 Cypher chains. W1.2 check-ins. | Corpus freeze at 18:00. Verify every row. |
| Fri 20 | Run all real-data pipelines. W1.5 ledger endpoint. W2.4 and E5b. W1.4 held-out run. Deploy the API. New README. | Three full rehearsals, one in airplane mode. Record the backup video. No new features after 20:00. |
| Sat 21 – Sun 22 (finale) | Hours 0–2: venue network test, choose graph backend. Hours 2–6: W3.5 voice notes from real speakers, written to `results/voice_real.json`. Hours 6–14: anything from the cut list in section 2 that slipped, then W3.4 languages. Hours 14–18: rehearse. From hour 18: freeze, fixes only. | Recruit speakers with consent. Rehearse the six beats until they take under three minutes. |

### Done means

- [ ] `uv run pytest` passes on Ubuntu and Windows in CI
- [ ] For every GO plan in the demo, expected battery is at or above the Bingo Line at every km, and the live update on the plan's own trajectory says continue
- [ ] No reason string or spoken sentence contains "100%"
- [ ] Every number in the exported snapshot traces to a file in `results/` or a live API response, enforced by a test
- [ ] The evidence ledger endpoint returns a non-zero count for at least two kinds of real Indian evidence
- [ ] With Neo4j stopped, `/api/health` says so and the API falls back; with it running, graph responses carry a real query and its latency
- [ ] A spoken Hindi ride request returns a spoken Hindi answer in under 3 seconds on the rule path
- [ ] A spoken charger report creates a `REPORTED` edge that the charger graph endpoint returns within 2 seconds
- [ ] `results/voice_real.json` exists with n of at least 30 from at least 10 speakers
- [ ] Every API call in the six demo beats succeeds against the deployed URL, and in airplane mode on the laptop
- [ ] The README's first screen states the thesis, the live link, and the real and simulated counts
- [ ] `docs/AI_USAGE.md` lists every tool and session used

The first four boxes are Opus 5.5's to close alone. The fifth depends on the teammate's rows; if the corpus is empty on Thursday night, the Open Charge Map check-ins and the venue voice notes are the two that can still fill it.

## 12. Open questions and what was not verified

### Questions for the organisers

- [ ] Can the finale build continue the shortlisted prototype, and may code written between the shortlist and 21 November be used? If only finale-hour work counts, hold back W2.4, W3.4 and W3.5, and build them live.
- [ ] How long is the pitch slot, how many judging rounds are there, and do track judges see a separate demo?
- [ ] Are there written rubrics for the Neo4j and Sarvam tracks? Neither official page publishes one.

### Things for Ansh to re-read

- The full T6.1 brief. The public theme page carries only a summary and links to a Google Doc I could not open. Any explicit deliverable in it outranks this guide.

### What this guide asserts without having checked

| Claim | Status |
| --- | --- |
| Cause of the Bingo Line contradiction (F2) | Read from the code and consistent with the data; not confirmed by a test |
| Sarvam model ids, modes and request fields beyond the model list | Model list read on 14 November; API reference not read |
| `sarvam-105b` is too slow for the main path | One 22.9 s call recorded in the repo's own notes; not re-measured |
| Aura Graph Analytics on the free tier | From Neo4j's limits page; the Python client and session start-up time were not tested |
| Open Charge Map has useful check-ins for Indian chargers | The API field exists; Indian coverage not measured |
| IIIT Delhi wifi blocks the Bolt port | Assumption |
| Hosting choice, HTTPS tunnel | Not researched |

### What I did not run

A live Neo4j instance, any Sarvam call, the app on Windows, the app on a physical phone, and the experiments E1 to E5 (I read their saved results).

### Sources

- [hackFront India 2026 on Where U Elevate](https://whereuelevate.com/drills/hackfrontindia-2026): criteria, prizes, rules
- [hackfrontindia.tech](https://hackfrontindia.tech/): track taglines, finale dates
- [Automobile & EV theme page](https://hackfrontindia.tech/themes/automobile-ev): T6.1 summary
- [Sarvam models](https://docs.sarvam.ai/api/getting-started/models)
- [Neo4j Aura quotas and limits](https://neo4j.com/docs/aura/quotas-and-limits/)
- [Open Charge Map API reference](https://andreibesleaga.github.io/ocm-api-reference/api/resources/poi.html) (third-party mirror; confirm against the official docs)

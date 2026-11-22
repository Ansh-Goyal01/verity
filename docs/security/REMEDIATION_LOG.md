# Security and site-health remediation log

Source: ArgosX public-surface scan of https://verity-three-pi.vercel.app, assessed 21 Nov 2026
(VibeScore 84/100, 14 findings, nothing critical; read-only, no signed-in checks).
Owners: Astra = frontend/headers/accessibility/SEO/performance; Claude Code = engine, graph, API review.

"Re-test" is what was actually checked. The final ArgosX scan (Ansh runs it, about 09:00) is the real re-test.

## ArgosX findings (14)

| # | Finding | Severity | Owner | Fix commit | Re-test (2026-11-22) |
|---|---|---|---|---|---|
| 1 | Missing X-Frame-Options | medium | Astra | c0f980b | `DENY` seen on `/`, `/web/home.html`, a 404 (curl) |
| 2 | Missing Content-Security-Policy on the response ArgosX scanned (the `/` redirect) | low | Astra | 3e4c09c | CSP present on `/` (`default-src 'none'`), pages and 404 (curl); page CSP unchanged |
| 3 | Missing X-Content-Type-Options | low | Astra | a9744d6 | `nosniff` on the same three responses |
| 4 | Missing Permissions-Policy | low | Astra | 4e61115 | `microphone=(self), camera=(), geolocation=(self)` on the same three responses |
| 5 | Missing Referrer-Policy | low | Astra | 1721440 | `strict-origin-when-cross-origin` on the same three responses; share paths keep `no-referrer` (api/middleware.py) |
| 6 | Color contrast (1 node) | medium a11y | Astra | 2ce347a | not re-verified by Claude Code; final scan |
| 7 | Scrollable region not keyboard-focusable | medium a11y | Astra | 2ce347a | not re-verified; final scan |
| 8 | Content not in landmarks | low a11y | Astra | 2ce347a | `<main>` present on home, planner, driver, console (curl) |
| 9 | No level-one heading | low a11y | Astra | 2ce347a | exactly one `<h1>` on home, planner, driver, console (curl) |
| 10 | Missing Open Graph tags | low seo | Astra | ad64bba | `og:title` present on the four pages (curl) |
| 11 | Missing canonical link | low seo | Astra | ad64bba | `rel="canonical"` present on the four pages (curl) |
| 12 | No robots.txt | low seo | Astra | ad64bba | `/robots.txt` returns 200 |
| 13 | No sitemap.xml | low seo | Astra | ad64bba | `/sitemap.xml` returns 200 |
| 14 | Page load about 2.9 s | low perf | Astra | d35a5ec | not re-measured; final scan |

**Decision recorded:** ArgosX suggested `Permissions-Policy: geolocation=(), camera=(), microphone=()`. Not used:
spoken trip planning and reports need the microphone and the trip check uses location, so the deployed value is
`microphone=(self), camera=(), geolocation=(self)` (same as the API's own header).

## Claude Code review and hardening (CC-2, CC-3)

| Item | Status | Notes |
|---|---|---|
| Demo ring-attack endpoints open to anyone when `VERITY_DEMO=1` | Fixed (CC-3, a616465) | Now admin-only: 401 no token, 403 other roles. Contract v18. Frontend must send the admin bearer token. |
| Auth on account routes | Verified | Every `/api/me*`, `/api/trips*`, `/api/reports/quick`, `/api/reports/{id}`, trip share creation, fleet (`fleet_manager`), CPO (`cpo`) and admin route depends on `current_user`; role checks via `require_role`. Open by design: `/api/auth/config`, `/api/vehicle-models`, `/api/sample-trip`, `/api/share/{token}` (capability URL). |
| Quick-report idempotency | Verified | Per-user advisory lock, same-status dedupe for 10 minutes, 429 on a contradicting status inside the cooldown, graph sync once (`synced_at`). Replay of the same report returns the same id (tests/test_finale_accounts.py). |
| Share capability URLs in logs | Verified | `/api/share/[redacted]` in uvicorn access logs, `no-referrer` on share paths (api/middleware.py). |
| Account ids in public graph views | Low risk, accepted | Sources appear as `user-<sha256(user id)>`. User ids are random UUIDs so the hash cannot be reversed, but an unsalted hash lets someone who already knows a UUID confirm a report. Hardening later: HMAC with a server secret. |
| Plan ids held in memory on serverless | Mitigated | A `plan_id` made on one instance can 404 on another. Error code `unknown_plan` unchanged; the message now says plans are in memory, expire and may live on another instance, and to plan again. The driver view already handles the code. Not persisted. |
| Unauthenticated writes to the trust graph | Open, accepted for the demo | `POST /api/reports` and `POST /api/voice/report/confirm` accept consented reports without sign-in, rate-limited by IP and per charger. Account reports (`/api/reports/quick`) are the authenticated path. Anyone behind many IPs could still stuff evidence; the ring detector and trust weights are the control. Revisit before any real use. |
| Idempotency receipts (Astra, 64f5815) | Reviewed, low risk | Keys are scoped per user and reusing a key for a different operation returns 409; replays return the stored result. Gap: `audit_log` has no unique index on `(user_id, action)`, so two concurrent first requests with the same key could both write a receipt (quick reports are protected by an advisory lock; other keyed writes are not). Suggested fix for Astra: a partial unique index on `(user_id, action) WHERE action LIKE 'write:%'` in a new migration, with `ON CONFLICT DO NOTHING`. Not blocking. |
| Database-backed account tests | Not run on this machine | tests/test_finale_accounts.py needs `VERITY_TEST_DATABASE_URL` (a local `verity_test_*` Postgres). Role isolation and CPO scoping are asserted there but were skipped here. Codex reported them passing against PostgreSQL. |
| Live sign-in configuration | Partially verified | `/api/auth/config` available, a fake bearer gives 401 (so `SUPABASE_URL` is valid). `DATABASE_URL` and the account screens need a signed-in check (`scripts/verify_deployed_api.py` with Ansh's private env file). |

## Held-out data (CC-8)

The BMW i3 held-out split stays sealed: `bmw_heldout.py --final` refuses when `results/bmw_i3_heldout.json` exists and
nothing in this work re-ran it. The India held-out split is scored once per experiment run only.

## Final scan

To be added after Ansh runs the final ArgosX scan on the latest main.

## Later changes (Claude Code, 2026-11-22)

- Calibration capture (`POST /api/trips/{id}/observation`, migration 002): ownership enforced in the INSERT, first record wins, RLS on with no browser grants. The endpoint and migration were not executed on this machine (no local Postgres).
- `suspected_rings` counted every source in a ring, including sources that reported only other operators' chargers; it now counts only sources that reported the operator's own chargers.
- WP9 `assured` and `reward` blocks are illustrative and need `config/assured.json` for any rupee figure.

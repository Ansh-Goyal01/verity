# Deploying the API (guide W5.3)

The CI container job builds the Dockerfile and checks the API with container networking disabled.
A public deployment URL is needed for the hosted smoke checks below.

The FastAPI service is the only thing that needs a public address. The pages in `web/` are static and do not run
on it. A phone's microphone only works on an **HTTPS** page, and an HTTPS page may only call an **HTTPS** API
(browsers block mixed content), so both the page and the API need HTTPS URLs.

## What the container needs (environment, never the image)

| Variable | Why |
|---|---|
| `SARVAM_API_KEY` | voice endpoints; without it they answer 503 and the cached demo audio still plays |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | optional; without them the API runs on the in-memory graph and `/api/health` says so |
| `VERITY_CORS_ORIGINS` | comma-separated browser origins that may call the API. Setting it REPLACES the built-in list (file://, localhost:5500, `https://ansh-goyal01.github.io`) |
| `VERITY_TRUST_PROXY=1` | only behind exactly one proxy that appends `X-Forwarded-For`; otherwise every visitor shares one voice rate limit |
| `PORT` | set by most hosts; the container listens on it, default 8000 |
| `VERITY_OFFLINE=1` | optional; uses packaged route/geocode caches, the memory graph, and cached speech |
| `SUPABASE_URL`, `DATABASE_URL` | hosted Auth project URL and TLS-verified database connection for accounts |

`/api/health` shows the graph backend, node and relationship counts, and `sarvam_key_present` (a boolean, never the key),
so one request tells you whether the deploy picked up its secrets.

## Hosts (checked 2026-11-18 with a web search; most sources are third-party blogs, so read the host's own pricing page before relying on any of this)

| Host | What was found | Fit |
|---|---|---|
| [Render](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026) free web service | Sleeps after 15 minutes without a request; a cold start takes about 30 to 60 seconds ([source](https://livemy.app/blog/render-pricing)). 750 free instance hours a month per workspace. The paid Starter plan (about $7 a month per service, per one guide) does not sleep. | Works if you **wake it before the pitch** (`curl https://<host>/api/health`, then again two minutes before going on) or pay for the always-on plan. |
| [Hugging Face Spaces](https://huggingface.co/docs/huggingface_hub/guides/manage-spaces) (Docker) | Free `cpu-basic` pauses after 48 hours of inactivity and the time cannot be changed on the free tier; a visit restarts it. A forum post claims free CPU Spaces were restricted for Docker; I could not confirm that from an official page. | Sleeps rarely, but verify a free Docker Space is still offered before building around it. |
| [Google Cloud Run](https://cloud.google.com/run/pricing) | Request-based billing has a free allowance (2 million requests a month; vCPU and memory seconds that differ by region). Scales to zero by default; an always-warm minimum instance is billed. Needs a billing account. | Gives HTTPS with no setup. Cold-start time was **not** found or measured. |
| Laptop + [Cloudflare quick tunnel](https://developers.cloudflare.com/argo-tunnel/trycloudflare) | `cloudflared tunnel --url http://localhost:8000` gives a random `trycloudflare.com` HTTPS URL with no account. Cloudflare calls quick tunnels testing-only: 200 concurrent requests, no server-sent events, no uptime guarantee, and the URL changes every run. | The **fallback** if the venue blocks the host (guide: second route), over a phone hotspot. |

**Recommendation (Ansh decides):** Render or Cloud Run for the main URL, woken before the pitch, and the tunnel as the
backup. Neither was run here, so none of the cold-start or cost figures above is measured.

## Steps for Ansh

1. Pick the host. Set the environment variables above in the host's dashboard (never in the repo).
2. Let the host build from the repo's `Dockerfile`, or build it locally with `docker build -t verity .`.
3. Open `https://<host>/api/health`: `status` ok, the graph backend you expect, `sarvam_key_present: true`.
4. Run the six demo beats against the URL. Open Mission Control with `?api=https://<host>` from an HTTPS page,
   and add that page's origin to `VERITY_CORS_ORIGINS` if it is not `https://ansh-goyal01.github.io`.
5. Neo4j Aura Free pauses when idle. The keep-alive (`scripts/aura_keepalive.py`) is a Windows task on the laptop, scheduled until 23 Nov, so it
   only runs while that laptop is on. For the demo, check `/api/health` shows `connected`, or let the API run on the in-memory graph (same answers).
6. Generate the QR code for the final URL, add it and the GIF to the README, and put the QR on the first and last slide.

Run `sh scripts/warmup.sh https://<host>` two minutes before the pitch. It checks
health, then warms the existing Noida–Agra dispatch plan.

## Account migration and restart check

Use a private env file with a direct, TLS-verified `DATABASE_URL`. The runner
locks migration execution and records each applied file's SHA-256. For a database
where the initial SQL was already applied manually, reconcile the migration history
before running the command.

```sh
uv run --env-file <private-env-file> python scripts/migrate.py
uv run --env-file <private-env-file> python scripts/verify_deployed_api.py https://<host> --create-saved-trip
# Restart or redeploy the same API environment, then check the ID printed by the previous command.
uv run --env-file <private-env-file> python scripts/verify_deployed_api.py https://<host> --verify-trip <trip-id>
```

The verifier needs `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `VERITY_VERIFY_EMAIL` and
`VERITY_VERIFY_PASSWORD` for a dedicated demo account. It signs in on each run and
checks the authenticated profile. The creation phase leaves a demo car and saved trip
for the restart check.

## Hotspot and tunnel (the fallback)

```
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
cloudflared tunnel --url http://localhost:8000      # prints https://<random>.trycloudflare.com
```

Use the printed URL as `?api=` and in `VERITY_CORS_ORIGINS` if the page is not on GitHub Pages. Start it before the
judges arrive: the address is different every time, so a QR code printed earlier will not work for it.

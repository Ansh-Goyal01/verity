# Verity frontend — how to open and test

These pages use plain HTML, CSS and JavaScript. No install or build step is needed.

## Open the project

1. Right-click the downloaded ZIP and choose **Extract All**.
2. Open the extracted folder. Keep `design`, `web` and `logger` next to each other.
3. Double-click `web/console.html` or `web/driver.html` to open it in Chrome.
4. The address must show the extracted folder. Do not open the HTML inside the ZIP or from a temporary ZIP folder.
5. Keep internet connected for Google Fonts, Leaflet and OpenStreetMap tiles.

Without `?api=` the pages run offline on the saved engine snapshot (`js/snapshot.js`), or on illustrative mocks if the snapshot is missing. Every offline response is simulated and the pages show a visible badge.

## Product site (start here)

Open `web/home.html`; the repository root `index.html` redirects to it. It is a single animated page. The hero shows a live Bingo Line instrument for the demo trip. The page then covers the problem, a scroll-driven "how a release is made" story, an animated pipeline, a proof bento with E1–E5 results, a competitor comparison, a driver phone that plays the saved replay, and the V19 refusal. Every value is read from `js/snapshot.js`. Animation uses Motion (`vendor/motion-12.43.0.js`, MIT, vendored so the page works offline). With reduced motion, or without that file, the page shows its final state. Competitor claims are sourced in `docs/COMPETITORS.md`.

## Mission Control

Open `web/index.html` (or the repository root `index.html`, which redirects to it). Six tabs, switchable with keys 1–6 or the Ctrl/⌘ K command palette:

1. **Mission**: the pitch in one screen. A rotating dispatch release for each vehicle, the three different answers for one booking, headline experiment numbers, and the INSUFFICIENT EVIDENCE refusal.
2. **Dispatch**: confidence gauge against the target, arrival band, map, charging plan with backups, interactive Bingo Line (hover to read any km), energy waterfall, levers and risk sources.
3. **Live trip**: plays the saved engine replay on a map next to a phone mock-up of the driver view. The broken-charger report and DIVERT / SLOW DOWN alert fire at their recorded times, with an optional voice alert (Hindi or English).
4. **Charger trust**: the full charger directory with trust filters, a sortable searchable table and the E5 ring-detection results, including the evasive ring that is not caught.
5. **Evidence**: E1–E5 charts drawn from the saved `results/*.json`, with sample sizes and confidence intervals, plus the last `run_all` status.
6. **Engine**: the pipeline, the physics equations, why shared uncertainty matters, and the project's honesty rules.

Data: with no `?api=` the pages load `js/snapshot.js`, which holds the **saved real engine outputs** from `docs/api_examples` and the experiment results. All of it comes from the simulated demo scenario, and the header says so. Regenerate it after new engine runs with `uv run python scripts/export_web_snapshot.py`. `?source=mock` uses the hand-written illustrative mocks instead. `?api=http://127.0.0.1:8000` uses the live engine. Tests: `node --test tests/frontend_app.test.cjs tests/frontend_console.test.cjs`.

## Fleet console

- Open `web/js/check-mocks.html`. All checks should show PASS.
- Open `web/console.html`. Three vehicle cards should load.
- Select V12. The route and chargers should spread across the map. Click **Fit route** if the window size changed. Hover over a charger for its evidence.
- If street tiles cannot load, the page displays a notice. The amber route and charger markers should still appear. Try the team's HTTPS page if tiles are blocked locally.
- Check the expected battery band, red Bingo Line, charging jump and point-of-no-return line. The waterfall ends at the arrival percentage.
- Click the cruise lever. Its new confidence and time cost should appear.
- Select V19. The refusal reason should replace the route, charts and levers.
- Try the light/dark theme button and resize to tablet width.

## Driver view

- Open `web/driver.html`. It starts in light theme.
- Enter 44 on the keypad, then press OK. The battery and Bingo margin should update. An empty or invalid entry must show an error without changing the battery.
- Switch between Hindi and English. Headings, controls, status messages and the divert alert should change. Place names remain the names from the data.
- Select 20x before pressing Play. The complete recorded replay is about two minutes at this speed. Changing the speed should preserve replay progress. Pause should stop it.
- At the broken-charger event, a full-screen divert alert should appear. Chrome will try browser speech in mock mode. Availability of Hindi/Indian English voices depends on the device.
- The VOICE button requires a connected API and microphone access. In mock mode use the keypad.
- Press Ctrl+Shift+M in Chrome DevTools to check the phone layout.

## Design and pitch assets

- Open `design/style-preview.html`. Check both themes, every palette swatch, the simulated-data badge and all three decision stamps.
- Open `design/cover.html`. It should remain a complete 16:9 slide as the window changes size.
- Open the five SVGs in `design/assets/`. They are illustrative pitch visuals, not measured results.

## Logger

- Open `logger/index.html`. Enter vehicle details and continue to the starting battery entry.
- Start a trip and allow location access if asked. In Chrome, local files may work for some checks; use the team's HTTPS GitHub Pages link for the real phone GPS test.
- Enter at least three battery readings while safely stopped.
- Reload during a trip. Saved readings and GPS points should restore.
- End the trip after saving the final battery reading. A JSON download should start. If Chrome blocks it, use the saved trip's Download button.
- Open the JSON in a text editor. Check `schema` is `verity.trip.v1`, GPS points exist, battery readings exist and `is_simulated` is false.
- Run a five-minute walk on your phone using the HTTPS link. GPS accuracy must be 50 metres or better for points to be saved. Keep the page visible.

## If something fails

Press F12, open Console and reload. Take a screenshot of any red errors and the address bar. File-not-found errors usually mean a missing folder or a renamed file. Live map tiles, speech playback and real phone GPS require testing on the actual device; data checks alone do not verify them.

Upload the extracted `logger`, `design` and `web` folders to the team repository. Do not upload the ZIP or any API keys.

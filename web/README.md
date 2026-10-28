# Verity frontend — how to open and test

These pages use plain HTML, CSS and JavaScript. No install or build step is needed.

## Open the project

1. Right-click the downloaded ZIP and choose **Extract All**.
2. Open the extracted folder. Keep `design`, `web` and `logger` next to each other.
3. Double-click `web/console.html` or `web/driver.html` to open it in Chrome.
4. The address must show the extracted folder. Do not open the HTML inside the ZIP or from a temporary ZIP folder.
5. Keep internet connected for Google Fonts, Leaflet and OpenStreetMap tiles.

`API_URL` in `web/js/api.js` is empty for mock mode. Keep it empty until the team engine is ready. Every example response is simulated and the pages show a visible badge.

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

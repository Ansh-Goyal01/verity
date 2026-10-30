/* Verity Mission Control. Reads only through VerityAPI (live engine, saved engine snapshot or mocks)
   and the saved experiment results bundled in js/snapshot.js. Never invents a value: when data is
   missing a panel says so. */
(function () {
  'use strict';
  const C = window.VerityCore, API = window.VerityAPI;
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = C.escapeHtml, pct = C.pct, isNum = C.isNum;
  const REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const TABS = ['mission', 'dispatch', 'trip', 'trust', 'evidence', 'engine'];
  const TAB_NAMES = { mission: 'Mission', dispatch: 'Dispatch', trip: 'Live trip', trust: 'Charger trust', evidence: 'Evidence', engine: 'Engine' };
  const state = { scenario: null, plans: {}, chargers: [], replays: {}, results: null, selected: 'V12', active: null, rendered: {} };
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };
  const tok = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const trustColor = t => tok({ high: '--go', medium: '--amber', low: '--nogo' }[t] || '--unknown');
  const vehicle = id => state.scenario?.vehicles.find(v => v.vehicle_id === id);
  const plan = id => state.plans[id];
  const round1 = v => isNum(v) ? Math.round(v * 10) / 10 : '—';
  const pctInt = v => isNum(v) ? `${Math.round(v * 100)}%` : '—';

  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.classList.add('show');
    clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove('show'), 3200);
  }

  /* ------------------------------------------------------------------ theme */
  const ICON_MOON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" fill="currentColor"/></svg>';
  const ICON_SUN = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5" fill="currentColor"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></g></svg>';
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const btn = $('#theme');
    btn.innerHTML = theme === 'light' ? ICON_MOON : ICON_SUN;
    btn.setAttribute('aria-label', theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
  }
  function toggleTheme() {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    applyTheme(next); store.set('verity-theme', next);
    state.rendered = {}; if (state.scenario && state.active) renderTab(state.active);
  }

  /* ------------------------------------------------------------------ router */
  function parseHash() {
    const [tab, arg] = location.hash.replace(/^#\/?/, '').split('/');
    return { tab: TABS.includes(tab) ? tab : 'mission', arg };
  }
  function moveInk() {
    const t = $(`.tab[aria-selected="true"]`), ink = $('.tab-ink');
    if (!t) return;
    ink.style.width = t.offsetWidth + 'px'; ink.style.transform = `translateX(${t.offsetLeft}px)`;
  }
  function show() {
    if (!state.scenario) return;
    const { tab, arg } = parseHash();
    if (arg && state.plans[arg]) { if (state.selected !== arg) { state.selected = arg; state.rendered.dispatch = false; } }
    if (state.active === 'trip' && tab !== 'trip') trip.pause();
    state.active = tab;
    TABS.forEach(t => {
      const on = t === tab;
      $('#view-' + t).hidden = !on;
      const b = $('#tab-' + t); b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1;
    });
    document.title = `Verity · ${TAB_NAMES[tab]}`;
    moveInk();
    renderTab(tab);
    enter(tab);
    requestAnimationFrame(() => Object.values(maps).forEach(m => { if (m.el.offsetParent) { m.map.invalidateSize(); if (!m.userMoved) fitMap(m); } }));
  }
  /* Spring entrance for a tab and its cards (Motion, vendored). Without Motion the CSS fade runs. */
  function enter(tab) {
    const M = window.Motion; if (!M || REDUCED) return;
    const view = $('#view-' + tab);
    M.animate(view, { opacity: [0, 1], y: [12, 0], filter: ['blur(6px)', 'blur(0px)'] }, { type: 'spring', bounce: 0, visualDuration: 0.45 });
    const cards = $$('.panel, .strip, .kpi, .ev, .rcard, .flow li, .pipe, .tile, .doc, .release', view).filter(el => el.offsetParent).slice(0, 24);
    if (cards.length) M.animate(cards, { opacity: [0, 1], y: [18, 0] }, { type: 'spring', bounce: 0.15, visualDuration: 0.55, delay: M.stagger(0.035, { startDelay: 0.05 }) });
  }
  function go(tab, arg) { location.hash = '#/' + tab + (arg ? '/' + arg : ''); }
  function renderTab(tab) {
    if (state.rendered[tab]) return;
    state.rendered[tab] = true;
    try { ({ mission: renderMission, dispatch: renderDispatch, trip: () => trip.render(), trust: renderTrust, evidence: renderEvidence, engine: renderEngine })[tab](); }
    catch (e) { console.error(e); $('#view-' + tab).insertAdjacentHTML('afterbegin', `<p class="state note warn">This section could not be drawn: ${esc(e.message)}</p>`); }
  }

  /* ------------------------------------------------------------------ maps */
  const maps = {};
  const OSM = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  function getMap(id) {
    if (maps[id]) return maps[id];
    const el = $('#' + id);
    if (!window.L) { el.innerHTML = '<p class="state" style="padding:16px">The map library could not load. Check your internet connection and reload. All plan values are still shown beside the map.</p>'; return null; }
    const map = L.map(el, { scrollWheelZoom: false, zoomSnap: 0.25 });
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: OSM }).addTo(map);
    map.attributionControl.setPrefix(false);
    const m = { id, el, map, tiles, layer: L.layerGroup().addTo(map), bounds: null, userMoved: false, routeOnly: false };
    let failed = 0;
    tiles.on('tileerror', () => { failed++; if (id === 'd-map') $('#d-mapnote').textContent = 'Some street-map tiles failed to load. The route and charger positions are still correct; use “Route-only view” or retry later.'; else if (failed === 1) toast('Street-map tiles failed to load. Route and chargers are still drawn.'); });
    tiles.on('load', () => { if (id === 'd-map' && !m.routeOnly) $('#d-mapnote').textContent = ''; failed = 0; });
    map.on('dragstart', () => { m.userMoved = true; });
    el.addEventListener('click', () => map.scrollWheelZoom.enable(), { once: true });
    if ('ResizeObserver' in window) new ResizeObserver(() => map.invalidateSize()).observe(el);
    maps[id] = m; return m;
  }
  function fitMap(m) { if (m && m.bounds && m.el.offsetParent) { m.map.invalidateSize(); m.map.fitBounds(m.bounds, { padding: [28, 28], maxZoom: 13, animate: false }); m.userMoved = false; } }
  function routePoints(p) { return (p?.route?.polyline || []).map(x => [Number(x[0]), Number(x[1])]).filter(x => x.every(Number.isFinite)); }
  function drawRoute(m, pts) {
    L.polyline(pts, { color: tok('--amber'), weight: 12, opacity: .16, interactive: false }).addTo(m.layer);
    L.polyline(pts, { color: tok('--amber'), weight: 4, opacity: 1, interactive: false }).addTo(m.layer);
  }
  function endpointMarkers(m, b) {
    const s = tok('--text'), bg = tok('--bg');
    [[b.origin, 'A'], [b.destination, 'B']].forEach(([p, l]) => L.circleMarker([p.lat, p.lon], { radius: 9, color: bg, weight: 3, fillColor: s, fillOpacity: 1 }).bindTooltip(`<span class="tt-k">${l === 'A' ? 'Origin' : 'Destination'}</span><br><b>${esc(p.name)}</b>`).addTo(m.layer));
  }
  function chargerTip(c) {
    return `<span class="tt-k">${esc(c.source || 'directory')} · ${esc(c.charger_id)}</span><br><b>${esc(c.name)}</b><br>`
      + `${c.p_works === null || c.p_works === undefined ? 'Chance it works: unknown' : pct(c.p_works) + ' chance it works'}<br>`
      + `${isNum(c.n_reports) ? c.n_reports : '—'} reports · ${c.days_since_last === null || c.days_since_last === undefined ? 'no recent report' : 'last ' + c.days_since_last + ' days ago'}<br>`
      + `Trust: <b>${esc(c.trust_level || 'unknown')}</b>${c.operator ? ' · ' + esc(c.operator) : ''}`;
  }

  /* ------------------------------------------------------------------ shared UI */
  function stamp(decision, extra = '') { return `<span class="stamp ${C.decisionClass(decision)} ${extra}" aria-label="Decision: ${C.decisionLabel(decision)}">${C.decisionLabel(decision)}</span>`; }
  const simTag = on => on ? '<span class="sim">Simulated</span>' : '';

  function setSource() {
    const el = $('#source'), src = API.dataSource(), sim = state.scenario?.is_simulated;
    const text = { live: 'Live engine', snapshot: 'Engine snapshot', mock: 'Illustrative mock' }[src] + (sim ? ' · Simulated' : '');
    el.className = 'source ' + (src === 'live' ? 'live' : '');
    el.querySelector('span').textContent = text;
    el.title = src === 'live' ? 'Connected to the engine API' : src === 'snapshot' ? 'Saved real engine outputs from docs/api_examples (the simulated demo scenario). Add ?api=http://127.0.0.1:8000 to use the live engine.' : 'Hand-written illustrative data, not engine output.';
  }
  function setTicker() {
    const b = state.scenario.booking, n = state.scenario.vehicles.length;
    const when = new Date(b.departure_time).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Kolkata' }) + ' IST';
    const sep = '<span class="sep">·</span>';
    $('#ticker-body').innerHTML = [esc(b.booking_id), `<b>${esc(b.origin.name)}</b> → <b>${esc(b.destination.name)}</b>`, `DEP <b>${esc(when)}</b>`, `<b>${b.passengers}</b> PAX`, `AC <b>${b.ac_on ? 'ON' : 'OFF'}</b>`, `TARGET <b>${pct(b.target_confidence)}</b>`, `<b>${n}</b> VEHICLES`].join(` ${sep} `)
      + (state.scenario.is_simulated ? ` ${sep} <span class="sim">Simulated</span>` : '');
  }

  /* ================================================================== 01 MISSION */
  let heroTimer = null;
  function releaseDoc(id, animate) {
    const p = plan(id), v = vehicle(id), b = state.scenario.booking;
    const refused = p.decision === 'INSUFFICIENT_EVIDENCE';
    return `<article class="doc" aria-label="Dispatch release for ${esc(id)}">
      <div class="doc-top"><div><div class="doc-title">DISPATCH RELEASE</div><div class="doc-id">${esc(p.plan_id)} · ${esc(id)} · ${esc(v?.label || '')}</div></div></div>
      <div class="doc-route"><span>${esc(b.origin.name)}</span><span class="arrow"></span><span>${esc(b.destination.name)}</span></div>
      <div class="doc-stamp">${stamp(p.decision, 'big' + (animate && !REDUCED ? ' slam' : ''))}</div>
      <div class="doc-grid">
        <div><small>Confidence</small><b>${pct(p.confidence)}</b></div>
        <div><small>Target</small><b>${pct(b.target_confidence)}</b></div>
        <div><small>Arrival p50</small><b>${isNum(p.arrival_soc?.p50) ? p.arrival_soc.p50 + '%' : '—'}</b></div>
        <div><small>Charging stops</small><b>${p.stops?.length ?? '—'}</b></div>
        <div><small>Alternates</small><b>${p.alternates?.length ?? '—'}</b></div>
        <div><small>No return at</small><b>${isNum(p.point_of_no_return_km) ? round1(p.point_of_no_return_km) + ' km' : '—'}</b></div>
      </div>
      ${refused ? `<p class="note" style="color:var(--unknown)">${esc(p.refusal_reason)}</p>` : ''}
      <div class="doc-foot">${simTag(p.is_simulated)}<span>Start battery ${isNum(v?.soc_pct) ? v.soc_pct + '%' : '—'}</span><span>Computed in ${isNum(p.compute_ms) ? p.compute_ms + ' ms' : '—'}</span></div>
    </article>`;
  }
  function renderMission() {
    const ids = state.scenario.vehicles.map(v => v.vehicle_id);
    const lead = plan('V12') ? 'V12' : ids[0], lp = plan(lead);
    const runs = state.results?.e4?.compute?.runs_per_forecast;
    $('#hero-facts').innerHTML = [
      ['Route', isNum(lp.route?.distance_km) ? `${round1(lp.route.distance_km)} km` : '—'],
      ['Drive time', C.fmtDuration(lp.route?.duration_min)],
      ['Runs per forecast', isNum(runs) ? runs.toLocaleString('en-IN') : '—'],
      ['Release computed', isNum(lp.compute_ms) ? `${(lp.compute_ms / 1000).toFixed(1)} s` : '—'],
    ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');

    // Rotating release document: one real plan at a time.
    let i = Math.max(0, ids.indexOf(lead));
    const hero = $('#hero-release');
    const paint = animate => { hero.innerHTML = releaseDoc(ids[i], animate) + `<div class="legend" style="justify-content:center">${ids.map((id, j) => `<button class="chip" data-hero="${j}" aria-pressed="${j === i}">${esc(id)}</button>`).join('')}</div>`; };
    paint(true);
    hero.onclick = e => { const b = e.target.closest('[data-hero]'); if (b) { i = +b.dataset.hero; paint(true); restart(); } };
    const restart = () => { clearInterval(heroTimer); if (!REDUCED) heroTimer = setInterval(() => { if (state.active === 'mission' && !hero.matches(':hover')) { i = (i + 1) % ids.length; paint(true); } }, 5200); };
    restart();

    $('#mission-strips').innerHTML = ids.map(id => {
      const p = plan(id), v = vehicle(id), cls = C.decisionClass(p.decision), refused = p.decision === 'INSUFFICIENT_EVIDENCE';
      return `<button class="strip ${cls}" data-go="${esc(id)}">
        <div class="strip-head"><div><div class="strip-id">${esc(id)}</div><div class="strip-label">${esc(v.label)} · ${esc(String(v.model_class || '').replace(/_/g, ' '))}</div></div>${stamp(p.decision)}</div>
        <div class="strip-rows">
          <div><small>Confidence</small><b>${pct(p.confidence)}</b></div>
          <div><small>Arrival p10–p90</small><b>${isNum(p.arrival_soc?.p10) ? `${p.arrival_soc.p10}–${p.arrival_soc.p90}%` : '—'}</b></div>
          <div><small>Start battery</small><b>${isNum(v.soc_pct) ? v.soc_pct + '%' : '—'}</b></div>
          <div><small>Trips calibrated</small><b>${v.trips_calibrated ?? '—'}</b></div>
        </div>
        <p class="strip-note">${esc(refused ? p.refusal_reason : v.health_note)}</p>
        <div class="soc" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, v.soc_pct || 0))}%"></span></div>
      </button>`;
    }).join('');
    $$('#mission-strips [data-go]').forEach(b => b.onclick = () => go('dispatch', b.dataset.go));

    renderKpis();
    const r = ids.map(plan).find(p => p.decision === 'INSUFFICIENT_EVIDENCE');
    const box = $('#mission-refuse');
    if (r) {
      box.innerHTML = `<div>${stamp(r.decision, 'big')}</div><div><h3>Refuse rather than guess.</h3><p>Most range tools always give an answer. Verity will not release ${esc(r.vehicle_id)}, even though its point forecast looks fine, because the plan leans on chargers nobody has reported on recently.</p><blockquote>${esc(r.refusal_reason)}</blockquote><p style="margin-top:14px"><a class="btn" href="#/dispatch/${esc(r.vehicle_id)}">See what would fix it →</a></p></div>`;
    } else box.hidden = true;
  }
  function renderKpis() {
    const R = state.results, out = [];
    if (!R) { $('#mission-kpis').innerHTML = '<p class="state">Saved experiment results are not loaded on this page.</p>'; return; }
    const e4 = R.e4?.scenarios?.['40% chargers down'];
    if (e4) out.push({ big: `<span class="bad">${pctInt(e4.stranding_rate_fixed)}</span><em>→</em><span class="good">${pctInt(e4.stranding_rate_dispatch)}</span>`, text: `Trips stranded when 40% of chargers are down: fixed shortest-path plan vs Verity dispatch (${e4.trips} trips).`, src: 'results/e4_dispatch_vs_fixed_simulated.json', tag: 'E4 · Dispatch vs fixed' });
    const sh = R.e2?.methods?.shared, ind = R.e2?.methods?.independent;
    if (sh && ind) out.push({ big: `<span class="bad">${pctInt(ind.coverage_90_arrival_band)}</span><em>→</em><span class="good">${pctInt(sh.coverage_90_arrival_band)}</span>`, text: `How often the real arrival battery landed inside the 90% band: naive independent noise vs shared trip factors (${R.e2.trips} trips).`, src: 'results/e2_calibration_simulated.json', tag: 'E2 · Calibration' });
    const r12 = R.e5?.scenarios?.['ring of 12']?.mae_on_attacked_chargers;
    if (r12) out.push({ big: `<span class="bad">${r12.avg_rating.toFixed(2)}</span><em>→</em><span class="good">${r12.full.toFixed(2)}</span>`, text: 'Error in “does this charger work?” on chargers attacked by a ring of 12 fake reporters: plain average rating vs Verity trust engine.', src: 'results/e5_trust_recovery_simulated.json', tag: 'E5 · Fake-report rings' });
    const tests = R.run_all?.steps?.find(s => s.step === 'tests');
    const m = tests?.last_line?.match(/(\d+) passed(?:, (\d+) skipped)?/);
    if (m) out.push({ big: `<span class="good">${m[1]}</span><em>tests</em>`, text: `Passing in the last full run${m[2] ? ` (${m[2]} skipped)` : ''}, alongside experiments E1–E5 regenerated from a clean state.`, src: 'results/run_all_summary.json', tag: 'Test suite', sim: false });
    $('#mission-kpis').innerHTML = out.map(k => `<div class="kpi"><div class="kpi-top"><span class="label">${esc(k.tag)}</span>${k.sim === false ? '' : simTag(true)}</div><div class="kpi-big">${k.big}</div><p>${k.text}</p><div class="src">${esc(k.src)}</div></div>`).join('');
  }

  /* ================================================================== 02 DISPATCH */
  function renderDispatch() {
    const ids = state.scenario.vehicles.map(v => v.vehicle_id);
    $('#rail').innerHTML = ids.map(id => {
      const p = plan(id);
      return `<button class="rcard ${C.decisionClass(p.decision)}" data-id="${esc(id)}" aria-pressed="${id === state.selected}">
        <div class="rcard-head"><span class="rcard-id">${esc(id)}</span>${stamp(p.decision)}</div>
        <div class="rcard-meta"><span>Conf. <b>${pct(p.confidence)}</b></span><span>Arr. <b>${isNum(p.arrival_soc?.p50) ? p.arrival_soc.p50 + '%' : '—'}</b></span><span>SoC <b>${vehicle(id).soc_pct}%</b></span></div>
      </button>`;
    }).join('');
    $$('#rail .rcard').forEach(b => b.onclick = () => go('dispatch', b.dataset.id));

    const id = state.selected, p = plan(id), v = vehicle(id), b = state.scenario.booking;
    const cls = C.decisionClass(p.decision), refused = p.decision === 'INSUFFICIENT_EVIDENCE';
    const circ = 2 * Math.PI * 52, conf = isNum(p.confidence) ? p.confidence : 0;
    const a = p.arrival_soc || {};
    $('#release').className = 'release ' + cls;
    $('#release').innerHTML = `
      <div class="rel-top">
        <div class="gauge" style="--c:var(--${cls})" role="img" aria-label="Confidence ${pct(p.confidence)}, target ${pct(b.target_confidence)}">
          <svg viewBox="0 0 120 120"><circle class="g-bg" cx="60" cy="60" r="52" fill="none" stroke-width="10"/>
          <circle class="g-val" cx="60" cy="60" r="52" fill="none" stroke-width="10" stroke-dasharray="${circ}" stroke-dashoffset="${REDUCED ? circ * (1 - conf) : circ}" data-off="${circ * (1 - conf)}"/>
          <circle class="g-tgt" cx="60" cy="60" r="52" fill="none" stroke-width="14" stroke-dasharray="2 ${circ}" stroke-dashoffset="${-circ * b.target_confidence + 1}"/></svg>
          <div class="gauge-txt"><b>${pct(p.confidence)}</b><small>CONFIDENCE</small></div>
        </div>
        <div class="rel-title"><small>DISPATCH RELEASE · ${esc(p.plan_id)}</small><h2>${esc(id)} · ${esc(v.label)} — ${esc(b.origin.name)} to ${esc(b.destination.name)}</h2><p>${esc(v.health_note)} · ${v.trips_calibrated} calibrated trips · usable ${isNum(v.usable_kwh_estimate?.p50) ? v.usable_kwh_estimate.p50 + ' kWh (p10 ' + v.usable_kwh_estimate.p10 + ', p90 ' + v.usable_kwh_estimate.p90 + ')' : '—'}</p></div>
        ${stamp(p.decision, 'big' + (REDUCED ? '' : ' slam'))}
      </div>
      ${refused ? `<div class="refusal-box"><b style="color:var(--unknown)">Not released.</b> ${esc(p.refusal_reason)}</div>` : ''}
      <div class="rel-stats">
        <div><small>Target</small><b>${pct(b.target_confidence)}</b></div>
        <div><small>Distance</small><b>${isNum(p.route?.distance_km) ? round1(p.route.distance_km) + ' km' : '—'}</b></div>
        <div><small>Drive time</small><b>${C.fmtDuration(p.route?.duration_min)}</b></div>
        <div><small>Charging stops</small><b>${p.stops?.length ?? '—'}</b></div>
        <div><small>Point of no return</small><b>${isNum(p.point_of_no_return_km) ? round1(p.point_of_no_return_km) + ' km' : '—'}</b></div>
        <div><small>Computed in</small><b>${isNum(p.compute_ms) ? p.compute_ms + ' ms' : '—'}</b></div>
      </div>
      ${isNum(a.p10) && isNum(a.p90) ? `<div class="band"><div class="band-cap">Arrival battery: <b>${a.p10}%–${a.p90}%</b> (p10–p90 of simulated runs), median <b>${a.p50}%</b></div>
        <div class="band-bar" style="--c:var(--${cls})"><div class="band-range" style="left:${a.p10}%;width:${Math.max(.5, a.p90 - a.p10)}%"></div>${isNum(a.p50) ? `<div class="band-mid" style="left:${a.p50}%"></div>` : ''}</div>
        <div class="band-scale"><span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span></div></div>` : ''}`;
    if (!REDUCED) requestAnimationFrame(() => requestAnimationFrame(() => { const g = $('#release .g-val'); if (g) g.style.strokeDashoffset = g.dataset.off; }));

    renderDispatchMap(p);
    renderPlan(p, v);
    $('#d-withheld').hidden = !refused;
    $('#d-charts').hidden = refused;
    if (refused) $('#d-withheld').innerHTML = '<b>Bingo Line and energy breakdown are withheld</b> because this trip was not released. Verify the chargers listed under Levers, then run dispatch again.';
    else { drawBingo(p); drawWaterfall(p, v.soc_pct); }
    renderLevers(p);
    renderRisks(p);
  }

  function renderDispatchMap(p) {
    const m = getMap('d-map'); if (!m) return;
    m.layer.clearLayers();
    const pts = routePoints(p);
    if (pts.length < 2) { $('#d-mapnote').textContent = 'No route coordinates are available for this plan.'; return; }
    m.bounds = L.latLngBounds(pts);
    const near = m.bounds.pad(0.08);
    state.chargers.filter(c => near.contains([c.lat, c.lon])).forEach(c => L.circleMarker([c.lat, c.lon], { radius: 4, weight: 1, color: trustColor(c.trust_level), fillColor: trustColor(c.trust_level), fillOpacity: .55 }).bindTooltip(chargerTip(c)).addTo(m.layer));
    drawRoute(m, pts);
    const cum = C.cumulativeKm(pts);
    if (isNum(p.point_of_no_return_km)) {
      const at = C.pointAtKm(pts, cum, p.route.distance_km, p.point_of_no_return_km);
      if (at) L.circleMarker(at, { radius: 7, color: tok('--nogo'), weight: 3, fillColor: tok('--bg'), fillOpacity: 1 }).bindTooltip(`<span class="tt-k">Point of no return</span><br><b>km ${round1(p.point_of_no_return_km)}</b><br>Past here, turning back is no longer the safe option.`).addTo(m.layer);
    }
    (p.alternates || []).forEach(a => L.circleMarker([a.lat, a.lon], { radius: 9, color: tok('--unknown'), weight: 3, fillColor: tok('--bg'), fillOpacity: 1 }).bindTooltip(`<span class="tt-k">Alternate · ${esc(a.charger_id)}</span><br><b>${esc(a.name)}</b><br>${pct(a.p_reach)} chance to reach · ${pct(a.p_works)} chance it works`).addTo(m.layer));
    (p.stops || []).forEach((s, i) => L.circleMarker([s.lat, s.lon], { radius: 11, color: tok('--bg'), weight: 3, fillColor: tok('--amber'), fillOpacity: 1 }).bindTooltip(`<span class="tt-k">Planned stop ${i + 1} · km ${round1(s.km_along_route)}</span><br><b>${esc(s.name)}</b><br>${pct(s.p_works)} chance it works · charge ${s.charge_minutes} min<br>${s.evidence?.n_reports ?? '—'} reports · trust ${esc(s.evidence?.trust_level || 'unknown')}`).addTo(m.layer));
    endpointMarkers(m, state.scenario.booking);
    $('#d-legend').innerHTML = `<span><i style="background:var(--amber)"></i>Planned stop</span><span><i class="ring" style="border-color:var(--unknown)"></i>Alternate</span><span><i class="ring" style="border-color:var(--nogo)"></i>Point of no return</span>` + ['high', 'medium', 'low', 'unknown'].map(t => `<span><i style="background:${trustColor(t)};opacity:.7"></i>${t} trust</span>`).join('');
    requestAnimationFrame(() => fitMap(m));
  }

  function renderPlan(p, v) {
    const items = [{ km: 0, cls: 'start', title: state.scenario.booking.origin.name, body: `Depart with ${v.soc_pct}% battery`, tags: [] }];
    (p.stops || []).forEach((s, i) => items.push({ km: s.km_along_route, cls: 'stop', title: s.name, body: `Arrive around ${s.arrive_soc_p50}% (p50) and charge for ${s.charge_minutes} min`, tags: [`<span class="tag"><b>${pct(s.p_works)}</b> works</span>`, `<span class="tag">${s.evidence?.n_reports ?? '—'} reports${isNum(s.evidence?.days_since_last) ? `, last ${s.evidence.days_since_last} d ago` : ''}</span>`, `<span class="tag ${esc(s.evidence?.trust_level || 'unknown')}">trust ${esc(s.evidence?.trust_level || 'unknown')}</span>`], label: `Stop ${i + 1}` }));
    if (isNum(p.point_of_no_return_km)) items.push({ km: p.point_of_no_return_km, cls: 'ponr', title: 'Point of no return', body: 'After this point the safe options are ahead, not behind.', tags: [] });
    items.push({ km: p.route?.distance_km, cls: 'end', title: state.scenario.booking.destination.name, body: isNum(p.arrival_soc?.p50) ? `Arrive with about ${p.arrival_soc.p50}% (p50)` : 'Arrival battery unavailable', tags: [] });
    items.sort((a, b) => (a.km ?? 0) - (b.km ?? 0));
    const alts = p.alternates || [];
    $('#d-plan').innerHTML = `<ol class="plan">${items.map(it => `<li><span class="pin ${it.cls}"></span><div class="p-km">KM ${round1(it.km)}${it.label ? ' · ' + it.label.toUpperCase() : ''}</div><h4>${esc(it.title)}</h4><p>${esc(it.body)}</p>${it.tags.length ? `<div class="p-tags">${it.tags.join('')}</div>` : ''}</li>`).join('')}</ol>`
      + (alts.length ? `<div class="alts"><h5>Backup chargers if a stop fails</h5>${alts.map(a => `<div class="alt"><div>${esc(a.name)}</div><span>reach ${pct(a.p_reach)}</span><span>works ${pct(a.p_works)}</span></div>`).join('')}</div>` : '<p class="note">No alternates were returned for this plan.</p>');
  }

  function drawBingo(p) {
    const el = $('#d-bingo'), data = p.bingo_line, dist = p.route?.distance_km;
    if (!Array.isArray(data) || !data.length || !isNum(dist)) { el.innerHTML = '<p class="state">Bingo Line unavailable from the API. No values have been assumed.</p>'; return; }
    const W = 640, H = 300, L = 40, R = 14, T = 14, B = 36;
    const x = k => L + k / dist * (W - L - R), y = v => T + (100 - v) / 100 * (H - T - B);
    const line = key => data.map(q => `${x(q.km).toFixed(1)},${y(q[key]).toFixed(1)}`).join(' ');
    const area = data.map((q, i) => `${i ? 'L' : 'M'}${x(q.km).toFixed(1)},${y(q.expected_soc_p90).toFixed(1)}`).join('') + data.slice().reverse().map(q => `L${x(q.km).toFixed(1)},${y(q.expected_soc_p10).toFixed(1)}`).join('') + 'Z';
    const step = dist > 150 ? 50 : 25, ticks = []; for (let k = 0; k < dist - step * .4; k += step) ticks.push(k); ticks.push(dist);
    const grid = [0, 20, 40, 60, 80, 100].map(v => `<path d="M${L} ${y(v)}H${W - R}" stroke="var(--line)"/><text x="${L - 7}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="var(--faint)">${v}</text>`).join('');
    const xt = ticks.map(k => `<text x="${x(k)}" y="${H - 16}" text-anchor="middle" font-size="10" fill="var(--faint)">${round1(k)}</text>`).join('');
    const stops = (p.stops || []).map(s => `<g><path d="M${x(s.km_along_route)} ${T}V${H - B}" stroke="var(--amber)" stroke-dasharray="2 4" opacity=".6"/><rect x="${x(s.km_along_route) - 14}" y="${H - B - 16}" width="28" height="14" rx="3" fill="var(--amber)"/><text x="${x(s.km_along_route)}" y="${H - B - 6}" text-anchor="middle" font-size="9" font-weight="700" fill="var(--amber-ink)">CHG</text></g>`).join('');
    const ponr = isNum(p.point_of_no_return_km) ? `<path d="M${x(p.point_of_no_return_km)} ${T}V${H - B}" stroke="var(--nogo)" stroke-dasharray="5 4"/><text x="${x(p.point_of_no_return_km) + 5}" y="${T + 11}" font-size="10" fill="var(--nogo)">NO RETURN ${round1(p.point_of_no_return_km)} km</text>` : '';
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Expected battery band, expected battery and Bingo Line along the route${p.is_simulated ? ' (simulated)' : ''}">
      <title>Bingo Line${p.is_simulated ? ' (simulated)' : ''}</title>${grid}
      <path d="${area}" fill="var(--amber)" fill-opacity=".18" class="fade"/>
      <polyline points="${line('expected_soc_pct')}" fill="none" stroke="var(--amber)" stroke-width="2.5" pathLength="1" class="${REDUCED ? '' : 'draw'}" style="--len:1"/>
      <polyline points="${line('bingo_soc_pct')}" fill="none" stroke="var(--nogo)" stroke-width="2.5" pathLength="1" class="${REDUCED ? '' : 'draw'}" style="--len:1"/>
      ${ponr}${stops}${xt}
      <text x="${(W + L) / 2}" y="${H - 2}" text-anchor="middle" font-size="10" fill="var(--faint)">KM ALONG ROUTE</text>
      <text x="${L + 4}" y="${T + 10}" font-size="10" fill="var(--faint)">BATTERY %</text>
      <g class="xh" visibility="hidden"><path class="xh-l" d="M0 ${T}V${H - B}" stroke="var(--text)" stroke-width="1" opacity=".5"/><circle class="xh-e" r="5" fill="var(--amber)" stroke="var(--bg)" stroke-width="2"/><circle class="xh-b" r="5" fill="var(--nogo)" stroke="var(--bg)" stroke-width="2"/></g>
      <rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" style="cursor:crosshair"/>
    </svg>
    <div class="legend"><span><i class="sq" style="background:var(--amber);opacity:.35"></i>Expected range p10–p90</span><span><i class="sq" style="background:var(--amber)"></i>Expected battery</span><span><i class="sq" style="background:var(--nogo)"></i>Bingo Line: divert below this</span></div>
    <div class="readout" aria-live="polite"></div>`;
    const svg = el.querySelector('svg'), xh = svg.querySelector('.xh'), ro = el.querySelector('.readout');
    const read = q => {
      const head = q.expected_soc_pct - q.bingo_soc_pct;
      ro.innerHTML = `<div><small>At km</small><b>${round1(q.km)}</b></div><div><small>Expected</small><b>${round1(q.expected_soc_pct)}%</b></div><div><small>Range p10–p90</small><b>${round1(q.expected_soc_p10)}–${round1(q.expected_soc_p90)}%</b></div><div><small>Bingo · headroom</small><b style="color:${head < 0 ? 'var(--nogo)' : 'inherit'}">${round1(q.bingo_soc_pct)}% · ${head >= 0 ? '+' : ''}${round1(head)}</b></div>`;
    };
    read(data[0]);
    const move = evt => {
      const r = svg.getBoundingClientRect(), px = (evt.clientX - r.left) / r.width * W;
      const q = C.nearestPoint(data, (px - L) / (W - L - R) * dist); if (!q) return;
      xh.setAttribute('visibility', 'visible');
      xh.querySelector('.xh-l').setAttribute('transform', `translate(${x(q.km)},0)`);
      const e = xh.querySelector('.xh-e'), bb = xh.querySelector('.xh-b');
      e.setAttribute('cx', x(q.km)); e.setAttribute('cy', y(q.expected_soc_pct)); bb.setAttribute('cx', x(q.km)); bb.setAttribute('cy', y(q.bingo_soc_pct));
      read(q);
    };
    const hit = svg.querySelector('.hit');
    hit.addEventListener('pointermove', move); hit.addEventListener('pointerdown', move);
    hit.addEventListener('pointerleave', () => xh.setAttribute('visibility', 'hidden'));
  }

  function drawWaterfall(p, start) {
    const el = $('#d-waterfall'), wf = C.waterfall(start, p.breakdown, p.arrival_soc?.p50);
    if (!wf.ok) { el.innerHTML = `<p class="state" role="status">${esc(wf.reason)} No values have been assumed.</p>`; return; }
    const n = wf.rows.length, W = Math.max(560, n * 62 + 50), H = 310, L = 36, R = 8, T = 24, B = 66;
    const y = v => T + (wf.high - v) / (wf.high - wf.low) * (H - T - B), gap = (W - L - R) / n, bw = Math.min(40, gap * .55);
    let html = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Energy breakdown in battery percentage points${p.is_simulated ? ' (simulated)' : ''}"><title>Energy breakdown${p.is_simulated ? ' (simulated)' : ''}</title>`;
    for (let v = Math.ceil(wf.low / 20) * 20; v <= wf.high; v += 20) html += `<path d="M${L} ${y(v)}H${W - R}" stroke="${v === 0 ? 'var(--line-strong)' : 'var(--line)'}"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="var(--faint)">${v}</text>`;
    wf.rows.forEach((r, i) => {
      const cx = L + gap * i + gap / 2, x0 = cx - bw / 2, top = y(Math.max(r.from, r.to)), bot = y(Math.min(r.from, r.to));
      const color = r.absolute ? (r.term === 'start' ? 'var(--amber)' : 'var(--text)') : r.value >= 0 ? 'var(--go)' : 'var(--nogo)';
      const label = r.absolute ? `${r.value}%` : `${r.value > 0 ? '+' : ''}${r.value}`;
      html += `<g><title>${esc(r.label)}: ${esc(label)}${r.absolute ? '' : ' points'}</title>`;
      html += r.from === r.to ? `<path d="M${x0} ${top}h${bw}" stroke="${color}" stroke-width="2.5"/>` : `<rect x="${x0}" y="${top}" width="${bw}" height="${Math.max(0, bot - top)}" rx="3" fill="${color}" class="${REDUCED ? '' : 'grow'}" style="animation-delay:${i * 70}ms"/>`;
      if (i < n - 1) html += `<path d="M${x0 + bw} ${y(r.to)}H${x0 + gap}" stroke="var(--line-strong)" stroke-dasharray="3 3"/>`;
      html += `<text x="${cx}" y="${Math.max(12, top - 7)}" text-anchor="middle" font-size="11.5" font-weight="600" fill="var(--text)">${esc(label)}</text>`;
      const lines = []; for (const w of r.label.split(/\s+|(?<=-)/)) { const prev = lines[lines.length - 1], joint = prev && prev.endsWith('-') ? '' : ' '; if (!lines.length || prev.length + w.length + joint.length > 10) lines.push(w); else lines[lines.length - 1] += joint + w; }
      html += `<text x="${cx}" y="${H - B + 20}" text-anchor="middle" font-size="10.5" fill="var(--muted)">${lines.map((t, j) => `<tspan x="${cx}" dy="${j ? 13 : 0}">${esc(t)}</tspan>`).join('')}</text></g>`;
    });
    html += '</svg>';
    html += `<p class="note">Battery percentage points by physics term${p.is_simulated ? ' (simulated)' : ''}. Bars are an energy attribution, not a timeline of the trip.</p>`;
    if (!wf.reconciles) html += '<p class="note warn" role="status">The supplied start and breakdown do not reconcile with the supplied arrival. API values are shown unchanged.</p>';
    el.innerHTML = html;
  }

  function renderLevers(p) {
    const el = $('#d-levers'), lv = p.levers || [];
    if (!lv.length) { el.innerHTML = '<p class="state">The engine returned no levers for this plan.</p>'; return; }
    el.innerHTML = lv.map((l, i) => `<button class="lever" data-i="${i}" aria-pressed="false"><span class="lever-label">${esc(l.label)}</span><span class="lever-cost">${isNum(l.time_cost_min) ? (l.time_cost_min > 0 ? '+' : '') + l.time_cost_min + ' min' : '—'}</span>
      <span class="lever-bar"><span>${pct(p.confidence)}</span><span class="lever-track"><span class="new" style="width:${(l.new_confidence || 0) * 100}%"></span><span class="now" style="width:${(p.confidence || 0) * 100}%"></span></span><span style="color:var(--go)">${pct(l.new_confidence)}</span></span></button>`).join('')
      + '<p class="note">Tap a lever to compare the confidence the engine computed for it with the current plan.</p>';
    $$('.lever', el).forEach(b => b.onclick = () => { const on = b.getAttribute('aria-pressed') !== 'true'; $$('.lever', el).forEach(x => x.setAttribute('aria-pressed', 'false')); b.setAttribute('aria-pressed', String(on)); });
  }
  function renderRisks(p) {
    const rs = p.risk_sources || [];
    const col = (kind, title, cls) => { const items = rs.filter(r => r.kind === kind); return `<div class="risk-col ${cls}"><h3>${title}</h3>${items.length ? items.map(r => `<div class="risk">${esc(r.label)}</div>`).join('') : '<p class="state small">No sources reported.</p>'}</div>`; };
    $('#d-risks').innerHTML = col('unknown', 'We lack data', 'unknown') + col('unpredictable', 'The world is variable', 'unpred');
  }

  /* ================================================================== 03 LIVE TRIP */
  const I18N = {
    en: { brand: 'VERITY · DRIVER', battery: 'BATTERY', toGo: 'KM TO GO', above: 'ABOVE BINGO', below: 'BELOW BINGO', ponr: 'NO RETURN', next: 'NEXT', ready: 'Press Play to start the recorded trip.', planMsg: 'Plan: charge at {name}, km {km}.', reported: 'Charger reported {status}: {name}', divert: 'DIVERT NOW', slow: 'SLOW DOWN', understood: 'Understood', safety: 'Use only when safely stopped, or let a passenger do it.', divertMessage: 'Divert to {name}. Planned charger reported broken.', noPlan: 'No charging stop planned.', done: 'Replay complete.' },
    hi: { brand: 'VERITY · ड्राइवर', battery: 'बैटरी', toGo: 'बाकी किमी', above: 'बिंगो से ऊपर', below: 'बिंगो से नीचे', ponr: 'वापसी सीमा', next: 'अगला', ready: 'रिकॉर्ड की गई यात्रा शुरू करने के लिए Play दबाएँ।', planMsg: 'योजना: {name} पर चार्ज करें, किमी {km}।', reported: 'चार्जर की रिपोर्ट ({status}): {name}', divert: 'अभी रास्ता बदलें', slow: 'धीमे चलें', understood: 'समझ गए', safety: 'आवाज़ या स्क्रीन का उपयोग सुरक्षित जगह रुकने के बाद ही करें।', divertMessage: '{name} की ओर मुड़ें। नियोजित चार्जर खराब बताया गया है।', noPlan: 'कोई चार्जिंग पड़ाव नियोजित नहीं है।', done: 'रीप्ले पूरा हुआ।' },
  };
  const SLOW_HI = 'नियोजित चार्जर खराब बताया गया है और कोई दूसरा चार्जर भरोसे से पहुँच में नहीं है: धीमे चलें और ऊर्जा बचाएँ।';
  const trip = (() => {
    const t = { id: null, lang: 'en', running: false, elapsed: 0, index: 0, last: 0, raf: 0, km: 0, soc: null, msg: null, alert: null, broken: new Set(), car: null, divertLine: null, cum: null, pts: null };
    const tr = (k, a = {}) => (I18N[t.lang][k] || k).replace(/\{(\w+)\}/g, (_, x) => String(a[x] ?? ''));
    const ids = () => state.scenario.vehicles.map(v => v.vehicle_id).filter(id => state.replays[id]);
    const findCharger = cid => { const p = plan(t.id); return (p.stops || []).find(s => s.charger_id === cid) || (p.alternates || []).find(a => a.charger_id === cid) || state.chargers.find(c => c.charger_id === cid); };
    const evDesc = e => {
      if (e.type === 'soc_reading') return { cls: 'ev-soc', head: 'Battery reading', body: `${e.soc_pct}% at km ${round1(e.km)}` };
      if (e.type === 'charger_report') { const c = findCharger(e.charger_id); return { cls: 'ev-report', head: `Charger report · ${e.status}`, body: `${c ? c.name : e.charger_id} (${e.charger_id}) · source trust ${e.source_trust}` }; }
      if (e.type === 'forecast') return { cls: 'ev-' + e.action, head: `Engine · ${String(e.action).replace('_', ' ')}`, body: e.message + (e.divert_to ? ` → ${e.divert_to.name} (${pct(e.divert_to.p_works)} works)` : '') };
      return { cls: '', head: e.type, body: '' };
    };
    function render() {
      const list = ids();
      if (!list.length) { $('#view-trip').innerHTML = '<p class="state">No trip replays are available from this data source.</p>'; return; }
      if (!list.includes(t.id)) t.id = list.includes(state.selected) ? state.selected : list.includes('V12') ? 'V12' : list[0];
      $('#t-vehicles').innerHTML = list.map(id => `<button role="radio" aria-checked="${id === t.id}" data-id="${esc(id)}">${esc(id)} ${C.decisionLabel(plan(id).decision) === 'GO' ? '' : '· ' + C.decisionLabel(plan(id).decision)}</button>`).join('');
      $$('#t-vehicles button').forEach(b => b.onclick = () => { t.id = b.dataset.id; render(); });
      $('#t-play').onclick = () => t.running ? pause() : play();
      $('#t-reset').onclick = () => { pause(); reset(); };
      reset();
    }
    function reset() {
      const p = plan(t.id), r = state.replays[t.id], v = vehicle(t.id);
      cancelAnimationFrame(t.raf); t.running = false; t.elapsed = 0; t.index = 0; t.broken = new Set(); t.alert = null;
      const first = r.events.find(e => e.type === 'soc_reading');
      t.km = 0; t.soc = first && first.t_offset_s === 0 ? first.soc_pct : v.soc_pct; t.msg = null; t.done = false;
      t.end = r.events.length ? r.events[r.events.length - 1].t_offset_s : 0;
      $('#t-play').textContent = '▶ Play';
      // map
      const m = getMap('t-map');
      if (m) {
        m.layer.clearLayers();
        t.pts = routePoints(p); t.cum = C.cumulativeKm(t.pts);
        if (t.pts.length > 1) {
          m.bounds = L.latLngBounds(t.pts); drawRoute(m, t.pts);
          (p.alternates || []).forEach(a => L.circleMarker([a.lat, a.lon], { radius: 8, color: tok('--unknown'), weight: 3, fillColor: tok('--bg'), fillOpacity: 1 }).bindTooltip(`<span class="tt-k">Alternate</span><br><b>${esc(a.name)}</b><br>${pct(a.p_works)} works`).addTo(m.layer));
          (p.stops || []).forEach(s => L.circleMarker([s.lat, s.lon], { radius: 10, color: tok('--bg'), weight: 3, fillColor: tok('--amber'), fillOpacity: 1 }).bindTooltip(`<span class="tt-k">Planned stop · km ${round1(s.km_along_route)}</span><br><b>${esc(s.name)}</b>`).addTo(m.layer));
          endpointMarkers(m, state.scenario.booking);
          t.car = L.marker(t.pts[0], { icon: L.divIcon({ className: '', html: '<div class="car-icon"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }), zIndexOffset: 1000, keyboard: false }).bindTooltip('Vehicle position at the last reported km').addTo(m.layer);
          t.divertLine = null;
          requestAnimationFrame(() => fitMap(m));
        }
      }
      // log
      $('#t-log').innerHTML = r.events.map((e, i) => { const d = evDesc(e); return `<li class="${d.cls} pending" data-i="${i}"><time>T+${C.fmtClock(e.t_offset_s)}</time><div><b>${esc(d.head)}</b><p>${esc(d.body)}</p></div></li>`; }).join('');
      $('#t-count').textContent = `${r.events.length} events${r.is_simulated ? ' · simulated' : ''}`;
      $('#t-marks').innerHTML = r.events.map(e => `<span class="${e.type === 'soc_reading' ? '' : 'bad'}" style="left:${t.end ? e.t_offset_s / t.end * 100 : 0}%"></span>`).join('');
      paint();
    }
    function moveCar(km) {
      if (!t.car || !t.pts) return;
      const to = C.pointAtKm(t.pts, t.cum, plan(t.id).route.distance_km, km); if (!to) return;
      const from = t.car.getLatLng(), t0 = performance.now(), dur = REDUCED ? 0 : 700;
      const step = now => { const f = dur ? Math.min(1, (now - t0) / dur) : 1, e = 1 - Math.pow(1 - f, 3); t.car.setLatLng([from.lat + (to[0] - from.lat) * e, from.lng + (to[1] - from.lng) * e]); if (f < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    }
    function handle(e) {
      const li = $(`#t-log li[data-i="${t.index}"]`); if (li) { li.classList.remove('pending'); const log = $('#t-log'); log.scrollTo({ top: li.offsetTop - log.offsetTop - log.clientHeight / 2, behavior: REDUCED ? 'auto' : 'smooth' }); }
      if (e.type === 'soc_reading') { t.km = e.km; t.soc = e.soc_pct; moveCar(e.km); }
      if (e.type === 'charger_report') {
        const c = findCharger(e.charger_id); t.broken.add(e.charger_id);
        t.msg = { k: 'reported', a: { status: e.status, name: c ? c.name : e.charger_id } };
        const m = maps['t-map']; if (m && c) L.marker([c.lat, c.lon], { icon: L.divIcon({ className: '', html: '<div class="broken-icon">✕</div>', iconSize: [24, 24], iconAnchor: [12, 12] }), zIndexOffset: 900 }).bindTooltip(`<span class="tt-k">Reported ${esc(e.status)}</span><br><b>${esc(c.name)}</b>`).addTo(m.layer);
      }
      if (e.type === 'forecast') {
        if (e.action === 'DIVERT' || e.action === 'SLOW_DOWN') {
          t.alert = e;
          if ($('#t-voice').checked) {
            const text = e.action === 'DIVERT' && e.divert_to ? tr('divertMessage', { name: e.divert_to.name }) : t.lang === 'hi' ? SLOW_HI : e.message;
            API.speak(text, t.lang === 'hi' ? 'hi-IN' : 'en-IN').catch(() => toast('The voice alert could not play. Read the message on screen.'));
          }
        }
        t.msg = { raw: e.message };
        const m = maps['t-map'];
        if (m && e.divert_to && t.car) {
          const a = t.car.getLatLng();
          t.divertLine = L.polyline([[a.lat, a.lng], [e.divert_to.lat, e.divert_to.lon]], { color: tok('--go'), weight: 4, dashArray: '8 8' }).addTo(m.layer);
          L.circleMarker([e.divert_to.lat, e.divert_to.lon], { radius: 13, color: tok('--go'), weight: 4, fillColor: tok('--bg'), fillOpacity: 1 }).bindTooltip(`<span class="tt-k">Divert target</span><br><b>${esc(e.divert_to.name)}</b><br>${pct(e.divert_to.p_works)} chance it works`).addTo(m.layer).openTooltip();
          if (!m.userMoved) m.map.flyToBounds(L.latLngBounds([[a.lat, a.lng], [e.divert_to.lat, e.divert_to.lon]]).pad(1.2), { duration: REDUCED ? 0 : 1.2, maxZoom: 12 });
        }
      }
    }
    function frame(now) {
      if (!t.running) return;
      t.elapsed += (now - t.last) / 1000 * Number($('#t-speed').value); t.last = now;
      const r = state.replays[t.id], due = C.dueEvents(r.events, t.index, t.elapsed);
      due.events.forEach(e => { handle(e); t.index++; });
      if (t.index >= r.events.length) { t.elapsed = t.end; t.running = false; $('#t-play').textContent = '↺ Replay'; t.done = true; }
      paint();
      if (t.running) t.raf = requestAnimationFrame(frame);
    }
    function play() { if (t.done) { t.done = false; reset(); } t.running = true; t.last = performance.now(); $('#t-play').textContent = '❚❚ Pause'; t.raf = requestAnimationFrame(frame); }
    function pause() { if (!t.running) return; t.running = false; cancelAnimationFrame(t.raf); $('#t-play').textContent = '▶ Play'; }
    function paint() {
      const p = plan(t.id), dist = p.route.distance_km;
      $('#t-clock').textContent = 'T+' + C.fmtClock(t.elapsed);
      $('#t-scrub').style.width = (t.end ? Math.min(100, t.elapsed / t.end * 100) : 0) + '%';
      const bingo = C.bingoAt(p.bingo_line, t.km), margin = isNum(bingo) && isNum(t.soc) ? Math.round(t.soc - bingo) : null;
      const stop = (p.stops || []).find(s => s.km_along_route >= t.km) || null;
      const prog = Math.min(100, t.km / dist * 100);
      let msg = t.msg ? (t.msg.raw ? (t.lang === 'hi' && t.alert?.action === 'SLOW_DOWN' ? SLOW_HI : t.lang === 'hi' && t.alert?.divert_to ? tr('divertMessage', { name: t.alert.divert_to.name }) : t.msg.raw) : tr(t.msg.k, t.msg.a)) : stop ? tr('planMsg', { name: stop.name, km: round1(stop.km_along_route) }) : tr('noPlan');
      if (!t.running && t.elapsed === 0) msg = tr('ready') + ' ' + msg;
      if (t.done && !t.alert) msg = tr('done');
      $('#phone-screen').innerHTML = `
        <div class="ps-bar"><span>${C.fmtClock(t.elapsed)}</span><span>${p.is_simulated ? 'SIM' : ''} ▮▮▮</span></div>
        <div class="ps-head"><b>${tr('brand')}</b><button class="ps-lang" type="button" data-lang>${t.lang === 'en' ? 'हिन्दी' : 'English'}</button></div>
        <div class="ps-body">
          <div class="ps-batt"><div><small>${tr('battery')}</small><strong style="color:${margin !== null && margin < 0 ? '#B42318' : '#8A5A00'}">${isNum(t.soc) ? t.soc : '—'}<span style="font-size:.45em">%</span></strong></div>
            <div class="ps-cell" aria-hidden="true"><span style="height:${Math.max(2, t.soc || 0)}%;background:${margin !== null && margin < 0 ? '#B42318' : '#11743F'}"></span>${isNum(bingo) ? `<i style="bottom:${Math.min(100, Math.max(0, bingo))}%"></i>` : ''}</div></div>
          <div class="ps-ms"><div class="ps-m"><small>${tr('toGo')}</small><strong>${round1(Math.max(0, dist - t.km))}</strong></div><div class="ps-m"><small>${margin !== null && margin < 0 ? tr('below') : tr('above')}</small><strong style="color:${margin !== null && margin < 0 ? '#B42318' : '#11743F'}">${margin === null ? '—' : Math.abs(margin) + '%'}</strong></div></div>
          <div class="ps-route"><div class="rh"><span>${esc(state.scenario.booking.origin.name)}</span><span>${esc(state.scenario.booking.destination.name)}</span></div>
            <div class="ps-track"><div class="ps-prog" style="width:${prog}%"></div>${(p.stops || []).map(s => `<span class="ps-dot stop" style="left:${s.km_along_route / dist * 100}%" title="${esc(s.name)}"></span>`).join('')}${isNum(p.point_of_no_return_km) ? `<span class="ps-dot ponr" style="left:${p.point_of_no_return_km / dist * 100}%" title="${tr('ponr')}"></span>` : ''}<span class="ps-dot me" style="left:${prog}%"></span></div></div>
          <div class="ps-msg"><small>${tr('next')}</small>${esc(msg)}</div>
        </div>
        <div class="ps-safe">${tr('safety')}</div>
        ${t.alert ? `<div class="ps-alert ${t.alert.action === 'SLOW_DOWN' ? 'slow' : ''}" role="alert"><span class="badge">${t.alert.action === 'DIVERT' ? tr('divert') : tr('slow')}</span><h3>${t.alert.action === 'DIVERT' ? tr('divert') : tr('slow')}</h3><p>${esc(t.alert.action === 'DIVERT' && t.alert.divert_to ? tr('divertMessage', { name: t.alert.divert_to.name }) : t.lang === 'hi' ? SLOW_HI : t.alert.message)}</p>${t.alert.divert_to ? `<small>${pct(t.alert.divert_to.p_works)} chance it works</small>` : ''}<button type="button" data-dismiss>${tr('understood')}</button></div>` : ''}`;
      const sc = $('#phone-screen');
      sc.querySelector('[data-lang]').onclick = () => { t.lang = t.lang === 'en' ? 'hi' : 'en'; paint(); };
      const d = sc.querySelector('[data-dismiss]'); if (d) d.onclick = () => { t.alert = null; paint(); };
    }
    return { render, pause, play: () => { if (state.active !== 'trip') go('trip'); setTimeout(play, 300); } };
  })();

  /* ================================================================== 04 TRUST */
  function renderTrust() {
    const list = state.chargers, st = C.chargerStats(list);
    const unknown = st.byTrust.unknown || 0;
    $('#trust-hero').innerHTML = `<div><p class="eyebrow">Charger directory · OSM + Open Charge Map</p>
      <div class="big"><span>${unknown}</span> / ${st.total} <small>chargers</small></div>
      <h2 class="h2" style="margin-top:14px">have no recent report saying they work.</h2>
      <p class="lede small">A pin on a map is not a working charger. Verity starts every charger at <b>unknown</b>, earns trust only from weighted reports, and plans a reachable backup for every stop. When a plan depends on unverified chargers, it refuses instead of guessing.</p></div>
      <div class="tiles">${[['In directory', st.total], ['From OpenStreetMap', st.bySource.osm || 0], ['From Open Charge Map', st.bySource.ocm || 0], ['With any reports', st.withReports]].map(([k, v]) => `<div class="tile"><small>${k}</small><b>${v}</b></div>`).join('')}
      <div class="tile" style="grid-column:1/-1"><small>Trust levels</small><div class="legend" style="margin-top:6px">${['high', 'medium', 'low', 'unknown'].map(t => `<span><i style="background:${trustColor(t)}"></i>${t}: <b style="color:var(--text)">&nbsp;${st.byTrust[t] || 0}</b></span>`).join('')}</div></div></div>`;

    let filter = 'all', sortKey = 'p_works', sortDir = -1, query = '';
    $('#trust-filters').innerHTML = ['all', 'high', 'medium', 'low', 'unknown'].map(f => `<button class="chip" data-f="${f}" aria-pressed="${f === filter}">${f}${f === 'all' ? '' : ' · ' + (st.byTrust[f] || 0)}</button>`).join('');
    const m = getMap('c-map');
    const markers = new Map();
    function drawMap() {
      if (!m) return;
      m.layer.clearLayers(); markers.clear();
      const lead = plan('V12') || Object.values(state.plans)[0], pts = routePoints(lead);
      if (pts.length > 1) L.polyline(pts, { color: tok('--amber'), weight: 3, opacity: .45, interactive: false }).addTo(m.layer);
      const shown = list.filter(c => filter === 'all' || c.trust_level === filter);
      shown.forEach(c => { const mk = L.circleMarker([c.lat, c.lon], { radius: c.trust_level === 'unknown' ? 4.5 : 8, weight: c.trust_level === 'unknown' ? 1 : 3, color: trustColor(c.trust_level), fillColor: trustColor(c.trust_level), fillOpacity: c.trust_level === 'unknown' ? .45 : .9 }).bindTooltip(chargerTip(c)).addTo(m.layer); markers.set(c.charger_id, mk); });
      const all = shown.map(c => [c.lat, c.lon]).concat(pts);
      m.bounds = all.length ? L.latLngBounds(all) : null;
      requestAnimationFrame(() => fitMap(m));
    }
    $('#c-legend').innerHTML = ['high', 'medium', 'low', 'unknown'].map(t => `<span><i style="background:${trustColor(t)}"></i>${t} trust</span>`).join('') + '<span><i class="sq" style="background:var(--amber);opacity:.5"></i>Demo route</span>';
    function drawTable() {
      const rows = list.filter(c => (filter === 'all' || c.trust_level === filter) && (!query || `${c.name} ${c.operator} ${c.charger_id}`.toLowerCase().includes(query)));
      const val = (c, k) => c[k] === null || c[k] === undefined || c[k] === '' ? null : c[k];
      rows.sort((a, b) => { const x = val(a, sortKey), y = val(b, sortKey); if (x === null && y === null) return 0; if (x === null) return 1; if (y === null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
      $('#c-table tbody').innerHTML = rows.map(c => `<tr data-id="${esc(c.charger_id)}" tabindex="0"><td><b>${esc(c.name)}</b><br><span class="op">${esc(c.charger_id)}</span></td><td class="op">${esc(c.operator || '—')}</td><td>${esc(c.source || '—')}</td><td class="num">${c.p_works === null || c.p_works === undefined ? '—' : pct(c.p_works)}</td><td class="num">${c.n_reports ?? '—'}</td><td class="num">${c.days_since_last === null || c.days_since_last === undefined ? '—' : c.days_since_last + ' d'}</td><td><span class="tag ${esc(c.trust_level || 'unknown')}">${esc(c.trust_level || 'unknown')}</span></td></tr>`).join('') || '<tr><td colspan="7" class="state">No chargers match.</td></tr>';
      $('#c-count').textContent = `${rows.length} of ${list.length} chargers shown${state.scenario.is_simulated ? ' · trust values are from the simulated demo scenario' : ''}. “Chance it works” is the engine's prior or posterior, not a measurement.`;
      $$('#c-table th').forEach(th => { if (th.dataset.sort === sortKey) th.setAttribute('aria-sort', sortDir > 0 ? 'ascending' : 'descending'); else th.removeAttribute('aria-sort'); });
    }
    $$('#trust-filters .chip').forEach(b => b.onclick = () => { filter = b.dataset.f; $$('#trust-filters .chip').forEach(x => x.setAttribute('aria-pressed', String(x === b))); drawMap(); drawTable(); });
    $$('#c-table th').forEach(th => th.onclick = () => { const k = th.dataset.sort; sortDir = k === sortKey ? -sortDir : (k === 'name' || k === 'operator' || k === 'source' ? 1 : -1); sortKey = k; drawTable(); });
    $('#c-search').oninput = e => { query = e.target.value.trim().toLowerCase(); drawTable(); };
    const focusRow = tr => { const mk = markers.get(tr.dataset.id); if (mk && m) { m.userMoved = true; m.map.flyTo(mk.getLatLng(), 13, { duration: REDUCED ? 0 : .8 }); mk.openTooltip(); $('#c-map').scrollIntoView({ block: 'nearest', behavior: REDUCED ? 'auto' : 'smooth' }); } };
    $('#c-table tbody').onclick = e => { const tr = e.target.closest('tr[data-id]'); if (tr) focusRow(tr); };
    $('#c-table tbody').onkeydown = e => { if (e.key === 'Enter') { const tr = e.target.closest('tr[data-id]'); if (tr) focusRow(tr); } };
    drawMap(); drawTable();

    const e5 = state.results?.e5?.scenarios;
    const ring = e5 ? Object.entries(e5).filter(([, s]) => s.ring_detection).map(([name, s]) => `<tr><td>${esc(name)}</td><td>${s.ring_detection.precision === null ? '—' : s.ring_detection.precision.toFixed(2)}</td><td class="${s.ring_detection.recall >= .99 ? 'pass' : 'fail'}">${s.ring_detection.recall.toFixed(2)}</td></tr>`).join('') : '';
    $('#trust-pipe').innerHTML = `<ol>
      <li><span class="tp-n">01</span><div><h4>Merge the directory</h4><p>OpenStreetMap and Open Charge Map entries within 50 m are merged into one charger.</p></div></li>
      <li><span class="tp-n">02</span><div><h4>Weigh every report by its source</h4><p>Drivers, operator feeds and audits each get a learned reliability (Dawid–Skene), so one lying feed cannot flip a charger.</p></div></li>
      <li><span class="tp-n">03</span><div><h4>Catch coordinated rings</h4><p>The Neo4j graph looks for groups of sources that report together, and discounts them.</p></div></li>
      <li><span class="tp-n">04</span><div><h4>Feed the forecast, not a badge</h4><p>Each charger's chance of working enters the Monte Carlo, which plans a backup within reach for every stop.</p></div></li></ol>
      ${ring ? `<table class="ev-table" style="margin-top:16px"><thead><tr><th>Ring detection (E5, simulated)</th><th>Precision</th><th>Recall</th></tr></thead><tbody>${ring}</tbody></table><p class="note">The evasive ring that spreads its reports over 10 days is <b>not</b> caught. We show that result instead of hiding it.</p>` : ''}`;
  }

  /* ================================================================== 05 EVIDENCE */
  function bars({ groups, series, max = 1, fmt = pctInt, height = 280, yTicks = 5, yfmt, width = 640 }) {
    const W = width, H = height, L = 44, R = 10, T = 22, B = 40;
    const y = v => T + (1 - v / max) * (H - T - B), gw = (W - L - R) / groups.length, bw = Math.min(46, gw * .7 / series.length);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img">`;
    for (let i = 0; i <= yTicks; i++) { const v = max * i / yTicks; s += `<path d="M${L} ${y(v)}H${W - R}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="var(--faint)">${(yfmt || fmt)(v)}</text>`; }
    groups.forEach((g, gi) => {
      const x0 = L + gi * gw + (gw - bw * series.length - 6 * (series.length - 1)) / 2;
      g.values.forEach((v, si) => {
        if (!isNum(v)) return;
        const x = x0 + si * (bw + 6), c = series[si].color;
        s += `<rect x="${x}" y="${y(v)}" width="${bw}" height="${Math.max(0, y(0) - y(v))}" rx="3" fill="${c}" class="${REDUCED ? '' : 'grow'}" style="animation-delay:${(gi * series.length + si) * 60}ms"><title>${esc(series[si].name)} · ${esc(g.label)}: ${fmt(v)}</title></rect>`;
        const ci = g.ci?.[si];
        if (ci) s += `<path d="M${x + bw / 2} ${y(ci[1])}V${y(ci[0])}M${x + bw / 2 - 5} ${y(ci[1])}h10M${x + bw / 2 - 5} ${y(ci[0])}h10" stroke="var(--text)" stroke-width="1.4" opacity=".7"/>`;
        s += `<text x="${x + bw / 2}" y="${(ci ? y(ci[1]) : y(v)) - 6}" text-anchor="middle" font-size="11" font-weight="600" fill="var(--text)">${fmt(v)}</text>`;
      });
      s += `<text x="${L + gi * gw + gw / 2}" y="${H - 14}" text-anchor="middle" font-size="11" fill="var(--muted)">${esc(g.label)}</text>`;
    });
    return s + '</svg>' + `<div class="legend">${series.map(x => `<span><i class="sq" style="background:${x.color}"></i>${esc(x.name)}</span>`).join('')}</div>`;
  }
  function card({ id, title, claim, body, foot, wide }) { return `<article class="ev${wide ? ' wide' : ''}"><div class="ev-top"><span class="ev-id">${id}</span><span class="sim">Simulated</span></div><h3>${title}</h3><p class="claim">${claim}</p>${body}<div class="foot">${foot}</div></article>`; }

  function renderEvidence() {
    const R = state.results, el = $('#evidence');
    if (!R) { el.innerHTML = '<p class="state">Saved experiment results are not bundled on this page. Run <code>python scripts/export_web_snapshot.py</code>.</p>'; return; }
    const out = [];
    const GO = tok('--go'), NO = tok('--nogo'), AM = tok('--amber'), UN = tok('--unknown'), MU = tok('--faint');

    // E4
    if (R.e4) {
      const sc = Object.entries(R.e4.scenarios), last = sc[sc.length - 1][1];
      out.push(card({ id: 'E4', wide: true, title: 'A fixed plan strands people. A dispatch plan with backups does not.',
        claim: `With ${sc[sc.length - 1][0]}, the shortest-path plan strands <b>${pctInt(last.stranding_rate_fixed)}</b> of trips; Verity's dispatch policy strands <b>${pctInt(last.stranding_rate_dispatch)}</b>, at a cost of ${round1(last.mean_minutes_dispatch_not_stranded - last.mean_minutes_fixed_not_stranded)} minutes per completed trip.`,
        body: bars({ groups: sc.map(([k, v]) => ({ label: k, values: [v.stranding_rate_fixed, v.stranding_rate_dispatch], ci: [v.ci95_fixed, v.ci95_dispatch] })), series: [{ name: 'Fixed shortest-path plan', color: NO }, { name: 'Verity dispatch (backups + Bingo Line)', color: GO }], max: .5, width: 1280, height: 320 }),
        foot: `${R.e4.trips_per_scenario} trips per scenario, ${R.e4.total_simulated_trips} total · whiskers are Wilson 95% intervals · ${esc(R.e4.notes?.[0] || '')}<br>results/e4_dispatch_vs_fixed_simulated.json` }));
    }
    // E2
    if (R.e2) {
      const sh = R.e2.methods.shared, ind = R.e2.methods.independent;
      const W = 640, H = 300, L = 44, Rr = 14, T = 14, B = 40, x = v => L + v * (W - L - Rr), y = v => T + (1 - v) * (H - T - B);
      let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Reliability diagram (simulated)">`;
      for (let i = 0; i <= 5; i++) { const v = i / 5; s += `<path d="M${L} ${y(v)}H${W - Rr}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="var(--faint)">${Math.round(v * 100)}%</text><text x="${x(v)}" y="${H - 22}" text-anchor="middle" font-size="10" fill="var(--faint)">${Math.round(v * 100)}%</text>`; }
      s += `<path d="M${x(0)} ${y(0)}L${x(1)} ${y(1)}" stroke="var(--muted)" stroke-dasharray="4 4"/><text x="${x(.62)}" y="${y(.66)}" font-size="10" fill="var(--faint)" transform="rotate(-24 ${x(.62)} ${y(.66)})">perfectly honest</text>`;
      const series = [[ind, UN, 'Independent per-segment noise'], [sh, AM, 'Shared trip factors (Verity)']];
      series.forEach(([m, c]) => { const pts = m.bins.filter(b => b.n > 0); s += `<polyline points="${pts.map(b => `${x(b.mean_pred)},${y(b.observed)}`).join(' ')}" fill="none" stroke="${c}" stroke-width="2.2" pathLength="1" class="${REDUCED ? '' : 'draw'}" style="--len:1"/>`; s += pts.map(b => `<circle cx="${x(b.mean_pred)}" cy="${y(b.observed)}" r="${3 + Math.sqrt(b.n) / 2.2}" fill="${c}" fill-opacity=".85" stroke="var(--surface)" stroke-width="1.5"><title>Predicted ${pctInt(b.mean_pred)}, observed ${pctInt(b.observed)} (n=${b.n})</title></circle>`).join(''); });
      s += `<text x="${(W + L) / 2}" y="${H - 4}" text-anchor="middle" font-size="10" fill="var(--faint)">PREDICTED CHANCE OF ARRIVING</text></svg>`;
      s += `<div class="legend">${series.map(([, c, n]) => `<span><i style="background:${c}"></i>${n}</span>`).join('')}<span>dot size ∝ trips in bin</span></div>`;
      s += `<table class="ev-table"><thead><tr><th>Method</th><th>ECE</th><th>Brier</th><th>90% band coverage</th><th>Band width</th></tr></thead><tbody>
        <tr><td>Independent noise</td><td>${ind.ece}</td><td>${ind.brier}</td><td class="fail">${pctInt(ind.coverage_90_arrival_band)}</td><td>${round1(ind.mean_90_band_width_pp)} pp</td></tr>
        <tr><td>Shared factors</td><td>${sh.ece}</td><td>${sh.brier}</td><td class="pass">${pctInt(sh.coverage_90_arrival_band)}</td><td>${round1(sh.mean_90_band_width_pp)} pp</td></tr></tbody></table>`;
      out.push(card({ id: 'E2', title: 'When Verity says 90%, it means about 90%.', claim: `Sampling uncertainty independently per road segment makes long trips look falsely certain: its 90% band caught the real arrival battery only <b>${pctInt(ind.coverage_90_arrival_band)}</b> of the time. Shared trip-level factors bring that to <b>${pctInt(sh.coverage_90_arrival_band)}</b>.`, body: s, foot: `${R.e2.trips} held-out simulated trips · ${R.e2.n_runs} Monte Carlo runs each · ECE = expected calibration error (lower is better)<br>results/e2_calibration_simulated.json` }));
    }
    // E3
    if (R.e3) {
      const sc = Object.entries(R.e3.scenarios);
      out.push(card({ id: 'E3', title: 'Knowing which chargers to distrust changes the plan.', claim: `Trust-aware planning changed the first charging stop in <b>${pctInt(sc[sc.length - 1][1].first_stop_changes)}</b> of trips and cut stranding from <b>${pctInt(sc[sc.length - 1][1].stranding_rate_blind)}</b> to <b>${pctInt(sc[sc.length - 1][1].stranding_rate_trust_aware)}</b> (${esc(sc[sc.length - 1][0])}).`,
        body: bars({ groups: sc.map(([k, v]) => ({ label: k, values: [v.stranding_rate_blind, v.stranding_rate_trust_aware] })), series: [{ name: 'Reliability-blind plan', color: NO }, { name: 'Trust-aware plan', color: GO }], max: .25, height: 250 }),
        foot: `${R.e3.trips_per_scenario} trips per scenario · ${esc(R.e3.notes?.[1] || '')}<br>results/e3_reliability_planning_simulated.json` }));
    }
    // E1
    if (R.e1) {
      const NAMES = { verity_pooled: ['Verity · fleet-pooled', AM], verity_class_prior: ['Verity · class prior', GO], nominal_spec: ['Spec-sheet range', UN], flat_minus_25: ['Flat −25% rule', MU], regression: ['Plain regression (no physics)', NO] };
      const meths = Object.entries(R.e1.methods), ks = meths[0][1].map(r => r.k);
      const maxV = Math.max(...meths.flatMap(([, rows]) => rows.map(r => r.mae_pp).filter(isNum)));
      const top = Math.ceil(maxV / 5) * 5;
      const W = 640, H = 290, L = 40, Rr = 14, T = 14, B = 40, x = i => L + i / (ks.length - 1) * (W - L - Rr), y = v => T + (1 - v / top) * (H - T - B);
      let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Arrival battery error vs calibration trips (simulated)">`;
      for (let i = 0; i <= 5; i++) { const v = top * i / 5; s += `<path d="M${L} ${y(v)}H${W - Rr}" stroke="var(--line)"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="var(--faint)">${Math.round(v)}</text>`; }
      ks.forEach((k, i) => { s += `<text x="${x(i)}" y="${H - 22}" text-anchor="middle" font-size="10" fill="var(--faint)">${k}</text>`; });
      meths.forEach(([name, rows]) => { const [, c] = NAMES[name] || [name, MU]; s += `<polyline points="${rows.map((r, i) => `${x(i)},${y(r.mae_pp)}`).join(' ')}" fill="none" stroke="${c}" stroke-width="${name.startsWith('verity') ? 3 : 1.8}" pathLength="1" class="${REDUCED ? '' : 'draw'}" style="--len:1"/>` + rows.map((r, i) => `<circle cx="${x(i)}" cy="${y(r.mae_pp)}" r="3.5" fill="${c}"><title>${esc((NAMES[name] || [name])[0])}, k=${r.k}: MAE ${r.mae_pp} pp</title></circle>`).join(''); });
      s += `<text x="${(W + L) / 2}" y="${H - 4}" text-anchor="middle" font-size="10" fill="var(--faint)">CALIBRATION TRIPS PER VEHICLE (k)</text><text x="${L + 4}" y="${T + 10}" font-size="10" fill="var(--faint)">ERROR, BATTERY POINTS</text></svg>`;
      s += `<div class="legend">${meths.map(([n]) => `<span><i class="sq" style="background:${(NAMES[n] || [n, MU])[1]}"></i>${esc((NAMES[n] || [n])[0])}</span>`).join('')}</div>`;
      const vp = R.e1.methods.verity_pooled, ns = R.e1.methods.nominal_spec;
      const k1 = vp?.[0], s1 = ns?.[0];
      out.push(card({ id: 'E1', title: 'One logged trip is enough to beat the spec sheet.', claim: k1 && s1 ? `After a single calibration trip, Verity's arrival-battery error is <b>${k1.mae_pp} pp</b> vs <b>${s1.mae_pp} pp</b> for the spec-sheet range. Its 90% band caught ${k1.covered90} of ${k1.n90} held-out trips (${pctInt(k1.coverage90)})${k1.coverage90 < 0.9 ? ': under the nominal 90%, and we report that' : ''}.` : 'Learning curve of arrival-battery error.', body: s, foot: `${R.e1.test_trips} held-out test trips across ${R.e1.vehicles} vehicles · ${R.e1.resamples} training-subset draws per k · ${esc(R.e1.notes?.[0] || '')}<br>results/e1_learning_curve_simulated.json` }));
    }
    // E5
    if (R.e5) {
      const sc = Object.entries(R.e5.scenarios).filter(([, s]) => s.mae_on_attacked_chargers);
      const MN = [['avg_rating', 'Average star rating', NO], ['ds', 'Dawid–Skene', UN], ['ds_usage', '+ usage signals', AM], ['full', 'Verity full (+ ring detection)', GO]];
      out.push(card({ id: 'E5', title: 'Fake reports do not fool the trust engine — except one attack we name.', claim: 'Error in the estimated chance a charger works, measured only on chargers targeted by coordinated fake reporters. A plain average collapses; Verity\'s full trust engine holds. The evasive ring that spreads its reports over 10 days is not detected by ring detection.',
        body: bars({ groups: sc.map(([k, v]) => ({ label: k.replace(' (10 days)', '').replace('ring of 12 + lying feed', '12 + lying feed').replace('evasive ring of 12', 'evasive 12'), values: MN.map(([m]) => v.mae_on_attacked_chargers[m]) })), series: MN.map(([, n, c]) => ({ name: n, color: c })), max: .6, fmt: v => v.toFixed(2), height: 270 }),
        foot: `${R.e5.worlds_per_scenario} simulated worlds per scenario · ${R.e5.chargers_per_world} chargers each · lower is better · mean absolute error of P(works)<br>results/e5_trust_recovery_simulated.json` }));
    }
    // Run status
    const ra = R.run_all;
    if (ra) {
      const tests = ra.steps.find(s => s.step === 'tests'), m = tests?.last_line?.match(/(\d+) passed(?:, (\d+) skipped)?/);
      out.push(`<article class="ev wide"><div class="ev-top"><span class="ev-id">RUN</span><span class="label">${ra.all_ok ? 'All steps OK' : 'Some steps failed'}</span></div><h3>Everything above regenerates from a clean state with one command.</h3>
        <div class="tests">${m ? `<div><div class="big">${m[1]}</div><span class="label">tests passed${m[2] ? ` · ${m[2]} skipped` : ''}</span></div>` : ''}
        <div class="steps">${ra.steps.map(s => { const skipped = /^SKIPPED/.test(s.last_line || ''); return `<span class="${skipped ? 'skip' : s.ok ? 'ok' : ''}" title="${esc(s.last_line || '')}">${esc(s.step)} · ${skipped ? 'skipped' : s.ok ? 'ok' : 'failed'} · ${s.seconds}s</span>`; }).join('')}</div></div>
        <div class="foot honest">Neo4j smoke test was skipped on the recording machine because credentials were not set; the in-memory graph gives the same answers. No real Indian road validation exists yet — every experiment above is simulated.<br>uv run python scripts/run_all.py · results/run_all_summary.json</div></article>`);
    }
    el.innerHTML = out.join('');
  }

  /* ================================================================== 06 ENGINE */
  function renderEngine() {
    const stages = [
      ['01', 'Log', 'logger/', 'GPS points and battery taps from real drives, in verity.trip.v1.'],
      ['02', 'Calibrate', 'calibrate.py', 'Bayesian posterior per vehicle, pooled across the fleet by model.'],
      ['03', 'Forecast', 'forecast.py', 'Physics energy model inside a seeded, vectorised Monte Carlo.'],
      ['04', 'Trust', 'trust/', 'Dawid–Skene source reliability, ring detection, Neo4j graph.'],
      ['05', 'Dispatch', 'dispatch.py', 'Candidate chains scored with backups: GO / NO-GO, Bingo Line, levers.'],
      ['06', 'Drive', 'voice.py', 'Live updates and Hindi/English voice alerts (Sarvam), keypad fallback.'],
    ];
    $('#pipeline').innerHTML = stages.map(([n, h, f, p], i) => `<div class="pipe" style="--i:${i}"><span class="flow-pulse" aria-hidden="true"></span><small>${n}</small><h3>${h}</h3><code>${f}</code><p>${p}</p></div>`).join('');
    const e2 = state.results?.e2?.methods;
    if (e2) {
      const row = (name, m, c) => `<div style="margin-bottom:14px"><div class="band-cap"><b>${name}</b> — 90% band ${round1(m.mean_90_band_width_pp)} pp wide, caught the truth ${pctInt(m.coverage_90_arrival_band)} of the time</div><div class="band-bar"><div class="band-range" style="--c:${c};left:0;width:${m.coverage_90_arrival_band * 100}%"></div><div class="band-mid" style="left:90%" title="90% target"></div></div></div>`;
      $('#engine-shared').innerHTML = `<p class="small muted" style="margin-top:0">A long trip has one driver, one weather, one payload. Sampling those once per simulated trip — not once per road segment — is what keeps the confidence honest. The white tick marks the 90% target.</p>${row('Independent per segment', e2.independent, 'var(--unknown)')}${row('Shared trip factors (Verity)', e2.shared, 'var(--go)')}<p class="note">E2, ${state.results.e2.trips} simulated trips · results/e2_calibration_simulated.json</p>`;
    } else $('#engine-shared').innerHTML = '<p class="state">E2 results are not loaded on this page.</p>';
    const c = state.results?.e4?.compute;
    $('#engine-run').textContent = c ? `Measured on the recording machine: a full dispatch with ${c.runs_per_forecast} runs per forecast on a ${c.real_route_km} km route with ${c.chargers_near_route} nearby chargers took ${c.full_dispatch_ms_mean} ms on average (max ${c.full_dispatch_ms_max} ms).` : '';
  }

  /* ------------------------------------------------------------------ command palette */
  function commands() {
    const cmds = TABS.map((t, i) => ({ label: `Go to ${TAB_NAMES[t]}`, hint: String(i + 1), run: () => go(t) }));
    state.scenario.vehicles.forEach(v => cmds.push({ label: `Open ${v.vehicle_id} release`, hint: C.decisionLabel(plan(v.vehicle_id).decision), run: () => go('dispatch', v.vehicle_id) }));
    cmds.push({ label: 'Play live trip', hint: 'replay', run: () => trip.play() });
    cmds.push({ label: 'Toggle light / dark theme', hint: 'theme', run: toggleTheme });
    cmds.push({ label: 'Open Verity home page', hint: 'home.html', run: () => { location.href = 'home.html'; } });
    cmds.push({ label: 'Open classic console', hint: 'console.html', run: () => { location.href = 'console.html' + location.search; } });
    cmds.push({ label: 'Open driver view', hint: 'driver.html', run: () => { location.href = 'driver.html' + location.search; } });
    return cmds;
  }
  function palette() {
    const box = $('#palette'), input = $('#palette-input'), list = $('#palette-list');
    let items = [], sel = 0, lastFocus = null;
    const draw = () => {
      const q = input.value.trim().toLowerCase();
      items = commands().filter(c => !q || c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q));
      sel = Math.min(sel, Math.max(0, items.length - 1));
      list.innerHTML = items.map((c, i) => `<li role="option" aria-selected="${i === sel}" data-i="${i}"><span>${esc(c.label)}</span><small>${esc(c.hint)}</small></li>`).join('') || '<li>No matches</li>';
    };
    const open = () => { if (!state.scenario) return; lastFocus = document.activeElement; box.hidden = false; input.value = ''; sel = 0; draw(); input.focus(); };
    const close = () => { box.hidden = true; lastFocus?.focus?.(); };
    const run = i => { const c = items[i]; close(); c?.run(); };
    $('#cmdk').onclick = open;
    box.onclick = e => { if (e.target === box) close(); const li = e.target.closest('li[data-i]'); if (li) run(+li.dataset.i); };
    input.oninput = () => { sel = 0; draw(); };
    input.onkeydown = e => {
      if (e.key === 'ArrowDown') { sel = (sel + 1) % Math.max(1, items.length); draw(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { sel = (sel - 1 + items.length) % Math.max(1, items.length); draw(); e.preventDefault(); }
      else if (e.key === 'Enter') run(sel);
      else if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); box.hidden ? open() : close(); return; }
      if (!box.hidden || e.metaKey || e.ctrlKey || e.altKey) return;
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName)) return;
      const n = Number(e.key); if (n >= 1 && n <= TABS.length) go(TABS[n - 1]);
    });
  }

  /* ------------------------------------------------------------------ boot */
  async function load() {
    const scenario = await API.getScenario();
    const b = scenario.booking;
    const entries = await Promise.all(scenario.vehicles.map(async v => [v.vehicle_id, await API.getDispatch({ vehicle_id: v.vehicle_id, origin: { lat: b.origin.lat, lon: b.origin.lon }, destination: { lat: b.destination.lat, lon: b.destination.lon }, departure_time: b.departure_time, start_soc_pct: v.soc_pct, passengers: b.passengers, ac_on: b.ac_on, target_confidence: b.target_confidence })]));
    state.scenario = scenario; state.plans = Object.fromEntries(entries);
    const all = entries.flatMap(([, p]) => routePoints(p)).concat([[b.origin.lat, b.origin.lon], [b.destination.lat, b.destination.lon]]);
    const lat = all.map(p => p[0]), lon = all.map(p => p[1]);
    try { state.chargers = (await API.getChargers({ min_lat: Math.min(...lat) - .25, min_lon: Math.min(...lon) - .25, max_lat: Math.max(...lat) + .25, max_lon: Math.max(...lon) + .25 })).chargers || []; }
    catch (e) { state.chargers = []; toast('Charger directory could not load: ' + e.message); }
    await Promise.all(entries.map(async ([id, p]) => { try { state.replays[id] = await API.getReplay(p.plan_id); } catch { /* no replay for this plan */ } }));
    state.results = window.VERITY_SNAPSHOT?.results || null;
    if (!state.plans[state.selected]) state.selected = scenario.vehicles[0].vehicle_id;
  }

  async function boot() {
    applyTheme(store.get('verity-theme') || document.documentElement.dataset.theme || 'dark');
    $('#theme').onclick = toggleTheme;
    $$('.tab').forEach(b => b.onclick = () => go(b.dataset.tab));
    $('.tabs').addEventListener('keydown', e => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const i = TABS.indexOf(state.active), n = TABS[(i + (e.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length];
      go(n); requestAnimationFrame(() => $('#tab-' + n).focus());
    });
    $('#d-fit').onclick = () => fitMap(maps['d-map']);
    $('#d-tiles').onclick = e => {
      const m = maps['d-map']; if (!m) return;
      m.routeOnly = !m.routeOnly; e.currentTarget.setAttribute('aria-pressed', String(m.routeOnly));
      if (m.routeOnly) { m.map.removeLayer(m.tiles); $('#d-mapnote').textContent = 'Route-only view: street tiles hidden. Route and charger positions still come from the plan and directory.'; }
      else { m.tiles.addTo(m.map); $('#d-mapnote').textContent = ''; }
    };
    window.addEventListener('resize', moveInk);
    if (document.fonts) document.fonts.ready.then(moveInk);
    palette();
    try {
      await load();
      setSource(); setTicker();
      $('#boot').remove();
      window.addEventListener('hashchange', show);
      show();
    } catch (e) {
      const bootEl = $('#boot'); bootEl.className = 'boot error';
      bootEl.innerHTML = `<b>Unable to load dispatch data.</b><span>${esc(e.message)}</span><span class="small muted">If you used ?api=…, check that the engine is running (uv run uvicorn api.main:app) and reload.</span>`;
      $('#source span').textContent = 'Not connected';
    }
  }
  boot();
})();

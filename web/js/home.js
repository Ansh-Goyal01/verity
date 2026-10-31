/* Verity landing page. Every number comes from js/snapshot.js (saved engine outputs and experiment
   results). Motion (vendor/motion-12.43.0.js) drives springs, scroll progress and in-view reveals;
   without it, or with reduced motion, the page is fully readable in its final state. */
(function () {
  'use strict';
  const M = window.Motion, C = window.VerityCore, S = window.VERITY_SNAPSHOT;
  const REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const MOTION = Boolean(M && !REDUCED);
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = C.escapeHtml, pct = C.pct, isNum = C.isNum;
  const r1 = v => isNum(v) ? Math.round(v * 10) / 10 : '—';
  const pctInt = v => isNum(v) ? Math.round(v * 100) + '%' : '—';
  if (MOTION) document.documentElement.classList.add('motion');
  // Opened with ?api=… (e.g. by start.py): keep the live-engine connection on links into the app.
  const apiParam = new URLSearchParams(location.search).get('api');
  if (apiParam) document.querySelectorAll('a[href^="index.html"], a[href^="driver.html"], a[href^="console.html"]').forEach(a => {
    const [path, hash] = a.getAttribute('href').split('#');
    a.setAttribute('href', `${path}?api=${encodeURIComponent(apiParam)}${hash ? '#' + hash : ''}`);
  });

  const spring = (extra = {}) => ({ type: 'spring', bounce: 0.18, visualDuration: 0.7, ...extra });
  function animate(el, kf, opts) { if (MOTION) return M.animate(el, kf, opts); return null; }
  /* Run cb once when el enters the viewport (Motion inView, or IntersectionObserver as fallback). */
  function onView(el, cb, amount = 0.3) {
    if (!el) return;
    if (M) return M.inView(el, cb, { amount });
    if (!('IntersectionObserver' in window)) return cb(el);
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { io.unobserve(e.target); cb(e.target); } }), { threshold: amount });
    io.observe(el);
  }
  function countTo(el, to, { decimals = 0, suffix = '', duration = 1.6 } = {}) {
    const fmt = v => (decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString('en-IN')) + suffix;
    if (!MOTION || !isNum(to)) { el.textContent = isNum(to) ? fmt(to) : '—'; return; }
    el.textContent = fmt(0);
    M.animate(0, to, { duration, ease: [0.16, 1, 0.3, 1], onUpdate: v => { el.textContent = fmt(v); } });
  }

  const api = S?.api, R = S?.results;
  const plans = api?.dispatch || {}, scen = api?.scenario, lead = plans.V12 || Object.values(plans)[0];
  const vehicle = id => scen?.vehicles.find(v => v.vehicle_id === id);

  /* ------------------------------------------------------------ nav + progress */
  const nav = $('#nav'), bar = $('#progress');
  const onScroll = () => nav.classList.toggle('scrolled', scrollY > 40);
  addEventListener('scroll', onScroll, { passive: true }); onScroll();
  if (M) M.scroll(p => { bar.style.transform = `scaleX(${p})`; });
  else addEventListener('scroll', () => { const h = document.documentElement; bar.style.transform = `scaleX(${h.scrollTop / Math.max(1, h.scrollHeight - h.clientHeight)})`; }, { passive: true });
  $$('.nav nav a').forEach(a => {
    const sec = $(a.getAttribute('href'));
    if (!sec || !('IntersectionObserver' in window)) return;
    new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { $$('.nav nav a').forEach(x => x.classList.remove('active')); a.classList.add('active'); } }), { rootMargin: '-45% 0px -50% 0px' }).observe(sec);
  });

  /* ------------------------------------------------------------ reveals */
  function reveal(el, i = 0) { animate(el, { opacity: [0, 1], y: [26, 0], filter: ['blur(8px)', 'blur(0px)'] }, spring({ bounce: 0, delay: i * 0.07 })); }
  if (MOTION) {
    M.animate('.h1 .w', { opacity: [0, 1], y: [50, 0], filter: ['blur(14px)', 'blur(0px)'] }, { type: 'spring', bounce: 0.25, visualDuration: 0.9, delay: M.stagger(0.09, { startDelay: 0.15 }) });
    $$('[data-reveal]').forEach(el => {
      const group = el.parentElement ? $$(':scope > [data-reveal]', el.parentElement) : [el];
      const i = Math.max(0, group.indexOf(el));
      if (el.closest('.hero')) { reveal(el, 3 + i); return; }
      onView(el, () => reveal(el, i), 0.2);
    });
    // Failsafe: never leave content invisible if an observer misses it.
    setTimeout(() => $$('[data-reveal]').forEach(el => { if (getComputedStyle(el).opacity === '0' && el.getBoundingClientRect().top < innerHeight) el.style.opacity = 1; }), 4000);
  }

  /* ------------------------------------------------------------ magnetic buttons + spotlight */
  if (MOTION && matchMedia('(pointer:fine)').matches) {
    $$('.magnetic').forEach(b => {
      b.addEventListener('pointermove', e => { const r = b.getBoundingClientRect(); M.animate(b, { x: (e.clientX - r.left - r.width / 2) * 0.22, y: (e.clientY - r.top - r.height / 2) * 0.3 }, { type: 'spring', stiffness: 300, damping: 18 }); });
      b.addEventListener('pointerleave', () => M.animate(b, { x: 0, y: 0 }, { type: 'spring', stiffness: 200, damping: 12 }));
    });
  }
  document.addEventListener('pointermove', e => {
    const card = e.target.closest?.('.spot'); if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty('--mx', `${e.clientX - r.left}px`); card.style.setProperty('--my', `${e.clientY - r.top}px`);
  }, { passive: true });

  if (!S || !lead) { $('#inst-chart').outerHTML = '<p class="dim">Saved engine outputs are not bundled with this page.</p>'; return; }

  /* ------------------------------------------------------------ shared chart: Bingo Line */
  function bingoChart(p, W, H, { labels = true } = {}) {
    const data = p.bingo_line, dist = p.route.distance_km, L = 34, R = 10, T = 12, B = labels ? 28 : 12;
    const x = k => L + k / dist * (W - L - R), y = v => T + (100 - v) / 100 * (H - T - B);
    const pts = key => data.map(q => `${x(q.km).toFixed(1)},${y(q[key]).toFixed(1)}`).join(' ');
    const area = data.map((q, i) => `${i ? 'L' : 'M'}${x(q.km).toFixed(1)},${y(q.expected_soc_p90).toFixed(1)}`).join('') + data.slice().reverse().map(q => `L${x(q.km).toFixed(1)},${y(q.expected_soc_p10).toFixed(1)}`).join('') + 'Z';
    const grid = [0, 25, 50, 75, 100].map(v => `<path d="M${L} ${y(v)}H${W - R}" stroke="rgba(255,255,255,.07)"/><text x="${L - 7}" y="${y(v) + 3}" text-anchor="end" font-size="9.5" fill="#6E6A62">${v}</text>`).join('');
    const ponr = isNum(p.point_of_no_return_km) ? `<path d="M${x(p.point_of_no_return_km)} ${T}V${H - B}" stroke="#FF6B6B" stroke-dasharray="4 4" opacity=".8"/><text x="${x(p.point_of_no_return_km) + 5}" y="${T + 10}" font-size="9.5" fill="#FF6B6B">NO RETURN · km ${r1(p.point_of_no_return_km)}</text>` : '';
    const stops = (p.stops || []).map(s => `<rect x="${x(s.km_along_route) - 13}" y="${H - B - 15}" width="26" height="13" rx="3" fill="#F5A524"/><text x="${x(s.km_along_route)}" y="${H - B - 5.5}" text-anchor="middle" font-size="8.5" font-weight="700" fill="#16120a">CHG</text>`).join('');
    const xl = labels ? [0, Math.round(dist / 2), dist].map(k => `<text x="${x(k)}" y="${H - 8}" text-anchor="middle" font-size="9.5" fill="#6E6A62">${r1(k)} km</text>`).join('') : '';
    return { x, y, data, svg: `<defs><linearGradient id="gband${W}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#F5A524" stop-opacity=".38"/><stop offset="1" stop-color="#F5A524" stop-opacity=".08"/></linearGradient><filter id="glow${W}"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
      ${grid}<path class="ch-band" d="${area}" fill="url(#gband${W})"/>
      <polyline class="ch-line" points="${pts('expected_soc_pct')}" fill="none" stroke="#F5A524" stroke-width="2.4" pathLength="1" stroke-dasharray="1" filter="url(#glow${W})"/>
      <polyline class="ch-line" points="${pts('bingo_soc_pct')}" fill="none" stroke="#FF6B6B" stroke-width="2" pathLength="1" stroke-dasharray="1"/>
      ${ponr}${stops}${xl}` };
  }
  function drawLines(root, delay = 0) {
    const lines = $$('.ch-line', root), band = $$('.ch-band', root);
    if (!MOTION) { lines.forEach(l => l.style.strokeDashoffset = 0); return; }
    lines.forEach((l, i) => M.animate(l, { strokeDashoffset: [1, 0] }, { duration: 1.8, ease: [0.65, 0, 0.35, 1], delay: delay + i * 0.25 }));
    band.forEach(b => M.animate(b, { opacity: [0, 1] }, { duration: 1.2, delay: delay + 0.6 }));
  }
  const stampHTML = d => `<span class="stamp ${C.decisionClass(d)}">${C.decisionLabel(d)}</span>`;

  /* ------------------------------------------------------------ hero */
  const b = scen.booking, runs = R?.e4?.compute?.runs_per_forecast;
  $('#hero-meta').innerHTML = [
    [`${r1(lead.route.distance_km)} km`, 'Demo route'],
    [isNum(runs) ? runs.toLocaleString('en-IN') : '—', 'Runs per forecast'],
    [isNum(lead.compute_ms) ? (lead.compute_ms / 1000).toFixed(1) + ' s' : '—', 'To sign a release'],
    [`${Object.keys(plans).length} answers`, 'One booking'],
  ].map(([v, k]) => `<li><b>${esc(v)}</b><span>${esc(k)}</span></li>`).join('');
  $('#inst-title').textContent = `${b.origin.name} → ${b.destination.name}`;
  $('#inst-foot').textContent = `${lead.plan_id} · ${lead.vehicle_id} · ${pct(lead.confidence)} confidence`;
  const ch = bingoChart(lead, 520, 250), chartEl = $('#inst-chart');
  chartEl.innerHTML = ch.svg + `<g id="car"><circle r="11" fill="#F5A524" opacity=".18"/><circle r="5.5" fill="#F5A524" stroke="#07080A" stroke-width="2"/></g><circle id="carb" r="4" fill="#FF6B6B" stroke="#07080A" stroke-width="2"/>`;
  const car = $('#car'), carb = $('#carb'), ro = $('#inst-readout'), st = $('#inst-stamp');
  function readout(q) {
    car.setAttribute('transform', `translate(${ch.x(q.km)},${ch.y(q.expected_soc_pct)})`);
    carb.setAttribute('cx', ch.x(q.km)); carb.setAttribute('cy', ch.y(q.bingo_soc_pct));
    ro.innerHTML = `<div><small>km</small><b>${r1(q.km)}</b></div><div><small>Expected</small><b>${r1(q.expected_soc_pct)}%</b></div><div><small>Range p10–p90</small><b>${r1(q.expected_soc_p10)}–${r1(q.expected_soc_p90)}</b></div><div><small>Bingo Line</small><b style="color:#FF6B6B">${r1(q.bingo_soc_pct)}%</b></div>`;
  }
  readout(ch.data[0]);
  let heroRun = null;
  function heroLoop() {
    st.innerHTML = '';
    if (!MOTION) { readout(ch.data[ch.data.length - 1]); st.innerHTML = stampHTML(lead.decision); drawLines(chartEl); return; }
    const n = ch.data.length - 1;
    heroRun = M.animate(0, n, { duration: 7, ease: 'linear', delay: 1.6, onUpdate: v => readout(ch.data[Math.round(v)]),
      onComplete: () => { st.innerHTML = stampHTML(lead.decision); M.animate(st.firstChild, { scale: [2.4, 1], rotate: [-16, -4], opacity: [0, 1] }, { type: 'spring', bounce: 0.45, visualDuration: 0.5 }); heroRun = setTimeout(heroLoop, 3200); } });
  }
  drawLines(chartEl, 0.5); heroLoop();
  // 3D tilt following the pointer
  const vis = $('.hero-visual'), inst = $('#instrument');
  if (MOTION && matchMedia('(pointer:fine)').matches) {
    vis.addEventListener('pointermove', e => { const r = vis.getBoundingClientRect(), px = (e.clientX - r.left) / r.width - 0.5, py = (e.clientY - r.top) / r.height - 0.5; M.animate(inst, { rotateY: px * 10, rotateX: -py * 8 }, { type: 'spring', stiffness: 120, damping: 18 }); });
    vis.addEventListener('pointerleave', () => M.animate(inst, { rotateY: 0, rotateX: 0 }, { type: 'spring', stiffness: 80, damping: 14 }));
  }
  const alt = (lead.alternates || []).slice().sort((a, c) => (c.p_works * c.p_reach) - (a.p_works * a.p_reach))[0];
  $('#fc-1').innerHTML = alt ? `<small>Backup if the stop fails</small><b><span class="fc-dot" style="background:#9DB4FF"></span>${pct(alt.p_works)} works</b><div class="dim" style="font-size:12px">${pct(alt.p_reach)} chance to reach</div>` : '';
  $('#fc-2').innerHTML = `<small>Arrival battery p10–p90</small><b><span class="fc-dot" style="background:#3DD68C"></span>${lead.arrival_soc.p10}–${lead.arrival_soc.p90}%</b><div class="dim" style="font-size:12px">median ${lead.arrival_soc.p50}%</div>`;
  if (MOTION) {
    M.animate('#fc-1', { y: [0, -12, 0] }, { duration: 6, repeat: Infinity, ease: 'easeInOut' });
    M.animate('#fc-2', { y: [0, 10, 0] }, { duration: 7, repeat: Infinity, ease: 'easeInOut', delay: 1 });
    M.scroll(M.animate('.aurora:not(.small)', { y: [0, 160], opacity: [0.55, 0.15] }, { ease: 'linear' }), { target: $('.hero'), offset: ['start start', 'end start'] });
  }

  /* ------------------------------------------------------------ problem */
  const stats = C.chargerStats(api.chargers?.chargers);
  onView($('.big-stat'), () => { countTo($('#t-unknown'), stats.byTrust.unknown || 0, { duration: 2 }); countTo($('#t-total'), stats.total, { duration: 2 }); }, 0.4);
  if (!MOTION) { $('#t-unknown').textContent = stats.byTrust.unknown || 0; $('#t-total').textContent = stats.total; }

  /* ------------------------------------------------------------ how: scenes */
  const ids = scen.vehicles.map(v => v.vehicle_id);
  (function scene0() {
    const vs = scen.vehicles.filter(v => v.usable_kwh_estimate);
    const lo = Math.floor(Math.min(...vs.map(v => v.usable_kwh_estimate.p10)) - 2), hi = Math.ceil(Math.max(...vs.map(v => v.usable_kwh_estimate.p90)) + 2);
    const at = v => (v - lo) / (hi - lo) * 100;
    const typical = vs.map(v => Number((v.health_note.match(/class typical \(([\d.]+) kWh\)/) || [])[1])).find(isNum);
    $('#scene-0').innerHTML = `<p class="dim" style="margin:0 0 18px;font-size:14px">Usable battery, calibrated from each car's own trips (p10–p90, median marked)</p><div class="cap-row">${vs.map(v => { const u = v.usable_kwh_estimate; return `<div class="cap"><span>${esc(v.vehicle_id)}</span><div class="cap-track"><div class="cap-band" style="left:${at(u.p10)}%;width:${at(u.p90) - at(u.p10)}%"></div><div class="cap-mid" style="left:${at(u.p50)}%"></div>${isNum(typical) ? `<div style="position:absolute;top:-6px;bottom:-6px;left:${at(typical)}%;border-left:1.5px dashed #9DB4FF"></div>` : ''}</div><span>${u.p50} kWh</span></div>`; }).join('')}</div>
      <div class="cap-axis"><span>${lo} kWh</span><span>${hi} kWh</span></div>
      ${isNum(typical) ? `<p class="dim" style="font-size:12.5px;margin:18px 0 0"><span style="color:#9DB4FF">┆</span> class typical ${typical} kWh · ${esc(vehicle('V07')?.health_note || '')}</p>` : ''}`;
  })();
  const ch1 = bingoChart(lead, 520, 300);
  $('#scene-1').innerHTML = `<svg viewBox="0 0 520 300" role="img" aria-label="Expected battery band and Bingo Line (simulated)">${ch1.svg}</svg><div class="legend" style="margin-top:10px"><span><i style="background:#F5A524;opacity:.4"></i>p10–p90 band</span><span><i style="background:#F5A524"></i>expected</span><span><i style="background:#FF6B6B"></i>Bingo Line — divert below</span></div>`;
  (function scene2() {
    const pts = lead.route.polyline, chs = api.chargers?.chargers || [];
    const all = pts.concat(chs.map(c => [c.lat, c.lon]));
    const lats = all.map(p => p[0]), lons = all.map(p => p[1]);
    const la0 = Math.min(...lats), la1 = Math.max(...lats), lo0 = Math.min(...lons), lo1 = Math.max(...lons), k = Math.cos((la0 + la1) / 2 * Math.PI / 180);
    const W = 520, H = 400, pad = 18, sx = (W - 2 * pad) / ((lo1 - lo0) * k), sy = (H - 2 * pad) / (la1 - la0), s = Math.min(sx, sy);
    const ox = (W - (lo1 - lo0) * k * s) / 2, oy = (H - (la1 - la0) * s) / 2;
    const P = (la, lo) => [ox + (lo - lo0) * k * s, oy + (la1 - la) * s];
    const col = t => ({ high: '#3DD68C', medium: '#F5A524', low: '#FF6B6B' }[t] || '#9DB4FF');
    const route = pts.map(q => P(q[0], q[1]).map(v => v.toFixed(1)).join(',')).join(' ');
    const dots = chs.map(c => { const [x, y] = P(c.lat, c.lon); return `<circle class="cdot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${c.trust_level === 'unknown' ? 2.6 : 6}" fill="${col(c.trust_level)}" opacity="${c.trust_level === 'unknown' ? .55 : 1}"/>`; }).join('');
    const stops = (lead.stops || []).map(s0 => { const [x, y] = P(s0.lat, s0.lon); return `<rect x="${x - 7}" y="${y - 7}" width="14" height="14" rx="3" fill="#F5A524" stroke="#07080A" stroke-width="2"/>`; }).join('');
    const alts = (lead.alternates || []).map(a => { const [x, y] = P(a.lat, a.lon); return `<circle cx="${x}" cy="${y}" r="8" fill="none" stroke="#9DB4FF" stroke-width="2.5"/>`; }).join('');
    $('#scene-2').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Demo route with the charger directory coloured by trust (simulated trust values)"><polyline class="ch-line" points="${route}" fill="none" stroke="#F5A524" stroke-width="3" stroke-linejoin="round" pathLength="1" stroke-dasharray="1"/>${dots}${alts}${stops}</svg><div class="legend" style="margin-top:6px"><span><i style="background:#9DB4FF;border-radius:50%"></i>unknown ${stats.byTrust.unknown || 0}</span><span><i style="background:#3DD68C;border-radius:50%"></i>high ${stats.byTrust.high || 0}</span><span><i style="background:#F5A524;border-radius:50%"></i>medium ${stats.byTrust.medium || 0}</span><span><i style="background:#FF6B6B;border-radius:50%"></i>low ${stats.byTrust.low || 0}</span><span><i style="background:#F5A524"></i>planned stop</span><span><i style="border:2px solid #9DB4FF;background:none;border-radius:50%"></i>backup</span></div>`;
  })();
  $('#scene-3').innerHTML = `<div class="stamps3">${ids.map(id => { const p = plans[id], v = vehicle(id); return `<div class="s3"><b>${esc(id)}</b><p>${p.decision === 'INSUFFICIENT_EVIDENCE' ? esc(p.refusal_reason) : `Confidence ${pct(p.confidence)} · arrival ${p.arrival_soc.p10}–${p.arrival_soc.p90}% · start ${v.soc_pct}%`}</p>${stampHTML(p.decision)}</div>`; }).join('')}</div><p class="dim" style="font-size:12.5px;margin:14px 0 0">Booking ${esc(b.booking_id)} · target ${pct(b.target_confidence)} · ${b.passengers} passengers · AC ${b.ac_on ? 'on' : 'off'}</p>`;
  if (!MOTION) $$('.ch-line').forEach(l => l.style.strokeDashoffset = 0);

  const LABELS = ['STEP 01 · LEARN THE CAR', 'STEP 02 · SIMULATE 2,000 FUTURES', 'STEP 03 · DISTRUST CHARGERS', 'STEP 04 · RELEASE OR REFUSE'];
  let current = -1;
  function setStep(i) {
    if (i === current) return; current = i;
    $$('.step').forEach((s, j) => s.classList.toggle('is-on', j === i));
    $$('.scene').forEach((s, j) => s.classList.toggle('is-on', j === i));
    $$('#stage-dots i').forEach((d, j) => d.classList.toggle('on', j === i));
    $('#stage-label').textContent = LABELS[i];
    const sc = $('#scene-' + i);
    if (!MOTION) return;
    if (i === 0) M.animate($$('.cap-band', sc), { scaleX: [0, 1], opacity: [0, 1] }, { type: 'spring', bounce: 0.2, visualDuration: 0.8, delay: M.stagger(0.12) });
    if (i === 1 || i === 2) drawLines(sc, 0.1);
    if (i === 2) M.animate($$('.cdot', sc), { opacity: [0, null], scale: [0, 1] }, { duration: 0.4, delay: M.stagger(0.004, { startDelay: 0.6 }) });
    if (i === 3) M.animate($$('.s3', sc), { opacity: [0, 1], x: [30, 0] }, { type: 'spring', bounce: 0.2, visualDuration: 0.6, delay: M.stagger(0.12) });
    if (i === 3) $$('.s3 .stamp', sc).forEach((s0, j) => M.animate(s0, { scale: [2.2, 1], rotate: [-14, -4], opacity: [0, 1] }, { type: 'spring', bounce: 0.45, visualDuration: 0.45, delay: 0.35 + j * 0.18 }));
  }
  $$('.cdot').forEach(d => { d.style.transformBox = 'fill-box'; d.style.transformOrigin = 'center'; });
  $$('.cap-band').forEach(d => { d.style.transformOrigin = 'left'; });
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) setStep(Number(e.target.dataset.step)); }), { rootMargin: '-45% 0px -45% 0px' });
    $$('.step').forEach(s => io.observe(s));
  }
  setStep(0);

  /* ------------------------------------------------------------ beams */
  function drawBeams() {
    const box = $('#beams'), svg = $('#beam-svg'), hub = $('#hub');
    if (getComputedStyle(svg).display === 'none') return;
    const br = box.getBoundingClientRect(), hr = hub.getBoundingClientRect();
    const hx = hr.left - br.left + hr.width / 2, hy = hr.top - br.top + hr.height / 2;
    svg.setAttribute('viewBox', `0 0 ${br.width} ${br.height}`);
    let html = '<defs><linearGradient id="bm" x1="0" x2="1"><stop offset="0" stop-color="#F5A524" stop-opacity="0"/><stop offset=".5" stop-color="#FFD28A"/><stop offset="1" stop-color="#FF7A1A" stop-opacity="0"/></linearGradient></defs>';
    $$('[data-node]:not(.hub)', box).forEach((n, i) => {
      const r = n.getBoundingClientRect(), left = r.left - br.left < hx;
      const nx = left ? r.right - br.left : r.left - br.left, ny = r.top - br.top + r.height / 2;
      const ex = left ? hx - hr.width / 2 : hx + hr.width / 2, mx = (nx + ex) / 2;
      const d = left ? `M${nx},${ny} C${mx},${ny} ${mx},${hy} ${ex},${hy}` : `M${ex},${hy} C${mx},${hy} ${mx},${ny} ${nx},${ny}`;
      html += `<path d="${d}" fill="none" stroke="rgba(255,255,255,.09)" stroke-width="2"/><path d="${d}" fill="none" stroke="url(#bm)" stroke-width="2.5" stroke-linecap="round" pathLength="100" stroke-dasharray="18 82" class="pulse" style="animation-delay:${i * -0.55}s"/>`;
    });
    svg.innerHTML = html + `<style>.pulse{animation:flow 2.8s linear infinite}@keyframes flow{from{stroke-dashoffset:100}to{stroke-dashoffset:0}}@media (prefers-reduced-motion:reduce){.pulse{animation:none;stroke-dasharray:none;opacity:.5}}</style>`;
  }
  drawBeams(); addEventListener('resize', drawBeams);
  if (document.fonts) document.fonts.ready.then(drawBeams);
  onView($('#beams'), () => { if (MOTION) M.animate($$('#beams [data-node]'), { opacity: [0, 1], scale: [0.85, 1] }, { type: 'spring', bounce: 0.35, visualDuration: 0.6, delay: M.stagger(0.08) }); }, 0.3);

  /* ------------------------------------------------------------ proof bento */
  (function bento() {
    if (!R) { $('#bento').innerHTML = '<p class="dim">Saved experiment results are not bundled with this page.</p>'; return; }
    const cards = [];
    const e4 = R.e4?.scenarios, e4k = e4 ? Object.keys(e4) : [], e4l = e4 ? e4[e4k[e4k.length - 1]] : null;
    if (e4l) cards.push(`<article class="bc w4 spot" data-reveal><div class="bc-top"><span class="bc-id">E4 · DISPATCH VS FIXED PLAN</span><span class="tag-sim">Simulated</span></div>
      <div class="num"><span class="bad" data-count="${e4l.stranding_rate_fixed * 100}" data-suffix="%">0%</span><span class="arrow">→</span><span class="good" data-count="${e4l.stranding_rate_dispatch * 100}" data-suffix="%">0%</span><small>trips stranded · ${esc(e4k[e4k.length - 1])}</small></div>
      <div class="minibars">${e4k.map(k => `<div class="mb"><div class="mb-pair"><span class="f" style="height:${e4[k].stranding_rate_fixed / 0.4 * 100}%" title="Fixed ${pctInt(e4[k].stranding_rate_fixed)}"></span><span class="d" style="height:${e4[k].stranding_rate_dispatch / 0.4 * 100}%" title="Dispatch ${pctInt(e4[k].stranding_rate_dispatch)}"></span></div><label>${esc(k.replace(' chargers down', ''))} down</label></div>`).join('')}</div>
      <div class="legend"><span><i style="background:#FF6B6B"></i>fixed shortest-path plan</span><span><i style="background:#3DD68C"></i>Verity dispatch</span></div>
      <span class="src">${R.e4.trips_per_scenario} trips per scenario · Wilson 95% CI ${pctInt(e4l.ci95_dispatch[0])}–${pctInt(e4l.ci95_dispatch[1])} for dispatch · results/e4_dispatch_vs_fixed_simulated.json</span></article>`);
    const e2 = R.e2?.methods;
    if (e2) {
      const W = 300, H = 240, L = 26, x = v => L + v * (W - L - 6), y = v => 6 + (1 - v) * (H - 30);
      const line = (m, c) => { const bs = m.bins.filter(q => q.n > 0); return `<polyline points="${bs.map(q => `${x(q.mean_pred)},${y(q.observed)}`).join(' ')}" fill="none" stroke="${c}" stroke-width="2" pathLength="1" stroke-dasharray="1" class="ch-line"/>` + bs.map(q => `<circle cx="${x(q.mean_pred)}" cy="${y(q.observed)}" r="${2.5 + Math.sqrt(q.n) / 3}" fill="${c}"><title>predicted ${pctInt(q.mean_pred)}, observed ${pctInt(q.observed)}, n=${q.n}</title></circle>`).join(''); };
      cards.push(`<article class="bc tall spot rel" data-reveal><div class="bc-top"><span class="bc-id">E2 · CALIBRATION</span><span class="tag-sim">Simulated</span></div>
        <h3>When it says 90%, it means about 90%.</h3><p>90% band caught the real arrival <b style="color:#3DD68C">${pctInt(e2.shared.coverage_90_arrival_band)}</b> of the time with shared trip factors, vs <b style="color:#9DB4FF">${pctInt(e2.independent.coverage_90_arrival_band)}</b> with naive per-segment noise.</p>
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Reliability diagram (simulated)"><path d="M${x(0)} ${y(0)}L${x(1)} ${y(1)}" stroke="rgba(255,255,255,.25)" stroke-dasharray="4 4"/>${[0, .5, 1].map(v => `<text x="${x(v)}" y="${H - 8}" text-anchor="middle" font-size="9" fill="#6E6A62">${v * 100}%</text>`).join('')}${line(e2.independent, '#9DB4FF')}${line(e2.shared, '#F5A524')}</svg>
        <div class="legend"><span><i style="background:#F5A524"></i>shared factors (Verity)</span><span><i style="background:#9DB4FF"></i>independent noise</span></div>
        <span class="src">${R.e2.trips} held-out trips · ECE ${e2.shared.ece} vs ${e2.independent.ece} · results/e2_calibration_simulated.json</span></article>`);
    }
    const r12 = R.e5?.scenarios?.['ring of 12']?.mae_on_attacked_chargers;
    if (r12) cards.push(`<article class="bc spot" data-reveal><div class="bc-top"><span class="bc-id">E5 · FAKE-REPORT RINGS</span><span class="tag-sim">Simulated</span></div>
      <div class="num"><span class="bad" data-count="${r12.avg_rating}" data-dec="2">0</span><span class="arrow">→</span><span class="good" data-count="${r12.full}" data-dec="2">0</span></div>
      <p>Error on chargers attacked by a ring of 12 fake reporters: plain average vs Verity's trust engine. An evasive ring that spreads reports over 10 days is <b>not</b> caught.</p>
      <span class="src">${R.e5.worlds_per_scenario} worlds · results/e5_trust_recovery_simulated.json</span></article>`);
    const e1 = R.e1?.methods, vp = e1?.verity_pooled?.[0], ns = e1?.nominal_spec?.[0];
    if (vp && ns) cards.push(`<article class="bc spot" data-reveal><div class="bc-top"><span class="bc-id">E1 · LEARNING CURVE</span><span class="tag-sim">Simulated</span></div>
      <div class="num"><span class="bad" data-count="${ns.mae_pp}" data-dec="1">0</span><span class="arrow">→</span><span class="good" data-count="${vp.mae_pp}" data-dec="1">0</span><small>pp error</small></div>
      <p>Arrival-battery error after a single calibration trip: spec-sheet range vs Verity fleet-pooled.</p>
      <span class="src">${R.e1.test_trips} held-out trips · ${R.e1.vehicles} vehicles · results/e1_learning_curve_simulated.json</span></article>`);
    const tests = R.run_all?.steps?.find(s0 => s0.step === 'tests'), m = tests?.last_line?.match(/(\d+) passed(?:, (\d+) skipped)?/);
    if (m) cards.push(`<article class="bc w3 spot" data-reveal><div class="bc-top"><span class="bc-id">TEST SUITE</span><span class="bc-id">${R.run_all.all_ok ? 'ALL STEPS OK' : 'SOME STEPS FAILED'}</span></div>
      <div class="num"><span class="good" data-count="${m[1]}">0</span><small>tests passing${m[2] ? ` · ${m[2]} skipped` : ''}</small></div>
      <p>One command regenerates every experiment and API example from a clean state.</p><span class="src">uv run python scripts/run_all.py · results/run_all_summary.json</span></article>`);
    const cp = R.e4?.compute;
    if (cp) cards.push(`<article class="bc w3 spot" data-reveal><div class="bc-top"><span class="bc-id">SPEED</span><span class="bc-id">RECORDING MACHINE</span></div>
      <div class="num"><span data-count="${cp.full_dispatch_ms_mean / 1000}" data-dec="2" data-suffix=" s">0</span><small>per full dispatch</small></div>
      <p>${cp.runs_per_forecast.toLocaleString('en-IN')} runs per forecast on a ${cp.real_route_km} km route with ${cp.chargers_near_route} nearby chargers, backups included (max ${cp.full_dispatch_ms_max} ms).</p><span class="src">results/e4_dispatch_vs_fixed_simulated.json · compute</span></article>`);
    $('#bento').innerHTML = cards.join('');
    $$('#bento .bc').forEach((card, i) => {
      if (MOTION) card.style.opacity = 0;
      onView(card, () => {
        reveal(card, i % 3);
        $$('[data-count]', card).forEach(el => countTo(el, Number(el.dataset.count), { decimals: Number(el.dataset.dec || 0), suffix: el.dataset.suffix || '' }));
        drawLines(card, 0.3);
        if (MOTION) M.animate($$('.mb-pair span', card), { scaleY: [0, 1] }, { type: 'spring', bounce: 0.25, visualDuration: 0.7, delay: M.stagger(0.06, { startDelay: 0.2 }) });
      }, 0.25);
    });
    if (!MOTION) $$('#bento .ch-line').forEach(l => l.style.strokeDashoffset = 0);
  })();

  /* ------------------------------------------------------------ driver phone */
  (function driver() {
    const rp = api.replay?.[lead.vehicle_id]; if (!rp) return;
    const T = { en: { brand: 'VERITY · DRIVER', battery: 'BATTERY', toGo: 'KM TO GO', above: 'ABOVE BINGO', below: 'BELOW BINGO', next: 'NEXT', divert: 'DIVERT NOW', msg: n => `Divert to ${n}. Planned charger reported broken.`, plan: (n, k) => `Plan: charge at ${n}, km ${k}.`, broken: n => `Charger reported broken: ${n}` },
      hi: { brand: 'VERITY · ड्राइवर', battery: 'बैटरी', toGo: 'बाकी किमी', above: 'बिंगो से ऊपर', below: 'बिंगो से नीचे', next: 'अगला', divert: 'अभी रास्ता बदलें', msg: n => `${n} की ओर मुड़ें। नियोजित चार्जर खराब बताया गया है।`, plan: (n, k) => `योजना: ${n} पर चार्ज करें, किमी ${k}।`, broken: n => `चार्जर खराब बताया गया: ${n}` } };
    let lang = 'en', i = 0, timer = null, s = { km: 0, soc: vehicle(lead.vehicle_id).soc_pct, msg: null, alert: null };
    const ev = rp.events, dist = lead.route.distance_km, stop = lead.stops?.[0];
    const name = cid => [...(lead.stops || []), ...(lead.alternates || [])].find(c => c.charger_id === cid)?.name || cid;
    $('#d-timeline').innerHTML = ev.map(e => `<li class="${e.type === 'soc_reading' ? '' : 'bad'}"><time>T+${C.fmtClock(e.t_offset_s)}</time><p>${e.type === 'soc_reading' ? `Battery ${e.soc_pct}% at km ${r1(e.km)}` : e.type === 'charger_report' ? `Charger reported <b>${esc(e.status)}</b>: ${esc(name(e.charger_id))}` : `Engine: <b>${esc(e.action)}</b> → ${esc(e.divert_to?.name || e.message)}`}</p></li>`).join('');
    function paint() {
      const t = T[lang], bingo = C.bingoAt(lead.bingo_line, s.km), m = Math.round(s.soc - bingo), prog = Math.min(100, s.km / dist * 100);
      const msg = s.msg || (stop ? t.plan(stop.name, r1(stop.km_along_route)) : '');
      $('#screen').innerHTML = `<div class="sc-bar"><span>${C.fmtClock(ev[Math.max(0, i - 1)]?.t_offset_s || 0)}</span><span>SIM ▮▮▮</span></div>
        <div class="sc-head"><b>${t.brand}</b><button class="sc-lang" type="button">${lang === 'en' ? 'हिन्दी' : 'English'}</button></div>
        <div class="sc-body"><div class="sc-batt"><div><small>${t.battery}</small><strong>${s.soc}<span style="font-size:.42em">%</span></strong></div><div class="sc-cell"><span style="height:${s.soc}%"></span></div></div>
        <div class="sc-ms"><div class="sc-m"><small>${t.toGo}</small><strong>${r1(dist - s.km)}</strong></div><div class="sc-m"><small>${m < 0 ? t.below : t.above}</small><strong style="color:${m < 0 ? '#B42318' : '#11743F'}">${isNum(m) ? Math.abs(m) + '%' : '—'}</strong></div></div>
        <div class="sc-track"><div class="rh"><span>${esc(b.origin.name)}</span><span>${esc(b.destination.name)}</span></div><div class="sc-line"><div class="sc-prog" style="width:${prog}%"></div><span class="sc-me" style="left:${prog}%"></span></div></div>
        <div class="sc-msg"><small>${t.next}</small>${esc(msg)}</div></div>
        <div class="sc-alert ${s.alert ? 'on' : ''}" role="${s.alert ? 'alert' : 'presentation'}"><span class="badge">${t.divert}</span><h4>${t.divert}</h4><p>${s.alert ? esc(t.msg(s.alert.name)) : ''}</p><small>${s.alert ? pct(s.alert.p_works) + ' chance it works' : ''}</small></div>`;
      $('.sc-lang').onclick = () => { lang = lang === 'en' ? 'hi' : 'en'; paint(); };
      $$('#d-timeline li').forEach((li, j) => li.classList.toggle('on', j < i));
    }
    function step() {
      if (i >= ev.length) { timer = setTimeout(() => { i = 0; s = { km: 0, soc: vehicle(lead.vehicle_id).soc_pct, msg: null, alert: null }; paint(); timer = setTimeout(step, 900); }, 3600); return; }
      const e = ev[i++];
      if (e.type === 'soc_reading') { s.km = e.km; s.soc = e.soc_pct; }
      if (e.type === 'charger_report') s.msg = T[lang].broken(name(e.charger_id));
      if (e.type === 'forecast' && e.divert_to) s.alert = e.divert_to;
      paint(); timer = setTimeout(step, e.type === 'soc_reading' ? 1100 : 1500);
    }
    paint();
    if (!MOTION) { while (i < ev.length) step(); clearTimeout(timer); return; }
    onView($('#phone'), () => { if (!timer) timer = setTimeout(step, 600); }, 0.4);
  })();

  // Lines start undrawn only when they will be animated in.
  if (MOTION) $$('.ch-line').forEach(l => { if (!l.closest('#inst-chart')) l.style.strokeDashoffset = 1; });
  if (MOTION && current >= 0) { const c0 = current; current = -1; setStep(c0); }

  /* ------------------------------------------------------------ honesty quote */
  const refused = Object.values(plans).find(p => p.decision === 'INSUFFICIENT_EVIDENCE');
  if (refused) $('#refusal').innerHTML = `${stampHTML(refused.decision)}“${esc(refused.refusal_reason)}”<cite>— Verity's actual dispatch response for ${esc(refused.vehicle_id)} on the demo booking (simulated scenario)</cite>`;
  else $('#refusal').textContent = 'When evidence is too thin, Verity says so instead of promising.';
})();

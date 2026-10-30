/* Pure helpers shared by Mission Control. No DOM access, so they run in Node tests too.
   Rule: these only format, select or interpolate values that the engine supplied. */
(function (root) {
  'use strict';

  const DECISION_LABEL = { GO: 'GO', NO_GO: 'NO-GO', INSUFFICIENT_EVIDENCE: 'INSUFFICIENT EVIDENCE' };
  const isNum = v => typeof v === 'number' && Number.isFinite(v);

  function pct(p) { return isNum(p) ? `${Math.round(p * 100)}%` : '—'; }
  function decisionLabel(d) { return DECISION_LABEL[d] || String(d || '—'); }
  function decisionClass(d) { return d === 'NO_GO' ? 'nogo' : d === 'INSUFFICIENT_EVIDENCE' ? 'unknown' : 'go'; }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function fmtClock(seconds) {
    if (!isNum(seconds)) return '—';
    const s = Math.max(0, Math.round(seconds)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
    const mm = String(m).padStart(2, '0'), ss = String(r).padStart(2, '0');
    return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  function fmtDuration(min) {
    if (!isNum(min)) return '—';
    const h = Math.floor(min / 60), m = Math.round(min % 60);
    return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`;
  }

  /* Linear interpolation of the supplied Bingo Line at a route position (same rule as driver.js). */
  function bingoAt(line, km) {
    if (!Array.isArray(line) || !line.length || !isNum(km)) return null;
    let left = line[0];
    for (const p of line) {
      if (p.km > km) {
        if (p.km === left.km) return left.bingo_soc_pct;
        const f = (km - left.km) / (p.km - left.km);
        return left.bingo_soc_pct + f * (p.bingo_soc_pct - left.bingo_soc_pct);
      }
      left = p;
    }
    return left.bingo_soc_pct;
  }

  /* The supplied point closest to km (no interpolation: hover readouts show real points only). */
  function nearestPoint(line, km) {
    if (!Array.isArray(line) || !line.length) return null;
    let best = line[0];
    for (const p of line) if (Math.abs(p.km - km) < Math.abs(best.km - km)) best = p;
    return best;
  }

  function haversineKm(a, b) {
    const R = 6371.0088, toRad = x => x * Math.PI / 180;
    const dLat = toRad(b[0] - a[0]), dLon = toRad(b[1] - a[1]);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  /* Cumulative geometric length of a polyline, used only to place a marker at a reported km. */
  function cumulativeKm(polyline) {
    const out = [0];
    for (let i = 1; i < polyline.length; i++) out.push(out[i - 1] + haversineKm(polyline[i - 1], polyline[i]));
    return out;
  }

  /* Map position for a reported km along a route of routeKm, scaling the polyline length to it. */
  function pointAtKm(polyline, cum, routeKm, km) {
    if (!polyline || polyline.length < 2) return null;
    const total = cum[cum.length - 1];
    const target = Math.min(total, Math.max(0, routeKm > 0 ? km / routeKm * total : 0));
    let lo = 0, hi = cum.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= target) lo = mid; else hi = mid; }
    const span = cum[hi] - cum[lo] || 1, f = (target - cum[lo]) / span;
    return [polyline[lo][0] + f * (polyline[hi][0] - polyline[lo][0]), polyline[lo][1] + f * (polyline[hi][1] - polyline[lo][1])];
  }

  /* Validate and stage the energy waterfall. Never fills gaps: returns ok:false with a reason. */
  function waterfall(start, breakdown, arrival) {
    if (!Array.isArray(breakdown) || !breakdown.length) return { ok: false, reason: 'Energy breakdown unavailable from the API.' };
    if (!breakdown.every(r => r && typeof r.term === 'string' && typeof r.label === 'string' && isNum(r.soc_pct)))
      return { ok: false, reason: 'Energy breakdown contains unavailable or invalid terms.' };
    if (!isNum(start)) return { ok: false, reason: 'Starting battery unavailable from the scenario.' };
    if (!isNum(arrival)) return { ok: false, reason: 'Arrival battery unavailable from the API.' };
    let running = start;
    const rows = [{ label: 'Start', term: 'start', value: start, from: 0, to: start, absolute: true }];
    for (const r of breakdown) { rows.push({ label: r.label, term: r.term, value: r.soc_pct, from: running, to: running + r.soc_pct, absolute: false }); running += r.soc_pct; }
    rows.push({ label: 'Arrival', term: 'arrival', value: arrival, from: 0, to: arrival, absolute: true });
    const levels = rows.flatMap(r => [r.from, r.to]);
    return { ok: true, rows, sum: running, reconciles: Math.abs(running - arrival) <= 1, low: Math.min(0, ...levels), high: Math.max(100, ...levels) };
  }

  /* Counts over the supplied directory; nothing is estimated. */
  function chargerStats(chargers) {
    const list = Array.isArray(chargers) ? chargers : [];
    const count = (key, fn) => list.reduce((acc, c) => { const k = fn(c); acc[k] = (acc[k] || 0) + 1; return acc; }, {});
    return {
      total: list.length,
      byTrust: count('trust', c => c.trust_level || 'unknown'),
      bySource: count('source', c => c.source || 'unknown'),
      withReports: list.filter(c => isNum(c.n_reports) && c.n_reports > 0).length,
    };
  }

  /* Replay events due at or before elapsed seconds, starting from index. */
  function dueEvents(events, index, elapsed) {
    const out = [];
    let i = index;
    while (i < events.length && events[i].t_offset_s <= elapsed) out.push(events[i++]);
    return { events: out, next: i };
  }

  const api = { pct, decisionLabel, decisionClass, escapeHtml, fmtClock, fmtDuration, bingoAt, nearestPoint, haversineKm, cumulativeKm, pointAtKm, waterfall, chargerStats, dueEvents, isNum };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.VerityCore = api;
})(typeof window !== 'undefined' ? window : globalThis);

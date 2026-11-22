export const esc = value => window.VerityCore.escapeHtml(value);
export const idPath = value => encodeURIComponent(String(value));
export const statusBox = () => '<p class="status" role="status" aria-live="polite"></p>';
export const section = (title, content) => `<section class="sheet"><h2>${esc(title)}</h2>${content}</section>`;
export const heading = (title, description) => `<header class="page-heading"><h1 tabindex="-1">${esc(title)}</h1><p>${esc(description)}</p></header>`;
export const button = (label, primary = false) => `<button type="submit" class="button ${primary ? 'button-primary' : 'button-outline'}">${esc(label)}</button>`;
export const option = (value, label, selected = false) => `<option value="${esc(value)}"${selected ? ' selected' : ''}>${esc(label)}</option>`;
export const dateText = value => value ? new Date(value).toLocaleString(undefined, {dateStyle:'medium', timeStyle:'short'}) : '—';
export function numberInput(name, label, value, min, max, step = 1) {
  return `<label>${esc(label)}<input type="number" name="${esc(name)}" value="${esc(value)}" min="${min}" max="${max}" step="${step}" required></label>`;
}
export function setStatus(node, message = '', error = false) {
  node.textContent = message;
  node.classList.toggle('error', error);
}
export function onSubmit(form, work, pending = 'Working…', onError) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.busy) return;
    const submit = event.submitter || form.querySelector('[type="submit"]');
    if (!submit.formNoValidate && !form.reportValidity()) return;
    const status = form.querySelector('.status');
    form.dataset.busy = 'true';
    submit.disabled = true;
    setStatus(status, pending);
    try {
      const message = await work(new FormData(form), submit);
      setStatus(status, message || '');
    } catch (error) {
      setStatus(status, error.message, true);
      if (onError) onError(error, status);
    } finally {
      delete form.dataset.busy;
      submit.disabled = false;
    }
  });
}
export function facts(items) {
  return `<dl class="facts">${items.map(([key,value]) => `<div><dt>${esc(key)}</dt><dd>${esc(value ?? '—')}</dd></div>`).join('')}</dl>`;
}
export function verdict(decision) {
  const cls = {GO:'go',NO_GO:'stop',INSUFFICIENT_EVIDENCE:'warn'}[decision] || 'warn';
  return `<strong class="verdict ${cls}">${esc(window.VerityCore.decisionLabel(decision))}</strong>`;
}
export function bandMethod(band) {
  if (!band) return '';
  return `<details><summary>Arrival band · ${esc(band.name)}</summary>
    ${facts([...(band.n == null ? [] : [['Calibration trips',band.n]]),
      ...(band.k == null ? [] : [['Calibration quantile',band.k]]), ['Applied multiplier',band.k_applied]])}
    <p>${esc(band.note)}</p>${band.sources?.length ? '<p>' + esc(band.sources.join(', ')) + '</p>' : ''}</details>`;
}
function thresholdTable(points) {
  return `<details class="threshold"><summary>Battery thresholds along the route (${points.length})</summary>
    <div class="data-scroll" tabindex="0" aria-label="Battery thresholds"><table><thead><tr><th>Distance</th><th>Expected battery</th><th>Divert below</th></tr></thead>
    <tbody>${points.map(p=>`<tr><td>${esc(p.km)} km</td><td>${esc(p.expected_soc_pct)}%</td><td>${esc(p.bingo_soc_pct)}%</td></tr>`).join('')}</tbody></table></div></details>`;
}
export function stopsAndThresholds(data) {
  const stops = data.stops || [];
  return `<h4>Charging stops</h4>${stops.length ? `<ol class="stops">${stops.map(s =>
    `<li><strong>${esc(s.name)}</strong><span>${esc(s.km_along_route)} km · ${esc(s.charge_minutes)} min${s.if_needed ? ' · if needed' : ''}</span></li>`).join('')}</ol>` :
    '<p class="muted">No charging stops listed.</p>'}${data.bingo_line?.length ? thresholdTable(data.bingo_line) : ''}`;
}
export const releaseLink = id => id ? `<a class="text-link" href="#release/${idPath(id)}">Open release and driver message</a>` : '';
export function release(plan, title, showLink = true) {
  const labels = {fastest:'Fastest', fewest_stops:'Fewest stops', safest:'Safest'};
  const name = title || (plan.labels || []).map(label=>labels[label] || label).join(' / ') || 'Trip decision';
  return `<article class="release-card"><div class="card-head"><h3>${esc(name)}</h3>${verdict(plan.decision)}</div>
    <p>${esc(plan.refusal_reason || plan.reason)}</p>${plan.meets_target === false ? '<p class="warning">Below the requested confidence target.</p>' : ''}
    ${facts([['Trip confidence',window.VerityCore.confidenceText(plan)],['Trip time',`${plan.route.duration_min} min`],['Distance',`${plan.route.distance_km} km`],['Median arrival battery',`${plan.arrival_soc.p50}%`],['Arrival band (p10–p90)',`${plan.arrival_soc.p10}–${plan.arrival_soc.p90}%`]])}
    ${plan.is_simulated ? '<p class="source-note">Includes simulated inputs.</p>' : ''}
    ${window.VerityCore.illustrativeBlocks(plan)}${bandMethod(plan.band_method)}${stopsAndThresholds(plan)}${showLink ? releaseLink(plan.plan_id) : ''}</article>`;
}
// Keep rows as label/value pairs so long charger IDs and report notes wrap on narrow screens.
export function records(rows, empty) {
  if (!rows?.length) return `<p class="muted">${esc(empty)}</p>`;
  const valueText = value => {
    if (value == null) return '—';
    if (Array.isArray(value)) return value.map(valueText).join(', ');
    if (typeof value === 'object') return Object.entries(value).map(([k,v])=>`${k.replaceAll('_',' ')}: ${valueText(v)}`).join(' · ');
    return String(value);
  };
  return rows.map(row=>`<dl class="record">${row.is_simulated || row.includes_simulated_reports ? '<div><dt>Evidence</dt><dd>Includes reports (simulated)</dd></div>' : ''}${Object.entries(row).filter(([key])=> !['is_simulated','includes_simulated_reports'].includes(key)).map(([key,v])=>
    `<div><dt>${esc(key.replaceAll('_',' '))}</dt><dd>${esc(valueText(v))}</dd></div>`).join('')}</dl>`).join('');
}
export function paginate(root, load, draw, size = 25) {
  let offset = 0;
  async function show(next) {
    root.innerHTML = '<p role="status">Loading…</p>';
    try {
      const rows = await load(next, size + 1);
      offset = next;
      root.innerHTML = '<div class="page-rows"></div><nav class="pager" aria-label="List pages"></nav>';
      draw(root.querySelector('.page-rows'), rows.slice(0, size));
      const pager = root.querySelector('.pager');
      for (const [label, target, visible] of [['Previous',offset - size,offset > 0],['Next',offset + size,rows.length > size]]) {
        if (!visible) continue;
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'button button-outline'; b.textContent = label;
        b.onclick = () => show(target); pager.append(b);
      }
    } catch (error) {
      root.innerHTML = `<p class="status error" role="status">${esc(error.message)}</p><button type="button" class="button button-outline">Retry</button>`;
      root.querySelector('button').onclick = () => show(next);
    }
  }
  show(0);
  return () => show(offset);
}

// Draws a charger neighbourhood as an SVG graph. Pure functions: nothing here touches the network or the DOM,
// and every label comes from the API response (no invented names, no invented real/simulated claims).
//
// Layered left to right so nothing can overlap: what reports on the charger and plans through it, then the charger,
// then its site / operator / nearest substation, then other chargers that share that substation. Rows have fixed
// spacing, labels sit above their node (up to two lines) and edges are curves between neighbouring columns.
import {esc} from './ui.js';

export const COLUMN_GAP = 260, ROW_GAP = 64, PAD_X = 140, PAD_TOP = 64, PAD_BOTTOM = 40, MAX_ROWS = 12, LABEL_CHARS = 30;
const ORDER = ['Charger', 'Site', 'Operator', 'Substation', 'Plan', 'Source', 'Channel', 'Ring'];
const KIND = {Charger: 'Charger', Site: 'Physical site', Operator: 'Operator', Substation: 'Nearest substation',
  Plan: 'Trip plan', Source: 'Reporting source', Channel: 'Report channel', Ring: 'Flagged ring'};
const DECISION = {GO: 'GO', NO_GO: 'NO-GO', INSUFFICIENT_EVIDENCE: 'Not enough evidence'};
const ROLE = {STOPS_AT: 'stops here', BACKUP_FOR: 'backup stop', AVOIDED: 'avoided'};
const COLUMNS = {Channel: 0, Ring: 0, Source: 1, Plan: 1, Site: 3, Operator: 3, Substation: 3};
const HEADING = ['Channel and ring', 'Plans and reports', 'This charger', 'Site, operator, grid', 'Nearby chargers'];

export const isSimulated = item => item?.props?.is_simulated === true;
const key = id => id.slice(id.indexOf(':') + 1);

function sourceText(k) {
  if (k.startsWith('ring-attack-')) return `Ring source ${k.slice('ring-attack-'.length)}`;
  if (k.startsWith('demo-user-')) return `Demo user ${k.slice('demo-user-'.length)}`;
  if (k.startsWith('user-')) return 'Signed-in driver';
  if (k.startsWith('feed:repair-')) return 'Operator repair';
  if (k === 'driver:driver_voice') return 'Driver voice report';
  if (k === 'driver:driver_voice_scripted') return 'Scripted voice report (demo)';
  return k;
}

// What a person should read on a node. ctx.role: plan id -> how it uses this charger; ctx.km: substation id -> distance.
export function nodeText(node, byId, ctx = {}) {
  const p = node.props || {}, k = key(node.id);
  if (node.label === 'Charger') {
    const raw = (p.name || '').trim(), cleaned = raw.replace(/\s*\(OpenStreetMap\)\s*$/i, '').trim();
    if (!raw) return p.operator ? `${p.operator} · ${k}` : `Charger ${k}`;
    return cleaned !== raw || /^EV charging station$/i.test(cleaned) ? `${cleaned || 'Charger'} · ${k}` : raw;
  }
  if (node.label === 'Operator') return `Operator: ${k}`;
  if (node.label === 'Site') { const home = byId.get('Charger:' + k); return home ? `Site of ${nodeText(home, byId)}` : 'Physical site'; }
  if (node.label === 'Substation') return ctx.km?.get(node.id) != null ? `Nearest substation · ${Math.round(Number(ctx.km.get(node.id)) * 10) / 10} km` : 'Nearest substation';
  if (node.label === 'Plan') {
    const role = ctx.role?.get(node.id);
    return `${DECISION[p.decision] || 'Plan'} for ${p.vehicle_id || k}${role ? ` · ${role}` : ''}`;
  }
  if (node.label === 'Channel') return `Channel: ${k.replace(/_/g, ' ')}`;
  if (node.label === 'Ring') return 'Flagged ring';
  if (node.label === 'Source') return sourceText(k);
  return k;
}

// Up to two lines of at most `chars` characters, broken at spaces; the rest is cut with an ellipsis.
export function wrap(text, chars = LABEL_CHARS) {
  const words = text.split(/\s+/).filter(Boolean), lines = [''];
  for (const word of words) {
    const line = lines[lines.length - 1];
    if (!line) lines[lines.length - 1] = word;
    else if ((line + ' ' + word).length <= chars) lines[lines.length - 1] = line + ' ' + word;
    else lines.push(word);
  }
  const shown = lines.slice(0, 2).map(l => l.length > chars ? l.slice(0, chars - 1) + '…' : l);
  if (lines.length > 2) shown[1] = shown[1].replace(/…?$/, '…');
  return shown;
}

function columnOf(node, focal) {
  return node.id === focal ? 2 : node.label === 'Charger' ? 4 : COLUMNS[node.label] ?? 3;
}

// Deterministic: the same response always draws the same picture. Returns positions and the size of the drawing.
export function layout(data) {
  const focal = 'Charger:' + data.charger_id, hidden = new Map(), columns = new Map();
  const rank = n => [ORDER.indexOf(n.label), n.id];
  const sorted = [...data.nodes].sort((a, b) => {
    const [la, ia] = rank(a), [lb, ib] = rank(b);
    return la - lb || (ia < ib ? -1 : ia > ib ? 1 : 0);
  });
  for (const node of sorted) {
    const col = columnOf(node, focal);
    if (!columns.has(col)) columns.set(col, []);
    columns.get(col).push(node);
  }
  const used = [...columns.keys()].sort((a, b) => a - b), shown = new Map();
  for (const col of used) {
    const all = columns.get(col);
    if (all.length > MAX_ROWS) {
      hidden.set(col, all.length - (MAX_ROWS - 1));
      shown.set(col, [...all.slice(0, MAX_ROWS - 1), {id: `more:${col}`, label: 'More', props: {count: all.length - (MAX_ROWS - 1)}}]);
    } else shown.set(col, all);
  }
  const rows = Math.max(1, ...[...shown.values()].map(list => list.length));
  const height = Math.max(300, PAD_TOP + PAD_BOTTOM + rows * ROW_GAP), mid = PAD_TOP + (height - PAD_TOP - PAD_BOTTOM) / 2;
  const width = PAD_X * 2 + (used.length - 1) * COLUMN_GAP, pos = new Map(), headings = [];
  used.forEach((col, index) => {
    const list = shown.get(col), x = PAD_X + index * COLUMN_GAP;
    headings.push({x, text: HEADING[col]});
    list.forEach((node, i) => pos.set(node.id, [x, Math.round(mid + (i - (list.length - 1) / 2) * ROW_GAP)]));
  });
  return {pos, width, height, hidden, headings, nodes: [...shown.values()].flat()};
}

// Real versus simulated, counted from the response itself.
export function summary(data) {
  const reports = data.edges.filter(e => e.type === 'REPORTED');
  const simulatedReports = reports.filter(isSimulated).length;
  const sources = data.nodes.filter(n => n.label === 'Source');
  return {reports: {real: reports.length - simulatedReports, simulated: simulatedReports},
    sources: {real: sources.length - sources.filter(isSimulated).length, simulated: sources.filter(isSimulated).length},
    plans: data.nodes.filter(n => n.label === 'Plan').length,
    chargers: data.nodes.filter(n => n.label === 'Charger').length};
}

export function legend(data) {
  const present = ORDER.filter(label => data.nodes.some(n => n.label === label));
  return `<ul class="graph-legend" aria-label="Legend">${present.map(label =>
    `<li><span class="swatch node-${label.toLowerCase()}" aria-hidden="true"></span>${esc(KIND[label])}</li>`).join('')}
    <li><span class="swatch swatch-sim" aria-hidden="true"></span>Dashed = simulated</li></ul>`;
}

// Labels for every node, unique on screen: a repeated name gets its id (chargers) or a number (everything else).
export function labels(data, nodes = data.nodes) {
  const byId = new Map(data.nodes.map(n => [n.id, n])), focal = 'Charger:' + data.charger_id;
  const role = new Map(), km = new Map();
  for (const e of data.edges) {
    if (e.to === focal && ROLE[e.type]) role.set(e.from, ROLE[e.type]);
    if (e.from === focal && e.type === 'NEAREST_SUBSTATION' && e.props?.distance_km != null) km.set(e.to, e.props.distance_km);
  }
  const text = new Map(nodes.filter(n => n.label !== 'More').map(n => [n.id, nodeText(n, byId, {role, km})]));
  const seen = new Map();
  for (const [id, label] of text) seen.set(label, (seen.get(label) || []).concat(id));
  for (const [label, ids] of seen) {
    if (ids.length < 2) continue;
    ids.forEach((id, i) => text.set(id, id.startsWith('Charger:') || id.startsWith('Plan:') ? `${label} · ${key(id)}`
      : id.startsWith('Source:user-') ? `${label} ${key(id).slice(5, 9)}` : `${label} (${i + 1})`));
  }
  for (const n of nodes) if (n.label === 'More') text.set(n.id, `+${n.props.count} more (see the tables below)`);
  return text;
}

function edgePath(a, b) {
  const [x1, y1] = a, [x2, y2] = b, mid = Math.round((x1 + x2) / 2);
  return x1 === x2 ? `M${x1} ${y1} C${x1 + 70} ${y1} ${x1 + 70} ${y2} ${x2} ${y2}` : `M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`;
}

export function graphSvg(data) {
  const view = layout(data), text = labels(data, view.nodes), focal = 'Charger:' + data.charger_id;
  const edges = data.edges.filter(e => view.pos.has(e.from) && view.pos.has(e.to));
  const named = edges.length <= 16;
  const heads = view.headings.map(h => `<text class="col-head" x="${h.x}" y="26" text-anchor="middle">${esc(h.text)}</text>`).join('');
  const lines = edges.map(e => {
    const a = view.pos.get(e.from), b = view.pos.get(e.to), sim = isSimulated(e);
    return `<g><path class="edge${sim ? ' sim' : ''}" d="${edgePath(a, b)}" fill="none"><title>${esc(e.type)}${sim ? ' (simulated)' : ''}</title></path>${
      named ? `<text class="edge-label" x="${Math.round((a[0] + b[0]) / 2)}" y="${Math.round((a[1] + b[1]) / 2) - 3}" text-anchor="middle">${esc(e.type.replace(/_/g, ' ').toLowerCase())}</text>` : ''}</g>`;
  }).join('');
  const dots = view.nodes.map(n => {
    const [x, y] = view.pos.get(n.id), label = text.get(n.id), sim = isSimulated(n), isFocal = n.id === focal, r = isFocal ? 20 : 14;
    const shown = wrap(sim ? `${label} · simulated` : label), up = r + 9;
    const action = n.label === 'Charger' && !isFocal ? ` tabindex="0" role="button" data-charger="${esc(key(n.id))}" aria-label="Explore from ${esc(label)}"` : '';
    const tspans = shown.map((line, i) => `<tspan x="0" dy="${i === 0 ? -(shown.length - 1) * 14 : 14}">${esc(line)}</tspan>`).join('');
    return `<g class="node node-${n.label.toLowerCase()}${sim ? ' sim' : ''}${isFocal ? ' focal' : ''}" transform="translate(${x} ${y})"${action}>
      <circle r="${r}"/><text y="${-up}" text-anchor="middle">${tspans}</text>
      <title>${esc(KIND[n.label] || n.label)}: ${esc(label)}${sim ? ' (simulated)' : ''}</title></g>`;
  }).join('');
  const s = summary(data), home = data.nodes.find(n => n.id === focal);
  const alt = `Graph of ${data.nodes.length} nodes and ${data.edges.length} relationships around ${home ? text.get(focal) : data.charger_id}. ${s.reports.simulated} simulated and ${s.reports.real} real reports.`;
  return `<div class="graph-scroll" role="region" tabindex="0" aria-label="Charger graph, scroll sideways if it does not fit"><svg class="graph-view" viewBox="0 0 ${view.width} ${view.height}" style="min-width:${Math.round(view.width * 0.8)}px" role="img" aria-label="${esc(alt)}">${heads}${lines}${dots}</svg></div>`;
}

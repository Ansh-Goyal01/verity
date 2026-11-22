// Draws a charger neighbourhood as an SVG graph. Pure functions: nothing here touches the network or the DOM,
// and every label comes from the API response (no invented names, no invented real/simulated claims).
import {esc} from './ui.js';

export const WIDTH = 760, HEIGHT = 520;
const ORDER = ['Charger', 'Site', 'Operator', 'Substation', 'Plan', 'Source', 'Channel', 'Ring'];
const KIND = {Charger: 'Charger', Site: 'Physical site', Operator: 'Operator', Substation: 'Nearest substation',
  Plan: 'Trip plan', Source: 'Reporting source', Channel: 'Report channel', Ring: 'Flagged ring'};
const DECISION = {GO: 'GO', NO_GO: 'NO-GO', INSUFFICIENT_EVIDENCE: 'Not enough evidence'};

export const isSimulated = item => item?.props?.is_simulated === true;
const key = id => id.slice(id.indexOf(':') + 1);
const short = (text, max = 24) => text.length > max ? text.slice(0, max - 1) + '…' : text;

// What a person should read on a node: the directory name or operator, never a bare id when a name exists.
export function nodeText(node, byId) {
  const p = node.props || {}, k = key(node.id);
  if (node.label === 'Charger') return p.name || p.operator || `Charger ${k}`;
  if (node.label === 'Operator') return k;
  if (node.label === 'Site') { const home = byId.get('Charger:' + k); return home ? `Site of ${nodeText(home, byId)}` : 'Physical site'; }
  if (node.label === 'Substation') return 'Nearest substation';
  if (node.label === 'Plan') return `${DECISION[p.decision] || 'Plan'} · ${p.vehicle_id || k}`;
  if (node.label === 'Ring') return 'Flagged ring';
  return k;
}

// Breadth-first distance from the focal charger, so related things sit on rings around it.
function distances(nodes, edges, focal) {
  const next = new Map(nodes.map(n => [n.id, []]));
  for (const e of edges) { next.get(e.from)?.push(e.to); next.get(e.to)?.push(e.from); }
  const dist = new Map([[focal, 0]]), queue = [focal];
  while (queue.length) {
    const at = queue.shift();
    for (const to of next.get(at) || []) if (!dist.has(to)) { dist.set(to, dist.get(at) + 1); queue.push(to); }
  }
  const far = Math.max(0, ...dist.values()) + 1;
  for (const n of nodes) if (!dist.has(n.id)) dist.set(n.id, far);
  return dist;
}

// Deterministic radial layout: the same response always draws the same picture.
export function layout(data, width = WIDTH, height = HEIGHT) {
  const focal = 'Charger:' + data.charger_id, dist = distances(data.nodes, data.edges, focal);
  const rings = Math.max(1, ...dist.values()), reach = Math.min(width, height) / 2 - 44, cx = width / 2, cy = height / 2;
  const order = n => [ORDER.indexOf(n.label), n.id];
  const sorted = [...data.nodes].sort((a, b) => {
    const [la, ia] = order(a), [lb, ib] = order(b);
    return la - lb || (ia < ib ? -1 : ia > ib ? 1 : 0);
  });
  const placed = new Map();
  for (let ring = 0; ring <= rings; ring++) {
    const here = sorted.filter(n => dist.get(n.id) === ring);
    here.forEach((n, i) => {
      if (ring === 0) { placed.set(n.id, [cx, cy]); return; }
      const angle = -Math.PI / 2 + (2 * Math.PI * i) / here.length + ring * 0.35, radius = (reach * ring) / rings;
      placed.set(n.id, [Math.round(cx + radius * Math.cos(angle) * (width / height) * 0.82),
        Math.round(cy + radius * Math.sin(angle))]);
    });
  }
  return placed;
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

export function graphSvg(data) {
  const byId = new Map(data.nodes.map(n => [n.id, n])), at = layout(data), focal = 'Charger:' + data.charger_id;
  const labelled = data.edges.length <= 24;
  const lines = data.edges.filter(e => at.has(e.from) && at.has(e.to)).map(e => {
    const [x1, y1] = at.get(e.from), [x2, y2] = at.get(e.to), sim = isSimulated(e);
    return `<g><line class="edge${sim ? ' sim' : ''}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"><title>${esc(e.type)}${sim ? ' (simulated)' : ''}</title></line>${
      labelled ? `<text class="edge-label" x="${Math.round((x1 + x2) / 2)}" y="${Math.round((y1 + y2) / 2) - 3}">${esc(e.type.replace(/_/g, ' ').toLowerCase())}</text>` : ''}</g>`;
  }).join('');
  const dots = data.nodes.map(n => {
    const [x, y] = at.get(n.id), text = nodeText(n, byId), sim = isSimulated(n), isFocal = n.id === focal, r = isFocal ? 22 : 15;
    const action = n.label === 'Charger' && !isFocal ? ` tabindex="0" role="button" data-charger="${esc(key(n.id))}" aria-label="Inspect ${esc(text)}"` : '';
    return `<g class="node node-${n.label.toLowerCase()}${sim ? ' sim' : ''}${isFocal ? ' focal' : ''}" transform="translate(${x} ${y})"${action}>
      <circle r="${r}"/><text y="${r + 14}" text-anchor="middle">${esc(short(text))}</text>
      <title>${esc(KIND[n.label] || n.label)}: ${esc(text)}${sim ? ' (simulated)' : ''}</title></g>`;
  }).join('');
  const s = summary(data);
  const alt = `Graph of ${data.nodes.length} nodes and ${data.edges.length} relationships around ${nodeText(byId.get(focal) || {id: focal, label: 'Charger'}, byId)}. ${s.reports.simulated} simulated and ${s.reports.real} real reports.`;
  return `<svg class="graph-view" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="${esc(alt)}">${lines}${dots}</svg>`;
}

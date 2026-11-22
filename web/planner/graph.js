import {api, accountApi, hasSession, keyedWrite} from './client.js';
import {esc, facts, idPath, numberInput, onSubmit, statusBox} from './ui.js';
import {graphSvg, legend, nodeText, summary} from './graph_view.js';

// Named chargers from the public directory (frozen Delhi-NCR ids), so the demo starts from real operators.
const EXAMPLES = [['CH-0015', 'Tata'], ['CH-0045', 'Statiq'], ['CH-0021', 'Tesla Supercharger'],
  ['CH-0022', 'Yuma'], ['CH-0025', 'Mercedes-Benz']];

function table(title, headings, rows, expanded = false) {
  return `<details${expanded ? ' open' : ''}><summary>${esc(title)} (${rows.length})</summary><div class="data-scroll" tabindex="0" aria-label="${esc(title)}"><table>
    <thead><tr>${headings.map(cell=>`<th scope="col">${esc(cell)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
}

// What is real and what is not, counted from the response itself (never asserted).
export function provenance(data) {
  const s = summary(data), many = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const lines = [`Real: ${many(s.chargers, 'charger')} from the public directory (OpenStreetMap, Open Charge Map) with their physical site, operator and nearest-substation links (proximity only, not known to feed it).`];
  if (s.plans) lines.push(`Engine output: ${many(s.plans, 'trip plan')} that include this charger.`);
  lines.push(`Reports: ${s.reports.real} real, ${s.reports.simulated} simulated.${s.reports.real ? '' : ' No real driver report is linked to this charger yet.'}`);
  return `<ul class="graph-note">${lines.map(line => `<li>${esc(line)}</li>`).join('')}</ul>`;
}

export function neighborhood(data) {
  const byId = new Map(data.nodes.map(n => [n.id, n])), home = byId.get('Charger:' + data.charger_id);
  return `<section class="sheet"><h2>${esc(home ? nodeText(home, byId) : data.charger_id)} <small>${esc(data.charger_id)}</small></h2>
    ${provenance(data)}${graphSvg(data)}${legend(data)}
    <p class="graph-hint">Select another charger in the graph to explore from it.</p>
    ${facts([['Depth',data.depth],['Backend',data.backend],['Query time',`${data.query_ms} ms`]])}
    ${table('Nodes',['ID','Type','Name'],data.nodes.map(n=>[n.id,n.label,n.props?.name]))}
    ${table('Relationships',['From','Relationship','To'],data.edges.map(e=>[e.from,e.type,e.to]))}
    <details><summary>${data.executed ? 'Executed query' : 'Equivalent graph query'}</summary><pre>${esc(data.cypher)}</pre></details></section>`;
}
export function ringResult(data) {
  const trust = row => [window.VerityCore.pct(row.p_works),row.n_reports,row.trust_level];
  return `<h2>Ring detection (simulated)</h2><p>${esc(data.charger_name)} · ${esc(data.charger_id)}</p>
    ${facts([['Detected',data.ring.found ? 'Yes' : 'No'],['Sources caught',`${data.ring.attack_sources_caught} of ${data.n_sources}`],['Reports added',data.reports_added],['Backend',data.ring.backend]])}
    ${table('Charger trust',['Stage','Chance charger works','Reports','Trust'],[
      ['Before',...trust(data.trust_before)],['After detection',...trust(data.trust_after)],
      ['Without detection',...trust(data.trust_without_ring_detection)]],true)}
    <p>${esc(data.limit.text)}</p>`;
}
function reportForm() {
  return hasSession()
    ? `<form class="sheet live-report"><h3>Add a real report</h3><p>Your report joins the graph as a real, non-simulated source, and the charger's trust updates.</p>
        <div class="form-actions"><button class="button button-outline" type="submit" value="working">Working</button>
        <button class="button button-outline" type="submit" value="not_working">Not working</button></div>${statusBox()}</form>`
    : '<p class="live-report">Sign in to add a real report and watch it join the graph. <a href="#account">Account sign-in</a></p>';
}
export function graph(root) {
  root.innerHTML = `<form class="sheet"><label>Charger ID<input name="charger" value="${EXAMPLES[0][0]}" maxlength="80" required></label>
    <div class="chips" role="group" aria-label="Example chargers"><span>Try:</span>${EXAMPLES.map(([id,name]) =>
      `<button class="button button-outline" type="button" data-example="${esc(id)}">${esc(name)}</button>`).join('')}</div>
    <div class="fields"><label>Neighborhood depth<select name="depth"><option value="1">1 · Charger audit</option><option value="2" selected>2 · Include related chargers and rings</option></select></label>
    ${numberInput('sources','Simulated sources',6,3,12)}</div>
    <div class="form-actions"><button class="button button-primary" type="submit" value="inspect" formnovalidate>Inspect neighborhood</button>
      <button class="button button-outline" type="submit" value="run">Run simulation</button>
      <button class="button button-outline" type="submit" formnovalidate value="reset">Reset demo reports</button></div>${statusBox()}</form>
    <p>Running or resetting simulated reports requires an administrator account. <a href="#account">Account sign-in</a></p>
    <div class="results" aria-live="polite"></div>`;
  const form = root.querySelector('form'), result = root.querySelector('.results');
  const inspect = form.querySelector('button[value="inspect"]');
  const open = id => { form.elements.charger.value = id; form.requestSubmit(inspect); };
  form.addEventListener('click', event => { const chip = event.target.closest('[data-example]'); if (chip) open(chip.dataset.example); });
  result.addEventListener('click', event => { const node = event.target.closest('[data-charger]'); if (node) open(node.dataset.charger); });
  result.addEventListener('keydown', event => {
    const node = event.target.closest?.('[data-charger]');
    if (node && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); open(node.dataset.charger); }
  });
  async function show(charger, depth) {
    const data = await api('/graph/neighborhood/' + idPath(charger) + '?depth=' + encodeURIComponent(depth));
    result.innerHTML = neighborhood(data) + reportForm();
    const report = result.querySelector('form.live-report');
    if (report) onSubmit(report, async (_, submit) => {
      const me = await accountApi('/me');
      await keyedWrite(me.profile.id, '/reports/quick', {charger_id: charger, status: submit.value});
      await show(charger, depth);
      result.querySelector('form.live-report .status').textContent = 'Report recorded. The graph above now includes it as a real report.';
    }, 'Recording your report…', (error, status) => {
      if (error.status === 429) status.textContent = 'You already reported a different status for this charger recently. Try again in a few minutes.';
    });
  }
  onSubmit(form, async (data,submit) => {
    const charger = data.get('charger').trim();
    result.replaceChildren();
    if (submit.value !== 'reset' && !charger) throw new Error('Enter the charger ID.');
    if (submit.value !== 'inspect' && !hasSession()) throw new Error('Sign in as an administrator to use the simulation.');
    if (submit.value === 'run') {
      const answer = await accountApi('/demo/ring-attack',{method:'POST',body:{charger_id:charger,n_sources:Number(data.get('sources'))}});
      const hood = await api('/graph/neighborhood/' + idPath(answer.charger_id) + '?depth=2');
      result.innerHTML = ringResult(answer) + neighborhood(hood);
    } else if (submit.value === 'reset') {
      const answer = await accountApi('/demo/ring-attack/reset',{method:'POST'});
      result.innerHTML = `<h2>Demo reset (simulated)</h2>${facts([['Reports removed',answer.reports_removed],['Demo reports left',answer.attack_reports_left],['Demo sources left',answer.attack_sources_left]])}`;
    } else {
      await show(charger, data.get('depth'));
    }
  }, 'Contacting the graph…', (error,status) => {
    if (error.status === 403) status.textContent = error.code === 'demo_disabled' || /switched off/i.test(error.message)
      ? 'The simulation is switched off on this deployment.' : 'An administrator account is required.';
    if (error.status === 503) status.textContent = 'The graph or account service is unavailable. Use the deployed site and retry.';
  });
}

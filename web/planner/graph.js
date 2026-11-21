import {api, accountApi, hasSession} from './client.js';
import {esc, facts, idPath, numberInput, onSubmit, statusBox} from './ui.js';

function table(title, headings, rows, expanded = false) {
  return `<details${expanded ? ' open' : ''}><summary>${esc(title)} (${rows.length})</summary><div class="data-scroll" tabindex="0" aria-label="${esc(title)}"><table>
    <thead><tr>${headings.map(cell=>`<th scope="col">${esc(cell)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
}
export function neighborhood(data) {
  return `<h2>${esc(data.charger_id)} neighborhood${data.is_simulated ? ' (simulated)' : ''}</h2>
    ${facts([['Depth',data.depth],['Backend',data.backend],['Query time',`${data.query_ms} ms`]])}
    ${table('Nodes',['ID','Type','Name'],data.nodes.map(n=>[n.id,n.label,n.props?.name]))}
    ${table('Relationships',['From','Relationship','To'],data.edges.map(e=>[e.from,e.type,e.to]))}
    <details><summary>${data.executed ? 'Executed query' : 'Equivalent graph query'}</summary><pre>${esc(data.cypher)}</pre></details>`;
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
export function graph(root) {
  root.innerHTML = `<form class="sheet"><label>Charger ID<input name="charger" value="CH-0003" maxlength="80" required></label>
    <div class="fields"><label>Neighborhood depth<select name="depth"><option value="1">1 · Charger audit</option><option value="2" selected>2 · Include related chargers and rings</option></select></label>
    ${numberInput('sources','Simulated sources',6,3,12)}</div>
    <div class="form-actions"><button class="button button-primary" type="submit" value="inspect" formnovalidate>Inspect neighborhood</button>
      <button class="button button-outline" type="submit" value="run">Run simulation</button>
      <button class="button button-outline" type="submit" formnovalidate value="reset">Reset demo reports</button></div>${statusBox()}</form>
    <p>Running or resetting simulated reports requires an administrator account. <a href="#account">Account sign-in</a></p>
    <div class="results" aria-live="polite"></div>`;
  const result = root.querySelector('.results');
  onSubmit(root.querySelector('form'), async (data,submit) => {
    const charger = data.get('charger').trim();
    result.replaceChildren();
    if (submit.value !== 'reset' && !charger) throw new Error('Enter the charger ID.');
    if (submit.value !== 'inspect' && !hasSession()) throw new Error('Sign in as an administrator to use the simulation.');
    if (submit.value === 'run') {
      const answer = await accountApi('/demo/ring-attack',{method:'POST',body:{charger_id:charger,n_sources:Number(data.get('sources'))}});
      result.innerHTML = ringResult(answer);
    } else if (submit.value === 'reset') {
      const answer = await accountApi('/demo/ring-attack/reset',{method:'POST'});
      result.innerHTML = `<h2>Demo reset (simulated)</h2>${facts([['Reports removed',answer.reports_removed],['Demo reports left',answer.attack_reports_left],['Demo sources left',answer.attack_sources_left]])}`;
    } else {
      result.innerHTML = neighborhood(await api('/graph/neighborhood/' + idPath(charger) + '?depth=' + encodeURIComponent(data.get('depth'))));
    }
  }, 'Contacting the graph…', (error,status) => {
    if (error.status === 403) status.textContent = error.code === 'demo_disabled' || /switched off/i.test(error.message)
      ? 'The simulation is switched off on this deployment.' : 'An administrator account is required.';
    if (error.status === 503) status.textContent = 'The graph or account service is unavailable. Use the deployed site and retry.';
  });
}

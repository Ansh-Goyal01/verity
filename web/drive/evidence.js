(() => {
  'use strict';
  const service = window.VerityTrip;
  const esc = window.VerityCore.escapeHtml;
  const repo = 'https://github.com/Ansh-Goyal01/verity/blob/1198474b9e26e355020d324ab89466d12aaed057/';
  const source = (path, label) => `<a class="text-link" href="${repo + path}" target="_blank" rel="noopener">${label}</a>`;
  const percent = n => (Number(n) * 100).toLocaleString('en-IN', { maximumFractionDigits: 1 }) + '%';
  function evidence(root) {
    const exp = window.VERITY_SNAPSHOT.results.e4;
    const rates = Object.keys(exp.scenarios);
    root.innerHTML = `<div class="work-head"><div><p class="eyebrow signal">Evidence</p><h1 tabindex="-1">Does it change the outcome?</h1>
      <p class="work-subtitle">Compare two dispatch policies, then inspect the field evidence behind the model.</p></div></div>
      <section class="proof-panel"><div class="proof-head"><div><p class="eyebrow">Charger outage experiment · Simulated</p><h2>Stranded trips</h2></div>
      <label>Charger outage rate<select id="outage-rate">${rates.map((k,i) => `<option value="${i}"${i===rates.length-1?' selected':''}>${esc(k)}</option>`).join('')}</select></label></div>
      <div id="outage-comparison" aria-live="polite"></div>${source('results/e4_dispatch_vs_fixed_simulated.json','View experiment data')}</section>
      <section id="field-ledger" class="proof-panel" aria-live="polite"><h2>Field evidence</h2><p>Loading measured results…</p></section>
      <details class="proof-panel engine-fold" id="engine-explainer"><summary>How the engine decides</summary>
      <ol><li><strong>Learn the vehicle.</strong> Logged trips update the estimate of usable battery capacity.</li>
      <li><strong>Forecast the road.</strong> Grade, payload, speed and climate control change the energy required.</li>
      <li><strong>Check the stops.</strong> The graph links reports, chargers, sites and backups that may share a failure.</li>
      <li><strong>Make the call.</strong> The engine checks the confidence target and the evidence supporting each stop.</li></ol>
      ${source('docs/ENGINE_NOTES.md','Read the engine notes')}</details>`;
    const chooser = root.querySelector('#outage-rate');
    function chart() {
      const row = exp.scenarios[rates[Number(chooser.value)]];
      const block = (title, stranded, rate, ci, cls) => `<div class="experiment-arm ${cls}"><h3>${title}</h3><div class="proof-number">${stranded}<small> / ${row.trips}</small></div>
        <p>${percent(rate)} stranded · 95% interval ${percent(ci[0])}–${percent(ci[1])}</p>
        <div class="trip-matrix" aria-hidden="true">${Array.from({length:row.trips},(_,i)=>i < stranded ? '<i class="stranded"></i>' : '<i></i>').join('')}</div></div>`;
      root.querySelector('#outage-comparison').innerHTML = `<div class="proof-arms">${block('Fixed charging stops',row.stranded_fixed,row.stranding_rate_fixed,row.ci95_fixed,'fixed')}
        ${block('Verity dispatch',row.stranded_dispatch,row.stranding_rate_dispatch,row.ci95_dispatch,'verity')}</div>
        <p class="matrix-key">Each square represents one simulated trip. Filled squares mark stranded trips.</p>`;
    }
    chooser.onchange = chart; chart();
    const box = root.querySelector('#field-ledger');
    service.ledger().then(ledger => {
      if (!box.isConnected) return;
      const bmw = ledger.real.bmw_i3_held_out, trips = ledger.real.trips, real = ledger.real.charger_reports;
      const stat = (label, n) => `<div><dt>${esc(label)}</dt><dd>${esc(String(n ?? 'Unavailable'))}</dd></div>`;
      box.innerHTML = `<p class="eyebrow">Recorded data · Evidence ledger</p><h2>What has been measured?</h2>
        <dl class="field-stats">${stat('Held-out BMW i3 trips in Germany',bmw.trips_scored)}${stat('Usable Indian trips',trips.usable)}${stat('Dated Indian charger reports',real.total_real_reports)}</dl>
        ${bmw.status === 'DONE' ? `<p>The median battery-drop error was ${esc(bmw.soc_drop_median_abs_error_pp)} percentage points over ${esc(bmw.km_scored)} km. The nominal 95% band covered ${percent(bmw.soc_drop_band95_coverage)} of these trips.</p>` : ''}
        <p>${esc(ledger.driver_real.reason)}</p><div class="proof-links">${source('results/bmw_i3_heldout.json','View held-out results')}<a class="text-link" data-app-link href="../logger/index.html">Record a field trip</a></div>`;
    }).catch(e => { if (box.isConnected) box.textContent = 'Could not load the evidence ledger. ' + e.message; });
  }

  function reportReceipt(answer) {
    const { trust_before: before, trust_after: after } = answer.report;
    return `<div class="report-receipt"><h3>${answer.is_simulated ? 'Practice result' : 'Observation recorded'}</h3>
      <p>Reports ${esc(before.n_reports)} to ${esc(after.n_reports)} · Trust ${esc(before.trust_level)} to ${esc(after.trust_level)}</p>
      <p>Plans checked ${esc(answer.plans_checked)} · Pending ${esc(answer.plans_pending)}.</p>
      ${window.VerityCore.illustrativeBlocks(answer.report)}${answer.plans.map(p => `<p>${esc(p.vehicle_id)} · ${esc(p.decision_before)} to ${esc(p.decision_after)}${p.changed?.length ? ' · ' + esc(p.changed.join(', ')) + ' changed' : ''}</p>`).join('')}
      ${answer.replan_failed ? '<p>The report was stored. A plan recheck failed and needs another attempt.</p>' : ''}</div>`;
  }
  function reports(root, row) {
    const practice = service.cases.practice;
    if (row.charger_id === practice.report.charger_id) {
      const details = document.createElement('details'); details.className = 'audit-fold';
      details.innerHTML = `<summary>Rehearse a broken-charger report</summary><p>This saved exercise shows what one simulated report did to V12’s cached plan.</p>
        <button class="button button-outline" type="button">Show the report outcome</button><div role="status"></div>`;
      details.querySelector('button').onclick = () => { details.querySelector('[role="status"]').innerHTML = reportReceipt(practice); };
      root.append(details);
    }
    if (!service.live) return;
    const form = document.createElement('form'); form.className = 'trip-form observation-form';
    form.innerHTML = `<h3>Report a real observation</h3><label>What did you observe?<select name="status" required><option value="">Choose a status</option>
      <option value="working">Working</option><option value="broken">Broken</option><option value="blocked">Blocked</option><option value="occupied">Occupied</option><option value="no_power">No power</option></select></label>
      <label class="consent"><input type="checkbox" name="consent" required>I saw this at the charger and agree to add it to its report history.</label>
      <button type="submit" class="button button-outline">Review observation</button><div class="report-confirm" role="status"></div>`;
    const status = form.querySelector('.report-confirm'), select = form.elements.status, consent = form.elements.consent;
    let posting = false;
    form.oninput = () => status.replaceChildren();
    form.onsubmit = e => {
      e.preventDefault(); if (posting || !form.reportValidity()) return;
      status.innerHTML = `<p>Record “${esc(select.value.replace('_',' '))}” for ${esc(row.name)} (${esc(row.charger_id)})?</p><button type="button" class="button instrument-button">Confirm and submit</button>`;
      status.querySelector('button').onclick = async () => {
        if (posting || !form.reportValidity()) return;
        posting = true; select.disabled = consent.disabled = form.querySelector('[type="submit"]').disabled = true;
        const chosen = select.value;
        status.textContent = 'Submitting the observation…';
        try {
          const answer = await window.VerityAPI.confirmReport({ charger_id: row.charger_id, status: chosen, consent: true, confirmed: true,
            language: 'en-IN', speak: false, from_real_person: true });
          status.innerHTML = reportReceipt(answer);
          root.dispatchEvent(new CustomEvent('charger-reported'));
          window.dispatchEvent(new CustomEvent('verity:report-recorded'));
        } catch(e) { status.textContent = e.message; posting = false; select.disabled = consent.disabled = form.querySelector('[type="submit"]').disabled = false; }
      };
    };
    root.append(form);
  }
  async function charger(root, id) {
    const row = service.saved.chargers.chargers.find(c => c.charger_id === id);
    root.innerHTML = `<a class="text-link" href="#/workspace/chargers">Charger directory</a><div class="work-head"><div>
      <p class="eyebrow">Charger evidence · ${esc(id)}</p><h1 tabindex="-1">${esc(row?.name || 'Charger details')}</h1></div></div><div class="charger-proof proof-panel" aria-live="polite">Loading charger history…</div><div class="proof-panel charger-report"></div>`;
    const content = root.querySelector('.charger-proof'), report = root.querySelector('.charger-report');
    async function refresh() {
      try {
        const view = await service.charger(id);
        if (!content.isConnected) return;
        if (!view) { content.textContent = 'This charger is missing from the saved directory.'; report.hidden = true; return; }
        const trust = view.trust;
        const observations = view.edges.filter(e => e.type === 'REPORTED' && e.to === 'Charger:' + id)
          .sort((a,b) => String(b.props.t).localeCompare(String(a.props.t)));
        const site = view.edges.find(e => e.type === 'AT' && e.from === 'Charger:' + id)?.to;
        const siblings = site ? view.edges.filter(e => e.type === 'AT' && e.to === site && e.from !== 'Charger:' + id) : [];
        content.innerHTML = `<p class="eyebrow">${service.live ? 'Connected' : 'Saved'} charger graph</p><h2>${esc(trust.trust_level)} trust</h2>
          <p>${esc(trust.n_reports)} reports · ${trust.days_since_last == null ? 'No dated report' : 'Latest report ' + esc(trust.days_since_last) + ' days before evaluation'}</p>
          ${siblings.length ? `<p>Shares a site with ${siblings.map(e=>esc(e.from.replace('Charger:',''))).join(', ')}. These plugs can fail together.</p>` : ''}
          <h3>Recent reports</h3>${observations.length ? `<ul>${observations.map(e=>`<li>${esc(e.props.t?.slice(0,10))} · ${esc(e.props.status.replace('_',' '))} · ${e.props.is_simulated?'Simulated':'Field report'}</li>`).join('')}</ul>` : '<p>No recent reports in this graph.</p>'}`;
      } catch(e) { if (content.isConnected) content.textContent = e.message; }
    }
    report.addEventListener('charger-reported', refresh);
    if (row) reports(report, row);
    report.hidden = !report.childElementCount;
    await refresh();
  }
  window.VerityProof = { evidence, charger };
})();

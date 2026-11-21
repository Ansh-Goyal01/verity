(() => {
  'use strict';
  const service = window.VerityTrip;
  const { saved, cases } = service;
  const root = document.querySelector('#trip');
  const esc = window.VerityCore.escapeHtml;
  const actions = { CONTINUE: 'Continue on plan', DIVERT: 'Divert to the backup', SLOW_DOWN: 'Slow down' };
  const verdicts = { GO: ['Release the trip', 'Take the ride', 'go'], NO_GO: ['Hold departure', 'Hold this ride', 'stop'],
    INSUFFICIENT_EVIDENCE: ['More evidence needed', 'More evidence needed', 'warn'] };
  let active = null;
  let generation = 0;
  let disposeVoice = null;
  let mapObserver = null;

  function leave() {
    generation++;
    service.stopSpeech();
    mapObserver?.disconnect();
    mapObserver = null;
    disposeVoice?.();
    disposeVoice = null;
  }

  const option = (value, label, selected) => `<option value="${esc(String(value))}"${selected ? ' selected' : ''}>${esc(String(label))}</option>`;
  const fact = (label, value) => `<div><dt>${esc(label)}</dt><dd>${esc(String(value))}</dd></div>`;
  const chargerLink = (id, name) => `<a class="charger-ref" href="#/workspace/chargers/${encodeURIComponent(id)}">${esc(name)} <small>${esc(id)}</small></a>`;
  function numberField(name, value, { min = 0, max = 100, step = 1, required = true } = {}) {
    return `<input type="number" name="${name}" min="${min}" max="${max}" step="${step}" ${required ? 'required' : ''} value="${value}" inputmode="decimal">`;
  }
  function batteryField(values, value) {
    return service.live ? numberField('soc', value) : `<select name="soc">${values.map(v => option(v, v + '%', v === value)).join('')}</select>`;
  }
  function tabs(mode) {
    return `<nav class="trip-modes" aria-label="Trip Check views">${[['intercity', 'Intercity'], ['cab', 'Cab ride'], ['current', 'Current trip']].map(([id, label]) => {
      const path = id === 'current' && active?.plan.decision === 'GO' ? currentPath(active) : '#/trip/' + id;
      return `<a href="${path}" ${id === mode ? 'aria-current="page"' : ''}>${label}</a>`;
    }).join('')}<a data-app-link href="planner.html">Custom planner</a></nav>`;
  }
  function currentPath(trip) { return `#/trip/current/${trip.request.vehicle_id}/${trip.request.start_soc_pct}`; }
  function frame(mode, title, intro) {
    root.innerHTML = `<div class="shell trip-shell"><div class="trip-heading"><div><p class="eyebrow signal">Trip Check</p><h1 tabindex="-1">${title}</h1><p>${intro}</p></div>${tabs(mode)}</div><div id="trip-layout" class="trip-layout"></div></div>`;
    return root.querySelector('#trip-layout');
  }
  function source() { return `<span class="run-source">${service.source}</span>`; }
  function line(stops) {
    return `<ol class="trip-line" aria-label="Itinerary">${stops.map(([label, place]) =>
      `<li><span>${esc(label)}</span><strong>${esc(place)}</strong></li>`).join('')}</ol>`;
  }
  function adjustment(plan) {
    if (!plan.levers?.length) return '';
    return `<section class="ticket-section"><h3>What changes the decision?</h3><div class="lever-options">${plan.levers.map(row => {
      const minutes = row.time_cost_min;
      const time = minutes < 0 ? `${-minutes} min saved` : minutes > 0 ? `${minutes} min longer` : 'Same journey time';
      return `<details><summary>${esc(row.label)}</summary><p>${window.VerityCore.pct(row.new_confidence)} modeled confidence · ${esc(plan.n_runs)} runs · ${time}</p></details>`;
    }).join('')}</div></section>`;
  }
  function slipHeader(plan, cab) {
    const [dispatchTitle, cabTitle, kind] = verdicts[plan.decision];
    return `<div class="ticket-top"><span>${cab ? 'Ride decision' : 'Dispatch decision'}${plan.is_simulated ? ' (simulated)' : ''}</span>${source()}</div>
      <div class="decision-title" data-tone="${kind}"><span class="status-rule"></span><h2 tabindex="-1">${cab ? cabTitle : dispatchTitle}</h2></div>
      <p class="ticket-reason">${esc(plan.reason || plan.refusal_reason)}</p>`;
  }
  function audioControls(plan) {
    if (!plan.speech?.['en-IN'] && !plan.speech?.['hi-IN']) return '';
    return `<div class="ticket-audio"><span>Hear the decision</span>${[['en-IN', 'English'], ['hi-IN', 'हिन्दी']].filter(([lang]) => plan.speech[lang]).map(([lang, name]) =>
      `<button type="button" data-speak="${lang}" lang="${lang}">${name}</button>`).join('')}<span role="status" data-audio-feedback></span></div>`;
  }
  function attachAudio(result, plan) {
    for (const button of result.querySelectorAll('[data-speak]')) button.onclick = async () => {
      try { await service.speak(plan.speech[button.dataset.speak], button.dataset.speak); }
      catch { result.querySelector('[data-audio-feedback]').textContent = plan.speech[button.dataset.speak]; }
    };
  }
  function showPlan(result, trip) {
    const { plan } = trip;
    const arrival = plan.arrival_soc;
    const held = plan.decision === 'INSUFFICIENT_EVIDENCE';
    result.innerHTML = slipHeader(plan, false) + `<dl class="ticket-facts">${fact('Trip confidence', window.VerityCore.confidenceText(plan))}
      ${plan.band_method ? fact('Arrival band', plan.band_method.name + (plan.band_method.n == null ? '' : ' · ' + plan.band_method.n + ' calibration trips')) : ''}
      ${fact('Target', Math.round(trip.request.target_confidence * 100) + '%')}
      ${held ? fact('Release', 'On hold') : fact('Arrival battery range', `${arrival.p10}–${arrival.p90}%`)}</dl>
      ${!held ? '<p class="ticket-note">The arrival range spans the middle 80% of simulated arrivals.</p>' : ''}
      ${held ? `<p class="ticket-note">${esc(plan.refusal_reason)}</p>` : ''}
      ${plan.stops.length ? `<section class="ticket-section"><h3>Planned stop</h3>${chargerLink(plan.stops[0].charger_id, plan.stops[0].name)}</section>` : ''}
      ${adjustment(plan)}<details class="audit-fold"><summary>Why this plan?</summary><div data-why aria-live="polite"></div></details>
      ${plan.decision === 'GO' ? `<a class="button instrument-button" href="${currentPath(trip)}">Open current trip</a>` : ''}
      ${audioControls(plan)}`;
    const proof = result.querySelector('.audit-fold');
    proof.ontoggle = async () => {
      if (!proof.open || proof.dataset.loaded) return;
      proof.dataset.loaded = 'loading';
      const body = proof.querySelector('[data-why]');
      body.textContent = 'Reading the plan’s charger links…';
      try {
        const graph = await service.why(trip);
        const links = graph.edges.filter(e => e.from === 'Plan:' + plan.plan_id && ['STOPS_AT', 'BACKUP_FOR', 'AVOIDED'].includes(e.type));
        const roles = { STOPS_AT: 'Planned stop', BACKUP_FOR: 'Backup', AVOIDED: 'Avoided' };
        body.innerHTML = `<p>${plan.n_runs.toLocaleString('en-IN')} simulated runs · ${esc(graph.backend)} graph</p>` +
          (links.length ? `<ul>${links.map(e => {
            const id = e.to.replace(/^Charger:/, '');
            const row = saved.chargers.chargers.find(c => c.charger_id === id);
            return `<li>${roles[e.type]} · ${chargerLink(id, row?.name || id)}${e.props?.reason ? ' · ' + esc(e.props.reason) : ''}</li>`;
          }).join('')}</ul>` : `<p>${esc(plan.refusal_reason || 'No charging stop is attached to this plan.')}</p>`);
      } catch (e) { delete proof.dataset.loaded; body.textContent = e.message; }
    };
    attachAudio(result, plan);
  }
  function showRide(result, ride) {
    const after = ride.after_drop;
    const rate = ride.earning_rate;
    result.innerHTML = slipHeader(ride, true) + `<dl class="ticket-facts">${fact('Ride confidence', window.VerityCore.confidenceText(ride))}
      ${fact('Arrival at drop', ride.ride.drop_soc.p50 + '%')}${fact('Ride distance', ride.ride.distance_km + ' km')}</dl>` +
      (after?.charger ? `<section class="ticket-section"><h3>Charge after drop-off</h3>${chargerLink(after.charger.charger_id, after.charger.name)}
      <dl class="ticket-facts paired">${fact('Expected time lost', after.expected_lost_min + ' min')}${fact('Earnings opportunity', '₹' + after.expected_lost_rs)}</dl>
      <p class="ticket-note">Modeled time away from paid rides at ${rate.is_example ? 'an example rate of ' : ''}₹${esc(rate.rs_per_hour)}/hour.</p>
      ${after.backups?.length ? `<details class="audit-fold"><summary>Backup chargers</summary><ul>${after.backups.map(c => `<li>${chargerLink(c.charger_id, c.name)}</li>`).join('')}</ul></details>` : ''}</section>` : '')
      + (ride.considered?.length ? `<details class="audit-fold"><summary>Compare nearby chargers</summary><ul>${ride.considered.map(c =>
        `<li>${chargerLink(c.charger_id, c.name)} · ${esc(c.road_km)} km<p>${esc(c.note)}</p></li>`).join('')}</ul></details>` : '')
      + adjustment(ride) + audioControls(ride);
    attachAudio(result, ride);
  }
  function waiting(result, message) { result.setAttribute('aria-busy', 'true'); result.innerHTML = `<p class="ticket-message" role="status">${esc(message)}</p>`; }
  function note(result, message) { result.removeAttribute('aria-busy'); result.innerHTML = `<p class="ticket-message" role="status">${esc(message)}</p>`; }
  function inputs(mode, id) {
    const cab = mode === 'cab', base = saved.driverDemo.request;
    const car = service.vehicle(id) || service.vehicle('V12');
    const layout = frame(mode, cab ? 'Is this fare worth taking?' : 'Can this EV make it?', cab ?
      'Check the pickup, the drop-off and the next working charger.' : 'See what your vehicle can reach on the Noida–Agra corridor.');
    const stops = cab ? [['Start', 'Noida Sector 62'], ['Pickup', 'Noida Sector 18'], ['Drop-off', 'IGI Terminal 3']] :
      [['From', saved.scenario.booking.origin.name], ['To', saved.scenario.booking.destination.name]];
    layout.innerHTML = `<section class="config-panel"><div class="config-top"><h2>${cab ? 'Ride details' : 'Departure'}</h2><span>${cab ? 'Delhi NCR' : 'Yamuna Expressway'}</span></div>
      ${line(stops)}<form class="trip-form" id="check-form"><div class="input-pair">${cab ? '' :
        `<label>Vehicle<select name="vehicle">${['V12', 'V07', 'V19'].map(v => option(v, v, v === car.vehicle_id)).join('')}</select></label>`}
      <label>Starting battery (%)${batteryField(cab ? [15,25,45,70] : [40,62,70,85], cab ? base.soc_pct : car.soc_pct)}</label>
      ${cab ? `<label>Earnings (₹/hour)${service.live ? numberField('rate', '', { min: 1, max: 10000, step: 'any', required: false }) :
        '<select name="rate"><option value="">60 · Example</option><option value="120">120</option></select>'}</label>` : ''}</div>
      ${cab && service.live ? `<details class="route-inputs"><summary>Edit Delhi NCR coordinates</summary>${['current','pickup','drop'].map((key, i) =>
        `<fieldset><legend>${['Current position','Pickup','Drop-off'][i]}</legend><div class="input-pair">${['lat','lon'].map(axis =>
          `<label>${axis === 'lat' ? 'Latitude' : 'Longitude'}${numberField(key + '_' + axis, base[key][axis], { min: axis === 'lat' ? -90 : -180, max: axis === 'lat' ? 90 : 180, step: 'any' })}</label>`).join('')}</div></fieldset>`).join('')}</details>` : ''}
      <p class="form-context">${cab ? 'V12 · 1 passenger · AC on' : '4 passengers · AC on · 90% confidence target'}</p>
      <button class="button instrument-button" type="submit">${cab ? 'Check this ride' : 'Check this trip'}</button></form>
      ${cab ? '<div id="ride-voice"></div>' : ''}</section>
      <section class="decision-ticket" id="trip-result" aria-label="Trip decision" aria-live="polite"></section>`;
    const form = layout.querySelector('form');
    const result = layout.querySelector('#trip-result');
    function invalidate(event) {
      generation++;
      form.querySelector('button').disabled = false;
      if (event.target.name === 'vehicle') form.elements.soc.value = service.vehicle(event.target.value).soc_pct;
      if (cab && event.target.name.includes('_')) {
        layout.querySelectorAll('.trip-line strong').forEach((el, i) => {
          const key = ['current','pickup','drop'][i], lat = form.elements[key + '_lat'].value, lon = form.elements[key + '_lon'].value;
          el.textContent = Number(lat) === base[key].lat && Number(lon) === base[key].lon ? stops[i][1] : lat + ', ' + lon;
        });
      }
      note(result, 'Inputs changed. Run the check to see the new decision.');
    }
    form.addEventListener('input', invalidate);
    form.addEventListener('change', invalidate);
    async function run(userAction) {
      const token = ++generation;
      waiting(result, 'Checking the route and its reachable chargers…');
      const fields = new FormData(form);
      form.querySelector('button').disabled = true;
      try {
        const soc = Number(fields.get('soc'));
        if (cab) {
          const point = key => service.live ? { lat: Number(fields.get(key + '_lat')), lon: Number(fields.get(key + '_lon')) } : base[key];
          const ride = await service.ride({ soc, rate: fields.get('rate') ? Number(fields.get('rate')) : null,
            current: point('current'), pickup: point('pickup'), drop: point('drop') });
          if (token !== generation) return;
          showRide(result, ride);
        } else {
          const trip = await service.dispatch(fields.get('vehicle'), soc);
          if (token !== generation) return;
          if (trip.plan.decision === 'GO') active = trip;
          showPlan(result, trip);
        }
        if (userAction) result.querySelector('h2').focus();
      } catch (e) { if (token === generation) note(result, e.message); }
      finally { if (token === generation) { result.removeAttribute('aria-busy'); form.querySelector('button').disabled = false; } }
    }
    form.onsubmit = event => { event.preventDefault(); run(true); };
    if (cab) disposeVoice = window.VerityRideVoice.mount(layout.querySelector('#ride-voice'), (ride) => {
      generation++;
      const loc = service.saved.driverDemo.request;
      // The confirmed voice contract uses the committed route, one passenger and the example earning rate.
      form.elements.soc.value = ride.soc;
      form.elements.rate.value = '';
      if (service.live) for (const key of ['current','pickup','drop']) for (const axis of ['lat','lon']) form.elements[key + '_' + axis].value = loc[key][axis];
      layout.querySelectorAll('.trip-line strong').forEach((el, i) => { el.textContent = stops[i][1]; });
      form.querySelector('button').disabled = false;
      result.removeAttribute('aria-busy'); showRide(result, ride.answer);
    });
    run(false);
  }

  async function current(id, soc) {
    const layout = frame('current', 'When the plan changes.', 'Try a battery reading or take a released plan through a simulated charger outage.');
    const token = ++generation;
    layout.innerHTML = '<p class="ticket-message" role="status">Opening the released trip…</p>';
    try {
      const trip = active?.request.vehicle_id === id && active.request.start_soc_pct === soc ? active : await service.dispatch(id, soc);
      if (token !== generation) return;
      if (trip.plan.decision !== 'GO') {
        layout.innerHTML = `<div class="config-panel"><h2>This trip is on hold</h2><p>${esc(trip.plan.reason)}</p><a class="button instrument-button" href="#/trip/intercity/${id}">Review departure</a></div>`;
        return;
      }
      active = trip;
      const place = trip.plan.stops[0];
      const readingEvents = trip.sample?.events || (saved.dispatch[id].plan_id === trip.plan.plan_id ? saved.replay[id].events : []);
      const startingReading = readingEvents.find(e => e.type === 'soc_reading' && e.km === cases.checkpoint.km)?.soc_pct ?? '';
      const routePlan = { ...trip.plan, route: trip.plan.route || saved.dispatch[id].route };
      layout.innerHTML = `<section class="config-panel"><div class="config-top"><h2>Current trip</h2><span>${esc(id)} · ${soc}% at departure</span></div>
        ${line([['From','Noida'],['To','Agra']])}<form id="reading-form" class="trip-form"><h3>Check a battery reading</h3>
        <p>At the ${cases.checkpoint.km} km checkpoint, enter the dashboard reading while stopped.</p>
        <label>Battery at checkpoint (%)${numberField('soc', startingReading)}</label>
        <button type="submit" class="button instrument-button">Update forecast</button></form>
        ${place ? `<div class="outage-control"><h3>Try a charger outage</h3><p>See the trip replay when the planned stop is reported broken.</p>
          <button type="button" data-outage class="button button-outline">Simulate charger outage</button></div>` : ''}</section>
        <div class="current-display"><figure class="current-route"><canvas role="img" aria-label="Noida–Agra route schematic"></canvas><figcaption>Route schematic · Noida–Agra</figcaption></figure>
        <section id="current-result" class="decision-ticket" aria-label="Driver instruction" aria-live="polite"></section></div>`;
      const result = layout.querySelector('#current-result'), form = layout.querySelector('#reading-form');
      const replayButton = layout.querySelector('[data-outage]');
      let request = 0;
      const canvas = layout.querySelector('.current-route canvas');
      let mapReading = {};
      function draw(reading, description) {
        mapReading = reading;
        canvas.setAttribute('aria-label', description);
        window.verityInstruments.drawJourney(canvas, routePlan, reading);
      }
      draw({}, 'Noida–Agra route and planned charging stops at departure.');
      if (window.ResizeObserver) {
        mapObserver = new ResizeObserver(() => window.verityInstruments.drawJourney(canvas, routePlan, mapReading));
        mapObserver.observe(canvas);
      }
      function display(title, message, facts, alternate, failed, tone = 'go') {
        result.innerHTML = `<div class="ticket-top"><span>Driver instruction</span>${source()}</div><div class="decision-title" data-tone="${tone}">
          <span class="status-rule"></span><h2 tabindex="-1" id="current-action">${esc(title)}</h2></div><p class="ticket-reason">${esc(message)}</p>
          <dl class="ticket-facts">${facts.map(([k,v]) => fact(k,v)).join('')}</dl>
          ${failed ? `<section class="ticket-section"><h3>Reported broken in replay</h3>${chargerLink(failed.charger_id, failed.name)}</section>` : ''}
          ${alternate ? `<section class="ticket-section"><h3>Use this backup</h3>${chargerLink(alternate.charger_id, alternate.name)}</section>` : ''}
          <a class="text-link" href="#/workspace/evidence">Inspect the evidence</a>`;
      }
      display('Ready to depart', trip.plan.reason, [['Vehicle', id], ['Starting battery', soc + '%'], ['Trip confidence', window.VerityCore.confidenceText(trip.plan)]]);
      async function update(kind) {
        const turn = ++request;
        const liveHere = () => token === generation && turn === request;
        const chosen = Number(form.elements.soc.value);
        waiting(result, kind === 'outage' ? 'Loading the engine’s charger-outage replay…' : 'Updating the forecast for this battery reading…');
        try {
          if (kind === 'outage') {
            const event = await service.outage(trip);
            if (!liveHere()) return;
            if (!event) { note(result, 'This plan has no charger outage in its replay. Try a battery update.'); return; }
            const { reading, answer, report } = event;
            const failed = trip.plan.stops.find(s => s.charger_id === report.charger_id);
            draw({ km: reading.km, forecast: answer, outage: true, failedId: report.charger_id },
              'Simulated charger outage at ' + reading.km + ' km. ' + answer.message);
            display(actions[answer.action], answer.message, [['Outage location', reading.km + ' km'], ['Battery', reading.soc_pct + '%'], ['Report', 'Simulated']],
              answer.divert_to, failed, answer.action === 'CONTINUE' ? 'go' : 'warn');
          } else {
            const answer = await service.forecast(trip, chosen);
            if (!liveHere()) return;
            draw({ km: answer.km_along_route, forecast: answer.divert_to ? answer : null },
              'Battery checkpoint at ' + answer.km_along_route + ' km. ' + answer.message);
            display(actions[answer.action], answer.message, [['Battery', chosen + '%'], ['Bingo Line', answer.bingo_soc_pct_here + '%'],
              ['Arrival range', `${answer.arrival_soc.p10}–${answer.arrival_soc.p90}%`]], answer.divert_to, null, answer.action === 'CONTINUE' ? 'go' : 'warn');
          }
          result.removeAttribute('aria-busy');
          result.querySelector('h2').focus();
        } catch (e) {
          if (!liveHere()) return;
          if (e.code === 'unknown_plan') { active = null; note(result, 'This plan expired. Open Trip Check and run the departure again.');
            const retry = document.createElement('a'); retry.href = '#/trip/intercity/' + id; retry.textContent = 'Recheck departure'; retry.className = 'button instrument-button'; result.append(retry); }
          else note(result, e.message);
        }
      }
      if (replayButton) replayButton.onclick = () => update('outage');
      form.onsubmit = e => { e.preventDefault(); update('reading'); };
    } catch (e) { if (token === generation) note(layout, e.message); }
  }
  function open(parts) {
    leave();
    const [, , page, id, value] = parts;
    if (page === 'current') {
      const car = service.vehicle(id) || (id ? null : service.vehicle(active?.request.vehicle_id || 'V12'));
      const battery = value === undefined ? (active?.request.vehicle_id === car?.vehicle_id ? active.request.start_soc_pct : car?.soc_pct) : Number(value);
      if (!car || !Number.isInteger(battery) || battery < 0 || battery > 100) {
        const layout = frame('current','Current trip','Choose a vehicle and battery to release a trip.');
        layout.innerHTML = '<a class="button instrument-button" href="#/trip">Open Trip Check</a>'; return;
      }
      current(car.vehicle_id, battery);
    } else inputs(page === 'cab' ? 'cab' : 'intercity', id);
  }
  window.addEventListener('verity:report-recorded', () => { active = null; });
  window.VerityTripView = { open, leave, chargerLink };
})();

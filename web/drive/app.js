(() => {
  'use strict';

  const snapshot = window.VERITY_SNAPSHOT;
  const data = { scenario: snapshot.api.scenario, releases: snapshot.api.dispatch, replay: snapshot.api.replay.V12,
    chargers: snapshot.api.chargers.chargers, experiment: snapshot.results.e4 };
  const instruments = window.verityInstruments;
  const flagship = data.releases.V12;
  const vehicleIds = Object.keys(data.releases);
  const { booking } = data.scenario;
  const events = data.replay.events;
  const diverted = events.find(event => event.action === 'DIVERT');
  const storyChapters = [0, 2, events.indexOf(diverted)];
  const target = probability(booking.target_confidence);
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const narrow = matchMedia('(max-width: 680px)');
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const work = $('#workspace-content');
  if (window.VerityAPI.isApiConnected()) $('#plan-entry').href = 'planner.html' + location.search;
  const driver = $('#driver');
  const modes = ['home', 'trip', 'workspace', 'driver'];
  const workTabs = ['dispatch', 'replay', 'chargers', 'evidence', 'engine'];
  const decision = {
    GO: ['Go', 'go', 'Backup within reach'],
    NO_GO: ['No-go', 'stop', 'Below confidence target'],
    INSUFFICIENT_EVIDENCE: ['Insufficient evidence', 'warn', 'Charger evidence needed']
  };
  const state = {
    mode: 'home',
    tab: 'dispatch',
    vehicle: 'V12',
    storyIndex: 0,
    forecastKm: 0,
    replayIndex: 0,
    replayTimer: null,
    workPlan: null,
    language: 'en',
    driverTab: 'route'
  };
  let hasRouted = false;

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }

  function probability(value) {
    return window.VerityCore.pct(value);
  }

  function pill(text, kind = 'neutral') {
    return '<span class="pill pill-' + kind + '">' + escapeHTML(text) + '</span>';
  }

  function statusBadge(release) {
    const [label, kind] = decision[release.decision];
    return pill(label, kind);
  }

  function metric(label, value, small = false) {
    return '<div class="metric"><dt>' + escapeHTML(label) + '</dt><dd' +
      (small ? ' class="metric-text"' : '') + '>' + escapeHTML(value) + '</dd></div>';
  }

  function vehiclePath(id) {
    return '#/workspace/dispatch/' + id;
  }

  function currentRelease() {
    return state.workPlan || data.releases[state.vehicle];
  }

  function routeName() {
    return booking.origin.name + ' → ' + booking.destination.name;
  }

  function initialiseHome() {
    $('#hero-release').innerHTML =
      '<div class="release-cell release-route"><span class="eyebrow">Simulated trip · V12</span>' +
      '<strong>' + escapeHTML(routeName()) + '</strong></div>' +
      '<div class="release-cell"><span class="eyebrow">Trip confidence · ' + escapeHTML(flagship.n_runs) + ' runs</span><div class="release-confidence"><strong>' +
      escapeHTML(flagship.confidence_display) + '</strong>' + statusBadge(flagship) + '</div></div>' +
      '<div class="release-cell" title="Bingo Line at departure"><span class="eyebrow">Bingo Line</span><strong>' +
      flagship.bingo_line[0].bingo_soc_pct + '%</strong></div><span class="release-arrow" aria-hidden="true">↗</span>';

    $('#home-fleet').innerHTML = vehicleIds.map(id => {
      const release = data.releases[id];
      const [, tone, summary] = decision[release.decision];
      const detail = release.refusal_reason ? 'Review the evidence' : window.VerityCore.confidenceText(release) + ' trip confidence';
      return '<a class="fleet-choice" data-decision="' + tone + '" href="#/trip/intercity/' + id +
        '"><span class="fleet-id-row"><strong class="fleet-id">' + id + '</strong>' + statusBadge(release) +
        '</span><span class="fleet-lede">' + summary + '</span><span class="fleet-detail"><span>' +
        escapeHTML(detail) + '</span><span aria-hidden="true">↗</span></span></a>';
    }).join('');
    const experiment = Object.entries(data.experiment.scenarios).at(-1);
    const [outage, arm] = experiment;
    $('#home-proof-context').textContent = 'At ' + outage.split(' ')[0] + ' charger outages, each policy faced the same ' + arm.trips + ' simulated trips.';
    $('#home-proof').innerHTML = '<span><b>' + arm.stranded_fixed + '</b> stranded with fixed stops</span><span><b>' + arm.stranded_dispatch + '</b> stranded with Verity</span><strong>Explore the experiment</strong>';
    $('#home-divert-name').textContent = diverted.divert_to.name;
    refreshStory(0);
    $$('.story-step').forEach(step => {
      $('button', step).addEventListener('click', () => {
        refreshStory(storyChapters[Number(step.dataset.stage)], true);
        if (narrow.matches) $('#story-visual').scrollIntoView({
          block: 'start',
          behavior: reduceMotion.matches ? 'instant' : 'smooth'
        });
      });
    });

    const reveal = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.remove('ready');
        reveal.unobserve(entry.target);
      }
    }, { threshold: .12 });
    for (const element of $$('.reveal')) {
      if (!reduceMotion.matches) element.classList.add('ready');
      reveal.observe(element);
    }

    const chapters = new IntersectionObserver(entries => {
      if (narrow.matches || state.mode !== 'home') return;
      const chapter = entries.find(entry => entry.isIntersecting);
      if (chapter) refreshStory(storyChapters[Number(chapter.target.dataset.stage)], true);
    }, { rootMargin: '-23% 0px -48% 0px' });
    $$('.story-step').forEach(step => chapters.observe(step));

    const scrub = $('#story-scrub');
    scrub.max = events.length - 1;
    scrub.addEventListener('input', () => refreshStory(Number(scrub.value)));
    $('.hero-scroll').addEventListener('click', () => refreshStory(0));
    initialiseHeroMotion();
  }

  function storyDetails(index) {
    const reading = readingAtEvent(index);
    if (!index) return {
      ...reading, chapter: 0, status: 'Go', kind: 'go',
      metrics: [
        ['Trip confidence', window.VerityCore.confidenceText(flagship)],
        ['Expected arrival', flagship.arrival_soc.p50 + '%'],
        ['Backup plan', 'Reachable', true]
      ]
    };
    if (!reading.outage && !reading.forecast) return {
      ...reading, chapter: 1, status: reading.margin >= 0 ? 'On plan' : 'Below Bingo Line',
      kind: reading.margin >= 0 ? 'go' : 'warn',
      metrics: [
        ['Battery reading', reading.soc + '%'],
        ['Bingo Line', reading.threshold + '%'],
        ['Margin', (reading.margin > 0 ? '+' : '') + reading.margin + ' pts']
      ]
    };
    return {
      ...reading, chapter: 2, status: reading.forecast ? 'Divert' : 'Charger offline',
      kind: reading.forecast ? 'stop' : 'warn',
      metrics: [
        ['Along the route', reading.km + ' km'],
        ['Planned charger', 'Outage', true],
        ['Next action', reading.forecast ? 'Use alternate' : 'Await update', true]
      ]
    };
  }

  function refreshStory(index, animate = false) {
    const changed = index !== state.storyIndex;
    state.storyIndex = index;
    const detail = storyDetails(index);
    const board = $('#story-visual');
    const badge = $('#story-status');
    board.dataset.chapter = detail.chapter;
    badge.textContent = detail.status;
    badge.className = 'pill pill-' + detail.kind;
    $('#story-metrics').innerHTML = '<dl class="story-readout">' +
      detail.metrics.map(args => metric(...args)).join('') + '</dl>';
    const comparison = $('#story-compare');
    const advice = $('#story-advice');
    comparison.hidden = detail.chapter === 2;
    advice.hidden = !comparison.hidden;
    if (advice.hidden) {
      $('#story-battery-fill').style.width = detail.soc + '%';
      $('#story-bingo-mark').style.left = detail.threshold + '%';
      $('#story-battery-caption').textContent = 'Battery ' + detail.soc + '%';
      $('#story-bingo-caption').textContent = 'Bingo Line ' + detail.threshold + '%';
    } else {
      advice.textContent = detail.forecast ? 'Alternate · ' + detail.forecast.divert_to.name :
        'A charger report arrived at ' + detail.km + ' km. Advance the replay to see the new route.';
    }
    const scrub = $('#story-scrub');
    scrub.value = index;
    scrub.setAttribute('aria-valuetext', eventLabel(events[index]));
    $('#story-location').textContent = detail.km + ' km';
    $$('.story-step').forEach(step => $('button', step).setAttribute('aria-pressed', Number(step.dataset.stage) === detail.chapter));

    if (detail.chapter === 0) return;
    const canvas = $('#story-map');
    instruments.drawJourney(canvas, flagship, detail);
    if (changed && animate && !reduceMotion.matches) {
      canvas.animate([{ opacity: .48 }, { opacity: .98 }], { duration: 420, easing: 'ease-out' });
    }
  }

  function initialiseHeroMotion() {
    const visual = $('#hero-visual');
    const hero = visual.closest('.hero');
    let scheduled = 0;

    function moveExposure() {
      scheduled = 0;
      if (state.mode !== 'home' || reduceMotion.matches || narrow.matches) return;
      const bounds = hero.getBoundingClientRect();
      if (bounds.bottom <= 0) return;
      const travel = Math.max(0, -bounds.top);
      visual.style.setProperty('--hero-offset', Math.min(34, travel * .062) + 'px');
      visual.style.setProperty('--hero-zoom', String(1.015 + Math.min(.14, travel * .00022)));
    }

    window.addEventListener('scroll', () => {
      if (state.mode === 'home' && !scheduled) scheduled = requestAnimationFrame(moveExposure);
    }, { passive: true });
    narrow.addEventListener?.('change', () => {
      if (narrow.matches) visual.removeAttribute('style');
    });
  }

  function workHeading(title, description, label = 'Simulated data', kicker = '') {
    return '<div class="work-head"><div>' +
      (kicker ? '<p class="eyebrow">' + escapeHTML(kicker) + '</p>' : '') +
      '<h1 tabindex="-1">' + escapeHTML(title) + '</h1>' +
      '<p class="work-subtitle">' + escapeHTML(description) + '</p></div>' +
      pill(label) + '</div>';
  }

  function vehiclePicker() {
    return '<nav class="vehicle-picker" aria-label="Select a vehicle">' + vehicleIds.map(id =>
      '<a class="vehicle-option" href="' + vehiclePath(id) + '" aria-current="' + (id === state.vehicle) +
        '"><strong>' + id + '</strong>' + (window.VerityTrip.live ? (id === state.vehicle && state.workPlan ? statusBadge(state.workPlan) : '') : statusBadge(data.releases[id])) + '</a>'
    ).join('') + '</nav>';
  }

  function schematic(id, caption) {
    return '<figure class="panel map-card"><div class="panel-top"><span class="eyebrow">' +
      escapeHTML(caption) + '</span><span class="eyebrow">' + flagship.route.distance_km + ' km</span></div>' +
      '<div class="map-wrap"><canvas id="' + id + '" class="map-canvas" role="img" ' +
      'aria-label="Schematic showing the Noida to Agra route, planned charging stops, and any marked alternate"></canvas>' +
      '<span class="map-label">Route schematic</span></div>' +
      '<figcaption class="map-card-legend"><span class="key-item key-line">Progress on trip route</span>' +
      '<span class="key-item" style="--key-color:var(--warn)">Planned charger</span>' +
      '<span class="key-item key-ring">Highlighted backup if available</span></figcaption></figure>';
  }

  function chargerPlans(release) {
    const stops = release.stops.map(stop => '<li><div><strong>' + escapeHTML(stop.name) +
      '</strong><small>' + stop.km_along_route + ' km · ' + stop.charge_minutes + ' min charge</small></div>' +
      pill(stop.evidence.trust_level + ' trust', stop.evidence.trust_level === 'high' ? 'go' : 'warn') + '</li>');
    const ranked = [...release.alternates].sort((left, right) => right.p_works * right.p_reach - left.p_works * left.p_reach);
    const alternate = ranked[0];
    if (alternate) stops.push('<li><div><strong>' + escapeHTML(alternate.name) + '</strong><small>Backup · ' +
      probability(alternate.p_reach) + ' reachable in ' + escapeHTML(release.n_runs) + ' simulated runs</small></div>' + pill('Backup', 'go') + '</li>');
    return '<section class="work-card panel"><h2>Charging plan</h2><ul class="charge-list">' + stops.join('') + '</ul></section>';
  }

  function dispatchPage(loaded = null) {
    if (window.VerityTrip.live && !loaded) {
      const id = state.vehicle;
      work.innerHTML = workHeading(routeName(), 'Loading the selected vehicle’s dispatch…', 'Connected engine') + vehiclePicker();
      const heading = work.firstElementChild;
      window.VerityTrip.dispatch(id, window.VerityTrip.vehicle(id).soc_pct).then(trip => {
        if (state.mode === 'workspace' && heading.isConnected) dispatchPage(trip.plan);
      }).catch(error => { if (heading.isConnected) $('.work-subtitle', heading).textContent = error.message; });
      return;
    }
    state.workPlan = loaded;
    const release = currentRelease();
    const car = data.scenario.vehicles.find(vehicle => vehicle.vehicle_id === state.vehicle);
    const header = workHeading(routeName(), 'Booking ' + booking.booking_id + ' · ' + booking.passengers +
      ' passengers · ' + target + ' confidence target', window.VerityTrip.source, 'Dispatch');
    if (release.refusal_reason) {
      work.innerHTML = header + vehiclePicker() +
        '<section class="panel refusal-card">' + statusBadge(release) + '<h2>Hold this release.</h2><p>' +
        escapeHTML(release.refusal_reason) + '</p><a class="text-link" style="margin-top:18px" href="#/workspace/chargers">Review charger evidence</a></section>';
      return;
    }

    work.innerHTML = header + vehiclePicker() + '<div class="dispatch-grid">' +
      '<section class="work-card panel release-card" aria-label="Release decision"><div class="release-top">' +
      '<span class="eyebrow">Release ' + escapeHTML(release.plan_id) + '</span>' + statusBadge(release) + '</div>' +
      '<div class="confidence">' + escapeHTML(release.confidence_display) + '</div>' +
      '<div class="confidence-caption">Trip confidence · ' + escapeHTML(release.n_runs) + ' simulated runs <b>· ' + target + ' target</b></div>' +
      '<div class="confidence-rule ' + (release.decision === 'NO_GO' ? 'is-stop' : '') + '" aria-hidden="true"><span style="width:' +
      probability(release.confidence) + '"></span><i style="left:' + target + '"></i></div>' +
      '<p class="explanation">' + escapeHTML(release.reason.replace(/^(GO|NO_GO) because /, '').replace(/^./, ch => ch.toUpperCase())) + '</p>' +
      '<dl class="work-metrics">' + metric('Start battery', car.soc_pct + '%') +
      metric('Arrival battery range (p10–p90)', release.arrival_soc.p10 + '–' + release.arrival_soc.p90 + '%') + '</dl></section>' +
      schematic('dispatch-map', 'Route and charging') +
      '<section class="work-card panel wide-panel"><div class="card-heading"><div><h2>Bingo Line</h2>' +
      '<p>Explore the minimum battery needed to stay on plan at each point along the route.</p></div></div>' +
      '<div class="chart-key"><span class="key-item key-line">Expected battery</span><span class="key-item key-dashed" style="--key-color:var(--stop)">Bingo Line</span>' +
      '<span class="key-item" style="--key-color:#5979b6">p10–p90 range</span></div>' +
      '<canvas id="forecast-chart" class="forecast-chart" role="img" aria-label="Forecast of battery level and Bingo Line by kilometre"></canvas>' +
      '<label class="chart-control">Route distance<input id="forecast-slider" type="range" min="0" max="' +
      (release.bingo_line.length - 1) + '" step="1" value="0"><output id="chart-readout" for="forecast-slider"></output></label></section>' +
      '<a class="button button-outline wide-panel" href="#/trip/intercity/' + state.vehicle + '">Edit departure in Trip Check</a>' + chargerPlans(release) +
      '<section class="work-card panel"><h2>Risk sources</h2><ul class="risk-list">' +
      release.risk_sources.map(risk => '<li>' + escapeHTML(risk.label) + '</li>').join('') + '</ul></section>' +
      (release.levers.length ? '<section class="work-card panel wide-panel"><h2>Try an adjustment</h2><p>Compare the saved what-if results.</p>' +
        release.levers.map((lever, index) => '<button type="button" class="button button-outline lever" data-lever="' + index +
          '" aria-pressed="false">' + escapeHTML(lever.label) + '</button>').join('') +
        '<div id="lever-result" class="lever-result" role="status"></div></section>' : '') +
      '<section class="work-card panel wide-panel"><h2>Energy breakdown</h2><ul class="charge-list">' +
      release.breakdown.map(item => '<li><strong>' + escapeHTML(item.label) + '</strong><span class="mono">' +
        (item.soc_pct > 0 ? '+' : '') + item.soc_pct + ' pts</span></li>').join('') + '</ul></section></div>';
    $('#forecast-slider').addEventListener('input', event => {
      state.forecastKm = release.bingo_line[Number(event.target.value)].km;
      refreshForecast();
    });
    $$('[data-lever]', work).forEach(button => button.addEventListener('click', () => {
      $$('[data-lever]', work).forEach(other => other.setAttribute('aria-pressed', other === button));
      const lever = release.levers[Number(button.dataset.lever)];
      $('#lever-result').innerHTML = '<p>What-if confidence <b>' + probability(lever.new_confidence) +
        '</b> · ' + escapeHTML(release.n_runs) + ' simulated runs · ' + (lever.time_cost_min < 0 ? -lever.time_cost_min + ' min saved' : lever.time_cost_min + ' min added') + '</p>';
    }));
    refreshForecast();
  }

  function refreshForecast() {
    const chart = $('#forecast-chart');
    if (!chart) return;
    const release = currentRelease();
    const reading = instruments.nearestReading(release, state.forecastKm);
    $('#chart-readout').textContent = reading.km + ' km · ' + reading.expected_soc_pct +
      '% expected · ' + reading.bingo_soc_pct + '% Bingo';
    $('#forecast-slider').setAttribute('aria-valuetext', $('#chart-readout').textContent);
    instruments.drawForecast(chart, release, state.forecastKm);
    instruments.drawJourney($('#dispatch-map'), release, { km: state.forecastKm });
  }

  function readingAtEvent(index) {
    const past = events.slice(0, index + 1);
    const point = past.findLast(event => event.type === 'soc_reading');
    const forecast = past.find(event => event.type === 'forecast');
    const outage = past.some(event => event.type === 'charger_report');
    const soc = point.soc_pct;
    const threshold = instruments.nearestReading(flagship, point.km).bingo_soc_pct;
    return { km: point.km, soc, threshold, margin: soc - threshold, forecast, outage };
  }

  function replayReading() {
    return readingAtEvent(state.replayIndex);
  }

  function formatReplayTime(seconds) {
    return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
      .map(value => String(value).padStart(2, '0')).join(':');
  }

  function eventLabel(event) {
    if (event.type === 'soc_reading') return event.soc_pct + '% battery at ' + event.km + ' km';
    if (event.type === 'charger_report') return 'Planned charger reported broken';
    return 'Divert to the alternate charger';
  }

  function replayPage() {
    work.innerHTML = workHeading('Trip replay', routeName() + ' · Vehicle V12 · Saved engine replay') +
      '<div class="dispatch-grid"><section class="work-card panel"><h2>Trip status</h2><div id="replay-body" style="margin-top:12px"></div>' +
      '<div class="chart-control"><label for="replay-slider">Replay position</label><input id="replay-slider" type="range" min="0" max="' +
      (events.length - 1) + '" step="1"></div>' +
      '<div class="actions" style="margin-top:10px"><button class="button button-primary" id="replay-play" data-replay="toggle"></button>' +
      '<button class="button button-outline" data-replay="divert">Jump to reroute</button></div></section>' +
      schematic('replay-map', 'Trip progress') +
      '<section class="work-card panel wide-panel"><h2>Recorded events</h2><ul class="replay-events">' +
      events.map((event, index) => '<li><button type="button" data-replay-index="' + index + '"><time>' +
        formatReplayTime(event.t_offset_s) + '</time>' + escapeHTML(eventLabel(event)) + '</button></li>').join('') +
      '</ul></section></div>';
    refreshReplay();
  }

  function refreshReplay() {
    const reading = replayReading();
    const replayBody = $('#replay-body');
    if (replayBody) {
      const tone = reading.forecast ? ' is-divert' : reading.outage ? ' is-warn' : '';
      const message = reading.forecast ? reading.forecast.message : reading.outage ? 'Planned charger reported broken' : 'Following the released plan';
      replayBody.innerHTML = '<div class="replay-status' + tone + '" role="status">' + escapeHTML(message) +
        '</div><dl class="work-metrics">' + metric('Battery reading', reading.soc + '%') +
        metric('Bingo Line at ' + reading.km + ' km', reading.threshold + '%') + '</dl>';
      $('#replay-play').textContent = state.replayTimer ? 'Pause' : state.replayIndex === events.length - 1 ? 'Replay from start' : 'Play replay';
      $('#replay-slider').value = state.replayIndex;
      $('#replay-slider').setAttribute('aria-valuetext', eventLabel(events[state.replayIndex]));
      $$('[data-replay-index]', work).forEach(btn => {
        btn.setAttribute('aria-current', Number(btn.dataset.replayIndex) === state.replayIndex ? 'step' : 'false');
      });
      instruments.drawJourney($('#replay-map'), flagship, reading);
    }
    if ($('#driver-status')) refreshDriverTrip(reading);
  }

  function haltReplay() {
    clearInterval(state.replayTimer);
    state.replayTimer = null;
  }

  function seekReplay(index) {
    haltReplay();
    state.replayIndex = index;
    refreshReplay();
  }

  function toggleReplay() {
    if (state.replayTimer) {
      haltReplay();
    } else {
        if (state.replayIndex === events.length - 1) state.replayIndex = 0;
      state.replayTimer = setInterval(() => {
        state.replayIndex += 1;
            if (state.replayIndex === events.length - 1) haltReplay();
        refreshReplay();
      }, 1200);
    }
    refreshReplay();
  }

  function chargersPage() {
    work.innerHTML = workHeading('Charger trust', 'Filter the directory by the strength of its simulated charger reports.') +
      '<div class="filter-bar"><label>Search chargers<input id="charger-search" type="search" placeholder="Name, operator or ID"></label>' +
      '<label>Trust level<select id="charger-trust"><option value="">All levels</option><option>high</option><option>medium</option>' +
      '<option>low</option><option>unknown</option></select></label></div><p id="charger-count" class="result-count" role="status"></p>' +
      '<div class="panel table-wrap" role="region" tabindex="0" aria-label="Charger directory"><table class="data-table"><thead><tr><th scope="col">Charger</th><th scope="col">Trust</th>' +
      '<th scope="col">Reports</th><th scope="col">Last report</th></tr></thead><tbody id="charger-rows"></tbody></table></div>';
    $('#charger-search').addEventListener('input', refreshChargers);
    $('#charger-trust').addEventListener('change', refreshChargers);
    state.directory = null;
    refreshChargers();
    if (window.VerityTrip.live) {
      const table = $('#charger-rows');
      $('#charger-count').textContent = 'Loading connected directory…';
      window.VerityAPI.getChargers({min_lat:27, min_lon:77, max_lat:29.5, max_lon:78.5}).then(answer => {
        if (table.isConnected) { state.directory = answer.chargers; refreshChargers(); }
      }).catch(error => { if (table.isConnected) $('#charger-count').textContent = 'Showing saved directory. ' + error.message; });
    }
  }

  function refreshChargers() {
    const query = $('#charger-search').value.trim().toLowerCase();
    const trust = $('#charger-trust').value;
    const directory = state.directory || data.chargers;
    const matches = directory.filter(row => (!trust || trust === row.trust_level) &&
      [row.name, row.operator, row.charger_id].some(value => String(value).toLowerCase().includes(query)));
    $('#charger-count').textContent = matches.length + ' of ' + directory.length + ' chargers';
    $('#charger-rows').innerHTML = matches.map(row =>
      '<tr><td><strong>' + escapeHTML(row.name) + '</strong><br><small class="eyebrow">' + escapeHTML(row.charger_id) +
      '</small></td><td>' + pill(row.trust_level, row.trust_level === 'high' ? 'go' : 'neutral') +
      '</td><td class="mono">' + row.n_reports + '</td><td>' +
      (row.days_since_last === null ? 'No reports' : row.days_since_last + ' d ago') + '</td></tr>'
    ).join('') || '<tr><td colspan="4">No chargers match these filters.</td></tr>';
  }

  const copy = {
    en: {
      mode: 'Driver mode', heading: 'On the road.', intro: 'See the next action and how much battery you have above the Bingo Line.',
      sim: 'Simulated', trip: 'Trip replay', ride: 'Ride check', route: 'Noida → Agra', battery: 'Battery', threshold: 'Bingo Line',
      continue: 'Continue on plan', above: 'pts above the Bingo Line', below: 'pts below the Bingo Line', at: 'At the Bingo Line',
      check: 'Check the backup plan', atLimit: 'Battery is at the Bingo Line', outage: 'Charger reported broken', ahead: 'Review the next replay event.',
      divert: 'Divert to the alternate', playback: 'Replay position', play: 'Play replay', pause: 'Pause', restart: 'Replay from start',
      jump: 'Jump to reroute', enter: 'Update battery reading', stopped: 'Enter a sample battery reading while stopped.',
      save: 'Update', distance: 'km travelled', airport: 'Delhi airport ride', pickup: 'Pickup', drop: 'Drop-off',
      rideInput: 'Starting battery', checkRide: 'Check this ride', takeRide: 'Take the ride', confidence: 'trip confidence',
      charge: 'Charge after drop-off', lostTime: 'Expected time lost', lostValue: 'Expected earnings lost',
      rate: 'Based on an example earning rate.', alternatives: 'Why skip the nearer charger?',
      nearer: 'A failure was reported at the nearer charger one day before this simulation.',
      logger: 'Open trip logger'
    },
    hi: {
      mode: 'ड्राइवर मोड', heading: 'सफ़र में।', intro: 'अगला कदम देखें और जानें कि बैटरी बिंगो लाइन से कितनी ऊपर है।',
      sim: 'सिमुलेशन', trip: 'सफ़र का रीप्ले', ride: 'राइड जाँचें', route: 'Noida → Agra', battery: 'बैटरी', threshold: 'बिंगो लाइन',
      continue: 'योजना के अनुसार चलें', above: 'अंक बिंगो लाइन से ऊपर', below: 'अंक बिंगो लाइन से नीचे', at: 'बैटरी बिंगो लाइन पर है',
      check: 'बैकअप योजना देखें', atLimit: 'बैटरी बिंगो लाइन पर है', outage: 'चार्जर खराब होने की रिपोर्ट', ahead: 'रीप्ले की अगली घटना देखें।',
      divert: 'बैकअप चार्जर की ओर जाएँ', playback: 'रीप्ले की स्थिति', play: 'रीप्ले चलाएँ', pause: 'रोकें', restart: 'फिर से चलाएँ',
      jump: 'रास्ता बदलने का क्षण', enter: 'बैटरी रीडिंग बदलें', stopped: 'रुककर नमूना बैटरी रीडिंग दर्ज करें।',
      save: 'अपडेट करें', distance: 'किमी तय किए', airport: 'दिल्ली एयरपोर्ट की राइड', pickup: 'पिकअप', drop: 'ड्रॉप',
      rideInput: 'शुरुआती बैटरी', checkRide: 'यह राइड जाँचें', takeRide: 'राइड लें', confidence: 'सफ़र का अनुमानित भरोसा',
      charge: 'ड्रॉप के बाद चार्ज करें', lostTime: 'समय का अनुमानित नुकसान', lostValue: 'कमाई का अनुमानित नुकसान',
      rate: 'कमाई की एक उदाहरण दर पर आधारित।', alternatives: 'नज़दीकी चार्जर क्यों छोड़ा?',
      nearer: 'सिमुलेशन से एक दिन पहले नज़दीकी चार्जर में खराबी की रिपोर्ट थी।',
      logger: 'ट्रिप लॉगर खोलें'
    }
  };

  function translation() {
    return copy[state.language];
  }

  function fieldIntro(kicker, title, intro, link, linkText) {
    return '<div class="field-intro"><p class="eyebrow signal">' + kicker + '</p><h1 tabindex="-1">' + title +
      '</h1><p>' + intro + '</p><a class="text-link" data-app-link href="' + link + '">' + linkText + ' ↗</a></div>';
  }

  function driverTrip() {
    const t = translation();
    return '<h2 class="phone-title">' + t.route + '</h2><div id="driver-status" class="driver-status" role="status"></div>' +
      '<dl class="driver-readings" id="driver-readings"></dl><label class="field-slider">' +
      '<span class="eyebrow">' + t.playback + '</span><input type="range" min="0" max="' +
      (events.length - 1) + '" step="1" id="driver-slider"><output id="driver-distance" for="driver-slider"></output></label>' +
      '<div class="actions"><button type="button" data-replay="toggle" class="button button-dark" id="driver-play"></button>' +
      '<button type="button" data-replay="divert" class="button field-secondary">' + t.jump + '</button></div>' +
      '<a class="text-link driver-check-link" href="#/trip/current/V12/62">' +
      (state.language === 'hi' ? 'बैटरी से नया अनुमान जाँचें' : 'Recalculate with a battery reading') + '</a>';
  }

  function driverPage() {
    const t = translation();
    const activeTrip = state.driverTab === 'route';
    driver.innerHTML = '<div class="shell field-grid" lang="' + state.language + '">' +
      '<div>' + fieldIntro(t.mode, t.heading, t.intro, '../logger/index.html', t.logger) +
      '<a class="text-link full-driver-link" data-app-link href="driver.html">' +
      (state.language === 'hi' ? 'ड्राइवर कंसोल खोलें' : 'Open full driver console') + '</a></div>' +
      '<div class="phone"><div class="phone-head">' + pill(t.sim) + '<div class="language-tabs" aria-label="Language">' +
      '<button type="button" data-language="en" lang="en" aria-label="English" aria-pressed="' + (state.language === 'en') +
      '">EN</button><button type="button" data-language="hi" lang="hi" aria-pressed="' + (state.language === 'hi') +
      '">हिंदी</button></div></div><nav class="field-tabs" aria-label="' + t.mode + '">' +
      '<button type="button" data-driver-tab="route" aria-pressed="' + activeTrip + '">' + t.trip + '</button>' +
      '<a href="#/trip/cab">' + t.ride + '</a></nav>' + driverTrip() + '</div></div>';
    refreshReplay();
  }

  function refreshDriverTrip(reading) {
    const t = translation();
    const status = $('#driver-status');
    if (reading.forecast) {
      status.className = 'driver-status is-divert';
      status.innerHTML = '<strong>' + t.divert + '</strong><span>' + escapeHTML(reading.forecast.divert_to.name) + '</span>';
    } else if (reading.outage || reading.margin <= 0) {
      const detail = reading.outage ? t.ahead : reading.margin === 0 ? t.at : Math.abs(reading.margin) + ' ' + t.below;
      status.className = 'driver-status is-warn';
      status.innerHTML = '<strong>' + (reading.outage ? t.outage : reading.margin === 0 ? t.atLimit : t.check) +
        '</strong><span>' + detail + '</span>';
    } else {
      status.className = 'driver-status';
      status.innerHTML = '<strong>' + t.continue + '</strong><span>' + reading.margin + ' ' + t.above + '</span>';
    }
    $('#driver-readings').innerHTML = metric(t.battery, reading.soc + '%') + metric(t.threshold, reading.threshold + '%');
    $('#driver-slider').value = state.replayIndex;
    $('#driver-slider').setAttribute('aria-valuetext', reading.km + ' ' + t.distance);
    $('#driver-distance').textContent = reading.km + ' / ' + flagship.route.distance_km + ' ' + t.distance;
    $('#driver-play').textContent = state.replayTimer ? t.pause : state.replayIndex === events.length - 1 ? t.restart : t.play;
  }

  function workPage(detail) {
    if (state.tab === 'chargers' && detail) return window.VerityProof.charger(work, detail);
    if (state.tab === 'evidence' || state.tab === 'engine') {
      window.VerityProof.evidence(work);
      if (state.tab === 'engine') $('#engine-explainer').open = true;
      return;
    }
    ({dispatch: dispatchPage, replay: replayPage, chargers: chargersPage})[state.tab]();
  }

  function refreshInstruments() {
    if (state.mode === 'home') refreshStory(state.storyIndex);
    if (state.mode === 'workspace' && state.tab === 'dispatch') refreshForecast();
    if (state.mode === 'workspace' && state.tab === 'replay') refreshReplay();
  }

  // The large recorded trip fixtures are only needed when a tool opens.
  const loaded = new Map();
  function loadTool(name) {
    if (!loaded.has(name)) {
      const pending = new Promise((resolve,reject) => {
        const script = document.createElement('script');
        script.src = 'drive/' + name + '.js';
        script.onload = resolve;
        script.onerror = () => { loaded.delete(name); script.remove(); reject(new Error('Tool unavailable')); };
        document.head.append(script);
      });
      loaded.set(name,pending);
    }
    return loaded.get(name);
  }

  async function route() {
    const hash = location.hash;
    if (hasRouted && hash && !hash.startsWith('#/')) return;
    const [, requestedMode, requestedTab, requestedVehicle] = hash.split('/');
    if (requestedMode === 'logger') { location.replace('../logger/index.html' + location.search); return; }
    const mode = modes.includes(requestedMode) ? requestedMode : 'home';
    const tab = workTabs.includes(requestedTab) ? requestedTab : 'dispatch';
    const vehicle = vehicleIds.includes(requestedVehicle) ? requestedVehicle : 'V12';
    const focusVehicle = hasRouted && mode === 'workspace' && state.mode === mode && tab === 'dispatch' && state.tab === tab && vehicle !== state.vehicle;
    const viewTransition = hasRouted && !focusVehicle;
    haltReplay();
    window.VerityTripView?.leave();
    if (mode !== 'home') {
      try {
        await loadTool('cases');
        await loadTool('trip-service');
        if (mode === 'trip') { await loadTool('ride-voice'); await loadTool('trip'); }
        if (mode === 'workspace' && ['evidence','engine','chargers'].includes(tab)) await loadTool('evidence');
      } catch {
        if (location.hash !== hash) return;
        for (const section of modes) $('#' + section).hidden = section !== mode;
        const target = mode === 'workspace' ? work : $('#' + mode);
        target.innerHTML = '<p role="alert">This view could not load. <button type="button">Retry</button></p>';
        target.querySelector('button').onclick = () => location.reload();
        return;
      }
      if (location.hash !== hash) return;
    }
    state.workPlan = null;
    state.mode = mode;
    state.tab = tab;
    state.vehicle = vehicle;
    state.forecastKm = 0;
    document.body.dataset.mode = mode;

    for (const section of modes) $('#' + section).hidden = section !== mode;
    $$('.main-nav a').forEach(link => {
      const navMode = mode;
      if (link.dataset.nav === navMode) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });

    $$('.workspace-nav a').forEach(link => {
      if (mode === 'workspace' && link.dataset.workspace === (tab === 'engine' ? 'evidence' : tab)) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });

    if (mode === 'workspace') workPage(requestedVehicle);
    if (mode === 'trip') window.VerityTripView.open(hash.split('/'));
    if (mode === 'driver') driverPage();
    if (mode === 'home') refreshStory(0);
    if (focusVehicle) $('.vehicle-option[aria-current="true"]').focus({ preventScroll: true });
    if (viewTransition) {
      window.scrollTo({ top: 0, behavior: 'instant' });
      if (mode !== 'home') $('#' + mode + ' h1')?.focus({ preventScroll: true });
    }
    document.title = mode === 'home' ? 'Verity — EV trip decisions' :
      'Verity — ' + ({ workspace: 'Fleet workspace', driver: 'Driver mode', trip: 'Trip Check' })[mode];
    hasRouted = true;
  }

  document.addEventListener('click', event => {
    const replay = event.target.closest('[data-replay]');
    if (replay?.dataset.replay === 'toggle') toggleReplay();
    if (replay?.dataset.replay === 'divert') seekReplay(events.indexOf(diverted));
    const eventButton = event.target.closest('[data-replay-index]');
    if (eventButton) seekReplay(Number(eventButton.dataset.replayIndex));

    const language = event.target.closest('[data-language]');
    if (language) {
      state.language = language.dataset.language;
      driverPage();
      $('[data-language="' + state.language + '"]').focus({ preventScroll: true });
    }
    const driverTab = event.target.closest('[data-driver-tab]');
    if (driverTab) {
      state.driverTab = driverTab.dataset.driverTab;
      haltReplay();
      driverPage();
      $('[data-driver-tab="' + state.driverTab + '"]').focus({ preventScroll: true });
    }
  });

  document.addEventListener('input', event => {
    if (event.target.matches('#replay-slider, #driver-slider')) seekReplay(Number(event.target.value));
  });

  $('.skip-link').addEventListener('click', event => {
    event.preventDefault();
    $('#main').focus();
  });
  let resizeFrame = 0;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(refreshInstruments);
  });
  window.addEventListener('hashchange', route);

  initialiseHome();
  route();
  document.fonts?.ready.then(refreshInstruments);
})();

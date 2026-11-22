import {api, accountApi} from './client.js';
import {button, esc, facts, numberInput, onSubmit, option, release, releaseLink, section, statusBox, verdict} from './ui.js';

const corridor = {
  origin: {name:'Noida', lat:28.5355, lon:77.391},
  destination: {name:'Agra', lat:27.1767, lon:78.0081},
};
const localDeparture = () => new Date(Date.now() + 330 * 60000).toISOString().slice(0,16);

function placeField(key, label) {
  return `<div class="place-field"><label for="place-${key}">${label}</label><input id="place-${key}" type="text" autocomplete="off" maxlength="120" required>
    <div class="places" aria-label="${label} search results"></div><small class="place-hint" aria-live="polite"></small></div>`;
}
function bindPlace(input, initial) {
  const list = input.parentElement.querySelector('.places'), hint = input.parentElement.querySelector('.place-hint');
  let selected, timer, revision = 0;
  function choose(place) {
    revision++;
    selected = place;
    input.value = place.name;
    input.setCustomValidity('');
    list.replaceChildren();
    hint.textContent = `${place.lat.toFixed(4)}, ${place.lon.toFixed(4)}`;
  }
  choose(initial);
  input.addEventListener('input', () => {
    const version = ++revision;
    clearTimeout(timer);
    list.replaceChildren();
    selected = null;
    input.setCustomValidity('Select a search result or enter a latitude, longitude pair.');
    const text = input.value.trim();
    const pair = /^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/.exec(text);
    if (pair && Math.abs(+pair[1]) <= 90 && Math.abs(+pair[2]) <= 180) {
      selected = {name:text, lat:+pair[1], lon:+pair[2]};
      input.setCustomValidity('');
      hint.textContent = 'Coordinates selected.';
      return;
    }
    hint.textContent = text.length < 2 ? 'Enter a place in India or latitude, longitude.' : 'Searching…';
    if (text.length < 2) return;
    timer = setTimeout(async () => {
      try {
        const {results} = await api('/geocode?' + new URLSearchParams({q:text, limit:5}));
        if (revision !== version) return;
        hint.textContent = results.length ? 'Choose a matching place.' : 'No matches. Try another name or enter coordinates.';
        for (const place of results) {
          const b = document.createElement('button'); b.type = 'button';
          b.textContent = `${place.name} · ${place.lat.toFixed(3)}, ${place.lon.toFixed(3)}`;
          b.onclick = () => { choose(place); input.focus(); };
          list.append(b);
        }
      } catch (error) { if (revision === version) hint.textContent = error.message + ' You can enter coordinates.'; }
    }, 400);
  });
  return {read: () => selected, choose};
}
function routeInputs() {
  return `<fieldset><legend>Route</legend><div class="route-fields">${placeField('origin','From')}${placeField('destination','To')}</div>
    <label>Departure (India time)<input type="datetime-local" name="departure" required value="${localDeparture()}"></label></fieldset>`;
}
function conditions() {
  return `<div class="fields">${numberInput('passengers','Passengers', 4,0,8)}${numberInput('confidence','Confidence target (%)',90,51,99)}</div>
    <label class="check"><input type="checkbox" name="ac" checked>Air conditioning on</label>`;
}
function journeyInputs(form) {
  const from = bindPlace(form.querySelector('#place-origin'),corridor.origin);
  const to = bindPlace(form.querySelector('#place-destination'),corridor.destination);
  function selectTrip(trip) {
    from.choose(trip.origin);
    to.choose(trip.destination);
    const inIndia = new Date(Date.parse(trip.departure_time) + 330 * 60000);
    form.elements.departure.value = inIndia.toISOString().slice(0,16);
    form.elements.departure.focus();
  }
  function body(data, named = false) {
    const place = point => named ? {lat:point.lat, lon:point.lon, name:point.name.slice(0,80)} : {lat:point.lat,lon:point.lon};
    return {origin:place(from.read()),destination:place(to.read()),departure_time:data.get('departure') + ':00+05:30',
      passengers:Number(data.get('passengers')),ac_on:data.has('ac'),target_confidence:Number(data.get('confidence')) / 100};
  }
  return {body, selectTrip};
}
const modelLabel = v => [v.make,v.model,v.variant].filter(Boolean).join(' ');
export const modelOptions = (models, selected) => models.map(v=>option(v.vehicle_id,modelLabel(v),v.vehicle_id === selected)).join('');
let cachedSelection;
export function openCachedPlan(trip, conditions) {
  cachedSelection = {trip, conditions};
  location.hash = '#plan';
}
export async function planner(root, cars = null, onCreated = null) {
  const privateTrip = cars !== null;
  if (privateTrip && !cars.length) {
    root.innerHTML = '<p class="muted">Add a car to your garage to plan a personal trip.</p>';
    return;
  }
  const vehicles = privateTrip ? null : (await api('/vehicles')).vehicles;
  const select = privateTrip ? cars.map(c=>option(c.id,c.nickname)).join('') : modelOptions(vehicles,'tata-nexon-ev-45');
  root.innerHTML = `<form class="journey-form"><div class="form-columns">${routeInputs()}<fieldset><legend>Vehicle &amp; conditions</legend>
    <label>${privateTrip ? 'Garage car' : 'Vehicle model'}<select name="vehicle" required>${select}</select></label>
    ${numberInput('soc','Starting battery (%)',75,0,100)}${conditions()}</fieldset></div>
    <div class="form-actions">${button(privateTrip ? 'Plan with my car' : 'Compare plans',true)}${statusBox()}</div></form><div class="results"></div>`;
  const form = root.querySelector('form'), results = root.querySelector('.results'), journey = journeyInputs(form);
  if (!privateTrip) {
    const health = document.createElement('small');
    form.append(health);
    api('/health').then(data => {
      health.textContent = 'Service status · ' + data.status + (data.is_simulated ? ' (simulated)' : '');
    }).catch(() => { health.textContent = 'Service status unavailable.'; });
  }
  if (!privateTrip && cachedSelection) {
    journey.selectTrip(cachedSelection.trip);
    for (const [name,value] of Object.entries(cachedSelection.conditions)) {
      const input = form.elements.namedItem(name);
      if (input.type === 'checkbox') input.checked = value;
      else input.value = value;
    }
    cachedSelection = null;
  }
  onSubmit(form, async data => {
    results.replaceChildren();
    const body = {...journey.body(data,!privateTrip),start_soc_pct:Number(data.get('soc'))};
    if (privateTrip) {
      const trip = await accountApi('/trips',{method:'POST',body:{...body,car_id:data.get('vehicle')}});
      onCreated(trip,results);
    } else {
      const response = await api('/plan',{method:'POST',body:{...body,vehicle_id:data.get('vehicle')}});
      const issues = response.unsupported_preferences.map(p=>`<p class="warning">${esc(p.reason)}</p>`).join('');
      results.innerHTML = `<h2 tabindex="-1">Plan comparison</h2>${issues}${response.options.map(p=>release(p)).join('') || '<p>No plans returned for these inputs.</p>'}`;
    }
    results.querySelector('h2')?.focus();
  }, privateTrip ? 'Planning your trip…' : 'Comparing charging plans…', (error,status) => routeError(error,status,results,journey.selectTrip));
}
export function routeError(error, status, results, selectTrip, opensPlanner = false) {
  if (error.status === 503 && error.code === 'offline') {
    results.replaceChildren();
    if (!error.cachedTrips.length) {
      status.textContent = 'Offline. No cached trips are available.';
      return;
    }
    status.textContent = opensPlanner ? 'Offline. Choose a cached trip to open in the trip planner.'
      : 'Offline. Choose a cached trip to fill in the route and departure.';
    for (const trip of error.cachedTrips) {
      const choice = document.createElement('button');
      choice.type = 'button';
      choice.className = 'button button-outline';
      choice.textContent = [trip.origin.name || trip.slug, trip.destination.name, new Date(trip.departure_time).toLocaleString('en-IN', {timeZone:'Asia/Kolkata'}) + ' IST'].filter(Boolean).join(' · ');
      choice.onclick = () => {
        status.textContent = 'Cached trip selected. Review the inputs before planning.';
        results.replaceChildren();
        selectTrip(trip);
      };
      results.append(choice);
    }
  } else if (error.code === 'route_too_long') {
    status.textContent = error.message + ' Choose a shorter leg.';
  } else if (error.code === 'route_unavailable' || (error.status === 503 && !error.code)) {
    status.textContent = 'Planning is unavailable. Retry shortly or choose another route.';
  } else if (error.status === 422 && !error.code) {
    status.textContent = 'Check the route, departure and vehicle inputs, or try a shorter leg.';
  }
}
function pickupVehicles(form, models) {
  const host = form.querySelector('.pickup-vehicles');
  const add = form.querySelector('[data-add-car]');
  function append() {
    const row = document.createElement('fieldset');
    row.className = 'booking-car';
    row.innerHTML = `<legend>Vehicle</legend><label>Model<select name="model">${modelOptions(models,'tata-nexon-ev-45')}</select></label>
      ${numberInput('battery','Battery at pickup (%)',75,0,100)}<button type="button" class="button button-outline">Remove vehicle</button>`;
    row.querySelector('button').onclick = () => { row.remove(); refresh(); };
    host.append(row);
    refresh();
  }
  function refresh() {
    const rows = [...host.children];
    rows.forEach((row,i) => {
      row.querySelector('legend').textContent = 'Vehicle ' + (i + 1);
      row.querySelector('button').disabled = rows.length === 1;
    });
    add.disabled = rows.length >= 8;
  }
  append();
  add.onclick = append;
  const mode = form.elements.fleetMode;
  mode.onchange = () => {
    const pickup = mode.value === 'pickup';
    host.parentElement.hidden = !pickup;
    host.parentElement.disabled = !pickup;
    form.querySelector('[type="submit"]').textContent = pickup ? 'Rank vehicles' : 'Rank demo vehicles';
  };
  mode.onchange();
}
export function bookingRequest(journey, data) {
  if (data.get('fleetMode') !== 'pickup') return journey;
  const battery = data.getAll('battery');
  return {...journey,vehicles:data.getAll('model').map((vehicle_id,i)=>({vehicle_id,soc_pct:Number(battery[i])}))};
}
export async function fleetBooking(root, managedVehicles = null) {
  const privateFleet = managedVehicles !== null;
  const models = privateFleet ? [] : (await api('/vehicles')).vehicles;
  root.innerHTML = `<form><div class="form-columns">${routeInputs()}<fieldset><legend>Booking conditions</legend>${conditions()}
    <p class="muted">${privateFleet ? 'Check acceptance using the vehicles and battery health saved to your fleet.' : 'Choose vehicles at the pickup, or rank the simulated demo fleet from its depots.'}</p></fieldset></div>
    ${privateFleet ? '' : `<label>Fleet to rank<select name="fleetMode"><option value="pickup">Vehicles at pickup</option><option value="demo">Demo vehicles at depots (simulated)</option></select></label>
      <fieldset><legend>Vehicles at pickup</legend><div class="pickup-vehicles"></div>
      <button type="button" class="button button-outline" data-add-car>Add vehicle</button></fieldset>`}
    <div class="form-actions">${button(privateFleet ? 'Check my fleet' : 'Rank demo vehicles',true)}${statusBox()}</div></form><div class="results"></div>`;
  const form = root.querySelector('form'), results = root.querySelector('.results'), journey = journeyInputs(form);
  if (!privateFleet) pickupVehicles(form,models);
  onSubmit(form, async data => {
    results.replaceChildren();
    if (privateFleet) {
      const collection = await managedVehicles();
      if (!collection.length) throw new Error('Add a fleet vehicle before checking a booking.');
      const names = new Map(collection.map(v=>[v.id,v.nickname]));
      const rows = [];
      for (let offset = 0; offset < collection.length; offset += 5) {
        rows.push(...await accountApi(`/fleet/booking/check?limit=5&offset=${offset}`,{method:'POST',body:journey.body(data)}));
      }
      results.innerHTML = `<h2 tabindex="-1">Booking decisions</h2>${rows.map(r=>section(names.get(r.vehicle_id) || r.vehicle_id,
        `${verdict(r.decision)}<p>${esc(r.reason)}</p>`)).join('')}`;
    } else {
      const result = await api('/fleet/booking',{method:'POST',body:bookingRequest(journey.body(data,true),data)});
      results.innerHTML = `<h2 tabindex="-1">Fleet ranking${result.is_simulated ? ' (simulated)' : ''}</h2>${result.recommended_plan_id ? '' : '<p>No vehicle is recommended for this booking.</p>'}
        ${result.rows.map(r=>`<article class="release-card"><div class="card-head"><h3>${esc(r.rank)}. ${esc(r.label)}</h3>${verdict(r.decision)}</div>
        ${r.recommended ? '<p class="recommend">Recommended by the engine</p>' : ''}${r.why_not ? `<p>${esc(r.why_not)}</p>` : ''}
        ${facts([['Confidence',window.VerityCore.confidenceText(r)],['Starting battery',`${r.soc_pct}%`],['Distance to pickup',r.start == null ? 'At pickup' : r.deadhead_km == null ? null : `${r.deadhead_km} km`],['Trip time',`${r.total_minutes} min`],['Charging',`${r.charging_minutes} min`],['Median arrival battery',`${r.arrival_soc.p50}%`]])}${releaseLink(r.plan_id)}</article>`).join('')}`;
    }
    results.querySelector('h2')?.focus();
  },'Checking the booking…', (error,status) => {
    if (privateFleet || form.elements.fleetMode.value === 'pickup') return routeError(error,status,results,journey.selectTrip);
    routeError(error,status,results,trip => openCachedPlan(trip,{
      passengers:form.elements.passengers.value, confidence:form.elements.confidence.value, ac:form.elements.ac.checked
    }),true);
  });
}

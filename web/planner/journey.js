import {api, accountApi} from './client.js';
import {button, esc, facts, numberInput, onSubmit, option, release, section, statusBox, verdict} from './ui.js';

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
  return () => selected;
}
function routeInputs() {
  return `<fieldset><legend>Route</legend><div class="route-fields">${placeField('origin','From')}${placeField('destination','To')}</div>
    <label>Departure (India time)<input type="datetime-local" name="departure" required value="${localDeparture()}"></label></fieldset>`;
}
function conditions() {
  return `<div class="fields">${numberInput('passengers','Passengers', 4,0,8)}${numberInput('confidence','Confidence target (%)',90,51,99)}</div>
    <label class="check"><input type="checkbox" name="ac" checked>Air conditioning on</label>`;
}
export function journeyInputs(form) {
  const from = bindPlace(form.querySelector('#place-origin'),corridor.origin);
  const to = bindPlace(form.querySelector('#place-destination'),corridor.destination);
  return (data, named = false) => {
    const place = point => named ? {lat:point.lat, lon:point.lon, name:point.name.slice(0,80)} : {lat:point.lat,lon:point.lon};
    return {origin:place(from()),destination:place(to()),departure_time:data.get('departure') + ':00+05:30',
      passengers:Number(data.get('passengers')),ac_on:data.has('ac'),target_confidence:Number(data.get('confidence')) / 100};
  };
}
const modelLabel = v => [v.make,v.model,v.variant].filter(Boolean).join(' ');
export const modelOptions = (models, selected) => models.map(v=>option(v.vehicle_id,modelLabel(v),v.vehicle_id === selected)).join('');
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
  onSubmit(form, async data => {
    results.replaceChildren();
    const body = {...journey(data,!privateTrip),start_soc_pct:Number(data.get('soc'))};
    if (privateTrip) {
      const trip = await accountApi('/trips',{method:'POST',body:{...body,car_id:data.get('vehicle')}});
      onCreated(trip,results);
    } else {
      const response = await api('/plan',{method:'POST',body:{...body,vehicle_id:data.get('vehicle')}});
      const issues = response.unsupported_preferences.map(p=>`<p class="warning">${esc(p.reason)}</p>`).join('');
      results.innerHTML = `<h2 tabindex="-1">Plan comparison</h2>${issues}${response.options.map(p=>release(p)).join('') || '<p>No plans returned for these inputs.</p>'}`;
    }
    results.querySelector('h2')?.focus();
  }, privateTrip ? 'Planning your trip…' : 'Comparing charging plans…');
}
export function fleetBooking(root, managedVehicles = null) {
  const privateFleet = managedVehicles !== null;
  root.innerHTML = `<form><div class="form-columns">${routeInputs()}<fieldset><legend>Booking conditions</legend>${conditions()}
    <p class="muted">${privateFleet ? 'Check acceptance using the vehicles and battery health saved to your fleet.' : 'Rank the demo fleet using its simulated starting locations and battery levels.'}</p></fieldset></div>
    <div class="form-actions">${button(privateFleet ? 'Check my fleet' : 'Rank demo vehicles',true)}${statusBox()}</div></form><div class="results"></div>`;
  const form = root.querySelector('form'), results = root.querySelector('.results'), journey = journeyInputs(form);
  onSubmit(form, async data => {
    results.replaceChildren();
    if (privateFleet) {
      const collection = await managedVehicles();
      if (!collection.length) throw new Error('Add a fleet vehicle before checking a booking.');
      const names = new Map(collection.map(v=>[v.id,v.nickname]));
      const rows = [];
      for (let offset = 0; offset < collection.length; offset += 5) {
        rows.push(...await accountApi(`/fleet/booking/check?limit=5&offset=${offset}`,{method:'POST',body:journey(data)}));
      }
      results.innerHTML = `<h2 tabindex="-1">Booking decisions</h2>${rows.map(r=>section(names.get(r.vehicle_id) || r.vehicle_id,
        `${verdict(r.decision)}<p>${esc(r.reason)}</p>`)).join('')}`;
    } else {
      const result = await api('/fleet/booking',{method:'POST',body:journey(data,true)});
      results.innerHTML = `<h2 tabindex="-1">Fleet ranking</h2>${result.recommended_plan_id ? '' : '<p>No vehicle is recommended for this booking.</p>'}
        ${result.rows.map(r=>`<article class="release-card"><div class="card-head"><h3>${esc(r.rank)}. ${esc(r.label)}</h3>${verdict(r.decision)}</div>
        ${r.recommended ? '<p class="recommend">Recommended by the engine</p>' : ''}${r.why_not ? `<p>${esc(r.why_not)}</p>` : ''}
        ${facts([['Confidence',r.confidence_display],['Trip time',`${r.total_minutes} min`],['Charging',`${r.charging_minutes} min`],['Median arrival battery',`${r.arrival_soc.p50}%`]])}</article>`).join('')}`;
    }
    results.querySelector('h2')?.focus();
  },'Checking the booking…');
}

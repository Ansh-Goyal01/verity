import {api, accountApi, keyedWrite, configuration, googleAvailable, googleSignIn, hasSession, signIn, signOut} from './client.js';
import {modelOptions, planner} from './journey.js';
import {button, dateText, esc, idPath, numberInput, onSubmit, option, paginate, release, section, setStatus, statusBox, stopsAndThresholds, verdict} from './ui.js';

export async function login(root, description, reopen) {
  root.innerHTML = section('Sign in', `<p>${esc(description)}</p>
    <form class="signin"><label>Email<input name="email" type="email" autocomplete="username" required></label>
    <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
    ${button('Sign in',true)}${statusBox()}</form>`);
  const form = root.querySelector('form');
  form.querySelector('button').disabled = true;
  const cfg = await configuration();
  if (!cfg.available) {
    form.querySelectorAll('input').forEach(input=>input.disabled=true);
    setStatus(form.querySelector('.status'),'Account sign-in is unavailable on this deployment. Public trip planning is available.');
    return;
  }
  form.querySelector('button').disabled = false;
  googleAvailable(cfg).then(available => {
    if (!available || !form.isConnected) return;
    const google = document.createElement('button');
    google.type = 'button'; google.className = 'button button-outline'; google.textContent = 'Continue with Google';
    google.onclick = () => googleSignIn(cfg).catch(error => setStatus(form.querySelector('.status'),error.message,true));
    form.append(google);
  });
  onSubmit(form, async data => {
    await signIn(data.get('email').trim(),data.get('password'));
    form.reset();
    if (root.isConnected) reopen();
  },'Signing in…');
}
export function signOutControl(node, reopen) {
  node.innerHTML = `<form>${button('Sign out')}${statusBox()}</form>`;
  onSubmit(node.querySelector('form'), async () => {
    try { await signOut(); }
    finally { if (node.isConnected) reopen(); }
  },'Signing out…');
}
export async function allFleetVehicles() {
  const rows = [];
  for (let offset = 0; ; offset += 100) {
    const page = await accountApi(`/fleet/vehicles?limit=100&offset=${offset}`);
    rows.push(...page);
    if (page.length < 100) return rows;
  }
}

export async function garage(root, {fleet, cars, reload}) {
  const models = await api('/vehicle-models');
  const path = fleet ? '/fleet/vehicles' : '/me/cars';
  const byId = new Map(models.map(m=>[m.vehicle_id,m]));
  function editor(car) {
    return `<form class="car-editor"><label>Nickname<input name="nickname" maxlength="80" required value="${esc(car?.nickname)}"></label>
      <label>Model<select name="model_id" required>${modelOptions(models,car?.model_id)}</select></label>
      <div class="fields">${numberInput('battery_health_pct','Battery health (%)',car?.battery_health_pct ?? 100,0.01,100,'any')}
      ${fleet ? numberInput('start_soc_pct','Starting battery (%)',car?.start_soc_pct ?? 75,0,100,'any') : ''}</div>
      ${button(car ? 'Save changes' : 'Add vehicle',true)}${statusBox()}</form>`;
  }
  const vehicleHtml = car => {
    const model = byId.get(car.model_id);
    return `<article class="vehicle"><div class="card-head"><h3>${esc(car.nickname)}</h3><span class="source-note">${esc(car.battery_health_pct)}% battery health</span></div>
      <p>${esc(model ? [model.make,model.model,model.variant].filter(Boolean).join(' ') : car.model_id)}${fleet ? ' · ' + esc(car.start_soc_pct) + '% charged' : ''}</p>
      <details><summary>Edit vehicle</summary>${editor(car)}
      <details class="remove"><summary>Remove vehicle</summary><form><p>Remove ${esc(car.nickname)} from ${fleet ? 'the fleet' : 'your garage'}?</p>${button('Confirm removal')}${statusBox()}</form></details></details></article>`;
  };
  root.innerHTML = `<div class="garage-list">${cars.map(vehicleHtml).join('') || '<p class="muted">No vehicles added yet.</p>'}</div>
    <details class="add-car" ${cars.length ? '' : 'open'}><summary>Add a vehicle</summary>${editor(null)}</details>`;
  function bindEditor(form, car) {
    onSubmit(form, async data => {
      const body = {model_id:data.get('model_id'),nickname:data.get('nickname').trim(),battery_health_pct:Number(data.get('battery_health_pct'))};
      if (fleet) body.start_soc_pct = Number(data.get('start_soc_pct'));
      await accountApi(car ? path + '/' + idPath(car.id) : path,{method:car ? 'PUT' : 'POST',body});
      if (root.isConnected) await reload();
    },'Saving vehicle…');
  }
  root.querySelectorAll('.vehicle').forEach((el,i)=>{
    bindEditor(el.querySelector('.car-editor'),cars[i]);
    onSubmit(el.querySelector('.remove form'), async () => {
      await accountApi(path + '/' + idPath(cars[i].id),{method:'DELETE'});
      if (root.isConnected) await reload();
    },'Removing vehicle…');
  });
  bindEditor(root.querySelector('.add-car form'),null);
}

function tripDetail(root, trip, changed, user) {
  const path = '/trips/' + idPath(trip.id);
  root.innerHTML = `<h2 tabindex="-1">Trip details</h2>${release(trip.plan,'Trip decision',false)}
    <div class="trip-tools">${section('Trip record',`<form class="save-trip">${button(trip.saved ? 'Trip saved' : 'Keep this trip')}${statusBox()}</form>
      <form class="arrival">${numberInput('arrival','Actual arrival battery (%)',trip.arrival_pct ?? '',0,100,'any')}${button('Record arrival')}${statusBox()}</form>`)}
    ${section('Share this trip',`<p>Anyone with the link can view the decision, charging stops, and battery thresholds for 48 hours.</p>
      <form class="share">${button('Create share link')}${statusBox()}</form><div class="share-output"></div>`)}</div>`;
  const stops = [...(trip.plan.stops || []), ...(trip.plan.alternates || [])];
  if (stops.length) {
    const form = document.createElement('form');
    form.className = 'sheet';
    form.innerHTML = `<h3>Report a charger</h3><label>Charging stop<select name="charger">${stops.map(s=>option(s.charger_id,s.name)).join('')}</select></label>
      <div class="form-actions"><button type="submit" value="working">Working</button><button type="submit" value="not_working">Not working</button></div>${statusBox()}`;
    const receipt = document.createElement('div');
    root.append(form, receipt);
    onSubmit(form, async (data,submit) => {
      const saved = await keyedWrite(user,'/reports/quick',{charger_id:data.get('charger'),status:submit.value});
      receipt.innerHTML = '<button type="button" class="button button-outline">View my report receipt</button><p role="status"></p>';
      receipt.querySelector('button').onclick = async () => {
        const output = receipt.querySelector('p');
        try {
          const row = await accountApi('/reports/' + idPath(saved.id));
          output.textContent = [row.charger_id, row.status.replaceAll('_',' '), dateText(row.created_at)].join(' · ');
        } catch (error) { output.textContent = error.message; }
      };
      return 'Report recorded ' + dateText(saved.created_at) + '.';
    },'Recording report…');
  }
  const save = root.querySelector('.save-trip');
  if (trip.saved) save.querySelector('button').disabled = true;
  onSubmit(save,async () => {
    await accountApi(path + '/save',{method:'POST'});
    save.replaceChildren(document.createTextNode('Trip saved.'));
    changed();
    return 'Saved.';
  });
  onSubmit(root.querySelector('.arrival'),async data => {
    const updated = await accountApi(path + '/arrival',{method:'POST',body:{arrival_pct:Number(data.get('arrival'))}});
    changed(); return `Recorded ${updated.arrival_pct}% on arrival.`;
  });
  onSubmit(root.querySelector('.share'),async () => {
    const share = await accountApi(path + '/share',{method:'POST'});
    const url = new URL(location.pathname,location.origin);
    url.hash = 'share/' + idPath(share.token);
    const box = root.querySelector('.share-output');
    box.innerHTML = `<label>Share link<input readonly value="${esc(url.href)}"></label><button type="button" class="button button-outline">Copy link</button><p class="status" role="status">Expires ${esc(dateText(share.expires_at))}.</p>`;
    box.querySelector('button').onclick = async () => {
      try { await navigator.clipboard.writeText(url.href); setStatus(box.querySelector('.status'),'Link copied.'); }
      catch { box.querySelector('input').select(); setStatus(box.querySelector('.status'),'Link selected. Copy it to share.'); }
    };
  },'Creating link…');
}

export async function account(root, panel, reopen) {
  if (!hasSession()) return login(root,'Sign in to manage your cars, save trips, and share a trip decision.',reopen);
  const {cars,profile} = await accountApi('/me');
  const tabs = [['garage','Garage'],['plan','Plan with my car'],['trips','Trip history']];
  const selected = tabs.some(([id])=>id===panel) ? panel : 'garage';
  root.innerHTML = `<div class="account-bar"><p>${esc(profile.display_name || 'Signed in')} · ${esc(profile.role.replaceAll('_',' '))}</p><div class="logout"></div></div>
    <nav class="subnav" aria-label="Account tools">${tabs.map(([id,label])=>`<a href="#account/${id}" ${selected===id?'aria-current="page"':''}>${label}</a>`).join('')}</nav><div class="account-panel"></div>`;
  signOutControl(root.querySelector('.logout'),reopen);
  const body = root.querySelector('.account-panel');
  if (selected === 'garage') {
    body.innerHTML = section('Your vehicles','<div class="garage"></div>');
    return garage(body.querySelector('.garage'),{fleet:false,cars,reload:reopen});
  }
  if (selected === 'plan') return planner(body,cars,(trip,node)=>tripDetail(node,trip,()=>{},profile.id));
  body.innerHTML = section('Trip history','<div class="history"></div>') + '<div class="trip-detail"></div>';
  const detail = body.querySelector('.trip-detail');
  const names = new Map(cars.map(c=>[c.id,c.nickname]));
  const refresh = paginate(body.querySelector('.history'),(offset,limit)=>accountApi(`/trips?offset=${offset}&limit=${limit}`),(host,rows)=>{
    if (!rows.length) {host.innerHTML = '<p class="muted">Trips you plan with a garage car will appear here.</p>'; return;}
    host.innerHTML = rows.map(r=>`<button type="button" class="history-row"><span><strong>${esc(names.get(r.car_id) || 'Trip')}</strong><small>${esc(dateText(r.created_at))}${r.saved?' · Saved':''}${r.arrival_pct!=null?' · Arrived at '+esc(r.arrival_pct)+'%':''}</small></span>${verdict(r.decision)}</button>`).join('');
    host.querySelectorAll('button').forEach((b,i)=>b.onclick = async () => {
      const display = document.createElement('div'); detail.replaceChildren(display);
      display.textContent = 'Opening trip…';
      try {
        const trip = await accountApi('/trips/' + idPath(rows[i].id));
        tripDetail(display,trip,refresh,profile.id); display.querySelector('h2').focus();
      } catch (error) { display.textContent = error.message; }
    });
  });
}
export async function sharedTrip(root,token) {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token || '')) throw new Error('This share link is incomplete.');
  const trip = await api('/share/' + idPath(token));
  root.innerHTML = section('Trip decision',verdict(trip.decision) + stopsAndThresholds(trip));
}

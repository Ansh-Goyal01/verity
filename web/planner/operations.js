import {accountApi, hasSession, keyedWrite} from './client.js';
import {allFleetVehicles, garage, login} from './account.js';
import {fleetBooking} from './journey.js';
import {button, dateText, esc, idPath, numberInput, onSubmit, option, paginate, records, section, statusBox} from './ui.js';

function links(base, selected, items) {
  return `<nav class="subnav" aria-label="Workspace sections">${items.map(([id,label])=>
    `<a href="#${base}/${id}" ${selected===id ? 'aria-current="page"' : ''}>${esc(label)}</a>`).join('')}</nav>`;
}
export async function fleet(root, panel, reopen) {
  const mine = ['vehicles','booking'].includes(panel);
  root.innerHTML = links('fleet',mine ? panel : 'rank',[['rank','Rank vehicles'],['vehicles','My fleet'],['booking','Check my booking']]) + '<div class="tool-panel"></div>';
  const area = root.querySelector('.tool-panel');
  if (!mine) return fleetBooking(area);
  if (!hasSession()) return login(area,'Use a fleet manager account to manage vehicles and check bookings.',reopen);
  const {profile} = await accountApi('/me');
  if (profile.role !== 'fleet_manager') {
    area.innerHTML = section('Fleet access','<p>This workspace requires a fleet manager account.</p><a class="text-link" href="#account">My account</a>');
    return;
  }
  if (panel === 'booking') return fleetBooking(area,allFleetVehicles);
  area.innerHTML = section('Fleet vehicles','<div class="vehicles"></div>');
  return garage(area.querySelector('.vehicles'),{fleet:true,cars:await allFleetVehicles(),reload:reopen});
}

function admin(root) {
  root.innerHTML = section('Pending operator claims','<div class="claims"></div>');
  const refresh = paginate(root.querySelector('.claims'),(offset,limit)=>accountApi(`/admin/cpo-claims?offset=${offset}&limit=${limit}`),(host,rows) => {
    host.innerHTML = rows.map(r=>`<article class="vehicle"><h3>${esc(r.operator_id)}</h3><p class="muted">Applicant ${esc(r.user_id)}</p>
      <details><summary>Review claim</summary><form><p>Approving grants access to this operator’s chargers and reports.</p>${button('Approve claim',true)}${statusBox()}</form></details></article>`).join('') || '<p>No claims awaiting approval.</p>';
    host.querySelectorAll('form').forEach((form,i)=>onSubmit(form,async()=>{
      await accountApi('/admin/cpo-claims/' + idPath(rows[i].id) + '/approve',{method:'POST'});
      refresh();
    },'Approving claim…'));
  });
}
async function claims(root, reopen) {
  const [rows, directory] = await Promise.all([accountApi('/cpo/claims'),accountApi('/cpo/directory')]);
  root.innerHTML = section('Your operator claims',rows.map(r=>`<div class="list-row"><strong>${esc(r.operator_id)}</strong><span>${r.approved_at ? 'Approved · ' + esc(dateText(r.approved_at)) : 'Pending approval'}</span></div>`).join('') || '<p>No operator claims yet.</p>') +
    section('Request access',`<form><label>Operator<select name="operator" required>${option('','Choose an operator')}${directory.operators.map(id=>option(id,id)).join('')}</select></label>${button('Submit claim',true)}${statusBox()}</form>`);
  onSubmit(root.querySelector('form'), async data=>{
    await accountApi('/cpo/claims',{method:'POST',body:{operator_id:data.get('operator').trim()}});
    if (root.isConnected) reopen();
  },'Submitting claim…');
}
async function chargers(root, reopen, user) {
  const data = await accountApi('/cpo/chargers');
  root.innerHTML = section('Managed chargers',data.chargers.map(c=>`<article class="vehicle"><h3>${esc(c.name || c.charger_id)}</h3>
    <p>${esc(c.charger_id)} · ${esc(c.operator_id || c.operator || '')}</p><details><summary>Record repair</summary><form><p>Confirm this charger has been repaired.</p>
    ${button('Mark fixed')}${statusBox()}</form></details></article>`).join('') || '<p>No chargers for your approved operators.</p>') +
    section('Open reports',records(data.open_reports,'No open reports.'));
  root.querySelectorAll('form').forEach((form,i)=>onSubmit(form,async()=>{
    const response = await keyedWrite(user,'/cpo/chargers/'+idPath(data.chargers[i].charger_id)+'/fixed');
    form.innerHTML = `<p role="status">Repair recorded ${esc(dateText(response.fixed_at))}.</p><button type="button" class="button button-outline">Refresh reports</button>`;
    form.querySelector('button').onclick = reopen;
  },'Recording repair…'));
}
async function risk(root) {
  const directory = await accountApi('/cpo/directory');
  root.innerHTML = section('Critical chargers',`<form><div class="fields"><label>Corridor<select name="corridor" required>${directory.corridors.map(id=>option(id,id)).join('')}</select></label>
    ${numberInput('range','Usable range (km)',180,1,2000,'any')}</div>${button('Check corridor',true)}${statusBox()}</form><div class="critical"></div>`) +
    section('Suspected reporting rings','<div class="rings">Loading…</div>') +
    section('Shared grid risk','<p>Nearby substations indicate a possible shared risk. Electrical connections are unverified.</p><div class="grid-risk">Loading…</div>');
  const form = root.querySelector('form');
  onSubmit(form,async data=>{
    const node = root.querySelector('.critical'); node.replaceChildren();
    const query = new URLSearchParams({corridor_id:data.get('corridor').trim(),usable_range_km:data.get('range')});
    node.innerHTML = records(await accountApi('/cpo/critical-chargers?' + query),'No critical chargers found for your operators on this corridor.');
  },'Checking corridor…');
  await Promise.all([['.rings','suspected-rings','No suspected reporting rings found.'],['.grid-risk','shared-grid-risk','No shared substation risk found.']].map(async ([selector,path,empty])=>{
    const node = root.querySelector(selector);
    try { node.innerHTML = records(await accountApi('/cpo/'+path),empty); }
    catch (error) { node.textContent = error.message; node.classList.add('error'); }
  }));
}
export async function operators(root, panel, reopen) {
  if (!hasSession()) return login(root,'Sign in with your operator or administrator account to manage claims and charger reports.',reopen);
  const {profile} = await accountApi('/me');
  if (profile.role === 'admin') return admin(root);
  if (profile.role !== 'cpo') {
    root.innerHTML = section('Operator access','<p>An operator or administrator account is required.</p><a class="text-link" href="#account">My account</a>');
    return;
  }
  const selected = ['chargers','risk'].includes(panel) ? panel : 'claims';
  root.innerHTML = links('operators',selected,[['claims','Operator claims'],['chargers','Chargers & reports'],['risk','Risk analysis']]) + '<div class="tool-panel"></div>';
  const area = root.querySelector('.tool-panel');
  if (selected === 'chargers') return chargers(area,reopen,profile.id);
  if (selected === 'risk') return risk(area);
  return claims(area,reopen);
}

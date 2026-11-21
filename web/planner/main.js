import {voice} from './voice.js';
import {graph} from './graph.js';
import {planner} from './journey.js';
import {account, sharedTrip, signOutControl} from './account.js';
import {hasSession} from './client.js';
import {fleet, operators} from './operations.js';
import {esc, heading} from './ui.js';

const views = {
  plan: ['Plan a trip','Choose your route and vehicle to compare charging plans.',planner],
  fleet: ['Fleet bookings','Check which vehicles can take a booking and inspect the engine’s recommendation.',fleet],
  voice: ['Spoken trip','Review the route, vehicle and battery level before confirming your plan.',voice],
  graph: ['Charger graph','Inspect charger relationships or test how simulated reports affect trust.',graph],
  account: ['Garage & trips','Manage your cars, plan with your battery health, and keep your trip history.',account],
  operators: ['Charger operations','Manage operator claims, review charger reports, and record repairs.',operators],
  share: ['Shared trip','View the decision and charging plan shared with you.',sharedTrip],
};
const host = document.querySelector('#view');
document.querySelector('.skip-link').onclick = event => {
  event.preventDefault();
  document.querySelector('#content').focus();
};
async function render() {
  const [key,panel] = location.hash.slice(1).split('/');
  const page = Object.hasOwn(views,key) ? key : 'plan';
  const [title,description,open] = views[page];
  document.title = title + ' · Verity';
  document.querySelectorAll('.tool-tabs a').forEach(a=>{
    if (a.hash === '#' + page) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  host.innerHTML = heading(title,description) + '<div class="page-body"><p role="status">Loading…</p></div>';
  const body = host.querySelector('.page-body');
  host.setAttribute('aria-busy','true');
  try {
    if (page === 'plan') await planner(body);
    else await open(body,panel,render);
  } catch (error) {
    body.innerHTML = `<div class="sheet"><h2>Unable to load this view</h2><p class="error" role="status">${esc(error.message)}</p>
      <button type="button" class="button button-outline">Try again</button><div class="logout"></div></div>`;
    body.querySelector('button').onclick = render;
    if (hasSession()) signOutControl(body.querySelector('.logout'),render);
  } finally {
    if (body.isConnected) host.setAttribute('aria-busy','false');
  }
}
window.addEventListener('hashchange',()=>{render(); window.scrollTo(0,0); host.querySelector('h1')?.focus({preventScroll:true});});
render();

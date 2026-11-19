const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const file = rel => fs.readFileSync(path.join(root, rel), 'utf8');

function load(connected = false) {
  const calls = [];
  const context = { window: { VerityAPI: {
    isApiConnected: () => connected,
    async getDispatch(request) { calls.push(request); return { plan_id: 'response-from-engine' }; },
    async checkRide(request) { calls.push(request); return { id: 'ride-from-engine' }; },
    async updateForecast(request) { calls.push(request); return { action: 'SLOW_DOWN' }; }
  } } };
  vm.createContext(context);
  for (const id of ['js/snapshot', 'drive/cases', 'drive/trip-service']) vm.runInContext(file('web/' + id + '.js'), context);
  return { service: context.window.VerityTrip, calls };
}

test('front door uses the canonical releases for the three fleet vehicles', async () => {
  const { service: s } = load();
  for (const [id, decision] of Object.entries({ V12: 'GO', V07: 'NO_GO', V19: 'INSUFFICIENT_EVIDENCE' })) {
    const { plan } = await s.dispatch(id, s.vehicle(id).soc_pct);
    assert.equal(plan, s.saved.dispatch[id]);
    assert.equal(plan.decision, decision);
  }
  const low = await s.dispatch('V12',40);
  assert.notEqual(low.plan.decision,'GO');
  await assert.rejects(s.dispatch('V12',63), /saved level/);
  await assert.rejects(s.dispatch('bad',62), /vehicles/);
});

test('each offline release has one engine response per supported reading; the separate outage uses its own event', async () => {
  const { service: s } = load();
  for (const [id, samples] of Object.entries(s.cases.dispatch)) for (const soc of Object.keys(samples)) {
    const trip = await s.dispatch(id, Number(soc));
    if (trip.plan.decision !== 'GO') { await assert.rejects(s.forecast(trip,42), /released trip/); continue; }
    assert.equal(trip.sample.forecasts.length, 101);
    for (const value of [0,20,41,100]) {
      const answer = await s.forecast(trip,value);
      assert.ok(['CONTINUE','SLOW_DOWN','DIVERT'].includes(answer.action));
      assert.equal(answer.km_along_route, s.cases.checkpoint.km);
    }
  }
  const flagship = await s.dispatch('V12',62);
  assert.equal((await s.forecast(flagship,41)).action, 'CONTINUE');
  assert.equal((await s.forecast(flagship,20)).action, 'SLOW_DOWN');
  const outage = await s.outage(flagship);
  assert.equal(outage.reading.km,106.6);
  assert.equal(outage.report.charger_id,flagship.plan.stops[0].charger_id);
  assert.equal(outage.answer.divert_to.charger_id,'CH-0003');
  await assert.rejects(s.forecast(flagship,42.5), /whole/);
});

test('airport runs match exact inputs, including the earning rate and the route', async () => {
  const { service: s } = load();
  const standard = { ...s.saved.driverDemo.request, soc:45, rate:null };
  assert.equal(await s.ride(standard), s.saved.rideCheck);
  const changed = await s.ride({ ...standard,rate:120 });
  assert.equal(changed.earning_rate.rs_per_hour,120);
  assert.ok(changed.after_drop.expected_lost_rs > s.saved.rideCheck.after_drop.expected_lost_rs);
  assert.equal((await s.ride({ ...standard,soc:15 })).decision,'NO_GO');
  await assert.rejects(s.ride({ ...standard,soc:47 }), /no saved run/);
  await assert.rejects(s.ride({ ...standard,drop:{lat:28.5,lon:77.1} }), /route/);
});

test('connected dispatch, forecast and ride check retain the entered values and engine plan ID', async () => {
  const { service: s,calls } = load(true);
  const trip = await s.dispatch('V12',63);
  assert.equal(calls[0].start_soc_pct,63);
  await s.forecast(trip,19);
  assert.equal(calls[1].plan_id,'response-from-engine');
  assert.equal(calls[1].soc_pct,19);
  assert.equal(calls[1].lat,s.cases.checkpoint.lat);
  await s.ride({ ...s.saved.driverDemo.request, soc:48,rate:75.5 });
  assert.equal(calls[2].soc_pct,48);
  assert.equal(calls[2].earning_rs_per_hour,75.5);
});

test('practice report is simulated and every saved voice sentence references a packaged recording', () => {
  const { service: s } = load();
  assert.equal(s.cases.practice.report.is_simulated,true);
  assert.ok(s.cases.practice.plans_checked > 0);
  const trust = s.cases.chargers[s.cases.practice.report.charger_id].trust;
  assert.deepEqual(trust,s.cases.practice.report.trust_before);
  for (const clip of s.cases.clips) assert.ok(fs.existsSync(path.join(root,'data/audio_cache',clip.file)));
});

test('logger requires two accurate positions and two battery readings before export', () => {
  const validate = file('logger/index.html').match(/function validate\(t\)\{[^\n]+/)[0];
  const check = vm.runInNewContext('(' + validate + ')');
  const sample={ schema:'verity.trip.v1',trip_id:'demo',driver_id:'anon',vehicle:{make:'Test',model:'EV',variant:'1',nominal_kwh:40,type:'car'},
    passengers:1,cargo_kg:0,ac_on:true,started_at:'t0',ended_at:'t1',notes:'',is_simulated:false,
    points:[{},{}],soc:[{pct:62,source:'tap',t:'t0'},{pct:41,source:'tap',t:'t1'}] };
  assert.equal(check(sample).length,0);
  assert.ok(check({...sample,points:[{}]}).length);
  assert.ok(check({...sample,soc:sample.soc.slice(0,1)}).length);
});

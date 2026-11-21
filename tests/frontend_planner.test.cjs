const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname,'..');
const read = name => JSON.parse(fs.readFileSync(path.join(root,name),'utf8'));
const core = require('../web/js/verity-core.js');
global.window = {VerityCore:core};
global.location = new URL('https://verity.example');
const release = read('docs/api_examples/dispatch_V12.json');

test('releases show the returned band, run count, censored confidence and simulation flag', async () => {
  const ui = await import('../web/planner/ui.js');
  const html = ui.release(release);
  for (const value of [release.band_method.name, release.band_method.note, core.confidenceText(release),
    'Includes simulated inputs']) assert.ok(html.includes(core.escapeHtml(value)));
  assert.equal(core.confidenceText({confidence:0.873,n_runs:41}), '85% · 41 runs');
  assert.equal(core.confidenceText({confidence:0,n_runs:9,confidence_display:'under 5%'}),'under 5% · 9 runs');
  for (const row of [{confidence:null,n_runs:2},{confidence:.9},{confidence:3,n_runs:1}])
    assert.equal(core.confidenceText(row), 'Unavailable');
});

test('voice decisions use the confirmed read-back and require a complete stage', async () => {
  const {confirmation} = await import('../web/planner/voice.js');
  const slots = {origin:{lat:1,lon:2},destination:{lat:3,lon:4},vehicle_id:'car',soc_pct:0,
    departure_time:'2026-11-21T08:30:00+05:30',ac_on:false,passengers:2};
  const heard = {stage:'confirm',needs_confirmation:true,reply_language:'hi-IN',slots:{...slots,parsed_by:'rules'}};
  for (const confirmed of [true,false]) {
    assert.deepEqual(confirmation(heard,confirmed,false),{...slots,language:'hi-IN',confirmed,speak:false});
  }
  assert.throws(()=>confirmation({...heard,stage:'clarify'},true,false),/Clarify/);
});

test('production offline errors preserve the offered trips and only select on click', async t => {
  const {api} = await import('../web/planner/client.js');
  const {routeError} = await import('../web/planner/journey.js');
  const trip = {slug:'cached',origin:{name:'Start',lat:1,lon:2},destination:{name:'End',lat:3,lon:4},
    departure_time:'2026-11-21T08:30:00+05:30'};
  t.mock.method(global,'fetch',async()=>Response.json({id:'trace',error:'Offline',code:'offline',cached_trips:[trip]},{status:503}));
  const status = {}, results = {children:[],replaceChildren(){this.children=[]},append(b){this.children.push(b)}};
  global.document = {createElement:()=>({})};
  let selected;
  await assert.rejects(api('/plan',{method:'POST',body:{}}),error=>{
    routeError(error,status,results,choice=>{selected=choice});
    return error.code === 'offline' && error.status === 503;
  });
  assert.equal(selected,undefined);
  assert.match(results.children[0].textContent,/Start.*End.*IST/);
  results.children[0].onclick();
  assert.deepEqual(selected,trip);
  assert.equal(results.children.length,0);
  routeError({status:422,code:'route_too_long',message:'Over the limit.'},status,results);
  assert.match(status.textContent,/shorter leg/);
  routeError({status:503,code:'route_unavailable'},status,results);
  assert.match(status.textContent,/Retry/);
  routeError({status:422},status,results);
  assert.match(status.textContent,/Check the route/);
});

test('voice recordings preserve the multipart boundary and transcript requests use JSON', async t => {
  const {api} = await import('../web/planner/client.js');
  const calls = [];
  t.mock.method(global,'fetch',async (url,opts)=>{calls.push(opts);return Response.json({});});
  const audio = new FormData();
  audio.append('audio',new Blob(['fixture'],{type:'audio/wav'}),'trip.wav');
  await api('/voice/plan',{method:'POST',body:audio});
  await api('/voice/plan',{method:'POST',body:{transcript:'test'}});
  assert.equal(calls[0].body,audio);
  assert.equal(calls[0].headers['Content-Type'],undefined);
  assert.deepEqual(JSON.parse(calls[1].body),{transcript:'test'});
});

test('Hindi catalogs preserve each English key and placeholder count', () => {
  const en = read('web/i18n/en.json'), hi = read('web/i18n/hi.json');
  const tokens = value=>(value.match(/\{[\w.-]+\}/g)||[]).sort();
  assert.deepEqual(Object.keys(hi).sort(),Object.keys(en).sort());
  for (const group of Object.keys(en)) {
    assert.deepEqual(Object.keys(hi[group]).sort(),Object.keys(en[group]).sort());
    for (const [key,value] of Object.entries(en[group])) {
      assert.ok(hi[group][key].trim(),key);
      assert.deepEqual(tokens(hi[group][key]),tokens(value),key);
    }
  }
});

test('fleet booking sends the entered pickup batteries and leaves depot routing to the API', async () => {
  const {bookingRequest} = await import('../web/planner/journey.js');
  const data = new FormData(), journey = {passengers:3,ac_on:false};
  data.set('fleetMode','pickup');
  for (const [model,battery] of [['car-a','0'],['car-b','84']]) {
    data.append('model',model);
    data.append('battery',battery);
  }
  assert.deepEqual(bookingRequest(journey,data),{...journey,vehicles:[
    {vehicle_id:'car-a',soc_pct:0},{vehicle_id:'car-b',soc_pct:84}]});
  data.set('fleetMode','demo');
  assert.deepEqual(bookingRequest(journey,data),journey);
});

test('an uncertain report or repair retries with its original request key', async t => {
  const values = new Map([['verity.session:https://verity.example',JSON.stringify({
    access_token:'test-session',expires_at:Date.now()/1000+600})]]);
  global.sessionStorage = {getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
  const {keyedWrite} = await import('../web/planner/client.js?retry-test');
  const sent = [];
  t.mock.method(global,'fetch',async (url,request)=>{
    sent.push(request.headers['Idempotency-Key']);
    if (sent.length % 2) throw new Error('Connection lost after submission');
    return Response.json({saved:true});
  });
  try {
    for (const path of ['/reports/quick','/cpo/chargers/test/fixed']) {
      const body = path.includes('quick') ? {charger_id:'test',status:'working'} : undefined;
      await assert.rejects(keyedWrite('owner',path,body), /reach the service/);
      assert.deepEqual(await keyedWrite('owner',path,body),{saved:true});
    }
    assert.ok(sent.every(Boolean));
    assert.equal(sent[0],sent[1]);
    assert.equal(sent[2],sent[3]);
    assert.notEqual(sent[0],sent[2]);
    assert.equal(values.size,1);
  } finally { delete global.sessionStorage; }
});

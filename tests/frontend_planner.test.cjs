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

test('microphone recordings become a named audio file and an empty recording is refused', async () => {
  const {recordedFile, micSupported, MAX_RECORDING_SECONDS} = await import('../web/planner/voice.js');
  const webm = recordedFile([new Blob(['abc'])], 'audio/webm;codecs=opus');
  assert.equal(webm.name, 'trip.webm');
  assert.equal(webm.type, 'audio/webm');
  assert.equal(webm.size, 3);
  assert.equal(recordedFile([new Blob(['x'])], 'audio/mp4').name, 'trip.mp4');
  assert.equal(recordedFile([new Blob(['x'])], 'audio/ogg;codecs=opus').name, 'trip.ogg');
  assert.equal(recordedFile([new Blob(['x'])], '').name, 'trip.webm');
  assert.equal(recordedFile([], 'audio/webm'), null);
  assert.equal(recordedFile([new Blob([])], 'audio/webm'), null);
  assert.equal(micSupported({navigator: {}}), false);
  assert.equal(micSupported({navigator: {mediaDevices: {getUserMedia() {}}}}), false);
  assert.equal(micSupported({navigator: {mediaDevices: {getUserMedia() {}}}, MediaRecorder: class {}}), true);
  assert.ok(MAX_RECORDING_SECONDS > 0 && MAX_RECORDING_SECONDS <= 60);
});

test('the charger graph is layered, names are readable and unique, and nothing overlaps', async () => {
  globalThis.window = globalThis.window || {};
  window.VerityCore = window.VerityCore || {escapeHtml: v => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};
  const g = await import('../web/planner/graph_view.js');
  const {neighborhood, provenance} = await import('../web/planner/graph.js');
  const node = (id, label, props = {}) => ({id, label, props});
  const edge = (from, to, type, props = {}) => ({from, to, type, props});
  const data = {
    charger_id: 'CH-0003', depth: 2,
    nodes: [
      node('Charger:CH-0003', 'Charger', {name: 'EV charging station (OpenStreetMap)', operator: ''}),
      node('Charger:CH-0004', 'Charger', {name: 'Tata'}), node('Charger:CH-0005', 'Charger', {name: 'Tata'}),
      node('Charger:CH-0006', 'Charger', {name: '<img src=x onerror=alert(1)>'}),
      node('Site:CH-0003', 'Site'), node('Operator:Tata', 'Operator'), node('Substation:way/9', 'Substation'),
      node('Plan:PL-1', 'Plan', {decision: 'GO', vehicle_id: 'tata-tiago-ev-24'}), node('Plan:PL-2', 'Plan', {decision: 'INSUFFICIENT_EVIDENCE', vehicle_id: 'V07'}),
      node('Plan:PL-3', 'Plan', {decision: 'NO_GO', vehicle_id: 'V12'}),
      node('Source:demo-user-0', 'Source', {is_simulated: true}),
      node('Source:ring-attack-5', 'Source', {is_simulated: true}), node('Source:user-abc', 'Source'),
      node('Channel:app_user', 'Channel'), node('Ring:r1', 'Ring'),
    ],
    edges: [
      edge('Charger:CH-0003', 'Site:CH-0003', 'AT'), edge('Charger:CH-0003', 'Operator:Tata', 'RUN_BY'),
      edge('Charger:CH-0003', 'Substation:way/9', 'NEAREST_SUBSTATION', {distance_km: 1.2}),
      edge('Charger:CH-0004', 'Substation:way/9', 'NEAREST_SUBSTATION', {distance_km: 2}),
      edge('Plan:PL-1', 'Charger:CH-0003', 'BACKUP_FOR'), edge('Plan:PL-2', 'Charger:CH-0003', 'STOPS_AT'),
      edge('Plan:PL-1', 'Charger:CH-0003', 'BACKUP_FOR'), edge('Plan:PL-3', 'Charger:CH-0004', 'STOPS_AT'),
      edge('Plan:PL-3', 'Charger:CH-0003', 'AVOIDED'),
      edge('Source:ring-attack-5', 'Charger:CH-0003', 'REPORTED', {is_simulated: true}), edge('Source:user-abc', 'Charger:CH-0003', 'REPORTED'),
      edge('Source:ring-attack-5', 'Channel:app_user', 'VIA'), edge('Source:ring-attack-5', 'Ring:r1', 'MEMBER_OF'),
    ],
  };
  const vehicles = new Map([['tata-tiago-ev-24', 'Tata Tiago EV (24 kWh)']]);
  const text = g.labels(data, data.nodes, vehicles);
  assert.equal(text.get('Charger:CH-0003'), 'EV charging station · CH-0003');  // the generic OSM name gets its id
  assert.equal(text.get('Operator:Tata'), 'Operator: Tata');
  assert.equal(text.get('Site:CH-0003'), 'Charging site');
  assert.equal(text.get('Substation:way/9'), 'Nearest substation · 1.2 km');
  assert.equal(text.get('Plan:PL-1'), 'GO for Tata Tiago EV (24 kWh) · backup stop');
  assert.equal(text.get('Source:demo-user-0'), 'Demo driver (simulated)');
  assert.equal(text.get('Plan:PL-2'), 'Not enough evidence for V07 · stops here');
  assert.equal(text.get('Source:ring-attack-5'), 'Ring source 5');
  assert.equal(text.get('Source:user-abc'), 'Signed-in driver');
  assert.equal(text.get('Charger:CH-0004'), 'Tata · CH-0004');  // two chargers named Tata: each gets its id
  assert.equal(text.get('Charger:CH-0005'), 'Tata · CH-0005');
  assert.equal(new Set(text.values()).size, text.size);  // every label on screen is unique
  assert.deepEqual(g.wrap('EV charging station · CH-0003'), ['EV charging station · CH-0003']);  // fits on one line
  assert.deepEqual(g.wrap('Site of EV charging station · CH-0003'), ['Site of EV charging station ·', 'CH-0003']);
  assert.equal(g.wrap('x'.repeat(80)).length, 1);
  assert.ok(g.wrap('word '.repeat(30)).every(line => line.length <= 31));
  // Layout: columns left to right, fixed row spacing, inside the drawing, identical on every call.
  const view = g.layout(data), x = id => view.pos.get(id)[0], y = id => view.pos.get(id)[1];
  assert.ok(x('Source:user-abc') < x('Plan:PL-1') && x('Plan:PL-1') < x('Charger:CH-0003') && x('Charger:CH-0003') < x('Site:CH-0003'));
  assert.equal(x('Channel:app_user'), x('Source:user-abc'));
  assert.equal(x('Site:CH-0003'), x('Charger:CH-0004'));
  assert.deepEqual(view.headings.map(h=>h.text), ['Who reported it', 'Trips that use it', 'This charger', 'Where it is & who runs it']);
  assert.deepEqual([...g.layout(data).pos], [...view.pos]);
  const byColumn = new Map();
  for (const [id, [px, py]] of view.pos) {
    assert.ok(px >= 0 && px <= view.width && py >= 0 && py <= view.height, id);
    byColumn.set(px, (byColumn.get(px) || []).concat(py).sort((a, b) => a - b));
  }
  for (const ys of byColumn.values()) ys.slice(1).forEach((value, i) => assert.ok(value - ys[i] >= g.ROW_GAP, 'rows keep their spacing'));
  const impact = g.outageImpact(data);
  assert.deepEqual([...impact.stops], ['Plan:PL-2']);
  assert.deepEqual([...impact.backups], ['Plan:PL-1']);
  const svg = g.graphSvg(data, vehicles);
  assert.match(svg, /node-plan plan-go/);
  assert.match(svg, /node-plan plan-no-go/);
  assert.match(svg, /node-plan plan-uncertain/);
  assert.doesNotMatch(svg, /outage-(stop|backup)/);
  assert.match(g.graphSvg(data, vehicles, impact), /plan-uncertain outage-stop/);
  assert.match(g.graphSvg(data, vehicles, impact), /plan-go outage-backup/);
  assert.match(neighborhood(data, vehicles), /<details><summary>Nodes/);
  assert.match(neighborhood(data, vehicles), /<details><summary>Relationships/);
  assert.equal((svg.match(/node-source/g) || []).length, 3);
  assert.equal((svg.match(/node-source sim/g) || []).length, 2);
  assert.match(svg, /data-charger="CH-0004"/);
  assert.doesNotMatch(svg, /data-charger="CH-0003"/);  // the focal charger is not a link to itself
  assert.doesNotMatch(svg, /<img/);  // names are escaped
  assert.match(svg, /overflow|graph-scroll/);
  assert.deepEqual(g.summary(data).reports, {real: 1, simulated: 1});
  assert.match(provenance(data), /1 real, 1 simulated/);
});

test('a crowded neighbourhood stays inside twelve rows per column and says how many it left out', async () => {
  globalThis.window = globalThis.window || {};
  window.VerityCore = window.VerityCore || {escapeHtml: v => String(v)};
  const g = await import('../web/planner/graph_view.js');
  const nodes = [{id: 'Charger:CH-1', label: 'Charger', props: {name: 'Focal'}}, {id: 'Substation:s', label: 'Substation', props: {}}];
  const edges = [{from: 'Charger:CH-1', to: 'Substation:s', type: 'NEAREST_SUBSTATION', props: {distance_km: 1}}];
  for (let i = 0; i < 40; i++) {
    nodes.push({id: 'Charger:CH-9' + i, label: 'Charger', props: {name: 'Neighbour ' + i}});
    edges.push({from: 'Charger:CH-9' + i, to: 'Substation:s', type: 'NEAREST_SUBSTATION', props: {distance_km: 2}});
  }
  const view = g.layout({charger_id: 'CH-1', nodes, edges});
  assert.ok(view.height <= 64 + 40 + g.MAX_ROWS * g.ROW_GAP);
  assert.equal(view.hidden.get(3), 41 - (g.MAX_ROWS - 1));
  assert.match(g.graphSvg({charger_id: 'CH-1', nodes, edges}), /\+30 more \(see the tables below\)/);
});

test('charger voice reports require a concrete read-back and first-person consent', async () => {
  const {reportConfirmation} = await import('../web/planner/voice.js');
  const heard = {stage:'confirm',needs_confirmation:true,charger:{charger_id:'CH-1'},status:'working',
    transcript:'charger working',reply_language:'en-IN'};
  for (const incomplete of [{stage:'clarify'},{needs_confirmation:false},{charger:null},{status:null}])
    assert.throws(()=>reportConfirmation({...heard,...incomplete},true,false));
  assert.throws(()=>reportConfirmation(heard,false,false));
  assert.deepEqual(reportConfirmation(heard,true,false),{confirmed:true,consent:true,from_real_person:true,
    charger_id:'CH-1',status:'working',transcript:'charger working',language:'en-IN',speak:false});
});

test('illustrative prices require a source and probabilities require a sample size', () => {
  assert.equal(core.illustrativeBlocks({}), '');
  const assured = {eligible:true,price_rs:15,p_strand:0.16};
  assert.doesNotMatch(core.illustrativeBlocks({assured}), /₹|15%/);
  const text = core.illustrativeBlocks({assured:{...assured,tow_cost_source:'https://example.org/quote'},n_runs:35});
  assert.match(text,/Illustrative cover/);
  assert.match(text,/₹15/);
  assert.match(text,/15% · 35 runs/);
  assert.doesNotMatch(core.illustrativeBlocks({reward:{eligible:true,amount_rs:10}}), /₹/);
});

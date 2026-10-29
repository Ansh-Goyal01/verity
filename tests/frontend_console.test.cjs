// Run: node --test tests/frontend_console.test.cjs
// Saved API examples are real engine outputs, not invented chart fixtures.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const read=name=>JSON.parse(fs.readFileSync(path.join(root,'docs/api_examples',name+'.json'),'utf8'));
const source=fs.readFileSync(path.join(root,'web/js/console.js'),'utf8');

async function render(change=()=>{}, supplied=null){
  const scenario=supplied?.scenario||read('demo_scenario');
  const plans=supplied?.plans||Object.fromEntries(['V07','V12','V19'].map(v=>[v,read('dispatch_'+v)]));
  change(plans.V12,scenario);
  const elements=new Map();
  const element=key=>{
    if(!elements.has(key))elements.set(key,{innerHTML:'',textContent:'',hidden:false,className:'',
      after(child){if(child?.id)elements.set('#'+child.id,child)},remove(){},setAttribute(){},querySelectorAll(){return []}});
    return elements.get(key);
  };
  const layers=new Set(),handlers={},credits=[];
  const base={on(event,fn){handlers[event]=fn;return this},addTo(){layers.add(this);return this},redraw(){this.retried=true;}};
  const map={attributionControl:{addAttribution(c){credits.push(c)}},setView(){return this},hasLayer:l=>layers.has(l),
    removeLayer:l=>layers.delete(l),whenReady(fn){fn()},invalidateSize(){},fitBounds(){}};
  const marker=()=>({bindTooltip(){return this},addTo(){return this}});
  const context={document:{querySelector:element,querySelectorAll:()=>[],createElement:()=>element(Symbol()),documentElement:{dataset:{theme:'dark'}}},
    window:{addEventListener(){}},ResizeObserver:class{observe(){}},setTimeout:()=>0,clearTimeout(){},requestAnimationFrame:fn=>fn(),
    VerityAPI:{getScenario:async()=>scenario,getDispatch:async req=>plans[req.vehicle_id],getChargers:async()=>({chargers:[]})},
    L:{map:()=>map,tileLayer:()=>base,polyline:marker,circleMarker:marker,layerGroup:marker,latLngBounds:x=>x}};
  context.window.L=context.L;
  await vm.runInNewContext(source,context);
  return {html:element('#waterfall').innerHTML,elements,base,handlers,layers,credits};
}

test('contract data renders start, all terms and arrival without NaN or clipped rotated labels',async()=>{
  const result=await render(),html=result.html;
  const plan=read('dispatch_V12'),soc=read('demo_scenario').vehicles.find(v=>v.vehicle_id==='V12').soc_pct;
  assert.equal(plan.starting_battery_pct,undefined);
  assert.equal(soc+plan.breakdown.reduce((s,t)=>s+t.soc_pct,0),plan.arrival_soc.p50);
  assert(html.includes(`Start: ${soc}%`));assert(html.includes(`Arrival: ${plan.arrival_soc.p50}%`));
  for(const row of plan.breakdown)assert(html.includes(row.label+': '));
  assert.equal((html.match(/<g>/g)||[]).length,plan.breakdown.length+2);
  assert(!/NaN|Infinity|undefined|rotate\(/.test(html));assert(html.includes('<tspan'));
  for(const match of html.matchAll(/height="([^"]+)"/g))assert(Number(match[1])>=0);
  assert(html.includes('(simulated)'));
});

test('missing/empty/invalid breakdown and missing starting SoC are explicit, never filled with zeros',async()=>{
  for(const change of [d=>delete d.breakdown,d=>d.breakdown=[],d=>d.breakdown[0].soc_pct=null,
    d=>d.breakdown[0].soc_pct=NaN,d=>delete d.arrival_soc.p50,(_d,s)=>delete s.vehicles.find(v=>v.vehicle_id==='V12').soc_pct]){
    const {html}=await render(change);assert(html.includes('unavailable')||html.includes('invalid'));
    assert(html.includes('No values have been assumed'));assert(!html.includes('<svg'));
  }
});

test('mismatched sums are disclosed and labels are escaped',async()=>{
  const {html}=await render(d=>{d.arrival_soc.p50+=10;d.breakdown[0].label='<script>alert(1)</script>'});
  assert(html.includes('do not reconcile'));assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));
});

test('tile failures persist after partial tile success; route-only and retry preserve credit',async()=>{
  const r=await render(),notice=r.elements.get('#mapnotice');
  r.handlers.tileerror();assert(notice.textContent.includes('unavailable or incomplete'));
  r.handlers.tileload();r.handlers.load();assert(notice.textContent.includes('unavailable or incomplete'));
  r.elements.get('#map-route-only').onclick();assert(!r.layers.has(r.base));assert(notice.textContent.includes('Route-only'));
  r.handlers.tileerror();assert(notice.textContent.includes('Route-only'));
  assert(r.credits.some(c=>c.includes('OpenStreetMap')&&c.includes('copyright')));
  r.elements.get('#map-retry').onclick();assert(r.layers.has(r.base));r.handlers.tileload();r.handlers.load();assert.equal(notice.textContent,'');
  r.elements.get('#map-retry').onclick();assert(r.base.retried);
});

test('live local API matches chart contract when explicitly requested', {skip:!process.env.VERITY_TEST_API},async()=>{
  const base=process.env.VERITY_TEST_API;
  const health=await (await fetch(base+'/api/health')).json();assert.equal(health.neo4j,'in_memory');
  const scenario=await (await fetch(base+'/api/demo/scenario')).json(),b=scenario.booking,plans={};
  for(const v of scenario.vehicles){
    const response=await fetch(base+'/api/dispatch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      vehicle_id:v.vehicle_id,origin:b.origin,destination:b.destination,departure_time:b.departure_time,start_soc_pct:v.soc_pct,
      passengers:b.passengers,ac_on:b.ac_on,target_confidence:b.target_confidence})});
    assert(response.ok);plans[v.vehicle_id]=await response.json();
  }
  const {html}=await render(()=>{},{scenario,plans});assert(html.includes('<svg'));assert(!/NaN|Infinity|undefined/.test(html));
  for(const v of scenario.vehicles)assert(Math.abs(v.soc_pct+plans[v.vehicle_id].breakdown.reduce((s,t)=>s+t.soc_pct,0)-plans[v.vehicle_id].arrival_soc.p50)<=1);
});

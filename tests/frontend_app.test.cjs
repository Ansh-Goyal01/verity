// Run: node --test tests/frontend_app.test.cjs
// Checks the Mission Control helpers and that web/js/snapshot.js matches the saved engine outputs.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const C=require(path.join(root,'web/js/verity-core.js'));
const read=name=>JSON.parse(fs.readFileSync(path.join(root,'docs/api_examples',name+'.json'),'utf8'));
function snapshot(){const ctx={window:{}};vm.runInNewContext(fs.readFileSync(path.join(root,'web/js/snapshot.js'),'utf8'),ctx);return JSON.parse(JSON.stringify(ctx.window.VERITY_SNAPSHOT));}

test('snapshot bundles the saved engine outputs unchanged',()=>{
  const s=snapshot();
  assert.equal(s.is_simulated,true);
  assert.deepEqual(s.api.scenario,read('demo_scenario'));
  for(const v of ['V07','V12','V19']){assert.deepEqual(s.api.dispatch[v],read('dispatch_'+v));assert.deepEqual(s.api.replay[v],read('replay_'+v));}
  assert.deepEqual(s.api.chargers,read('chargers'));
});

test('snapshot results are copied from results/ without edits',()=>{
  const s=snapshot(),res=f=>JSON.parse(fs.readFileSync(path.join(root,'results',f),'utf8'));
  assert.deepEqual(s.results.e4.scenarios,res('e4_dispatch_vs_fixed_simulated.json').scenarios);
  assert.deepEqual(s.results.e3.scenarios,res('e3_reliability_planning_simulated.json').scenarios);
  assert.deepEqual(s.results.e5.scenarios,res('e5_trust_recovery_simulated.json').scenarios);
  const e2=res('e2_calibration_simulated.json');
  assert.equal(s.results.e2.methods.shared.coverage_90_arrival_band,e2.methods.shared.coverage_90_arrival_band);
  const e1=res('e1_learning_curve_simulated.json');
  assert.equal(s.results.e1.methods.verity_pooled[0].mae_pp,e1.methods.verity_pooled['1'].mae_pp);
  for(const k of ['e1','e2','e3','e4','e5'])assert.equal(s.results[k].is_simulated,true);
});

test('waterfall reconciles real plans and refuses to fill gaps',()=>{
  const scen=read('demo_scenario');
  for(const v of scen.vehicles){const p=read('dispatch_'+v.vehicle_id),wf=C.waterfall(v.soc_pct,p.breakdown,p.arrival_soc.p50);
    assert.equal(wf.ok,true);assert.equal(wf.reconciles,true);assert.equal(wf.rows.length,p.breakdown.length+2);}
  assert.equal(C.waterfall(undefined,[{term:'a',label:'A',soc_pct:-1}],5).ok,false);
  assert.equal(C.waterfall(60,[],5).ok,false);
  assert.equal(C.waterfall(60,[{term:'a',label:'A',soc_pct:null}],5).ok,false);
  assert.equal(C.waterfall(60,[{term:'a',label:'A',soc_pct:-10}],40).reconciles,false);
});

test('bingo interpolation, nearest point and formatting',()=>{
  const line=[{km:0,bingo_soc_pct:40},{km:10,bingo_soc_pct:20},{km:20,bingo_soc_pct:20}];
  assert.equal(C.bingoAt(line,5),30);assert.equal(C.bingoAt(line,99),20);assert.equal(C.bingoAt([],5),null);
  assert.equal(C.nearestPoint(line,8).km,10);
  assert.equal(C.pct(0.95),'95%');assert.equal(C.pct(null),'—');
  assert.equal(C.decisionLabel('NO_GO'),'NO-GO');assert.equal(C.decisionClass('INSUFFICIENT_EVIDENCE'),'unknown');
  assert.equal(C.fmtClock(4514),'1:15:14');assert.equal(C.fmtDuration(176),'2 h 56 min');
  assert.equal(C.escapeHtml('<b x="1">'),'&lt;b x=&quot;1&quot;&gt;');
});

test('route positions stay on the polyline and replay events fire in order',()=>{
  const p=read('dispatch_V12'),pts=p.route.polyline,cum=C.cumulativeKm(pts);
  assert.deepEqual(C.pointAtKm(pts,cum,p.route.distance_km,0),pts[0]);
  const end=C.pointAtKm(pts,cum,p.route.distance_km,p.route.distance_km);
  assert(Math.abs(end[0]-pts[pts.length-1][0])<1e-9&&Math.abs(end[1]-pts[pts.length-1][1])<1e-9);
  const r=read('replay_V12'),a=C.dueEvents(r.events,0,2000),b=C.dueEvents(r.events,a.next,1e9);
  assert.equal(a.events.length+b.events.length,r.events.length);assert(a.events.every(e=>e.t_offset_s<=2000));
});

test('charger stats only count the supplied directory',()=>{
  const c=read('chargers').chargers,st=C.chargerStats(c);
  assert.equal(st.total,c.length);
  assert.equal(Object.values(st.byTrust).reduce((x,y)=>x+y,0),c.length);
  assert.equal(st.withReports,c.filter(x=>x.n_reports>0).length);
});

test('pages only reference local files that exist (offline demo stays intact)',()=>{
  for(const page of ['web/home.html','web/index.html','index.html']){
    const html=fs.readFileSync(path.join(root,page),'utf8'),dir=path.dirname(path.join(root,page));
    const refs=[...html.matchAll(/(?:src|href)="([^"#?]+)"/g)].map(m=>m[1]).filter(u=>!/^(https?:|data:|mailto:)/.test(u));
    assert(refs.length>0,page);
    for(const r of refs)assert(fs.existsSync(path.join(dir,r)),`${page} references missing ${r}`);
  }
  assert(fs.readFileSync(path.join(root,'web/vendor/motion-12.43.0.js'),'utf8').includes('.Motion={}'));
});

(async function(){const $=s=>document.querySelector(s), bookingEl=$('#booking'),vehiclesEl=$('#vehicles'),status=$('#status');let scenario,dispatches={},directory={chargers:[]},map,routeLayer,chargerLayer,selected='V12';const pct=x=>Number.isFinite(x)?`${Math.round(x*20)*5}%`:'—', coord=c=>[c.lat,c.lon];function fmtTime(s){return new Date(s).toLocaleString('en-IN',{dateStyle:'medium',timeStyle:'short'});}function svgText(x,y,t,size=10,color='var(--muted)',anchor='middle'){return `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${color}" font-size="${size}" font-family="IBM Plex Mono,monospace">${t}</text>`;}
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function decisionLabel(value){return value==='NO_GO'?'NO-GO':value==='INSUFFICIENT_EVIDENCE'?'INSUFFICIENT EVIDENCE':'GO';}
function card(vehicle, plan) {
  const id = escape(vehicle.vehicle_id);
  const name = id + ' · ' + escape(vehicle.label);
  if (!plan) return `<button class="vehicle" disabled><strong>${name}</strong><p>Release unavailable</p></button>`;
  const refused = plan.decision === 'INSUFFICIENT_EVIDENCE';
  const selectedCard = selected === vehicle.vehicle_id;
  const detail = refused ? `<p class="refusal-card">${escape(plan.refusal_reason)}</p>` :
    `<div class="metricrow"><span>Confidence</span><b>${escape(plan.confidence_display || pct(plan.confidence))} · ${escape(plan.n_runs)} runs</b></div>
     <div class="metricrow"><span>Arrival battery</span><b>${plan.arrival_soc.p10}–${plan.arrival_soc.p90}%</b></div>
     <p>${escape(vehicle.health_note)}</p>`;
  return `<button class="vehicle${selectedCard ? ' selected' : ''}" data-id="${id}" aria-pressed="${selectedCard}">
    <div class="vehiclehead"><strong>${name}</strong><span class="stamp ${escape(plan.decision)}">${escape(decisionLabel(plan.decision))}</span></div>${detail}</button>`;
}
function renderCards() {
  vehiclesEl.innerHTML = scenario.vehicles.map(v => card(v, dispatches[v.vehicle_id])).join('');
  vehiclesEl.querySelectorAll('button[data-id]').forEach(button => {
    button.onclick = () => {
      selected = button.dataset.id;
      renderCards();
      renderSelected();
      vehiclesEl.querySelector('button[aria-pressed="true"]')?.focus({preventScroll:true});
    };
  });
}
let mapResizeObserver,activeMapBounds,fitTimer;
function fitMap(){if(!map||!activeMapBounds||document.querySelector('.mapcard').hidden)return;map.invalidateSize({pan:false,animate:false});map.fitBounds(activeMapBounds,{padding:[32,32],maxZoom:10,animate:false});}
function scheduleMapFit(){clearTimeout(fitTimer);fitTimer=setTimeout(fitMap,120);}
function initMap(d,cs){
  if(!window.L){$('#map').textContent='The map library could not load. Reload the page or reopen the complete project folder.';return;}
  if(!map){
    map=L.map('map',{preferCanvas:true}).setView([27.85,77.69],7);
    const credits='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
    map.attributionControl.addAttribution(credits); // Keep source credit in route-only view too.
    const base=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:credits});
    // Basemap requests are independent of the local API and route overlays.
    const notice=document.createElement('p');notice.id='mapnotice';notice.className='state';notice.setAttribute('role','status');$('#map').after(notice);
    const controls=document.createElement('div');controls.className='mapcontrols';
    controls.innerHTML='<button id="map-retry" class="ghost" type="button">Retry street tiles</button><button id="map-route-only" class="ghost" type="button">Use route-only view</button>';
    notice.after(controls);
    const tileStates=new Map();let routeOnly=false;
    const updateTileNotice=()=>{
      if(routeOnly)return;
      const viewport=map.getContainer().getBoundingClientRect();
      const visible=[...tileStates].filter(([tile])=>{
        const r=tile.getBoundingClientRect();
        return r.width>0&&r.height>0&&r.right>viewport.left&&r.left<viewport.right&&r.bottom>viewport.top&&r.top<viewport.bottom;
      }).map(([,state])=>state);
      if(visible.includes('failed'))notice.textContent='Some visible street-map tiles failed to load. Route and charger locations remain available. Retry the tiles or use route-only view.';
      else if(!visible.length||visible.includes('pending'))notice.textContent='Loading street-map tiles...';
      else notice.textContent='';
    };
    for(const [event,state] of [['tileloadstart','pending'],['tileerror','failed'],['tileload','loaded']]){
      base.on(event,e=>{tileStates.set(e.tile,state);updateTileNotice();});
    }
    base.on('tileunload',e=>{tileStates.delete(e.tile);updateTileNotice();});
    base.on('load',updateTileNotice);
    map.on('moveend zoomend resize',updateTileNotice);
    $('#map-route-only').onclick=()=>{routeOnly=true;if(map.hasLayer(base))map.removeLayer(base);notice.textContent='Route-only view: street tiles are hidden. Route and charger coordinates still come from the selected plan and directory; the plain background is not a street map.';};
    $('#map-retry').onclick=()=>{routeOnly=false;tileStates.clear();notice.textContent='Retrying street-map tiles...';if(!map.hasLayer(base))base.addTo(map);else base.redraw();};
    base.addTo(map);
    mapResizeObserver=new ResizeObserver(scheduleMapFit);mapResizeObserver.observe($('#map'));
    window.addEventListener('resize',scheduleMapFit);
    if(document.fonts)document.fonts.ready.then(scheduleMapFit);
  }
  if(routeLayer)map.removeLayer(routeLayer);if(chargerLayer)map.removeLayer(chargerLayer);
  const points=d.route.polyline.map(p=>[Number(p[0]),Number(p[1])]).filter(p=>p.every(Number.isFinite));
  if(points.length<2){$('#mapnotice').textContent='No route coordinates are available.';return;}
  activeMapBounds=L.latLngBounds(points);
  routeLayer=L.polyline(points,{color:'#567eeb',weight:5,opacity:1}).addTo(map);
  const colors={high:'#8ec6f7',medium:'#f3be68',low:'#f58278',unknown:'#99a7bb'};
  const items=(cs.chargers||[]).map(c=>L.circleMarker(coord(c),{radius:6,color:colors[c.trust_level]||colors.unknown,fillColor:colors[c.trust_level]||colors.unknown,fillOpacity:.95,weight:2}).bindTooltip(`<b>${escape(c.name)}</b><br>${c.p_works===null?'Chance works unknown':pct(c.p_works)+' chance works'}<br>${c.n_reports} reports, ${c.days_since_last===null?'last report unknown':'last '+c.days_since_last+' days ago'}<br>Trust: ${escape(c.trust_level)}`));
  d.stops.forEach(stop=>items.push(L.circleMarker(coord(stop),{radius:10,color:'#152541',weight:2,fillColor:'#9db6ff',fillOpacity:1}).bindTooltip(`<b>PLANNED · ${escape(stop.name)}</b><br>${pct(stop.p_works)} chance works<br>${stop.evidence.n_reports} reports, ${stop.evidence.days_since_last===null?'last report unknown':'last '+stop.evidence.days_since_last+' days ago'}`)));
  d.alternates.forEach(a=>items.push(L.circleMarker(coord(a),{radius:9,color:'#152541',weight:2,dashArray:'4 2',fillColor:'#ccb6fc',fillOpacity:1}).bindTooltip(`<b>ALTERNATE · ${escape(a.name)}</b><br>${pct(a.p_works)} chance works · ${escape(a.trust?.n_reports ?? '—')} reports`)));
  chargerLayer=L.layerGroup(items).addTo(map);
  $('#mapkey').innerHTML=Object.entries(colors).map(([k,c])=>`<span><i class="keydot" style="background:${c}"></i>${k} trust</span>`).join('')+'<span><i class="keydot" style="background:#9db6ff"></i>Planned stop</span><span><i class="keydot" style="background:#ccb6fc"></i>Alternate</span>';
  map.whenReady(()=>requestAnimationFrame(scheduleMapFit));
}

function drawBingo(d){const data=d.bingo_line,W=600,H=270,L=46,R=18,T=18,B=42,x=k=>L+k/d.route.distance_km*(W-L-R),y=v=>T+(100-v)/100*(H-T-B);let area=data.map((p,i)=>`${i?'L':'M'}${x(p.km)},${y(p.expected_soc_p90)}`).join(' ')+' '+data.slice().reverse().map(p=>`L${x(p.km)},${y(p.expected_soc_p10)}`).join(' ')+' Z';let line=(key,color,dash='')=>`<polyline points="${data.map(p=>`${x(p.km)},${y(p[key])}`).join(' ')}" fill="none" stroke="${color}" stroke-width="${key==='bingo_soc_pct'?2.4:2}" ${dash}/>`;let grid=[0,20,40,60,80,100].map(v=>`<path d="M${L} ${y(v)}H${W-R}" stroke="var(--line)"/>${svgText(L-7,y(v)+3,v,9,'var(--muted)','end')}`).join('');const xTicks=[0,50,100,150,200,d.route.distance_km].map(km=>`<path d="M${x(km)} ${H-B}v4" stroke="var(--muted)"/>${svgText(x(km),H-12,km===d.route.distance_km?km.toFixed(1):km,8,'var(--muted)')}`).join('');let stops=d.stops.map(s=>`<path d="M${x(s.km_along_route)} ${H-B}v-13" stroke="var(--accent)" stroke-width="2"/>`).join('');const por=x(d.point_of_no_return_km);$('#bingo').innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Expected battery band, expected battery, Bingo Line"><rect x="0" y="0" width="${W}" height="${H}" fill="transparent"/>${grid}<path d="${area}" fill="var(--accent)" opacity=".2"/>${line('expected_soc_pct','var(--accent)')}${line('bingo_soc_pct','var(--nogo)')}<path d="M${por} ${T}V${H-B}" stroke="var(--nogo)" stroke-dasharray="5 4"/>${svgText(por+3,T+10,`PONR ${d.point_of_no_return_km} km`,8,'var(--nogo)','start')}${stops}${xTicks}${svgText(W/2,H-2,'KM ALONG ROUTE',9)}${svgText(L+2,T+11,'BATTERY %',8,'var(--muted)','start')}</svg><div class="mapkey"><span>Blue band · expected range</span><span>Blue line · expected p50</span><span style="color:var(--nogo)">Red line · Bingo Line</span><span>Blue ticks · charging stop</span></div>`;}
function drawWaterfall(d,start){
  const container=$('#waterfall'),arrival=d.arrival_soc?.p50;
  const unavailable=message=>{container.innerHTML='<p class="state" role="status">'+message+' No values have been assumed.</p>';};
  if(!Array.isArray(d.breakdown)||!d.breakdown.length)return unavailable('Energy breakdown unavailable from the API.');
  if(!d.breakdown.every(row=>row&&typeof row.term==='string'&&typeof row.label==='string'&&typeof row.soc_pct==='number'&&Number.isFinite(row.soc_pct)))return unavailable('Energy breakdown contains unavailable or invalid terms.');
  if(typeof start!=='number'||!Number.isFinite(start))return unavailable('Starting battery unavailable from the scenario.');
  if(typeof arrival!=='number'||!Number.isFinite(arrival))return unavailable('Arrival battery unavailable from the API.');
  const vals=[{label:'Start',v:start},...d.breakdown.map(t=>({label:t.label,v:t.soc_pct})),{label:'Arrival',v:arrival}];
  let cumulative=start;const levels=[0,start,arrival];d.breakdown.forEach(t=>{cumulative+=t.soc_pct;levels.push(cumulative);});
  const low=Math.min(0,...levels),high=Math.max(100,...levels),W=Math.max(700,vals.length*92+76),H=340,L=52,R=24,T=28,B=100;
  const y=v=>T+(high-v)/(high-low)*(H-T-B),bar=38,gap=(W-L-R)/vals.length;
  let html=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Battery percentage energy waterfall${d.is_simulated?' (simulated)':''}"><title>Energy breakdown${d.is_simulated?' (simulated)':''}; changes are battery percentage points</title>`;
  for(let i=0;i<=5;i++){const v=low+(high-low)*i/5;html+=`<path d="M${L} ${y(v)}H${W-R}" stroke="var(--line)"/>${svgText(L-7,y(v)+3,Math.round(v*10)/10,10,'var(--muted)','end')}`;}
  let running=start;
  vals.forEach((it,i)=>{
    const xx=L+i*gap+gap/2-bar/2,last=i===vals.length-1,absolute=i===0||last;
    const from=absolute?0:running,to=absolute?it.v:running+it.v;
    const top=y(Math.max(from,to)),bottom=y(Math.min(from,to)),color=absolute?(last?'var(--go)':'var(--accent)'):(it.v>=0?'var(--go)':'var(--nogo)');
    if(!absolute)running=to;
    const valueLabel=`${!absolute&&it.v>0?'+':''}${it.v}${absolute?'%':' pp'}`,labelX=xx+bar/2;
    html+=`<g><title>${escape(it.label)}: ${escape(valueLabel)}</title>`;
    html+=from===to?`<path d="M${xx} ${top}h${bar}" stroke="${color}" stroke-width="2"/>`:`<rect x="${xx}" y="${top}" width="${bar}" height="${bottom-top}" rx="3" fill="${color}"/>`;
    html+=svgText(labelX,Math.max(14,top-7),valueLabel,11,'var(--text)');
    const lines=[];for(const word of it.label.split(/\s+/)){if(!lines.length||lines[lines.length-1].length+word.length+1>14)lines.push(word);else lines[lines.length-1]+=' '+word;}
    html+=`<text x="${labelX}" y="${H-B+20}" text-anchor="middle" fill="var(--muted)" font-size="11" font-family="IBM Plex Mono,monospace">${lines.map((line,j)=>`<tspan x="${labelX}" dy="${j?14:0}">${escape(line)}</tspan>`).join('')}</text></g>`;
  });
  html+='</svg><p class="state">Energy attribution'+(d.is_simulated?' (simulated)':'')+'. Intermediate totals are by energy term, not a trip timeline.</p>';
  if(Math.abs(cumulative-arrival)>1)html+='<p class="state" role="status">The supplied start and breakdown do not reconcile with the supplied arrival. API values are shown unchanged.</p>';
  container.innerHTML=html;
}

function renderSelected(){const d=dispatches[selected];const refused=d.decision==='INSUFFICIENT_EVIDENCE';document.querySelector('.mapcard').hidden=refused;document.querySelector('.charts').hidden=refused;document.querySelector('.lower').hidden=refused;$('#release').innerHTML=refused?`<div class="refusal"><span class="stamp ${escape(d.decision)}">${decisionLabel(d.decision)}</span><p>${escape(d.refusal_reason)}</p></div>`:`<div class="release"><span class="stamp ${escape(d.decision)}">${decisionLabel(d.decision)}</span><span>Confidence <b>${escape(d.confidence_display || pct(d.confidence))} · ${escape(d.n_runs)} runs</b></span><span>Arrival <b>${d.arrival_soc.p10}–${d.arrival_soc.p90}%</b></span></div>`;if(refused)return;drawBingo(d);drawWaterfall(d,scenario.vehicles.find(v=>v.vehicle_id===d.vehicle_id)?.soc_pct);$('#levers').innerHTML=d.levers.length?d.levers.map(l=>`<button class="lever" data-confidence="${l.new_confidence}" data-cost="${l.time_cost_min}">${escape(l.label)}</button>`).join('')+'<div class="leverresult" aria-live="polite"></div>':'<p class="state">No levers are available.</p>';document.querySelectorAll('.lever').forEach(b=>b.onclick=()=>{$('.leverresult').textContent=`New confidence ${pct(+b.dataset.confidence)} · ${d.n_runs} runs · +${b.dataset.cost} min`;});const groups={unknown:'We lack data',unpredictable:'The road is unpredictable'};$('#risks').innerHTML=Object.entries(groups).map(([kind,label])=>`<div class="riskgroup"><h3>${label}</h3>${d.risk_sources.filter(r=>r.kind===kind).map(r=>`<div class="riskitem">${escape(r.label)}</div>`).join('')||'<div class="state">No sources reported.</div>'}</div>`).join('');initMap(d,directory);}
$('#map-fit').onclick=fitMap;
try {
  scenario = await VerityAPI.getScenario();
  $('#sim').hidden = !scenario.is_simulated;
  const b = scenario.booking;
  bookingEl.textContent = `${b.origin.name} → ${b.destination.name} · ${fmtTime(b.departure_time)} · ${b.passengers} passengers · AC ${b.ac_on ? 'on' : 'off'} · target ${pct(b.target_confidence)}`;
  const plans = scenario.vehicles.map(async vehicle => {
    const plan = await VerityAPI.getDispatch({
      vehicle_id: vehicle.vehicle_id, start_soc_pct: vehicle.soc_pct, origin: {lat:b.origin.lat,lon:b.origin.lon},
      destination: {lat:b.destination.lat,lon:b.destination.lon}, departure_time:b.departure_time,
      passengers:b.passengers, ac_on:b.ac_on, target_confidence:b.target_confidence
    });
    return [vehicle.vehicle_id, plan];
  });
  const [releases, chargers] = await Promise.all([
    Promise.allSettled(plans),
    VerityAPI.getChargers({min_lat:27.1,min_lon:77.2,max_lat:28.7,max_lon:78.3}).catch(() => null)
  ]);
  dispatches = Object.fromEntries(releases.filter(r => r.status === 'fulfilled').map(r => r.value));
  if (!Object.keys(dispatches).length) throw new Error('No vehicle releases are available.');
  if (!dispatches[selected]) selected = Object.keys(dispatches)[0];
  directory = chargers || directory;
  const notices = [];
  if (releases.some(r => r.status === 'rejected')) notices.push('Some vehicle releases could not load.');
  if (!chargers) notices.push('The charger directory could not load. The route and planned stops are shown.');
  status.hidden = notices.length === 0;
  status.textContent = notices.join(' ');
  renderCards();
  renderSelected();
} catch (error) {
  status.hidden = false;
  status.className = 'state error';
  status.textContent = 'Unable to load fleet dispatch: ' + error.message;
  vehiclesEl.innerHTML = '<p class="state">Vehicle plans could not be loaded.</p>';
}
$('#theme').onclick=()=>{const light=document.documentElement.dataset.theme==='light';document.documentElement.dataset.theme=light?'dark':'light';$('#theme').textContent=light?'Light theme':'Dark theme';};})();
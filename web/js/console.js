(async function(){const $=s=>document.querySelector(s), bookingEl=$('#booking'),vehiclesEl=$('#vehicles'),status=$('#status');let scenario,dispatches={},chargers,map,routeLayer,chargerLayer,selected='V12';const pct=x=>`${Math.round(x*100)}%`, coord=c=>[c.lat,c.lon];function fmtTime(s){return new Date(s).toLocaleString('en-IN',{dateStyle:'medium',timeStyle:'short'});}function svgText(x,y,t,size=10,color='var(--muted)',anchor='middle'){return `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${color}" font-size="${size}" font-family="IBM Plex Mono,monospace">${t}</text>`;}
function decisionLabel(value){return value==='NO_GO'?'NO-GO':value==='INSUFFICIENT_EVIDENCE'?'INSUFFICIENT EVIDENCE':'GO';}
function card(v,d){const refused=d.decision==='INSUFFICIENT_EVIDENCE';return `<button class="vehicle ${v.vehicle_id===selected?'selected':''}" data-id="${v.vehicle_id}"><div class="vehiclehead"><strong>${v.vehicle_id} · ${v.label}</strong><span class="stamp ${d.decision}">${decisionLabel(d.decision)}</span></div>${refused?`<p class="refusal-card">${d.refusal_reason}</p>`:`<div class="metricrow"><span>Confidence</span><b>${pct(d.confidence)}</b></div><div class="metricrow"><span>Arrival battery</span><b>${d.arrival_soc.p10}–${d.arrival_soc.p90}%</b></div><p>${v.health_note}</p>`}</button>`;}
function renderCards(){vehiclesEl.innerHTML=scenario.vehicles.map(v=>card(v,dispatches[v.vehicle_id])).join('');vehiclesEl.querySelectorAll('button').forEach(b=>b.onclick=()=>{selected=b.dataset.id;renderCards();renderSelected();});}
let mapResizeObserver,activeMapBounds,fitTimer;
function fitMap(){if(!map||!activeMapBounds||document.querySelector('.mapcard').hidden)return;map.invalidateSize({pan:false,animate:false});map.fitBounds(activeMapBounds,{padding:[32,32],maxZoom:10,animate:false});}
function scheduleMapFit(){clearTimeout(fitTimer);fitTimer=setTimeout(fitMap,120);}
function initMap(d,cs){
  if(!window.L){$('#map').textContent='The map library could not load. Check your internet connection and reload.';return;}
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
    let tileFailures=0,tileSuccesses=0,routeOnly=false;
    const fallback=()=>{notice.textContent='Street-map tiles are unavailable or incomplete. Route and charger locations remain visible over a plain background. Retry the tiles or use route-only view. This does not indicate an API failure.';};
    base.on('tileerror',()=>{tileFailures++;if(!routeOnly)fallback();});
    base.on('tileload',()=>{tileSuccesses++;});
    base.on('load',()=>{if(routeOnly)return;if(tileFailures)fallback();else if(tileSuccesses)notice.textContent='';});
    $('#map-route-only').onclick=()=>{routeOnly=true;if(map.hasLayer(base))map.removeLayer(base);notice.textContent='Route-only view: street tiles are hidden. Route and charger coordinates still come from the selected plan and directory; the plain background is not a street map.';};
    $('#map-retry').onclick=()=>{routeOnly=false;tileFailures=0;tileSuccesses=0;notice.textContent='Retrying street-map tiles...';if(!map.hasLayer(base))base.addTo(map);else base.redraw();};
    base.addTo(map);
    mapResizeObserver=new ResizeObserver(scheduleMapFit);mapResizeObserver.observe($('#map'));
    window.addEventListener('resize',scheduleMapFit);
    if(document.fonts)document.fonts.ready.then(scheduleMapFit);
  }
  if(routeLayer)map.removeLayer(routeLayer);if(chargerLayer)map.removeLayer(chargerLayer);
  const points=d.route.polyline.map(p=>[Number(p[0]),Number(p[1])]).filter(p=>p.every(Number.isFinite));
  if(points.length<2){$('#mapnotice').textContent='No route coordinates are available.';return;}
  activeMapBounds=L.latLngBounds(points);
  routeLayer=L.polyline(points,{color:'#F5A524',weight:5,opacity:1}).addTo(map);
  const colors={high:'#3dd68c',medium:'#f5a524',low:'#ff6b6b',unknown:'#9db4ff'};
  const items=(cs.chargers||[]).map(c=>L.circleMarker(coord(c),{radius:6,color:colors[c.trust_level]||colors.unknown,fillColor:colors[c.trust_level]||colors.unknown,fillOpacity:.95,weight:2}).bindTooltip(`<b>${c.name}</b><br>${c.p_works===null?'Chance works unknown':pct(c.p_works)+' chance works'}<br>${c.n_reports} reports, ${c.days_since_last===null?'last report unknown':'last '+c.days_since_last+' days ago'}<br>Trust: ${c.trust_level}`));
  d.stops.forEach(stop=>items.push(L.circleMarker(coord(stop),{radius:10,color:'#14161A',weight:2,fillColor:'#F5A524',fillOpacity:1}).bindTooltip(`<b>PLANNED · ${stop.name}</b><br>${pct(stop.p_works)} chance works<br>${stop.evidence.n_reports} reports, ${stop.evidence.days_since_last===null?'last report unknown':'last '+stop.evidence.days_since_last+' days ago'}`)));
  d.alternates.forEach(a=>items.push(L.circleMarker(coord(a),{radius:9,color:'#14161A',weight:2,fillColor:'#9db4ff',fillOpacity:1}).bindTooltip(`<b>ALTERNATE · ${a.name}</b><br>${pct(a.p_works)} chance works`)));
  chargerLayer=L.layerGroup(items).addTo(map);
  $('#mapkey').innerHTML=Object.entries(colors).map(([k,c])=>`<span><i class="keydot" style="background:${c}"></i>${k} trust</span>`).join('')+'<span><i class="keydot" style="background:#f5a524"></i>Planned stop</span><span><i class="keydot" style="background:#9db4ff"></i>Alternate</span>';
  map.whenReady(()=>requestAnimationFrame(scheduleMapFit));
}

function drawBingo(d){const data=d.bingo_line,W=600,H=270,L=46,R=18,T=18,B=42,x=k=>L+k/d.route.distance_km*(W-L-R),y=v=>T+(100-v)/100*(H-T-B);let area=data.map((p,i)=>`${i?'L':'M'}${x(p.km)},${y(p.expected_soc_p90)}`).join(' ')+' '+data.slice().reverse().map(p=>`L${x(p.km)},${y(p.expected_soc_p10)}`).join(' ')+' Z';let line=(key,color,dash='')=>`<polyline points="${data.map(p=>`${x(p.km)},${y(p[key])}`).join(' ')}" fill="none" stroke="${color}" stroke-width="${key==='bingo_soc_pct'?2.4:2}" ${dash}/>`;let grid=[0,20,40,60,80,100].map(v=>`<path d="M${L} ${y(v)}H${W-R}" stroke="var(--line)"/>${svgText(L-7,y(v)+3,v,9,'var(--muted)','end')}`).join('');const xTicks=[0,50,100,150,200,d.route.distance_km].map(km=>`<path d="M${x(km)} ${H-B}v4" stroke="var(--muted)"/>${svgText(x(km),H-12,km===d.route.distance_km?km.toFixed(1):km,8,'var(--muted)')}`).join('');let stops=d.stops.map(s=>`<path d="M${x(s.km_along_route)} ${H-B}v-13" stroke="var(--amber)" stroke-width="2"/>`).join('');const por=x(d.point_of_no_return_km);$('#bingo').innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Expected battery band, expected battery, Bingo Line"><rect x="0" y="0" width="${W}" height="${H}" fill="transparent"/>${grid}<path d="${area}" fill="var(--amber)" opacity=".2"/>${line('expected_soc_pct','var(--amber)')}${line('bingo_soc_pct','var(--nogo)')}<path d="M${por} ${T}V${H-B}" stroke="var(--nogo)" stroke-dasharray="5 4"/>${svgText(por+3,T+10,`PONR ${d.point_of_no_return_km} km`,8,'var(--nogo)','start')}${stops}${xTicks}${svgText(W/2,H-2,'KM ALONG ROUTE',9)}${svgText(L+2,T+11,'BATTERY %',8,'var(--muted)','start')}</svg><div class="mapkey"><span>Amber band · expected range</span><span>Amber line · expected p50</span><span style="color:var(--nogo)">Red line · Bingo Line</span><span>Amber ticks · charging stop</span></div>`;}
function drawWaterfall(d,start){
  const container=$('#waterfall'),arrival=d.arrival_soc?.p50;
  const unavailable=message=>{container.innerHTML='<p class="state" role="status">'+message+' No values have been assumed.</p>';};
  if(!Array.isArray(d.breakdown)||!d.breakdown.length)return unavailable('Energy breakdown unavailable from the API.');
  if(!d.breakdown.every(row=>row&&typeof row.term==='string'&&typeof row.label==='string'&&typeof row.soc_pct==='number'&&Number.isFinite(row.soc_pct)))return unavailable('Energy breakdown contains unavailable or invalid terms.');
  if(typeof start!=='number'||!Number.isFinite(start))return unavailable('Starting battery unavailable from the scenario.');
  if(typeof arrival!=='number'||!Number.isFinite(arrival))return unavailable('Arrival battery unavailable from the API.');
  const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
    const top=y(Math.max(from,to)),bottom=y(Math.min(from,to)),color=absolute?(last?'var(--go)':'var(--amber)'):(it.v>=0?'var(--go)':'var(--nogo)');
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

function renderSelected(){const d=dispatches[selected];const refused=d.decision==='INSUFFICIENT_EVIDENCE';document.querySelector('.mapcard').hidden=refused;document.querySelector('.charts').hidden=refused;document.querySelector('.lower').hidden=refused;$('#release').innerHTML=refused?`<div class="refusal"><span class="stamp ${d.decision}">${decisionLabel(d.decision)}</span><p>${d.refusal_reason}</p></div>`:`<div class="release"><span class="stamp ${d.decision}">${decisionLabel(d.decision)}</span><span>Confidence <b>${pct(d.confidence)}</b></span><span>Arrival <b>${d.arrival_soc.p10}–${d.arrival_soc.p90}%</b></span></div>`;if(refused)return;drawBingo(d);drawWaterfall(d,scenario.vehicles.find(v=>v.vehicle_id===d.vehicle_id)?.soc_pct);$('#levers').innerHTML=d.levers.length?d.levers.map(l=>`<button class="lever" data-confidence="${l.new_confidence}" data-cost="${l.time_cost_min}">${l.label}</button>`).join('')+'<div class="leverresult" aria-live="polite"></div>':'<p class="state">No levers are available.</p>';document.querySelectorAll('.lever').forEach(b=>b.onclick=()=>{$('.leverresult').textContent=`New confidence ${pct(+b.dataset.confidence)} · +${b.dataset.cost} min`;});const groups={unknown:'We lack data',unpredictable:'The road is unpredictable'};$('#risks').innerHTML=Object.entries(groups).map(([kind,label])=>`<div class="riskgroup"><h3>${label}</h3>${d.risk_sources.filter(r=>r.kind===kind).map(r=>`<div class="riskitem">${r.label}</div>`).join('')||'<div class="state">No sources reported.</div>'}</div>`).join('');initMap(d,window._chargers);}
$('#map-fit').onclick=fitMap;
try{scenario=await VerityAPI.getScenario();if(scenario.is_simulated)$('#sim').hidden=false;const b=scenario.booking;bookingEl.textContent=`${b.origin.name} → ${b.destination.name} · ${fmtTime(b.departure_time)} · ${b.passengers} passengers · AC ${b.ac_on?'ON':'OFF'} · target ${pct(b.target_confidence)}`;for(const v of scenario.vehicles){dispatches[v.vehicle_id]=await VerityAPI.getDispatch({vehicle_id:v.vehicle_id,origin:{lat:b.origin.lat,lon:b.origin.lon},destination:{lat:b.destination.lat,lon:b.destination.lon},departure_time:b.departure_time,start_soc_pct:v.soc_pct,passengers:b.passengers,ac_on:b.ac_on,target_confidence:b.target_confidence});}const cs=await VerityAPI.getChargers({min_lat:27.1,min_lon:77.2,max_lat:28.7,max_lon:78.3});window._chargers=cs;renderCards();status.remove();renderSelected();}catch(e){status.className='state error';status.textContent=`Unable to load fleet dispatch: ${e.message}`;vehiclesEl.innerHTML='<p class="state">Vehicle plans could not be loaded.</p>';}
$('#theme').onclick=()=>{const light=document.documentElement.dataset.theme==='light';document.documentElement.dataset.theme=light?'dark':'light';$('#theme').textContent=light?'Light theme':'Dark theme';};})();
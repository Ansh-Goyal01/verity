(async function () {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const templates = {
    en: {
      loading:'Loading trip…', brand:'VERITY · DRIVER RELEASE', simulated:'SIMULATED DATA', battery:'BATTERY', remaining:'KM TO GO', above:'ABOVE BINGO', below:'BELOW BINGO', start:'START', ponr:'POINT OF NO RETURN', stop:'STOP', destination:'DESTINATION', replay:'TRIP REPLAY', report:'REPORT BATTERY', play:'Play', pause:'Pause', speed:'Speed', voice:'VOICE', update:'Update', delete:'DEL', ok:'OK', safety:'Only use voice or the keypad when safely stopped, or let a passenger do it.', divert:'DIVERT NOW', understood:'Understood', light:'Light', dark:'Dark', ready:'{count} events · ready', running:'Event {index} of {count}', paused:'Paused · event {index} of {count}', complete:'Replay complete', invalid:'Enter a whole battery percentage from 0 to 100.', updated:'Battery updated to {soc}%.', voiceMock:'Voice input needs a connected API. Use the keypad in mock mode.', voiceUnsupported:'Voice recording is not supported. Use the keypad.', listening:'Recording. Tap VOICE again when finished.', processing:'Processing recording…', voiceFailed:'Voice input failed. Please use the keypad.', voiceEmpty:'No battery percentage recognized. Use the keypad.', speechFailed:'The voice alert could not play. Read the divert message on screen.', loadFailed:'Unable to load the trip. {error}', divertMessage:'Divert to {name}. Planned charger reported broken.', micLabel:'Record battery percentage', batteryLabel:'Battery percentage', position:'Your position', stopTitle:'Planned stop: {name}'
    },
    hi: {
      loading:'यात्रा लोड हो रही है…', brand:'VERITY · ड्राइवर रिलीज़', simulated:'सिमुलेटेड डेटा', battery:'बैटरी', remaining:'बाकी दूरी (किमी)', above:'बिंगो से ऊपर', below:'बिंगो से नीचे', start:'शुरुआत', ponr:'वापसी की अंतिम सीमा', stop:'चार्जिंग पड़ाव', destination:'गंतव्य', replay:'यात्रा रीप्ले', report:'बैटरी दर्ज करें', play:'चलाएँ', pause:'रोकें', speed:'गति', voice:'बोलें', update:'दर्ज करें', delete:'मिटाएँ', ok:'ठीक', safety:'आवाज़ या कीपैड का उपयोग सुरक्षित जगह रुकने के बाद ही करें, या किसी यात्री से करवाएँ।', divert:'अभी रास्ता बदलें', understood:'समझ गए', light:'लाइट', dark:'डार्क', ready:'{count} घटनाएँ · तैयार', running:'घटना {index} / {count}', paused:'रुका हुआ · घटना {index} / {count}', complete:'रीप्ले पूरा हुआ', invalid:'बैटरी प्रतिशत 0 से 100 तक की पूर्ण संख्या में दर्ज करें।', updated:'बैटरी {soc}% दर्ज की गई।', voiceMock:'आवाज़ के लिए API कनेक्शन चाहिए। मॉक मोड में कीपैड का उपयोग करें।', voiceUnsupported:'आवाज़ रिकॉर्ड नहीं हो सकती। कीपैड का उपयोग करें।', listening:'रिकॉर्डिंग जारी है। समाप्त होने पर बोलें बटन फिर दबाएँ।', processing:'रिकॉर्डिंग की जाँच हो रही है…', voiceFailed:'आवाज़ दर्ज नहीं हुई। कीपैड का उपयोग करें।', voiceEmpty:'बैटरी प्रतिशत समझ नहीं आया। कीपैड का उपयोग करें।', speechFailed:'आवाज़ वाला अलर्ट नहीं चल सका। स्क्रीन पर रास्ता बदलने का संदेश पढ़ें।', loadFailed:'यात्रा लोड नहीं हुई। {error}', divertMessage:'{name} की ओर मुड़ें। नियोजित चार्जर खराब बताया गया है।', micLabel:'बैटरी प्रतिशत रिकॉर्ड करें', batteryLabel:'बैटरी प्रतिशत', position:'आपकी स्थिति', stopTitle:'चार्जिंग पड़ाव: {name}'
    }
  };
  let language='en', scenario, plan, replay, current={km:0,soc:0};
  let entryMessage=null, currentAlert=null, recorder=null, stream=null;
  let eventIndex=0, running=false, timer=null, elapsed=0, lastWall=0, speed=1, phase='ready';
  const message=(key,args={}) => (templates[language][key]||key).replace(/\{(\w+)\}/g,(_,k)=>String(args[k]??''));
  function entry(key,args={}){entryMessage={key,args};$('#entrymsg').textContent=message(key,args);}
  function renderStatus(){if(replay)$('#replayStatus').textContent=message(phase,{index:eventIndex,count:replay.events.length});}
  function renderLanguage(){
    document.documentElement.lang=language==='hi'?'hi':'en';if($('#state'))$('#state').textContent=message('loading');
    document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=message(el.dataset.i18n));
    $('#language').textContent=language==='en'?'हिन्दी':'English';
    $('#theme').textContent=message(document.documentElement.dataset.theme==='light'?'dark':'light');
    $('#manual').setAttribute('aria-label',message('batteryLabel'));$('#mic').setAttribute('aria-label',message('micLabel'));
    $('#driverDot').title=message('position');$('#bingoDot').title=message('ponr');
    renderStatus();if(entryMessage)$('#entrymsg').textContent=message(entryMessage.key,entryMessage.args);
    if(plan){drawStopMarkers();renderMetrics();}if(currentAlert)renderAlert();
  }
  function drawStopMarkers(){
    $('#stopMarkers').replaceChildren();
    plan.stops.forEach(stop=>{const marker=document.createElement('span');marker.className='stopmarker';marker.title=message('stopTitle',{name:stop.name});marker.style.left=(stop.km_along_route/plan.route.distance_km*100)+'%';$('#stopMarkers').append(marker);});
  }
  function bingoAt(km){
    const points=plan.bingo_line;
    if(!points.length)return null;
    let left=points[0];for(const p of points){if(p.km>km){if(p.km===left.km)return left.bingo_soc_pct;const f=(km-left.km)/(p.km-left.km);return left.bingo_soc_pct+f*(p.bingo_soc_pct-left.bingo_soc_pct);}left=p;}
    return left.bingo_soc_pct;
  }
  function renderMetrics(){
    $('#soc').textContent=`${current.soc}%`;$('#km').textContent=Math.max(0,plan.route.distance_km-current.km).toFixed(1);
    const threshold=bingoAt(current.km);
    if(threshold===null){$('#margin').textContent='—';}else{const margin=Math.round(current.soc-threshold);$('#margin').textContent=`${Math.abs(margin)}%`;$('#marginLabel').textContent=message(margin<0?'below':'above');$('#margin').style.color=margin<0?'var(--nogo)':'';}
    const p=Math.min(100,Math.max(0,current.km/plan.route.distance_km*100));$('#progress').style.width=p+'%';$('#driverDot').style.left=p+'%';$('#bingoDot').style.left=(plan.point_of_no_return_km/plan.route.distance_km*100)+'%';
  }
  function renderAlert(){$('#alertTitle').textContent=message('divert');$('#alertText').textContent=message('divertMessage',{name:currentAlert.divert_to.name});}
  function handleEvent(event){
    if(event.type==='soc_reading'){current={km:event.km,soc:event.soc_pct};renderMetrics();}
    if(event.type==='forecast'&&event.action==='DIVERT'&&event.divert_to){currentAlert=event;renderAlert();$('#alert').hidden=false;VerityAPI.speak(message('divertMessage',{name:event.divert_to.name}),language==='hi'?'hi-IN':'en-IN').catch(()=>entry('speechFailed'));}
  }
  function accrue(){if(running){const now=performance.now();elapsed+=(now-lastWall)/1000*speed;lastWall=now;}}
  function clearTimer(){if(timer!==null){clearTimeout(timer);timer=null;}}
  function tick(){
    clearTimer();accrue();
    while(eventIndex<replay.events.length&&replay.events[eventIndex].t_offset_s<=elapsed){handleEvent(replay.events[eventIndex]);eventIndex++;}
    if(eventIndex===replay.events.length){running=false;phase='complete';renderStatus();return;}
    phase='running';renderStatus();
    if(running)timer=setTimeout(tick,Math.max(10,Math.min(1000,(replay.events[eventIndex].t_offset_s-elapsed)*1000/speed)));
  }
  $('#play').onclick=()=>{if(!replay||running)return;if(phase==='complete'){eventIndex=0;elapsed=0;current={km:0,soc:scenario.vehicles.find(v=>v.vehicle_id===plan.vehicle_id).soc_pct};currentAlert=null;$('#alert').hidden=true;renderMetrics();}running=true;lastWall=performance.now();tick();};
  $('#pause').onclick=()=>{if(!running)return;accrue();running=false;clearTimer();phase='paused';renderStatus();};
  $('#speed').onchange=()=>{accrue();speed=Number($('#speed').value);if(running){lastWall=performance.now();clearTimer();timer=setTimeout(tick,Math.max(10,Math.min(1000,(replay.events[eventIndex].t_offset_s-elapsed)*1000/speed)));}};
  $('#dismiss').onclick=()=>{$('#alert').hidden=true;currentAlert=null;};
  $('#language').onclick=()=>{language=language==='en'?'hi':'en';renderLanguage();};
  $('#theme').onclick=()=>{const el=document.documentElement;el.dataset.theme=el.dataset.theme==='light'?'dark':'light';renderLanguage();};
  function submit(){const raw=$('#manual').value.trim();if(!/^\d{1,3}$/.test(raw)||Number(raw)>100){entry('invalid');return;}current.soc=Number(raw);renderMetrics();entry('updated',{soc:current.soc});}
  $('#submit').onclick=submit;$('#manual').addEventListener('keydown',e=>{if(e.key==='Enter')submit();});
  document.querySelectorAll('.keypad button').forEach(button=>button.onclick=()=>{const key=button.dataset.key;if(key==='OK')submit();else if(key==='DEL')$('#manual').value=$('#manual').value.slice(0,-1);else if($('#manual').value.length<3)$('#manual').value+=key;});
  $('#mic').onclick=async()=>{
    if(!VerityAPI.isApiConnected()){entry('voiceMock');return;}
    if(recorder?.state==='recording'){recorder.stop();return;}
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){entry('voiceUnsupported');return;}
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:true});recorder=new MediaRecorder(stream);const active=recorder,chunks=[];
      active.ondataavailable=e=>chunks.push(e.data);
      active.onstop=async()=>{stream.getTracks().forEach(t=>t.stop());recorder=null;stream=null;entry('processing');try{const result=await VerityAPI.recognize(new Blob(chunks,{type:active.mimeType}),language==='hi'?'hi-IN':'en-IN');if(Number.isInteger(result.soc_pct)&&result.soc_pct>=0&&result.soc_pct<=100){$('#manual').value=result.soc_pct;submit();}else entry('voiceEmpty');}catch{entry('voiceFailed');}};
      active.start();entry('listening');
    }catch{entry('voiceFailed');}
  };
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){$('#alert').hidden=true;currentAlert=null;}});
  renderLanguage();
  try{
    scenario=await VerityAPI.getScenario();const vehicle=scenario.vehicles.find(v=>v.vehicle_id==='V12');if(!vehicle)throw new Error('V12 is unavailable.');const b=scenario.booking;
    plan=await VerityAPI.getDispatch({vehicle_id:vehicle.vehicle_id,origin:{lat:b.origin.lat,lon:b.origin.lon},destination:{lat:b.destination.lat,lon:b.destination.lon},departure_time:b.departure_time,start_soc_pct:vehicle.soc_pct,passengers:b.passengers,ac_on:b.ac_on,target_confidence:b.target_confidence});
    replay=await VerityAPI.getReplay(plan.plan_id);$('#sim').hidden=!(scenario.is_simulated||plan.is_simulated||replay.is_simulated);current={km:0,soc:vehicle.soc_pct};$('#origin').textContent=b.origin.name;$('#dest').textContent=b.destination.name;renderLanguage();$('#state').remove();$('#trip').hidden=false;
  }catch(error){$('#state').textContent=message('loadFailed',{error:error.message});}
})();

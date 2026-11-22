const API_URL = (((typeof location !== "undefined" && new URLSearchParams(location.search).get("api")) || (typeof window !== "undefined" && window.VERITY_CONFIG && window.VERITY_CONFIG.apiBaseUrl) || "").replace(/\/+$/, "")); // ?api=URL wins, else js/config.js apiBaseUrl; empty means offline mode (saved snapshot).
/* Data sources, in order of preference:
   1. Live engine (?api=URL).
   2. Engine snapshot: saved real engine outputs from docs/api_examples, bundled in js/snapshot.js
      by scripts/export_web_snapshot.py.
   Every offline response is simulated and the pages show a visible badge. */
(function(){
const S=()=>window.VERITY_SNAPSHOT?.api;
const wait=x=>new Promise(r=>setTimeout(()=>r(x),300));async function req(path,opts){let res;try{res=await fetch(API_URL+path,opts);}catch(e){throw new Error('Network request failed. Check your connection and try again.');}let data;try{data=await res.json();}catch(e){throw new Error('The server returned an unreadable response.');}if(!res.ok){const error=new Error(data.message||data.error||`Request failed (${res.status}).`);error.code=data.code||(!data.id&&data.error);error.cachedTrips=data.cached_trips||[];error.status=res.status;
    if(res.status===429){
      const seconds=res.headers.get('Retry-After');
      error.message=/^\d+$/.test(seconds||'')?'Too many requests. Try again in '+seconds+' seconds.':'Too many requests. Wait a moment and try again.';
    }
    throw error;}return data;}function fail(s){throw new Error(s);}
function offlineReplay(planId){const s=S();if(s)return Object.values(s.replay||{}).find(r=>r.plan_id===planId);return null;}
function connected(path, body) {
  if (!API_URL) return Promise.reject(new Error('Connect an engine to use this action.'));
  return req(path, body === undefined ? undefined : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
}
window.VerityAPI={
  checkRide: request => connected('/api/ride-check', request),
  planEvidence: id => connected('/api/graph/why/' + encodeURIComponent(id)),
  chargerEvidence: id => connected('/api/graph/charger/' + encodeURIComponent(id)),
  evidenceLedger: () => connected('/api/evidence/ledger'),
  confirmReport: request => connected('/api/voice/report/confirm', request),
  confirmRide: request => connected('/api/voice/ride-check', request),
  async hearRide(blob, language) {
    if (!API_URL) throw new Error('Connect an engine to record a ride request.');
    const form = new FormData();
    form.append('audio', blob, blob.type.includes('mp4') ? 'ride.mp4' : 'ride.webm');
    form.append('language', language);
    form.append('speak', 'false');
    return req('/api/voice/ride-check', {method: 'POST', body: form});
  },
isApiConnected(){return Boolean(API_URL);},dataSource(){return API_URL?'live':S()?'snapshot':'unavailable';},async getHealth(){if(!API_URL)return wait(S()?.health||{status:'offline',is_simulated:true});return req('/api/health');},async getScenario(){if(!API_URL){const s=S()?.scenario;if(!s)fail('Scenario data is unavailable.');return wait(s);}return req('/api/demo/scenario');},async getDispatch(request){if(!API_URL){const d=(S()?.dispatch)?.[request?.vehicle_id];if(!d)fail('No saved dispatch exists for that vehicle.');return wait(d);}return req('/api/dispatch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});},async getChargers(bounds){if(!API_URL)return wait(S()?.chargers||fail('Charger data is unavailable.'));const q=new URLSearchParams({min_lat:bounds.min_lat,min_lon:bounds.min_lon,max_lat:bounds.max_lat,max_lon:bounds.max_lon});return req('/api/chargers?'+q);},async getReplay(planId){if(!API_URL){const r=offlineReplay(planId);if(!r)fail('Replay data was not found for this plan.');return wait(r);}return req('/api/demo/replay/'+encodeURIComponent(planId));},async updateForecast(request){if(!API_URL)return wait(S()?.forecastUpdate||fail('Forecast data is unavailable.'));return req('/api/forecast/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});},async recognize(audioBlob,language){if(!API_URL)throw new Error('Voice input needs a connected API. Use the keypad in offline mode.');const form=new FormData();form.append('audio',audioBlob,'battery.webm');form.append('language',language);const data=await req('/api/voice/stt',{method:'POST',body:form});return data;},async speak(text,language){if(API_URL){try{const data=await req('/api/voice/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,language})});const audio=new Audio(`data:audio/${data.format||'wav'};base64,${data.audio_base64}`);await audio.play();return;}catch(e){/* Continue with browser voice. */}}if(!('speechSynthesis'in window))throw new Error('Voice playback is not available in this browser.');speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.lang=language==='hi-IN'?'hi-IN':'en-IN';const voices=speechSynthesis.getVoices();const chosen=language==='hi-IN'?voices.find(v=>/^hi(-|_)/i.test(v.lang)):voices.find(v=>/^en-IN$/i.test(v.lang))||voices.find(v=>/^en-IN/i.test(v.lang));if(chosen)u.voice=chosen;speechSynthesis.speak(u);}};
})();

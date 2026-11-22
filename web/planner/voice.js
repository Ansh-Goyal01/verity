import {api} from './client.js';
import {openCachedPlan, routeError} from './journey.js';
import {button, esc, onSubmit, release, statusBox} from './ui.js';

export function confirmation(heard, confirmed, speak) {
  if (heard.stage !== 'confirm' || !heard.needs_confirmation) throw new Error('Clarify the trip first.');
  const {origin, destination, vehicle_id, soc_pct, departure_time, ac_on, passengers} = heard.slots;
  return {confirmed, language:heard.reply_language, speak, origin, destination, vehicle_id,
    soc_pct, departure_time, ac_on, passengers};
}

function playback(clip) {
  if (!clip?.audio_base64 || clip.format !== 'wav') return '';
  return `<audio controls aria-label="Spoken reply" src="data:audio/wav;base64,${esc(clip.audio_base64)}"></audio>`;
}

export const MAX_RECORDING_SECONDS = 30;

export function micSupported(env = globalThis) {
  return Boolean(env.navigator?.mediaDevices?.getUserMedia && env.MediaRecorder);
}

// A named audio file from what MediaRecorder produced, or null when nothing was captured.
export function recordedFile(chunks, mimeType) {
  const type = (mimeType || 'audio/webm').split(';')[0].trim().toLowerCase();
  const blob = new Blob(chunks, {type});
  if (!blob.size) return null;
  const extension = type.includes('mp4') ? 'mp4' : type.includes('ogg') ? 'ogg' : 'webm';
  return new File([blob], `trip.${extension}`, {type});
}

// The microphone next to the file upload: record, hear it back, discard or record again. One source at a time.
function microphone(form, changed) {
  const mic = form.querySelector('[data-mic]'), note = form.querySelector('[data-mic-note]');
  const preview = form.querySelector('[data-preview]'), discard = form.querySelector('[data-discard]');
  const picker = form.elements.audio;
  let file = null, stream = null, recorder = null, ticker = null, url = null;
  const say = text => { note.textContent = text; };
  const stopTracks = () => { stream?.getTracks().forEach(track => track.stop()); stream = null; };
  let generation = 0;
  const clear = () => {
    generation++;
    if (recorder?.state === 'recording') { recorder.onstop = null; recorder.stop(); }
    stopTracks(); clearInterval(ticker); recorder = null; file = null;
    if (url) URL.revokeObjectURL(url);
    url = null; preview.removeAttribute('src'); preview.hidden = discard.hidden = true;
    mic.textContent = 'Record with microphone'; mic.setAttribute('aria-pressed', 'false');
  };
  if (!micSupported()) {
    mic.hidden = true;
    say('Recording is not available in this browser. Upload a file or type what you would say.');
    return {file: () => null};
  }
  picker.addEventListener('change', () => { if (picker.files.length) { clear(); say(''); } });
  discard.onclick = () => { clear(); say('Recording discarded.'); changed(); };
  mic.onclick = async () => {
    if (recorder?.state === 'recording') { recorder.stop(); return; }
    clear(); picker.value = ''; changed();
    const pending = generation;
    mic.disabled = true;
    try {
      const input = await navigator.mediaDevices.getUserMedia({audio:true});
      if (pending !== generation || !form.isConnected) { input.getTracks().forEach(track => track.stop()); return; }
      stream = input;
    }
    catch { say('Microphone unavailable. Allow it in your browser, or upload a file or type instead.'); return; }
    finally { mic.disabled = false; }
    let active;
    try { active = recorder = new MediaRecorder(stream); }
    catch { clear(); say('Recording is unavailable. Upload a file or type instead.'); return; }
    const chunks = [];
    active.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    active.onstop = () => {
      clearInterval(ticker); stopTracks();
      mic.textContent = 'Record again'; mic.setAttribute('aria-pressed', 'false');
      file = recordedFile(chunks, active.mimeType);
      if (!file) { say('Nothing was recorded. Try again.'); return; }
      url = URL.createObjectURL(file); preview.src = url; preview.hidden = discard.hidden = false;
      say('Recorded. Listen before submitting, or record again.'); changed();
    };
    const started = Date.now();
    active.onerror = () => { clear(); say('Recording stopped. Upload a file or type instead.'); };
    try { active.start(); }
    catch { clear(); say('Recording could not start. Upload a file or type instead.'); return; }
    mic.textContent = 'Stop recording'; mic.setAttribute('aria-pressed', 'true');
    const tick = () => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      say(`Recording… ${seconds}s (stops at ${MAX_RECORDING_SECONDS}s)`);
      if (seconds >= MAX_RECORDING_SECONDS && active.state === 'recording') active.stop();
    };
    tick(); ticker = setInterval(tick, 1000);
  };
  const cleanup = () => {
    clear();
    removeEventListener('hashchange',cleanup);
    removeEventListener('pagehide',cleanup);
  };
  addEventListener('pagehide',cleanup);
  addEventListener('hashchange',cleanup);
  return {file: () => file};
}

export function reportConfirmation(heard, consent, speak) {
  if (heard.stage !== 'confirm' || !heard.needs_confirmation || !heard.charger?.charger_id || !heard.status || !consent)
    throw new Error('Confirm the charger, its status and your consent first.');
  return {confirmed:true, consent:true, from_real_person:true, charger_id:heard.charger.charger_id,
    status:heard.status, transcript:heard.transcript, language:heard.reply_language, speak};
}

export function voice(root, panel) {
  const report = panel === 'report';
  const topic = report ? 'charger report' : 'trip';
  root.innerHTML = `<nav class="form-actions" aria-label="Voice tasks"><a href="#voice" ${!report ? 'aria-current="page"' : ''}>Plan a trip</a>
    <a href="#voice/report" ${report ? 'aria-current="page"' : ''}>Report a charger</a></nav>
    <form class="sheet"><fieldset><legend>Describe your ${topic}</legend>
    <label>Upload a recording<input type="file" name="audio" accept="audio/*"></label>
    <div class="mic-row"><button type="button" class="button button-outline" data-mic aria-pressed="false">Record with microphone</button>
      <button type="button" class="button button-outline" data-discard hidden>Discard recording</button></div>
    <audio controls data-preview hidden aria-label="Your recording"></audio>
    <p class="mic-note" data-mic-note role="status" aria-live="polite"></p>
    <label>Or type what you would say<input name="transcript" maxlength="${report ? 300 : 500}" autocomplete="off"></label>
    <label>Language<select name="language"><option value="unknown">Detect language</option><option value="hi-IN">हिन्दी</option><option value="en-IN">English</option></select></label>
    <label class="check"><input type="checkbox" name="speak" checked>Include spoken replies</label>
    ${button('Review ' + topic,true)}</fieldset>${statusBox()}</form><div class="results"></div>`;
  const form = root.querySelector('form'), result = root.querySelector('.results');
  form.oninput = () => result.replaceChildren();
  const mic = microphone(form, () => result.replaceChildren());
  onSubmit(form, async data => {
    result.replaceChildren();
    const audio = mic.file() || data.get('audio'), transcript = data.get('transcript').trim();
    if (Boolean(audio?.size) === Boolean(transcript)) throw new Error('Choose one recording or enter one transcript.');
    let body = {transcript, language:data.get('language'), speak:data.has('speak')};
    if (audio?.size) {
      body = new FormData();
      body.append('audio',audio);
      body.append('language',data.get('language'));
      body.append('speak',String(data.has('speak')));
    }
    form.querySelector('fieldset').disabled = true;
    let heard;
    try { heard = await api(report ? '/voice/report' : '/voice/plan',{method:'POST',body}); }
    catch (error) {
      if (audio?.size && error.status === 503) throw new Error('Voice transcription is unavailable. Discard the recording or clear the upload and type your words instead.');
      throw error;
    }
    finally { form.querySelector('fieldset').disabled = false; }
    const ready = heard.stage === 'confirm' && heard.needs_confirmation;
    result.innerHTML = `<section class="sheet"><h2 tabindex="-1">${(ready ? 'Confirm your ' : 'Clarify your ') + topic}${heard.is_simulated ? ' (simulated)' : ''}</h2>
      <p>${esc(heard.transcript)}</p><p lang="${esc(heard.reply_language)}">${esc(heard.prompt)}</p>${playback(heard.prompt_audio)}
      ${ready ? `<form>${report ? '<label class="check"><input type="checkbox" name="consent" required>I personally observed this and agree to add the report and transcript to this charger’s history.</label>' : ''}
        <div class="form-actions">${button(report ? 'Confirm and record' : 'Confirm and plan',true)}<button class="button button-outline" formnovalidate type="submit" value="cancel">Cancel</button></div>${statusBox()}</form>` : '<p>Update the recording or transcript above and review it again.</p>'}</section>`;
    result.querySelector('h2').focus();
    if (!ready) return;
    const review = result.querySelector('form');
    const offers = document.createElement('div');
    review.after(offers);
    onSubmit(review, async (reviewData, submit) => {
      const yes = submit.value !== 'cancel';
      form.querySelector('fieldset').disabled = true;
      if (report && !yes) {
        form.querySelector('fieldset').disabled = false;
        result.innerHTML = '<p role="status">Report cancelled.</p>';
        return;
      }
      let answer;
      try {
        answer = report
          ? await api('/voice/report/confirm',{method:'POST',body:reportConfirmation(heard,reviewData.has('consent'),data.has('speak'))})
          : await api('/voice/plan',{method:'POST',body:confirmation(heard,yes,data.has('speak'))});
      } finally { form.querySelector('fieldset').disabled = false; }
      if (report) {
        result.innerHTML = '<section class="sheet" role="status"><h2>' +
          (answer.stage === 'recorded' ? 'Observation recorded' : 'Observation was not recorded') +
          (answer.is_simulated ? ' (simulated)' : '') + '</h2><p>' + esc(answer.speech) + '</p>' +
          window.VerityCore.illustrativeBlocks(answer.report || {}) + playback(answer.audio) + '</section>';
        return;
      }
      if (answer.stage === 'not_confirmed') {
        result.innerHTML = '<p role="status">Trip cancelled. You can edit the details above.</p>';
        return;
      }
      result.innerHTML = release(answer.release,'Spoken trip') + playback(answer.audio);
    }, 'Sending your answer…', (error,status) => !report && routeError(error,status,offers,trip => openCachedPlan(trip,{
      vehicle:heard.slots.vehicle_id, soc:heard.slots.soc_pct, passengers:heard.slots.passengers, ac:heard.slots.ac_on
    }),true));
  }, 'Reading your ' + topic + '…');
}

(() => {
  'use strict';
  const service = window.VerityTrip;
  const esc = window.VerityCore.escapeHtml;
  function mount(container, apply) {
    let recording, stream, timer, prompt, serial = 0, closed = false;
    container.innerHTML = `<details class="voice-fold"><summary>${service.live ? 'Request a ride by voice' : 'Try the spoken ride check'}</summary>
      <p>${service.live ? 'Speak the battery, pickup and drop. Confirm the read-back before running the check.' : 'Hear the recorded airport request, then confirm the battery and destinations.'}</p>
      <label>Language<select data-lang><option value="en-IN">English</option><option value="hi-IN">हिन्दी</option></select></label>
      <button type="button" data-start class="button button-outline">${service.live ? 'Record request' : 'Play sample read-back'}</button>
      <div data-heard role="status"></div><div data-confirm hidden><button type="button" data-yes class="button instrument-button">Confirm ride</button>
      <button type="button" data-no class="button button-outline">Cancel</button></div></details>`;
    const $ = s => container.querySelector(s);
    const button = $('[data-start]'), heard = $('[data-heard]'), controls = $('[data-confirm]'), language = $('[data-lang]');
    function release() {
      clearTimeout(timer);
      if (recording?.state === 'recording') recording.stop();
      stream?.getTracks().forEach(track => track.stop());
    }
    function reset() {
      ++serial; service.stopSpeech(); release(); prompt = null; controls.hidden = true; heard.replaceChildren(); language.disabled = false; button.disabled = false;
      button.textContent = service.live ? 'Record request' : 'Play sample read-back';
    }
    function readBack(value) {
      prompt = value;
      heard.innerHTML = `<p lang="${esc(value.reply_language)}">${esc(value.prompt)}</p>`;
      controls.hidden = value.stage !== 'confirm';
    }
    button.onclick = async () => {
      if (recording?.state === 'recording') { release(); return; }
      reset();
      const token = serial;
      if (!service.live) {
        const clip = service.cases.clips.find(c => c.source.includes('read-back') && c.language === language.value);
        if (!clip) { heard.textContent = 'This recording is unavailable. Use the ride form above.'; return; }
        readBack({ stage: 'confirm', reply_language: language.value, prompt: clip.text, slots: { soc_pct: service.saved.driverDemo.request.soc_pct } });
        try { await service.speak(clip.text, language.value); } catch { /* The same read-back is visible. */ }
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { heard.textContent = 'Recording is unavailable here. Use the ride form above.'; return; }
      try {
        button.disabled = true;
        const granted = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (closed || token !== serial) { granted.getTracks().forEach(track => track.stop()); return; }
        stream = granted; recording = new MediaRecorder(stream); const chunks = [], recorder = recording;
        recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = async () => {
          granted.getTracks().forEach(track => track.stop());
          if (closed || token !== serial) return;
          button.disabled = true; heard.textContent = 'Transcribing your request…';
          try {
            const result = await window.VerityAPI.hearRide(new Blob(chunks, { type: recorder.mimeType }), language.value);
            if (token === serial && !closed) readBack(result);
          } catch (e) { if (token === serial && !closed) heard.textContent = e.message + ' Use the ride form above.'; }
          finally { if (token === serial && !closed) { button.disabled = false; button.textContent = 'Record again'; language.disabled = false; } }
        };
        language.disabled = true; recorder.start(); button.disabled = false; button.textContent = 'Finish recording';
        heard.textContent = 'Listening for up to 25 seconds.'; timer = setTimeout(release, 25000);
      } catch { if (token === serial && !closed) { release(); button.disabled = false; language.disabled = false; heard.textContent = 'Microphone unavailable. You can type the ride details above.'; } }
    };
    language.onchange = reset;
    $('[data-no]').onclick = reset;
    $('[data-yes]').onclick = async () => {
      if (!prompt) return;
      const token = ++serial, current = prompt, lang = current.reply_language;
      controls.hidden = true; button.disabled = true; language.disabled = true;
      heard.textContent = 'Checking the confirmed ride…';
      try {
        const slots = current.slots;
        const answer = service.live ? (await window.VerityAPI.confirmRide({ confirmed: true, soc_pct: slots.soc_pct,
          pickup_id: slots.pickup.id, drop_id: slots.drop.id, language: lang, speak: false })).ride_check : service.saved.rideCheck;
        if (token !== serial || closed) return;
        apply({ soc: slots.soc_pct, answer });
        heard.textContent = lang === 'hi-IN' ? 'राइड की पुष्टि हुई।' : 'Ride confirmed.';
        try { await service.speak(answer.speech[lang], lang); } catch { /* The engine decision is visible in the ticket. */ }
      } catch (e) { if (token === serial && !closed) { heard.textContent = e.message; controls.hidden = false; } }
      finally { if (token === serial && !closed) { button.disabled = false; language.disabled = false; } }
    };
    return () => { closed = true; ++serial; release(); };
  }
  window.VerityRideVoice = { mount };
})();

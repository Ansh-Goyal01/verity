/* Trip Check reads exact saved runs offline and the same API contracts when connected. */
(() => {
  'use strict';
  const api = window.VerityAPI;
  const saved = window.VERITY_SNAPSHOT.api;
  const cases = window.VerityCases;
  const live = api.isApiConnected();
  const vehicle = id => saved.scenario.vehicles.find(car => car.vehicle_id === id);
  const coords = ({ lat, lon }) => ({ lat, lon });
  const { booking } = saved.scenario;

  function dispatchRequest(id, soc) {
    return { vehicle_id: id, start_soc_pct: soc, origin: coords(booking.origin), destination: coords(booking.destination),
      departure_time: booking.departure_time, passengers: booking.passengers, ac_on: booking.ac_on,
      target_confidence: booking.target_confidence };
  }

  async function dispatch(id, soc) {
    if (!vehicle(id)) throw new Error('Choose one of the three vehicles in the fleet.');
    const request = dispatchRequest(id, soc);
    if (live) return { request, plan: await api.getDispatch(request) };
    const sample = cases.dispatch[id]?.[soc];
    if (!sample) throw new Error('This battery level needs a connected engine. Choose a saved level.');
    const plan = sample.release || saved.dispatch[id];
    return { request, plan, sample };
  }

  async function ride({ soc, rate, current, pickup, drop }) {
    const base = saved.driverDemo.request;
    const request = { ...base, soc_pct: soc, earning_rs_per_hour: rate, current, pickup, drop };
    if (live) return api.checkRide(request);
    if (['current', 'pickup', 'drop'].some(key => ['lat', 'lon'].some(axis => request[key][axis] !== base[key][axis]))) {
      throw new Error('Choose the saved airport route or connect an engine.');
    }
    const sample = (soc === base.soc_pct && rate === null) ? saved.rideCheck : cases.rides[`${soc}/${rate ?? 'example'}`];
    if (!sample) throw new Error('These inputs have no saved run. Select a saved battery and earning rate.');
    return sample;
  }

  async function forecast(trip, soc) {
    if (!Number.isInteger(soc) || soc < 0 || soc > 100) throw new Error('Enter a whole battery percentage from 0 to 100.');
    if (!live) {
      const answer = trip.sample?.forecasts?.[soc];
      if (!answer) throw new Error('A released trip is needed for a battery update.');
      return answer;
    }
    const request = { plan_id: trip.plan.plan_id, ...coords(cases.checkpoint), t: trip.request.departure_time, soc_pct: soc };
    return api.updateForecast(request);
  }

  async function outage(trip) {
    const events = live ? (await api.getReplay(trip.plan.plan_id)).events : trip.sample.events;
    const reportIndex = events.findIndex(event => event.type === 'charger_report');
    if (reportIndex < 0) return null;
    const reading = events.slice(0, reportIndex).filter(event => event.type === 'soc_reading').pop();
    const answer = events.slice(reportIndex + 1).find(event => event.type === 'forecast');
    if (!reading || !answer) throw new Error('The outage replay is missing a reading or an engine decision.');
    return { reading, report: events[reportIndex], answer };
  }

  let playing = null;
  function stopSpeech() {
    playing?.pause?.();
    playing = null;
    window.speechSynthesis?.cancel();
  }
  function speak(text, language) {
    stopSpeech();
    const clip = cases.clips.find(entry => entry.text === text && entry.language === language);
    if (!clip) return api.speak(text, language);
    playing = new Audio('../data/audio_cache/' + encodeURIComponent(clip.file));
    return playing.play();
  }

  window.VerityTrip = {
    live, vehicle, dispatch, ride, forecast, outage, speak, stopSpeech, cases, saved,
    why: trip => live ? api.planEvidence(trip.plan.plan_id) : Promise.resolve(trip.sample.why),
    charger: id => live ? api.chargerEvidence(id) : Promise.resolve(cases.chargers[id]),
    ledger: () => live ? api.evidenceLedger() : Promise.resolve(cases.ledger),
    source: live ? 'Connected engine · Simulation' : 'Saved engine runs · Simulation'
  };
})();

/* Illustrative mock data, not results. */
(function () {
  const route = [
    [28.5355, 77.3910], [28.4930, 77.3820], [28.4510, 77.3790],
    [28.4100, 77.3900], [28.3500, 77.4150], [28.2850, 77.4450],
    [28.2150, 77.4780], [28.1450, 77.5100], [28.0750, 77.5440],
    [28.0050, 77.5780], [27.9350, 77.6120], [27.8650, 77.6460],
    [27.7950, 77.6800], [27.7250, 77.7140], [27.6550, 77.7480],
    [27.5850, 77.7820], [27.5150, 77.8160], [27.4450, 77.8500],
    [27.3750, 77.8840], [27.3050, 77.9180], [27.2350, 77.9520],
    [27.1950, 77.9800], [27.1767, 78.0081]
  ];
  const booking = {
    booking_id: 'BK-001',
    origin: { name: 'Noida', lat: 28.5355, lon: 77.3910 },
    destination: { name: 'Agra', lat: 27.1767, lon: 78.0081 },
    departure_time: '2026-10-28T14:00:00+05:30', passengers: 4,
    ac_on: true, target_confidence: 0.9, is_simulated: true
  };
  const vehicles = [
    { vehicle_id: 'V07', label: 'Vehicle 7', model_class: 'compact_esuv', soc_pct: 68,
      usable_kwh_estimate: { p10: 29.0, p50: 31.5, p90: 33.0 },
      health_note: 'Aged battery: usable capacity estimated below nominal', trips_calibrated: 6, is_simulated: true },
    { vehicle_id: 'V12', label: 'Vehicle 12', model_class: 'compact_esuv', soc_pct: 82,
      usable_kwh_estimate: { p10: 35.0, p50: 37.5, p90: 39.0 },
      health_note: 'Healthy battery: capacity estimate well calibrated', trips_calibrated: 18, is_simulated: true },
    { vehicle_id: 'V19', label: 'Vehicle 19', model_class: 'compact_esuv', soc_pct: 74,
      usable_kwh_estimate: { p10: 32.0, p50: 34.0, p90: 36.0 },
      health_note: 'Healthy battery; planned charger relies on stale reports', trips_calibrated: 11, is_simulated: true }
  ];
  const makeTerms = rows => rows.map(([term, label, soc_pct]) => ({ term, label, soc_pct }));

  function bingoLine(start, arrival, charge) {
    const distance = 231.4, stopKm = 120.5;
    const nonCharging = arrival - start - charge;
    const points = [];
    for (let km = 0; km <= 230; km += 10) points.push(km);
    points.push(stopKm, distance);
    return [...new Set(points)].sort((a, b) => a - b).map(km => {
      let expected;
      if (km < stopKm) expected = start + nonCharging * 0.7 * km / stopKm;
      else expected = start + nonCharging * 0.7 + charge + nonCharging * 0.3 * (km - stopKm) / (distance - stopKm);
      expected = km === distance ? arrival : expected;
      return {
        km: +km.toFixed(1),
        bingo_soc_pct: +Math.max(0, expected - 13).toFixed(1),
        expected_soc_pct: +expected.toFixed(1),
        expected_soc_p10: +Math.max(0, expected - 6).toFixed(1),
        expected_soc_p90: +Math.min(100, expected + 5).toFixed(1)
      };
    });
  }

  function dispatch(id, decision, start, arrival, confidence, breakdown, stopCharger, refusal) {
    const charge = breakdown.find(row => row[0] === 'charging')[2];
    const stale = stopCharger === 'CH-0203';
    const stopAt = stale ? [27.7950, 77.6800] : [27.8650, 77.6460];
    const alternateAt = [27.9350, 77.6120];
    return {
      plan_id: `PL-${id.toLowerCase()}-2026`, vehicle_id: id, decision, confidence,
      starting_battery_pct: start,
      arrival_soc: { p10: Math.max(0, arrival - 6), p50: arrival, p90: Math.min(100, arrival + 5) },
      route: { distance_km: 231.4, duration_min: 205, polyline: route },
      stops: [{ charger_id: stopCharger, name: stale ? 'Mathura South Fast Charge' : 'Yamuna Expressway Hub',
        lat: stopAt[0], lon: stopAt[1], km_along_route: 120.5,
        arrive_soc_p50: +(start + (arrival - start - charge) * 0.7).toFixed(1),
        charge_minutes: 22, p_works: stale ? 0.35 : 0.95,
        evidence: { n_reports: stale ? 2 : 18, days_since_last: stale ? 41 : 2, trust_level: stale ? 'low' : 'high' }, is_simulated: true }],
      alternates: [{ for_stop_index: 0, charger_id: 'CH-0188', name: 'Mathura Backup Charge',
        lat: alternateAt[0], lon: alternateAt[1], p_reach: 0.9, p_works: 0.85, is_simulated: true }],
      bingo_line: bingoLine(start, arrival, charge), point_of_no_return_km: 104.0,
      breakdown: makeTerms(breakdown),
      levers: [{ lever_id: 'cruise_90', label: 'Cruise at 90 instead of 100 km/h',
        new_confidence: Math.min(0.95, confidence + 0.05), time_cost_min: 12, is_simulated: true }],
      risk_sources: [
        { kind: 'unknown', label: 'Recent charger reports are limited near the midpoint', is_simulated: true },
        { kind: 'unpredictable', label: 'Traffic around the expressway exits can vary', is_simulated: true }
      ],
      refusal_reason: refusal, compute_ms: 850, is_simulated: true
    };
  }

  const dispatches = {
    V07: dispatch('V07', 'NO_GO', 68, 4, 0.65, [
      ['rolling', 'Rolling resistance', -19], ['aero', 'Air drag', -31], ['grade', 'Climbs', -3],
      ['inertia', 'Stop-and-go', -8], ['regen', 'Regenerative braking', 5],
      ['aux', 'AC and electronics', -14], ['charging', 'Charging stops', 6]
    ], 'CH-0142', null),
    V12: dispatch('V12', 'GO', 82, 36, 0.9, [
      ['rolling', 'Rolling resistance', -20], ['aero', 'Air drag', -32], ['grade', 'Climbs', -3],
      ['inertia', 'Stop-and-go', -8], ['regen', 'Regenerative braking', 5],
      ['aux', 'AC and electronics', -14], ['charging', 'Charging stops', 26]
    ], 'CH-0142', null),
    V19: dispatch('V19', 'INSUFFICIENT_EVIDENCE', 74, 20, 0.45, [
      ['rolling', 'Rolling resistance', -20], ['aero', 'Air drag', -32], ['grade', 'Climbs', -3],
      ['inertia', 'Stop-and-go', -8], ['regen', 'Regenerative braking', 5],
      ['aux', 'AC and electronics', -14], ['charging', 'Charging stops', 18]
    ], 'CH-0203', 'We cannot verify the planned charger: its last report is 41 days old, so there is not enough current evidence to release this trip.')
  };

  const trust = ['high', 'medium', 'low', 'unknown'];
  const chargers = [];
  for (let i = 0; i < 15; i++) {
    const pt = route[Math.min(route.length - 1, Math.floor((i + 1) * route.length / 16))];
    const id = i === 4 ? 'CH-0142' : i === 11 ? 'CH-0188' : i === 2 ? 'CH-0203' : `CH-${String(201 + i).padStart(4, '0')}`;
    const t = id === 'CH-0188' ? 'medium' : trust[i % trust.length];
    const special = id === 'CH-0142' ? [27.8650, 77.6460] : id === 'CH-0188' ? [27.9350, 77.6120] : id === 'CH-0203' ? [27.7950, 77.6800] : null;
    chargers.push({
      charger_id: id,
      name: id === 'CH-0142' ? 'Yamuna Expressway Hub' : id === 'CH-0188' ? 'Mathura Backup Charge' : id === 'CH-0203' ? 'Mathura South Fast Charge' : ['Taj Highway Charge', 'Expressway North', 'Kosi Fast Charge', 'Raya Service Plaza', 'Agra Entry Station'][i % 5],
      lat: special ? special[0] : pt[0] + (i % 2 ? 0.014 : -0.013),
      lon: special ? special[1] : pt[1] + (i % 3 - 0.9) * 0.018,
      operator: ['ChargeGrid', 'Tata Power', 'Statiq', 'Jio-bp'][i % 4], site_type: 'highway',
      connectors: [{ type: 'CCS2', kw: i % 2 ? 60 : 120 }],
      p_works: id === 'CH-0142' ? 0.95 : id === 'CH-0188' ? 0.85 : id === 'CH-0203' ? 0.35 : t === 'high' ? 0.95 : t === 'medium' ? 0.75 : t === 'low' ? 0.4 : null,
      trust_level: t, n_reports: t === 'high' ? 18 : t === 'medium' ? 8 : t === 'low' ? 2 : 0,
      days_since_last: t === 'high' ? 2 : t === 'medium' ? 9 : t === 'low' ? 41 : null,
      source: t === 'high' ? 'audit' : t === 'unknown' ? 'simulated' : 'ocm', is_simulated: true
    });
  }
  const replay = {
    plan_id: dispatches.V12.plan_id,
    events: [
      { t_offset_s: 0, type: 'soc_reading', km: 0, soc_pct: 82, is_simulated: true },
      { t_offset_s: 900, type: 'soc_reading', km: 24, soc_pct: 77, is_simulated: true },
      { t_offset_s: 1800, type: 'soc_reading', km: 48, soc_pct: 70, is_simulated: true },
      { t_offset_s: 2400, type: 'charger_report', charger_id: 'CH-0142', status: 'broken', source_trust: 'high', is_simulated: true },
      { t_offset_s: 2410, type: 'forecast', action: 'DIVERT', message: 'Planned charger reported broken. Divert to the alternate ahead.',
        divert_to: { charger_id: 'CH-0188', name: 'Mathura Backup Charge', lat: 27.9350, lon: 77.6120, p_works: 0.85, is_simulated: true }, is_simulated: true }
    ], is_simulated: true
  };
  const mocks = {
    scenario: { booking, vehicles, is_simulated: true }, dispatch: dispatches,
    chargers: { chargers, is_simulated: true }, replay,
    forecastUpdate: { plan_id: dispatches.V12.plan_id, km_along_route: 64.2, confidence: 0.8,
      arrival_soc: { p10: 18, p50: 25, p90: 31 }, bingo_soc_pct_here: 20, action: 'CONTINUE',
      divert_to: null, message: 'Battery is draining faster than planned; still above the Bingo Line.', is_simulated: true }
  };
  (function mark(x) { if (!x || typeof x !== 'object') return; x.is_simulated = true; Object.values(x).forEach(mark); })(mocks);
  window.VERITY_MOCKS = mocks;
})();

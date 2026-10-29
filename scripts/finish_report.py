"""Measure the demo and generate finish reports from artifacts, without held-out-data reads."""
from __future__ import annotations
import argparse
import json
import os
import platform
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from api.service import EngineService
from verity.dispatch import dispatch
from verity.paths import RESULTS_DIR as R, DOCS_DIR as D

EXPECTED = {'V07': 'NO_GO', 'V12': 'GO', 'V19': 'INSUFFICIENT_EVIDENCE'}


def read(p: Path) -> dict:
    return json.loads(p.read_text(encoding='utf-8-sig'))


def wilson(k: int, n: int) -> list[float]:
    p, z = k / n, 1.96
    c = (p + z*z/(2*n))/(1+z*z/n)
    h = z*(p*(1-p)/n+z*z/(4*n*n))**0.5/(1+z*z/n)
    return [max(0,c-h), min(1,c+h)]


def measure() -> None:
    import numpy as np
    from verity.chargers import load_chargers
    svc = EngineService(n_runs=1500, graph_backend='memory')
    out = {'is_simulated': True, 'at_utc': datetime.now(timezone.utc).isoformat(),
           'machine': {'os': platform.platform(), 'processor': platform.processor(), 'logical_cpus': os.cpu_count(), 'python': platform.python_version()},
           'demo': {}, 'timings': {}, 'neo4j': 'NOT DONE: credentials absent; historical timings are not current-machine measurements'}
    try:
        chargers, _ = load_chargers()
        km = np.array([rc.km_along for rc in svc.near]); gaps = np.diff(km); i = int(np.argmax(gaps))
        out['directory'] = {'total': len(chargers), 'corridor': len(km), 'corridor_offset_km': 3,
                            'tagged_power': sum(bool(c.connectors) for c in chargers),
                            'corridor_tagged_power': sum(bool(rc.charger.connectors) for rc in svc.near),
                            'largest_gap_km': float(gaps[i]), 'gap_endpoints_km': km[i:i+2].tolist(),
                            'note': 'REAL directory locations only; gap excludes route endpoints, not an audit of availability'}
        scenario = svc.demo_scenario(); booking = scenario['booking']
        for v in scenario['vehicles']:
            vid = v['vehicle_id']; fv = svc.fleet[vid]
            exported = read(D/'api_examples'/f'dispatch_{vid}.json')
            assert exported['decision'] == EXPECTED[vid] and exported['is_simulated']
            seeds = []
            for seed in range(8):
                result = dispatch(fv.ctx, svc.route, svc.near, svc.trust, start_soc=fv.soc_pct,
                                  seed=seed, cfg=svc.cfg, compute_levers=False)
                seeds.append({'seed':seed,'decision':result.decision,'raw':result.confidence_raw,'shown':result.confidence,
                              'mc_wilson95':wilson(int(result.state.ev.success.sum()), svc.cfg.n_runs)})
            out['demo'][vid] = {'decision': exported['decision'], 'exported_confidence': exported['confidence'],
                               'n_seeds':len(seeds), 'runs_per_seed':svc.cfg.n_runs, 'seeds':seeds,
                               'raw_range':[min(s['raw'] for s in seeds),max(s['raw'] for s in seeds)]}
            req = {k:booking[k] for k in ('origin','destination','departure_time','passengers','ac_on','target_confidence')}
            req.update(vehicle_id=vid,start_soc_pct=v['soc_pct'])
            times = []
            for _ in range(3):
                svc.plans.clear()  # bypass plan cache; initialization/HTTP excluded
                t0 = time.perf_counter(); svc.dispatch(req); times.append(1000*(time.perf_counter()-t0))
            out['timings'][vid] = times
            print(f'Measured {vid}',flush=True)
        out['replay_V12'] = read(D/'api_examples'/'replay_V12.json')['events'][-1]['action']
        assert out['replay_V12'] == 'DIVERT'
    finally:
        svc.close()
    out['notes'] = ['SIMULATED fleet/reports. Seed range is not a CI. MC Wilson describes finite draws conditional on the model, not field accuracy.',
                    'Memory timings are full uncached service dispatch with levers, initialization and HTTP excluded. No timing CI.']
    (R/'finish_measurements.json').write_text(json.dumps(out,indent=1),encoding='utf-8')


def write_reports() -> None:
    m = read(R/'finish_measurements.json'); run = read(R/'run_all_summary.json')
    raw_log = (R/'logs'/'pytest_final.log').read_bytes()
    log = raw_log.decode('utf-16' if raw_log.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    matches = re.findall(r'\d+ passed[^\r\n]*? in [\d.]+s[^\r\n]*',log)
    if not matches or re.search(r'\d+ failed',log):
        raise ValueError('No successful final pytest log; refusing to invent a result')
    tests = matches[-1].strip('= ')
    (R/'finish_validation.json').write_text(json.dumps({'pytest_summary':tests,'run_all':run,'source_log':'results/logs/pytest_final.log'},indent=1),encoding='utf-8')
    rows = []
    def row(claim: str, value: object, sample: str, label: str, source: str) -> None:
        rows.append([claim,str(value),sample,label,source])
    before = read(R/'e1_learning_curve_simulated_BEFORE_fix.json'); after = read(R/'e1_learning_curve_simulated.json')
    for method, points in after['methods'].items():
        for k, v in points.items():
            old = before['methods'][method][k]
            row(f'E1 BEFORE {method}, k={k}',f"MAE {old['mae_pp']} pp; mean subset coverage90 {old['coverage_90']}",f"{before['test_trips']} unique trips; {old['n_predictions']} repeated predictions, no independent-trial CI",'SIMULATED','results/e1_learning_curve_simulated_BEFORE_fix.json')
            row(f'E1 AFTER {method}, k={k}',f"MAE {v['mae_pp']} pp",f"{after['resamples']} subset draws; {after['test_trips']} unique trips / {after['vehicles']} vehicles; no MAE CI",'SIMULATED','results/e1_learning_curve_simulated.json')
            for level,b in v['bands'].items():
                row(f'E1 AFTER {method}, k={k}, {level}% band',f"mixture {b['coverage']:.4f}; mean single-subset {b['mean_subset_coverage']:.4f}",f"{b['covered']}/{b['n']}; mixture Wilson95 {b['wilson95']}; per-subset Wilson in JSON",'SIMULATED','results/e1_learning_curve_simulated.json')
    row('BMW E1','NOT DONE (dataset not on this machine)','No evaluation/CI','NOT DONE','docs/PROGRESS_FINISH.md')
    for prefix, stem in [('E2','e2_calibration_simulated'),('E3','e3_reliability_planning_simulated'),('E4','e4_dispatch_vs_fixed_simulated'),('E5','e5_trust_recovery_simulated')]:
        data = read(R/f'{stem}.json'); source = f'results/{stem}.json'
        if prefix == 'E2':
            for method,v in data['methods'].items():
                n = data['trips']; k = round(v['coverage_90_arrival_band']*n)
                row(f'E2 {method}',f"ECE {v['ece']}; Brier {v['brier']}; coverage90 {v['coverage_90_arrival_band']}; width {v['mean_90_band_width_pp']} pp",f'n={n}; coverage Wilson95 {wilson(k,n)}; no ECE/Brier CI','SIMULATED',source)
                h = v['when_forecast_ge_0.9'];n=h['n'];k=round(h['observed_arrival_rate']*n)
                row(f'E2 {method}, forecasts >=90%',h,f'n={n}; observed-rate Wilson95 {wilson(k,n)}','SIMULATED',source)
            row('E2 energy forecast relative bias by class',data['median_forecast_energy_error_rel_by_class'],f"n={data['trips']}; no CI saved",'SIMULATED',source)
        elif prefix in ('E3','E4'):
            for scenario,v in data['scenarios'].items():
                n=v['trips']; methods=('blind','trust_aware') if prefix=='E3' else ('fixed','dispatch')
                for method in methods:
                    rate=v[('stranding_rate_' if prefix=='E3' else 'stranding_rate_')+method]
                    interval=wilson(round(rate*n),n) if prefix=='E3' else v['ci95_'+method]
                    row(f'{prefix} {scenario}, {method} stranded',rate,f'n={n}; Wilson95 {interval}','SIMULATED',source)
                row(f'{prefix} {scenario}, paired outcomes/minutes/timing',v,f'n={n}; rates above have marginal CIs; paired difference/minutes CI not saved','SIMULATED',source)
        else:
            for scenario,v in data['scenarios'].items():
                row(f'E5 {scenario}, MAE',v['mae'],f"{data['worlds_per_scenario']} worlds, {data['chargers_per_world']} chargers/world; SD is NOT a 95% CI",'SIMULATED',source)
                row(f'E5 {scenario}, ring and attacked-charger outcomes',{'ring':v['ring_detection'],'attacked':v['mae_on_attacked_chargers']},f"{data['worlds_per_scenario']} worlds; no account-level CI available",'SIMULATED',source)
    for vid,v in m['demo'].items():
        row(f'Demo {vid}',f"{v['decision']}; exported rounded model confidence {v['exported_confidence']}; raw range {v['raw_range']}",f"{v['n_seeds']} seeds x {v['runs_per_seed']} MC worlds; range NOT CI; conditional finite-MC Wilson per seed in JSON",'SIMULATED',f'docs/api_examples/dispatch_{vid}.json; results/finish_measurements.json')
    row('V12 replay',m['replay_V12'],'One scripted scenario, no success-rate CI','SIMULATED','docs/api_examples/replay_V12.json')
    row('Charger directory counts and largest corridor gap',m['directory'],'Directory census, no operational audit or availability CI','REAL locations only','results/finish_measurements.json; data/processed/chargers_ncr.json')
    times=[t for ts in m['timings'].values() for t in ts]
    row('Uncached memory full dispatch',f'mean {statistics.mean(times):.1f} ms; range {min(times):.1f}-{max(times):.1f} ms',f'n={len(times)}; 1500 MC runs; init/HTTP excluded; no CI','SIMULATED workload','results/finish_measurements.json')
    row('Neo4j dispatch timing',m['neo4j'],'No current-machine sample','NOT DONE','results/finish_measurements.json')
    row('Tests',tests,'Full suite; live Neo4j/BMW checks skipped; Starlette deprecation warning','SOFTWARE CHECK','results/logs/pytest_final.log')
    row('Full run_all',run,'E1 verifies/replots immutable evaluation; E2-E5 execute full sizes; smoke SKIPPED without keys','SOFTWARE CHECK','results/run_all_summary.json')
    forbidden=['Validated in India, accurate on Indian roads, audited chargers, drivers told us, completed interviews or fleet pilot.',
               'BMW validation done here; Sarvam/Aura live verified here; current-machine Neo4j timing.',
               'Nominal 90% now guarantees 90%: single-subset bands still under-cover; mixture coverage is a different quantity.',
               'R4a changed production forecast uncertainty, or repeated subset predictions are independent held-out trips.',
               'Same-site independence in reality; directory records guarantee access/working status; assumed 25 kW is measured power.',
               'All attacks detected: E5 misses the ten-day evasive ring.',
               'Sub-second dispatch or model/MC confidence proves real-world safety.']
    caveat='All proportions are fractions unless explicitly shown as percent. E1 BEFORE mean subset coverage differs from AFTER mixture coverage: quote AFTER mean single-subset coverage for comparison. Remaining undercoverage is retained. Wilson is descriptive and not adjusted for shared vehicles. E2/E3 counts are reconstructed from saved proportions at sufficient precision to identify integer counts. E5 SD is not a CI. No real Indian validation exists.'
    deck='# Results for deck (script generated)\n\n'+f"Machine: `{json.dumps(m['machine'])}`. Recorded {m['at_utc']}.\n\n"+caveat+'\n\n| Claim | Value | n and 95% CI | REAL or SIMULATED | Source file |\n|---|---|---|---|---|\n'
    deck+='\n'.join('| '+' | '.join(x.replace('|','/').replace('\n',' ') for x in row_)+' |' for row_ in rows)
    ending='\n\n## Do not claim\n\n'+'\n'.join('- '+s for s in forbidden)+'\n'
    (D/'RESULTS_FOR_DECK.md').write_text(deck+ending,encoding='utf-8')
    status='ALL STEPS OK' if run['all_ok'] else 'SOME STEPS FAILED'
    report=f'# Final finish-track report\n\nFull clean-output run_all: **{status}**. Final pytest: **{tests}**.\n\n'
    report+='R0, R1, R2, R6, R4a, R7, R8 completed; R3 reused, never regenerated. R5/R4b blocked. R10 full validation, fresh exports and reports completed as recorded below. R9 skipped (optional site-correlation change deferred; limitation retained). Local milestone commits created on main using the user-provided repository-only identity; see PROGRESS_FINISH.md for final R11 status. No push.\n\n'
    report+='| Run step | OK | Seconds | Detail |\n|---|---|---|---|\n'+'\n'.join(f"| {s['step']} | {s['ok']} | {s['seconds']} | {s['last_line'].replace('|','/')} |" for s in run['steps'])
    report+='\n\nE1 final held-out evaluation ran exactly once in R4a; R10 verifies the code/training/settings fingerprint and replots its immutable saved evaluation without reopening held-out observations. E2-E5 run at full sizes. Neo4j smoke SKIPPED is not live success. Examples were freshly exported again after run_all.\n\n'
    report+='\n'.join(f"- {v}: {d['decision']}, rounded model confidence {d['exported_confidence']} (SIMULATED fleet/reports)." for v,d in m['demo'].items())+f"\n- V12 replay ends in {m['replay_V12']}.\n\n"
    report+='## Blockers and exact manual steps\n\n- BMW: **NOT DONE (dataset not on this machine)**. Open https://doi.org/10.21227/6jr9-5235, log in if requested, download Measurement Data.zip; place 70 TripXnn.csv files and Overview.xlsx directly in data/raw/bmw_i3/. Run `uv run python -m verity.datasets.bmw_i3`; require loader_ready, n_trips=70. Preserve the split; fetch https://www.mdpi.com/2032-6653/11/2/41, freeze training-only flag exclusions and documented parameters, then load test with final_evaluation=True exactly once. Label eventual REAL BMW Germany heater/AC data, not Indian conditions. No BMW test trips opened here.\n- Sarvam: add your SARVAM_API_KEY locally to .env, verify current official endpoint/header/request/model details, then make at most one TTS and one STT call; stop on auth failure. Key absent, no calls or live verification here.\n- Neo4j: provide credentials locally, run existing smoke/parity checks once and benchmark there. Current graph migration has fake-driver coverage only.\n- Disclosure: teammate must fill frontend tool names and confirm historical A13 assistant attribution.\n- Git: repository-only author identity was supplied by the user and configured locally. Review the local commits before pushing yourself.\n\n'
    report+='## Three claims requiring most caution\n\n1. E1 nominal 90% single-subset bands still under-cover. Mixture coverage is different; the correction is experiment-only.\n2. E4 stranding reductions are SIMULATED small-sample outcomes with independent failures, not Indian field validation.\n3. Demo confidence/GO/DIVERT outcomes use sweep-selected scenarios; same-site independence, assumed power and six-chain shortlist limit interpretation.\n\n'
    report+='See [EVIDENCE_STATUS.md](EVIDENCE_STATUS.md) and README for all system/evidence limits; [RESULTS_FOR_DECK.md](RESULTS_FOR_DECK.md) contains measured numbers, n/intervals, machine and provenance. No energy/trust/threshold/demo-input changes or new dependencies.\n'
    (D/'FINAL_REPORT.md').write_text(report+ending,encoding='utf-8')
    print('Generated both reports from saved script outputs')


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--measure',action='store_true');p.add_argument('--write',action='store_true');a=p.parse_args()
    if not (a.measure or a.write):p.error('choose --measure and/or --write')
    if a.measure:measure()
    if a.write:write_reports()

if __name__=='__main__':main()

"""Regenerates the sample data in data/ from the red-team test suite workbook.

Usage: python3 tools/generate_sample_data.py path/to/Sample_Test_Suite.xlsx data/

Severity, likelihood and detectability come from the workbook's Test Cases sheet. Everything else
(results, evidence, workflow areas, the inspection simulation, the snowball case and the oversight
experiment) is synthesized and illustrative. The random seed is fixed, so the output is repeatable.
Needs: pip install openpyxl
"""
import openpyxl, json, random, math, os, sys
random.seed(83)
OUT = sys.argv[2]; os.makedirs(OUT, exist_ok=True)
wb = openpyxl.load_workbook(sys.argv[1])
ws = wb['Test Cases']

AREA = {}
for k in 'D1 D2 D3 D4 D5 D6 D7 C5 S2 S4'.split(): AREA[k] = 'System execution'
for k in 'C4 C6 I1 I2 I4 I5 I6 O5 S1'.split(): AREA[k] = 'Planning'
for k in 'C1 C2 C3 C7 I3 O1 O2 O3 O4 O6 S3'.split(): AREA[k] = 'Reasoning'
for k in 'H1 H2 H3 H4 H5 H6 H7 H8'.split(): AREA[k] = 'Human review'
MVP = {'D1': 'Snowball root cause', 'C6': 'Calculation error', 'I1': 'Intent misread', 'C4': 'Wrong tool selected', 'D2': 'Coverage gap', 'H5': 'Plausibility anchoring'}

def tier(r):
    return 'Critical' if r >= 60 else 'High' if r >= 36 else 'Medium' if r >= 18 else 'Low'

rows = []
for r in ws.iter_rows(min_row=5, values_only=True):
    if not r[0]: continue
    rid, layer, cat, test, setup, prompt, exp, passc, ctrl, s, l, d = r[:12]
    reg = r[14]
    rpn = s * l * d
    # sample execution outcome, failure more likely at high RPN
    u = random.random()
    p_fail = min(0.85, rpn / 110)
    if u < 0.12: status, result = 'Not started', 'Not run'
    elif u < 0.12 + (1 - 0.12) * p_fail: status, result = 'Done', 'Fail'
    elif u < 0.12 + (1 - 0.12) * (p_fail + (1 - p_fail) * 0.35): status, result = 'Done', 'Partial'
    else: status, result = 'Done', 'Pass'
    runs = 10
    if result == 'Fail': k = random.randint(6, 10)
    elif result == 'Partial': k = random.randint(2, 5)
    elif result == 'Pass': k = 0
    else: k = None
    if k is None: ev = 'Not yet executed'
    elif k == 0: ev = f'0 of {runs} runs failed; control behaved as specified'
    else: ev = f'{k} of {runs} runs failed; trace T-{rid}-{random.randint(1,9):02d}'
    rows.append(dict(id=rid, layer=layer, category=cat, test=test, area=AREA[rid],
                     severity=s, likelihood=l, detectability=d, rpn=rpn, tier=tier(rpn),
                     status=status, result=result, what=exp, evidence=ev, control=ctrl,
                     exposure=reg, mvp=MVP.get(rid, ''), fail_runs=k))
json.dump(rows, open(f'{OUT}/risks.json', 'w'), indent=0)

# ---------- inspection strategy simulation ----------
N = 2000
areas = ['Reasoning', 'Planning', 'System execution']
area_w = [0.35, 0.30, 0.35]
# how much signal the guardrail risk score carries for each area
signal = {'Reasoning': 1.2, 'Planning': 0.7, 'System execution': 1.9}
pop = []
for i in range(N):
    err = random.random() < 0.065
    if err:
        a = random.choices(areas, area_w)[0]
        sev = random.choices([1, 2, 3, 4, 5], [0.15, 0.25, 0.3, 0.2, 0.1])[0]
        score = random.gauss(signal[a], 1.0) + 0.15 * sev
    else:
        a, sev = None, 0
        score = random.gauss(0, 1.0)
    pop.append(dict(err=err, area=a, sev=sev, score=score))
errs = [p for p in pop if p['err']]
E = len(errs); W = sum(p['sev'] for p in errs); C = sum(1 for p in errs if p['sev'] >= 4)
catch_p = 0.62  # reviewer catch prob given inspected (baseline-ish)
MIN_PER = 4.5

ranked = sorted(pop, key=lambda p: -p['score'])
curve = []
for b in range(2, 32, 2):
    k = round(N * b / 100)
    # random: expectation
    rec_r = b / 100 * catch_p
    top = ranked[:k]
    caught = [p for p in top if p['err']]
    rec_k = len(caught) / E * catch_p
    sw_k = sum(p['sev'] for p in caught) / W * catch_p
    curve.append(dict(budget=b, method='Random sampling', recall=round(rec_r, 4), weighted_recall=round(rec_r, 4)))
    curve.append(dict(budget=b, method='Risk-based', recall=round(rec_k, 4), weighted_recall=round(sw_k, 4)))
json.dump(curve, open(f'{OUT}/strategy_curve.json', 'w'), indent=0)

B = 10; k = N * B // 100
top = ranked[:k]
summ = []
for m in ['Random sampling', 'Risk-based']:
    if m == 'Random sampling':
        insp = E * B / 100; crit_insp = C * B / 100
    else:
        insp = sum(1 for p in top if p['err']); crit_insp = sum(1 for p in top if p['err'] and p['sev'] >= 4)
    summ.append(dict(method=m, budget_pct=B, outputs=N, reviewed=k, errors_present=E,
                     errors_inspected=round(insp, 1), errors_caught=round(insp * catch_p, 1),
                     critical_present=C, critical_caught=round(crit_insp * catch_p, 1),
                     review_hours=round(k * MIN_PER / 60, 1),
                     errors_missed=round(E - insp * catch_p, 1)))
json.dump(summ, open(f'{OUT}/strategy_summary.json', 'w'), indent=0)

by_area = []
for a in areas:
    ea = [p for p in errs if p['area'] == a]
    for m in ['Random sampling', 'Risk-based']:
        if m == 'Random sampling': c = len(ea) * B / 100 * catch_p
        else: c = sum(1 for p in top if p['area'] == a) * catch_p
        by_area.append(dict(area=a, method=m, errors=len(ea), caught=round(c, 1)))
json.dump(by_area, open(f'{OUT}/strategy_by_area.json', 'w'), indent=0)

# ---------- snowball dependency graph ----------
nodes = [
    dict(node='nav', label='Vendor NAV feed', stage=0, area='System execution', affected=1, unit='fund', state='root', note='Growth fund NAV not updated; prior close reused'),
    dict(node='cache', label='Price cache', stage=1, area='System execution', affected=1, unit='stale price', state='propagating', note='Stale price served with no as-of timestamp'),
    dict(node='hold', label='Holdings join', stage=2, area='System execution', affected=412, unit='households', state='propagating', note='412 households hold the fund'),
    dict(node='perf', label='Daily performance report', stage=3, area='System execution', affected=412, unit='reports', state='propagating', note='Overnight report also built on the stale price'),
    dict(node='calc', label='Impact calculator', stage=3, area='Reasoning', affected=268, unit='answers', state='propagating', note='Fund shown as flat; loss understated'),
    dict(node='answer', label='Agent answer', stage=4, area='Reasoning', affected=268, unit='answers', state='propagating', note="'-1.6%, no action needed' reads as plausible"),
    dict(node='review', label='Advisor review', stage=5, area='Human review', affected=268, unit='answers', state='control', note='Reviewer checks the number, not its base or freshness'),
    dict(node='client', label='Sent to clients', stage=6, area='Human review', affected=241, unit='clients (random)', state='harm', note='Under random sampling, most reach clients'),
]
edges = [dict(edge='nav-cache', source='nav', target='cache', carried=1),
         dict(edge='cache-hold', source='cache', target='hold', carried=412),
         dict(edge='hold-calc', source='hold', target='calc', carried=268),
         dict(edge='hold-perf', source='hold', target='perf', carried=412),
         dict(edge='calc-answer', source='calc', target='answer', carried=268),
         dict(edge='answer-review', source='answer', target='review', carried=268),
         dict(edge='review-client', source='review', target='client', carried=241)]
json.dump(nodes, open(f'{OUT}/snowball_nodes.json', 'w'), indent=0)
json.dump(edges, open(f'{OUT}/snowball_edges.json', 'w'), indent=0)
outcome = [
    dict(method='Random sampling', affected=268, inspected=27, caught=6, first_catch_min=212, reached_clients=241, root_cause_found='No: each catch fixed as a one-off'),
    dict(method='Risk-based', affected=268, inspected=268, caught=1, first_catch_min=14, reached_clients=19, root_cause_found='Yes: freshness flag clustered on one fund'),
]
json.dump(outcome, open(f'{OUT}/snowball_outcome.json', 'w'), indent=0)

# ---------- oversight experiment ----------
ov = [
    dict(condition='C1', linked='H1', label='Answer text only (baseline)', reviewers=12, shown=60, caught=21, median_s=38),
    dict(condition='C2', linked='H2', label='Coverage and freshness banner', reviewers=12, shown=60, caught=39, median_s=44),
    dict(condition='C3', linked='H3', label='Error after 50 correct items', reviewers=12, shown=60, caught=14, median_s=22),
    dict(condition='C4', linked='H4', label='30 items pending in queue', reviewers=12, shown=60, caught=16, median_s=19),
    dict(condition='C5', linked='H5', label='Wrong number looks plausible', reviewers=12, shown=60, caught=11, median_s=31),
    dict(condition='C6', linked='H6', label="'No action needed' hard-stop", reviewers=12, shown=60, caught=52, median_s=41),
    dict(condition='C7', linked='H7', label='Alerts on 40% of items', reviewers=12, shown=60, caught=18, median_s=17),
    dict(condition='C8', linked='H2 + H4', label='Banner under queue load', reviewers=12, shown=60, caught=30, median_s=26),
]
json.dump(ov, open(f'{OUT}/oversight.json', 'w'), indent=0)
print('wrote', OUT)

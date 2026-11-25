import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.services.domain import sample_rows, compute
from datetime import date

rows = sample_rows()
# Backfill for HB-052 demand week 2026-08-03 (index ~32), posted 2026-08-10:
# invisible to origins on/before 2026-08-10, visible to later origins.
batch = [{'sku': 'HB-052', 'week': '2026-08-03', 'posted_at': '2026-08-10', 'added_qty': 9}]
run = compute(rows, {'alpha': 0.2, 'beta': 0.15, 'holdout_origins': 8, 'idle_weeks': 8}, batch)
plain = compute(rows, {'alpha': 0.2, 'beta': 0.15, 'holdout_origins': 8, 'idle_weeks': 8})

for sku in ('HB-052',):
    pw = [w for w in plain['windows'] if w['备件编号'] == sku]
    rw = [w for w in run['windows'] if w['备件编号'] == sku]
    before = [w for w in rw if w['预测当周'] <= '2026-08-10']
    after = [w for w in rw if w['预测当周'] > '2026-08-10']
    print('origins:', [w['预测当周'] for w in rw])
    # Origins at or before posting day: identical forecasts to plain.
    for w in rw:
        p = next(x for x in pw if x['预测当周'] == w['预测当周'])
        if w['预测当周'] <= '2026-08-10':
            assert w['SBA预测（件）'] == p['SBA预测（件）'] and w['TSB预测（件）'] == p['TSB预测（件）']
        else:
            # Later origins incorporate the extra 9 units in training -> forecast moves.
            assert (w['SBA预测（件）'], w['TSB预测（件）']) != (p['SBA预测（件）'], p['TSB预测（件）']) or w['已入账实际需求（件）'] != p['已入账实际需求（件）']
    assert after, 'need at least one origin after posting'
    print('training info-set cutoff respected: OK')

# Final live forecast for HB-052 with the early backfill must exceed plain.
p52 = next(r for r in plain['rows'] if r['备件编号'] == 'HB-052')
r52 = next(r for r in run['rows'] if r['备件编号'] == 'HB-052')
print('HB-052 TSB plain vs with-backfill:', p52['TSB提前期需求（件）'], r52['TSB提前期需求（件）'])
assert r52['TSB提前期需求（件）'] > p52['TSB提前期需求（件）']
print('ALL PIT TRAINING TESTS PASSED')

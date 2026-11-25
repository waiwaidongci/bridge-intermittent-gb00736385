import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.services.domain import sample_rows, compute

rows = sample_rows()
opts = {'alpha': 0.20, 'beta': 0.15, 'holdout_origins': 8, 'idle_weeks': 8}
plain = compute(rows, opts)

# A backfill posted very early (before all 8 origins) changes every window's actuals and training.
early = [{'sku': 'HB-041', 'week': '2026-02-16', 'posted_at': '2026-02-17', 'added_qty': 10}]
run = compute(rows, opts, early)
pw = [w for w in plain['windows'] if w['备件编号'] == 'HB-041']
rw = [w for w in run['windows'] if w['备件编号'] == 'HB-041']
# All origins are after 2026-02-17, so the demand week (index 8) backfill is in every training set.
for w in rw:
    assert w['预测当周'] > '2026-02-17'
# Selection can legitimately differ; just ensure it is one of the two models and metrics coherent.
part = next(r for r in run['rows'] if r['备件编号'] == 'HB-041')
assert part['采用模型'] in ('TSB', 'Croston SBA')
# MAE equals mean of per-window errors for the selected comparison.
import statistics
sba_mae = statistics.mean(w['SBA误差（件）'] for w in rw)
tsb_mae = statistics.mean(w['TSB误差（件）'] for w in rw)
assert abs(sba_mae - part['SBA留出MAE（件）']) < 1e-12
assert abs(tsb_mae - part['TSB留出MAE（件）']) < 1e-12
print('MAE aggregation matches per-window errors: OK')
print('selection with early backfill:', part['采用模型'], '| plain:', next(r for r in plain['rows'] if r['备件编号']=='HB-041')['采用模型'])

# Empty backfill list behaves like none.
assert compute(rows, opts, [])['rows'] == plain['rows']

# Backfill on the closing week itself (posted any time) is excluded from holdout actuals
# because closing_day is the closing week Monday with strict <.
closing = [{'sku': 'HB-014', 'week': '2026-11-16', 'posted_at': '2026-11-16', 'added_qty': 5}]
crun = compute(rows, opts, closing)
assert crun['metrics']['结账日前补录入账件数'] == 0
assert crun['rows'] == plain['rows']
print('closing-week backfill excluded from this run (waits for next analysis): OK')

# Multiple batches' worth of entries in one call accumulate.
multi = [
    {'sku': 'HB-014', 'week': '2026-10-26', 'posted_at': '2026-10-27', 'added_qty': 1},
    {'sku': 'HB-014', 'week': '2026-11-02', 'posted_at': '2026-11-03', 'added_qty': 2},
]
mrun = compute(rows, opts, multi)
assert mrun['metrics']['补录条数'] == 2
assert mrun['metrics']['结账日前补录入账件数'] == 3
hit = [w for w in mrun['windows'] if w['备件编号']=='HB-014' and w['窗口内迟到补录件数'] > 0]
assert hit, 'windows covering backfilled weeks should show visible late qty'
assert sum({(w['预测当周']): w['窗口内迟到补录件数'] for w in hit}.values()) >= 3
print('multiple entries across weeks accumulate in windows: OK')
print('ALL EDGE TESTS PASSED')

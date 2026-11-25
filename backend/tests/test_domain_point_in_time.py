import sys, math, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.services.domain import (
    sample_rows, sample_backfills, compute, _group_rows,
    _validate_backfills, _asof_demand,
)
from datetime import date

rows = sample_rows()
backfills = sample_backfills()

# 1. No-backfill run must reproduce legacy numbers (HB-041 SBA 5.32, TSB 4.69).
plain = compute(rows, {'alpha': 0.20, 'beta': 0.15, 'holdout_origins': 8, 'idle_weeks': 8})
hb041 = next(r for r in plain['rows'] if r['备件编号'] == 'HB-041')
print('plain HB-041 SBA/TSB:', round(hb041['SBA提前期需求（件）'], 4), round(hb041['TSB提前期需求（件）'], 4))
assert math.isclose(hb041['SBA提前期需求（件）'], 5.32, abs_tol=0.01), hb041['SBA提前期需求（件）']
assert math.isclose(hb041['TSB提前期需求（件）'], 4.69, abs_tol=0.01), hb041['TSB提前期需求（件）']
assert hb041['补货点（件）'] == 8 and hb041['库存缺口（件）'] == 6

# 2. Plain run must produce one window row per part per origin (4*8=32).
assert len(plain['windows']) == 32, len(plain['windows'])
w0 = plain['windows'][0]
assert set(['备件编号','预测当周','窗口结束周','SBA预测（件）','TSB预测（件）','已入账实际需求（件）','SBA误差（件）','TSB误差（件）','窗口内迟到补录件数']) <= set(w0)

# 3. Late backfill posted AFTER closing: backtest windows must be identical to plain.
late = compute(rows, {'alpha': 0.20, 'beta': 0.15, 'holdout_origins': 8, 'idle_weeks': 8}, backfills)
for a, b in zip(plain['windows'], late['windows']):
    assert a == b, (a, b)
# Metrics: all backfill units posted after closing -> 0 counted units, but lines counted.
assert late['metrics']['补录条数'] == 3
assert late['metrics']['结账日前补录入账件数'] == 0
# Headline results identical to plain.
for pa, la in zip(plain['rows'], late['rows']):
    assert pa == la, (pa, la)
print('late (post-closing) backfill leaves backtest and selection unchanged: OK')

# 4. Same qty but posted BEFORE closing: actuals in affected windows change, forecasts do not.
early = [
    {'sku': 'HB-041', 'week': '2026-10-19', 'posted_at': '2026-10-20', 'added_qty': 4},
    {'sku': 'HB-027', 'week': '2026-07-27', 'posted_at': '2026-07-28', 'added_qty': 2},
    {'sku': 'HB-014', 'week': '2026-11-02', 'posted_at': '2026-11-03', 'added_qty': 1},
]
early_run = compute(rows, {'alpha': 0.20, 'beta': 0.15, 'holdout_origins': 8, 'idle_weeks': 8}, early)
assert early_run['metrics']['补录条数'] == 3
assert early_run['metrics']['结账日前补录入账件数'] == 7
changed = [w for w in early_run['windows'] if w['窗口内迟到补录件数'] > 0]
assert changed, 'at least one window should show visible late qty'
for w in changed:
    plain_w = next(p for p in plain['windows'] if p['备件编号'] == w['备件编号'] and p['预测当周'] == w['预测当周'])
    assert w['已入账实际需求（件）'] == plain_w['已入账实际需求（件）'] + w['窗口内迟到补录件数']
    # forecasts for that origin unchanged only if posted after the forecast day; check errors moved.
    assert w['SBA误差（件）'] != plain_w['SBA误差（件）'] or w['TSB误差（件）'] != plain_w['TSB误差（件）']
print('early (pre-closing) backfill raises actuals and errors in affected windows: OK')

# 5. Posting exactly on forecast day must NOT be visible at that origin (strict <).
series, cutoff = _group_rows(rows)
entries = _validate_backfills([{'sku':'HB-052','week':'2026-08-03','posted_at':'2026-08-03','added_qty':5}], series)
# HB-052 starts 2025-12-22; index of 2026-08-03:
start = date(2025,12,22); idx = (date(2026,8,3)-start).days//7
asof = _asof_demand(series['HB-052'], entries, date(2026,8,3))
assert asof[idx] == series['HB-052'][idx]['demand'], 'same-day posting must be excluded'
asof2 = _asof_demand(series['HB-052'], entries, date(2026,8,10))
assert asof2[idx] == series['HB-052'][idx]['demand'] + 5
print('strict posting-day boundary: OK')

# 6. Validation failures.
def expect_error(batch, fragment):
    try:
        _validate_backfills(batch, series)
    except ValueError as e:
        assert fragment in str(e), (str(e), fragment)
        return
    raise AssertionError('expected error containing ' + fragment)

expect_error([{'sku':'UNKNOWN','week':'2026-08-03','posted_at':'2026-08-04','added_qty':1}], '不在周台账')
expect_error([{'sku':'HB-052','week':'2026-08-04','posted_at':'2026-08-05','added_qty':1}], '周一')
expect_error([{'sku':'HB-052','week':'2026-08-03','posted_at':'2026-08-02','added_qty':1}], '早于需求周')
expect_error([{'sku':'HB-052','week':'2026-08-03','posted_at':'2026-08-04','added_qty':0}], '新增件数')
expect_error([
    {'sku':'HB-052','week':'2026-08-03','posted_at':'2026-08-04','added_qty':1},
    {'sku':'HB-052','week':'2026-08-03','posted_at':'2026-08-05','added_qty':2},
], '重复')
print('backfill validation: OK')

# 7. JSON-serializable (no NaN) and window columns declared.
import json
json.dumps(early_run, allow_nan=False)
assert early_run['window_columns'] == list(early_run['windows'][0].keys())
print('json serialization: OK')
print('ALL DOMAIN TESTS PASSED')

"""Compare weekly intermittent-demand forecasts for bridge spare parts.

Work orders are often entered after the weekly closing.  To keep the rolling
backtest honest, every demand contribution carries an entry (posting) date:

* the regular ledger row of a week is treated as posted on that week's Monday;
* late work-order backfills carry their own ``posted_at`` entry date.

A backtest window at origin week ``t`` only trains on contributions already
posted before the forecast day (the Monday of week ``t``).  Its actual demand
only counts contributions posted before the run's closing day, so backfills
entered after closing never shrink the historical error.  Re-running an old
analysis keeps the ledger revision and backfill batch pinned at run creation.
"""

from collections import defaultdict
from datetime import date, timedelta
from math import ceil, isclose, ulp
from statistics import mean, pvariance


MIN_TRAIN_WEEKS = 8
MAX_LEAD_WEEKS = 26
MAX_QUANTITY = 1_000_000_000
MAX_BACKFILL_ROWS = 5000


def sample_rows() -> list[dict]:
    """Fixed work-order demand ledger with a common closing week."""
    parts = (
        ('HB-014', '东闸桥', '液压接头密封包', 4, 2, 1,
         {1: 2, 6: 3, 11: 2, 17: 3, 20: 2, 27: 3, 32: 2, 36: 3, 41: 2, 45: 3}),
        ('HB-027', '东闸桥', '限位开关', 6, 1, 2,
         {1: 3, 5: 2, 10: 4, 14: 3, 19: 2, 23: 4, 28: 3, 32: 2}),
        ('HB-041', '西泵桥', '导向轮轴承', 5, 3, 2,
         {2: 1, 8: 12, 13: 2, 18: 1, 24: 18, 31: 2, 38: 1, 44: 16}),
        ('HB-052', '西泵桥', '编码器安装支架', 3, 1, 5,
         {0: 1, 8: 1, 15: 2, 22: 1, 30: 1, 39: 2, 47: 1}),
    )
    start = date(2025, 12, 22)
    return [
        {
            'sku': sku,
            'bridge': bridge,
            'part': part,
            'week': (start + timedelta(weeks=index)).isoformat(),
            'demand': events.get(index, 0),
            # Synthetic weekly balances with work-order drawdowns.
            'on_hand': max(0, closing_stock + (47 - index) % 6 - events.get(index, 0))
            if index < 47 else closing_stock,
            'lead_weeks': lead,
            'reserve': reserve,
        }
        for sku, bridge, part, lead, reserve, closing_stock, events in parts
        for index in range(48)
    ]


def sample_backfills() -> list[dict]:
    """Late work-order batch posted on 2026-11-25, after the 2026-11-16 close.

    The entries add demand to weeks that already had a positive order, so the
    continuous ledger stays unchanged while point-in-time backtests must ignore
    them: they were not visible on any forecast day inside the history.
    """
    return [
        {'sku': 'HB-041', 'week': '2026-10-19', 'posted_at': '2026-11-25', 'added_qty': 4},
        {'sku': 'HB-027', 'week': '2026-07-27', 'posted_at': '2026-11-25', 'added_qty': 2},
        {'sku': 'HB-014', 'week': '2026-11-02', 'posted_at': '2026-11-25', 'added_qty': 1},
    ]


def _validate_row(row):
    for key, label in (('sku', '备件编号'), ('bridge', '桥位'), ('part', '备件名称')):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f'{label}为空，请补齐备件台账后重新导入。')
    sku = row['sku']
    raw_week = row.get('week')
    try:
        week = date.fromisoformat(raw_week)
        if week.isoformat() != raw_week or week.weekday() != 0:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ValueError(f'{sku} 的周次 {raw_week} 无效，请填写 YYYY-MM-DD 格式的周一日期。') from error
    for key, label, lower, upper in (
        ('demand', '周需求量', 0, MAX_QUANTITY),
        ('on_hand', '期末可用库存', 0, MAX_QUANTITY),
        ('lead_weeks', '采购提前期', 1, MAX_LEAD_WEEKS),
        ('reserve', '最低保有量', 0, MAX_QUANTITY),
    ):
        value = row.get(key)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f'{sku} {week} 的{label}无效，请填写 {lower} 至 {upper} 的整数。')
    return week


def _group_rows(rows):
    groups = defaultdict(dict)
    for row in rows:
        week = _validate_row(row)
        ledger = groups[row['sku']]
        if week in ledger:
            raise ValueError(f"{row['sku']} {week} 存在重复周，请汇总需求并保留一条期末库存记录。")
        ledger[week] = row

    series = {}
    for sku, ledger in sorted(groups.items()):
        weeks = sorted(ledger)
        closing = ledger[weeks[-1]]
        for week in weeks:
            row = ledger[week]
            if (row['bridge'], row['part']) != (closing['bridge'], closing['part']):
                raise ValueError(f'{sku} {week} 的桥位或名称不一致，请核对备件编号及主数据。')
        for previous, current in zip(weeks, weeks[1:]):
            if (current - previous).days != 7:
                missing = previous + timedelta(weeks=1)
                raise ValueError(f'{sku} 缺少 {missing} 周，请补齐台账并为确认无需求的周填写 0。')
        series[sku] = [ledger[week] for week in weeks]

    cutoff = max(history[-1]['week'] for history in series.values())
    for sku, history in series.items():
        if history[-1]['week'] != cutoff:
            raise ValueError(f'{sku} 缺少截至 {cutoff} 的同期库存，请补齐结账周需求及库存。')
    return series, cutoff


def _validate_backfills(backfills, series):
    """Normalize a backfill batch into (sku, week_index, posting_date, qty)."""
    if not backfills:
        return []
    if len(backfills) > MAX_BACKFILL_ROWS:
        raise ValueError(f'单份补录批次最多 {MAX_BACKFILL_ROWS} 条。')
    seen = set()
    entries = []
    for position, item in enumerate(backfills, start=1):
        sku = item.get('sku')
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError(f'补录第 {position} 条备件编号为空，请补齐后重新导入。')
        sku = sku.strip()
        if sku not in series:
            raise ValueError(f'补录第 {position} 条的备件 {sku} 不在周台账中，请先导入对应备件的连续周记录。')
        try:
            demand_week = date.fromisoformat(item.get('week'))
            if demand_week.isoformat() != item.get('week') or demand_week.weekday() != 0:
                raise ValueError
        except (ValueError, TypeError) as error:
            raise ValueError(f'补录第 {position} 条的需求周 {item.get("week")} 无效，请填写周一的 YYYY-MM-DD 日期。') from error
        try:
            posted = date.fromisoformat(item.get('posted_at'))
            if posted.isoformat() != item.get('posted_at'):
                raise ValueError
        except (ValueError, TypeError) as error:
            raise ValueError(f'补录第 {position} 条的入账日 {item.get("posted_at")} 无效，请填写 YYYY-MM-DD 日期。') from error
        if posted < demand_week:
            raise ValueError(f'{sku} 需求周 {demand_week} 的补录入账日 {posted} 早于需求周，工单不能在需求发生前入账。')
        quantity = item.get('added_qty')
        if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
            raise ValueError(f'{sku} {demand_week} 的补录新增件数无效，请填写 1 至 {MAX_QUANTITY} 的整数。')
        key = (sku, demand_week)
        if key in seen:
            raise ValueError(f'{sku} {demand_week} 在同一补录批次中重复，请把同周补录汇总为一条。')
        seen.add(key)
        ledger = series[sku]
        start = date.fromisoformat(ledger[0]['week'])
        end = date.fromisoformat(ledger[-1]['week'])
        if not start <= demand_week <= end:
            raise ValueError(f'{sku} 没有 {demand_week} 周的台账记录，补录只能落在已有连续周内。')
        index = (demand_week - start).days // 7
        entries.append((sku, index, posted, quantity))
    return entries


def _asof_demand(ledger, backfill_entries, as_of):
    """Weekly demand containing only contributions posted before `as_of`.

    Regular ledger demand is booked on the week's Monday.  Backfills use their
    own entry date.  The strict comparison means a contribution posted exactly
    on `as_of` is not yet visible when the forecast is made that morning.
    """
    demand = []
    for index, row in enumerate(ledger):
        week = date.fromisoformat(row['week'])
        total = row['demand'] if week < as_of else 0
        for sku, backfill_index, posted, quantity in backfill_entries:
            if backfill_index == index and posted < as_of:
                total += quantity
        demand.append(total)
    return demand


def _settings(options):
    alpha = options.get('alpha', 0.20)
    beta = options.get('beta', 0.15)
    for label, value in (('需求量与间隔平滑 α', alpha), ('需求发生概率平滑 β', beta)):
        if type(value) not in (float, int) or not 0.01 <= value <= 0.99:
            raise ValueError(f'请将{label}设为 0.01 至 0.99 的数值。')
    origins = options.get('holdout_origins', 8)
    if type(origins) is not int or not 3 <= origins <= 12:
        raise ValueError('请将滚动留出窗口数设为 3 至 12 的整数。')
    idle_weeks = options.get('idle_weeks', 8)
    if type(idle_weeks) is not int or not 2 <= idle_weeks <= 26:
        raise ValueError('请将停用核查阈值设为 2 至 26 周的整数。')
    return alpha, beta, origins, idle_weeks


def _forecast_states(demand, alpha, beta):
    """After k observations, states[k] predicts each of the next weeks."""
    states = [(0.0, 0.0)]
    previous_event = 0
    size = interval = probability = sba = 0.0
    for week, quantity in enumerate(demand, start=1):
        if quantity:
            gap = week - previous_event
            if previous_event:
                size += alpha * (quantity - size)
                interval += alpha * (gap - interval)
                probability += beta * (1.0 - probability)
            else:
                size = quantity
                interval = gap
                probability = 1.0 / gap
            sba = (1.0 - alpha / 2.0) * size / interval
            previous_event = week
        else:
            probability *= 1.0 - beta
        states.append((sba, size * probability))
    return states


def _rolling_holdout(ledger, backfill_entries, lead, origins, alpha, beta, closing_day):
    """Point-in-time holdout over the last `origins` complete lead windows.

    Training at each origin only sees demand posted before the forecast day.
    Actuals only count contributions posted before the run's closing day.
    """
    last_origin = len(ledger) - lead
    first_origin = last_origin - origins + 1
    errors = [[], []]
    positive_windows = 0
    windows = []
    actual_demand = _asof_demand(ledger, backfill_entries, closing_day)
    for origin in range(first_origin, last_origin + 1):
        forecast_day = date.fromisoformat(ledger[origin]['week'])
        origin_demand = _asof_demand(ledger, backfill_entries, forecast_day)
        # Rebuild model states from the information set available at origin.
        origin_states = _forecast_states(origin_demand[:origin], alpha, beta)
        actual = sum(actual_demand[origin:origin + lead])
        positive_windows += actual > 0
        sba_forecast = lead * origin_states[origin][0]
        tsb_forecast = lead * origin_states[origin][1]
        sba_error = abs(sba_forecast - actual)
        tsb_error = abs(tsb_forecast - actual)
        errors[0].append(sba_error)
        errors[1].append(tsb_error)
        windows.append({
            'origin_index': origin,
            'forecast_day': forecast_day,
            'window_end': date.fromisoformat(ledger[origin + lead - 1]['week']),
            'sba_forecast': sba_forecast,
            'tsb_forecast': tsb_forecast,
            'actual': actual,
            'sba_error': sba_error,
            'tsb_error': tsb_error,
            'late_visible': sum(quantity for _sku, index, posted, quantity in backfill_entries
                                if origin <= index < origin + lead and posted < closing_day),
        })
    return mean(errors[0]), mean(errors[1]), positive_windows, first_origin, windows


def _analyze_part(ledger, backfill_entries, alpha, beta, origins, idle_weeks, closing_day):
    closing = ledger[-1]
    sku, stock, lead, reserve = (closing[key] for key in ('sku', 'on_hand', 'lead_weeks', 'reserve'))
    minimum = MIN_TRAIN_WEEKS + lead + origins - 1
    if len(ledger) < minimum:
        raise ValueError(f'{sku} 仅有 {len(ledger)} 周，当前提前期和回测设置至少需要 {minimum} 周。'
                         '请补齐连续周或减少留出窗口数。')
    # Full-history view as of closing: drives the live reorder-point forecast.
    visible_demand = _asof_demand(ledger, backfill_entries, closing_day)
    states = _forecast_states(visible_demand, alpha, beta)
    sba_mae, tsb_mae, positive_windows, first_origin, windows = _rolling_holdout(
        ledger, backfill_entries, lead, origins, alpha, beta, closing_day)
    sba, tsb = (rate * lead for rate in states[-1])
    use_tsb = tsb_mae < sba_mae and not isclose(tsb_mae, sba_mae, rel_tol=0, abs_tol=1e-9)
    selected = tsb if use_tsb else sba
    # Remove floating-point noise at a positive integer before rounding up.
    nearest_unit = round(selected)
    if nearest_unit > 0 and abs(selected - nearest_unit) <= 4 * ulp(selected):
        selected = nearest_unit
    reorder_point = ceil(selected) + reserve
    gap = max(reorder_point - stock, 0)
    if gap:
        stock_signal = '低于补货点'
    elif stock == reorder_point and reorder_point > 0:
        stock_signal = '到达补货点'
    else:
        stock_signal = '未触发补货点'

    events = [index for index, quantity in enumerate(visible_demand) if quantity > 0]
    positives = [visible_demand[index] for index in events]
    trailing_zeros = len(visible_demand) - 1 - events[-1] if events else len(visible_demand)
    adi = len(visible_demand) / len(events) if events else None
    cv2 = pvariance(positives) / mean(positives) ** 2 if len(events) >= 2 else None
    lumpy = cv2 is not None and adi >= 1.32 and cv2 >= 0.49
    flags = []
    if len(events) < 2:
        flags.append('正需求样本不足')
    if lumpy:
        flags.append('批量波动（Lumpy）')
    if trailing_zeros >= idle_weeks:
        flags.append('长期零需求，核查停用')
    if positive_windows == 0:
        flags.append('留出期全零，复核选模')

    part_windows = []
    for window in windows:
        part_windows.append({
            '备件编号': sku,
            '预测当周': window['forecast_day'].isoformat(),
            '窗口结束周': window['window_end'].isoformat(),
            'SBA预测（件）': window['sba_forecast'],
            'TSB预测（件）': window['tsb_forecast'],
            '已入账实际需求（件）': window['actual'],
            'SBA误差（件）': window['sba_error'],
            'TSB误差（件）': window['tsb_error'],
            '窗口内迟到补录件数': window['late_visible'],
        })

    return {
        '备件编号': sku,
        '桥位': closing['bridge'],
        '备件名称': closing['part'],
        '库存结账周': closing['week'],
        '提前期（周）': lead,
        '期末可用库存（件）': stock,
        '最低保有量（件）': reserve,
        '采用模型': 'TSB' if use_tsb else 'Croston SBA',
        'SBA提前期需求（件）': sba,
        'TSB提前期需求（件）': tsb,
        '补货点（件）': reorder_point,
        '库存缺口（件）': gap,
        '库存信号': stock_signal,
        '人工复核提示': '；'.join(flags) or '常规复核',
        'SBA留出MAE（件）': sba_mae,
        'TSB留出MAE（件）': tsb_mae,
        '首个留出周': ledger[first_origin]['week'],
        '有需求的留出窗口数': positive_windows,
        '连续零需求（周）': trailing_zeros,
        'ADI（周）': adi,
        'CV²': cv2,
    }, part_windows


def compute(rows: list[dict], options: dict, backfills: list[dict] | None = None) -> dict:
    if not rows:
        raise ValueError('请上传包含备件编号、周需求和期末库存的 CSV。')
    alpha, beta, origins, idle_weeks = _settings(options)
    series, cutoff = _group_rows(rows)
    backfill_entries = _validate_backfills(backfills or [], series)
    # The forecast day of the newest week is the Monday of the closing week.
    closing_day = date.fromisoformat(cutoff)
    results = []
    window_rows = []
    late_units = 0
    late_lines = 0
    for sku, ledger in series.items():
        part_entries = [entry for entry in backfill_entries if entry[0] == sku]
        result, part_windows = _analyze_part(
            ledger, part_entries, alpha, beta, origins, idle_weeks, closing_day)
        results.append(result)
        window_rows.extend(part_windows)
        late_lines += len(part_entries)
        late_units += sum(quantity for _sku, _index, posted, quantity in part_entries
                          if posted < closing_day)
    results.sort(key=lambda row: (-row['库存缺口（件）'], row['备件编号']))
    triggers = sum(row['库存信号'] != '未触发补货点' for row in results)
    reviews = sum(row['人工复核提示'] != '常规复核' for row in results)
    backfill_note = (
        f'本次绑定 {late_lines} 条迟到工单补录，其中结账日前已入账 {late_units} 件并计入实际需求；'
        '结账日后入账的补录不参与本次回测与选模。'
        if backfill_entries else '本次未绑定补录批次，周台账需求按各周周一入账处理。')
    return {
        'headline': f'{cutoff} 结账周：{triggers} 种备件触及补货点，{reviews} 种备件有专项复核提示。',
        'metrics': {
            '备件种数': len(results),
            '触及补货点数': triggers,
            '专项复核数': reviews,
            '每种备件留出窗口数': origins,
            '采用 TSB 数': sum(row['采用模型'] == 'TSB' for row in results),
            '补录条数': late_lines,
            '结账日前补录入账件数': late_units,
        },
        'rows': results,
        'windows': window_rows,
        'window_columns': list(window_rows[0]) if window_rows else [],
        'can_publish': True,
        'notes': [
            f'SBA 用 α={alpha:g} 平滑正需求量与间隔，再乘 (1−α/2) 修正。TSB 同样平滑正需求量，'
            f'并以 β={beta:g} 逐周更新发生概率，零需求周使概率衰减。两者在首个正需求周初始化，'
            '初始间隔为从台账首周起计的周数，初始概率取该间隔的倒数。',
            '工单常在结账后才补录。周台账一行视为在该周周一入账，迟到工单补录按各自入账日入账。'
            '每个回测窗口只拿预测当日（起点周周一）之前已入账的需求训练；算误差时只用本次结账日'
            '（最近结账周周一）之前已入账的实际需求，结账日后才补录的工单不会改写历史误差，'
            '避免拿补齐后的记录高估预测水平。',
            f'回测取最近 {origins} 个完整提前期窗口，每次推进一周，训练至少 8 周且只用留出周之前、'
            '且当时已入账的需求。两种模型都用当时的周需求率乘提前期，与窗口内结账日前可见的实际'
            '总需求比较，按 MAE 较低者选模，并列用 SBA。窗口可能重叠。无正需求训练段的预测为 0，'
            '留出期全零时须复核评分。',
            '按全史更新选中模型时同样只累计结账日前已入账的需求，补货点取提前期预计件数向上取整'
            '再加最低保有量。缺口为补货点减同期可用库存，最低记 0。补货点为正且库存等于该点时也'
            '提示复核。信号仅供盘点和采购复核，采购量还需核对在途和调拨。',
            '每次分析在提交时绑定周台账版本与补录批次，按规范化内容的 SHA-256 去重；重跑旧分析'
            '不会改换当时选定的周记录和补录批次，新补录需新建分析任务。',
            'ADI 为总周数除以正需求周数，CV² 为正需求量总体方差除以均值平方。'
            'ADI ≥ 1.32 且 CV² ≥ 0.49 标为 Lumpy，集中检修的大批量领用可超过均值预测，请核对检修工单。',
            f'连续 {idle_weeks} 周零需求触发停用核查。请区分退役、缺货未领、替代件和工单漏记。'
            '低 TSB 预测不足以判定停用或清退，关键件保有量由维护规程复核。',
            backfill_note,
        ],
        'chart': {
            'label': '提前期需求估计（按库存缺口排列）',
            'unit': '件',
            'points': [
                {'label': f"{row['备件编号']} · {model}", 'value': row[f'{model}提前期需求（件）']}
                for row in results[:6] for model in ('SBA', 'TSB')
            ],
        },
    }

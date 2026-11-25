import {api,download} from '../api/client.js';
import {empty,errorSlot} from '../components/common/layout.js';
import {report} from '../components/common/report.js';
import {table} from '../components/common/table.js';
import {decisions,states} from '../constants/enums.js';
import {bindForm} from '../hooks/forms.js';
import {state} from '../stores/session.js';
import {fieldInput} from '../types/fields.js';
import {esc,formData,time} from '../utils/html.js';

export async function analysis() {
  const profile = state.profile;
  const [datasets,runs,backfills] = await Promise.all([api('/datasets'),api('/runs'),api('/backfills')]);
  const batchOptions = ['<option value="">不绑定补录（按周台账入账）</option>']
    .concat(backfills.map(row => `<option value="${row.id}" ${row.id === state.backfillId ? 'selected' : ''}>${esc(row.name)} · #${row.id}</option>`))
    .join('');
  const form = state.account.role === 'analyst' && datasets.length ? `<form id="queue" class="inline-form"><label>数据版本<select name="dataset_id">${datasets.map(row => `<option value="${row.id}" ${row.id === state.datasetId ? 'selected' : ''}>${esc(row.name)} · #${row.id}</option>`).join('')}</select></label><label>迟到工单补录批次<select name="backfill_id">${batchOptions}</select></label>${profile.options.map(field => `<label>${esc(field.label)}${fieldInput(field,esc)}</label>`).join('')}<button type="submit">提交计算</button>${errorSlot}</form>` : empty(datasets.length ? '分析员可提交计算。' : '先导入数据版本。', '#1', '查看数据台账');
  return `<section><h2>计算条件</h2><p>${esc(profile.method)}</p><p class="hint">每个回测窗口只拿预测当日前已入账的需求训练，误差按本次结账日前已入账的实际需求计算；任务提交后即固定周记录与补录批次。</p>${form}</section><section><h2>分析记录</h2>${table(runs.map(run => ({任务:{link:`#3/${run.id}`,text:`#${run.id}`},数据版本:run.dataset_id,补录批次:run.backfill_id ?? '—',状态:states[run.state],提交时间:time(run.created),复核:decisions[run.decision] ?? '待复核'})))}</section>`;
}

export async function currentRun() {
  const runs = await api('/runs');
  const chosen = runs.find(item => item.id === state.runId) || runs[0];
  return {runs,run:chosen ? await api(`/runs/${chosen.id}`) : null};
}

export function chooseRun(runs, selected) {
  return `<label>分析任务<select id="pick-run">${runs.map(row => `<option value="${row.id}" ${selected === row.id ? 'selected' : ''}>#${row.id} · ${states[row.state]} · ${time(row.created)}</option>`).join('')}</select></label>`;
}

function backtestWindows(run) {
  const result = run.result;
  if (!result?.windows?.length) return '';
  const columns = (result.window_columns || Object.keys(result.windows[0])).map(key => ({key,label:key}));
  return `<section class="backtest"><h2>逐窗口回测（按入账时点）</h2><p class="hint">两种预测、本次结账日前已入账的实际需求与逐窗口误差；重跑旧分析不会改换当时选定的周记录和补录批次。</p><button id="backtest-download" class="secondary" data-id="${run.id}" type="button">下载逐窗口回测 CSV</button><p id="backtest-error" class="error" role="alert"></p>${table(result.windows,columns)}</section>`;
}

export async function results() {
  const {runs,run} = await currentRun();
  if (!run) return empty('提交一条分析任务后可在这里查看结果。','#2','配置分析');
  const result = run.result ? report(run.result) : `<p ${run.error ? 'class="error"' : 'data-poll'}>${esc(run.error || states[run.state])}</p>`;
  const pinned = run.result
    ? `<p class="hint">绑定周记录 #${run.dataset_id} · 补录批次 ${run.backfill_id ? `#${run.backfill_id}` : '无'} · 计算状态 ${states[run.state]}</p>`
    : '';
  return `<section>${chooseRun(runs,run.id)}${pinned}${result}${backtestWindows(run)}${run.result ? `<a class="button" href="#4/${run.id}">进入业务复核</a>` : '<a href="#2">回到分析条件</a>'}</section>`;
}

export function bindAnalysis(root, section, refresh) {
  bindForm(root, '#queue', async form => {
    const {dataset_id,backfill_id,...options} = formData(form);
    const run = await api('/runs',{dataset_id:Number(dataset_id),backfill_id:backfill_id ? Number(backfill_id) : null,options});
    location.hash = `3/${run.id}`;
  });
  root.querySelector('#pick-run')?.addEventListener('change', event => {location.hash = `${section}/${event.target.value}`;});
  root.querySelector('#backtest-download')?.addEventListener('click', async event => {
    const key = event.currentTarget.dataset.id;
    const slot = root.querySelector('#backtest-error');
    try {
      await download(`/runs/${key}/backtest.csv`, `回测窗口-${key}.csv`);
    } catch (error) {
      if (slot) slot.textContent = error.message;
    }
  });
}

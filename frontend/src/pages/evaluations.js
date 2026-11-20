import {heading} from '../components/common/header.js';
import {table} from '../components/common/table.js';
import {esc, time} from '../utils/format.js';
import {status} from '../components/common/status.js';

function outputLedger(d) {
  const rows = d.departmentOutputs.map(r => ({
    部门: r.department, 月份: r.period, 产量: r.output, 改造: r.retrofit ? '是' : '否',
  }));
  const form = d.role === 'viewer' ? '' : `<form id="outputs-batch" class="inline-form">
      <label>研究部门<input name="department" required placeholder="材料试验室"></label>
      <label>月份<input name="period" required placeholder="2026-10"></label>
      <label>产量<input name="output" type="number" step="any" min="0" required></label>
      <label>改造部门<select name="retrofit"><option value="">保持不变</option><option value="false">否</option><option value="true">是</option></select></label>
      <button>登记/更新一行</button>
    </form>
    <p class="warning">同一部门同一月份再次提交即更新产量；改造标记按部门保存，选择「保持不变」不会改动原标记。</p>`;
  return form + `
    <details open><summary>已登记 ${rows.length} 行部门产量</summary>${table(rows)}</details>`;
}

export function evaluations(c, d) {
  const signed = d.runs.filter(r => r.approved && r.snapshot?.period && r.snapshot.period !== '全部')
    .sort((a, b) => a.snapshot.period < b.snapshot.period ? -1 : 1);
  return heading('RETROFIT REVIEW / 07', '改造部门净减排评估', '登记各部门两期产量与改造标记；用前期月报冻结因子重算两份已复核月报，配对后求同优方案的净减排均值区间。')
    + `<details open><summary>部门产量与改造登记</summary>${outputLedger(d)}</details>`
    + `<h2>发起评估</h2>
    <p>每对净减排 = 改造部门单位产量排放降幅 − 对照部门单位产量排放降幅；单位产量排放 = 部门排放 ÷ 部门产量。配对仅允许前期产量差不超过上限，先配最多对，再让产量绝对差总和最小。</p>
    ${d.role === 'viewer' ? '<p class="empty">业务阅读者可查看区间与配对；发起评估需要分析师或负责人。</p>' : `<form id="evaluation-form" class="inline-form" data-key="">
      <label>评估名称<input name="name" value="改造净减排配对评估" required></label>
      <label>前期已复核月报<select name="earlier_run_id">${signed.map((r, i) => `<option value="${r.id}" ${i === 0 ? 'selected' : ''}>${esc(r.name)}（${esc(r.snapshot.period)}）</option>`).join('')}</select></label>
      <label>后期已复核月报<select name="later_run_id">${signed.map((r, i) => `<option value="${r.id}" ${i === signed.length - 1 ? 'selected' : ''}>${esc(r.name)}（${esc(r.snapshot.period)}）</option>`).join('')}</select></label>
      <label>前期产量差上限<input name="gap_limit" type="number" step="any" min="0" value="50" required></label>
      <button>发起评估</button>
    </form>`}
    <p class="warning">排队时冻结报告快照、部门产量与改造标记；网络重试沿用同一申请编号，只生效一次。改表后必须重新发起，旧结论不可发布。</p>`
    + evaluationList(c, d);
}

function pairTable(pairs) {
  return table(pairs.map(p => ({
    改造部门: p.retrofit, 对照部门: p.control,
    改造前期产量: p.retrofit_output_earlier, 对照前期产量: p.control_output_earlier,
    前期产量差: p.output_gap, 改造降幅: p.retrofit_reduction,
    对照降幅: p.control_reduction, 净减排: p.net_reduction,
  })));
}

function resultPanel(e) {
  if (!e.result) return `<p>${e.error ? '评估失败：' + esc(e.error) : '排队中，稍后刷新。'}</p>`;
  const r = e.result, s = r.summary, lo = r.bounds.lower, hi = r.bounds.upper;
  const degenerate = s['均值区间是否退化为单值'];
  return `<div class="metrics">
      <div><small>最大配对数</small><strong>${s['最大配对数']}</strong></div>
      <div><small>最小产量差总和</small><strong>${s['最小产量绝对差总和']}</strong></div>
      <div><small>同优均值下界</small><strong>${s['同优均值下界']}</strong></div>
      <div><small>同优均值上界</small><strong>${s['同优均值上界']}</strong></div>
    </div>
    <p class="method">同优配对净减排均值区间：<b>${s['同优均值下界']} ≤ 均值 ≤ ${s['同优均值上界']}</b>（kgCO₂e/单位产量；精确端点 ${esc(s['同优均值下界精确值'])} 与 ${esc(s['同优均值上界精确值'])}）${degenerate ? '。全部同优配对均值相同，区间退化为单值。' : ''}</p>
    <p class="method">${esc(r.method)}</p>
    ${r.warnings?.length ? '<p class="warning">' + r.warnings.map(esc).join('；') + '</p>' : ''}
    <h3>下界对应的一组配对（均值 ${lo.mean_net_reduction}）</h3>${pairTable(lo.pairs)}
    <h3>上界对应的一组配对（均值 ${hi.mean_net_reduction}）</h3>${pairTable(hi.pairs)}
    <h3>部门单位产量排放与降幅</h3>${table(r.departments)}`;
}

export function evaluationList(c, d) {
  if (!d.evaluations.length) return '<h2>评估记录</h2><p class="empty">暂无评估。</p>';
  return '<h2>评估记录</h2>' + d.evaluations.map(e => `<section class="report">
    <div class="report-top"><h2>${esc(e.name)}</h2>${status(e.status)}${e.published ? '<span class="status completed">已发布</span>' : ''}</div>
    <p>依据：报告 ${e.earlier_run_id} → ${e.later_run_id} · 前期产量差上限 ${esc(e.gap_limit)} · 申请编号 ${esc(e.request_key)}</p>
    <p class="checksum">冻结依据 SHA256 ${esc(e.checksum)}</p>
    <p>${e.published ? '已发布，结论冻结于 ' + time(e.published_at) : '未发布'}</p>
    ${resultPanel(e)}
    ${e.status === 'completed' && !e.published ? `<button class="publish-eval" data-id="${e.id}">核对区间并发布</button>` : ''}
  </section>`).join('');
}

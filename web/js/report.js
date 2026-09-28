/* 回测报告渲染器：指标卡 + 净值/基准曲线 + 回撤曲线 + 交易明细 */

function renderBacktestReport(container, result, title = '回测报告') {
  const r = result;
  const benchName = r.benchmark_name || '买入持有';
  const metrics = [
    { k: '总收益率', v: fmtPct(r.total_return), cls: pctClass(r.total_return) },
    { k: `基准收益(${benchName})`, v: fmtPct(r.benchmark_return), cls: pctClass(r.benchmark_return) },
    { k: '超额收益', v: fmtPct(r.excess_return), cls: pctClass(r.excess_return) },
    { k: '年化收益率', v: fmtPct(r.annual_return), cls: pctClass(r.annual_return) },
    { k: '最大回撤', v: fmtPct(r.max_drawdown), cls: 'down' },
    { k: '胜率', v: fmtPct(r.win_rate, 1), cls: '' },
    { k: '盈亏比', v: fmtNum(r.profit_factor), cls: '' },
    { k: '夏普比率', v: fmtNum(r.sharpe_ratio), cls: '' },
    { k: '卡玛比率', v: fmtNum(r.calmar_ratio), cls: '' },
    { k: '交易次数', v: r.total_trades, cls: '' },
    { k: '平均持仓', v: fmtNum(r.avg_hold_days, 1) + '天', cls: '' },
    { k: '最终资金', v: fmtNum(r.final_value), cls: pctClass(r.total_return) },
  ];

  const uid = 'rpt_' + Math.random().toString(36).slice(2, 8);
  container.innerHTML = `
    <div class="card">
      <h3>${title}${r.from_cache ? ' <span class="hint">(缓存结果)</span>' : ''}</h3>
      <div class="report-metrics">
        ${metrics.map(m => `<div class="metric-card"><div class="v ${m.cls}">${m.v}</div><div class="k">${m.k}</div></div>`).join('')}
      </div>
      <div id="${uid}_equity" class="report-chart"></div>
      <div id="${uid}_dd" class="report-chart-small"></div>
      <h3 style="margin-top:14px">交易明细 (${r.trades.length}笔)</h3>
      <div class="table-wrap">
        <table><thead><tr><th>日期</th><th>类型</th><th>价格</th><th>股数</th><th>资金余额</th><th>盈亏</th></tr></thead>
        <tbody>${r.trades.map(t => `<tr>
          <td>${t.date}</td><td>${t.type}</td><td>${fmtNum(t.price)}</td>
          <td>${t.shares}</td><td>${fmtNum(t.cash_after)}</td>
          <td class="${t.profit > 0 ? 'up' : t.profit < 0 ? 'down' : ''}">${t.profit ? fmtNum(t.profit) : '--'}</td>
        </tr>`).join('')}</tbody></table>
      </div>
    </div>`;

  // 净值 vs 基准曲线
  const dv = r.daily_values;
  const dates = dv.map(d => d.date);
  const base = dv[0] ? dv[0].total_value : 1;
  const navData = dv.map(d => (d.total_value / base * 100).toFixed(2));

  const benchPts = dv.filter(d => d.benchmark !== undefined);
  let benchSeries = [];
  if (benchPts.length > 1) {
    const b0 = benchPts[0].benchmark;
    const bmap = {};
    benchPts.forEach(d => { bmap[d.date] = (d.benchmark / b0 * 100).toFixed(2); });
    benchSeries = [{
      name: `基准(${benchName})`, type: 'line', showSymbol: false,
      data: dates.map(d => bmap[d] !== undefined ? bmap[d] : null),
      lineStyle: { width: 1.5, type: 'dashed' }, itemStyle: { color: '#94a3b8' },
      connectNulls: true
    }];
  }

  const eqChart = registerChart(echarts.init(document.getElementById(uid + '_equity')));
  eqChart.setOption({
    animation: false,
    title: { text: '净值曲线（起点=100）', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    legend: { data: ['策略净值', `基准(${benchName})`], top: 4 },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    dataZoom: [{ type: 'inside' }],
    series: [{
      name: '策略净值', type: 'line', data: navData, showSymbol: false,
      lineStyle: { width: 2 }, itemStyle: { color: '#2563eb' },
      areaStyle: { color: 'rgba(37,99,235,0.08)' }
    }, ...benchSeries]
  });

  // 回撤曲线
  let peak = -Infinity;
  const ddData = dv.map(d => {
    peak = Math.max(peak, d.total_value);
    return peak > 0 ? -((peak - d.total_value) / peak * 100).toFixed(2) : 0;
  });
  const ddChart = registerChart(echarts.init(document.getElementById(uid + '_dd')));
  ddChart.setOption({
    animation: false,
    title: { text: '回撤(%)', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 34, bottom: 24 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false } },
    yAxis: { splitLine: { lineStyle: { color: '#f1f5f9' } }, axisLabel: { fontSize: 10 } },
    series: [{
      type: 'line', data: ddData, showSymbol: false,
      lineStyle: { width: 1, color: '#ef4444' }, itemStyle: { color: '#ef4444' },
      areaStyle: { color: 'rgba(239,68,68,0.15)' }
    }]
  });

  return { eqChart, ddChart };
}

function openReportModal(result) {
  const modal = document.getElementById('report-modal');
  const body = document.getElementById('report-modal-body');
  modal.classList.remove('hidden');
  renderBacktestReport(body, result);
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('report-close').addEventListener('click', () => {
    document.getElementById('report-modal').classList.add('hidden');
  });
  document.getElementById('report-modal').addEventListener('click', (e) => {
    if (e.target.id === 'report-modal') e.target.classList.add('hidden');
  });
});

/* 策略工坊：单次回测 + SVM寻优 + 敏感性热力图 */
const Lab = {
  getTimingParams: null,
  optimalParams: null,
  lastSens: null,

  /* 切回本页时重绘热力图（防止寻优在后台完成时图表渲染到隐藏容器） */
  async onShow() {
    if (this.lastSens) this.renderHeatmap(this.lastSens);
  },

  async init() {
    const meta = await loadMeta();
    await refreshAllStockSelects();

    const timingSel = document.getElementById('lab-timing');
    timingSel.innerHTML = Object.keys(meta.timing_strategies)
      .map(k => `<option>${k}</option>`).join('');

    const stopSel = document.getElementById('lab-stop');
    stopSel.innerHTML = Object.keys(meta.stop_strategies)
      .map(k => `<option>${k}</option>`).join('');
    stopSel.value = '稳健型';

    this._rebuildParams();
    timingSel.addEventListener('change', () => this._rebuildParams());

    document.getElementById('btn-lab-backtest').addEventListener('click', () => this.runBacktest());
    document.getElementById('btn-lab-optimize').addEventListener('click', () => this.runOptimize());
    document.getElementById('btn-apply-optimal').addEventListener('click', () => this.applyOptimal());
  },

  _rebuildParams() {
    const name = document.getElementById('lab-timing').value;
    const meta = META.timing_strategies[name];
    this.getTimingParams = buildParamSliders(
      document.getElementById('lab-timing-params'),
      meta ? meta.params : {});
  },

  async runBacktest() {
    const name = document.getElementById('lab-stock').value;
    if (!name) return toast('请先拉取股票数据（数据管理页）');
    const panel = document.getElementById('report-panel');
    panel.innerHTML = '<div class="card loading">回测运行中...</div>';
    try {
      const r = await API.post('/api/backtest', {
        name,
        timing: document.getElementById('lab-timing').value,
        timing_params: this.getTimingParams ? this.getTimingParams() : {},
        stop_strategy: document.getElementById('lab-stop').value,
        buy_ratio: Number(document.getElementById('lab-buy-ratio').value)
      });
      renderBacktestReport(panel, r);
    } catch (e) {
      panel.innerHTML = '';
      toast(e.message);
    }
  },

  async runOptimize() {
    const name = document.getElementById('lab-stock').value;
    if (!name) return toast('请先拉取股票数据');
    const btn = document.getElementById('btn-lab-optimize');
    const wrap = document.getElementById('optimize-progress');
    btn.disabled = true;
    wrap.classList.remove('hidden');
    try {
      const { task_id } = await API.post('/api/optimize', {
        name, buy_ratio: Number(document.getElementById('lab-buy-ratio').value)
      });
      const result = await pollTask(task_id, (cur, total, msg) => {
        const pct = total > 0 ? Math.round(cur / total * 100) : 0;
        document.getElementById('optimize-fill').style.width = pct + '%';
        document.getElementById('optimize-text').textContent = `${msg} (${cur}/${total})`;
      });
      document.getElementById('optimize-fill').style.width = '100%';
      document.getElementById('optimize-text').textContent = '完成 ✓';
      this.showOptimizeResult(result);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  },

  showOptimizeResult(r) {
    this.optimalParams = r.best_params;
    document.getElementById('optimize-result').classList.remove('hidden');
    const p = r.best_params || {};
    const names = { fast: '快线', slow: '慢线', signal: '信号线', stop_loss: '止损',
                    take_profit_1: '一阶止盈', take_profit_2: '二阶止盈', sell_ratio: '止盈卖出比例' };
    document.getElementById('optimize-best').innerHTML = `
      <p style="margin-bottom:8px">方法: <b>${r.method === 'svm' ? 'SVM代理模型' : '网格搜索'}</b>　
      最优收益率: <b class="${pctClass(r.best_score)}">${fmtPct(r.best_score)}</b></p>
      <div class="report-metrics">
        ${Object.entries(p).map(([k, v]) =>
          `<div class="metric-card"><div class="v">${v}</div><div class="k">${names[k] || k}</div></div>`).join('')}
      </div>`;
    if (r.sensitivity && r.sensitivity.scores.length) {
      this.lastSens = r.sensitivity;
      this.renderHeatmap(r.sensitivity);
    }
    document.getElementById('optimize-result').scrollIntoView({ behavior: 'smooth' });
  },

  renderHeatmap(sens) {
    const dom = document.getElementById('heatmap-chart');
    const data = [];
    const vals = [];
    sens.scores.forEach((row, si) => {
      row.forEach((v, fi) => {
        if (v !== null && v !== undefined && isFinite(v)) {
          data.push([fi, si, v]);
          vals.push(v);
        }
      });
    });
    // 空数据保护：全部格子无信号时显示提示而不是崩坏的图
    if (!vals.length) {
      dom.innerHTML = '<p class="hint" style="text-align:center;padding-top:120px">该股票在此参数网格下无有效回测结果，敏感性数据不足</p>';
      return;
    }
    renderChart('heatmap-chart', {
      animation: false,
      tooltip: {
        formatter: p => `快线${sens.fast[p.value[0]]} × 慢线${sens.slow[p.value[1]]}<br/>收益率: ${p.value[2]}%`
      },
      grid: { left: 70, right: 90, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: sens.fast.map(f => '快线' + f), splitArea: { show: true } },
      yAxis: { type: 'category', data: sens.slow.map(s => '慢线' + s), splitArea: { show: true } },
      visualMap: {
        min: Math.min(...vals), max: Math.max(...vals),
        calculable: true, orient: 'vertical', right: 10, top: 'center',
        inRange: { color: ['#10b981', '#fef3c7', '#ef4444'] },
        text: ['高收益', '低收益']
      },
      series: [{
        type: 'heatmap', data,
        label: { show: true, fontSize: 10, formatter: p => p.value[2] },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.3)' } }
      }]
    });
  },

  applyOptimal() {
    if (!this.optimalParams) return;
    const p = this.optimalParams;
    // 切到MACD择时并填入参数
    document.getElementById('lab-timing').value = 'MACD金叉死叉';
    this._rebuildParams();
    const sliders = document.getElementById('lab-timing-params').querySelectorAll('.param-item');
    sliders.forEach(item => {
      const label = item.querySelector('span').textContent;
      const map = { '快线': 'fast', '慢线': 'slow', '信号线': 'signal' };
      const key = map[label];
      if (key && p[key] !== undefined) {
        const input = item.querySelector('input');
        input.value = p[key];
        item.querySelector('.val').textContent = p[key];
      }
    });
    toast('已将最优MACD参数应用到回测面板（止损参数寻优结果见上方卡片）');
  }
};

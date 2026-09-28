/* 行情分析页：K线 + 择时参数滑块实时重画 + 选股侧栏 + 模拟交易 */
const Market = {
  chart: null,
  getTimingParams: null,
  currentStock: null,

  async init() {
    this.chart = new KlineChart('kline-chart');
    const meta = await loadMeta();

    // 股票搜索框（共享datalist）与择时策略下拉
    await refreshAllStockSelects();
    const timingSel = document.getElementById('market-timing');
    timingSel.innerHTML = '<option value="">无择时信号</option>' +
      Object.keys(meta.timing_strategies).map(k => `<option>${k}</option>`).join('');
    timingSel.value = 'MACD金叉死叉';

    const selectSel = document.getElementById('select-strategy');
    selectSel.innerHTML = Object.keys(meta.selection_strategies)
      .map(k => `<option title="${meta.selection_strategies[k].desc}">${k}</option>`).join('');

    // 止损策略下拉（回测/模拟交易共用）
    const stopSel = document.getElementById('market-stop');
    stopSel.innerHTML = Object.keys(meta.stop_strategies)
      .map(k => `<option>${k}</option>`).join('');
    stopSel.value = '稳健型';

    this._rebuildTimingParams();

    // 事件
    document.getElementById('market-stock').addEventListener('change', () => this.loadKline());
    timingSel.addEventListener('change', () => { this._rebuildTimingParams(); this.loadKline(); });
    document.getElementById('btn-apply-timing').addEventListener('click', () => this.loadKline());
    document.getElementById('btn-run-backtest').addEventListener('click', () => this.runBacktest());
    document.getElementById('btn-exec-trade').addEventListener('click', () => this.execTrade());
    document.getElementById('btn-run-select').addEventListener('click', () => this.runSelect());
    document.getElementById('btn-acc-reset').addEventListener('click', () => this.resetAccount());

    await this.refreshAccount();
    await this.loadKline();
  },

  /* 每次回到行情页：如果当前选中股票变了（比如刚拉完数据），自动加载 */
  async onShow() {
    const name = document.getElementById('market-stock').value.trim();
    if (name && name !== this.currentStock && isValidStock(name)) {
      await this.loadKline();
      await this.refreshAccount();
    }
  },

  _rebuildTimingParams() {
    const name = document.getElementById('market-timing').value;
    const meta = META.timing_strategies[name];
    this.getTimingParams = buildParamSliders(
      document.getElementById('market-timing-params'),
      meta ? meta.params : {});
    // 滑块拖动后防抖实时重画
    let timer;
    document.getElementById('market-timing-params').addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => this.loadKline(), 400);
    });
  },

  async loadKline() {
    const name = document.getElementById('market-stock').value.trim();
    if (!name) return;
    if (!isValidStock(name)) {
      toast('未找到该股票，请从下拉列表中选择');
      return;
    }
    this.currentStock = name;
    const timing = document.getElementById('market-timing').value;
    const params = this.getTimingParams ? this.getTimingParams() : {};
    try {
      const data = await API.get(
        `/api/kline?name=${encodeURIComponent(name)}&timing=${encodeURIComponent(timing)}&params=${encodeURIComponent(JSON.stringify(params))}`);
      this.chart.render(data, {
        subIndicator: timing.includes('KDJ') ? 'KDJ' : 'MACD',
        showBoll: timing.includes('布林') || timing.includes('BOLL')
      });
    } catch (e) { toast(e.message); }
  },

  _tradeSetup() {
    return {
      stop_strategy: document.getElementById('market-stop').value,
      buy_ratio: Number(document.getElementById('market-buy-ratio').value)
    };
  },

  async runBacktest() {
    const name = document.getElementById('market-stock').value;
    const timing = document.getElementById('market-timing').value;
    if (!name || !timing) return toast('请选择股票和择时策略');
    try {
      toast('回测运行中...');
      const r = await API.post('/api/backtest', {
        name, timing, timing_params: this.getTimingParams ? this.getTimingParams() : {},
        ...this._tradeSetup()
      });
      openReportModal(r);
    } catch (e) { toast(e.message); }
  },

  async execTrade() {
    const name = document.getElementById('market-stock').value;
    const timing = document.getElementById('market-timing').value;
    if (!name || !timing) return toast('请选择股票和择时策略');
    if (!confirm(`用账户全部资金对 ${name} 执行一次「整段历史」模拟交易？\n（止损：${document.getElementById('market-stop').value}，买入比例见工具栏）`)) return;
    try {
      const r = await API.post('/api/trade/execute', {
        name, timing, timing_params: this.getTimingParams ? this.getTimingParams() : {},
        ...this._tradeSetup()
      });
      toast(`模拟交易完成：收益 ${r.total_return.toFixed(2)}%，账户余额 ${fmtNum(r.final_value)}`);
      await this.refreshAccount();
    } catch (e) { toast(e.message); }
  },

  async runSelect() {
    const strategy = document.getElementById('select-strategy').value;
    const box = document.getElementById('select-results');
    box.innerHTML = '<p class="hint">选股中...</p>';
    try {
      const r = await API.post('/api/select', { strategy, top_n: 8 });
      if (!r.results.length) {
        box.innerHTML = '<p class="hint">当前没有符合条件的股票</p>';
        return;
      }
      box.innerHTML = '';
      r.results.forEach(item => {
        const card = document.createElement('div');
        card.className = 'stock-card';
        card.innerHTML = `<div class="name">${item.name}</div><div class="reason">${item.reason}</div>`;
        card.addEventListener('click', async () => {
          document.getElementById('market-stock').value = item.name;
          await this.loadKline();
          toast(`已加载 ${item.name}`);
          document.getElementById('kline-chart').scrollIntoView({ behavior: 'smooth' });
        });
        box.appendChild(card);
      });
    } catch (e) { box.innerHTML = `<p class="hint">${e.message}</p>`; }
  },

  async refreshAccount() {
    try {
      const r = await API.get('/api/account');
      document.getElementById('acc-cash').textContent = fmtNum(r.account.cash);
      const profit = r.account.cash - r.account.initial_cash;
      const el = document.getElementById('acc-profit');
      el.textContent = (profit >= 0 ? '+' : '') + fmtNum(profit);
      el.style.color = profit >= 0 ? '#ef4444' : '#10b981';
      const h = document.getElementById('trade-history-mini');
      h.innerHTML = r.history.slice().reverse().map(t =>
        `<div>[${t.time}] ${t.stock} · ${t.timing} · ${t.trades}笔 · 收益${t.return}%</div>`).join('')
        || '<div>暂无交易记录</div>';
    } catch (e) { /* 忽略 */ }
  },

  async resetAccount() {
    if (!confirm('确定清零重置账户和交易记录吗？')) return;
    await API.post('/api/account/reset');
    await this.refreshAccount();
    toast('账户已重置');
  }
};

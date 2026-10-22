/* 其余页面：箱体 / ML / 资讯 / AI / 词典 / 数据管理 / 设置 */

/* ============ 大盘指数 ============ */
const IndexPage = {
  chart: null,
  current: '000001.SH',

  async init() {
    this.chart = new KlineChart('index-kline');
    const r = await API.get('/api/index/list');
    const box = document.getElementById('index-chips');
    box.innerHTML = '';
    Object.entries(r.indices).forEach(([code, name]) => {
      const chip = document.createElement('button');
      chip.className = 'chip' + (code === this.current ? ' chip-active' : '');
      chip.textContent = name;
      chip.dataset.code = code;
      chip.addEventListener('click', () => {
        this.current = code;
        box.querySelectorAll('.chip').forEach(c => c.classList.remove('chip-active'));
        chip.classList.add('chip-active');
        this.load();
      });
      box.appendChild(chip);
    });
    await this.load();
  },

  async load() {
    try {
      const d = await API.get(`/api/index/kline?code=${this.current}&days=500`);
      this.chart.render(d, { subIndicator: 'none' });
    } catch (e) { toast(e.message); }
  }
};

/* ============ 箱体分析 ============ */
const BoxPage = {
  async init() {
    document.getElementById('btn-box-run').addEventListener('click', () => this.run());
  },

  async run() {
    const btn = document.getElementById('btn-box-run');
    const wrap = document.getElementById('box-progress');
    btn.disabled = true;
    wrap.classList.remove('hidden');
    try {
      const { task_id } = await API.post('/api/box/batch');
      const r = await pollTask(task_id, (cur, total, msg) => {
        const pct = total > 0 ? Math.round(cur / total * 100) : 0;
        document.getElementById('box-fill').style.width = pct + '%';
        document.getElementById('box-text').textContent = `${msg} (${cur}/${total})`;
      });
      document.getElementById('box-fill').style.width = '100%';
      document.getElementById('box-text').textContent = '完成 ✓';
      this.render(r);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  },

  render(r) {
    const box = document.getElementById('box-results');
    if (!r.strategy_stats.length) {
      box.innerHTML = '<div class="card"><p class="hint">没有发现任何信号</p></div>';
      return;
    }
    const holds = r.hold_days || [3, 5, 10, 20];

    const cell = (v, suffix = '%') => v === null || v === undefined
      ? '<td>--</td>'
      : `<td class="${pctClass(v)}">${v}${suffix}</td>`;

    box.innerHTML = `
      <div class="card">
        <h3>各策略胜率统计（共${r.total_signals}个信号 / ${r.stock_count}只股票）</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>策略</th><th>说明</th><th>信号数</th>
            ${holds.map(h => `<th>${h}日胜率</th><th>${h}日均收益</th>`).join('')}</tr></thead>
          <tbody>${r.strategy_stats.map(s => `<tr>
            <td title="${s.marker}">${s.name || s.marker}</td><td class="hint">${s.desc}</td><td>${s.signal_count}</td>
            ${holds.map(h => cell(s['win_' + h]) + cell(s['avg_' + h])).join('')}
          </tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="card">
        <h3>最近信号明细</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>股票</th><th>信号日</th><th>买入日</th><th>策略</th>
            ${holds.map(h => `<th>${h}日收益</th>`).join('')}</tr></thead>
          <tbody>${r.recent_signals.map(s => `<tr>
            <td>${s.code}</td><td>${s.meetdate}</td><td>${s.buydate}</td>
            <td title="${s.desc}">${s.name || s.marker}</td>
            ${holds.map(h => cell(s['h' + h])).join('')}
          </tr>`).join('')}</tbody>
        </table></div>
      </div>`;
  }
};

/* ============ ML预测 ============ */
/* 特征名 → 中文对照（特征重要性图表展示用） */
const FEATURE_CN = {
  return_1: '1日动量', return_3: '3日动量', return_5: '5日动量', return_10: '10日动量',
  vol_ratio: '量比(对5日均量)', vol_trend: '量能趋势(5/20日)', pv_corr: '量价相关性',
  price_position: '20日价格位置', volatility_5: '5日波动率', volatility_10: '10日波动率',
  ma5_bias: 'MA5偏离度', ma10_bias: 'MA10偏离度', ma20_bias: 'MA20偏离度',
  ma_alignment: '均线多头排列', kdj_k: 'KDJ-K值', kdj_j: 'KDJ-J值',
  boll_width: '布林带宽', boll_pos: '布林带位置',
  macd_dif_norm: 'MACD-DIF(归一)', macd_bar_norm: 'MACD柱(归一)', rsi: 'RSI强弱'
};
const featureCN = k => FEATURE_CN[k] || k;

const MlPage = {
  async init() {
    await refreshAllStockSelects();
    document.getElementById('btn-ml-train').addEventListener('click', () => this.train());
    document.getElementById('btn-ml-predict').addEventListener('click', () => this.predict());
  },

  async train() {
    const name = document.getElementById('ml-stock').value;
    if (!name) return toast('请先拉取股票数据');
    const info = document.getElementById('ml-train-info');
    info.innerHTML = '<p class="hint">训练中（含5折时序交叉验证）...</p>';
    try {
      const r = await API.post('/api/ml/train', {
        name, target_days: Number(document.getElementById('ml-days').value)
      });
      if (r.error) { info.innerHTML = `<p class="hint">${r.error}</p>`; return; }
      info.innerHTML = `<p class="hint">CV准确率 ${r.cv_accuracy}%±${r.cv_std}%（基线 ${r.baseline_accuracy}%）样本 ${r.samples}</p>`;
      this.renderCv(r);
      this.renderFi(r.feature_importance);
    } catch (e) { info.innerHTML = `<p class="hint">${e.message}</p>`; }
  },

  async predict() {
    const name = document.getElementById('ml-stock').value;
    const td = Number(document.getElementById('ml-days').value);
    const box = document.getElementById('ml-predict-result');
    box.innerHTML = '<p class="hint">预测中...</p>';
    try {
      const r = await API.post('/api/ml/predict', { name, target_days: td });
      if (r.error) { box.innerHTML = `<p class="hint">${r.error}</p>`; return; }
      const trendColor = r.trend === '上涨' ? '#ef4444' : r.trend === '下跌' ? '#10b981' : '#f59e0b';
      box.innerHTML = `
        <div class="big-trend" style="color:${trendColor}">${r.trend}</div>
        <div class="prob-bar">
          <div class="prob-up" style="width:${r.up_prob}%">涨 ${r.up_prob}%</div>
          <div class="prob-down" style="width:${r.down_prob}%">跌 ${r.down_prob}%</div>
        </div>
        <p class="hint">预测周期: 未来${r.target_days || td}天 · 置信度 ${r.confidence}%（概率已经过保序回归校准）</p>`;
      if (r.train_report && r.train_report.cv_accuracy) this.renderCv(r.train_report);
      if (r.train_report && r.train_report.feature_importance) this.renderFi(r.train_report.feature_importance);
    } catch (e) { box.innerHTML = `<p class="hint">${e.message}</p>`; }
  },

  renderCv(r) {
    if (!r.cv_scores || !r.cv_scores.length) return;
    renderChart('ml-cv-chart', {
      animation: false,
      tooltip: {},
      legend: { data: ['各折准确率', '基线'], top: 0, textStyle: { fontSize: 11 } },
      grid: { left: 50, right: 20, top: 30, bottom: 30 },
      xAxis: { type: 'category', data: r.cv_scores.map((_, i) => `第${i + 1}折`) },
      yAxis: { min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
      series: [
        { name: '各折准确率', type: 'bar', data: r.cv_scores, itemStyle: { color: '#2563eb' },
          label: { show: true, position: 'top', formatter: '{c}%' }, barMaxWidth: 40 },
        { name: '基线', type: 'line', data: r.cv_scores.map(() => r.baseline_accuracy),
          lineStyle: { type: 'dashed', color: '#f59e0b' }, showSymbol: false }
      ]
    });
  },

  renderFi(fi) {
    if (!fi || !Object.keys(fi).length) return;
    const keys = Object.keys(fi).reverse();
    renderChart('ml-fi-chart', {
      animation: false,
      tooltip: { formatter: p => `${p.name}<br/>重要性: ${p.value}` },
      grid: { left: 120, right: 30, top: 10, bottom: 24 },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: keys.map(featureCN), axisLabel: { fontSize: 11 } },
      series: [{ type: 'bar', data: keys.map(k => fi[k]),
        itemStyle: { color: '#8b5cf6' }, barMaxWidth: 16 }]
    });
  }
};

/* ============ 资讯情绪 ============ */
const NewsPage = {
  lastSentiment: null,

  async init() {
    await refreshAllStockSelects();
    document.getElementById('btn-news-refresh').addEventListener('click', () => this.load(true));
    document.getElementById('news-stock').addEventListener('change', () => this.loadStockNews());
    await this.load();
    // 输入框已有合法股票时自动加载个股新闻
    const sel = document.getElementById('news-stock');
    if (sel.value.trim() && isValidStock(sel.value.trim())) await this.loadStockNews();
  },

  /* 切回本页时重绘温度表盘（防止在后台加载完成后图表渲染到隐藏容器） */
  async onShow() {
    if (this.lastSentiment) this.renderSentiment(this.lastSentiment);
  },

  async loadStockNews() {
    const name = document.getElementById('news-stock').value.trim();
    const box = document.getElementById('stock-news-list');
    if (!name) { box.innerHTML = '<p class="hint">请先选择股票</p>'; return; }
    if (!isValidStock(name)) { box.innerHTML = '<p class="hint">未找到该股票，请从下拉列表中选择</p>'; return; }
    box.innerHTML = '<p class="hint">加载中...</p>';
    try {
      const r = await API.get('/api/news/stock?name=' + encodeURIComponent(name));
      const news = r.news || [];
      if (!news.length || news[0].error) {
        box.innerHTML = `<p class="hint">${news[0] ? news[0].error : '暂无相关新闻'}</p>`;
        return;
      }
      box.innerHTML = news.map(n => `
        <div class="news-item">
          <div class="t">${n.time} · ${n.source}</div>
          <div class="title">${n.url
            ? `<a class="news-link" href="${n.url}" target="_blank" rel="noopener">${n.title} ↗</a>`
            : n.title}</div>
          ${n.content ? `<div class="c">${n.content}</div>` : ''}
        </div>`).join('');
    } catch (e) { box.innerHTML = `<p class="hint">${e.message}</p>`; }
  },

  async load(force = false) {
    // 情绪与快讯分开加载，互不影响
    const sBox = document.getElementById('sentiment-cards');
    const fBox = document.getElementById('news-feed');
    if (force) {
      sBox.innerHTML = '<p class="hint">情绪数据加载中（最长约15秒）...</p>';
      fBox.innerHTML = '<p class="hint">快讯加载中...</p>';
    }
    API.get('/api/news/sentiment?refresh=' + (force ? 1 : 0))
      .then(r => this.renderSentiment(r.sentiment))
      .catch(e => { sBox.innerHTML = `<p class="hint">${e.message}</p>`; });
    API.get('/api/news/feed?refresh=' + (force ? 1 : 0))
      .then(r => this.renderFeed(r.feed))
      .catch(e => { fBox.innerHTML = `<p class="hint">${e.message}</p>`; });
  },

  renderSentiment(s) {
    this.lastSentiment = s;
    const box = document.getElementById('sentiment-cards');
    const cards = s.cards || s;   // 兼容旧格式
    const entries = Object.entries(cards).filter(([k]) => !k.endsWith('_error'));
    if (!entries.length) {
      box.innerHTML = '<p class="hint">情绪数据获取失败（可能是网络问题或接口变动）</p>';
      return;
    }
    box.innerHTML = entries.map(([k, v]) =>
      `<div class="metric-card"><div class="v" style="font-size:15px">${v}</div><div class="k">${k}</div></div>`).join('');

    // 市场温度仪表盘
    if (s.temperature !== null && s.temperature !== undefined) {
      renderChart('sentiment-gauge', {
        series: [{
          type: 'gauge', min: 0, max: 100, splitNumber: 5,
          radius: '95%', center: ['50%', '60%'],
          axisLine: {
            lineStyle: { width: 14, color: [
              [0.25, '#60a5fa'], [0.4, '#93c5fd'], [0.55, '#fbbf24'],
              [0.75, '#fb923c'], [1, '#ef4444']
            ]}
          },
          pointer: { length: '55%', width: 4 },
          axisTick: { distance: -14, length: 5, lineStyle: { color: '#fff', width: 1 } },
          splitLine: { distance: -14, length: 14, lineStyle: { color: '#fff', width: 2 } },
          axisLabel: { distance: 18, fontSize: 10, color: '#64748b' },
          title: { fontSize: 12, color: '#64748b', offsetCenter: [0, '35%'] },
          detail: {
            fontSize: 26, fontWeight: 700, offsetCenter: [0, '75%'],
            formatter: v => `${v}°`,
            color: s.temperature >= 55 ? '#ef4444' : s.temperature >= 40 ? '#f59e0b' : '#3b82f6'
          },
          data: [{ value: s.temperature, name: s.temp_desc || '市场温度' }]
        }]
      });
    } else {
      document.getElementById('sentiment-gauge').innerHTML =
        '<p class="hint" style="text-align:center;padding-top:80px">温度数据不足</p>';
    }
  },

  renderFeed(feed) {
    const box = document.getElementById('news-feed');
    if (!feed.length || feed[0].error) {
      box.innerHTML = `<p class="hint">${feed[0] ? feed[0].error : '暂无快讯'}</p>`;
      return;
    }
    box.innerHTML = feed.map(n => `
      <div class="news-item">
        <div class="t">${n.time} · ${n.source}</div>
        <div class="title">${n.title}</div>
        <div class="c">${n.content || ''}</div>
      </div>`).join('');
  }
};

/* ============ AI助手 ============ */
const AiPage = {
  async init() {
    await refreshAllStockSelects();

    document.getElementById('btn-ai-send').addEventListener('click', () => this.send());
    document.getElementById('ai-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') this.send();
    });
    document.getElementById('btn-ai-clear').addEventListener('click', async () => {
      await API.post('/api/ai/clear');
      document.getElementById('ai-chat').innerHTML = '';
    });
    document.querySelectorAll('.quick-questions .chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.getElementById('ai-input').value = chip.dataset.q;
        this.send();
      });
    });

    // 恢复历史
    const r = await API.get('/api/ai/history');
    r.history.forEach(m => this.appendMsg(m.role === 'user' ? 'user' : 'ai', m.content));
  },

  appendMsg(role, text) {
    const chat = document.getElementById('ai-chat');
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  },

  async send() {
    const input = document.getElementById('ai-input');
    const q = input.value.trim();
    if (!q) return;
    input.value = '';
    this.appendMsg('user', q);
    const thinking = this.appendMsg('ai', '思考中...');
    try {
      const r = await API.post('/api/ai/ask', {
        question: q,
        stock_name: document.getElementById('ai-stock').value,
        include_news: document.getElementById('ai-include-news').checked
      });
      thinking.textContent = r.answer;
    } catch (e) { thinking.textContent = e.message; }
    document.getElementById('ai-chat').scrollTop = 1e9;
  }
};

/* ============ 术语词典 ============ */
const GlossaryPage = {
  terms: [],

  async init() {
    const data = await API.get('/api/glossary');
    this.terms = Array.isArray(data) ? data : (data.terms || []);
    this.renderList('');
    this.showOverview();
    document.getElementById('glossary-search').addEventListener('input', e => {
      this.renderList(e.target.value.trim());
    });
  },

  /* 未选中术语时：分类总览 */
  showOverview() {
    const box = document.getElementById('glossary-detail');
    const cats = {};
    this.terms.forEach(t => { (cats[t.category] = cats[t.category] || []).push(t); });
    box.innerHTML = `
      <div class="glossary-welcome">
        <h2>量化交易术语词典</h2>
        <p class="hint">共收录 ${this.terms.length} 个术语 · ${Object.keys(cats).length} 个分类，点击左侧术语或下方分类查看</p>
        <div class="cat-cards">
          ${Object.entries(cats).map(([cat, items]) => `
            <div class="cat-card" data-cat="${cat}">
              <div class="cat-name">${cat}</div>
              <div class="cat-count">${items.length} 个术语</div>
              <div class="cat-preview">${items.slice(0, 4).map(t => t.term).join(' · ')}</div>
            </div>`).join('')}
        </div>
      </div>`;
    box.querySelectorAll('.cat-card').forEach(card => {
      card.addEventListener('click', () => {
        const first = this.terms.find(t => t.category === card.dataset.cat);
        if (first) this.showDetail(first);
        document.getElementById('glossary-search').value = first ? first.term : '';
        this.renderList(first ? first.term : '');
      });
    });
  },

  renderList(kw) {
    const box = document.getElementById('glossary-list');
    const cats = {};
    this.terms
      .filter(t => !kw || t.term.toLowerCase().includes(kw.toLowerCase()) ||
                   (t.definition || '').includes(kw))
      .forEach(t => { (cats[t.category] = cats[t.category] || []).push(t); });

    box.innerHTML = '';
    for (const [cat, items] of Object.entries(cats)) {
      const title = document.createElement('div');
      title.className = 'cat-title';
      title.textContent = cat;
      box.appendChild(title);
      items.forEach(t => {
        const el = document.createElement('div');
        el.className = 'term-item';
        el.textContent = t.term;
        el.addEventListener('click', () => {
          box.querySelectorAll('.term-item').forEach(x => x.classList.remove('active'));
          el.classList.add('active');
          this.showDetail(t);
        });
        box.appendChild(el);
      });
    }
    if (!box.children.length) box.innerHTML = '<p class="hint">没有匹配的术语</p>';
  },

  showDetail(t) {
    const box = document.getElementById('glossary-detail');
    box.innerHTML = `
      <div class="term-header">
        <h2>${t.term}</h2>
        <span class="term-cat-badge">${t.category}</span>
      </div>
      <div class="term-section">
        <div class="term-label">简明定义</div>
        <div class="def">${t.definition || ''}</div>
      </div>
      ${t.detail ? `<div class="term-section">
        <div class="term-label">详细解释</div>
        <div class="detail">${t.detail}</div>
      </div>` : ''}
      ${t.related && t.related.length ? `
        <div class="term-section">
          <div class="term-label">相关术语</div>
          <div class="related-terms">
            ${t.related.map(r => `<button class="chip" data-term="${r}">${r}</button>`).join('')}
          </div>
        </div>` : ''}`;
    box.querySelectorAll('.related-terms .chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const target = this.terms.find(x => x.term === chip.dataset.term);
        if (target) {
          this.showDetail(target);
          document.getElementById('glossary-search').value = '';
          this.renderList('');
        } else toast('该术语暂未收录');
      });
    });
  }
};

/* ============ 数据管理 ============ */
const DataPage = {
  async init() {
    document.getElementById('btn-fetch').addEventListener('click', () => this.fetchData());
    document.getElementById('btn-update-local').addEventListener('click', () => this.fetchData(true));
    const settings = await API.get('/api/settings');
    document.getElementById('fetch-source').value = settings.data_source || 'akshare';
    this.timer = setInterval(() => {
      if (document.getElementById('page-data').classList.contains('active')) this.refresh();
    }, 30000);
    await this.refresh();
  },

  async refresh() {
    try {
      META = null; // 强制下次重新拉元信息
      const meta = await API.get('/api/meta');
      const s = meta.data_status;
      const u = meta.update_status || {};
      const rstate = u.last_result || {};
      const settings = await API.get('/api/settings');
      document.getElementById('update-status').textContent = [
        `主数据源：${settings.data_source || 'akshare'}；最近尝试源：${u.source || '--'}`,
        `最近尝试：${u.last_attempt || '--'}`,
        `最近成功：${u.last_success || '--'}`,
        `数据截止：${s.latest_date || '--'}（逐股截止见下表）`,
        rstate.msg || '',
        ...(rstate.warnings || []),
        ...((rstate.items || []).filter(x => x.status === 'failed' || x.status === 'no_new_data')
          .map(x => `${x.name}: ${x.interface || ''} ${x.category || ''} ${x.reason || '尚未覆盖目标交易日'}`)),
        u.scheduler && u.scheduler.error ? `自动调度：${u.scheduler.error}` : ''
      ].join('\n');
      document.getElementById('data-status').innerHTML = `
        <div class="metric-card"><div class="v">${s.stock_count}</div><div class="k">本地股票数</div></div>
        <div class="metric-card"><div class="v" style="font-size:14px">${s.earliest_date || '--'}</div><div class="k">最早数据</div></div>
        <div class="metric-card"><div class="v" style="font-size:14px">${s.latest_date || '--'}</div><div class="k">最新数据</div></div>`;
      const r = await API.get('/api/stocks');
      _stockListCache = null;
      document.getElementById('stock-list-table').innerHTML = `
        <table><thead><tr><th>股票</th><th>数据条数</th><th>起始</th><th>截止</th></tr></thead>
        <tbody>${r.stocks.map(x => `<tr><td>${x.name}</td><td>${x.rows}</td><td>${x.start}</td><td>${x.end}</td></tr>`).join('')}</tbody></table>`;
    } catch (e) { toast(e.message); }
  },

  async fetchData(localOnly = false) {
    const btn = document.getElementById('btn-fetch');
    const wrap = document.getElementById('fetch-progress');
    btn.disabled = true;
    document.getElementById('btn-update-local').disabled = true;
    wrap.classList.remove('hidden');
    try {
      const { task_id } = await API.post(localOnly ? '/api/data/update' : '/api/data/fetch', {
        source: document.getElementById('fetch-source').value,
        limit: Number(document.getElementById('fetch-limit').value),
        start: document.getElementById('fetch-start').value.trim()
      });
      const r = await pollTask(task_id, (cur, total, msg) => {
        const pct = total > 0 ? Math.round(cur / total * 100) : 0;
        document.getElementById('fetch-fill').style.width = pct + '%';
        document.getElementById('fetch-text').textContent = `${msg} (${cur}/${total})`;
      });
      document.getElementById('fetch-text').textContent = r.msg;
      document.getElementById('fetch-text').style.color = r.ok ? '#10b981' : '#dc2626';
      toast(r.msg || '拉取完成', 4000);
      await this.refresh();
      await refreshAllStockSelects();
    } catch (e) { toast(e.message); }
    btn.disabled = false;
    document.getElementById('btn-update-local').disabled = false;
  }
};

/* ============ 设置 ============ */
const SettingsPage = {
  providers: {},

  async init() {
    document.getElementById('btn-test-data').addEventListener('click', async () => {
      const box = document.getElementById('data-test-result');
      const button = document.getElementById('btn-test-data');
      button.disabled = true;
      box.textContent = '诊断中…（使用已保存的 Token）';
      try {
        const { task_id } = await API.post('/api/data/diagnose', {
          source: document.getElementById('set-data-source').value
        });
        const result = await pollTask(task_id);
        box.textContent = `${result.source}\n` + result.checks.map(c =>
          `${c.interface}: ${c.ok ? '可用' : c.category} — ${c.reason}`).join('\n');
      } catch (e) { box.textContent = e.message; }
      finally { button.disabled = false; }
    });
    document.getElementById('btn-save-settings').addEventListener('click', () => this.save());
    document.getElementById('btn-test-ai').addEventListener('click', () => this.testAi());
    document.getElementById('set-provider').addEventListener('change', () => {
      // 切换服务商时清空模型框，避免带着上一家服务商的模型名（1211错误的根源）
      document.getElementById('set-model').value = '';
      this.updateProviderHint();
      toast('已切换服务商，模型将使用新服务商的默认模型');
    });
    await this.refresh();
  },

  async testAi() {
    const box = document.getElementById('ai-test-result');
    box.textContent = '测试中（约几秒）...';
    try {
      // 用输入框中当前（可能未保存的）配置测试；Key为空则用已保存的
      const body = {
        provider: document.getElementById('set-provider').value,
        model: document.getElementById('set-model').value.trim()
      };
      const key = document.getElementById('set-key').value.trim();
      if (key) body.key = key;
      const r = await API.post('/api/settings/test', body);
      box.textContent = r.msg;
      box.style.color = r.ok ? '#10b981' : '#ef4444';
    } catch (e) {
      box.textContent = e.message;
      box.style.color = '#ef4444';
    }
  },

  /* 每次进入页面都重新拉取设置 */
  async onShow() { await this.refresh(); },

  async refresh() {
    try {
      const s = await API.get('/api/settings');
      this.providers = s.ai_providers || {};
      document.getElementById('set-data-source').value = s.data_source || 'akshare';
      const sel = document.getElementById('set-provider');
      if (!sel.options.length) {
        sel.innerHTML = Object.entries(this.providers)
          .map(([k, v]) => `<option value="${k}">${v.name}</option>`).join('');
      }
      sel.value = s.ai_provider || 'siliconflow';
      this.updateProviderHint();
      document.getElementById('set-token').placeholder =
        s._has_token ? `已保存 (${s.tushare_token})，留空保持不变` : '未设置';
      document.getElementById('set-key').placeholder =
        s._has_key ? `已保存 (${s.sf_api_key})，留空保持不变` : '未设置';
      document.getElementById('set-model').value = s.ai_model || '';
      document.getElementById('set-cash').value = s.initial_cash;
      document.getElementById('set-auto-update').checked = !!s.auto_update;
    } catch (e) { /* 忽略 */ }
  },

  updateProviderHint() {
    const key = document.getElementById('set-provider').value;
    const p = this.providers[key];
    if (!p) return;
    document.getElementById('provider-note').textContent = p.note || '';
    document.getElementById('key-hint').textContent = `${p.site} 获取`;
    document.getElementById('set-model').placeholder = `默认：${p.default_model}`;
  },

  async save() {
    const token = document.getElementById('set-token').value.trim();
    const key = document.getElementById('set-key').value.trim();
    const body = {
      ai_provider: document.getElementById('set-provider').value,
      ai_model: document.getElementById('set-model').value.trim(),
      initial_cash: Number(document.getElementById('set-cash').value),
      data_source: document.getElementById('set-data-source').value,
      auto_update: document.getElementById('set-auto-update').checked
    };
    if (token) body.tushare_token = token;
    if (key) body.sf_api_key = key;
    await API.post('/api/settings', body);
    document.getElementById('settings-status').textContent = '已保存 ✓';
    document.getElementById('set-token').value = '';
    document.getElementById('set-key').value = '';
    toast('设置已保存');
    await this.refresh();  // 立即刷新显示，确认保存生效
  }
};

/* 工具库：API请求、任务轮询、提示、公共控件 */
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || `请求失败 ${r.status}`);
    }
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || `请求失败 ${r.status}`);
    }
    return r.json();
  }
};

function toast(msg, ms = 2500) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), ms);
}

/* 轮询异步任务，onProgress(cur,total,msg)，完成后 resolve result */
function pollTask(taskId, onProgress) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const t = await API.get(`/api/tasks/${taskId}`);
        if (t.status === 'done') return resolve(t.result);
        if (t.status === 'error') return reject(new Error(t.error || '任务失败'));
        if (t.status === 'not_found') return reject(new Error('任务不存在'));
        onProgress && onProgress(t.current, t.total, t.message);
        setTimeout(tick, 500);
      } catch (e) { reject(e); }
    };
    tick();
  });
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v)) return '--';
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v)) return '--';
  return Number(v).toFixed(digits) + '%';
}

function pctClass(v) { return v >= 0 ? 'up' : 'down'; }

/* 股票列表缓存 + 全局共享 datalist（所有股票输入框可搜索） */
let _stockListCache = null;
async function loadStockList(force = false) {
  if (!_stockListCache || force) {
    const r = await API.get('/api/stocks');
    _stockListCache = r.stocks.map(s => s.name);
  }
  return _stockListCache;
}

function isValidStock(name) {
  return _stockListCache && _stockListCache.includes(name);
}

/* 填充共享 datalist，所有带 list="stock-datalist" 的输入框自动获得搜索能力 */
async function refreshAllStockSelects() {
  let stocks;
  try {
    stocks = await loadStockList(true);
  } catch (e) { return; }
  const dl = document.getElementById('stock-datalist');
  if (dl) {
    dl.innerHTML = stocks.map(s => `<option value="${s}">`).join('');
  }
}

/* 根据策略参数元信息生成滑块组，返回读取函数 */
function buildParamSliders(container, paramsMeta, values = {}) {
  container.innerHTML = '';
  const getters = {};
  for (const [key, meta] of Object.entries(paramsMeta || {})) {
    const val = values[key] !== undefined ? values[key] : meta.default;
    const item = document.createElement('div');
    item.className = 'param-item';
    item.innerHTML = `<span>${meta.label || key}</span>
      <input type="range" min="${meta.min}" max="${meta.max}" step="${meta.step}" value="${val}">
      <span class="val">${val}</span>`;
    const range = item.querySelector('input');
    const valSpan = item.querySelector('.val');
    range.addEventListener('input', () => { valSpan.textContent = range.value; });
    getters[key] = () => Number(range.value);
    container.appendChild(item);
  }
  return () => {
    const out = {};
    for (const [k, g] of Object.entries(getters)) out[k] = g();
    return out;
  };
}

/* 全局元信息缓存 */
let META = null;
async function loadMeta() {
  if (!META) META = await API.get('/api/meta');
  return META;
}

/* 全局图表实例登记表：用于页面切换/窗口变化时统一 resize */
const _chartRegistry = new Set();
function registerChart(c) { if (c) _chartRegistry.add(c); return c; }
function resizeAllCharts() {
  _chartRegistry.forEach(c => {
    try {
      if (c.isDisposed && c.isDisposed()) { _chartRegistry.delete(c); return; }
      const d = c.getDom && c.getDom();
      if (d && d.isConnected && d.clientWidth > 0 && d.clientHeight > 0) c.resize();
    } catch (e) { /* 忽略单个图表异常 */ }
  });
}

/* 稳健的图表渲染：销毁旧实例 + 容器不可见时延迟重试渲染 + 自动resize
   关键修复：页面隐藏(display:none)时 echarts 只能拿到 0 尺寸，
   画出来就是空白。这里在容器尺寸为 0 时持续重试，直到页面重新可见。 */
const _renderTokens = new WeakMap();
function renderChart(domId, option) {
  const dom = document.getElementById(domId);
  if (!dom) return null;
  const old = echarts.getInstanceByDom(dom);
  if (old) old.dispose();
  dom.innerHTML = '';
  const chart = echarts.init(dom);
  registerChart(chart);
  const token = {};
  _renderTokens.set(dom, token);
  const tryRender = (attempt) => {
    if (_renderTokens.get(dom) !== token) return;  // 已被更新的渲染取代
    if (!dom.isConnected) return;
    if (dom.clientWidth === 0 || dom.clientHeight === 0) {
      // 容器不可见（页面未激活）：每 300ms 重试，最长约 10 分钟
      if (attempt < 2000) setTimeout(() => tryRender(attempt + 1), 300);
      return;
    }
    try {
      chart.setOption(option);
      chart.resize();
    } catch (e) {
      console.error('图表渲染失败', domId, e);
    }
  };
  tryRender(0);
  return chart;
}

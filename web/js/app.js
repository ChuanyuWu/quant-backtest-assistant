/* 应用入口：页面路由与懒加载初始化 */
const PAGES = {
  market: Market,
  index: IndexPage,
  lab: Lab,
  box: BoxPage,
  ml: MlPage,
  news: NewsPage,
  ai: AiPage,
  glossary: GlossaryPage,
  data: DataPage,
  settings: SettingsPage
};

const _inited = new Set();

async function switchPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.page === name));

  if (!_inited.has(name)) {
    _inited.add(name);
    try {
      await PAGES[name].init();
    } catch (e) {
      console.error(name, e);
      toast('页面加载出错: ' + e.message);
    }
  }
  // 每次切换都刷新股票下拉（防止拉取数据后列表过期）
  refreshAllStockSelects();
  // 页面级 onShow 钩子
  if (PAGES[name].onShow) {
    try { await PAGES[name].onShow(); } catch (e) { console.error(e); }
  }
  // 每次切换页面后，统一让当前可见的图表自适应尺寸
  setTimeout(() => { try { resizeAllCharts(); } catch (e) {} }, 60);
}

document.addEventListener('DOMContentLoaded', async () => {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => switchPage(btn.dataset.page));
  });
  await switchPage('market');
});

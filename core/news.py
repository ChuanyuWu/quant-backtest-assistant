"""财经资讯与市场情绪（akshare，免费无需Token）

设计原则：
- 每个接口15秒超时，挂起不会拖垮整个页面
- 快讯/情绪分开获取，一个失败不影响另一个
- akshare接口偶有变动，全部容错处理
"""
from typing import Dict, List
import time
import concurrent.futures

_cache: Dict[str, tuple] = {}
CACHE_TTL = 300  # 5分钟缓存


def _with_timeout(func, timeout=15, default=None):
    ex = concurrent.futures.ThreadPoolExecutor(1)
    try:
        fut = ex.submit(func)
        return fut.result(timeout)
    except Exception:
        return default
    finally:
        ex.shutdown(wait=False)


def _cached(key: str, fetcher, force: bool = False):
    now = time.time()
    if not force and key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    result = fetcher()
    _cache[key] = (now, result)
    return result


def get_news_feed(limit: int = 30, force: bool = False) -> List[Dict]:
    """财经快讯流：财联社→东财→新浪 三级备用源，每源重试2次"""
    def fetch():
        import akshare as ak
        sources = [
            ("财联社", lambda: ak.stock_info_global_cls(),
             {"time": "发布时间", "title": "标题", "content": "内容"}),
            ("东方财富", lambda: ak.stock_info_global_em(),
             {"time": "时间", "title": "摘要", "content": "摘要"}),
            ("新浪财经", lambda: ak.stock_info_global_sina(),
             {"time": "时间", "title": "内容", "content": "内容"}),
        ]
        for src_name, call, cols in sources:
            for _ in range(2):
                def do():
                    df = call()
                    items = []
                    for _, r in df.head(limit).iterrows():
                        title = str(r.get(cols["title"], ""))
                        content = str(r.get(cols["content"], ""))
                        items.append({
                            "time": str(r.get(cols["time"], "")),
                            "title": title if title != content or cols["title"] == cols["content"] else title,
                            "content": content[:300] if content != title else "",
                            "source": src_name,
                        })
                    return items
                items = _with_timeout(do, timeout=12)
                if items:
                    return items
                time.sleep(1)
        return [{"error": "三个快讯源均获取失败（网络或源站问题），稍后再点刷新"}]
    return _cached("news_feed", fetch, force)


def get_stock_news(stock_code: str, limit: int = 10) -> List[Dict]:
    """个股新闻，stock_code 如 '600519'"""
    def fetch():
        import akshare as ak
        def do():
            df = ak.stock_news_em(symbol=stock_code)
            items = []
            for _, r in df.head(limit).iterrows():
                items.append({
                    "time": str(r.get("发布时间", "")),
                    "title": str(r.get("新闻标题", "")),
                    "content": str(r.get("新闻内容", ""))[:300],
                    "source": str(r.get("文章来源", "东方财富")),
                    "url": str(r.get("新闻链接", "")),
                })
            return items
        items = _with_timeout(do, timeout=15)
        if items:
            return items
        return [{"error": "个股新闻获取失败或超时"}]
    return _cached(f"stock_news_{stock_code}", fetch)


def get_market_sentiment(force: bool = False) -> Dict:
    """市场情绪多维版：涨跌停/炸板率/连板高度/昨涨停表现/涨跌家数/成交额 + 综合市场温度

    每个来源独立超时重试，部分失败时降级显示。返回:
    {cards: {...}, temperature: 0-100, temp_desc: str, components: {...}}
    """
    def fetch():
        import akshare as ak
        import datetime
        today = datetime.datetime.now().strftime("%Y%m%d")
        cards, comp = {}, {}

        # 涨停池（含连板数）
        zt_df = _with_timeout(lambda: ak.stock_zt_pool_em(date=today), timeout=15)
        if zt_df is not None and not zt_df.empty:
            cards["涨停家数"] = len(zt_df)
            comp["zt"] = min(len(zt_df) / 60, 1.5)  # 60家以上满分
            if "连板数" in zt_df.columns:
                cards["最高连板"] = f"{int(zt_df['连板数'].max())}板"
                comp["lb"] = min(zt_df["连板数"].max() / 5, 1.5)
        else:
            cards["涨停家数"] = "-"

        # 跌停池
        dt_df = _with_timeout(lambda: ak.stock_zt_pool_dtgc_em(date=today), timeout=15)
        cards["跌停家数"] = len(dt_df) if dt_df is not None else "-"
        if dt_df is not None:
            comp["dt"] = max(1 - len(dt_df) / 30, 0)

        # 炸板池 → 炸板率
        zb_df = _with_timeout(lambda: ak.stock_zt_pool_zbgc_em(date=today), timeout=15)
        if zb_df is not None and zt_df is not None:
            zt_n, zb_n = len(zt_df), len(zb_df)
            rate = zb_n / (zt_n + zb_n) * 100 if (zt_n + zb_n) else 0
            cards["炸板率"] = f"{rate:.0f}%"
            comp["zb"] = max(1 - rate / 50, 0)

        # 昨日涨停今日表现（赚钱效应）
        prev_df = _with_timeout(lambda: ak.stock_zt_pool_previous_em(date=today), timeout=15)
        if prev_df is not None and not prev_df.empty and "涨跌幅" in prev_df.columns:
            avg = prev_df["涨跌幅"].mean()
            cards["昨涨停今均涨"] = f"{avg:+.1f}%"
            comp["prev"] = max(0, min((avg + 3) / 6, 1.5))

        # 全市场快照：涨跌家数 + 成交额（一个接口搞定，替代不稳定的乐咕）
        def via_spot():
            spot = ak.stock_zh_a_spot_em()
            up = int((spot["涨跌幅"] > 0).sum())
            down = int((spot["涨跌幅"] < 0).sum())
            amt = spot["成交额"].sum() / 1e8
            return up, down, amt
        spot_r = _with_timeout(via_spot, timeout=20)
        if spot_r:
            up, down, amt = spot_r
            cards["上涨/下跌"] = f"{up}/{down}"
            cards["两市成交额"] = f"{amt:.0f}亿"
            comp["ud"] = max(0, min(up / max(up + down, 1) * 2, 1.5))

        # 乐咕活跃度（补充，失败就算了）
        def fetch_activity():
            df = ak.stock_market_activity_legu()
            return {str(r.get("item", "")): str(r.get("value", ""))
                    for _, r in df.iterrows()}
        activity = _with_timeout(fetch_activity, timeout=10, default={}) or {}
        for k in ("活跃度", "赚钱效应"):
            if k in activity:
                cards[k] = activity[k]

        # 卡片展示顺序：涨停跌停相邻，其余按重要性
        order = ["涨停家数", "跌停家数", "最高连板", "炸板率", "昨涨停今均涨",
                 "上涨/下跌", "两市成交额", "活跃度", "赚钱效应"]
        cards = {k: cards[k] for k in order if k in cards}

        # 综合市场温度：各维度加权（权重按可用维度归一）
        weights = {"zt": 0.22, "dt": 0.13, "zb": 0.15, "lb": 0.15,
                   "prev": 0.20, "ud": 0.15}
        avail = [(comp[k], w) for k, w in weights.items() if k in comp]
        if avail:
            wsum = sum(w for _, w in avail)
            score = sum(min(v, 1.5) * w for v, w in avail) / wsum
            temperature = round(min(score / 1.5, 1.0) * 100)
        else:
            temperature = None

        if temperature is None:
            desc = "数据不足"
        elif temperature >= 75:
            desc = "火热🔥（注意追高风险）"
        elif temperature >= 55:
            desc = "偏暖"
        elif temperature >= 40:
            desc = "中性"
        elif temperature >= 25:
            desc = "偏冷"
        else:
            desc = "冰点🧊（关注情绪反转）"

        if not cards:
            cards["提示"] = "情绪数据获取失败，请点刷新重试"
        return {"cards": cards, "temperature": temperature,
                "temp_desc": desc, "components": comp}
    return _cached("sentiment_v2", fetch, force)

"""行情数据在线获取：Tushare / akshare 双数据源，统一前复权(qfq)

v8 改动（修复数据一致性）：
1. 两个数据源统一为前复权价格，不再混存"不复权 + 前复权"两种口径
2. 前复权因子随除权除息变化，增量拼接会在除权日产生价格断点，
   因此更新时改为【全量重拉并整文件覆盖】，保证任一文件内复权因子一致
3. 写入前按 6 位代码去重：同一代码的异名旧文件（如 万科Ａ/万科A）会被清理

Tushare 限频规则：pro_bar 约 50次/分钟，每次调用间隔1.3秒，超限自动退避重试。
"""
import os
import re
import sys
import time
import datetime as dt
import concurrent.futures
from typing import Callable, Optional, Dict, List

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.app_config import AppConfig
from core.data_io import sanitize_filename
from core.schema import standardize
from core.settings import get_settings

ProgressCb = Optional[Callable[[int, int, str], None]]

TUSHARE_INTERVAL = 1.3   # 每次调用间隔，保证 <50次/分钟
AKSHARE_INTERVAL = 0.35


def _retry(func, times=3, delay=2.0, backoff_on_limit=True):
    """通用重试：识别限频类错误并加大退避

    Tushare 低积分账号部分接口（如 trade_cal）限 1次/分钟，
    15秒的退避不够，识别到"频率超限"时按分钟级窗口等待。
    """
    last = None
    for attempt in range(times):
        try:
            return func()
        except Exception as e:
            last = e
            msg = str(e)
            if backoff_on_limit and "频率超限" in msg:
                # "频率超限(N次/分钟)" → 等到限频窗口翻篇
                time.sleep(65 if "1次/分钟" in msg else 20 * (attempt + 1))
            elif backoff_on_limit and ("频" in msg or "限" in msg or "timeout" in msg.lower()):
                time.sleep(15 * (attempt + 1))
            else:
                time.sleep(delay)
    raise last


# 交易日历缓存：trade_cal 是低频接口（低积分账号 1次/分钟），
# 同一次运行内不重复请求相同区间
_CAL_CACHE: Dict[tuple, List[str]] = {}


def _trade_days(pro, start: str, end: str) -> List[str]:
    key = (start, end)
    if key not in _CAL_CACHE:
        cal = _retry(lambda: pro.trade_cal(exchange="SSE", start_date=start,
                                           end_date=end, is_open="1"))
        _CAL_CACHE[key] = sorted(cal["cal_date"].tolist())
    return _CAL_CACHE[key]


def _with_timeout(func, timeout=30):
    """带超时的调用（防止接口无限挂起）"""
    ex = concurrent.futures.ThreadPoolExecutor(1)
    try:
        fut = ex.submit(func)
        return fut.result(timeout)
    finally:
        ex.shutdown(wait=False)


def _code_of(name: str) -> str:
    """从 '名称(000002)' 提取6位代码，提取不到返回空串"""
    m = re.search(r"\((\d{6})\)", name)
    return m.group(1) if m else ""


def _remove_code_conflicts(name: str):
    """删除同一代码但文件名不同的旧文件（全角Ａ/半角A 等历史遗留）"""
    code = _code_of(name)
    if not code:
        return
    folder = AppConfig.STOCK_DATA_DIR
    target = sanitize_filename(name) + ".csv"
    for fname in os.listdir(folder):
        if fname.endswith(".csv") and fname != target and f"({code})" in fname:
            try:
                os.remove(os.path.join(folder, fname))
                print(f"清理同代码旧文件: {fname}")
            except OSError:
                pass


def _save_full(name: str, df: pd.DataFrame):
    """整文件覆盖写入（前复权数据必须整体一致，不做增量合并）"""
    _remove_code_conflicts(name)
    df = df.drop_duplicates(subset="交易日期", keep="last") \
           .sort_values("交易日期").reset_index(drop=True)
    path = os.path.join(AppConfig.STOCK_DATA_DIR, sanitize_filename(name) + ".csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _local_stocks() -> Dict[str, pd.DataFrame]:
    """{股票名: 本地DataFrame}"""
    out = {}
    folder = AppConfig.STOCK_DATA_DIR
    os.makedirs(folder, exist_ok=True)
    for fname in os.listdir(folder):
        if not fname.endswith(".csv"):
            continue
        try:
            df = standardize(pd.read_csv(os.path.join(folder, fname), encoding="utf-8-sig"))
            if not df.empty:
                out[os.path.splitext(fname)[0]] = df
        except Exception:
            continue
    return out


# ---------------- Tushare ----------------

def _tushare_stock_list(pro, limit: int) -> List[Dict]:
    """选取股票池：主板非ST，按最新总市值降序取前limit只
    （用daily_basic一次调用拿到全市场市值，不占用daily额度）"""
    stocks = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,name,market,list_date")
    stocks = stocks[~stocks["name"].str.contains("ST", na=False)]
    stocks = stocks[stocks["market"] == "主板"]

    ordered = None
    try:
        # 找最近一个交易日（日历有缓存，不重复占用 trade_cal 低频额度）
        today = dt.datetime.now().strftime("%Y%m%d")
        week_ago = (dt.datetime.now() - dt.timedelta(days=10)).strftime("%Y%m%d")
        last_day = _trade_days(pro, week_ago, today)[-1]
        # daily_basic 需要 2000 积分，低积分账号会抛权限异常
        mv = pro.daily_basic(trade_date=last_day, fields="ts_code,total_mv")
        mv = mv.dropna(subset=["total_mv"]).sort_values("total_mv", ascending=False)
        ordered = {c: i for i, c in enumerate(mv["ts_code"].tolist())}
        stocks["_order"] = stocks["ts_code"].map(ordered)
    except Exception as e:
        print(f"daily_basic按市值排序失败（积分不足时退回akshare市值）: {e}")

    if "_order" not in stocks.columns:
        try:
            import akshare as ak
            spot = ak.stock_zh_a_spot_em()
            spot = spot.dropna(subset=["总市值"]).sort_values("总市值", ascending=False)
            code_order = {c: i for i, c in enumerate(spot["代码"].tolist())}
            stocks["_order"] = stocks["ts_code"].map(
                lambda c: code_order.get(c.split(".")[0], 10 ** 9))
        except Exception as e:
            print(f"akshare市值排序也失败（退回代码顺序）: {e}")

    if "_order" in stocks.columns:
        stocks = stocks.sort_values("_order").drop(columns=["_order"])

    return stocks.head(limit).to_dict("records")


def _tushare_fetch_one(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """pro_bar 前复权日线（需先 ts.set_token）。前复权保证与 akshare 口径一致"""
    import tushare as ts
    df = _retry(lambda: ts.pro_bar(ts_code=ts_code, adj="qfq",
                                   start_date=start, end_date=end))
    if df is None or df.empty:
        return pd.DataFrame()
    return standardize(df.sort_values("trade_date"))


def _fetch_tushare(limit: int, start: str, end: str,
                   progress: ProgressCb = None) -> Dict:
    import tushare as ts
    token = get_settings().get("tushare_token", "").strip()
    if not token:
        return {"ok": False, "msg": "请先在「设置」页填写 Tushare Token（tushare.pro 免费注册）"}

    ts.set_token(token)
    pro = ts.pro_api()

    if progress:
        progress(0, 1, "获取股票列表中...")
    try:
        stocks = _retry(lambda: _tushare_stock_list(pro, limit))
    except Exception as e:
        return {"ok": False, "msg": f"获取股票列表失败：{e}（可能是Token无效或积分不足）"}

    local = _local_stocks()
    name_of = {}   # ts_code -> 标准股票名
    for row in stocks:
        code6 = row["ts_code"].split(".")[0]
        matched = None
        for n in local:
            if f"({code6})" in n:
                matched = n
                break
        name_of[row["ts_code"]] = matched or f"{row['name']}({code6})"

    # 前复权数据在除权日后历史价格会整体变化，增量拼接会产生价格断点，
    # 因此无论新旧股票都全量重拉并整文件覆盖
    saved, failed = 0, 0
    total = len(name_of)
    for i, (ts_code, name) in enumerate(name_of.items()):
        try:
            df = _tushare_fetch_one(ts_code, start, end)
            if not df.empty:
                _save_full(name, df)
                saved += 1
        except Exception as e:
            failed += 1
            print(f"Tushare拉取 {name} 失败: {e}")
        if progress:
            progress(i + 1, total, f"Tushare: {name}")
        time.sleep(TUSHARE_INTERVAL)

    return {"ok": True,
            "msg": f"Tushare完成：更新{saved}只（前复权全量覆盖），失败{failed}只",
            "saved": saved, "failed": failed}


# ---------------- akshare ----------------

def _akshare_stock_list(limit: int) -> List[Dict]:
    import akshare as ak

    def via_spot():
        spot = ak.stock_zh_a_spot_em()
        spot = spot[~spot["名称"].str.contains("ST", na=False)]
        spot = spot[spot["代码"].str.startswith(("60", "00"))]
        spot = spot.sort_values("总市值", ascending=False)
        return [{"name": r["名称"], "code": r["代码"]}
                for _, r in spot.head(limit).iterrows()]

    def via_code_name():
        df = ak.stock_info_a_code_name()
        df = df[~df["name"].str.contains("ST", na=False)]
        df = df[df["code"].str.startswith(("60", "00"))]
        return [{"name": r["name"], "code": r["code"]}
                for _, r in df.head(limit).iterrows()]

    try:
        return _with_timeout(lambda: _retry(via_spot, times=2), timeout=40)
    except Exception:
        # 快照接口不可用时退回轻量列表接口
        return _with_timeout(lambda: _retry(via_code_name, times=2), timeout=40)


def _akshare_fetch_one(code: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak
    df = _retry(lambda: ak.stock_zh_a_hist(
        symbol=code, period="daily",
        start_date=start, end_date=end, adjust="qfq"))
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                            "最低": "low", "收盘": "close",
                            "成交量": "vol", "成交额": "amount"})
    return standardize(df)


def _fetch_akshare(limit: int, start: str, end: str,
                   progress: ProgressCb = None) -> Dict:
    if progress:
        progress(0, 1, "获取股票列表中（最长约40秒）...")
    try:
        stocks = _akshare_stock_list(limit)
    except Exception as e:
        return {"ok": False, "msg": f"获取股票列表失败：{e}（akshare源站偶发不稳定，可稍后重试或改用Tushare）"}

    saved, failed = 0, 0
    total = len(stocks)
    for i, row in enumerate(stocks):
        name = f"{row['name']}({row['code']})"
        try:
            # 前复权数据全量重拉覆盖（增量拼接会在除权日产生价格断点）
            new_df = _akshare_fetch_one(row["code"], start, end)
            if not new_df.empty:
                _save_full(name, new_df)
                saved += 1
        except Exception as e:
            failed += 1
            print(f"akshare拉取 {name} 失败: {e}")
        if progress:
            progress(i + 1, total, f"akshare: {name}")
        time.sleep(AKSHARE_INTERVAL)

    return {"ok": True,
            "msg": f"akshare完成：更新{saved}只（前复权全量覆盖），失败{failed}只",
            "saved": saved, "failed": failed}


# ---------------- 公共 ----------------

def fetch_stock_data(source: str = "akshare", limit: int = 200,
                     start: str = "20240101", end: str = None,
                     progress: ProgressCb = None) -> Dict:
    """在线拉取数据统一入口（已存在的股票自动增量更新）"""
    end = end or dt.datetime.now().strftime("%Y%m%d")
    os.makedirs(AppConfig.STOCK_DATA_DIR, exist_ok=True)
    if source == "tushare":
        return _fetch_tushare(limit, start, end, progress)
    return _fetch_akshare(limit, start, end, progress)


def update_local_stocks(source: str = "akshare", start: str = "20240101",
                        end: str = None, progress: ProgressCb = None) -> Dict:
    """只更新本地已有股票（每日自动更新专用）

    与 fetch_stock_data 的区别：不引入新股票池，本地有什么就更新什么，
    避免股票跌出市值 top-N 后停止更新、或池子轮换不断新增股票。
    """
    end = end or dt.datetime.now().strftime("%Y%m%d")
    local = _local_stocks()
    if not local:
        return {"ok": False, "msg": "本地没有股票数据，请先在「数据管理」页拉取"}

    if source == "tushare":
        import tushare as ts
        token = get_settings().get("tushare_token", "").strip()
        if not token:
            return {"ok": False, "msg": "未配置 Tushare Token，无法更新"}
        ts.set_token(token)

    interval = TUSHARE_INTERVAL if source == "tushare" else AKSHARE_INTERVAL
    updated, failed = 0, 0
    total = len(local)
    for i, name in enumerate(sorted(local)):
        code = _code_of(name)
        last_date = local[name]["交易日期"].max().strftime("%Y-%m-%d")
        if not code:
            failed += 1
            continue
        # 本地已覆盖到 end 则跳过
        if local[name]["交易日期"].max() >= pd.to_datetime(end):
            if progress:
                progress(i + 1, total, f"已是最新: {name}")
            continue
        try:
            if source == "tushare":
                suffix = ".SH" if code.startswith("6") else ".SZ"
                df = _tushare_fetch_one(code + suffix, start, end)
            else:
                df = _akshare_fetch_one(code, start, end)
            if not df.empty:
                _save_full(name, df)
                updated += 1
        except Exception as e:
            failed += 1
            print(f"更新 {name} 失败: {e}")
        if progress:
            progress(i + 1, total, f"更新: {name} (原截止{last_date})")
        time.sleep(interval)

    return {"ok": True,
            "msg": f"本地更新完成：更新{updated}只，失败{failed}只，共{total}只",
            "saved": updated, "failed": failed}

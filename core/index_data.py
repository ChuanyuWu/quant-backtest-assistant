"""大盘指数数据：Tushare优先，akshare备用，本地CSV缓存"""
import os
import sys
import datetime as dt
from typing import Dict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.app_config import AppConfig
from core.schema import standardize
from core.settings import get_settings

INDEX_LIST = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000688.SH": "科创50",
}

_CACHE_DIR = os.path.join(AppConfig.DATA_DIR, "indices")


def _cache_path(ts_code: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, ts_code.replace(".", "_") + ".csv")


def _fetch_tushare(ts_code: str, start: str, end: str) -> pd.DataFrame:
    token = get_settings().get("tushare_token", "").strip()
    if not token:
        return pd.DataFrame()
    import tushare as ts
    ts.set_token(token)
    pro = ts.pro_api()
    df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame()
    return standardize(df.sort_values("trade_date"))


def _fetch_akshare(ts_code: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak
    code6 = ts_code.split(".")[0]
    df = ak.index_zh_a_hist(symbol=code6, period="daily",
                            start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                            "最低": "low", "收盘": "close", "成交量": "vol",
                            "成交额": "amount"})
    return standardize(df)


def get_index_kline(ts_code: str, days: int = 500) -> pd.DataFrame:
    """获取指数日线（带本地缓存，每天最多刷新一次）

    缓存只增不减：新拉取的数据与旧缓存按日期合并去重，
    避免小窗口请求把大缓存覆盖成小文件。"""
    path = _cache_path(ts_code)
    today = dt.datetime.now().strftime("%Y%m%d")

    cached = pd.DataFrame()
    if os.path.exists(path):
        try:
            cached = standardize(pd.read_csv(path, encoding="utf-8-sig"))
        except Exception:
            cached = pd.DataFrame()

    # 缓存新鲜（今天已更新过）且行数足够，直接用
    if not cached.empty and len(cached) >= days:
        last = cached["交易日期"].max()
        # 收盘后(15:30+)数据已到今天，或盘中缓存到上一交易日，都算新鲜
        fresh_threshold = dt.datetime.now() - dt.timedelta(days=3)
        if last >= fresh_threshold and \
                dt.datetime.fromtimestamp(os.path.getmtime(path)).date() == dt.datetime.now().date():
            return cached.tail(days).reset_index(drop=True)

    # 拉取窗口要覆盖缓存已有的最早日期，保证缓存只增不减
    need_start = dt.datetime.now() - dt.timedelta(days=int(days * 1.6))
    if not cached.empty:
        cache_first = cached["交易日期"].min()
        if cache_first < need_start:
            need_start = cache_first
    start = need_start.strftime("%Y%m%d")

    df = pd.DataFrame()
    try:
        df = _fetch_tushare(ts_code, start, today)
    except Exception as e:
        print(f"tushare指数 {ts_code} 失败: {e}")
    if df.empty:
        try:
            df = _fetch_akshare(ts_code, start, today)
        except Exception as e:
            print(f"akshare指数 {ts_code} 失败: {e}")

    if df.empty:
        # 网络失败时退而用旧缓存（哪怕不够新鲜）
        if not cached.empty:
            return cached.tail(days).reset_index(drop=True)
        return df

    # 与旧缓存合并去重，缓存只增不减
    if not cached.empty:
        df = (pd.concat([cached, df], ignore_index=True)
              .drop_duplicates(subset=["交易日期"], keep="last")
              .sort_values("交易日期").reset_index(drop=True))
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df.tail(days).reset_index(drop=True)


def list_indices() -> Dict[str, str]:
    return dict(INDEX_LIST)

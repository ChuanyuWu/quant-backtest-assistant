"""Validated, atomic full-history qfq updates. No unadjusted/qfq mixing."""
import datetime as dt
import hashlib
import os
import re
import time
from typing import Callable, Optional, Dict, List

import numpy as np
import pandas as pd
import requests

from config.app_config import AppConfig
from core.data_io import sanitize_filename, atomic_write, file_lock, load_json, save_json
from core.schema import standardize, REQUIRED_COLS
from core.settings import get_settings
from core.update_state import now, patch_state
from core.provider_worker import call as _ak_call

ProgressCb = Optional[Callable[[int, int, str], None]]
TUSHARE_INTERVAL = 1.3
AKSHARE_INTERVAL = 0.35


class DataError(Exception):
    def __init__(self, interface, category, reason):
        self.interface, self.category = interface, category
        super().__init__(redact(reason))


def redact(reason):
    text = str(reason)
    token = get_settings().get("tushare_token", "")
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(r"(?i)(token|authorization|api_key)([\s:=]+)[^\s,;]+", r"\1\2[REDACTED]", text)
    return text[:400]


def classify(exc):
    if isinstance(exc, DataError):
        return exc.category
    msg = str(exc).lower()
    # Provider workers serialize exceptions. Recognize transport errors before
    # inspecting server messages; URLs contain dates such as 20240101/20240301.
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError)) or any(
            x in msg for x in ("timeout", "timed out", "connection", "proxyerror", "超时", "网络")):
        return "network"
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    explicit_status = re.search(r"\b(?:http(?: error)?|status(?: code)?)[\s:=]+(401|403|429)\b", msg)
    if explicit_status:
        status = int(explicit_status.group(1))
    if status == 401 or any(x in msg for x in ("token", "凭证", "认证", "unauthorized")):
        return "authentication"
    if status == 403 or any(x in msg for x in ("权限", "积分", "permission", "forbidden")):
        return "permission"
    if status == 429 or any(x in msg for x in ("频率", "频次", "每分钟", "每天最多", "限流", "rate limit")):
        return "rate_limit"
    if isinstance(exc, requests.RequestException):
        return "network"
    return "invalid_data" if isinstance(exc, (ValueError, KeyError)) else "unknown"


def _retry(func, times=3, delay=2.0, interface="unknown", **kwargs):
    for attempt in range(times):
        try:
            return func()
        except Exception as exc:
            category = classify(exc)
            if category not in ("network", "rate_limit") or attempt == times - 1:
                raise DataError(interface, category, exc) from exc
            time.sleep(65 if category == "rate_limit" else delay * 2 ** attempt)



def _code_of(name):
    match = re.search(r"\((\d{6})\)", name)
    return match.group(1) if match else ""


def _stock_lock(code):
    return file_lock(os.path.join(AppConfig.DATA_DIR, "locks", code + ".lock"))


def _local_stocks(code=None):
    out = {}
    os.makedirs(AppConfig.STOCK_DATA_DIR, exist_ok=True)
    for filename in sorted(os.listdir(AppConfig.STOCK_DATA_DIR)):
        if filename.endswith(".csv") and (code is None or f"({code})" in filename):
            try:
                out[filename[:-4]] = standardize(pd.read_csv(
                    os.path.join(AppConfig.STOCK_DATA_DIR, filename), encoding="utf-8-sig"))
            except Exception:
                # Keep the name so corrupt files are reported rather than silently skipped.
                out[filename[:-4]] = pd.DataFrame()
    return out


def _validate(df):
    if df is None or df.empty:
        raise DataError("validation", "empty", "接口未返回行情")
    if not set(REQUIRED_COLS).issubset(df.columns):
        raise DataError("validation", "invalid_data", "行情缺少必需字段")
    values = df[["开盘价", "最高价", "最低价", "收盘价", "成交量"]]
    if not np.isfinite(values.to_numpy(dtype=float)).all() or df["交易日期"].isna().any():
        raise DataError("validation", "invalid_data", "行情含无效数值或日期")
    if ((values.iloc[:, :4] <= 0).any().any() or (values["成交量"] < 0).any()
            or (df["最高价"] < df[["开盘价", "收盘价", "最低价"]].max(axis=1)).any()
            or (df["最低价"] > df[["开盘价", "收盘价", "最高价"]].min(axis=1)).any()):
        raise DataError("validation", "invalid_data", "行情价格范围或成交量异常")


def _save_full(name, df, old=None):
    _validate(df)
    df = df.drop_duplicates("交易日期", keep="last").sort_values("交易日期").reset_index(drop=True)
    if old is not None and not old.empty:
        if not set(old["交易日期"]).issubset(set(df["交易日期"])):
            raise DataError("validation", "history_truncated", "响应缺少已有历史日期，保留原文件")
    path = os.path.join(AppConfig.STOCK_DATA_DIR, sanitize_filename(name) + ".csv")
    atomic_write(path, lambda f: df.to_csv(f, index=False), encoding="utf-8-sig")
    # Delete aliases only after successful publication, under the same code lock.
    code = _code_of(name)
    for filename in os.listdir(AppConfig.STOCK_DATA_DIR):
        if code and filename.endswith(".csv") and f"({code})" in filename and filename != os.path.basename(path):
            os.unlink(os.path.join(AppConfig.STOCK_DATA_DIR, filename))


def _tushare_call(interface, **params):
    token = get_settings().get("tushare_token", "").strip()
    if not token:
        raise DataError(interface, "authentication", "未配置 Tushare Token")

    def request():
        # Rate-limit each underlying HTTP request, including diagnostics and retries.
        # A qfq refresh uses both daily and adj_factor, not a single API request.
        key = hashlib.sha256(token.encode()).hexdigest()[:16]
        rate_path = os.path.join(AppConfig.DATA_DIR, "locks", f"tushare-{key}.json")
        with file_lock(rate_path + ".lock"):
            previous = load_json(rate_path, {}).get("last", 0)
            time.sleep(max(0, TUSHARE_INTERVAL - (time.time() - previous)))
            save_json(rate_path, {"last": time.time()})
        response = requests.post("https://api.tushare.pro", json={
            "api_name": interface, "token": token, "params": params, "fields": ""
        }, timeout=(10, 30))
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(payload.get("msg") or f"Tushare code={payload.get('code')}")
        data = payload.get("data") or {}
        return pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])
    return _retry(request, interface=interface)


def _tushare_fetch_one(ts_code, start, end):
    daily = _tushare_call("daily", ts_code=ts_code, start_date=start, end_date=end)
    if daily.empty:
        return pd.DataFrame()
    factors = _tushare_call("adj_factor", ts_code=ts_code, start_date=start, end_date=end)
    if factors.empty:
        raise DataError("adj_factor", "empty", "复权因子为空，不能降级写入不复权价格")
    data = daily.merge(factors[["trade_date", "adj_factor"]], on="trade_date", validate="one_to_one")
    if len(data) != len(daily) or data["adj_factor"].isna().any() or (data["adj_factor"] <= 0).any():
        raise DataError("adj_factor", "invalid_data", "复权因子缺失或无效")
    data = data.sort_values("trade_date")
    for col in ("open", "high", "low", "close"):
        data[col] = (data[col] * data["adj_factor"] / data["adj_factor"].iloc[-1]).round(4)
    data["amount"] = data["amount"] * 1000  # Tushare 千元 -> 元; volume remains 手.
    return standardize(data)


def _sina_stock_list(limit):
    """Independent, bounded list endpoint; no exchange/EM list dependency."""
    stocks, seen = [], set()
    for page in range(1, 101):
        def request():
            response = requests.get(
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                params={"page": page, "num": 80, "sort": "mktcap", "asc": 0,
                        "node": "hs_a", "symbol": "", "_s_r_a": "page"}, timeout=(10, 20))
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("新浪股票列表响应无效")
            return data
        rows = _retry(request, times=2, interface="sina_stock_list")
        for row in rows:
            code, name = str(row.get("code", "")), str(row.get("name", ""))
            if re.fullmatch(r"(?:60|00)\d{4}", code) and "ST" not in name.upper() and code not in seen:
                stocks.append({"code": code, "name": name})
                seen.add(code)
                if len(stocks) >= limit:
                    return stocks
        if len(rows) < 80:
            break
    if not stocks:
        raise DataError("sina_stock_list", "empty", "新浪股票列表为空")
    return stocks


def _akshare_stock_list(limit: int) -> List[Dict]:
    try:
        return _sina_stock_list(limit)
    except Exception:
        pass

    def via_spot():
        spot = _ak_call("stock_zh_a_spot_em")
        spot = spot[~spot["名称"].str.contains("ST", na=False)]
        spot = spot[spot["代码"].str.startswith(("60", "00"))]
        spot = spot.sort_values("总市值", ascending=False)
        return [{"name": r["名称"], "code": r["代码"]}
                for _, r in spot.head(limit).iterrows()]

    def via_code_name():
        df = _ak_call("stock_info_a_code_name")
        df = df[~df["name"].str.contains("ST", na=False)]
        df = df[df["code"].str.startswith(("60", "00"))]
        return [{"name": r["name"], "code": r["code"]}
                for _, r in df.head(limit).iterrows()]

    try:
        return _retry(via_spot, times=2, interface="stock_zh_a_spot_em")
    except Exception:
        # 快照接口不可用时退回轻量列表接口
        return _retry(via_code_name, times=2, interface="stock_info_a_code_name")


def _akshare_fetch_one(code: str, start: str, end: str, prefer_sina=False) -> pd.DataFrame:
    interface = "stock_zh_a_hist"
    try:
        if prefer_sina:
            raise DataError(interface, "network", "本次任务主行情接口不可用，继续使用新浪")
        df = _retry(lambda: _ak_call(interface, symbol=code, period="daily",
                    start_date=start, end_date=end, adjust="qfq"), interface=interface)
        if df is None or df.empty:
            raise DataError(interface, "empty", "东方财富返回空行情")
        df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                               "最低": "low", "收盘": "close", "成交量": "vol", "成交额": "amount"})
    except Exception as exc:
        if classify(exc) not in ("network", "rate_limit", "empty"):
            raise
        interface = "stock_zh_a_daily"
        df = _retry(lambda: _ak_call(interface, symbol=("sh" if code.startswith("6") else "sz") + code,
                    start_date=start, end_date=end, adjust="qfq"), interface=interface)
        if df is None or df.empty:
            return pd.DataFrame()
        df["volume"] = df["volume"] / 100  # Sina 股 -> canonical 手.
    result = standardize(df)
    if len(result) != len(df):
        raise DataError(interface, "invalid_data", "行情含无法解析的行")
    result.attrs["interface"] = interface
    return result



def diagnose(source):
    checks = []
    end = now().strftime("%Y%m%d")
    start = (now() - dt.timedelta(days=30)).strftime("%Y%m%d")
    interfaces = ("daily", "adj_factor") if source == "tushare" else ("stock_zh_a_hist",)
    for interface in interfaces:
        try:
            if source == "tushare":
                df = _tushare_call(interface, ts_code="600519.SH", start_date=start, end_date=end)
            elif source == "akshare":
                df = _akshare_fetch_one("600519", start, end)
            else:
                raise ValueError("未知数据源")
            if df is None or df.empty:
                raise DataError(interface, "empty", "接口返回空数据")
            actual = df.attrs.get("interface", interface)
            checks.append({"interface": actual, "requested_interface": interface,
                           "ok": True, "category": "available",
                           "reason": "接口可用" if actual == interface else "主接口不可用，已通过新浪前复权接口回退"})
        except Exception as exc:
            checks.append({"interface": interface, "ok": False, "category": classify(exc), "reason": redact(exc)})
    return {"source": source, "ok": all(c["ok"] for c in checks), "checks": checks}


def _result(source, items, target):
    counts = {key: sum(x["status"] == key for x in items) for key in
              ("success", "failed", "up_to_date", "no_new_data")}
    good = counts["success"] + counts["up_to_date"]
    pending = counts["failed"] + counts["no_new_data"]
    status = ("partial_success" if good and pending else "failed" if counts["failed"] else
              "no_new_data" if counts["no_new_data"] or not items else
              "success" if counts["success"] else "up_to_date")
    labels = {"success": "更新成功", "partial_success": "部分成功", "failed": "更新失败",
              "up_to_date": "已是最新", "no_new_data": "暂无新数据"}
    return {"ok": status in ("success", "up_to_date"), "status": status, "source": source,
            "target_date": target, "saved": counts["success"], "failed": counts["failed"],
            "counts": counts, "items": items,
            "msg": f"{source}: {labels[status]}，更新{counts['success']}只，失败{counts['failed']}只，暂无新数据{counts['no_new_data']}只"}


def _run(source, start, end, progress=None, limit=None):
    patch_state(last_attempt=now().isoformat(), source=source)
    items = []
    warnings = []
    target = end or now().strftime("%Y%m%d")
    try:
        if source not in ("akshare", "tushare"):
            raise ValueError("未知数据源")
        for date in (start, target):
            dt.datetime.strptime(date, "%Y%m%d")
        if start > target:
            raise ValueError("起始日期不能晚于结束日期")
        if limit is not None and not 1 <= limit <= 6000:
            raise ValueError("股票数量必须在1至6000之间")
        if end is None:
            from core.update_scheduler import trading_days
            current = now()
            cutoff = current.date() if current.time() >= dt.time(17, 30) else current.date() - dt.timedelta(days=1)
            eligible = [day for day in trading_days(current.date()) if day <= cutoff.isoformat()]
            if not eligible:
                raise ValueError("交易日历没有适用日期")
            target = eligible[-1].replace("-", "")
        local = _local_stocks()
        if limit is None:
            names = sorted(local)
        else:
            if progress:
                progress(0, 1, f"{source}: 获取股票列表")
            if source == "tushare":
                rows = _tushare_call("stock_basic", exchange="", list_status="L")
                rows = rows[(rows["market"] == "主板") & ~rows["name"].str.contains("ST", na=False)]
                try:
                    market = _tushare_call("daily_basic", trade_date=target)
                    if market.empty:
                        raise DataError("daily_basic", "empty", "市值数据尚未发布")
                    rows = rows.merge(market[["ts_code", "total_mv"]], on="ts_code", how="left")
                    rows = rows.sort_values("total_mv", ascending=False)
                except Exception as exc:
                    # Optional ranking permissions must not gate qfq quote capabilities.
                    rows = rows.sort_values("ts_code")
                    warnings.append("daily_basic 市值排序不可用，按代码选取股票池：" + redact(exc))
                stocks = [{"name": r["name"], "code": r["ts_code"].split(".")[0]} for r in rows.head(limit).to_dict("records")]
            else:
                stocks = _akshare_stock_list(limit)
            names = [f"{r['name']}({r['code']})" for r in stocks]
        fatal = None
        prefer_sina = False
        for i, requested_name in enumerate(names):
            name = requested_name
            code = _code_of(name)
            if fatal:
                items.append(dict(fatal, name=name, status="failed", skipped=True))
                if progress:
                    progress(i + 1, len(names), f"{source}: 权限不可用，跳过 {name}")
                continue
            try:
                if not code:
                    raise ValueError("文件名缺少六位证券代码")
                with _stock_lock(code):
                    # Re-read after locking, including any aliases written by another task.
                    matches = {n: d for n, d in _local_stocks(code).items() if _code_of(n) == code}
                    if matches:
                        name = max(matches, key=lambda n: len(matches[n]))
                    old = matches.get(name, pd.DataFrame())
                    if old.empty and matches:
                        raise DataError("local", "invalid_data", "本地文件损坏，请先备份并修复")
                    if not old.empty and old["交易日期"].max() >= pd.Timestamp(target):
                        items.append({"name": name, "status": "up_to_date"})
                    else:
                        earliest = min([pd.Timestamp(start)] + [d["交易日期"].min() for d in matches.values() if not d.empty])
                        begin = earliest.strftime("%Y%m%d")
                        df = (_tushare_fetch_one(code + (".SH" if code.startswith("6") else ".SZ"), begin, target)
                              if source == "tushare" else _akshare_fetch_one(code, begin, target,
                                  **({"prefer_sina": True} if prefer_sina else {})))
                        if df is not None and df.attrs.get("interface") == "stock_zh_a_daily":
                            prefer_sina = True
                        if df is None or df.empty or (not old.empty and df["交易日期"].max() <= old["交易日期"].max()):
                            items.append({"name": name, "status": "no_new_data", "category": "empty_or_stale", "reason": "未返回更新日期的行情，保留原文件"})
                        else:
                            for historical in matches.values():
                                if not historical.empty and not set(historical["交易日期"]).issubset(set(df["交易日期"])):
                                    raise DataError("validation", "history_truncated", "响应缺少已有历史日期，保留原文件")
                            _save_full(name, df, old)
                            caught_up = df["交易日期"].max() >= pd.Timestamp(target)
                            items.append({"name": name, "status": "success" if caught_up else "no_new_data",
                                          "written": True, "interface": df.attrs.get("interface", source), "end": df["交易日期"].max().strftime("%Y-%m-%d")})
            except Exception as exc:
                error = {"name": name, "status": "failed", "category": classify(exc),
                         "interface": getattr(exc, "interface", source), "reason": redact(exc)}
                items.append(error)
                if error["category"] in ("permission", "authentication"):
                    fatal = error
            if progress:
                progress(i + 1, len(names), f"{source}: {name} — {items[-1]['status']}")
            if not fatal and i < len(names) - 1:
                time.sleep(TUSHARE_INTERVAL if source == "tushare" else AKSHARE_INTERVAL)
        if not names:
            items.append({"name": "股票列表", "status": "failed", "category": "empty", "reason": "没有可更新的股票"})
    except Exception as exc:
        items.append({"name": "初始化", "status": "failed", "category": classify(exc),
                      "interface": getattr(exc, "interface", source), "reason": redact(exc)})
    result = _result(source, items, target)
    result["warnings"] = warnings
    updates = {"last_result": result, "last_finished": now().isoformat()}
    if result["ok"]:
        updates["last_success"] = now().isoformat()
    patch_state(**updates)
    return result


def fetch_stock_data(source="akshare", limit=200, start="20240101", end=None, progress=None):
    return _run(source, start, end, progress, limit)


def update_local_stocks(source="akshare", start="20240101", end=None, progress=None):
    return _run(source, start, end, progress)

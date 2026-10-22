"""Opt-in public-source smoke test. Does not modify local quotes or require tokens.

Run from the project root: python -m tests.live_smoke
"""
import json
from concurrent.futures import ThreadPoolExecutor

from core.fetcher import _validate, redact
from core.provider_worker import call
from core.schema import standardize


def check(operation):
    try:
        if operation == "tool_trade_date_hist_sina":
            df = call(operation, timeout=20)
            if df.empty or "trade_date" not in df:
                raise ValueError("交易日历响应无效")
        else:
            kwargs = {"symbol": "600519" if operation == "stock_zh_a_hist" else "sh600519",
                      "start_date": "20240102", "end_date": "20240105", "adjust": "qfq"}
            if operation == "stock_zh_a_hist":
                kwargs["period"] = "daily"
            df = call(operation, timeout=20, **kwargs)
            df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                                    "最低": "low", "收盘": "close", "成交量": "vol", "成交额": "amount"})
            df = standardize(df)
            _validate(df)
        return {"interface": operation, "ok": True, "rows": len(df)}
    except Exception as exc:
        return {"interface": operation, "ok": False, "reason": redact(exc)}


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(check, ["stock_zh_a_hist", "stock_zh_a_daily", "tool_trade_date_hist_sina"]))
    print(json.dumps(results, ensure_ascii=True))

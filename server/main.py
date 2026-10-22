"""量化交易系统网页版 —— FastAPI 服务入口"""
import sys
import os
import re
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from fastapi import FastAPI, Query, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal, Optional

from config.app_config import AppConfig
from core import data_manager, indicators, cache, fetcher
from core.data_io import load_json
from core.update_state import read_state, revision
from core.update_scheduler import tick
from core.settings import get_settings, update_settings
from core.execution_engine import ExecutionEngine
from core.virtual_trade import BUILTIN_STOP_STRATEGIES, StopLossParams
from core.strategies import (TIMING_STRATEGIES, SELECTION_STRATEGIES,
                             run_timing_strategy, run_strategy)
from core.optimizer import StrategyOptimizer
from core.ml_predictor import StockPredictor
from core import news as news_mod
from core import index_data
from strategies.box_strategy.batch_process import batch_analyze
from strategies.box_strategy.process_stock import DEFAULT_STRATEGIES as BOX_STRATEGIES
from ai import assistant
from server import tasks

app = FastAPI(title="量化交易策略回测系统")


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

# ML模型缓存（按 股票+预测周期 缓存，键为 (name, target_days)）
_ml_models = {}


@app.on_event("startup")
def _on_startup():
    """启动可恢复的每日更新；异名文件在完整行情验证成功后清理。"""
    import threading
    threading.Thread(target=_auto_update_loop, daemon=True).start()

# ---------------- 工具 ----------------

def _err(msg: str, code: int = 400):
    return JSONResponse({"error": msg}, status_code=code)


def _parse_code(name: str) -> str:
    """从 '贵州茅台(600519)' 提取代码"""
    m = re.search(r"\((\d{6})\)", name)
    return m.group(1) if m else ""


def _get_df(name: str):
    df = data_manager.load_stock_by_name(name)
    if df.empty:
        return None
    return df


def _buy_hold_benchmark(df: pd.DataFrame) -> pd.DataFrame:
    """买入持有基准（同一只股票）"""
    return df[["交易日期", "收盘价"]].copy()


def _get_benchmark(df: pd.DataFrame):
    """回测基准：优先沪深300指数，失败则退回买入持有本股票
    返回 (benchmark_df, benchmark_name)"""
    try:
        idx = index_data.get_index_kline("000300.SH", days=len(df) + 60)
        if not idx.empty:
            return idx[["交易日期", "收盘价"]], "沪深300"
    except Exception as e:
        print(f"指数基准获取失败，退回买入持有: {e}")
    return _buy_hold_benchmark(df), "买入持有"


def _resolve_stop(stop_name: str = None, custom: dict = None):
    if custom:
        return StopLossParams(**{k: float(v) for k, v in custom.items()})
    return BUILTIN_STOP_STRATEGIES.get(stop_name or "无止损")


# ---------------- 元信息 ----------------

@app.get("/api/meta")
def api_meta():
    timing = {k: {"params": v["params"], "desc": v["desc"]}
              for k, v in TIMING_STRATEGIES.items()}
    selection = {k: {"desc": v["desc"]} for k, v in SELECTION_STRATEGIES.items()}
    stops = {k: (vars(v) if v else None)
             for k, v in BUILTIN_STOP_STRATEGIES.items()}
    return {
        "timing_strategies": timing,
        "selection_strategies": selection,
        "stop_strategies": stops,
        "indicators": indicators.list_indicators(),
        "box_strategies": [{"marker": s["marker"],
                            "name": s.get("name", s["marker"]),
                            "desc": s["desc"]}
                           for s in BOX_STRATEGIES],
        "data_status": data_manager.get_data_status(),
        "update_status": read_state(),
    }


# ---------------- 股票与K线 ----------------

@app.get("/api/stocks")
def api_stocks():
    return {"stocks": data_manager.list_stocks_detail()}


@app.get("/api/kline")
def api_kline(name: str, timing: str = "", params: str = "{}"):
    df = _get_df(name)
    if df is None:
        return _err(f"未找到股票数据: {name}", 404)

    try:
        tp = json.loads(params)
    except json.JSONDecodeError:
        tp = {}

    # 计算全部图表指标
    df = indicators.compute_many(df, [
        {"name": "MA"}, {"name": "MACD"}, {"name": "KDJ"}, {"name": "BOLL"},
    ])

    # 择时信号
    buy_idx, sell_idx = [], []
    if timing and timing in TIMING_STRATEGIES:
        buy_idx, sell_idx = run_timing_strategy(df, timing, **tp)

    dates = [d.strftime("%Y-%m-%d") for d in df["交易日期"]]

    def col(c, nd=3):
        s = df[c]
        return [round(float(v), nd) if pd.notna(v) else None for v in s]

    return {
        "name": name,
        "dates": dates,
        "ohlc": [[round(float(r["开盘价"]), 2), round(float(r["收盘价"]), 2),
                  round(float(r["最低价"]), 2), round(float(r["最高价"]), 2)]
                 for _, r in df.iterrows()],
        "volume": [int(v) for v in df["成交量"]],
        "ma": {"MA5": col("MA5", 2), "MA10": col("MA10", 2), "MA20": col("MA20", 2)},
        "macd": {"DIF": col("DIF"), "DEA": col("DEA"), "bar": col("MACD柱")},
        "kdj": {"K": col("K", 2), "D": col("D", 2), "J": col("J", 2)},
        "boll": {"mid": col("BOLL中轨", 2), "upper": col("BOLL上轨", 2),
                 "lower": col("BOLL下轨", 2)},
        "buy_signals": buy_idx,
        "sell_signals": sell_idx,
    }


# ---------------- 大盘指数 ----------------

@app.get("/api/index/list")
def api_index_list():
    return {"indices": index_data.list_indices()}


@app.get("/api/index/kline")
def api_index_kline(code: str = "000001.SH", days: int = 500):
    df = index_data.get_index_kline(code, days)
    if df.empty:
        return _err(f"指数数据获取失败: {code}（检查网络或Tushare Token）", 404)
    df = indicators.compute(df, "MA")
    dates = [d.strftime("%Y-%m-%d") for d in df["交易日期"]]

    def col(c, nd=2):
        return [round(float(v), nd) if pd.notna(v) else None for v in df[c]]

    return {
        "code": code,
        "name": index_data.INDEX_LIST.get(code, code),
        "dates": dates,
        "ohlc": [[round(float(r["开盘价"]), 2), round(float(r["收盘价"]), 2),
                  round(float(r["最低价"]), 2), round(float(r["最高价"]), 2)]
                 for _, r in df.iterrows()],
        "volume": [int(v) for v in df["成交量"]],
        "ma": {"MA5": col("MA5"), "MA10": col("MA10"), "MA20": col("MA20")},
        "buy_signals": [], "sell_signals": [],
        "macd": None, "kdj": None, "boll": None,
    }


# ---------------- 选股 ----------------

@app.post("/api/select")
def api_select(payload: dict = Body(...)):
    strategy = payload.get("strategy")
    top_n = int(payload.get("top_n", 8))
    params = payload.get("params", {})
    if strategy not in SELECTION_STRATEGIES:
        return _err(f"未知选股策略: {strategy}")
    results = run_strategy(strategy, top_n=top_n, **params)
    return {"results": results, "strategy": strategy}


# ---------------- 回测 ----------------

@app.post("/api/backtest")
def api_backtest(payload: dict = Body(...)):
    name = payload.get("name", "")
    timing = payload.get("timing", "MACD金叉死叉")
    tp = payload.get("timing_params", {})
    buy_ratio = float(payload.get("buy_ratio", 0.5))
    use_cache = payload.get("use_cache", True)

    df = _get_df(name)
    if df is None:
        return _err(f"未找到股票数据: {name}", 404)

    stop = _resolve_stop(payload.get("stop_strategy"), payload.get("stop_custom"))
    stop_dict = vars(stop) if stop else None

    initial_cash = float(payload.get("initial_cash", AppConfig.INITIAL_CASH))
    cache_key = {"name": name, "timing": timing, "tp": tp,
                 "buy_ratio": buy_ratio, "stop": stop_dict,
                 "initial_cash": initial_cash,
                 "revision": revision(df),
                 "rows": len(df),
                 "last_date": str(df["交易日期"].iloc[-1].date())}
    if use_cache:
        hit = cache.get_cached_backtest(cache_key)
        if hit:
            hit["from_cache"] = True
            return hit

    buy_idx, sell_idx = run_timing_strategy(df, timing, **tp)
    engine = ExecutionEngine(initial_cash=initial_cash)
    bench_df, bench_name = _get_benchmark(df)
    result = engine.run(df, buy_idx, sell_idx, buy_ratio, stop,
                        benchmark_df=bench_df)
    out = result.to_dict()
    out["buy_signals"] = buy_idx
    out["sell_signals"] = sell_idx
    out["benchmark_name"] = bench_name
    out["from_cache"] = False
    cache.set_cached_backtest(cache_key, out)
    return out


# ---------------- 模拟交易账户 ----------------

@app.get("/api/account")
def api_account():
    acc = load_json(AppConfig.ACCOUNTS_FILE, {})
    settings = get_settings()
    if "cash" not in acc:
        acc = {"cash": settings.get("initial_cash", 100000),
               "initial_cash": settings.get("initial_cash", 100000)}
    history = load_json(AppConfig.TRADE_HISTORY_FILE, [])
    return {"account": acc, "history": history[-100:]}


@app.post("/api/account/reset")
def api_account_reset():
    settings = get_settings()
    init = settings.get("initial_cash", 100000)
    from core.data_io import save_json
    save_json(AppConfig.ACCOUNTS_FILE, {"cash": init, "initial_cash": init})
    save_json(AppConfig.TRADE_HISTORY_FILE, [])
    return {"ok": True}


@app.post("/api/trade/execute")
def api_trade_execute(payload: dict = Body(...)):
    """用当前账户资金执行一次模拟交易，更新账户"""
    from core.data_io import save_json
    name = payload.get("name", "")
    timing = payload.get("timing", "MACD金叉死叉")
    tp = payload.get("timing_params", {})
    buy_ratio = float(payload.get("buy_ratio", 0.5))

    df = _get_df(name)
    if df is None:
        return _err(f"未找到股票数据: {name}", 404)

    settings = get_settings()
    acc = load_json(AppConfig.ACCOUNTS_FILE, {})
    init = settings.get("initial_cash", 100000)
    cash = acc.get("cash", init)

    stop = _resolve_stop(payload.get("stop_strategy"), payload.get("stop_custom"))
    buy_idx, sell_idx = run_timing_strategy(df, timing, **tp)
    engine = ExecutionEngine(initial_cash=cash)
    result = engine.run(df, buy_idx, sell_idx, buy_ratio, stop,
                        benchmark_df=_buy_hold_benchmark(df))

    acc = {"cash": result.final_value, "initial_cash": init}
    save_json(AppConfig.ACCOUNTS_FILE, acc)

    history = load_json(AppConfig.TRADE_HISTORY_FILE, [])
    history.append({
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stock": name, "timing": timing,
        "trades": len(result.trades),
        "return": round(result.total_return, 2),
        "cash_after": round(result.final_value, 2),
    })
    save_json(AppConfig.TRADE_HISTORY_FILE, history[-200:])

    out = result.to_dict()
    out["account"] = acc
    return out


# ---------------- 异步任务：优化/箱体/数据拉取 ----------------

class DataFetchRequest(BaseModel):
    source: Optional[Literal["akshare", "tushare"]] = None
    limit: int = Field(default=200, ge=1, le=6000)
    start: str = Field(default="20240101", pattern=r"^\d{8}$")
    end: Optional[str] = Field(default=None, pattern=r"^\d{8}$")

@app.post("/api/optimize")
def api_optimize(payload: dict = Body(...)):
    name = payload.get("name", "")
    df = _get_df(name)
    if df is None:
        return _err(f"未找到股票数据: {name}", 404)

    def job(progress=None):
        engine = ExecutionEngine()
        opt = StrategyOptimizer(df, engine, float(payload.get("buy_ratio", 0.5)))
        return opt.optimize(int(payload.get("n_random", 30)),
                            int(payload.get("n_predict", 10)),
                            progress_callback=progress)

    return {"task_id": tasks.create_task(job)}


@app.post("/api/box/batch")
def api_box_batch(payload: dict = Body(...)):
    def job(progress=None):
        return batch_analyze(progress=progress)
    return {"task_id": tasks.create_task(job)}


@app.post("/api/data/fetch")
def api_data_fetch(payload: DataFetchRequest):
    source = payload.source or get_settings().get("data_source", "akshare")
    limit, start, end = payload.limit, payload.start, payload.end

    def job(progress=None):
        return fetcher.fetch_stock_data(source, limit, start, end, progress)

    return {"task_id": tasks.create_task(job)}


@app.get("/api/tasks/{task_id}")
def api_task(task_id: str):
    tasks.cleanup_tasks()
    return tasks.get_task(task_id)


@app.post("/api/data/diagnose")
def api_data_diagnose(payload: dict = Body(...)):
    source = payload.get("source", get_settings().get("data_source", "akshare"))
    if source not in ("akshare", "tushare"):
        return _err("未知数据源")
    return {"task_id": tasks.create_task(lambda progress=None: fetcher.diagnose(source))}


@app.post("/api/data/update")
def api_data_update():
    source = get_settings().get("data_source", "akshare")
    return {"task_id": tasks.create_task(
        lambda progress=None: fetcher.update_local_stocks(source, progress=progress))}


# ---------------- ML预测 ----------------

@app.post("/api/ml/train")
def api_ml_train(payload: dict = Body(...)):
    name = payload.get("name", "")
    td = int(payload.get("target_days", 5))
    df = _get_df(name)
    if df is None:
        return _err(f"未找到股票数据: {name}", 404)
    predictor = StockPredictor()
    report = predictor.train(df, td)
    if "error" not in report:
        _ml_models[(name, td)] = (revision(df), predictor)
    report["target_days"] = td
    return report


@app.post("/api/ml/predict")
def api_ml_predict(payload: dict = Body(...)):
    name = payload.get("name", "")
    td = int(payload.get("target_days", 5))
    df = _get_df(name)
    if df is None:
        return _err(f"未找到股票数据: {name}", 404)
    # 模型按 股票+周期 匹配；没训过该周期就自动用该周期训练
    entry = _ml_models.get((name, td))
    predictor = entry[1] if entry and entry[0] == revision(df) else None
    if predictor is None:
        predictor = StockPredictor()
        report = predictor.train(df, td)
        if "error" in report:
            return report
        _ml_models[(name, td)] = (revision(df), predictor)
    out = predictor.predict(df)
    out["target_days"] = td
    return out


# ---------------- 资讯情绪 ----------------

@app.get("/api/news/feed")
def api_news_feed(refresh: int = 0):
    return {"feed": news_mod.get_news_feed(force=bool(refresh))}


@app.get("/api/news/sentiment")
def api_news_sentiment(refresh: int = 0):
    return {"sentiment": news_mod.get_market_sentiment(force=bool(refresh))}


@app.get("/api/news/stock")
def api_news_stock(name: str):
    code = _parse_code(name)
    if not code:
        return {"news": [], "msg": "股票名称中未包含代码"}
    return {"news": news_mod.get_stock_news(code)}


# ---------------- AI助手 ----------------

@app.post("/api/ai/ask")
def api_ai_ask(payload: dict = Body(...)):
    question = payload.get("question", "").strip()
    if not question:
        return _err("问题不能为空")
    stock_name = payload.get("stock_name", "")
    include_news = payload.get("include_news", False)

    contexts = []
    if stock_name:
        df = _get_df(stock_name)
        if df is not None:
            contexts.append(assistant.build_stock_context(df, stock_name))
        if include_news:
            code = _parse_code(stock_name)
            if code:
                contexts.append(assistant.build_news_context(
                    news_mod.get_stock_news(code)))
    elif include_news:
        contexts.append(assistant.build_news_context(news_mod.get_news_feed(10)))

    answer = assistant.ask_ai(question, "\n\n".join(c for c in contexts if c))
    return {"answer": answer, "history": assistant.get_history()}


@app.get("/api/ai/history")
def api_ai_history():
    return {"history": assistant.get_history()}


@app.post("/api/ai/clear")
def api_ai_clear():
    assistant.clear_history()
    return {"ok": True}


# ---------------- 术语词典 ----------------

@app.get("/api/glossary")
def api_glossary():
    return load_json(AppConfig.GLOSSARY_FILE, {})


# ---------------- 设置 ----------------

@app.get("/api/settings")
def api_get_settings():
    from core.settings import AI_PROVIDERS
    s = get_settings()
    # 脱敏展示：key只显示前后各4位
    masked = dict(s)
    for k in ("tushare_token", "sf_api_key"):
        v = s.get(k, "")
        masked[k] = v if len(v) <= 8 else v[:4] + "*" * (len(v) - 8) + v[-4:]
    masked["_has_token"] = bool(s.get("tushare_token"))
    masked["_has_key"] = bool(s.get("sf_api_key"))
    masked["ai_providers"] = {
        k: {"name": v["name"], "default_model": v["default_model"],
            "site": v["site"], "note": v["note"]}
        for k, v in AI_PROVIDERS.items()}
    return masked


@app.post("/api/settings")
def api_set_settings(payload: dict = Body(...)):
    if "data_source" in payload and payload["data_source"] not in ("akshare", "tushare"):
        return _err("未知数据源")
    # 含*的是脱敏值，不覆盖
    clean = {k: v for k, v in payload.items()
             if v is not None and "*" not in str(v)}
    update_settings(clean)
    return {"ok": True}


@app.post("/api/settings/test")
def api_test_ai(payload: dict = Body(default={})):
    """测试AI连接：用当前（或传入的）配置发一条最小请求"""
    import requests as rq
    from core.settings import get_ai_config, AI_PROVIDERS

    saved = get_ai_config()
    # 允许前端传入未保存的配置进行测试；Key留空则用已保存的
    if payload.get("provider") or payload.get("model"):
        p = AI_PROVIDERS.get(payload.get("provider", ""),
                             AI_PROVIDERS["siliconflow"])
        cfg = {"url": p["url"], "model": payload.get("model") or p["default_model"],
               "api_key": payload.get("key") or saved["api_key"],
               "provider_name": p["name"]}
    else:
        cfg = saved
        if payload.get("key"):
            cfg = {**cfg, "api_key": payload["key"]}

    if not cfg["api_key"]:
        return {"ok": False, "msg": f"未填写 {cfg['provider_name']} 的 API Key"}
    try:
        r = rq.post(cfg["url"],
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json={"model": cfg["model"],
                          "messages": [{"role": "user", "content": "hi"}],
                          "max_tokens": 5},
                    timeout=20)
        if r.status_code == 200:
            return {"ok": True,
                    "msg": f"连接成功 ✓ 服务商 {cfg['provider_name']}，模型 {cfg['model']}"}
        if r.status_code == 429:
            return {"ok": False,
                    "msg": f"模型当前访问量过大被限流（免费模型高峰期常见），"
                           f"Key 本身没问题，稍后重试或换个模型（请求模型: {cfg['model']}）"}
        return {"ok": False,
                "msg": f"HTTP {r.status_code}: {r.text[:150]}（请求模型: {cfg['model']}）"}
    except Exception as e:
        return {"ok": False, "msg": f"请求异常: {e}"}


# ---------------- 静态页面 ----------------

@app.get("/")
def index():
    return FileResponse(os.path.join(AppConfig.WEB_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=AppConfig.WEB_DIR), name="static")


# ---------------- 每日自动更新 ----------------

def _auto_update_loop():
    import time
    while True:
        try:
            tick()
        except Exception as exc:
            print(f"[自动更新] {fetcher.redact(exc)}")
        time.sleep(60)


def run():
    import uvicorn
    # 自动更新线程由 startup 事件启动，此处不再重复
    uvicorn.run(app, host=AppConfig.HOST, port=AppConfig.PORT, log_level="warning")


if __name__ == "__main__":
    run()

"""技术指标注册表：统一接口，新指标注册即用

每个指标注册：名称、计算函数、默认参数、输出列、说明。
策略/图表通过 compute() 按需计算，结果以新列加入DataFrame。
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Callable

_REGISTRY: Dict[str, Dict] = {}


def register(name: str, func: Callable, default_params: Dict,
             outputs: List[str], description: str,
             param_meta: Dict = None):
    """
    param_meta: {参数名: {'label': 中文名, 'min': x, 'max': y, 'step': z}}
    供前端自动生成参数滑块。
    """
    _REGISTRY[name] = {
        "func": func,
        "default_params": dict(default_params),
        "outputs": outputs,
        "description": description,
        "param_meta": param_meta or {},
    }


def list_indicators() -> List[Dict]:
    """供前端展示/生成控件"""
    return [{
        "name": name,
        "description": meta["description"],
        "outputs": meta["outputs"],
        "default_params": meta["default_params"],
        "param_meta": meta["param_meta"],
    } for name, meta in _REGISTRY.items()]


def compute(df: pd.DataFrame, name: str, **params) -> pd.DataFrame:
    """计算指定指标，返回带新列的DataFrame副本"""
    if name not in _REGISTRY:
        raise ValueError(f"未注册的指标: {name}")
    meta = _REGISTRY[name]
    p = dict(meta["default_params"])
    p.update({k: v for k, v in params.items() if v is not None})
    result = meta["func"](df, **p)
    out = df.copy()
    for col, series in result.items():
        out[col] = series
    return out


def compute_many(df: pd.DataFrame, specs: List[Dict]) -> pd.DataFrame:
    """批量计算: specs = [{'name': 'MACD', 'params': {...}}, ...]"""
    out = df
    for spec in specs:
        out = compute(out, spec["name"], **spec.get("params", {}))
    return out


# ==================== 指标实现 ====================

def _macd(df, fast=12, slow=26, signal=9):
    close = df["收盘价"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    bar = 2 * (dif - dea)
    return {"DIF": dif, "DEA": dea, "MACD柱": bar}


def _rsi(df, period=14):
    close = df["收盘价"]
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    # Wilder 平滑（比简单均值更标准的RSI定义）
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.fillna(50.0)
    return {f"RSI{period}": rsi}


def _kdj(df, n=9, m1=3, m2=3):
    low_n = df["最低价"].rolling(n, min_periods=1).min()
    high_n = df["最高价"].rolling(n, min_periods=1).max()
    rsv = (df["收盘价"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return {"K": k, "D": d, "J": j}


def _boll(df, period=20, std_num=2.0):
    close = df["收盘价"]
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return {
        "BOLL中轨": mid,
        "BOLL上轨": mid + std_num * std,
        "BOLL下轨": mid - std_num * std,
    }


def _ma(df, periods=(5, 10, 20)):
    close = df["收盘价"]
    out = {}
    for p in periods:
        out[f"MA{p}"] = close.rolling(int(p)).mean()
    return out


def _vol_ma(df, periods=(5, 20)):
    vol = df["成交量"]
    return {f"VOL_MA{int(p)}": vol.rolling(int(p)).mean() for p in periods}


# ==================== 注册 ====================

register("MACD", _macd,
         {"fast": 12, "slow": 26, "signal": 9},
         ["DIF", "DEA", "MACD柱"],
         "指数平滑异同移动平均线，判断趋势与买卖点",
         {"fast": {"label": "快线", "min": 5, "max": 30, "step": 1},
          "slow": {"label": "慢线", "min": 15, "max": 60, "step": 1},
          "signal": {"label": "信号线", "min": 3, "max": 20, "step": 1}})

register("RSI", _rsi,
         {"period": 14},
         ["RSI14"],
         "相对强弱指标，衡量超买超卖",
         {"period": {"label": "周期", "min": 6, "max": 30, "step": 1}})

register("KDJ", _kdj,
         {"n": 9, "m1": 3, "m2": 3},
         ["K", "D", "J"],
         "随机指标，短线超买超卖与金叉死叉信号",
         {"n": {"label": "N周期", "min": 5, "max": 20, "step": 1},
          "m1": {"label": "M1", "min": 2, "max": 6, "step": 1},
          "m2": {"label": "M2", "min": 2, "max": 6, "step": 1}})

register("BOLL", _boll,
         {"period": 20, "std_num": 2.0},
         ["BOLL中轨", "BOLL上轨", "BOLL下轨"],
         "布林带，判断价格波动区间与突破",
         {"period": {"label": "周期", "min": 10, "max": 40, "step": 1},
          "std_num": {"label": "标准差倍数", "min": 1.0, "max": 3.0, "step": 0.1}})

register("MA", _ma,
         {"periods": (5, 10, 20)},
         ["MA5", "MA10", "MA20"],
         "移动平均线，趋势判断基础")

register("VOL_MA", _vol_ma,
         {"periods": (5, 20)},
         ["VOL_MA5", "VOL_MA20"],
         "成交量均线")


# ==================== 兼容旧接口 ====================

def calculate_macd(close: pd.Series, fast=12, slow=26, signal=9):
    df = pd.DataFrame({"收盘价": close})
    r = _macd(df, fast, slow, signal)
    return r["DIF"], r["DEA"], r["MACD柱"]


def calculate_rsi(close: pd.Series, period=14):
    df = pd.DataFrame({"收盘价": close})
    return _rsi(df, period)[f"RSI{period}"]

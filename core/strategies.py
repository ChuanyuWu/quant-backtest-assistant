"""选股策略 + 择时策略（网页版）

择时策略统一签名: (df, **params) -> (buy_idx_list, sell_idx_list)
选股策略统一签名: (stock_data_dir, top_n, **params) -> [ {'name','score','reason'} ]
"""
import sys
import os
from typing import List, Tuple, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.app_config import AppConfig
from core.indicators import compute, calculate_macd, calculate_rsi
from core.data_manager import load_single_stock, get_stock_file_list


# ==================== 择时策略 ====================

def strategy_macd_cross_timing(df, fast=12, slow=26, signal=9):
    """MACD金叉死叉择时"""
    if df.empty or len(df) < slow + signal:
        return [], []
    dif, dea, _ = calculate_macd(df["收盘价"], fast, slow, signal)
    golden = (dif > dea) & (dif.shift(1) <= dea.shift(1))
    death = (dif < dea) & (dif.shift(1) >= dea.shift(1))
    return golden[golden].index.tolist(), death[death].index.tolist()


def strategy_double_ma_timing(df, short_ma=5, long_ma=20, vol_ratio=1.5):
    """双均线交叉择时（带成交量确认）"""
    if df.empty or len(df) < long_ma + 5:
        return [], []
    close, vol = df["收盘价"], df["成交量"]
    ma_s = close.rolling(short_ma).mean()
    ma_l = close.rolling(long_ma).mean()
    golden = (ma_s > ma_l) & (ma_s.shift(1) <= ma_l.shift(1))
    death = (ma_s < ma_l) & (ma_s.shift(1) >= ma_l.shift(1))
    vol_ma20 = vol.rolling(20).mean()
    buy_idx = [idx for idx in golden[golden].index
               if idx >= 20 and vol.iloc[idx] > vol_ma20.iloc[idx] * vol_ratio]
    return buy_idx, death[death].index.tolist()


def strategy_kdj_timing(df, n=9, m1=3, m2=3, oversold=20, overbought=80):
    """KDJ择时：超卖区金叉买入，超买区死叉卖出"""
    if df.empty or len(df) < n + 10:
        return [], []
    r = compute(df, "KDJ", n=n, m1=m1, m2=m2)
    k, d = r["K"], r["D"]
    golden = (k > d) & (k.shift(1) <= d.shift(1))
    death = (k < d) & (k.shift(1) >= d.shift(1))
    buy_idx = [i for i in golden[golden].index if k.iloc[i] < oversold + 30]
    sell_idx = [i for i in death[death].index if k.iloc[i] > overbought - 30]
    return buy_idx, sell_idx


def strategy_boll_timing(df, period=20, std_num=2.0):
    """布林带择时：下轨反弹买入，上轨回落卖出"""
    if df.empty or len(df) < period + 5:
        return [], []
    r = compute(df, "BOLL", period=period, std_num=std_num)
    close = df["收盘价"]
    lower, upper = r["BOLL下轨"], r["BOLL上轨"]
    # 收盘价从下轨下方回到上方 = 反弹买入
    buy = (close > lower) & (close.shift(1) <= lower.shift(1))
    sell = (close < upper) & (close.shift(1) >= upper.shift(1))
    return buy[buy].index.tolist(), sell[sell].index.tolist()


TIMING_STRATEGIES: Dict[str, Dict] = {
    "MACD金叉死叉": {
        "func": strategy_macd_cross_timing,
        "params": {
            "fast": {"label": "快线", "default": 12, "min": 5, "max": 30, "step": 1},
            "slow": {"label": "慢线", "default": 26, "min": 15, "max": 60, "step": 1},
            "signal": {"label": "信号线", "default": 9, "min": 3, "max": 20, "step": 1},
        },
        "desc": "DIF上穿DEA买入，下穿卖出",
    },
    "双均线交叉": {
        "func": strategy_double_ma_timing,
        "params": {
            "short_ma": {"label": "短均线", "default": 5, "min": 3, "max": 20, "step": 1},
            "long_ma": {"label": "长均线", "default": 20, "min": 10, "max": 60, "step": 1},
            "vol_ratio": {"label": "量比确认", "default": 1.5, "min": 1.0, "max": 3.0, "step": 0.1},
        },
        "desc": "短均线上穿长均线且放量买入，下穿卖出",
    },
    "KDJ超买超卖": {
        "func": strategy_kdj_timing,
        "params": {
            "n": {"label": "N周期", "default": 9, "min": 5, "max": 20, "step": 1},
            "oversold": {"label": "超卖线", "default": 20, "min": 10, "max": 40, "step": 5},
            "overbought": {"label": "超买线", "default": 80, "min": 60, "max": 90, "step": 5},
        },
        "desc": "KDJ低位金叉买入，高位死叉卖出",
    },
    "布林带突破": {
        "func": strategy_boll_timing,
        "params": {
            "period": {"label": "周期", "default": 20, "min": 10, "max": 40, "step": 1},
            "std_num": {"label": "标准差倍数", "default": 2.0, "min": 1.0, "max": 3.0, "step": 0.1},
        },
        "desc": "下轨反弹买入，上轨回落卖出",
    },
}


def run_timing_strategy(df, strategy_name: str, **params) -> Tuple[List[int], List[int]]:
    if strategy_name not in TIMING_STRATEGIES:
        return [], []
    func = TIMING_STRATEGIES[strategy_name]["func"]
    valid = {k: v for k, v in params.items() if v is not None}
    return func(df, **valid)


# ==================== 选股策略 ====================

def _base_filter(df, name, min_len, min_amount):
    if df.empty or len(df) < min_len:
        return False
    if "ST" in name.upper():
        return False
    df_amount = (df["成交量"] * df["收盘价"]).tail(20).mean()
    return df_amount >= min_amount


def strategy_macd_score_selection(stock_data_dir=None, top_n=5, **params):
    """MACD综合打分选股"""
    stock_data_dir = stock_data_dir or AppConfig.STOCK_DATA_DIR
    macd_fast = params.get("macd_fast", AppConfig.DEFAULT_MACD_FAST)
    macd_slow = params.get("macd_slow", AppConfig.DEFAULT_MACD_SLOW)
    macd_signal = params.get("macd_signal", AppConfig.DEFAULT_MACD_SIGNAL)
    rsi_period = params.get("rsi_period", AppConfig.DEFAULT_RSI_PERIOD)
    rsi_low = params.get("rsi_low", AppConfig.DEFAULT_RSI_LOW)
    rsi_high = params.get("rsi_high", AppConfig.DEFAULT_RSI_HIGH)
    vol_ratio_th = params.get("vol_ratio", AppConfig.DEFAULT_VOL_RATIO)
    min_amount = params.get("min_amount", AppConfig.DEFAULT_MIN_AMOUNT)

    selected = []
    names, paths = get_stock_file_list(stock_data_dir)
    for name, path in zip(names, paths):
        try:
            df = load_single_stock(path)
            if not _base_filter(df, name, max(macd_slow + macd_signal, rsi_period) + 10, min_amount):
                continue
            close = df["收盘价"]
            dif, dea, _ = calculate_macd(close, macd_fast, macd_slow, macd_signal)
            rsi = calculate_rsi(close, rsi_period)

            score = 0
            if dif.iloc[-1] > 0:
                score += 15
            if dif.iloc[-1] > dea.iloc[-1]:
                score += 15
            if len(dif) >= 3 and dif.iloc[-1] > dif.iloc[-3]:
                score += 10
            if rsi_low <= rsi.iloc[-1] <= rsi_high:
                score += 20

            vol5, vol20 = df["成交量"].tail(5).mean(), df["成交量"].tail(20).mean()
            vr = vol5 / vol20 if vol20 > 0 else 1
            if vr > vol_ratio_th:
                score += 20

            if len(close) >= 20:
                if close.tail(5).mean() > close.tail(10).mean() > close.tail(20).mean():
                    score += 10

            selected.append({"name": name, "score": score, "reason": f"得分:{score}"})
        except Exception as e:
            print(f"选股处理 {name} 失败: {e}")

    selected.sort(key=lambda x: x["score"], reverse=True)
    return selected[:top_n]


def strategy_ma_alignment_selection(stock_data_dir=None, top_n=5, **params):
    """均线多头排列选股"""
    stock_data_dir = stock_data_dir or AppConfig.STOCK_DATA_DIR
    ma_short, ma_mid, ma_long = params.get("ma_short", 5), params.get("ma_mid", 10), params.get("ma_long", 20)
    vol_ratio_th = params.get("vol_ratio", 0.7)
    min_amount = params.get("min_amount", 10000000)

    selected = []
    names, paths = get_stock_file_list(stock_data_dir)
    for name, path in zip(names, paths):
        try:
            df = load_single_stock(path)
            if not _base_filter(df, name, ma_long + 10, min_amount):
                continue
            close = df["收盘价"]
            ma_s = close.rolling(ma_short).mean()
            ma_m = close.rolling(ma_mid).mean()
            ma_l = close.rolling(ma_long).mean()
            if not (ma_s.iloc[-1] > ma_m.iloc[-1] > ma_l.iloc[-1] and close.iloc[-1] > ma_s.iloc[-1]):
                continue
            vol5, vol20 = df["成交量"].tail(5).mean(), df["成交量"].tail(20).mean()
            vr = vol5 / vol20 if vol20 > 0 else 1
            if vr < vol_ratio_th:
                continue
            score = vr * 20 + (close.iloc[-1] / ma_l.iloc[-1]) * 10
            selected.append({"name": name, "score": round(score, 1), "reason": f"量比:{vr:.1f}"})
        except Exception as e:
            print(f"均线选股 {name} 失败: {e}")

    selected.sort(key=lambda x: x["score"], reverse=True)
    return selected[:top_n]


def strategy_kdj_oversold_selection(stock_data_dir=None, top_n=5, **params):
    """KDJ超卖反弹选股：K值低位金叉的股票"""
    stock_data_dir = stock_data_dir or AppConfig.STOCK_DATA_DIR
    n = params.get("n", 9)
    k_max = params.get("k_max", 30)
    min_amount = params.get("min_amount", AppConfig.DEFAULT_MIN_AMOUNT)

    selected = []
    names, paths = get_stock_file_list(stock_data_dir)
    for name, path in zip(names, paths):
        try:
            df = load_single_stock(path)
            if not _base_filter(df, name, n + 15, min_amount):
                continue
            r = compute(df, "KDJ", n=n)
            k, d, j = r["K"], r["D"], r["J"]
            golden_recent = any(
                k.iloc[-i] > d.iloc[-i] and k.iloc[-i - 1] <= d.iloc[-i - 1]
                for i in range(1, min(6, len(df) - 1))
            )
            if k.iloc[-1] < k_max and golden_recent:
                score = (k_max - k.iloc[-1]) + (50 - min(50, j.iloc[-1]))
                selected.append({"name": name, "score": round(score, 1),
                                 "reason": f"K={k.iloc[-1]:.0f},低位金叉"})
        except Exception as e:
            print(f"KDJ选股 {name} 失败: {e}")

    selected.sort(key=lambda x: x["score"], reverse=True)
    return selected[:top_n]


def strategy_boll_break_selection(stock_data_dir=None, top_n=5, **params):
    """布林带突破选股：放量突破上轨"""
    stock_data_dir = stock_data_dir or AppConfig.STOCK_DATA_DIR
    period = params.get("period", 20)
    std_num = params.get("std_num", 2.0)
    vol_ratio_th = params.get("vol_ratio", 1.5)
    min_amount = params.get("min_amount", AppConfig.DEFAULT_MIN_AMOUNT)

    selected = []
    names, paths = get_stock_file_list(stock_data_dir)
    for name, path in zip(names, paths):
        try:
            df = load_single_stock(path)
            if not _base_filter(df, name, period + 10, min_amount):
                continue
            r = compute(df, "BOLL", period=period, std_num=std_num)
            close = df["收盘价"]
            upper = r["BOLL上轨"]
            if close.iloc[-1] <= upper.iloc[-1]:
                continue
            vol5, vol20 = df["成交量"].tail(5).mean(), df["成交量"].tail(20).mean()
            vr = vol5 / vol20 if vol20 > 0 else 1
            if vr < vol_ratio_th:
                continue
            break_pct = (close.iloc[-1] - upper.iloc[-1]) / upper.iloc[-1] * 100
            score = break_pct * 10 + vr * 5
            selected.append({"name": name, "score": round(score, 1),
                             "reason": f"突破上轨{break_pct:.1f}%,量比{vr:.1f}"})
        except Exception as e:
            print(f"BOLL选股 {name} 失败: {e}")

    selected.sort(key=lambda x: x["score"], reverse=True)
    return selected[:top_n]


SELECTION_STRATEGIES: Dict[str, Dict] = {
    "MACD打分选股": {"func": strategy_macd_score_selection,
                   "desc": "DIF方向/金叉/RSI区间/量比/均线排列综合打分"},
    "均线多头排列选股": {"func": strategy_ma_alignment_selection,
                     "desc": "MA5>MA10>MA20且价在MA5上，量比与趋势打分"},
    "KDJ超卖反弹选股": {"func": strategy_kdj_oversold_selection,
                     "desc": "K值低位且近期金叉，博反弹"},
    "BOLL放量突破选股": {"func": strategy_boll_break_selection,
                      "desc": "收盘价放量突破布林上轨"},
}


def run_strategy(strategy_name: str, stock_data_dir: str = None, top_n: int = 5, **params):
    if strategy_name not in SELECTION_STRATEGIES:
        return []
    return SELECTION_STRATEGIES[strategy_name]["func"](stock_data_dir, top_n, **params)

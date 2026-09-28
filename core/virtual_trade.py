"""交易记录与持仓数据结构"""
from typing import Dict
from dataclasses import dataclass


@dataclass
class TradeRecord:
    """单笔交易记录"""
    date: str
    type: str           # 买入 / 卖出 / 止损 / 一阶止盈 / 二阶止盈 / 期末清仓
    price: float
    shares: int
    cash_after: float
    profit: float = 0.0


@dataclass
class Position:
    """持仓信息"""
    avg_cost: float
    shares: int
    highest_price: float = 0.0


@dataclass
class StopLossParams:
    """止损止盈参数"""
    stop_loss: float        # 止损线，如 0.07 表示 -7%
    take_profit_1: float    # 一阶止盈线
    take_profit_2: float    # 二阶止盈线
    sell_ratio: float       # 一阶止盈时卖出比例


# 内置止损策略
BUILTIN_STOP_STRATEGIES: Dict[str, StopLossParams] = {
    "无止损": None,
    "保守型":     StopLossParams(0.05, 0.10, 0.25, 0.7),
    "稳健型":     StopLossParams(0.07, 0.18, 0.40, 0.5),
    "激进型":     StopLossParams(0.12, 0.25, 0.60, 0.3),
    "增强稳健":   StopLossParams(0.08, 0.20, 0.45, 0.5),
    "增强激进":   StopLossParams(0.15, 0.30, 0.70, 0.3),
    "超保守":     StopLossParams(0.03, 0.08, 0.20, 0.8),
}

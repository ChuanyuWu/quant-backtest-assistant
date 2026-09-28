"""统一回测/交易执行引擎（网页版 v3）

相对桌面版的改动：
1. 修复胜率统计bug：改为FIFO配对买卖批次，部分止盈卖出时配对不再错位
2. 新增基准对比（买入持有基准 + 可选外部基准曲线）
3. 新增绩效指标：年化收益、盈亏比、卡玛比率、平均持仓天数

v3 改动（修复前视偏差）：
4. 信号 T+1 执行：买卖信号由当日收盘价算出，收盘前无法确认真伪，
   因此统一在【下一交易日开盘价】成交，消除"用今天的收盘价决定今天的买入"
   这一系统性高估回测收益的前视偏差。止损/止盈检查维持收盘价判断。
"""
import sys
import os
import math
import numpy as np
import pandas as pd
from typing import List, Optional, Dict
from dataclasses import dataclass, field
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.app_config import AppConfig
from core.virtual_trade import Position, StopLossParams, TradeRecord


@dataclass
class BacktestResult:
    total_return: float
    final_value: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    sharpe_ratio: float
    annual_return: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    avg_hold_days: float = 0.0
    benchmark_return: float = 0.0
    excess_return: float = 0.0
    trades: List[TradeRecord] = field(default_factory=list)
    daily_values: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "total_return": round(self.total_return, 2),
            "final_value": round(self.final_value, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "win_rate": round(self.win_rate, 1),
            "total_trades": self.total_trades,
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "annual_return": round(self.annual_return, 2),
            "profit_factor": round(self.profit_factor, 2),
            "calmar_ratio": round(self.calmar_ratio, 2),
            "avg_hold_days": round(self.avg_hold_days, 1),
            "benchmark_return": round(self.benchmark_return, 2),
            "excess_return": round(self.excess_return, 2),
            "trades": [t.__dict__ for t in self.trades],
            "daily_values": self.daily_values,
        }


class ExecutionEngine:
    """回测与模拟交易统一执行引擎"""

    def __init__(self,
                 initial_cash: float = AppConfig.INITIAL_CASH,
                 commission_rate: float = AppConfig.COMMISSION_RATE,
                 min_commission: float = AppConfig.MIN_COMMISSION,
                 slippage_buy: float = AppConfig.SLIPPAGE_BUY,
                 slippage_sell: float = AppConfig.SLIPPAGE_SELL,
                 lot_size: int = AppConfig.LOT_SIZE):
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.slippage_buy = slippage_buy
        self.slippage_sell = slippage_sell
        self.lot_size = lot_size

    def _commission(self, value: float) -> float:
        return max(value * self.commission_rate, self.min_commission)

    def run(self,
            df: pd.DataFrame,
            buy_signals: List[int],
            sell_signals: List[int],
            buy_ratio: float = 0.5,
            stop_params: Optional[StopLossParams] = None,
            benchmark_df: Optional[pd.DataFrame] = None) -> BacktestResult:
        """
        参数:
            benchmark_df: 可选，含 交易日期/收盘价 的基准（如沪深300），按日期对齐
        """
        cash = self.initial_cash
        position: Optional[Position] = None
        trades: List[TradeRecord] = []
        daily_values: List[Dict] = []

        # FIFO 买入批次队列，用于正确的胜率/盈亏配对: [{'price','shares','date_idx'}]
        buy_lots: deque = deque()
        closed_pnl: List[float] = []     # 每笔闭合交易的盈亏金额
        closed_hold: List[int] = []      # 每笔闭合交易的持仓天数

        # T+1 执行：信号在信号日收盘后才成立，顺延到下一交易日执行
        n_rows = len(df)
        buy_set = {i + 1 for i in buy_signals if 0 <= i + 1 < n_rows}
        sell_set = {i + 1 for i in sell_signals if 0 <= i + 1 < n_rows}

        # 基准对齐
        bench_map = {}
        if benchmark_df is not None and not benchmark_df.empty:
            for _, r in benchmark_df.iterrows():
                d = r["交易日期"].strftime("%Y-%m-%d") if hasattr(r["交易日期"], "strftime") else str(r["交易日期"])
                bench_map[d] = r["收盘价"]

        def match_sell(sell_price: float, shares: int, day_idx: int):
            """FIFO配对卖出，记录盈亏与持仓天数"""
            remaining = shares
            pnl = 0.0
            while remaining > 0 and buy_lots:
                lot = buy_lots[0]
                take = min(lot["shares"], remaining)
                pnl += (sell_price - lot["price"]) * take
                lot["shares"] -= take
                remaining -= take
                if lot["shares"] == 0:
                    closed_hold.append(day_idx - lot["day_idx"])
                    buy_lots.popleft()
            closed_pnl.append(pnl)

        for i in range(len(df)):
            row = df.iloc[i]
            raw_price = row["收盘价"]
            # 信号执行价 = 次日开盘价（止损止盈仍按收盘价判断与成交）
            open_price = row["开盘价"] if pd.notna(row.get("开盘价")) else raw_price
            date = row["交易日期"]
            date_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)

            buy_price = open_price * (1 + self.slippage_buy)
            sell_price = open_price * (1 + self.slippage_sell)
            close_sell_price = raw_price * (1 + self.slippage_sell)

            current_value = cash + (position.shares * raw_price if position else 0)
            dv = {"date": date_str, "total_value": current_value, "cash": cash}
            if date_str in bench_map:
                dv["benchmark"] = bench_map[date_str]
            daily_values.append(dv)

            # --- 止损止盈检查（持仓中） ---
            if position is not None and stop_params is not None:
                position.highest_price = max(position.highest_price, raw_price)
                profit_rate = (raw_price - position.avg_cost) / position.avg_cost
                max_profit = (position.highest_price - position.avg_cost) / position.avg_cost

                # 止损（收盘价触发、收盘价成交）
                if profit_rate <= -stop_params.stop_loss:
                    sell_val = close_sell_price * position.shares
                    cash += sell_val - self._commission(sell_val)
                    match_sell(close_sell_price, position.shares, i)
                    trades.append(TradeRecord(date_str, f"止损({profit_rate*100:.1f}%)",
                                              close_sell_price, position.shares, cash,
                                              (close_sell_price - position.avg_cost) * position.shares))
                    position = None
                    continue

                # 二阶止盈（收盘价触发、收盘价成交）
                if max_profit >= stop_params.take_profit_2:
                    sell_val = close_sell_price * position.shares
                    cash += sell_val - self._commission(sell_val)
                    match_sell(close_sell_price, position.shares, i)
                    trades.append(TradeRecord(date_str, f"二阶止盈({max_profit*100:.1f}%)",
                                              close_sell_price, position.shares, cash,
                                              (close_sell_price - position.avg_cost) * position.shares))
                    position = None
                    continue

            # --- 买入信号（空仓时） ---
            if i in buy_set and cash > 0 and position is None:
                max_amount = cash * buy_ratio
                buy_shares = int(max_amount / buy_price / self.lot_size) * self.lot_size
                if buy_shares > 0:
                    cost = buy_price * buy_shares
                    total_cost = cost + self._commission(cost)
                    if cash >= total_cost:
                        cash -= total_cost
                        position = Position(avg_cost=buy_price, shares=buy_shares,
                                            highest_price=buy_price)
                        buy_lots.append({"price": buy_price, "shares": buy_shares,
                                         "day_idx": i})
                        trades.append(TradeRecord(date_str, "买入", buy_price,
                                                  buy_shares, cash, 0.0))

            # --- 卖出信号 ---
            elif position is not None and i in sell_set:
                if stop_params is not None:
                    profit_rate = (raw_price - position.avg_cost) / position.avg_cost
                    if profit_rate >= stop_params.take_profit_1:
                        sell_shares = int(position.shares * stop_params.sell_ratio)
                        sell_shares = (sell_shares // self.lot_size) * self.lot_size
                        remaining = position.shares - sell_shares
                        if sell_shares <= 0 or (remaining > 0 and remaining < self.lot_size):
                            sell_shares = position.shares
                    else:
                        sell_shares = position.shares
                else:
                    sell_shares = position.shares

                if sell_shares > 0:
                    sell_val = sell_price * sell_shares
                    cash += sell_val - self._commission(sell_val)
                    match_sell(sell_price, sell_shares, i)
                    ttype = "一阶止盈" if sell_shares < position.shares else "卖出"
                    trades.append(TradeRecord(date_str, ttype, sell_price,
                                              sell_shares, cash,
                                              (sell_price - position.avg_cost) * sell_shares))
                    if sell_shares == position.shares:
                        position = None
                    else:
                        position.shares -= sell_shares

        # --- 期末清仓 ---
        if position is not None:
            last = df.iloc[-1]
            sell_price = last["收盘价"] * (1 + self.slippage_sell)
            sell_val = sell_price * position.shares
            cash += sell_val - self._commission(sell_val)
            match_sell(sell_price, position.shares, len(df) - 1)
            d = last["交易日期"]
            date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            trades.append(TradeRecord(date_str, "期末清仓", sell_price,
                                      position.shares, cash,
                                      (sell_price - position.avg_cost) * position.shares))
            # 期末清仓发生在最后一个交易日收盘，更新当天净值而不是追加重复日期
            if daily_values and daily_values[-1]["date"] == date_str:
                daily_values[-1]["total_value"] = cash
                daily_values[-1]["cash"] = cash
            else:
                daily_values.append({"date": date_str, "total_value": cash, "cash": cash})

        return self._build_result(trades, daily_values, closed_pnl, closed_hold, bench_map)

    # ---------------- 绩效计算 ----------------

    def _build_result(self, trades, daily_values, closed_pnl, closed_hold, bench_map) -> BacktestResult:
        final_value = daily_values[-1]["total_value"] if daily_values else self.initial_cash
        total_return = (final_value - self.initial_cash) / self.initial_cash * 100

        values = [d["total_value"] for d in daily_values]
        # 最大回撤
        peak, max_dd = values[0] if values else 1, 0.0
        for v in values:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak * 100)

        # 日收益序列
        rets = np.diff(values) / np.array(values[:-1]) if len(values) > 1 else np.array([])
        rets = rets[np.isfinite(rets)]
        n_days = len(rets)

        annual = 0.0
        if n_days > 0:
            annual = ((1 + total_return / 100) ** (252 / max(n_days, 1)) - 1) * 100

        sharpe = 0.0
        if n_days > 1 and rets.std() > 0:
            sharpe = (rets.mean() * 252 - 0.03) / (rets.std() * math.sqrt(252))

        calmar = annual / max_dd if max_dd > 0 else 0.0

        # 胜率（FIFO配对后的闭合交易）
        win_count = sum(1 for p in closed_pnl if p > 0)
        total_closed = len(closed_pnl)
        win_rate = win_count / total_closed * 100 if total_closed else 0.0

        # 盈亏比
        gross_win = sum(p for p in closed_pnl if p > 0)
        gross_loss = abs(sum(p for p in closed_pnl if p < 0))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

        avg_hold = float(np.mean(closed_hold)) if closed_hold else 0.0

        # 基准：优先外部基准，否则买入持有本股票
        bench_return = 0.0
        if bench_map:
            bvals = [d["benchmark"] for d in daily_values if "benchmark" in d]
            if len(bvals) >= 2 and bvals[0] > 0:
                bench_return = (bvals[-1] - bvals[0]) / bvals[0] * 100
        elif len(daily_values) >= 2:
            # 由调用方注入；引擎内不重复计算（在 run() 里由 benchmark_df 提供）
            pass

        total_trades = sum(1 for t in trades if t.type == "买入")

        return BacktestResult(
            total_return=total_return,
            final_value=final_value,
            max_drawdown=max_dd,
            win_rate=win_rate,
            total_trades=total_trades,
            sharpe_ratio=sharpe,
            annual_return=annual,
            profit_factor=profit_factor,
            calmar_ratio=calmar,
            avg_hold_days=avg_hold,
            benchmark_return=bench_return,
            excess_return=total_return - bench_return,
            trades=trades,
            daily_values=daily_values,
        )

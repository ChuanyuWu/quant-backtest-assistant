"""箱体策略批量分析 + 胜率统计（网页版）"""
import sys
import os
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.data_manager import get_stock_file_list, load_single_stock
from core.schema import to_box_format
from .process_stock import process_one_stock_data, DEFAULT_STRATEGIES, DEFAULT_HOLD_DAYS

ProgressCb = Optional[Callable[[int, int, str], None]]


def batch_analyze(hold_days_list: Optional[List[int]] = None,
                  strategies: Optional[List[Dict]] = None,
                  progress: ProgressCb = None) -> Dict:
    """
    批量分析所有本地股票
    返回: {
        'strategy_stats': 各策略×持有期的胜率/平均收益/信号数,
        'recent_signals': 最近信号明细,
        'total_signals': 总信号数,
        'stock_count': 处理股票数
    }
    """
    hold_days_list = hold_days_list or DEFAULT_HOLD_DAYS
    strategies = strategies or DEFAULT_STRATEGIES

    names, paths = get_stock_file_list()
    all_signals = []
    total = len(names)

    for i, (name, path) in enumerate(zip(names, paths)):
        try:
            df = load_single_stock(path)
            if len(df) >= 60:
                sig = process_one_stock_data(name, to_box_format(df),
                                             list_query_setting=strategies,
                                             hold_days_list=hold_days_list)
                if not sig.empty:
                    all_signals.append(sig)
        except Exception as e:
            print(f"箱体分析 {name} 失败: {e}")
        if progress:
            progress(i + 1, total, f"分析中: {name}")

    if not all_signals:
        return {"strategy_stats": [], "recent_signals": [],
                "total_signals": 0, "stock_count": total}

    df_all = pd.concat(all_signals, ignore_index=True)

    # ---- 各策略×持有期统计 ----
    stats = []
    desc_map = {s["marker"]: s["desc"] for s in strategies}
    name_map = {s["marker"]: s.get("name", s["marker"]) for s in strategies}
    for marker, grp in df_all.groupby("marker"):
        row = {"marker": marker, "name": name_map.get(marker, marker),
               "desc": desc_map.get(marker, marker),
               "signal_count": len(grp)}
        for hd in hold_days_list:
            col = f"h{hd}zdf"
            valid = grp[col].dropna()
            row[f"win_{hd}"] = round((valid > 0).mean() * 100, 1) if len(valid) else None
            row[f"avg_{hd}"] = round(valid.mean() * 100, 2) if len(valid) else None
        stats.append(row)
    stats.sort(key=lambda x: x["signal_count"], reverse=True)

    # ---- 最近信号明细 ----
    recent = (df_all.sort_values("meetdate").tail(50)
              .sort_values("meetdate", ascending=False))
    recent_signals = []
    for _, r in recent.iterrows():
        item = {"code": r["code"], "meetdate": r["meetdate"],
                "buydate": r["buydate"], "marker": r["marker"],
                "name": name_map.get(r["marker"], r["marker"]),
                "desc": desc_map.get(r["marker"], r["marker"])}
        for hd in hold_days_list:
            v = r.get(f"h{hd}zdf")
            item[f"h{hd}"] = round(v * 100, 2) if pd.notna(v) else None
        recent_signals.append(item)

    return {
        "strategy_stats": stats,
        "recent_signals": recent_signals,
        "total_signals": len(df_all),
        "stock_count": total,
        "hold_days": hold_days_list,
    }

"""箱体策略：11种买入条件 + 单只股票信号处理"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional

from .feature_boxes import model_feature_boxes, DEFAULT_PARAMS

DEFAULT_HOLD_DAYS = [3, 5, 10, 20]

# 11种默认买入策略（与桌面版一致，name 为前端展示用中文名）
DEFAULT_STRATEGIES = [
    {"marker": "short_box_bottom_buy", "name": "短期箱底企稳",
     "query": "box_exists_short==1 and zdf_to_bottom_short<0.02 and zdf_to_bottom_short>-0.03 and volume_ratio_short<0.7 and price_position_short<0.2",
     "desc": "短期箱体底部区域缩量企稳买入"},
    {"marker": "mid_box_bottom_buy", "name": "中期箱底企稳",
     "query": "box_exists_mid==1 and zdf_to_bottom_mid<0.02 and zdf_to_bottom_mid>-0.03 and volume_ratio_mid<0.7 and price_position_mid<0.2",
     "desc": "中期箱体底部区域缩量企稳买入"},
    {"marker": "short_box_breakout_buy", "name": "短期放量突破",
     "query": "box_exists_short==1 and breakout_short==1 and volume_ratio_short>1.5 and shock_intensity_short<0.1",
     "desc": "短期箱体放量突破买入"},
    {"marker": "mid_box_breakout_buy", "name": "中期放量突破",
     "query": "box_exists_mid==1 and breakout_mid==1 and volume_ratio_mid>1.5 and shock_intensity_mid<0.1",
     "desc": "中期箱体放量突破买入"},
    {"marker": "long_box_breakout_buy", "name": "长期放量突破",
     "query": "box_exists_long==1 and breakout_long==1 and volume_ratio_long>1.5 and shock_intensity_long<0.15",
     "desc": "长期箱体放量突破买入"},
    {"marker": "short_box_accumulation", "name": "短期主力吸筹",
     "query": "box_exists_short==1 and accumulation_degree_short>0.6 and volume_shrink_ratio_short<0.5 and price_position_short<0.3",
     "desc": "短期箱体主力吸筹明显，底部区域"},
    {"marker": "mid_box_accumulation", "name": "中期主力吸筹",
     "query": "box_exists_mid==1 and accumulation_degree_mid>0.6 and volume_shrink_ratio_mid<0.5 and price_position_mid<0.3",
     "desc": "中期箱体主力吸筹明显，底部区域"},
    {"marker": "multi_box_resonance", "name": "短中箱体共振",
     "query": "box_exists_short==1 and box_exists_mid==1 and price_position_short<0.3 and price_position_mid<0.3 and volume_ratio_short<0.8 and volume_ratio_mid<0.8",
     "desc": "短中期箱体底部共振缩量"},
    {"marker": "short_breakout_with_mid_support", "name": "短期突破带中撑",
     "query": "breakout_short==1 and box_exists_mid==1 and price_position_mid<0.5 and volume_ratio_short>1.3",
     "desc": "短期突破且有中期箱体支撑（未到顶部）"},
    {"marker": "box_top_correction", "name": "箱顶缩量整理",
     "query": "box_exists_short==1 and price_position_short>0.7 and price_position_short<1 and volume_ratio_short<0.8 and shock_intensity_short<0.15",
     "desc": "箱体上沿附近缩量整理，等待突破"},
    {"marker": "trend_confirmation", "name": "趋势确认突破",
     "query": "breakout_short==1 and breakout_mid==1 and volume_ratio_short>1.3 and volume_ratio_mid>1.3",
     "desc": "短中期同时放量突破，趋势确认信号"},
]


def process_one_stock_data(stock_name: str, df_box: pd.DataFrame,
                           model_params: Optional[Dict] = None,
                           list_query_setting: Optional[List[Dict]] = None,
                           hold_days_list: Optional[List[int]] = None) -> pd.DataFrame:
    """
    处理单只股票：特征计算 → 信号匹配 → 未来持有期收益
    df_box: 箱体格式DataFrame（day/open/high/low/close/volume/amount）
    返回信号表: code/meetdate/buydate/marker/h3zdf/h5zdf/...
    """
    list_query_setting = list_query_setting or DEFAULT_STRATEGIES
    hold_days_list = hold_days_list or DEFAULT_HOLD_DAYS

    if len(df_box) < 60:
        return pd.DataFrame()

    df_features = model_feature_boxes(df_box, model_params or dict(DEFAULT_PARAMS))

    close = df_features["close"].values
    days = df_features["day"].values
    n = len(df_features)

    results = []
    for q in list_query_setting:
        try:
            signal_idx = df_features.query(q["query"]).index.values
        except Exception:
            continue

        for idx in signal_idx:
            if idx >= n - 1:
                continue
            buy_idx = idx + 1  # T+1买入
            buy_price = close[buy_idx]
            if buy_price <= 0:
                continue

            row = {
                "code": stock_name,
                "meetdate": pd.Timestamp(days[idx]).strftime("%Y-%m-%d"),
                "buydate": pd.Timestamp(days[buy_idx]).strftime("%Y-%m-%d"),
                "marker": q["marker"],
            }
            for hd in hold_days_list:
                t_idx = buy_idx + hd - 1
                row[f"h{hd}zdf"] = ((close[t_idx] - buy_price) / buy_price
                                    if t_idx < n else np.nan)
            results.append(row)

    return pd.DataFrame(results) if results else pd.DataFrame()

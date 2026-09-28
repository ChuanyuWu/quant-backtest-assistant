"""箱体特征提取（网页版：全向量化重写，比桌面版逐日循环快百倍以上）"""
import numpy as np
import pandas as pd
from typing import Dict, Optional

DEFAULT_PARAMS = {
    "box_height_zdf": 0.18,
    "list_box_width": [40, 50, 60],
    "width_label_map": {40: "short", 50: "mid", 60: "long"},
}


def model_feature_boxes(df_data: pd.DataFrame, model_params: Optional[Dict] = None) -> pd.DataFrame:
    """
    输入: ['day','open','close','high','low','volume','amount'] 升序DataFrame
    输出: 附加箱体特征列（与桌面版列名完全一致）
    """
    params = dict(DEFAULT_PARAMS)
    if model_params:
        params.update(model_params)

    zdf_th = params["box_height_zdf"]
    df = df_data.copy().reset_index(drop=True)

    oc_min = np.minimum(df["open"], df["close"])
    oc_max = np.maximum(df["open"], df["close"])
    vol = df["volume"]
    amount = df["amount"]
    close = df["close"]

    for w in params["list_box_width"]:
        label = params["width_label_map"][w]
        p = f"_{label}"

        bottom = oc_min.rolling(w).min()
        top = oc_max.rolling(w).max()
        height = (top - bottom) / bottom
        valid = (height < zdf_th) & bottom.notna() & (bottom > 0)

        vol_mean = vol.rolling(w).mean()
        amount_mean = amount.rolling(w).mean()

        volume_ratio = vol / vol_mean
        volume_break = volume_ratio > 1.2

        breakout = valid & volume_break & (close > top)
        breakdown = valid & volume_break & (close < bottom)

        df[f"box_exists{p}"] = valid.astype(int)
        df[f"bottom{p}"] = bottom.where(valid)
        df[f"top{p}"] = top.where(valid)
        df[f"zdf_to_bottom{p}"] = ((close - bottom) / bottom).where(valid)
        df[f"zdf_to_top{p}"] = ((close - top) / top).where(valid)
        df[f"volume_ratio{p}"] = volume_ratio.where(valid)
        df[f"amount_ratio{p}"] = (amount / amount_mean).where(valid)
        df[f"shock_intensity{p}"] = height.where(valid)
        df[f"volume_shrink_ratio{p}"] = (vol.rolling(w).min() / vol.rolling(w).max()).where(valid)
        df[f"accumulation_degree{p}"] = vol.rolling(w).corr(amount).where(valid)
        df[f"price_position{p}"] = ((close - bottom) / (top - bottom)).where(valid & (top > bottom))
        df[f"breakout{p}"] = breakout.astype(int)
        df[f"breakdown{p}"] = breakdown.astype(int)
        df[f"breakout_strength{p}"] = ((close - top) / top).where(breakout, 0.0)
        df[f"volume_breakout{p}"] = (breakout & (volume_ratio > 1.5)).astype(int)
        df[f"box_formation_days{p}"] = pd.Series(w, index=df.index).where(valid)
        df[f"days_since_box_start{p}"] = pd.Series(w - 1, index=df.index).where(valid)

    return df

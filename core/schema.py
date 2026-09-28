"""统一数据规范：全系统唯一的数据列名约定与转换入口

规范 DataFrame 列：
    交易日期(datetime64) 开盘价 最高价 最低价 收盘价 成交量 [成交额]
均为数值型，按交易日期升序，索引为 0..n-1。
"""
import pandas as pd

REQUIRED_COLS = ["交易日期", "开盘价", "最高价", "最低价", "收盘价", "成交量"]


def standardize(df: pd.DataFrame, date_col: str = "trade_date") -> pd.DataFrame:
    """把 tushare/akshare 等来源的原始DataFrame转换为系统规范格式"""
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    col_map = {
        "open": "开盘价", "high": "最高价", "low": "最低价",
        "close": "收盘价", "vol": "成交量", "volume": "成交量",
        "amount": "成交额", "date": "交易日期", "day": "交易日期",
        "trade_date": "交易日期",
    }
    df = df.rename(columns={c: v for c, v in col_map.items() if c in df.columns})

    if "交易日期" not in df.columns and date_col in df.columns:
        df = df.rename(columns={date_col: "交易日期"})

    if "交易日期" not in df.columns:
        return pd.DataFrame()

    # 日期解析：兼容 20240101 / 2024-01-01
    raw = df["交易日期"].astype(str).str.strip()
    parsed = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
    if parsed.isna().all():
        parsed = pd.to_datetime(raw, errors="coerce")
    df["交易日期"] = parsed

    for col in ["开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["交易日期", "开盘价", "收盘价", "成交量"])
    keep = [c for c in ["交易日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"] if c in df.columns]
    df = df[keep].sort_values("交易日期").reset_index(drop=True)
    return df


def to_box_format(df: pd.DataFrame) -> pd.DataFrame:
    """规范格式 -> 箱体策略使用的英文列格式"""
    out = pd.DataFrame({
        "day": df["交易日期"],
        "open": df["开盘价"], "high": df["最高价"],
        "low": df["最低价"], "close": df["收盘价"],
        "volume": df["成交量"],
    })
    if "成交额" in df.columns:
        out["amount"] = df["成交额"]
    else:
        out["amount"] = df["成交量"] * df["收盘价"] * 100
    return out

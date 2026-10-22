"""股票数据管理：本地CSV加载、列表、状态统计"""
import os
import re
import sys
import pandas as pd
from typing import List, Tuple, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.app_config import AppConfig
from core.schema import standardize


def load_single_stock(filepath: str) -> pd.DataFrame:
    """加载并清洗单只股票CSV数据，返回规范DataFrame"""
    try:
        df = pd.read_csv(filepath, encoding="utf-8-sig")
        return standardize(df)
    except Exception as e:
        print(f"加载股票失败 {filepath}: {e}")
        return pd.DataFrame()


def load_stock_by_name(name: str) -> pd.DataFrame:
    """按名称加载股票数据（防路径遍历：解析后的路径必须位于数据目录内）"""
    if not name or "/" in name or "\\" in name:
        return pd.DataFrame()
    base = os.path.realpath(AppConfig.STOCK_DATA_DIR)
    path = os.path.realpath(os.path.join(base, f"{name}.csv"))
    if not path.startswith(base + os.sep) or not os.path.exists(path):
        return pd.DataFrame()
    return load_single_stock(path)


def dedupe_stock_files() -> int:
    """按 6 位代码合并同代码的重复 CSV（如 万科Ａ/万科A 历史遗留）

    保留行数较多的文件名，数据按日期合并去重，删除其余文件。
    返回清理掉的文件数。
    """
    folder = AppConfig.STOCK_DATA_DIR
    if not os.path.isdir(folder):
        return 0
    by_code: Dict[str, List[str]] = {}
    for fname in os.listdir(folder):
        if not fname.endswith(".csv"):
            continue
        m = re.search(r"\((\d{6})\)", fname)
        if m:
            by_code.setdefault(m.group(1), []).append(fname)

    removed = 0
    for code, files in by_code.items():
        if len(files) < 2:
            continue
        frames = []
        for f in files:
            try:
                df = standardize(pd.read_csv(os.path.join(folder, f),
                                             encoding="utf-8-sig"))
                if not df.empty:
                    frames.append((f, df))
            except Exception:
                continue
        if not frames:
            continue
        # 保留行数最多的文件名作为正式名；先规范化再合并，保证日期格式一致
        keep_name = max(frames, key=lambda x: len(x[1]))[0]
        merged = (pd.concat([df for _, df in frames], ignore_index=True)
                  .drop_duplicates(subset="交易日期", keep="last")
                  .sort_values("交易日期").reset_index(drop=True))
        if merged.empty:
            continue
        merged.to_csv(os.path.join(folder, keep_name),
                      index=False, encoding="utf-8-sig")
        for f, _ in frames:
            if f != keep_name:
                try:
                    os.remove(os.path.join(folder, f))
                    removed += 1
                    print(f"重复股票文件已合并: {f} -> {keep_name}")
                except OSError:
                    pass
    return removed


def get_stock_file_list(folder: str = None) -> Tuple[List[str], List[str]]:
    """获取指定文件夹下所有CSV文件的名称和路径"""
    folder = folder or AppConfig.STOCK_DATA_DIR
    os.makedirs(folder, exist_ok=True)
    files = sorted(f for f in os.listdir(folder) if f.endswith(".csv"))
    paths = [os.path.join(folder, f) for f in files]
    names = [os.path.splitext(f)[0] for f in files]
    return names, paths


def get_data_status() -> Dict:
    """本地数据总体状态：股票数、日期覆盖范围"""
    names, paths = get_stock_file_list()
    latest, earliest = None, None
    # 覆盖所有文件；抽样会隐藏未更新的股票，逐股截止由列表接口展示。
    sample = paths
    for p in sample:
        try:
            df = pd.read_csv(p, encoding="utf-8-sig", usecols=[0])
            col = df.columns[0]
            dates = df[col].astype(str)
            dmin, dmax = dates.min(), dates.max()
            earliest = dmin if earliest is None else min(earliest, dmin)
            latest = dmax if latest is None else max(latest, dmax)
        except Exception:
            continue
    return {
        "stock_count": len(names),
        "earliest_date": earliest,
        "latest_date": latest,
        "data_dir": AppConfig.STOCK_DATA_DIR,
    }


def list_stocks_detail() -> List[Dict]:
    """每只股票的基本信息（名称、行数、起止日期）"""
    names, paths = get_stock_file_list()
    result = []
    for name, p in zip(names, paths):
        try:
            df = pd.read_csv(p, encoding="utf-8-sig", usecols=[0])
            col = df.columns[0]
            dates = df[col].astype(str)
            result.append({
                "name": name,
                "rows": len(df),
                "start": dates.min(),
                "end": dates.max(),
            })
        except Exception:
            result.append({"name": name, "rows": 0, "start": "-", "end": "-"})
    return result

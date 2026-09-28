"""机器学习预测股票走势（网页版 v2）

改进：
1. TimeSeriesSplit 时序交叉验证，报告均值±标准差，比单次70/30切分更可信
2. 概率校准（CalibratedClassifierCV），让"预测上涨概率"更接近真实频率
3. 扩充特征：KDJ、布林带位置、MACD、量价相关等
"""
import numpy as np
import pandas as pd
from typing import Dict


class StockPredictor:
    """随机森林 + 概率校准的涨跌预测"""

    def __init__(self):
        self.model = None
        self._feature_cols = []
        self._train_report = {}

    def _create_features(self, df: pd.DataFrame) -> pd.DataFrame:
        from core.indicators import compute

        feats = pd.DataFrame(index=df.index)
        close = df["收盘价"]
        vol = df["成交量"]

        # 基础动量
        feats["return_1"] = close.pct_change(1)
        feats["return_3"] = close.pct_change(3)
        feats["return_5"] = close.pct_change(5)
        feats["return_10"] = close.pct_change(10)

        # 量比与量价
        feats["vol_ratio"] = vol / vol.rolling(5).mean()
        feats["vol_trend"] = vol.rolling(5).mean() / vol.rolling(20).mean()
        feats["pv_corr"] = close.pct_change().rolling(10).corr(vol.pct_change()).fillna(0.0)

        # 价格位置
        high_20 = df["最高价"].rolling(20).max()
        low_20 = df["最低价"].rolling(20).min()
        feats["price_position"] = (close - low_20) / (high_20 - low_20 + 1e-4)

        # 波动率
        feats["volatility_5"] = feats["return_1"].rolling(5).std()
        feats["volatility_10"] = feats["return_1"].rolling(10).std()

        # 均线偏离与排列
        ma5, ma10, ma20 = (close.rolling(w).mean() for w in (5, 10, 20))
        feats["ma5_bias"] = (close - ma5) / ma5
        feats["ma10_bias"] = (close - ma10) / ma10
        feats["ma20_bias"] = (close - ma20) / ma20
        feats["ma_alignment"] = ((ma5 > ma10).astype(int) + (ma10 > ma20).astype(int)) / 2

        # 指标特征（复用注册表）
        ind = compute(df, "KDJ")
        feats["kdj_k"] = ind["K"] / 100
        feats["kdj_j"] = ind["J"] / 100

        ind = compute(df, "BOLL")
        width = (ind["BOLL上轨"] - ind["BOLL下轨"]) / (ind["BOLL中轨"] + 1e-9)
        feats["boll_width"] = width
        feats["boll_pos"] = (close - ind["BOLL下轨"]) / (ind["BOLL上轨"] - ind["BOLL下轨"] + 1e-9)

        ind = compute(df, "MACD")
        feats["macd_dif_norm"] = ind["DIF"] / (close + 1e-9)
        feats["macd_bar_norm"] = ind["MACD柱"] / (close + 1e-9)

        rsi = compute(df, "RSI")
        feats["rsi"] = rsi["RSI14"] / 100

        self._feature_cols = feats.columns.tolist()
        return feats

    def train(self, df: pd.DataFrame, target_days: int = 5) -> Dict:
        """时序交叉验证训练 + 概率校准"""
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.model_selection import TimeSeriesSplit
            from sklearn.calibration import CalibratedClassifierCV
        except ImportError:
            return {"error": "请安装 scikit-learn"}

        features = self._create_features(df)
        close = df["收盘价"]
        future_return = close.shift(-target_days) / close - 1
        target = (future_return > 0).astype(int)

        valid_idx = features.dropna().index.intersection(target.dropna().index)
        X = features.loc[valid_idx].values
        y = target.loc[valid_idx].values

        if len(X) < 100:
            return {"error": f"数据不足，至少需要100条有效样本，当前{len(X)}条"}

        base_rf = RandomForestClassifier(
            n_estimators=200, max_depth=8, min_samples_leaf=5,
            class_weight="balanced_subsample", random_state=42, n_jobs=-1)

        # 时序交叉验证评估
        tscv = TimeSeriesSplit(n_splits=5)
        cv_scores = []
        for tr_idx, te_idx in tscv.split(X):
            m = RandomForestClassifier(
                n_estimators=200, max_depth=8, min_samples_leaf=5,
                class_weight="balanced_subsample", random_state=42, n_jobs=-1)
            m.fit(X[tr_idx], y[tr_idx])
            cv_scores.append(m.score(X[te_idx], y[te_idx]))

        cv_mean = float(np.mean(cv_scores))
        cv_std = float(np.std(cv_scores))

        # 用全部数据训练 + 保序回归概率校准
        self.model = CalibratedClassifierCV(base_rf, method="isotonic", cv=3)
        self.model.fit(X, y)

        importances = base_rf.fit(X, y).feature_importances_
        fi = dict(sorted(zip(self._feature_cols, importances),
                         key=lambda x: x[1], reverse=True)[:8])

        up_ratio = float(y.mean())
        self._train_report = {
            "cv_accuracy": round(cv_mean * 100, 1),
            "cv_std": round(cv_std * 100, 1),
            "cv_scores": [round(s * 100, 1) for s in cv_scores],
            "baseline_accuracy": round(max(up_ratio, 1 - up_ratio) * 100, 1),
            "samples": len(X),
            "feature_importance": {k: round(float(v), 4) for k, v in fi.items()},
        }
        return self._train_report

    def predict(self, df: pd.DataFrame) -> Dict:
        """预测未来走势（校准后的概率）"""
        if self.model is None:
            return {"error": "请先训练模型"}

        features = self._create_features(df)
        latest = features.iloc[-1:]
        if latest.isna().any().any():
            return {"error": "最新数据不足以计算特征"}

        proba = self.model.predict_proba(latest.values)[0]
        classes = list(self.model.classes_)
        up_prob = float(proba[classes.index(1)]) if 1 in classes else 0.5
        down_prob = 1 - up_prob

        trend = "上涨" if up_prob > 0.55 else ("下跌" if up_prob < 0.45 else "震荡")
        confidence = abs(up_prob - 0.5) * 2

        return {
            "up_prob": round(up_prob * 100, 1),
            "down_prob": round(down_prob * 100, 1),
            "trend": trend,
            "confidence": round(confidence * 100, 1),
            "train_report": self._train_report,
        }

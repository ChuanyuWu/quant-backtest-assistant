"""策略参数优化器 —— SVM代理模型 + 网格搜索回退（网页版 v2）

改进：
1. SVR训练前做标准化（StandardScaler），代理模型更可靠
2. 采样时强制参数约束（fast<slow, stop_loss<tp1<tp2），减少无效组合
3. 输出参数敏感性曲面数据（fast×slow收益网格），供前端画热力图
"""
import numpy as np
from typing import Dict, Tuple, List, Callable, Optional


class StrategyOptimizer:
    """使用SVM代理模型 + 真实回测进行参数优化"""

    def __init__(self, df, engine, buy_ratio=0.5):
        self.df = df
        self.engine = engine
        self.buy_ratio = buy_ratio

    def _random_params(self, n=30) -> List[Dict]:
        """随机生成n组满足约束的参数组合"""
        params_list = []
        attempts = 0
        while len(params_list) < n and attempts < n * 10:
            attempts += 1
            fast = int(np.random.choice([8, 10, 12, 14, 16]))
            slow = int(np.random.choice([20, 23, 26, 30, 35]))
            sl = float(np.random.choice([0.03, 0.05, 0.07, 0.10]))
            tp1 = float(np.random.choice([0.10, 0.15, 0.18, 0.25]))
            tp2 = float(np.random.choice([0.30, 0.40, 0.50, 0.60]))
            if fast >= slow or sl >= tp1 or tp1 >= tp2:
                continue
            params_list.append({
                "fast": fast, "slow": slow,
                "signal": int(np.random.choice([5, 7, 9, 12])),
                "stop_loss": sl, "take_profit_1": tp1,
                "take_profit_2": tp2,
                "sell_ratio": float(np.random.choice([0.3, 0.5, 0.7])),
            })
        return params_list

    @staticmethod
    def _features(p: Dict) -> List[float]:
        return [p["fast"], p["slow"], p["signal"],
                p["stop_loss"] * 100, p["take_profit_1"] * 100,
                p["take_profit_2"] * 100, p["sell_ratio"] * 100]

    def _evaluate(self, params: Dict) -> float:
        from core.strategies import strategy_macd_cross_timing
        from core.virtual_trade import StopLossParams

        buy_signals, sell_signals = strategy_macd_cross_timing(
            self.df, params["fast"], params["slow"], params["signal"]
        )
        if not buy_signals:
            return -999
        stop = StopLossParams(params["stop_loss"], params["take_profit_1"],
                              params["take_profit_2"], params["sell_ratio"])
        result = self.engine.run(self.df, buy_signals, sell_signals,
                                 self.buy_ratio, stop)
        return result.total_return

    def optimize(self, n_random=30, n_predict=10,
                 progress_callback: Optional[Callable] = None) -> Dict:
        """
        返回: {
            'best_params': dict, 'best_score': float,
            'sensitivity': {'fast': [...], 'slow': [...], 'scores': [[...]]},
            'method': 'svm' / 'grid',
            'train_samples': [{'params':..., 'score':...}]
        }
        """
        try:
            from sklearn.svm import SVR
            from sklearn.preprocessing import StandardScaler
            use_svm = True
        except ImportError:
            use_svm = False

        train_params = self._random_params(n_random)
        train_scores = []
        for i, p in enumerate(train_params):
            score = self._evaluate(p)
            train_scores.append(score)
            if progress_callback:
                progress_callback(i + 1, n_random + n_predict + 25, "随机搜索中...")

        valid = [(p, s) for p, s in zip(train_params, train_scores) if s > -999]
        if len(valid) < 10 or not use_svm:
            result = self._grid_fallback(progress_callback)
            result["train_samples"] = [
                {"params": p, "score": round(s, 2)} for p, s in valid
            ]
            return result

        valid_params, valid_scores = zip(*valid)

        X = np.array([self._features(p) for p in valid_params])
        y = np.array(valid_scores)

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        svm = SVR(kernel="rbf", C=10, gamma=0.1)
        svm.fit(X_scaled, y)

        candidates = self._random_params(200)
        X_cand = scaler.transform(np.array([self._features(p) for p in candidates]))
        pred_scores = svm.predict(X_cand)

        top_idx = np.argsort(pred_scores)[-n_predict:]
        best_params, best_score = None, -999
        for i, idx in enumerate(top_idx):
            p = candidates[idx]
            score = self._evaluate(p)
            if score > best_score:
                best_score, best_params = score, p
            if progress_callback:
                progress_callback(n_random + i + 1, n_random + n_predict + 25,
                                  f"SVM验证中 ({i+1}/{n_predict})...")

        sensitivity = self._sensitivity_surface(
            best_params, progress_callback, n_random + n_predict)

        return {
            "best_params": best_params,
            "best_score": round(best_score, 2),
            "method": "svm",
            "sensitivity": sensitivity,
            "train_samples": [{"params": p, "score": round(s, 2)}
                              for p, s in valid],
        }

    def _sensitivity_surface(self, base_params: Dict,
                             progress_callback=None, done=0) -> Dict:
        """以最优参数的止损参数为固定值，扫描 fast×slow 收益网格"""
        from core.strategies import strategy_macd_cross_timing
        from core.virtual_trade import StopLossParams

        if base_params is None:
            return {"fast": [], "slow": [], "scores": []}

        fast_range = [8, 10, 12, 14, 16]
        slow_range = [20, 23, 26, 30, 35]
        stop = StopLossParams(base_params["stop_loss"],
                              base_params["take_profit_1"],
                              base_params["take_profit_2"],
                              base_params["sell_ratio"])
        scores = []
        total = len(fast_range) * len(slow_range)
        count = 0
        for slow in slow_range:
            row = []
            for fast in fast_range:
                count += 1
                if fast >= slow:
                    row.append(None)
                else:
                    b, s = strategy_macd_cross_timing(
                        self.df, fast, slow, base_params["signal"])
                    if not b:
                        row.append(None)
                    else:
                        r = self.engine.run(self.df, b, s, self.buy_ratio, stop)
                        row.append(round(r.total_return, 2))
                if progress_callback:
                    progress_callback(done + count, done + total, "敏感性分析中...")
            scores.append(row)

        return {"fast": fast_range, "slow": slow_range, "scores": scores}

    def _grid_fallback(self, progress_callback=None) -> Dict:
        from itertools import product

        best_params, best_score = None, -999
        combos = [(f, s, sig, sl, tp1)
                  for f in [10, 12, 14] for s in [23, 26, 30]
                  for sig in [7, 9] for sl in [0.05, 0.07, 0.10]
                  for tp1 in [0.15, 0.18, 0.25] if sl < tp1]
        total = len(combos)
        for count, (fast, slow, signal, sl, tp1) in enumerate(combos, 1):
            params = {"fast": fast, "slow": slow, "signal": signal,
                      "stop_loss": sl, "take_profit_1": tp1,
                      "take_profit_2": 0.40, "sell_ratio": 0.5}
            score = self._evaluate(params)
            if score > best_score:
                best_score, best_params = score, params
            if progress_callback:
                progress_callback(count, total, f"网格搜索中 ({count}/{total})...")

        sensitivity = self._sensitivity_surface(best_params)
        return {"best_params": best_params, "best_score": round(best_score, 2),
                "method": "grid", "sensitivity": sensitivity}

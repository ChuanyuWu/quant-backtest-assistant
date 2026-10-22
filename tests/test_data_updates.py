import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest.mock import patch, Mock

import pandas as pd
import requests

from config.app_config import AppConfig
from core import fetcher, update_scheduler as scheduler
from core.data_io import atomic_write, file_lock, save_json
from core.update_state import SHANGHAI, patch_state, read_state, revision


def frame(dates=("2023-12-29", "2024-01-02"), close=10):
    return pd.DataFrame({"交易日期": pd.to_datetime(dates), "开盘价": close,
                         "最高价": close + 1, "最低价": close - 1,
                         "收盘价": close, "成交量": 100, "成交额": 100000})


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for key, value in {"STOCK_DATA_DIR": root / "stocks", "DATA_DIR": root / "data",
                           "CONFIG_FILE": root / "data/config.json"}.items():
            value.parent.mkdir(parents=True, exist_ok=True)
            self.enterContext(patch.object(AppConfig, key, str(value)))
        Path(AppConfig.STOCK_DATA_DIR).mkdir()
        self.enterContext(patch.object(fetcher.time, "sleep"))

    def seed(self, name="测试(600000)", df=None):
        path = Path(AppConfig.STOCK_DATA_DIR) / (name + ".csv")
        (frame(("2023-12-29",)) if df is None else df).to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def run_update(self, **kwargs):
        return fetcher.update_local_stocks(end="20240102", **kwargs)

    def test_permission_stops_pool_without_retries(self):
        for name in ("测试(600000)", "另一只(600001)"):
            self.seed(name)
        with patch.object(fetcher, "_tushare_fetch_one", side_effect=fetcher.DataError(
                "adj_factor", "permission", "积分不足")) as call:
            result = self.run_update(source="tushare")
        self.assertEqual(call.call_count, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["items"][1]["name"], "测试(600000)")
        self.assertTrue(result["items"][1]["skipped"])

    def test_empty_and_stale_are_not_success(self):
        path = self.seed()
        before = path.read_bytes()
        for returned in (pd.DataFrame(), frame(("2023-12-29",))):
            with self.subTest(empty=returned.empty), patch.object(fetcher, "_akshare_fetch_one", return_value=returned):
                result = self.run_update()
                self.assertEqual(result["status"], "no_new_data")
                self.assertFalse(result["ok"])
                self.assertEqual(path.read_bytes(), before)

    def test_full_refresh_keeps_earlier_history(self):
        self.seed()
        with patch.object(fetcher, "_akshare_fetch_one", return_value=frame()) as call:
            result = self.run_update()
        self.assertEqual(call.call_args.args[1], "20231229")
        self.assertEqual(result["status"], "success")
        self.assertIn("last_success", read_state())

    def test_rejects_truncated_or_invalid_response(self):
        path = self.seed()
        before = path.read_bytes()
        invalid = frame()
        invalid.loc[0, "最高价"] = 1
        for returned in (frame(("2024-01-02",)), invalid):
            with self.subTest(rows=len(returned)), patch.object(fetcher, "_akshare_fetch_one", return_value=returned):
                result = self.run_update()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(path.read_bytes(), before)

    def test_partial_success_and_full_failure(self):
        self.seed()
        self.seed("另一只(600001)")
        with patch.object(fetcher, "_akshare_fetch_one", side_effect=[frame(), TimeoutError("timeout")]):
            result = self.run_update()
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertNotIn("last_success", read_state())

    def test_already_latest_skips_provider(self):
        self.seed(df=frame())
        with patch.object(fetcher, "_akshare_fetch_one") as call:
            self.assertEqual(self.run_update()["status"], "up_to_date")
            call.assert_not_called()

    def test_interrupted_atomic_write_keeps_alias_and_old_file(self):
        path = self.seed()
        old = path.read_bytes()
        with patch("core.data_io.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                fetcher._save_full("新名称(600000)", frame())
        self.assertEqual(path.read_bytes(), old)
        self.assertFalse(list(path.parent.glob(".update-*")))

    def test_concurrent_jobs_fetch_once_for_same_code(self):
        self.seed()
        results = []
        with patch.object(fetcher, "_akshare_fetch_one", return_value=frame()) as call:
            workers = [threading.Thread(target=lambda: results.append(self.run_update())) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(call.call_count, 1)
        self.assertEqual({r["status"] for r in results}, {"success", "up_to_date"})

    def test_retry_categories_and_redaction(self):
        for exc, category, count in ((RuntimeError("token无效"), "authentication", 1),
                                     (RuntimeError("权限不足"), "permission", 1),
                                     (RuntimeError("每分钟最多访问50次"), "rate_limit", 3),
                                     (requests.Timeout("timeout"), "network", 3),
                                     (ValueError("bad schema"), "invalid_data", 1)):
            call = Mock(side_effect=exc)
            with self.subTest(category=category), self.assertRaises(fetcher.DataError) as caught:
                fetcher._retry(call, interface="daily")
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(call.call_count, count)
        save_json(AppConfig.CONFIG_FILE, {"tushare_token": "secret-value"})
        self.assertNotIn("secret-value", fetcher.redact("failed secret-value"))

    def test_dates_in_provider_urls_are_not_http_status_codes(self):
        for date in ("20240101", "20240301", "20240429"):
            error = RuntimeError("HTTPSConnectionPool(host='push2his.eastmoney.com', port=443): "
                                 "Max retries exceeded with url: /api?beg=" + date +
                                 " (Caused by ProxyError('Unable to connect to proxy'))")
            self.assertEqual(fetcher.classify(error), "network")
        for code, category in ((401, "authentication"), (403, "permission"), (429, "rate_limit")):
            response = requests.Response()
            response.status_code = code
            self.assertEqual(fetcher.classify(requests.HTTPError("request failed", response=response)), category)
            self.assertEqual(fetcher.classify(RuntimeError(f"HTTP {code}")), category)

    def test_realistic_eastmoney_proxy_error_runs_sina_fallback(self):
        error = RuntimeError("HTTPSConnectionPool: /api?beg=20240101 (Caused by ProxyError('Unable to connect to proxy'))")
        sina = pd.DataFrame({"date": ["2024-01-02"], "open": [10], "high": [11],
                             "low": [9], "close": [10], "volume": [10000], "amount": [100000]})
        with patch.object(fetcher, "_ak_call", side_effect=[error, error, error, sina]) as call:
            out = fetcher._akshare_fetch_one("000001", "20240101", "20240102")
        self.assertEqual(call.call_count, 4)
        self.assertEqual(out.attrs["interface"], "stock_zh_a_daily")

    def test_independent_sina_universe_filters_and_deduplicates(self):
        response = Mock()
        response.json.return_value = [
            {"code": "601398", "name": "工商银行"},
            {"code": "601398", "name": "工商银行"},
            {"code": "000001", "name": "平安银行"},
            {"code": "000002", "name": "ST测试"},
            {"code": "688001", "name": "科创板"}]
        with patch.object(fetcher.requests, "get", return_value=response) as get, patch.object(
                fetcher, "_ak_call") as ak:
            result = fetcher._akshare_stock_list(200)
        self.assertEqual([row["code"] for row in result], ["601398", "000001"])
        ak.assert_not_called()
        self.assertEqual(get.call_args.kwargs["timeout"], (10, 20))

    def test_bulk_job_reuses_working_backup_source(self):
        self.seed()
        self.seed("另一只(600001)")
        quotes = frame()
        quotes.attrs["interface"] = "stock_zh_a_daily"
        with patch.object(fetcher, "_akshare_fetch_one", return_value=quotes) as call:
            self.assertTrue(self.run_update()["ok"])
        self.assertEqual(call.call_args_list[1].kwargs, {"prefer_sina": True})

    def test_diagnosis_distinguishes_daily_and_adjustment_permission(self):
        with patch.object(fetcher, "_tushare_call", side_effect=[frame(), fetcher.DataError(
                "adj_factor", "permission", "需要2000积分")]):
            result = fetcher.diagnose("tushare")
        self.assertTrue(result["checks"][0]["ok"])
        self.assertEqual(result["checks"][1]["category"], "permission")
        self.assertFalse(result["ok"])

    def test_tushare_qfq_uses_complete_factors_and_normalizes_amount(self):
        daily = pd.DataFrame({"trade_date": ["20240102", "20231229"], "open": [20, 10],
                              "high": [21, 11], "low": [19, 9], "close": [20, 10],
                              "vol": [100, 100], "amount": [10, 10]})
        factors = pd.DataFrame({"trade_date": ["20240102", "20231229"], "adj_factor": [2, 1]})
        with patch.object(fetcher, "_tushare_call", side_effect=[daily, factors]):
            out = fetcher._tushare_fetch_one("600000.SH", "20231229", "20240102")
        self.assertEqual(out["收盘价"].tolist(), [5, 20])
        self.assertEqual(out["成交额"].tolist(), [10000, 10000])
        with patch.object(fetcher, "_tushare_call", side_effect=[daily, factors.iloc[:1]]):
            with self.assertRaises(fetcher.DataError):
                fetcher._tushare_fetch_one("600000.SH", "20231229", "20240102")

    def test_akshare_fallback_keeps_qfq_and_converts_volume(self):
        sina = pd.DataFrame({"date": ["2024-01-02"], "open": [10], "high": [11],
                             "low": [9], "close": [10], "volume": [10000], "amount": [100000]})
        with patch.object(fetcher, "_ak_call", side_effect=[pd.DataFrame(), sina]) as call:
            out = fetcher._akshare_fetch_one("600000", "20240101", "20240102")
        self.assertEqual(out["成交量"].iloc[0], 100)
        self.assertEqual(call.call_args.kwargs["adjust"], "qfq")
        self.assertEqual(out.attrs["interface"], "stock_zh_a_daily")

    def test_revision_changes_when_history_changes_without_new_dates(self):
        self.assertNotEqual(revision(frame()), revision(frame(close=11)))

    def test_content_change_invalidates_backtest_and_ml_reuse(self):
        from server import main
        main._ml_models.clear()
        self.addCleanup(main._ml_models.clear)
        with patch.object(AppConfig, "BACKTEST_CACHE_FILE", str(Path(self.temp.name) / "backtests.json")), patch.object(
                main, "_get_df", return_value=frame()) as get_df, patch.object(
                main, "run_timing_strategy", return_value=([], [])), patch.object(
                main, "_get_benchmark", return_value=(frame(), "test")), patch.object(
                main, "ExecutionEngine") as engine, patch.object(main, "StockPredictor") as model:
            engine.return_value.run.return_value.to_dict.return_value = {"total_return": 0}
            model.return_value.train.return_value = {}
            model.return_value.predict.return_value = {"up_prob": 50}
            main.api_backtest({"name": "测试(600000)"})
            self.assertTrue(main.api_backtest({"name": "测试(600000)"})["from_cache"])
            main.api_ml_predict({"name": "测试(600000)"})
            main.api_ml_predict({"name": "测试(600000)"})
            self.assertEqual(model.return_value.train.call_count, 1)
            get_df.return_value = frame(close=11)  # Same rows and dates, changed historical prices.
            self.assertFalse(main.api_backtest({"name": "测试(600000)"})["from_cache"])
            main.api_ml_predict({"name": "测试(600000)"})
            self.assertEqual(model.return_value.train.call_count, 2)
            self.assertEqual(engine.return_value.run.call_count, 2)

    def test_os_lock_excludes_another_process(self):
        path = str(Path(self.temp.name) / "process.lock")
        script = ("import sys; from core.data_io import file_lock; "
                  "ctx=file_lock(sys.argv[1],blocking=False); "
                  "print(ctx.__enter__()); ctx.__exit__(None,None,None)")
        with file_lock(path):
            result = subprocess.run([sys.executable, "-c", script, path],
                                    capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(result.stdout.strip(), "False")

    def test_scheduler_holidays_retry_intervals_and_restart(self):
        save_json(AppConfig.CONFIG_FILE, {"auto_update": True, "data_source": "akshare"})
        day = dt.datetime(2024, 1, 2, 17, 30, tzinfo=SHANGHAI)
        days = ["2023-12-29", "2024-01-02"]  # Jan 1 holiday
        with patch.object(scheduler, "trading_days", return_value=days), patch.object(
                fetcher, "update_local_stocks", return_value={"ok": False, "status": "no_new_data"}) as call:
            scheduler.tick(day)
            self.assertEqual(read_state()["scheduler"]["next_attempt"], (day + dt.timedelta(minutes=15)).isoformat())
            scheduler.tick(day + dt.timedelta(minutes=14))
            self.assertEqual(call.call_count, 1)
            scheduler.tick(day + dt.timedelta(minutes=15))
            self.assertEqual(read_state()["scheduler"]["next_attempt"], (day + dt.timedelta(minutes=45)).isoformat())
            scheduler.tick(day + dt.timedelta(minutes=45))
            self.assertEqual(read_state()["scheduler"]["next_attempt"], (day + dt.timedelta(minutes=105)).isoformat())
            call.return_value = {"ok": True, "status": "success"}
            scheduler.tick(day + dt.timedelta(minutes=105))
            self.assertEqual(read_state()["scheduler"]["completed_date"], "20240102")
            scheduler.tick(day + dt.timedelta(minutes=106))
            self.assertEqual(call.call_count, 4)

    def test_scheduler_before_close_and_holiday_catchup(self):
        save_json(AppConfig.CONFIG_FILE, {"auto_update": True})
        with patch.object(scheduler, "trading_days", return_value=["2023-12-29", "2024-01-02"]), patch.object(
                fetcher, "update_local_stocks", return_value={"ok": True, "status": "up_to_date"}) as call:
            scheduler.tick(dt.datetime(2024, 1, 1, 18, tzinfo=SHANGHAI))
            self.assertEqual(call.call_args.kwargs["end"], "20231229")
            scheduler.tick(dt.datetime(2024, 1, 2, 17, 29, tzinfo=SHANGHAI))
            self.assertEqual(call.call_count, 1)
            scheduler.tick(dt.datetime(2024, 1, 2, 17, 30, tzinfo=SHANGHAI))
            self.assertEqual(call.call_args.kwargs["end"], "20240102")

    def test_calendar_failure_is_visible_and_retried(self):
        save_json(AppConfig.CONFIG_FILE, {"auto_update": True})
        with patch.object(scheduler, "trading_days", side_effect=TimeoutError("calendar timeout")):
            scheduler.tick(dt.datetime(2024, 1, 2, 18, tzinfo=SHANGHAI))
        self.assertIn("calendar timeout", read_state()["scheduler"]["error"])

    def test_calendar_cache_avoids_network(self):
        save_json(os.path.join(AppConfig.DATA_DIR, "trade_calendar.json"),
                  {"year": 2024, "days": ["2024-01-02", "2024-12-31"]})
        with patch("core.provider_worker.call") as call:
            self.assertEqual(len(scheduler.trading_days(dt.date(2024, 1, 2))), 2)
            call.assert_not_called()

    def test_http_error_and_secret_never_reach_diagnosis(self):
        save_json(AppConfig.CONFIG_FILE, {"tushare_token": "secret-value"})
        response = Mock()
        response.json.return_value = {"code": 2002, "msg": "token secret-value 无效"}
        with patch.object(fetcher.requests, "post", return_value=response) as post:
            result = fetcher.diagnose("tushare")
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"][0]["category"], "authentication")
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 30))

    def test_api_settings_validation_and_business_failure_result(self):
        from fastapi.testclient import TestClient
        from server.main import app
        client = TestClient(app)
        self.assertEqual(client.post("/api/settings", json={"data_source": "tushare"}).status_code, 200)
        self.assertEqual(client.get("/api/settings").json()["data_source"], "tushare")
        self.assertEqual(client.post("/api/data/fetch", json={"source": "invalid"}).status_code, 422)
        self.assertEqual(client.post("/api/data/fetch", json={"limit": -1}).status_code, 422)
        self.seed()
        with patch("server.tasks.create_task", return_value="mock-task"):
            response = client.post("/api/data/fetch", json={"source": "tushare", "end": "20240102"})
            self.assertEqual(response.json()["task_id"], "mock-task")
        from server import tasks
        with patch.object(fetcher, "_akshare_fetch_one", return_value=pd.DataFrame()):
            task = tasks.create_task(lambda progress=None: self.run_update(progress=progress))
            for _ in range(100):
                result = tasks.get_task(task)
                if result["status"] != "running":
                    break
                threading.Event().wait(0.01)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["outcome"], "no_new_data")
        self.assertFalse(result["result"]["ok"])

    def test_automatic_target_uses_latest_published_trading_day(self):
        self.seed()
        with patch.object(fetcher, "now", return_value=dt.datetime(2024, 1, 3, 12, tzinfo=SHANGHAI)), patch.object(
                scheduler, "trading_days", return_value=["2023-12-29", "2024-01-02", "2024-01-03"]), patch.object(
                fetcher, "_akshare_fetch_one", return_value=frame()) as call:
            result = fetcher.update_local_stocks()
        self.assertEqual(result["target_date"], "20240102")
        self.assertEqual(call.call_args.args[2], "20240102")

    def test_no_more_than_three_retries_per_day(self):
        save_json(AppConfig.CONFIG_FILE, {"auto_update": True})
        day = dt.datetime(2024, 1, 2, 17, 30, tzinfo=SHANGHAI)
        with patch.object(scheduler, "trading_days", return_value=["2024-01-02"]), patch.object(
                fetcher, "update_local_stocks", return_value={"ok": False, "status": "failed"}) as call:
            for minutes in (0, 15, 45, 105, 180, 300):
                scheduler.tick(day + dt.timedelta(minutes=minutes))
            self.assertEqual(call.call_count, 4)
            scheduler.tick(day + dt.timedelta(days=1))
            self.assertEqual(call.call_count, 5)

    def test_scheduler_does_not_duplicate_concurrent_run(self):
        save_json(AppConfig.CONFIG_FILE, {"auto_update": True})
        with file_lock(os.path.join(AppConfig.DATA_DIR, "scheduler.lock")):
            with patch.object(scheduler, "trading_days") as call:
                worker = threading.Thread(target=lambda: scheduler.tick(
                    dt.datetime(2024, 1, 2, 18, tzinfo=SHANGHAI)))
                worker.start()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()


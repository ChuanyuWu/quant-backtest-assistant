"""Restart-safe Shanghai close-of-day scheduler with a cached trading calendar."""
import datetime as dt
import os

from config.app_config import AppConfig
from core import fetcher
from core.data_io import file_lock, load_json, save_json
from core.settings import get_settings
from core.update_state import now, read_state, patch_state

RETRY_MINUTES = (15, 30, 60)


def trading_days(today):
    path = os.path.join(AppConfig.DATA_DIR, "trade_calendar.json")
    cached = load_json(path, {})
    # An annual refresh is enough for the provider's historical calendar. Never
    # infer holidays from weekdays if the calendar is unavailable.
    if cached.get("year") != today.year:
        from core.provider_worker import call
        frame = fetcher._retry(lambda: call("tool_trade_date_hist_sina"),
                               times=2, interface="tool_trade_date_hist_sina")
        days = sorted(str(day)[:10] for day in frame["trade_date"])
        if not days or days[-1] < today.isoformat():
            raise ValueError("交易日历未覆盖当前日期")
        cached = {"year": today.year, "days": days}
        save_json(path, cached)
    return cached["days"]


def tick(current=None):
    current = current or now()
    if current.tzinfo is None:
        raise ValueError("调度时间必须包含时区")
    current = current.astimezone(now().tzinfo)
    if not get_settings().get("auto_update"):
        return
    with file_lock(os.path.join(AppConfig.DATA_DIR, "scheduler.lock"), blocking=False) as acquired:
        if not acquired:
            return
        state = read_state()
        scheduled = state.get("scheduler", {})
        if scheduled.get("next_attempt") and current < dt.datetime.fromisoformat(scheduled["next_attempt"]):
            return
        try:
            days = trading_days(current.date())
        except Exception as exc:
            patch_state(scheduler={**scheduled, "error": fetcher.redact(exc),
                        "next_attempt": (current + dt.timedelta(minutes=15)).isoformat()})
            return
        cutoff = current.date() if current.time() >= dt.time(17, 30) else current.date() - dt.timedelta(days=1)
        eligible = [day for day in days if day <= cutoff.isoformat()]
        if not eligible:
            return
        target = eligible[-1].replace("-", "")
        if scheduled.get("completed_date", "") >= target:
            return
        attempts = scheduled.get("attempts", 0) if scheduled.get("target_date") == target else 0
        # After the three retries, leave a visible failure and resume on the next
        # Shanghai day (including a weekend catch-up after Friday failure).
        if attempts >= 4 and scheduled.get("attempt_day") == current.date().isoformat():
            return
        if scheduled.get("attempt_day") != current.date().isoformat():
            attempts = 0
        scheduler = {"target_date": target, "attempts": attempts + 1,
                     "attempt_day": current.date().isoformat(),
                     "completed_date": scheduled.get("completed_date", ""),
                     "next_attempt": (current + dt.timedelta(minutes=RETRY_MINUTES[min(attempts, 2)])).isoformat()}
        # Persist before network IO: a restart will not immediately duplicate a run.
        patch_state(scheduler=scheduler)
        try:
            result = fetcher.update_local_stocks(get_settings().get("data_source", "akshare"), end=target)
        except Exception as exc:
            scheduler["error"] = fetcher.redact(exc)
        else:
            scheduler["status"] = result["status"]
            if result["ok"]:
                scheduler["completed_date"] = target
                scheduler["next_attempt"] = None
        patch_state(scheduler=scheduler)

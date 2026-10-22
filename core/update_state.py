"""Persistent update status and content revisions, shared by manual and scheduled jobs."""
import datetime as dt
import hashlib
import os

import pandas as pd

from config.app_config import AppConfig
from core.data_io import file_lock, load_json, save_json

SHANGHAI = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


def now():
    return dt.datetime.now(SHANGHAI)


def state_path():
    return os.path.join(AppConfig.DATA_DIR, "updates.json")


def read_state():
    return load_json(state_path(), {})


def patch_state(**updates):
    with file_lock(state_path() + ".lock"):
        state = read_state()
        state.update(updates)
        save_json(state_path(), state)
        return state


def revision(df):
    """Hash content, not just dates/row count (qfq history can change)."""
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()

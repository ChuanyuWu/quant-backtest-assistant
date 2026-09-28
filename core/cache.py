"""回测结果缓存：按参数哈希存取，避免重复计算"""
import sys
import os
import json
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.app_config import AppConfig
from core.data_io import load_json, save_json

_MAX_ENTRIES = 200


def _hash_params(params: dict) -> str:
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_cached_backtest(params: dict):
    cache = load_json(AppConfig.BACKTEST_CACHE_FILE, {})
    return cache.get(_hash_params(params))


def set_cached_backtest(params: dict, result: dict):
    cache = load_json(AppConfig.BACKTEST_CACHE_FILE, {})
    if len(cache) >= _MAX_ENTRIES:
        # 简单FIFO清理
        for key in list(cache.keys())[: len(cache) - _MAX_ENTRIES + 1]:
            cache.pop(key)
    cache[_hash_params(params)] = result
    save_json(AppConfig.BACKTEST_CACHE_FILE, cache)


def clear_cache():
    save_json(AppConfig.BACKTEST_CACHE_FILE, {})

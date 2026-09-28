"""数据持久化工具：JSON读写、文件名清理"""
import os
import json
import re
from typing import Any


def save_json(filepath: str, data: Any):
    """保存数据到JSON文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(filepath: str, default: Any = None) -> Any:
    """从JSON文件加载数据，文件不存在则返回默认值"""
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default if default is not None else {}


def sanitize_filename(filename: str) -> str:
    """去除文件名中的非法字符"""
    illegal_chars = r'[\\/*?:"<>|]'
    sanitized = re.sub(illegal_chars, '_', filename).strip('. ')
    return sanitized or "unnamed"

"""数据持久化工具：JSON读写、文件名清理"""
import os
import json
import re
import tempfile
import threading
from contextlib import contextmanager
from typing import Any


def save_json(filepath: str, data: Any):
    """保存数据到JSON文件"""
    atomic_write(filepath, lambda f: json.dump(data, f, ensure_ascii=False, indent=2))


def atomic_write(filepath, writer, encoding="utf-8"):
    """Write beside the destination, then atomically publish; preserve old data on failure."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".update-", dir=os.path.dirname(filepath))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, filepath)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


_local_locks = {}
_lock_guard = threading.Lock()


@contextmanager
def file_lock(path, blocking=True):
    """Thread + OS advisory lock; released by OS even if a worker crashes."""
    with _lock_guard:
        lock = _local_locks.setdefault(os.path.abspath(path), threading.RLock())
    acquired = lock.acquire(blocking=blocking)
    stream = None
    os_locked = False
    try:
        if acquired:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            stream = open(path, "a+b")
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    os_locked = True
                    break
                except OSError:
                    if not blocking:
                        break
                    import time
                    time.sleep(0.05)
        yield acquired and os_locked
    finally:
        if os_locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        if stream is not None:
            stream.close()
        if acquired:
            lock.release()


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

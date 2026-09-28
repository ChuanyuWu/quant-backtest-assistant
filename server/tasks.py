"""异步任务管理器：长任务后台线程执行，前端轮询进度"""
import uuid
import threading
import traceback
from typing import Callable, Dict

_tasks: Dict[str, Dict] = {}
_lock = threading.Lock()


def create_task(func: Callable, *args, **kwargs) -> str:
    """创建并启动任务，返回task_id。func需接受 progress 回调参数。"""
    task_id = uuid.uuid4().hex[:12]
    with _lock:
        _tasks[task_id] = {
            "status": "running",   # running / done / error
            "current": 0, "total": 0,
            "message": "准备中...",
            "result": None, "error": None,
        }

    def progress(cur, total, msg):
        with _lock:
            if task_id in _tasks:
                _tasks[task_id].update(current=cur, total=total, message=msg)

    def runner():
        try:
            result = func(*args, progress=progress, **kwargs)
            with _lock:
                _tasks[task_id].update(status="done", result=result,
                                       message="完成")
        except Exception as e:
            traceback.print_exc()
            with _lock:
                _tasks[task_id].update(status="error", error=str(e),
                                       message=f"出错: {e}")

    threading.Thread(target=runner, daemon=True).start()
    return task_id


def get_task(task_id: str) -> Dict:
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return {"status": "not_found"}
        return dict(task)


def cleanup_tasks():
    """清理已完成的老任务（保留最近50个）"""
    with _lock:
        done = [k for k, v in _tasks.items() if v["status"] in ("done", "error")]
        for k in done[:-50]:
            _tasks.pop(k, None)

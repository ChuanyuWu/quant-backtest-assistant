"""量化交易系统网页版 —— 一键启动"""
import sys
import os
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.app_config import AppConfig


def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://{AppConfig.HOST}:{AppConfig.PORT}")


if __name__ == "__main__":
    print("=" * 50)
    print("  量化交易策略回测系统（网页版）")
    print(f"  浏览器访问: http://{AppConfig.HOST}:{AppConfig.PORT}")
    print("  关闭此窗口即停止服务")
    print("=" * 50)
    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    from server.main import app
    uvicorn.run(app, host=AppConfig.HOST, port=AppConfig.PORT, log_level="warning")

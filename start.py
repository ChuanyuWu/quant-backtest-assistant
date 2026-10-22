"""量化交易系统网页版 —— 一键启动"""
import sys
import os
import threading
import time
import webbrowser
import socket

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.app_config import AppConfig


def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://{AppConfig.HOST}:{AppConfig.PORT}")


def existing_project(url):
    """Only reuse this checkout's server; never stop an unrelated port owner."""
    with requests.Session() as session:
        session.trust_env = False
        for attempt in range(3):
            try:
                response = session.get(url + "/api/meta", timeout=1)
                response.raise_for_status()
                folder = response.json().get("data_status", {}).get("data_dir", "")
                return bool(folder) and os.path.normcase(os.path.realpath(folder)) == os.path.normcase(
                    os.path.realpath(AppConfig.STOCK_DATA_DIR))
            except (requests.RequestException, ValueError, AttributeError):
                if attempt < 2:
                    time.sleep(0.2)
    return False


def main():
    url = f"http://{AppConfig.HOST}:{AppConfig.PORT}"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((AppConfig.HOST, AppConfig.PORT))
    except OSError as exc:
        listener.close()
        if existing_project(url):
            print(f"服务已在运行，直接打开页面：{url}")
            print("本次未启动新服务；关闭此窗口不影响已运行的服务。")
            webbrowser.open(url)
            return 0
        print(f"无法启动：{AppConfig.HOST}:{AppConfig.PORT} 不可用（{exc}）。")
        print("该端口未识别为本项目服务，请检查占用程序。")
        return 1
    print("=" * 50)
    print("  量化交易策略回测系统（网页版）")
    print(f"  浏览器访问: http://{AppConfig.HOST}:{AppConfig.PORT}")
    print("  关闭此窗口即停止服务")
    print("=" * 50)
    try:
        import uvicorn
        from server.main import app
        threading.Thread(target=open_browser, daemon=True).start()
        config = uvicorn.Config(app, host=AppConfig.HOST, port=AppConfig.PORT, log_level="warning")
        # Pass the socket we bound above so simultaneous launches cannot race.
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

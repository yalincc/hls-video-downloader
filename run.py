"""
视频下载器 — 一键启动
双击 / python run.py → Web 界面（自动启动 worker + 服务）
python run.py <链接>   → 命令行直接下载
"""
import sys
import os
import subprocess
import webbrowser
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def start_worker():
    """后台 Worker（独立进程，更稳定）"""
    worker_script = BASE_DIR / "web" / "worker.py"
    return subprocess.Popen(
        [sys.executable, "-u", str(worker_script)],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def start_server(worker_proc):
    """Web 服务"""
    from web.server import app

    def open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:5001")

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n  [OK] Worker 已启动 (PID: {worker_proc.pid})")
    print(f"  浏览器打开: http://127.0.0.1:5001")
    print(f"  粘贴链接即可下载")
    print(f"  按 Ctrl+C 停止\n")

    try:
        app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
    finally:
        worker_proc.terminate()
        worker_proc.wait(timeout=5)
        print("  已停止。")


def start_cli():
    """命令行模式"""
    sys.argv = [sys.argv[0]] + sys.argv[1:]
    from download import main
    main()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        start_cli()
    else:
        print("=" * 50)
        print("  视频下载器")
        print("=" * 50)
        worker = start_worker()
        start_server(worker)

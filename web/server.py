"""
Web 管理界面 — Flask 服务

功能:
- 添加视频链接到下载队列
- 查看实时进度
- 停止正在下载的任务

启动: python web/server.py
访问: http://localhost:5001
"""

import json
import re
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

# 路径
BASE_DIR = Path(__file__).resolve().parent.parent
QUEUE_FILE = BASE_DIR / "queue.txt"
DOWNLOAD_DIR = BASE_DIR / "downloads"
STATUS_DIR = DOWNLOAD_DIR / ".status"
DONE_LOG = DOWNLOAD_DIR / ".done.txt"
STOP_DIR = DOWNLOAD_DIR / ".stop_signals"

DOWNLOAD_DIR.mkdir(exist_ok=True)
STATUS_DIR.mkdir(exist_ok=True)
STOP_DIR.mkdir(exist_ok=True)
QUEUE_FILE.touch(exist_ok=True)
DONE_LOG.touch(exist_ok=True)

ENCODING = "utf-8"

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/add", methods=["POST"])
def api_add():
    data = request.get_json()
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "empty"}), 400

    existing = set()
    if QUEUE_FILE.exists():
        existing = set(l.strip() for l in QUEUE_FILE.read_text(encoding=ENCODING, errors="replace").splitlines() if l.strip())

    added = 0
    with open(QUEUE_FILE, "a", encoding=ENCODING) as f:
        for url in urls:
            url = url.strip()
            if url and url.startswith("http") and url not in existing:
                f.write(url + "\n")
                existing.add(url)
                added += 1

    return jsonify({"count": added})


@app.route("/api/stop/<task_id>", methods=["POST"])
def api_stop(task_id):
    (STOP_DIR / f"{task_id}.stop").touch()
    return jsonify({"ok": True})


@app.route("/api/log/clear", methods=["POST"])
def api_clear_log():
    DONE_LOG.write_text("", encoding=ENCODING)
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    queue_lines = []
    if QUEUE_FILE.exists():
        queue_lines = [l.strip() for l in QUEUE_FILE.read_text(encoding=ENCODING, errors="replace").splitlines() if l.strip()]

    files_count = len(list(DOWNLOAD_DIR.glob("*.mp4")))

    tasks = []
    active = 0
    for sf in sorted(STATUS_DIR.glob("*.json"), reverse=True)[:20]:
        try:
            data = json.loads(sf.read_text(encoding=ENCODING, errors="replace"))
            task_id = sf.stem
            data["id"] = task_id
            if (STOP_DIR / f"{task_id}.stop").exists() and data.get("status") == "downloading":
                data["status"] = "stopping"
            if data.get("status") in ("downloading", "extracting"):
                active += 1
            tasks.append(data)
        except Exception:
            continue

    log_lines = DONE_LOG.read_text(encoding=ENCODING, errors="replace").splitlines()[-80:] if DONE_LOG.exists() else []
    parts = []
    for line in log_lines:
        if "[DONE]" in line or "[SKIP]" in line:
            parts.append(f'<span class="ok">{line}</span>')
        elif "[FAIL]" in line or "[WARN]" in line or "[ERROR]" in line or "失败" in line:
            parts.append(f'<span class="err">{line}</span>')
        elif "[WAIT]" in line or "[STOP]" in line:
            parts.append(f'<span class="warn">{line}</span>')
        elif "[DL]" in line or "[FETCH]" in line or "[MERGE]" in line or "[CONVERT]" in line:
            parts.append(f'<span class="info">{line}</span>')
        else:
            parts.append(line)

    return jsonify({
        "queue": len(queue_lines),
        "files": files_count,
        "active": active,
        "tasks": tasks,
        "logHtml": "<br>".join(parts) if parts else "等待任务...",
    })


if __name__ == "__main__":
    print(f"\n视频下载 Web 管理")
    print(f"   队列: {QUEUE_FILE}")
    print(f"   输出: {DOWNLOAD_DIR}")
    print(f"   启动 worker: python web/worker.py")
    print(f"   访问: http://localhost:5001\n")
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)

"""
视频下载器 Web 服务
网页负责接收 URL，写入队列文件；后台 worker 负责提取 m3u8 并下载。
"""
import json
import os
import re
import signal
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

# ---- 路径：基于本文件位置 ----
BASE_DIR = Path(__file__).resolve().parent.parent
QUEUE_FILE = BASE_DIR / "queue.txt"
OUTPUT_DIR = BASE_DIR / "video_download"
DONE_LOG = OUTPUT_DIR / ".done.txt"
STOP_SIGNAL_DIR = OUTPUT_DIR / ".stop_signals"

OUTPUT_DIR.mkdir(exist_ok=True)
STOP_SIGNAL_DIR.mkdir(exist_ok=True)
QUEUE_FILE.touch(exist_ok=True)
DONE_LOG.touch(exist_ok=True)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


# ========== API ==========

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/add", methods=["POST"])
def api_add():
    """添加 URL 到下载队列"""
    data = request.get_json()
    urls = data.get("urls", [])
    output_dir = data.get("outputDir", "").strip()

    if not urls:
        return jsonify({"error": "empty"}), 400

    existing = set()
    if QUEUE_FILE.exists():
        existing = set(q for q in QUEUE_FILE.read_text().splitlines() if q.strip())

    # 如果指定了输出目录，存一份配置
    if output_dir:
        cfg = {"output_dir": output_dir}
        (BASE_DIR / ".web_config.json").write_text(json.dumps(cfg))

    added = 0
    with open(QUEUE_FILE, "a") as f:
        for url in urls:
            url = url.strip()
            if url and url.startswith("http") and url not in existing:
                f.write(url + "\n")
                existing.add(url)
                added += 1

    return jsonify({"count": added})


@app.route("/api/stop/<task_id>", methods=["POST"])
def api_stop(task_id):
    """发送停止信号给指定下载任务"""
    signal_file = STOP_SIGNAL_DIR / f"{task_id}.stop"
    signal_file.touch()
    log(f"⏹ 停止信号: video_{task_id}")
    return jsonify({"ok": True})


@app.route("/api/log/clear", methods=["POST"])
def api_log_clear():
    """清空运行日志"""
    DONE_LOG.write_text("")
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    """获取当前状态：队列长度、已下载文件数、运行中任务、日志"""
    # 队列数量
    queue_lines = [l for l in QUEUE_FILE.read_text().splitlines() if l.strip()] if QUEUE_FILE.exists() else []
    queue_count = len(queue_lines)

    # 已下载文件数
    file_count = len(list(OUTPUT_DIR.glob("video_*.ts")))

    # 读取所有状态文件
    tasks = []
    active_count = 0
    for sf in sorted(OUTPUT_DIR.glob("_status_*.json"), reverse=True)[:10]:
        try:
            data = json.loads(sf.read_text())
            task_id = sf.stem.replace("_status_", "")
            output_file = OUTPUT_DIR / f"video_{task_id}.ts"
            data["id"] = task_id

            # 检查停止信号
            stop_file = STOP_SIGNAL_DIR / f"{task_id}.stop"
            if stop_file.exists() and data.get("status") == "downloading":
                data["status"] = "stopping"

            # 如果文件已存在，状态改为完成
            if output_file.exists() and data.get("status") in ("downloading", "extracting", "stopping"):
                size_mb = output_file.stat().st_size / (1024 * 1024)
                data["status"] = "done"
                data["size"] = f"{size_mb:.0f} MB"
                write_status_file(task_id, status="done", size=f"{size_mb:.0f} MB")

            if data.get("status") in ("downloading", "extracting", "stopping"):
                active_count += 1

            # 尝试从队列文件中找 URL
            url = ""
            # 简单匹配：通过 id 反查 URL
            tasks.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    # 日志 (最近 60 行)
    log_lines = DONE_LOG.read_text().splitlines()[-60:] if DONE_LOG.exists() else []
    parts = []
    for l in log_lines:
        if "✅" in l or "完成" in l:
            parts.append(f'<span class="ok">{l}</span>')
        elif "✗" in l or "❌" in l or "失败" in l or "ERROR" in l:
            parts.append(f'<span class="err">{l}</span>')
        elif "⏭" in l or "跳过" in l:
            parts.append(f'<span class="warn">{l}</span>')
        elif "⏹" in l or "停止" in l:
            parts.append(f'<span class="warn">{l}</span>')
        elif "🔍" in l or "📡" in l or "获取" in l or "下载" in l:
            parts.append(f'<span class="info">{l}</span>')
        else:
            parts.append(l)
    log_html = "<br>".join(parts) if parts else "等待任务..."

    return jsonify({
        "queue": queue_count,
        "files": file_count,
        "active": active_count,
        "tasks": tasks,
        "logHtml": log_html,
    })


# ========== 辅助 ==========

def log(msg: str):
    """写日志"""
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    print(line, flush=True)
    try:
        with open(DONE_LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_status_file(task_id: str, **kwargs):
    """写入某个任务的 JSON 状态文件"""
    sf = OUTPUT_DIR / f"_status_{task_id}.json"
    try:
        existing = {}
        if sf.exists():
            existing = json.loads(sf.read_text())
        existing.update(kwargs)
        sf.write_text(json.dumps(existing, ensure_ascii=False))
    except Exception:
        pass


# ========== 启动 ==========

if __name__ == "__main__":
    print(f"\n📥 HLS Video Downloader Web 服务")
    print(f"   队列文件: {QUEUE_FILE}")
    print(f"   输出目录: {OUTPUT_DIR}")
    print(f"   访问地址: http://localhost:5001")
    print(f"   启动 worker: python web_downloader/worker.py")
    print()
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)

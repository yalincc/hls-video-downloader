"""
后台下载 Worker
监听 queue.txt，逐个处理下载任务。

用法: python web/worker.py
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
QUEUE_FILE = BASE_DIR / "queue.txt"
DOWNLOAD_DIR = BASE_DIR / "downloads"
STATUS_DIR = DOWNLOAD_DIR / ".status"
STOP_DIR = DOWNLOAD_DIR / ".stop_signals"
DONE_LOG = DOWNLOAD_DIR / ".done.txt"
LOCK_FILE = DOWNLOAD_DIR / ".worker.lock"
DOWNLOAD_SCRIPT = BASE_DIR / "download.py"

DOWNLOAD_DIR.mkdir(exist_ok=True)
STATUS_DIR.mkdir(exist_ok=True)
STOP_DIR.mkdir(exist_ok=True)
QUEUE_FILE.touch(exist_ok=True)
DONE_LOG.touch(exist_ok=True)


def log(msg: str):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        safe = line.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(safe, flush=True)
    try:
        with open(DONE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_status(task_id: str, **kwargs):
    sf = STATUS_DIR / f"{task_id}.json"
    try:
        existing = {}
        if sf.exists():
            existing = json.loads(sf.read_text())
        existing.update(kwargs)
        sf.write_text(json.dumps(existing, ensure_ascii=False))
    except Exception:
        pass


def main():
    log("Worker 启动")

    while True:
        try:
            if not QUEUE_FILE.exists() or QUEUE_FILE.stat().st_size == 0:
                time.sleep(3)
                continue

            lines = [l.strip() for l in QUEUE_FILE.read_text().splitlines() if l.strip()]
            if not lines:
                time.sleep(3)
                continue

            page_url = lines[0]
            QUEUE_FILE.write_text("\n".join(lines[1:]) + ("\n" if len(lines) > 1 else ""))

            if not page_url.startswith("http"):
                log(f"  [WARN] 无效: {page_url}")
                continue

            vid = re.search(r"/(\d{5,})", page_url)
            task_id = vid.group(1) if vid else str(int(time.time()))
            out_file = DOWNLOAD_DIR / f"video_{task_id}.mp4"

            log(page_url)

            # 跳过已完成
            if out_file.exists():
                size_mb = out_file.stat().st_size / (1024 * 1024)
                log(f"  [SKIP] 已存在 ({size_mb:.0f} MB)")
                write_status(task_id, status="done", size=f"{size_mb:.0f} MB")
                continue

            # 全局锁
            if LOCK_FILE.exists():
                log("  [WAIT] 其他任务进行中，重新排队")
                with open(QUEUE_FILE, "a") as f:
                    f.write(page_url + "\n")
                time.sleep(5)
                continue

            LOCK_FILE.touch()
            write_status(task_id, status="extracting")

            # 检查停止信号
            if (STOP_DIR / f"{task_id}.stop").exists():
                log("  [STOP] 已停止")
                write_status(task_id, status="stopped")
                LOCK_FILE.unlink(missing_ok=True)
                continue

            # 执行下载
            cmd = [
                sys.executable, "-u", str(DOWNLOAD_SCRIPT),
                page_url,
                "--output-dir", str(DOWNLOAD_DIR),
                "--output", f"video_{task_id}",
                "--workers", "5",
            ]

            log("  [DL] 下载中...")
            try:
                import os
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                result = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=1800, cwd=str(BASE_DIR), env=env
                )

                if (STOP_DIR / f"{task_id}.stop").exists():
                    log("  [STOP] 已停止")
                    write_status(task_id, status="stopped")
                    LOCK_FILE.unlink(missing_ok=True)
                    continue

                output = result.stdout + result.stderr
                if "[DONE]" in output:
                    m = re.search(r"([\d.]+)\s*MB", output)
                    size = m.group(1) if m else "?"
                    log(f"  [DONE] {size} MB")
                    write_status(task_id, status="done", size=f"{size} MB")
                else:
                    log("  [FAIL] 失败")
                    for line in output.splitlines():
                        if any(kw in line for kw in ("ERROR", "失败", "Error")):
                            log(f"     {line.strip()}")
                    write_status(task_id, status="fail", error=output[-500:])

            except subprocess.TimeoutExpired:
                log("  [FAIL] 超时 (30分钟)")
                write_status(task_id, status="fail", error="timeout")

            LOCK_FILE.unlink(missing_ok=True)
            time.sleep(2)

        except Exception as e:
            log(f"[ERROR] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()

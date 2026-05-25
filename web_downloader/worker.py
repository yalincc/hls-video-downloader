"""
后台下载 Worker
监听队列文件 queue.txt，逐个交给 download_video.py 处理。
自动提取 m3u8 + 下载 + 合并，全自动。

用法: python web_downloader/worker.py
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# ---- 路径：基于本文件位置 ----
BASE_DIR = Path(__file__).resolve().parent.parent
QUEUE_FILE = BASE_DIR / "queue.txt"
OUT_DIR = BASE_DIR / "video_download"
DONE_LOG = OUT_DIR / ".done.txt"
STOP_SIGNAL_DIR = OUT_DIR / ".stop_signals"
DOWNLOAD_SCRIPT = BASE_DIR / "download_video.py"
LOCK_FILE = OUT_DIR / ".worker.lock"

OUT_DIR.mkdir(exist_ok=True)
STOP_SIGNAL_DIR.mkdir(exist_ok=True)
QUEUE_FILE.touch(exist_ok=True)
DONE_LOG.touch(exist_ok=True)


# ========== 工具 ==========

def log(msg: str):
    """写日志（同时打印到控制台）"""
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    print(line, flush=True)
    try:
        with open(DONE_LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_status(task_id: str, **kwargs):
    """写入指定任务的 JSON 状态文件"""
    sf = OUT_DIR / f"_status_{task_id}.json"
    try:
        existing = {}
        if sf.exists():
            existing = json.loads(sf.read_text())
        existing.update(kwargs)
        sf.write_text(json.dumps(existing, ensure_ascii=False))
    except Exception:
        pass


def should_stop(task_id: str) -> bool:
    """检查是否有停止信号"""
    return (STOP_SIGNAL_DIR / f"{task_id}.stop").exists()


# ========== 主循环 ==========

def main():
    log(f"Worker 启动 | 输出目录: {OUT_DIR}")

    while True:
        try:
            # ---- 检查队列 ----
            if not QUEUE_FILE.exists() or QUEUE_FILE.stat().st_size == 0:
                time.sleep(3)
                continue

            lines = [l.strip() for l in QUEUE_FILE.read_text().splitlines() if l.strip()]
            if not lines:
                time.sleep(3)
                continue

            page_url = lines[0]
            # 从队列移除第一行
            QUEUE_FILE.write_text("\n".join(lines[1:]) + ("\n" if len(lines) > 1 else ""))

            if not page_url.startswith("http"):
                log(f"  ⚠ 无效链接: {page_url}")
                continue

            # ---- 生成任务 ID ----
            vid = re.search(r'/(\d{5,})', page_url)
            vid_id = vid.group(1) if vid else str(int(time.time()))
            out_file = OUT_DIR / f"video_{vid_id}.ts"

            log(page_url)

            # 跳过已下载
            if out_file.exists():
                size_mb = out_file.stat().st_size / (1024 * 1024)
                log(f"  ⏭ 已存在 ({size_mb:.0f} MB)")
                write_status(vid_id, status="done", done=1, total=1, size=f"{size_mb:.0f} MB")
                continue

            # 全局锁：同一时间只处理一个下载
            if LOCK_FILE.exists():
                log("  ⏳ 另一个任务进行中，重新排队")
                with open(QUEUE_FILE, "a") as f:
                    f.write(page_url + "\n")
                time.sleep(5)
                continue

            LOCK_FILE.touch()
            write_status(vid_id, status="extracting", done=0, total=0)

            # 检查停止信号
            if should_stop(vid_id):
                log("  ⏹ 已停止")
                write_status(vid_id, status="stopped")
                LOCK_FILE.unlink(missing_ok=True)
                continue

            # ---- 使用 download_video.py 的 --page-url 全自动处理 ----
            # 它会：抓页面 → 找 m3u8 → 下载分片 → 合并
            status_file = OUT_DIR / f"_status_{vid_id}.json"

            # 读取自定义输出目录配置
            config_file = BASE_DIR / ".web_config.json"
            output_dir = str(OUT_DIR)
            if config_file.exists():
                try:
                    cfg = json.loads(config_file.read_text())
                    if cfg.get("output_dir"):
                        output_dir = cfg["output_dir"]
                except Exception:
                    pass

            cmd = [
                sys.executable, "-u", str(DOWNLOAD_SCRIPT),
                "--page-url", page_url,
                "--output", f"video_{vid_id}.ts",
                "--output-dir", output_dir,
                "--workers", "5",
                "--status-file", str(status_file),
            ]

            log(f"  📥 自动处理中...")
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=1200, cwd=str(BASE_DIR))

                # 检查停止信号
                if should_stop(vid_id):
                    log("  ⏹ 已停止")
                    write_status(vid_id, status="stopped")
                    LOCK_FILE.unlink(missing_ok=True)
                    continue

                # 检查结果
                output = result.stdout + result.stderr
                if "✅" in output:
                    m = re.search(r'([\d.]+ MB)', output)
                    size = m.group(1) if m else "?"
                    log(f"  ✅ {size}")
                    write_status(vid_id, status="done", size=size)
                else:
                    log(f"  ❌ 失败")
                    write_status(vid_id, status="fail", error=output[-300:])
                    # 输出日志帮助排查
                    for line in output.splitlines():
                        if "ERROR" in line or "失败" in line or "Error" in line:
                            log(f"     {line.strip()}")

            except subprocess.TimeoutExpired:
                log("  ❌ 超时 (20分钟)")
                write_status(vid_id, status="fail", error="timeout")

            LOCK_FILE.unlink(missing_ok=True)
            time.sleep(2)

        except Exception as e:
            log(f"[ERROR] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()

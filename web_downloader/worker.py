"""
后台下载 Worker
监听队列文件 queue.txt，逐个处理：
  1. 用 playwright-cli 从页面提取 m3u8 地址
  2. 调用 download_video.py 下载
  3. 写状态文件和日志供 Web 界面读取

用法: python web_downloader/worker.py
"""
import json
import os
import re
import shutil
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


def find_playwright_cli() -> str:
    """自动检测 playwright-cli 路径"""
    # 优先找全局安装的
    candidates = ["playwright-cli"]
    # Windows 下常见位置
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", "playwright-cli.cmd"))
            candidates.append(os.path.join(appdata, "npm", "playwright-cli"))
    for cmd in candidates:
        found = shutil.which(cmd)
        if found:
            return found
    # 最后 fallback
    return "playwright-cli"


def extract_m3u8(pw_cli: str, page_url: str) -> str | None:
    """用 playwright-cli 打开页面，提取 m3u8 地址"""
    env = {**os.environ, "NODE_OPTIONS": ""}
    for attempt in range(3):
        try:
            subprocess.run([pw_cli, "kill-all"], env=env,
                           capture_output=True, timeout=10)
            time.sleep(2)

            subprocess.run([pw_cli, "open", page_url], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30)
            time.sleep(2)

            result = subprocess.run(
                [pw_cli, "eval",
                 'JSON.stringify(Array.from(document.querySelectorAll("source")).map(function(s){return {src:s.src}}))'],
                env=env, capture_output=True, text=True, timeout=10)

            subprocess.run([pw_cli, "close"], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5)

            m = re.search(r'https://[^"\s\\]+\.m3u8[^"\s\\]*', result.stdout)
            if m:
                url = m.group(0).rstrip("\\/")
                log(f"  ✓ m3u8: {url[:80]}...")
                return url
            log(f"  ⚠ 尝试 {attempt + 1}/3 未找到 m3u8")
        except subprocess.TimeoutExpired:
            log(f"  ⚠ 尝试 {attempt + 1}/3 超时")
        except FileNotFoundError:
            log(f"  ❌ 未找到 playwright-cli，请执行: npm install -g @playwright/cli")
            return None
        except Exception as e:
            log(f"  ⚠ 尝试 {attempt + 1}/3 异常: {e}")
    return None


def get_referer(page_url: str) -> str:
    """从页面 URL 提取 referer (协议 + 域名)"""
    m = re.match(r'(https?://[^/]+)', page_url)
    return m.group(1) + "/" if m else ""


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
    pw_cli = find_playwright_cli()
    log(f"Worker 启动 | playwright-cli: {pw_cli}")

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

            url = lines[0]
            # 从队列移除第一行
            QUEUE_FILE.write_text("\n".join(lines[1:]) + ("\n" if len(lines) > 1 else ""))

            # ---- 生成任务 ID ----
            vid = re.search(r'/(\d+)', url)
            vid_id = vid.group(1) if vid else str(int(time.time()))
            out_file = OUT_DIR / f"video_{vid_id}.ts"

            log(url)

            # 跳过已下载
            if out_file.exists():
                size_mb = out_file.stat().st_size / (1024 * 1024)
                log(f"  ⏭ 已存在 ({size_mb:.0f} MB)")
                write_status(vid_id, status="done", done=1, total=1, size=f"{size_mb:.0f} MB")
                continue

            # 全局锁：同一时间只处理一个下载
            if LOCK_FILE.exists():
                log("  ⏭ 另一个下载任务正在进行，重新排队")
                # 放回队列
                with open(QUEUE_FILE, "a") as f:
                    f.write(url + "\n")
                time.sleep(5)
                continue

            LOCK_FILE.touch()
            write_status(vid_id, status="extracting", done=0, total=0)

            # ---- 提取 m3u8 ----
            m3u8 = extract_m3u8(pw_cli, url)
            if not m3u8:
                log("  ❌ 未找到视频")
                write_status(vid_id, status="fail", done=0, total=0, error="m3u8 not found")
                LOCK_FILE.unlink(missing_ok=True)
                continue

            # 检查停止信号
            if should_stop(vid_id):
                log("  ⏹ 已停止")
                write_status(vid_id, status="stopped", done=0, total=0)
                LOCK_FILE.unlink(missing_ok=True)
                STOP_SIGNAL_DIR.mkdir(exist_ok=True)
                continue

            write_status(vid_id, status="downloading", done=0, total=100)

            # ---- 下载 ----
            referer = get_referer(url)
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
                "--url", m3u8,
                "--referer", referer,
                "--output", f"video_{vid_id}.ts",
                "--output-dir", output_dir,
                "--workers", "5",
                "--status-file", str(status_file),
            ]

            log(f"  📥 开始下载...")
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=1200, cwd=str(BASE_DIR))

                # 检查是否被用户停止
                if should_stop(vid_id):
                    log("  ⏹ 已停止")
                    write_status(vid_id, status="stopped")
                    # 停止信号发给 download_video.py — 它通过 SIGINT 处理
                    LOCK_FILE.unlink(missing_ok=True)
                    STOP_SIGNAL_DIR.mkdir(exist_ok=True)
                    continue

                # 检查结果
                output = result.stdout + result.stderr
                if "✅" in output:
                    m = re.search(r'([\d.]+ MB)', output)
                    size = m.group(1) if m else "?"
                    log(f"  ✅ {size}")
                    write_status(vid_id, status="done", size=size)
                else:
                    log(f"  ❌ 失败 (详见日志)")
                    write_status(vid_id, status="fail", error=output[-200:])
            except subprocess.TimeoutExpired:
                log("  ❌ 下载超时 (20分钟)")
                write_status(vid_id, status="fail", error="timeout")

            LOCK_FILE.unlink(missing_ok=True)
            time.sleep(2)

        except Exception as e:
            log(f"[ERROR] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()

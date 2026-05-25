"""
通用 m3u8 / HLS 视频下载工具

下载加密或未加密的 HLS 流视频，支持 AES-128 解密、断点续传、进度显示。

用法:
  # 直接给 m3u8 地址
  python download_video.py --url <m3u8地址> [--referer <来源页>]

  # 给视频页面地址，自动提取 m3u8（推荐）
  python download_video.py --page-url <视频页面链接> [--referer <来源页>]

  # 指定输出目录
  python download_video.py --page-url <链接> --output-dir ./videos --output video.ts
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from Crypto.Cipher import AES
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"

# Global flag for graceful shutdown
_stop_flag = False


def _handle_sigint(signum, frame):
    global _stop_flag
    if _stop_flag:
        print("\n  强制退出。", flush=True)
        sys.exit(1)
    _stop_flag = True
    print("\n  正在停止下载（再按一次 Ctrl+C 强制退出）...", flush=True)


signal.signal(signal.SIGINT, _handle_sigint)


def parse_args():
    p = argparse.ArgumentParser(description="通用 m3u8 / HLS 视频下载器")
    p.add_argument("--url", default=None, help="m3u8 播放列表地址（跟 --page-url 二选一）")
    p.add_argument("--page-url", default=None, help="视频页面链接，自动提取 m3u8（跟 --url 二选一）")
    p.add_argument("--referer", default=None, help="Referer 请求头（防盗链网站必填）")
    p.add_argument("--output", default=None, help="输出文件名（默认: video.ts）")
    p.add_argument("--output-dir", default=None, help="输出目录（默认: 当前目录）")
    p.add_argument("--workers", type=int, default=10, help="并发线程数（默认: 10，CDN 限制时降低）")
    p.add_argument("--retry", type=int, default=3, help="单分片重试次数（默认: 3）")
    p.add_argument("--no-resume", action="store_true", help="禁用断点续传，从头下载")
    p.add_argument("--status-file", default=None, help="进度状态文件（JSON，供网页端读取）")
    return p.parse_args()


def make_session(referer: str | None) -> requests.Session:
    """创建带重试机制的 requests Session"""
    s = requests.Session()
    headers = {
        "User-Agent": UA,
        "Accept-Encoding": "gzip, deflate",
    }
    if referer:
        headers["Referer"] = referer
    s.headers.update(headers)
    retry = Retry(total=3, backoff_factor=0.5,
                  status_forcelist=[500, 502, 503, 504],
                  allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def resolve_url(base_url: str, ref: str) -> str:
    """将相对 URL 解析为绝对 URL"""
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref
    # 相对于 m3u8 所在目录
    base = base_url.rsplit("/", 1)[0] + "/"
    if ref.startswith("/"):
        # 相对于站点根目录
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{ref}"
    return base + ref


def parse_m3u8(session: requests.Session, url: str, referer: str | None):
    """下载并解析 m3u8，返回 (ts_urls列表, key_url或None, iv或None)"""
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return _parse_m3u8_lines(resp.text, url)


def fetch_key(session: requests.Session, key_url: str) -> bytes:
    """获取 AES-128 密钥"""
    resp = session.get(key_url, timeout=30)
    resp.raise_for_status()
    return resp.content


def decrypt_segment(data: bytes, key: bytes, iv: bytes | None, seg_index: int) -> bytes:
    """AES-128-CBC 解密单个 TS 分片"""
    if iv is None:
        iv = seg_index.to_bytes(16, byteorder="big")
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    return cipher.decrypt(data)


def _parse_m3u8_lines(text: str, base_url: str):
    """从纯文本解析 m3u8，返回 (ts_urls, key_url, iv)"""
    ts_urls = []
    key_url = None
    iv = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-KEY:"):
            import re
            m_method = re.search(r'METHOD=([^,]+)', line)
            m_uri = re.search(r'URI="([^"]+)"', line)
            m_iv = re.search(r'IV=(0x[0-9a-fA-F]+)', line)
            if m_method and m_method.group(1) == "AES-128" and m_uri:
                key_url = resolve_url(base_url, m_uri.group(1))
            if m_iv:
                iv = bytes.fromhex(m_iv.group(1)[2:])
        elif line.startswith("#") or not line:
            continue
        else:
            ts_urls.append(resolve_url(base_url, line))
    return ts_urls, key_url, iv


def fetch_m3u8_via_curl(url: str, referer: str | None) -> str:
    """Fallback: 当 Python requests 失败时用 curl 获取 m3u8 内容"""
    print("  Python requests 失败，尝试 curl...", flush=True)
    cmd = ["curl", "-s", "--connect-timeout", "15", "--max-time", "30", url]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(f"curl 也失败了: {result.stderr or '空响应'}")
        return result.stdout
    except FileNotFoundError:
        raise RuntimeError("系统未安装 curl，无法降级")


def find_playwright_cli() -> str:
    """自动检测 playwright-cli 路径"""
    candidates = ["playwright-cli"]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", "playwright-cli.cmd"))
            candidates.append(os.path.join(appdata, "npm", "playwright-cli"))
    for cmd in candidates:
        import shutil
        found = shutil.which(cmd)
        if found:
            return found
    return "playwright-cli"


def extract_m3u8_from_page(page_url: str, referer: str | None = None) -> str:
    """用 Playwright 打开视频页面，自动提取 m3u8 地址"""
    import re
    pw_cli = find_playwright_cli()
    env = {**os.environ, "NODE_OPTIONS": ""}

    print(f"  🔍 在页面中查找 m3u8...", flush=True)

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
                print(f"  ✓ 提取成功: {url[:80]}...", flush=True)
                return url

            print(f"  ⚠ 第 {attempt + 1}/3 次尝试未找到 m3u8", flush=True)

        except subprocess.TimeoutExpired:
            print(f"  ⚠ 第 {attempt + 1}/3 次超时", flush=True)
        except FileNotFoundError:
            print(f"\n[ERROR] 未安装 playwright-cli，请执行:", flush=True)
            print(f"       npm install -g @playwright/cli", flush=True)
            print(f"       playwright-cli install", flush=True)
            sys.exit(1)
        except Exception as e:
            print(f"  ⚠ 第 {attempt + 1}/3 次异常: {e}", flush=True)

    print("\n[ERROR] 无法从页面提取 m3u8 地址", flush=True)
    sys.exit(1)


def write_status(status_file: str | None, **kwargs):
    """写入 JSON 状态文件供 Web 界面读取"""
    if not status_file:
        return
    try:
        data = {"status": "downloading", "done": 0, "total": 0, "failed": 0, **kwargs}
        Path(status_file).write_text(json.dumps(data))
    except Exception:
        pass


def main():
    args = parse_args()

    # ---- 处理页面提取模式 --page-url ----
    if args.page_url:
        m3u8_url = args.url if args.url else None
        if not m3u8_url:
            # 从页面自动提取
            print(f"[1/4] 打开视频页面提取 m3u8", flush=True)
            print(f"       {args.page_url}", flush=True)
            m3u8_url = extract_m3u8_from_page(args.page_url, args.referer)
            print(f"       → {m3u8_url[:80]}...", flush=True)
        # 自动补 referer（如果用页面url且没手工指定）
        if not args.referer:
            from urllib.parse import urlparse
            parsed = urlparse(args.page_url)
            args.referer = f"{parsed.scheme}://{parsed.netloc}/"
        args.url = m3u8_url

    if not args.url:
        print("[ERROR] 请提供 --url（m3u8 地址）或 --page-url（视频页面链接）")
        print("   python download_video.py --page-url <视频页面链接>")
        sys.exit(1)

    session = make_session(args.referer)

    # ---- 确定输出路径 ----
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_name = args.output or "video.ts"
    out_path = output_dir / out_name
    tmpdir = output_dir / f".tmp_{out_name}"
    tmpdir.mkdir(parents=True, exist_ok=True)

    start = time.time()

    # ---- 获取 m3u8 播放列表 ----
    step_n = "2/4" if args.page_url else "1/4"
    print(f"[{step_n}] 获取 m3u8 播放列表", flush=True)
    print(f"       {args.url}", flush=True)
    try:
        ts_urls, key_url, iv = parse_m3u8(session, args.url, args.referer)
    except requests.RequestException:
        try:
            text = fetch_m3u8_via_curl(args.url, args.referer)
            ts_urls, key_url, iv = _parse_m3u8_lines(text, args.url)
        except Exception as e:
            print(f"\n[ERROR] 获取 m3u8 失败: {e}", flush=True)
            if args.referer is None:
                print("  提示: 可尝试添加 --referer 参数（设为页面地址）", flush=True)
            sys.exit(1)

    write_status(args.status_file, total=len(ts_urls))

    if not ts_urls:
        print("[ERROR] 未找到任何 TS 分片！", flush=True)
        sys.exit(1)

    print(f"       ✓ 共 {len(ts_urls)} 个 TS 分片", flush=True)

    # ---- 处理 AES-128 加密 ----
    key = None
    if key_url:
        print(f"       🔐 AES-128 加密, 获取密钥...", flush=True)
        try:
            key = fetch_key(session, key_url)
            print(f"        ✓ 密钥获取成功 ({len(key)} bytes)", flush=True)
        except Exception as e:
            print(f"       [ERROR] 获取密钥失败: {e}", flush=True)
            sys.exit(1)

    step_dl = "3/4" if args.page_url else "2/4"
    # ---- 断点续传检查 ----
    resume_from = 0
    if not args.no_resume:
        existing_segs = sorted(tmpdir.glob("seg_*.ts"))
        if existing_segs:
            # 检查是否连续 — 找最后一个完整的分片
            for seg_file in existing_segs:
                idx = int(seg_file.stem.split("_")[1])
                if idx == resume_from:
                    resume_from += 1
                else:
                    break
            if resume_from > 0:
                print(f"       🔄 检测到 {resume_from} 个已下载分片，继续下载", flush=True)
        write_status(args.status_file, done=resume_from, total=len(ts_urls))

    # ---- 并发下载 ----
    print(f"[{step_dl}] 下载分片（workers={args.workers}）", flush=True)

    total = len(ts_urls)
    pbar = tqdm(
        total=total, initial=resume_from,
        unit="seg", ncols=70,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    )

    downloaded = {}
    failed = []
    lock = None  # dummy for thread safety on status writes

    def download_one(idx: int, url: str) -> tuple[int, str | None]:
        """下载（或跳过）单个 TS 分片"""
        if _stop_flag:
            return idx, None

        fname = tmpdir / f"seg_{idx:05d}.ts"

        # 断点续传：已存在且大小 > 0 就跳过
        if not args.no_resume and fname.exists() and fname.stat().st_size > 0:
            return idx, str(fname)

        for attempt in range(args.retry):
            if _stop_flag:
                return idx, None
            try:
                r = session.get(url, timeout=60)
                r.raise_for_status()
                data = r.content

                # AES-128 解密
                if key:
                    seg_iv = iv if iv else idx.to_bytes(16, byteorder="big")
                    cipher = AES.new(key, AES.MODE_CBC, iv=seg_iv)
                    data = cipher.decrypt(data)

                fname.write_bytes(data)
                return idx, str(fname)
            except Exception as e:
                if attempt == args.retry - 1:
                    return idx, None
                time.sleep(1)
        return idx, None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, i, u): i for i, u in enumerate(ts_urls)}
        for fut in as_completed(futures):
            if _stop_flag:
                # 取消剩余任务
                break
            idx, path = fut.result()
            if path:
                downloaded[idx] = path
            else:
                failed.append(idx)
            pbar.update(1)
            # 每 10 个分片写一次状态
            done_count = pbar.n
            if done_count % 10 == 0:
                write_status(args.status_file, done=done_count, failed=len(failed))

    pbar.close()

    # 写入最终状态
    write_status(args.status_file, done=pbar.n, failed=len(failed),
                 status="stopped" if _stop_flag else "downloading")

    if _stop_flag:
        print("\n   ⏸ 用户停止。部分分片已下载，下次可用 --output 相同路径续传。", flush=True)
        sys.exit(0)

    if failed:
        print(f"  ⚠ {len(failed)}/{total} 分片失败: {failed[:15]}{'...' if len(failed) > 15 else ''}", flush=True)

    if not downloaded:
        print("[ERROR] 无成功分片，退出。", flush=True)
        sys.exit(1)

    step_merge = "4/4" if args.page_url else "3/4"
    # ---- 合并 ----
    print(f"[{step_merge}] 合并 {len(downloaded)} 个分片 → {out_path}", flush=True)
    with open(out_path, "wb") as fout:
        for idx in sorted(downloaded):
            fout.write(Path(downloaded[idx]).read_bytes())

    # 清理临时分片
    for idx in sorted(downloaded):
        try:
            os.remove(downloaded[idx])
        except OSError:
            pass
    try:
        tmpdir.rmdir()
    except OSError:
        pass

    elapsed = time.time() - start
    size_mb = out_path.stat().st_size / (1024 * 1024)
    success = len(downloaded)
    pct = (success / total) * 100 if total > 0 else 0
    print(f"\n✅ 完成 | {size_mb:.1f} MB | 耗时 {elapsed:.0f}s | {success}/{total} ({pct:.0f}%)", flush=True)
    print(f"   📁 {out_path.resolve()}", flush=True)


if __name__ == "__main__":
    main()

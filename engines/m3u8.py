"""
m3u8 / HLS 流下载引擎

功能:
- 解析 m3u8 播放列表，提取 TS 分片地址
- 多线程并发下载分片
- AES-128-CBC 解密
- 断点续传（跳过已下载的分片）
- 进度回调（供 Web UI 使用）

用法:
  from engines.m3u8 import M3u8Downloader
  dl = M3u8Downloader()
  dl.download("https://cdn.example.com/video/index.m3u8", output="video.ts")
"""

import json
import os
import re
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

# AES-128-CBC 解密 — 优先 pycryptodome，其次 cryptography
try:
    from Crypto.Cipher import AES as _AES

    def aes_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
        return _AES.new(key, _AES.MODE_CBC, iv=iv).decrypt(data)
except ImportError:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    def aes_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
        c = Cipher(algorithms.AES(key), modes.CBC(iv))
        d = c.decryptor()
        return d.update(data) + d.finalize()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

# 全局停止标志
_stop_flag = False


def _handle_sigint(signum, frame):
    global _stop_flag
    if _stop_flag:
        print("\n  强制退出。", flush=True)
        sys.exit(1)
    _stop_flag = True
    print("\n  正在停止（再按 Ctrl+C 强制退出）...", flush=True)


signal.signal(signal.SIGINT, _handle_sigint)


# ── helpers ──────────────────────────────────────────────

def _resolve_url(base_url: str, ref: str) -> str:
    """相对 URL → 绝对 URL"""
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref
    if ref.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{ref}"
    base = base_url.rsplit("/", 1)[0] + "/"
    return base + ref


def _make_session(referer: str | None, retries: int = 3) -> requests.Session:
    """创建带重试机制的 Session"""
    s = requests.Session()
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
    if referer:
        headers["Referer"] = referer
    s.headers.update(headers)
    retry = Retry(total=retries, backoff_factor=0.5,
                  status_forcelist=[500, 502, 503, 504],
                  allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=30, pool_maxsize=30)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _parse_m3u8(text: str, base_url: str) -> tuple[list[str], str | None, bytes | None]:
    """解析 m3u8 文本，返回 (ts_urls, key_url, iv)"""
    ts_urls: list[str] = []
    key_url: str | None = None
    iv: bytes | None = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-KEY:"):
            m_method = re.search(r'METHOD=([^,]+)', line)
            m_uri = re.search(r'URI="([^"]+)"', line)
            m_iv = re.search(r'IV=(0x[0-9a-fA-F]+)', line)
            if m_method and m_method.group(1) == "AES-128" and m_uri:
                key_url = _resolve_url(base_url, m_uri.group(1))
            if m_iv:
                iv = bytes.fromhex(m_iv.group(1)[2:])
        elif line.startswith("#") or not line:
            continue
        else:
            ts_urls.append(_resolve_url(base_url, line))

    return ts_urls, key_url, iv


# ── downloader ──────────────────────────────────────────

class M3u8Downloader:
    """m3u8/HLS 分片下载器"""

    def __init__(
        self,
        workers: int = 8,
        retry: int = 3,
        referer: str | None = None,
        progress_callback: Callable | None = None,
    ):
        """
        Args:
            workers: 并发下载线程数
            retry: 单个分片最大重试次数
            referer: Referer 请求头（防盗链站点必填）
            progress_callback: 进度回调 fn(done: int, total: int, failed: int)
        """
        self.workers = workers
        self.retry = retry
        self.referer = referer
        self.progress_callback = progress_callback

    def download(
        self,
        m3u8_url: str,
        output: str | Path = "video.ts",
        no_resume: bool = False,
    ) -> Path:
        """下载 m3u8 流并合并为单个文件

        Args:
            m3u8_url: m3u8 播放列表地址
            output: 输出文件路径
            no_resume: 禁用断点续传

        Returns:
            输出文件 Path
        """
        global _stop_flag
        _stop_flag = False

        output = Path(output)
        tmpdir = output.parent / f".tmp_{output.name}"
        tmpdir.mkdir(parents=True, exist_ok=True)

        session = _make_session(self.referer, self.retry)

        # 1. 获取 m3u8 播放列表
        print(f"  [FETCH] 获取播放列表: {m3u8_url[:80]}...", flush=True)
        resp = session.get(m3u8_url, timeout=30)
        resp.raise_for_status()
        ts_urls, key_url, iv = _parse_m3u8(resp.text, m3u8_url)

        if not ts_urls:
            raise RuntimeError("未找到任何 TS 分片")

        total = len(ts_urls)
        print(f"  [OK] 共 {total} 个分片", flush=True)

        # 2. 获取 AES-128 密钥
        key: bytes | None = None
        if key_url:
            print(f"  [AES] AES-128 加密，获取密钥...", flush=True)
            kr = session.get(key_url, timeout=30)
            kr.raise_for_status()
            key = kr.content
            print(f"  [OK] 密钥 {len(key)} bytes", flush=True)

        # 3. 断点续传检查
        resume_from = 0
        if not no_resume:
            existing = sorted(tmpdir.glob("seg_*.ts"))
            for seg in existing:
                idx = int(seg.stem.split("_")[1])
                if idx == resume_from and seg.stat().st_size > 0:
                    resume_from += 1
                else:
                    break
            if resume_from > 0:
                print(f"  [RESUME] 续传: {resume_from}/{total} 已下载", flush=True)

        # 4. 并发下载
        print(f"  [DL] 下载中 ({self.workers} 线程)...", flush=True)
        start_time = time.time()
        downloaded: dict[int, str] = {}
        failed: list[int] = []

        def _download_one(idx: int, url: str) -> tuple[int, str | None]:
            if _stop_flag:
                return idx, None

            fname = tmpdir / f"seg_{idx:05d}.ts"

            if not no_resume and fname.exists() and fname.stat().st_size > 0:
                return idx, str(fname)

            for attempt in range(self.retry):
                if _stop_flag:
                    return idx, None
                try:
                    r = session.get(url, timeout=90)
                    r.raise_for_status()
                    data = r.content

                    if key:
                        seg_iv = iv if iv else idx.to_bytes(16, byteorder="big")
                        data = aes_decrypt(data, key, seg_iv)

                    fname.write_bytes(data)
                    return idx, str(fname)
                except Exception:
                    if attempt < self.retry - 1:
                        time.sleep(1)
            return idx, None

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_download_one, i, u): i for i, u in enumerate(ts_urls)}
            with tqdm(total=total, initial=resume_from, unit="seg", ncols=70,
                      bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]") as pbar:
                for fut in as_completed(futures):
                    if _stop_flag:
                        break
                    idx, path = fut.result()
                    if path:
                        downloaded[idx] = path
                        pbar.n = len(downloaded)
                        pbar.refresh()
                    else:
                        failed.append(idx)
                        pbar.update(0)
                    # 进度回调
                    if self.progress_callback and len(downloaded) % 10 == 0:
                        self.progress_callback(len(downloaded), total, len(failed))

        if _stop_flag:
            print("  [STOP] 用户停止。下次可续传。", flush=True)
            raise KeyboardInterrupt

        if not downloaded:
            raise RuntimeError("无成功分片")

        if failed:
            print(f"  [WARN] {len(failed)}/{total} 分片失败: {failed[:10]}...", flush=True)

        # 5. 合并分片
        print(f"  [MERGE] 合并 {len(downloaded)} 个分片 -> {output}", flush=True)
        with open(output, "wb") as fout:
            for idx in sorted(downloaded):
                fout.write(Path(downloaded[idx]).read_bytes())

        # 6. 清理
        for idx in sorted(downloaded):
            try:
                os.remove(downloaded[idx])
            except OSError:
                pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass

        elapsed = time.time() - start_time
        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"  [DONE] {size_mb:.1f} MB | {elapsed:.0f}s | {len(downloaded)}/{total}", flush=True)

        return output

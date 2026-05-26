"""
直链下载引擎
支持 HTTP Range 多段并发下载 + 断点续传。

适用于 mp4 / flv / webm 等直接可访问的视频文件。

用法:
  from engines.direct import DirectDownloader
  dl = DirectDownloader()
  dl.download("https://cdn.example.com/video.mp4", output="video.mp4")
"""

import os
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import requests
from tqdm import tqdm

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

_stop_flag = False


def _handle_sigint(signum, frame):
    global _stop_flag
    if _stop_flag:
        sys.exit(1)
    _stop_flag = True
    print("\n  正在停止...", flush=True)


signal.signal(signal.SIGINT, _handle_sigint)


class DirectDownloader:
    """HTTP Range 多段并发下载器"""

    def __init__(
        self,
        segments: int = 8,
        referer: str | None = None,
        progress_callback: Callable | None = None,
    ):
        """
        Args:
            segments: 并发分段数
            referer: Referer 请求头
            progress_callback: fn(done_bytes: int, total_bytes: int)
        """
        self.segments = segments
        self.referer = referer
        self.progress_callback = progress_callback

    def download(
        self,
        url: str,
        output: str | Path = "video.mp4",
        no_resume: bool = False,
    ) -> Path:
        """多段并发下载文件

        Args:
            url: 文件直链
            output: 输出路径
            no_resume: 禁用断点续传

        Returns:
            输出文件 Path
        """
        global _stop_flag
        _stop_flag = False

        output = Path(output)
        progress_file = output.parent / f".{output.name}.progress"

        headers = {"User-Agent": UA}
        if self.referer:
            headers["Referer"] = self.referer

        # 1. HEAD 请求获取文件大小
        print(f"  [FETCH] 获取文件信息: {url[:80]}...", flush=True)
        resp = requests.head(url, headers=headers, timeout=30, allow_redirects=True)
        if resp.status_code == 405:  # HEAD 不支持
            resp = requests.get(url, headers=headers, stream=True, timeout=30)
            total_size = int(resp.headers.get("Content-Length", 0))
            resp.close()
        else:
            total_size = int(resp.headers.get("Content-Length", 0))

        if total_size == 0:
            raise RuntimeError("无法获取文件大小（服务器未返回 Content-Length）")

        size_mb = total_size / (1024 * 1024)
        print(f"  [OK] 文件大小: {size_mb:.1f} MB", flush=True)

        # 2. 处理断点续传
        if no_resume:
            progress_file.unlink(missing_ok=True)

        # 3. 分片策略
        seg_size = total_size // self.segments
        ranges: list[tuple[int, int]] = []
        for i in range(self.segments):
            start = i * seg_size
            end = (start + seg_size - 1) if i < self.segments - 1 else total_size - 1
            ranges.append((start, end))

        # 4. 并发下载各段
        print(f"  ⬇ 下载中 ({self.segments} 段并发)...", flush=True)
        start_time = time.time()
        seg_files: dict[int, str] = {}
        done_bytes = [0]  # 用 list 避开闭包捕获
        lock = threading.Lock()

        def _download_segment(idx: int, start: int, end: int):
            if _stop_flag:
                return idx, None

            seg_file = output.parent / f".{output.name}.seg{idx:03d}"
            seg_headers = {**headers, "Range": f"bytes={start}-{end}"}

            # 断点续传：检查已有文件
            if seg_file.exists() and not no_resume:
                existing_size = seg_file.stat().st_size
                expected = end - start + 1
                if existing_size >= expected:
                    with lock:
                        done_bytes[0] += expected
                    return idx, str(seg_file)
                else:
                    # 部分下载，续传
                    seg_headers["Range"] = f"bytes={start + existing_size}-{end}"

            for attempt in range(3):
                if _stop_flag:
                    return idx, None
                try:
                    r = requests.get(url, headers=seg_headers, stream=True, timeout=120)
                    r.raise_for_status()
                    mode = "ab" if seg_file.exists() and not no_resume else "wb"
                    with open(seg_file, mode) as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if _stop_flag:
                                r.close()
                                return idx, None
                            f.write(chunk)
                            with lock:
                                done_bytes[0] += len(chunk)
                    return idx, str(seg_file)
                except Exception:
                    if attempt < 2:
                        time.sleep(2)
            return idx, None

        with ThreadPoolExecutor(max_workers=self.segments) as pool:
            futures = {
                pool.submit(_download_segment, i, s, e): i
                for i, (s, e) in enumerate(ranges)
            }

            with tqdm(total=total_size, unit="B", unit_scale=True, ncols=70,
                      bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]") as pbar:
                last_bytes = 0
                while not all(f.done() for f in futures):
                    if _stop_flag:
                        break
                    current = done_bytes[0]
                    pbar.update(current - last_bytes)
                    last_bytes = current
                    if self.progress_callback and current - last_bytes > 1024 * 1024:
                        self.progress_callback(current, total_size)
                    time.sleep(0.5)

                # 最终刷新
                pbar.update(done_bytes[0] - last_bytes)

            # 收集结果
            for fut in as_completed(futures):
                idx, path = fut.result()
                if path:
                    seg_files[idx] = path

        if _stop_flag:
            print("  ⏸ 已停止。下次可续传。", flush=True)
            raise KeyboardInterrupt

        # 5. 合并文件
        print(f"  [MERGE] 合并 {len(seg_files)} 段 → {output}", flush=True)
        with open(output, "wb") as fout:
            for idx in sorted(seg_files):
                with open(seg_files[idx], "rb") as fin:
                    fout.write(fin.read())

        # 6. 清理
        for f in seg_files.values():
            try:
                os.remove(f)
            except OSError:
                pass
        progress_file.unlink(missing_ok=True)

        elapsed = time.time() - start_time
        actual_mb = output.stat().st_size / (1024 * 1024)
        speed = actual_mb / elapsed if elapsed > 0 else 0
        print(f"  [DONE] 完成 | {actual_mb:.1f} MB | {elapsed:.0f}s | {speed:.1f} MB/s", flush=True)

        return output




"""
yt-dlp 提取器
只负责提取视频的真实下载地址和信息（格式列表、标题、时长等），不做实际下载。

用法:
  from extractors.ytdlp import YtdlpExtractor
  ext = YtdlpExtractor()
  info = ext.extract("https://www.youtube.com/watch?v=xxx")
  # info["formats"] → [{url, ext, resolution, ...}, ...]
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class YtdlpError(Exception):
    """yt-dlp 提取失败"""


class YtdlpNotFound(YtdlpError):
    """系统未安装 yt-dlp"""


def _find_ytdlp() -> str:
    """查找 yt-dlp 可执行文件路径"""
    # 优先用 python -m yt_dlp（虚拟环境/本地安装）
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        if result.returncode == 0:
            return "module"
    except Exception:
        pass

    # 再试全局命令
    for cmd in ("yt-dlp", "yt-dlp.exe"):
        try:
            result = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
            )
            if result.returncode == 0:
                return cmd
        except FileNotFoundError:
            continue

    raise YtdlpNotFound(
        "未找到 yt-dlp。请安装: pip install yt-dlp"
    )


def _build_cmd(ytdlp_path: str) -> list[str]:
    """构建 yt-dlp 命令前缀"""
    if ytdlp_path == "module":
        return [sys.executable, "-m", "yt_dlp"]
    return [ytdlp_path]


class YtdlpExtractor:
    """使用 yt-dlp 提取视频信息和真实下载地址"""

    def __init__(self, cookies_file: str | None = None, proxy: str | None = None):
        """
        Args:
            cookies_file: Netscape 格式 cookie 文件路径（用于登录态站点）
            proxy: 代理地址，如 socks5://127.0.0.1:1080
        """
        self._ytdlp = _find_ytdlp()
        self._cookies = cookies_file
        self._proxy = proxy

    @property
    def available(self) -> bool:
        """yt-dlp 是否可用"""
        return True  # 构造函数已检查

    def extract(self, url: str) -> dict[str, Any]:
        """提取视频信息，返回 yt-dlp 的 info_dict

        Returns:
            {
                "id": "视频ID",
                "title": "标题",
                "duration": 时长秒数,
                "formats": [
                    {
                        "format_id": "137",
                        "ext": "mp4",
                        "resolution": "1920x1080",
                        "filesize": 字节数（可能为None）,
                        "url": "真实下载地址",
                        "protocol": "https" | "m3u8_native" | ...
                    }, ...
                ],
                "subtitles": {...},
            }
        """
        cmd = _build_cmd(self._ytdlp) + [
            "--dump-json",
            "--no-download",
            "--no-playlist",
            "--no-check-formats",
            "--ignore-errors",
        ]

        if self._cookies:
            cmd += ["--cookies", self._cookies]
        if self._proxy:
            cmd += ["--proxy", self._proxy]

        cmd.append(url)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise YtdlpError(f"yt-dlp 提取超时 (60s): {url}")
        except FileNotFoundError:
            raise YtdlpNotFound("yt-dlp 命令未找到")

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise YtdlpError(f"yt-dlp 提取失败: {stderr or '未知错误'}")

        stdout = result.stdout.strip()
        if not stdout:
            raise YtdlpError(f"yt-dlp 无输出。该站点可能不被支持: {url}")

        # 取第一行 JSON（可能是播放列表多行）
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

        raise YtdlpError(f"yt-dlp 输出无法解析: {stdout[:200]}")

    def get_best_url(self, url: str, prefer: str = "best") -> tuple[str, dict]:
        """提取最佳视频地址和它的 info

        Args:
            url: 视频页面 URL
            prefer: "best" 最高画质 / "worst" 最低画质 / "mp4" 优先 mp4

        Returns:
            (download_url, format_dict)
        """
        info = self.extract(url)
        formats = info.get("formats", [])
        if not formats:
            raise YtdlpError(f"未找到任何可用格式: {url}")

        # 筛选策略
        if prefer == "mp4":
            candidates = [f for f in formats if f.get("ext") == "mp4"]
            if not candidates:
                candidates = formats
        elif prefer == "worst":
            candidates = sorted(formats, key=lambda f: f.get("filesize") or 0)
        else:  # best
            candidates = formats

        # 选最高画质的 mp4，或者最后一个（通常是最高画质）
        chosen = candidates[-1]
        video_url = chosen.get("url", "")
        if not video_url:
            raise YtdlpError("所选格式没有可下载的 URL")

        return video_url, chosen

    def get_all_urls(self, url: str) -> list[dict]:
        """提取所有可下载格式的 URL

        Returns:
            [{format_id, ext, resolution, url, protocol, filesize}, ...]
        """
        info = self.extract(url)
        results = []
        for fmt in info.get("formats", []):
            if fmt.get("url"):
                results.append({
                    "format_id": fmt.get("format_id", ""),
                    "ext": fmt.get("ext", ""),
                    "resolution": fmt.get("resolution", ""),
                    "url": fmt["url"],
                    "protocol": fmt.get("protocol", ""),
                    "filesize": fmt.get("filesize"),
                })
        return results

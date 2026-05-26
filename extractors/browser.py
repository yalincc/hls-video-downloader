"""
Playwright 浏览器提取器
启动无头浏览器访问页面，监听网络请求，拦截 .m3u8 / .mp4 等视频地址。

适用于 JS 动态渲染的站点（yt-dlp 不支持的）。

用法:
  from extractors.browser import BrowserExtractor
  ext = BrowserExtractor()
  url = ext.extract("https://some-site.com/video/123")
  # → "https://cdn.example.com/video/index.m3u8"

依赖: pip install playwright && playwright install chromium
"""

import asyncio
import re
import time
from typing import Optional


class BrowserError(Exception):
    """浏览器提取失败"""


class BrowserNotFound(BrowserError):
    """Playwright 未安装"""


VIDEO_PATTERNS = [
    r'https?://[^"\'<>\s]+\.m3u8[^"\'<>\s]*',
    r'https?://[^"\'<>\s]+\.mp4[^"\'<>\s]*',
    r'https?://[^"\'<>\s]+\.ts[^"\'<>\s]*',
    r'https?://[^"\'<>\s]+\.flv[^"\'<>\s]*',
    r'https?://[^"\'<>\s]+\.mkv[^"\'<>\s]*',
    r'https?://[^"\'<>\s]+\.webm[^"\'<>\s]*',
]


class BrowserExtractor:
    """Playwright 浏览器提取器"""

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 30000,
        referer: str | None = None,
        user_agent: str | None = None,
    ):
        """
        Args:
            headless: 是否无头模式
            timeout_ms: 页面加载超时（毫秒）
            referer: 自定义 Referer
            user_agent: 自定义 UA
        """
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._referer = referer
        self._user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        )

    @property
    def available(self) -> bool:
        """Playwright 是否可用"""
        try:
            import importlib
            importlib.import_module("playwright")
            return True
        except ImportError:
            return False

    def extract(self, url: str) -> str:
        """从页面提取视频地址

        Returns:
            视频地址（m3u8 优先，其次 mp4）

        Raises:
            BrowserNotFound: Playwright 未安装
            BrowserError: 提取失败
        """
        return asyncio.run(self._extract_async(url))

    def extract_all(self, url: str) -> list[str]:
        """提取页面上所有匹配的视频地址"""
        return asyncio.run(self._extract_async(url, all_urls=True))

    async def _extract_async(self, url: str, all_urls: bool = False):
        """异步核心逻辑"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserNotFound(
                "未安装 playwright。请执行:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

        captured_urls: list[str] = []

        async def on_request(request):
            """拦截所有网络请求"""
            req_url = request.url
            for pattern in VIDEO_PATTERNS:
                if re.search(pattern, req_url, re.IGNORECASE):
                    if req_url not in captured_urls:
                        captured_urls.append(req_url)

        async def on_response(response):
            """拦截所有响应（有些站点 URL 只在响应里出现）"""
            resp_url = response.url
            content_type = response.headers.get("content-type", "")
            if (
                "mpegurl" in content_type
                or "vnd.apple.mpegurl" in content_type
                or "video" in content_type
            ):
                if resp_url not in captured_urls:
                    captured_urls.append(resp_url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self._headless)
            context = await browser.new_context(
                user_agent=self._user_agent,
                referer=self._referer or url,
                # 允许视频自动播放
                bypass_csp=True,
            )
            page = await context.new_page()

            # 监听网络请求和响应
            page.on("request", on_request)
            page.on("response", on_response)

            try:
                # 导航到目标页面
                await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)

                # 等待一段时间让视频请求发出
                await asyncio.sleep(3)

                # 尝试滚动页面触发懒加载
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                await asyncio.sleep(1)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

            except Exception as e:
                # 即使超时也可能已经捕获到请求
                if not captured_urls:
                    await browser.close()
                    raise BrowserError(f"页面加载失败: {e}")

            await browser.close()

        if not captured_urls:
            raise BrowserError(
                f"未在页面中捕获到视频地址。页面可能使用 WebSocket 或其他协议。\n"
                f"  提示: 尝试手动打开浏览器开发者工具 → Network → 筛选 m3u8/mp4"
            )

        if all_urls:
            return captured_urls

        # 优先返回 m3u8，其次 mp4
        m3u8_urls = [u for u in captured_urls if ".m3u8" in u.lower()]
        if m3u8_urls:
            # 返回最可能是主视频的 m3u8（通常是最长的或包含特定关键词的）
            preferred = [u for u in m3u8_urls if "master" not in u.lower() and "index" not in u.lower()]
            if preferred:
                return preferred[0]
            return m3u8_urls[0]

        mp4_urls = [u for u in captured_urls if ".mp4" in u.lower()]
        if mp4_urls:
            mp4_urls.sort(key=len, reverse=True)  # 最长的通常是完整视频
            return mp4_urls[0]

        return captured_urls[0]

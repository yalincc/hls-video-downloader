"""
HTTP + 正则提取器（轻量快速兜底方案）

直接 HTTP GET 页面 HTML，用正则找出 .m3u8 / .mp4 链接。
不需要浏览器、不需要 yt-dlp，零额外依赖（除 requests）。

支持的站点模式:
  - 直接在 HTML 里写死 m3u8/mp4 链接
  - JS 变量中嵌入视频 URL
  - 双重 URL 编码的嵌套页面（常见于中文成人站）
  - 详情页 → 播放页的跳转模式（自动跟踪播放链接）
  - iframe 内嵌播放页

用法:
  from extractors.http import HttpExtractor
  ext = HttpExtractor()
  url = ext.extract("https://simple-site.com/video/123")
"""

import re
from urllib.parse import unquote, urljoin

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


class HttpError(Exception):
    """HTTP 提取失败"""


class HttpExtractor:
    """纯 HTTP 请求 + 正则提取视频地址"""

    def __init__(
        self,
        referer: str | None = None,
        user_agent: str | None = None,
        timeout: int = 15,
    ):
        self._referer = referer
        self._user_agent = user_agent or UA
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return True

    def extract(self, url: str) -> str:
        urls = self.extract_all(url)
        if not urls:
            raise HttpError(f"未在页面中找到视频地址: {url}")
        return urls[0]

    def extract_all(self, url: str) -> list[str]:
        """提取页面上所有匹配的视频地址

        策略（按顺序尝试）:
          1. 直接找 .m3u8 / .mp4 完整 URL
          2. 双重 URL 编码内容解码后搜索
          3. JS 变量中的视频 URL
          4. 跟踪"播放"链接（详情页 → 播放页）
          5. iframe 内嵌页面递归
        """
        headers = {"User-Agent": self._user_agent}
        if self._referer:
            headers["Referer"] = self._referer

        html = self._fetch(url, headers)
        all_pages = [html]

        # 策略 2: 双重 URL 编码解码
        decoded = self._try_decode_embedded(html)
        if decoded:
            all_pages.append(decoded)

        # 在所有页面内容中搜索
        found: list[str] = []

        # 策略 1: 直接找 .m3u8 / .mp4
        for page_html in all_pages:
            for pat in [
                r'https?://[^"\'<>\s]+\.m3u8[^"\'<>\s]*',
                r'https?://[^"\'<>\s]+\.mp4[^"\'<>\s]*',
            ]:
                for m in re.finditer(pat, page_html, re.IGNORECASE):
                    u = m.group(0).rstrip("\\/")
                    if u not in found:
                        found.append(u)

        if found:
            return self._prioritize(found)

        # 策略 3: JS 变量中的视频 URL
        for page_html in all_pages:
            for pat in [
                r'["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']',
                r'video[_\-]?url["\']?\s*[:=]\s*["\'](https?://[^"\']+)["\']',
                r'url["\']?\s*[:=]\s*["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']',
                r'src["\']?\s*[:=]\s*["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']',
                r'player_[^=]+=\s*["\'](https?://[^"\']+)["\']',
                r'"url"\s*:\s*"(https?://[^"]+\.(?:m3u8|mp4)[^"]*)"',
            ]:
                for m in re.finditer(pat, page_html, re.IGNORECASE):
                    u = m.group(1).rstrip("\\/")
                    if u not in found:
                        found.append(u)

        if found:
            return self._prioritize(found)

        # 策略 4: 跟踪播放链接（详情页 → 播放页）
        # 在所有页面内容（含解码后）中搜索播放链接
        play_urls: list[str] = []
        for page_html in all_pages:
            play_urls.extend(self._find_play_links(page_html, url))
        play_urls = list(dict.fromkeys(play_urls))  # 去重保序
        for play_url in play_urls[:3]:  # 最多尝试3个
            try:
                play_html = self._fetch(play_url, headers)
                # 直接搜索 m3u8/mp4
                for pat in [
                    r'https?://[^"\'<>\s]+\.m3u8[^"\'<>\s]*',
                    r'https?://[^"\'<>\s]+\.mp4[^"\'<>\s]*',
                ]:
                    for m in re.finditer(pat, play_html, re.IGNORECASE):
                        u = m.group(0).rstrip("\\/")
                        if u not in found:
                            found.append(u)

                if found:
                    return self._prioritize(found)

                # Also decode if needed
                play_decoded = self._try_decode_embedded(play_html)
                if play_decoded:
                    for pat in [
                        r'https?://[^"\'<>\s]+\.m3u8[^"\'<>\s]*',
                        r'https?://[^"\'<>\s]+\.mp4[^"\'<>\s]*',
                    ]:
                        for m in re.finditer(pat, play_decoded, re.IGNORECASE):
                            u = m.group(0).rstrip("\\/")
                            if u not in found:
                                found.append(u)
                    if found:
                        return self._prioritize(found)

            except Exception:
                continue

        # 策略 5: iframe 递归
        for page_html in all_pages:
            iframe_pattern = r'<iframe[^>]+src=["\']([^"\']+)["\']'
            for m in re.finditer(iframe_pattern, page_html):
                iframe_src = m.group(1)
                if not iframe_src.startswith("http"):
                    iframe_src = urljoin(url, iframe_src)
                try:
                    iframe_html = self._fetch(iframe_src, headers)
                    for pat in [
                        r'https?://[^"\'<>\s]+\.m3u8[^"\'<>\s]*',
                        r'https?://[^"\'<>\s]+\.mp4[^"\'<>\s]*',
                    ]:
                        for m2 in re.finditer(pat, iframe_html, re.IGNORECASE):
                            u = m2.group(0).rstrip("\\/")
                            if u not in found:
                                found.append(u)
                    if found:
                        break
                except Exception:
                    continue

        return self._prioritize(found)

    def _try_decode_embedded(self, html: str) -> str | None:
        """尝试解码双重 URL 编码的嵌套页面"""
        # 模式: var _h="%253C%2521DOCTYPE%2520html%253E..."
        m = re.search(r'var\s+\w+\s*=\s*"(%25[0-9A-F]{2}[^"]{500,})"', html)
        if not m:
            return None
        try:
            encoded = m.group(1)
            d1 = unquote(encoded)
            d2 = unquote(d1)
            if len(d2) > 500 and '<' in d2:
                return d2
        except Exception:
            pass
        return None

    def _find_play_links(self, html: str, base_url: str) -> list[str]:
        """从详情页提取播放页链接"""
        play_urls: list[str] = []

        # 常见播放链接模式
        patterns = [
            r'<a[^>]+href=["\']([^"\']*play[^"\']*)["\'][^>]*>(?:立即播放|在线播放|播放|观看)',
            r'<a[^>]+href=["\']([^"\']*play[^"\']*)["\']',
            r'href=["\']([^"\']*play[^"\']*-\d+\.html)["\']',
            r'href=["\']([^"\']*play[^"\']*-\d+-\d+\.html)["\']',
        ]

        for pat in patterns:
            for m in re.finditer(pat, html, re.IGNORECASE):
                href = m.group(1)
                if not href.startswith("http"):
                    href = urljoin(base_url, href)
                if href not in play_urls:
                    play_urls.append(href)

        return play_urls

    def _fetch(self, url: str, headers: dict) -> str:
        """HTTP GET 并返回文本"""
        resp = requests.get(url, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        return resp.text

    def extract_title(self, url: str) -> str:
        """从页面提取标题（用于自动命名）

        策略:
          1. <title> 标签
          2. og:title / meta title
        """
        headers = {"User-Agent": self._user_agent}
        if self._referer:
            headers["Referer"] = self._referer

        try:
            html = self._fetch(url, headers)
        except Exception:
            return ""

        # 先尝试解码嵌套页面 — 解码后的内容标题更可靠
        decoded = self._try_decode_embedded(html)
        search_in = []
        if decoded:
            search_in.append(decoded)  # 优先搜解码后的
        search_in.append(html)         # 原始兜底

        for page in search_in:
            # <title>...</title>
            m = re.search(r'<title[^>]*>([^<]+)</title>', page, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
                if title:
                    return title

            # og:title
            m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', page, re.IGNORECASE)
            if m:
                return m.group(1).strip()

        return ""

    def extract_with_meta(self, url: str) -> tuple[str, str]:
        """提取视频地址 + 标题

        Returns:
            (video_url, title)
        """
        video_url = self.extract(url)

        # 标题从原始 URL 页面提取
        title = self.extract_title(url)
        if not title:
            # 可能播放页才有标题，尝试从找到 m3u8 的页面提取
            # 用原始 URL 的域名拼一个简单的标题
            from urllib.parse import urlparse
            title = urlparse(url).netloc

        return video_url, title

    def _prioritize(self, urls: list[str]) -> list[str]:
        """m3u8 优先，排除 master playlist"""
        m3u8 = [u for u in urls if ".m3u8" in u.lower()]
        mp4 = [u for u in urls if ".mp4" in u.lower()]
        other = [u for u in urls if u not in m3u8 and u not in mp4]

        # m3u8: 排除 master，优先 index/media
        m3u8_preferred = [u for u in m3u8 if "master" not in u.lower()]
        if not m3u8_preferred:
            m3u8_preferred = m3u8

        return m3u8_preferred + mp4 + other

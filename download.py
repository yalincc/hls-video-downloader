"""
通用视频下载器 — CLI 入口

自动检测视频类型并选择最佳下载策略：

  python download.py <url>                    # 自动选择策略
  python download.py <url> --strategy ytdlp   # 强制 yt-dlp
  python download.py <url> --strategy browser # 强制 Playwright 浏览器
  python download.py <url> --strategy http    # 强制 HTTP 正则
  python download.py <url> --workers 16       # 16 线程
  python download.py <url> --output my.mp4    # 指定输出文件
  python download.py <url> --force            # 强制重新下载（忽略去重缓存）
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from extractors.ytdlp import YtdlpExtractor, YtdlpError, YtdlpNotFound
from extractors.browser import BrowserExtractor, BrowserError, BrowserNotFound
from extractors.http import HttpExtractor, HttpError
from engines.m3u8 import M3u8Downloader
from engines.direct import DirectDownloader
from processor import ts_to_mp4, is_ffmpeg_available

# ── 文件名清理 ──────────────────────────────────────────

def _clean_filename(title: str, max_len: int = 60) -> str:
    """从标题中提取安全文件名

    "04年原创博主，身材天花板「一只顶美」《红色内衣黑丝网袜回归》..."
    → "04年原创博主-身材天花板-一只顶美-红色内衣黑丝网袜回归"
    """
    # 去掉常见的站点后缀
    for sep in (" - ", " | ", " — ", "｜", "_-_"):
        if sep in title:
            parts = title.split(sep)
            # 去掉明显是站点名的部分（含"网""在线""视频"等）
            title = parts[0]
            for p in parts[1:]:
                if not re.search(r'(网|在线|视频|电影|播放|直播|社区|成人)', p):
                    title = p if len(p) > len(title) else title
                    break

    # 替换各种括号
    title = re.sub(r'[「『【（\(](.*?)[」』】）\)]', r'-\1-', title)
    title = re.sub(r'[《〈](.*?)[》〉]', r'-\1-', title)

    # 去掉非法文件名字符
    title = re.sub(r'[\\/:*?"<>|]', '', title)

    # 空白 → 空格
    title = re.sub(r'\s+', ' ', title).strip()

    # 标点换横线
    title = re.sub(r'[,，。、；;：:!！?？…\.]+', '-', title)
    title = re.sub(r'-{2,}', '-', title).strip('-')

    if not title:
        return "video"

    # 截断
    if len(title) > max_len:
        # 尽量在标点处截断
        truncated = title[:max_len]
        last_dash = truncated.rfind('-')
        if last_dash > max_len // 2:
            truncated = truncated[:last_dash]
        title = truncated

    return title.strip('-')


# ── 去重缓存 ────────────────────────────────────────────

def _load_dedup(cache_path: Path) -> dict:
    """加载去重缓存 {m3u8_url_hash: {output, title, time}}"""
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_dedup(cache_path: Path, cache: dict):
    """保存去重缓存"""
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')


def _hash_url(url: str) -> str:
    """URL 短哈希"""
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ── 提取策略 ────────────────────────────────────────────

def _try_ytdlp(url: str) -> tuple[str, str]:
    """策略 1: yt-dlp 提取 → (video_url, title)"""
    print("[策略 1/3] yt-dlp 提取...", flush=True)
    ext = YtdlpExtractor()
    info = ext.extract(url)
    fmt = info.get("formats", [{}])[-1] if info.get("formats") else {}
    video_url = fmt.get("url", "")
    if not video_url:
        video_url, fmt = ext.get_best_url(url)
    title = info.get("title", "")
    print(f"  [OK] {info.get('title', '')[:40]} | {fmt.get('ext')} {fmt.get('resolution', '')}", flush=True)
    return video_url, title


def _try_browser(url: str) -> tuple[str, str]:
    """策略 2: Playwright 浏览器提取"""
    print("[策略 2/3] Playwright 浏览器提取...", flush=True)
    ext = BrowserExtractor()
    if not ext.available:
        raise BrowserNotFound("Playwright 未安装")
    video_url = ext.extract(url)
    print(f"  [OK] 捕获: {video_url[:80]}...", flush=True)
    return video_url, ""


def _try_http(url: str) -> tuple[str, str]:
    """策略 3: HTTP 正则提取"""
    print("[策略 3/3] HTTP 正则提取...", flush=True)
    ext = HttpExtractor()
    video_url, title = ext.extract_with_meta(url)
    print(f"  [OK] 找到: {video_url[:80]}...", flush=True)
    if title:
        print(f"  [TITLE] 标题: {title[:60]}", flush=True)
    return video_url, title


def _is_m3u8_url(url: str) -> bool:
    return ".m3u8" in url.lower() or ".m3u" in url.lower()


# ── 主流程 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="通用视频下载器 — 自动提取并下载视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python download.py https://example.com/video/123
  python download.py https://example.com/video/123 --workers 16
  python download.py https://example.com/video/123 --output my_video.mp4
  python download.py https://example.com/video/123 --force
        """,
    )
    parser.add_argument("url", help="视频页面链接")
    parser.add_argument("--m3u8", default=None, help="直接指定 m3u8 地址（跳过提取，直接下载）")
    parser.add_argument("--strategy", choices=["auto", "ytdlp", "browser", "http"],
                        default="auto", help="提取策略（默认: auto）")
    parser.add_argument("--output", "-o", default=None, help="输出文件名（默认: 从标题自动生成）")
    parser.add_argument("--output-dir", "-d", default="downloads", help="输出目录（默认: downloads）")
    parser.add_argument("--workers", "-w", type=int, default=8, help="并发线程数（默认: 8）")
    parser.add_argument("--referer", "-r", default=None, help="Referer 请求头")
    parser.add_argument("--no-resume", action="store_true", help="禁用断点续传")
    parser.add_argument("--no-convert", action="store_true", help="不自动转 mp4")
    parser.add_argument("--force", "-f", action="store_true", help="强制重新下载（忽略去重缓存）")

    args = parser.parse_args()
    url = args.url.strip()

    if not url.startswith("http"):
        print("[ERROR] 请输入完整 URL（以 http:// 或 https:// 开头）")
        sys.exit(1)

    # ── 第一步: 提取真实视频地址 ──
    if args.m3u8:
        # 直传模式：跳过提取
        video_url = args.m3u8
        title = ""
        print(f"[直传] m3u8: {video_url[:80]}...", flush=True)
    else:
        strategies = {
            "auto": [_try_ytdlp, _try_browser, _try_http],
            "ytdlp": [_try_ytdlp],
            "browser": [_try_browser],
            "http": [_try_http],
        }

        video_url = None
        errors = []

        for strategy_fn in strategies[args.strategy]:
            try:
                video_url, title = strategy_fn(url)
                break
            except (YtdlpNotFound, BrowserNotFound):
                errors.append(f"{strategy_fn.__name__}: 依赖未安装")
                continue
            except (YtdlpError, BrowserError, HttpError) as e:
                errors.append(f"{strategy_fn.__name__}: {e}")
                continue

        if not video_url:
            print("\n[ERROR] 所有提取策略均失败:")
            for err in errors:
                print(f"  - {err}")
            sys.exit(1)

    # ── 去重检查 ──
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dedup_path = output_dir / ".downloads.json"
    dedup = _load_dedup(dedup_path)
    dedup_key = _hash_url(video_url)

    if not args.force and dedup_key in dedup:
        cached = dedup[dedup_key]
        existing_file = output_dir / cached.get("output", "")
        if existing_file.exists():
            print(f"\n⏭ 已下载过: {cached.get('title', cached['output'])}")
            print(f"   {existing_file} ({existing_file.stat().st_size / 1024 / 1024:.1f} MB)")
            print(f"   如需重新下载: --force")
            sys.exit(0)

    # ── 第二步: 确定输出文件名 ──
    if args.output:
        out_name = Path(args.output)
        if out_name.suffix:
            out_path = output_dir / out_name
        else:
            out_path = output_dir / f"{out_name.stem}.ts"
    elif title:
        safe_name = _clean_filename(title)
        # 如果和已有文件冲突，加哈希后缀
        out_path = output_dir / f"{safe_name}.ts"
        if out_path.exists():
            short_hash = dedup_key[:6]
            out_path = output_dir / f"{safe_name}_{short_hash}.ts"
    else:
        import time
        vid_id = str(int(time.time()))
        out_path = output_dir / f"video_{vid_id}.ts"

    # ── 第三步: 下载 ──
    if _is_m3u8_url(video_url):
        print(f"\n[下载] m3u8 流 → {out_path.name}", flush=True)
        dl = M3u8Downloader(workers=args.workers, referer=args.referer)
        dl.download(video_url, output=out_path, no_resume=args.no_resume)
    else:
        print(f"\n[下载] 直链 → {out_path.name}", flush=True)
        dl = DirectDownloader(segments=args.workers, referer=args.referer)
        dl.download(video_url, output=out_path, no_resume=args.no_resume)

    # ── 第四步: 记录去重缓存 ──
    dedup[dedup_key] = {
        "output": out_path.name,
        "title": title[:80] if title else out_path.stem,
        "time": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_dedup(dedup_path, dedup)

    # ── 第五步: 转为 mp4 ──
    final_path = out_path
    if not args.no_convert and is_ffmpeg_available():
        mp4_path = out_path.with_suffix(".mp4")
        ts_to_mp4(out_path, output_path=mp4_path, delete_source=True)
        final_path = mp4_path
        # 更新缓存中的文件名
        dedup[dedup_key]["output"] = mp4_path.name
        _save_dedup(dedup_path, dedup)
    elif not args.no_convert:
        print(f"  [WARN] 未安装 ffmpeg，跳过转换。", flush=True)
        print(f"  手动: ffmpeg -i {out_path} -c copy {out_path.with_suffix('.mp4')}", flush=True)

    print(f"\n[FILE] {final_path.resolve()}", flush=True)


if __name__ == "__main__":
    main()

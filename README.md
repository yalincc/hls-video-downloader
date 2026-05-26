# Video Downloader

> One URL ¡ú auto detect ¡ú multi-threaded download ¡ú playable MP4

Supports YouTube, Bilibili, Pornhub, xvideos, and any m3u8/HLS streaming site.

## Features

- **Three-tier extraction** ¡ª yt-dlp ¡ú Playwright ¡ú HTTP regex, auto fallback
- **Multi-threaded** ¡ª concurrent segment / HTTP Range download
- **AES-128 decryption** ¡ª encrypted HLS streams
- **Resume support** ¡ª pick up where you left off
- **Auto MP4 conversion** ¡ª ffmpeg remux, ready to play
- **Smart naming** ¡ª extracts title from page for filename
- **Dedup cache** ¡ª same m3u8 won't download twice
- **Web UI** ¡ª browser-based queue management with live progress

## Quick Start

```bash
pip install -r requirements.txt
python download.py "https://example.com/video/123"
```

Requires **ffmpeg** for MP4 conversion:
- macOS: `brew install ffmpeg`
- Ubuntu: `sudo apt install ffmpeg`
- Windows: download from https://ffmpeg.org

## Usage

### CLI

```bash
python download.py <url>                        # auto-detect, 8 threads
python download.py <url> --workers 12           # 12 threads
python download.py <url> -d ./videos            # custom output dir
python download.py <url> -o my-video            # custom filename
python download.py <url> --force                # re-download
python download.py <url> --m3u8 <m3u8-url>      # skip extraction
```

### Web UI

```bash
python run.py
# Opens http://127.0.0.1:5001 ¡ª paste URLs, watch progress
```

### Optional: Playwright

```bash
pip install playwright
playwright install chromium
```

## How It Works

```
URL input
  ¡ú yt-dlp (1800+ sites) ¡ú Playwright (JS sites) ¡ú HTTP regex (fallback)
  ¡ú m3u8 segment / direct download (multi-threaded + resume)
  ¡ú ffmpeg TS¡úMP4 conversion
  ¡ú playable MP4 file
```

## Project Structure

```
download.py          CLI entry point
run.py               Web UI launcher
run.bat              Windows one-click start
processor.py         ffmpeg post-processing
extractors/          URL extraction layer
engines/             Download engines
web/                 Web interface (Flask)
requirements.txt     Python dependencies
```

## Performance

| Threads | Speed | Recommendation |
|---------|-------|---------------|
| 8 | 4.0 MB/s | Default |
| 16 | 4.2 MB/s | Optimal |
| 32+ | Varies | Diminishing returns |

## License

MIT

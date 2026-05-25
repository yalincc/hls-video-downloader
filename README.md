# HLS Video Downloader

📥 网页 HLS/m3u8 流视频下载工具。自动提取 m3u8 地址，并发下载 TS 分片并合并为单个视频文件。

支持 **AES-128 加密流**、**断点续传**、**进度条**、**Web 界面**。

## 功能特性

- 🔗 **自动提取 m3u8** — 通过 Playwright 从视频页面自动提取真实播放地址
- ⚡ **并发下载** — 多线程下载 TS 分片，显著提速
- 🔐 **AES-128 解密** — 自动检测并解密加密的 HLS 流
- 🔄 **断点续传** — 中断后重新运行会跳过已下载的分片
- 📊 **百分比进度条** — CLI 使用 tqdm 实时显示进度、速度、剩余时间
- 🌐 **Web 界面** — 浏览器操作，支持队列管理、停止任务、选择输出目录
- 📋 **批量处理** — 从文本文件批量下载，自动跳过已完成的
- 🛑 **优雅停止** — Ctrl+C 或 Web 界面按钮，安全中断

## 快速开始

### 环境要求

- **Python 3.9+**
- **Node.js 18+** + `playwright-cli`（网页端自动提取 m3u8 时需要）
- **Git**（可选，用于版本管理）

### 安装

```bash
# 1. 克隆项目
git clone https://github.com/yourname/hls-video-downloader.git
cd hls-video-downloader

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 安装 Playwright（用于自动提取 m3u8）
npm install -g @playwright/cli
playwright-cli install
```

### 命令行下载

```bash
# 直接下载（已知 m3u8 地址）
python download_video.py --url https://example.com/video/index.m3u8 --output video.ts

# 带 Referer（防盗链网站）
python download_video.py --url https://example.com/video/index.m3u8 \
    --referer https://example.com/ \
    --output video.ts

# 自定义输出目录和并发数
python download_video.py --url <m3u8> \
    --output-dir ./my_videos \
    --output movie.ts \
    --workers 8
```

### 批量下载

准备 `urls.txt`，每行一个视频页面链接：

```bash
# 批量下载（严格顺序，逐个处理）
bash batch_download.sh urls.txt

# 指定 Referer 和输出目录
bash batch_download.sh urls.txt --referer https://example.com/ --output-dir ./videos
```

### Web 界面

```bash
# 一键启动（后台 worker + Web 服务）
# Windows:
双击 web_downloader/run.bat

# macOS / Linux:
bash web_downloader/run.sh

# 或分别启动：
python web_downloader/worker.py    # 后台下载守护进程
python web_downloader/server.py    # Web 服务 → http://localhost:5001
```

打开 `http://localhost:5001`，粘贴链接即可加入下载队列。

## 文件结构

```
├── download_video.py              # 核心下载引擎
│   ├── --url          m3u8 播放列表地址（必填）
│   ├── --referer      Referer 请求头（防盗链网站必填）
│   ├── --output       输出文件名（默认: video.ts）
│   ├── --output-dir   输出目录（默认: 当前目录）
│   ├── --workers      并发线程数（默认: 10）
│   ├── --retry        单分片重试次数（默认: 3）
│   ├── --no-resume    禁用断点续传
│   └── --status-file  进度状态文件（JSON，供 Web 界面读取）
│
├── batch_download.sh              # 批量顺序下载脚本
│   ├── --output-dir  输出目录
│   ├── --referer     Referer 请求头
│   └── --workers     并发线程数
│
├── web_downloader/
│   ├── server.py                  # Flask Web 服务（端口 5001）
│   ├── worker.py                  # 后台下载守护进程
│   ├── templates/
│   │   └── index.html             # Web 界面
│   ├── run.bat                    # Windows 一键启动
│   └── run.sh                     # macOS/Linux 一键启动
│
├── requirements.txt               # Python 依赖
├── urls.txt.example               # 示例 URL 文件
└── video_download/                # 默认输出目录
```

## 输出文件

下载完成后得到 `.ts` (MPEG Transport Stream) 文件，可用以下播放器直接打开：

- **VLC** — 推荐，直接拖入即可播放
- **PotPlayer**
- **MPC-HC**

转为 mp4（需安装 ffmpeg）：

```bash
ffmpeg -i video.ts -c copy video.mp4
```

## 常见问题

### m3u8 服务器返回 404

防盗链限制，加上 Referer 头：
```bash
python download_video.py --url <m3u8> --referer https://example.com/
```

### 分片下载失败（ConnectionResetError）

CDN 限制并发。降低线程数：
```bash
python download_video.py --workers 3 ...
```

### 加密视频无法播放

工具会自动检测 `#EXT-X-KEY` 标签并解密 AES-128 加密流。如果解密后仍有问题，可能是密钥服务器需要额外的请求头。

### playwright 启动失败

```bash
# 清理旧的守护进程
playwright-cli kill-all

# Windows 强制清理
taskkill /f /im node.exe
```

## 工作原理

```
视频页面 → Playwright 提取 → m3u8 索引文件 → 解析分片列表
    ↓
并发下载 TS 分片（支持断点续传）
    ↓
AES-128 解密（如加密）
    ↓
合并 → 完整视频文件
```

大多数视频站使用 HLS (HTTP Live Streaming) 协议，视频被切成若干秒的 `.ts` 碎片，通过 `.m3u8` 文件索引。本工具自动化整个提取-下载-合并流程。

## License

MIT

#!/bin/bash
# ============================================
# HLS Video Downloader — 一键启动（macOS/Linux）
# 启动后台 worker + Web 服务
# ============================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BASE_DIR"

echo ""
echo "  ============================"
echo "  📥 HLS Video Downloader"
echo "  ============================"
echo ""

# 启动后台 Worker
echo "  ▶ 启动后台 Worker..."
python -u web_downloader/worker.py &
WORKER_PID=$!
echo "  ✓ Worker PID: $WORKER_PID"
echo ""

# 等一会儿再启动 Web
sleep 2

# 启动 Web 服务
echo "  ▶ 启动 Web 服务 (http://localhost:5001)"
echo ""
python -u web_downloader/server.py

# 退出时清理 Worker
trap "kill $WORKER_PID 2>/dev/null" EXIT

#!/bin/bash
# =====================================================
# HLS Video Downloader — 批量下载
# 用法:
#   bash batch_download.sh urls.txt
#   bash batch_download.sh urls.txt --output-dir ./my_videos
#   bash batch_download.sh urls.txt --referer https://example.com/
# =====================================================
set -euo pipefail

# ---- 路径：基于脚本所在目录 ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOWNLOADER="$SCRIPT_DIR/download_video.py"
OUT_DIR="${SCRIPT_DIR}/video_download"
LOG="$OUT_DIR/.batch_log.txt"
URL_FILE=""
REFERER=""
WORKERS=5

# ---- 解析参数 ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            OUT_DIR="$2"
            shift 2 ;;
        --referer)
            REFERER="$2"
            shift 2 ;;
        --workers)
            WORKERS="$2"
            shift 2 ;;
        --help|-h)
            echo "用法: bash batch_download.sh <urls.txt> [选项]"
            echo "  --output-dir <路径>   输出目录（默认: ./video_download）"
            echo "  --referer   <URL>     Referer 请求头（防盗链网站必填）"
            echo "  --workers   <数字>    并发线程数（默认: 5）"
            exit 0 ;;
        *)
            URL_FILE="$1"
            shift ;;
    esac
done

if [[ -z "$URL_FILE" ]]; then
    echo "❌ 请指定 URL 文件"
    echo "   用法: bash batch_download.sh urls.txt [--referer https://...]"
    exit 1
fi

if [[ ! -f "$URL_FILE" ]]; then
    echo "❌ 文件不存在: $URL_FILE"
    exit 1
fi

mkdir -p "$OUT_DIR"

# ---- 读取 URL 列表 ----
mapfile -t URLS < <(grep -v '^#' "$URL_FILE" | grep -E '^https?://' | sort -u)
TOTAL=${#URLS[@]}

if [[ $TOTAL -eq 0 ]]; then
    echo "❌ 文件中没有有效的 URL"
    exit 1
fi

SUCCESS=0
FAILED=0
SKIPPED=0

echo "==============================================" | tee "$LOG"
echo "📥 HLS Video Downloader — 批量下载" | tee -a "$LOG"
echo "   文件: $URL_FILE" | tee -a "$LOG"
echo "   输出: $OUT_DIR" | tee -a "$LOG"
echo "   总数: $TOTAL 个视频" | tee -a "$LOG"
[[ -n "$REFERER" ]] && echo "   Referer: $REFERER" | tee -a "$LOG"
echo "==============================================" | tee -a "$LOG"
echo "" | tee -a "$LOG"

for i in "${!URLS[@]}"; do
    URL="${URLS[$i]}"
    NUM=$((i + 1))

    # 生成文件名：从 URL 中提取 ID 或序号
    VID_ID=$(echo "$URL" | grep -oP '\b\d{5,}\b' | head -1)
    [[ -z "$VID_ID" ]] && VID_ID=$(printf "%03d" "$NUM")
    OUT_FILE="$OUT_DIR/video_${VID_ID}.ts"

    echo "" | tee -a "$LOG"
    echo "[$NUM/$TOTAL] $URL" | tee -a "$LOG"

    # 跳过已下载
    if [[ -f "$OUT_FILE" ]]; then
        SIZE=$(du -h "$OUT_FILE" | cut -f1)
        echo "  ⏭ 已存在 ($SIZE)" | tee -a "$LOG"
        ((SKIPPED++))
        continue
    fi

    # 构建下载命令
    CMD=("python" "-u" "$DOWNLOADER" "--url" "$URL" "--output" "$OUT_FILE" "--workers" "$WORKERS")
    [[ -n "$REFERER" ]] && CMD+=("--referer" "$REFERER")

    echo "  📥 下载中..." | tee -a "$LOG"
    if "${CMD[@]}" 2>&1 | tee -a "$LOG" | tail -5; then
        if [[ -f "$OUT_FILE" ]]; then
            ((SUCCESS++))
            echo "  ✅ 完成" | tee -a "$LOG"
        else
            ((FAILED++))
            echo "  ❌ 失败" | tee -a "$LOG"
        fi
    else
        ((FAILED++))
        echo "  ❌ 下载出错" | tee -a "$LOG"
    fi
done

echo "" | tee -a "$LOG"
echo "==============================================" | tee -a "$LOG"
echo "📊 成功: $SUCCESS | 失败: $FAILED | 跳过: $SKIPPED | 总计: $TOTAL" | tee -a "$LOG"
echo "📁 输出目录: $OUT_DIR" | tee -a "$LOG"
echo "==============================================" | tee -a "$LOG"

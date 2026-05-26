"""
ffmpeg 后处理模块
负责将下载的临时文件转为可直接播放的 MP4。

功能:
- TS 合并 → MP4（无损 remux）
- 音视频分离流合并
- 格式转码（可选）
- 自动清理临时文件
"""

import os
import subprocess
from pathlib import Path


class ProcessorError(Exception):
    """后处理失败"""


class FfmpegNotFound(ProcessorError):
    """未找到 ffmpeg"""


def _find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件"""
    for cmd in ("ffmpeg", "ffmpeg.exe"):
        try:
            result = subprocess.run(
                [cmd, "-version"], capture_output=True, timeout=10
            )
            if result.returncode == 0:
                return cmd
        except FileNotFoundError:
            continue
    raise FfmpegNotFound(
        "未找到 ffmpeg。请安装: https://ffmpeg.org/download.html\n"
        "  macOS: brew install ffmpeg\n"
        "  Ubuntu: sudo apt install ffmpeg\n"
        "  Windows: choco install ffmpeg 或从官网下载"
    )


def ts_to_mp4(input_path: Path, output_path: Path | None = None, delete_source: bool = True) -> Path:
    """TS 无损转为 MP4（remux，不重新编码）

    Args:
        input_path: .ts 文件路径
        output_path: 输出 .mp4 路径，默认同目录同名 .mp4
        delete_source: 成功后是否删除源 .ts

    Returns:
        输出文件 Path
    """
    ffmpeg = _find_ffmpeg()

    if output_path is None:
        output_path = input_path.with_suffix(".mp4")

    print(f"  [CONVERT] 转换: {input_path.name} → {output_path.name} (无损 remux)", flush=True)

    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-c", "copy",       # 不重新编码
        "-movflags", "+faststart",  # 网页快速播放
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        # 如果 copy 失败（编码不兼容），尝试重新编码
        print(f"  [WARN] 无损转换失败，尝试重新编码...", flush=True)
        cmd2 = [
            ffmpeg, "-y",
            "-i", str(input_path),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=1200)
        if result2.returncode != 0:
            raise ProcessorError(f"ffmpeg 转换失败:\n{result2.stderr[-500:]}")

    if delete_source and output_path.exists():
        try:
            os.remove(input_path)
            print(f"  [OK] 已清理源文件: {input_path.name}", flush=True)
        except OSError:
            pass

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  [DONE] 输出: {output_path.name} ({size_mb:.1f} MB)", flush=True)
    return output_path


def merge_audio_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path | None = None,
    delete_source: bool = True,
) -> Path:
    """合并音视频分离流

    常用于 B站 / YouTube 等 DASH 格式下载后的处理。

    Args:
        video_path: 纯视频文件
        audio_path: 纯音频文件
        output_path: 输出路径
        delete_source: 成功后是否删除源文件
    """
    ffmpeg = _find_ffmpeg()

    if output_path is None:
        output_path = video_path.with_name(
            video_path.stem.replace("_video", "") + ".mp4"
        )

    print(f"  [CONVERT] 合并音视频 → {output_path.name}", flush=True)

    cmd = [
        ffmpeg, "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        raise ProcessorError(f"音视频合并失败:\n{result.stderr[-500:]}")

    if delete_source:
        for f in (video_path, audio_path):
            if f.exists():
                try:
                    os.remove(f)
                except OSError:
                    pass

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  [DONE] 输出: {output_path.name} ({size_mb:.1f} MB)", flush=True)
    return output_path


def is_ffmpeg_available() -> bool:
    """检查 ffmpeg 是否可用"""
    try:
        _find_ffmpeg()
        return True
    except FfmpegNotFound:
        return False

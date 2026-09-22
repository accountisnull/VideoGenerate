import json
import subprocess
from pathlib import Path


def run(command):
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "媒体命令执行失败")
    return result.stdout


def self_check(folder: Path, tools: dict):
    """Generate synthetic test media only; no models or publishing calls."""
    if not tools.get("ffmpeg") or not tools.get("ffprobe"):
        raise RuntimeError("未找到 FFmpeg 或 ffprobe，请配置项目根目录 .env 中的 FFMPEG_PATH 和 FFPROBE_PATH")
    folder.mkdir(parents=True, exist_ok=True)
    ffmpeg = [tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y"]
    run(ffmpeg + [
        "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=25", "-t", "2",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / "picture.mp4"),
    ])
    run(ffmpeg + [
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "2",
        "-af", "volume=0.1", "-c:a", "pcm_s16le", str(folder / "audio.wav"),
    ])
    output = folder / "environment-check.mp4"
    run(ffmpeg + [
        "-i", str(folder / "picture.mp4"), "-i", str(folder / "audio.wav"),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
        "-movflags", "+faststart", "-shortest", str(output),
    ])
    info = json.loads(run([
        tools["ffprobe"], "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(output),
    ]))
    kinds = {stream["codec_type"] for stream in info["streams"]}
    duration = float(info["format"]["duration"])
    if not {"audio", "video"}.issubset(kinds) or not 1.8 <= duration <= 2.3:
        raise RuntimeError("测试 MP4 音视频流或时长不符合预期")
    return output, f"自检通过：MP4 包含视频和音轨，实测 {duration:.2f} 秒。仅测试图案和测试音，未调用模型或发布。"

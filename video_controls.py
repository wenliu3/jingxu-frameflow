"""Output dimensions and precise timing shared by generation and local publishing."""
from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import tempfile

RATIOS = {"16:9": (16, 9), "4:3": (4, 3), "1:1": (1, 1), "3:4": (3, 4), "9:16": (9, 16), "21:9": (21, 9)}


def image_size(path: str) -> tuple[int, int]:
    import imageio_ffmpeg
    reader = imageio_ffmpeg.read_frames(path)
    try:
        return tuple(next(reader)["size"])
    finally:
        reader.close()


def output_size(ratio: str, resolution: str, image: str, megapixels: float, *, force: bool = False) -> tuple[int, int] | None:
    if ratio == "auto" and resolution == "custom" and not force:
        return None
    if ratio not in RATIOS and ratio != "auto":
        raise ValueError("不支持的画幅比例")
    a, b = image_size(image) if ratio == "auto" else RATIOS[ratio]
    aspect = a / b
    if resolution == "custom":
        height = math.sqrt(megapixels * 1_000_000 / aspect)
        width = height * aspect
    else:
        short = {"480p": 480, "720p": 720}.get(resolution)
        if short is None:
            raise ValueError("不支持的清晰度")
        # Fit the ratio within the HD/SD envelope. Ultra-wide uses the same long edge.
        if aspect >= 1:
            width = min(short * 16 / 9, short * aspect)
            height = width / aspect
        else:
            height = min(short * 16 / 9, short / aspect)
            width = height * aspect
    return max(64, round(width / 2) * 2), max(64, round(height / 2) * 2)


def h3_frames(duration: float) -> int:
    return max(5, 17 * round((round(duration * 24) - 5) / 17) + 5)


def publish_video(source: str, output: str, *, width: int | None = None, height: int | None = None,
                  duration: float | None = None, generate_audio: bool = True) -> dict:
    """Atomically publish an adapted clip; never expose an incomplete conversion."""
    import imageio_ffmpeg
    root = Path(output).resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".video-publish-", dir=root) as folder:
        temporary = Path(folder).resolve()
        if temporary.parent != root:
            raise RuntimeError("无效的视频临时目录")
        result = temporary / "final.mp4"
        filters = []
        if width and height:
            filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")
        if duration is not None:
            filters += ["fps=24", f"tpad=stop_mode=clone:stop_duration={duration}"]
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-i", source, "-map", "0:v:0"]
        if filters:
            command += ["-vf", ",".join(filters)]
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
        if generate_audio:
            command += ["-map", "0:a?", "-c:a", "aac"]
            if duration is not None:
                command += ["-af", "apad"]
        else:
            command += ["-an"]
        if duration is not None:
            command += ["-t", str(duration)]
        command += ["-movflags", "+faststart", str(result)]
        process = subprocess.run(command, capture_output=True, timeout=600)
        if process.returncode or not result.is_file() or not result.stat().st_size:
            raise RuntimeError("视频画幅、音轨或时长处理失败，原有版本未受影响")
        reader = imageio_ffmpeg.read_frames(str(result))
        try:
            metadata = next(reader)
        finally:
            reader.close()
        os.replace(result, output)
        return {"width": metadata["size"][0], "height": metadata["size"][1], "actual_duration": metadata["duration"], "has_audio": bool(metadata.get("audio_codec"))}

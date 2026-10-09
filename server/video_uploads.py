"""Project-owned video uploads, normalized for browser playback and visual reference."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

MAX_VIDEO_BYTES = 100_000_000
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def _import_video(source: Path, staging: Path, directory: Path, task_id: str, filename: str):
    import imageio_ffmpeg
    from PIL import Image

    try:
        reader = imageio_ffmpeg.read_frames(str(source))
        try:
            metadata = next(reader)
            first_frame = next(reader)
        finally:
            reader.close()
        duration = float(metadata.get("duration", 0))
        width, height = metadata["size"]
        if not math.isfinite(duration) or duration <= 0 or duration > 120:
            raise HTTPException(422, "请上传两分钟以内的可播放视频")
        if min(width, height) < 2 or max(width, height) > 4096:
            raise HTTPException(422, "视频尺寸应在 2–4096 像素之间")
        if not math.isfinite(metadata["fps"]) or metadata["fps"] <= 0 or duration * metadata["fps"] < 5:
            raise HTTPException(422, "视频至少需要五帧")
        identifier = uuid.uuid4().hex[:12]
        name = f"upload_{identifier}.mp4"
        video = staging / name
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        process = subprocess.run([
            ffmpeg, "-y", "-v", "error", "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", str(video),
        ], capture_output=True, timeout=180)
        if process.returncode or not video.is_file() or not video.stat().st_size:
            raise ValueError("video conversion failed")
        reader = imageio_ffmpeg.read_frames(str(video))
        try:
            normalized = next(reader)
        finally:
            reader.close()
        poster = video.with_suffix(".jpg")
        image = Image.frombytes("RGB", (width, height), first_frame)
        image.thumbnail((960, 960))
        image.save(poster, quality=88)
        digest = hashlib.sha256()
        with video.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        record = {
            "id": identifier, "status": "succeeded", "origin": "upload", "file": name,
            "url": f"/files/{task_id}/video_uploads/{name}",
            "poster": f"/files/{task_id}/video_uploads/{poster.name}",
            "original_filename": filename, "width": normalized["size"][0], "height": normalized["size"][1],
            "actual_duration": normalized["duration"], "has_audio": bool(normalized.get("audio_codec")),
            "sha256": digest.hexdigest(),
        }
        manifest = video.with_suffix(".json")
        manifest.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        published = []
        try:
            for item in (video, poster, manifest):
                destination = directory / item.name
                os.replace(item, destination)
                published.append(destination)
        except OSError:
            for item in published:
                item.unlink(missing_ok=True)
            raise
        return record
    except HTTPException:
        raise
    except (OSError, ValueError, RuntimeError, StopIteration, subprocess.SubprocessError) as exc:
        raise HTTPException(422, "无法读取视频，请上传可播放的 MP4、MOV、WebM、MKV 或 M4V 文件") from exc


def register_video_upload_routes(app, task_lookup, out_dir):
    @app.post("/api/tasks/{task_id}/video-uploads")
    async def upload_video(task_id: str, request: Request, filename: str = "video.mp4"):
        task_lookup(task_id)
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:256]
        if Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
            raise HTTPException(422, "支持 MP4、MOV、WebM、MKV 和 M4V 视频")
        root = Path(out_dir(task_id)).resolve()
        directory = (root / "video_uploads").resolve()
        if directory.parent != root:
            raise HTTPException(422, "无效的视频保存目录")
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".upload-", dir=directory) as folder:
            staging = Path(folder)
            source = staging / ("source" + Path(filename).suffix.lower())
            size = 0
            with source.open("wb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise HTTPException(413, "每个视频不超过 100MB")
                    handle.write(chunk)
            if not size:
                raise HTTPException(400, "请选择视频文件")
            return await run_in_threadpool(_import_video, source, staging, directory, task_id, filename)

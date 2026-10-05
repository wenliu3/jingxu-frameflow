"""Background exports for an explicitly ordered list of project-local video segments."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class CanvasExportRequest(BaseModel):
    files: list[str] = Field(min_length=1, max_length=100)
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"


def resolve_clips(directory: str, names: list[str]) -> list[Path]:
    root = (Path(directory) / "segments").resolve()
    clips = []
    for name in names:
        if not re.fullmatch(r"[\w-]+\.mp4", name, flags=re.UNICODE):
            raise HTTPException(422, "只能导出当前作品的 MP4 视频片段")
        clip = (root / name).resolve()
        if clip.parent != root or not clip.is_file():
            raise HTTPException(404, f"视频片段不存在：{name}")
        clips.append(clip)
    return clips


def render_export(clips: list[Path], output: Path, aspect: str, ffmpeg: str, progress: Callable) -> None:
    import imageio_ffmpeg

    width, height = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}[aspect]
    temporary = Path(tempfile.mkdtemp(prefix=".canvas-export-", dir=output.parent))
    try:
        for index, clip in enumerate(clips):
            reader = imageio_ffmpeg.read_frames(str(clip))
            try:
                metadata = next(reader)
            finally:
                reader.close()
            command = [ffmpeg, "-y", "-v", "error", "-i", str(clip)]
            if not metadata.get("audio_codec"):
                command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            command += [
                "-map", "0:v:0", "-map", "0:a:0" if metadata.get("audio_codec") else "1:a:0",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", str(metadata["duration"]), "-shortest", str(temporary / f"clip_{index:03d}.mp4"),
            ]
            result = subprocess.run(command, capture_output=True, timeout=900)
            if result.returncode:
                raise RuntimeError(f"第 {index + 1} 个视频片段无法读取或转换")
            progress(index + 1)
        manifest = temporary / "concat.txt"
        manifest.write_text("".join(f"file 'clip_{i:03d}.mp4'\n" for i in range(len(clips))), encoding="utf-8")
        result = subprocess.run([
            ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(manifest),
            "-vf", "fps=24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", str(temporary / "final.mp4"),
        ], capture_output=True, timeout=600)
        rendered = temporary / "final.mp4"
        if result.returncode or not rendered.is_file() or rendered.stat().st_size == 0:
            raise RuntimeError("成片合并失败，请检查视频片段是否完整")
        os.replace(rendered, output)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def register_canvas_export_routes(app: FastAPI, task_lookup: Callable, out_dir: Callable, ffmpeg: Callable) -> None:
    jobs: dict[str, dict] = {}
    lock = threading.Lock()

    @app.post("/api/tasks/{task_id}/canvas-export")
    def start_export(task_id: str, body: CanvasExportRequest):
        task_lookup(task_id)
        clips = resolve_clips(out_dir(task_id), body.files)
        with lock:
            if any(job["task_id"] == task_id and job["status"] == "running" for job in jobs.values()):
                raise HTTPException(409, "这个作品已有成片正在导出，请稍后再试")
            for key in list(jobs):
                if len(jobs) < 100: break
                if jobs[key]["status"] != "running": del jobs[key]
            job_id = uuid.uuid4().hex[:12]
            job = {"job_id": job_id, "task_id": task_id, "status": "running", "done": 0, "total": len(clips), "error": "", "url": ""}
            jobs[job_id] = job

        def run():
            try:
                directory = Path(out_dir(task_id)) / "export"
                directory.mkdir(exist_ok=True)
                name = f"canvas_{job_id}.mp4"

                def progress(done):
                    with lock: job["done"] = done

                render_export(clips, directory / name, body.aspect, ffmpeg(), progress)
                with lock:
                    job.update(status="succeeded", url=f"/files/{task_id}/export/{name}")
            except Exception as exc:
                message = "导出等待超时，请减少镜头数量后重试" if isinstance(exc, subprocess.TimeoutExpired) else "成片导出失败，请检查视频文件和本地磁盘" if isinstance(exc, OSError) else str(exc)[:300]
                with lock: job.update(status="failed", error=message)

        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job_id}

    @app.get("/api/tasks/{task_id}/canvas-export/{job_id}")
    def export_status(task_id: str, job_id: str):
        task_lookup(task_id)
        with lock:
            job = jobs.get(job_id)
            if not job or job["task_id"] != task_id:
                raise HTTPException(404, "导出任务不存在，服务重启后可在作品的 export 目录查找成片")
            return dict(job)

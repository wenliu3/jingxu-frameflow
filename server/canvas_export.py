"""Background exports for an explicitly ordered list of project-local video segments."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator


class CanvasExportRequest(BaseModel):
    files: list[str] = Field(min_length=1, max_length=100)
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"


class CanvasExportClip(BaseModel):
    file: str = Field(max_length=200)
    order: int = Field(ge=1, le=300)
    title: str = Field(default="", max_length=1000)


class CanvasClipsRequest(BaseModel):
    clips: list[CanvasExportClip] = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def unique_orders(self):
        orders = [clip.order for clip in self.clips]
        if len(orders) != len(set(orders)):
            raise ValueError("镜头序号不能重复")
        return self


def safe_export_title(title: str) -> str:
    title = unicodedata.normalize("NFC", title)
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", title)
    title = re.sub(r"\s+", " ", title).strip(" ._")
    return title[:80].rstrip(" ._") or "镜头"


def clip_export_name(clip: CanvasExportClip, digits: int = 2) -> str:
    title = re.sub(r"^\s*\d{1,3}\s*[.、·_\-:：]\s*", "", clip.title)
    return f"{clip.order:0{digits}d}_{safe_export_title(title)}.mp4"


def pack_clips(clips: list[Path], metadata: list[CanvasExportClip], output: Path, progress: Callable) -> None:
    if len(clips) != len(metadata):
        raise ValueError("镜头信息与视频数量不一致")
    digits = max(2, len(str(max(item.order for item in metadata))))
    # MP4 is already compressed. Stream the originals into a ZIP without re-encoding.
    with tempfile.TemporaryDirectory(prefix=".canvas-package-", dir=output.parent) as temporary:
        archive = Path(temporary) / "clips.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            for index, (clip, item) in enumerate(zip(clips, metadata)):
                bundle.write(clip, arcname=clip_export_name(item, digits))
                progress(index + 1)
        os.replace(archive, output)


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

    def start_job(task_id: str, task: dict, clips: list[Path], mode: str, render: Callable):
        with lock:
            if any(job["task_id"] == task_id and job["status"] == "running" for job in jobs.values()):
                raise HTTPException(409, "这个作品已有视频正在导出，请稍后再试")
            for key in list(jobs):
                if len(jobs) < 100: break
                if jobs[key]["status"] != "running": del jobs[key]
            job_id = uuid.uuid4().hex[:12]
            title = safe_export_title((task.get("project") or {}).get("title") or "作品")
            suffix = "zip" if mode == "clips" else "mp4"
            download_name = f"{title}_{'镜头素材' if mode == 'clips' else '成片'}.{suffix}"
            job = {"job_id": job_id, "task_id": task_id, "mode": mode, "download_name": download_name, "status": "running", "done": 0, "total": len(clips), "error": "", "url": ""}
            jobs[job_id] = job

        def run():
            try:
                directory = Path(out_dir(task_id)) / "export"
                directory.mkdir(exist_ok=True)
                name = f"{'clips' if mode == 'clips' else 'canvas'}_{job_id}.{suffix}"

                def progress(done):
                    with lock: job["done"] = done

                render(directory / name, progress)
                with lock:
                    job.update(status="succeeded", url=f"/files/{task_id}/export/{name}")
            except Exception as exc:
                message = "导出等待超时，请减少镜头数量后重试" if isinstance(exc, subprocess.TimeoutExpired) else "视频导出失败，请检查视频文件和本地磁盘" if isinstance(exc, OSError) else str(exc)[:300]
                with lock: job.update(status="failed", error=message)

        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job_id, "mode": mode}

    @app.post("/api/tasks/{task_id}/canvas-export")
    def start_export(task_id: str, body: CanvasExportRequest):
        task = task_lookup(task_id)
        clips = resolve_clips(out_dir(task_id), body.files)
        return start_job(task_id, task, clips, "merge", lambda output, progress: render_export(clips, output, body.aspect, ffmpeg(), progress))

    @app.post("/api/tasks/{task_id}/canvas-export/clips")
    def start_clips_export(task_id: str, body: CanvasClipsRequest):
        task = task_lookup(task_id)
        metadata = sorted(body.clips, key=lambda clip: clip.order)
        clips = resolve_clips(out_dir(task_id), [clip.file for clip in metadata])
        return start_job(task_id, task, clips, "clips", lambda output, progress: pack_clips(clips, metadata, output, progress))

    @app.get("/api/tasks/{task_id}/canvas-export/{job_id}")
    def export_status(task_id: str, job_id: str):
        task_lookup(task_id)
        with lock:
            job = jobs.get(job_id)
            if not job or job["task_id"] != task_id:
                raise HTTPException(404, "导出任务不存在，服务重启后可在作品的 export 目录查找视频或素材包")
            return dict(job)

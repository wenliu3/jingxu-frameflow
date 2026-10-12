"""Durable, idempotent image and speech jobs for canvas material nodes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
import time
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from atomic_io import atomic_write_json


class MediaBody(BaseModel):
    node_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    request_id: str = Field(pattern=r"^[a-f0-9]{12,32}$")
    kind: Literal["image", "audio"]
    image_mode: Literal["native", "character", "scene", "prop"] = "native"
    style_prompt: str = Field("", max_length=1000)
    title: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=3000)
    ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] = "16:9"
    voice_id: str = Field("", max_length=100)
    count: Literal[1, 2, 4] = 1

    @field_validator("count", mode="before")
    @classmethod
    def strict_count(cls, value):
        if type(value) is not int:
            raise ValueError("生成数量必须为 1、2 或 4 张")
        return value


def register_canvas_media_routes(
    app: FastAPI, lookup: Callable, out_dir: Callable,
    prepare: Callable, render: Callable, publish: Callable, voices: Callable,
) -> None:
    lock = threading.RLock()
    jobs: dict[tuple[str, str], dict] = {}

    def path(task_id, job_id):
        return Path(out_dir(task_id)) / "media_jobs" / f"{job_id}.json"

    def persist(task_id, job):
        path(task_id, job["job_id"]).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path(task_id, job["job_id"]), job)

    def restore(task_id, job_id):
        key = (task_id, job_id)
        if key in jobs:
            return jobs[key]
        file = path(task_id, job_id)
        if not file.is_file():
            raise HTTPException(404, "找不到这次图片或语音生成任务")
        try:
            job = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise HTTPException(500, "生成记录读取失败，请保留作品文件") from exc
        if job["status"] in {"queued", "running"}:
            job.update(status="interrupted", error="服务重启中断了生成。请确认素材结果后手动重试。")
            persist(task_id, job)
        jobs[key] = job
        return job

    def run(task_id, job, body):
        try:
            with lock:
                job.update(status="running")
                persist(task_id, job)
            extension = ".png" if body.kind == "image" else ".mp3"
            output = path(task_id, job["job_id"]).with_suffix(extension)
            output.parent.mkdir(parents=True, exist_ok=True)
            render(task_id, body, str(output))
            if not output.is_file() or not output.stat().st_size:
                raise RuntimeError("生成服务没有返回有效文件")
            # Publishing and the terminal record are serialized with duplicate submissions.
            with lock:
                result = publish(task_id, body, str(output))
                job.update(status="succeeded", result=result, error="")
                persist(task_id, job)
        except Exception as exc:
            with lock:
                message = exc.detail if isinstance(exc, HTTPException) else str(exc)
                job.update(status="failed", error=str(message)[:1500])
                persist(task_id, job)

    @app.post("/api/tasks/{task_id}/media-jobs")
    def submit(task_id: str, body: MediaBody):
        lookup(task_id)
        if not body.prompt.strip() or not body.title.strip():
            raise HTTPException(422, "请填写名称和生成内容")
        if body.kind == "audio" and body.image_mode != "native":
            raise HTTPException(422, "语音节点不支持图片类型")
        if body.kind == "audio" and body.style_prompt.strip():
            raise HTTPException(422, "语音节点不支持图片风格")
        if body.kind == "audio" and body.count != 1:
            raise HTTPException(422, "语音节点一次生成一条语音")
        if body.kind == "image" and body.image_mode in {"character", "prop"}:
            body.ratio = "16:9"
        with lock:
            key = (task_id, body.request_id)
            if key in jobs or path(task_id, body.request_id).is_file():
                job = restore(task_id, body.request_id)
                if MediaBody.model_validate(job["input"]).model_dump() != body.model_dump():
                    raise HTTPException(409, "这次提交编号已用于其他内容，请重新提交")
                return copy.deepcopy(job)
            active = [j for j in jobs.values() if j["status"] in {"queued", "running"}]
            if any(j["task_id"] == task_id and j["node_id"] == body.node_id for j in active):
                raise HTTPException(409, "这个节点正在生成，请等待完成")
            if len(active) >= 2:
                raise HTTPException(409, "已有两个图片或语音任务在生成，请稍后再试")
            if body.kind == "audio" and body.voice_id not in {v["id"] for v in voices()}:
                raise HTTPException(422, "请选择当前语音服务的音色")
            asset = prepare(task_id, body)
            job = {
                "job_id": body.request_id, "task_id": task_id, "node_id": body.node_id,
                "status": "queued", "kind": body.kind, "asset_name": asset["name"],
                "input": body.model_dump(), "created_at": time.time(), "error": "",
            }
            persist(task_id, job)
            jobs[key] = job
            threading.Thread(target=run, args=(task_id, job, body), daemon=True).start()
            return copy.deepcopy(job)

    @app.get("/api/tasks/{task_id}/media-jobs/{job_id}")
    def status(task_id: str, job_id: str):
        lookup(task_id)
        if not (12 <= len(job_id) <= 32) or any(c not in "0123456789abcdef" for c in job_id):
            raise HTTPException(404, "找不到这次生成任务")
        with lock:
            return copy.deepcopy(restore(task_id, job_id))

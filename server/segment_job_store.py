"""Durable candidate progress, without provider credentials or automatic resubmission."""
import json
import os
from pathlib import Path
import re
import tempfile


def job_path(directory, job_id):
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise ValueError("无效任务编号")
    return Path(directory) / "segments" / ".jobs" / f"{job_id}.json"


def save_job(directory, job):
    path = job_path(directory, job["job_id"])
    _write_json(path, job)


def save_candidate(directory, candidate):
    filename = candidate["file"]
    if not re.fullmatch(r"seg_[a-f0-9]{12}(?:_\d{2})?\.mp4", filename):
        raise ValueError("无效候选文件名")
    path = Path(directory) / "segments" / Path(filename).with_suffix(".json")
    _write_json(path, candidate["record"])


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def restore_job(directory, task_id, job_id):
    path = job_path(directory, job_id)
    if not path.is_file():
        return None
    job = json.loads(path.read_text(encoding="utf-8"))
    if job.get("task_id") != task_id or job.get("job_id") != job_id:
        raise ValueError("任务记录与作品不匹配")
    if job["status"] == "running":
        job["status"] = "interrupted"
        job["error"] = "后端重启，未提交的候选已停止。已完成结果保留，请先确认远端任务再重试。"
        for result in job.get("results", []):
            if result["status"] in ("running", "queued"):
                result["status"] = "interrupted"
        save_job(directory, job)
    return job

"""Serial candidate execution, independent of HTTP routing and provider configuration."""
from __future__ import annotations

import os
import secrets
from typing import Callable

from server.segment_job_store import save_candidate, save_job


def make_candidates(record: dict, directory: str, seed: int | None) -> list[dict]:
    base_seed = seed if seed is not None else secrets.randbelow(2**31 - 4)
    candidates = []
    for index in range(record["candidate_count"]):
        filename = record["file"] if record["candidate_count"] == 1 else f"seg_{record['job_id']}_{index + 1:02d}.mp4"
        candidate_seed = None if record["video_backend"] == "api" else (base_seed + index) % 2**31
        candidates.append({
            "index": index, "status": "queued", "error": "", "file": filename,
            "out_path": os.path.join(directory, filename), "seed": candidate_seed,
            "record": {**record, "file": filename, "candidate_index": index, "seed": candidate_seed},
        })
    return candidates


def run_candidates(job: dict, provider, generation: dict, directory: str, error_text: Callable, lock) -> None:
    """Keep successful alternatives; stop unsubmitted work when persistence fails."""
    candidates = job["results"]
    for candidate in candidates:
        candidate["status"] = "running"
        try:
            save_job(directory, job)
            provider.generate(**generation, out_path=candidate["out_path"], seed=candidate["seed"])
            if not os.path.isfile(candidate["out_path"]) or os.path.getsize(candidate["out_path"]) <= 0:
                raise RuntimeError("视频服务没有返回有效文件")
            candidate["status"] = "succeeded"
            measured = getattr(provider, "last_output_info", {})
            if isinstance(measured, dict):
                candidate.update(measured)
                candidate["record"].update(measured)
        except Exception as exc:
            candidate["status"] = "failed"
            candidate["error"] = error_text(exc)
        with lock:
            job["done"] += 1
        candidate["record"].update(status=candidate["status"], error=candidate["error"])
        try:
            save_candidate(directory, candidate)
            save_job(directory, job)
        except OSError:
            for pending in candidates:
                if pending["status"] == "queued":
                    pending.update(status="interrupted", error="任务进度保存失败，未提交生成")
            break
    successful = [c for c in candidates if c["status"] == "succeeded"]
    with lock:
        job["status"] = "succeeded" if successful else "failed"
        job["error"] = (
            "" if len(successful) == len(candidates)
            else f"{len(candidates) - len(successful)} 个候选失败，已保留成功结果" if successful
            else candidates[0]["error"]
        )
        if successful:
            job["out_path"] = successful[0]["out_path"]
    try:
        save_job(directory, job)
    except OSError:
        job["error"] = "任务进度写入失败，已完成的视频保留在生成记录中"

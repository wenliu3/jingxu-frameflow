"""Resolve project-owned successful versions into immutable visual inputs."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from fastapi import HTTPException
from video_controls import h3_frames


def resolve_video_sources(directory, sources, duration, workflow):
    if not sources:
        return []
    if workflow != "ref2va":
        raise HTTPException(422, "视频参考和续拍需要 ComfyUI 的 Ref2VA 工作流")
    if sum(s.usage == "continue" for s in sources) > 1:
        raise HTTPException(422, "一段新视频只能有一个续拍起点")
    root = Path(directory).resolve()
    try:
        canvas = json.loads((root / "workflow.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "无法核对来源版本，请先保存画布") from exc
    nodes = {n["id"]: n for n in canvas.get("nodes", [])}
    result = []
    for source in sources:
        node = nodes.get(source.source_node)
        versions = (node or {}).get("data", {}).get("versions", [])
        version = next((v for v in versions if v.get("id") == source.version_id), None)
        if not node or node.get("type") not in {"shot", "footage"} or not version or version.get("status") != "succeeded" or version.get("file") != source.file:
            raise HTTPException(409, "来源不是当前作品的成功版本，请重新选择来源视频")
        uploaded = node.get("type") == "footage"
        directory = (root / ("video_uploads" if uploaded else "segments")).resolve()
        if not source.file.startswith("upload_" if uploaded else "seg_"):
            raise HTTPException(422, "来源视频类型与文件不匹配")
        path = (directory / source.file).resolve()
        if not path.is_relative_to(root) or path.parent != directory or not path.is_file():
            raise HTTPException(422, "来源视频文件缺失或不属于当前作品")
        try:
            sidecar = path.with_suffix(".json")
            if uploaded and not sidecar.is_file():
                raise HTTPException(422, "上传视频的记录缺失，请重新上传")
            if sidecar.is_file():
                record = json.loads(sidecar.read_text(encoding="utf-8"))
                if uploaded and (record.get("origin") != "upload" or record.get("file") != source.file):
                    raise HTTPException(422, "上传视频记录与来源不匹配")
                if record.get("status", "succeeded") != "succeeded":
                    raise HTTPException(409, "来源视频尚未成功完成")
                job_id = record.get("job_id", "")
                if isinstance(job_id, str) and len(job_id) == 12 and all(c in "0123456789abcdef" for c in job_id):
                    job_file = root / "segments" / ".jobs" / f"{job_id}.json"
                    if job_file.is_file():
                        job = json.loads(job_file.read_text(encoding="utf-8"))
                        candidate = next((c for c in job.get("results", []) if c.get("file") == source.file), None)
                        if not candidate or candidate.get("status") != "succeeded":
                            raise HTTPException(409, "来源视频尚未成功完成")
            before = path.stat()
            with path.open("rb") as handle:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
                digest = hasher.hexdigest()
            if source.expected_sha256 and source.expected_sha256 != digest:
                raise HTTPException(409, "来源文件在编排后发生变化，请重新编排")
            prepared = _prepare(root, path, digest, source, duration)
            after = path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise HTTPException(409, "来源文件在处理时发生变化，请重试")
        except HTTPException:
            raise
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            raise HTTPException(422, "无法读取或处理来源视频，请重新选择可播放的成功版本") from exc
        result.append({**prepared, "source_node": source.source_node, "version_id": source.version_id,
                       "file": source.file, "usage": source.usage, "motion_reference": source.motion_reference,
                       "expected_sha256": digest, "description": version.get("description", ""),
                       "label": node.get("data", {}).get("title", "来源视频")})
    return result


def public_sources(sources):
    return [{k: v for k, v in source.items() if k not in {"clip_path", "tail_path"}} for source in sources]


def _prepare(root, path, digest, source, duration):
    import imageio_ffmpeg
    from PIL import Image
    cache = root / "segments" / ".references" / digest
    cache.mkdir(parents=True, exist_ok=True)
    info_path = cache / "source.json"
    tail_path = cache / f"tail_{digest[:24]}.png"
    if info_path.is_file() and tail_path.is_file():
        info = json.loads(info_path.read_text(encoding="utf-8"))
    else:
        reader = imageio_ffmpeg.read_frames(str(path))
        try:
            metadata = next(reader)
            if metadata.get("duration", 0) > 120:
                raise ValueError("请使用两分钟以内的来源视频")
            # Decode sequentially: this is the last valid frame, not a guessed seek.
            count, last = 0, None
            for frame in reader:
                count += 1
                last = frame
                if count > 24 * 60 * 60:
                    raise ValueError("来源视频过长")
            if count < 5 or metadata["fps"] <= 0:
                raise ValueError("来源视频没有足够的有效帧")
            info = {"source_duration": count / metadata["fps"], "source_width": metadata["size"][0], "source_height": metadata["size"][1]}
            with tempfile.TemporaryDirectory(dir=cache) as folder:
                temporary = Path(folder) / "tail.png"
                Image.frombytes("RGB", tuple(metadata["size"]), last).save(temporary)
                os.replace(temporary, tail_path)
                temporary = Path(folder) / "source.json"
                temporary.write_text(json.dumps(info), encoding="utf-8")
                os.replace(temporary, info_path)
        finally:
            reader.close()
    end = source.end if source.end is not None else info["source_duration"]
    start = source.start if source.start is not None else max(0, end - source.tail_seconds)
    if not 0 <= start < end <= info["source_duration"] + 0.02:
        raise ValueError("参考范围超出来源视频")
    # Ref2VA crops from the beginning when references exceed the target. Keep the
    # selected range's ending, with an exact 17k+5 batch no longer than the target.
    available = math.floor((end - start) * 24 + 1e-6)
    frames = min(h3_frames(duration), 17 * ((available - 5) // 17) + 5)
    if frames < 5:
        raise ValueError("参考片段至少需要五帧")
    actual_start = end - frames / 24
    key = hashlib.sha256(f"v1:{actual_start:.9f}:{frames}".encode()).hexdigest()[:20]
    clip_path = cache / f"ref_{digest[:24]}_{key}.mp4"
    motion = source.usage == "reference" or source.motion_reference
    if motion and not clip_path.is_file():
        with tempfile.TemporaryDirectory(dir=cache) as folder:
            temporary = Path(folder) / "reference.mp4"
            command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(path), "-ss", str(actual_start),
                       "-an", "-vf", "fps=24,scale=trunc(iw/2)*2:trunc(ih/2)*2", "-frames:v", str(frames),
                       "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(temporary)]
            process = subprocess.run(command, capture_output=True, timeout=120)
            if process.returncode:
                raise RuntimeError("参考片段裁切失败")
            reader = imageio_ffmpeg.read_frames(str(temporary))
            try:
                next(reader)
                if sum(1 for _ in reader) != frames:
                    raise ValueError("参考片段解码帧数不完整")
            finally:
                reader.close()
            os.replace(temporary, clip_path)
    return {**info, "start": actual_start, "end": end, "frames": frames, "fps": 24,
            "tail_seconds": source.tail_seconds, "range_trimmed": available > h3_frames(duration), "clip_path": str(clip_path) if motion else "",
            "tail_path": str(tail_path), "guide": "last_valid_frame_at_0" if source.usage == "continue" else ""}

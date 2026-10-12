"""Muse browser adapter; does not use undocumented APIs or export account cookies."""
from __future__ import annotations

import os
import json
import re
import tempfile
from pathlib import Path

from server.muse_bridge import BRIDGE
from video_controls import publish_video
from atomic_io import atomic_write_json


class MuseVideoProvider:
    def __init__(self, timeout=3600, bridge=None):
        self.timeout = float(timeout)
        self.bridge = bridge or BRIDGE
        self.last_output_info = {}

    def generate(self, image_path, video_prompt, duration, out_path, audio="", last_frame_path="",
                 megapixels=None, ref_image_paths=None, *, width=None, height=None, seed=None,
                 generate_audio=True, exact_duration=False, ref_video_paths=None, guide_frame_path="", recover_only=False,
                 downloaded_path="", ratio="auto", resolution="custom"):
        if downloaded_path:
            return self.import_download(downloaded_path, out_path, generate_audio=generate_audio)
        if recover_only:
            return self.recover(out_path, generate_audio=generate_audio)
        if last_frame_path or guide_frame_path:
            raise ValueError("Muse 浏览器接入暂不支持锁定首尾帧")
        if seed is not None or exact_duration or width or height or megapixels is not None:
            raise ValueError("Muse 的画幅、清晰度和时长由网页生成能力决定，暂不支持精确参数")
        images = list(ref_image_paths) if ref_image_paths is not None else ([image_path] if image_path else [])
        if len(images) > 9 or any(not os.path.isfile(p) for p in images):
            raise ValueError("Muse 参考图片缺失或超过 9 张")
        videos = list(ref_video_paths or [])
        if len(videos) > 3 or any(not os.path.isfile(p) for p in videos):
            raise ValueError("Muse 参考视频缺失或超过 3 个")
        if ratio not in {"auto", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"} or resolution not in {"custom", "480p", "720p"}:
            raise ValueError("Muse 画幅或清晰度选项无效")
        return self._retrieve("generate", {
            "images": [str(Path(p).resolve()) for p in images], "prompt": video_prompt,
            "videos": [str(Path(p).resolve()) for p in videos], "ratio": ratio, "resolution": resolution,
            "duration": duration, "audio": audio if generate_audio else "",
            "generate_audio": generate_audio,
        }, out_path, generate_audio)

    def recover(self, out_path, *, generate_audio=True):
        """Retrieve the original submitted task; never send text or upload material."""
        if not Path(out_path).with_suffix(".muse.json").is_file():
            raise ValueError("原 Muse 提交记录不存在，不能取回成片")
        return self._retrieve("recover", {}, out_path, generate_audio)

    def import_download(self, source, out_path, *, generate_audio=True):
        """Validate a user-downloaded file and complete the original canvas version."""
        journal = Path(out_path).with_suffix(".muse.json")
        record = json.loads(journal.read_text(encoding="utf-8"))
        if record.get("stage") not in ("submitting", "submitted", "downloaded") or not re.fullmatch(r"muse_[a-f0-9]{12}", record.get("task_id", "")):
            raise ValueError("原 Muse 任务尚未提交，不能导入成片")
        self.last_output_info = {}
        measured = publish_video(source, out_path, generate_audio=generate_audio)
        record.update(stage="downloaded", result_source="local_download")
        record.pop("error", None)
        atomic_write_json(journal, record)
        self.last_output_info = {**measured, "muse_task_id": record["task_id"],
                                 "muse_chat_url": record.get("chat_url", "")}
        return out_path

    def _retrieve(self, command, payload, out_path, generate_audio):
        parent = Path(out_path).resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        self.last_output_info = {}
        with tempfile.TemporaryDirectory(prefix=".muse-video-", dir=parent) as folder:
            raw = str(Path(folder) / "raw.mp4")
            result = self.bridge.call(command, {**payload, "raw_path": raw,
                "journal_path": str(Path(out_path).with_suffix(".muse.json").resolve()),
                "timeout_s": self.timeout,
            }, timeout=self.timeout + 300)
            # Decode and measure before publishing: HTML, empty or broken downloads fail.
            measured = publish_video(raw, out_path, generate_audio=generate_audio)
            self.last_output_info = {**measured, "muse_task_id": result.get("task_id", ""),
                                     "muse_chat_url": result.get("chat_url", "")}
        return out_path

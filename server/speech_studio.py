"""Account voice catalog and explicitly requested, cached speech auditions."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

import tts
from speech_controls import SpeechSettings

SAMPLE_TEXT = "你好，欢迎来到我的故事。让我们一起，听见文字里的温度。"


def service_signature():
    # A change of account must not reuse another account's voices or previews.
    return hashlib.sha256(json.dumps([tts.current_provider(),
        os.getenv("AUDIO_BASE_URL", tts.DEFAULT_MINIMAX_BASE), os.getenv("AUDIO_API_KEY", ""),
        os.getenv("AUDIO_MODEL", tts.DEFAULT_MINIMAX_MODEL)]).encode()).hexdigest()


def normalize_voices(data):
    known = {voice["id"]: voice for voice in tts.list_voices(tts.VOICES_MINIMAX)}
    items = {}
    language_names = {"Chinese (Mandarin)": "普通话", "Chinese (Cantonese)": "粤语",
        "English": "英语", "Japanese": "日语", "Korean": "韩语", "French": "法语",
        "German": "德语", "Spanish": "西班牙语", "Portuguese": "葡萄牙语"}
    for field, source in (("system_voice", "system"), ("voice_cloning", "cloned"), ("voice_generation", "designed")):
        for voice in data.get(field) or []:
            if not isinstance(voice, dict):
                continue
            voice_id = voice.get("voice_id")
            if not isinstance(voice_id, str) or not voice_id or len(voice_id) > 100:
                continue
            preset = known.get(voice_id, {})
            description = voice.get("description") or []
            hint = description if isinstance(description, str) else " · ".join(d for d in description if isinstance(d, str))
            gender = preset.get("gender", "")
            if not gender:
                gender = "female" if re.search(r"\bfemale\b|女声|女性", hint, re.I) else (
                    "male" if re.search(r"\bmale\b|男声|男性", hint, re.I) else "")
            prefix = voice_id.split("_", 1)[0]
            items[voice_id] = {"id": voice_id, "cn": preset.get("cn") or voice.get("voice_name") or voice_id,
                "gender": gender, "locale": preset.get("locale") or language_names.get(prefix, prefix if "_" in voice_id and prefix.isalpha() else "未标注"),
                "hint": preset.get("hint") or hint[:1000], "source": source}
    return list(items.values())


class VoiceCatalog:
    def __init__(self):
        self.lock = threading.RLock()
        self.cached = None
        self.signature = None
        self.expires = 0

    def get(self, refresh=False):
        if tts.current_provider() != "minimax":
            return {"provider": "edge", "source": "preset", "notice": "",
                "items": [{**voice, "source": "system"} for voice in tts.list_voices()]}
        signature = service_signature()
        with self.lock:
            if self.signature == signature and self.cached and not refresh and time.monotonic() < self.expires:
                return copy.deepcopy(self.cached)
            previous = self.cached if self.signature == signature else None
            result = {"provider": "minimax", "source": "preset", "notice": "",
                "items": [{**voice, "source": "system"} for voice in tts.list_voices(tts.VOICES_MINIMAX)]}
            ttl = 30
            key = (os.getenv("AUDIO_API_KEY") or "").strip()
            if not key:
                result["notice"] = "连接 MiniMax 后可同步完整音色库和账户音色。当前显示内置音色。"
            else:
                import requests
                base = (os.getenv("AUDIO_BASE_URL") or tts.DEFAULT_MINIMAX_BASE).strip().rstrip("/")
                try:
                    response = requests.request("post", f"{base}/get_voice",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"voice_type": "all"}, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    if (data.get("base_resp") or {}).get("status_code") not in (None, 0):
                        raise ValueError("音色服务暂不可用")
                    items = normalize_voices(data)
                    if not items:
                        raise ValueError("音色服务返回空列表")
                    result.update(source="synced", items=items)
                    ttl = 600
                except (requests.RequestException, ValueError, TypeError, AttributeError):
                    if previous and previous["source"] == "synced":
                        result = copy.deepcopy(previous)
                        result["notice"] = "同步暂未成功，继续使用上次同步的音色。可稍后刷新。"
                    else:
                        result["notice"] = "完整音色库同步暂未成功，当前显示内置音色。请检查音频服务配置后刷新。"
            self.signature, self.cached, self.expires = signature, result, time.monotonic() + ttl
            return copy.deepcopy(result)


class SpeechPreviewBody(BaseModel):
    voice_id: str = Field(min_length=1, max_length=100)
    speech_settings: SpeechSettings = Field(default_factory=SpeechSettings)


def register_speech_routes(app, lookup, out_dir, catalog):
    preview_lock = threading.Lock()

    @app.get("/api/audio/voices")
    def voices(refresh: bool = False):
        return catalog.get(refresh=refresh)

    @app.post("/api/tasks/{task_id}/speech-preview")
    def preview(task_id: str, body: SpeechPreviewBody):
        lookup(task_id)
        try:
            body.speech_settings.for_provider(tts.current_provider())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if body.voice_id not in {voice["id"] for voice in catalog.get()["items"]}:
            raise HTTPException(422, "请选择当前语音服务的音色")
        digest = hashlib.sha256(json.dumps([service_signature(), body.model_dump(), SAMPLE_TEXT],
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        path = Path(out_dir(task_id)) / "voice_previews" / f"{digest}.mp3"
        # Serializing auditions also makes repeated clicks share the same result.
        with preview_lock:
            cached = path.is_file() and path.stat().st_size > 0
            if not cached:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp.mp3")
                try:
                    tts.synth(SAMPLE_TEXT, body.voice_id, str(temporary), settings=body.speech_settings)
                    if not temporary.is_file() or not temporary.stat().st_size:
                        raise ValueError("试听服务没有返回音频")
                    os.replace(temporary, path)
                except Exception as exc:
                    raise HTTPException(502, "试听生成失败，请检查音频服务配置后重试") from exc
                finally:
                    temporary.unlink(missing_ok=True)
        return {"url": f"/files/{task_id}/voice_previews/{path.name}", "cached": bool(cached)}

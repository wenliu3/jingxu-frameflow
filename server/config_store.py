"""Cached, atomic service settings; saving never contacts a model service."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable


class ConfigStore:
    def __init__(self, path, defaults, env_names, *, legacy_path=None, environ=None):
        self.path = Path(path)
        self.defaults = dict(defaults)
        source = os.environ if environ is None else environ
        # Capture fallback values before runtime settings are applied to os.environ.
        self.fallback = {key: str(source.get(env_names[key], "") or "").strip() or value
                         for key, value in self.defaults.items()}
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self.lock = threading.RLock()
        self._stamp = None
        self._cached = None

    @staticmethod
    def _signature(path):
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def _read(self):
        if not self.path.exists():
            saved = {}
        else:
            with self.path.open(encoding="utf-8") as fh:
                saved = json.load(fh)
            if not isinstance(saved, dict):
                raise ValueError("服务配置必须是一个 JSON 对象")
        if "comfyui_url" not in saved and self.legacy_path and self.legacy_path.exists():
            with self.legacy_path.open(encoding="utf-8") as fh:
                legacy = json.load(fh)
            if isinstance(legacy, dict) and legacy.get("url"):
                saved["comfyui_url"] = legacy["url"]
        result = dict(self.fallback)
        for key in self.defaults:
            if key in saved:
                result[key] = str(saved[key] if saved[key] is not None else "").strip()
        # Normalize retired enum values, while explicit blank keys/LoRA stay blank.
        for key, allowed, default in (
            ("video_backend", ("comfyui", "api"), "comfyui"),
            ("video_mode", ("i2v", "flf", "portrait"), "i2v"),
            ("video_workflow", ("i2v", "ref2va"), "i2v"),
            ("audio_provider", ("edge", "minimax"), "edge"),
        ):
            if key in result and result[key] not in allowed:
                result[key] = default
        return result

    def get(self):
        with self.lock:
            stamp = (self._signature(self.path), self._signature(self.legacy_path) if self.legacy_path else None)
            if self._cached is None or stamp != self._stamp:
                result = self._read()
                self._cached, self._stamp = result, stamp
            return dict(self._cached)

    def patch(self, changes, *, validate: Callable | None = None, apply: Callable | None = None):
        with self.lock:
            unknown = set(changes) - self.defaults.keys()
            if unknown:
                raise ValueError("包含未知的服务配置字段")
            result = {**self.get(), **{key: str(value).strip() for key, value in changes.items()}}
            if validate:
                validate(result)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".service-config-", suffix=".tmp", delete=False) as fh:
                    temp_path = Path(fh.name)
                    json.dump(result, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temp_path, self.path)
            finally:
                if temp_path and temp_path.exists():
                    temp_path.unlink()
            self._cached = None
            saved = self.get()
            if apply:
                apply(saved)
            return saved

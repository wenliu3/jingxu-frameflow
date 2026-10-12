"""Local JSON-lines bridge to an isolated, user-operated Muse browser profile."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid


class MuseBridge:
    def __init__(self, root):
        self.root = Path(root)
        self.process = None
        self.lock = threading.RLock()
        self.pending = {}

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Muse 连接需要 Node.js，请安装后重新启动镜序")
        if not (self.root / "web/node_modules/playwright").is_dir():
            raise RuntimeError("Muse 浏览器组件未安装，请在项目目录执行 npm ci --prefix web")
        process = subprocess.Popen(
            [node, str(self.root / "server/muse_browser.mjs"), str(self.root)],
            cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.process = process
        threading.Thread(target=self._read, args=(process,), daemon=True).start()

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    response = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    entry = self.pending.get(response.get("id"))
                    if entry and entry[2] is process:
                        entry[1].update(response)
                        entry[0].set()
        finally:
            with self.lock:
                for event, result, owner in self.pending.values():
                    if owner is process and not event.is_set():
                        result.update(error="Muse 浏览器连接已关闭。若任务已发送，请先在 Muse 检查结果，避免重复生成。")
                        event.set()
            process.stdout.close()

    def call(self, command, payload=None, timeout=45):
        request_id = uuid.uuid4().hex
        event, result = threading.Event(), {}
        with self.lock:
            self._start()
            process = self.process
            self.pending[request_id] = (event, result, process)
            try:
                process.stdin.write(json.dumps({"id": request_id, "command": command, "payload": payload or {}}, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                self.pending.pop(request_id, None)
                raise RuntimeError("Muse 后台连接不可用，请重新打开 Muse 面板") from exc
        try:
            if not event.wait(timeout):
                raise RuntimeError("等待 Muse 超时。已发送的任务不会自动重发，请先在 Muse 检查结果。")
            if result.get("error"):
                raise RuntimeError(result["error"])
            return result.get("result", {})
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def status(self):
        with self.lock:
            running = self.process and self.process.poll() is None
        if not running:
            return {"connected": False, "browser_open": False, "background": False, "busy": False,
                    "message": "请打开 Muse 面板，登录后选择聊天"}
        return self.call("status", timeout=10)

    def close(self):
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                # EOF makes the worker close its own browser. Never kill unrelated Chrome.
                process.stdin.close()


BRIDGE = MuseBridge(Path(__file__).resolve().parent.parent)
atexit.register(BRIDGE.close)

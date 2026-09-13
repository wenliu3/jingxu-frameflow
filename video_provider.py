"""图生视频 Provider。

把分镜的首帧图 + video_prompt 送到 ComfyUI（MiniMax H3）生成带音轨的 mp4。
接口约定与 image_provider 一致：换后端只改这一个文件。

ComfyUI 的四步协议（全部由本类自动完成，调用方无需关心）：
1. POST /upload/image   传首帧图 → 得到服务端文件名
2. POST /prompt         提交 API 格式工作流（节点图包在 {"prompt": ...} 里）→ 得到 prompt_id
3. GET  /history/{id}   轮询直到 status.completed
4. GET  /view           按 filename/subfolder/type 下载 mp4

注意事项：
- H3 是统一模型，工作流里没有负向提示词输入，shot.negative_prompt 在视频阶段用不上。
- 帧数有硬约束：length % 17 == 5（官方模板用数学节点保证，这里在 _build_workflow 里算）。
- 画质/速度旋钮通过环境变量调节（H3_MEGAPIXELS / H3_STEPS / H3_LORA），见 .env.example。
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from base64 import b64encode

import requests


class ApiVideoProvider:
    """外接视频 API（硅基流动风格的任务制协议）。

    协议：
      submit: POST {base}/video/submit   {"model":..., "prompt":..., "image_url": <data uri>}
      status: GET  {base}/video/status?requestId=...
      status ∈ InQueue / InProgress / Done / Failed；Done 后 results[].url 是 mp4 直链。

    首帧图以 base64 data uri 随请求发送。不同厂商的视频 API 协议差异很大，
    要接新厂商时改这一个文件即可，配置与前端不用动。
    """

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        poll_interval: float = 10.0,
        timeout: float = 1800.0,
    ) -> None:
        self.base = (base_url or "").strip().rstrip("/")
        self.key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.poll_interval = poll_interval
        self.timeout = timeout
        if not self.base or not self.key or not self.model:
            raise RuntimeError("外接视频 API 未配置完整：需要 API 地址、API Key 和模型名称")

    def generate(self, image_path: str, video_prompt: str, duration: float, out_path: str) -> str:
        with open(image_path, "rb") as fh:
            data_uri = "data:image/png;base64," + b64encode(fh.read()).decode()
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

        submit = requests.post(
            f"{self.base}/video/submit",
            headers=headers,
            json={"model": self.model, "prompt": video_prompt, "image_url": data_uri},
            timeout=120,
        )
        submit.raise_for_status()
        body = submit.json()
        request_id = str(body.get("requestId") or body.get("id") or "")
        if not request_id:
            raise RuntimeError(f"提交失败：{json.dumps(body, ensure_ascii=False)[:300]}")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            status_resp = requests.get(
                f"{self.base}/video/status",
                headers=headers,
                params={"requestId": request_id},
                timeout=60,
            )
            status_resp.raise_for_status()
            data = status_resp.json()
            status = str(data.get("status", ""))
            if status == "Failed":
                raise RuntimeError(f"API 返回 Failed：{json.dumps(data, ensure_ascii=False)[:300]}")
            if status == "Done":
                results = data.get("results") or []
                url = (results[0] or {}).get("url") if results else ""
                if not url:
                    raise RuntimeError(f"Done 但返回里没有视频 url：{json.dumps(data, ensure_ascii=False)[:300]}")
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                with requests.get(url, stream=True, timeout=300) as dl:
                    dl.raise_for_status()
                    with open(out_path, "wb") as fh:
                        for chunk in dl.iter_content(chunk_size=1 << 16):
                            if chunk:
                                fh.write(chunk)
                return out_path
        raise TimeoutError(f"外接 API 视频生成超时（{self.timeout:.0f}s），requestId={request_id}")


class ComfyUIVideoProvider:
    """通过 HTTP API 驱动 ComfyUI 生成视频。"""

    def __init__(
        self,
        base_url: str | None = None,
        workflow_path: str | None = None,
        poll_interval: float = 10.0,
        timeout_per_shot: float = 1800.0,
    ) -> None:
        self.base = (base_url or os.getenv("COMFYUI_URL", "")).strip().rstrip("/")
        if not self.base:
            # 与 server 共享的运行时配置：前端「视频服务」面板填的地址存在这里
            conf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comfyui_config.json")
            try:
                with open(conf, encoding="utf-8") as fh:
                    self.base = str(json.load(fh).get("url", "")).strip()
            except (OSError, json.JSONDecodeError):
                pass
        if not self.base:
            raise RuntimeError(
                "缺少 ComfyUI 地址。在前端「视频服务」面板填隧道地址，或设 COMFYUI_URL 环境变量。"
            )
        wf_path = workflow_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "comfyui", "h3_i2v_api.json"
        )
        with open(wf_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        # 剔除 _说明 / _调参指南 这类注释键，只留纯节点图
        self.template = {k: v for k, v in raw.items() if not k.startswith("_")}
        self.client_id = "avm_" + uuid.uuid4().hex[:8]
        self.poll_interval = poll_interval
        self.timeout = timeout_per_shot

    def generate(
        self,
        image_path: str,
        video_prompt: str,
        duration: float,
        out_path: str,
        audio: str = "",
        last_frame_path: str = "",
    ) -> str:
        """生成一条视频并落盘，返回 out_path。阻塞直到完成或超时。

        audio 是声音设计（环境音/说话人音色），H3 的画面与音频共用一份提示词，
        所以只能拼在 prompt 末尾做软引导，无法像负向提示词那样硬性排除。
        last_frame_path 是上一镜视频的尾帧图：给了就按 fl2va 模式生成，
        让本镜首帧承接上一镜结尾（首尾帧接力），实现镜头间的真实动作衔接。
        """
        image_name = self._upload(image_path)
        last_frame_name = self._upload(last_frame_path) if last_frame_path else ""
        workflow = self._build_workflow(video_prompt, duration, image_name, audio, last_frame_name)
        prompt_id = self._submit(workflow)
        filename, subfolder = self._wait(prompt_id)
        self._download(filename, subfolder, out_path)
        return out_path

    # ---- 四步协议 ----

    def _upload(self, image_path: str) -> str:
        with open(image_path, "rb") as fh:
            resp = requests.post(
                f"{self.base}/upload/image",
                files={"image": (os.path.basename(image_path), fh)},
                data={"overwrite": "true"},
                timeout=120,
            )
        resp.raise_for_status()
        data = resp.json()
        # 带子目录时 LoadImage 的 image 字段要写 "subfolder/name" 形式
        sub = data.get("subfolder", "")
        name = data.get("name", "")
        return f"{sub}/{name}" if sub else name

    def _build_workflow(
        self,
        video_prompt: str,
        duration: float,
        image_name: str,
        audio: str = "",
        last_frame_name: str = "",
    ) -> dict:
        wf = json.loads(json.dumps(self.template))  # 深拷贝
        wf["100"]["inputs"]["image"] = image_name
        prompt = video_prompt.strip()
        if audio.strip():
            prompt = f"{prompt}\n\n声音设计：{audio.strip()}"
        wf["104"]["inputs"]["prompt"] = prompt
        # 首尾帧接力：last_frame 节点按需动态注入（模板里不放，避免未接线节点
        # 参与 ComfyUI 的输入校验）
        if last_frame_name:
            wf["101"] = {
                "class_type": "LoadImage",
                "inputs": {"image": last_frame_name},
            }
            wf["104"]["inputs"]["last_frame"] = ["101", 0]
        # 帧数：时长 ×24fps，就近吸附到 ≡5 (mod 17) 的网格（H3 的帧数约束）。
        # 就近而不是向上：向上吸附会把 4.5s 的请求吞成 5.17s，时长设定失真。
        frames = max(5, round(duration * 24))
        frames = max(5, 17 * round((frames - 5) / 17) + 5)
        wf["104"]["inputs"]["length"] = frames
        wf["15"]["inputs"]["noise_seed"] = random.randint(0, 2**31)
        # 画质/速度旋钮
        wf["119"]["inputs"]["megapixels"] = float(os.getenv("H3_MEGAPIXELS", "0.9"))
        steps = int(os.getenv("H3_STEPS", "8"))
        wf["9"]["inputs"]["steps"] = steps
        lora_name = os.getenv("H3_LORA", "").strip()
        if lora_name:
            # turbo LoRA 蒸馏档：8 步左右出片
            wf["121"]["inputs"]["lora_name"] = lora_name
        else:
            # 标准（无 LoRA）档：卸掉 LoRA 节点，模型直连采样器，20 步以上无蒸馏伪影
            wf["9"]["inputs"]["model"] = ["6", 0]
            wf.pop("121", None)
        return wf

    def _submit(self, workflow: dict) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        resp = requests.post(f"{self.base}/prompt", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"ComfyUI 拒绝了工作流：{json.dumps(data, ensure_ascii=False)[:500]}")
        return data["prompt_id"]

    def _wait(self, prompt_id: str) -> tuple[str, str]:
        """轮询 history 直到完成，返回 (filename, subfolder)。"""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            resp = requests.get(f"{self.base}/history/{prompt_id}", timeout=30)
            resp.raise_for_status()
            history = resp.json()
            if not history:
                continue
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"工作流执行出错：{json.dumps(status.get('messages', []), ensure_ascii=False)[:600]}")
            if not status.get("completed"):
                continue
            # 从 outputs 里翻出视频文件（不依赖具体节点 id / 键名）
            for outputs in entry.get("outputs", {}).values():
                for items in outputs.values():
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if isinstance(item, dict) and item.get("filename"):
                            return item["filename"], item.get("subfolder", "")
            raise RuntimeError("工作流完成但 outputs 里没有文件")
        raise TimeoutError(f"视频生成超时（{self.timeout:.0f}s），prompt_id={prompt_id}")

    def _download(self, filename: str, subfolder: str, out_path: str) -> None:
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        resp = requests.get(
            f"{self.base}/view",
            params={"filename": filename, "subfolder": subfolder, "type": "output"},
            stream=True,
            timeout=300,
        )
        resp.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)

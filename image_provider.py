"""图像生成 Provider。

ModelScope 只是第一个实现。抽成接口是为了将来换即梦 / 豆包 / Agnes 时，
改动只落在这一个文件里，pipeline 和 Agent 都不用动。

调用 ModelScope 的两个硬约束（已实测确认）：
1. 这是异步接口——先提交拿 task_id，再轮询 GET /v1/tasks/{task_id}，
   不是一次请求直接拿图。
2. 提交时必须带 X-ModelScope-Async-Mode: true。

模型选型（2026-09 现状，ModelScope API-Inference 实际可用列表）：
- Tongyi-MAI/Z-Image-Turbo —— 默认。6B / S3-DiT，<3 秒出图，中文 prompt 直接可用。
  分镜图、定妆照、素材（道具/场景）**统一用它**。
- Qwen/Qwen-Image-2512 —— 更懂中文、文字渲染强，但出人物明显不如 Z-Image-Turbo
  （2026-09-14 实测结论，改回来过）。别拿它出人像。
- black-forest-labs/FLUX.2-dev —— 人像质感强，但**只吃英文 prompt**，中文进去出来的
  是完全无关的画面。填它到面板也能跑：代码按模型名自动切英文 prompt（见 pipeline._is_flux）。
- 注意：FLUX 系是 guidance-distilled 模型，接口不吃 negative_prompt
  （发送可能报错），代码里按模型名跳过。

**全链路纯文生图（2026-09-14 起）**：不再有图生图 / Edit 模型 / 参考图上传。
人物四视角与道具两视角的一致性靠「逐字相同的 anchor + 同一角色共用 seed」约束，
不靠参考图。原 Edit 路线（Qwen-Image-Edit-2511）已整体移除。
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod

import requests

SIZE_TABLE: dict[str, str] = {
    "16:9": "1024x576",
    "9:16": "576x1024",
    "1:1": "1024x1024",
    "4:3": "1024x768",
    "3:4": "768x1024",
}

DEFAULT_NEGATIVE = (
    "extra fingers, extra limbs, fused fingers, distorted face, deformed hands, "
    "malformed limbs, text, watermark, signature, lowres, blurry, jpeg artifacts"
)


def _require_env(name: str) -> str:
    """取必填环境变量。缺失时给出可操作的提示，而不是裸的 KeyError。"""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"缺少环境变量 {name}。请复制 .env.example 为 .env 并填入对应密钥。"
        )
    return value


class ImageProvider(ABC):
    """文生图后端接口。"""

    model: str = "unknown"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        out_path: str,
        negative_prompt: str = "",
        size: str = "1024x576",
        seed: int | None = None,
    ) -> str:
        """生成一张图并落盘，返回落盘路径。

        seed 不是可选项里的装饰：定妆照四视角 / 道具两视角靠**同一 seed**
        把身份锚住，只让视角描述变化。同一角色换 seed 就等于换一个人。
        """

    def close(self) -> None:
        """释放资源。默认无操作。"""


class ModelScopeProvider(ImageProvider):
    BASE = "https://api-inference.modelscope.cn/v1"

    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
        poll_interval: float = 2.0,
        timeout: float = 180.0,
    ) -> None:
        self.token = token or _require_env("MODELSCOPE_API_TOKEN")
        self.model = model or os.getenv(
            "MODELSCOPE_IMAGE_MODEL", "Tongyi-MAI/Z-Image-Turbo"
        )
        # 服务配置面板可下发自定义地址（IMAGE_BASE_URL），缺省用官方入口
        self.base = os.getenv("IMAGE_BASE_URL", self.BASE).rstrip("/")
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _headers(self, async_mode: bool = False, task_type: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if async_mode:
            headers["X-ModelScope-Async-Mode"] = "true"
        if task_type:
            # 轮询任务时必须带这个头。缺了它服务端会去错的队列里找，
            # 返回 code 500 "task not found" 并把状态标成 FAILED。
            headers["X-ModelScope-Task-Type"] = task_type
        return headers

    def generate(
        self,
        prompt: str,
        out_path: str,
        negative_prompt: str = "",
        size: str = "1024x576",
        seed: int | None = None,
    ) -> str:
        url = self._submit(prompt, negative_prompt, size, seed)
        self._download(url, out_path)
        return out_path

    @staticmethod
    def _download(url: str, out_path: str) -> None:
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        resp = requests.request("get", url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)

    def _submit(
        self,
        prompt: str,
        negative_prompt: str,
        size: str,
        seed: int | None,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": size,
        }
        # FLUX 系是 guidance-distilled，接口不吃 negative_prompt
        if negative_prompt and "flux" not in self.model.lower():
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed

        submitted = requests.request(
            "post",
            f"{self.base}/images/generations",
            headers=self._headers(async_mode=True),
            json=payload,
            timeout=60,
        )
        submitted.raise_for_status()
        task_id = submitted.json().get("task_id")
        if not task_id:
            raise RuntimeError(f"ModelScope 未返回 task_id：{submitted.text[:300]}")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            polled = requests.request(
                "get",
                f"{self.BASE}/tasks/{task_id}",
                headers=self._headers(task_type="image_generation"),
                timeout=60,
            )
            polled.raise_for_status()
            data = polled.json()
            status = str(data.get("task_status", "")).upper()

            if status == "SUCCEED":
                images = data.get("output_images") or []
                if not images:
                    raise RuntimeError(f"任务成功但无图片：{data}")
                return images[0]
            if status in {"FAILED", "FAIL", "CANCELED", "CANCELLED"}:
                raise RuntimeError(f"ModelScope 任务失败：{data}")

        raise TimeoutError(f"ModelScope 任务超时（{self.timeout}s），task_id={task_id}")


def _ratio_of(size: str) -> str:
    """把 "1024x576" 这类像素尺寸化简成 "16:9" 这类比例。"""
    from math import gcd

    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except ValueError:
        return "16:9"
    g = gcd(w, h) or 1
    return f"{w // g}:{h // g}"


class AgnesProvider(ImageProvider):
    """Agnes AI 文生图。

    选它的理由：免费额度长期有效，注册不用绑卡，是当前门槛最低的可用图像通道。
    与 ModelScope 的参数差异：Agnes 用 quality（"1K"/"2K"/"4K"）+ ratio（"16:9"）
    而不是像素尺寸，所以这里把 size 化简成比例。
    """

    BASE = "https://apihub.agnes-ai.com/v1"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        quality: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key or _require_env("AGNES_API_KEY")
        self.model = model or os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash")
        self.quality = quality or os.getenv("AGNES_QUALITY", "1K")
        self.timeout = timeout

    def generate(
        self,
        prompt: str,
        out_path: str,
        negative_prompt: str = "",
        size: str = "1024x576",
        seed: int | None = None,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": self.quality,
            "ratio": _ratio_of(size),
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed

        resp = requests.request(
            "post",
            f"{self.BASE}/images/generations",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        self._save(resp.json(), out_path)
        return out_path

    @staticmethod
    def _save(data: dict, out_path: str) -> None:
        """宽容解析：不同服务商的返回结构不一样，这里都试一遍。"""
        item = None
        for key in ("data", "images", "output", "result"):
            value = data.get(key)
            if isinstance(value, list) and value:
                item = value[0]
                break
            if isinstance(value, dict):
                item = value
                break
        if item is None:
            raise RuntimeError(f"Agnes 返回结构无法识别：{str(data)[:300]}")

        if isinstance(item, str):
            ModelScopeProvider._download(item, out_path)
            return
        if item.get("url"):
            ModelScopeProvider._download(item["url"], out_path)
            return
        if item.get("b64_json"):
            import base64

            parent = os.path.dirname(out_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(out_path, "wb") as fh:
                fh.write(base64.b64decode(item["b64_json"]))
            return
        raise RuntimeError(f"无法从返回中取到图片：{str(item)[:300]}")


def create_provider(name: str | None = None) -> ImageProvider:
    """按 IMAGE_PROVIDER 环境变量选择后端，默认 modelscope。

    模型一律由 MODELSCOPE_IMAGE_MODEL（= 面板的 image_model）决定。
    2026-09-14 起取消「质量模型」双轨：分镜图、定妆照、素材共用同一个模型，
    双轨的代价是面板改了模型但定妆照不生效，用户看到的是"改了没用"。
    """
    key = (name or os.getenv("IMAGE_PROVIDER", "modelscope")).strip().lower()
    if key == "modelscope":
        return ModelScopeProvider()
    if key == "agnes":
        return AgnesProvider()
    raise ValueError(f"未知的 IMAGE_PROVIDER：{key}（可选 modelscope / agnes）")

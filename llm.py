"""DeepSeek 文本调用。

模型现状（2026-09-11 核实）：
- 当前模型 ID 是 `deepseek-flash`，服务 DeepSeek-V4.1-Flash（2026-09-10 发布）。
- `deepseek-chat` / `deepseek-reasoner` 是**已通知弃用的兼容别名**，不要再用于新代码。
- 1M 上下文 / 384K 最大输出 / 支持 JSON Output。

两个必须显式处理的坑：
1. **思考模式默认开启**。V4 系列默认按 high effort 推理，推理 token 计入输出预算和费用；
   预算不足时会返回 HTTP 200 但 content 为空。这里显式关闭（本任务无需复杂推理）。
   另：思考模式下 temperature 参数**不生效**，关闭后才会按 0.7 起作用。
2. **JSON Output 偶发返回空内容**。官方文档明确提及此行为，要求配合明确的 JSON 指令。
   这里用重试兜住。

不引入 SDK 是为了让依赖只剩 requests，出问题时能一眼看到原始请求。
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

# 8000 只够一次输出十几镜的 JSON；放开到 16000 兜住「AI 自定镜数」时
# 一次拆 20-30 镜的情况（模型上限 384K，max_tokens 只是封顶，不按它计费）。
MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "16000"))
RETRIES = 2


def chat_json(system: str, user: str, temperature: float = 0.7) -> dict:
    """调用 DeepSeek，返回解析后的 JSON 对象。

    地址 / 模型 / 密钥都在调用时读环境变量——服务配置是前端面板运行时
    下发的（server 会写进 os.environ），import 时读死会导致改配置不生效。
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY。请在前端「服务配置」面板里填入密钥。"
        )
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

    last_error: Exception | None = None

    for attempt in range(RETRIES + 1):
        resp = requests.request(
            "post",
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": MAX_TOKENS,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
            },
            timeout=300,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"].get("content") or ""

        if not content.strip():
            last_error = RuntimeError("返回了空 content（JSON Output 已知偶发行为）")
            if attempt < RETRIES:
                time.sleep(1.5)
            continue

        try:
            return _loads(content)
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(1.5)

    raise RuntimeError(
        f"连续 {RETRIES + 1} 次未能取到可解析的 JSON，最后一次错误：{last_error}"
    )


def _loads(text: str) -> dict:
    """容错解析：剥掉 ```json 围栏，必要时从长文本里抠出第一个 JSON 对象。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))

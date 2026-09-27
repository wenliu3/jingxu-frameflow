"""测 ModelScope 图像接口能否并发提交（不轮询，纯看提交这一跳）。

为什么关心：`_submit` 超时后，**上游任务仍在 RUNNING**（实测过：客户端 180s 放弃后，
任务在 3 分钟后仍在跑，最终 SUCCEED）。如果账号侧有「同时只能有 1 个任务」的限制，
那么"失败就立刻重试"会**一边把旧任务继续堆在队列里、一边被上游直接拒绝** ——
重试策略就选错了，应该改成"拉长等待"。

做法：连提两个任务，只看提交响应，不等结果。
    - 两个都拿到 task_id        → 可并发，重试可行
    - 第二个被拒（4xx/错误码）  → 有并发限制，重试会更糟
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import requests  # noqa: E402

BASE = os.getenv("IMAGE_BASE_URL", "https://api-inference.modelscope.cn/v1").rstrip("/")
MODEL = os.getenv("MODELSCOPE_IMAGE_MODEL", "Tongyi-MAI/Z-Image-Turbo")


def submit(n: int, prompt: str) -> None:
    headers = {
        "Authorization": f"Bearer {os.getenv('MODELSCOPE_API_TOKEN', '').strip()}",
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true",
    }
    body = {"model": MODEL, "prompt": prompt, "n": 1, "size": "1024x576"}
    r = requests.request(
        "post", f"{BASE}/images/generations", headers=headers, json=body, timeout=60
    )
    print(f"提交 #{n}：HTTP {r.status_code}")
    try:
        data = r.json()
        print(f"   task_id = {data.get('task_id')}")
        if data.get("code"):
            print(f"   code={data.get('code')} message={data.get('message')}")
    except Exception:  # noqa: BLE001
        print(f"   {r.text[:400]}")
    print(f"   完整响应：{r.text[:400]}")


def main() -> int:
    print(f"model = {MODEL}")
    print("-" * 72)
    p = "a lone stone lantern in misty ruins, muted teal palette, cinematic light"
    submit(1, p)
    submit(2, p)          # 紧接着提第二个
    print("-" * 72)
    print("两个都拿到 task_id → 可并发；第二个被拒 → 账号侧有并发/配额限制。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""查一个 ModelScope 异步任务此刻的真实状态。

用途：`image_provider._submit` 超时后只抛出 task_id，**任务本身还在上游跑**。
过一会儿再查它的最终状态，就能区分三种完全不同的故障：

    - SUCCEED         → 只是**排队太久**，任务没坏。修法是加长超时 / 加长轮询，不必重试
    - RUNNING/QUEUED  → 严重积压（或永远轮不到），重试也是白搭，要换通道或降速
    - FAILED          → 上游算了但失败（内容/算力），重试有意义

用法：
    python dev/tools/check_ms_task.py <task_id>
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import requests  # noqa: E402

BASE = os.getenv("IMAGE_BASE_URL", "https://api-inference.modelscope.cn/v1").rstrip("/")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    task_id = sys.argv[1]
    token = os.getenv("MODELSCOPE_API_TOKEN", "").strip()
    if not token:
        print("[FAIL] 没有 MODELSCOPE_API_TOKEN")
        return 2

    url = f"{BASE}/tasks/{task_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        # ⚠️ 轮询必须带这个头，否则服务端去错的队列里找，返回 "task not found"
        "X-ModelScope-Task-Type": "image_generation",
    }
    print(f"查询 {url}")
    print(f"（查询时刻 {time.strftime('%H:%M:%S')}）")
    print("-" * 72)
    for i in range(1, 7):
        r = requests.request("get", url, headers=headers, timeout=30)
        print(f"#{i} HTTP {r.status_code}")
        try:
            data = r.json()
            status = data.get("task_status")
            print(f"   task_status = {status}")
            print(f"   {json.dumps(data, ensure_ascii=False)[:600]}")
            if str(status).upper() in {"SUCCEED", "FAILED", "FAIL", "CANCELED", "CANCELLED"}:
                print("-" * 72)
                print("→ 已经是终态，停止查询。")
                return 0
        except Exception:  # noqa: BLE001
            print(f"   非 JSON：{r.text[:300]}")
        if i < 6:
            time.sleep(10)
    print("-" * 72)
    print("→ 查了 6 次仍未到终态：任务长期卡在队列里，不是单纯慢。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

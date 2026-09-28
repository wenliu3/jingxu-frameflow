"""验证 Qwen/Qwen-Image-2.1 在魔搭 API-Inference 的可用性（2026-09-28）。

✅ 实测结论（2026-09-28）：**可用，且与项目参数全兼容**。
  · 真实提交成功：model=Qwen/Qwen-Image-2.1 + size=1024x576 + negative_prompt
    + seed → 10.7s 出图 703KB PNG。换模型是纯配置操作，零代码改动。
  · ⚠️ 两个**不可信**的判据（别再被它们误导）：
    ① GET /v1/models 的清单只列文本/对话类模型 —— 连日常在用的
       Tongyi-MAI/Z-Image-Turbo 都不在里面，"清单里没有"推不出"生图不可用"；
    ② 模型页内嵌 JSON 的 SupportApiInference 字段显示 false，与页面徽标
       （支持图像 API-Inference）和实际调用结果都矛盾，以真实提交为准。

背景：模型页显示「支持图像 API-Inference」徽标与代码范例框，但页面内嵌
JSON 里 `SupportApiInference=false`，两处证据矛盾。这里做两级验证：

  ① 只读（默认）：GET {base}/models 查"在服务模型清单"里有没有它 —— 零额度消耗；
  ② --image：真实出图一次，**用项目实际会发的参数**（size=1024x576 /
     negative_prompt / seed），验证尺寸与参数兼容性 —— 消耗 1 次额度。

用法：
    python dev/probe_qwen_image_21.py            # 只读清单
    python dev/probe_qwen_image_21.py --image    # 额外真实出图一次
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TARGET = "Qwen/Qwen-Image-2.1"


def _load_config() -> tuple[str, str]:
    """从 service_config.json 读图像通道配置（与后端面板同源）。"""
    path = os.path.join(ROOT, "service_config.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    token = str(cfg.get("image_api_key") or "").strip()
    base = str(cfg.get("image_base_url") or "https://api-inference.modelscope.cn/v1").rstrip("/")
    if not token:
        sys.exit("service_config.json 里没有 image_api_key")
    return token, base


def list_models(token: str, base: str) -> None:
    import requests

    resp = requests.get(
        f"{base}/models",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    print(f"[只读] GET /models → HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:400])
        return
    data = resp.json()
    items = data.get("data") if isinstance(data, dict) else data
    ids = []
    for it in items or []:
        if isinstance(it, dict):
            ids.append(str(it.get("id") or it.get("model") or ""))
        else:
            ids.append(str(it))
    print(f"       在服务模型数：{len(ids)}")
    for h in sorted(ids):
        print(f"       · {h}")
    exact = [i for i in ids if i == TARGET]
    print(f"       目标 {TARGET} 在清单里：{'是' if exact else '否'}")


def probe_image(token: str, base: str) -> None:
    """真实出图一次：用项目实际会发的参数验证兼容性。"""
    os.environ["MODELSCOPE_API_TOKEN"] = token
    os.environ["IMAGE_BASE_URL"] = base
    os.environ["IMAGE_PROVIDER"] = "modelscope"
    import time

    import image_provider

    out = os.path.join(os.environ.get("TEMP") or ROOT, "probe_qwen_image_21.png")
    provider = image_provider.ModelScopeProvider(model=TARGET)
    t0 = time.time()
    provider.generate(
        "a photorealistic red apple on a wooden table, soft window light, shallow depth of field",
        out,
        negative_prompt="blurry, watermark, text",
        size="1024x576",          # 项目分镜/场景最常用的尺寸
        seed=20260928,
    )
    size = os.path.getsize(out)
    print(f"[实测] 出图成功：{out}（{size} bytes，{time.time() - t0:.1f}s）")


if __name__ == "__main__":
    token, base = _load_config()
    list_models(token, base)
    if "--image" in sys.argv:
        probe_image(token, base)

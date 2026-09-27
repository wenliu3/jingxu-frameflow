"""诊断：复现「出图间歇性失败」，把上游返回的原始报错打出来。

背景（2026-09-20 夜）：同一批场景素材，有的第一次就成，有的 500 两次后第三次成，
有的 500 三次、成两次、又 500 —— **同样输入重试就能过**，说明不是内容问题，
而是上游间歇性失败或限流。但接口把异常压成一句 detail 只在 toast 里闪一下，
服务端日志里只有一行 500，**具体 HTTP 码与响应体拿不到**。

这个脚本用**同一个提示词**连打 N 次，捕获第一次失败并打印：
    异常类型 / 完整消息 / HTTP 状态码 / **响应体原文** / 本次耗时

对比"失败耗时 vs 成功耗时"还能顺带区分：
    - 秒级失败  → 提交阶段被上游直接拒（限流 429 / 参数 400）
    - 中途失败  → 任务跑起来后 FAILED（上游算力抖动）
    - 长耗时失败 → 超时（provider 里 timeout=180s）

用法：
    python dev/tools/probe_image_failures.py [次数] [比例] [--all] [--timeout=45]
    例：python dev/tools/probe_image_failures.py 8 16:9
        python dev/tools/probe_image_failures.py 5 16:9 --all --timeout=45

    --all          不要停在第一次失败，跑满次数 → 用来**测成功率**
    --timeout=N    把轮询上限压到 N 秒。正常出图 <10 秒，所以 45 秒还没好
                   基本就是排队卡住 → 用短超时快速采样，不必每次干等 180 秒
"""

from __future__ import annotations

import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import image_provider  # noqa: E402
import pipeline  # noqa: E402

# 与「场景」分支同构的一段提示词：环境 + 镜头 + 光影，禁人物
PROMPT = pipeline._scene_prompt(
    "高精度3D半写实国漫风，暖金主调配青灰暗部",
    "破败的城郊小庙，塌了半边的屋脊露出椽木，供桌歪斜、香炉倾倒积灰，"
    "墙面斑驳脱落露出土坯，门前枯草与碎石，远处是荒芜的野地与低垂暮色，"
    "冷调环境光穿过破洞落在积灰的地面上，空气里有浮尘",
)
NEGATIVE = (
    image_provider.DEFAULT_NEGATIVE
    + ", person, people, human, human figure, crowd, silhouette"
)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    attempts = int(args[0]) if len(args) > 0 else 6
    ratio = args[1] if len(args) > 1 else "16:9"
    keep_going = "--all" in flags
    timeout = 180.0
    for f in flags:
        if f.startswith("--timeout="):
            timeout = float(f.split("=", 1)[1])
    size = pipeline.SIZE_TABLE.get(ratio, "1024x576")

    # ⚠️ 直接实例化而不是 create_provider()：只有这样才能改 timeout
    provider = image_provider.ModelScopeProvider(timeout=timeout)
    print(f"provider = {type(provider).__name__} / {provider.model}")
    print(f"size     = {ratio} → {size}")
    print(f"轮询上限  = {timeout:.0f}s（正常出图 <10s，超过基本就是排队卡住）")
    print(f"提示词    = {len(PROMPT)} 字（{PROMPT[:60]}…）")
    print(f"共 {attempts} 次，同一个提示词，{'跑满（测成功率）' if keep_going else '失败即停'}")
    print("=" * 72)

    ok = fail = 0
    ok_times: list[float] = []
    fail_times: list[float] = []
    for i in range(1, attempts + 1):
        out = os.path.join(ROOT, "outputs", "_diag", f"stress_{i:02d}.png")
        t0 = time.monotonic()
        try:
            provider.generate(
                PROMPT,
                out,
                negative_prompt=NEGATIVE,
                size=size,
                seed=pipeline._stable_seed(f"stress|{i}|{os.urandom(4).hex()}"),
            )
            dt = time.monotonic() - t0
            ok += 1
            ok_times.append(dt)
            print(f"#{i:>2}  [ OK ] {dt:6.1f}s  {os.path.getsize(out)} bytes")
        except Exception as exc:  # noqa: BLE001
            dt = time.monotonic() - t0
            fail += 1
            fail_times.append(dt)
            print(f"#{i:>2}  [FAIL] {dt:6.1f}s  {type(exc).__name__}: {exc}")
            resp = getattr(exc, "response", None)
            if resp is not None:
                print(f"        HTTP {resp.status_code}")
                print(f"        响应头：{dict(resp.headers)}")
                print(f"        响应体：{resp.text[:2000]}")
            if not keep_going:
                traceback.print_exc()
                break

    print("=" * 72)
    print(f"成功 {ok} / 失败 {fail}")
    if ok_times:
        print(f"  成功耗时：{', '.join(f'{t:.1f}s' for t in ok_times)}")
    if fail_times:
        print(f"  失败耗时：{', '.join(f'{t:.1f}s' for t in fail_times)}")
    if ok and not fail:
        print("→ 全成功，当前窗口上游正常。（把次数调大再跑一轮更能看出抖动）")
    elif ok and fail:
        print("→ **双峰分布**：成功的几秒就回、失败的卡到超时 → 排队抽签，不是内容或参数问题。")
    elif fail:
        print(f"→ 全失败且都卡满 {timeout:.0f}s：当前上游整体拥塞，重试也是回到队尾。")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

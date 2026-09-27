"""诊断：复现「某个角色出形象图失败」的整条链，并把**原始报错**打出来。

为什么需要它：
    `POST /api/tasks/{id}/characters/{i}/portrait` 内部把任何异常都压成
    `HTTPException(500, "定妆照生成失败：<类型>: <消息>")`。这条 detail 只走前端 toast，
    一闪就没，服务端 stdout 里**只留下一行 500，没有原因**（Starlette 对已捕获的
    HTTPException 不打 traceback）。于是"出图失败"这件事事后完全无法复盘 ——
    2026-09-20 凌宸那次就是这样，只能靠复现才知道是上游瞬时故障。

它做三件事，都不写进用户的真实作品目录：
    ① 读 `outputs/<task_id>/project.json` 还原 Project（style / logline / 已有角色）
    ② 走 `agents.compose_portrait_sheet` 扩写，打印 body / sheet / front_prompt 的**字数与全文**
    ③ 走 `image_provider` 真出一张正面图，落盘到 `outputs/_diag/`
    失败时打印异常类型 + 完整消息 + HTTP 状态码 + **响应体原文**（这是关键）

用法：
    python dev/tools/check_portrait_chain.py <task_id> <角色下标> "角色描述"
    例：python dev/tools/check_portrait_chain.py c355ad940012 1 "银发青年剑客，清冷"
"""

from __future__ import annotations

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import agents  # noqa: E402
import image_provider  # noqa: E402
import pipeline  # noqa: E402


class _Char:
    """只喂 compose_portrait_sheet 真正读的两个字段。"""

    def __init__(self, name: str, anchor: str = "") -> None:
        self.name = name
        self.anchor = anchor


class _Project:
    def __init__(self, data: dict) -> None:
        self.logline = data.get("logline", "")
        self.style = data.get("style", "")
        self.characters = [
            _Char(c.get("name", ""), c.get("anchor", ""))
            for c in data.get("characters", [])
        ]


def _load_project(task_id: str) -> tuple[_Project, dict]:
    path = os.path.join(ROOT, "outputs", task_id, "project.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return _Project(data), data


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    task_id, index, hint = sys.argv[1], int(sys.argv[2]), sys.argv[3]

    project, raw = _load_project(task_id)
    if index >= len(project.characters):
        print(f"[FAIL] 下标 {index} 越界，这个作品只有 {len(project.characters)} 个角色")
        return 2
    char = project.characters[index]

    print(f"作品   = {task_id}（{raw.get('title') or '未命名'}）")
    print(f"角色   = {char.name}（下标 {index}）")
    print(f"style  = {project.style!r}   logline = {project.logline!r}")
    print(f"hint   = {len(hint)} 字")
    print("-" * 72)

    print("① 角色 Agent 扩写……")
    try:
        designed = agents.compose_portrait_sheet(project, char.name, hint=hint)
    except Exception as exc:  # noqa: BLE001
        # 这一层失败，接口给的是 502（不是 500）—— 先看这里能快速分流
        print(f"[FAIL] 角色设计失败（接口会返回 502）：{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1
    person = designed["body"]
    print(f"   body  字数 = {len(person)}")
    print(f"   sheet 字数 = {len(designed['sheet'])}")

    _, _, view_desc, _ = pipeline.PORTRAIT_VIEWS[0]
    front_prompt = pipeline._character_portrait_prompt(project.style, person, view_desc)
    print(f"   front_prompt 字数 = {len(front_prompt)}")
    print(f"   body 全文 = {person}")
    print(f"   front_prompt 全文 = {front_prompt}")

    print("-" * 72)
    print("② 出正面图（默认 1:1，与弹窗默认一致）……")
    provider = image_provider.create_provider()
    print(f"   provider = {type(provider).__name__} / {provider.model}")
    out = os.path.join(ROOT, "outputs", "_diag", f"{char.name}_front.png")
    try:
        provider.generate(
            front_prompt,
            out,
            negative_prompt=pipeline.PORTRAIT_NEGATIVE,
            size=pipeline.SIZE_TABLE["1:1"],
            seed=pipeline._stable_seed(f"diag|{char.name}|{person}|{os.urandom(4).hex()}"),
        )
        print(f"[ OK ] 落盘 {out}（{os.path.getsize(out)} bytes）")
        return 0
    except Exception as exc:  # noqa: BLE001
        # ⚠️ 这里就是接口报 500 的那个点
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        resp = getattr(exc, "response", None)
        if resp is not None:
            print(f"   HTTP status = {resp.status_code}")
            print(f"   响应体 = {resp.text[:2000]}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

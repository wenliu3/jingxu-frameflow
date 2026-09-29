# -*- coding: utf-8 -*-
"""验证出片轮询的**容错**：隧道抖一下，不能把整条片子弄丢。

背景（2026-09-29 修）：
    H3 跑一条 10 秒的片实测要 **31 分钟**（见 `ComfyUIVideoProvider.__init__` 里那段），
    这中间隧道断几秒、ComfyUI 短暂卡住都很常见。而 `_wait` 从前一次 `requests.get`
    失败就直接抛出去 —— 用户看到"出片失败"，可 ComfyUI 那边片子还在照常生成，
    最后好端端躺在 history 里（花了 31 分钟 GPU 时间，白跑）。
    换成自己服务器做跳板之后（见 tunnel_aliyun.sh）不会有 60 分钟硬断线了，
    但网络抖动仍然存在，所以这里要能扛。

现在的规矩：
    · 连接类错误 / HTTP 5xx  → 记一笔接着等，连续 `_POLL_TOLERANCE` 次才放弃
    · HTTP 4xx              → 真出错（prompt_id 不认之类），立刻抛，不傻等

跑法（在项目根执行，**全程打桩：不联网、不碰 ComfyUI、不烧额度**）：
    python dev/api/_verify_poll_resilience.py
"""
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import video_provider as vp  # noqa: E402

WF = os.path.join(ROOT, "comfyui", "h3_r2v_api.json")

# 一份"已经跑完"的 history 响应。outputs 的结构故意套了两层，
# 因为 _wait 是"从 outputs 里翻 filename"、不依赖具体节点 id / 键名。
DONE = {
    "pid123": {
        "status": {"completed": True, "status_str": "success"},
        "outputs": {"60": {"gifs": [{"filename": "出片.mp4", "subfolder": ""}]}},
    }
}

results: list[bool] = []
calls = {"n": 0}


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"\n        → {extra}" if extra else ""))


class FakeResp:
    """够 _wait 用的最小响应对象（raise_for_status / json / status_code）。"""

    def __init__(self, payload=None, code: int = 200) -> None:
        self._payload = payload if payload is not None else {}
        self.status_code = code
        self.response = self          # HTTPError 会去读 exc.response.status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


def install(behaviour):
    """把 requests.get 换成 behaviour(第几次调用) —— 第几次是 0 起。

    behaviour 要么返回一个 FakeResp，要么直接抛异常（模拟断线）。
    """
    calls["n"] = 0

    def _get(url, **kw):
        i = calls["n"]
        calls["n"] += 1
        return behaviour(i)

    vp.requests.get = _get


def new_provider():
    # poll_interval 调到 0.01：这些用例关心的是"重试逻辑"，不想真等 10 秒一轮
    return vp.ComfyUIVideoProvider(
        base_url="http://fake-comfyui", workflow_path=WF,
        poll_interval=0.01, timeout_per_shot=30,
    )


print("=" * 74)
print("出片轮询容错验证（打桩，不联网）")
print("=" * 74 + "\n")

# ---------- 1) 抖几下还能等到结果 ----------
print("-- 1) 连断 3 次后恢复正常 --")
install(lambda i: (_ for _ in ()).throw(requests.exceptions.ConnectionError("tunnel reset"))
        if i < 3 else FakeResp(DONE))
p = new_provider()
got = p._wait("pid123")
check("断线 3 次之后仍然拿到了片子（不再白跑）", got == ("出片.mp4", ""), str(got))
check("而且确实重试了（一共问了 4 次：3 次失败 + 1 次成功）", calls["n"] == 4, f"calls={calls['n']}")

# ---------- 2) 5xx 也算"抖一下"，同样重试 ----------
print("\n-- 2) ComfyUI 连回两个 500 再恢复 --")
install(lambda i: FakeResp(code=500) if i < 2 else FakeResp(DONE))
p = new_provider()
got2 = p._wait("pid123")
check("HTTP 500 被当成暂时故障（重试后拿到片子）", got2 == ("出片.mp4", ""), str(got2))
check("重试次数对得上（2 次 500 + 1 次成功）", calls["n"] == 3, f"calls={calls['n']}")

# ---------- 3) 一直连不上：要有上限，且话说清楚 ----------
print("\n-- 3) 一直连不上（不该无限等）--")
install(lambda i: (_ for _ in ()).throw(requests.exceptions.ConnectionError("tunnel down")))
p = new_provider()
try:
    p._wait("pid123")
    check("一直连不上时应该报错", False, "居然返回了结果")
except RuntimeError as exc:
    msg = str(exc)
    check("一直连不上时按 _POLL_TOLERANCE 放弃", calls["n"] == p._POLL_TOLERANCE,
          f"calls={calls['n']} tolerance={p._POLL_TOLERANCE}")
    check("报错里说清是「连续 N 次联系不上」", "连续" in msg and "联系不上" in msg, msg[:80])
    check("报错里给了下一步（片子可能在 ComfyUI 那边）", "output" in msg and "prompt_id" in msg, msg[:120])
except Exception as exc:                              # noqa: BLE001
    check("一直连不上时应该报 RuntimeError", False, f"{type(exc).__name__}: {exc}")

# ---------- 4) 4xx 是真出错，立刻抛、不浪费 30 轮 ----------
print("\n-- 4) HTTP 404（prompt_id 不认）--")
install(lambda i: FakeResp(code=404))
p = new_provider()
try:
    p._wait("pid123")
    check("404 时应该报错", False, "居然返回了结果")
except requests.exceptions.HTTPError:
    check("4xx 立刻抛，不重试（只问了 1 次）", calls["n"] == 1, f"calls={calls['n']}")
except Exception as exc:                              # noqa: BLE001
    check("4xx 应该原样抛 HTTPError", False, f"{type(exc).__name__}: {exc}")

# ---------- 5) 正常工作流没被这套容错改坏 ----------
print("\n-- 5) 一切正常时（不该有额外请求 / 不该变慢）--")
install(lambda i: FakeResp(DONE))
p = new_provider()
got3 = p._wait("pid123")
check("一次就拿到结果", got3 == ("出片.mp4", "") and calls["n"] == 1, f"{got3} calls={calls['n']}")

bad = results.count(False)
print("\n" + "=" * 74)
print(f"结果：{len(results) - bad}/{len(results)} 通过" + ("  ← 有 FAIL" if bad else ""))
print("=" * 74)
sys.exit(1 if bad else 0)

# -*- coding: utf-8 -*-
"""「AI 生成音色」—— 后端验证。

**不烧额度**的部分（默认全跑）：
  1) 非法 gender → 422
  2) 角色下标越界 → 404
  3) `_verified_voice_id` 的回退逻辑（纯函数单测）—— 这是最要紧的一条：
     模型会编一个池子外的 id，或跨性别挑，两者都必须被兜住，否则角色会配上
     明显不对的声音，而**用户看不出是模型错的**。

会真调模型的部分（默认 SKIP；要跑：E2E_LIVE=1）：
  4) 真跑一次：deepseek 挑音色 + 音频后端合成样本
     → 断言 tts_voice 在池子里、性别对得上、样本文件真的落盘了。
     成本极低（minimax 按字符计费，一次样本 20 字 ≈ 0.007 元），
     但它**不是**出图/出视频那种日额度，所以单独一个闸。

跑完自动 DELETE 掉建的 draft。
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVE = os.environ.get("E2E_LIVE") == "1"
_results = []


def load_env():
    """把 .env 灌进 os.environ —— 直接跑脚本时没有 server 那套运行时下发。"""
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def req(method, path, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw or "null")
        except Exception:
            return e.code, {"detail": raw}


def check(name, cond, extra=""):
    _results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"\n        → {extra}" if extra else ""))


def detail(res):
    d = (res or {}).get("detail")
    return str(d) if d else ""


def main():
    load_env()
    print("=" * 74)
    print("「AI 生成音色」· 后端验证" + ("（含真实调用）" if LIVE else "（不烧额度）"))
    print("=" * 74)

    # ---------- 先做纯函数单测：_verified_voice_id 的回退 ----------
    sys.path.insert(0, os.path.join(ROOT, "server"))
    sys.path.insert(0, ROOT)
    import app as server_app
    import tts

    voices = tts.list_voices()
    print(f"\n[音频后端] {tts.provider_label()} · 音色池 {len(voices)} 个")
    female = next((v for v in voices if v["gender"] == "female"), None)
    male = next((v for v in voices if v["gender"] == "male"), None)
    if not female or not male:
        print("  当前池子缺少单一性别，无法验证回退逻辑")
        return 1

    check("Agent 给的合法 id 原样通过",
          server_app._verified_voice_id(female["id"], voices, "female", "") == female["id"])
    check("模型编的池外 id → 被回退到池内",
          server_app._verified_voice_id("female-nonexistent", voices, "female", "")
          in {v["id"] for v in voices},
          server_app._verified_voice_id("female-nonexistent", voices, "female", ""))
    got = server_app._verified_voice_id(male["id"], voices, "female", "")
    check("模型跨性别挑（男声 id + 指定女声）→ 回退成女声",
          tts.voice_gender(got) == "female", f"got={got}")
    got2 = server_app._verified_voice_id(male["id"], voices, "female", "低沉沙哑的中年男声")
    check("跨性别 + 描述也指向男声 → 仍坚持用户选的性别",
          tts.voice_gender(got2) == "female", f"got={got2}")

    # ---------- 接口层 ----------
    st, draft = req("POST", "/api/tasks/draft", {"title": "_verify_voice"})
    tid = (draft or {}).get("task_id") or (draft or {}).get("id")
    if not tid:
        print(f"  建 draft 失败：{st} {draft}")
        return 1
    print(f"\n[临时作品] {tid}\n")

    try:
        st, ch = req("POST", f"/api/tasks/{tid}/characters",
                     {"name": "验证角色", "design": False})
        if st != 200:
            print(f"  建角色失败：{st} {ch}")
            return 1

        st, res = req("POST", f"/api/tasks/{tid}/characters/0/voice/generate",
                      {"prompt": "低沉沙哑", "gender": "robot"})
        check("非法 gender → 422", st == 422, f"{st} {detail(res)}")

        st, res = req("POST", f"/api/tasks/{tid}/characters/9/voice/generate",
                      {"prompt": "随便", "gender": "female"})
        check("角色下标越界 → 404", st == 404, f"{st} {detail(res)}")

        # ---------- 真跑一次 ----------
        if not LIVE:
            print("  SKIP  真实挑音色 + 合成样本（要跑：E2E_LIVE=1）")
        else:
            st, res = req("POST", f"/api/tasks/{tid}/characters/0/voice/generate",
                          {"prompt": "低沉沙哑的中年男声，语速慢，带点疲惫", "gender": "male"})
            ok = st == 200
            check("真实生成：接口返回 200", ok, f"{st} {detail(res) if not ok else ''}")
            if ok:
                vid = res.get("tts_voice", "")
                ids = {v["id"] for v in voices}
                check("挑中的音色在池子里", vid in ids, vid)
                check("挑中的音色性别与所选一致",
                      tts.voice_gender(vid) == "male", f"{vid} → {tts.voice_gender(vid)}")
                fname = res.get("voice_sample", "")
                path = os.path.join(ROOT, "outputs", tid, "characters", fname) if fname else ""
                check("样本文件真的落盘了", bool(fname) and os.path.isfile(path), path)
                if os.path.isfile(path):
                    print(f"        样本大小 {os.path.getsize(path)} 字节 · 音色 {tts.voice_label(vid)}（{vid}）")
    finally:
        st, _ = req("DELETE", f"/api/tasks/{tid}?confirm=true&purge=true")
        print(f"\n[清理] 删除临时作品 {tid} → {st}")

    bad = _results.count(False)
    print("\n" + "=" * 74)
    print(f"结果：{len(_results) - bad}/{len(_results)} 通过" + ("" if bad == 0 else "  ← 有 FAIL"))
    print("=" * 74)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

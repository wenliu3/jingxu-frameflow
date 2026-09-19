# -*- coding: utf-8 -*-
"""「其他图片」AI 生成 —— 后端接线验证（默认**不烧额度**）。

验的是"接口收得对 / 拒得对"，不验出图效果：

  1) image + 空 prompt  → 422「先描述一下你想要的画面」
     ★ 关键一条：说明 kind 校验已放行 image，不再被旧文案"只支持场景与道具"挡掉
  2) audio + 真 prompt  → 422（黑名单仍然有效，上传型素材没被误放行）
  3) reroll 对 image    → 422，且**上传的图还在**（防"删完不补"那道保护）
  4) image + 真 prompt  → 真出图，默认 SKIP；要跑：E2E_LIVE=1（会走日额度）

跑完自动 DELETE 掉建的 draft 作品，不留垃圾。
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE = os.environ.get("E2E_LIVE") == "1"

_results = []


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
    print("=" * 74)
    print("「其他图片」AI 生成 · 后端接线验证" + ("（含真实出图）" if LIVE else "（不烧额度）"))
    print("=" * 74)

    st, draft = req("POST", "/api/tasks/draft", {"title": "_verify_other_image"})
    tid = (draft or {}).get("task_id") or (draft or {}).get("id")
    if not tid:
        print(f"  建 draft 失败：{st} {draft}")
        return 1
    print(f"\n[临时作品] {tid}\n")

    try:
        # ---------- 建一个 image 素材 + 一个 audio 素材 ----------
        st, img = req("POST", f"/api/tasks/{tid}/assets",
                      {"kind": "image", "name": "验证用图", "design": False})
        if st != 200:
            print(f"  建 image 素材失败：{st} {img}")
            return 1
        st, aud = req("POST", f"/api/tasks/{tid}/assets",
                      {"kind": "audio", "name": "验证用音频", "design": False})
        if st != 200:
            print(f"  建 audio 素材失败：{st} {aud}")
            return 1

        st, task = req("GET", f"/api/tasks/{tid}")
        assets = ((task or {}).get("project") or {}).get("assets") or []
        idx_img = next((i for i, a in enumerate(assets) if a.get("kind") == "image"), None)
        idx_aud = next((i for i, a in enumerate(assets) if a.get("kind") == "audio"), None)
        if idx_img is None or idx_aud is None:
            print(f"  拿不到素材下标：{assets}")
            return 1

        # 给 image 素材传一张真图（用来验"reroll 不会把它删掉"）
        face = os.path.join(ROOT, "_e2e_face.png")
        with open(face, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        st, up = req("POST", f"/api/tasks/{tid}/assets/{idx_img}/upload",
                     {"filename": "_e2e_face.png", "data_b64": b64})
        check("image 素材可上传", st == 200, f"{st} {up if st != 200 else ''}")

        # ---------- 1) image + 空 prompt ----------
        st, res = req("POST", f"/api/tasks/{tid}/assets/{idx_img}/generate",
                      {"prompt": "", "ratio": ""})
        check("image 空描述 → 422 且提示新文案",
              st == 422 and "先描述一下你想要的画面" in detail(res),
              f"{st} {detail(res)}")

        # ---------- 2) audio 仍被挡 ----------
        st, res = req("POST", f"/api/tasks/{tid}/assets/{idx_aud}/generate",
                      {"prompt": "随便一段画面", "ratio": ""})
        check("audio → 422（上传型素材没被误放行）",
              st == 422 and "只支持场景、道具与其他图片" in detail(res),
              f"{st} {detail(res)}")

        # ---------- 3) reroll 对 image 拒绝，且不删图 ----------
        st, res = req("POST", f"/api/tasks/{tid}/assets/{idx_img}/reroll")
        check("reroll 对 image → 422", st == 422, f"{st} {detail(res)}")
        st, task = req("GET", f"/api/tasks/{tid}")
        assets = ((task or {}).get("project") or {}).get("assets") or []
        imgs = assets[idx_img].get("images") or []
        alive = [p for p in imgs if os.path.isfile(p)]
        check("reroll 被拒后，已上传的图仍在磁盘上",
              bool(imgs) and len(alive) == len(imgs),
              f"images={imgs} alive={alive}")

        # ---------- 4) 真出图（默认跳过） ----------
        if not LIVE:
            print("  SKIP  image + 真描述 → 真实出图（要跑：E2E_LIVE=1）")
        else:
            st, res = req("POST", f"/api/tasks/{tid}/assets/{idx_img}/generate",
                          {"prompt": "黄昏的天台，主角背对镜头站在栏杆边，远处城市灯火初上，逆光",
                           "ratio": ""})
            ok = st == 200 and bool((res or {}).get("images"))
            check("image + 真描述 → 出图成功", ok,
                  f"{st} {str(res)[:200] if not ok else (res or {}).get('images')}")
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

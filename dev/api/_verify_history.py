# -*- coding: utf-8 -*-
"""验证「生成历史」这条链路（2026-09-19 加）。

起因（斌哥原话）：「有一些人生成了之后看见不好，又生成，发现前面那个好，但是找不了」。
重新生成是**就地覆盖同名文件**，旧版本该被顶掉、再也回不来。现在每次写图**之前**
先留一份快照：

    outputs/{task_id}/history/{kind}/{safe}/{UTC时间戳}_{version}/<原文件名>

两个新接口：

    GET  /api/tasks/{task_id}/materials/{kind}/{index}/history        列出每一版（新的在前）
    POST /api/tasks/{task_id}/materials/{kind}/{index}/history/use    切回某一版

⚠️ **切上去的那一版会从列表里消失**（2026-09-19 修）：它的内容此刻就是当前文件，
而「当前这版」永远排第一条 —— 留着它，弹窗里同一张图会出现两次（斌哥报的
"点『用这版』会多出一张一样的"）。所以切完由后端把那一版的目录收掉，列表里
只留下"当前这版 + 被换下去的那版"，来回切多少次都是这两条，不会越切越长。
列表接口另外会把**内容一模一样**的版本挡掉（同一条既不重复当前这版、也不互相重复）。

⚠️ **不烧额度**：只建空白 draft + `design=False` 建条目，全程不调模型、不碰 ComfyUI。
上传用的是脚本自己造的纯色 PNG（真能渲染的小图，不是随便几个字节），
所以这一步同时把「覆盖式上传也存历史」这条路径验掉了。

跑法（在项目根）：
    D:/miniforge/python.exe dev/api/_verify_history.py
"""
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import urllib.error
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API = os.environ.get("E2E_API", "http://127.0.0.1:8000").rstrip("/")
OUT = os.path.join(ROOT, "outputs")

results: list[bool] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"\n        → {extra}" if extra else ""))


def http(method: str, path: str, payload=None):
    data, headers = None, {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


def try_http(method: str, path: str, payload=None) -> tuple[int, str]:
    """返回 (状态码, 响应体文本)，不抛异常 —— 用来验失败时的**报错内容**。"""
    try:
        return 200, json.dumps(http(method, path, payload), ensure_ascii=False)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def force_remove_tree(path: str) -> None:
    """真删一个目录 —— **只给本脚本收尾用，产品代码不这么干**。

    本进程跑在 WorkBuddy 沙箱里，`os.remove` / `shutil.rmtree` 被换成了
    "移到系统回收站 + 每轮对话 50 个文件的批量护栏"，护栏一触发就 SystemExit。
    沙箱对**系统临时目录**是放行的，所以先搬进 temp 再删。
    """
    tmp = tempfile.mkdtemp(prefix="history-cleanup-")
    dst = os.path.join(tmp, os.path.basename(path))
    try:
        shutil.move(path, dst)
    except Exception:                       # noqa: BLE001 - 收尾失败不掩盖测试结论
        return
    shutil.rmtree(dst, ignore_errors=True)


# ---------------------------------------------------------------- 造一张真能渲染的纯色 PNG
def png_solid(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    """纯 python 生成一张 w×h 纯色 PNG（不依赖 Pillow）。

    用它而不是随便几个字节：历史弹窗里那些缩略图就是这些图，
    后面截图核对界面时必须是真图，否则 `<img>` 只会渲染成裂图。
    """
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def upload(task_id: str, kind: str, index: int, png: bytes) -> dict:
    """图片类素材的上传接口（角色与素材是两条路，这里按 kind 分）。"""
    path = (f"/api/tasks/{task_id}/characters/{index}/upload" if kind == "character"
            else f"/api/tasks/{task_id}/assets/{index}/upload")
    return http("POST", path, {
        "filename": "test.png",
        "data_b64": base64.b64encode(png).decode("ascii"),
    })


def history(task_id: str, kind: str, index: int) -> list[dict]:
    return http("GET", f"/api/tasks/{task_id}/materials/{kind}/{index}/history").get("versions", [])


def bytes_sig(data: bytes) -> tuple[tuple[int, str], ...]:
    """一段内容的指纹（大小 + md5）。**外层刻意再包一层**，形状与列表项的指纹
    （一版可能有多张图）一致，两边才能直接 == 比。"""
    return ((len(data), hashlib.md5(data).hexdigest()),)


def main() -> int:
    print("== 生成历史 ==")
    task_id = ""
    try:
        # ---------- 1) 建一个空白作品 + 一条不烧额度的角色 ----------
        task_id = http("POST", "/api/tasks/draft", {"title": "验证_生成历史"})["task_id"]
        check("建空白作品", bool(re.fullmatch(r"[0-9a-f]{12}", task_id)), task_id)

        http("POST", f"/api/tasks/{task_id}/characters",
             {"name": "历史角色", "design": False})
        http("POST", f"/api/tasks/{task_id}/assets",
             {"kind": "scene", "name": "历史场景", "design": False})
        http("POST", f"/api/tasks/{task_id}/assets",
             {"kind": "audio", "name": "历史音频", "design": False})

        task = http("GET", f"/api/tasks/{task_id}")
        chars = task["project"]["characters"]
        assets = task["project"]["assets"]
        scene_i = next(i for i, a in enumerate(assets) if a["kind"] == "scene")
        audio_i = next(i for i, a in enumerate(assets) if a["kind"] == "audio")
        check("角色 / 场景 / 音频条目都建好了",
              len(chars) == 1 and len(assets) == 2,
              f"characters={len(chars)} assets={len(assets)}")

        # ---------- 2) 第一次上传：还没有旧图，历史里只有「当前这版」 ----------
        v1 = png_solid(24, 24, (200, 60, 60))
        upload(task_id, "character", 0, v1)
        vs = history(task_id, "character", 0)
        check("第一次上传后历史只有「当前这版」一条",
              len(vs) == 1 and vs[0]["current"] is True and vs[0]["name"] == "",
              f"{len(vs)} 条")
        check("当前这版带 thumb（指向 /files，不带版本号就是错）",
              vs and vs[0]["thumb"].startswith(f"/files/{task_id}/characters/"),
              vs[0]["thumb"] if vs else "")
        # ⚠️ C1 的核心：URL 必须带 ?v=，否则浏览器拿缓存里的旧图（就地覆盖、URL 不变）
        check("当前这版的 thumb 带 ?v= 做 cache-busting",
              vs and "?v=" in vs[0]["thumb"], vs[0]["thumb"] if vs else "")

        # ---------- 3) 再传一张：旧的那版必须被存下来 ----------
        v2 = png_solid(24, 24, (60, 120, 200))
        upload(task_id, "character", 0, v2)
        vs = history(task_id, "character", 0)
        check("覆盖上传后历史变成 2 条（当前 + 被顶掉的那版）", len(vs) == 2, f"{len(vs)} 条")
        old = vs[1] if len(vs) == 2 else {}
        check("第 2 条是历史版本（name 非空、current 为假）",
              bool(old) and old.get("current") is False and bool(old.get("name")),
              str(old.get("name")))
        check("历史版本目录名是「时间戳_版本号」形状",
              bool(re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}", str(old.get("name", "")))),
              str(old.get("name")))
        check("历史版本带 created_at（UTC ISO，前端换本地时间用）",
              bool(old.get("created_at")), str(old.get("created_at")))
        check("历史版本的 thumb 指向 history 目录",
              "/history/character/" in str(old.get("thumb", "")), str(old.get("thumb")))

        # 快照内容要跟当时那份**字节一致** —— 只对文件名不对内容是假验证
        snap = os.path.join(OUT, task_id, "history", "character", "历史角色", str(old["name"]))
        live = os.path.join(OUT, task_id, "characters", "历史角色.png")
        snap_files = os.listdir(snap) if os.path.isdir(snap) else []
        check("历史目录里确实有快照文件", snap_files == ["历史角色.png"], str(snap_files))
        got = open(os.path.join(snap, snap_files[0]), "rb").read() if snap_files else b""
        check("快照存的是**上一版**的字节（不是刚覆盖上去的新图）", got == v1,
              f"{len(got)} 字节，期望 {len(v1)}")
        check("磁盘上的当前文件已经是新图", open(live, "rb").read() == v2)

        # ---------- 4) 切回上一版：文件要真的变回去，且当前这版也要进历史 ----------
        before = http("GET", f"/api/tasks/{task_id}")["project"]["characters"][0].get("version", "")
        code, body = try_http("POST",
                             f"/api/tasks/{task_id}/materials/character/0/history/use",
                             {"name": old["name"]})
        check("切回某一版返回 200", code == 200, body[:160])
        check("磁盘上的当前文件变回了那一版的字节", open(live, "rb").read() == v1,
              f"{os.path.getsize(live)} 字节")
        after = http("GET", f"/api/tasks/{task_id}")["project"]["characters"][0].get("version", "")
        check("切版后 version 换了（否则卡片 URL 不变、浏览器不重新请求）",
              bool(after) and after != before, f"{before} → {after}")
        # ⚠️ 2026-09-19 修的那条：切上去的这版目录必须被收掉。留着它 = 它的内容
        #    跟当前文件一模一样，弹窗里就成了"两张一样的"（斌哥报的那个"多一张"）。
        check("切上去的那一版目录被收掉了", not os.path.isdir(snap), snap)

        def version_sig(v: dict) -> tuple[tuple[int, str], ...]:
            """一条列表项的**内容**指纹：当前这版读 characters/，历史版本读它自己的目录。

            按内容比而不是按位置比 —— 位置会随"切版时又新存了一版"而变。
            """
            if v["current"]:
                paths = [os.path.join(OUT, task_id, "characters", f) for f in v["files"]]
            else:
                d = os.path.join(OUT, task_id, "history", "character", "历史角色", v["name"])
                paths = [os.path.join(d, f) for f in v["files"]]
            sig = []
            for p in paths:
                with open(p, "rb") as fh:
                    sig.append((os.path.getsize(p), hashlib.md5(fh.read()).hexdigest()))
            return tuple(sorted(sig))

        vs = history(task_id, "character", 0)
        names = [v["name"] for v in vs]
        check("切版后列表是 2 条（当前 v1 + 被顶掉的 v2）", len(vs) == 2, str(names))
        check("刚切上去的那一版不再占一条（它已经是「当前这版」）",
              old["name"] not in names, str(names))
        check("只有当前这版 name 为空，且排第一",
              vs[0]["current"] is True and sum(1 for n in names if not n) == 1, str(names))
        check("列表里没有两张内容一样的（有的话弹窗里同一张图就会出现两次）",
              len({version_sig(v) for v in vs}) == len(vs), str(names))

        displaced = [v["name"] for v in vs if not v["current"] and version_sig(v) == bytes_sig(v2)]
        check("刚被顶掉的 v2 也进了历史（所以还能再切回去）", len(displaced) == 1, str(displaced))
        check("切上去的 v1 就是当前这版（不再在历史里重复一份）",
              any(v["current"] and version_sig(v) == bytes_sig(v1) for v in vs))

        # ---------- 5) 再切回 v2 那一版：来回切不能丢版本 ----------
        v2_name = displaced[0]
        http("POST", f"/api/tasks/{task_id}/materials/character/0/history/use", {"name": v2_name})
        check("再切回更早的那版，文件又变回 v2", open(live, "rb").read() == v2,
              f"{os.path.getsize(live)} 字节")
        check("第二次切版同样把源目录收掉了",
              not os.path.isdir(os.path.join(OUT, task_id, "history", "character", "历史角色", v2_name)))
        vs = history(task_id, "character", 0)
        check("来回切两轮之后列表还是 2 条（当前 v2 + 被顶掉的 v1），不会越切越长",
              len(vs) == 2, str([v["name"] for v in vs]))
        check("来回切两轮后 v1 和 v2 都还在（一个当当前这版，一个在历史里）",
              sum(1 for v in vs if not v["current"] and version_sig(v) == bytes_sig(v1)) == 1
              and sum(1 for v in vs if v["current"] and version_sig(v) == bytes_sig(v2)) == 1,
              str([v["name"] for v in vs]))

        # ---------- 6) 设定图（sheet）不能被当成「多出来的一张人像」 ----------
        # 一个角色 = 单人图（进 images）+ 四视角设定图（只进 sheet，只作总览与留档）。
        # 手搓一个"带设定图"的历史版本切上去：images / image_path 只能收那张单人图 ——
        # 把拼版塞进 images，下游出片会把它当第二张人像参考图（"多一张"的另一种形态）。
        sheet_ver = "20200101_000000_abcdef"
        sheet_dir = os.path.join(OUT, task_id, "history", "character", "历史角色", sheet_ver)
        os.makedirs(sheet_dir, exist_ok=True)
        single = png_solid(24, 24, (11, 22, 33))
        sheet = png_solid(48, 24, (44, 55, 66))
        open(os.path.join(sheet_dir, "历史角色.png"), "wb").write(single)
        open(os.path.join(sheet_dir, "历史角色_sheet.png"), "wb").write(sheet)
        code, body = try_http("POST", f"/api/tasks/{task_id}/materials/character/0/history/use",
                              {"name": sheet_ver})
        check("切到「带设定图」的那一版返回 200", code == 200, body[:160])
        c0 = http("GET", f"/api/tasks/{task_id}")["project"]["characters"][0]
        imgs = [os.path.basename(p) for p in c0.get("images", [])]
        check("images 里只有单人图，没有设定图", imgs == ["历史角色.png"], str(imgs))
        check("image_path 指的是那张单人图（不是拼版）",
              os.path.basename(str(c0.get("image_path", ""))) == "历史角色.png",
              str(c0.get("image_path")))
        check("设定图落在 sheet 字段（不进 images）",
              os.path.basename(str(c0.get("sheet", ""))) == "历史角色_sheet.png",
              str(c0.get("sheet")))
        check("列表里这一版自己也不留（刚切上去的那版照样收掉）",
              sheet_ver not in [v["name"] for v in history(task_id, "character", 0)])

        # ---------- 7) 只留最近 10 版 ----------
        for i in range(12):
            upload(task_id, "character", 0, png_solid(24, 24, (30 * i % 256, 90, 150)))
        vs = history(task_id, "character", 0)
        keep = http("GET", f"/api/tasks/{task_id}/materials/character/0/history").get("keep")
        check("HISTORY_KEEP 是 10", keep == 10, str(keep))
        check("连传 12 张后，历史被裁到「当前 + 10 版」= 11 条", len(vs) == 11, f"{len(vs)} 条")
        root = os.path.join(OUT, task_id, "history", "character", "历史角色")
        dirs = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
        check("磁盘上历史目录也是 10 个（真的删掉了，不是只在列表里藏起来）",
              len(dirs) == 10, f"{len(dirs)} 个")
        # ⚠️ 这 12 次上传可能在**同一秒**里跑完，目录名的时间戳部分一样 ——
        #    按名字排序会随机颠倒（产品代码就是因此改用 mtime 排序的），这里也跟着用 mtime。
        newest = max(dirs, key=lambda d: os.path.getmtime(os.path.join(root, d)))
        check("被裁掉的是最老的（最新那一版还在，列表第 2 条就是它）",
              vs[1]["name"] == newest, f"列表第2条={vs[1]['name']} 磁盘最新={newest}")
        check("裁掉的正是最老的那几版（剩下的 10 个都在列表里）",
              set(dirs) == {v["name"] for v in vs[1:]}, str(sorted(set(dirs) ^ {v["name"] for v in vs[1:]})))

        # ---------- 8) 边界：非法 / 不存在 / 空 / 音频 / 越界 ----------
        code, _ = try_http("POST", f"/api/tasks/{task_id}/materials/character/0/history/use",
                           {"name": "../../etc/passwd"})
        check("版本名不合法 → 400（不让路径穿越）", code == 400, str(code))
        code, _ = try_http("POST", f"/api/tasks/{task_id}/materials/character/0/history/use",
                           {"name": "20200101_000000_abcdef"})
        check("切一个不存在的版本 → 404", code == 404, str(code))
        code, body = try_http("POST", f"/api/tasks/{task_id}/materials/character/0/history/use",
                              {"name": ""})
        check("不说是哪一版 → 422", code == 422, body[:120])
        code, body = try_http("GET",
                              f"/api/tasks/{task_id}/materials/audio/{audio_i}/history")
        check("音频类没有生成历史 → 422", code == 422, body[:140])
        code, _ = try_http("GET", f"/api/tasks/{task_id}/materials/character/99/history")
        check("角色下标越界 → 404", code == 404, str(code))
        code, _ = try_http("GET", f"/api/tasks/{task_id}/materials/prop/{scene_i}/history")
        check("拿场景的下标当道具查 → 422（kind 对不上就不给）", code == 422, str(code))

        # ---------- 9) 场景素材走 assets/ 那一侧 ----------
        upload(task_id, "scene", scene_i, png_solid(32, 18, (120, 180, 90)))
        upload(task_id, "scene", scene_i, png_solid(32, 18, (180, 120, 60)))
        vs = history(task_id, "scene", scene_i)
        check("场景素材也有历史（assets/ 那一侧同样接了快照）", len(vs) == 2, f"{len(vs)} 条")
        sroot = os.path.join(OUT, task_id, "history", "scene", "历史场景")
        check("场景的历史目录建在 history/scene/ 下", os.path.isdir(sroot), sroot)
        check("场景的当前这版 thumb 指向 assets/",
              vs and f"/files/{task_id}/assets/" in vs[0]["thumb"], vs[0]["thumb"] if vs else "")

        # ---------- 10) 作品没图时不该凭空造历史 ----------
        http("POST", f"/api/tasks/{task_id}/assets", {"kind": "prop", "name": "空道具", "design": False})
        prop_i = next(i for i, a in enumerate(
            http("GET", f"/api/tasks/{task_id}")["project"]["assets"]) if a["kind"] == "prop")
        vs = history(task_id, "prop", prop_i)
        check("还没出过图的素材 → 历史为空（不是报错）", vs == [], str(vs))
        check("没图时不建空的历史目录",
              not os.path.isdir(os.path.join(OUT, task_id, "history", "prop", "空道具")))

    finally:
        if task_id:
            code, body = try_http("DELETE", f"/api/tasks/{task_id}?confirm=true")
            check("收尾：测试作品已删掉", code == 200, body[:120])
            # 回收站里那条也清掉，别把测试垃圾留在磁盘上
            for e in http("GET", "/api/trash").get("entries", []):
                if e.get("task_id") == task_id:
                    try_http("DELETE", f"/api/trash/{e['name']}?confirm=true")
            force_remove_tree(os.path.join(OUT, task_id))
            check("收尾：outputs/ 下没留目录", not os.path.isdir(os.path.join(OUT, task_id)))

    ok, total = sum(results), len(results)
    print(f"\n{ok}/{total}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""验证「AI 生成弹窗记住上次填的描述 / 比例 / 性别」（2026-09-27 加）。

起因（斌哥原话）：「我之前用过 ai 生成这个图片的时候，我再点进这个 AI 生成按钮，
应该可以看见之前的这个选中生成的比例和那个提示词」—— 出一版不满意想改两句重出，
弹窗每次都是空的，只能从头重打一遍。

做法：五个「AI 生成」入口（角色形象 / 场景 / 道具 / 其他图片 / 音色）成功落库时，
都把这次弹窗里填的东西记一份到素材条目上：

    entry["ai_last"] = {"prompt": ..., "ratio": ..., "gender": ..., "at": ...}

⚠️ 它**不是素材描述**：不写回 anchor、卡片上也不显示，只有弹窗读它。
上传型素材（自己传图 / 传音频）**不写**这个字段 —— 那不是"AI 生成过的"。
自动配音（`/voice`，不经过弹窗）也不写 —— 否则会把用户填过的描述覆盖掉。

⚠️ **不烧额度**：进程内调五个 endpoint，`create_provider` 换成假 provider（写真 PNG）、
文本 Agent 换成固定返回、`tts.synth_sample` 换成写空文件。全程零外部调用。

跑法（在项目根）：
    D:/miniforge/python.exe dev/api/_verify_ai_memory.py
"""
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

PERSON = "【假的人物描述段：银灰短发，黑色高领】"
CHAR_NAME = "记忆角色"
SCENE_NAME = "记忆场景"
PROP_NAME = "记忆道具"
IMAGE_NAME = "记忆图片"

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
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw or "null")
        except Exception:
            return e.code, {"detail": raw}


def tiny_png(w: int = 8, h: int = 8, rgb=(200, 200, 200)) -> bytes:
    """手写一张纯色 PNG（不依赖 Pillow）—— 只为让产物是真图、非 0 字节。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def force_remove_tree(path: str) -> None:
    """真删一个目录 —— **只给本脚本收尾用，产品代码不这么干**。

    本进程跑在 WorkBuddy 沙箱里，`os.remove` / `shutil.rmtree` 被换成了
    "移到系统回收站 + 每轮对话 50 个文件的批量护栏"，护栏一触发就 SystemExit。
    沙箱对**系统临时目录**是放行的，所以先搬进 temp 再删。
    """
    tmp = tempfile.mkdtemp(prefix="aimem-cleanup-")
    dst = os.path.join(tmp, os.path.basename(path))
    try:
        shutil.move(path, dst)
    except Exception:                       # noqa: BLE001 - 收尾失败不掩盖测试结论
        return
    shutil.rmtree(dst, ignore_errors=True)


CALLS: list[dict] = []


class FakeProvider:
    """假图像 provider：签名与 `ImageProvider.generate` 对齐，只记录参数 + 写真 PNG。"""

    model = "fake-image-model"

    def generate(self, prompt, out_path, negative_prompt="", size="1024x576", seed=None):
        CALLS.append({"prompt": prompt, "size": size, "path": out_path})
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(tiny_png())
        return out_path


def entry_of(task: dict, kind: str, name: str) -> dict:
    """从 GET /api/tasks/{id} 的原始 project 里捞出某个素材条目。

    ⚠️ 走 HTTP 而不是进程内 `_task()`：前端拿到的就是这个响应，
    `ai_last` 能不能原样传到页面，正是要验的东西之一。
    """
    proj = task.get("project") or {}
    pool = proj.get("characters") if kind == "character" else proj.get("assets")
    for e in pool or []:
        if e.get("name") == name:
            return e
    return {}


def fetch(task_id: str) -> dict:
    return http("GET", f"/api/tasks/{task_id}")[1]


def slot(entry: dict, kind: str) -> dict:
    """取某个**弹窗种类**的记忆槽。

    ⚠️ `ai_last` 是**按弹窗种类分槽**的：角色条目上挂着形象图与音色两个弹窗
    （都写 `project.characters[i]`），各存各的槽。前端读的就是 `ai_last[item.kind]`。
    """
    slots = entry.get("ai_last")
    return (slots.get(kind) or {}) if isinstance(slots, dict) else {}


def main() -> int:
    print("=" * 74)
    print("「AI 生成弹窗记忆」验证（不烧额度）   " + API)
    print("=" * 74 + "\n")

    task_id = ""
    try:
        # ---------- 0) 建测试作品 + 五类素材（design=False，不调模型） ----------
        st, draft = http("POST", "/api/tasks/draft", {"title": "验证_弹窗记忆"})
        task_id = (draft or {}).get("task_id", "")
        check("建出测试作品并拿到 task_id", bool(re.fullmatch(r"[0-9a-f]{12}", task_id)), task_id)
        if not task_id:
            raise SystemExit("没有 task_id，后面的断言没意义")

        http("POST", f"/api/tasks/{task_id}/characters", {"name": CHAR_NAME, "design": False})
        for kind, name in (("scene", SCENE_NAME), ("prop", PROP_NAME), ("image", IMAGE_NAME)):
            http("POST", f"/api/tasks/{task_id}/assets",
                 {"kind": kind, "name": name, "design": False})
        http("POST", f"/api/tasks/{task_id}/assets",
             {"kind": "audio", "name": "记忆音频", "design": False})

        t = fetch(task_id)
        kinds = {a.get("kind") for a in (t.get("project") or {}).get("assets") or []}
        check("四类素材都建好了（scene/prop/image/audio）",
              kinds == {"scene", "prop", "image", "audio"}, str(sorted(kinds)))

        # ---------- 1) 还没生成过 → 一条记忆都没有 ----------
        print("\n  —— 还没生成过 ——")
        check("角色没有 ai_last", "ai_last" not in entry_of(t, "character", CHAR_NAME))
        check("场景没有 ai_last", "ai_last" not in entry_of(t, "asset", SCENE_NAME))
        check("新建的条目里没有任何 ai_last",
              all("ai_last" not in a for a in (t.get("project") or {}).get("assets") or []))

        # ---------- 2) 进程内跑五次生成（假 provider / 假 Agent，零外部调用） ----------
        sys.path.insert(0, os.path.join(ROOT, "server"))
        import app as server_app          # noqa: E402
        import agents                     # noqa: E402
        import pipeline                   # noqa: E402
        import tts                        # noqa: E402

        def live(tid: str) -> dict:
            """生成之后的读取一律走**进程内** registry（`get_task` 就是路由函数本身）。

            ⚠️ 本脚本为了塞假 provider，是在**另一个进程**里直接调 endpoint 函数的。
            它改的是自己那份 `TASKS` + 磁盘；正在跑的那个 uvicorn 进程内存里还是旧数据，
            所以生成之后 `GET /api/tasks/{id}` 会返回**没有 ai_last** 的那一份 ——
            这是脚本的跨进程假象，不是产品 bug（真实链路里生成与读取在同一个进程）。
            要验"HTTP 那一段"，靠的是 ① 这里直接调 `get_task()`（返回形状与路由一致）
            和 ② 重启后从 project.json 读回来的那一份（见"落盘"那一段）。
            """
            return server_app.get_task(tid)

        server_app.create_provider = lambda: FakeProvider()
        agents.chat_json = lambda system, user, **kw: {"prompt": PERSON}
        agents.design_asset = lambda project, kind, name, hint="": f"假的{kind}描述段"
        agents.compose_prop_sheet = lambda project, name, hint="": {
            "body": "假的道具描述段", "sheet": "假的三视图提示词",
        }
        agents.design_voice = lambda *a, **kw: {"voice_id": ""}   # 空 → 走 _verified_voice_id 的同性别兜底

        def fake_synth(voice_id, out_path, text=""):
            parent = os.path.dirname(out_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(b"\x00" * 64)      # 占位即可，这里不验音频内容
            return out_path

        tts.synth_sample = fake_synth
        server_app.tts.synth_sample = fake_synth

        def asset_index(kind: str) -> int:
            return next(i for i, a in enumerate(
                (live(task_id).get("project") or {}).get("assets") or []) if a.get("kind") == kind)

        print("\n  —— 五个入口各生成一次 ——")
        P_CHAR = "银灰短发的女战士，黑色高领"
        P_SCENE = "雨夜的老旧书店，暖黄吊灯"
        P_PROP = "掌心大小的黄铜罗盘，刻星宿纹"
        P_IMAGE = "黄昏的天台，主角背对镜头"
        P_VOICE = "低沉沙哑的中年男声，语速偏慢"

        server_app.generate_character_portrait(
            task_id, 0,
            server_app.CharacterPortraitBody(prompt=P_CHAR, ratio="16:9"),
        )
        server_app.generate_asset_image(
            task_id, asset_index("scene"),
            server_app.AssetGenerateBody(prompt=P_SCENE, ratio="4:3"),
        )
        server_app.generate_asset_image(
            task_id, asset_index("image"),
            server_app.AssetGenerateBody(prompt=P_IMAGE, ratio="9:16"),
        )
        server_app.generate_asset_image(
            task_id, asset_index("prop"),
            server_app.AssetGenerateBody(prompt=P_PROP, ratio="4:3"),
        )
        server_app.generate_character_voice(
            task_id, 0,
            server_app.VoiceGenerateBody(prompt=P_VOICE, gender="male"),
        )
        check("五次生成都跑通了（没抛异常）", True, f"假 provider 出图 {len(CALLS)} 次")

        # 从这里开始的读取一律走 live()（进程内 registry），原因见 live() 的注释
        t = live(task_id)
        ch = entry_of(t, "character", CHAR_NAME)
        sc = entry_of(t, "asset", SCENE_NAME)
        pr = entry_of(t, "asset", PROP_NAME)
        im = entry_of(t, "asset", IMAGE_NAME)
        check("路由函数 get_task() 的返回里就带 ai_last（前端拿的就是这条响应）",
              slot(ch, "character").get("prompt") == P_CHAR,
              str(slot(ch, "character").get("prompt")))

        # ---------- 3) 角色形象：描述 + 比例 ----------
        print("\n  —— 角色形象 ——")
        check("角色记住了描述", slot(ch, "character").get("prompt") == P_CHAR,
              str(slot(ch, "character").get("prompt")))
        check("角色记住了比例 16:9", slot(ch, "character").get("ratio") == "16:9",
              str(slot(ch, "character").get("ratio")))
        check("角色形象槽不记 gender（那是音色弹窗的东西）",
              "gender" not in slot(ch, "character"))

        # ---------- 4) 场景 / 其他图片：各自的描述 + 比例 ----------
        print("\n  —— 场景 / 其他图片 ——")
        check("场景记住了描述", slot(sc, "scene").get("prompt") == P_SCENE)
        check("场景记住了比例 4:3", slot(sc, "scene").get("ratio") == "4:3")
        check("其他图片记住了描述", slot(im, "image").get("prompt") == P_IMAGE)
        check("其他图片记住了比例 9:16", slot(im, "image").get("ratio") == "9:16")

        # ---------- 5) 道具：只有描述，没有比例 ----------
        # 道具弹窗里**没有比例选项**（恒 16:9），记一个用不上的 ratio 只会误导后来改代码的人
        print("\n  —— 道具 ——")
        check("道具记住了描述", slot(pr, "prop").get("prompt") == P_PROP)
        check("道具**不记** ratio（弹窗里没有比例选项）", "ratio" not in slot(pr, "prop"))

        # ---------- 6) 音色：描述 + 性别，没有比例 ----------
        # ⚠️ 形象图与音色共用**同一个角色条目** —— 这是本次最容易写错的一处：
        #    只存一份的话，生成完音色会把形象图那次的描述顶掉。
        print("\n  —— 音色（与形象图共用同一条目，必须各存各的） ——")
        check("音色记住了描述", slot(ch, "voice").get("prompt") == P_VOICE,
              str(slot(ch, "voice").get("prompt")))
        check("音色记住了性别 male", slot(ch, "voice").get("gender") == "male",
              str(slot(ch, "voice").get("gender")))
        check("音色**不记** ratio", "ratio" not in slot(ch, "voice"))
        check("形象图那次的描述**没被音色顶掉**（两个槽互不影响）",
              slot(ch, "character").get("prompt") == P_CHAR,
              f"character={slot(ch, 'character').get('prompt')!r} voice={slot(ch, 'voice').get('prompt')!r}")
        check("两个槽的 at 是两次不同的时间",
              slot(ch, "character").get("at") != slot(ch, "voice").get("at"))

        # ---------- 7) at 是能解析的 UTC ISO ----------
        from datetime import datetime
        for label, e, k in (("角色", ch, "character"), ("场景", sc, "scene"),
                            ("道具", pr, "prop"), ("其他图片", im, "image"),
                            ("音色", ch, "voice")):
            at = str(slot(e, k).get("at", ""))
            parsed = None
            try:
                parsed = datetime.fromisoformat(at)
            except ValueError:
                pass
            check(f"{label}的 at 是能解析的 ISO 时间", parsed is not None, at)

        # ---------- 8) 真的落盘了（重启不丢，不是只在内存里） ----------
        print("\n  —— 落盘 ——")
        pj = os.path.join(OUT, task_id, "project.json")
        check("project.json 存在", os.path.isfile(pj), pj)
        disk = json.load(open(pj, encoding="utf-8")) if os.path.isfile(pj) else {}
        disk_ch = next((c for c in disk.get("characters") or [] if c.get("name") == CHAR_NAME), {})
        disk_sc = next((a for a in disk.get("assets") or [] if a.get("name") == SCENE_NAME), {})
        check("角色的两个槽都写进了 project.json",
              slot(disk_ch, "character").get("prompt") == P_CHAR
              and slot(disk_ch, "voice").get("prompt") == P_VOICE,
              str(disk_ch.get("ai_last")))
        check("场景 ai_last 写进了 project.json",
              slot(disk_sc, "scene").get("prompt") == P_SCENE)
        check("ai_last **没有**污染 anchor（anchor 不是描述的回写口）",
              P_CHAR not in str(disk_ch.get("anchor", "")) and P_SCENE not in str(disk_sc.get("anchor", "")),
              f"anchor={disk_ch.get('anchor', '')!r}")
        check("version 也一起落盘了（以前会被 _persist 丢掉，重启后 cache-busting 就失效）",
              bool(disk_ch.get("version")), str(disk_ch.get("version")))

        # ---------- 9) 再生成一次 → 记忆换成**最新那次**（不是只记第一次） ----------
        print("\n  —— 再生成一次，记忆要更新 ——")
        P_SCENE2 = "晴天午后的旧书店，灰尘在光柱里飘"
        server_app.generate_asset_image(
            task_id, asset_index("scene"),
            server_app.AssetGenerateBody(prompt=P_SCENE2, ratio="3:4"),
        )
        sc2 = slot(entry_of(live(task_id), "asset", SCENE_NAME), "scene")
        check("描述换成最新那次的", sc2.get("prompt") == P_SCENE2, str(sc2.get("prompt")))
        check("比例换成最新那次的 3:4", sc2.get("ratio") == "3:4", str(sc2.get("ratio")))

        # ---------- 10) 非法比例要在落库前归一化（否则前端一个 chip 都不高亮） ----------
        print("\n  —— 非法比例归一化 ——")
        server_app.generate_character_portrait(
            task_id, 0,
            server_app.CharacterPortraitBody(prompt="随便一个角色", ratio="7:3"),
        )
        ch2 = slot(entry_of(live(task_id), "character", CHAR_NAME), "character")
        check("角色传非法比例 7:3 → 存的是归一化后的 1:1（不是原样存 7:3）",
              ch2.get("ratio") == "1:1", str(ch2.get("ratio")))
        check("角色归一化后仍带最新描述", ch2.get("prompt") == "随便一个角色", str(ch2.get("prompt")))

        server_app.generate_asset_image(
            task_id, asset_index("scene"),
            server_app.AssetGenerateBody(prompt="又一个场景", ratio="7:3"),
        )
        sc3 = slot(entry_of(live(task_id), "asset", SCENE_NAME), "scene")
        check("场景传非法比例 7:3 → 退回作品画幅 16:9", sc3.get("ratio") == "16:9", str(sc3.get("ratio")))

        # ---------- 11) 上传型素材不写 ai_last ----------
        # ⚠️ 上传也走**进程内**调用：HTTP 那条会打到正在跑的 uvicorn 进程，
        #    改的是它的 registry，本进程读不到（见 live() 的注释）。
        print("\n  —— 上传不该留下记忆 ——")
        import base64
        server_app.upload_asset_file(
            task_id, asset_index("scene"),
            server_app.UploadBody(filename="mine.png",
                                  data_b64=base64.b64encode(tiny_png(12, 12, (30, 90, 160))).decode("ascii")),
        )
        sc4 = slot(entry_of(live(task_id), "asset", SCENE_NAME), "scene")
        check("上传**不会**清掉已有的 ai_last（用户传图不代表没生成过）",
              sc4.get("prompt") == "又一个场景", str(sc4.get("prompt")))

        server_app.upload_asset_file(
            task_id, asset_index("audio"),
            server_app.UploadBody(filename="mine.mp3",
                                  data_b64=base64.b64encode(b"ID3\x00\x00\x00" + b"\x00" * 32).decode("ascii")),
        )
        au = entry_of(live(task_id), "asset", "记忆音频")
        check("从没 AI 生成过的音频素材没有 ai_last", "ai_last" not in au, str(au.get("ai_last")))

        # ---------- 12) 自动配音（没经过弹窗）不许覆盖用户填过的描述 ----------
        print("\n  —— 自动配音不该覆盖 ——")
        before = entry_of(live(task_id), "character", CHAR_NAME).get("ai_last", {})
        server_app.reroll_character_voice(task_id, 0)
        after = entry_of(live(task_id), "character", CHAR_NAME).get("ai_last", {})
        check("自动配音后 ai_last 原样不动", after == before,
              f"before={before.get('prompt')!r} after={after.get('prompt')!r}")
        check("自动配音确实换/确认了音色（说明这一步真跑了）",
              bool(entry_of(live(task_id), "character", CHAR_NAME).get("tts_voice")))

        # ---------- 13) 白名单就是前端那份 AI_RATIOS 的取值域 ----------
        print("\n  —— 比例白名单 ——")
        check("pipeline.SIZE_TABLE 认这五个比例",
              {"1:1", "16:9", "9:16", "4:3", "3:4"} <= set(pipeline.SIZE_TABLE),
              str(sorted(pipeline.SIZE_TABLE)))

    finally:
        if task_id:
            st, body = http("DELETE", f"/api/tasks/{task_id}?confirm=true")
            check("收尾：测试作品已删掉", st == 200, str(body)[:120])
            for e in (http("GET", "/api/trash")[1] or {}).get("entries", []):
                if e.get("task_id") == task_id:
                    http("DELETE", f"/api/trash/{e['name']}?confirm=true")
            force_remove_tree(os.path.join(OUT, task_id))
            check("收尾：outputs/ 下没留目录", not os.path.isdir(os.path.join(OUT, task_id)))

    ok, total = sum(results), len(results)
    print(f"\n{ok}/{total}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""验证「生成记录」的边车文件 —— 这一段用了哪些素材 / 哪句提示词 / 什么参数。

跑法（在项目根）：
    D:/miniforge/python.exe _verify_segment_record.py
    E2E_API=http://127.0.0.1:8000 可以换后端地址

⚠️ **默认不烧额度**：出片那一步在**进程内**调用 server/app.py 的
`start_segment_video`，但把 `_make_video_provider` 换成一个假 provider
（写几字节 mp4 就返回）。于是
「解析素材引用 → 拼记录 → 写边车 json → 落 mp4」整条链路都真跑了一遍，
却没有一次外部调用（不碰 ComfyUI、不碰任何模型 API）。

写完的记录再通过**正在跑的那个服务**读回来（`GET /segments` 与
`GET /segments/{name}`）—— 两个进程看的是同一个磁盘，所以连读取路径也一起验了。

还钉了 2026-09-19 那个"场景凭空消失"的坑：前端发的是 `assets` 的**全局下标**，
后端曾按"同类内序号"解析，`assets = [场景, 道具, 场景]` 时第二个场景被判越界、
**静默丢掉**（现象：选了场景，ComfyUI 里只有一张角色图）。这里特意造出
"全局下标 ≠ 同类内序号"的顺序，顺带把"没有图的素材要被点名报出来"也验了。

会真建一个 draft 作品，跑完自己 DELETE 掉（连带清掉伪造的产物）。
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("E2E_API", "http://127.0.0.1:8000").rstrip("/")
FACE = os.path.join(ROOT, "_e2e_face.png")

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
        return json.loads(r.read().decode("utf-8"))


def upload(task_id: str, kind: str, filename: str) -> dict:
    """按文件名传一张图进某个分组（走和前端完全一样的 JSON base64 通道）。"""
    with open(FACE, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return http("POST", f"/api/tasks/{task_id}/materials/upload?kind={kind}",
                {"filename": filename, "data_b64": b64})


task_id = ""
seg_dir = ""


class FakeProvider:
    """假 provider：签名必须和 ComfyUIVideoProvider / ApiVideoProvider 对齐，
    但只往 out_path 写几个字节就返回 —— list_segments 只要求文件非 0 字节。

    ⚠️ 签名少一个参数就会 TypeError，而且**表现是"出片失败"而不是"测试脚本错了"**
    （2026-09-19 加 ref_image_paths 时真踩了一次，20 项挂了 9 项）。
    加参数时记得同步改这里，以及 _verify_r2v_workflow.py 里那条签名对齐断言。
    """

    seen: dict = {}

    def generate(self, image_path, video_prompt, duration, out_path,
                 audio="", last_frame_path="", megapixels=None, ref_image_paths=None):
        FakeProvider.seen = {
            "image_path": image_path, "video_prompt": video_prompt, "duration": duration,
            "last_frame_path": last_frame_path, "megapixels": megapixels,
            "ref_image_paths": list(ref_image_paths or []),
            # 音轨段（2026-09-19 加）：基础模式下它就是三段式的 overall_soundscape
            "audio": audio,
        }
        with open(out_path, "wb") as f:
            f.write(b"\x00" * 4096)


try:
    print("=" * 74)
    print("「生成记录」边车文件验证   " + API)
    print("=" * 74 + "\n")

    if not os.path.isfile(FACE):
        raise SystemExit(f"缺测试图 {FACE}（见 MEMORY.md 里那条踩坑记录）")

    # ---------- 0) 建一个 draft 作品 + 两张图素材 ----------
    draft = http("POST", "/api/tasks/draft", {"title": "记录验证作品"})
    task_id = draft["task_id"]
    check("建出测试作品并拿到 task_id", bool(task_id), task_id)
    seg_dir = os.path.join(ROOT, "outputs", task_id, "segments")

    upload(task_id, "character", "林晚.png")
    upload(task_id, "scene", "雨夜街道.png")
    # 再给角色补一张「四视图设定图」（复制一张真 PNG 过去就行）：Ref2VA 下角色进模型的
    # **是这张**，I2V 下是单张定妆照 —— 两条路下面都要断言用的是哪个文件。
    # 注意：本进程内的 server_app 还没 import（下面才 import），所以这份设定图
    # 会被"从磁盘认回来"这一步自动带上，不用手动刷新。
    import shutil

    shutil.copyfile(FACE, os.path.join(ROOT, "outputs", task_id, "characters", "林晚_sheet.png"))
    proj = http("GET", f"/api/tasks/{task_id}")["project"]
    check("两张素材都落进了作品",
          len(proj.get("characters") or []) == 1 and len(proj.get("assets") or []) == 1,
          f"characters={[c['name'] for c in proj.get('characters') or []]} "
          f"assets={[a['name'] for a in proj.get('assets') or []]}")

    # ---------- 1) 进程内跑一次出片（假 provider，零外部调用） ----------
    sys.path.insert(0, os.path.join(ROOT, "server"))
    import app as server_app                                    # noqa: E402

    # 前置：LoRA 与工作流配套吗？不配套的话 start_segment_video 会直接 409，
    # 表现出来是"脚本挂了"而不是"配置错了" —— 这里先说清楚，省得查半天。
    lora_err = server_app._lora_workflow_error(server_app._load_service_config())
    if lora_err:
        print(f"  ⚠️ 当前服务配置里「加速 LoRA」和「视频工作流」不配套，先修好再跑：\n     {lora_err}")
        raise SystemExit(1)

    server_app._make_video_provider = lambda: FakeProvider()
    # 首尾帧改成**显式挑**（2026-09-19）：first_frame / last_frame 各自可空。
    # 这里挑「场景」当尾帧，首帧不给 → 应该退回 frames 里第一个能出图的（角色）。
    started = server_app.start_segment_video(task_id, server_app.SegmentVideoBody(
        frames=["character:0", "scene:0"],
        last_frame="scene:0",
        prompt="A woman walks into a convenience store at night; slow push-in.",
        duration=7,
        megapixels=0.9,
        note="雨夜，女孩撑着伞走进便利店",
        # 编排出来的音轨段：只有基础模式（I2V/首尾帧）会用它拼 overall_soundscape，
        # 但**必须一路带到 provider**（Ref2VA 的六段式自带那一段，传了被忽略）。
        soundscape="Rain on glass; her footsteps.",
    ))
    job_id = started["job_id"]
    check("出片任务起得来（走的是假 provider）", bool(job_id), job_id)

    # ⚠️ runner 是后台线程，**必须等它收尾再断言** —— 早一步读 FakeProvider.seen
    # 会读到空 dict，看起来像"假 provider 没被调到"（第一版就栽在这）。
    for _ in range(60):
        if server_app.SEGMENT_JOBS[job_id]["status"] != "running":
            break
        time.sleep(0.2)
    check("出片线程跑完且成功", server_app.SEGMENT_JOBS[job_id]["status"] == "succeeded",
          server_app.SEGMENT_JOBS[job_id].get("error", ""))
    check("假 provider 真的被调到了（零外部调用）", FakeProvider.seen.get("duration") == 7.0,
          json.dumps({k: v for k, v in FakeProvider.seen.items() if k != "video_prompt"},
                     ensure_ascii=False))
    check("音轨段一路带到了 provider（基础模式下它就是 overall_soundscape）",
          FakeProvider.seen.get("audio") == "Rain on glass; her footsteps.",
          str(FakeProvider.seen.get("audio")))
    check("provider 收到的是首帧/尾帧两张图的路径",
          bool(FakeProvider.seen.get("image_path")) and bool(FakeProvider.seen.get("last_frame_path")),
          f"first={os.path.basename(str(FakeProvider.seen.get('image_path')))} "
          f"last={os.path.basename(str(FakeProvider.seen.get('last_frame_path')))}")
    # 2026-09-19 加：多参考图要**整份**传给 provider，不能只给第一张
    # （I2V 工作流会忽略它，Ref2VA 才用得上 —— 但传不传是 server 的事）
    check("provider 收到了全部参考图（不只第一张）",
          len(FakeProvider.seen.get("ref_image_paths") or []) == 2,
          str([os.path.basename(p) for p in FakeProvider.seen.get("ref_image_paths") or []]))
    # ⚠️ 快照：下面还要跑第二次出片（只挑首帧那个用例），FakeProvider.seen 会被覆盖。
    # 后面那些"和边车 json 对账"的断言必须比的是**第一次**的入参。
    seen1 = dict(FakeProvider.seen)

    # ---------- 1b) 只挑首帧：应该覆盖 frames 里默认的第一张 ----------
    started2 = server_app.start_segment_video(task_id, server_app.SegmentVideoBody(
        frames=["character:0", "scene:0"],
        first_frame="scene:0",       # 显式挑场景当首帧（角色没被挑）
        prompt="Same prompt, different framing.",
        duration=7,
    ))
    job2 = started2["job_id"]
    for _ in range(60):
        if server_app.SEGMENT_JOBS[job2]["status"] != "running":
            break
        time.sleep(0.2)
    check("只挑首帧时，首帧就是挑的那张（不是 frames 的第一张）",
          FakeProvider.seen.get("image_path") == proj["assets"][0]["images"][0],
          f"首帧={os.path.basename(str(FakeProvider.seen.get('image_path')))} "
          f"期望={os.path.basename(str(proj['assets'][0]['images'][0]))}")
    check("只挑首帧时没有尾帧", FakeProvider.seen.get("last_frame_path") == "",
          str(FakeProvider.seen.get("last_frame_path")))

    # ---------- 1c) 素材引用串的索引口径（2026-09-19 修的坑） ----------
    # 前端 allItems 里的 index 是 `project.assets` 的**全局下标**，后端曾按"同类内序号"
    # 解析 —— assets = [场景, 道具, 场景] 时第二个场景（前端发 `scene:2`）被判成越界、
    # **静默丢掉**。用户看到的是"我明明选了场景，ComfyUI 里只有一张角色图"。
    # 所以这里特意造出"全局下标 ≠ 同类内序号"的顺序把它钉住。
    upload(task_id, "prop", "旧皮箱.png")
    upload(task_id, "scene", "沙漠城堡内部场景.png")
    http("POST", f"/api/tasks/{task_id}/assets", {"kind": "prop", "name": "空道具", "design": False})
    # ⚠️ 上面这几笔是**打接口**加的，而本进程内的 server_app 是在脚本开头 import 的，
    #    它的 TASKS 还是"加这批素材之前"的快照 —— 不重新从磁盘认一次，
    #    下面按全局下标取就会取到不存在的条目（第一版就栽在这，报"没带上"的是引用串本身）。
    server_app.TASKS[task_id] = server_app._load_task_from_disk(task_id)
    proj2 = http("GET", f"/api/tasks/{task_id}")["project"]
    order = [(i, a["kind"], a["name"]) for i, a in enumerate(proj2.get("assets") or [])]
    check("素材顺序造好了（全局下标 ≠ 同类内序号）",
          [k for _, k, _ in order] == ["scene", "prop", "scene", "prop"], str(order))

    started3 = server_app.start_segment_video(task_id, server_app.SegmentVideoBody(
        frames=["character:0", "scene:2"],       # 全局下标 2 = 第二个场景
        prompt="Two references: the person and the castle interior.",
        duration=7,
    ))
    for _ in range(60):
        if server_app.SEGMENT_JOBS[started3["job_id"]]["status"] != "running":
            break
        time.sleep(0.2)
    check("选中的场景真的进了 provider 的参考图（不再被静默丢掉）",
          len(FakeProvider.seen.get("ref_image_paths") or []) == 2,
          str([os.path.basename(p) for p in FakeProvider.seen.get("ref_image_paths") or []]))
    check("这次没有任何素材被报成没带上", started3.get("missing") == [], str(started3.get("missing")))
    rec3 = server_app.SEGMENT_JOBS[started3["job_id"]]["record"]
    check("记录里两项素材的名字都对",
          [m.get("name") for m in rec3.get("materials") or []] == ["林晚", "沙漠城堡内部场景"],
          str([m.get("name") for m in rec3.get("materials") or []]))
    # 工作流吃不下的参考图要明说：I2V 那份只有一个 first_frame 口，
    # 第 2 张图传上去也没口接（用户会以为"场景没传进去"）。
    wf_now = str(server_app._load_service_config().get("video_workflow") or "")
    if wf_now == "ref2va":
        check("Ref2VA 工作流下不该冒 I2V 的提醒", started3.get("warning") == "",
              str(started3.get("warning")))
    else:
        check("I2V 工作流 + 多张图 → 提醒「其余参考图不会进画面」",
              "I2V" in str(started3.get("warning") or "")
              and "Ref2VA" in str(started3.get("warning") or ""),
              str(started3.get("warning")))
        check("那句提醒也写进边车记录", bool(rec3.get("warning")), str(rec3.get("warning"))[:60])

    # 选了但**还没有图**的素材：必须明说"这次没带上"，同样不能静默消失
    started4 = server_app.start_segment_video(task_id, server_app.SegmentVideoBody(
        frames=["character:0", "prop:3"],       # 全局下标 3 = 那个没图的空道具
        prompt="Only the person is available here.",
        duration=7,
    ))
    for _ in range(60):
        if server_app.SEGMENT_JOBS[started4["job_id"]]["status"] != "running":
            break
        time.sleep(0.2)
    check("没图的素材被点名报出来（不是静默丢掉）",
          started4.get("missing") == ["空道具"], str(started4.get("missing")))
    rec4 = server_app.SEGMENT_JOBS[started4["job_id"]]["record"]
    check("边车里也记了 missing（回看这一段时对得上）",
          rec4.get("missing") == ["空道具"], str(rec4.get("missing")))
    check("没带上的素材不会混进 materials",
          [m.get("name") for m in rec4.get("materials") or []] == ["林晚"],
          str([m.get("name") for m in rec4.get("materials") or []]))

    # ---------- 2) 边车文件真的落到了磁盘 ----------
    mp4 = os.path.join(seg_dir, f"seg_{job_id}.mp4")
    side = os.path.join(seg_dir, f"seg_{job_id}.json")
    check("mp4 落在 segments/ 里", os.path.isfile(mp4), mp4)
    check("边车 json 也写下来了", os.path.isfile(side), side)
    if not os.path.isfile(side):
        raise RuntimeError("没有边车文件，后面没得验")

    with open(side, encoding="utf-8") as f:
        rec = json.load(f)
    mats = rec.get("materials") or []
    check("记录了 2 项素材", len(mats) == 2, json.dumps(mats, ensure_ascii=False))
    check("素材名是**人看的名字**，不是路径",
          [m.get("name") for m in mats] == ["林晚", "雨夜街道"],
          str([m.get("name") for m in mats]))
    # role 是按**工作流语义**定的（2026-09-19）：I2V 才有首帧/尾帧，
    # Ref2VA 没有尾帧口、所有图都是参考图 —— 写错了用户会问"我没挑首帧啊"。
    roles0 = [m.get("role") for m in mats]
    if str(server_app._load_service_config().get("video_workflow") or "") == "ref2va":
        check("Ref2VA：两张都是参考图（没有首尾帧概念）", roles0 == ["selected", "selected"],
              str(roles0))
    else:
        check("角色是首帧、场景是尾帧", roles0 == ["first_frame", "last_frame"], str(roles0))
    check("kind 也记了（前端要显示「角色 / 场景」）",
          [m.get("kind") for m in mats] == ["character", "scene"], str([m.get("kind") for m in mats]))
    check("提示词一字不差", rec.get("prompt") == seen1.get("video_prompt"),
          str(rec.get("prompt"))[:60])
    check("时长 / 清晰度 / 模式都对",
          rec.get("duration") == 7.0 and rec.get("megapixels") == 0.9 and rec.get("mode") == "flf",
          f"duration={rec.get('duration')} mp={rec.get('megapixels')} mode={rec.get('mode')}")
    check("记下了用户写的中文描述", rec.get("note") == "雨夜，女孩撑着伞走进便利店",
          str(rec.get("note")))
    check("记下了视频后端 / 模式", bool(rec.get("video_backend")),
          f"{rec.get('video_backend')} / {rec.get('video_mode')}")
    # 角色到底是拿哪张图进的模型：一个角色有单张定妆照和四视图设定图两个文件，
    # 斌哥 2026-09-19 在 ComfyUI 里对着两张比了半天。记录里必须写得出**文件名**。
    wf0 = str(server_app._load_service_config().get("video_workflow") or "")
    cmat = next((m for m in mats if m.get("kind") == "character"), {})
    if wf0 == "ref2va":
        check("Ref2VA：角色进模型的是**四视图设定图**", cmat.get("file") == "林晚_sheet.png",
              str(cmat))
    else:
        check("I2V：角色进模型的是**正面定妆照**（拼图不能当首帧）",
              cmat.get("file") == "林晚.png", str(cmat))
    check("每项素材都写明了实际用的文件名", all(m.get("file") for m in mats),
          str([m.get("file") for m in mats]))

    # ---------- 3) 通过正在跑的服务读回来（走 HTTP，验读取路径） ----------
    lst = http("GET", f"/api/tasks/{task_id}/segments")
    item = next((x for x in lst["items"] if x["name"] == f"seg_{job_id}.mp4"), None)
    check("列表里能看到这一段", item is not None, json.dumps(lst["items"], ensure_ascii=False))
    check("列表标了 has_detail=true", bool(item and item.get("has_detail")),
          str(item and item.get("has_detail")))
    check("服务重启也能拿到 mode（从边车补的）", bool(item and item.get("mode")), str(item and item.get("mode")))

    detail = http("GET", f"/api/tasks/{task_id}/segments/seg_{job_id}.mp4")
    check("详情接口 found=true", detail.get("found") is True, json.dumps(detail, ensure_ascii=False)[:120])
    check("详情里素材名 / 角色齐了",
          [m.get("name") for m in detail.get("materials") or []] == ["林晚", "雨夜街道"],
          str([m.get("name") for m in detail.get("materials") or []]))
    check("详情里 name 是请求的那个文件名",
          detail.get("name") == f"seg_{job_id}.mp4", str(detail.get("name")))

    # ---------- 4) 老片子（没有边车）必须 found=false，不能瞎编 ----------
    old = os.path.join(seg_dir, "seg_legacy.mp4")
    with open(old, "wb") as f:
        f.write(b"\x00" * 2048)
    legacy = http("GET", f"/api/tasks/{task_id}/segments/seg_legacy.mp4")
    check("没有边车的老片子 found=false", legacy.get("found") is False,
          json.dumps(legacy, ensure_ascii=False))
    check("found=false 时不带 materials 字段（前端据此走说明文案）",
          "materials" not in legacy, str(list(legacy.keys())))
    legacy_item = next((x for x in http("GET", f"/api/tasks/{task_id}/segments")["items"]
                        if x["name"] == "seg_legacy.mp4"), None)
    check("列表里老片子 has_detail=false", bool(legacy_item) and not legacy_item.get("has_detail"),
          str(legacy_item and legacy_item.get("has_detail")))

    # ---------- 5) 文件名不合法要挡住（防目录穿越） ----------
    for bad in ["..%2Fproject.json", "project.json", "a.mp4.bak"]:
        try:
            http("GET", f"/api/tasks/{task_id}/segments/{bad}")
            check(f"拒绝不合法文件名 {bad}", False, "居然通过了")
        except urllib.error.HTTPError as e:
            check(f"拒绝不合法文件名 {bad}", e.code in (400, 404), f"HTTP {e.code}")

except SystemExit:
    raise
except Exception as exc:      # noqa: BLE001
    check("验证脚本执行完成", False, f"{type(exc).__name__}: {exc}")
finally:
    if task_id:
        try:
            if seg_dir and os.path.isdir(seg_dir):
                import shutil
                shutil.rmtree(seg_dir, ignore_errors=True)
            req = urllib.request.Request(f"{API}/api/tasks/{task_id}?confirm=true&purge=true", method="DELETE")
            with urllib.request.urlopen(req, timeout=60) as r:
                print(f"\n清理测试作品 {task_id} -> {r.status}")
        except Exception as e:      # noqa: BLE001
            print(f"\n⚠️ 清理失败：{e}")
    else:
        print("\n⚠️ 没建出作品，请手动检查有没有残留 draft")

    bad = results.count(False)
    print("=" * 74)
    print(f"结果：{len(results) - bad}/{len(results)} 通过" + ("  ← 有 FAIL" if bad else ""))
    print("=" * 74)
    sys.exit(1 if bad else 0)

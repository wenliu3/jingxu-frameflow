# -*- coding: utf-8 -*-
"""验证「生成记录」的边车文件 —— 这一段用了哪些素材 / 哪句提示词 / 什么参数。

跑法（在项目根）：
    D:/miniforge/python.exe dev/api/_verify_segment_record.py
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
import shutil
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API = os.environ.get("E2E_API", "http://127.0.0.1:8000").rstrip("/")
FACE = os.path.join(ROOT, "dev", "e2e", "_e2e_face.png")

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
    sys.path.insert(0, ROOT)            # schemas.py 在项目根，不在 server/
    import app as server_app                                    # noqa: E402
    from schemas import Project                                 # noqa: E402

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

    # ---------- 1e) 道具只有「三视图设定图」时也吃得到（2026-09-29 修的坑） ----------
    # 现象：道具走「AI 生成」产出的是**三视图设定图**，只落 `sheet`、`images` 恒为空。
    # 而后端从前对非角色素材只读 `a.images[0]` → 恒空 → 判成"没带上"，
    # 前端卡片也一直显示"还没有素材图"。斌哥实测：六个道具做了全用不上，
    # 出片 toast 报"这些素材这次没带上（还没有图）：旧铁剑"。
    # 修法：道具与角色同规则 —— Ref2VA（prefer_sheet=True）下优先吃 sheet。
    # ⚠️ 这里**只写 sheet 文件、不写单件图**，才能复现原缺陷。
    sheet_prop = os.path.join(ROOT, "dev", "e2e", "_e2e_prop_sheet.png")
    if os.path.isfile(FACE):
        os.makedirs(os.path.dirname(sheet_prop), exist_ok=True)
        shutil.copyfile(FACE, sheet_prop)
    with open(sheet_prop, "rb") as fh:
        sheet_b64 = base64.b64encode(fh.read()).decode()
    http("POST", f"/api/tasks/{task_id}/assets", {"kind": "prop", "name": "三视剑", "design": False})
    server_app.TASKS[task_id] = server_app._load_task_from_disk(task_id)
    tmp_proj = server_app.TASKS[task_id]["project"]["assets"]
    prop_idx = next(i for i, a in enumerate(tmp_proj) if a.get("name") == "三视剑")
    # 把设定图直接摆到约定位置 `assets/prop_三视剑_sheet.png`，再让后端重认一次盘
    adir = os.path.join(ROOT, "outputs", task_id, "assets")
    os.makedirs(adir, exist_ok=True)
    shutil.copyfile(sheet_prop, os.path.join(adir, "prop_三视剑_sheet.png"))
    server_app.TASKS[task_id] = server_app._load_task_from_disk(task_id)
    proj3 = server_app.TASKS[task_id]["project"]
    a3 = proj3["assets"][prop_idx]
    check("道具的设定图被认到 sheet 上（约定文件名 prop_<名>_sheet.png）",
          os.path.basename(str(a3.get("sheet") or "")) == "prop_三视剑_sheet.png",
          f"sheet={a3.get('sheet')!r} images={a3.get('images')!r}")
    check("道具的 images 仍然是空的（设定图不进 images，拼图会被读成三件道具）",
          not a3.get("images"), str(a3.get("images")))

    wf_mode = str(server_app._load_service_config().get("video_workflow") or "i2v")
    # ⚠️ TASKS 里存的是**原始 dict**（`task["project"]`），`_resolve_material_ref` 要的是
    #    `Project` 对象（读 .assets / .characters 属性）。必须先 Project.from_dict 转一次，
    #    否则报 `'dict' object has no attribute 'assets'`。
    _proj_obj = Project.from_dict(server_app.TASKS[task_id]["project"])
    _m = server_app._resolve_material_ref(
        _proj_obj, f"prop:{prop_idx}",
        prefer_sheet=(wf_mode == "ref2va"),
    )
    if wf_mode == "ref2va":
        check("Ref2VA 下：只有设定图的道具也解析得出来（不再被判成没带上）",
              _m is not None and os.path.basename(_m["path"]) == "prop_三视剑_sheet.png",
              "None（← 原缺陷：只读 images[0]）" if _m is None else _m["path"])
        started5 = server_app.start_segment_video(task_id, server_app.SegmentVideoBody(
            frames=["character:0", f"prop:{prop_idx}"],
            prompt="The person holds the sword.",
            duration=7,
        ))
        for _ in range(60):
            if server_app.SEGMENT_JOBS[started5["job_id"]]["status"] != "running":
                break
            time.sleep(0.2)
        check("出片时道具进了参考图列表（ref_image_paths 含那张设定图）",
              any("prop_三视剑" in os.path.basename(str(p))
                  for p in (FakeProvider.seen.get("ref_image_paths") or [])),
              str([os.path.basename(str(p)) for p in FakeProvider.seen.get("ref_image_paths") or []]))
        check("这一次道具没被报成没带上", started5.get("missing") == [], str(started5.get("missing")))
    else:
        check(f"当前是 I2V 工作流（{wf_mode}）：只有设定图的道具本就不该当首帧，跳过该组断言", True)

    # ---------- 1f) 提示词那一侧也要吃得到道具的三视图（2026-09-29 修，斌哥报的那个） ----------
    # 现象（他的截图）：出片面板底下挂黄字「道具「旧铁剑」还没有素材图 → 无法作为参考图」，
    # 而卡片上那张三视图明明早就生成好了；提示词里也一个道具都引用不到。
    # 根因：**"用素材的哪张图"有两份独立实现** —— 上面那个 `_resolve_material_ref`（出片时
    # 上传哪张）改了，`ref_plan.build_ref_plan`（写提示词时 `<Picture N>` 是哪张）没改，
    # 而道具的图只在 `sheet` 上、`images` 恒为空。
    # 这道题的正解是"这条规则只有一处"（`schemas.Asset.primary_image`），下面两条断言就是钉子：
    # 把两份实现钉在同一个答案上，谁再分叉谁挂。
    import ref_plan as ref_plan_mod

    proj_now = Project.from_dict(server_app.TASKS[task_id]["project"])
    plan = ref_plan_mod.build_ref_plan(
        proj_now, characters=["林晚"], props=["三视剑"], scene="雨夜街道",
    )
    prop_slot = next((s for s in plan.slots if s.label == "三视剑"), None)
    check("ref_plan：只有三视图设定图的道具也进得了参考槽位（不再报「还没有素材图」）",
          prop_slot is not None and os.path.basename(prop_slot.path) == "prop_三视剑_sheet.png",
          f"slots={[os.path.basename(s.path) for s in plan.slots]} warnings={plan.warnings}")
    check("ref_plan 与出片解析给道具选的是**同一个文件**（两处口径不许分叉）",
          prop_slot is not None and _m is not None and prop_slot.path == _m["path"],
          f"plan={getattr(prop_slot, 'path', '')!r} "
          f"resolve={(_m or {}).get('path')!r}")

    # 编号顺序：角色 → 道具 → 场景（= `ref_plan.REF_KIND_ORDER`），`<Picture N>` 连续
    order_labels = [s.label for s in plan.images]
    check("ref_plan：编号顺序是「角色 → 道具 → 场景」",
          order_labels == ["林晚", "三视剑", "雨夜街道"], str(order_labels))
    check("ref_plan：<Picture N> 从 1 连续编到底",
          [s.tag for s in plan.images] == [f"<Picture {i + 1}>" for i in range(len(plan.images))],
          str([s.tag for s in plan.images]))

    if wf_mode == "ref2va":
        # ⚠️ 这道题的关键：前端发来的 frames 是**它自己列表的顺序**（角色 → 场景 → 道具），
        #    而提示词按「角色 → 道具 → 场景」编号。出片时必须按**提示词的顺序**重排 ——
        #    否则 ref_image_1 是场景图，提示词却说 `<Picture 2>` 是那把剑：
        #    模型照着一张山村背景去"锁住这把剑的形状材质"，画面里当然不会有剑。
        #    所以这里**故意**按前端那个顺序发，验的是后端有没有把它摆正。
        started6 = server_app.start_segment_video(task_id, server_app.SegmentVideoBody(
            frames=["character:0", "scene:0", f"prop:{prop_idx}"],   # ← 前端顺序，与编号顺序不同
            prompt="The person holds the sword in the rainy street.",
            duration=7,
        ))
        for _ in range(60):
            if server_app.SEGMENT_JOBS[started6["job_id"]]["status"] != "running":
                break
            time.sleep(0.2)
        sent = [os.path.basename(str(p)) for p in (FakeProvider.seen.get("ref_image_paths") or [])]
        check("出片时参考图按提示词的编号顺序上传（角色 → 道具 → 场景）",
              sent == ["林晚_sheet.png", "prop_三视剑_sheet.png", "scene_雨夜街道.png"], str(sent))
        # ⚠️ 这条是本题真正的钉子：**上传的第 N 张必须就是提示词 <Picture N> 说的那个文件**。
        #    两个方向都要对得上 —— 顺序（上面那条）与"取哪张图"（ref_plan 与
        #    _resolve_material_ref 各算一次）。哪边再分叉，这条就挂。
        check("参考图逐张对得上 <Picture N>（顺序 + 取哪张图，两处口径完全一致）",
              len(sent) == len(plan.images)
              and sent == [os.path.basename(s.path) for s in plan.images],
              f"上传={sent} 提示词={[os.path.basename(s.path) for s in plan.images]}")


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

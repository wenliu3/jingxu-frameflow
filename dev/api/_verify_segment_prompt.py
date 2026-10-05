# -*- coding: utf-8 -*-
"""验证「让 AI 帮写提示词」这条链路（2026-09-18 改版）。

默认**不调模型** —— 只验代码负责的那几件事：
  · 镜头时间轴（几颗、每颗几秒、最后一个切点离结尾够不够 2 秒）
  · MM:SS.mmm 时间戳格式
  · 负面约束有没有拼进 detailed_description
  · 六段式结构还在不在（字段名、顺序）
  · audit 对一份合规正文不再报警

加 E2E_LIVE=1 会**真调一次文本模型**，把生成的正文打出来给人看质量。
文本调用很便宜、也不占出图/出视频的日额度，但仍默认关着 —— 想跑的时候再开。

跑法（在项目根）：
    D:/miniforge/python.exe dev/api/_verify_segment_prompt.py
    E2E_LIVE=1 D:/miniforge/python.exe dev/api/_verify_segment_prompt.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


# 文本模型的密钥平时是**服务端**从 service_config.json 写进环境变量的
# （server/app.py 的 `_apply_service_config`，import 时就跑）。本脚本是独立进程，
# 得自己补这一步 —— 否则 E2E_LIVE=1 会报「缺少 DEEPSEEK_API_KEY」，
# 看起来像"密钥没配"，其实只是没加载。
def load_service_keys() -> None:
    try:
        with open(os.path.join(ROOT, "service_config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return
    for key, env in (("text_api_key", "DEEPSEEK_API_KEY"),
                     ("text_base_url", "DEEPSEEK_BASE_URL"),
                     ("text_model", "DEEPSEEK_MODEL")):
        value = str(cfg.get(key) or "").strip()
        if value:
            os.environ.setdefault(env, value)


load_service_keys()

import ref_plan                                    # noqa: E402
from schemas import Asset, Character, Project      # noqa: E402

LIVE = os.environ.get("E2E_LIVE") == "1"
results: list[bool] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"\n        → {extra}" if extra else ""))


def fake_project() -> Project:
    """一个最小的作品：1 个角色 + 1 个场景。只用来验拼装，不碰磁盘。"""
    return Project(
        title="雨夜便利店",
        logline="女孩雨夜走进便利店，遇见了不该遇见的人。",
        style="写实电影感，冷蓝与暖橙对撞，胶片颗粒",
        style_en="Live-action, cinematic, cool-warm contrast, 35mm film grain",
        aspect_ratio="16:9",
        characters=[Character(name="林晚", anchor="短发少女，米色风衣",
                              anchor_en="a young woman with short hair in a beige trench coat",
                              image_path="D:/fake/linwan.png")],
        assets=[Asset(kind="scene", name="雨夜街道", anchor="湿漉漉的柏油路与霓虹招牌",
                      images=["D:/fake/street.png"])],
    )


print("=" * 74)
print("「AI 帮写提示词」链路验证" + ("（含真实模型调用）" if LIVE else "（不调模型）"))
print("=" * 74 + "\n")

# ---------- 1) 镜头颗粒度 ----------
print("-- 镜头时间轴 --")
expect = [(4, 2), (5, 2), (6, 3), (10, 3), (12, 4), (15, 4)]
got = [(d, ref_plan.shot_count_for(d)) for d, _ in expect]
check("镜头数按官方颗粒度分档", got == expect, str(got))

bad = []
for d in (4, 5, 6, 8, 10, 12, 15):
    tl = ref_plan.plan_shot_timeline(d)
    last_start = tl[-1][1]
    if last_start > d - 2 + 1e-6:
        bad.append(f"{d}s: 最后一切在 {last_start:.3f}s")
check("每个时长下最后一个切点都离结尾 ≥2 秒", not bad, "; ".join(bad) or "全部满足")

tl10 = ref_plan.plan_shot_timeline(10)
check("时间轴覆盖满整段、无缝隙",
    abs(sum(x[2] for x in tl10) - 10) < 1e-6 and abs(tl10[0][1]) < 1e-9,
    " / ".join(f"#{i} {s:.3f}s+{l:.3f}s" for i, s, l in tl10))

# ---------- 2) 时间戳格式 ----------
print("\n-- 时间戳格式（官方要 MM:SS.mmm） --")
for sec, want in ((0.0, "00:00.000"), (3.333, "00:03.333"), (6.667, "00:06.667"),
                  (11.25, "00:11.250"), (65.5, "01:05.500")):
    got_ts = ref_plan.format_timestamp(sec)
    check(f"{sec}s → {want}", got_ts == want, got_ts)

# ---------- 3) 六段式拼装 + 负面约束 ----------
print("\n-- 六段式拼装 --")
project = fake_project()
body = (
    "[Shot 1] 35mm film grain, low-saturation cool palette with warm practical lights. "
    "Shot 1 is about 5 seconds: a medium close-up of <Subject 1> as she pushes the door open.\n\n"
    "[Shot 2] At 00:05.000, a medium shot from behind the counter as she stops mid-step."
)
plan = ref_plan.compose_ref2va_prompt(
    project, video_prompt=body, soundscape="Steady rain on glass; her footsteps.",
    duration=10.0, characters=["林晚"], scene="雨夜街道", use_voice=False,
)
sections = ["subject_definitions:", "summary:", "retention_analysis:",
            "detailed_description:", "overall_soundscape:", "non_diegetic_music:"]
missing = [s for s in sections if s not in plan.prompt]
check("六个字段名一个不少", not missing, "缺：" + str(missing) or "都在")
order = [plan.prompt.index(s) for s in sections]
check("字段顺序没变（官方顺序不能换）", order == sorted(order), str(order))
check("负面约束拼进了 detailed_description", ref_plan.DETAILED_CONSTRAINTS in plan.prompt)
check("负面约束在正文**末尾**（不是插在镜头中间）",
    plan.prompt.index(ref_plan.DETAILED_CONSTRAINTS) > plan.prompt.index("At 00:05.000"))
check("约束里点名了字幕/水印/UI", "subtitles" in ref_plan.DETAILED_CONSTRAINTS
      and "watermark" in ref_plan.DETAILED_CONSTRAINTS)
check("约束保持连贯运动，并允许明确的幻想效果", "coherent motion" in ref_plan.DETAILED_CONSTRAINTS
      and "fantastical effect" in ref_plan.DETAILED_CONSTRAINTS)
check("只禁止未请求的人物，允许明确换衣与环境变化", "unrequested people" in ref_plan.DETAILED_CONSTRAINTS
      and "requested changes" in ref_plan.DETAILED_CONSTRAINTS)
check("音轨段用的是传进来的 soundscape，不是兜底句",
    "Steady rain on glass" in plan.prompt, plan.prompt.split("overall_soundscape:")[1][:70])
check("手写模式（soundscape 为空）会填兜底句",
    "Natural ambience" in ref_plan.compose_ref2va_prompt(
        project, video_prompt=body, duration=10.0, characters=["林晚"], use_voice=False).prompt)
check("拼装是幂等的（重复调用不会叠加两份约束）",
    ref_plan.compose_ref2va_prompt(
        project, video_prompt=body + "\n\n" + ref_plan.DETAILED_CONSTRAINTS,
        duration=10.0, characters=["林晚"], use_voice=False,
    ).prompt.count(ref_plan.DETAILED_CONSTRAINTS) == 1)

# ---------- 3b) 写正文的 agent：运行时规则源 + 两套格式分模式（2026-09-19） ----------
# 规则精要（docs/h3-guides/h3-prompt-rules.md）是**运行时读**的：官方更新只改文件、不改代码。
# 它由 agents._prompt_rules() 读进 system prompt，所以这里既验文件在位、也验真的拼进去了。
print("\n-- 写正文的 agent（规则源 / 模式）--")
import agents                                       # noqa: E402

rules = agents._prompt_rules()
check("规则精要文件读得到（docs/h3-guides/h3-prompt-rules.md）", len(rules) > 800, f"{len(rules)} 字")
_must = ("Push In", "subject_definitions", "fully_preserved", "at 0.00 seconds")
check("规则里有关键几条（运镜词表 / 六段式字段 / retention 标记 / 对齐指令）",
      all(k in rules for k in _must),
      "缺：" + str([k for k in _must if k not in rules]))

captured: dict = {}
_orig_chat = agents.chat_json


def _fake_chat(system, user, **_kw):
    captured["system"] = system
    captured["user"] = user
    return {"video_prompt": "[Shot 1] Live-action, a medium shot frames her.",
            "soundscape": "Room tone and footsteps."}


agents.chat_json = _fake_chat
try:
    agents.compose_segment_prompt(
        project, description="她在窗边回头", material_lines=["角色「林晚」（参考图 <Picture 1>）"],
        duration=8.0,
    )
    check("Ref2VA：system 里拼进了规则精要", "Push In" in captured["system"])
    check("Ref2VA：仍然要求用编号引用素材", "<Subject N> 引用" in captured["system"]
          or "<Subject N>" in captured["system"])
    agents.compose_segment_prompt(
        project, description="她在窗边回头", material_lines=["角色「林晚」"],
        duration=8.0, ref_mode=False,
    )
    check("基础模式：system 明确「不要用 <Subject N> 编号」",
          "不要用 `<Subject N>` 编号" in captured["system"])
    check("基础模式：素材清单不再叫「编号 -> 是什么」", "没有编号" in captured["user"])
    check("基础模式：仍然带规则精要（两套格式共用同一份规则源）", "Push In" in captured["system"])
finally:
    agents.chat_json = _orig_chat

# ---------- 4) audit 对合规正文不报警 ----------
print("\n-- 体检（audit_detailed_description） --")
check("合规正文没有警告", not ref_plan.audit_detailed_description(body, 10.0),
      str(ref_plan.audit_detailed_description(body, 10.0)))
check("最后一镜贴到片尾会被抓到",
    any("不足 2 秒" in w for w in ref_plan.audit_detailed_description(
        body.replace("00:05.000", "00:09.500"), 10.0)),
    str(ref_plan.audit_detailed_description(body.replace("00:05.000", "00:09.500"), 10.0)))
# ⚠️ 这条是补出来的：原来 n=0 会让"偏碎"和"最后一镜贴片尾"两条检查**全部静默跳过**，
# 一份完全没有 [Shot N] 标记的正文会被判合格 —— 实测真跑时就撞上了。
check("一个 [Shot N] 标记都没有会被抓到（原来会静默跳过）",
    any("[Shot N] 标记都没有" in w for w in ref_plan.audit_detailed_description(
        "35mm film grain, anamorphic 2.39:1. She walks in and stops. "
        "The camera pushes in at slow speed.", 10.0)),
    str(ref_plan.audit_detailed_description(
        "35mm film grain, anamorphic 2.39:1. She walks in and stops. "
        "The camera pushes in at slow speed.", 10.0)))
check("镜头太碎会被抓到",
    any("偏碎" in w for w in ref_plan.audit_detailed_description(
        body + "\n[Shot 3] At 00:06.000, x.\n[Shot 4] At 00:07.000, y.\n[Shot 5] At 00:08.000, z.", 5.0)),
    str(ref_plan.audit_detailed_description(
        body + "\n[Shot 3] At 00:06.000, x.\n[Shot 4] At 00:07.000, y.\n[Shot 5] At 00:08.000, z.", 5.0)))
check("约束段本身不会把镜头计数带偏（体检用的是模型原文）",
    not any("偏碎" in w for w in ref_plan.audit_detailed_description(body, 10.0)))

# ---------- 5) 真跑一次（可选） ----------
if LIVE:
    print("\n-- 真实调用（E2E_LIVE=1，走 HTTP 打真正的服务） --")
    import base64
    import urllib.request

    api = os.environ.get("E2E_API", "http://127.0.0.1:8000").rstrip("/")
    face = os.path.join(ROOT, "dev", "e2e", "_e2e_face.png")
    task_id = ""

    def http(method, path, payload=None):
        data, headers = None, {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(api + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())

    try:
        task_id = http("POST", "/api/tasks/draft", {"title": "提示词验证"})["task_id"]
        b64 = base64.b64encode(open(face, "rb").read()).decode()
        for kind, name in (("character", "林晚.png"), ("scene", "雨夜街道.png")):
            http("POST", f"/api/tasks/{task_id}/materials/upload?kind={kind}",
                 {"filename": name, "data_b64": b64})

        res = http("POST", f"/api/tasks/{task_id}/compose", {
            "characters": ["林晚"], "scene": "雨夜街道",
            "description": "雨夜，女孩撑着伞走进便利店，镜头慢慢推近她的脸，"
                           "她忽然停住，盯着货架尽头的那个人。",
            "duration": 10.0, "use_voice": False,
        })
        vp, ss = res.get("video_prompt", ""), res.get("soundscape", "")
        print("\n----- video_prompt -----\n" + vp + "\n")
        print("----- soundscape -----\n" + ss + "\n")
        words = len(vp.split())
        check("接口返回了 video_prompt 与 soundscape 两个字段", bool(vp) and bool(ss),
              f"prompt={len(vp)} 字符 / soundscape={len(ss)} 字符")
        check("音轨真的被传进六段式（不是兜底句）",
              bool(ss) and ss.split(".")[0][:20] in res.get("prompt", ""),
              res.get("prompt", "").split("overall_soundscape:")[-1][:90])
        check("六段式里带上了负面约束", ref_plan.DETAILED_CONSTRAINTS in res.get("prompt", ""))
        check("Ref2VA 正文篇幅接近官方建议", 300 <= words <= 550, f"{words} 词")
        check("正文用 <Subject N> 引用了素材", "<Subject" in vp)
        check("时间轴照抄了代码给的那张表",
              "[Shot 2]" not in vp,
              "正文里的时间戳：" + str(sorted(set(
                  w for w in vp.replace(",", " ").split() if ":" in w and "." in w))[:6]))
        check("正文包含首镜标记，风格可位于标记前", "[Shot 1]" in vp, vp[:60])
        check("写了表演细节（眉/眼/嘴角/呼吸/手 至少一个）",
              any(k in vp.lower() for k in ("eyebrow", "brow", "eye", "lip", "mouth",
                                            "breath", "hand", "shoulder", "jaw")), "")
        check("真跑的正文过 audit 无警告",
              not ref_plan.audit_detailed_description(vp, 10.0),
              str(ref_plan.audit_detailed_description(vp, 10.0)))
    except Exception as exc:      # noqa: BLE001
        check("真实调用成功", False, f"{type(exc).__name__}: {exc}")
    finally:
        if task_id:
            try:
                req = urllib.request.Request(f"{api}/api/tasks/{task_id}?confirm=true&purge=true",
                                             method="DELETE")
                with urllib.request.urlopen(req, timeout=60) as r:
                    print(f"清理测试作品 {task_id} -> {r.status}")
            except Exception as e:      # noqa: BLE001
                print(f"⚠️ 清理失败：{e}")
else:
    print("\n（跳过真实模型调用；要看质量就加 E2E_LIVE=1）")

bad = results.count(False)
print("\n" + "=" * 74)
print(f"结果：{len(results) - bad}/{len(results)} 通过" + ("  ← 有 FAIL" if bad else ""))
print("=" * 74)
sys.exit(1 if bad else 0)

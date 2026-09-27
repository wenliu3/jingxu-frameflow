# -*- coding: utf-8 -*-
"""验证「让 AI 帮写」这条链路（2026-09-19 加：中文进、**中文出**）。

两步走：
  ① POST /api/tasks/{id}/prompt/optimize   中文口语描述 → **中文**画面描述   ← 本脚本验这个
  ② POST /api/tasks/{id}/compose           中文 → 英文 H3 六段式正文         ← _verify_segment_prompt.py 验

默认**不调模型**，只验代码负责的部分：
  · OPTIMIZE_SYSTEM 里那几条硬约束还在（中文输出 / 不写编号 / 要表演细节 / 只回 JSON）
  · 素材清单两种模式：optimize 那版**一行编号都不许带**，compose 那版**必须带编号**
  · 两个函数的签名没被这次抽函数带偏
  · 路由挂上了；不存在的作品回 4xx

加 E2E_LIVE=1 会真调一次文本模型（DeepSeek，很便宜，**不占出图/出视频那种日额度**），
把中文结果打出来给人看质量。会真建一个 draft 作品，跑完自己 DELETE。

跑法（在项目根）：
    D:/miniforge/python.exe dev/api/_verify_optimize_prompt.py
    E2E_LIVE=1 D:/miniforge/python.exe dev/api/_verify_optimize_prompt.py
"""
import inspect
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server"))
API = os.environ.get("E2E_API", "http://127.0.0.1:8000").rstrip("/")


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

import agents                                      # noqa: E402
import ref_plan                                    # noqa: E402
from schemas import Asset, Character, Project      # noqa: E402

LIVE = os.environ.get("E2E_LIVE") == "1"
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
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


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


TAG_RE = re.compile(r"<(Picture|Subject|Audio)\s*\d+>")

print("=" * 74)
print("「让 AI 帮写」中文优化链路验证" + ("（含真实模型调用）" if LIVE else "（不调模型）"))
print("=" * 74 + "\n")

# ---------- 1) OPTIMIZE_SYSTEM 的硬约束 ----------
print("-- OPTIMIZE_SYSTEM 硬约束 --")
sys_prompt = agents.OPTIMIZE_SYSTEM
for kw, why in [
    ("输出中文", "必须明确要求中文输出（用户只看中文）"),
    ("不要输出英文", "要挡住模型顺手翻译成英文"),
    ("表演细节", "表演细节是像真的/像摆拍的分界线，不能漏"),
    ("只用我给的名字", "防止模型凭空造角色名"),
    ("编号", "要明确禁止写 <Picture N> 这类编号"),
    ('{"description"', "必须要求 JSON 输出（chat_json 靠它解析）"),
]:
    check(f"system 里写了「{kw}」—— {why}", kw in sys_prompt)

# ---------- 2) 函数签名 ----------
print("\n-- 函数签名 --")
params = set(inspect.signature(agents.optimize_description).parameters)
check("optimize_description(description=, material_names=, duration=)",
      {"description", "material_names", "duration"} <= params, str(sorted(params)))
check("compose_segment_prompt 仍是 (description=, material_lines=, duration=)（没被抽函数带偏）",
      {"description", "material_lines", "duration"} <= set(inspect.signature(agents.compose_segment_prompt).parameters))

# ---------- 3) 素材清单：两种模式 ----------
print("\n-- 素材清单（optimize 不带编号 / compose 必须带编号）--")
try:
    import app as server_app                       # noqa: E402  —— server/app.py
except Exception as exc:                            # pragma: no cover
    check("能 import server/app.py 以验 _material_lines_for", False, f"{type(exc).__name__}: {exc}")
else:
    plan = ref_plan.build_ref_plan(fake_project(), characters=["林晚"], scene="雨夜街道")
    with_tags = server_app._material_lines_for(plan)
    no_tags = server_app._material_lines_for(plan, with_tags=False)
    print("    带编号  :", json.dumps(with_tags, ensure_ascii=False))
    print("    不带编号:", json.dumps(no_tags, ensure_ascii=False))
    check("compose 那版每一行都带素材编号",
          bool(with_tags) and all(TAG_RE.search(line) for line in with_tags),
          " | ".join(with_tags))
    check("optimize 那版一行编号都不带",
          all(not TAG_RE.search(line) for line in no_tags), " | ".join(no_tags))
    check("optimize 那版仍保留名字（模型要照着名字写，不是照着编号）",
          any("林晚" in line for line in no_tags) and any("雨夜街道" in line for line in no_tags))
    check("抽出 _material_lines_for 后 compose 的行为没变（首行仍是「主体 = 类型「名字」（参考图 编号）」）",
          bool(with_tags) and "= 角色「林晚」" in with_tags[0], with_tags[0] if with_tags else "（空）")
    paths = {getattr(r, "path", "") for r in server_app.app.routes}
    check("路由 /api/tasks/{task_id}/prompt/optimize 挂上了",
          "/api/tasks/{task_id}/prompt/optimize" in paths)

# ---------- 4) 路由行为（打正在跑的服务）----------
print("\n-- 路由行为（HTTP）--")
try:
    http("POST", "/api/tasks/deadbeefdead/prompt/optimize", {"description": "测试"})
    check("不存在的作品应该报错", False, "居然成功了")
except urllib.error.HTTPError as exc:
    check("不存在的作品回 4xx", 400 <= exc.code < 500, f"HTTP {exc.code}")

# ---------- 5) 真实调用（可选）----------
if LIVE:
    print("\n-- 真实调用：一句中文 → 一段中文画面描述 --")
    task_id = ""
    try:
        task_id = http("POST", "/api/tasks/draft", {"title": "优化提示词验证"})["task_id"]
        print("    建了临时作品", task_id)
        res = http("POST", f"/api/tasks/{task_id}/prompt/optimize", {
            "description": "一个人在海边等日落，风吹着头发",
            "duration": 10,
        })
        text = str(res.get("description") or "")
        zh = len(re.findall(r"[\u4e00-\u9fff]", text))
        en = len(re.findall(r"[A-Za-z]{4,}", text))
        print("\n" + "-" * 74)
        print(text)
        print("-" * 74 + "\n")
        check("返回了内容", bool(text.strip()))
        check("正文是中文（汉字 > 80）", zh > 80, f"汉字 {zh} 个 / 共 {len(text)} 字")
        check("没有偷偷写成英文", en < 12, f"英文词 {en} 个")
        check("没有出现素材编号", not TAG_RE.search(text))
        check("没有写成 [Shot N]（那是下一步英文正文的事）", "[Shot" not in text)
    finally:
        if task_id:
            try:
                http("DELETE", f"/api/tasks/{task_id}?confirm=true&purge=true")
                print("    清理临时作品", task_id)
            except Exception as exc:                # pragma: no cover
                print("    ⚠️ 清理失败：", exc)
else:
    print("\n（跳过一次真实模型调用：加 E2E_LIVE=1 再跑）")

bad = results.count(False)
print("\n" + "=" * 74)
print(f"结果：{len(results) - bad}/{len(results)} 通过" + ("  ← 有 FAIL" if bad else ""))
print("=" * 74)
sys.exit(1 if bad else 0)

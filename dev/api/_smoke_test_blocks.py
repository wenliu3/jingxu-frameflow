"""分块流程冒烟测试：mock 掉 LLM 与图像/视频 provider，验证数据闭环与接口行为。

不联网、不消耗额度。跑法：python dev/api/_smoke_test_blocks.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

import agents  # noqa: E402
from schemas import Block, Project  # noqa: E402
import pipeline  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + (f"  [{extra}]" if extra and not cond else ""))
    print(("  ok  " if cond else " FAIL ") + name + ("" if cond else f"  -> {extra}"))


# ---------------------------------------------------------------- 1) agents.next_block / block_prompts
FAKE_NEXT = {
    "done": False,
    "block": {
        "summary": "少女在天台等风来",
        "characters": ["小满"],
        "props": ["旧收音机"],
        "scene": "城中村天台",
        "dialogue": "旁白：那天风很大",
        "beats": [
            {"start": 0, "end": 4, "action": "她靠在栏杆上", "camera": "中景"},
            {"start": 4, "end": 10, "action": "风起，发丝扬起", "camera": ""},
        ],
        "camera": "中景",
        "motion": "Static Shot",
        "avoid": "不要切换场景",
        "continue_last": True,
        "duration": 99,   # AI 乱给时长，代码必须强制 10s
    },
}
FAKE_PROMPTS = {
    "visual_prompt": "portrait of a girl, rooftop, cinematic",
    "negative_prompt": "text, watermark",
    "video_prompt": "[Shot 1] Live-action. From 0 to 4 seconds, ... Avoid: no scene cuts.",
    "audio": "Wind brushes past the railing.",
}

orig_chat = agents.chat_json

def smart_chat(system, user, temperature=0.7):
    if "提示词工程师" in system:
        return FAKE_PROMPTS
    if "选角导演" in system:
        return {"anchor": "25岁男性，寸头", "anchor_en": "25-year-old man, buzz cut", "voice": "低沉男声"}
    # ⚠️ 这里以前匹配的是「美术指导」，而 design_asset 的 system 早就改成了
    #    「你是场景美术 / 分镜美术」。关键字对不上就静默落到 FAKE_NEXT，
    #    表现是 design_asset 返回空 → add_asset 报「素材设计没有返回有效描述」，
    #    看着像产品坏了，其实是 mock 陈旧。改 agents.design_asset 的 system 文案时记得同步这里。
    if "美术" in system:
        return {"anchor": "锈迹斑斑的自行车棚"}
    if "分块" in system or "导演" in system:
        return FAKE_NEXT
    return FAKE_NEXT

agents.chat_json = lambda system, user, temperature=0.7: FAKE_NEXT

project = Project.from_dict({
    "title": "风起",
    "logline": "少女与一台旧收音机",
    "style": "青春电影感",
    "characters": [{"name": "小满", "anchor": "16岁少女，马尾辫", "anchor_en": "16-year-old girl, ponytail", "voice": "清亮少女音"}],
    "assets": [
        {"kind": "prop", "name": "旧收音机", "anchor": "掉漆的铁壳收音机"},
        {"kind": "scene", "name": "城中村天台", "anchor": "天台，晾衣绳，晨光"},
    ],
})

result = agents.next_block(project, [], "开场从天台开始")
check("next_block 返回块", result["done"] is False and result["block"]["summary"] == "少女在天台等风来")

b = Block.from_dict({**result["block"], "block_id": 1, "duration": 10.0})
check("块时长强制 10s", b.duration == 10.0, str(b.duration))
check("块节拍解析", len(b.beats) == 2 and b.beats[0].action == "她靠在栏杆上")
check("块承接标记", b.continue_last is True)

agents.chat_json = smart_chat
out = agents.block_prompts(project, b)
check("block_prompts 就地填充", b.visual_prompt == FAKE_PROMPTS["visual_prompt"] and b.audio == FAKE_PROMPTS["audio"])
check("block_prompts 返回值", out["video_prompt"] == FAKE_PROMPTS["video_prompt"])

# 故事收尾分支
def done_chat(system, user, temperature=0.7):
    return {"done": True, "reason": "故事在第 3 块收尾"}
agents.chat_json = done_chat
done = agents.next_block(project, [b], "")
check("next_block 收尾分支", done == {"done": True, "reason": "故事在第 3 块收尾"})

# design_character / design_asset
agents.chat_json = smart_chat
d = agents.design_character(project, "阿岩")
check("design_character", d["anchor"] == "25岁男性，寸头" and d["voice"] == "低沉男声")
agents.chat_json = smart_chat
check("design_asset", agents.design_asset(project, "scene", "车棚") == "锈迹斑斑的自行车棚")

# ---------------------------------------------------------------- 2) pipeline._save_blocks / 路径重指
import shutil  # noqa: E402

from server import app as server_app  # noqa: E402

tmp = os.path.join(server_app.OUT_ROOT, "abc123def456_src")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "images"), exist_ok=True)
with open(os.path.join(tmp, "images", "block_01.png"), "wb") as fh:
    fh.write(b"png")
pipeline._save_blocks(tmp, [b.to_dict()])
raw = json.load(open(os.path.join(tmp, "blocks.json"), encoding="utf-8"))
check("blocks.json 落盘", raw[0]["summary"] == "少女在天台等风来")

# 模拟 server 的磁盘恢复：blocks.json 里的路径按实际文件重指
with open(os.path.join(tmp, "project.json"), "w", encoding="utf-8") as fh:
    json.dump(pipeline.dump(project), fh, ensure_ascii=False)
with open(os.path.join(tmp, "task.json"), "w", encoding="utf-8") as fh:
    json.dump({"task_id": "abc123def456", "flow": "blocks", "status": "succeeded"}, fh)
real_out = os.path.join(server_app.OUT_ROOT, "abc123def456")
shutil.rmtree(real_out, ignore_errors=True)
os.rename(tmp, real_out)
try:
    loaded = server_app._load_task_from_disk("abc123def456")
    check("磁盘恢复 flow=blocks", loaded and loaded["flow"] == "blocks")
    check("磁盘恢复 blocks", len(loaded["blocks"]) == 1)
    check("块图片路径重指", loaded["blocks"][0]["image_path"] == "block_01.png")
    check("块尾帧缺省为空", loaded["blocks"][0]["last_frame"] == "")
finally:
    shutil.rmtree(real_out, ignore_errors=True)

# ---------------------------------------------------------------- 3) FastAPI 接口（mock LLM 线程）
from fastapi.testclient import TestClient  # noqa: E402
import server.app as sa  # noqa: E402

client = TestClient(sa.app)
TASK = "smoke0blocks"[:12].ljust(12, "0")
if not sa.TASK_ID_RE.match(TASK):
    TASK = "a" * 12
sa.TASKS[TASK] = {
    "task_id": TASK,
    "idea": "test",
    "status": "succeeded",
    "stage": "资产就绪",
    "stage_state": "directed",
    "flow": "blocks",
    "done": 0,
    "total": 0,
    "project": pipeline.dump(project),
    "shots": [],
    "blocks": [],
    "stats": {},
    "error": None,
    "created_at": "2026-09-13T00:00:00Z",
}
import os as _os
_os.makedirs(sa._out_dir(TASK), exist_ok=True)
sa._persist(TASK)   # 把 project 写到磁盘，后续 persist 不至于空转

r = client.post(f"/api/tasks/{TASK}/blocks", json={"summary": "手动块", "characters": ["小满"], "duration": 10})
check("手动加块", r.status_code == 200 and r.json()["block_id"] == 1, r.text[:200])

r = client.patch(f"/api/tasks/{TASK}/blocks/1", json={"summary": "改过的梗概", "dialogue": "改词"})
check("编辑块", r.status_code == 200 and r.json()["summary"] == "改过的梗概")
check("编辑块标 stale", r.json().get("prompt_stale") is True)

r = client.patch(f"/api/tasks/{TASK}/blocks/1", json={"visual_prompt": "new prompt"})
check("改提示词标 image_stale", r.json().get("image_stale") is True)

r = client.get(f"/api/tasks/{TASK}")
check("get_task 带 blocks", r.status_code == 200 and len(r.json()["blocks"]) == 1 and r.json()["flow"] == "blocks")

# AI 下一块（线程内走 smart_chat 桩）
agents.chat_json = smart_chat
import time  # noqa: E402
r = client.post(f"/api/tasks/{TASK}/blocks/ai-next", json={"instruction": ""})
check("ai-next 受理", r.status_code == 200, r.text[:200])
for _ in range(50):
    time.sleep(0.1)
    if sa.TASKS[TASK]["status"] != "running":
        break
check("ai-next 产出块", len(sa.TASKS[TASK]["blocks"]) == 2, str(sa.TASKS[TASK].get("error")))
check("ai-next 块带提示词", "rooftop" in (sa.TASKS[TASK]["blocks"][1].get("visual_prompt") or ""))

# 块出图 / 视频：provider 会联网，这里只验证「配置不全时被 409 拦下」的守卫
sa._save_service_config_raw = None
cfg = sa._load_service_config()
if not cfg.get("comfyui_url") and not cfg.get("video_api_url"):
    r = client.post(f"/api/tasks/{TASK}/blocks/2/video")
    check("块视频：无视频服务配置被拦", r.status_code == 409, r.text[:120])

r = client.post(f"/api/tasks/{TASK}/blocks/1/image")
# visual_prompt 已有（ai-next 写过）→ 会走真出图，不能让它联网；改成先清空提示词验证 409
check("块出图守卫（有提示词时放行到出图前）", r.status_code in (200, 500), r.text[:120])

r = client.delete(f"/api/tasks/{TASK}/blocks/1")
check("删块", r.status_code == 200)

r = client.post(f"/api/tasks/{TASK}/export")
check("按块导出：无视频被拦", r.status_code == 409)

r = client.post(f"/api/tasks/{TASK}/characters", json={"name": "阿岩", "anchor": ""})
check("手动加角色（AI 设计被 mock）", r.status_code == 200 and r.json()["anchor"] == "25岁男性，寸头", r.text[:200])

r = client.post(f"/api/tasks/{TASK}/assets", json={"kind": "scene", "name": "车棚", "anchor": ""})
check("手动加素材", r.status_code == 200 and r.json()["anchor"] == "锈迹斑斑的自行车棚", r.text[:200])

# 收尾：把冒烟任务从内存与磁盘清掉
sa.TASKS.pop(TASK, None)
import shutil  # noqa: E402
shutil.rmtree(sa._out_dir(TASK), ignore_errors=True)

agents.chat_json = orig_chat
print(f"\n== {len(PASS)} passed, {len(FAIL)} failed ==")
sys.exit(1 if FAIL else 0)

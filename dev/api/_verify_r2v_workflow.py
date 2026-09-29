# -*- coding: utf-8 -*-
"""验证「多参考图」这条接线（2026-09-19 加）—— **全程离线，不碰 ComfyUI**。

背景：I2V 那份工作流只有一个 `first_frame` 口，所以"选了三张素材，只有第一张真的
发出去"。要支持多人物同框得换 `MiniMaxH3ReferenceToVideo`（Ref2VA），它收
`ref_images.ref_image_0/1/2`。本脚本验的就是**拼出来的工作流对不对**：

  · i2v 模板：首帧/尾帧还接在原来的口上（回归，别改坏）
  · r2v 模板：N 张参考图依次接到 ref_image_0..N-1，且补了 audio_vae
  · 两张图共用后半段（采样器/解码/存盘），节点号没被写死
  · 帧数吸附、步数、像素预算这些旋钮照旧生效
  · 两个 provider 的 generate 签名对齐（MEMORY 里记过的一个老坑）

⚠️ 它**不能**证明工作流在真实 ComfyUI 上跑得通 —— 那要连上 ComfyUI 提交一次。
这里只保证"图拼对了"，把失败范围缩到最小。

跑法：D:/miniforge/python.exe dev/api/_verify_r2v_workflow.py
"""
import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import video_provider as vp                                    # noqa: E402

results: list[bool] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"\n        → {extra}" if extra else ""))


I2V = os.path.join(ROOT, "comfyui", "h3_i2v_api.json")
R2V = os.path.join(ROOT, "comfyui", "h3_r2v_api.json")


def provider(path: str) -> vp.ComfyUIVideoProvider:
    # base_url 只是构造函数要求，本脚本不会发任何请求
    return vp.ComfyUIVideoProvider(base_url="http://127.0.0.1:1", workflow_path=path)


def h3_node(wf: dict) -> tuple[str, dict]:
    for k, v in wf.items():
        if isinstance(v, dict) and str(v.get("class_type", "")) in vp._H3_NODE_CLASSES:
            return k, v
    return "", {}


print("=" * 74)
print("多参考图接线验证（离线）")
print("=" * 74 + "\n")

if not os.path.isfile(R2V):
    raise SystemExit(f"缺 {R2V}")

# ---------- 1) I2V 回归：首帧 / 尾帧 ----------
print("-- i2v 模板（回归，不能改坏） --")
wf = provider(I2V)._build_workflow("P", 10.0, "a.png", last_frame_name="b.png", megapixels=0.9)
hid, h3 = h3_node(wf)
check("找得到 H3 主节点", bool(hid), f"节点 {hid} = {h3.get('class_type')}")
check("还是 MiniMaxH3ImageToVideo", h3.get("class_type") == "MiniMaxH3ImageToVideo")
check("首帧接在 first_frame 上", h3["inputs"].get("first_frame") == ["100", 0],
      str(h3["inputs"].get("first_frame")))
check("尾帧接在 last_frame 上（动态注入的 101）", h3["inputs"].get("last_frame") == ["101", 0],
      str(h3["inputs"].get("last_frame")))
check("两个 LoadImage 的图名都对",
      wf["100"]["inputs"]["image"] == "a.png" and wf["101"]["inputs"]["image"] == "b.png")
check("没有多余的 ref_images 键", not [k for k in h3["inputs"] if k.startswith("ref_images")])

wf_no_last = provider(I2V)._build_workflow("P", 10.0, "a.png")
check("没给尾帧时不注入 101", "101" not in wf_no_last)
check("没给尾帧时没有 last_frame 键", "last_frame" not in h3_node(wf_no_last)[1]["inputs"])

# ---------- 2) Ref2VA：多张参考图 ----------
print("\n-- r2v 模板（多参考图） --")
p = provider(R2V)
wf1 = p._build_workflow("P", 10.0, "a.png")
hid1, h31 = h3_node(wf1)
check("主节点是 MiniMaxH3ReferenceToVideo", h31.get("class_type") == "MiniMaxH3ReferenceToVideo",
      h31.get("class_type"))
check("只给一张图时只接 ref_image_0",
      h31["inputs"].get("ref_images.ref_image_0") == ["100", 0]
      and "ref_images.ref_image_1" not in h31["inputs"],
      str([k for k in h31["inputs"] if k.startswith("ref_images")]))
check("不注入多余的 LoadImage", not [k for k in wf1 if int(k) >= 900], str(sorted(wf1, key=int)[-3:]))

wf3 = p._build_workflow("P", 10.0, "a.png", extra_refs=["b.png", "c.png"])
hid3, h33 = h3_node(wf3)
refs = {k: v for k, v in h33["inputs"].items() if k.startswith("ref_images")}
check("三张图依次接到 ref_image_0/1/2",
      refs == {"ref_images.ref_image_0": ["100", 0],
               "ref_images.ref_image_1": ["901", 0],
               "ref_images.ref_image_2": ["902", 0]},
      json.dumps(refs))
check("注入的 LoadImage 图名对得上",
      wf3["901"]["inputs"]["image"] == "b.png" and wf3["902"]["inputs"]["image"] == "c.png")
check("注入的节点是 LoadImage", wf3["901"]["class_type"] == "LoadImage")
check("补了 audio_vae（r2v 节点要它，i2v 没有这个口）",
      h33["inputs"].get("audio_vae") == ["24", 0], str(h33["inputs"].get("audio_vae")))
check("没有 first_frame / last_frame 这些 I2V 专有键",
      "first_frame" not in h33["inputs"] and "last_frame" not in h33["inputs"],
      str(sorted(h33["inputs"])))
check("传了 last_frame_name 也不会接到 r2v 上（Ref2VA 没有尾帧概念）",
      "last_frame" not in h3_node(
          p._build_workflow("P", 10.0, "a.png", last_frame_name="b.png"))[1]["inputs"])

# ---------- 2b) 上限是 **9 张**，不是 3 张（2026-09-27 查官方节点 schema 定死） ----------
# 官方 `comfy_extras/nodes_minimax_h3.py` 的 define_schema()：
#   io.Autogrow.Input("ref_images", template=io.Autogrow.TemplatePrefix(
#       input=io.Image.Input("ref_image", ...), prefix="ref_image_", min=0, max=9))
# → ref_images.ref_image_0 … _8，共 9 个。
# ⚠️ 之前这里只有"三张"一条用例，正好跟代码里那个**错的 3 张截断**互相印证、
#    一起把问题盖住了（两边都是照 UI 导出文件里连了 3 条反推的）。
print("\n-- r2v 上限：9 张（官方 schema） --")
wf9 = p._build_workflow("P", 10.0, "a.png", extra_refs=[f"r{i}.png" for i in range(1, 9)])
hid9, h39 = h3_node(wf9)
refs9 = {k: v for k, v in h39["inputs"].items() if k.startswith("ref_images")}
check("九张图接满 ref_image_0 … _8",
      refs9 == {f"ref_images.ref_image_{i}": (["100", 0] if i == 0 else [str(900 + i), 0])
                for i in range(9)},
      f"{len(refs9)} 个口")
check("九张的 LoadImage 图名逐个对得上",
      all(wf9[str(900 + i)]["inputs"]["image"] == f"r{i}.png" for i in range(1, 9)),
      str(sorted(k for k in wf9 if int(k) >= 900)))
check("**没有**多出第 10 个口（ref_image_9 不存在，ComfyUI 会拒单）",
      "ref_images.ref_image_9" not in h39["inputs"],
      str(sorted(k for k in h39["inputs"] if k.startswith("ref_images"))))

# ⚠️ 这个常量**必须用子进程读，不能在本进程里 `import app`**：
#    app.py 顶部会 `load_dotenv()`，把 `.env` 里的 H3_LORA / H3_STEPS / H3_MEGAPIXELS
#    灌进本进程。而本脚本上面已经按"没有 .env"的环境跑过几次 `_build_workflow` ——
#    无 H3_LORA 时 `_build_workflow` 会 **pop 掉节点 121**（LoRA 节点），有则保留。
#    同进程里前后环境不一致，下面那条"后半段节点集合与 i2v 一致"就会多出一个 121，
#    报成"采样器/解码被改坏了"（2026-09-27 踩过，排查了半天）。
import subprocess                                              # noqa: E402
_probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, 'server'); import app; print(app.REF2VA_MAX_REFS)"],
    cwd=ROOT, capture_output=True, text=True,
)
_val = ""
_lines = [ln for ln in (_probe.stdout or "").splitlines() if ln.strip()]
if _lines:
    _val = _lines[-1].strip()      # ⚠️ 取**最后一行**：app 导入时会打印「[启动] 从磁盘恢复 N 个历史任务」
check("server/app.py 的 REF2VA_MAX_REFS 也是 9（截断阈值与节点 schema 一致）",
      _val == "9", _val or (_probe.stderr or "")[-200:])

# ---------- 3) 两张共用后半段 ----------
print("\n-- 共用部分 --")


def nodes_only(wf: dict) -> set[str]:
    """只取**节点**键。

    ⚠️ `_` 开头的是给人看的备注（`_怎么来的` / `_说明` / `_调参指南` / `_必须的模型文件`），
    不是节点。拿全量键比节点集合的话，「r2v 那份多写了两条备注」会被误报成
    「后半段被改坏了」—— 报出来的 FAIL 会把人引去查采样器/解码，其实啥事没有。
    """
    return {k for k in wf if not k.startswith("_")}


same = nodes_only(wf1) - {"100", "104", "901", "902"}
base = nodes_only(provider(I2V)._build_workflow("P", 10.0, "a.png")) - {"100", "104"}
check("后半段节点集合与 i2v 一致（采样器/解码/存盘没被改坏）",
      same == base,
      f"r2v 独有：{sorted(same - base)}；i2v 独有：{sorted(base - same)}")
check("采样器从 H3 节点的 LATENT 槽（槽 1）取输入",
      wf1["14"]["inputs"]["latent_image"] == ["104", 1], str(wf1["14"]["inputs"]["latent_image"]))
check("guider 从 H3 节点的槽 0 取 conditioning",
      wf1["16"]["inputs"]["conditioning"] == ["104", 0], str(wf1["16"]["inputs"]["conditioning"]))

# ---------- 4) 旋钮照旧生效 ----------
print("\n-- 旋钮 --")
check("帧数吸附到 17k+5 的网格（10s→240→243）",
      wf1["104"]["inputs"]["length"] % 17 == 5, f"length={wf1['104']['inputs']['length']}")
check("像素预算用调用方给的值",
      wf1["119"]["inputs"]["megapixels"] == 0.9, str(wf1["119"]["inputs"]["megapixels"]))
check("seed 每调用一次都变",
      provider(R2V)._build_workflow("P", 10.0, "a.png")["15"]["inputs"]["noise_seed"]
      != provider(R2V)._build_workflow("P", 10.0, "a.png")["15"]["inputs"]["noise_seed"])
# ⚠️ 2026-09-19 修的那条：**六段式本身就是完整提示词**，不能再套基础模式的壳
#（官方 skills/h3-prompt-writing/references/ 里两套格式是并列的）。
SIX = ("subject_definitions: <Subject 1> is the person shown in <Picture 1>.\n\n"
       "summary: [reference generation] A short clip.\n\n"
       "retention_analysis: <Subject 1> (appears in every shot): fully_preserved - same face.\n\n"
       "detailed_description: [Shot 1] Live-action, a medium shot frames <Subject 1>.\n\n"
       "overall_soundscape: Room tone and footsteps.\n\n"
       "non_diegetic_music: N/A")
p6 = provider(R2V)._build_workflow(SIX, 10.0, "a.png")
prompt6 = p6["104"]["inputs"]["prompt"]
check("Ref2VA：六段式**原样**进 H3 节点（开头就是 subject_definitions）",
      prompt6.startswith("subject_definitions:") and prompt6 == SIX, prompt6[:70])
check("Ref2VA：不再多套基础模式的壳",
      "integrated_multimodal_description" not in prompt6,
      prompt6[:200])
check("Ref2VA：不再插「关键帧对齐指令」（它没有关键帧）",
      "fully referenced" not in prompt6 and "aligns with the" not in prompt6)
# 兜底：r2v 上来了个**基础格式**（旧分镜链路，正文没有 subject_definitions）→ 仍按三段拼
BASE = "[Shot 1] Live-action, a medium shot frames a woman opening a door."
pbase = provider(R2V)._build_workflow(BASE, 10.0, "a.png")
check("六段式误用在基础模式时只取 detailed_description 正文",
      "subject_definitions" not in vp._base_body(SIX)
      and vp._base_body(SIX).startswith("[Shot 1]"), vp._base_body(SIX)[:60])
check("基础格式的提示词仍走三段 + 对齐指令",
      "fully referenced" in pbase["104"]["inputs"]["prompt"]
      and "integrated_multimodal_description" in pbase["104"]["inputs"]["prompt"],
      pbase["104"]["inputs"]["prompt"][:70])

# ---------- 5) 模型文件与签名 ----------
print("\n-- 模型文件 / 签名 --")
r2v_raw = json.load(open(R2V, encoding="utf-8"))
need = r2v_raw["_必须的模型文件"]
found = {}
for v in r2v_raw.values():
    if isinstance(v, dict) and v.get("class_type") in ("UNETLoader", "CLIPLoader", "LoraLoaderModelOnly"):
        vals = [x for x in v["inputs"].values() if isinstance(x, str)]
        found[v["class_type"]] = vals[0]
check("UNET 是 ref2va 权重", found.get("UNETLoader") == need["unet"], str(found.get("UNETLoader")))
check("CLIP 是 nvfp4 文本编码器", found.get("CLIPLoader") == need["clip"], str(found.get("CLIPLoader")))
check("LoRA 是 ref2v 4step", found.get("LoraLoaderModelOnly") == need["lora"],
      str(found.get("LoraLoaderModelOnly")))
check("步数默认就是 4（配 4step LoRA）", r2v_raw["9"]["inputs"]["steps"] == 4,
      str(r2v_raw["9"]["inputs"]["steps"]))

# ⚠️ ref_image_size 是**必填**（2026-09-29 踩到）：官方给节点加了这个必填输入之后，
#    我们导出时还没有它 —— 出片直接 400 `Required input is missing`，而界面上只显示
#    "400 Bad Request"（那时的 _submit 把响应体丢了）。这条断言就是防它再漏。
h3_node = next((v for v in r2v_raw.values()
                if isinstance(v, dict) and v.get("class_type") == "MiniMaxH3ReferenceToVideo"), {})
check("H3 节点带了 ref_image_size（schema 里必填，缺了直接 400）",
      (h3_node.get("inputs") or {}).get("ref_image_size") in ("match", "max"),
      f"ref_image_size={(h3_node.get('inputs') or {}).get('ref_image_size')!r}（只能是 match / max）")

# _submit 拒单时要把 ComfyUI 的 node_errors 翻成人话 —— 从前只剩一句 "400 Bad Request"
class _FakeResp:
    status_code = 400
    text = ""
    def json(self):
        return {
            "error": {"type": "prompt_outputs_failed_validation",
                      "message": "Prompt outputs failed validation"},
            "node_errors": {"104": {"class_type": "MiniMaxH3ReferenceToVideo", "errors": [
                {"type": "required_input_missing",
                 "message": "Required input is missing: ref_image_size",
                 "extra_info": {"input_name": "ref_image_size"}}]}},
        }

_msg = vp.ComfyUIVideoProvider._prompt_error_text(_FakeResp())
check("400 的报错里带上了「哪个节点缺什么」（不再只有一句 Bad Request）",
      "104" in _msg and "ref_image_size" in _msg and "Required input is missing" in _msg,
      _msg.replace("\n", " | "))

sig_c = inspect.signature(vp.ComfyUIVideoProvider.generate)
sig_a = inspect.signature(vp.ApiVideoProvider.generate)
check("两个 provider 的 generate 签名一致（老坑：少一个参数就 TypeError）",
      list(sig_c.parameters) == list(sig_a.parameters),
      f"comfyui={list(sig_c.parameters)} / api={list(sig_a.parameters)}")
check("两个签名都带 ref_image_paths", "ref_image_paths" in sig_a.parameters)

bad = results.count(False)
print("\n" + "=" * 74)
print(f"结果：{len(results) - bad}/{len(results)} 通过" + ("  ← 有 FAIL" if bad else ""))
print("=" * 74)
sys.exit(1 if bad else 0)

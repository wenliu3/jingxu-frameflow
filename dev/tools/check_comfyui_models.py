"""诊断：实例上的 ComfyUI **缺哪些模型文件 / 哪些节点**，并直接给出下载命令。

为什么需要它：
    「出片失败」最常见的原因是实例上少了模型文件（换模式要换权重，见 docs/REF2VA.md）。
    从前只有一条线索 —— 出片时 `video_provider._preflight_models` 抛的那句
    「ComfyUI 上找不到这些模型文件：…」。可它只在**提交那一刻**才说话，而且
    ① 你手上得有素材、② 得真的点了生成才看得到。
    这个脚本把同一件事提前到"一条命令"，并且正着查：**实例上到底有什么**。

它做四件事（只读，不写任何文件、不碰任何模型）：
    ① 读服务配置里的 ComfyUI 地址（和出片用的是同一个，不另填）
    ② 把工作流 JSON 里用到的模型文件逐个对着实例的清单点名
       （UNETLoader→diffusion_models、CLIPLoader→text_encoders、
         LoraLoaderModelOnly→loras、VAELoader→vae）
    ③ **照着实例报的节点 schema 逐项核对输入**（缺必填 / 枚举值不在列表 / 多余字段）
       —— ComfyUI 在 POST /prompt 时做的是同一套校验，不过它把结果塞在 400 的响应体里，
          从前那具响应体被丢掉了，用户只看到一句 "400 Bad Request"（见下方那次的教训）
    ④ 缺哪个就打印**能直接粘到实例终端**的下载命令

⚠️ 为什么要有 ③（2026-09-29 的教训）：节点 schema 是可以变的 —— 那次官方给
`MiniMaxH3ReferenceToVideo` 加了一个**必填**输入 `ref_image_size`，我们导出工作流时它还不存在，
于是出片直接 400 `Required input is missing`，而在镜序界面上只显示"400 Bad Request"，
查了半天。这类问题**不用等到出片**，对着 /object_info 比一遍就知道。

用法（在项目根）：
    python dev/tools/check_comfyui_models.py              # 查当前配置的那份工作流
    python dev/tools/check_comfyui_models.py ref2va       # 指定 i2v / ref2va
    python dev/tools/check_comfyui_models.py --all        # 两份都查
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, ROOT)

import requests  # noqa: E402

import app as server_app  # noqa: E402

# 节点类型 → (取哪个字段, 在 ComfyUI 的哪个模型目录)
_MODEL_INPUTS = (
    ("UNETLoader", "unet_name", "diffusion_models"),
    ("CLIPLoader", "clip_name", "text_encoders"),
    ("LoraLoaderModelOnly", "lora_name", "loras"),
    ("VAELoader", "vae_name", "vae"),
)

# 工作流文件 → 它是哪条链路（与 server/app.py 的 video_workflow 取值一一对应）
WORKFLOWS = {
    "i2v": os.path.join(ROOT, "comfyui", "h3_i2v_api.json"),
    "ref2va": os.path.join(ROOT, "comfyui", "h3_r2v_api.json"),
}

# 官方仓库（魔搭与 HuggingFace 同名）。下载命令按这个拼。
_REPO = "Comfy-Org/MiniMax-H3"
# 实例上的 ComfyUI 目录（deploy_comfyui_ms.sh / deploy_comfyui.sh 都装在这里）
_INSTANCE_DIR = "/mnt/workspace/ComfyUI"


def _wanted(workflow: dict) -> list[tuple[str, str, str, str]]:
    """这份工作流要用到的模型文件 → [(节点类型, 字段名, 目录, 文件名)]，去重后排序。"""
    out: list[tuple[str, str, str, str]] = []
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        for cls, field, folder in _MODEL_INPUTS:
            if str(node.get("class_type")) != cls:
                continue
            name = str((node.get("inputs") or {}).get(field) or "").strip()
            row = (cls, field, folder, name)
            if name and row not in out:
                out.append(row)
    return sorted(out, key=lambda r: (r[2], r[3]))


def _available(base: str, cls: str, field: str) -> list[str] | None:
    """实例上这个节点当前能选的文件名。查不动返回 None（网络/版本问题，不假装知道）。"""
    try:
        resp = requests.get(f"{base}/object_info/{cls}", timeout=30)
        resp.raise_for_status()
        req = ((resp.json().get(cls) or {}).get("input") or {}).get("required") or {}
        opts = req.get(field) or []
        return list(opts[0]) if opts and isinstance(opts[0], list) else []
    except Exception:  # noqa: BLE001 - 查不动就如实说查不动
        return None


# 有些输入是**出片时才填**的，静态检查一定误报，跳过：
#   LoadImage.image —— video_provider 会先把图上传到 ComfyUI，再把这个字段改成
#                      上传后的文件名（见 _upload / _build_workflow）。
# 模型文件那几个字段（unet_name / clip_name / …）也跳过：下面「模型文件点名」那一步
# 查得更细（还会给下载命令），这里再报一遍只是噪音。
_SKIP_INPUTS = {("LoadImage", "image")} | {(cls, field) for cls, field, _ in _MODEL_INPUTS}


def _check_inputs(info: dict, workflow: dict) -> list[str]:
    """照着实例报的节点 schema 逐项核对输入，返回问题清单。

    ComfyUI 提交时做的是同一套校验，只是把结果放进 400 的响应体里 ——
    这里提前查出来，省得点了出片才发现（见文件头的教训）。
    """
    problems: list[str] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        spec = info.get(cls)
        if not spec:
            problems.append(f"{cls} #{nid}：这个节点类型在实例上不存在")
            continue
        blocks = spec.get("input") or {}
        req = blocks.get("required") or {}
        opt = blocks.get("optional") or {}
        given = node.get("inputs") or {}
        for name in req:
            if name not in given and (cls, name) not in _SKIP_INPUTS:
                problems.append(f"{cls} #{nid}：**缺必填输入** {name}")
        for name, val in given.items():
            if (cls, name) in _SKIP_INPUTS:
                continue
            if name in req:
                sv = req[name]
            elif name in opt:
                sv = opt[name]
            else:
                # Autogrow：`ref_images.ref_image_0` 这种实例化出来的口，
                # 前缀（ref_images）在 schema 里就算它合法
                if name.split(".")[0] in req or name.split(".")[0] in opt:
                    continue
                problems.append(f"{cls} #{nid}：输入 {name} 不在节点 schema 里")
                continue
            kind = sv[0] if isinstance(sv, list) and sv else sv
            if isinstance(kind, list) and not isinstance(val, list) and val not in kind:
                problems.append(f"{cls} #{nid}：{name}={val!r} 不在可选值里（{kind[:6]}…）")
    return problems


def check(base: str, label: str, path: str) -> int:
    """查一份工作流，返回缺了几项（节点 + 模型文件 + 输入 schema）。"""
    print(f"\n{'=' * 74}\n{label}   {os.path.relpath(path, ROOT)}\n{'=' * 74}")
    if not os.path.isfile(path):
        print(f"  ❌ 工作流文件不存在：{path}")
        return 1
    with open(path, encoding="utf-8") as fh:
        workflow = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}

    # ---- 节点齐不齐（缺节点的话，下多少模型都没用） ----
    try:
        all_nodes = requests.get(f"{base}/object_info", timeout=180).json()
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 连不上实例（{base}）：{type(exc).__name__}: {exc}")
        return 1
    used = sorted({str(v["class_type"]) for v in workflow.values()
                   if isinstance(v, dict) and v.get("class_type")})
    node_missing = [c for c in used if c not in all_nodes]
    print(f"  节点：用到 {len(used)} 个，缺 {len(node_missing)} 个"
          + (f" → {node_missing}" if node_missing else " ✅"))
    if node_missing:
        print("     （缺节点要去实例上 `cd /mnt/workspace/ComfyUI && git pull` 升级，下模型没用）")

    # ---- 输入 schema（缺必填 / 枚举值不对 —— 这就是 ComfyUI 那个 400 的内容） ----
    input_problems = _check_inputs(all_nodes, workflow)
    if input_problems:
        print(f"  输入校验：❌ {len(input_problems)} 处对不上（点出片会直接 400）")
        for x in input_problems:
            print(f"     · {x}")
    else:
        print("  输入校验：✅ 与实例的节点 schema 完全对得上")

    # ---- 模型文件逐个点名 ----
    missing: list[tuple[str, str]] = []
    unknown = 0
    for cls, field, folder, name in _wanted(workflow):
        opts = _available(base, cls, field)
        if opts is None:
            unknown += 1
            print(f"  ❓ {folder}/{name}（实例没回清单，查不动）")
        elif name in opts:
            print(f"  ✅ {folder}/{name}")
        else:
            missing.append((folder, name))
            print(f"  ❌ {folder}/{name}   ← 实例上没有")

    # ---- 缺的怎么下 ----
    if missing:
        print(f"\n  缺 {len(missing)} 个文件。在**实例的终端**里执行（幂等，已存在会跳过）：\n")
        print(f"    cd {_INSTANCE_DIR}")
        for folder, name in missing:
            print(f"    modelscope download --model {_REPO} {folder}/{name} --local_dir models")
        print("\n  自建 / 租卡环境把 `modelscope download --model X Y --local_dir Z` 换成：")
        print("    huggingface-cli download X Y --local-dir Z")
        print("\n  下完在 ComfyUI 页面刷新一下（让它重新扫模型目录），再回镜序点生成。")
    elif not unknown and not node_missing and not input_problems:
        print("\n  ✅ 这份工作流要的东西实例上全有，可以直接出片。")

    return len(missing) + len(node_missing) + len(input_problems)


def main(argv: list[str]) -> int:
    cfg = server_app._load_service_config()
    base = str(cfg.get("comfyui_url") or "").strip().rstrip("/")
    if not base:
        print("❌ 服务配置里 ComfyUI 地址是空的（去「服务配置」填，或看 docs/comfyui-tunnel-guide.md）")
        return 1

    print(f"ComfyUI 实例：{base}")
    try:
        stats = requests.get(f"{base}/system_stats", timeout=20).json()
        dev = (stats.get("devices") or [{}])[0]
        print(f"在线 ✅  {dev.get('name')} | VRAM {(dev.get('vram_total') or 0) / 2 ** 30:.1f} GB")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 连不上：{type(exc).__name__}: {exc}")
        print("   隧道地址 60 分钟一换，先看 docs/comfyui-tunnel-guide.md 换一个再试。")
        return 1

    arg = (argv[1] if len(argv) > 1 else "").strip()
    if arg == "--all":
        picks = list(WORKFLOWS.items())
    else:
        current = str(cfg.get("video_workflow") or "i2v")
        if arg and arg not in WORKFLOWS:
            print(f"⚠️ 不认识的参数 {arg!r}，按当前配置查 {current}")
            arg = ""
        if arg:
            picks = [(arg, WORKFLOWS[arg])]
        else:
            picks = [(current, WORKFLOWS[current])]
            print(f"当前「视频工作流」配置 = {current}（要查另一份就传 i2v / ref2va，或 --all）")

    bad = 0
    for label, path in picks:
        bad += check(base, f"【{label}】", path)
    print(f"\n{'=' * 74}")
    print("结论：" + ("全部就绪 ✅" if not bad else f"有 {bad} 项缺失 ❌（上面有下载命令）"))
    print("=" * 74)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

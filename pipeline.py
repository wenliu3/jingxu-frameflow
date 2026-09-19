"""顺序编排。

不做状态图、不做任务队列。四个阶段顺序执行，任意阶段失败就整次退出。

唯一一点额外的工程投入是 prompt 哈希缓存。理由：ModelScope 的图像模型日额度很小，
而调提示词时会反复对同一个分镜出图——没有缓存，一个下午就能把额度烧光。
缓存键包含模型名，所以换了模型不会错误命中旧图。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
from typing import Callable

# stdout 被重定向到文件时默认是块缓冲，进度会攒在缓冲区里不刷出来，
# 从外面看就像"卡死了"。这里改成行缓冲，每行立即落盘。
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # stdout 被替换成非 TextIOWrapper 时静默跳过
    pass

from dotenv import load_dotenv

load_dotenv()

import agents  # noqa: E402  （必须在 load_dotenv 之后导入）
import tts  # noqa: E402
from image_provider import (  # noqa: E402
    DEFAULT_NEGATIVE,
    SIZE_TABLE,
    ImageProvider,
    create_provider,
)
from schemas import Project, Shot, dump  # noqa: E402


def _cache_key(model: str, prompt: str, negative: str, size: str) -> str:
    raw = f"{model}|{size}|{prompt}|{negative}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _stable_seed(key: str) -> int:
    """由稳定的字符串派生一个稳定 seed（同 key 永远同 seed）。

    多视角一致性靠它：同一角色的四张定妆照、同一道具的两张图共用**同一个 seed**，
    prompt 只差视角描述，模型就会被同一个噪声起点锚住身份。
    同一角色重跑（不换 seed）会拿到近乎一样的图，所以"重新生成"必须换 seed
    ——否则用户点重生成会看到一模一样的四张，以为按钮坏了。
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) % (2**31 - 1)


def render_one(
    shot: Shot,
    provider: ImageProvider,
    cache_dir: str,
    img_dir: str,
    size: str,
    force: bool = False,
    name: str | None = None,
) -> tuple[str, bool]:
    """给单个分镜出图，返回 (图片路径, 是否命中缓存)。

    name 是落盘文件名（不含扩展名），默认 shot_XX——块首帧图复用同一套缓存，
    传 name="block_XX" 即可，不重复造一套渲染。

    force=True 绕过缓存。**编辑分镜后点「重新出图」必须传 force**：
    缓存键只包含 prompt / negative / size / model，如果用户只改了景别、运镜、时长
    这类不进 prompt 的字段，缓存键不变，会直接返回旧图——看起来就像"重生成失灵"。
    """
    target = os.path.join(img_dir, f"{name or f'shot_{shot.shot_id:02d}'}.png")
    negative = shot.negative_prompt or DEFAULT_NEGATIVE
    cached = os.path.join(
        cache_dir, _cache_key(provider.model, shot.visual_prompt, negative, size) + ".png"
    )

    # 两个目录都要确保存在。provider.generate 内部只会创建它自己写的那一层
    # （images/），cache/ 不会——少了这句，copyfile 会在写缓存时 FileNotFoundError。
    for d in (img_dir, cache_dir):
        os.makedirs(d, exist_ok=True)

    if not force and os.path.exists(cached):
        shutil.copyfile(cached, target)
        hit = True
    else:
        provider.generate(shot.visual_prompt, target, negative, size)
        shutil.copyfile(target, cached)
        hit = False

    shot.image_path = target
    return target, hit


async def _render_all(
    shots: list[Shot],
    provider: ImageProvider,
    cache_dir: str,
    img_dir: str,
    size: str,
    concurrency: int,
    on_progress: Callable[[str, dict], None] | None = None,
) -> dict[str, int]:
    """并发出图，带信号量限流。限流是必要的：一次打光日额度就无法回滚。

    每张图完成就回调一次 on_progress，前端因此能逐张显示，而不是等全部出完。
    """
    sem = asyncio.Semaphore(concurrency)
    stats = {"api": 0, "cache": 0}

    async def one(shot: Shot) -> None:
        async with sem:
            path, hit = await asyncio.to_thread(
                render_one, shot, provider, cache_dir, img_dir, size, False,
            )
            stats["cache" if hit else "api"] += 1
            print(f"      #{shot.shot_id:02d} ok  {os.path.basename(path)}")
            if on_progress:
                on_progress("shot_done", {"shot_id": shot.shot_id, "path": path})

    await asyncio.gather(*(one(s) for s in shots))
    return stats


# 定妆照的四个视角。切到 Ref2VA 后，参考图要能覆盖面部特征、身体轮廓和体型比例，
# 单个正面半身像不足以让模型在侧向、背向的构图里保持同一个人。
# 元组第二个元素是文件名后缀——正面沿用旧命名（无后缀），旧任务能直接复用已生成的正面图，
# 只补另外三个视角，不必把额度重烧一遍。
# 第四个元素是英文视角描述：FLUX 系文本编码器只吃英文，中文 prompt 对它是噪音
# （实测中文 prompt 进 FLUX.2 出来的是完全无关的画面）。
PORTRAIT_VIEWS = (
    ("front", "", "正面半身像，面向镜头，表情自然",
     "front-facing half-body portrait, facing the camera, natural expression"),
    ("side", "_side", "侧面全身像，正侧面 90 度，完整呈现身体轮廓与侧脸剪影",
     "full-body side view at exactly 90 degrees, showing the full body silhouette and facial profile"),
    ("back", "_back", "背面全身像，背对镜头，呈现发型背面与服装后片",
     "full-body view from behind, showing the back of the hair and clothing"),
    ("full", "_full", "正面全身像，从头顶到脚完整入画，呈现体型比例与整套服装",
     "front-facing full-body portrait, head to feet fully in frame, showing body proportions and the complete outfit"),
)


def _is_flux(model_name: str) -> bool:
    """FLUX 系模型只吃英文 prompt（guidance-distilled，英文语料文本编码器）。"""
    return "flux" in (model_name or "").lower()


# ⚠️ 定妆照专用负向词。**不要**加进 image_provider.DEFAULT_NEGATIVE ——
# 那个是场景/道具/其他图片共用的，而道具的「三视图」本身就是分格图，
# 全局加"不要分格"会把道具设定图一起打死。所以只在角色单张这一路挂。
#
# ⚠️ 2026-09-19：光禁「分格 / 拼贴」还不够。实锤：选 16:9（1024×576）出单张定妆照时，
# 模型仍把画面排成了「正面全身 + 正面全身 + 背面」三格（莫卡那张就是），宽画幅本身
# 就在勾引它铺开排多格。所以再补「三格 / 三视图 / 横向并排 / 并排人像 / character lineup /
# side-by-side views」。真正的保证还是"画幅别贪宽 + 出来不对就再生成一次"（每次换 seed）。
PORTRAIT_NEGATIVE = (
    DEFAULT_NEGATIVE
    + ", grid, collage, contact sheet, split screen, multiple panels, multiple views, "
      "character turnaround sheet, character lineup, side-by-side views, "
      "duplicated figure, two people, clone, "
      "分格, 拼贴, 多格, 多视角, 四格, 三格, 三视图, 九宫格, 横向并排, 并排人像, "
      "重复人物, 两个人物, 同一个人画两遍, 边框"
)

# ⚠️ 设定图专用负向词：版式是「16:9 横版四格」（左格大特写 + 正面/侧面/背面三个全身），
# 所以禁的是「走成竖排两栏」和「格数不对 / 重复视角 / 左格画成全身」
# （莫卡那张竖版两栏时就是把第 1、2 格画成了同一个正面全身，特写整格丢掉）。
SHEET_NEGATIVE = (
    DEFAULT_NEGATIVE
    + ", two panels, three panels, vertical two-column layout, stacked panels, "
      "character turnaround, turnaround sheet, three-view sheet, no close-up, three full bodies, "
      "duplicated view, repeated angle, same pose twice, full body in the left panel, "
      "text, caption, watermark, label, numbering, "
      "两格, 三格, 竖排两栏, 左右两栏, 上下分栏, 三视图, 转身图, 三张全身像, 全身三连, "
      "缺少面部特写, 重复视角, 同一个角度画两遍, 左格画成全身, 文字, 标注, 编号, 水印"
)


def _character_portrait_prompt(style: str, anchor: str, view_desc: str) -> str:
    """单张定妆照的提示词（中文版：Z-Image / Qwen 系）。

    view_desc 是唯一的变量——背景、光线、风格各张完全一致，
    这样除了角度以外没有别的差异，便于核对是不是同一个角色。

    刻意用「均匀柔光无阴影」而不是「电影感柔光」：定妆照是给模型看的参考件，
    不是艺术照。戏剧性光影会把服装颜色和面部结构埋进阴影里，参考价值反而下降。

    ⚠️ 2026-09-19 重写（旧版有实锤缺陷）。旧版写着
    「人物特征（以下每一项**在四张图中**必须完全一致）」+「这是同一角色的**多视角设定图**
    中的一张」，本意是让各视角保持同一个人，**实际被模型读成「输出一张多格拼图」**：
    实测 `金鳞.png` / `莫卡.png`（本该是 1:1 单张正面半身）都变成了 1024×576 的四格拼图，
    `紊流.png` 变成 2×2 四格。而拼图会被视频模型读成「画面里有四个人」——
    代码在别处特意防过这件事（"整张设定图不能进 images"），结果被单张模板自己绕过去了。

    重写原则：**一个「张 / 图 / 格」的数量词都不出现**。
    同一件事改用「与角色设定保持一致」表达；并明确写死
    「整幅画面只有这一个人物、只呈现这一个视角、不分格不拼贴」。
    一致性真正靠的是**同一个 seed + 逐字相同的 anchor**（旧注释列的第 ② 条其实是误判）。
    """
    style_part = f"整体视觉风格：{style}。" if style else ""
    return (
        f"单人角色定妆照。人物特征：{anchor}。"
        f"{view_desc}，纯浅灰色干净背景，均匀柔光无阴影，细节清晰，无遮挡。"
        f"整幅画面只有这一个人物、只呈现这一个视角，人物居中、完整入画，"
        f"面部五官、发型发色、体型比例、服装款式与颜色、配饰细节与角色设定保持一致。"
        f"画面不分格、不拼贴、不做多视角排版、不重复同一个人物，"
        f"不出现任何文字、水印、logo、边框、编号或标注。"
        f"{style_part}"
    )


def _character_portrait_prompt_en(style_en: str, anchor_en: str, view_desc_en: str) -> str:
    """英文版定妆照提示词（FLUX.2-dev 等英文语料模型专用）。"""
    style_part = f"Overall visual style: {style_en}." if style_en else ""
    return (
        f"Character reference sheet: {anchor_en}. Single person, {view_desc_en}, "
        "plain light-gray seamless background, even soft lighting with no harsh shadows, "
        f"sharp details, nothing occluded. {style_part}"
    )


def _safe_char_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())
    return cleaned.strip("_") or "character"


# 道具的两个视角。道具不像人物有「背面」的信息增量，正/侧覆盖大多数分镜构图；
# 需要细节特写时再加第三个视角，先不过度设计
PROP_VIEWS = (
    ("front", "_front", "主视图（正对镜头）"),
    ("side", "_side", "侧视图（从正右侧 90 度观察）"),
)


def _prop_prompt(style: str, anchor: str, view_desc: str = "主视图（正对镜头）") -> str:
    """道具设定图提示词（中文版）。

    与角色同理：2026-09-14 起两视角一致性靠「逐字相同的 anchor + 同一 seed」，
    所以模板里明说这是同一道具的多视角图，并把要锁的特征逐项列出来。
    """
    style_part = f"整体视觉风格：{style}。" if style else ""
    return (
        f"道具设定图。道具特征（以下每一项在两张图中必须完全一致）：{anchor}。"
        f"单一道具居中，{view_desc}，纯浅灰色干净背景，"
        f"均匀柔光无阴影，细节清晰，无遮挡。"
        f"这是同一道具的多视角设定图中的一张：材质、颜色、形状、尺寸比例、"
        f"磨损与做旧细节必须与其他视角完全一致，画面中是同一个道具。"
        f"{style_part}"
    )


def _scene_prompt(style: str, anchor: str) -> str:
    style_part = f"整体视觉风格：{style}。" if style else ""
    return (
        f"场景设定图，空镜环境全景，画面中不出现任何人物：{anchor}。"
        f"空间结构清晰，光源方向明确，细节丰富。{style_part}"
    )


def _free_image_prompt(style: str, desc: str) -> str:
    """「其他图片」AI 生成用的画面提示词（中文）。

    与 `_scene_prompt` / `_prop_prompt` 是三种不同的东西，别混用：
      - `_scene_prompt`：空镜环境设定图，**强制无人**，给场景锚用
      - `_prop_prompt` ：纯浅灰底的单件道具，给多视角设定图用
      - 本条          ：**一整张完整画面**，给「其他图片」用

    为什么单独一条：「其他图片」在流程里是首尾帧与 Ref2VA 参考图的来源
    （见 schemas.Shot.image_path / video_provider 的 FL2VA 分支），要的是能直接
    当一帧用的画面 —— 环境、光影、构图都得成立，而不是"抠出来的一件东西"。
    所以这里既不压人物，也不要求纯色背景。
    """
    style_part = f"整体视觉风格：{style}。" if style else ""
    return (
        f"单幅完整画面，构图完整、光影统一：{desc}。"
        f"主体清晰，环境与背景具体可辨，前中后景层次分明，细节丰富。{style_part}"
    )


def _prop_prompt_en(style_en: str, anchor_en: str) -> str:
    style_part = f"Overall visual style: {style_en}." if style_en else ""
    return (
        f"Prop reference sheet: {anchor_en}. A single prop centered in frame, "
        "plain light-gray seamless background, even soft lighting with no harsh shadows, "
        "sharp details, nothing occluded. This is one of several views of the same prop: "
        "material, color, shape, proportions and wear must match the other views exactly. "
        f"{style_part}"
    )


def _scene_prompt_en(style_en: str, anchor_en: str) -> str:
    style_part = f"Overall visual style: {style_en}." if style_en else ""
    return (
        f"Environment reference sheet, empty establishing shot with no people: {anchor_en}. "
        "Clear spatial structure, well-defined light direction, rich detail. "
        f"{style_part}"
    )


def _gen_assets(project, out_dir, on_progress=None, seed_salt: str = "") -> None:
    """生成道具与场景素材图，逐张判存、缺哪张补哪张（与定妆照同规则）。

    道具：主视图 + 侧视图，**两张都是纯文生图**，共用同一个 seed，
    prompt 只差视角描述 —— 这是 2026-09-14 取消图生图后的替代方案。
    场景：单张全景，尺寸跟随项目画幅——场景图是环境参考，比例应与最终视频一致，
    否则模型会从横图参考里想象出竖图画面。

    seed_salt 非空时换一批 seed（素材「重新生成」用；同 seed 重跑会得到近乎一样的图）。
    """
    if not project.assets:
        return
    size_square = SIZE_TABLE.get("1:1", "1024x1024")
    size_scene = SIZE_TABLE.get(project.aspect_ratio, "1024x576")
    adir = os.path.join(out_dir, "assets")
    os.makedirs(adir, exist_ok=True)
    for a in project.assets:
        # 「其他图片 / 其他音频」是用户自己上传的原始素材，**不生成** ——
        # 没有图像 API 的用户全靠它们，程序不能反过来去调模型把用户的东西盖掉
        if a.kind in ("image", "audio"):
            continue
        base = _safe_char_name(a.name)
        if not a.anchor:
            print(f"      ⚠️ 素材 {a.name} 缺少 anchor，跳过生成")
            continue
        # 整个素材（正/侧两视角）共用一个 seed，模型才会把它当成同一个物体
        seed = _stable_seed(f"asset|{a.kind}|{a.name}|{a.anchor}|{seed_salt}")
        # FLUX 系只吃英文，缺英文锚点就走中文模型（Z-Image 等）
        provider = create_provider()
        use_flux = bool(getattr(a, "anchor_en", "")) and _is_flux(provider.model)

        if a.kind == "scene":
            path = os.path.join(adir, f"scene_{base}.png")
            if os.path.isfile(path):
                a.images = [path]
                continue
            try:
                if use_flux:
                    print(f"      场景 {a.name} 生成中（FLUX 系 · 英文 prompt）")
                    provider.generate(
                        _scene_prompt_en(project.style_en, a.anchor_en), path,
                        size=size_scene, seed=seed,
                    )
                else:
                    print(f"      场景 {a.name} 生成中…")
                    provider.generate(
                        _scene_prompt(project.style, a.anchor), path,
                        negative_prompt=DEFAULT_NEGATIVE, size=size_scene, seed=seed,
                    )
                a.images = [path]
                if on_progress:
                    on_progress("asset_done", {"kind": a.kind, "name": a.name, "path": path})
            except Exception as exc:
                print(f"      ⚠️ 场景失败 {a.name}：{exc}")
            continue

        # 道具：逐视角，同一 seed
        done: list[str] = []
        for key, suffix, view_desc in PROP_VIEWS:
            path = os.path.join(adir, f"prop_{base}{suffix}.png")
            if os.path.isfile(path):
                done.append(path)
                continue
            try:
                if use_flux:
                    print(f"      道具 {a.name} · {key} 生成中（FLUX 系 · 英文 prompt）")
                    provider.generate(
                        _prop_prompt_en(project.style_en, a.anchor_en), path,
                        size=size_square, seed=seed,
                    )
                else:
                    print(f"      道具 {a.name} · {key} 生成中…")
                    provider.generate(
                        _prop_prompt(project.style, a.anchor, view_desc), path,
                        negative_prompt=DEFAULT_NEGATIVE, size=size_square, seed=seed,
                    )
                done.append(path)
                if on_progress:
                    on_progress("asset_done", {"kind": a.kind, "name": a.name, "path": path})
            except Exception as exc:
                print(f"      ⚠️ 道具失败 {a.name}·{key}：{exc}")
        a.images = done


def _gen_character_portraits(project, out_dir, on_progress=None, seed_salt: str = "") -> None:
    """给每个角色生成四个视角的定妆照（1:1），逐张判存、缺哪张补哪张。

    为什么是四个视角：切到 Ref2VA（全能参考）后，参考图要能覆盖面部特征、身体轮廓
    和体型比例，单个正面半身像不足以让模型在侧向、背向的构图里保持同一个人。
    I2VA 模式下只用得上第一张（正面半身），多出来的三张不影响它。

    ⚠️ 四视角是 **4 倍图像额度消耗**（图像模型走 ModelScope API，日额度很小）。
    所以每张都先判存：已存在的直接复用，只补缺的那些——中途失败、重跑、
    以及从旧任务升上来的单张定妆照，都不会被重烧一遍。

    **一致性怎么保证（2026-09-14 起，没有参考图了）**：四张共用同一个 seed +
    逐字相同的 anchor/style，只有视角描述那一句在变。
    `_stable_seed` 让同一角色每次重跑都落在同一个 seed 上（可复现），
    seed_salt 非空时换一批（「重新生成」用，否则同 seed 出来的四张跟上次几乎一样）。
    """
    size = SIZE_TABLE.get("1:1", "1024x1024")
    cdir = os.path.join(out_dir, "characters")
    os.makedirs(cdir, exist_ok=True)
    provider = create_provider()
    use_flux = _is_flux(provider.model)
    if use_flux:
        print("      图像模型是 FLUX 系 → 定妆照用英文 prompt（中文对它是噪音）")
    for c in project.characters:
        base = _safe_char_name(c.name)
        front_path = os.path.join(cdir, f"{base}.png")
        # 缺英文锚点就不走英文 prompt：宁可换语言也不能拿错误语言喂模型
        char_use_flux = use_flux and bool(getattr(c, "anchor_en", ""))
        if use_flux and not char_use_flux:
            print(f"      角色 {c.name} 缺英文锚点 → 该角色退回中文 prompt")
        # 四视角共用：模型把「同一个 seed + 只差视角描述」当作同一个人
        seed = _stable_seed(f"character|{c.name}|{c.anchor}|{seed_salt}")
        done: list[str] = []
        for key, suffix, view_desc, view_desc_en in PORTRAIT_VIEWS:
            path = os.path.join(cdir, f"{base}{suffix}.png")
            if os.path.isfile(path):
                done.append(path)   # 已有就复用，不重复烧额度
                continue
            try:
                if char_use_flux:
                    prompt = _character_portrait_prompt_en(
                        project.style_en, c.anchor_en, view_desc_en
                    )
                    print(f"      角色定妆照 {c.name} · {key} 生成中（英文 prompt）")
                    provider.generate(prompt, path, size=size, seed=seed)
                else:
                    prompt = _character_portrait_prompt(project.style, c.anchor, view_desc)
                    print(f"      角色定妆照 {c.name} · {key} 生成中…")
                    provider.generate(
                        prompt, path, negative_prompt=PORTRAIT_NEGATIVE, size=size, seed=seed
                    )
                done.append(path)
                if on_progress:
                    on_progress("character_done", {"name": c.name, "view": key, "path": path})
            except Exception as exc:
                print(f"      ⚠️ 角色定妆照失败 {c.name}·{key}：{exc}")
        c.images = done
        # image_path 始终指向正面那张；正面失败时才退回第一张成功的
        c.image_path = front_path if os.path.isfile(front_path) else (done[0] if done else "")


def _gen_voice_samples(project, out_dir, on_progress=None) -> None:
    """给每个角色造一段音色样本（3-7 秒），逐张判存、缺哪个补哪个。

    产物落在 `characters/voice_<角色名>.mp3`，用途是给 **Ref2VA 的 `ref_audios`**
    当音色参考，让 H3 在整片里用同一个音色说话。

    ⚠️ 它**不进当前 I2VA/FL2VA 链路** —— 那两个工作流没有音频输入口，所以现在生成
    出来是先备着的，等云端换上 ref2va 权重 + Ref2VA 工作流才吃得上。
    环境音 / 配乐也不归它管，那是 H3 的 `overall_soundscape` 干的。

    失败只记警告、不阻断：edge-tts 走的是微软的在线服务，断网或被墙都会挂，
    而音色样本属于"锦上添花"的资产，不该让整个资产准备阶段陪葬。
    """
    if not project.characters:
        return
    cdir = os.path.join(out_dir, "characters")
    os.makedirs(cdir, exist_ok=True)
    # 统一分配，保证同片内音色不重复（标准普通话只有 6 个，撞了就同性别轮转）
    assigned = tts.assign_voices(project.characters)
    print(f"      音色后端：{tts.provider_label()}")
    for c in project.characters:
        # assign_voices 已保证：在当前后端池子里的 id 原样保留；
        # 换过后端、id 不在池子里的会被重挑（自动迁移，别在这里再 or 一次旧值）
        c.tts_voice = assigned.get(c.name, "") or tts.default_voice()
        # voice 描述里没有性别线索时，音色是按轮转分配的 —— 可能分到异性音色。
        # 这里明说一句，别让它悄悄错下去（界面上也能看到音色名并手动改）
        if not tts.pick_voice(c.voice or ""):
            print(
                f"      ⚠️ 角色 {c.name} 的 voice 描述里没有性别线索"
                f"（当前：{c.voice or '未设定'}）→ 音色按轮转分配为"
                f"「{tts.voice_label(c.tts_voice)}」，建议人工确认"
            )
        fname = tts.sample_filename(c.name)
        path = os.path.join(cdir, fname)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            c.voice_sample = fname    # 已有就复用，不重复合成
            continue
        try:
            print(f"      音色样本 {c.name} 生成中（{tts.voice_label(c.tts_voice)}）…")
            tts.synth_sample(c.tts_voice, path)
            c.voice_sample = fname
            if on_progress:
                on_progress("voice_done", {"name": c.name, "voice": c.tts_voice, "file": fname})
        except Exception as exc:
            print(f"      ⚠️ 音色样本 {c.name} 失败（不阻断）：{type(exc).__name__}: {exc}")


def stage_director(
    user_input: str,
    out_dir: str,
    shot_count: int | None = None,
    ratio: str | None = None,
    on_progress: Callable[[str, dict], None] | None = None,
    characters: list[dict] | None = None,
    source_text: str | None = None,
) -> dict:
    """阶段一：导演。产出故事设定 + 角色锚点 + 角色定妆照，不含分镜和分镜图。

    characters / source_text 来自前端：预配置角色与小说改编两种输入模式。
    """

    def emit(stage: str, **info) -> None:
        if on_progress:
            on_progress(stage, info)

    os.makedirs(out_dir, exist_ok=True)
    print("[阶段一] 导演 Agent · 生成故事设定与角色锚点")
    emit("stage", step=1, label="生成故事设定")
    project = agents.director(
        user_input, characters=characters, source_text=source_text
    )
    if ratio:
        project.aspect_ratio = ratio
    print(f"      标题：{project.title}")
    print(f"      风格：{project.style}")
    for c in project.characters:
        print(f"      角色 {c.name}：{c.anchor}")

    _gen_character_portraits(project, out_dir, on_progress)
    _gen_assets(project, out_dir, on_progress)
    _gen_voice_samples(project, out_dir, on_progress)

    emit("project", project=dump(project))
    with open(os.path.join(out_dir, "project.json"), "w", encoding="utf-8") as fh:
        json.dump(dump(project), fh, ensure_ascii=False, indent=2)
    return dump(project)


def stage_storyboard(
    out_dir: str,
    project,
    shot_count: int | None = None,
    on_progress: Callable[[str, dict], None] | None = None,
) -> list[Shot]:
    """阶段二：分镜师拆解 + 提示词工程师填提示词台词。产出 shots.json。"""

    def emit(stage: str, **info) -> None:
        if on_progress:
            on_progress(stage, info)

    img_dir = os.path.join(out_dir, "images")
    cache_dir = os.path.join(out_dir, "cache")
    for d in (out_dir, img_dir, cache_dir):
        os.makedirs(d, exist_ok=True)

    plan = f"{shot_count} 个分镜" if shot_count else "分镜（数量由 AI 决定）"
    print(f"[阶段二] 分镜 Agent · 拆解{plan}")
    emit("stage", step=2, label="拆解分镜")
    shots = agents.storyboard(project, shot_count)
    total_secs = sum(s.duration for s in shots)
    print(f"      共 {len(shots)} 镜，预计总时长约 {total_secs:g}s")
    emit("shots", shots=[s.to_dict() for s in shots])

    print("[阶段三] 提示词 Agent · 叙事语言转视觉语言")
    emit("stage", step=3, label="生成提示词")
    agents.prompt_engineer(project, shots)
    emit("prompts", shots=[s.to_dict() for s in shots])

    _save_outputs(out_dir, project, shots)
    return shots


def stage_images(
    out_dir: str,
    project,
    shots: list[Shot],
    concurrency: int = 2,
    on_progress: Callable[[str, dict], None] | None = None,
) -> dict:
    """阶段四（出图）：只渲染还没有图片的分镜，断点续跑。

    全链路纯文生图（2026-09-14 起无参考图 / 无 Edit 模型）：
    跨镜一致性靠 visual_prompt 里逐字相同的角色 anchor 与全局 style 约束。
    """

    def emit(stage: str, **info) -> None:
        if on_progress:
            on_progress(stage, info)

    img_dir = os.path.join(out_dir, "images")
    cache_dir = os.path.join(out_dir, "cache")
    for d in (out_dir, img_dir, cache_dir):
        os.makedirs(d, exist_ok=True)

    size = SIZE_TABLE.get(project.aspect_ratio, "1024x576")
    provider = create_provider()
    print(
        f"[阶段四] 出图 Agent · {provider.model} · "
        f"{project.aspect_ratio} -> {size}，并发 {concurrency}"
    )
    emit("stage", step=4, label="出图中", total=len(shots))

    pending = []
    for s in shots:
        expected = os.path.join(img_dir, f"shot_{s.shot_id:02d}.png")
        if s.image_path and os.path.isfile(s.image_path):
            continue
        if os.path.isfile(expected):
            s.image_path = expected
            continue
        pending.append(s)

    stats = asyncio.run(
        _render_all(
            pending, provider, cache_dir, img_dir, size, concurrency, on_progress,
        )
    )
    print(f"      新生成 {stats['api']} 张，缓存命中 {stats['cache']} 张")
    return stats


def run_pipeline(
    user_input: str,
    shot_count: int | None = None,
    out_dir: str = "outputs",
    concurrency: int = 2,
    ratio: str | None = None,
    text_only: bool = False,
    on_progress: Callable[[str, dict], None] | None = None,
) -> dict:
    """一次性完整管线（命令行入口）。工作台走 stage_* 分阶段接口。"""
    project_dump = stage_director(user_input, out_dir, shot_count, ratio, on_progress)
    project = Project.from_dict(project_dump)
    shots = stage_storyboard(out_dir, project, shot_count, on_progress)
    stats = {"api": 0, "cache": 0}
    if not text_only:
        stats = stage_images(out_dir, project, shots, concurrency, on_progress)
    _save_outputs(out_dir, project, shots)
    if on_progress:
        on_progress("done", stats=stats, shots=[s.to_dict() for s in shots])
    return {
        "project": dump(project),
        "shots": [s.to_dict() for s in shots],
        "stats": stats,
    }


def regenerate_shot(
    project: Project,
    shot: Shot,
    out_dir: str = "outputs",
    regen_prompt: bool = False,
    force: bool = True,
) -> Shot:
    """重生成单个分镜。

    regen_prompt=True  先按当前的 scene_desc 重写 prompt 再出图（用户改了画面描述时用）
    regen_prompt=False 直接用现有 prompt 重出图（只想换个画面时用）
    force=True         绕过缓存。用户点了「重新出图」就期待看到不一样的图，
                       所以默认开启；否则改了不进 prompt 的字段会拿回旧图。
    """
    img_dir = os.path.join(out_dir, "images")
    cache_dir = os.path.join(out_dir, "cache")
    for d in (out_dir, img_dir, cache_dir):
        os.makedirs(d, exist_ok=True)

    if regen_prompt:
        agents.prompt_engineer(project, [shot])

    size = SIZE_TABLE.get(project.aspect_ratio, "1024x576")
    provider = create_provider()
    render_one(shot, provider, cache_dir, img_dir, size, force=force)
    return shot


def _save_blocks(out_dir: str, blocks: list) -> None:
    """块数据单独落盘（blocks.json），与旧流程的 shots.json 互不干扰。"""
    with open(os.path.join(out_dir, "blocks.json"), "w", encoding="utf-8") as fh:
        json.dump(
            [b.to_dict() if hasattr(b, "to_dict") else b for b in blocks],
            fh, ensure_ascii=False, indent=2,
        )


def render_block_image(
    project,
    block,
    out_dir: str,
    force: bool = True,
) -> str:
    """给单个块出首帧图（第 0 秒画面），返回图片路径。同步执行。

    复用 render_one 的缓存：块只贡献 visual_prompt / negative_prompt 与编号，
    所以这里借一个只带这三个字段的 Shot 壳过渲染，
    文件名用 name="block_XX" 与分镜图隔离。
    """
    img_dir = os.path.join(out_dir, "images")
    cache_dir = os.path.join(out_dir, "cache")
    for d in (img_dir, cache_dir):
        os.makedirs(d, exist_ok=True)
    size = SIZE_TABLE.get(project.aspect_ratio, "1024x576")
    provider = create_provider()
    shell = Shot(
        shot_id=block.block_id,
        visual_prompt=block.visual_prompt,
        negative_prompt=block.negative_prompt,
    )
    render_one(
        shell, provider, cache_dir, img_dir, size,
        force=force, name=f"block_{block.block_id:02d}",
    )
    return os.path.join(img_dir, f"block_{block.block_id:02d}.png")


def _save_outputs(out_dir: str, project, shots: list[Shot]) -> None:
    with open(os.path.join(out_dir, "project.json"), "w", encoding="utf-8") as fh:
        json.dump(dump(project), fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "shots.json"), "w", encoding="utf-8") as fh:
        json.dump([s.to_dict() for s in shots], fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "preview.html"), "w", encoding="utf-8") as fh:
        fh.write(_render_preview(project, shots))


def _render_preview(project, shots: list[Shot]) -> str:
    cards = []
    for s in shots:
        if s.image_path:
            rel = s.image_path.replace(os.sep, "/").split("/")[-1]
            head = f'<img src="images/{rel}" alt="shot {s.shot_id}">'
        else:
            head = '<div class="noimg">未出图</div>'
        cards.append(
            f'<article class="shot">'
            f"{head}"
            f'<div class="meta">'
            f'<span class="id">#{s.shot_id:02d}</span>'
            f'<span class="tag">{s.camera}</span>'
            f'<span class="tag">{s.motion}</span>'
            f'<span class="tag">{s.duration:g}s</span>'
            f"</div>"
            f'<p class="plabel">画面描述 · 不直接送模型</p>'
            f'<p class="desc">{s.scene_desc}</p>'
            f'<p class="plabel">图片提示词 · 出首帧图</p>'
            f'<p class="prompt">{s.visual_prompt}</p>'
            f'<p class="plabel">视频提示词 · 配首帧图送 H3</p>'
            f'<p class="prompt">{s.video_prompt}</p>'
            f"</article>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{project.title} · 分镜预览</title>
<style>
  body {{ background:#F1EFE8; color:#2C2C2A; font-family:"Helvetica Neue",Arial,"Microsoft YaHei",sans-serif; margin:0; padding:32px; }}
  h1 {{ font-size:26px; margin:0 0 6px; letter-spacing:-0.5px; }}
  .logline {{ margin:0 0 4px; font-size:15px; }}
  .style {{ margin:0 0 28px; font-size:14px; color:#5F5E5A; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:24px; }}
  .shot {{ background:#fff; border:3px solid #2C2C2A; box-shadow:6px 6px 0 #2C2C2A; padding:14px; }}
  .shot img {{ width:100%; display:block; border:3px solid #2C2C2A; }}
  .noimg {{ border:3px dashed #B4B2A9; padding:44px 0; text-align:center; font-size:13px; color:#888780; }}
  .meta {{ display:flex; gap:6px; align-items:center; margin:12px 0 8px; flex-wrap:wrap; }}
  .id {{ font-weight:500; font-size:16px; }}
  .tag {{ background:#EF9F27; border:2px solid #2C2C2A; padding:1px 7px; font-size:12px; }}
  .desc {{ font-size:14px; margin:0 0 10px; line-height:1.6; }}
  .plabel {{ font-size:11px; color:#888780; margin:0 0 3px; }}
  .prompt {{ font-size:12px; color:#5F5E5A; margin:0 0 10px; line-height:1.5; font-family:monospace; }}
</style>
</head>
<body>
<h1>{project.title}</h1>
<p class="logline">{project.logline}</p>
<p class="style">{project.style} · {project.aspect_ratio}</p>
<main class="grid">
{"".join(cards)}
</main>
</body>
</html>"""

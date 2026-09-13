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


def render_one(
    shot: Shot,
    provider: ImageProvider,
    cache_dir: str,
    img_dir: str,
    size: str,
    force: bool = False,
) -> tuple[str, bool]:
    """给单个分镜出图，返回 (图片路径, 是否命中缓存)。

    force=True 绕过缓存。**编辑分镜后点「重新出图」必须传 force**：
    缓存键只包含 prompt / negative / size / model，如果用户只改了景别、运镜、时长
    这类不进 prompt 的字段，缓存键不变，会直接返回旧图——看起来就像"重生成失灵"。
    """
    target = os.path.join(img_dir, f"shot_{shot.shot_id:02d}.png")
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
                render_one, shot, provider, cache_dir, img_dir, size
            )
            stats["cache" if hit else "api"] += 1
            print(f"      #{shot.shot_id:02d} ok  {os.path.basename(path)}")
            if on_progress:
                on_progress("shot_done", {"shot_id": shot.shot_id, "path": path})

    await asyncio.gather(*(one(s) for s in shots))
    return stats


def _character_portrait_prompt(style: str, anchor: str) -> str:
    style_part = f"整体视觉风格：{style}。" if style else ""
    return (
        f"角色定妆照：{anchor}。单人正面半身像，面向镜头，表情自然，"
        f"纯浅灰色干净背景，细节清晰，电影感柔光。{style_part}"
    )


def _safe_char_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())
    return cleaned.strip("_") or "character"


def _gen_character_portraits(project, out_dir, on_progress=None) -> None:
    """给每个角色生成定妆照（1:1）。已有定妆照的角色自动跳过，不重复烧额度。"""
    provider = create_provider()
    size = SIZE_TABLE.get("1:1", "1024x1024")
    cdir = os.path.join(out_dir, "characters")
    os.makedirs(cdir, exist_ok=True)
    for c in project.characters:
        if c.image_path and os.path.isfile(c.image_path):
            continue
        path = os.path.join(cdir, f"{_safe_char_name(c.name)}.png")
        c.image_path = path
        prompt = _character_portrait_prompt(project.style, c.anchor)
        try:
            print(f"      角色定妆照 {c.name} 生成中…")
            provider.generate(prompt, path, negative_prompt=DEFAULT_NEGATIVE, size=size)
            if on_progress:
                on_progress("character_done", {"name": c.name, "path": path})
        except Exception as exc:
            print(f"      ⚠️ 角色定妆照失败 {c.name}：{exc}")
            c.image_path = ""


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
    """阶段四（出图）：只渲染还没有图片的分镜，断点续跑。"""

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
            pending, provider, cache_dir, img_dir, size, concurrency, on_progress
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

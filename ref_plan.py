"""Ref2VA（全能参考）的槽位编排与六段式提示词组装。

## 为什么需要这个文件

H3 的完整系统有三部分：**H3-Context-IR**（把自由输入整理成结构化表示的预处理层）+
H3-Base（生成）+ H3-Regenerate-2K。**Context-IR 是官方托管服务，没有随开源发布** ——
所以本地 ComfyUI 跑 H3 时，"把素材和意图整理成模型能懂的结构"这件事得自己做。
这就是本文件存在的理由：它不是可选的美化，是本地部署下决定出片质量的那一层。

## 官方 Ref2VA 六段式（顺序不能换，字段名照抄）

    subject_definitions: 帮每个素材取编号并描述外观
    summary:             一段话交代这支片做什么，**开头挂方括号任务类型标记**
    retention_analysis:  每个素材保留到什么程度，用固定标记词
    detailed_description: 主体。分镜 / 画面 / 动作 / 运镜 / 台词，建议 350-500 英文词
    overall_soundscape:  环境音与动作音（场景里的东西发出来的）
    non_diegetic_music:  只有观众听得到的配乐，无则 N/A

## 四类标签（全文含义统一）

| 标签 | 指什么 |
| --- | --- |
| `<Subject N>` | 从素材里"拉出来"的**可复用**角色/物件/风格 |
| `<Picture N>` | 第 N 张参考图（**素材本身**） |
| `<Video N>` | 第 N 支参考视频（剪辑源 / 续接起点 / 整片结构） |
| `<Audio N>` | 第 N 段参考音频（音色 / 节奏 / BGM 风格） |

⚠️ 两条最容易错的：
1. **编号是「分类编号」，且与上传顺序一一对应**：第 1 张图 = `<Picture 1>`，
   第 1 段音频 = `<Audio 1>`。**参考视频和参考音频不占用 `<Picture>` 编号，
   也不会自动变成 `<Subject>`**。
   （LibTV 界面是把素材混在一个序列里编号的 —— 照抄它的"音频 5"写成 `<Audio 5>`
   会指向不存在的槽位。这是本项目实测过的一个坑。）
2. **`<Subject N>` 是从图片里"拉出来"的，不是图片本身**：写
   `<Subject 1> is the young woman in <Picture 1>, ...`，而不是给图片起个名字。

## retention_analysis 的两套标记词，绝不能混用

- 视觉内容（`<Subject>` / `<Picture>` / `<Video>`）：
  `fully_preserved` / `partially_preserved` / `attribute_transfer` / `weak_reference`
- **音频（`<Audio>`）**：`fully_copy` / `partially_copy` / `reference` / `weak_reference`

⚠️ 把音频的 `fully_copy` 写成 `fully_preserved`，模型很可能就不会照那段音频走。
反过来，本项目的场景是**只借音色**，所以用 `reference` —— 并在说明里写死
「不要复用它的内容当台词」，这就是防样本内容泄漏的那道闸（旧的 `voice only` 语法，
在六段式里的正确落点就是这里）。

## 时长与镜头颗粒度（官方经验值，写错会明显掉质）

- 最后一切要离结尾**至少 2 秒**（10 秒片，最后一刀不晚于 00:08.000）
- 5 秒片 1-2 颗镜头｜10 秒 2-3 颗｜15 秒 3-4 颗
- **切得越碎，人物越容易在镜头间跑掉** —— 参考模式尤其明显，因为每一刀模型都要
  重新对一次参考图。所以宁可少切。

`audit_detailed_description()` 就是照这三条做体检的。
而**时间轴本身由 `plan_shot_timeline()` 算好喂给文本 Agent**（2026-09-18 起），
不让模型自己排 —— 它排出来的十有八九会把最后一镜贴到片尾。

## 负面约束（2026-09-18 加）

`DETAILED_CONSTRAINTS` 会追加在 `detailed_description` 末尾，由代码固定拼上：
不出现字幕/水印/UI、动作遵守真实物理（禁止漂浮滑行穿模瞬移）、
单镜内不切第三人外部视角、多镜之间人物左右位置与视线轴线不许翻转。
来源是一份公认写得好的提示词范本 —— 这几条不写就会偶发。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 官方上限（与 ComfyUI 的 MiniMaxH3ReferenceToVideo 输入口一一对应）
MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIO = 3
MAX_FILES = 12
MAX_SECONDS = 15.0


# --------------------------------------------------------------- 镜头时间轴
# 一段时长切几颗镜头。官方经验值是 5 秒 1-2 颗｜10 秒 2-3 颗｜15 秒 3-4 颗，
# 这里取每档的**上限**：一颗镜头里能交代的信息有限，太少会显得"什么都没发生"；
# 但**不越过上限** —— 切得越碎，参考模式下人物越容易在镜头之间跑掉。
def shot_count_for(duration: float) -> int:
    if duration <= 5:
        return 2
    if duration <= 10:
        return 3
    return 4


def plan_shot_timeline(duration: float) -> list[tuple[int, float, float]]:
    """把一段时长均分成镜头时间轴 → [(镜头号, 起始秒, 该镜时长秒), ...]

    ⚠️ **时间轴由代码算，不交给模型**。让模型自己排时间轴有两个老毛病：
      ① 最后一镜贴着片尾 —— 那一镜还没演完片子就结束了；
      ② 镜头数超出官方颗粒度 —— 人物在镜头之间跑掉。
    这两条 `audit_detailed_description()` 事后都会警告。与其事后吵，不如事前给定。

    均分天然满足"最后一个切点离结尾至少 2 秒"：第 N 颗的起点是 (N-1)/N × 总长，
    只要 N ≤ 总长/2 就成立 —— 而上面每档的镜头数都满足这一条（15 秒 4 颗时
    最后一切在 11.25s，离结尾 3.75s）。
    """
    n = shot_count_for(duration)
    step = duration / n
    return [(i + 1, i * step, step) for i in range(n)]


def format_timestamp(sec: float) -> str:
    """秒 → 官方要求的 MM:SS.mmm（如 3.333 → "00:03.333"）"""
    minutes = int(sec // 60)
    return f"{minutes:02d}:{sec - minutes * 60:06.3f}"


# ------------------------------------------------------- detailed_description 收尾
# 负面约束，追加在 detailed_description 末尾。
#
# 来源：一份公认写得好的提示词范本，它的收尾段专治视频模型最常见的几类翻车 ——
# 自己"脑补"出字幕/水印/UI、动作违反物理（漂浮、滑行、穿模、瞬移、无人机跳切）、
# 以及多镜之间把人物左右位置/视线/轴线翻掉（正反打最容易翻）。
# 这几条**不写就会偶发**，写上去能明显压住。
#
# 由**代码**追加而不是交给文本 Agent 写：它是固定套路，模型偶尔会漏，
# 而漏了从提示词表面看不出来 —— 等出片才发现代价太大。
# ⚠️ 2026-09-19 补了**人数**那条（"only the cast … no additional people"）：
#    斌哥拿了一份公认写得好的范本来问"我们有负面提示词吗"，对照下来就缺这一条 ——
#    而"背景里凭空多出一个人"正是视频模型最高频的翻车点之一，范本里专门写了
#    "全段只有 XX 两位现场人物 / 禁止背景突然出现父母、路人"。
DETAILED_CONSTRAINTS = (
    "Constraints: no added text, subtitles, captions, watermarks, logos, icons or UI "
    "overlays anywhere in frame. Only the referenced cast appears in frame: do not "
    "introduce additional people, extras, crowds or background characters at any time, "
    "and do not turn printed faces in photos or posters into living people. "
    "All motion obeys real-world physics with believable "
    "weight and inertia - no floating, sliding, morphing, clipping, teleporting or "
    "impossible camera jumps. Each shot is a single continuous take; no cutaway to an "
    "outside third-person view within a shot. Character left/right positions, gaze "
    "direction, body orientation and screen direction stay consistent across cuts - "
    "never flip the axis. The referenced appearance, wardrobe, props and environment "
    "stay identical throughout."
)


@dataclass
class RefSlot:
    """一个参考槽位。kind + index 决定它在 ComfyUI 里连到哪个输入口。"""

    kind: str                 # "image" | "audio"
    index: int               # 0-based，连到 ref_image_N / ref_audio_N
    tag: str                  # "<Picture 1>" / "<Audio 1>" / "<Video 1>"
    path: str
    role: str                 # "character" | "prop" | "scene" | "voice"
    label: str                # 人看的：角色名 / 道具名 / 场景名
    retention: str            # 视觉四选一 / 音频四选一
    subject: str = ""         # 只对图片有效，如 "<Subject 1>"
    note: str = ""            # 给模型看的一句说明（进 retention_analysis）

    @property
    def input_slot(self) -> str:
        """ComfyUI 节点上的输入口名（0-based，与官方工作流一致）。"""
        return f"ref_{self.kind}s.ref_{'image' if self.kind == 'image' else 'audio'}_{self.index}"

    @property
    def cn(self) -> str:
        return "图片" if self.kind == "image" else "音频"

    @property
    def human(self) -> str:
        """界面/日志用的一行说明。中文编号是给人看的（H3 的 tag 永远是英文）。"""
        return f"{self.cn} {self.index + 1} 作为「{self.label}」的{_ROLE_CN.get(self.role, self.role)}"


_ROLE_CN = {
    "character": "外貌锁定",
    "prop": "道具锁定",
    "scene": "环境锁定",
    "image": "画面参考",
    "voice": "音色参考",
    "audio": "音频参考",
}


@dataclass
class RefPlan:
    """一次生成的完整编排结果。"""

    slots: list[RefSlot] = field(default_factory=list)
    prompt: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def images(self) -> list[RefSlot]:
        return [s for s in self.slots if s.kind == "image"]

    @property
    def audios(self) -> list[RefSlot]:
        return [s for s in self.slots if s.kind == "audio"]

    def manifest(self) -> str:
        """给人看的连线操作单（中文）。**图片与音频分组列**，对着 ComfyUI 连不容易乱——
        两类的编号各自从 1 数，混着列会让人以为是一个连续序列。"""
        lines = [
            f"  参考槽位共 {len(self.slots)} 个"
            f"（图 {len(self.images)}/{MAX_IMAGES} · 音频 {len(self.audios)}/{MAX_AUDIO}，合计上限 {MAX_FILES}）"
        ]
        if self.images:
            lines.append("  ── 图片 ──")
            for s in self.images:
                lines.append(f"    {s.tag:<12} {s.human}")
                lines.append(f"      └ {s.input_slot}  ←  {s.path}")
        if self.audios:
            lines.append("  ── 音频 ──")
            for s in self.audios:
                lines.append(f"    {s.tag:<12} {s.human}")
                lines.append(f"      └ {s.input_slot}  ←  {s.path}")
        return "\n".join(lines)


def _find_character(project, name: str):
    return next((c for c in project.characters if c.name == name), None)


def _clean(text: str) -> str:
    """去掉会影响英文句子的换行与多余空格（素材描述常带换行）。"""
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".")


# 参考槽位的**种类顺序** —— 它决定 `<Picture N>` 的编号，是提示词与出片两边共用的契约。
#
# ⚠️ 出片那一侧（`server/app.py::start_segment_video`）必须按**同一个规则**给参考图
#    排序：ComfyUI 按上传顺序收图（`ref_image_0 … ref_image_8`，见 video_provider），
#    而提示词是按这份顺序写的。两边一旦错位，就是"提示词说 `<Picture 2>` 是那把剑，
#    模型收到的第 2 张却是背景"—— 从画面上看只会觉得"参考没生效"，很难查回来。
REF_KIND_ORDER = ("character", "prop", "scene", "image", "audio")


def kind_rank(kind: str) -> int:
    """素材种类 → 它在参考槽位里的排位（不认识的种类排最后，不去猜它的位置）。

    稳定排序的 key：同一种类内保持用户点选的先后（见 start_segment_video 的调用点）。
    """
    try:
        return REF_KIND_ORDER.index(str(kind or ""))
    except ValueError:
        return len(REF_KIND_ORDER)


def build_ref_plan(
    project,
    *,
    characters=(),
    props=(),
    scene: str = "",
    images=(),
    audios=(),
    use_voice: bool = True,
    extra_views: int = 0,
) -> RefPlan:
    """把这一镜用到的素材排成参考槽位（顺序 = 上传顺序 = tag 编号顺序）。

    顺序规则（决定了 tag 编号，改之前想清楚）：
      **角色 → 道具 → 场景 → 其他图片 → 最后音频（角色音色 + 其他音频）**
    图片编号连续占 `<Picture 1..N>`；音频**另起一号**占 `<Audio 1..M>`。
    这份顺序就是 `REF_KIND_ORDER` —— 出片那一侧（`server/app.py::start_segment_video`）
    按同一个规则给参考图排序，见那里的注释：**它和这里是同一个契约，改一处必须改两处**。

    `images` / `audios` 是「其他图片 / 其他音频」类素材的名字 —— 它们是**用户自己上传的**，
    对"没有任何图像/文本 API，只有 ComfyUI"的用户来说，这就是全部的素材来源，
    所以编排器必须能吃下它们，不能只认角色/道具/场景。

    ⚠️ **"用素材的哪张图"这条规则不在这里**，在 `schemas.Character.primary_image` /
    `schemas.Asset.primary_image`：设定图（sheet）优先，没有才退回单张。这里只负责
    **排序与编号** —— 但两件事必须一起对，否则"提示词说的那张"和"模型收到的那张"不是同一张。

    extra_views>0 时，每个角色额外带 1 张其它视角（侧面/全身）来加强身份保真 ——
    会多占槽位，9 张上限下别开太大。
    """
    plan = RefPlan()
    img_i = 0
    aud_i = 0
    subject_i = 0

    # ---- 角色：定妆照（正面半身）+ 音色样本
    for name in characters:
        c = _find_character(project, name)
        if c is None:
            plan.warnings.append(f"角色「{name}」不在 project.characters 里，已跳过")
            continue
        # ⚠️ 第一张图必须与出片时**实际上传的那张**是同一张（2026-09-29 修）：
        #    Ref2VA 下 `_resolve_material_ref` 给角色用的是四视图设定图（sheet），
        #    而这里从前写死 `image_path`（单张正面照）—— manifest 那份"连线操作单"指的
        #    文件于是跟真正进模型的不是同一个。取哪张只有 `Character.primary_image` 一处
        #    （I2V / 首尾帧那条链路另说：那里首帧必须是单张正面照）。
        first = c.primary_image
        paths: list[str] = []
        if first:
            paths.append(first)
        if extra_views > 0 and c.images:
            for p in c.images:
                if p != first and len(paths) <= extra_views:
                    paths.append(p)
        if not paths:
            plan.warnings.append(f"角色「{name}」还没有定妆照 → 无法作为参考图")
        for p in paths:
            if img_i >= MAX_IMAGES:
                plan.warnings.append(f"参考图已达上限 {MAX_IMAGES}，角色「{name}」的后续视角被丢弃")
                break
            subject_i += 1
            plan.slots.append(
                RefSlot(
                    kind="image", index=img_i, tag=f"<Picture {img_i + 1}>", path=p,
                    role="character", label=name, retention="fully_preserved",
                    subject=f"<Subject {subject_i}>",
                    # note 只放**锚点本身**（完整名词短语），锁哪些特征由 _LOCK_TRAITS 给 ——
                    # 两处混在一起写会导致 subject_definitions 语法破掉（实测踩过）
                    note=_clean(c.anchor_en) or f"the person named {name}",
                )
            )
            img_i += 1
        # 音色样本：只借音色，不复用内容
        if use_voice:
            if not c.voice_sample:
                plan.warnings.append(
                    f"角色「{name}」没有音色样本 → 这一镜锁不住它的声音"
                    "（在角色卡上点「生成音色样本」）"
                )
            elif aud_i >= MAX_AUDIO:
                plan.warnings.append(f"参考音频已达上限 {MAX_AUDIO}，角色「{name}」的样本被丢弃")
            else:
                plan.slots.append(
                    RefSlot(
                        kind="audio", index=aud_i, tag=f"<Audio {aud_i + 1}>",
                        path=c.voice_sample, role="voice", label=name, retention="reference",
                    )
                )
                aud_i += 1

    # ---- 道具：三视图设定图（sheet）优先，没有才退回单张主视图 / 上传图
    # ⚠️ **不能只看 `a.images`（2026-09-29 修）**：道具走「AI 生成」产出的是**一张三视图**，
    #    产物只落 `a.sheet`、`images` 恒为空（见 `schemas.Asset.sheet` 与
    #    `server/app.py::generate_asset_image` 的落库注释）。从前这里只读 `a.images[0]`，
    #    于是**六个道具全被判成"还没有素材图"**：出片时一张都用不上，提示词里一个道具
    #    都引用不到，卡片上还挂着黄字「道具「旧铁剑」还没有素材图 → 无法作为参考图」。
    #    "取哪张图"这条规则只有 `Asset.primary_image` 一处 —— 必须与出片时的
    #    `_resolve_material_ref` 给出同一个答案，否则编号与实际上传的图会对不上。
    for name in props:
        a = project.asset_of(name, "prop")
        if a is None:
            plan.warnings.append(f"道具「{name}」不在 project.assets 里，已跳过")
            continue
        path = a.primary_image
        if not path:
            plan.warnings.append(f"道具「{name}」还没有素材图 → 无法作为参考图")
            continue
        if img_i >= MAX_IMAGES:
            plan.warnings.append(f"参考图已达上限 {MAX_IMAGES}，道具「{name}」被丢弃")
            continue
        subject_i += 1
        plan.slots.append(
            RefSlot(
                kind="image", index=img_i, tag=f"<Picture {img_i + 1}>", path=path,
                role="prop", label=name, retention="fully_preserved",
                subject=f"<Subject {subject_i}>",
                note=_clean(a.anchor_en) or f"the prop named {name}",
            )
        )
        img_i += 1

    # ---- 场景：单张全景
    if scene:
        a = project.asset_of(scene, "scene")
        if a is None:
            plan.warnings.append(f"场景「{scene}」不在 project.assets 里，已跳过")
        elif not a.images:
            plan.warnings.append(f"场景「{scene}」还没有素材图 → 无法作为参考图")
        elif img_i >= MAX_IMAGES:
            plan.warnings.append(f"参考图已达上限 {MAX_IMAGES}，场景「{scene}」被丢弃")
        else:
            subject_i += 1
            plan.slots.append(
                RefSlot(
                    kind="image", index=img_i, tag=f"<Picture {img_i + 1}>", path=a.images[0],
                    role="scene", label=scene, retention="fully_preserved",
                    subject=f"<Subject {subject_i}>",
                    note=_clean(a.anchor_en) or f"the environment named {scene}",
                )
            )
            img_i += 1

    # ---- 其他图片 / 其他音频：用户自己上传的额外素材
    # 这两类不定义 <Subject>（它们没有"可复用角色"的语义，就是画面/声音参考本身）
    for name in images:
        a = project.asset_of(name, "image")
        if a is None:
            plan.warnings.append(f"图片素材「{name}」不在 project.assets 里，已跳过")
            continue
        if not a.images:
            plan.warnings.append(f"图片素材「{name}」还没有文件 → 先上传")
            continue
        if img_i >= MAX_IMAGES:
            plan.warnings.append(f"参考图已达上限 {MAX_IMAGES}，图片「{name}」被丢弃")
            continue
        plan.slots.append(
            RefSlot(
                kind="image", index=img_i, tag=f"<Picture {img_i + 1}>", path=a.images[0],
                role="image", label=name, retention="fully_preserved",
                note="use its composition, colour and lighting as the visual reference",
            )
        )
        img_i += 1

    for name in audios:
        a = project.asset_of(name, "audio")
        if a is None:
            plan.warnings.append(f"音频素材「{name}」不在 project.assets 里，已跳过")
            continue
        if not a.images:
            plan.warnings.append(f"音频素材「{name}」还没有文件 → 先上传")
            continue
        if aud_i >= MAX_AUDIO:
            plan.warnings.append(f"参考音频已达上限 {MAX_AUDIO}，音频「{name}」被丢弃")
            continue
        plan.slots.append(
            RefSlot(
                kind="audio", index=aud_i, tag=f"<Audio {aud_i + 1}>",
                path=a.images[0], role="audio", label=name, retention="reference",
            )
        )
        aud_i += 1

    total = len(plan.slots)
    if total > MAX_FILES:
        plan.warnings.append(f"素材合计 {total} 个，超过 H3 的 {MAX_FILES} 个上限，需减")
    return plan


def _subject_map(plan: RefPlan) -> dict[str, str]:
    """角色名 → 它的 `<Subject N>`。

    音频 slot 自己不产生 Subject（Subject 是从图片里"拉出来"的），但音频的说明里
    必须指名它锁的是哪个 Subject —— 否则英文句里会退化成 "the character 朱丹"
    （中文名混进英文句，实测踩过）。所以两段文本都要靠这张表查。
    """
    return {
        s.label: s.subject for s in plan.slots if s.kind == "image" and s.subject
    }


def _subject_definitions(plan: RefPlan) -> str:
    """第一段：给每个素材取编号并描述外观。

    ⚠️ 写法是 `<Subject N> is ... <Picture N>: {锚点}.` —— **用冒号直接接完整的锚点短语**。
    早先我写成 `is the person shown in <Picture 1>, with {锚点}`，出来的句子是
    "is the person shown in X, with a woman in her late twenties with..."，语法直接破掉：
    锚点本身就是"一个人 + 其特征"的完整名词短语，不能再套 with。
    """
    parts: list[str] = []
    subjects = _subject_map(plan)
    for s in plan.slots:
        if s.kind == "image":
            # 「其他图片」没有 <Subject> 语义（它不是"可复用的角色"，就是画面参考本身），
            # 所以不能套 `<Subject N> is ...` 的句式 —— 那会写出一个不存在的编号。
            if not s.subject:
                parts.append(
                    f"{s.tag} is an additional visual reference for the target video: "
                    f"{s.note}."
                )
                continue
            noun = {"character": "person", "prop": "object", "scene": "environment"}.get(s.role, "subject")
            parts.append(f"{s.subject} is the {noun} shown in {s.tag}: {s.note}.")
        elif s.role == "voice":
            ref = subjects.get(s.label) or f"the character {s.label}"
            parts.append(
                f"{s.tag} is a short clean voice sample for {ref}, "
                "provided only as a timbre reference."
            )
        else:
            parts.append(
                f"{s.tag} is a standalone audio reference for the target video."
            )
    return " ".join(parts)


# retention 那一段要锁哪些特征：按角色类型给固定短语。
# 不重复 anchor 全文 —— 官方范例也只列特征要点
# （"her facial features, hair and knit sweater stay identical to <Picture 1>"），
# 重复一遍锚点会让这一段变成冗长的复读。
_LOCK_TRAITS = {
    "character": "facial features, hairstyle, body proportions and outfit",
    "prop": "shape, material, colour and wear",
    "scene": "spatial layout, materials and light direction",
    "image": "composition, colour palette and lighting",
}
_SHOT_HINT = {
    "character": "appears in every shot",
    "prop": "present in the scene",
    "scene": "the whole scene",
    "image": "visual reference",
}


def _retention_analysis(plan: RefPlan) -> str:
    """第三段：每个素材保留到什么程度。**两套标记词不能混**。

    三种情况要分开写，混了就会写出不存在的 Subject：
      - 有 `<Subject N>` 的（角色/道具/场景）→ 保留强度 + 与哪张图一致
      - 没有 Subject 的「其他图片」→ 只能用 `attribute_transfer`（借它的构图/色调，
        画面内容可以完全不同，这正是"参考"而不是"照搬"）
      - 音频分两类：角色音色（挂到 Subject 上）与其他音频（只借氛围，不做成品音轨）
    """
    parts: list[str] = []
    subjects = _subject_map(plan)
    for s in plan.slots:
        if s.kind == "image":
            traits = _LOCK_TRAITS.get(s.role, "key visual traits")
            if s.subject:
                parts.append(
                    f"{s.subject} ({_SHOT_HINT.get(s.role, 'appears in the scene')}): "
                    f"{s.retention} - {traits} stay identical to {s.tag} throughout."
                )
            else:
                parts.append(
                    f"{s.tag}: attribute_transfer - borrow its {traits}; "
                    "the target shot may differ in content."
                )
        elif s.role == "voice":
            ref = subjects.get(s.label) or f"the character {s.label}"
            # 音频只能用 fully_copy / partially_copy / reference / weak_reference。
            # 本项目是"只借音色"，用 reference 并写死不要复用内容 ——
            # 这就是防样本内容泄漏成台词的那道闸（旧写法的 voice only 在这里）
            parts.append(
                f"{s.tag}: reference - use only as the voice timbre of {ref}; "
                "do not reuse its spoken content as dialogue or narration."
            )
        else:
            parts.append(
                f"{s.tag}: reference - borrow only its atmosphere and style; "
                "do not reuse it as the final audio track."
            )
    return " ".join(parts)


def compose_ref2va_prompt(
    project,
    *,
    video_prompt: str,
    soundscape: str = "",
    music: str = "N/A",
    characters=(),
    props=(),
    scene: str = "",
    images=(),
    audios=(),
    duration: float = 0.0,
    task_tags: tuple[str, ...] = ("reference generation",),
    use_voice: bool = True,
    extra_views: int = 0,
) -> RefPlan:
    """组装一份完整的 Ref2VA 六段式提示词。

    `video_prompt` 是 `detailed_description` 的正文，由分镜 Agent 产出 ——
    里面已经带 `[Shot 1]` / `[Shot 2] At 00:05.000,` 与台词块。本函数只负责
    把「参考素材的声明」和「分镜正文」拼成官方六段，**不改写正文内容**：
    正文的措辞交给文本 Agent，字段名与编号是代码的职责（同一个分工原则，
    见 video_provider.compose_h3_prompt 的注释）。

    `task_tags` 会以方括号写在 summary 开头。本项目常见组合：
      - 只借角色音色：`("reference generation", "audio reference")`
      - 整段音乐当音轨：`("reference generation", "audio reuse")`（并把 music 段写成
        「不再另加音效」，**不要写 N/A**，否则模型以为没有音乐）
    """
    plan = build_ref_plan(
        project, characters=characters, props=props, scene=scene,
        images=images, audios=audios, use_voice=use_voice, extra_views=extra_views,
    )

    # ---- 第二段 summary：任务类型标记 + 一句话交代
    # ⚠️ 拼句子时注意两点（都是实测踩出来的）：
    #   ① `A {dur} clip` 中间必须有名词，写成 "A 10-second in the style of ..." 直接破句
    #   ② style_en 里通常已经含 "live-action, cinematic"，别在 clip 前再补一次 live-action
    style = _clean(getattr(project, "style_en", "") or getattr(project, "style", ""))
    subject_names = ", ".join(
        s.subject for s in plan.slots if s.kind == "image" and s.role == "character"
    ) or "the referenced subjects"
    dur = f"{duration:.0f}-second " if duration else ""
    summary = (
        f"[{' + '.join(task_tags)}] A {dur}{style + ' ' if style else ''}clip "
        f"featuring {subject_names}, keeping the referenced appearance, props and "
        "environment consistent while the specified action unfolds."
    )

    # 正文末尾追加固定的负面约束。**体检用的仍是模型原文**（video_prompt），
    # 不然这段样板会把 audit 的镜头计数带偏。
    body = video_prompt.strip()
    if body and DETAILED_CONSTRAINTS not in body:
        body = f"{body}\n\n{DETAILED_CONSTRAINTS}"

    blocks = [
        f"subject_definitions: {_subject_definitions(plan)}",
        f"summary: {summary}",
        f"retention_analysis: {_retention_analysis(plan)}",
        f"detailed_description: {body}",
        f"overall_soundscape: {soundscape.strip() or 'Natural ambience and physical sounds matching the on-screen action.'}",
        f"non_diegetic_music: {music}",
    ]
    plan.prompt = "\n\n".join(b for b in blocks if b.strip())
    plan.warnings.extend(audit_detailed_description(video_prompt, duration))

    # 正文里应当引用 <Subject N> —— 不引用的话模型只能靠 subject_definitions 自己猜
    # "哪张图演谁"，参考模式最核心的一致性优势就打折了。
    # （注意：当前分镜 Agent 产出的 video_prompt 是按 I2VA 写的，不会带 Subject 引用，
    #   Ref2VA 接上后要让提示词 Agent 在正文里用上编号 —— 这条警告就是提醒这件事。）
    if any(s.subject for s in plan.slots) and "<Subject" not in (video_prompt or ""):
        plan.warnings.append(
            "detailed_description 里没有任何 <Subject N> 引用 → 模型只能靠 "
            "subject_definitions 自行对应素材，一致性会打折。"
            "建议提示词 Agent 在 Ref2VA 模式下把 <Subject N> 写进正文"
        )
    return plan


def audit_detailed_description(text: str, duration: float = 0.0) -> list[str]:
    """按官方经验值给 detailed_description 做三项体检。

    这三条不满足不会报错，但会明显掉质 —— 所以宁可提前吵一句，别等出片才发现。
    """
    warns: list[str] = []
    t = text or ""
    if not t.strip():
        return ["detailed_description 是空的 → Ref2VA 至少要写清目标镜头在做什么"]

    shots = re.findall(r"\[Shot\s+(\d+)\]", t)
    n = len(shots)
    if n == 0:
        # ⚠️ 这条是补的：原来 n=0 会让下面两条检查**全部静默跳过**，
        # 于是一份"一颗镜头标记都没有"的正文会被判为合格。
        # 实测踩过 —— 强调"第一句先写影像质感"之后，模型很容易把 [Shot 1] 整个省掉。
        warns.append(
            "正文里一个 [Shot N] 标记都没有 → 官方格式要求每颗镜头以 [Shot N] 开头"
            "（[Shot 1] 不写时间戳，[Shot 2] 起接 'At MM:SS.mmm,'）"
        )
    elif shots[0] != "1":
        warns.append(f"第一个镜头编号是 [Shot {shots[0]}]，应当从 [Shot 1] 开始")

    # ① 镜头颗粒度：切得越碎，参考模式下人物越容易跑
    if duration:
        if duration <= 5 and n > 2:
            warns.append(f"{duration:.0f} 秒片有 {n} 颗镜头，偏碎（官方经验 5 秒 1-2 颗）")
        elif 5 < duration <= 10 and n > 3:
            warns.append(f"{duration:.0f} 秒片有 {n} 颗镜头，偏碎（官方经验 10 秒 2-3 颗）")
        elif duration > 10 and n > 4:
            warns.append(f"{duration:.0f} 秒片有 {n} 颗镜头，偏碎（官方经验 15 秒 3-4 颗）")

    # ② 后续镜头必须以 At MM:SS.mmm, 开头（[Shot 1] 不带时间戳）
    for m in re.finditer(r"\[Shot\s+(\d+)\]\s*([^[]*)", t):
        idx, body = int(m.group(1)), m.group(2).strip()
        if idx > 1 and not re.match(r"At\s+\d{2}:\d{2}\.\d{3}\s*,", body):
            warns.append(f"[Shot {idx}] 后面缺 'At MM:SS.mmm,' 时间戳（只有 [Shot 1] 不写）")
            break

    # ③ 最后一切要离结尾至少 2 秒
    stamps = [float(s) * 60 + float(ms) for s, ms in
              re.findall(r"At\s+(\d{2}):(\d{2})\.\d{3}", t)]
    if duration and stamps and max(stamps) > duration - 2 + 1e-6:
        warns.append(
            f"最后一个切点在 {max(stamps):.2f}s，距结尾（{duration:.0f}s）不足 2 秒 —— "
            "那一镜还没演完片子就结束了"
        )
    return warns

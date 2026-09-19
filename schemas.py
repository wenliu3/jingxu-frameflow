"""Agent 之间传递的数据契约。

设计要点：Shot 里的 camera / motion / duration 会被视频阶段**真实消费**——
camera 决定首帧图的视角词，motion 决定 H3 的运镜（官方英文术语），
duration 决定生成帧数并吸附到 17 帧网格。分镜阶段一次填好，后续不必重跑
（重跑就意味着重新烧额度）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Character:
    """角色。anchor 是跨镜头保持一致的关键——固定的、可复述的外貌描述。

    images 是四个视角的定妆照（正面半身 / 侧面 / 背面 / 全身），阶段一产出：
    既给用户核对，也是 **Ref2VA（全能参考）模式的参考图来源**。
    注意：I2VA 模式只能拿其中一张当首帧，多视角图只有切到 Ref2VA 才吃得满
    （Ref2VA 最多接受 9 张参考图）。
    image_path 保留为「正面半身」那一张，向后兼容旧任务数据。
    """

    name: str
    anchor: str
    anchor_en: str = ""          # 英文锚点：只给 FLUX 系模型用（中文 prompt 对它是噪音）。用 Z-Image 等中文模型时不参与
    voice: str = ""              # 音色/声音设计（少女音清亮、低沉沙哑…），写进 H3 的说话人描述
    tts_voice: str = ""          # TTS 音色 id（如 zh-CN-YunxiNeural）。是 voice 那段文字描述的"降级落地"，供造音色样本用
    voice_sample: str = ""       # 音色样本**文件名**（如 voice_林晚.mp3）。只存文件名，换机器不用重指路径
    image_path: str = ""         # 正面半身定妆照（兼容旧数据，等于 images 的第一张）
    images: list[str] = field(default_factory=list)   # 四视角定妆照路径，顺序：正/侧/背/全身
    sheet: str = ""              # 「四视图设定图」原图：**最左一张大的面部半身特写 +
                                 # 正面/标准侧面/背面三个全身，四格横向排版**，恒 16:9 横版
                                 # （版式照斌哥给的参考图，见 agents.PORTRAIT_SHEET_LAYOUT）。
                                 # 只作总览与留档，**不进 images**：下游要的是单人图
                                 # （I2VA 首帧、Ref2VA 参考图），拼图会被模型读成"画面里有四个人"。
                                 # ⚠️ 2026-09-19 当天在「横版四格」与「竖版两栏」之间来回改过一次，
                                 # 现定稿横版四格（金鳞那张全对、莫卡那张竖版出成两张一样的正面全身）。
                                 # crop_ref 是独立的离线工具，**没有任何产品代码调用它**，
                                 # 所以改版式不影响链路。

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Character":
        images = [str(x) for x in (d.get("images") or [])]
        image_path = str(d.get("image_path", ""))
        # 旧任务只有单张 image_path：把它当作 images 的第一张，避免下游到处判空
        if not images and image_path:
            images = [image_path]
        return cls(
            name=str(d.get("name", "")),
            anchor=str(d.get("anchor", "")),
            anchor_en=str(d.get("anchor_en", "")),
            voice=str(d.get("voice", "")),
            tts_voice=str(d.get("tts_voice", "")),
            voice_sample=str(d.get("voice_sample", "")),
            image_path=image_path or (images[0] if images else ""),
            images=images,
            sheet=str(d.get("sheet", "")),
        )


@dataclass
class Asset:
    """故事素材：道具或场景。对标 LibTV 的「资产层」——素材与故事分离、可生成可上传、跨分镜复用。

    kind="prop"   道具：多视角（主视图 / 侧视图）。道具在分镜里会以不同角度出现，
                  多视角参考图才能在任意构图里锁住它。
    kind="scene"  场景：单张全景。是**故事发生地的环境设定图**（如「废弃钟楼内部全貌」），
                  不是任何一镜的分镜画面；作用是给分镜出图当环境锚，跨镜保持同一空间。

    anchor 写法与角色同源：固定的、可复述的视觉事实（材质、颜色、磨损 / 空间结构、
    光源方向、材质质感），禁止情绪词。
    一致性靠 anchor 文字约束 + 固定 seed，不走参考图（2026-09-14 起取消图生图）。
    """

    kind: str                    # "prop" | "scene"
    name: str
    anchor: str
    anchor_en: str = ""          # 英文锚点（FLUX 系专用，同 Character.anchor_en）
    images: list[str] = field(default_factory=list)
    sheet: str = ""              # 「三视图设定图」原图（一张图里并排正视/侧视/后视，**只道具用**）。
                                 # 与 Character.sheet 同理：只作总览与留档，**不进 images** ——
                                 # 下游要的是单件图，拼图会被模型读成"画面里有三件道具"。
                                 # 场景不搞多视角（一张全景就够），所以没有 scene 的 sheet。

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Asset":
        return cls(
            kind=str(d.get("kind", "prop")),
            name=str(d.get("name", "")),
            anchor=str(d.get("anchor", "")),
            anchor_en=str(d.get("anchor_en", "")),
            images=[str(x) for x in (d.get("images") or [])],
            sheet=str(d.get("sheet", "")),
        )


@dataclass
class Project:
    """全局设定。所有分镜共享的上下文，用来对抗「越出图越跑偏」。"""

    title: str
    logline: str
    style: str
    style_en: str = ""           # 英文风格（FLUX 系定妆照/素材 prompt 用）
    aspect_ratio: str = "16:9"   # 给默认值：dataclass 里带默认值的字段后面不能再有无默认字段
    characters: list[Character] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)   # 道具 + 场景（kind 区分）

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        return cls(
            title=str(d.get("title", "未命名")),
            logline=str(d.get("logline", "")),
            style=str(d.get("style", "")),
            style_en=str(d.get("style_en", "")),
            aspect_ratio=str(d.get("aspect_ratio", "16:9")),
            characters=[Character.from_dict(c) for c in d.get("characters", [])],
            assets=[Asset.from_dict(a) for a in (d.get("assets") or [])],
        )

    def anchor_of(self, name: str) -> str:
        for c in self.characters:
            if c.name == name:
                return c.anchor
        return ""

    def asset_of(self, name: str, kind: str = "") -> "Asset | None":
        """按名字找素材；kind 非空时限定类型（道具与场景可能重名）。"""
        for a in self.assets:
            if a.name == name and (not kind or a.kind == kind):
                return a
        return None


@dataclass
class Shot:
    """单个分镜。"""

    shot_id: int
    scene_desc: str = ""          # 中文画面描述，给人审阅
    visual_prompt: str = ""       # 英文画面提示词，给图像模型，产出这一镜的首帧图
    negative_prompt: str = ""
    video_prompt: str = ""        # 英文画面描述，= H3 官方 integrated_multimodal_description 的正文
    audio: str = ""               # 英文环境音，= H3 官方 overall_soundscape 的正文
    camera: str = ""              # 景别：远景/全景/中景/近景/特写
    motion: str = ""              # 运镜：H3 官方英文术语（Push In / Arc Shot…），见 agents.STORYBOARD_SYSTEM
    duration: float = 5.0         # 秒。官方训练区间 4-15s，低于 4s 稳定性下降；会吸附到 17 帧网格
    transition: str = "cut"       # cut=切场景独立镜头；continue=承接上一镜尾帧
    dialogue: str = ""
    character_refs: list[str] = field(default_factory=list)
    prop_refs: list[str] = field(default_factory=list)    # 该镜出现的道具名，从 project.assets 的 prop 里选
    scene_refs: list[str] = field(default_factory=list)   # 该镜所在场景名，从 project.assets 的 scene 里选（至多一个）
    image_path: str = ""
    video_path: str = ""          # 图生视频产物 mp4 路径，由 video_agent 回写

    @classmethod
    def from_dict(cls, d: dict[str, Any], shot_id: int) -> "Shot":
        return cls(
            shot_id=shot_id,
            scene_desc=str(d.get("scene_desc", "")),
            visual_prompt=str(d.get("visual_prompt", "")),
            negative_prompt=str(d.get("negative_prompt", "")),
            video_prompt=str(d.get("video_prompt", "")),
            audio=str(d.get("audio", "")),
            camera=str(d.get("camera", "")),
            motion=str(d.get("motion", "")),
            duration=float(d.get("duration", 5.0) or 5.0),
            transition=str(d.get("transition") or "cut"),
            dialogue=str(d.get("dialogue", "")),
            character_refs=[str(x) for x in (d.get("character_refs") or [])],
            prop_refs=[str(x) for x in (d.get("prop_refs") or [])],
            scene_refs=[str(x) for x in (d.get("scene_refs") or [])],
            image_path=str(d.get("image_path", "")),
            video_path=str(d.get("video_path", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BlockBeat:
    """块内时间轴的一个节拍：第几秒到第几秒在干什么。

    一块 = 一条 10s 视频。用户明确要求「每几秒的时候在干什么」要写清楚，
    beats 就是这个要求的结构化载体：给人审阅、给提示词 Agent 翻译成英文
    分秒描述、也给后续想在时间轴上精修的编辑留了抓手。
    """

    start: float = 0.0
    end: float = 0.0
    action: str = ""             # 中文：这段时间里谁在做什么
    camera: str = ""             # 这段时间的镜头怎么动（景别/运镜，可留空沿用块级）

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BlockBeat":
        return cls(
            start=float(d.get("start", 0) or 0),
            end=float(d.get("end", 0) or 0),
            action=str(d.get("action", "")),
            camera=str(d.get("camera", "")),
        )


@dataclass
class Block:
    """一个创作块 = 一条 10 秒视频。

    与旧 Shot 的本质区别：Shot 是「一镜一图一动作」（2-10s），块是**一小段戏**
    （固定 10s），内部按秒分节拍。分块而不是一次拆完，是为了让用户逐块验收：
    AI 只生成「下一块」，满意了再要下一块，故事走向随时可干预。

    characters / props / scene 是这块选用哪些资产（名字引用 project 里的角色与素材），
    提示词 Agent 会把对应锚点/音色拼进去——这就是资产阶段先行铺垫的意义。
    avoid 是「不该干什么」：写进视频提示词的禁止条款（H3 没有独立的负向通道，
    只能用 "Avoid: ..." 短句表达，措辞务必具体）。
    continue_last=True 时，本块起点不是自己的首帧图，而是上一块视频的最后一帧
    （FL2VA 首尾帧接力）——块的 10s 不够讲完一段戏时，下一块从这里接着演。
    last_frame 是本块视频生成后抽取的最后一帧，既是接力源，也是「这段戏
    演到哪里了」的直观凭证。
    """

    block_id: int
    summary: str = ""            # 中文：这一块讲什么，给人看、给 AI 接力时对齐剧情
    characters: list[str] = field(default_factory=list)
    props: list[str] = field(default_factory=list)
    scene: str = ""              # 至多一个场景名
    dialogue: str = ""           # 中文台词/旁白（说话人：台词 行式），翻译进视频提示词
    beats: list[BlockBeat] = field(default_factory=list)
    camera: str = ""             # 主景别
    motion: str = ""             # 主运镜（H3 官方英文术语，同 Shot.motion 词表）
    avoid: str = ""              # 中文：不该出现什么（翻译成 Avoid 短句）
    duration: float = 10.0       # 块固定 10s；字段留着，万一以后想支持其他时长
    continue_last: bool = False  # 承接上一块尾帧（上一块视频必须已存在）
    visual_prompt: str = ""      # 英文首帧图提示词
    negative_prompt: str = ""
    video_prompt: str = ""       # 英文 H3 正文（含分秒节拍与 Avoid 条款）
    audio: str = ""              # 英文环境音
    image_path: str = ""
    video_path: str = ""
    last_frame: str = ""         # 本块视频的最后一帧（生成视频后自动提取）

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Block":
        return cls(
            block_id=int(d.get("block_id", 0) or 0),
            summary=str(d.get("summary", "")),
            characters=[str(x) for x in (d.get("characters") or [])],
            props=[str(x) for x in (d.get("props") or [])],
            scene=str(d.get("scene", "")),
            dialogue=str(d.get("dialogue", "")),
            beats=[BlockBeat.from_dict(b) for b in (d.get("beats") or []) if isinstance(b, dict)],
            camera=str(d.get("camera", "")),
            motion=str(d.get("motion", "")),
            avoid=str(d.get("avoid", "")),
            duration=float(d.get("duration", 10.0) or 10.0),
            continue_last=bool(d.get("continue_last", False)),
            visual_prompt=str(d.get("visual_prompt", "")),
            negative_prompt=str(d.get("negative_prompt", "")),
            video_prompt=str(d.get("video_prompt", "")),
            audio=str(d.get("audio", "")),
            image_path=str(d.get("image_path", "")),
            video_path=str(d.get("video_path", "")),
            last_frame=str(d.get("last_frame", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def dump(obj: Any) -> dict[str, Any]:
    return asdict(obj)

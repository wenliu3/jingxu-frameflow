"""Agent 之间传递的数据契约。

设计要点：Shot 里的 camera / motion / duration 三个字段当前不被消费，
是给未来的「图生视频 Agent」预留的。现在让分镜 Agent 顺手填上，
未来接视频时直接可用，不需要回头重跑导演和分镜（重跑就意味着重新烧额度）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Character:
    """角色。anchor 是跨镜头保持一致的关键——固定的、可复述的外貌描述。
    image_path 是角色定妆照（阶段一产出），给用户核对，也为将来 ref2va 参考生成铺路。"""

    name: str
    anchor: str
    voice: str = ""              # 音色/声音设计（少女音清亮、低沉沙哑…），随镜头写进声音设计
    image_path: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Character":
        return cls(
            name=str(d.get("name", "")),
            anchor=str(d.get("anchor", "")),
            voice=str(d.get("voice", "")),
            image_path=str(d.get("image_path", "")),
        )


@dataclass
class Project:
    """全局设定。所有分镜共享的上下文，用来对抗「越出图越跑偏」。"""

    title: str
    logline: str
    style: str
    aspect_ratio: str
    characters: list[Character] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        return cls(
            title=str(d.get("title", "未命名")),
            logline=str(d.get("logline", "")),
            style=str(d.get("style", "")),
            aspect_ratio=str(d.get("aspect_ratio", "16:9")),
            characters=[Character.from_dict(c) for c in d.get("characters", [])],
        )

    def anchor_of(self, name: str) -> str:
        for c in self.characters:
            if c.name == name:
                return c.anchor
        return ""


@dataclass
class Shot:
    """单个分镜。"""

    shot_id: int
    scene_desc: str = ""          # 中文画面描述，给人审阅
    visual_prompt: str = ""       # 英文画面提示词，给图像模型，产出这一镜的首帧图
    negative_prompt: str = ""
    video_prompt: str = ""        # 中文动态提示词，配首帧图送图生视频模型
    audio: str = ""               # 声音设计（环境音/说话人音色），拼进 H3 提示词随视频生成
    camera: str = ""              # 景别：远景/全景/中景/近景/特写   —— 视频预留
    motion: str = ""              # 运镜：固定/缓慢推镜/横移/跟随      —— 视频预留
    duration: float = 5.0         # 秒（H3 训练区间下限 124 帧 = 5s）—— 视频预留
    transition: str = "cut"       # cut=切场景独立镜头；continue=承接上一镜尾帧
    dialogue: str = ""
    character_refs: list[str] = field(default_factory=list)
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
            image_path=str(d.get("image_path", "")),
            video_path=str(d.get("video_path", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def dump(obj: Any) -> dict[str, Any]:
    return asdict(obj)

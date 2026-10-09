"""Validated inputs for video prompt composition and candidate generation."""
from typing import Literal

from pydantic import BaseModel, Field


class VideoSource(BaseModel):
    source_node: str = Field(min_length=1, max_length=100)
    version_id: str = Field(min_length=1, max_length=100)
    file: str = Field(pattern=r"^(?:seg_[a-f0-9]{12}(?:_\d{2})?|upload_[a-f0-9]{12})\.mp4$")
    usage: Literal["reference", "continue"] = "reference"
    start: float | None = Field(None, ge=0, le=3600, allow_inf_nan=False)
    end: float | None = Field(None, gt=0, le=3600, allow_inf_nan=False)
    tail_seconds: float = Field(3, ge=0.25, le=15, allow_inf_nan=False)
    motion_reference: bool = True
    expected_sha256: str = Field("", pattern=r"^(?:[a-f0-9]{64})?$")


class ComposeBody(BaseModel):
    """单段生成的编排输入：选中的素材 + 一段描述。

    `video_prompt` 非空时**完全不碰任何模型接口** —— 这就是「只有 ComfyUI 地址、
    自己上传素材、自己写提示词」的用户能走通的那条路。
    """

    characters: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    scene: str = ""
    images: list[str] = Field(default_factory=list)   # 「其他图片」类素材名
    audios: list[str] = Field(default_factory=list)   # 「其他音频」类素材名
    description: str = Field("", max_length=2000)   # 中文口语描述（走 LLM 时才用）
    context: str = Field("", max_length=4000)       # 前情文字，不能代替当前视频描述
    video_prompt: str = Field("", max_length=8000)  # 已是英文正文时直接给，跳过 LLM
    duration: float = Field(10.0, ge=1, le=15, allow_inf_nan=False)
    video_sources: list[VideoSource] = Field(default_factory=list, max_length=3)
    use_voice: bool = False


class OptimizePromptBody(BaseModel):
    """「让 AI 帮写」的输入：选中的素材 + 一段中文口语描述。

    与 ComposeBody 的区别：这里**只产出中文**，不注入素材编号、不写英文正文。
    素材是**可选**的 —— 用户还没选素材也能先把想法写具体。
    """

    characters: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    scene: str = ""
    images: list[str] = Field(default_factory=list)
    audios: list[str] = Field(default_factory=list)
    description: str = Field("", max_length=2000)
    duration: float = 10.0
    use_voice: bool = True


class SegmentVideoBody(BaseModel):
    """单段生成的提交体。

    `frames` 保存素材引用（如 "character:0"、"scene:1"、"image:0"），
    按实际参考顺序由后端解析路径，并通过 `frame_names` 复核素材身份。
    `video_sources` 仅接受当前作品内成功的视频版本，提交前固定范围和指纹。

    `first_frame` / `last_frame` 保留旧接口的显式首尾帧引用；空首帧回退到
    第一张可用素材。当前画布的续拍起始帧由视频来源解析，不依赖旧选择器。
    """

    video_sources: list[VideoSource] = Field(default_factory=list, max_length=3)
    frames: list[str] = Field(default_factory=list, max_length=9)
    first_frame: str = ""   # 素材引用；空 = 用 frames 里第一个能出图的
    last_frame: str = ""    # 素材引用；空 = 不设尾帧
    prompt: str = Field("", max_length=20000)
    duration: float = Field(10.0, ge=1, le=15, allow_inf_nan=False)
    # 像素预算（前端「清晰度」档位）。None = 用服务配置里的全局值。
    # 与服务设置采用相同范围；不把像素预算伪装成固定 1080p。
    megapixels: float | None = Field(None, ge=0.1, le=0.98, allow_inf_nan=False)
    frame_names: dict[str, str] = Field(default_factory=dict)
    expected_backend: str | None = Field(None, pattern="^(comfyui|api)$")
    expected_workflow: str | None = Field(None, pattern="^(i2v|ref2va|api)$")
    # 「AI 帮我写」模式下用户输入的那段中文描述。**不参与出片**，只为「生成记录」
    # 留个底 —— 过几天回看时，一段英文正文（prompt）是看不懂自己当初想要什么的。
    # 手写模式没有这一层，留空即可。
    note: str = Field("", max_length=2000)
    # 编排出来的**音轨段**（环境底声 + 动作音），2026-09-19 加。
    # 只有基础模式（I2V / 首尾帧）才用得上：那边提示词是三段式，`overall_soundscape`
    # 由出片时拼（`video_provider.compose_h3_prompt` 的 audio 参数）。
    # Ref2VA 的六段式里已经自带 overall_soundscape 一段，这里传了也会被忽略。
    soundscape: str = Field("", max_length=2000)
    ratio: Literal["auto", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"] = "auto"
    resolution: Literal["custom", "480p", "720p"] = "custom"
    candidate_count: Literal[1, 2, 4] = 1
    generate_audio: bool = True
    exact_duration: bool = False
    seed: int | None = Field(None, ge=0, le=2**31 - 1, strict=True)

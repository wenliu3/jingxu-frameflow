"""分镜生成工作台的 HTTP 接口。

四个设计要点：
1. **生成是长任务**（6 镜约 90 秒），POST 必须立即返回 task_id，后台线程执行，
   前端轮询取进度。同步等待必然超时。
2. **逐镜回传**：每张图出来就更新任务状态，前端能逐张渲染，不是等全部出完才显示。
3. **任务状态放内存**：单机工具，进程重启丢失可接受，不引入数据库。
4. **每个任务独立输出目录**：outputs/{task_id}/，多任务互不覆盖。

静态产物由同一个进程挂载（/files），最终只跑一个服务，不是前后端两个进程。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

# ---------------------------------------------------------------- 服务配置
# 全部服务参数（ComfyUI 地址 / 文本模型 / 图片模型）由前端「服务配置」弹窗
# 下发，持久化到 service_config.json；.env 里的同名变量退化为兜底默认值。
# 必须在 import pipeline 之前 apply——llm/image_provider 首次读取环境变量
# 时拿到的就是面板里的值，改配置后也实时生效（llm 是调用时读取）。
CONF_PATH = os.path.join(ROOT, "service_config.json")
SERVICE_DEFAULTS = {
    "video_backend": "comfyui",   # comfyui = 自建实例 | api = 外接视频 API
    # 给 H3 喂什么图。三种明确模式，**没有「自动」**——由用户选，不由程序猜：
    #   i2v      = 首帧图生视频：拿本镜的分镜图当首帧（构图最可控）
    #   flf      = 首尾帧：上一镜尾帧 + 本镜分镜图前后夹住，动作真衔接
    #   portrait = 人物图生视频：直接用角色定妆照当首帧，**不必每镜出图**
    "video_mode": "i2v",
    "comfyui_url": "",
    "text_base_url": "https://api.deepseek.com",
    "text_api_key": "",
    "text_model": "deepseek-flash",
    "image_base_url": "https://api-inference.modelscope.cn/v1",
    "image_api_key": "",
    "image_model": "Tongyi-MAI/Z-Image-Turbo",
    "video_megapixels": "0.5",
    "video_steps": "4",
    "video_lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    # 用哪份 ComfyUI 工作流（2026-09-19 加）。**默认 i2v，不自动切**：
    #   i2v    = comfyui/h3_i2v_api.json —— 单张首帧（最多再加一张尾帧）
    #   ref2va = comfyui/h3_r2v_api.json —— 最多 9 张参考图 + 音频参考（见 REF2VA_MAX_REFS）
    # ⚠️ Ref2VA 那份要**额外下 3 个模型文件**（见那份文件里的 _必须的模型文件）。
    # 所以这里绝不能"检测到文件就自动切"—— 文件我早就放进去了，模型没下的话
    # 一自动切就变成"每次出片都失败"。必须由人显式打开。
    "video_workflow": "i2v",
    # 一段视频最多等多久（秒），2026-09-19 加。原来硬编码 1800 秒，
    # 实测 H3 跑一条 10 秒的片要 **1868 秒**（31 分钟）—— 只差 68 秒就等到成品，
    # 结果我们提前放弃：ComfyUI 那边跑完了，用户这边「生成记录」却是空的。
    # 所以默认给到 3600 秒；机器慢/片子长还可以在 service_config.json 里往上调。
    "video_timeout_s": "3600",
    "video_api_url": "",
    "video_api_key": "",
    "video_api_model": "",
    # 音频（角色音色样本）。edge = edge-tts 免费但只有 8 个音色；
    # minimax = MiniMax 开放平台，27 个中文音色且按角色气质分类，3.5 元/万字符
    "audio_provider": "edge",
    "audio_base_url": "https://api.minimaxi.com/v1",
    "audio_api_key": "",
    "audio_model": "speech-2.8-hd",
}
# 三种视频输入模式（值域）。**没有 auto**：斌哥明确要求不由程序猜（2026-09-13）。
VIDEO_MODES = ("i2v", "flf", "portrait")
# 用哪份 ComfyUI 工作流（2026-09-19）。同样是显式选择，不自动切。
VIDEO_WORKFLOWS = ("i2v", "ref2va")
# 音频后端值域。edge = edge-tts（免费，8 音色）；minimax = MiniMax 开放平台（27 音色，付费）
AUDIO_PROVIDERS = ("edge", "minimax")

_ENV_OF = {
    "video_backend": "VIDEO_BACKEND",
    "video_mode": "VIDEO_MODE",
    "comfyui_url": "COMFYUI_URL",
    "text_base_url": "DEEPSEEK_BASE_URL",
    "text_api_key": "DEEPSEEK_API_KEY",
    "text_model": "DEEPSEEK_MODEL",
    "image_base_url": "IMAGE_BASE_URL",
    "image_api_key": "MODELSCOPE_API_TOKEN",
    "image_model": "MODELSCOPE_IMAGE_MODEL",
    "video_megapixels": "H3_MEGAPIXELS",
    "video_steps": "H3_STEPS",
    "video_lora": "H3_LORA",
    "video_workflow": "H3_WORKFLOW",
    "video_timeout_s": "H3_VIDEO_TIMEOUT",
    "video_api_url": "VIDEO_API_URL",
    "video_api_key": "VIDEO_API_KEY",
    "video_api_model": "VIDEO_API_MODEL",
    "audio_provider": "AUDIO_PROVIDER",
    "audio_base_url": "AUDIO_BASE_URL",
    "audio_api_key": "AUDIO_API_KEY",
    "audio_model": "AUDIO_MODEL",
}


def _load_service_config() -> dict:
    """读盘，缺的字段依次回退：.env 同名变量 → 内置默认值。
    兼容一次旧版 comfyui_config.json（只存过隧道地址）。"""
    try:
        with open(CONF_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, json.JSONDecodeError):
        saved = {}
    if not str(saved.get("comfyui_url") or "").strip():
        try:
            with open(os.path.join(ROOT, "comfyui_config.json"), encoding="utf-8") as fh:
                legacy = json.load(fh)
            saved["comfyui_url"] = legacy.get("url") or saved.get("comfyui_url")
        except (OSError, json.JSONDecodeError):
            pass
    cfg = {}
    for key, default in SERVICE_DEFAULTS.items():
        cfg[key] = str(saved.get(key) or "").strip() or os.getenv(_ENV_OF[key], "").strip() or default
    # 值域在配置层就卡住：历史配置里存过已废弃的 auto / t2v，这里归一化，
    # 接口返回值就和实际生效值永远一致——否则前端下拉会匹配不到任何 option，
    # 显示成一个和实际行为不符的选项。
    if cfg["video_mode"] not in VIDEO_MODES:
        cfg["video_mode"] = "i2v"
    if cfg["video_workflow"] not in VIDEO_WORKFLOWS:
        cfg["video_workflow"] = "i2v"
    if cfg["audio_provider"] not in AUDIO_PROVIDERS:
        cfg["audio_provider"] = "edge"
    return cfg


def _apply_service_config(cfg: dict) -> None:
    for key, env in _ENV_OF.items():
        value = str(cfg.get(key) or "").strip()
        if value:
            os.environ[env] = value


_apply_service_config(_load_service_config())

import agents  # noqa: E402
import llm  # noqa: E402
import pipeline  # noqa: E402
import ref_plan  # noqa: E402
import tts  # noqa: E402
from image_provider import DEFAULT_NEGATIVE, create_provider  # noqa: E402
from schemas import Block, Project, Shot  # noqa: E402
from video_provider import ComfyUIVideoProvider  # noqa: E402

OUT_ROOT = os.path.join(ROOT, "outputs")
os.makedirs(OUT_ROOT, exist_ok=True)

# ComfyUI 地址等参数统一从 service_config.json 读（前端「服务配置」弹窗维护），
# 每次调用现读现用——改配置立即生效，无需重启。


def _load_comfyui_url() -> str:
    return _load_service_config()["comfyui_url"]


def _video_mode() -> str:
    """当前的视频输入模式：i2v / flf / portrait。

    现读现用——面板里改了立即生效，不需要重启。
    值域归一化已经在 _load_service_config 里做过，这里直接取即可。
    """
    return _load_service_config()["video_mode"]


def _make_video_provider():
    """按「服务配置」里的视频后端选择实例化 provider。"""
    cfg = _load_service_config()
    if cfg.get("video_backend") == "api":
        from video_provider import ApiVideoProvider

        return ApiVideoProvider(
            cfg.get("video_api_url"), cfg.get("video_api_key"), cfg.get("video_api_model")
        )
    # video_workflow = ref2va 时换成多参考图那份（2026-09-19）。默认 i2v —— 见
    # SERVICE_DEFAULTS 里的说明，**不能自动切**（模型没下就切 = 每次出片都失败）。
    workflow_path = ""
    if cfg.get("video_workflow") == "ref2va":
        workflow_path = os.path.join(ROOT, "comfyui", "h3_r2v_api.json")
        if not os.path.isfile(workflow_path):
            raise RuntimeError(
                "配置里选了 Ref2VA 工作流，但 comfyui/h3_r2v_api.json 不存在。"
                "要么把它放回去，要么把「视频工作流」改回 i2v。"
            )
    # 超时从服务配置读（见 SERVICE_DEFAULTS.video_timeout_s 那条注释：1800 秒实测不够）
    try:
        timeout_s = float(str(cfg.get("video_timeout_s") or "").strip() or 3600)
    except ValueError:
        timeout_s = 3600.0
    return ComfyUIVideoProvider(
        base_url=cfg["comfyui_url"], workflow_path=workflow_path or None,
        timeout_per_shot=timeout_s,
    )


# 「加速 LoRA」必须和「视频工作流」配套 —— 两边是各自蒸馏的，混用不报错但出来是糊的。
# 2026-09-19 斌哥问过"这个 LoRA 和那个 ref2v 有什么区别"：名字里都有"4 步"纯属巧合，
# 它们是**两条工作流的配套件**，不是同一个东西的两个档位。按底模分：
#   i2v    → minimax_h3_fl2va_pruned_bf16  → 4 步 / 8 步 fl2v LoRA（步数档位在这里才有意义）
#   ref2va → minimax_h3_ref2va_pruned_int8_convrot → Ref2V 4 步 LoRA
_I2V_LORAS = (
    "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
)
_REF2V_LORA = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"

# 每份加速 LoRA 是照哪个步数蒸馏的。前端面板也有一份（LR 里的 LORA_STEPS），改一处要改两处。
# 用途是出片前提醒"步数填得不配套"：不拦，但那条路子通常更糊更慢（2026-09-19 斌哥问
# "Ref2V 4 步 LoRA + 12 步会怎么样"）。
_LORA_STEPS = {
    "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors": 4,
    "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors": 8,
    "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors": 4,
}


def _lora_workflow_error(cfg: dict) -> str | None:
    """LoRA 与工作流不配套时返回给用户的文案（配套就返回 None）。"""
    lora = str(cfg.get("video_lora") or "").strip()
    if not lora:
        return None                      # 「不加载 LoRA」是合法选择（配 20 步以上）
    if str(cfg.get("video_workflow") or "i2v") == "ref2va":
        if lora in _I2V_LORAS:
            return (
                "「视频工作流」是 Ref2VA，但「加速 LoRA」选的是 I2V / 首尾帧那份（fl2v）。"
                f"两者不是一套（底模不同），出片会糊 —— 请把 LoRA 换成 {_REF2V_LORA}（配 4 步），"
                "或者把工作流切回 i2v。"
            )
        return None
    if lora == _REF2V_LORA:
        return (
            "「加速 LoRA」是 Ref2V 那份（多图参考专用），但「视频工作流」不是 Ref2VA。"
            "两者不是一套（底模不同），出片会糊 —— 请把工作流切成 Ref2VA，"
            "或把 LoRA 换成 fl2v 的 4 步 / 8 步那份。"
        )
    return None


def _video_config_error(cfg: dict) -> str | None:
    """生成视频前的配置自检：返回给用户的报错文案，None 表示配置就绪。"""
    lora_error = _lora_workflow_error(cfg)
    if lora_error:
        return lora_error
    if cfg.get("video_mode") == "t2v":
        return (
            "文生视频模式尚未接入。它需要一份「不含首帧图输入」的工作流"
            "（宽高改由 ResolutionSelector 提供），现有 i2v 工作流的宽高是从首帧图量的，"
            "不能直接把图摘掉。请按 docs/REF2VA.md 里的导出步骤，从 ComfyUI 模板库导出 "
            "MiniMax H3 T2V 的 API 工作流，存为 comfyui/h3_t2v_api.json"
        )
    backend = cfg.get("video_backend", "comfyui")
    if backend == "api":
        if not (cfg.get("video_api_url") and cfg.get("video_api_key") and cfg.get("video_api_model")):
            return "外接视频 API 未配置完整：需要 API 地址、Key 和模型名称（服务配置面板）"
        return None
    if not cfg.get("comfyui_url"):
        return "未配置 ComfyUI 地址：点右上角「服务配置」，把实例隧道地址填进去"
    return None


# 连不上实例时 requests 抛出的原始报错（实测）：
#   ProxyError: HTTPSConnectionPool(host='xxx.free.pinggy.net', port=443):
#   Max retries exceeded with url: /upload/image
#   (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed')))
# 这串东西里没有任何用户能照做的信息。而隧道过期恰恰是本项目最常见的故障
# （pinggy 免费隧道会断），所以直接把动作写进文案。
_CONN_ERROR_HINTS = (
    "Max retries exceeded",
    "Unable to connect",
    "Connection refused",
    "Name or service not known",
    "Tunnel connection failed",
    "Temporary failure in name resolution",
    "timed out",
)


def _video_error_text(exc: Exception) -> str:
    """出片失败的文案：连不上实例时换成能照着做的提示，其它错误保持原样。

    其它错误（比如工作流被 ComfyUI 拒了、节点报错）本身就有诊断价值，不该被
    盖成一句"连接失败"——只有连接类错误才改写。
    """
    text = f"{type(exc).__name__}: {exc}"
    is_conn = isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    ) or any(h in text for h in _CONN_ERROR_HINTS)
    if not is_conn:
        return text
    url = _load_comfyui_url()
    if not url:
        return f"没有配置 ComfyUI 地址：点右上角「服务配置」填进去。原始报错：{text[:200]}"
    return (
        f"连不上 ComfyUI（{url}）。最常见的原因是隧道过期 —— 到 GPU 实例上重跑 "
        f"deploy_comfyui.sh，把新地址填进「服务配置」。原始报错：{text[:300]}"
    )


app = FastAPI(title="AI 分镜工作台")

# 开发期允许 Vite dev server(5173) 直连；生产同源部署后其实用不到
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TASKS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()

# 图生视频任务：出一条视频要几分钟，必须异步。job 记录本身也在内存，
# 和 TASKS 同样的取舍——单机工具，重启丢了就丢了（视频文件在磁盘上）。
VIDEO_JOBS: dict[str, dict[str, Any]] = {}

# 一键批量：一个案例的所有分镜排队出片，逐镜回传进度。
BATCH_JOBS: dict[str, dict[str, Any]] = {}


class GenerateRequest(BaseModel):
    idea: str = Field(..., min_length=1, description="创意或剧情描述")
    shots: int | None = Field(
        None, ge=1, le=120, description="分镜数量；不传则由 AI 按故事节奏自行决定"
    )
    ratio: str | None = None
    concurrency: int = Field(2, ge=1, le=4)
    text_only: bool = False
    characters: list[dict] | None = Field(
        None, description="用户预配置的角色 [{name, anchor}]，anchor 留空由 AI 设计"
    )
    source_text: str | None = Field(
        None, max_length=200000, description="小说/剧本原文；给出时按改编模式处理"
    )


class ShotPatch(BaseModel):
    scene_desc: str | None = None
    audio: str | None = None
    transition: str | None = Field(None, pattern="^(cut|continue)$")
    visual_prompt: str | None = None
    negative_prompt: str | None = None
    video_prompt: str | None = None
    camera: str | None = None
    motion: str | None = None
    duration: float | None = None
    dialogue: str | None = None


class ServiceConfigPatch(BaseModel):
    video_backend: str = "comfyui"
    video_mode: str = ""
    comfyui_url: str = ""
    text_base_url: str = ""
    text_api_key: str = ""
    text_model: str = ""
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = ""
    video_megapixels: str = ""
    video_steps: str = ""
    video_lora: str = ""
    # 用哪份 ComfyUI 工作流（i2v / ref2va）。⚠️ 默认空串 —— 空串会被
    # _load_service_config 兜回 SERVICE_DEFAULTS 的 "i2v"，
    # 所以"没传这个字段"的老客户端不会被迫切到 Ref2VA。
    video_workflow: str = ""
    # 一段视频最多等多久（秒）。空串同样兜回默认值（3600）。
    video_timeout_s: str = ""
    video_api_url: str = ""
    video_api_key: str = ""
    video_api_model: str = ""
    audio_provider: str = ""
    audio_base_url: str = ""
    audio_api_key: str = ""
    audio_model: str = ""


class TaskPatch(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


class CharacterPatch(BaseModel):
    """角色编辑：名字、锚点提示词、音色描述与 TTS 音色。

    锚点是分镜提示词和定妆照共用的角色一致性描述；
    tts_voice 是 voice 那段文字描述的"降级落地"（具体一个音色 id），造音色样本用。
    """
    name: str | None = Field(None, min_length=1, max_length=40)
    anchor: str | None = Field(None, max_length=2000)
    voice: str | None = Field(None, max_length=500)
    tts_voice: str | None = Field(None, max_length=80)


class CharacterCreate(BaseModel):
    """手动添加角色。

    design=True（默认，老行为）：锚点/音色留空时由 AI 按故事设定设计（design_character）。
    design=False：只落一张空条目，**不碰任何模型接口** —— 名字可空（后端补占位名），
    给「添加素材」里"选一个类型就立刻新建一张卡片，名字/内容在卡片上填"那条流程用。
    """
    name: str = Field("", max_length=40)
    anchor: str = Field("", max_length=2000)
    voice: str = Field("", max_length=500)
    design: bool = True


class CharacterPortraitBody(BaseModel):
    """「AI 生成角色图」的入参：一段自由描述（中文即可，越长越好但不必很规范）。

    2026-09-15 斌哥定：这段描述**只用于当次出图，不写回角色的 anchor** ——
    素材工坊的用法是"选图 → 出视频"，角色**有图能用**就够，不需要维护一段角色描述。

    ratio 只作用于**正面定妆照**；四视图设定图恒 **16:9 横版**（四个视角要横向并排，
    竖版排不下，所以不跟随这个值）。取值必须是 image_provider.SIZE_TABLE 里的键。
    """

    prompt: str = Field("", max_length=1000)
    ratio: str = Field("1:1", max_length=8)


class AssetGenerateBody(BaseModel):
    """「AI 生成素材图」的入参。服务**场景 / 道具 / 其他图片**三类（不含上传型的音频）。

    与 CharacterPortraitBody 一样：这段描述**只用于当次出图，不写回 anchor**。
    ratio 留空时跟随作品的画幅（场景是全景、其他图片常被当首帧，跟随画幅最自然）。
    """

    prompt: str = Field("", max_length=1000)
    ratio: str = Field("", max_length=8)


class VoiceGenerateBody(BaseModel):
    """「AI 生成音色」的入参。

    gender 是**硬约束**（女主配男声是硬错，而且用户看不出来是模型错的），
    所以让用户显式选，不交给模型猜。取值 female / male，空串 = 不限制。

    prompt 是用户对声音的形容（"低沉沙哑、语速慢、带点疲惫"），交给配音 Agent
    理解后从当前音频后端的音色池里挑一个 id —— 池子是预置音色，**造不出新音色**，
    所以"生成音色"实际是"从池子里挑最贴的那一个"。
    """

    prompt: str = Field("", max_length=500)
    gender: str = Field("", max_length=8)


def _placeholder_name(base: str, taken: list[str]) -> str:
    """给"先落一张空卡片"的条目起个不撞车的占位名：未命名角色 / 未命名角色 2 …

    名字是分镜引用素材的键（重名会让引用关系变含糊），所以占位名也必须唯一，
    不能所有空条目都叫「未命名」。用户在那张卡片上填真名时会走 PATCH 改掉。
    """
    if base not in taken:
        return base
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


# 素材四种类型的中文名。prop/scene 是"要跨镜保持一致、需要锚点"的设定件；
# image/audio 是用户自己上传的原始素材（不参与锚点体系，也不需要生成）。
ASSET_KINDS = ("prop", "scene", "image", "audio")
ASSET_CN = {"prop": "道具", "scene": "场景", "image": "图片", "audio": "音频"}


class AssetCreate(BaseModel):
    """手动添加素材。

    design=True（默认，老行为）：锚点留空时由 AI 设计（仅对道具/场景有意义）。
    design=False：只落一张空条目、不碰模型接口，名字可空（后端补占位名）。
    """
    kind: str = Field("prop", pattern="^(prop|scene|image|audio)$")
    name: str = Field("", max_length=40)
    anchor: str = Field("", max_length=2000)
    design: bool = True


class AssetPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    anchor: str | None = Field(None, max_length=2000)


class BlockCreate(BaseModel):
    """手动添加块。字段语义与 agents.NEXT_BLOCK_SYSTEM 的产出对齐。"""
    summary: str = Field("", max_length=500)
    characters: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    scene: str = Field("", max_length=40)
    dialogue: str = Field("", max_length=4000)
    beats: list[dict] = Field(default_factory=list)
    camera: str = Field("", max_length=20)
    motion: str = Field("", max_length=40)
    avoid: str = Field("", max_length=1000)
    duration: float = Field(10.0, ge=2, le=15)
    continue_last: bool = False


class BlockPatch(BaseModel):
    """块编辑：剧情字段与提示词都可改。改剧情后可重新让 AI 写提示词。"""
    summary: str | None = Field(None, max_length=500)
    characters: list[str] | None = None
    props: list[str] | None = None
    scene: str | None = Field(None, max_length=40)
    dialogue: str | None = Field(None, max_length=4000)
    beats: list[dict] | None = None
    camera: str | None = Field(None, max_length=20)
    motion: str | None = Field(None, max_length=40)
    avoid: str | None = Field(None, max_length=1000)
    duration: float | None = Field(None, ge=2, le=15)
    continue_last: bool | None = None
    visual_prompt: str | None = None
    negative_prompt: str | None = None
    video_prompt: str | None = None
    audio: str | None = None


class NextBlockBody(BaseModel):
    """AI 生成下一块时的用户指令：点名剧情走向、出场角色等，空串表示由 AI 接力推进。"""
    instruction: str = Field("", max_length=4000)


class DraftCreate(BaseModel):
    """新建空白作品：只有标题，还没有故事。

    「素材优先」流程的起点 —— 先攒角色/场景/道具，故事可以晚点再由助手聊出来。
    """
    title: str = Field("", max_length=80)


class AssistantBody(BaseModel):
    """AI 创作助手的一轮对话。

    history 由前端回传（只取最近几轮），后端不存会话：单机工具，
    会话就是页面上的那几个气泡，重启后从问候语重开即可。
    """
    task_id: str = ""
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list)


# 组级上传（「新建作品」页六个空卡片上的上传按钮）支持的类型。
# character 落到 project.characters，其余四种落到 project.assets（kind 同名）。
MATERIAL_KINDS = ("character", "scene", "prop", "image", "audio")
MATERIAL_CN = {"character": "角色", "scene": "场景", "prop": "道具", "image": "图片", "audio": "音频"}
# 音色样本可接受的后缀：录制出来是 webm/ogg，自己剪的是 m4a/wav，都要收
VOICE_EXTS = (".mp3", ".wav", ".m4a", ".webm", ".ogg", ".aac")


def _task(task_id: str) -> dict[str, Any]:
    task = TASKS.get(task_id)
    if not task:
        # 内存里没有（服务重启过），试试从磁盘认回来
        task = _load_task_from_disk(task_id)
        if task:
            TASKS[task_id] = task
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return task


def _out_dir(task_id: str) -> str:
    return os.path.join(OUT_ROOT, task_id)


# 任务目录名就是 generate() 里生成的 12 位 hex。
# 用正则卡一下，避免把 outputs/ 根目录或手建的临时目录当成任务。
TASK_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _save_meta(task_id: str) -> None:
    """把运行态元数据落一份 task.json。

    分镜数据本来就写在 shots.json 里，这里只补那些**只存在于内存**、
    重启就会丢的东西：原始创意、统计、状态、报错。
    """
    task = TASKS.get(task_id)
    if not task:
        return
    meta = {
        "task_id": task_id,
        "idea": task.get("idea", ""),
        "status": task.get("status", ""),
        "created_at": task.get("created_at", ""),
        "stats": task.get("stats", {}),
        "flow": task.get("flow", "blocks"),
        "error": task.get("error"),
    }
    try:
        with open(os.path.join(_out_dir(task_id), "task.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _load_task_from_disk(task_id: str) -> dict[str, Any] | None:
    """把一个任务目录读回内存。至少要有 project.json 才认。

    停在角色阶段（还没拆分镜）的任务没有 shots.json，只有设定数据，
    缺了就跳过会让这类任务在重启后从作品列表里消失。
    """
    if not TASK_ID_RE.match(task_id):
        return None
    out_dir = _out_dir(task_id)
    project = _read_json(os.path.join(out_dir, "project.json"))
    if project is None:
        return None
    shots = _read_json(os.path.join(out_dir, "shots.json"))
    if not isinstance(shots, list):
        shots = []

    # shots.json 里的 image_path 是生成时的绝对路径，换机器或挪目录后就是废的，
    # 所以按目录里实际存在的文件名重新指一次。
    img_dir = os.path.join(out_dir, "images")
    for s in shots:
        name = f"shot_{int(s.get('shot_id') or 0):02d}.png"
        s["image_path"] = name if os.path.isfile(os.path.join(img_dir, name)) else ""
        s.setdefault("video_prompt", "")
        # 视频同理：磁盘上有就认回来，前端才有东西可播
        vid_rel = f"videos/{name.replace('.png', '.mp4')}"
        s["video_path"] = vid_rel if os.path.isfile(os.path.join(out_dir, vid_rel)) else ""

    # 角色定妆照同理：project.json 里存的是生成时的绝对路径，换机器或挪目录后就是废的。
    # 按 characters/ 里实际存在的文件重新指一遍——少了这一步，
    # 「无分镜图生视频」在换机器重启后会找不到角色图而直接失败。
    cdir = os.path.join(out_dir, "characters")
    for c in project.get("characters", []):
        safe = pipeline._safe_char_name(str(c.get("name", "")))
        found = []
        for suffix in ("", "_side", "_back", "_full"):
            fp = os.path.join(cdir, f"{safe}{suffix}.png")
            if os.path.isfile(fp):
                found.append(fp)
        c["images"] = found
        front = os.path.join(cdir, f"{safe}.png")
        c["image_path"] = front if os.path.isfile(front) else (found[0] if found else "")
        # 四视图设定图原图：只作总览与留档，**不进 images**（下游要的是单人图）
        sheet = os.path.join(cdir, f"{safe}_sheet.png")
        c["sheet"] = sheet if os.path.isfile(sheet) else ""
        # 旧任务可能存过 ref_image（2026-09-14 取消参考图），清掉避免僵尸字段
        c.pop("ref_image", None)
        # 音色样本只存文件名，按文件在不在判存 —— 缺了就清空，前端据此显示"未生成"。
        # 用前缀扫描而不是卡死 voice_{名}.mp3：录制的样本是 webm/wav，
        # 后缀跟实际内容走（见 upload_character_voice）。
        found_voice = ""
        if os.path.isdir(cdir):
            for fn in sorted(os.listdir(cdir)):
                if fn.startswith(f"voice_{safe}.") and os.path.isfile(os.path.join(cdir, fn)):
                    found_voice = fn
                    break
        c["voice_sample"] = found_voice

    # 素材图同理重指（换机器/挪目录后 project.json 里的绝对路径全废）。
    adir = os.path.join(out_dir, "assets")
    # 文件名约定与 _asset_file_name 一一对应，改一处就要改另一处
    _ASSET_FILE_NAME = {
        "scene": ("scene_{safe}", ".png"),
        "image": ("img_{safe}", ".png"),
    }
    for a in project.get("assets", []):
        kind = str(a.get("kind", "prop"))
        safe = pipeline._safe_char_name(str(a.get("name", "")))
        if kind == "audio":
            # 音频后缀不固定（上传 .mp3、录制 .webm），按 snd_{名}. 前缀扫描
            found_audio = ""
            if os.path.isdir(adir):
                for fn in sorted(os.listdir(adir)):
                    if fn.startswith(f"snd_{safe}.") and os.path.isfile(os.path.join(adir, fn)):
                        found_audio = fn
                        break
            a["images"] = [os.path.join(adir, found_audio)] if found_audio else []
        elif kind in _ASSET_FILE_NAME:
            stem, ext = _ASSET_FILE_NAME[kind]
            fp = os.path.join(adir, stem.format(safe=safe) + ext)
            a["images"] = [fp] if os.path.isfile(fp) else []
        else:
            found = []
            for suffix in ("_front", "_side"):
                fp = os.path.join(adir, f"prop_{safe}{suffix}.png")
                if os.path.isfile(fp):
                    found.append(fp)
            a["images"] = found
            # 道具的三视图设定图（「AI 生成」那条路产出的）：只作总览与留档，不进 images
            prop_sheet = os.path.join(adir, f"prop_{safe}_sheet.png")
            a["sheet"] = prop_sheet if os.path.isfile(prop_sheet) else ""
        # 其他类型的素材没有设定图，补空字段避免读的时候到处判 None
        a.setdefault("sheet", "")
        # 旧任务里可能存过 ref_image（2026-09-14 已取消参考图/图生图），清掉避免僵尸字段
        a.pop("ref_image", None)

    # 块数据（分块流程）同理：路径按目录里实际存在的文件重指。
    blocks_raw = _read_json(os.path.join(out_dir, "blocks.json"))
    if not isinstance(blocks_raw, list):
        blocks_raw = []
    for b in blocks_raw:
        bid = int(b.get("block_id") or 0)
        img_name = f"block_{bid:02d}.png"
        b["image_path"] = img_name if os.path.isfile(os.path.join(img_dir, img_name)) else ""
        vid_rel = f"videos/block_{bid:02d}.mp4"
        b["video_path"] = vid_rel if os.path.isfile(os.path.join(out_dir, vid_rel)) else ""
        lf_rel = f"frames/block_{bid:02d}_last.png"
        b["last_frame"] = lf_rel if os.path.isfile(os.path.join(out_dir, lf_rel)) else ""

    # 阶段进度推断：有图=全流程走完；有 shots.json=停在提示词；只有设定=停在角色
    any_image = any(s.get("image_path") for s in shots)
    stage_state = "directed"
    if any_image:
        stage_state = "imaged"
    elif shots:
        stage_state = "storyboarded"
    if blocks_raw:
        stage_state = "blocks"
    # 「素材优先」建的空白作品：没有故事也没有块 —— 认成 draft，
    # 前端据此显示「未开始」，而不是「待确认故事」（后者暗示有故事可确认）。
    if not blocks_raw and not str(project.get("logline") or "").strip():
        stage_state = "draft"

    # 流程类型：分块（新）或分镜（旧任务）。磁盘上有 blocks.json 的一律按分块认，
    # 其余看 task.json 里记录的 flow，再兜底按「没有块数据」的旧任务处理。
    meta = _read_json(os.path.join(out_dir, "task.json")) or {}
    flow = "blocks" if blocks_raw else str(meta.get("flow") or "shots")
    if flow not in ("blocks", "shots"):
        flow = "shots"

    created = meta.get("created_at") or datetime.fromtimestamp(
        os.path.getmtime(out_dir), tz=timezone.utc
    ).isoformat()

    return {
        "task_id": task_id,
        "idea": meta.get("idea", ""),
        "status": meta.get("status") or "succeeded",
        "stage": "完成",
        "stage_state": stage_state,
        "flow": flow,
        "done": sum(1 for s in shots if s.get("image_path")),
        "total": len(shots),
        "project": project,
        "shots": shots,
        "blocks": blocks_raw,
        "stats": meta.get("stats", {}),
        "error": meta.get("error"),
        "created_at": created,
    }


def _scan_tasks() -> None:
    """启动时扫一遍 outputs/，把磁盘上的历史任务认回来。

    这是「任务列表不依赖浏览器」的关键一步：任务数据本来就在磁盘上，
    缺的只是让后端重新认识它们。
    """
    if not os.path.isdir(OUT_ROOT):
        return
    loaded = 0
    for name in os.listdir(OUT_ROOT):
        if not TASK_ID_RE.match(name):
            continue
        task = _load_task_from_disk(name)
        if task:
            TASKS[name] = task
            loaded += 1
    if loaded:
        print(f"[启动] 从磁盘恢复 {loaded} 个历史任务")


def _reload(task_id: str) -> tuple[Project | None, list[Shot]]:
    """把内存里的任务分镜读回成 Shot 对象。统一走 Shot.from_dict，
    新增字段（audio/transition/voice…）不需要记得同步这里。"""
    project = None
    shot_list: list[Shot] = []
    task = TASKS.get(task_id) or {}
    if task.get("project"):
        project = Project.from_dict(task["project"])
    for raw in task.get("shots", []):
        shot_list.append(Shot.from_dict(raw, int(raw["shot_id"])))
    return project, shot_list


def _merge_entry_extras(merged: dict[str, Any], raw: dict[str, Any]) -> None:
    """把 raw 条目上**不属于 dataclass 的自定义键**补回 dump 出来的 project。

    `Project.from_dict` / `Character.from_dict` / `Asset.from_dict` 只认声明过的字段，
    `asdict` 也只吐声明过的字段 —— 于是挂在条目上的自定义键（`version` / `stale` /
    `ai_last`）在 `_persist()` 写 project.json 时会被**悄悄丢掉**。
    实测代价：磁盘上真实作品的 project.json 里连 `version` 都没有 ——
    一重启后端，卡片 URL 就少了 `?v=`，cache-busting 失效，又变回
    "重新生成后要刷新浏览器才看到新图"（2026-09-19 斌哥报过的那条）。

    两边按下标一一对应：dataclass 的顺序就是 raw 的顺序（都是从同一个 raw 建出来的）。
    只补 `merged` 里没有的键，不动 dataclass 认得的字段。
    """
    for key in ("characters", "assets"):
        m_list = merged.get(key) or []
        r_list = raw.get(key) or []
        for m, r in zip(m_list, r_list):
            if not isinstance(m, dict) or not isinstance(r, dict):
                continue
            for k, v in r.items():
                if k not in m:
                    m[k] = v


def _persist(task_id: str) -> None:
    """把内存里的状态同步写回 project.json、shots.json 与 blocks.json。

    ⚠️ project.json 写的是**合并后**的 dict，不是裸的 `dump(project)` ——
    条目上还有 dataclass 不认的自定义键（见 `_merge_entry_extras`），
    直接 dump 会让它们每写一次盘就丢一次。
    """
    task = _task(task_id)
    raw = task.get("project") or {}
    project = Project.from_dict(raw) if raw else None
    shots = [
        Shot.from_dict(r, int(r["shot_id"]))
        for r in task.get("shots", [])
    ]
    if project:
        merged = pipeline.dump(project)
        _merge_entry_extras(merged, raw)
        pipeline._save_outputs(_out_dir(task_id), project, shots, project_json=merged)
    if task.get("blocks") is not None:
        pipeline._save_blocks(
            _out_dir(task_id),
            sorted(task["blocks"], key=lambda b: int(b.get("block_id") or 0)),
        )


def _make_progress(task: dict) -> Callable[[str, dict], None]:
    """把 pipeline 的 on_progress 事件落到任务状态。task 是共享 dict，
    所有写操作都拿 LOCK。"""

    def on_progress(stage: str, info: dict) -> None:
        with LOCK:
            if stage == "stage":
                task["stage"] = info.get("label", "")
                task["status"] = "running"
                if "total" in info:
                    task["total"] = info["total"]
            elif stage == "project":
                task["project"] = info["project"]
            elif stage in ("shots", "prompts"):
                task["shots"] = info["shots"]
                task["total"] = len(info["shots"])
            elif stage == "shot_done":
                for s in task["shots"]:
                    if s["shot_id"] == info["shot_id"]:
                        s["image_path"] = info["path"]
                        break
                task["done"] += 1
                if task["total"]:
                    task["done"] = min(task["done"], task["total"])
            elif stage == "character_done":
                # 四视角是逐张回传的：追加进 images（同名去重），正面那张同时更新 image_path。
                # 中间态的 images 可能不完整（已存在而被跳过的视角不会发事件），
                # 但导演阶段结束时会再发一次完整的 project 事件，最终状态以它为准。
                for c in (task.get("project") or {}).get("characters", []):
                    if c.get("name") == info["name"]:
                        path = info["path"]
                        imgs = c.setdefault("images", [])
                        if path not in imgs:
                            imgs.append(path)
                        if info.get("view") == "front" or not c.get("image_path"):
                            c["image_path"] = path
                        break

    return on_progress


def _run_task(task_id: str, req: GenerateRequest) -> None:
    """阶段一：导演 + 角色定妆照。完成后停在「角色就绪」等用户验收。"""
    task = TASKS[task_id]
    on_progress = _make_progress(task)
    try:
        pipeline.stage_director(
            req.idea,
            _out_dir(task_id),
            shot_count=req.shots,
            ratio=req.ratio,
            on_progress=on_progress,
            characters=req.characters,
            source_text=req.source_text,
        )
        with LOCK:
            chars = (task.get("project") or {}).get("characters", [])
            assets = (task.get("project") or {}).get("assets", [])
            task["status"] = "succeeded"
            task["stage"] = "资产就绪"
            task["stage_state"] = "directed"
            task["done"] = len(chars) + len(assets)
            task["total"] = len(chars) + len(assets)
            _save_meta(task_id)
    except Exception as exc:
        with LOCK:
            task["status"] = "failed"
            task["stage"] = "失败"
            task["error"] = f"{type(exc).__name__}: {exc}"
            task["traceback"] = traceback.format_exc()[-2000:]
            _save_meta(task_id)


def _run_stage_storyboard(task_id: str) -> None:
    """阶段二：分镜拆解 + 提示词台词。完成后停在「提示词就绪」。"""
    task = TASKS[task_id]

    def on_progress(stage: str, info: dict) -> None:
        with LOCK:
            if stage == "stage":
                task["stage"] = info.get("label", "")
                task["status"] = "running"
            elif stage in ("shots", "prompts"):
                task["shots"] = info["shots"]
                task["total"] = len(info["shots"])

    try:
        project = Project.from_dict(task["project"])
        pipeline.stage_storyboard(
            _out_dir(task_id), project, task.get("shot_count"), on_progress
        )
        with LOCK:
            task["status"] = "succeeded"
            task["stage"] = "提示词就绪"
            task["stage_state"] = "storyboarded"
            _save_meta(task_id)
    except Exception as exc:
        with LOCK:
            task["status"] = "failed"
            task["stage"] = "失败"
            task["error"] = f"{type(exc).__name__}: {exc}"
            task["traceback"] = traceback.format_exc()[-2000:]
            _save_meta(task_id)


def _run_stage_images(task_id: str) -> None:
    """阶段四：出图。只画还没有图片的分镜。"""
    task = TASKS[task_id]

    def on_progress(stage: str, info: dict) -> None:
        with LOCK:
            if stage == "stage":
                task["stage"] = info.get("label", "")
                task["status"] = "running"
                if "total" in info:
                    task["total"] = info["total"]
            elif stage == "shot_done":
                for s in task["shots"]:
                    if s["shot_id"] == info["shot_id"]:
                        s["image_path"] = info["path"]
                        break
                task["done"] += 1
                if task["total"]:
                    task["done"] = min(task["done"], task["total"])

    try:
        project, shots = _reload(task_id)
        concurrency = int(task.get("concurrency") or 2)
        stats = pipeline.stage_images(
            _out_dir(task_id), project, shots, concurrency, on_progress,
        )
        with LOCK:
            task["shots"] = [s.to_dict() for s in shots]
            task["stats"] = stats
            task["status"] = "succeeded"
            task["stage"] = "完成"
            task["stage_state"] = "imaged"
            _save_meta(task_id)
    except Exception as exc:
        with LOCK:
            task["status"] = "failed"
            task["stage"] = "失败"
            task["error"] = f"{type(exc).__name__}: {exc}"
            task["traceback"] = traceback.format_exc()[-2000:]
            _save_meta(task_id)


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        "task_id": task_id,
        "idea": req.idea,
        "status": "running",
        "stage": "排队中",
        "stage_state": "new",
        "flow": "blocks",            # 新任务一律走「资产准备 → 分块编排」流程
        "shot_count": req.shots,
        "concurrency": req.concurrency,
        "done": 0,
        "total": req.shots,
        "project": None,
        "shots": [],
        "blocks": [],
        "stats": {},
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    threading.Thread(target=_run_task, args=(task_id, req), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/tasks")
def list_tasks() -> list[dict]:
    """任务列表。以磁盘为准，服务重启、换浏览器、清缓存都不会丢。"""
    with LOCK:
        items = [
            {
                "task_id": t["task_id"],
                "idea": t.get("idea", ""),
                "title": (t.get("project") or {}).get("title", ""),
                "shots": len(t.get("shots") or []),
                "status": t["status"],
                "created_at": t.get("created_at", ""),
            }
            for t in TASKS.values()
        ]
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    with LOCK:
        task = _task(task_id)
        return {
            "task_id": task["task_id"],
            "idea": task.get("idea", ""),
            "status": task["status"],
            "stage": task["stage"],
            "stage_state": task.get("stage_state", "new"),
            "stage_note": task.get("stage_note", ""),
            "flow": task.get("flow", "shots"),
        "done": task["done"],
        "total": task["total"],
        "project": task["project"],
        "shots": task["shots"],
        "blocks": task.get("blocks", []),
            "stats": task.get("stats", {}),
            "error": task["error"],
            "traceback": task.get("traceback"),
            "created_at": task.get("created_at", ""),
        }


@app.patch("/api/tasks/{task_id}")
def rename_task(task_id: str, body: TaskPatch) -> dict:
    """侧栏重命名：改 project.title 并同步写回 project.json / preview.html。"""
    with LOCK:
        task = _task(task_id)
        if not task.get("project"):
            raise HTTPException(status_code=409, detail="该任务还没有设定数据，无法重命名")
        task["project"]["title"] = body.title.strip()
        _persist(task_id)
    return {"task_id": task_id, "title": task["project"]["title"]}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, confirm: bool = False, purge: bool = False) -> dict:
    """删除历史任务：目录移入 outputs/_trash/ 回收站而不是直接删，
    手滑了还能从那里把图片和视频整包拿回来。

    ⚠️ `confirm` 是协议级的确认闸：**不带 `?confirm=true` 一律 409**。
    加它是因为 2026-09-14 实测：浏览器自动化用「按文字点击」时，会命中
    删除按钮的 aria-label（里面含作品名），一次点击就删掉一个作品 ——
    把确认抬到协议层，自动化工具怎么点都删不掉。

    `purge=true`：移进回收站后**顺手真删掉**，不留垃圾。给验证脚本收尾用 ——
    它们的收尾一直是软删除，跑一次就在回收站留一条，日积月累堆了 200+ 条（2026-09-19 发现）。
    ⚠️ 删不掉**不算失败**（响应仍是 200，多一个 `purged:false` 和 `note`）：
    那时它已经躺在回收站里了，用户可以在界面上清掉；报错只会让脚本误以为收尾失败。
    """
    if not confirm:
        raise HTTPException(
            status_code=409,
            detail="删除需要确认：请带 ?confirm=true 再请求（防误删）",
        )
    with LOCK:
        _task(task_id)
        TASKS.pop(task_id, None)
    src = _out_dir(task_id)
    moved = ""
    if os.path.isdir(src):
        trash = os.path.join(OUT_ROOT, "_trash")
        os.makedirs(trash, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        moved = os.path.join(trash, f"{stamp}_{task_id}")
        try:
            shutil.move(src, moved)
        except OSError:
            shutil.rmtree(src, ignore_errors=True)
            moved = ""
    out: dict[str, Any] = {"deleted": task_id}
    if purge and moved:
        why = _remove_trash_dir(moved)
        out["purged"] = not why
        if why:
            out["note"] = why
    return out


# ---------------------------------------------------------------- 回收站
# 删除一直是**软删除**（delete_task 把目录移到 outputs/_trash/），但移进去就再也看不见了 ——
# 没有列表、没法还原、也没法真删。2026-09-19 斌哥要求给它一个出口：
# 「回收站里面可以清除或者恢复，清除就是完全消失了，恢复就是回到我的作品」。


def _trash_root() -> str:
    return os.path.join(OUT_ROOT, "_trash")


# 彻底删除被环境拦住时的说明。WorkBuddy 会给它启动的 Python 进程注入一个
# 「安全删除」shim（把 os.remove / os.rmdir / shutil.rmtree 全换成"移到系统回收站"），
# 并且**按会话累计删除的文件数**，超过阈值（默认 50）就要求人工确认 ——
# 那个确认信号由 WorkBuddy 客户端弹窗产生，而我们的后端是常驻服务，永远收不到。
# 用户自己在本机终端启动后端就没有这层 shim，删除会正常工作。
_PURGE_BLOCKED_HINT = (
    "彻底删除被拦下了：当前后端跑在 WorkBuddy 沙箱里，它给删除函数套了安全护栏"
    "（每个会话累计最多约 50 个文件，超过要人工确认），而这个确认在常驻服务里收不到。"
    "请自己在本机终端启动后端（cd server && python -m uvicorn app:app --port 8000），"
    "那边没有这层护栏，「彻底删除」会正常工作。"
)


# delete_task 写进去的目录名就是 `%Y%m%d_%H%M%S_{task_id}`
_TRASH_NAME_RE = re.compile(r"^(\d{8})_(\d{6})_([0-9a-f]{12})$")


def _trash_dir_size(path: str) -> int:
    """整个作品目录占多少字节。**读不到就当 0** —— 列表不该因为一个坏文件打不开。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total


def _trash_entries() -> list[dict[str, Any]]:
    """扫 `outputs/_trash/`，把 delete_task 移进来的目录解析成条目。

    ⚠️ **只认「时间戳_作品id」这个形状**：`_trash/` 里还散着别的历史垃圾（早期测试、手工丢的），
    一并列出来会让用户以为那也是自己的作品。
    """
    root = _trash_root()
    if not os.path.isdir(root):
        return []
    entries: list[dict[str, Any]] = []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        m = _TRASH_NAME_RE.match(name)
        if not m or not os.path.isdir(full):
            continue
        try:
            stamp = datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S")
            deleted_at = stamp.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:                                  # pragma: no cover
            deleted_at = ""
        title = ""
        pj = os.path.join(full, "project.json")
        if os.path.isfile(pj):
            try:
                with open(pj, encoding="utf-8") as fh:
                    title = str(json.load(fh).get("title") or "")
            except (OSError, json.JSONDecodeError):
                pass
        entries.append({
            "name": name,
            "task_id": m.group(3),
            "title": title,
            "deleted_at": deleted_at,
            "bytes": _trash_dir_size(full),
        })
    # 最近删的在最前（目录名本身就是时间戳，倒序即可）
    entries.sort(key=lambda e: e["name"], reverse=True)
    return entries


def _resolve_trash(name: str) -> tuple[str, str]:
    """把回收站条目名解析成 `(绝对路径, task_id)`。

    **防穿越**：只接受裸目录名且形状严格（`%Y%m%d_%H%M%S_{12位id}`），`../` 之类一律 400。
    """
    m = _TRASH_NAME_RE.match(name)
    if not m or os.path.basename(name) != name:
        raise HTTPException(status_code=400, detail="回收站条目名不合法")
    full = os.path.join(_trash_root(), name)
    if not os.path.isdir(full):
        raise HTTPException(status_code=404, detail="回收站里没有这一条")
    return full, m.group(3)


def _remove_trash_dir(src: str) -> str:
    """真删一个回收站目录。成功返回 `""`，失败返回**给人看的**原因。

    抽出来给两处共用：`purge_trash`（用户点「彻底删除」）和
    `delete_task(purge=True)`（验证脚本收尾，不想在回收站留垃圾）。

    **绝不抛异常** —— 调用方要的是"删没删掉"，不是 traceback。这里的异常一旦冒到 ASGI，
    Starlette 会把它变成一句 `text/plain` 的 "Internal Server Error"，前端连原因都拿不到。
    ⚠️ 沙箱的安全删除护栏抛的是 **`SystemExit` 而不是 `OSError`**，所以两个都要接。
    """
    try:
        shutil.rmtree(src)
    except SystemExit:                                      # 护栏在动手之前就拦下了
        return _PURGE_BLOCKED_HINT
    except OSError as exc:
        return f"彻底删除失败：{exc}"
    if os.path.exists(src):                                 # 没抛异常但目录还在 → 也是被拦了
        return _PURGE_BLOCKED_HINT
    return ""


@app.get("/api/trash")
def list_trash() -> dict:
    """回收站里有什么（删过的作品，还占着磁盘）。"""
    return {"entries": _trash_entries()}


@app.post("/api/trash/{name}/restore")
def restore_trash(name: str) -> dict:
    """从回收站恢复回「我的作品」—— 目录移回 `outputs/{task_id}` 并重新认领。"""
    src, task_id = _resolve_trash(name)
    dst = _out_dir(task_id)
    if os.path.exists(dst):
        raise HTTPException(
            status_code=409,
            detail=f"作品 {task_id} 已经在列表里了：先把它删掉，再恢复这一条",
        )
    with LOCK:
        TASKS.pop(task_id, None)
    try:
        shutil.move(src, dst)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"恢复失败：{exc}") from exc
    with LOCK:
        task = _load_task_from_disk(task_id)
        if task:
            TASKS[task_id] = task
    return {"restored": task_id}


@app.delete("/api/trash/{name}")
def purge_trash(name: str, confirm: bool = False) -> dict:
    """**彻底删掉**回收站里的一条 —— 从磁盘上消失，恢复不了。

    和 `delete_task` 一样，`confirm` 是**协议级**的确认闸（不带 `?confirm=true` 一律 409）：
    浏览器自动化按文字点击会命中按钮的 aria-label，把确认抬到协议层才拦得住。
    """
    if not confirm:
        raise HTTPException(
            status_code=409,
            detail="彻底删除需要确认：请带 ?confirm=true 再请求（这一步恢复不了）",
        )
    src, _task_id = _resolve_trash(name)
    with LOCK:
        TASKS.pop(_task_id, None)
    why = _remove_trash_dir(src)
    if why:
        raise HTTPException(status_code=500, detail=why)
    return {"purged": name}


# ---------------------------------------------------------------- 分阶段流程
# 阶段一（/api/generate）：导演 + 角色定妆照 → 停在 directed 等验收
# 阶段二（stage/storyboard）：分镜 + 提示词台词 → 停在 storyboarded 等验收
# 阶段三（stage/images）：出图，只画缺图的分镜 → imaged
# 视频生成接口在后面，语义不变。


def _start_stage_thread(task_id: str, stage_key: str, runner) -> None:
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        task["status"] = "running"
        task["stage"] = "排队中"
        task["error"] = None
        task["traceback"] = None
    threading.Thread(target=runner, args=(task_id,), daemon=True).start()


@app.post("/api/tasks/{task_id}/stage/storyboard")
def run_storyboard_stage(task_id: str) -> dict:
    with LOCK:
        task = _task(task_id)
        if not task.get("project"):
            raise HTTPException(status_code=409, detail="请先完成角色生成阶段")
    _start_stage_thread(task_id, "storyboard", _run_stage_storyboard)
    return {"stage": "storyboard"}


@app.post("/api/tasks/{task_id}/stage/images")
def run_images_stage(task_id: str) -> dict:
    with LOCK:
        task = _task(task_id)
        if not task.get("shots"):
            raise HTTPException(status_code=409, detail="请先完成分镜与提示词阶段")
    _start_stage_thread(task_id, "images", _run_stage_images)
    return {"stage": "images"}


@app.patch("/api/tasks/{task_id}/characters/{index}")
def patch_character(task_id: str, index: int, patch: CharacterPatch) -> dict:
    """编辑角色：改名字或锚点提示词。

    锚点变了，旧定妆照就不再对应新形象，标记 stale 让前端提示重新生成；
    名字查重是因为分镜的角色引用按名字关联，重名会让引用关系变含糊。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="任务执行中，请等结束后再修改角色")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        char = chars[index]
        changed = False
        if patch.name is not None:
            new_name = patch.name.strip()
            if not new_name:
                raise HTTPException(status_code=422, detail="角色名不能为空")
            if new_name != char.get("name") and any(
                j != index and c.get("name") == new_name for j, c in enumerate(chars)
            ):
                raise HTTPException(status_code=422, detail=f"已有同名角色：{new_name}")
            if new_name != char.get("name"):
                char["name"] = new_name
                changed = True
        if patch.anchor is not None and patch.anchor.strip() != char.get("anchor"):
            char["anchor"] = patch.anchor.strip()
            char["stale"] = True
            changed = True
        voice_changed = False
        if patch.voice is not None and patch.voice.strip() != char.get("voice", ""):
            char["voice"] = patch.voice.strip()
            voice_changed = True
            changed = True
        if patch.tts_voice is not None and patch.tts_voice.strip() != char.get("tts_voice", ""):
            # 只改音色 id 不动样本文件：样本要用户点「重新生成音色」才重合成
            # （否则每次编辑角色都白跑一次 TTS，而 edge-tts 是要联网的）
            char["tts_voice"] = patch.tts_voice.strip()
            changed = True
        # 音色描述变了 → TTS 音色必须跟着走。否则「描述写低沉沙哑、实际用少女音」
        # 这种不一致会一直挂着，而它同时污染 I2VA 的自编音色与 Ref2VA 的克隆音色。
        # 挑不出（描述里没有性别线索）就保留原值，不硬猜。
        # 旧样本对应旧音色，已过期：清字段并删文件，前端会显示「未生成样本」提示重做。
        if voice_changed and patch.tts_voice is None:
            picked = tts.pick_voice(char["voice"])
            if picked:
                char["tts_voice"] = picked
                fname = str(char.get("voice_sample") or "")
                if fname:
                    try:
                        old = os.path.join(_out_dir(task_id), "characters", fname)
                        if os.path.isfile(old):
                            os.remove(old)
                    except OSError:
                        pass
                    char["voice_sample"] = ""
        if changed:
            _persist(task_id)
        return dict(char)


# ---------------------------------------------------------------- 生成历史（版本快照）
# 「重新生成」是**就地覆盖同名文件**（characters/金蝉.png），旧图直接被顶掉 ——
# 用户重生成之后觉得还不如上一版，就再也找不回来了（2026-09-19 斌哥报的）。
# 这里在每次写图**之前**把当前这组图复制一份：
#
#   outputs/{task_id}/history/{kind}/{safe}/{UTC时间戳}_{当时的version}/<原文件名>
#   例：outputs/1a2b3c4d5e6f/history/character/金蝉/20260919_051122_ab12cd/金蝉_sheet.png
#
# ⚠️ 快照是**复制**不是搬走 —— 生成器随后要往原路径写新图，原文件得留在那儿被覆盖。
# ⚠️ 历史目录**不进** `images` / `sheet` 字段：它只是留档，下游出片只认 image_path；
#    混进 images 会让视频模型把每一版都当成一张参考图。
# ⚠️ 目录名第一段是 UTC 时间戳，只作**可读标识**；排序/裁剪一律按目录 mtime ——
#    时间戳只到秒，同一秒里的两版靠目录名排会随机颠倒（详见 `_history_versions`）。
# ⚠️ 覆盖式的**上传**也走同一套（用户传了新图想找回上一张，是同一个诉求）。

HISTORY_KEEP = 10          # 每个素材最多留最近几版
_HISTORY_NAME_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{6}$")
_HISTORY_KINDS = ("character", "scene", "prop", "image")


def _history_dir(task_id: str, kind: str, safe: str) -> str:
    return os.path.join(_out_dir(task_id), "history", kind, safe)


def _entry_field(entry: Any, field: str, default: Any) -> Any:
    """同一段逻辑既要吃 pydantic 对象也要吃 dict，统一取值。"""
    return entry.get(field, default) if isinstance(entry, dict) else getattr(entry, field, default)


def _material_files(kind: str, entry: Any) -> list[str]:
    """这个素材当前的全部图（打快照、判断"有没有东西可存"都用它）。"""
    out: list[str] = []
    for p in [_entry_field(entry, "image_path", ""),
              *(_entry_field(entry, "images", None) or []),
              _entry_field(entry, "sheet", "")]:
        if p and p not in out:
            out.append(p)
    return out


def _history_iso(name: str) -> str:
    """版本目录名 20260919_051122_ab12cd → ISO 时间；解析不了给空串。"""
    try:
        dt = datetime.strptime(name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return dt.isoformat()


def _history_versions(task_id: str, kind: str, safe: str) -> list[dict[str, Any]]:
    """某个素材的历史版本，新的在前。每项 {name, created_at, bytes, files}

    ⚠️ 排序按**目录 mtime** 而不是目录名。目录名里的时间戳只精确到秒，同一秒里存两版
    （比如"切回上一版"紧跟一次"重新生成"）就只能拿版本号那 6 位随机 hex 当第二关键字，
    顺序会**随机颠倒** —— 而 `_prune_history` 正是靠这个顺序决定"哪些是最老的"，
    排错就会删掉该留的。mtime 在 Windows 上到 100ns，够用；目录名只作可读标识。
    """
    root = _history_dir(task_id, kind, safe)
    if not os.path.isdir(root):
        return []
    rows: list[tuple[float, str]] = []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if not os.path.isdir(full) or not _HISTORY_NAME_RE.match(name):
            continue
        try:
            rows.append((os.path.getmtime(full), name))
        except OSError:
            continue
    out: list[dict[str, Any]] = []
    for _mtime, name in sorted(rows, reverse=True):
        full = os.path.join(root, name)
        files = sorted(
            f for f in os.listdir(full)
            if os.path.isfile(os.path.join(full, f)) and not f.startswith(".")
        )
        if not files:
            continue
        out.append({
            "name": name,
            "files": files,
            "bytes": sum(os.path.getsize(os.path.join(full, f)) for f in files),
            "created_at": _history_iso(name),
        })
    return out


def _prune_history(task_id: str, kind: str, safe: str) -> None:
    """只留最近 HISTORY_KEEP 版。删不掉（沙箱护栏等）就算了 —— 绝不让出图失败。"""
    for old in _history_versions(task_id, kind, safe)[HISTORY_KEEP:]:
        try:
            shutil.rmtree(os.path.join(_history_dir(task_id, kind, safe), old["name"]))
        except Exception:                       # noqa: BLE001 - 收尾失败不影响出图
            pass


def _drop_history_dir(task_id: str, kind: str, safe: str, name: str) -> None:
    """删掉某一版历史目录（**只给「用这版」切完之后收尾用**）。

    切完那一版的内容已经原样复制回当前文件了，而「当前这版」永远排在列表第一条 ——
    目录再留着，弹窗里同一张图就会出现两次。删不掉就算了，绝不让切版失败。
    """
    if not _HISTORY_NAME_RE.match(name or ""):
        return
    try:
        shutil.rmtree(os.path.join(_history_dir(task_id, kind, safe), name))
    except Exception:                           # noqa: BLE001 - 收尾失败不影响切版
        pass


def _file_sig(path: str) -> tuple[int, str]:
    """(字节数, 内容 md5)。读不到给 (-1, "") —— 让它永远不会跟别的版本判定成同一张。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return (-1, "")
    return (len(data), hashlib.md5(data).hexdigest())


def _files_signature(paths: list[str]) -> tuple[tuple[int, str], ...]:
    """一组图的**内容**指纹。只比文件名或大小会误判（同一模型重生成出来的大小经常很接近），
    所以按内容哈希比 —— 用来认出"这两版其实根本就是同一张图"。

    刻意不带文件名：同一张图换个名字存两次，照样该被认成重复。
    """
    return tuple(sorted(_file_sig(p) for p in paths))


def _snapshot_history(task_id: str, kind: str, safe: str,
                      files: list[str], version: str = "") -> str:
    """把**当前**这组图存成一版历史。没有可存的文件就返回 ""（首次生成就是这样）。"""
    present = [p for p in files if p and os.path.isfile(p)]
    if not present:
        return ""
    # ⚠️ 传进来的 version 必须归一成 6 位小写 hex：`_HISTORY_NAME_RE` 卡死了目录名格式，
    #    脏 version（空 / 大写 / 超长）会让这一版**存进去但列不出来**（看着像历史丢了）。
    ver = re.sub(r"[^0-9a-f]", "", str(version or "").lower())[:6]
    if len(ver) < 6:
        ver = uuid.uuid4().hex[:6]
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{ver}"
    dest = os.path.join(_history_dir(task_id, kind, safe), name)
    try:
        os.makedirs(dest, exist_ok=True)
        for p in present:
            shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
    except OSError:
        return ""                               # 存历史失败不该让出图失败
    _prune_history(task_id, kind, safe)
    return name


def _history_thumb(files: list[str]) -> str:
    """列表缩略图用哪张：优先「设定图 / 三视图」（信息量大），否则第一张。
    与卡片的取舍保持一致 —— 卡片也是优先显示 sheet。"""
    for f in files:
        if f.endswith("_sheet.png"):
            return f
    return files[0] if files else ""


def _live_image_url(task_id: str, kind: str, filename: str, version: str = "") -> str:
    """当前这版图的 URL。带 version 做 cache-busting —— 文件是就地覆盖的，
    不带版本号浏览器会拿缓存里的旧图（见前端 `characterImageUrl` 那段注释）。"""
    bucket = "characters" if kind == "character" else "assets"
    url = f"/files/{task_id}/{bucket}/{filename}"
    return f"{url}?v={version}" if version else url


def _history_image_url(task_id: str, kind: str, safe: str, version: str, filename: str) -> str:
    return f"/files/{task_id}/history/{kind}/{safe}/{version}/{filename}"


class HistoryUseBody(BaseModel):
    name: str = ""


def _history_target(task: dict[str, Any], kind: str, index: int) -> tuple[str, dict[str, Any]]:
    """kind + index → (safe 名, 条目 dict)。character 走 characters[]，其余走 assets[]。"""
    if kind not in _HISTORY_KINDS:
        raise HTTPException(status_code=422, detail=f"这个类别没有生成历史：{kind}")
    project = task.get("project") or {}
    if kind == "character":
        rows = project.get("characters") or []
        if index < 0 or index >= len(rows):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        entry = rows[index]
    else:
        rows = project.get("assets") or []
        if index < 0 or index >= len(rows):
            raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
        entry = rows[index]
        if str(entry.get("kind") or "") != kind:
            raise HTTPException(status_code=422, detail=f"这个素材不是「{kind}」")
    return pipeline._safe_char_name(str(entry.get("name") or f"{kind}{index}")), entry


@app.get("/api/tasks/{task_id}/materials/{kind}/{index}/history")
def material_history(task_id: str, kind: str, index: int) -> dict:
    """某个素材出过的每一版图。

    **新的在前，第一条是当前这版**（`current: true` 且 `name` 为空 —— 它在磁盘上，
    不在历史目录里）。只读，不碰任何文件。

    ⚠️ **内容重复的版本不列出来**：跟「当前这版」一模一样的、以及彼此一模一样的，
    只保留最新的那一条。2026-09-19 之前「用这版」切完之后会把源目录留在历史里，
    于是弹窗里同一张图出现两次（斌哥报的"点用这版会多出一张一样的"）——
    产生源头已经修了（见 use_material_history），但用户磁盘上**已经躺着**的重复
    得靠这里挡掉，否则那个作品看起来还是没修好。
    """
    with LOCK:
        task = _task(task_id)
        safe, entry = _history_target(task, kind, index)
        files = [p for p in _material_files(kind, entry) if p and os.path.isfile(p)]
        current_version = str(_entry_field(entry, "version", "") or "")

    versions: list[dict[str, Any]] = []
    seen: set[tuple[tuple[int, str], ...]] = set()
    if files:
        names = [os.path.basename(p) for p in files]
        seen.add(_files_signature(files))
        versions.append({
            "name": "",
            "current": True,
            "created_at": "",
            "bytes": sum(os.path.getsize(p) for p in files),
            "files": names,
            "thumb": _live_image_url(task_id, kind, _history_thumb(names), current_version),
            "version": current_version,
        })
    hroot = _history_dir(task_id, kind, safe)
    for v in _history_versions(task_id, kind, safe):
        sig = _files_signature([os.path.join(hroot, v["name"], f) for f in v["files"]])
        if sig in seen:
            continue
        seen.add(sig)
        versions.append({
            "name": v["name"],
            "current": False,
            "created_at": v["created_at"],
            "bytes": v["bytes"],
            "files": v["files"],
            "thumb": _history_image_url(task_id, kind, safe, v["name"], _history_thumb(v["files"])),
            "version": v["name"].rsplit("_", 1)[-1],
        })
    return {"versions": versions, "keep": HISTORY_KEEP}


@app.post("/api/tasks/{task_id}/materials/{kind}/{index}/history/use")
def use_material_history(task_id: str, kind: str, index: int, body: HistoryUseBody) -> dict:
    """把某一版历史图**覆盖回当前文件**，并把它记成当前版本。

    ⚠️ 动手前先把**当前**这版也存进历史 —— 否则"切回去又后悔"就再也回不来了。
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="要切回哪一版？")
    if not _HISTORY_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="版本名不合法")

    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        safe, _entry = _history_target(task, kind, index)

    src_dir = os.path.join(_history_dir(task_id, kind, safe), name)
    if not os.path.isdir(src_dir):
        raise HTTPException(status_code=404, detail=f"这一版不在了：{name}")
    src_files = sorted(
        f for f in os.listdir(src_dir)
        if os.path.isfile(os.path.join(src_dir, f)) and not f.startswith(".")
    )
    if not src_files:
        raise HTTPException(status_code=422, detail="这一版里没有图")

    project, _ = _reload(task_id)
    if kind == "character":
        if index >= len(project.characters):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        target = project.characters[index]
    else:
        if index >= len(project.assets):
            raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
        target = project.assets[index]

    # 先把当前这版存进历史，再覆盖 —— 顺序反了就没法"再切回来"
    _snapshot_history(task_id, kind, safe, _material_files(kind, target),
                      str(_entry_field(target, "version", "") or ""))

    dest_dir = os.path.join(_out_dir(task_id), "characters" if kind == "character" else "assets")
    os.makedirs(dest_dir, exist_ok=True)
    restored: list[str] = []
    sheet = ""
    for fn in src_files:
        dest = os.path.join(dest_dir, fn)
        shutil.copy2(os.path.join(src_dir, fn), dest)
        restored.append(dest)
        if fn.endswith("_sheet.png"):
            sheet = dest

    # 「单人图」与「设定图」严格分开：sheet 是四视角拼版，混进 images 会被下游
    # 当成**又多了一张人像参考图**。取舍与 pipeline 生成链路、_load_task_from_disk
    # 完全一致（那两处的注释：sheet "只作总览与留档，不进 images"）。
    portraits = [p for p in restored if p != sheet]
    if kind == "character":
        # 正面那张优先当形象图（生成时 image_path 就指它）；这一版里没有正面才退而求其次
        base = os.path.join(dest_dir, f"{safe}.png")
        front = base if base in portraits else (
            portraits[0] if portraits else (sheet or restored[0])
        )
        target.image_path = front
        target.images = (
            [front, *[p for p in portraits if p != front]] if portraits
            else ([sheet] if sheet else [])
        )
    else:
        target.images = portraits
    target.sheet = sheet

    with LOCK:
        bucket = "characters" if kind == "character" else "assets"
        entry = task["project"][bucket][index] = pipeline.dump(target)
        entry["version"] = uuid.uuid4().hex[:6]
        entry.pop("stale", None)
        _persist(task_id)
        updated = dict(entry)
    # ⚠️ 收掉刚切上去的那一版目录：它的内容此刻**原样**躺在当前文件里，
    #    而「当前这版」永远排在列表第一条 —— 留着它，弹窗里同一张图就会出现两次
    #    （2026-09-19 斌哥报的"点『用这版』会多出一张一样的"）。
    #    "来回切"不受影响：被换下去的那一版在上面已经存进历史了，切多少次都找得回来。
    _drop_history_dir(task_id, kind, safe, name)
    updated["restored_from"] = name
    return updated


@app.post("/api/tasks/{task_id}/characters/{index}/reroll")
def reroll_character(task_id: str, index: int) -> dict:
    """重新生成某个角色的**全部四个视角**定妆照（同步执行，约 40-80 秒）。

    实现是「删旧图 + 跑 pipeline._gen_character_portraits」：判存逻辑天然只补缺的，
    不会动其他角色已就绪的图。

    ⚠️ 必须传 seed_salt：四视角靠固定 seed 保持一致，不换 seed 的话
    「重新生成」出来的四张会跟上一批几乎一模一样，看起来像按钮失灵。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")

    project, _ = _reload(task_id)
    if not project.characters:
        raise HTTPException(status_code=404, detail="项目没有角色")
    # ⚠️ 下面第一件事就是**删**旧图 —— 快照必须赶在删之前，晚一行旧图就没了。
    _snapshot_history(
        task_id, "character",
        pipeline._safe_char_name(project.characters[index].name),
        _material_files("character", project.characters[index]),
        str(_entry_field(project.characters[index], "version", "") or ""),
    )
    for p in project.characters[index].images or []:
        if os.path.isfile(p):
            os.remove(p)
    project.characters[index].images = []
    project.characters[index].image_path = ""
    try:
        pipeline._gen_character_portraits(
            project, _out_dir(task_id), seed_salt=uuid.uuid4().hex[:8]
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"定妆照生成失败：{type(exc).__name__}: {exc}",
        ) from exc

    with LOCK:
        entry = task["project"]["characters"][index] = pipeline.dump(
            project.characters[index]
        )
        entry["version"] = uuid.uuid4().hex[:6]
        entry.pop("stale", None)          # 新照对应新锚点，解除待更新标记
        _persist(task_id)
        updated = dict(entry)
    return updated


def _remember_ai_input(
    entry: dict[str, Any], kind: str, prompt: str, *, ratio: str = "", gender: str = ""
) -> None:
    """记下这次「AI 生成」弹窗里填的描述，以及选的比例 / 性别。

    只服务一个目的：**下次对同一个素材再点「AI 生成」时，弹窗把上次填的填回去**
    （2026-09-27 斌哥提的：出一版不满意，想改两句重出，结果弹窗是空的，只能从头重打）。

    ⚠️ 按**弹窗种类**分槽存（`entry["ai_last"][kind]`），不是一个条目一份：
    角色条目上挂着两个弹窗（形象图 / 音色，都写 `project.characters[i]`），
    只存一份的话，生成完音色会把形象图那次的描述顶掉 —— 再打开形象图弹窗
    看到的就是音色描述。`kind` 就是前端那个 `item.kind`。

    ⚠️ 它**不是素材描述**，所以：
      - 不写回 `anchor`（`anchor` 是跨镜头一致性的锚，会被下游出图/出片消费，
        写进去等于把"这次随手写的口语"固化成设定）；
      - 卡片上不显示，只有弹窗读它；
      - 上传型素材（自己传图/传音频）不会写这个字段 —— 那不是"AI 生成过的"。
    前端读的是 `entry.ai_last[item.kind]`（`get_task` 直接回 `task["project"]` 原始
    dict，所以自定义字段能原样传到页面，与 `version` / `stale` 同一条路）。
    """
    rec: dict[str, Any] = {"prompt": prompt}
    if ratio:
        rec["ratio"] = ratio
    if gender:
        rec["gender"] = gender
    rec["at"] = datetime.now(timezone.utc).isoformat()
    slots = entry.get("ai_last")
    if not isinstance(slots, dict):     # 手改过的 project.json 里可能是个脏值
        slots = {}
    slots[kind] = rec
    entry["ai_last"] = slots


@app.post("/api/tasks/{task_id}/characters/{index}/portrait")
def generate_character_portrait(
    task_id: str, index: int, body: CharacterPortraitBody
) -> dict:
    """按用户当次写的描述，生成角色的形象图：一张**正面半身定妆照** + 一张**多视角设定图**。

    流程（同步，约 20-60 秒，**2 次图像额度**）：
      ① 角色 Agent（`agents.compose_portrait_sheet`）把口语描述扩写成「人物描述段」
      ② 出正面半身单人图（画幅用 `body.ratio`，默认 1:1）→ `characters/<名>.png` ——
         **下游真正要用的那张**（I2VA 首帧 / Ref2VA 参考图都必须是单人图）
      ③ 出**16:9 横版** 设定图 → `characters/<名>_sheet.png` ——
         **四视图横向排版：最左一张大的面部半身特写 + 正面/标准侧面/背面三个全身**，
         给人核对形象用（版式按斌哥给的参考图，16:9 恒横版）
       ④ `images` 只放单人图；设定图单独记在 `sheet`，**不进 images**

      ⚠️ 2026-09-19：设定图版式与画幅来回改过一次，**现在定稿 16:9 横版四格**。
      当天早些时候按豆包样张改成「竖版 9:16 左右两栏」，但斌哥随后给的参考
      （左侧超大面积面部特写 + 右侧正面/标准侧面/背面全身，16:9 横版构图）是**横版**，
      前端弹窗与 `api.js` 里写的也一直是 16:9。详见 `agents.PORTRAIT_SHEET_LAYOUT` 的注释
      —— 那里记了 `金鳞_sheet.png`（横版，全对）与 `莫卡_sheet.png`（竖版，出成两张一样的正面全身）
      这组对比实证。

    为什么不"只出一张设定图再切开"省额度：**实测不可行**。右栏几个视角之间几乎没有缝隙
    （`crop_ref` 用自适应阈值也只能切出 2 块），靠列投影切不准。

    ⚠️ 整张设定图**不能**进 `images`：拼图会被视频模型读成"画面里有四个人"。

    与 `reroll_character` 的分工：`reroll` 读角色已有的 `anchor`、按四视角逐张出图
    （4 倍额度，每张 1:1 高清）；这条走**用户这次输入的描述**、两张，且**不写回** `anchor`。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")

    want = body.prompt.strip()
    if not want:
        raise HTTPException(status_code=422, detail="先描述一下你想要的角色形象")

    project, _ = _reload(task_id)
    if index >= len(project.characters):
        raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
    char = project.characters[index]

    # ① 角色 Agent：一句口语描述 → 「人物描述段」+「四视图设定图提示词」。
    #    人物段（长相/发型/服装/配饰）由 Agent 写；版式段与收尾段由代码拼死
    #    （见 agents.PORTRAIT_SHEET_LAYOUT）—— 文本模型只负责"人长什么样"。
    try:
        designed = agents.compose_portrait_sheet(project, char.name, hint=want)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"角色设计失败：{type(exc).__name__}: {exc}",
        ) from exc
    person = designed["body"]

    cdir = os.path.join(_out_dir(task_id), "characters")
    os.makedirs(cdir, exist_ok=True)
    base = pipeline._safe_char_name(char.name)
    front_path = os.path.join(cdir, f"{base}.png")
    sheet_path = os.path.join(cdir, f"{base}_sheet.png")
    # ⚠️ 写图**之前**先把当前这版存进历史（下面两张图都是就地覆盖同名文件）
    _snapshot_history(task_id, "character", base, _material_files("character", char),
                      str(_entry_field(char, "version", "") or ""))
    provider = create_provider()
    salt = uuid.uuid4().hex[:8]     # 两张图共用同一个 salt，但前缀不同 → seed 仍各自独立

    # ② 先出**正面半身单人图**：这是下游真正要用的那张 —— I2VA 首帧与
    #    Ref2VA 参考图都必须是单人图。先出它，保证后面那张设定图即便失败，
    #    角色也已经"有图能用"。画幅由用户选的 ratio 决定（白名单外的值退回 1:1）。
    _k, _s, view_desc, _e = pipeline.PORTRAIT_VIEWS[0]
    front_prompt = pipeline._character_portrait_prompt(project.style, person, view_desc)
    ratio = body.ratio if body.ratio in pipeline.SIZE_TABLE else "1:1"
    try:
        provider.generate(
            front_prompt,
            front_path,
            negative_prompt=pipeline.PORTRAIT_NEGATIVE,
            size=pipeline.SIZE_TABLE[ratio],
            seed=pipeline._stable_seed(f"portrait|{char.name}|{person}|{salt}"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"定妆照生成失败：{type(exc).__name__}: {exc}",
        ) from exc

    # ③ 再出**设定图**（16:9 横版四格）：左格大特写 + 正面/侧面/背面三个全身，给人核对形象用。
    #    ⚠️ 它**不进 images**：拼图喂给视频模型会被读成"画面里有四个人"。
    #    横版是四个视角排得下的唯一画幅（也是参考图与前端文案里写的那个），恒 16:9，不跟随 ratio。
    #    两张图共用同一段 person 描述，所以是同一个人。
    sheet_note = ""
    try:
        provider.generate(
            designed["sheet"],
            sheet_path,
            negative_prompt=pipeline.SHEET_NEGATIVE,
            size=pipeline.SIZE_TABLE.get("16:9", "1024x576"),
            seed=pipeline._stable_seed(f"sheet|{char.name}|{person}|{salt}"),
        )
    except Exception as exc:
        sheet_note = f"设定图失败（正面定妆照已就绪）：{type(exc).__name__}: {exc}"
        sheet_path = ""

    # ④ 落库：images 放单人图（下游直接可用）；设定图单独记在 sheet，只作总览与留档
    with LOCK:
        entry = task["project"]["characters"][index]
        kept = [
            p for p in (entry.get("images") or []) if os.path.isfile(p) and p != front_path
        ]
        entry["images"] = [front_path, *kept]
        entry["image_path"] = front_path
        if sheet_path:
            entry["sheet"] = sheet_path
        entry["version"] = uuid.uuid4().hex[:6]
        # 弹窗记忆：下次再点「AI 生成」时把这段描述与比例填回去
        _remember_ai_input(entry, "character", want, ratio=ratio)
        entry.pop("stale", None)
        _persist(task_id)
        updated = dict(entry)
    if sheet_note:
        updated["warning"] = sheet_note
    return updated


@app.post("/api/tasks/{task_id}/assets/{index}/generate")
def generate_asset_image(task_id: str, index: int, body: AssetGenerateBody) -> dict:
    """按用户当次写的描述生成一张素材图（同步，约 10-25 秒）。

    三条分支，都先过 `agents.design_asset` 扩写，描述**都不写回 anchor**
    （素材工坊的用法是"选图 → 出片"，有图能用就够，不维护一段描述）：

      - **场景**：出**一张空镜**（环境 + 镜头 + 光影，强制无人物）
        → `assets/scene_<名>.png`，进 `images`
      - **其他图片**：出**一整张完整画面**（可含人物与环境；不压人物、不要求纯色底）
        → `assets/img_<名>.png`，进 `images`（2026-09-17 加）
      - **道具**：出**一张三视图**（正视 / 侧视 / 后视并排）
        → `assets/prop_<名>_sheet.png`，记在 `sheet`
        （⚠️ **不进 images** —— 拼图会被模型读成"画面里有三件道具"）

    三条的 prompt 模板分别是 `pipeline._scene_prompt` / `pipeline._free_image_prompt` /
    `agents.compose_prop_sheet`，**别互相借用**：场景要无人、道具要纯色底、
    其他图片要完整画面，三者的诉求正好互相冲突。

    为什么场景不搞多视角：场景是故事发生地的环境锚，一张全景就够用；
    多视角只在"同一个东西要在侧面/背面也保持一致的"角色与道具上才有价值。

    为什么道具是"一张三视图"而不是"三张独立图"：道具没有表情与动态，
    三个视角放进同一张画布天然就是同一件东西（同一个生成过程，不靠 seed 对齐），
    1 次额度换 3 个视角。场景则是环境锚，一张全景就够，不做多视角。

    为什么「其他图片」单独一支：它在流程里是首帧 / 尾帧 / Ref2VA 参考图的来源
    （见 `schemas.Shot.image_path`、`video_provider` 的 FL2VA 分支），
    要的是能直接当一帧用的完整画面，用现成图凑合往往接不上。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        assets = (task.get("project") or {}).get("assets") or []
        if index < 0 or index >= len(assets):
            raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
        kind = str(assets[index].get("kind", ""))
        if kind not in ("scene", "prop", "image"):
            raise HTTPException(
                status_code=422,
                detail="这个入口只支持场景、道具与其他图片（音频是上传型素材，传文件即可）",
            )

    want = body.prompt.strip()
    if not want:
        raise HTTPException(status_code=422, detail="先描述一下你想要的画面")

    project, _ = _reload(task_id)
    if index >= len(project.assets):
        raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
    asset = project.assets[index]

    adir = os.path.join(_out_dir(task_id), "assets")
    os.makedirs(adir, exist_ok=True)
    safe = pipeline._safe_char_name(asset.name)
    # ⚠️ 下面三条分支（scene / image / prop）都是**就地覆盖同名文件**，统一在这里先留一版。
    #    放在分支之前：三条路都要存，写进各自分支等于复制三遍。
    _snapshot_history(task_id, kind, safe, _material_files(kind, asset),
                      str(_entry_field(asset, "version", "") or ""))
    provider = create_provider()
    salt = uuid.uuid4().hex[:8]     # 同一个 salt 串不同前缀 → 各分支 seed 仍各自独立

    if kind == "scene":
        # ① 场景 Agent：口语描述 → 环境 + 镜头 + 光影（禁人物）
        try:
            anchor = agents.design_asset(project, "scene", asset.name, hint=want)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"场景设计失败：{type(exc).__name__}: {exc}",
            ) from exc
        anchor = (anchor or "").strip() or want

        # ② 出图：单张空镜。negative 里再压一道"人"——文本约束偶尔会被模型忽略，
        #    负面词是最便宜的第二道保险。
        path = os.path.join(adir, f"scene_{safe}.png")
        ratio = body.ratio if body.ratio in pipeline.SIZE_TABLE else project.aspect_ratio
        if ratio not in pipeline.SIZE_TABLE:
            ratio = "16:9"
        try:
            provider.generate(
                pipeline._scene_prompt(project.style, anchor),
                path,
                negative_prompt=DEFAULT_NEGATIVE + ", person, people, human, human figure, crowd, silhouette",
                size=pipeline.SIZE_TABLE[ratio],
                seed=pipeline._stable_seed(f"scene|{asset.name}|{anchor}|{salt}"),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"场景图生成失败：{type(exc).__name__}: {exc}",
            ) from exc

        # ③ 落库：images 只留这一张；anchor 原样不动（这段描述只服务这次出图）
        with LOCK:
            entry = task["project"]["assets"][index]
            entry["images"] = [path]
            entry["version"] = uuid.uuid4().hex[:6]
            # 弹窗记忆：下次再点「AI 生成」时把这段描述与比例填回去
            _remember_ai_input(entry, "scene", want, ratio=ratio)
            entry.pop("stale", None)
            _persist(task_id)
            updated = dict(entry)
        return updated

    # ---------------- 其他图片：一整张完整画面（首尾帧 / 参考图用） ----------------
    # 2026-09-17 加。之前「其他图片」只有上传入口，而它恰恰是最需要按剧情定制的一类 ——
    # 它在流程里当首帧 / 尾帧 / Ref2VA 参考图（见 schemas.Shot.image_path），
    # 用现成图凑合往往接不上。所以给一条"写描述 → Agent 扩写 → 出图"的路，
    # 与场景/道具同构，只是**不压人物、不要求纯色背景**（要的是能直接当一帧的画面）。
    if kind == "image":
        # ① 画面 Agent：口语描述 → 主体 + 环境 + 镜头光影
        try:
            desc = agents.design_asset(project, "image", asset.name, hint=want)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"画面设计失败：{type(exc).__name__}: {exc}",
            ) from exc
        desc = (desc or "").strip() or want

        # ② 出图：文件名必须与 `_ASSET_FILE_NAME["image"]` 及重启重指规则一致，
        #    否则重启后这张图会被当成"不存在"而丢掉。
        #    尺寸跟随作品画幅 —— 它常被直接当首帧，比例与成片不一致会被裁。
        path = os.path.join(adir, f"img_{safe}.png")
        ratio = body.ratio if body.ratio in pipeline.SIZE_TABLE else project.aspect_ratio
        if ratio not in pipeline.SIZE_TABLE:
            ratio = "16:9"
        try:
            provider.generate(
                pipeline._free_image_prompt(project.style, desc),
                path,
                negative_prompt=DEFAULT_NEGATIVE,
                size=pipeline.SIZE_TABLE[ratio],
                seed=pipeline._stable_seed(f"image|{asset.name}|{desc}|{salt}"),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"图片生成失败：{type(exc).__name__}: {exc}",
            ) from exc

        # ③ 落库：images 换成这一张；anchor 原样不动（这段描述只服务这次出图）
        with LOCK:
            entry = task["project"]["assets"][index]
            entry["images"] = [path]
            entry["version"] = uuid.uuid4().hex[:6]
            # 弹窗记忆：下次再点「AI 生成」时把这段描述与比例填回去
            _remember_ai_input(entry, "image", want, ratio=ratio)
            entry.pop("stale", None)
            _persist(task_id)
            updated = dict(entry)
        return updated

    # ---------------- 道具：三视图设定图（恒 16:9 —— 三个视角要横向并排） ----------------
    # ① 道具 Agent：口语描述 → 外观描述段 + 三视图版式
    try:
        designed = agents.compose_prop_sheet(project, asset.name, hint=want)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"道具设计失败：{type(exc).__name__}: {exc}",
        ) from exc

    # ② 出图：一张三视图。negative 再压"多件不同物 + 人手"——三视图最怕模型
    #    画成三件不一样的东西，或者顺手加只手去拿它。
    path = os.path.join(adir, f"prop_{safe}_sheet.png")
    try:
        provider.generate(
            designed["sheet"],
            path,
            negative_prompt=DEFAULT_NEGATIVE + ", person, people, human, hand, multiple different objects",
            size=pipeline.SIZE_TABLE["16:9"],
            seed=pipeline._stable_seed(f"prop|{asset.name}|{designed['body']}|{salt}"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"道具三视图生成失败：{type(exc).__name__}: {exc}",
        ) from exc

    # ③ 落库：三视图只记在 sheet，**不进 images**（拼图会被读成"三件道具"）
    with LOCK:
        entry = task["project"]["assets"][index]
        entry["sheet"] = path
        entry["version"] = uuid.uuid4().hex[:6]
        # 弹窗记忆：道具恒 16:9（弹窗里也没有比例选项），只记描述
        _remember_ai_input(entry, "prop", want)
        entry.pop("stale", None)
        _persist(task_id)
        updated = dict(entry)
    return updated


def _save_voice_sample(
    task: dict,
    task_id: str,
    index: int,
    character,
    voice_id: str,
    remember: dict[str, Any] | None = None,
) -> dict:
    """合成音色样本并落库。`/voice`（自动配）与 `/voice/generate`（AI 挑）共用。

    voice_id 由调用方定好：reroll 那条走 `tts.assign_voices`，AI 生成那条走配音 Agent
    并已在外面校验过。这里只管"合成 + 写回"，不再碰音色选择逻辑。

    `remember` 只有 `/voice/generate`（用户自己填过描述的那条）才传：
    把弹窗里填的描述与性别记进 `entry["ai_last"]`，下次打开弹窗填回去。
    自动配的那条（`/voice`）没填过任何东西，传了反而会把用户的描述覆盖掉。
    """
    character.tts_voice = voice_id or tts.default_voice()
    fname = tts.sample_filename(character.name)
    path = os.path.join(_out_dir(task_id), "characters", fname)
    try:
        tts.synth_sample(character.tts_voice, path)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"音色合成失败（{tts.provider_label()} 需要联网）：{type(exc).__name__}: {exc}",
        ) from exc

    with LOCK:
        entry = task["project"]["characters"][index]
        entry["tts_voice"] = character.tts_voice
        entry["voice_sample"] = fname
        entry["version"] = uuid.uuid4().hex[:6]
        if remember:
            _remember_ai_input(entry, **remember)
        _persist(task_id)
        return dict(entry)


def _verified_voice_id(voice_id: str, voices: list[dict], gender: str, hint: str) -> str:
    """校验配音 Agent 挑的音色 id，不合格就回退。

    模型有两种可预期的失误，都必须在这里兜住 —— 否则角色会配上明显不对的声音，
    而**用户看不出是模型错的**：
      ① 编一个池子外的 id，或把中文名当 id 回
      ② 跨性别挑
    回退顺序：关键词匹配（`tts.pick_voice`）→ 该性别池的第一个。
    """
    by_id = {str(v.get("id", "")): v for v in voices}
    want = str(voice_id or "").strip()

    def acceptable(vid: str) -> bool:
        v = by_id.get(vid)
        return bool(v) and (not gender or v.get("gender") == gender)

    if acceptable(want):
        return want

    same = [v for v in voices if not gender or v.get("gender") == gender] or list(voices)
    fallback = ""
    if hint.strip():
        # pick_voice 判不出性别时返回空串（它宁可留空也不硬猜），那就走下面的兜底
        guess = tts.pick_voice(hint)
        if acceptable(guess):
            fallback = guess
    if not fallback:
        fallback = str(same[0].get("id", "")) if same else str(voices[0].get("id", ""))
    print(f"[音色] Agent 给的 id 不合格（{want!r}）→ 回退到 {fallback!r}")
    return fallback


@app.post("/api/tasks/{task_id}/characters/{index}/voice")
def reroll_character_voice(task_id: str, index: int, voice_id: str = "") -> dict:
    """重新合成某个角色的音色样本（同步执行，1-3 秒）。

    voice_id 非空时顺手把角色的 TTS 音色改成它 —— 前端下拉选完直接调这一个接口，
    一步完成「换音色 + 出样本」，不用先 PATCH 再 POST 两趟。

    ⚠️ 这个样本是给 **Ref2VA 的 ref_audios** 用的参考件，当前 I2VA/FL2VA 链路
    没有音频输入口，吃不到它。所以它在界面上"生成了但暂时不影响出片"是正常的。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        if voice_id.strip():
            chars[index]["tts_voice"] = voice_id.strip()

    project, _ = _reload(task_id)
    if not project.characters:
        raise HTTPException(status_code=404, detail="项目没有角色")
    c = project.characters[index]
    # assign_voices 会把"不在当前音频后端池子里"的旧音色 id 重挑 ——
    # 换后端（edge ↔ minimax）后旧 id 对新后端是无效的，不重挑会拿 edge 的 id 去问 MiniMax
    c.tts_voice = tts.assign_voices(project.characters).get(c.name, "") or tts.default_voice()
    return _save_voice_sample(task, task_id, index, c, c.tts_voice)


@app.post("/api/tasks/{task_id}/characters/{index}/voice/generate")
def generate_character_voice(task_id: str, index: int, body: VoiceGenerateBody) -> dict:
    """按「性别 + 一段描述」挑音色并合成样本（同步，2-6 秒）。

    三步：
      ① 配音 Agent（`agents.design_voice`）从**当前音频后端的音色池**里挑一个 id
      ② 校验 id 在池子里、性别对得上（`_verified_voice_id`），不合格就回退
      ③ 合成样本 → `characters/voice_<名>.mp3`，并把结果写回 `tts_voice`

    与 `/voice`（reroll）的分工：那条按角色**已有的** voice 描述自动配；
    这条按用户**当场写的**一句话配，用户描述优先。
    另外这条会把描述与性别记进 `ai_last`，下次打开「AI 生成音色」弹窗时填回去。

    ⚠️ 音频池是**预置音色**（minimax 27 个 / edge 8 个），模型造不出新音色 ——
    所谓"AI 生成音色"，实际是让模型从池子里挑最贴的那一个，再拿它合成样本。
    """
    gender = body.gender.strip().lower()
    if gender not in ("female", "male", ""):
        raise HTTPException(status_code=422, detail="性别只能是 female 或 male")

    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")

    project, _ = _reload(task_id)
    if not project.characters:
        raise HTTPException(status_code=404, detail="项目没有角色")
    c = project.characters[index]

    voices = tts.list_voices()      # 当前后端的池子，单一来源在 tts.py，前端不要另存一份
    if not voices:
        raise HTTPException(status_code=502, detail="当前音频后端没有可用音色")
    try:
        picked = agents.design_voice(project, c, voices, gender=gender, hint=body.prompt.strip())
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"音色挑选失败：{type(exc).__name__}: {exc}",
        ) from exc

    voice_id = _verified_voice_id(picked.get("voice_id", ""), voices, gender, body.prompt)
    # remember：这条是"用户自己填过描述"的那条，描述与性别要留给下次弹窗当默认值。
    # kind="voice" —— 角色条目上形象图与音色是两个槽，别互相顶掉（见 _remember_ai_input）。
    return _save_voice_sample(
        task, task_id, index, c, voice_id,
        remember={"kind": "voice", "prompt": body.prompt.strip(), "gender": gender},
    )


@app.get("/api/voices")
def list_tts_voices() -> list[dict]:
    """可选 TTS 音色清单。**按当前音频后端返回对应池子** ——
    单一来源是 tts.py，前端不要另存一份硬编码。"""
    return tts.list_voices()


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
    video_prompt: str = Field("", max_length=8000)  # 已是英文正文时直接给，跳过 LLM
    duration: float = 10.0
    use_voice: bool = True


# 素材类型的中文说法，写进给 LLM 的素材清单
_MATERIAL_CN = {"character": "角色", "prop": "道具", "scene": "场景"}


def _material_lines_for(plan, *, with_tags: bool = True) -> list[str]:
    """把槽位清单翻成给 LLM 看的「这是什么」。

    with_tags=True  → 带编号（`参考图 <Picture 1>`），给 /compose 用：
                      正文必须用编号引用素材，不引用模型只能自己猜哪张图演谁
    with_tags=False → 只有名字与类型，给 /prompt/optimize 用：
                      那一步还不该出现编号（见 agents.optimize_description 的说明）
    """
    lines: list[str] = []
    for s in plan.slots:
        if s.kind == "image":
            if s.subject:
                kind_cn = _MATERIAL_CN.get(s.role, "素材")
                lines.append(f"{s.subject} = {kind_cn}「{s.label}」（参考图 {s.tag}）" if with_tags
                             else f"{kind_cn}「{s.label}」")
            else:
                lines.append(f"{s.tag} = 用户上传的图片「{s.label}」（画面参考）" if with_tags
                             else f"用户上传的图片「{s.label}」")
        elif s.role == "voice":
            note = "只供音色参考，不要在正文里描述它"
            lines.append(f"{s.tag} = 角色「{s.label}」的音色样本（{note}）" if with_tags
                         else f"角色「{s.label}」的音色样本（{note}）")
        else:
            note = "只借氛围，不要在正文里描述它"
            lines.append(f"{s.tag} = 用户上传的音频「{s.label}」（{note}）" if with_tags
                         else f"用户上传的音频「{s.label}」（{note}）")
    return lines


@app.post("/api/tasks/{task_id}/compose")
def compose_segment(task_id: str, body: ComposeBody) -> dict:
    """把「选中的素材 + 一段描述」编成 Ref2VA 六段式提示词。**不生成视频**。

    分两条路（这也是为什么这个接口不需要每次都花 LLM 的钱）：
    - 给了 `video_prompt`（已是英文正文）→ 直接组装
    - 只给 `description`（中文）→ 让分镜 Agent 按素材编号写成英文正文，再组装

    返回六段式 prompt + manifest（ComfyUI 连线操作单）+ 警告 + 槽位清单。
    """
    with LOCK:
        task = _task(task_id)
    project, _ = _reload(task_id)
    if project is None:
        raise HTTPException(status_code=409, detail="这个作品还没有故事设定，先生成设定再编排")

    # 走哪套格式由**服务配置里的工作流**决定（2026-09-19）：
    #   ref2va → 六段式（标签体系 + 参考关系），出片时直接进节点
    #   i2v    → 基础模式：只出正文，三段壳与关键帧对齐指令由出片时拼
    wf_ref2va = str(_load_service_config().get("video_workflow") or "i2v") == "ref2va"

    # 先算一次槽位拿到编号表。build_ref_plan 是确定性的（按固定顺序编号），
    # 所以这里算出来的编号与下面 compose 内部再算一次的结果必然一致。
    seed_plan = ref_plan.build_ref_plan(
        project, characters=body.characters, props=body.props,
        scene=body.scene, images=body.images, audios=body.audios,
        use_voice=body.use_voice,
    )

    video_prompt = body.video_prompt.strip()
    # 音轨段：只有「让 AI 帮写」那条路才有（它和画面是同一件事的两面，交给同一个 Agent 写）。
    # 手写模式用户只给正文，这里留空 → compose_ref2va_prompt 会填一句通用兜底。
    soundscape = ""
    if not video_prompt:
        if not body.description.strip():
            raise HTTPException(
                status_code=422,
                detail="需要 description（中文描述）或 video_prompt（英文正文）其中之一",
            )
        # 素材清单要不要带编号，取决于走哪套格式（见下面 wf_ref2va）：
        #   Ref2VA → 带 `<Picture N>`/`<Subject N>`（正文必须引用编号）
        #   基础模式 → 只给名字（那套标签体系在这里不存在）
        material_lines = _material_lines_for(seed_plan, with_tags=wf_ref2va)
        if not material_lines:
            raise HTTPException(
                status_code=422,
                detail="没有可用素材：先给角色出定妆照，或勾选至少一个已就绪的素材",
            )
        try:
            composed = agents.compose_segment_prompt(
                project, description=body.description,
                material_lines=material_lines, duration=body.duration,
                ref_mode=wf_ref2va,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"写提示词失败：{type(exc).__name__}: {exc}"
            ) from exc
        video_prompt = composed["video_prompt"]
        soundscape = composed["soundscape"]
        if not video_prompt:
            raise HTTPException(status_code=502, detail="分镜 Agent 没有返回正文，请重试")

    if not wf_ref2va:
        # 基础模式（I2V / 首尾帧）：**正文就是正文，不再套六段式** ——
        # 三段字段（integrated_multimodal_description / overall_soundscape /
        # non_diegetic_music）与「关键帧对齐指令」由出片时的
        # `video_provider.compose_h3_prompt` 拼，那里才知道吸附后的真实帧数。
        # ⚠️ soundscape 要一起回传：前端会把它带给 /segment/video，
        #    否则基础模式没有 overall_soundscape（模型就会自己编配乐/环境音）。
        return {
            "video_prompt": video_prompt,
            "soundscape": soundscape,
            "prompt": video_prompt,
            "manifest": "",
            "warnings": [],
            "slots": [],
        }

    tags = ["reference generation"]
    # 有音频参考就补上对应的任务类型标记（官方六段式的 summary 要用方括号声明任务类型）
    if any(s.kind == "audio" for s in seed_plan.slots):
        tags.append("audio reference")
    plan = ref_plan.compose_ref2va_prompt(
        project, video_prompt=video_prompt, soundscape=soundscape, duration=body.duration,
        characters=body.characters, props=body.props, scene=body.scene,
        images=body.images, audios=body.audios,
        use_voice=body.use_voice, task_tags=tuple(tags),
    )
    return {
        "video_prompt": video_prompt,
        "soundscape": soundscape,
        "prompt": plan.prompt,
        "manifest": plan.manifest(),
        "warnings": plan.warnings,
        "slots": [
            {
                "tag": s.tag, "kind": s.kind, "path": s.path, "role": s.role,
                "label": s.label, "input_slot": s.input_slot, "human": s.human,
            }
            for s in plan.slots
        ],
    }


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


@app.post("/api/tasks/{task_id}/prompt/optimize")
def optimize_prompt(task_id: str, body: OptimizePromptBody) -> dict:
    """把用户那段中文口语描述扩写成**中文**画面描述（2026-09-19 斌哥定）。

    两步走的第一步：点「让 AI 帮写」→ 拿到一段中文，看得懂、能改；
    点「生成视频」时再走 `/compose` 编成英文六段式（那一步本来就是"中文进、英文出"）。

    为什么不一步到位：英文六段式摆给用户看，他判断不了"这是不是我想拍的东西"，改也无从改起。
    代价是这里多一次文本模型调用（DeepSeek，很便宜，**不属于出图那种日额度**）。

    ⚠️ **不注入素材编号**：编号表是出片时按当时选中的素材算的（`build_ref_plan`），
    而用户在这两步之间还可能改选素材。这里只给模型**名字**。
    """
    with LOCK:
        _task(task_id)
    project, _ = _reload(task_id)
    if project is None:
        raise HTTPException(status_code=409, detail="这个作品还没有故事设定，先生成设定再写提示词")

    description = body.description.strip()
    if not description:
        raise HTTPException(status_code=422, detail="先写一句想拍什么，再来让 AI 帮你写")

    # 素材是**可选**的：没选素材也要能优化（用户可能还在想拍什么）。
    # 有素材就把名字给它，正文里能自然地说「林晚」「咖啡馆」，而不是"一个人""一个地方"。
    plan = ref_plan.build_ref_plan(
        project, characters=body.characters, props=body.props,
        scene=body.scene, images=body.images, audios=body.audios,
        use_voice=body.use_voice,
    )
    material_names = _material_lines_for(plan, with_tags=False)

    try:
        optimized = agents.optimize_description(
            project, description=description,
            material_names=material_names, duration=body.duration,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"优化提示词失败：{type(exc).__name__}: {exc}"
        ) from exc
    if not optimized:
        raise HTTPException(status_code=502, detail="模型没有返回内容，请重试")

    return {"description": optimized, "materials": material_names}


class SegmentVideoBody(BaseModel):
    """单段生成的提交体。

    `frames` 是**选中的素材引用**（如 "character:0"、"scene:1"、"image:0"），
    按用户点选的顺序排 —— 第 1 个能出图的默认当首帧。
    让后端解析成路径而不是前端传路径：前端伪造不了服务器文件位置。

    `first_frame` / `last_frame`（2026-09-19 加）是**用户在「首尾帧」选择器里显式挑的**
    两个引用，各自可空：
      只给 first_frame → 以它开头｜只给 last_frame → 以它收尾｜两个都给 → 一头一尾
      两个都不给 → 没有首尾帧，首帧退回 frames 里第一个能出图的素材
    ⚠️ 替代了原来的 `use_last_frame`（那个是"勾一下就拿选中的第 2 张当尾帧"，
    尾帧是哪张完全由点选顺序决定，用户控制不了）。
    """

    frames: list[str] = Field(default_factory=list)
    first_frame: str = ""   # 素材引用；空 = 用 frames 里第一个能出图的
    last_frame: str = ""    # 素材引用；空 = 不设尾帧
    prompt: str = Field("", max_length=20000)
    duration: float = 10.0
    # 像素预算（前端「清晰度」档位）。None = 用服务配置里的全局值。
    # H3 输出上限是 768p（megapixels 0.98 → 1344x768），所以"1080P"这一档
    # 实际是"按模型上限出片"，不是真 1080p。
    megapixels: float | None = Field(None, ge=0.1, le=1.5)
    # 「AI 帮我写」模式下用户输入的那段中文描述。**不参与出片**，只为「生成记录」
    # 留个底 —— 过几天回看时，一段英文正文（prompt）是看不懂自己当初想要什么的。
    # 手写模式没有这一层，留空即可。
    note: str = Field("", max_length=2000)
    # 编排出来的**音轨段**（环境底声 + 动作音），2026-09-19 加。
    # 只有基础模式（I2V / 首尾帧）才用得上：那边提示词是三段式，`overall_soundscape`
    # 由出片时拼（`video_provider.compose_h3_prompt` 的 audio 参数）。
    # Ref2VA 的六段式里已经自带 overall_soundscape 一段，这里传了也会被忽略。
    soundscape: str = Field("", max_length=2000)


def _resolve_material_ref(
    project, ref: str, prefer_sheet: bool = False
) -> dict[str, str] | None:
    """把 "character:0" / "scene:0" / "prop:1" / "image:0" / "audio:0" 解析成
    `{ref, kind, name, path}`；解析不出来（或那张图不存在）返回 None。

    道具用主视图（images[0]），场景/图片/音频用唯一那张。

    角色走哪张图由 `prefer_sheet` 决定（2026-09-19 斌哥定）：
      - False（I2V / 首尾帧那条链路）：用**正面定妆照**（image_path）—— 那张是要当**首帧**
        的，四格拼图当首帧 = 视频第一秒就是四个人并排，绝对不行。
      - True（Ref2VA 多图参考）：优先用**四视图设定图**（sheet）—— 参考图不是帧，给的信息
        越多越好：一张里就有大特写 + 正/侧/背三个全身，正是用户做这张图的目的。
        （他的原话："我上传那个角色图肯定是要多视角那个啊"。）没有设定图才退回单张。

    ⚠️ **索引一律是列表的全局下标**：角色 = `project.characters[i]`，
    其余 = `project.assets[i]`（**不是「同类内的序号」**）。
    前端 allItems 里 `index: i` 就是这个 i，卡片 key、上传、改名、删除、生成历史
    全都按它发请求，后端那些接口也都是按全局下标取的 —— 这里曾经单独按"同类内序号"解析，
    于是 `assets = [场景, 道具, 场景]` 这种顺序下第二个场景（前端发 `scene:2`）
    被判成越界 → **静默丢掉**。用户看到的现象就是"我明明选了场景，
    ComfyUI 里只有一张角色图"（2026-09-19 实测踩到，见 `_verify_segment_record.py`）。

    两件事必须一起做：按全局下标取，**再核对取到的条目 kind 对不对** ——
    只按全局下标不看 kind，就是更早那次 `image:0` 取到道具、整条链路退化成 T2VA 的老坑。

    多返回 kind/name 是给「生成记录」用的：只存路径的话，用户看到的是一串
    `outputs/3f2a…/assets/…` 的哈希目录，等于什么都没说。**名字才是用户认得的那个东西。**
    """
    kind, _, idx = str(ref or "").partition(":")
    try:
        i = int(idx)
    except ValueError:
        return None
    path, name = "", ""
    if kind == "character":
        chars = project.characters
        if 0 <= i < len(chars):
            c = chars[i]
            if prefer_sheet and c.sheet and os.path.isfile(c.sheet):
                path, name = c.sheet, c.name
            else:
                path, name = c.image_path, c.name
    else:
        assets = project.assets
        if 0 <= i < len(assets) and str(assets[i].kind or "") == kind:
            a = assets[i]
            path = a.images[0] if a.images else ""
            name = a.name
    if not path:
        return None
    return {"ref": f"{kind}:{i}", "kind": kind, "name": name or f"{kind}{i}", "path": path}


def _ref_display_name(project, ref: str) -> str:
    """给**解析失败**的引用串配一个用户认得的名字，用于"这次没带上"的提示。

    解析失败可能是：索引越界 / kind 对不上 / 那张素材还没有图。三种都得说得出是谁，
    否则用户只看到"有几张没带上"，还是不知道该去补哪一张。拿不到名字就退回引用串本身。
    """
    kind, _, idx = str(ref or "").partition(":")
    try:
        i = int(idx)
    except ValueError:
        return ref
    if kind == "character":
        if 0 <= i < len(project.characters):
            return project.characters[i].name or ref
        return ref
    if 0 <= i < len(project.assets):
        return project.assets[i].name or ref
    return ref


# 单段生成的任务状态。与 video jobs 分开 —— 它不属于任何 shot/block，
# 产物直接落 segments/，由 /files 挂载播放。
SEGMENT_JOBS: dict[str, dict] = {}


# 「生成记录」的边车文件：一段视频配一个同名 json（seg_xxx.mp4 → seg_xxx.json），
# 记下这段是用哪些素材、哪句提示词、什么参数生成的。见 start_segment_video 里的说明。
def _seg_sidecar_path(task_id: str, name: str) -> str:
    return os.path.join(_out_dir(task_id), "segments", os.path.splitext(name)[0] + ".json")


def _seg_sidecar(task_id: str, name: str) -> dict[str, Any] | None:
    """读某段视频的边车记录。**没有或读坏了都返回 None**（不是抛异常）——
    2026-09-18 之前生成的片子本来就没有这个文件，那是正常情况，不是错误。"""
    path = _seg_sidecar_path(task_id, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# Ref2VA 能接几张参考图。**真值来自官方节点的 schema，不是猜的**：
#   comfy_extras/nodes_minimax_h3.py → MiniMaxH3ReferenceToVideo.define_schema()
#   io.Autogrow.Input("ref_images", template=io.Autogrow.TemplatePrefix(
#       input=io.Image.Input("ref_image", ...), prefix="ref_image_", min=0, max=9))
# 即 ref_images.ref_image_0 … ref_image_8，共 9 个（Autogrow，可增长）。
# ⚠️ 2026-09-27 之前这里写的是 3 —— 那是照 `comfyui/h3_r2v_ui.json`（UI 导出文件）里
#    "只连了 3 条"反推的，**没查节点 schema**。第 4 张起其实完全接得进去，白白被丢掉
#    （斌哥："一个场景很多角色，不是放不了这么多图片？"）。
# ⚠️ 判断"某个节点有几个口"要查 schema 或 `GET /object_info/<节点名>`，
#    **别拿 UI 导出文件里连了几条当上限** —— 这份文件里只连了 3 条而已。
REF2VA_MAX_REFS = 9


@app.post("/api/tasks/{task_id}/segment/video")
def start_segment_video(task_id: str, body: SegmentVideoBody) -> dict:
    """按「选中的素材 + 已编排的提示词」生成一段视频。

    首帧/尾帧由 `body.first_frame` / `body.last_frame` **显式指定**（可各自为空）：
      只给首帧 → 以它开头｜只给尾帧 → 以它收尾｜都给 → 一头一尾｜都不给 → 没有首尾帧，
      首帧退回 frames 里第一个能出图的素材。
    """
    task = _task(task_id)
    if task["status"] == "running":
        raise HTTPException(status_code=409, detail="已有任务在执行中")
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="缺提示词 —— 先点「生成视频」完成编排，或自己在手写模式里填")
    # 配置自检（2026-09-19 补）：这条流程以前**没走**这一步，配置不对只能等出片线程里失败。
    # 现在包括"LoRA 和工作流不配套"这种一眼能看出的错（混用会糊，白等半小时）。
    cfg_error = _video_config_error(_load_service_config())
    if cfg_error:
        raise HTTPException(status_code=409, detail=cfg_error)

    project, _ = _reload(task_id)
    if project is None:
        raise HTTPException(status_code=409, detail="这个作品还没有故事设定")
    # 走的是哪份工作流，这一处定两件事（2026-09-19）：
    #   ① 角色拿哪张图：I2V 拿正面定妆照（那张要当**首帧**，四格拼图当首帧＝第一秒四个人并排）；
    #      Ref2VA 拿**四视图设定图**（参考图不是帧，信息越多越好 —— 这张图就是为此做的）。
    #   ② 素材在记录里算什么身份：I2V 有首帧/尾帧；Ref2VA 没有首尾帧，全是参考图。
    cfg_now = _load_service_config()
    wf_ref2va = str(cfg_now.get("video_workflow") or "i2v") == "ref2va"

    resolved: list[dict[str, str]] = []
    missing: list[str] = []      # 解析不出来 / 图还不存在的，说得出名字（前端要弹提示）
    for ref in body.frames:
        m = _resolve_material_ref(project, ref, prefer_sheet=wf_ref2va)
        if m and os.path.isfile(m["path"]):
            resolved.append(m)
        elif str(ref or "").strip():
            # ⚠️ **绝不能默默丢掉**。2026-09-19 的坑：索引口径不一致让第二个场景
            #    （前端发 scene:2）解析成越界，一声不响地被滤掉 —— 用户以为带上了，
            #    到 ComfyUI 一看只有一张角色图。凡是没带上的，都要有名字、进返回值。
            name = _ref_display_name(project, str(ref).strip())
            if name not in missing:
                missing.append(name)
    paths = [m["path"] for m in resolved]
    if missing:
        print(f"[出片] 这些素材没带上（没有图 / 引用对不上）：{'、'.join(missing)}")

    notes: list[str] = []
    # Ref2VA 的参考图口是 `ref_image_0 … ref_image_8` 共 9 个（见 REF2VA_MAX_REFS）。
    # 第 10 张会被拼成 `ref_image_9` —— 那才是工作流里不存在的输入，ComfyUI 会直接拒单。
    # 所以在这里就截断，并说清楚少了谁。
    if wf_ref2va and len(paths) > REF2VA_MAX_REFS:
        notes.append(
            f"Ref2VA 最多吃 {REF2VA_MAX_REFS} 张参考图，多的这次没带上："
            f"{'、'.join(m['name'] for m in resolved[REF2VA_MAX_REFS:])}。"
        )
        resolved = resolved[:REF2VA_MAX_REFS]
        paths = [m["path"] for m in resolved]

    # 用户在选择器里显式挑的首/尾帧优先（2026-09-19）。挑的那张通常也在 frames 里，
    # 但也允许单独挑一张没勾选的图 —— 两种都吃。
    def _pick(ref: str) -> dict[str, str] | None:
        m = _resolve_material_ref(project, ref, prefer_sheet=wf_ref2va) if ref.strip() else None
        return m if m and os.path.isfile(m["path"]) else None

    first_pick, last_pick = _pick(body.first_frame), _pick(body.last_frame)
    first = first_pick["path"] if first_pick else (paths[0] if paths else "")
    last = last_pick["path"] if last_pick else ""
    if not first:
        # 一张图都没有：这是 T2VA（自定义 / 没选素材）的路径
        raise HTTPException(
            status_code=422,
            detail="当前是纯文生视频（没选任何图片素材）：需要先把 H3 的 T2VA 工作流导成 "
                   "comfyui/h3_t2v_api.json（在 ComfyUI 里加载官方 t2v 模板后「导出（API）」）。"
                   "不想导的话，选一张图当首帧即可",
        )
    duration = min(max(body.duration, 4.0), 15.0)   # 官方区间 4-15s

    # 这一段实际用了哪些图、各自什么身份。三个来源合并去重：
    #   ① 显式挑的首帧 / 尾帧（可能不在 frames 里）
    #   ② frames 里被当作首帧的那张
    #   ③ 其余选中的素材（参与写提示词，但没当成帧）
    # 三者要分开说，否则用户会以为每一张都进了模型。
    # role 是按工作流语义定的（见上面 cfg_now 那段）：Ref2VA 下没有首帧/尾帧一说 ——
    # 继续写会让人以为"我明明没挑首帧，怎么冒出个首帧"（斌哥 2026-09-19 就这么问过）。
    materials: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    def _add(m: dict[str, str] | None, role: str) -> None:
        if not m or m["path"] in seen_paths:
            return
        seen_paths.add(m["path"])
        materials.append({
            "ref": m["ref"], "kind": m["kind"], "name": m["name"], "role": role,
            # 实际用的**文件名**：一个角色有单张定妆照和四视图设定图两个文件，
            # 用户会问"到底传的哪个"（2026-09-19 他在 ComfyUI 里比了半天）。
            "file": os.path.basename(m["path"]),
        })

    _add(first_pick, "selected" if wf_ref2va else "first_frame")
    if not first_pick and resolved:
        _add(resolved[0], "selected" if wf_ref2va else "first_frame")
    if not wf_ref2va:
        # Ref2VA 那份工作流里根本没有 last_frame 这个口，写了也是假的
        _add(last_pick, "last_frame")
    for m in resolved:
        _add(m, "selected")

    job_id = uuid.uuid4().hex[:12]
    out_dir = os.path.join(_out_dir(task_id), "segments")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"seg_{job_id}.mp4")

    # 「生成记录」要能回答"这一段用了哪些素材、哪句提示词、什么参数"。
    # ⚠️ **必须落到磁盘**（seg_<job>.json 边车文件）：SEGMENT_JOBS 是内存字典，
    # 服务一重启就空，而用户回看记录往往是几天以后 —— 那时 mp4 还在，参数没了。
    # 出片前就写：这一步之后才开始跑模型，写下去的是"我打算这么生成"，
    # 万一跑到一半服务挂了，留下的记录也比什么都没有强。
    # 工作流能力与"选了几张图 / 挑没挑首尾帧"对不上时明说（2026-09-19）：
    # ① I2V 那份工作流的 H3 节点**只有一个 first_frame 口**，选出来的第 2、3 张参考图
    #    会被 provider 上传到 ComfyUI，但**图里没有口接它们** —— 用户会看到"图传上去了
    #    却没连上"。而这边的提示词是按 Ref2VA 六段式写的（正文里还会引用 <Picture 2>），
    #    更容易让人以为场景已经喂进去了。
    # ② Ref2VA 反过来没有尾帧概念，用户挑的尾帧会**被无声忽略**。
    if wf_ref2va and last_pick:
        notes.append(
            f"Ref2VA 工作流没有尾帧概念：你挑的尾帧「{last_pick['name']}」"
            "这次只当参考图用、不会锁结尾。"
        )
    if len(paths) > 1 and not wf_ref2va:
        notes.append(
            f"当前是 I2V 工作流：只有第一张参考图会当首帧进模型，"
            f"另外 {len(paths) - 1} 张（{('、'.join(m['name'] for m in resolved[1:]))}）"
            "只参与写提示词、不会进画面。要真的多图参考，去「服务配置」把「视频工作流」切成 Ref2VA。"
        )
    # 步数与所选 LoRA 的蒸散步数不配套也要说（不拦）：蒸馏件照 4/8 步的轨迹调的，
    # 填 12 步不会报错，但通常更糊、还要多花约 3 倍时间。想要更多步请「不加载 LoRA」。
    # ⚠️ 这一段是**独立**的一条，别塞进上面那两个 if 的链子里（曾经把 I2V 那条挤成 elif，
    #    结果两种提醒只能出一个）。
    lora_steps = _LORA_STEPS.get(str(cfg_now.get("video_lora") or "").strip())
    try:
        steps_now = int(str(cfg_now.get("video_steps") or "").strip() or 0)
    except ValueError:
        steps_now = 0
    if lora_steps and steps_now and steps_now != lora_steps:
        notes.append(
            f"采样步数填的是 {steps_now}，但所选 LoRA 是 {lora_steps} 步蒸馏的 —— "
            "不报错，但通常更糊也更慢（采样时间大致与步数成正比）。"
            "想要更多步，请把「加速 LoRA」换成「不加载 LoRA」再跑 20 步以上。"
        )
    warning = " ".join(notes)
    if warning:
        print(f"[出片] {warning}")

    record = {
        "job_id": job_id,
        "file": os.path.basename(out_path),
        "created": time.time(),
        "mode": "flf" if last else "i2v",
        "duration": duration,
        # 真正下发给 provider 的像素预算。None 时 ComfyUI 那条会落到
        # H3_MEGAPIXELS 环境变量（video_provider 里），这里按同一套规则算出来，
        # 免得记录里写个 null 让人以为没生效。
        "megapixels": float(body.megapixels) if body.megapixels else float(os.getenv("H3_MEGAPIXELS", "0.9")),
        "video_backend": str(cfg_now.get("video_backend") or ""),
        "video_mode": str(cfg_now.get("video_mode") or ""),
        # 用哪份工作流（i2v / ref2va）。「生成记录」里要能说清"这段是单图首帧还是多图参考"——
        # mode 那个字段（i2v/flf）说的是"有没有尾帧"，跟工作流不是一回事。
        "video_workflow": "ref2va" if wf_ref2va else "i2v",
        "note": body.note.strip(),
        "prompt": prompt,
        "materials": materials,
        # 选了但没能带上的素材（没有图 / 引用对不上）。记在边车里，回看这一段时
        # 能对上"为什么画面里没有它"。
        "missing": missing,
        # 工作流能力与所选素材不匹配时的那句提醒（见上面 warning 的计算）
        "warning": warning,
    }
    try:
        with open(_seg_sidecar_path(task_id, record["file"]), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except Exception as exc:      # 写不下记录不该挡住出片
        print(f"[生成记录] 边车写入失败 {record['file']}：{exc}")

    SEGMENT_JOBS[job_id] = {
        "job_id": job_id, "task_id": task_id, "status": "running",
        "mode": record["mode"],
        "first_frame": first, "last_frame": last,
        "out_path": out_path, "error": "", "created": record["created"],
        "record": record,
    }

    def runner() -> None:
        job = SEGMENT_JOBS[job_id]
        try:
            cfg = _load_service_config()
            if cfg.get("video_backend") != "api" and not str(cfg.get("comfyui_url") or "").strip():
                raise RuntimeError("ComfyUI 地址为空：请在「服务配置」里填")
            provider = _make_video_provider()
            provider.generate(
                image_path=first, video_prompt=prompt, duration=duration,
                out_path=out_path, last_frame_path=last, megapixels=body.megapixels,
                # 基础模式下这句会进 overall_soundscape（三段式的一段）；
                # Ref2VA 的六段式自带那一段，传进去被忽略。
                audio=body.soundscape.strip(),
                # 多张参考图（2026-09-19）：只有走 Ref2VA 工作流时才真的接得进去，
                # I2V 那份只有一个 first_frame 口、会忽略这个参数。见 video_provider
                # 的 _build_workflow —— 那里按 class_type 判断走哪条。
                ref_image_paths=paths,
            )
            with LOCK:
                job["status"] = "succeeded"
        except Exception as exc:
            with LOCK:
                job["status"] = "failed"
                job["error"] = _video_error_text(exc)

    threading.Thread(target=runner, daemon=True).start()
    return {
        "job_id": job_id, "mode": SEGMENT_JOBS[job_id]["mode"],
        "first_frame": first, "last_frame": last,
        # 选了却没带上的素材（名字）。前端据此弹一句提示 —— 静默丢素材是这个功能
        # 2026-09-19 最难查的一个 bug，别再让它无声无息。
        "missing": missing,
        # 工作流吃不下的那些参考图（I2V 只吃首帧）。同上，前端弹一句提醒。
        "warning": warning,
    }


@app.get("/api/segment-jobs/{job_id}")
def segment_video_job(job_id: str) -> dict:
    """单段生成的状态轮询。产物 mp4 由 /files 挂载直接播。"""
    job = SEGMENT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
    out = dict(job)
    if job["status"] == "succeeded" and os.path.isfile(job["out_path"]):
        out["video_url"] = f"/files/{job['task_id']}/segments/{os.path.basename(job['out_path'])}"
    return out


@app.get("/api/tasks/{task_id}/segments")
def list_segments(task_id: str) -> dict:
    """这个作品生成过的所有单段视频（02「生成视频」的产物），新的排前面。

    ⚠️ 数据源是**磁盘上的 mp4**（outputs/{task_id}/segments/），不是 SEGMENT_JOBS ——
    后者是内存字典，服务一重启就空，前端刷新页面也会丢（segJob 同理）。
    但文件是一直在的，用户要能随时回看自己生成过什么，所以以磁盘为准。
    内存里还留着的 job 顺手把 mode（I2V / FLF）补上；重启后补不了，前端要容忍空串。
    """
    _task(task_id)      # 作品不存在直接 404
    seg_dir = os.path.join(_out_dir(task_id), "segments")
    items: list[dict[str, Any]] = []
    if os.path.isdir(seg_dir):
        for name in os.listdir(seg_dir):
            if not name.lower().endswith(".mp4"):
                continue
            path = os.path.join(seg_dir, name)
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            if size <= 0:
                continue    # 出片中途挂掉会留个 0 字节的壳，别列出来骗人
            items.append({
                "name": name,
                "url": f"/files/{task_id}/segments/{name}",
                "size": size,
                "created": os.path.getmtime(path),   # 秒级时间戳
                "mode": "",
                # 有没有「用了什么」可看（边车 json 在不在）。前端据此决定那颗按钮
                # 要不要置灰 —— 记录本身不塞进列表里，提示词可能很长，
                # 没必要每次进工作台都全量传一遍，点开时再单独取。
                "has_detail": os.path.isfile(_seg_sidecar_path(task_id, name)),
            })
    items.sort(key=lambda x: x["created"], reverse=True)
    by_name = {
        os.path.basename(str(job.get("out_path") or "")): job
        for job in SEGMENT_JOBS.values() if job.get("task_id") == task_id
    }
    for it in items:
        job = by_name.get(it["name"])
        if job:
            it["mode"] = str(job.get("mode") or "")
        elif it["has_detail"]:
            # 服务重启后内存里没有 job 了，模式从边车文件里补回来
            side = _seg_sidecar(task_id, it["name"])
            if side:
                it["mode"] = str(side.get("mode") or "")
    return {"items": items, "count": len(items)}


@app.get("/api/tasks/{task_id}/segments/{name}")
def segment_detail(task_id: str, name: str) -> dict:
    """某一段视频「用了什么」——素材 / 提示词 / 参数。

    数据源是出片时写下的边车文件（segments/seg_xxx.json），**不是** SEGMENT_JOBS。
    ⚠️ 2026-09-18 之前生成的片子没有这个文件，此时返回 `found=False`，
    前端要如实说"这条没留下记录"，**不要**渲染成一堆空字段（那看起来像 bug）。
    """
    _task(task_id)      # 作品不存在直接 404
    base = os.path.basename(name)
    if base != name or not base.lower().endswith(".mp4"):
        # 挡目录穿越：basename 一旦被改写（"../x" / "a\\b"）就说明不是干净的文件名
        raise HTTPException(status_code=400, detail="文件名不合法")
    if not os.path.isfile(os.path.join(_out_dir(task_id), "segments", base)):
        raise HTTPException(status_code=404, detail=f"没有这段视频：{base}")

    side = _seg_sidecar(task_id, base)
    if side is None:
        return {"found": False, "name": base}
    out: dict[str, Any] = {"found": True, "name": base}
    out.update(side)
    out["name"] = base       # 以请求到的文件名为准，别被边车里的 file 字段顶掉
    return out


@app.post("/api/tasks/{task_id}/characters")
def add_character(task_id: str, body: CharacterCreate) -> dict:
    """手动添加角色。

    design=True 且锚点留空：由 AI 按故事设定设计（同步，一次文本调用）。
    design=False：只落一张空条目 —— 名字空则补占位名，不调模型。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        if not task.get("project"):
            raise HTTPException(status_code=409, detail="请先完成故事设定阶段")
        chars = task["project"].setdefault("characters", [])
        name = body.name.strip() or _placeholder_name(
            "未命名角色", [str(c.get("name") or "") for c in chars]
        )
        if any(c.get("name") == name for c in chars):
            raise HTTPException(status_code=422, detail=f"已有同名角色：{name}")

    anchor, anchor_en, voice = body.anchor.strip(), "", body.voice.strip()
    if not anchor and body.design:
        project = Project.from_dict(task["project"])
        try:
            designed = agents.design_character(project, name)
            anchor = designed["anchor"]
            anchor_en = designed["anchor_en"]
            voice = voice or designed["voice"]
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"角色形象设计失败（可手填锚点后重试）：{type(exc).__name__}: {exc}",
            ) from exc
        if not anchor:
            raise HTTPException(
                status_code=502,
                detail="角色形象设计没有返回有效锚点，请手填锚点后重试",
            )

    with LOCK:
        entry = {
            "name": name,
            "anchor": anchor,
            "anchor_en": anchor_en,
            "voice": voice,
            "tts_voice": "",
            "voice_sample": "",
            "image_path": "",
            "images": [],
        }
        task["project"]["characters"].append(entry)
        # 顺手给「还没有音色」的角色分配一个不撞车的建议值。
        # 只写 id、不合成样本 —— 合成要联网，不该在"添加角色"这种轻操作里发生，
        # 用户点「生成音色」时再出音频。
        assigned = tts.assign_voices(task["project"]["characters"])
        for ch in task["project"]["characters"]:
            if not str(ch.get("tts_voice") or "").strip():
                ch["tts_voice"] = assigned.get(str(ch.get("name") or ""), "")
        _persist(task_id)
        return dict(entry)


@app.delete("/api/tasks/{task_id}/characters/{index}")
def delete_character(task_id: str, index: int) -> dict:
    """删除角色：连同定妆照文件一起删。块里引用的名字会留着（提示用户自查）。"""
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        removed = chars.pop(index)
    for suffix in ("", "_side", "_back", "_full"):
        p = os.path.join(
            _out_dir(task_id),
            "characters",
            f"{pipeline._safe_char_name(removed.get('name', ''))}{suffix}.png",
        )
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    with LOCK:
        _persist(task_id)
    return {"deleted": removed.get("name", "")}


class UploadBody(BaseModel):
    """素材上传。用 JSON base64 而不是 multipart：免去 python-multipart 依赖，
    前端 FileReader 读成 base64 直接 POST，一张图几 MB 没有负担。"""
    filename: str = "upload.png"
    data_b64: str = ""


def _decode_upload(body: UploadBody) -> bytes:
    import base64 as _b64

    text = str(body.data_b64 or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="缺少文件内容（data_b64）")
    # 前端可能带 data:image/png;base64, 前缀，容错剥掉
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return _b64.b64decode(text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"base64 解码失败：{exc}") from exc


# 素材文件名后缀**必须与 _load_task_from_disk 里找的名字一致**，否则重启后认不回来。
# 图片一律 .png：本项目的定妆照/素材图命名早就定死无后缀+ .png，
# 内容其实是 JPEG 也照样渲染（豆包导出的图就是这样，见 image_provider 的注释）。
# 音频例外 —— 后缀跟实际上传内容走，见 _asset_file_name。
_ASSET_FILE = {
    "prop": ("prop_{safe}_front", ".png"),
    "scene": ("scene_{safe}", ".png"),
    "image": ("img_{safe}", ".png"),
}


def _asset_file_name(kind: str, name: str, filename: str = "") -> str:
    """素材落盘的文件名。

    图片类走 _ASSET_FILE 的固定后缀；音频类**后缀跟实际内容走**：
    浏览器录制出来是 webm/ogg，硬写成 .mp3 会让 StaticFiles 报 audio/mpeg，
    前端 <audio> 可能拒绝播。_load_task_from_disk 按 snd_{名}. 前缀扫描认它。
    """
    safe = pipeline._safe_char_name(name)
    if kind == "audio":
        ext = os.path.splitext(str(filename or ""))[1].lower()
        if ext not in VOICE_EXTS:
            ext = ".mp3"
        return f"snd_{safe}{ext}"
    stem, ext = _ASSET_FILE[kind]
    return stem.format(safe=safe) + ext


@app.post("/api/tasks/{task_id}/characters/{index}/upload")
def upload_character_image(task_id: str, index: int, body: UploadBody) -> dict:
    """上传角色图，**直接作为正面定妆照**。

    与 2026-09-14 删掉的那个上传不同：那时上传的图是喂给 Edit 模型当图生图参考的；
    现在全链路已经没有图生图，上传就是「这张图就是这个角色」。
    这条是「没有任何图像 API 也能用」的关键 —— 自己备素材，全流程不碰模型接口。
    """
    raw = _decode_upload(body)
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        name = chars[index].get("name", f"character{index}")

    cdir = os.path.join(_out_dir(task_id), "characters")
    os.makedirs(cdir, exist_ok=True)
    # ⚠️ **覆盖式的上传也存历史**：用户传了新图又想找回上一张，跟"重新生成"是同一个诉求。
    #    下面是 `open(path, "wb")`，同名文件直接被顶掉。
    _snapshot_history(task_id, "character", pipeline._safe_char_name(name),
                      _material_files("character", chars[index]),
                      str(chars[index].get("version", "") or ""))
    path = os.path.join(cdir, f"{pipeline._safe_char_name(name)}.png")
    with open(path, "wb") as fh:
        fh.write(raw)

    with LOCK:
        entry = task["project"]["characters"][index]
        entry["image_path"] = path
        # 正面图占 images 第一位（四视角约定里正面就是无后缀那张），其余视角保留
        rest = [p for p in (entry.get("images") or []) if os.path.basename(p) != os.path.basename(path)]
        entry["images"] = [path] + rest
        entry.pop("stale", None)
        entry["version"] = uuid.uuid4().hex[:6]
        _persist(task_id)
        return dict(entry)


@app.post("/api/tasks/{task_id}/assets/{index}/upload")
def upload_asset_file(task_id: str, index: int, body: UploadBody) -> dict:
    """上传素材文件，**直接作为这个素材的图/音频**。

    按 kind 落到约定文件名（见 `_ASSET_FILE`），道具只填主视图、场景是单张全景、
    其他图片/其他音频各一张 —— 与 `_load_task_from_disk` 的重指规则一一对应，
    改文件名前先看那边的判断题。
    """
    raw = _decode_upload(body)
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        assets = (task.get("project") or {}).get("assets") or []
        if index < 0 or index >= len(assets):
            raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
        kind = str(assets[index].get("kind") or "prop")
        name = str(assets[index].get("name") or f"asset{index}")

    if kind not in _ASSET_FILE and kind != "audio":
        raise HTTPException(status_code=422, detail=f"这个素材类型不支持上传：{kind}")
    adir = os.path.join(_out_dir(task_id), "assets")
    os.makedirs(adir, exist_ok=True)
    # ⚠️ 上传是**覆盖同名文件**，覆盖前先留一版历史。
    #    音频跳过：音频没有"版本"一说，`_HISTORY_KINDS` 里也没有 audio（走进去只会 422）。
    if kind != "audio":
        _snapshot_history(task_id, kind, pipeline._safe_char_name(name),
                          _material_files(kind, assets[index]),
                          str(assets[index].get("version", "") or ""))
    path = os.path.join(adir, _asset_file_name(kind, name, body.filename))
    if kind == "audio":
        # 换样本要清掉旧后缀（.mp3 → .webm 这类），否则目录里留两份、
        # 重启后认到的是排序靠前的那份
        safe = pipeline._safe_char_name(name)
        for fn in os.listdir(adir):
            if fn.startswith(f"snd_{safe}.") and fn != os.path.basename(path):
                try:
                    os.remove(os.path.join(adir, fn))
                except OSError:
                    pass
    with open(path, "wb") as fh:
        fh.write(raw)

    with LOCK:
        entry = task["project"]["assets"][index]
        # 道具的主视图要排在最前（_collect 之类的下游按 images[0] 取主视图）
        rest = [p for p in (entry.get("images") or []) if os.path.basename(p) != os.path.basename(path)]
        entry["images"] = [path] + rest
        entry["version"] = uuid.uuid4().hex[:6]
        _persist(task_id)
        return dict(entry)


@app.post("/api/tasks/{task_id}/assets")
def add_asset(task_id: str, body: AssetCreate) -> dict:
    """手动添加素材（道具/场景/图片/音频）。

    锚点留空且 design=True 时由 AI 按故事设定设计（同步，一次文本调用）；
    design=False 则只落一张空条目（名字空则补占位名），不碰模型接口。

    场景锚点纪律：空间结构 + 光源方向 + 材质质感，禁止出现人物；
    道具锚点纪律：材质/颜色/形状/磨损，禁止情绪词。与导演 Agent 同一套。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        if not task.get("project"):
            raise HTTPException(status_code=409, detail="请先完成故事设定阶段")
        assets_now = task["project"].setdefault("assets", [])
        name = body.name.strip() or _placeholder_name(
            f"未命名{ASSET_CN.get(body.kind, '素材')}",
            [str(a.get("name") or "") for a in assets_now if a.get("kind") == body.kind],
        )

    anchor = body.anchor.strip()
    # 「其他图片 / 其他音频」是用户自己上传的原始素材，**不参与锚点体系** ——
    # 它们没有"要跨镜保持一致的描述"这回事，所以不花 LLM 的钱去设计锚点。
    # 这条也是"没有文本 API 也能用"的关键：这类素材全流程不碰任何模型接口。
    # design=False 同理：这是"先落一张空卡片"的路径，用户自己填名字和内容，
    # 谁都不该替他花一次模型调用。
    if not anchor and body.design and body.kind in ("prop", "scene"):
        project = Project.from_dict(task["project"])
        try:
            anchor = agents.design_asset(project, body.kind, name)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"素材设计失败（可手填描述后重试）：{type(exc).__name__}: {exc}",
            ) from exc
        if not anchor:
            raise HTTPException(
                status_code=502,
                detail="素材设计没有返回有效描述，请手填描述后重试",
            )

    with LOCK:
        assets = task["project"].setdefault("assets", [])
        if any(a.get("name") == name and a.get("kind") == body.kind for a in assets):
            raise HTTPException(
                status_code=422, detail=f"已有同名{ASSET_CN.get(body.kind, '素材')}：{name}"
            )
        entry = {
            "kind": body.kind,
            "name": name,
            "anchor": anchor,
            "anchor_en": "",
            "images": [],
        }
        assets.append(entry)
        _persist(task_id)
        return dict(entry)


@app.patch("/api/tasks/{task_id}/assets/{index}")
def patch_asset(task_id: str, index: int, patch: AssetPatch) -> dict:
    """编辑素材名字与描述。描述变了旧图就不再对应，标记 stale 提示重新生成。"""
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        assets = (task.get("project") or {}).get("assets") or []
        if index < 0 or index >= len(assets):
            raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
        entry = assets[index]
        changed = False
        if patch.name is not None and patch.name.strip() != entry.get("name"):
            new_name = patch.name.strip()
            if not new_name:
                raise HTTPException(status_code=422, detail="素材名不能为空")
            if any(
                j != index and a.get("name") == new_name and a.get("kind") == entry.get("kind")
                for j, a in enumerate(assets)
            ):
                raise HTTPException(status_code=422, detail=f"已有同名素材：{new_name}")
            entry["name"] = new_name
            changed = True
        if patch.anchor is not None and patch.anchor.strip() != entry.get("anchor"):
            entry["anchor"] = patch.anchor.strip()
            entry["stale"] = True
            changed = True
        if changed:
            _persist(task_id)
        return dict(entry)


@app.delete("/api/tasks/{task_id}/assets/{index}")
def delete_asset(task_id: str, index: int) -> dict:
    """删除素材：连同已生成的素材图一起删。"""
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        assets = (task.get("project") or {}).get("assets") or []
        if index < 0 or index >= len(assets):
            raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
        removed = assets.pop(index)
    for p in removed.get("images") or []:
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    with LOCK:
        _persist(task_id)
    return {"deleted": removed.get("name", "")}


# ---------------------------------------------------------------- 分块流程
# 一块 = 一条 10s 视频。与旧分镜流程的本质区别：不一次拆完全片，
# AI 每次只产出「下一块」，用户逐块验收后再继续；块内选用哪些角色/道具/场景
# 由块自己声明，提示词与首帧图都据此拼装资产锚点。

def _block_by_id(task: dict, block_id: int) -> dict | None:
    for b in task.get("blocks", []):
        if int(b.get("block_id") or 0) == block_id:
            return b
    return None


def _resolve_block_image(task_id: str, block: dict) -> str:
    """块首帧图路径。image_path 可能是生成时的绝对路径，也可能是磁盘恢复后的
    纯文件名，两种都要能解析到真实文件（与 _resolve_image 同一套规则）。"""
    p = block.get("image_path", "")
    if p and os.path.isabs(p) and os.path.isfile(p):
        return p
    name = os.path.basename(p) if p else f"block_{int(block.get('block_id') or 0):02d}.png"
    cand = os.path.join(_out_dir(task_id), "images", name)
    return cand if os.path.isfile(cand) else ""


def _extract_last_frame(task_id: str, block_id: int, video_path: str) -> str:
    """抽取块视频的最后一帧，存 frames/block_XX_last.png，返回绝对路径或空串。

    尾帧的用途：下一块勾了「承接上一块」时，从这一帧接着演——块只有 10 秒，
    一段戏没演完就靠它无缝续上（H3 的 I2VA 会把它当第 0 秒的画面）。
    """
    try:
        frames_dir = os.path.join(_out_dir(task_id), "frames")
        os.makedirs(frames_dir, exist_ok=True)
        out = os.path.join(frames_dir, f"block_{block_id:02d}_last.png")
        subprocess.run(
            [_ffmpeg_exe(), "-y", "-sseof", "-0.1", "-i", video_path,
             "-frames:v", "1", "-update", "1", out],
            capture_output=True, timeout=120,
        )
        return out if os.path.isfile(out) else ""
    except Exception:
        return ""


def _block_last_frame(task_id: str, block: dict) -> str:
    """取某块已抽取的尾帧；没有就从它的视频现抽（块重生成过视频等边缘情况）。"""
    bid = int(block.get("block_id") or 0)
    rel = str(block.get("last_frame") or "")
    if rel:
        p = rel if os.path.isabs(rel) else os.path.join(_out_dir(task_id), rel)
        if os.path.isfile(p):
            return p
    vid_rel = str(block.get("video_path") or "")
    if vid_rel:
        v = vid_rel if os.path.isabs(vid_rel) else os.path.join(_out_dir(task_id), vid_rel)
        if os.path.isfile(v):
            return _extract_last_frame(task_id, bid, v)
    return ""


def _block_start_frame(task_id: str, task: dict, block: dict) -> str:
    """块视频的起点帧。

    continue_last=True 时往回找最近一个有视频的块，拿它的尾帧当第 0 秒——
    真正的「接着演」，块删过、顺序有空洞也无所谓。找不到尾帧就回落到
    本块自己的首帧图（承接不了至少别空转）。
    """
    bid = int(block.get("block_id") or 0)
    if block.get("continue_last"):
        prev_blocks = sorted(
            (b for b in task.get("blocks", []) if int(b.get("block_id") or 0) < bid),
            key=lambda b: int(b.get("block_id") or 0),
            reverse=True,
        )
        for prev in prev_blocks:
            lf = _block_last_frame(task_id, prev)
            if lf:
                return lf
    return _resolve_block_image(task_id, block)


def _run_ai_next_block(task_id: str, instruction: str) -> None:
    """AI 生成下一块的后台线程：分块 Agent 出块 → 提示词 Agent 顺手把提示词写好。

    提示词失败不回滚块本身——块已经拿到了，用户可以编辑剧情后手动重试提示词。
    """
    task = TASKS[task_id]
    try:
        with LOCK:
            project = Project.from_dict(task["project"])
            blocks = [Block.from_dict(b) for b in task.get("blocks", [])]
            next_id = max((b.block_id for b in blocks), default=0) + 1
        result = agents.next_block(project, blocks, instruction)
        if result.get("done"):
            with LOCK:
                task["status"] = "succeeded"
                task["stage"] = "分块编排"
                task["stage_state"] = "blocks"
                task["done"] = len(task.get("blocks", []))
                task["total"] = max(task.get("total", 0), len(task.get("blocks", [])))
                task["stage_note"] = str(result.get("reason") or "故事已经收尾")
                _save_meta(task_id)
            return
        block = Block.from_dict({
            **result["block"],
            "block_id": next_id,
            "duration": 10.0,   # 块固定 10s，AI 不给这个字段的发言权
        })
        try:
            agents.block_prompts(project, block)
        except Exception as exc:
            print(f"[block] 第 {next_id:02d} 块提示词生成失败（块已保留，可重试）：{exc}")
        with LOCK:
            task["blocks"] = sorted(
                task.get("blocks", []) + [block.to_dict()],
                key=lambda b: int(b.get("block_id") or 0),
            )
            task["status"] = "succeeded"
            task["stage"] = "分块编排"
            task["stage_state"] = "blocks"
            task["done"] = len(task["blocks"])
            task["total"] = len(task["blocks"])
            task["stage_note"] = ""
            _persist(task_id)
            _save_meta(task_id)
    except Exception as exc:
        with LOCK:
            task["status"] = "failed"
            task["stage"] = "失败"
            task["error"] = f"{type(exc).__name__}: {exc}"
            task["traceback"] = traceback.format_exc()[-2000:]
            _save_meta(task_id)


@app.post("/api/tasks/{task_id}/blocks/ai-next")
def ai_next_block(task_id: str, body: NextBlockBody) -> dict:
    """AI 生成下一块。两次文本调用（出块 + 提示词）约 20-60 秒，走后台线程轮询。"""
    with LOCK:
        task = _task(task_id)
        if not task.get("project"):
            raise HTTPException(status_code=409, detail="请先完成资产准备阶段")
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        task["status"] = "running"
        task["stage"] = "分块中"
        task["error"] = None
        task["traceback"] = None
    threading.Thread(
        target=_run_ai_next_block, args=(task_id, body.instruction), daemon=True
    ).start()
    return {"started": True}


@app.post("/api/tasks/{task_id}/blocks")
def add_block(task_id: str, body: BlockCreate) -> dict:
    """手动添加一块。提示词留空，出图前先点「AI 写提示词」或自己填。"""
    with LOCK:
        task = _task(task_id)
        if not task.get("project"):
            raise HTTPException(status_code=409, detail="请先完成资产准备阶段")
        blocks = task.setdefault("blocks", [])
        block = Block.from_dict({
            **body.model_dump(),
            "block_id": max((int(b.get("block_id") or 0) for b in blocks), default=0) + 1,
        })
        blocks.append(block.to_dict())
        task["stage_state"] = "blocks"
        task["done"] = len(blocks)
        task["total"] = len(blocks)
        _persist(task_id)
        return block.to_dict()


# 块的剧情字段：改了它们就该重写提示词（提示词由这些字段翻译而来）
_BLOCK_STORY_FIELDS = (
    "summary", "characters", "props", "scene", "dialogue",
    "beats", "camera", "motion", "avoid", "continue_last",
)


@app.patch("/api/tasks/{task_id}/blocks/{block_id}")
def patch_block(task_id: str, block_id: int, patch: BlockPatch) -> dict:
    with LOCK:
        task = _task(task_id)
        block = _block_by_id(task, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"块不存在：{block_id}")
        changed_story = False
        changed_prompt = False
        for field, value in patch.model_dump(exclude_none=True).items():
            if block.get(field) == value:
                continue
            block[field] = value
            if field in _BLOCK_STORY_FIELDS:
                changed_story = True
            else:
                changed_prompt = True
        if changed_story:
            # 剧情变了，旧提示词不再对应，标记出来让前端提示重写
            block["prompt_stale"] = True
        if changed_prompt:
            block["image_stale"] = True
        _persist(task_id)
        return block


@app.delete("/api/tasks/{task_id}/blocks/{block_id}")
def delete_block(task_id: str, block_id: int) -> dict:
    """删除块：连同首帧图/视频/尾帧文件一起删，编号不复用（新块取 max+1）。"""
    with LOCK:
        task = _task(task_id)
        block = _block_by_id(task, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"块不存在：{block_id}")
        task["blocks"].remove(block)
        task["done"] = len(task["blocks"])
        task["total"] = len(task["blocks"])
        _persist(task_id)
    for rel in (
        block.get("image_path") or "",
        block.get("video_path") or "",
        block.get("last_frame") or "",
    ):
        if not rel:
            continue
        p = rel if os.path.isabs(rel) else os.path.join(_out_dir(task_id), rel)
        # 图片字段可能是纯文件名（磁盘恢复形态），按 images/ 目录再试一次
        if not os.path.isfile(p) and not os.path.isabs(rel):
            p = os.path.join(_out_dir(task_id), "images", rel)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    return {"deleted": block_id}


@app.post("/api/tasks/{task_id}/blocks/{block_id}/prompt")
def block_prompt(task_id: str, block_id: int) -> dict:
    """AI 为单块写/重写提示词（同步，一次文本调用）。"""
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        block = _block_by_id(task, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"块不存在：{block_id}")
    project, _ = _reload(task_id)
    if project is None:
        raise HTTPException(status_code=409, detail="任务还没有设定数据")
    bobj = Block.from_dict(block)
    try:
        agents.block_prompts(project, bobj)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"提示词生成失败：{type(exc).__name__}: {exc}"
        ) from exc
    with LOCK:
        block.update(bobj.to_dict())
        block["prompt_stale"] = False
        _persist(task_id)
        return block


@app.post("/api/tasks/{task_id}/blocks/{block_id}/image")
def block_image(task_id: str, block_id: int, force: bool = True) -> dict:
    """给块出首帧图（第 0 秒画面）。同步执行，与单镜重出图同一代价级别。"""
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        block = _block_by_id(task, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"块不存在：{block_id}")
        if not (block.get("visual_prompt") or "").strip():
            raise HTTPException(
                status_code=409,
                detail="还没有图片提示词：先点「AI 写提示词」或手填 visual_prompt",
            )
    project, _ = _reload(task_id)
    bobj = Block.from_dict(block)
    try:
        path = pipeline.render_block_image(
            project, bobj, _out_dir(task_id), force=force
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"首帧图生成失败：{type(exc).__name__}: {exc}"
        ) from exc
    with LOCK:
        block["image_path"] = path
        block["image_stale"] = False
        block["version"] = uuid.uuid4().hex[:6]
        _persist(task_id)
        return block


@app.post("/api/tasks/{task_id}/assets/{index}/reroll")
def reroll_asset(task_id: str, index: int) -> dict:
    """重新生成某个素材的全部视角（同步执行）。

    实现是「删旧图 + 跑 _gen_assets」：判存逻辑天然只补缺的，
    不会动到其他素材已就绪的图。删的时候整个素材的所有视角一起删。

    ⚠️ 必须传 seed_salt：两视角靠固定 seed 保持一致，不换 seed 的话
    「重新生成」出来的图会跟上一批几乎一模一样。
    """
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        assets = (task.get("project") or {}).get("assets") or []
        if index < 0 or index >= len(assets):
            raise HTTPException(status_code=404, detail=f"素材不存在：{index}")
        # ⚠️ 必须在往下走之前挡住上传型素材：下面第一步就是"删掉该素材现有的图"，
        # 而 `_gen_assets` 只补 prop/scene —— 对 image/audio 放行 = 删完不补，
        # 用户上传的图直接丢。其他图片的重新出图走「AI 生成」（/assets/{i}/generate）。
        if str(assets[index].get("kind", "")) not in ("scene", "prop"):
            raise HTTPException(
                status_code=422,
                detail="只有场景与道具能在这里重新生成。其他图片请用卡片上的「AI 生成」，音频重新上传即可。",
            )

    project, _ = _reload(task_id)
    if not project.assets:
        raise HTTPException(status_code=404, detail="项目没有素材")
    adir = os.path.join(_out_dir(task_id), "assets")
    # ⚠️ 下面立刻要**删**旧图 —— 快照必须赶在删之前。
    #    这条只放行 scene / prop（上面挡过），所以 kind 一定是这两个之一。
    _snapshot_history(
        task_id, str(getattr(project.assets[index], "kind", "") or "prop"),
        pipeline._safe_char_name(project.assets[index].name),
        _material_files("", project.assets[index]),
        str(_entry_field(project.assets[index], "version", "") or ""),
    )
    for p in project.assets[index].images or []:
        if os.path.isfile(p):
            os.remove(p)
    try:
        pipeline._gen_assets(project, _out_dir(task_id), seed_salt=uuid.uuid4().hex[:8])
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"素材生成失败：{type(exc).__name__}: {exc}",
        ) from exc

    with LOCK:
        task["project"]["assets"][index] = pipeline.dump(project.assets[index])
        task["project"]["assets"][index]["version"] = uuid.uuid4().hex[:6]
        _persist(task_id)
        updated = dict(task["project"]["assets"][index])
    return updated


@app.patch("/api/tasks/{task_id}/shots/{shot_id}")
def patch_shot(task_id: str, shot_id: int, patch: ShotPatch) -> dict:
    with LOCK:
        task = _task(task_id)
        target = None
        for s in task["shots"]:
            if int(s["shot_id"]) == shot_id:
                target = s
                break
        if target is None:
            raise HTTPException(status_code=404, detail=f"分镜不存在：{shot_id}")

        changed = False
        for field, value in patch.model_dump(exclude_none=True).items():
            if target.get(field) != value:
                target[field] = value
                changed = True
        if changed:
            # 场景描述或提示词改过，旧图就不再对应了，标记出来让前端提示重新出图
            if patch.scene_desc is not None or patch.visual_prompt is not None:
                target["stale"] = True
        _persist(task_id)
        return target


@app.post("/api/tasks/{task_id}/shots/{shot_id}/regenerate")
def regenerate_shot(
    task_id: str,
    shot_id: int,
    regen_prompt: bool = False,
    force: bool = True,
) -> dict:
    with LOCK:
        task = _task(task_id)
        target = None
        for s in task["shots"]:
            if int(s["shot_id"]) == shot_id:
                target = s
                break
        if target is None:
            raise HTTPException(status_code=404, detail=f"分镜不存在：{shot_id}")
        project, _ = _reload(task_id)

    if project is None:
        raise HTTPException(status_code=409, detail="任务尚未生成分镜，无法重生成")

    shot = Shot.from_dict(target, int(target["shot_id"]))

    try:
        pipeline.regenerate_shot(
            project=project,
            shot=shot,
            out_dir=_out_dir(task_id),
            regen_prompt=regen_prompt,
            force=force,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"{type(exc).__name__}: {exc}"
        ) from exc

    with LOCK:
        target.update(shot.to_dict())
        target["stale"] = False
        # 加时间戳，绕过浏览器对同名图片的缓存
        target["version"] = uuid.uuid4().hex[:6]
        _persist(task_id)
        return target


# ---------------------------------------------------------------- 图生视频
# 前端「生成视频」按钮走这里：POST 启动后台线程，GET 轮询状态。
# 产物 mp4 落在 outputs/{task_id}/videos/，由 /files 静态挂载直接播，不用新路由。


def _video_out_path(task_id: str, shot_id: int) -> str:
    return os.path.join(_out_dir(task_id), "videos", f"shot_{shot_id:02d}.mp4")


def _resolve_image(task_id: str, shot: dict) -> str | None:
    """shots.json 里的 image_path 有两种形态：新任务存绝对路径，
    从磁盘恢复的任务只存文件名。两种都要能解析到真实文件。"""
    p = shot.get("image_path", "")
    if p and os.path.isabs(p) and os.path.isfile(p):
        return p
    name = os.path.basename(p) if p else f"shot_{int(shot.get('shot_id') or 0):02d}.png"
    cand = os.path.join(_out_dir(task_id), "images", name)
    return cand if os.path.isfile(cand) else None


def _portrait_first_frame(task_id: str, shot: dict) -> str | None:
    """人物图生视频的首帧来源：用该镜出场角色的定妆照，**不需要每镜出图**。

    这张图选的是「正面全身」优先——四视角里它信息最全（体型比例 + 整套服装），
    模型从它展开时拿到的约束最完整；半身像只给了上半截，镜头一拉开就容易
    在下半身和脚上"编"。

    ⚠️ 这条路有取舍，别当成免费的午餐：I2VA 的图是**第一帧画面**，官方要求把
    style / identity / clothing / composition / space 一起锚定上去。所以用定妆照
    当首帧，构图会被那张图约束（它是纯灰底正面像），背景能不能换成提示词里的
    场景要靠实跑验证。想要"只锁身份、放开构图"，那是 Ref2VA，要另一套权重。
    """
    task = TASKS.get(task_id) or {}
    chars = [c for c in ((task.get("project") or {}).get("characters") or []) if c.get("name")]
    if not chars:
        return None
    by_name = {c["name"]: c for c in chars}

    # 优先本镜出场的角色；character_refs 为空（无人物镜头）时退回全部角色
    wanted = [n for n in (shot.get("character_refs") or []) if n in by_name] or list(by_name)

    def images_of(c: dict) -> list[str]:
        imgs = [p for p in (c.get("images") or []) if p and os.path.isfile(p)]
        if imgs:
            return imgs
        single = str(c.get("image_path") or "")     # 兼容旧任务的单张定妆照
        return [single] if single and os.path.isfile(single) else []

    for name in wanted:
        imgs = images_of(by_name[name])
        if not imgs:
            continue
        for suffix in ("_full", "_side", "_back"):
            hit = next((p for p in imgs if suffix in os.path.basename(p)), None)
            if hit:
                return hit
        return imgs[0]      # 退回第一张（正面半身）
    return None


def _chain_last_frame(task_id: str, shot: dict) -> str:
    """首尾帧接力：本镜标记 continue 且上一镜视频已存在时，
    抽取上一镜最后一帧作为本镜首帧（H3 fl2va 模式），实现真实动作衔接。
    任何一环不满足都返回空串，回落到本镜自己的首帧图。"""
    try:
        if str(shot.get("transition") or "cut") != "continue":
            return ""
        sid = int(shot.get("shot_id") or 0)
        if sid <= 1:
            return ""
        prev = os.path.join(_out_dir(task_id), "videos", f"shot_{sid - 1:02d}.mp4")
        if not os.path.isfile(prev):
            return ""
        frames_dir = os.path.join(_out_dir(task_id), "frames")
        os.makedirs(frames_dir, exist_ok=True)
        out = os.path.join(frames_dir, f"chain_{sid:02d}.png")
        if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(prev):
            return out  # 上一镜视频重生成过（mtime 更新）时缓存自动失效重抽
        subprocess.run(
            [_ffmpeg_exe(), "-y", "-sseof", "-0.1", "-i", prev,
             "-frames:v", "1", "-update", "1", out],
            capture_output=True, timeout=120,
        )
        return out if os.path.isfile(out) else ""
    except Exception:
        return ""


def _resolve_last_frame(task_id: str, shot: dict, mode: str) -> str:
    """按「视频模式」决定这一镜要不要拿上一镜尾帧做首尾帧接力。

    只有 flf 会接力，另外两种都不接：

    - i2v      ：不接力，本镜的分镜图就是唯一起点
    - portrait ：不接力。起点是角色的静态定妆照，从它接出尾帧没有意义
    - flf      ：接力。**不管分镜标的是 cut 还是 continue**，只要上一镜视频在
                 就用它的尾帧。代价是跨场景（cut）也会把上一场的画面带进新场景，
                 这是显式选该模式时的已知取舍。
    """
    if mode == "flf":
        return _chain_last_frame(task_id, {**shot, "transition": "continue"})
    return ""


def _run_video_job(job: dict[str, Any]) -> None:
    task = TASKS.get(job["task_id"])
    try:
        target = None
        if task:
            for s in task["shots"]:
                if int(s["shot_id"]) == job["shot_id"]:
                    target = s
                    break
        mode = _video_mode()
        if mode == "portrait":
            # 人物图生视频：起点是角色定妆照，不需要这一镜出过分镜图
            image = _portrait_first_frame(job["task_id"], target or {})
            if not image:
                raise RuntimeError(
                    "人物图生视频需要一个有定妆照的角色，但这个作品里还没有。"
                    "回到「故事设定」阶段给角色生成定妆照即可；"
                    "也可以改用「首帧图生视频」模式。"
                )
        else:
            image = _resolve_image(
                job["task_id"],
                {"shot_id": job["shot_id"], "image_path": (target or {}).get("image_path", "")},
            )
            if not image:
                raise RuntimeError("找不到首帧图，请先出图")
        # 视频提示词没填时退化为 scene_desc（Qwen3-VL 能理解中文）
        prompt = (target or {}).get("video_prompt") or (target or {}).get("scene_desc", "")
        if not prompt:
            raise RuntimeError("视频提示词为空")
        duration = float((target or {}).get("duration") or 5.0)
        audio = str((target or {}).get("audio") or "")
        last_frame = _resolve_last_frame(job["task_id"], target or {}, mode)

        provider = _make_video_provider()
        provider.generate(
            image, prompt, duration, _video_out_path(job["task_id"], job["shot_id"]),
            audio=audio, last_frame_path=last_frame,
        )

        with LOCK:
            job["status"] = "succeeded"
            job["video_path"] = f"videos/shot_{job['shot_id']:02d}.mp4"
            job["version"] = uuid.uuid4().hex[:6]
            if target is not None:
                target["video_path"] = job["video_path"]
                target["version"] = job["version"]
                _persist(job["task_id"])
    except Exception as exc:
        with LOCK:
            job["status"] = "failed"
            job["error"] = _video_error_text(exc)


@app.post("/api/tasks/{task_id}/shots/{shot_id}/video")
def start_video(task_id: str, shot_id: int) -> dict:
    with LOCK:
        task = _task(task_id)
        target = next((s for s in task["shots"] if int(s["shot_id"]) == shot_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"分镜不存在：{shot_id}")
        cfg_error = _video_config_error(_load_service_config())
        if cfg_error:
            raise HTTPException(status_code=409, detail=cfg_error)
        for j in VIDEO_JOBS.values():
            if (
                j["task_id"] == task_id
                and j["shot_id"] == shot_id
                and j["status"] == "running"
            ):
                raise HTTPException(status_code=409, detail="该分镜已有视频在生成中")

    # 前置校验按模式走：人物图生视频不要求这一镜出过图，只要求有角色定妆照
    if _video_mode() == "portrait":
        if _portrait_first_frame(task_id, target) is None:
            raise HTTPException(
                status_code=409,
                detail="人物图生视频需要角色定妆照：回到「故事设定」阶段生成，或改用「首帧图生视频」",
            )
    elif _resolve_image(task_id, target) is None:
        raise HTTPException(status_code=409, detail="该分镜还没有首帧图，请先出图")

    job_id = uuid.uuid4().hex[:12]
    VIDEO_JOBS[job_id] = {
        "job_id": job_id,
        "task_id": task_id,
        "shot_id": shot_id,
        "status": "running",
        "error": None,
        "video_path": None,
        "version": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    threading.Thread(target=_run_video_job, args=(VIDEO_JOBS[job_id],), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/video-jobs/{job_id}")
def video_job(job_id: str) -> dict:
    job = VIDEO_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"视频任务不存在：{job_id}")
    return {
        "job_id": job["job_id"],
        "task_id": job["task_id"],
        "shot_id": job.get("shot_id"),
        "block_id": job.get("block_id"),
        "status": job["status"],
        "error": job["error"],
        "video_path": job["video_path"],
        "last_frame": job.get("last_frame"),
        "version": job["version"],
    }


# ---------------------------------------------------------------- 块视频
# 一块 = 一条 10s 视频。起点帧由 _block_start_frame 决定：勾了「承接上一块」
# 就拿上一块的尾帧当第 0 秒（真接力），否则用本块自己的首帧图。
# 生成完成自动抽尾帧（frames/block_XX_last.png），下一块接力就吃这个文件。


def _block_video_out_path(task_id: str, block_id: int) -> str:
    return os.path.join(_out_dir(task_id), "videos", f"block_{block_id:02d}.mp4")


def _run_block_video_job(job: dict[str, Any]) -> None:
    task = TASKS.get(job["task_id"])
    try:
        block = next(
            (
                b for b in (task or {}).get("blocks", [])
                if int(b.get("block_id") or 0) == job["block_id"]
            ),
            None,
        )
        if not block:
            raise RuntimeError("块不存在（可能已被删除）")
        start = _block_start_frame(job["task_id"], task or {}, block)
        if not start:
            if block.get("continue_last"):
                raise RuntimeError(
                    "承接上一块需要上一块的视频先生成（从它的尾帧接着演）；"
                    "也可以取消「承接上一块」，用本块自己的首帧图"
                )
            raise RuntimeError("找不到首帧图，请先生成本块的首帧图")
        # 视频提示词没填时退化为 summary（Qwen3-VL 能理解中文），但不能两者都空
        prompt = (block.get("video_prompt") or "").strip() or str(block.get("summary") or "").strip()
        if not prompt:
            raise RuntimeError("视频提示词为空：先点「AI 写提示词」或手填")
        duration = float(block.get("duration") or 10.0)
        audio = str(block.get("audio") or "")
        out = _block_video_out_path(job["task_id"], job["block_id"])
        provider = _make_video_provider()
        provider.generate(start, prompt, duration, out, audio=audio)

        last_frame = _extract_last_frame(job["task_id"], job["block_id"], out)
        with LOCK:
            job["status"] = "succeeded"
            job["video_path"] = f"videos/block_{job['block_id']:02d}.mp4"
            job["last_frame"] = f"frames/block_{job['block_id']:02d}_last.png" if last_frame else ""
            job["version"] = uuid.uuid4().hex[:6]
            if task is not None:
                block["video_path"] = job["video_path"]
                block["last_frame"] = job["last_frame"]
                block["version"] = job["version"]
                _persist(job["task_id"])
    except Exception as exc:
        with LOCK:
            job["status"] = "failed"
            job["error"] = _video_error_text(exc)


@app.post("/api/tasks/{task_id}/blocks/{block_id}/video")
def start_block_video(task_id: str, block_id: int) -> dict:
    with LOCK:
        task = _task(task_id)
        block = _block_by_id(task, block_id)
        if block is None:
            raise HTTPException(status_code=404, detail=f"块不存在：{block_id}")
        cfg_error = _video_config_error(_load_service_config())
        if cfg_error:
            raise HTTPException(status_code=409, detail=cfg_error)
        for j in VIDEO_JOBS.values():
            if (
                j.get("block_id") == block_id
                and j["task_id"] == task_id
                and j["status"] == "running"
            ):
                raise HTTPException(status_code=409, detail="该块已有视频在生成中")

    # 起点帧预检：承接需要上一块尾帧，独立需要本块首帧图，两者都没有就提前拦下
    task_live = TASKS.get(task_id) or {}
    if not _block_start_frame(task_id, task_live, block):
        raise HTTPException(
            status_code=409,
            detail="没有可用的起点帧：先生成本块首帧图，或确认上一块的视频已经生成",
        )

    job_id = uuid.uuid4().hex[:12]
    VIDEO_JOBS[job_id] = {
        "job_id": job_id,
        "task_id": task_id,
        "block_id": block_id,
        "status": "running",
        "error": None,
        "video_path": None,
        "last_frame": None,
        "version": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    threading.Thread(
        target=_run_block_video_job, args=(VIDEO_JOBS[job_id],), daemon=True
    ).start()
    return {"job_id": job_id}


# ---------------------------------------------------------------- 一键批量生成
# GPU 只能串行出片，批量任务内部逐镜排队；每镜完成就写回 shots.json，
# 前端轮询进度时能同步看到新视频。


def _batch_targets(task_id: str, task: dict) -> list[dict]:
    """挑出需要生成的分镜：有可用的起点图、且还没有视频的。

    「起点图」是什么由模式决定：i2v / flf 要这一镜的分镜图；portrait 只要角色定妆照，
    所以人物图生视频可以一镜都不出图。
    已有视频的自动跳过——一键生成是"补齐"语义，断点续跑不会重复烧已经出过的片。
    """
    mode = _video_mode()
    targets = []
    for s in task.get("shots", []):
        image = (
            _portrait_first_frame(task_id, s)
            if mode == "portrait"
            else _resolve_image(task_id, s)
        )
        if not image:
            continue
        out = _video_out_path(task_id, int(s["shot_id"]))
        if os.path.isfile(out):
            continue
        targets.append(
            {
                "shot_id": int(s["shot_id"]),
                "image": image,
                "out": out,
            }
        )
    targets.sort(key=lambda t: t["shot_id"])
    return targets


def _apply_video_result(task: dict, shot_id: int, rel_path: str) -> None:
    for s in task.get("shots", []):
        if int(s["shot_id"]) == shot_id:
            s["video_path"] = rel_path
            s["version"] = uuid.uuid4().hex[:6]
            return


@app.post("/api/tasks/{task_id}/videos/batch")
def start_batch_videos(task_id: str) -> dict:
    with LOCK:
        task = _task(task_id)
        cfg_error = _video_config_error(_load_service_config())
        if cfg_error:
            raise HTTPException(status_code=409, detail=cfg_error)
        targets = _batch_targets(task_id, task)
        if not targets:
            with_images = sum(
                1 for s in task.get("shots", []) if _resolve_image(task_id, s)
            )
            if with_images:
                raise HTTPException(
                    status_code=409,
                    detail="所有分镜都已经有视频了，无需重复生成（想重做就先删掉对应 mp4，或用命令行 video_agent.py --force）",
                )
            if _video_mode() == "portrait":
                raise HTTPException(
                    status_code=409,
                    detail="人物图生视频需要角色定妆照：回到「故事设定」阶段生成，或改用「首帧图生视频」",
                )
            raise HTTPException(status_code=409, detail="还没有已出图的分镜，先生成分镜图")
    job_id = uuid.uuid4().hex[:12]
    BATCH_JOBS[job_id] = {
        "job_id": job_id,
        "task_id": task_id,
        "status": "running",
        "total": len(targets),
        "done": 0,
        "current": None,
        "targets": targets,
        "errors": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    threading.Thread(target=_run_batch, args=(BATCH_JOBS[job_id],), daemon=True).start()
    return {"job_id": job_id, "total": len(targets)}


def _run_batch(job: dict[str, Any]) -> None:
    task = TASKS.get(job["task_id"])
    try:
        provider = _make_video_provider()
    except Exception as exc:
        with LOCK:
            job["status"] = "failed"
            job["errors"].append({"shot_id": None, "error": str(exc)})
        return

    # 模式在整批开始时取一次：中途在面板上改了，不应该让同一批里的镜头用上不同模式
    batch_mode = _video_mode()

    for t in job["targets"]:
        with LOCK:
            job["current"] = t["shot_id"]
        shot_data = next(
            (s for s in (task or {}).get("shots", []) if int(s["shot_id"]) == t["shot_id"]),
            {},
        )
        prompt = shot_data.get("video_prompt") or shot_data.get("scene_desc", "")
        duration = float(shot_data.get("duration") or 5.0)
        audio = str(shot_data.get("audio") or "")
        # 批量是串行的：轮到本镜时上一镜视频必然已落盘，接力才能取到尾帧
        last_frame = _resolve_last_frame(job["task_id"], shot_data or {}, batch_mode)
        try:
            provider.generate(t["image"], prompt, duration, t["out"], audio=audio, last_frame_path=last_frame)
            with LOCK:
                rel = f"videos/shot_{t['shot_id']:02d}.mp4"
                job["done"] += 1
                if task:
                    _apply_video_result(task, t["shot_id"], rel)
                    _persist(job["task_id"])
        except Exception as exc:
            with LOCK:
                job["done"] += 1
                job["errors"].append({"shot_id": t["shot_id"], "error": _video_error_text(exc)[:300]})

    with LOCK:
        job["status"] = "failed" if not job["done"] else ("succeeded" if not job["errors"] else "partial")
        job["current"] = None


@app.get("/api/video-batch/{job_id}")
def video_batch(job_id: str) -> dict:
    job = BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"批量任务不存在：{job_id}")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "total": job["total"],
        "done": job["done"],
        "current": job["current"],
        "errors": job["errors"],
    }


# ---------------------------------------------------------------- 合并导出
# 把各镜 mp4 按镜号顺序拼成成片。优先 -c copy 秒拼；分镜编码参数不一致时
# 自动退回重编码。ffmpeg 二进制来自 imageio-ffmpeg（无需系统安装）。


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


@app.post("/api/tasks/{task_id}/export")
def export_video(task_id: str) -> dict:
    with LOCK:
        task = _task(task_id)
        clips = []
        if task.get("flow") == "blocks" and task.get("blocks"):
            # 分块流程：按块号顺序拼接
            for b in sorted(task["blocks"], key=lambda x: int(x.get("block_id") or 0)):
                rel = b.get("video_path", "")
                if not rel:
                    continue
                p = rel if os.path.isabs(rel) else os.path.join(_out_dir(task_id), rel)
                if os.path.isfile(p):
                    clips.append(p)
            if not clips:
                raise HTTPException(status_code=409, detail="还没有任何块视频，先逐块生成视频")
        else:
            for s in sorted(task.get("shots", []), key=lambda x: int(x["shot_id"])):
                rel = s.get("video_path", "")
                if not rel:
                    continue
                p = rel if os.path.isabs(rel) else os.path.join(_out_dir(task_id), rel)
                if os.path.isfile(p):
                    clips.append(p)
            if not clips:
                raise HTTPException(status_code=409, detail="还没有任何视频，先点「生成视频」")

    export_dir = os.path.join(_out_dir(task_id), "export")
    os.makedirs(export_dir, exist_ok=True)
    out = os.path.join(export_dir, "final.mp4")
    list_path = os.path.join(export_dir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for c in clips:
            fh.write(f"file '{c.replace(os.sep, '/')}'\n")

    ff = _ffmpeg_exe()
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if not (os.path.isfile(out) and os.path.getsize(out) > 10000):
        # copy 拼接失败通常是编码参数不一致，退回重编码
        cmd = [ff, "-y"]
        for c in clips:
            cmd += ["-i", c]
        cmd += [
            "-filter_complex", f"concat=n={len(clips)}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", out,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    if not os.path.isfile(out):
        tail = (proc.stderr or "")[-400:]
        raise HTTPException(status_code=500, detail=f"ffmpeg 合并失败：{tail}")

    return {
        "file": "export/final.mp4",
        "clips": len(clips),
        "size": os.path.getsize(out),
    }


def _new_draft(title: str = "") -> str:
    """建一个只有标题、还没有故事的空白作品，并把 project.json 落盘。

    为什么单独开这个入口：`/api/generate` 是「一句创意 → 导演 Agent 自动生成设定」，
    那条路**必须先有故事**。而「新建作品」页是素材优先 —— 先攒角色/场景/道具，
    故事可以晚点由 AI 助手聊出来。所以这里造一个空壳作品，
    让 add_character / add_asset / 上传 / 出片这些既有接口有个能挂的对象。
    """
    task_id = uuid.uuid4().hex[:12]
    os.makedirs(_out_dir(task_id), exist_ok=True)
    TASKS[task_id] = {
        "task_id": task_id,
        "idea": "",
        "status": "succeeded",
        "stage": "待开始",
        "stage_state": "draft",
        "flow": "blocks",
        "shot_count": None,
        "concurrency": 2,
        "done": 0,
        "total": 0,
        "project": {
            "title": (title.strip() or "未命名作品")[:80],
            "logline": "",
            "style": "",
            "style_en": "",
            "aspect_ratio": "16:9",
            "characters": [],
            "assets": [],
        },
        "shots": [],
        "blocks": [],
        "stats": {},
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _persist(task_id)
    _save_meta(task_id)
    return task_id


@app.post("/api/tasks/draft")
def create_draft(body: DraftCreate) -> dict:
    """新建空白作品。返回 task_id，前端拿到后所有素材接口都能用了。"""
    task_id = _new_draft(body.title)
    with LOCK:
        task = _task(task_id)
        return {"task_id": task_id, "project": dict(task["project"])}


def _unique_name(base: str, taken: set[str]) -> str:
    """素材重名自动加序号：上传时名字取自文件名，撞车很常见。"""
    base = base[:40] or "素材"
    if base not in taken:
        return base
    for i in range(2, 100):
        cand = f"{base} {i}"[:40]
        if cand not in taken:
            return cand
    return base


@app.post("/api/tasks/{task_id}/materials/upload")
def upload_material(task_id: str, kind: str, body: UploadBody) -> dict:
    """组级上传：选个文件就多一项素材，不用先想名字。

    「新建作品」页六个空卡片上的「上传图片 / 上传音频」走这里。
    名字取文件名（重名自动加序号），落盘命名与 upload_character_image /
    upload_asset_file 完全一致，所以 _load_task_from_disk 重启后认的回来。

    锚点一律留空 —— 上传就是「这张图就是这个角色」，不走模型、不花额度，
    这也是「没有任何图像/文本 API 也能用」那条路的入口。
    """
    if kind not in MATERIAL_KINDS:
        raise HTTPException(status_code=422, detail=f"不支持的素材类型：{kind}")
    raw = _decode_upload(body)

    stem = os.path.splitext(os.path.basename(str(body.filename or "")))[0]
    base = re.sub(r"[\\/:*?\"<>|\s]+", " ", stem).strip() or MATERIAL_CN.get(kind, "素材")

    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        project = task.get("project")
        if not project:
            raise HTTPException(status_code=409, detail="作品数据不完整，无法添加素材")

        if kind == "character":
            chars = project.setdefault("characters", [])
            name = _unique_name(base, {str(c.get("name") or "") for c in chars})
            cdir = os.path.join(_out_dir(task_id), "characters")
            os.makedirs(cdir, exist_ok=True)
            path = os.path.join(cdir, f"{pipeline._safe_char_name(name)}.png")
            with open(path, "wb") as fh:
                fh.write(raw)
            entry = {
                "name": name, "anchor": "", "anchor_en": "", "voice": "",
                "tts_voice": "", "voice_sample": "",
                "image_path": path, "images": [path],
            }
            chars.append(entry)
            index = len(chars) - 1
            # 顺手分配一个不撞车的建议音色（只写 id，不合成样本）
            assigned = tts.assign_voices(chars)
            for ch in chars:
                if not str(ch.get("tts_voice") or "").strip():
                    ch["tts_voice"] = assigned.get(str(ch.get("name") or ""), "")
        else:
            assets = project.setdefault("assets", [])
            name = _unique_name(base, {
                str(a.get("name") or "") for a in assets if str(a.get("kind")) == kind
            })
            adir = os.path.join(_out_dir(task_id), "assets")
            os.makedirs(adir, exist_ok=True)
            path = os.path.join(adir, _asset_file_name(kind, name, body.filename))
            with open(path, "wb") as fh:
                fh.write(raw)
            entry = {"kind": kind, "name": name, "anchor": "", "anchor_en": "", "images": [path]}
            assets.append(entry)
            index = len(assets) - 1

        _persist(task_id)
        return {"kind": kind, "index": index, "name": name, "entry": dict(entry)}


@app.post("/api/tasks/{task_id}/characters/{index}/voice/upload")
def upload_character_voice(task_id: str, index: int, body: UploadBody) -> dict:
    """上传或录制的音色样本，直接当这个角色的参考音频。

    与 reroll_character_voice（调 TTS 合成）互补：这条不碰任何模型接口，
    是「自己有录音 / 没有 TTS Key」时的入口。
    后缀跟实际内容走（录出来是 webm），_load_task_from_disk 按前缀扫描认它。
    """
    raw = _decode_upload(body)
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        name = str(chars[index].get("name") or f"character{index}")

    ext = os.path.splitext(str(body.filename or ""))[1].lower()
    if ext not in VOICE_EXTS:
        ext = ".mp3"
    cdir = os.path.join(_out_dir(task_id), "characters")
    os.makedirs(cdir, exist_ok=True)
    safe = pipeline._safe_char_name(name)
    # 换样本要清掉旧后缀，否则目录里会留两份、重启后认到的是排序靠前的那份
    for fn in os.listdir(cdir):
        if fn.startswith(f"voice_{safe}."):
            try:
                os.remove(os.path.join(cdir, fn))
            except OSError:
                pass
    fname = f"voice_{safe}{ext}"
    with open(os.path.join(cdir, fname), "wb") as fh:
        fh.write(raw)

    with LOCK:
        entry = task["project"]["characters"][index]
        entry["voice_sample"] = fname
        _persist(task_id)
        return dict(entry)


ASSISTANT_SYSTEM = """你是「镜序 FRAMEFLOW」的 AI 创作助手，帮用户把一部短片要用的素材（角色 / 场景 / 道具）攒齐。

你的产出会**直接写进作品**，所以每次回复都要给出具体、能直接用的内容。

只输出这个 JSON 结构，不要多余字段：
{
  "reply": "给用户看的中文回复。日常 2-4 句；用户要分镜或提示词时，可以分行写（每行一个镜头 / 一句提示词）",
  "title": "作品名（只有用户给了明确题材/片名时才填，否则空串）",
  "logline": "一句话故事（只有用户描述了故事时才填，否则空串）",
  "style": "影像风格（只有用户明确说了风格才填，否则空串）",
  "characters": [{"name": "角色名 2-4 字", "anchor": "固定可复述的外貌特征", "voice": "声音设计"}],
  "assets": [{"kind": "scene 或 prop", "name": "素材名", "anchor": "视觉锚点"}],
  "suggestions": ["下一步建议1", "下一步建议2", "下一步建议3"]
}

锚点纪律（写不对整条链路都会跑偏）：
- 角色锚点：固定的、可复述的视觉事实 —— 年龄、发型发色、服装、显著特征。
  禁止"帅气""忧郁"这类落不到画面的词；疤痕/痣等小特征要带程度词（浅淡/细微）。
- 场景锚点：空间结构 + 光源方向 + 材质质感，**禁止出现任何人物**。
- 道具锚点：材质 / 颜色 / 形状 / 磨损细节，禁止情绪词。
- voice：年龄感 + 音色 + 语速语气，例如"清亮的少女音，语速偏快"。

行为准则：
- 用户只是闲聊或提问时，characters / assets 留空数组，别硬塞素材。
- 用户说"生成角色 / 生成场景"时，一次给 1-3 个，名字要具体（"林晚"），
  不要用"主角""配角""某个人"这种占位名。
- name 必须是**完整的人名 / 素材名**，2 个字以上。绝对不能是「的」「了」这类助词，
  也不能是从句子里截下来的碎片 —— 宁可少给一个，也不许给半个名字。
- 已经存在的角色/素材不要重复给（见「当前作品已有」）。
- reply 里要说清东西已经加到素材区了，并给一个自然的下一步。"""


# 助手偶尔会把助词或句子碎片当成角色名返回（2026-09-15 实测：某个作品里落了一个
# 叫「的」的角色，锚点也是「的」，在素材区就是一张完全没法用的卡片）。
# 系统提示里已经写了「2 个字以上」，但模型不保证遵守 —— 落库前再兜一道，
# 不满足的**不写进作品**，并把原始值回给前端，别让它悄悄留在那儿。
_NAME_JUNK = set("的了是和在与或而就都也还很被把给对从向为以及等这那一之其")


def _looks_like_junk_name(name: str, min_len: int = 2) -> bool:
    """名字短于 min_len，或者整串都是助词/虚词 —— 都判为模型吐出来的垃圾。"""
    if len(name) < min_len:
        return True
    return all(ch in _NAME_JUNK for ch in name)


def _assistant_context(task: dict) -> str:
    """把当前作品状态压成一段上下文 —— 助手每轮都看最新的，不会拿旧上下文打架。"""
    project = task.get("project") or {}
    chars = project.get("characters") or []
    assets = project.get("assets") or []
    lines = [
        f"作品名：{project.get('title') or '（未命名）'}",
        f"故事：{project.get('logline') or '（还没写）'}",
        f"风格：{project.get('style') or '（还没定）'}",
        "当前作品已有的角色：",
    ]
    lines += [f"  - {c.get('name')}：{c.get('anchor') or '（还没定形象）'}" for c in chars] or ["  （暂无）"]
    lines.append("当前作品已有的素材：")
    lines += [
        f"  - [{a.get('kind')}] {a.get('name')}：{a.get('anchor') or '（还没定描述）'}"
        for a in assets
    ] or ["  （暂无）"]
    return "\n".join(lines)


@app.post("/api/assistant")
def assistant(body: AssistantBody) -> dict:
    """AI 创作助手：一轮对话 → 直接把角色 / 场景 / 道具写进作品。

    没有作品时先建一个空白作品（助手常常是「新建作品」页的第一个动作用户界面）。
    """
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")

    task_id = body.task_id.strip()
    if not task_id or not TASK_ID_RE.match(task_id) or not os.path.isdir(_out_dir(task_id)):
        task_id = _new_draft()
    with LOCK:
        task = _task(task_id)

    history_lines = []
    for item in (body.history or [])[-8:]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        who = "助手" if str(item.get("role")) == "assistant" else "用户"
        history_lines.append(f"[{who}] {text[:600]}")

    user = _assistant_context(task)
    if history_lines:
        user += "\n\n最近的对话：\n" + "\n".join(history_lines)
    user += f"\n\n用户这句话：{message}"

    try:
        data = llm.chat_json(ASSISTANT_SYSTEM, user, temperature=0.7)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"助手调用失败：{type(exc).__name__}: {exc}"
        ) from exc

    created_chars: list[str] = []
    created_assets: list[str] = []
    skipped: list[str] = []
    rejected: list[str] = []

    with LOCK:
        project = task.get("project")
        if not project:
            raise HTTPException(status_code=409, detail="作品数据不完整")

        # 只在用户还没定的时候才写标题/故事/风格 —— 助手不该覆盖用户已有的设定
        title = str(data.get("title") or "").strip()
        if title and str(project.get("title") or "") in ("", "未命名作品"):
            project["title"] = title[:80]
        logline = str(data.get("logline") or "").strip()
        if logline and not str(project.get("logline") or "").strip():
            project["logline"] = logline[:2000]
        style = str(data.get("style") or "").strip()
        if style and not str(project.get("style") or "").strip():
            project["style"] = style[:500]

        chars = project.setdefault("characters", [])
        for raw in (data.get("characters") or [])[:6]:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()[:40]
            if not name:
                continue
            if _looks_like_junk_name(name):
                rejected.append(name)
                continue
            if any(c.get("name") == name for c in chars):
                skipped.append(name)
                continue
            chars.append({
                "name": name,
                "anchor": str(raw.get("anchor") or "").strip()[:2000],
                "anchor_en": "",
                "voice": str(raw.get("voice") or "").strip()[:500],
                "tts_voice": "",
                "voice_sample": "",
                "image_path": "",
                "images": [],
            })
            created_chars.append(name)

        assets = project.setdefault("assets", [])
        for raw in (data.get("assets") or [])[:8]:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()[:40]
            if not name:
                continue
            # 素材名允许一个字（「刀」「伞」都算合理），所以只拦「整串都是助词」
            if _looks_like_junk_name(name, min_len=1):
                rejected.append(name)
                continue
            kind = str(raw.get("kind") or "prop").strip()
            if kind not in ("scene", "prop"):
                kind = "prop"
            if any(a.get("name") == name and a.get("kind") == kind for a in assets):
                skipped.append(name)
                continue
            assets.append({
                "kind": kind,
                "name": name,
                "anchor": str(raw.get("anchor") or "").strip()[:2000],
                "anchor_en": "",
                "images": [],
            })
            created_assets.append(name)

        assigned = tts.assign_voices(chars)
        for ch in chars:
            if not str(ch.get("tts_voice") or "").strip():
                ch["tts_voice"] = assigned.get(str(ch.get("name") or ""), "")

        _persist(task_id)
        _save_meta(task_id)

    # 模型声称"已经加好了"，但它给的名字被拦下来时，必须在回复里说清楚 ——
    # 否则用户看着素材区是空的，以为是自己看漏了。
    reply = str(data.get("reply") or "").strip() or "我把这条记下了。"
    if rejected:
        shown = "、".join(f"「{n}」" for n in rejected[:3])
        reply += f"\n（这次给出的名字不完整（{shown}），我没有写进作品，麻烦让我重新生成一次。）"

    return {
        "task_id": task_id,
        "reply": reply,
        "created": {"characters": created_chars, "assets": created_assets},
        "skipped": skipped,
        "rejected": rejected,
        "suggestions": [
            str(s).strip()[:40] for s in (data.get("suggestions") or []) if str(s).strip()
        ][:4],
    }


@app.get("/api/config")
def get_config() -> dict:
    """前端「服务配置」弹窗读当前值（含已保存的密钥，本机单用户工具）。"""
    return _load_service_config()


@app.post("/api/config")
def set_config(body: ServiceConfigPatch) -> dict:
    """保存全部服务配置并实时生效；顺手探活 ComfyUI 给面板一个红绿状态。"""
    cfg = body.model_dump()
    with open(CONF_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    _apply_service_config(_load_service_config())
    reachable = None
    if cfg.get("video_backend", "comfyui") != "api" and cfg.get("comfyui_url"):
        try:
            reachable = requests.get(f"{cfg['comfyui_url'].rstrip('/')}/system_stats", timeout=8).ok
        except requests.RequestException:
            reachable = False
    return {"ok": True, "comfyui_reachable": reachable}


# 启动时把磁盘上的历史任务认回来，这样任务列表不再依赖浏览器
_scan_tasks()

app.mount("/files", StaticFiles(directory=OUT_ROOT), name="files")

# 前端生产构建（web/dist）：存在就由同一进程伺服，单服务部署（npm run build 后
# 直接访问 http://127.0.0.1:8000）。开发模式走 vite dev（5173），这里不生效。
WEB_DIST = os.path.join(ROOT, "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

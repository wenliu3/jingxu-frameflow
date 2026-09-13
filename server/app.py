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

import json
import os
import re
import shutil
import subprocess
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
    "video_api_url": "",
    "video_api_key": "",
    "video_api_model": "",
}
_ENV_OF = {
    "video_backend": "VIDEO_BACKEND",
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
    "video_api_url": "VIDEO_API_URL",
    "video_api_key": "VIDEO_API_KEY",
    "video_api_model": "VIDEO_API_MODEL",
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
    return cfg


def _apply_service_config(cfg: dict) -> None:
    for key, env in _ENV_OF.items():
        value = str(cfg.get(key) or "").strip()
        if value:
            os.environ[env] = value


_apply_service_config(_load_service_config())

import pipeline  # noqa: E402
from image_provider import DEFAULT_NEGATIVE, create_provider  # noqa: E402
from schemas import Project, Shot  # noqa: E402
from video_provider import ComfyUIVideoProvider  # noqa: E402

OUT_ROOT = os.path.join(ROOT, "outputs")
os.makedirs(OUT_ROOT, exist_ok=True)

# ComfyUI 地址等参数统一从 service_config.json 读（前端「服务配置」弹窗维护），
# 每次调用现读现用——改配置立即生效，无需重启。


def _load_comfyui_url() -> str:
    return _load_service_config()["comfyui_url"]


def _make_video_provider():
    """按「服务配置」里的视频后端选择实例化 provider。"""
    cfg = _load_service_config()
    if cfg.get("video_backend") == "api":
        from video_provider import ApiVideoProvider

        return ApiVideoProvider(
            cfg.get("video_api_url"), cfg.get("video_api_key"), cfg.get("video_api_model")
        )
    return ComfyUIVideoProvider(base_url=cfg["comfyui_url"])


def _video_config_error(cfg: dict) -> str | None:
    """生成视频前的配置自检：返回给用户的报错文案，None 表示配置就绪。"""
    backend = cfg.get("video_backend", "comfyui")
    if backend == "api":
        if not (cfg.get("video_api_url") and cfg.get("video_api_key") and cfg.get("video_api_model")):
            return "外接视频 API 未配置完整：需要 API 地址、Key 和模型名称（服务配置面板）"
        return None
    if not cfg.get("comfyui_url"):
        return "未配置 ComfyUI 地址：点右上角「服务配置」，把实例隧道地址填进去"
    return None


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
    visual_prompt: str | None = None
    negative_prompt: str | None = None
    video_prompt: str | None = None
    camera: str | None = None
    motion: str | None = None
    duration: float | None = None
    dialogue: str | None = None


class ServiceConfigPatch(BaseModel):
    video_backend: str = "comfyui"
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
    video_api_url: str = ""
    video_api_key: str = ""
    video_api_model: str = ""


class TaskPatch(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


class CharacterPatch(BaseModel):
    """角色编辑：名字与锚点提示词。锚点是分镜提示词和定妆照共用的角色一致性描述。"""
    name: str | None = Field(None, min_length=1, max_length=40)
    anchor: str | None = Field(None, max_length=2000)


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

    # 阶段进度推断：有图=全流程走完；有 shots.json=停在提示词；只有设定=停在角色
    any_image = any(s.get("image_path") for s in shots)
    stage_state = "directed"
    if any_image:
        stage_state = "imaged"
    elif shots:
        stage_state = "storyboarded"

    meta = _read_json(os.path.join(out_dir, "task.json")) or {}
    created = meta.get("created_at") or datetime.fromtimestamp(
        os.path.getmtime(out_dir), tz=timezone.utc
    ).isoformat()

    return {
        "task_id": task_id,
        "idea": meta.get("idea", ""),
        "status": meta.get("status") or "succeeded",
        "stage": "完成",
        "stage_state": stage_state,
        "done": sum(1 for s in shots if s.get("image_path")),
        "total": len(shots),
        "project": project,
        "shots": shots,
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
    """从磁盘把任务的分镜数据读回来，供单镜编辑/重生成使用。"""
    project = None
    shot_list: list[Shot] = []
    task = TASKS.get(task_id) or {}
    if task.get("project"):
        project = Project.from_dict(task["project"])
    for raw in task.get("shots", []):
        shot_list.append(
            Shot(
                shot_id=int(raw["shot_id"]),
                scene_desc=raw.get("scene_desc", ""),
                visual_prompt=raw.get("visual_prompt", ""),
                negative_prompt=raw.get("negative_prompt", ""),
                video_prompt=raw.get("video_prompt", ""),
                camera=raw.get("camera", ""),
                motion=raw.get("motion", ""),
                duration=float(raw.get("duration") or 3.0),
                dialogue=raw.get("dialogue", ""),
                character_refs=list(raw.get("character_refs") or []),
                image_path=raw.get("image_path", ""),
                video_path=raw.get("video_path", ""),
            )
        )
    return project, shot_list


def _persist(task_id: str) -> None:
    """把内存里的状态同步写回 preview.html 与 shots.json。"""
    task = _task(task_id)
    project = Project.from_dict(task["project"]) if task.get("project") else None
    shots = [
        Shot(
            shot_id=int(r["shot_id"]),
            scene_desc=r.get("scene_desc", ""),
            visual_prompt=r.get("visual_prompt", ""),
            negative_prompt=r.get("negative_prompt", ""),
            video_prompt=r.get("video_prompt", ""),
            camera=r.get("camera", ""),
            motion=r.get("motion", ""),
            duration=float(r.get("duration") or 3.0),
            dialogue=r.get("dialogue", ""),
            character_refs=list(r.get("character_refs") or []),
            image_path=r.get("image_path", ""),
            video_path=r.get("video_path", ""),
        )
        for r in task.get("shots", [])
    ]
    if project:
        pipeline._save_outputs(_out_dir(task_id), project, shots)


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
                for c in (task.get("project") or {}).get("characters", []):
                    if c.get("name") == info["name"]:
                        c["image_path"] = info["path"]
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
            task["status"] = "succeeded"
            task["stage"] = "角色就绪"
            task["stage_state"] = "directed"
            task["done"] = len(chars)
            task["total"] = len(chars)
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
            _out_dir(task_id), project, shots, concurrency, on_progress
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
        "shot_count": req.shots,
        "concurrency": req.concurrency,
        "done": 0,
        "total": req.shots,
        "project": None,
        "shots": [],
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
            "done": task["done"],
            "total": task["total"],
            "project": task["project"],
            "shots": task["shots"],
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
def delete_task(task_id: str) -> dict:
    """删除历史任务：目录移入 outputs/_trash/ 回收站而不是直接删，
    手滑了还能从那里把图片和视频整包拿回来。"""
    with LOCK:
        _task(task_id)
        TASKS.pop(task_id, None)
    src = _out_dir(task_id)
    if os.path.isdir(src):
        trash = os.path.join(OUT_ROOT, "_trash")
        os.makedirs(trash, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        try:
            shutil.move(src, os.path.join(trash, f"{stamp}_{task_id}"))
        except OSError:
            shutil.rmtree(src, ignore_errors=True)
    return {"deleted": task_id}


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
        if changed:
            _persist(task_id)
        return dict(char)


@app.post("/api/tasks/{task_id}/characters/{index}/reroll")
def reroll_character(task_id: str, index: int) -> dict:
    """重新生成某个角色的定妆照（同步执行，一张图十几秒）。"""
    with LOCK:
        task = _task(task_id)
        if task["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在执行中")
        chars = (task.get("project") or {}).get("characters") or []
        if index < 0 or index >= len(chars):
            raise HTTPException(status_code=404, detail=f"角色不存在：{index}")
        char = chars[index]
        style = (task.get("project") or {}).get("style", "")
        anchor = char.get("anchor", "")
        name = char.get("name", f"角色{index}")

    provider = create_provider()
    cdir = os.path.join(_out_dir(task_id), "characters")
    os.makedirs(cdir, exist_ok=True)
    old = char.get("image_path")
    path = os.path.join(
        cdir, f"{pipeline._safe_char_name(name)}_{uuid.uuid4().hex[:6]}.png"
    )
    try:
        provider.generate(
            pipeline._character_portrait_prompt(style, anchor),
            path,
            negative_prompt=DEFAULT_NEGATIVE,
            size="1024x1024",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    with LOCK:
        task["project"]["characters"][index]["image_path"] = path
        task["project"]["characters"][index]["version"] = uuid.uuid4().hex[:6]
        task["project"]["characters"][index].pop("stale", None)  # 新照对应新锚点，解除待更新标记
        _persist(task_id)
        updated = dict(task["project"]["characters"][index])
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

    shot = Shot(
        shot_id=int(target["shot_id"]),
        scene_desc=target.get("scene_desc", ""),
        visual_prompt=target.get("visual_prompt", ""),
        negative_prompt=target.get("negative_prompt", ""),
        video_prompt=target.get("video_prompt", ""),
        camera=target.get("camera", ""),
        motion=target.get("motion", ""),
        duration=float(target.get("duration") or 3.0),
        dialogue=target.get("dialogue", ""),
        character_refs=list(target.get("character_refs") or []),
        video_path=target.get("video_path", ""),
    )

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


def _run_video_job(job: dict[str, Any]) -> None:
    task = TASKS.get(job["task_id"])
    try:
        target = None
        if task:
            for s in task["shots"]:
                if int(s["shot_id"]) == job["shot_id"]:
                    target = s
                    break
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
        duration = float((target or {}).get("duration") or 3.0)

        provider = _make_video_provider()
        provider.generate(image, prompt, duration, _video_out_path(job["task_id"], job["shot_id"]))

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
            job["error"] = f"{type(exc).__name__}: {exc}"


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

    if _resolve_image(task_id, target) is None:
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
        "shot_id": job["shot_id"],
        "status": job["status"],
        "error": job["error"],
        "video_path": job["video_path"],
        "version": job["version"],
    }


# ---------------------------------------------------------------- 一键批量生成
# GPU 只能串行出片，批量任务内部逐镜排队；每镜完成就写回 shots.json，
# 前端轮询进度时能同步看到新视频。


def _batch_targets(task_id: str, task: dict) -> list[dict]:
    """挑出需要生成的分镜：有首帧图、且还没有视频的。
    已有视频的自动跳过——一键生成是"补齐"语义，断点续跑不会重复烧已经出过的片。"""
    targets = []
    for s in task.get("shots", []):
        image = _resolve_image(task_id, s)
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

    for t in job["targets"]:
        with LOCK:
            job["current"] = t["shot_id"]
        shot_data = next(
            (s for s in (task or {}).get("shots", []) if int(s["shot_id"]) == t["shot_id"]),
            {},
        )
        prompt = shot_data.get("video_prompt") or shot_data.get("scene_desc", "")
        duration = float(shot_data.get("duration") or 3.0)
        try:
            provider.generate(t["image"], prompt, duration, t["out"])
            with LOCK:
                rel = f"videos/shot_{t['shot_id']:02d}.mp4"
                job["done"] += 1
                if task:
                    _apply_video_result(task, t["shot_id"], rel)
                    _persist(job["task_id"])
        except Exception as exc:
            with LOCK:
                job["done"] += 1
                job["errors"].append({"shot_id": t["shot_id"], "error": str(exc)[:300]})

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

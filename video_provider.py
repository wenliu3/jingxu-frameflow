"""图生视频 Provider。

把分镜的首帧图 + video_prompt 送到 ComfyUI（MiniMax H3）生成带音轨的 mp4。
接口约定与 image_provider 一致：换后端只改这一个文件。

ComfyUI 的四步协议（全部由本类自动完成，调用方无需关心）：
1. POST /upload/image   传首帧图 → 得到服务端文件名
2. POST /prompt         提交 API 格式工作流（节点图包在 {"prompt": ...} 里）→ 得到 prompt_id
3. GET  /history/{id}   轮询直到 status.completed
4. GET  /view           按 filename/subfolder/type 下载 mp4

注意事项：
- H3 是统一模型，工作流里没有负向提示词输入，shot.negative_prompt 在视频阶段用不上。
- 帧数有硬约束：length % 17 == 5（官方模板用数学节点保证，这里在 _build_workflow 里算）。
- 画质/速度旋钮通过环境变量调节（H3_MEGAPIXELS / H3_STEPS / H3_LORA），见 .env.example。
- 提示词按 H3 **官方三段式**拼装（见 compose_h3_prompt）：正文英文由文本 Agent 产出，
  字段名、对齐指令、配乐字段由代码补齐。官方提示词指南只有英文版（base-en.txt /
  ref-en.txt），正文不要写中文——中英混写会稀释控制信息。
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from base64 import b64encode

import requests


class ApiVideoProvider:
    """外接视频 API（硅基流动风格的任务制协议）。

    协议：
      submit: POST {base}/video/submit   {"model":..., "prompt":..., "image_url": <data uri>}
      status: GET  {base}/video/status?requestId=...
      status ∈ InQueue / InProgress / Done / Failed；Done 后 results[].url 是 mp4 直链。

    首帧图以 base64 data uri 随请求发送。不同厂商的视频 API 协议差异很大，
    要接新厂商时改这一个文件即可，配置与前端不用动。
    """

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        poll_interval: float = 10.0,
        timeout: float = 1800.0,
    ) -> None:
        self.base = (base_url or "").strip().rstrip("/")
        self.key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.poll_interval = poll_interval
        self.timeout = timeout
        if not self.base or not self.key or not self.model:
            raise RuntimeError("外接视频 API 未配置完整：需要 API 地址、API Key 和模型名称")

    def generate(
        self,
        image_path: str,
        video_prompt: str,
        duration: float,
        out_path: str,
        audio: str = "",
        last_frame_path: str = "",
        megapixels: float | None = None,
        ref_image_paths: list[str] | None = None,
    ) -> str:
        """签名必须与 ComfyUIVideoProvider 对齐——server 侧（_run_video_job / _run_batch）
        统一按 generate(..., audio=..., last_frame_path=...) 调用，少一个参数就是 TypeError，
        而且配置自检会放行，表现为「面板显示就绪，一点生成就失败」。

        last_frame_path 在这里被忽略：这套任务制协议只接受单张首帧图，没有首尾帧概念。
        duration 同样不被这套协议消费（没有时长字段）。
        megapixels 同样被忽略：分辨率由这套协议自己的档位决定，没有像素预算入参。
        ref_image_paths（2026-09-19 加）同样被忽略：这套协议没有多参考图入参。
        **加这个参数是为了签名对齐**，别因为"它没用"就删掉 —— 删了上面那条 TypeError 就回来了。
        audio 用自然语言拼进去，不套 H3 的三段式字段名——面板里填的模型可能是
        Hailuo-02 之类非 H3 模型，字段名是 H3 专有的，硬套反而干扰。
        """
        with open(image_path, "rb") as fh:
            data_uri = "data:image/png;base64," + b64encode(fh.read()).decode()
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

        prompt = video_prompt.strip()
        if audio.strip():
            prompt = f"{prompt}\n\nAmbient sound: {audio.strip()}"

        submit = requests.post(
            f"{self.base}/video/submit",
            headers=headers,
            json={"model": self.model, "prompt": prompt, "image_url": data_uri},
            timeout=120,
        )
        submit.raise_for_status()
        body = submit.json()
        request_id = str(body.get("requestId") or body.get("id") or "")
        if not request_id:
            raise RuntimeError(f"提交失败：{json.dumps(body, ensure_ascii=False)[:300]}")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            status_resp = requests.get(
                f"{self.base}/video/status",
                headers=headers,
                params={"requestId": request_id},
                timeout=60,
            )
            status_resp.raise_for_status()
            data = status_resp.json()
            status = str(data.get("status", ""))
            if status == "Failed":
                raise RuntimeError(f"API 返回 Failed：{json.dumps(data, ensure_ascii=False)[:300]}")
            if status == "Done":
                results = data.get("results") or []
                url = (results[0] or {}).get("url") if results else ""
                if not url:
                    raise RuntimeError(f"Done 但返回里没有视频 url：{json.dumps(data, ensure_ascii=False)[:300]}")
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                with requests.get(url, stream=True, timeout=300) as dl:
                    dl.raise_for_status()
                    with open(out_path, "wb") as fh:
                        for chunk in dl.iter_content(chunk_size=1 << 16):
                            if chunk:
                                fh.write(chunk)
                return out_path
        raise TimeoutError(f"外接 API 视频生成超时（{self.timeout:.0f}s），requestId={request_id}")


# H3 的主节点类名。Ref2VA 那份收**多张**参考图（ref_images.ref_image_N），
# I2V 那份只收一张首帧（first_frame）。两者的输出槽位一致
# （槽0 = conditioning 给 BasicGuider，槽1 = LATENT 给采样器），所以后半段图不用改。
_H3_NODE_CLASSES = ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo")


def _base_body(text: str) -> str:
    """取出"该进 `integrated_multimodal_description` 的正文"。

    ⚠️ 2026-09-19：六段式的编排结果（Ref2VA 用的那套）如果被送去基础模式
    （I2V / FL2VA），**只能取 `detailed_description` 那一段** —— 基础模式没有
    subject_definitions / summary / retention_analysis 这些章节，整段塞进去等于
    把六个章节压成一个字段（畸形提示词：模型看到一堆"字段名: 内容"的嵌套）。
    正文外的参考关系本来也不该出现在这里。
    """
    t = (text or "").strip()
    if "detailed_description:" not in t:
        return t
    tail = t.split("detailed_description:", 1)[1]
    for stop in ("overall_soundscape:", "non_diegetic_music:"):
        tail = tail.split(stop, 1)[0]
    return tail.strip()


def compose_h3_prompt(
    video_prompt: str,
    audio: str = "",
    frames: int = 0,
    has_last_frame: bool = False,
) -> str:
    """把分镜字段拼成 H3 官方的三段式提示词。

    官方结构（顺序不能乱，模型对开头内容的权重最高）：
        integrated_multimodal_description → overall_soundscape → non_diegetic_music
    图生视频模式还要在最前面加一行「对齐指令」，声明参考图锚定在哪一秒。

    两个刻意的设计：
    - **对齐指令由代码生成，不让文本模型写**：它依赖生成参数（吸附后的帧数），
      模型看不到这些参数，写出来的时间戳必然对不上。
    - **non_diegetic_music 固定写 N/A**：H3 不写就会自己编配乐。想开配乐改这里。
    """
    blocks: list[str] = []
    if has_last_frame:
        # FL2VA：两张图分别锚定开头与结尾，要写出真实时长（帧数 / 24fps）
        seconds = frames / 24.0
        blocks.append(
            "How the reference pictures align with the target video - "
            "Picture 1 (from [Shot 1]) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from [Shot N]) aligns with the {seconds:.2f}-second mark of the target video."
        )
    else:
        # I2VA：只有首帧，锚定 0.00s
        blocks.append(
            "For the target video, at 0.00 seconds into the target video, "
            "[Picture 1] (from [Shot 1]) is fully referenced."
        )
    blocks.append(f"integrated_multimodal_description: {video_prompt.strip()}")
    if audio.strip():
        blocks.append(f"overall_soundscape: {audio.strip()}")
    blocks.append("non_diegetic_music: N/A")
    return "\n\n".join(blocks)


class ComfyUIVideoProvider:
    """通过 HTTP API 驱动 ComfyUI 生成视频。"""

    def __init__(
        self,
        base_url: str | None = None,
        workflow_path: str | None = None,
        poll_interval: float = 10.0,
        timeout_per_shot: float = 3600.0,
    ) -> None:
        # ⚠️ 超时别按"看起来够用"给。2026-09-19 实测：H3 跑一条 **10 秒**的片，
        #    从 execution_start 到 execution_success 是 **1868 秒（31 分钟）**，
        #    原来的 1800 秒只差 68 秒就等到成品了 —— 结果我们提前放弃，
        #    ComfyUI 那边片子好端端躺在 history 里，用户这边"生成记录"空空如也。
        #    现在默认 3600 秒（服务配置 video_timeout_s / H3_VIDEO_TIMEOUT 可改）。
        self.base = (base_url or os.getenv("COMFYUI_URL", "")).strip().rstrip("/")
        if not self.base:
            # 与 server 共享的运行时配置：前端「视频服务」面板填的地址存在这里
            conf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comfyui_config.json")
            try:
                with open(conf, encoding="utf-8") as fh:
                    self.base = str(json.load(fh).get("url", "")).strip()
            except (OSError, json.JSONDecodeError):
                pass
        if not self.base:
            raise RuntimeError(
                "缺少 ComfyUI 地址。在前端「视频服务」面板填隧道地址，或设 COMFYUI_URL 环境变量。"
            )
        wf_path = workflow_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "comfyui", "h3_i2v_api.json"
        )
        with open(wf_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        # 剔除 _说明 / _调参指南 这类注释键，只留纯节点图
        self.template = {k: v for k, v in raw.items() if not k.startswith("_")}
        self.client_id = "avm_" + uuid.uuid4().hex[:8]
        self.poll_interval = poll_interval
        self.timeout = timeout_per_shot

    def generate(
        self,
        image_path: str,
        video_prompt: str,
        duration: float,
        out_path: str,
        audio: str = "",
        last_frame_path: str = "",
        megapixels: float | None = None,
        ref_image_paths: list[str] | None = None,
    ) -> str:
        """生成一条视频并落盘，返回 out_path。阻塞直到完成或超时。

        audio 是这一镜的环境音，对应 H3 官方的 overall_soundscape 字段——由
        compose_h3_prompt 按官方三段式拼进提示词，是**带字段名的独立区块**，
        不是拼在正文末尾（拼进正文会被模型当成画面描述的一部分）。
        last_frame_path 是上一镜视频的尾帧图：给了就切到 FL2VA 模式（首尾帧对齐），
        让本镜首帧承接上一镜结尾（首尾帧接力），实现镜头间的真实动作衔接。
        megapixels 是这一条的像素预算（前端「清晰度」档位）。None = 用环境变量里的默认值。
        ref_image_paths（2026-09-19 加）是**多张参考图**，只有走 Ref2VA 工作流时才有意义：
        I2V 那份只有一个 `first_frame` 口，传了也只能用上第一张。见 _build_workflow。
        """
        image_name = self._upload(image_path)
        last_frame_name = self._upload(last_frame_path) if last_frame_path else ""
        # 第 1 张参考图就是 image_path 本身，别再传一遍；这里只补第 2 张起
        extra_refs = [self._upload(p) for p in (ref_image_paths or [])[1:] if p]
        workflow = self._build_workflow(
            video_prompt, duration, image_name, audio, last_frame_name, megapixels, extra_refs
        )
        if self._is_ref2va(workflow):
            self._preflight_models(workflow)
        prompt_id = self._submit(workflow)
        filename, subfolder = self._wait(prompt_id)
        self._download(filename, subfolder, out_path)
        return out_path

    # ---- 四步协议 ----

    # 工作流里"点名要用某个模型文件"的节点 —— 缺文件时 ComfyUI 的报错是
    # `value not in list: unet_name: 'xxx' not in [...]`，看着像工作流坏了。
    _MODEL_INPUTS = (
        ("UNETLoader", "unet_name"),
        ("CLIPLoader", "clip_name"),
        ("LoraLoaderModelOnly", "lora_name"),
    )

    @staticmethod
    def _is_ref2va(workflow: dict) -> bool:
        return any(
            isinstance(n, dict) and str(n.get("class_type")) == "MiniMaxH3ReferenceToVideo"
            for n in workflow.values()
        )

    def _preflight_models(self, workflow: dict) -> None:
        """Ref2VA 要**另外 3 个模型文件**（unet / clip / lora）。提交前对着实例的清单
        点一次名，缺什么就直说 —— 别让用户对着一句 "value not in list" 猜。

        只在 Ref2VA 这条路上跑（i2v 那几个文件本来就在，白查一遍要 3 个来回）。
        查不动（网络/接口问题）就放行，交给 _submit 去报真正的错。
        """
        missing: list[str] = []
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            for cls, field in self._MODEL_INPUTS:
                if str(node.get("class_type")) != cls:
                    continue
                want = str((node.get("inputs") or {}).get(field) or "").strip()
                if not want:
                    continue
                try:
                    resp = requests.get(f"{self.base}/object_info/{cls}", timeout=30)
                    resp.raise_for_status()
                    req = ((resp.json().get(cls) or {}).get("input") or {}).get("required") or {}
                    opts = req.get(field) or []
                    opts = opts[0] if opts and isinstance(opts[0], list) else []
                except Exception:                       # noqa: BLE001 - 查不动就别拦
                    return
                if want not in opts:
                    missing.append(f"{field} = {want}")
        if missing:
            raise RuntimeError(
                "ComfyUI 上找不到这些模型文件：" + "；".join(missing)
                + "。这一份 Ref2VA 工作流要额外下载："
                  "unet = minimax_h3_ref2va_pruned_int8_convrot.safetensors、"
                  "clip = qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors、"
                  "lora = minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
                  "（VAE 两个文件与 I2V 共用，不用重下）。下完刷新 ComfyUI 再出片即可。"
            )

    def _upload(self, image_path: str) -> str:
        with open(image_path, "rb") as fh:
            resp = requests.post(
                f"{self.base}/upload/image",
                files={"image": (os.path.basename(image_path), fh)},
                data={"overwrite": "true"},
                timeout=120,
            )
        resp.raise_for_status()
        data = resp.json()
        # 带子目录时 LoadImage 的 image 字段要写 "subfolder/name" 形式
        sub = data.get("subfolder", "")
        name = data.get("name", "")
        return f"{sub}/{name}" if sub else name

    def _build_workflow(
        self,
        video_prompt: str,
        duration: float,
        image_name: str,
        audio: str = "",
        last_frame_name: str = "",
        megapixels: float | None = None,
        extra_refs: list[str] | None = None,
    ) -> dict:
        wf = json.loads(json.dumps(self.template))  # 深拷贝

        # ⚠️ H3 主节点**按 class_type 找，不写死节点号**：模板里是 104，但"用户自己
        # 从 ComfyUI 导出的那份"节点号完全可能不同（实测两份文件节点号从 92 到 146 都有）。
        # 写死 104 的话，换成自己导的文件就会 KeyError 或改错节点。
        h3_id = next(
            (k for k, v in wf.items()
             if isinstance(v, dict) and str(v.get("class_type", "")) in _H3_NODE_CLASSES),
            "",
        )
        if not h3_id:
            raise RuntimeError(
                "工作流里找不到 H3 主节点（要 MiniMaxH3ReferenceToVideo 或 "
                f"MiniMaxH3ImageToVideo 其中之一）：{sorted(wf)}"
            )
        h3 = wf[h3_id]
        is_ref2va = h3["class_type"] == "MiniMaxH3ReferenceToVideo"
        # Ref2VA 用 ref_images.ref_image_N 收参考图；I2V 只有 first_frame（+可选 last_frame）
        first_slot = "ref_images.ref_image_0" if is_ref2va else "first_frame"

        # 首张图的 LoadImage：优先用模板里**已经接在主节点上**的那个（自己导的文件也能用），
        # 接不上才退回模板号 100。
        link = h3["inputs"].get(first_slot)
        first_id = str(link[0]) if isinstance(link, list) and len(link) == 2 else "100"
        if first_id not in wf:
            first_id = "100"
        wf[first_id]["inputs"]["image"] = image_name
        h3["inputs"][first_slot] = [first_id, 0]

        # 帧数：时长 ×24fps，就近吸附到 ≡5 (mod 17) 的网格（H3 的帧数约束）。
        # 就近而不是向上：向上吸附会把 4.5s 的请求吞成 5.17s，时长设定失真。
        frames = max(5, round(duration * 24))
        frames = max(5, 17 * round((frames - 5) / 17) + 5)
        h3["inputs"]["length"] = frames
        # ⚠️ 2026-09-19 修：**Ref2VA 的提示词不能再套基础模式的壳**。
        # 官方两套格式是并列的（见 skills/h3-prompt-writing/references/）：
        #   基础模式（T2VA/I2VA/FL2VA/L2VA）：第一行「关键帧对齐指令」→
        #       integrated_multimodal_description → overall_soundscape → non_diegetic_music
        #   Ref2VA：**六段式本身就是完整提示词**（subject_definitions → summary →
        #       retention_analysis → detailed_description → overall_soundscape →
        #       non_diegetic_music），没有关键帧对齐那一行（它根本没有关键帧）。
        # 之前两条路都走 compose_h3_prompt，于是六段式被压进 integrated_multimodal_description
        # 一个字段里，前面还多了一行只对关键帧模式成立的对齐指令 —— 模型收到的是一份"畸形"
        # 提示词：六个章节降级成一个字段、<Subject N> 标签在没有声明的地方被引用。
        prompt_text = video_prompt.strip()
        if is_ref2va and "subject_definitions:" in prompt_text:
            h3["inputs"]["prompt"] = prompt_text
        else:
            # 基础模式（含"Ref2VA 工作流但提示词还是基础格式"的兜底）：拼三段 + 对齐指令。
            # Ref2VA 没有尾帧概念，所以那里的对齐指令按"只有首帧"写。
            # 传进来的是六段式时只取正文（见 _base_body 的注释）。
            h3["inputs"]["prompt"] = compose_h3_prompt(
                _base_body(video_prompt), audio, frames,
                bool(last_frame_name) and not is_ref2va,
            )

        if is_ref2va:
            # 第 2 张起动态注入 LoadImage。用 9xx 号段：模板号是 1xx/2xx，用户导出的
            # 文件号段不定，9xx 撞上的概率最低。
            # ⚠️ 必须**按需注入**，不能预先把空槽位摆在模板里 —— 未接线的节点会参与
            # ComfyUI 的输入校验（老代码里 last_frame 也是这个套路）。
            for i, name in enumerate(extra_refs or [], start=1):
                nid = str(900 + i)
                wf[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
                h3["inputs"][f"ref_images.ref_image_{i}"] = [nid, 0]
        elif last_frame_name:
            # 首尾帧接力：last_frame 节点按需动态注入
            wf["101"] = {
                "class_type": "LoadImage",
                "inputs": {"image": last_frame_name},
            }
            h3["inputs"]["last_frame"] = ["101", 0]

        wf["15"]["inputs"]["noise_seed"] = random.randint(0, 2**31)
        # 画质/速度旋钮。megapixels 由调用方（前端「清晰度」档位）优先，
        # 没给才落回环境变量——档位是按条选的，不该改一次就影响全局。
        wf["119"]["inputs"]["megapixels"] = (
            float(megapixels) if megapixels else float(os.getenv("H3_MEGAPIXELS", "0.9"))
        )
        steps = int(os.getenv("H3_STEPS", "8"))
        wf["9"]["inputs"]["steps"] = steps
        lora_name = os.getenv("H3_LORA", "").strip()
        if lora_name:
            # turbo LoRA 蒸馏档：8 步左右出片
            wf["121"]["inputs"]["lora_name"] = lora_name
        else:
            # 标准（无 LoRA）档：卸掉 LoRA 节点，模型直连采样器，20 步以上无蒸馏伪影
            wf["9"]["inputs"]["model"] = ["6", 0]
            wf.pop("121", None)
        return wf

    def _submit(self, workflow: dict) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        resp = requests.post(f"{self.base}/prompt", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"ComfyUI 拒绝了工作流：{json.dumps(data, ensure_ascii=False)[:500]}")
        return data["prompt_id"]

    def _wait(self, prompt_id: str) -> tuple[str, str]:
        """轮询 history 直到完成，返回 (filename, subfolder)。"""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            resp = requests.get(f"{self.base}/history/{prompt_id}", timeout=30)
            resp.raise_for_status()
            history = resp.json()
            if not history:
                continue
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"工作流执行出错：{json.dumps(status.get('messages', []), ensure_ascii=False)[:600]}")
            if not status.get("completed"):
                continue
            # 从 outputs 里翻出视频文件（不依赖具体节点 id / 键名）
            for outputs in entry.get("outputs", {}).values():
                for items in outputs.values():
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if isinstance(item, dict) and item.get("filename"):
                            return item["filename"], item.get("subfolder", "")
            raise RuntimeError("工作流完成但 outputs 里没有文件")
        raise TimeoutError(f"视频生成超时（{self.timeout:.0f}s），prompt_id={prompt_id}")

    def _download(self, filename: str, subfolder: str, out_path: str) -> None:
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        resp = requests.get(
            f"{self.base}/view",
            params={"filename": filename, "subfolder": subfolder, "type": "output"},
            stream=True,
            timeout=300,
        )
        resp.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)

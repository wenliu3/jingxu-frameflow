# Ref2VA 接入指引（全能参考模式）

> 状态：**代码已接入**（2026-09-19：`comfyui/h3_r2v_api.json` + `video_provider.ref_image_paths` +
> `server/app.py` 的 `video_workflow` 配置，离线 `_verify_r2v_workflow.py` 全过）。
> **仍未做的**：① 实例上下模型文件；② 一次真实出片；③ 音频参考没接。

## 一句话

让 H3 用「**角色定妆照 + 提示词**」生成视频，而不是「分镜首帧图」——
参考图只锁定人物身份，**构图交给提示词**，因此不必先为每一镜出图。

## 先分清：它和「首尾帧」不是一回事

这是最容易搞混的一处。ComfyUI 模板库里那三个 MiniMax H3 模板
（**文生视频 / 图生视频 / 首尾帧**）**全部属于 fl2va 权重，一个都不是 Ref2VA**：

| | 首尾帧（FL2VA） | 全能参考（Ref2VA） |
| --- | --- | --- |
| 给的图是什么 | **画面帧**：开头一张、结尾一张 | **角色参考**：人物长什么样 |
| 图约束了什么 | 开场构图 + 结束构图 | **只有人物身份** |
| 中间怎么变 | 模型在两张图之间插值 | 由提示词自由决定 |
| 要额外权重吗 | 不要（fl2va 自带） | **要**（ref2va） |

一句话：**首尾帧是「把画面钉在两头」，Ref2VA 是「把人物钉住、画面放开」。**
只有后者能实现「人物图 + 提示词、不必先出分镜图」。

## 为什么需要它

H3 有两个任务专用 checkpoint，**同一份 ComfyUI 部署一次只能挂一个**：

| 模式 | 权重 | 输入 | 构图控制权 | 提示词结构 |
| --- | --- | --- | --- | --- |
| I2VA 首帧生视频 | fl2va | 1 张图锚定 0.00s | 被图钉死 | 三段式 |
| **Ref2VA 全能参考** | **ref2va** | ≤9 图 + ≤3 视频 + ≤3 音频 | **图只锁身份** | **六段式** |

现在项目跑的是 I2VA：每镜必须出一张首帧图，画面构图就是那张图。
换成 Ref2VA 后，参考图变成「角色的四个视角定妆照」，每镜的机位、景别、光线
全部由 `video_prompt` 决定——这才是「分镜图不一定要生成」的技术前提。

> 注意：说「用 Ref2VA」不等于「完全不用图」。它仍然吃图，只是那些图是**角色参考图**
> 而不是**画面首帧图**。真正一张图都不要的是 T2VA（纯文生视频），代价是人物跨镜头一致性
> 只能靠文字描述维持，会明显变差。

## 现在缺什么（三件，缺一不可）

| # | 缺的东西 | 状态 |
| --- | --- | --- |
| 1 | `minimax_h3_ref2va_pruned_int8_convrot.safetensors`（约 20GB） | deploy 脚本**已加下载**（`WANT_REF2VA=1`，默认开） |
| 2 | ComfyUI 版本含 `MiniMaxH3ReferenceToVideo` 节点 | **待升级**（该节点 2026-08-03 随 PR #15224 合并） |
| 3 | `comfyui/h3_r2v_api.json`（API 格式工作流） | **待导出**，见下方步骤 3 |

## 操作步骤

### 步骤 1 · 确认权重

```bash
# 在实例上执行（幂等，已存在会跳过）
bash deploy_comfyui_ms.sh          # ModelScope 实例
bash deploy_comfyui.sh             # 自建 / 租卡环境
```

只想下参考图权重、不重跑其他步骤时，直接拉单文件：

```bash
cd /mnt/workspace/ComfyUI
modelscope download --model Comfy-Org/MiniMax-H3 \
  diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors \
  --local_dir models
```

⚠️ **盘空间先确认**：这个文件约 20GB。DSW 常驻盘通常很小，
放不下就 `--local_dir` 指到临时盘，再把 `models/diffusion_models/` 软链回去。

### 步骤 2 · 升级 ComfyUI

```bash
cd /mnt/workspace/ComfyUI
git pull
```

升级后必须验证节点在不在，缺了后面全白做：

```bash
# 节点存在性检查：搜不到就说明版本还是旧的
grep -rl "MiniMaxH3ReferenceToVideo" comfy_extras/ comfy/ 2>/dev/null | head
```

也可以直接看 API 文档：

```bash
curl -s http://127.0.0.1:8188/object_info | python3 -c "
import json,sys
d=json.load(sys.stdin)
print([k for k in d if 'MiniMaxH3' in k])
"
```

输出里应该有 `MiniMaxH3ReferenceToVideo`，同时也要有 `MiniMaxH3ImageToVideo`。

### 步骤 3 · 导出工作流（关键一步）

这一步必须在 ComfyUI 界面上做——UI 格式（`nodes`/`links`）和 API 格式（节点图）
之间的转换由前端完成，命令行没有可靠替代。

1. 浏览器打开 ComfyUI（隧道地址或 DSW 网关地址）；
2. 左侧 **Templates**（模板库）→ **Video** → 找到 **MiniMax H3 R2V** 并加载；
   - 模板不存在说明步骤 2 没做完
3. 菜单 **Workflow → Export (API)**；
   - 不同版本可能叫 **Save (API Format)**，需要先在设置里打开
     *Enable Dev mode Options*；
4. 把导出的 JSON 保存为项目的 `comfyui/h3_r2v_api.json`。

导出后自查一遍：文件里应该能搜到 `MiniMaxH3ReferenceToVideo`，
并且有 `LoadImage` 节点和 4 步加速 LoRA 的加载节点（和 i2v 那份结构类似）。

### 步骤 4 · 对照现有 I2V 工作流核对

打开 `comfyui/h3_i2v_api.json` 和新的 `h3_r2v_api.json`，
把下面几项填进本文末尾的「待确认」表格——**代码要按实测结果写，不能猜**：

- 参考图节点上，图片输入的字段名是什么（推测是 `ref_images`）
- 多张图是「一个输入收数组」还是「多个独立输入」
- 参考图的尺寸模式字段名与取值（推测 `ref_image_size`，取值 `match` / `max`）
- UNETLoader 的 `unet_name` 是否已指向 ref2va 权重

## 代码侧还需要改什么（待实现）

| 位置 | 改动 |
| --- | --- |
| `video_provider.py` | 新增 `ComfyUIRef2VAProvider`：复用四步协议，提交前把该镜出场角色的定妆照逐张上传并注入参考图输入；提示词走六段式 |
| `video_provider.py` | 新增 `compose_ref2va_prompt()`：`subject_definitions` → `summary` → `retention_analysis` → `detailed_description` → `overall_soundscape` → `non_diegetic_music` |
| `agents.py` | 提示词体系增加「六段式」分支（现在只有三段式），由视频后端决定产出哪种 |
| `server/app.py` | 单镜 / 批量生成要按模式分流：Ref2VA 下**不再要求存在首帧图**（这是「不出分镜图」的落点） |
| `server/app.py` | 服务配置面板增加「视频模式」选项（`首帧 I2VA` / `参考 Ref2VA`） |
| `schemas.py` | `Shot` 可考虑加 `reference` 字段覆盖默认参考图；不加则由 `character_refs` + 角色定妆照推导 |
| `web/` | 服务配置面板加模式选择；分镜卡在 Ref2VA 下隐藏「首帧图」相关操作 |

**参考图怎么来**：用分镜已有的 `character_refs`（出场角色名）去 `project.characters`
里取对应角色的 `images`（四视角）——**不需要新增字段**。多个角色时按出场顺序拼起来，
注意 Ref2VA 上限是 9 张图，三个角色就是 12 张，超了要按镜头重要性取舍（比如只取正面+全身）。

## 待确认（必须在实例上实测）

> 这些是写代码前必须落实的事实。凭空猜节点输入格式，写出来的代码只会静默出废片。

| 项 | 推测值 | 实测结果 |
| --- | --- | --- |
| 参考图输入字段名 | `ref_images` | ✅ `ref_images`（`h3_r2v_ui.json` 里带真实连线编号） |
| 多图传参形式（数组 or 多个输入） | 待定 | ✅ **多个独立输入**：`ref_images.ref_image_0` … `_8`（Autogrow） |
| 尺寸模式字段名 / 取值 | `ref_image_size` = `match` \| `max` | ✅ 同推测（`match` 缩到生成画布面积，`max` 短边 2048、最准但慢数倍） |
| 参考图上限 | 9 张 | ✅ **9 张**。官方源码 `comfy_extras/nodes_minimax_h3.py`：`Autogrow.TemplatePrefix(prefix="ref_image_", min=0, max=9)`。⚠️ 2026-09-27 前代码里误按 3 张截断（照 UI 导出文件里只连了 3 条反推的） |
| 参考视频 / 参考音频字段 | `ref_videos` / `ref_audios` | ✅ `ref_videos`（3 个）/ `ref_video_audios`（3 个）/ `ref_audios`（3 个），同样 0 起编号 |
| 切回 I2VA 是否要改 UNETLoader | 只需换工作流 | ✅ 只需换工作流（两份 JSON 各自写死了自己的 unet/clip/lora） |

> ⚠️ **判断"某个节点有几个口"的正确姿势**：查 `comfy_extras/*.py` 里的 schema，
> 或实例上的 `GET /object_info/<节点名>`。**别拿 UI 导出文件里连了几条当上限** ——
> 那只是作者当时连了几条。3 张 vs 9 张这个错就是这么来的。

## 与现有链路的关系

两条路不冲突，可以并存：

- **I2VA 保留**：关键镜头、需要精确构图和统一画面风格的，出首帧图后走原链路；
- **Ref2VA 新增**：过渡镜头、动作镜头、构图想让模型自由发挥的，只给角色参考图。

代价是 **GPU 时间**：Ref2VA 要额外编码参考图，且 9 张图的参考开销不小。
批量出片时建议按镜头类型分配，别全量都走 Ref2VA。

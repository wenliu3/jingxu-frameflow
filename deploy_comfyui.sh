#!/bin/bash
# ============================================================
# deploy_comfyui.sh — 租卡环境一键部署 ComfyUI + MiniMax H3
# 适用：任何 Linux GPU 机器（AutoDL / 有公网 IP 的服务器等）
# 不含内网穿透——平台给了公网地址就直接填前端「视频服务」面板。
#
# 用法：
#   bash deploy_comfyui.sh                    # 部署到 ~/comfyui-avm
#   bash deploy_comfyui.sh /data/comfyui      # 指定目录（数据盘大就放数据盘）
#   MODEL_SOURCE=hf bash deploy_comfyui.sh    # 海外机器走 HuggingFace 下载
#   PORT=9000 bash deploy_comfyui.sh          # 换端口
#
# 前提：平台镜像里已带 PyTorch + CUDA（AutoDL 选镜像时勾选）。
# 本脚本不会动 torch——版本冲突是租卡环境最大的坑。
# 脚本幂等：重复执行会跳过已装/已下载的部分。
# ============================================================

set -u
DIR="${1:-$HOME/comfyui-avm}"
PORT="${PORT:-8188}"
MODEL_SOURCE="${MODEL_SOURCE:-cn}"   # cn=ModelScope（国内快） | hf=HuggingFace（海外快）

step() { echo; echo "==> $*"; }

step "0/6 环境"
command -v git >/dev/null || { echo "❌ 缺 git：apt install -y git"; exit 1; }
command -v python3 >/dev/null || { echo "❌ 缺 python3"; exit 1; }
if ! python3 -c "import torch" 2>/dev/null; then
  echo "❌ 当前环境没有 PyTorch。租卡时请选带 PyTorch/CUDA 的镜像，"
  echo "   或自行安装与显卡匹配的版本（本脚本故意不碰 torch）。"
  exit 1
fi
python3 -c "import torch; print('torch', torch.__version__, '| cuda:', torch.cuda.is_available())" \
  || echo "⚠️  torch.cuda 不可用，生成会失败或极慢"

step "1/6 克隆 ComfyUI"
mkdir -p "$DIR"
cd "$DIR"
if [ ! -f ComfyUI/main.py ]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git 2>/dev/null \
    || git clone --depth 1 https://gh-proxy.com/https://github.com/comfyanonymous/ComfyUI.git
fi
cd ComfyUI

step "2/6 安装依赖（自动绕开 torch 三件套，多镜像轮换）"
grep -vE "^torch( |$|==|>=|<=)" requirements.txt > /tmp/req_avm.txt
ok=""
for IDX in https://mirrors.cloud.tencent.com/pypi/simple https://mirrors.aliyun.com/pypi/simple/ https://pypi.org/simple; do
  echo "[deps] trying $IDX"
  pip3 install -q -r /tmp/req_avm.txt modelscope soundfile huggingface_hub -i "$IDX" > /tmp/pip_avm.log 2>&1
  if python3 -c "import sqlalchemy, av, comfyui_frontend_package, modelscope, soundfile" 2>/dev/null; then
    echo "[deps] OK via $IDX"
    ok=1
    break
  fi
done
[ -n "$ok" ] || { echo "❌ 依赖安装失败，看 /tmp/pip_avm.log 定位"; exit 1; }

step "3/6 下载 MiniMax H3 模型（fl2va 约 66GB / Ref2VA 约 36GB，断点续传，已存在的自动跳过）"
M=models

# 每个文件的**大致体积（GB，向上取整）** —— 只用来算"盘够不够"，不参与下载。
# 数字来自魔搭 Comfy-Org/MiniMax-H3 的文件列表（2026-09-29 核对）。
size_gb() {
  case "$1" in
    diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors)  echo 38 ;;
    text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors) echo 26 ;;
    diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors) echo 20 ;;
    text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) echo 15 ;;
    vae/minimax_h3_video_vae_fp16.safetensors)  echo 5 ;;
    vae/minimax_h3_audio_vae_fp32.safetensors)  echo 1 ;;
    loras/*) echo 2 ;;
    *) echo 0 ;;
  esac
}

FILES=(
  # ⚠️ 两个 VAE 是**两条路共用**的（I2V 和 Ref2VA 都要），无论选哪一套都不能省。
  "vae/minimax_h3_video_vae_fp16.safetensors"
  "vae/minimax_h3_audio_vae_fp32.safetensors"
)

# fl2va：文生视频 / 首帧 / 首尾帧（T2VA / I2VA / FL2VA / L2VA），约 66GB（不含共用 VAE）。
# 它与 ref2va 是**两个独立 checkpoint，同一时刻只能挂一个**，所以要手动切换。
# 不需要就 WANT_FL2VA=0 bash deploy_comfyui.sh 跳过。
if [ "${WANT_FL2VA:-1}" = "1" ]; then
  FILES+=(
    "diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors"
    "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
    "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    "loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
  )
fi

# ref2va：全能参考模式（Ref2VA）——用角色参考图锁定身份，构图交给提示词，
# 这才是「人物图 + 提示词、不出分镜图」那条路线需要的权重。约 36GB。
#
# ⚠️ **三个文件缺一不可（2026-09-29 修）**：从前这里只加了 unet 那一个，于是"照脚本
#    部署完"仍然出不了片 —— `comfyui/h3_r2v_api.json` 还要求：
#      ① 换一个 text_encoder：nvfp4_awq 那份，**与 fl2va 用的 int8_convrot 不是同一个文件**；
#      ② 换一个 ref2v 专用 LoRA（fl2v 那两个用不了）。
#    实测现象：出片直接失败，报「ComfyUI 上找不到这些模型文件：unet_name = …；clip_name = …；
#    lora_name = …」—— 那句话由 `video_provider._preflight_models` 在提交前点名。
# 注：ref2va 只有 pruned INT8 量化版（比标准 INT8 小约 40%），没有 bf16 版。
if [ "${WANT_REF2VA:-1}" = "1" ]; then
  FILES+=(
    "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
  )
fi

# ⚠️ **盘够不够先算一遍**（2026-10-01 加）：两套权重加起来约 108GB，
#    小盘机器上默认全下会写到一半炸掉。先算"还缺几个 GB"，不够就早退。
need=0
for f in "${FILES[@]}"; do
  [ -f "$M/$f" ] && continue
  need=$(( need + $(size_gb "$f") ))
done
avail=$(df -P -BG "$DIR" 2>/dev/null | tail -1 | awk '{print $4}' | tr -d 'G')
echo "[disk] 本次需要新下载约 ${need}GB；$DIR 可用 ${avail:-?}GB"
if [ "${avail:-0}" -gt 0 ] && [ "$need" -gt "$avail" ]; then
  echo
  echo "❌ 盘不够：还要 ${need}GB，只剩 ${avail}GB。两套权重加起来约 108GB。"
  echo "   挑一套下（推荐 Ref2VA —— 镜序的提示词体系就是围绕它做的）："
  echo "     WANT_FL2VA=0 bash deploy_comfyui.sh     # 只下 Ref2VA，约 41GB"
  echo "     WANT_REF2VA=0 bash deploy_comfyui.sh    # 只下 I2V(fl2va)，约 72GB"
  echo "   或者把模型下到大盘（数据盘）：bash deploy_comfyui.sh /data/comfyui"
  exit 1
fi

for f in "${FILES[@]}"; do
  if [ -f "$M/$f" ]; then
    echo "已有 $f，跳过"
    continue
  fi
  if [ "$MODEL_SOURCE" = "hf" ]; then
    huggingface-cli download Comfy-Org/MiniMax-H3 "$f" --local-dir "$M"
  else
    modelscope download --model Comfy-Org/MiniMax-H3 "$f" --local_dir "$M"
  fi
done

step "4/6 启动 ComfyUI（0.0.0.0:$PORT）"
if [ -f /tmp/avm_comfy.pid ] && kill -0 "$(cat /tmp/avm_comfy.pid)" 2>/dev/null; then
  kill "$(cat /tmp/avm_comfy.pid)"
  sleep 2
fi
nohup python3 main.py --listen 0.0.0.0 --port "$PORT" > "$DIR/comfyui.log" 2>&1 &
echo $! > /tmp/avm_comfy.pid

step "5/6 健康检查"
ready=""
for i in $(seq 1 30); do
  sleep 2
  if curl -s -m 2 "http://127.0.0.1:$PORT/system_stats" > /dev/null; then
    echo "✅ ComfyUI 已就绪"
    ready=1
    break
  fi
  if [ "$i" = "30" ]; then
    echo "❌ 60 秒内没起来，看日志：tail -30 $DIR/comfyui.log"
    exit 1
  fi
done

step "6/6 完成 — 接到前端"
echo "本机地址: http://127.0.0.1:$PORT"
echo ""
echo "下一步（二选一，填进前端「视频服务」面板）："
echo "  • 平台给了公网映射网址（AutoDL 自定义服务等）→ 直接填那个网址"
echo "  • 浏览器打不开平台网址 → 在这台机器上跑一次 pinggy："
echo "      ssh -p 443 -R0:127.0.0.1:$PORT -o StrictHostKeyChecking=no free.pinggy.io"
echo "    把它打印的 https://xxxx.free.pinggy.net 填进面板"

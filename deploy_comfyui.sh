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

step "3/6 下载 MiniMax H3 模型（约 78GB，断点续传，已存在的自动跳过）"
M=models
FILES=(
  "diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors"
  "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
  "vae/minimax_h3_video_vae_fp16.safetensors"
  "vae/minimax_h3_audio_vae_fp32.safetensors"
  "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
  "loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
)
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

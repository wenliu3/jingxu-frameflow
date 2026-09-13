#!/bin/bash
# ============================================================
# deploy_comfyui_ms.sh — ModelScope 实例一键部署+启动
# （ComfyUI + MiniMax H3 全套模型 + pinggy 隧道）
#
# 用法：放在 /mnt/workspace 下执行   bash deploy_comfyui_ms.sh
# 幂等：重复执行会跳过已装/已下载的部分，只重启服务和隧道。
# 换了新容器（实例重启，pip 依赖全丢）也会自动补装依赖。
# ============================================================

set -u
DIR=/mnt/workspace/ComfyUI
PORT=8188

step() { echo; echo "==> $*"; }

step "0/6 环境（torch 是平台自带的 ROCm 版，脚本不动它）"
python3 -c "import torch; print('torch', torch.__version__, '| cuda:', torch.cuda.is_available())" \
  || { echo "❌ torch 不可用"; exit 1; }

step "1/6 ComfyUI 代码"
if [ ! -f "$DIR/main.py" ]; then
  cd /mnt/workspace
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git 2>/dev/null \
    || git clone --depth 1 https://gh-proxy.com/https://github.com/comfyanonymous/ComfyUI.git \
    || { echo "❌ 克隆失败"; exit 1; }
fi
cd "$DIR"

step "2/6 依赖（换新容器会全丢，这里多镜像轮换自动补）"
NEED=""
python3 -c "import sqlalchemy, av, kornia, spandrel, alembic, comfyui_frontend_package, comfyui_workflow_templates, soundfile, modelscope" 2>/dev/null || NEED=1
if [ -n "$NEED" ]; then
  echo "[deps] 缺依赖，安装中（多镜像轮换）..."
  grep -vE "^torch( |$|==|>=|<=)" requirements.txt > /tmp/req_ms.txt
  for IDX in https://mirrors.cloud.tencent.com/pypi/simple https://mirrors.aliyun.com/pypi/simple/ https://pypi.org/simple; do
    echo "[deps] trying $IDX"
    pip3 install -q -r /tmp/req_ms.txt modelscope -i "$IDX" > /tmp/pip_ms.log 2>&1
    python3 -c "import sqlalchemy, av, comfyui_frontend_package, modelscope" 2>/dev/null && { echo "[deps] OK via $IDX"; break; }
  done
  python3 -c "import comfyui_workflow_templates" 2>/dev/null \
    || pip3 install -q comfyui-workflow-templates -i https://mirrors.cloud.tencent.com/pypi/simple
  python3 -c "import soundfile" 2>/dev/null \
    || pip3 install -q soundfile -i https://mirrors.aliyun.com/pypi/simple/
fi
python3 -c "import sqlalchemy, av, comfyui_frontend_package, comfyui_workflow_templates, modelscope, soundfile" 2>/dev/null \
  || { echo "❌ 依赖不齐，看 /tmp/pip_ms.log 定位"; exit 1; }
echo "[deps] OK"

step "3/6 模型（ModelScope 内网下载极快，已存在的自动跳过）"
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
  modelscope download --model Comfy-Org/MiniMax-H3 "$f" --local_dir "$M"
done

step "4/6 启动 ComfyUI（0.0.0.0:$PORT）"
pkill -f "main[.]py --listen" 2>/dev/null || true
sleep 2
nohup python3 main.py --listen 0.0.0.0 --port "$PORT" > /mnt/workspace/comfyui.log 2>&1 &
echo $! > /tmp/avm_comfy.pid

step "5/6 健康检查"
ready=""
for i in $(seq 1 60); do
  sleep 2
  if curl -s -m 2 "http://127.0.0.1:$PORT/system_stats" > /dev/null; then
    echo "✅ ComfyUI 已就绪"
    ready=1
    break
  fi
  if [ "$i" = "60" ]; then
    echo "❌ 没起来，看日志：tail -30 /mnt/workspace/comfyui.log"
    exit 1
  fi
done

step "6/6 隧道（ModelScope 没有公网 IP，必须走）"
if [ -f /mnt/workspace/pinggy.pid ] && kill -0 "$(cat /mnt/workspace/pinggy.pid)" 2>/dev/null; then
  kill "$(cat /mnt/workspace/pinggy.pid)"
  sleep 1
fi
nohup ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -p 443 -R0:127.0.0.1:$PORT free.pinggy.io > /mnt/workspace/pinggy.log 2>&1 &
echo $! > /mnt/workspace/pinggy.pid
sleep 12
grep -oE "https://[a-zA-Z0-9.-]+\.(link|net)" /mnt/workspace/pinggy.log | head -1 | tee /mnt/workspace/tunnel_url.txt

DSW_ID=$(hostname | cut -d- -f1-2)
echo ""
echo "==================== 部署完成 ===================="
echo "前端「视频服务」面板填这个（隧道，60 分钟一换）："
echo "  $(cat /mnt/workspace/tunnel_url.txt)"
echo ""
echo "浏览器直接看 ComfyUI（跟登录态走，地址不过期）："
echo "  https://dsw-gateway-cn-hangzhou.data.aliyun.com/$DSW_ID/proxy/$PORT/"
echo "=================================================="

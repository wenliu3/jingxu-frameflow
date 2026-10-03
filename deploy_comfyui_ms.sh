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
# 与 ref2va 是两个独立 checkpoint，同一时刻只能挂一个（切模式只换工作流，见 docs/REF2VA.md）。
if [ "${WANT_FL2VA:-1}" = "1" ]; then
  FILES+=(
    "diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors"
    "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
    "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    "loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
  )
fi

# ref2va：全能参考模式（Ref2VA）——用角色参考图锁定身份、构图交给提示词，
# 这才是「人物图 + 提示词、不出分镜图」那条路线需要的权重。约 36GB。
#
# ⚠️ **三个文件缺一不可（2026-09-29 修）**：从前这里只加了 unet 那一个，于是"照脚本
#    部署完"仍然出不了片 —— `comfyui/h3_r2v_api.json` 还要求：
#      ① 换一个 text_encoder：nvfp4_awq 那份，**与 fl2va 用的 int8_convrot 不是同一个文件**；
#      ② 换一个 ref2v 专用 LoRA（fl2v 那两个用不了）。
#    实测现象：出片直接失败，报「ComfyUI 上找不到这些模型文件：unet_name = …；clip_name = …；
#    lora_name = …」—— 那句话由 `video_provider._preflight_models` 在提交前点名。
if [ "${WANT_REF2VA:-1}" = "1" ]; then
  FILES+=(
    "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
  )
fi

# ⚠️ **盘够不够先算一遍**（2026-10-01 加）。ModelScope 的持久盘只有 100GB，
#    而两套权重加起来约 108GB —— 默认全下会**把盘写满**，写到一半失败，
#    还可能把实例搞到起不来。所以这里先算"还缺几个 GB"，不够就早退，
#    并告诉用户「只下一套」怎么跑，而不是等 40 分钟后才炸。
need=0
for f in "${FILES[@]}"; do
  [ -f "$M/$f" ] && continue
  need=$(( need + $(size_gb "$f") ))
done
avail=$(df -P -BG "$DIR" 2>/dev/null | tail -1 | awk '{print $4}' | tr -d 'G')
echo "[disk] 本次需要新下载约 ${need}GB；$DIR 可用 ${avail:-?}GB"
if [ "${avail:-0}" -gt 0 ] && [ "$need" -gt "$avail" ]; then
  echo
  echo "❌ 盘不够：还要 ${need}GB，只剩 ${avail}GB。两套权重加起来约 108GB，100GB 的盘装不下。"
  echo "   挑一套下（推荐 Ref2VA —— 镜序的提示词体系就是围绕它做的）："
  echo "     WANT_FL2VA=0 bash deploy_comfyui_ms.sh     # 只下 Ref2VA，约 41GB"
  echo "     WANT_REF2VA=0 bash deploy_comfyui_ms.sh    # 只下 I2V(fl2va)，约 72GB"
  echo "   想两套都要，就把模型放到临时盘再把 models/ 软链回来（实例一重启就得重下）。"
  exit 1
fi

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
# 有阿里云跳板就用它（地址固定、不过期）；没有才退回 pinggy（60 分钟一换地址）。
# 判据：`ALIYUN_JUMP=user@ip` 传了，或者实例上已经有 tunnel_aliyun.sh + aliyun_jump.txt。
if [ -n "${ALIYUN_JUMP:-}" ] || { [ -x /mnt/workspace/tunnel_aliyun.sh ] && [ -s /mnt/workspace/aliyun_jump.txt ]; }; then
  echo "[tunnel] 检测到阿里云跳板 → 用 tunnel_aliyun.sh（地址固定，不再走 pinggy）"
  if [ -n "${ALIYUN_JUMP:-}" ]; then
    bash /mnt/workspace/tunnel_aliyun.sh "$ALIYUN_JUMP" "${ALIYUN_RPORT:-18188}"
  else
    bash /mnt/workspace/tunnel_aliyun.sh
  fi
  echo ""
  echo "==================== 部署完成 ===================="
  echo "隧道地址固定，填一次就不用再改（镜序「服务配置 → ComfyUI 地址」）。"
  echo "浏览器直接看 ComfyUI（跟登录态走，地址不过期）："
  DSW_ID=$(hostname | cut -d- -f1-2)
  echo "  https://dsw-gateway-cn-hangzhou.data.aliyun.com/$DSW_ID/proxy/$PORT/"
  echo "=================================================="
  exit 0
fi

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
echo "💡 嫌 60 分钟一换地址麻烦？用自己的服务器做跳板（地址固定）："
echo "   1) 把项目根的 tunnel_aliyun.sh 传到 /mnt/workspace/"
echo "   2) bash /mnt/workspace/tunnel_aliyun.sh root@你的服务器IP"
echo "   详见 docs/comfyui-tunnel-guide.md"
echo ""
echo "浏览器直接看 ComfyUI（跟登录态走，地址不过期）："
echo "  https://dsw-gateway-cn-hangzhou.data.aliyun.com/$DSW_ID/proxy/$PORT/"
echo "=================================================="

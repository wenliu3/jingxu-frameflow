#!/bin/bash
# ============================================================
# tunnel_aliyun.sh — 用**你自己的阿里云服务器**当跳板（替代 pinggy）
#
# 为什么要换：pinggy 免费档每条隧道 **60 分钟**到期、重连后**域名会变**，
# 于是每跑一会儿就得改一次「服务配置」。换成自己的服务器后：
#   · 地址固定（就是你阿里云的公网 IP + 一个端口），填一次就不用再管
#   · 不会 60 分钟断一次；断了也自己重连
#   · 不经过任何第三方
#
# 原理：阿里云服务器当「中转站」。实例这边**主动连出去**（内网机器只能这样），
# 把它的 8188 端口挂到阿里云的某个端口上；镜序直接连阿里云那个地址。
#
#   [ModelScope 实例 :8188] ──ssh -R──> [阿里云 :18188] <──http── [你本机镜序]
#
# ---- 一次性准备（在**阿里云服务器**上做，只需一次）----
#   ① 允许把端口挂到公网（默认只允许挂到 127.0.0.1）：
#        echo "GatewayPorts yes" | sudo tee -a /etc/ssh/sshd_config
#        sudo systemctl restart sshd
#   ② 阿里云控制台 → 安全组 → 入方向 → 放行 TCP 18188
#   ③ 实例要能**免密**登进阿里云（后台进程没有终端，敲不了密码）：
#        在实例上 ssh-keygen -t ed25519 -N '' 然后把 ~/.ssh/id_ed25519.pub
#        追加到阿里云的 ~/.ssh/authorized_keys；先在实例上手敲一次
#        `ssh root@你的IP` 确认**不问密码**能进去，再跑这个脚本。
#
# ⚠️ 安全：安全组的源别图省事写 0.0.0.0/0 —— ComfyUI **没有任何密码**，
#    对全世界开放等于把 GPU 和 100GB 盘送人。填你自己宽带的出口 IP，
#    或者只用下面 BIND=127.0.0.1 那种「不暴露」的姿势。
#    详见 docs/comfyui-tunnel-guide.md
#
# ---- 用法（在**实例**上）----
#   第一次（把跳板机地址当参数传一次，它会记下来）：
#       bash /mnt/workspace/tunnel_aliyun.sh root@1.2.3.4
#       → 打印「已记住跳板机 …」
#   以后每次（实例重启后也一样）：
#       bash /mnt/workspace/tunnel_aliyun.sh
#
#   临时换参数：
#       RPORT=18188 ALIYUN_PORT=22 bash /mnt/workspace/tunnel_aliyun.sh
#       （跳板机也可以走环境变量：ALIYUN=root@1.2.3.4 bash tunnel_aliyun.sh）
# ============================================================

set -u

JUMP_FILE=${JUMP_FILE:-/mnt/workspace/aliyun_jump.txt}
LOOP=${LOOP:-/mnt/workspace/tunnel_aliyun_loop.sh}
LOG=${LOG:-/mnt/workspace/tunnel_aliyun.log}
PIDFILE=${PIDFILE:-/mnt/workspace/tunnel_aliyun.pid}

LPORT=${LPORT:-8188}          # 实例上 ComfyUI 的端口
RPORT=${RPORT:-18188}         # 挂到阿里云上的端口（安全组要放行它）
ALIYUN_PORT=${ALIYUN_PORT:-22}
BIND=${BIND:-0.0.0.0}         # 0.0.0.0=公网可访问（需要 GatewayPorts yes）
                              # 127.0.0.1=只有阿里云本机能连（更安全，但本机还得再转发一层）

# 跳板机是谁：参数 > 环境变量 > 上次记下来的那个文件。
# 带参数来就顺手写进文件 —— 这样"第一次带、以后不带"是自然用法，不用记两条命令。
if [ -n "${1:-}" ]; then
  ALIYUN=$1
  printf '%s\n' "$ALIYUN" > "$JUMP_FILE"
  echo "==> 已记住跳板机 $ALIYUN（下次直接 bash $0 就行）"
else
  ALIYUN=${ALIYUN:-$(cat "$JUMP_FILE" 2>/dev/null | tr -d '[:space:]')}
fi
if [ -z "$ALIYUN" ]; then
  echo "❌ 还不知道跳板机是谁。第一次这样跑（把 IP 换成你阿里云的公网 IP）："
  echo "     bash $0 root@1.2.3.4"
  echo "   它会记到 $JUMP_FILE，以后直接 bash $0 即可。"
  exit 1
fi

# 停掉旧的：用 PID 文件，**不要 pkill -f** —— 那个会自匹配把脚本自己杀掉
# （这个坑 docs/comfyui-tunnel-guide.md 的「坑清单」里记着）
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null
  sleep 1
fi
: > "$LOG"

# 断线自动重连：写成独立脚本，方便单独看/单独调，也免得引号套太多层。
# ConnectTimeout=10 是故意的：连不上要**10 秒内**暴露出来，别让这个脚本傻等 2 分钟
# 还以为一切正常。
cat > "$LOOP" <<EOF
#!/bin/bash
# 由 tunnel_aliyun.sh 生成，改它没用 —— 改上面那个脚本
# 地址：$ALIYUN  端口：$BIND:$RPORT → 127.0.0.1:$LPORT  (ssh 端口 $ALIYUN_PORT)
while true; do
  echo "[\$(date '+%F %T')] 连接 $ALIYUN ..."
  ssh -o StrictHostKeyChecking=no \\
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \\
      -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \\
      -N -R $BIND:$RPORT:127.0.0.1:$LPORT -p $ALIYUN_PORT $ALIYUN
  code=\$?    # ⚠️ 必须先接住再 echo：下面那句里有 \$(date)，命令替换会把 \$? 冲成 0
  echo "[\$(date '+%F %T')] 断了（退出码 \$code），5 秒后重连"
  sleep 5
done
EOF
chmod +x "$LOOP"

nohup "$LOOP" > "$LOG" 2>&1 &
echo $! > "$PIDFILE"

# 等它连上。判定标准：日志里出现了「连接」，而且**还没出现「断了」**。
# 只看进程活着是不够的（连不上时它就是活着在重试），只看日志有输出也不够。
echo "==> 隧道进程 PID=$(cat "$PIDFILE")，日志：$LOG"
for i in $(seq 1 6); do
  sleep 3
  if grep -q "断了" "$LOG"; then break; fi
  if [ "$i" = "6" ]; then break; fi
done

echo "==> 日志："
tail -8 "$LOG"
echo

if grep -q "断了" "$LOG" || ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "❌ 没连上。对着上面那句真实报错看："
  echo "   Permission denied / Host key verification failed"
  echo "     → 免密登录没配好：先在实例上 `ssh $ALIYUN` 手敲一次，确认不问密码"
  echo "   Connection timed out / Connection refused"
  echo "     → 跳板机地址或 ALIYUN_PORT 不对；阿里云安全组也要放行它的 22 端口"
  echo "   remote port forwarding failed / cannot listen"
  echo "     → 阿里云上没开 GatewayPorts yes（见本脚本头部「一次性准备」①）"
  exit 1
fi

IP=${ALIYUN#*@}
echo "✅ 隧道已建立（地址固定，不会再变）。镜序「服务配置 → ComfyUI 地址」填："
if [ "$BIND" = "0.0.0.0" ]; then
  echo "     http://$IP:$RPORT"
  echo "   本机自测： curl -s http://$IP:$RPORT/system_stats"
else
  echo "     http://127.0.0.1:$RPORT   ← $BIND 绑定只有阿里云本机能连，"
  echo "       你本机还要再开一条本地转发： ssh -N -L 8188:127.0.0.1:$RPORT $ALIYUN"
  echo "       然后镜序填 http://127.0.0.1:8188（这个最安全：公网上完全看不到）"
fi
echo
echo "⚠️ 安全组记得放行 TCP $RPORT，但**别对全世界开放** —— ComfyUI 没有密码。"
echo "   实例重启后重跑一次本脚本即可，ComfyUI 那边跑 start_comfyui.sh。"

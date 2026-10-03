#!/bin/bash
# ============================================================
# tunnel_aliyun.sh — 实例重启后的**一键恢复**：ComfyUI + 阿里云隧道
#
# 为什么是一个脚本干两件事（2026-09-29 定）：
#   这两件事**永远一起做**，拆成两条命令只会让人记两遍（实测被问过"为啥要跑两个"）。
#   所以合成一条：**先把 ComfyUI 拉起来（新容器补依赖要几分钟），再挂隧道（十几秒）**。
#   ⚠️ 顺序不能反（2026-10-02 改）：隧道先通、ComfyUI 后起的话，中间那几分钟里
#      镜序连上去是失败的（日志里 `connect_to 127.0.0.1 port 8188: failed.`），
#      看着像"隧道坏了"，其实只是后端还没起来。
#   它也顺带取代了原来那个 start_comfyui.sh —— 那个每次都会起一个 pinggy 隧道、
#   打印两个 60 分钟就失效的地址，很容易被当成"隧道就是这个"抄进「服务配置」。
#
# 为什么要用阿里云跳板（替代 pinggy）：
#   pinggy 免费档每条隧道 **60 分钟**到期、重连后**域名会变**，于是每跑一会儿就得改一次
#   「服务配置」。换成自己的服务器后：地址固定（填一次就不用再管）、不会 60 分钟断一次、
#   也不经过任何第三方。
#
# 原理：阿里云服务器当「中转站」。实例这边**主动连出去**（内网机器只能这样），
# 把它的 8188 端口挂到阿里云的某个端口上；镜序直接连阿里云那个地址。
#
#   [ModelScope 实例 :8188] ──ssh -R──> [阿里云 :18188] <──http── [你本机镜序]
#
# ---- 一次性准备（在**阿里云服务器**上做，只需一次）----
#   ① 允许把端口挂到公网（默认只允许挂到 127.0.0.1）：
#        grep -q "^GatewayPorts" /etc/ssh/sshd_config || echo "GatewayPorts yes" | sudo tee -a /etc/ssh/sshd_config
#        sudo systemctl restart sshd || sudo systemctl restart ssh
#   ② 阿里云控制台 → 安全组 → 入方向 → 放行端口（每台实例一个，比如 18188 / 18189）
#   ③ **把本机密钥加到阿里云** —— 这一步脚本会帮你做一半：第一次跑它自己生成密钥，
#      并把「可以整行粘进 authorized_keys」的那一行打印出来，你复制给服务器主人即可。
#      用**受限密钥**（只能转发这一个端口、拿不到 shell），别用 ssh-copy-id。
#
# ⚠️ 安全：安全组的源别图省事写 0.0.0.0/0 —— ComfyUI **没有任何密码**，
#    对全世界开放等于把 GPU 和 100GB 盘送人。填你自己宽带的出口 IP，
#    或者只用下面 BIND=127.0.0.1 那种「不暴露」的姿势。
#    详见 docs/comfyui-tunnel-guide.md
#
# ---- 用法（在**实例**上）----
#   第一次：带上跳板机地址（**同一台跳板机上再挂第二台实例时，把端口也带上**）
#       bash /mnt/workspace/tunnel_aliyun.sh root@1.2.3.4           # 挂到 18188
#       bash /mnt/workspace/tunnel_aliyun.sh root@1.2.3.4 18189     # 兄弟那台用 18189
#       → 第一次会生成密钥并打印「那一行」，发给服务器主人加进 authorized_keys
#       → 加完再跑一次，就通了（打印「已记住：跳板机 …，端口 …」）
#   以后每次（**实例重启后也只要这一条**）：
#       bash /mnt/workspace/tunnel_aliyun.sh
#
#   临时换参数 / 只要隧道不管 ComfyUI：
#       SKIP_COMFYUI=1 bash /mnt/workspace/tunnel_aliyun.sh
#       RPORT=18189 ALIYUN_PORT=22 bash /mnt/workspace/tunnel_aliyun.sh
#       （跳板机也可以走环境变量：ALIYUN=root@1.2.3.4 bash tunnel_aliyun.sh）
#
#   ⚠️ 几台实例共用一台跳板机时，**每台必须用不同端口**（18188 / 18189 / …），
#      否则第二台会报 `remote port forwarding failed for listen port 18188`。
#      安全组要把每个端口都放行，受限密钥的 permitlisten 也要与之一致。
#
# ---- 以后想改什么，改哪里（**这个文件基本不用动**）----
#   · 换端口（兄弟那台/换一个）  → 当参数传一次就记住：
#         bash /mnt/workspace/tunnel_aliyun.sh root@1.2.3.4 18189
#   · 换跳板机 IP                → 同上，把 IP 换掉传一次
#   · 临时试一个值、不改记忆     → RPORT=19000 bash /mnt/workspace/tunnel_aliyun.sh
#   · 看/手改"记住的值"          → cat /mnt/workspace/aliyun_jump.txt
#                                  （第 1 行跳板机，第 2 行端口；删掉它就回到默认 18188）
#   · 公网上完全看不到（最安全） → BIND=127.0.0.1 bash /mnt/workspace/tunnel_aliyun.sh
#   · ComfyUI 装在别处           → COMFY_DIR=/别的/路径 bash /mnt/workspace/tunnel_aliyun.sh
#   · 只要隧道、不管 ComfyUI     → SKIP_COMFYUI=1 bash /mnt/workspace/tunnel_aliyun.sh
#
#   下面 ${XXX:-默认值} 那种写法 = "环境变量能覆盖的默认值"，不改也能用。
# ============================================================

set -u

JUMP_FILE=${JUMP_FILE:-/mnt/workspace/aliyun_jump.txt}
LOOP=${LOOP:-/mnt/workspace/tunnel_aliyun_loop.sh}
LOG=${LOG:-/mnt/workspace/tunnel_aliyun.log}
PIDFILE=${PIDFILE:-/mnt/workspace/tunnel_aliyun.pid}

# ⚠️ **私钥必须留在持久盘（2026-10-01 修）**。DSW 实例一重启就**换新容器**：
#    `/root`、`/usr`、pip 依赖全没了，只有 `/mnt/workspace`（和 `/mnt/data`）是 NAS 挂载、留得住。
#    从前用默认的 `~/.ssh/id_ed25519` —— 重启后密钥消失，ssh 就退回**问密码**；
#    而后台重连根本没有终端可以输密码，于是隧道再也起不来
#    （2026-10-01 实测踩到：日志里一句 `root@1.2.3.4's password:` 然后卡住）。
#    所以这里放两处：
#      · $KEY       持久盘上的**母本**（/mnt/workspace/.ssh/）—— 实例重启不丢
#      · $LOCAL_KEY 本地盘上的**工作拷贝**（~/.ssh/）—— ssh 实际用它。
#        为什么不让 ssh 直接用母本：`/mnt/workspace` 是 NAS 挂载，chmod 不一定生效，
#        而 ssh 见到"权限过松"的私钥会**直接拒绝使用**（UNPROTECTED PRIVATE KEY FILE）。
#        本地盘复制一份、chmod 600 一定生效，最稳。
KEY=${KEY:-/mnt/workspace/.ssh/id_ed25519}
LOCAL_KEY=${LOCAL_KEY:-$HOME/.ssh/id_ed25519}

LPORT=${LPORT:-8188}          # 实例上 ComfyUI 的端口
RPORT=${RPORT:-}              # 挂到阿里云上的端口（安全组要放行它）；留空 = 用记住的 / 18188
ALIYUN_PORT=${ALIYUN_PORT:-22}
BIND=${BIND:-0.0.0.0}         # 0.0.0.0=公网可访问（需要 GatewayPorts yes）
                              # 127.0.0.1=只有阿里云本机能连（更安全，但本机还得再转发一层）

COMFY_DIR=${COMFY_DIR:-/mnt/workspace/ComfyUI}
COMFY_LOG=${COMFY_LOG:-/mnt/workspace/comfyui.log}
WAIT_COMFY=${WAIT_COMFY:-480} # 等 ComfyUI 就绪的上限秒数（新容器补依赖要几分钟）
SKIP_COMFYUI=${SKIP_COMFYUI:-0}

# 跳板机 + 端口：参数 > 环境变量 > 上次记下来的文件（第 1 行主机、第 2 行端口）。
# 带参数来就顺手写进文件 —— 这样"第一次带、以后不带"是自然用法，不用记两条命令。
#
# ⚠️ **端口也要记住**：同一台跳板机可以同时挂好几台实例 —— 比如兄弟俩各有一台
#    ModelScope 实例，用 18188 / 18189 两个端口共用这台阿里云。各记各的端口，
#    谁也不用每次敲 `RPORT=18189`（2026-09-29 加）。
SAVED_HOST=$(sed -n '1p' "$JUMP_FILE" 2>/dev/null | tr -d '[:space:]')
SAVED_PORT=$(sed -n '2p' "$JUMP_FILE" 2>/dev/null | tr -d '[:space:]')

if [ -n "${1:-}" ]; then
  ALIYUN=$1
  RPORT=${2:-${RPORT:-${SAVED_PORT:-18188}}}
  printf '%s\n%s\n' "$ALIYUN" "$RPORT" > "$JUMP_FILE"
  echo "==> 已记住：跳板机 $ALIYUN，端口 $RPORT（下次直接 bash $0 就行）"
else
  ALIYUN=${ALIYUN:-$SAVED_HOST}
  RPORT=${RPORT:-${SAVED_PORT:-18188}}
fi
if [ -z "$ALIYUN" ]; then
  echo "❌ 还不知道跳板机是谁。第一次这样跑（把 IP 换成你阿里云的公网 IP）："
  echo "     bash $0 root@1.2.3.4              # 默认挂到 18188"
  echo "     bash $0 root@1.2.3.4 18189        # 同一台跳板机上的第二台实例用别的端口"
  echo "   它会记到 $JUMP_FILE，以后直接 bash $0 即可。"
  exit 1
fi

# ---------------------------------------------------------------- 零、密钥
mkdir -p "$(dirname "$KEY")" "$(dirname "$LOCAL_KEY")" 2>/dev/null
chmod 700 "$(dirname "$KEY")" "$(dirname "$LOCAL_KEY")" 2>/dev/null

# ① 本地盘有、持久盘没有 → 把本地那把"收编"进持久盘。
#    这样老用户（密钥一直在 ~/.ssh）不用重新配 authorized_keys，重启后也还在。
if [ ! -f "$KEY" ] && [ -f "$LOCAL_KEY" ]; then
  cp -p "$LOCAL_KEY" "$KEY" 2>/dev/null && cp -p "$LOCAL_KEY.pub" "$KEY.pub" 2>/dev/null
  echo "==> [0/2] 把已有的 SSH 密钥收进持久盘（$KEY）—— 以后实例重启不会再丢"
fi

# ② 两边都没有 → 生成一把，打印"可以整行粘进 authorized_keys"的那一行，然后退出
if [ ! -f "$KEY" ]; then
  echo "==> [0/2] 第一次用：生成 SSH 密钥"
  ssh-keygen -t ed25519 -N '' -f "$LOCAL_KEY" -q || { echo "❌ 生成密钥失败（$LOCAL_KEY）"; exit 1; }
  cp -p "$LOCAL_KEY" "$KEY" 2>/dev/null
  cp -p "$LOCAL_KEY.pub" "$KEY.pub" 2>/dev/null
  chmod 600 "$LOCAL_KEY" "$KEY" 2>/dev/null
  echo
  echo "    接下来做**一次**：把下面这一整行发给服务器主人，"
  echo "    让他追加到阿里云服务器的 /root/.ssh/authorized_keys ："
  echo
  echo "restrict,port-forwarding,permitlisten=\"$RPORT\",command=\"/bin/false\" $(cat "$LOCAL_KEY.pub")"
  echo
  echo "    （那一行前面的 restrict… 是**受限密钥**：只允许它转发 $RPORT 这一个端口，"
  echo "      拿不到 shell —— 别用 ssh-copy-id，那等于把你的 root 全权交出去）"
  echo
  echo "    加完再跑一次本脚本即可，以后都不用管。"
  exit 1
fi

# ③ 持久盘有、本地盘没有（实例重启过，本地盘被清空）→ 从持久盘恢复
if [ ! -f "$LOCAL_KEY" ]; then
  cp -p "$KEY" "$LOCAL_KEY" 2>/dev/null || { echo "❌ 从持久盘恢复密钥失败：$KEY"; exit 1; }
  cp -p "$KEY.pub" "$LOCAL_KEY.pub" 2>/dev/null
  echo "==> [0/2] 已从持久盘恢复 SSH 密钥（实例重启过，本地盘被清空了）"
fi
chmod 600 "$LOCAL_KEY" 2>/dev/null
chmod 600 "$KEY" 2>/dev/null

# ---------------------------------------------------------------- 一、挂隧道
# ⚠️ **顺序是「先起 ComfyUI、再挂隧道」（2026-10-02 改）**。
#    原来是反的（隧道先挂、ComfyUI 后起），结果中间有 3~5 分钟的**窗口期**：
#    隧道已经通了，但它转发的 8188 还没人监听 —— 这时候镜序「保存并测试」会报红、
#    点了出片会失败，而 `tunnel_aliyun.log` 里留下几行
#        connect_to 127.0.0.1 port 8188: failed.
#    看着像"隧道坏了"，其实只是 ComfyUI 还没起来（实测被这个误导过一次）。
#    改成先起 ComfyUI 之后，脚本最后打印「可以出片了」时是**真的可以出片**。
setup_tunnel() {
# 先做隧道：它只要十几秒，而 ComfyUI 可能要几分钟（补依赖）。
#
# 停掉旧的：用 PID 文件，**不要 pkill -f** —— 那个会自匹配把脚本自己杀掉
# （这个坑 docs/comfyui-tunnel-guide.md 的「坑清单」里记着）
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null
  sleep 1
fi
: > "$LOG"

# 断线自动重连：写成独立脚本，方便单独看/单独调，也免得引号套太多层。
#
# · ConnectTimeout=10：连不上要**10 秒内**暴露出来，别傻等 2 分钟还以为一切正常。
# · BatchMode=yes：**永远不要问密码**。后台进程没有终端，问了也没人答 ——
#   不如立刻失败，让下面的报错对照表把它翻译成人话。
# · IdentitiesOnly=yes：只用我们指定的这把钥匙，别去翻 agent 里的别的钥匙。
cat > "$LOOP" <<EOF
#!/bin/bash
# 由 tunnel_aliyun.sh 生成，改它没用 —— 改上面那个脚本
# 地址：$ALIYUN  端口：$BIND:$RPORT → 127.0.0.1:$LPORT  (ssh 端口 $ALIYUN_PORT)
# 私钥：$LOCAL_KEY（母本在 $KEY）
while true; do
  echo "[\$(date '+%F %T')] 连接 $ALIYUN ..."
  ssh -i "$LOCAL_KEY" -o IdentitiesOnly=yes -o BatchMode=yes \\
      -o StrictHostKeyChecking=no \\
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
echo "==> [2/2] 挂隧道 → $ALIYUN（进程 PID=$(cat "$PIDFILE")，日志 $LOG）"
for _ in 1 2 3 4 5 6; do
  sleep 3
  if grep -q "断了" "$LOG"; then break; fi
done

if grep -q "断了" "$LOG" || ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "❌ 隧道没连上。真实报错是："
  tail -4 "$LOG" | sed 's/^/     /'
  echo "   对照表："
  echo "   Permission denied"
  echo "     → 这把密钥还没被服务器接受：头一次跑要把它加到阿里云（上一步会打印完整的一行）；"
  echo "       或者 authorized_keys 里的 permitlisten 端口和这里的 $RPORT 对不上"
  echo "   Host key verification failed"
  echo "     → 服务器换了，或 known_hosts 脏了：ssh-keygen -R ${ALIYUN#*@}"
  echo "   Connection timed out / Connection refused"
  echo "     → 跳板机地址或 ALIYUN_PORT 不对；阿里云安全组也要放行它的 22 端口"
  echo "   remote port forwarding failed / cannot listen"
  echo "     → 两种可能：① 阿里云上没开 GatewayPorts yes（见本脚本头部「一次性准备」①）；"
  echo "       ② 端口 $RPORT 已经被占 —— 同一台跳板机上每台实例必须用**不同端口**"
  echo "          （受限密钥的 permitlisten 也要与它一致）"
  echo "   想手工看一眼密钥通不通： ssh -i $LOCAL_KEY -o BatchMode=yes $ALIYUN 'echo 免密OK'"
  exit 1
fi

IP=${ALIYUN#*@}
echo "     ✅ 隧道好了（地址固定，不会再变）"
if [ "$BIND" = "0.0.0.0" ]; then
  echo "        镜序「服务配置 → ComfyUI 地址」= http://$IP:$RPORT"
else
  echo "        ⚠️ $BIND 绑定只有阿里云本机能连，你本机还要再开一条本地转发："
  echo "           ssh -N -L 8188:127.0.0.1:$RPORT $ALIYUN"
  echo "           然后镜序填 http://127.0.0.1:8188（公网上完全看不到，最安全）"
fi
echo "        ⚠️ 安全组要放行 TCP $RPORT，但**别对全世界开放** —— ComfyUI 没有密码。"
}

# ---------------------------------------------------------------- 二、拉起 ComfyUI
# 依赖检查/安装那一段与原来那个 start_comfyui.sh 是同一套（多镜像轮换）。
# 故意**不去调**那个脚本：它每次都会顺手起一个 pinggy 隧道、并打印两个 60 分钟就失效的
# 地址 —— 那正是让人把错地址抄进「服务配置」的源头（2026-09-29 实测被问过一次）。
ensure_comfyui() {
  if [ "$SKIP_COMFYUI" = "1" ]; then
    echo "==> [1/2] SKIP_COMFYUI=1，跳过 ComfyUI"
    return 0
  fi
  echo "==> [1/2] 拉起 ComfyUI（$COMFY_DIR）"
  if [ ! -d "$COMFY_DIR" ]; then
    echo "     ⚠️ 没找到 $COMFY_DIR —— 跳过（隧道已经好了，ComfyUI 你自己起）"
    return 0
  fi
  cd "$COMFY_DIR" || return 0

  # ① 依赖：换新容器会全丢，缺了就地补。
  #    ⚠️ **绝对不要 pip install torch** —— 实例自带的是 ROCm 版，
  #    装 torchvision/torchaudio 会连带把 torch 换成 CUDA 版，所以先把它从
  #    requirements 里滤掉。这是 docs/comfyui-tunnel-guide.md 坑清单的第 3 条。
  if ! python3 -c "import sqlalchemy, av, kornia, spandrel, alembic, comfyui_frontend_package, comfyui_workflow_templates, soundfile" 2>/dev/null; then
    echo "     [deps] 缺依赖，正在补装（多镜像轮换，约 3~5 分钟）…"
    grep -vE "^torch( |$|==|>=|<=)" requirements.txt > /tmp/req_avm.txt
    for IDX in https://mirrors.cloud.tencent.com/pypi/simple https://mirrors.aliyun.com/pypi/simple/ https://pypi.org/simple; do
      echo "     [deps] 试 $IDX"
      pip3 install -q -r /tmp/req_avm.txt -i "$IDX" > /tmp/pip_avm.log 2>&1
      python3 -c "import sqlalchemy, av, comfyui_frontend_package" 2>/dev/null && { echo "     [deps] OK"; break; }
    done
    python3 -c "import comfyui_workflow_templates" 2>/dev/null \
      || pip3 install -q comfyui-workflow-templates -i https://mirrors.cloud.tencent.com/pypi/simple
    python3 -c "import soundfile" 2>/dev/null \
      || pip3 install -q soundfile -i https://mirrors.aliyun.com/pypi/simple/
  fi

  # ② 已经在跑就不动它（幂等 —— 重复跑这条命令不会打断正在生成的活儿）
  if curl -s -m 2 "http://127.0.0.1:$LPORT/system_stats" > /dev/null 2>&1; then
    echo "     ✅ 已经在跑（:$LPORT），没动它"
    return 0
  fi

  echo "     [start] 启动中…（日志：$COMFY_LOG）"
  nohup python3 main.py --listen 0.0.0.0 --port "$LPORT" > "$COMFY_LOG" 2>&1 &

  # ③ 等它就绪 —— 慢是正常的（刚补完依赖，首次导入要一会儿）
  waited=0
  while [ "$waited" -lt "$WAIT_COMFY" ]; do
    sleep 3
    waited=$((waited + 3))
    if curl -s -m 2 "http://127.0.0.1:$LPORT/system_stats" > /dev/null 2>&1; then
      echo "     ✅ 就绪（等了 ${waited}s）"
      return 0
    fi
    [ $((waited % 30)) = 0 ] && echo "          …等了 ${waited}s"
  done
  echo "     ⚠️ 等了 ${WAIT_COMFY}s 还没就绪 —— 看 $COMFY_LOG（依赖没装全 / 端口被占）"
  echo "        下面照样会把隧道挂上；等 ComfyUI 起来镜序直接就能用。"
  return 0
}

# ⚠️ 顺序：**先 ComfyUI（慢，3~5 分钟），再隧道（快，十几秒）**。
#    反过来的话，脚本会说"可以出片了"而 ComfyUI 其实还没起（见 setup_tunnel 上面的注释）。
ensure_comfyui
setup_tunnel

echo
echo "==================== 可以出片了 ===================="
if [ "$BIND" = "0.0.0.0" ]; then
  echo "镜序「服务配置 → ComfyUI 地址」： http://$IP:$RPORT"
  echo "本机自测： curl -s http://$IP:$RPORT/system_stats"
fi
echo "实例重启后，重跑这一条命令即可： bash $0"
echo "=================================================="

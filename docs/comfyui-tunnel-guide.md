# ModelScope 实例 ComfyUI 内网穿透指南

> 适用环境：ModelScope PAI-DSW Notebook（本机实例 `dsw-2173775`，杭州区，AMD GPU 192G）
> 目标：把实例里跑在 8188 端口的 ComfyUI 暴露出去——给浏览器用，或给你本地
> `ai_video_multiagent` 流水线直连。
> 最后验证时间：2026-09-12

---

## 一分钟快速启动（实例重启后照抄这段）

实例每次冷启动后，SSH/终端里跑：

```bash
bash /mnt/workspace/start_comfyui.sh
```

这个脚本做三件事：拉起 ComfyUI（8188）→ 建 pinggy 隧道 → 把公网地址写进
`/mnt/workspace/tunnel_url.txt`。跑完 `cat /mnt/workspace/tunnel_url.txt` 拿地址。

- 浏览器用（最稳，无需隧道）：
  `https://dsw-gateway-cn-hangzhou.data.aliyun.com/dsw-<实例ID>/proxy/8188/`
  **实例 ID 每次重启都会变**（如 `dsw-2173775` → `dsw-2174679`），看 JupyterLab
  地址栏里 `/dsw-*/lab` 的那段就是当前 ID，替换进去即可。
- 流水线用：拿 tunnel_url.txt 里的 pinggy 地址，填到本地 `.env` 的 `COMFYUI_URL`。
- 验证通了没有：
  ```bash
  curl -s -m 5 http://127.0.0.1:8188/system_stats    # 实例内
  curl -s -m 10 https://<隧道地址>/system_stats       # 本机
  ```

---

## 背景：为什么需要穿透

DSW 实例没有公网 IP，外部无法直接访问实例内任意端口。实例自带的 JupyterLab 走
`https://dsw-gateway-cn-hangzhou.data.aliyun.com/dsw-2173775/...` 这个网关，网关支持
`/proxy/<端口>/` 子路径转发到实例内对应端口——**但所有网关请求都要求阿里云登录态**。

```
你的浏览器 ──(带登录cookie)──> DSW网关 ──> 实例:8888 (Jupyter)
你的浏览器 ──(带登录cookie)──> DSW网关 ──> 实例:8188 (ComfyUI)   ✅ /proxy/8188/
本地Python  ──(无cookie)─────> DSW网关 ──> 302 跳阿里云登录页     ❌
```

所以分两种需求：

| 需求 | 方案 |
|---|---|
| 人用浏览器操作 ComfyUI | 直接用网关 `/proxy/8188/`，零配置，永远可用 |
| 程序（本地流水线）直连 | 必须建隧道，拿到无鉴权公网地址 |

---

## 方案实测对比（为什么这个行、那个不行）

以下全部在实例内实测过（2026-09-12），结论按"能不能给本地程序用"分：

### ❌ DSW 网关代理 `/proxy/<端口>/`——只够浏览器用
带浏览器 cookie 时完美（连 ComfyUI 的 WebSocket 都正常），但**无 cookie 一律 302**
跳 `account.aliyun.com` 登录页。把登录 cookie 抠出来塞给 requests 既脆弱又过期快，
不建议。→ 结论：浏览器专用，见上表。

### ❌ cloudflared 快速隧道——阿里云机房连不上 Cloudflare
```bash
# 二进制下载本身要靠 gh-proxy 加速（GitHub 直连极慢，见后文"坑"）
curl -sLf -o /tmp/cloudflared https://gh-proxy.com/https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
/tmp/cfd1 tunnel --url http://127.0.0.1:8188 --no-autoupdate
```
启动后卡在 `Requesting new quick Tunnel on trycloudflare.com...`，最后报：
```
failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel":
context deadline exceeded (Client.Timeout exceeded while awaiting headers)
```
原因：建隧道要访问 `api.trycloudflare.com`，阿里云出海到 Cloudflare 这条 API 路径
直接超时。**放弃，不要浪费时间重试。**

### ❌ localhost.run SSH 反向隧道——连上了但不给地址
`ssh -R 80:127.0.0.1:8188 nokey@localhost.run` 能连上（22 端口出网是通的），
欢迎语正常，但**不分配 URL**——现行策略匿名隧道必须绑定 SSH key，实测带了
`-i key` 依然只有 connection id 没有地址。放弃。

### ✅ pinggy.io SSH 反向隧道（走 443 端口）——最终方案
```bash
ssh -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -p 443 -R0:127.0.0.1:8188 \
    free.pinggy.io
```
成功原因：① SSH 走 **443 端口**（阿里云出网对 443 最宽容，22 端口虽然实测也通，
但 pinggy 的 443 是 TLS 包装，最稳）；② **免注册免 key**，连接即分配两个公网
HTTPS 地址（`.run.pinggy-free.link` 和 `.free.pinggy.net` 两种后缀，任选）；
③ 分配的域名国内访问无障碍。

**免费档限制（要知道）：**
- 每条隧道 **60 分钟**到期断开，重连后**域名会变**（随机词 + 出口 IP 组成）；
- 带宽和连接数有限制，个人开发够用；
- 所以流水线侧 `COMFYUI_URL` 要支持"从 tunnel_url.txt 重读"或每次启动时更新。

### 各层寿命一览（能维持多久）

| 层 | 寿命 | 到期后怎么办 |
|---|---|---|
| pinggy 免费隧道 | **单条 60 分钟**，重连后域名改变 | 重跑 `start_comfyui.sh`（幂等，只换隧道） |
| 浏览器走的网关代理 | 无 60 分钟限制，实例活着+登录态有效即可 | 无需处理 |
| ComfyUI 进程 | 跟实例同生共死 | 实例重启后由 start_comfyui.sh 拉起 |
| 实例本身 | 单次约 8 小时 + 1 小时无操作自动关机 | ModelScope 页面重新启动实例，重跑脚本 |
| `/mnt/workspace` 数据（模型/脚本） | **持久**，实例重启不丢 | 无需处理；`/tmp` 和运行中进程会没 |

### 其他排除项（不踩坑说明）
- GitHub release 直连下载：`objects.githubusercontent.com` 从实例出去只有
  几百 B/s（127KB 下了 6 分钟）。**凡是 GitHub 大文件，一律加
  `https://gh-proxy.com/` 前缀**，实测 40MB 几秒下完。
- PyPI 上没有 `cloudflared` 这个包（别信记忆，`from versions: none` 是真的）。
- frp/nps：需要自己有公网服务器，没有就不讨论。
- Tailscale/ZeroTier：可用但要两端装客户端+注册，杀鸡用牛刀。

---

## start_comfyui.sh（已放在实例 `/mnt/workspace/`）

```bash
#!/bin/bash
# 一键启动 v3：依赖自检(多镜像轮换) + ComfyUI + pinggy 隧道(PID文件管理)
cd /mnt/workspace/ComfyUI
if ! python3 -c "import sqlalchemy, av, kornia, spandrel, alembic, comfyui_frontend_package, comfyui_workflow_templates, soundfile" 2>/dev/null; then
  echo "[deps] missing, installing with mirror rotation..."
  grep -vE "^torch( |$|==|>=|<=)" requirements.txt > /tmp/req.txt
  for IDX in https://mirrors.cloud.tencent.com/pypi/simple https://mirrors.aliyun.com/pypi/simple/ https://pypi.org/simple; do
    echo "[deps] trying $IDX"
    pip3 install -r /tmp/req.txt -i "$IDX" > /tmp/pip_current.log 2>&1
    if python3 -c "import sqlalchemy, av, comfyui_frontend_package" 2>/dev/null; then
      echo "[deps] OK via $IDX"
      break
    fi
    echo "[deps] $IDX failed, next..."
  done
  pip3 install soundfile -i https://mirrors.aliyun.com/pypi/simple/ > /tmp/pip_sf.log 2>&1
fi
if ! curl -s -m 2 http://127.0.0.1:8188/system_stats > /dev/null; then
  nohup python3 main.py --listen 0.0.0.0 --port 8188 > /mnt/workspace/comfyui.log 2>&1 &
  echo "ComfyUI starting (pid $!)"
fi
if [ -f /mnt/workspace/pinggy.pid ]; then kill "$(cat /mnt/workspace/pinggy.pid)" 2>/dev/null; fi
nohup ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -p 443 -R0:127.0.0.1:8188 free.pinggy.io > /mnt/workspace/pinggy.log 2>&1 &
echo $! > /mnt/workspace/pinggy.pid
sleep 12
grep -oE "https://[a-zA-Z0-9.-]+\.(link|net)" /mnt/workspace/pinggy.log | head -2 | tee /mnt/workspace/tunnel_url.txt
echo "Browser: 把 JupyterLab 地址栏 /dsw-*/lab 的实例ID 填入 https://dsw-gateway-cn-hangzhou.data.aliyun.com/dsw-<实例ID>/proxy/8188/"
```

要点：
- 先探测 8188 是否已有服务，**幂等**，重复跑不会起两个 ComfyUI；
- `pkill -f "pinggy[.]io"` 清理旧隧道。注意 `[.]` 写法是故意的，见下文"坑"；
- 隧道日志在 `/mnt/workspace/pinggy.log`，URL 同时落在 `tunnel_url.txt`。

---

## 坑清单（都是实踩过的）

1. **`pkill -f` 自匹配自杀**：`pkill -f "http.server 8188"` 会匹配到脚本自己
   （`bash -c` 的完整命令行里就包含这个字符串），导致整段脚本静默死亡、无任何
   输出。写法必须让模式匹配不到自身：`pkill -f "http[.]server 8188"`——正则
   `[.]` 匹配真实的 `.`，但脚本文本里是字面的 `[.]`，不会命中自己。
2. **pip 源选择**（装 ComfyUI 依赖时）：
   - 阿里云镜像：包最全但**新包同步慢**（缺过 `comfyui-workflow-templates==0.11.59`），
     且偶发单文件龟速（100MB 文件 14.8 kB/s）；
   - 清华镜像：快，但会 403（soundfile 遇到过）；
   - **腾讯镜像 `https://mirrors.cloud.tencent.com/pypi/simple`**：那个 100MB 的
     模板包从这里秒下。卡住就换源，别等。
   - 官方 `pypi.org`：索引能通，`files.pythonhosted.org` 大文件走 Fastly，国内
     抽风，只当兜底。
3. **绝对不要 `pip install torch`**：实例自带 `torch 2.12.0+rocm`（AMD 卡就用它），
   pip 装 torchvision/torchaudio 时会连带把 torch 换成 CUDA 版。装 ComfyUI 依赖要
   先过滤：`grep -vE "^torch( |$|==|>=|<=" requirements.txt`。
4. **modelscope CLI 下载会在 `--local_dir` 下重建仓库子目录**：下
   `Comfy-Org/MiniMax-H3` 的 `diffusion_models/xxx.safetensors` 到
   `--local_dir models/` 时，实际落在 `models/diffusion_models/xxx.safetensors`
   （正好和 ComfyUI 目录约定同名，直接对上）。若 `--local_dir` 指到了子目录里，
   会多套一层，ComfyUI 递归扫描也能认，但最好 `mv` 拉平。
5. **实例会休眠**：1 小时无操作自动关机 + 单次实例约 8 小时时长上限。
   `/mnt/workspace` 是 NAS，重启不丢数据；`/tmp` 和运行中的进程会没。
   重启后只需重跑 `start_comfyui.sh`。
6. **pinggy 地址漂移**：60 分钟重连一次，域名就换。流水线里别把地址写死在代码，
   读 `.env` 或 tunnel_url.txt；嫌麻烦就写个 cron 每 50 分钟重启隧道并把新地址
   同步出来。
7. **实例重启 = 换新容器**：实例 ID 会变（`dsw-2173775` → `dsw-2174679`），
   `/mnt/workspace` 的模型、脚本都在，**但系统盘上 pip 装的依赖全部消失**
   （H3 上线当天实测：新实例 sqlalchemy 都没有）。v3 启动脚本已内置依赖自检 +
   多镜像轮换，开机跑一遍即可自愈，约 3~5 分钟。
8. **pip 镜像可靠性实测**：腾讯镜像（快、稳、新包同步快）> 阿里云（能用但慢、
   偶发缺新包）> 清华（会对容器 IP 403 限流，直接废掉整次安装）> 官方 pypi
   （索引通、大文件龟速）。脚本按这个顺序轮换，装完用 import 自检。
9. **pkill 不仅自匹配，还会误杀调用者**：即便脚本是文件（自身命令行不含脚本
   内容），如果外层调用方（比如 `bash -c` 包一层）的命令行里恰好包含模式串，
   一样被 SIGTERM。后台进程统一用 PID 文件管理：启动时 `echo $! > xxx.pid`，
   清理时 `kill "$(cat xxx.pid)"`，不用 pkill -f。

---

## 前端（分镜工作台）已集成

分镜工作台的每个分镜多了「生成视频」按钮（表格和画廊视图都有）：

- 点击后后端起后台任务：上传该镜首帧图 + 提交工作流（自动带上前端里编辑的
  视频提示词和时长）→ 轮询 → 完成后视频出现在图片正下方，直接可播。
- 视频文件落在 `outputs/{task_id}/videos/`，由 server 的 `/files` 静态挂载播放；
  `video_path` 会写回 shots.json。
- 前置条件：隧道地址在前端右上角「视频服务」面板里填（保存到 comfyui_config.json，
  自带连通性测试）；`.env` 里的 `COMFYUI_URL` 只是命令行兜底。改完不需要重启
  uvicorn——地址是每次生成任务时实时读取的。
- 画质/速度旋钮同样走 `H3_MEGAPIXELS` / `H3_STEPS` / `H3_LORA` 环境变量。
- 命令行等价物：`python video_agent.py outputs/<task_id>`（批量、断点续跑，
  地址读取顺序：`--url` 参数 > 前端面板保存的 comfyui_config.json > COMFYUI_URL）。

## 换平台租显卡部署

不想用 ModelScope 时，租任何 Linux GPU 机器（AutoDL 等）：把项目根目录的
`deploy_comfyui.sh` 传到租的机器上执行 `bash deploy_comfyui.sh`，自动完成
克隆 / 依赖（多镜像轮换）/ 78GB 模型下载 / 启动。要点：

- **不含内网穿透**——AutoDL 这类平台自带端口映射网址，直接填前端「视频服务」面板；
  有公网 IP 的填 `http://IP:8188`。只有完全没有公网入口的平台才需要 pinggy。
- 海外机器加 `MODEL_SOURCE=hf` 走 HuggingFace 下载；国内默认 ModelScope。
- 脚本故意不碰 torch（版本冲突是租卡环境最大的坑），选平台镜像时带 PyTorch/CUDA 即可。
- 换机器 = 78GB 模型重新下载，代码和工作流零改动。
- 本机显卡也能跑：ComfyUI 启动后前面板填 `http://127.0.0.1:8188`。

## 方案 B：把流水线搬进实例（彻底绕开穿透）

如果不想受隧道 60 分钟限制，另一个思路是把 `ai_video_multiagent` 也跑在实例里，
流水线用 `http://127.0.0.1:8188` 直连 ComfyUI，你只通过网关代理在浏览器里用
Web UI（`/proxy/<端口>/`，已验证连 WebSocket 都正常）。代价是代码要同步到
`/mnt/workspace`（git clone 或上传），DeepSeek key 也要配一份到实例。
适合长时间批量生成；偶尔玩一玩用方案 A 就够。

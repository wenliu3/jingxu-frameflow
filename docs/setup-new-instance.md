# 从零装一台 ModelScope 实例，接上「镜序」

> 适用：**新开的** ModelScope PAI-DSW 实例（AMD 卡，持久盘 100GB，什么环境都没有），
> 想让它接上「镜序」出片。也适用于旧实例彻底重来。
>
> 全程 **40~70 分钟**，其中 30~50 分钟是下模型（魔搭内网很快，挂着就行）。
> 最后验证：**2026-10-01**（这台就是照这份跑出来的）

---

## 0. 先做一个决定：下哪一套权重

⚠️ **100GB 的持久盘装不下两套**（fl2va 约 72GB + Ref2VA 约 36GB = 108GB）。
deploy 脚本现在会**先算盘够不够**，不够就早退并让你选，不会写到一半炸。

| 方案 | 要下 | 占用 | 镜序里「视频工作流」填 | 适合 |
| --- | --- | --- | --- | --- |
| **Ref2VA**（推荐） | 5 个文件 | **约 41GB** | `ref2va` | 人物参考图锁身份、构图交给提示词 —— **镜序的提示词体系就是围绕它做的** |
| I2V / 首尾帧 | 6 个文件 | 约 72GB | `i2v` | 每镜先出一张首帧图，构图被图钉死。老路子，更"稳"但要多做一步 |

**推荐 Ref2VA**：省一半空间、不用先出分镜首帧图、跨镜头人物一致性靠参考图（而不是靠文字描述）。

> ⚠️ **两个 VAE 是两条路共用的**（`minimax_h3_video_vae_fp16` + `minimax_h3_audio_vae_fp32`，
> 约 6GB）—— 无论选哪一套都别省，删了出不了片。

---

## 1. 准备：把两个脚本传上去

从项目根拿走这两个文件：

| 文件 | 干什么用 |
| --- | --- |
| [deploy_comfyui_ms.sh](../deploy_comfyui_ms.sh) | **一次性**：装 ComfyUI 代码 + 依赖 + 下模型 |
| [tunnel_aliyun.sh](../tunnel_aliyun.sh) | **每次重启**：拉起 ComfyUI + 挂隧道（一条命令） |

在 JupyterLab 左侧文件区点 **↑ 上传**，两个都传到 `/mnt/workspace/`。传完确认：

```bash
ls -l /mnt/workspace/deploy_comfyui_ms.sh /mnt/workspace/tunnel_aliyun.sh
```

---

## 2. 一键装（在实例终端里跑）

JupyterLab：**File → New → Terminal**，然后：

```bash
cd /mnt/workspace
WANT_FL2VA=0 bash deploy_comfyui_ms.sh          # 只下 Ref2VA（约 41GB，推荐）
# 想两套都要（需要大盘）：bash deploy_comfyui_ms.sh
```

它按 6 步走，全程有输出：

| 步骤 | 做什么 | 大概耗时 |
| --- | --- | --- |
| 1/6 | 检查 torch（实例自带 ROCm 版，**脚本不碰它**） | 秒 |
| 2/6 | 克隆 ComfyUI（没装过才克隆） | 1~3 分钟 |
| 3/6 | 补 Python 依赖（多镜像轮换） | 3~5 分钟 |
| 4/6 | **下模型**（带盘空间预检，已存在的跳过） | **20~40 分钟** |
| 5/6 | 启动 ComfyUI（0.0.0.0:8188） | 1 分钟 |
| 6/6 | 隧道（有阿里云跳板就用它，没有才退回 pinggy） | 十几秒 |

**跑完自检一下**（这一步很关键，能提前发现"模型没下全 / 节点升级了"）：

```bash
curl -s http://127.0.0.1:8188/system_stats | head -c 120     # 有 JSON 就是起来了
```

> 💡 盘不够的话，第 4 步会**早退**并打印两条建议命令（`WANT_FL2VA=0` / `WANT_REF2VA=0`），
> 不会下到一半把盘写满、把实例搞坏。

---

## 3. 配隧道（用跳板机，地址固定不过期）

### 3a. 在**阿里云服务器**上（做一次）

```bash
# ① 允许把端口挂到公网（默认只允许挂到 127.0.0.1）
grep -q "^GatewayPorts" /etc/ssh/sshd_config || echo "GatewayPorts yes" | sudo tee -a /etc/ssh/sshd_config
sudo systemctl restart sshd || sudo systemctl restart ssh

# ② 控制台 → 安全组 → 入方向 → 放行端口（**每台实例一个端口**，都别忘）
#     实例 A → 18188    实例 B → 18189
```

**③ 加这个实例的公钥**（第 3b 步会打印出来发给你）。⚠️ **一定用下面这行"受限密钥"**，
别用 `ssh-copy-id` —— 那等于把你的 **root 全权**交出去：

```
restrict,port-forwarding,permitlisten="18189",command="/bin/false" ssh-ed25519 AAAAC3... 某人的实例
```

这行是**实测过**的（OpenSSH 8.0）：

| 行为 | 结果 |
| --- | --- |
| 转发 `0.0.0.0:18189` | ✅ 允许 |
| 转发别的端口（`18190`） | ✅ 被拒 |
| `ssh 你服务器 任意命令` / 登进去 | ✅ 被堵死 |

> ⚠️ `restrict` **单独用不够**：它只禁 pty/agent/X11/转发，**不禁 `ssh 主机 命令`**
> （那种连接不申请 pty）。必须补 `command="/bin/false"`。而它不影响端口转发
> （`ssh -R` 走全局请求，不开 session 通道）。

### 3b. 在**实例**上

**密钥不用你手工生成** —— 第一次跑隧道脚本时它会自己生成，并把「可以整行粘进
`authorized_keys`」的那一行打印出来：

```bash
# ① 第一次跑：生成密钥 + 打印那一行（不会起隧道，它让你先把密钥配好）
bash /mnt/workspace/tunnel_aliyun.sh root@服务器IP 18189
```

它会打印类似：

```
==> [0/2] 第一次用：生成 SSH 密钥

    接下来做**一次**：把下面这一整行发给服务器主人，
    让他追加到阿里云服务器的 /root/.ssh/authorized_keys ：

restrict,port-forwarding,permitlisten="18189",command="/bin/false" ssh-ed25519 AAAAC3... root@dsw-xxxx

    加完再跑一次本脚本即可，以后都不用管。
```

把那一行发给服务器主人加进去（见 3a 的第 ③ 步），然后**再跑一次**：

```bash
# ② 再跑一次：这次真的挂隧道（也把 ComfyUI 拉起来）
bash /mnt/workspace/tunnel_aliyun.sh
```

看到这段就成了：

```
==> [1/2] 挂隧道 → root@服务器IP
     ✅ 隧道好了（地址固定，不会再变）
        镜序「服务配置 → ComfyUI 地址」= http://服务器IP:18189
==> [2/2] 拉起 ComfyUI（/mnt/workspace/ComfyUI）
     ✅ 已经在跑（:8188），没动它

==================== 可以出片了 ====================
```

> ⚠️ **为什么密钥要专门放持久盘**（这一点踩过坑）：DSW 实例一重启就**换新容器**，
> `/root`、`/usr`、pip 依赖**全都没了**，只有 `/mnt/workspace`（和 `/mnt/data`）是挂载、
> 留得住。密钥要是放在默认的 `~/.ssh/`，重启后就消失 → ssh 退回**问密码** →
> 而后台重连根本没有终端可以输密码 → **隧道再也起不来**（现象：日志里一句
> `root@你的IP's password:` 然后卡住，同时 ComfyUI 那边一切正常）。
> 所以脚本把**母本**放 `/mnt/workspace/.ssh/`，每次跑再从它恢复一份到 `~/.ssh/` 给 ssh 用
> （本地盘权限一定对，NAS 上 `chmod` 不一定生效，而 ssh 见到"权限过松"的私钥会拒绝使用）。

---

## 4. 让镜序连上

镜序右上角 **「服务配置」**：

| 项 | 填 |
| --- | --- |
| 视频后端 | ComfyUI |
| ComfyUI 地址 | `http://服务器IP:18189` ← 这台实例分到的端口 |
| 视频工作流 | `ref2va`（第 0 步选的那套，别填错） |
| 加速 LoRA | `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` |
| 采样步数 | `4`（配 4 步 LoRA，填 8 会出伪影） |

填完点**保存并测试**。

---

## 5. 出片前自检 + 排错

**每次换环境/升级 ComfyUI 之后，先跑这条**（只读，不提交、不烧 GPU）：

```bash
python dev/tools/check_comfyui_models.py
```

它一次性告诉你三件事：**节点缺不缺、模型文件缺不缺、输入 schema 对不对得上**。

| 报错 | 意思 | 怎么办 |
| --- | --- | --- |
| `找不到这些模型文件：unet_name = …` | 模型没下全 | 照报错里给的 `modelscope download` 命令补 |
| `ComfyUI 拒绝了工作流（HTTP 400）… Required input is missing: xxx` | 节点升级了、工作流没跟上 | 把报错发出来；工作流模板需要补那个字段 |
| `连不上 ComfyUI` | 隧道断了 / 实例关机了 | 实例上重跑 `bash /mnt/workspace/tunnel_aliyun.sh` |
| `remote port forwarding failed for listen port 18188` | 端口被占了 | 换一个端口（`… 18189`），安全组也要放行 |
| `Permission denied` | 免密没配好 | 在实例上手敲 `ssh root@服务器IP` 确认不问密码 |

---

## 6. 以后每次重启：**就一条命令**

```bash
bash /mnt/workspace/tunnel_aliyun.sh
```

它 = 挂隧道 + 拉起 ComfyUI（缺依赖会自动补）。**镜序那边的地址不用改**（跳板的地址是固定的）。

实例本身：DSW 单次约 8 小时 + 1 小时无操作自动关机，关机后重跑上面那条即可。
`/mnt/workspace` 是持久的，模型和脚本不会丢。

---

## 附：这套东西分别在哪

| 东西 | 在哪 | 会不会丢 |
| --- | --- | --- |
| ComfyUI 代码 + 模型 | 实例 `/mnt/workspace/ComfyUI` | 持久，重启不丢 |
| 两个脚本 | 实例 `/mnt/workspace/` | 持久 |
| 记下来的跳板机/端口 | 实例 `/mnt/workspace/aliyun_jump.txt` | 持久（第 1 行主机、第 2 行端口） |
| 隧道日志 | 实例 `/mnt/workspace/tunnel_aliyun.log` | — |
| 隧道端口 | **阿里云服务器**上监听（每实例一个） | 实例一没就断，重跑脚本恢复 |
| 免密密钥 | 母本在实例 `/mnt/workspace/.ssh/`（持久）；工作拷贝在 `~/.ssh/`；公钥在阿里云 `authorized_keys` | 持久 ✅（这正是 2026-10-01 那次故障的修法） |

相关文档：[隧道指南](comfyui-tunnel-guide.md) · [Ref2VA 接入指引](REF2VA.md)

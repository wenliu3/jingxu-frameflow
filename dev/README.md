# dev/ —— 开发期验证脚本

每加一条功能，就留一个能重跑的验收脚本，都收在这个目录。
**产品运行不需要这里任何东西**，删掉不影响功能；但重跑它们能快速回归。

## 约定

- **所有脚本都在项目根目录执行**（脚本内的相对路径按项目根解析，如 `docs/_shots`、`outputs/`）。
- 后端先起：`python -m uvicorn server.app:app --host 127.0.0.1 --port 8000`
- UI / E2E 脚本默认打 `http://127.0.0.1:8000`（后端同源伺服 `web/dist` 生产包）；
  要打 dev server 时加前缀：`E2E_URL=http://127.0.0.1:5173/`。
- 默认**不烧额度**：会真调模型的步骤一律 SKIP，`E2E_LIVE=1` 才跑。
- 截图统一写到 `docs/_shots/`（已加入 .gitignore）。

## dev/api/ —— 后端接口验证（Python）

| 脚本 | 验什么 |
| --- | --- |
| `_smoke_test_blocks.py` | 分块流程冒烟：mock 掉 LLM 与图像/视频 provider，验数据闭环与接口行为 |
| `_verify_ai_memory.py` | 「AI 生成」弹窗记忆 `ai_last`：五个入口各写自己那槽、上传/自动配音不写、落盘、重生成覆盖、非法比例归一化 |
| `_verify_history.py` | 生成历史：写图前快照、切回某一版、只留最近 10 版、路径穿越防护 |
| `_verify_other_image.py` | 「其他图片」AI 生成的接口校验（kind 放行/黑名单、reroll 保护） |
| `_verify_optimize_prompt.py` | 「让 AI 帮写」：中文进、中文出 |
| `_verify_portrait_prompt.py` | 角色定妆照提示词链路（零外部调用） |
| `_verify_purge_errors.py` | 「彻底删除」失败时能给出可读的错误 |
| `_verify_r2v_workflow.py` | Ref2VA 多参考图工作流接线（全程离线，不碰 ComfyUI） |
| `_verify_segment_prompt.py` | 分段视频提示词审计（`[Shot N]` 标记、规则文件运行时读取） |
| `_verify_segment_record.py` | 生成记录边车文件：这段用了哪些素材 / 哪句提示词 / 什么参数 |
| `_verify_trash.py` | 回收站：软删除 → 恢复 → 彻底删除 |
| `_verify_voice.py` | AI 音色挑选与合成（含 `_verified_voice_id` 回退逻辑单测） |

## dev/ui/ —— 界面验证与截图（Node + puppeteer-core）

| 脚本 | 验什么 |
| --- | --- |
| `_verify_ai_memory_ui.mjs` | 「AI 生成」弹窗回填上次的描述/比例/性别（改写接口响应注入 `ai_last`，不烧额度） |
| `_verify_framepick.mjs` | 首尾帧选择器：可挑场景/其他图片，人物与道具的图选不了 |
| `_verify_history_ui.mjs` | 生成历史弹窗 + 重生成后就地换图（`?v=` 缓存击穿） |
| `_verify_library.mjs` | 「我的作品」页顶部区块（含反向断言：撤掉的东西别长回来） |
| `_verify_other_image_ui.mjs` | 「其他图片」的 AI 生成入口 |
| `_verify_pick_ui.mjs` | 素材勾选交互（卡片级点击与小圆圈都已撤掉） |
| `_verify_prompt_ui.mjs` | 「让 AI 帮写」按钮接线（只调一次、直接写回输入框） |
| `_verify_promptout.mjs` | 编排结果预览：界面只出现人话，机器文字不许进 DOM |
| `_verify_records.mjs` | 「生成记录」tab（数据源为后端磁盘产物 `segments/`） |
| `_verify_sections.mjs` | 01/02 区块分布（新建作品页 vs 工作台两个 tab） |
| `_verify_settings.mjs` | 服务设置：「示例」按钮已撤、视频工作流下拉可保存 |
| `_verify_trash_ui.mjs` | 回收站界面（拦截 `/api/trash*` 回罐头数据） |
| `_verify_voice_ui.mjs` | 角色音频的 AI 生成入口 |
| `_shots.mjs` | 截图：新建作品页（空 / 有素材）+ 我的作品页 |
| `_shots_add.mjs` | 截图：「添加素材」新流程 |
| `_shots_layers.mjs` | 截图：01 准备素材「一行一层」版式 |
| `_shots_workspace.mjs` | 截图：工作台（素材工坊）版式 |

## dev/e2e/ —— 端到端冒烟（Node + puppeteer-core）

| 脚本 | 验什么 |
| --- | --- |
| `_e2e_create.mjs` | 「素材优先」链路：按需建 draft → 加素材 → 勾选 → 提示词 → 校验 |
| `_e2e_video.mjs` | 02「生成视频」链路 + 工作台两个形态 |
| `_e2e_face.png` | 上传用的测试人脸图（曾丢过一次，多个脚本依赖它，别删） |

## dev/tools/ —— 独立工具与实验

| 脚本 | 用途 |
| --- | --- |
| `crop_ref.py` | 把豆包 / 即梦产出的多视角角色拼贴图裁成单视角图 |
| `test_collage_edit.py` | 「整张拼贴直发 Edit 模型」路线实验（一次调用出四个视角） |

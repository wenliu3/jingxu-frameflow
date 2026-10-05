# 代码职责与维护入口

本次整理聚焦实际使用的素材工作台、创作画布和视频候选链路。移除已失去入口的前端功能，拆开参数界面、请求约束、后台执行和文件保存。

## 前端

| 文件 | 职责 |
| --- | --- |
| `web/src/App.vue` | 作品导航、服务设置、回收站和生成记录；不执行旧分镜或分块操作 |
| `web/src/theme.js` / `theme.css` | 浏览器主题偏好与全局日间配色；组件通过颜色变量保留原夜间色，弹窗和预览继承根主题 |
| `web/src/app-shell.css` | 页面容器与生成记录样式，作为 App 的 scoped 样式加载 |
| `web/src/components/CreateWorkbench.vue` | 素材增删编辑、上传、录音、AI 生成、提示词、规划助手和素材历史 |
| `web/src/material-studio.css` | 素材工作台及其弹窗样式，作为工作台的 scoped 样式加载 |
| `web/src/components/WorkflowCanvas.vue` | 画布交互、保存、生成队列和成片导出协调 |
| `web/src/components/VideoComposer.vue` | 当前视频的描述、参考图、提交和历史版本入口 |
| `web/src/components/ReferenceThumbnail.vue` | 带序号的参考缩略图、悬停放大与断开操作 |
| `web/src/components/PromptEditor.vue` / `web/src/promptMentions.js` | 提示词内素材标签、@ 搜索、键盘与剪贴板编辑、素材身份与断开校验 |
| `web/src/components/VideoGenerationSettings.vue` | 参数摘要、画幅/清晰度/时长/音频/数量/种子控制及弹窗定位 |
| `web/src/components/VideoSourceChip.vue` / `web/src/videoSources.js` | 视频来源预览、用途与版本选择、快照、依赖就绪检查 |
| `web/src/components/VideoCompare.vue` | 成功候选播放对比与选用 |
| `web/src/workflowGraph.js` / `workflowStudio.js` / `workflowVideo.js` | 图结构、编排与校验、节点迁移、版本与候选进度；保持与界面独立 |
| `web/src/videoSettings.js` / `serviceSettings.js` | 参数选项、请求转换与服务配置校验 |
| `web/src/api.js` | 当前界面使用的 HTTP 封装、合并并发读取和上传 |

素材工作台不再有隐藏的视频表单；所有视频创作走画布。模板、队列和历史导入统一使用同一套视频参数。修改版本或参数规则时，优先修改对应逻辑模块，再修改界面。

## 后端

| 文件 | 职责 |
| --- | --- |
| `server/app.py` | 接口注册、作品与素材解析、配置快照和生成前检查；保留仍在使用的旧作品与 CLI 接口 |
| `server/video_contracts.py` | 编排、提示词优化和候选生成请求的字段、类型与范围 |
| `server/prompt_mentions.py` | 根据本次真实参考槽位，把素材身份转换为 Subject/Picture 标签；拒绝未选择的引用 |
| `h3_prompt_policy.py` | 当前镜头意图、六段结构、毫秒时间轴、参考编号与视觉保留标记的离线校验 |
| `agents.py` / `ref_plan.py` | 文本编排与一次纠错、参考槽位、六段式组装；按模式加载本地 H3 规则精要 |
| `server/video_sources.py` | 作品内成功版本校验、尾帧与规范参考片段、内容指纹与缓存 |
| `server/segment_generation.py` | 候选文件与种子分配、串行执行、部分失败处理、进度更新 |
| `server/segment_job_store.py` | 候选边车文件、批次记录的原子保存与重启恢复 |
| `video_provider.py` | ComfyUI/API 协议、工作流接线、远端轮询与下载 |
| `video_controls.py` | 输出尺寸、H3 帧数、精确时长和音轨处理 |
| `server/workflow_store.py` / `config_store.py` | 画布和服务配置的保存、版本冲突与配置缓存 |
| `server/canvas_export.py` | 成片导出的文件校验、后台合并和进度 |
| `server/project_summary.py` | 作品列表摘要和封面 |

接口层负责校验与解析，后台执行模块使用已固定的 Provider 和生成参数；保存模块不依赖 HTTP 或远端服务。写记录失败时停止尚未提交的候选。临时文件完整写入后才替换目标文件，保留已有完整记录。

`server/app.py` 仍承载素材、规划助手和旧项目接口。整理时不删除这些兼容接口：CLI、历史作品和有效接口验证仍会使用。后续拆路由时按素材、规划助手、作品生命周期分别拆，保留公开路径和请求行为。

## 已移除

- App 内旧分镜/分块事件、旧批量生成与导出、无入口灯箱和闲置状态。
- 素材组件内旧视频表单、参考图选择、首尾帧弹窗、轮询和已撤掉的一键补齐。
- 29 个无调用方的前端 API 方法，214 条失效 scoped 样式规则。
- `PromptComposer.vue`、`ImageLightbox.vue` 两个无运行时引用的组件。
- 旧视频表单相关的 `_verify_framepick.mjs`、`_verify_pick_ui.mjs`、`_verify_promptout.mjs`、`_verify_prompt_ui.mjs`、`_verify_sections.mjs`，以及 `_e2e_create.mjs`、`_e2e_video.mjs`；验收已由当前画布与素材浏览器测试覆盖。

先前画布整改移除的 11 个停用文件见 [工作台整改记录](studio-redesign.md)。用户作品、服务配置、密钥、模型文件和部署脚本继续保留。

## 验证

在项目根目录执行：

```sh
python -m pip install -r requirements-dev.txt
npm ci --prefix web
npm run check --prefix web
npm run format:check --prefix web
npm run test --prefix web
npm run build --prefix web
python -m unittest discover -s dev/api -p "test_*.py"
# 首次安装浏览器；另一个终端运行前端开发服务器
npm run browser:install --prefix web
npm run test:browser --prefix web
```

`check` 检查本地导入、入口可达性、未使用组件、未定义模板变量和静态 API 方法引用；不自动删除文件。浏览器测试默认连接 5173，拦截所有 API，不调用模型或修改真实作品；覆盖画布参数、候选对比、队列、恢复、导出、手机布局，以及素材编辑、提示词、AI/历史/预览/助手弹窗与生成记录。后端测试使用临时目录，包含真实本地视频转换与写入失败保护。

前端统一使用 `web/.prettierrc.json` 定义代码格式；`npm run format --prefix web` 格式化，`format:check` 校验。浏览器测试使用 `web` 的 Playwright 依赖和安装的 Chromium，无需本机工具目录；可通过 `PYTHON` 指定生成测试视频的 Python。节点样式合并重复选择器，保留现有状态和主题规则。

新增功能应放进负责该行为的模块。旧入口撤掉时一起检查事件、状态、接口封装、样式与验收脚本，避免只藏界面、留下第二套执行逻辑。

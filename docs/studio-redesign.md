# 创作工作台整改 · 2026-10-04

参考用户提供的 LibTV 无限画布截图，改为素材与视频创作卡组成的无限画布，保留 ComfyUI 视频接入。

## 已完成

- 统一深色主题和紫色强调色；首页提供创作入口、可编辑三镜模板和近期作品。
- 一镜一张视频创作卡：描述、参考图、生成状态和播放结果属于同一节点，不再额外创建分镜/输出对。
- 点开作品或选中视频即可在视频卡正下方的输入框创作；输入框支持素材缩略图、运镜、提示词模式，以及画幅、480P/720P、自定义时长、音频开关和候选数量设置。
- 参数直接接入 ComfyUI H3 工作流；下载后适配尺寸、精确时长和音轨。1/2/4 个候选使用独立种子串行生成，分别保存文件和记录，部分失败仍保留成功结果。
- 最多四个成功版本并排播放对比，选择结果用于成片。历史导入保留自定义参数并合并同次候选，后端持久化批次状态，重启不自动重复生成。
- 默认收起镜头导航，队列与素材设置按需打开，画布占据整个工作区。
- 自动合并旧画布的分镜与视频节点；成功视频、进行中的任务编号和素材引用保留在创作卡内。
- 分段描述建镜、模板建镜、上传与拖入图片、运镜描述预设和节点复制快捷键。
- 生成前检查、串行队列、暂停继续、保存失败保护和刷新恢复等待队列。
- 重生成在当前卡片追加版本，失败不会替换成功画面。卡片可切换与下载版本，输入框支持历史描述回填；成片导出默认采用当前选中的版本。
- 修复只有三视图的道具被判为不可用；单首帧初始化不再自动连接多个输入。
- 可选择各镜成功版本的本地成片导出，后台执行、统一尺寸和帧率、补静音、下载结果。
- JSON 备份追加导入，校验连线、容量、节点和位置，隔离其他作品的任务与媒体。

## 清理

确认无运行时引用后删除 11 个停用文件：

- `AiAssistant.vue`、`MaterialStudio.vue`：功能已合入 CreateWorkbench。
- `StageRail.vue`、`ProjectBrief.vue`：旧阶段条与资料面板。
- `ShotBoard.vue`、`ShotCard.vue`、`ShotRow.vue`：旧分镜列表与卡片。
- `BlockBoard.vue`、`BlockCard.vue`：旧分块面板。
- `storage.js`：仅供已删除的旧分镜视图使用。
- `assets/hero.png`：未使用的旧首页图片。

CLI、视频 Provider、ComfyUI 工作流、部署脚本和有效回归脚本继续保留。用户作品、密钥、服务配置与本地备份未更改。

## 验证

```sh
node --test dev/ui/test_workflow_graph.mjs dev/ui/test_workflow_video.mjs dev/ui/test_workflow_studio.mjs dev/ui/test_video_settings.mjs dev/ui/test_service_settings.mjs dev/ui/test_project_library.mjs
python -m unittest dev.api.test_workflow_store dev.api.test_video_contract dev.api.test_video_controls dev.api.test_config_store dev.api.test_project_summary dev.api.test_canvas_export -v
node dev/ui/test_studio_browser.mjs
npm run build --prefix web
```

浏览器验收拦截全部 API，用演示素材检查旧画布合并、底部输入、版本切换、模板、编辑、串行任务、暂停恢复、保存失败、版本保留、导出顺序、上传和手机布局，不调用模型、不创建真实作品。前端默认地址为 `http://127.0.0.1:5173`，可由 E2E_URL 调整；浏览器依赖使用 `web` 内的 Playwright；首次运行先执行 `npm run browser:install --prefix web`，再运行 `npm run test:browser --prefix web`。可用 CHROME_PATH 指定浏览器，PYTHON 指定测试视频生成的 Python。

本地 ffmpeg 验证成片合并、尺寸适配、精确时长、音轨开关、源文件保留和失败时原子发布。浏览器验证参数实际提交、候选部分失败、有效 MP4 播放对比、历史候选合并与手机参数弹窗。真实 ComfyUI 生成未调用，需要用实际服务验证远端模型和工作流。

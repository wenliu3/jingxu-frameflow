# 开发与验证

这里保存离线回归、浏览器验收和历史诊断工具。产品运行不依赖这些脚本；维护代码时使用下面的统一入口。代码职责见 [代码整理说明](../docs/code-organization.md)。

## 日常检查

在项目根目录执行，使用安装了 Python 依赖的环境：

```sh
python -m pip install -r requirements-dev.txt
npm ci --prefix web
npm run check --prefix web
npm run format:check --prefix web
npm run test --prefix web
npm run build --prefix web
python -m unittest discover -s dev/api -p "test_*.py" -v
```

`check` 检查组件导入、入口可达性、模板变量和本地引用；前端逻辑测试不启动浏览器。后端 `test_*.py` 使用临时目录和模拟服务，包含本地 FFmpeg 视频转换，不修改用户作品或调用真实模型。`requirements-dev.txt` 补齐 FastAPI TestClient 所需的 `httpx`。

修改前端后可运行 `npm run format --prefix web`，格式规则在 `web/.prettierrc.json`。提交前确认检查和构建通过。

## 完整浏览器验收

```sh
npm run browser:install --prefix web
npm run dev --prefix web
# 在另一个终端执行
npm run test:browser --prefix web
npm run test:browser:lifecycle --prefix web
npm run test:browser:muse --prefix web
npm run test:browser:export --prefix web
```

验收使用 `web` 的 Playwright 依赖和安装的 Chromium，默认地址为 `http://127.0.0.1:5173`。全部 API 和素材请求被拦截，不需要启动后端或 ComfyUI，不创建真实作品，也不消耗模型额度。测试视频在临时目录生成，截图写到已忽略的 `docs/_shots/`。

文档截图：启动前端后，在项目根目录运行 `node dev/tools/capture_screenshots.mjs`。脚本使用离线演示素材，直接截取当前界面的图片、语音、文本、视频参数、素材工坊和手机布局，输出到 `docs/images/`；同样支持 `E2E_URL` 和 `CHROME_PATH`。视频参考、导出与其他 Muse 截图来自上面的浏览器验收，选用后整理到 [项目截图集](../docs/screenshots.md)。

覆盖视频节点迁移、浮动输入框、参数、素材引用、参考视频与续拍请求、串行候选、播放对比、保存失败、队列暂停与恢复、导出、素材弹窗、生成记录、手机布局，以及日夜模式与刷新保留。

`test:browser:lifecycle` 专门模拟延迟响应，验证大量历史记录最多六路并发、结果顺序，以及离开画布后停止未开始的读取、忽略迟到的画布和任务结果。导出记录在重新进入作品后仍可查询。

`test:browser:muse` 使用完全拦截的 Muse 页面和镜序接口，验证上传、消息发送、唯一任务匹配、相邻回复中的成片卡片、打开成片后的下载按钮、草稿保护，以及原任务恢复取回到同一画布版本，不登录账号或消耗额度。真实账号首次连接见 [Muse 接入说明](../docs/muse-browser.md)。

| 环境变量 | 用途 |
| --- | --- |
| `E2E_URL` | 修改前端测试地址 |
| `PYTHON` | 指定安装了 `imageio-ffmpeg` 的 Python；默认 `python` |
| `CHROME_PATH` | 使用指定的浏览器可执行文件；默认使用安装的 Chromium |
| `FRAMEFLOW_BROWSER_MODULES` | 可选的外部 Playwright 模块目录；一般不需要设置 |

PowerShell 示例：`$env:PYTHON='D:\miniforge\python.exe'`，然后执行测试命令。离线测试验证界面和请求链路，不代表已经验证真实 H3 的生成质量或续拍接缝。

## 测试职责

| 位置 | 主要覆盖 |
| --- | --- |
| `dev/api/test_h3_prompt_policy.py`、`test_video_contract.py` | 提示词结构、时间轴、素材编号、参数和模型输出纠错 |
| `dev/api/test_video_sources.py` | 来源归属、成功版本、指纹、参考片段和尾帧、工作流接线 |
| `dev/api/test_video_controls.py`、`test_canvas_export.py` | 实际视频尺寸、时长、音轨、成片合并和原片 ZIP 打包 |
| `dev/api/test_workflow_store.py`、`test_config_store.py` | 原子保存、并发、冲突、配置缓存及失败保护 |
| `dev/api/test_atomic_io.py` | 作品、镜头、分块、预览和任务元数据的写盘失败保护、并发文件替换 |
| `dev/api/test_project_summary.py` | 作品摘要、封面、数量和缓存 |
| `dev/ui/test_workflow_*.mjs` | 图结构、迁移、编排、队列与版本 |
| `dev/ui/test_video_*.mjs` | 视频参数、来源用途、固定版本和依赖 |
| `dev/ui/test_prompt_mentions.mjs` | 素材身份标签、解析和断开校验 |
| `dev/ui/test_service_settings.mjs`、`test_project_library.mjs`、`test_theme.mjs` | 配置草稿、作品筛选、主题偏好 |
| `dev/ui/test_studio_browser.mjs` | 当前界面的完整离线浏览器验收 |
| `dev/ui/test_async_tasks.mjs`、`test_canvas_lifecycle_browser.mjs` | 历史读取限流、结果顺序与取消调度、页面切换时的延迟响应 |
| `dev/ui/test_canvas_export_browser.mjs` | 镜头勾选、版本、ZIP 下载、成片合成和导出布局 |

单项测试也可直接运行，例如：

```sh
python -m unittest dev.api.test_video_sources -v
node --test dev/ui/test_prompt_mentions.mjs
```

## 历史诊断与截图工具

`dev/api/_verify_*`、`dev/ui/_verify_*` 和 `_shots*` 保留用于单项诊断与截图；其中一些面向旧页面或需要运行中的后端，不能替代当前回归入口。使用前检查脚本地址、依赖与是否会调用真实服务，各脚本的开关不完全一致。`dev/e2e/_e2e_face.png` 是历史上传测试素材。

| 工具 | 用途 |
| --- | --- |
| `dev/tools/check_comfyui_models.py` | 检查远端 ComfyUI 的节点和模型文件 |
| `dev/tools/check_portrait_chain.py` | 诊断角色出图链路 |
| `dev/tools/crop_ref.py` | 裁切多视角角色图 |
| `dev/tools/check_ms_task.py`、`probe_ms_concurrency.py`、`probe_image_failures.py` | 查询或探测图片服务行为 |
| `dev/tools/test_collage_edit.py` | 多视角图片编辑实验 |
| `dev/ui/_shots_readme.mjs` | 历史 README 截图工具，使用前核对当前页面入口 |

这些诊断和实验可能读取本地配置、修改测试输出或调用实际服务；先阅读对应脚本再运行。当前视频续拍设计见 [视频参考与续拍说明](../docs/video-continuation-plan.md)，提示词链路见 [编排说明](../docs/h3-prompt-pipeline.md)。
## 多类型画布验证

`python -m unittest dev.api.test_canvas_media` 验证图片/语音任务的幂等提交、失败和重启恢复、本地文件发布及真实台词传递；全部使用临时作品和模拟生成服务。

`npm --prefix web run test:browser:media` 验证「＋」菜单、文本编辑、图片比例与视频参考、语音音色/下载、刷新恢复及音频上传；浏览器拦截所有接口与媒体请求，不操作用户作品或调用模型。

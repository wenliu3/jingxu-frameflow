// 后端接口封装。
// 开发期由 vite proxy 把 /api、/files 转发到 8000 端口，所以这里写相对路径即可；
// 生产是同源部署（FastAPI 同时挂载静态文件），相对路径同样成立。

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    // ⚠️ **body 只能读一次。**
    // 原来是「先 await res.json()，catch 里再 await res.text()」—— 非 JSON 的错误响应
    // （Starlette 的未捕获异常就是 `text/plain` 的 "Internal Server Error"）会让第一次读
    // 失败但 body 已被消费，第二次读直接抛
    // 「Failed to execute 'text' on 'Response': body stream already read」，
    // 把真正的报错顶掉 —— 用户看到的就是这句和故障毫无关系的话。
    // 现在统一先取文本，再尝试当 JSON 解析。
    const raw = await res.text().catch(() => '')
    let detail = raw.trim()
    try {
      const body = JSON.parse(raw)
      if (body && body.detail != null) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      // 不是 JSON（纯文本 / HTML 错误页）→ 原样用文本
    }
    const err = new Error(detail || `请求失败（${res.status}）`)
    err.status = res.status
    throw err
  }
  return res.json()
}

// 文件上传的公共封装：File -> base64 -> POST JSON（不带 data: 前缀的部分留在后端剥）。
// 三个上传接口（角色图 / 素材文件 / 其他素材）是同一个形状，没必要各写一遍。
// filename 可覆盖：浏览器录制的音频是 Blob，没有 name，后端要靠后缀决定落盘格式。
function uploadFile(path, file, filename = '') {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('读取文件失败'))
    reader.onload = () => {
      const dataB64 = String(reader.result || '').split(',')[1] || ''
      request(path, {
        method: 'POST',
        body: JSON.stringify({ filename: filename || file.name || 'upload.bin', data_b64: dataB64 }),
      }).then(resolve, reject)
    }
    reader.readAsDataURL(file)
  })
}

export const api = {
  // 任务列表由后端扫磁盘给出，不读浏览器存储
  listTasks() {
    return request('/api/tasks')
  },

  // ---------- 素材优先：新建空白作品 ----------
  // 「新建作品」页是先攒素材、后写故事的流程，而 /api/generate 必须先有创意。
  // 所以第一次动素材（上传 / 添加 / 问助手）时先要一个空壳作品，所有素材接口才有挂载对象。
  createDraft(title = '') {
    return request('/api/tasks/draft', {
      method: 'POST',
      body: JSON.stringify({ title }),
    })
  },

  // ---------- AI 创作助手 ----------
  // 一轮对话 = 一次调用。历史由前端回传（只取最近几轮），后端不存会话。
  // 返回 { task_id, reply, created:{characters,assets}, skipped, suggestions }，
  // created 非空说明作品被改了，前端要重新拉一次任务。
  assistantChat({ taskId = '', message, history = [] }) {
    return request('/api/assistant', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, message, history }),
    })
  },

  // 组级上传：选个文件就多一项素材（名字取文件名，重名自动加序号）。
  // kind: character | scene | prop | image | audio
  uploadMaterial(taskId, kind, file) {
    return uploadFile(
      `/api/tasks/${taskId}/materials/upload?kind=${encodeURIComponent(kind)}`,
      file,
    )
  },

  // ---------- 素材规划助手：文档 → 素材与提示词（2026-09-27） ----------
  // 与 assistantChat 的分工：那条是凭空聊天想素材；这条读用户**上传的文档**抽取素材，
  // 而且同名条目会被**改写**（"第二个场景改成黄昏"能落下去）。
  // ⚠️ 两者都只写条目和提示词，**不出图、不出音、不占出图/出片额度**。
  listDocs(taskId) {
    return request(`/api/tasks/${taskId}/docs`)
  },

  // 上传即解析：后端读不出字（扫描件 / 格式不支持）会直接 422，前端弹提示即可。
  // 同名文件是**覆盖**（同一份剧本改了一版再传是最常见的用法）。
  uploadDoc(taskId, file) {
    return uploadFile(`/api/tasks/${taskId}/docs`, file)
  },

  deleteDoc(taskId, name) {
    return request(`/api/tasks/${taskId}/docs/${encodeURIComponent(name)}?confirm=true`, {
      method: 'DELETE',
    })
  },

  // docs 传数组＝只读这几份；**不传这个字段**＝读作品下全部文档。
  // 传空数组＝一份都不读（用户把附件全删了就不该还在背地里读）。
  assistantPlan(taskId, { message = '', history = [], docs = null } = {}) {
    const body = { message, history }
    if (docs !== null) body.docs = docs
    return request(`/api/tasks/${taskId}/assistant/plan`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  // 上传/录制角色音色样本（不碰任何模型接口，与 rerollCharacterVoice 互补）。
  // 录制出来的 Blob 没有文件名，这里显式给一个带正确后缀的名字，
  // 否则后端只能按默认 .png 处理、兜底成 .mp3。
  uploadCharacterVoice(taskId, index, file, filename = '') {
    return uploadFile(`/api/tasks/${taskId}/characters/${index}/voice/upload`, file, filename)
  },

  generate(payload) {
    return request('/api/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  getTask(taskId) {
    return request(`/api/tasks/${taskId}`)
  },

  patchShot(taskId, shotId, patch) {
    return request(`/api/tasks/${taskId}/shots/${shotId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  },

  // 历史任务管理：重命名改 project.title；删除把目录移进 outputs/_trash/ 回收站
  renameTask(taskId, title) {
    return request(`/api/tasks/${taskId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    })
  },

  deleteTask(taskId) {
    // 后端要求 ?confirm=true（协议级防误删闸：自动化工具按文字点击时
    // 会命中删除按钮的 aria-label，一次点击就删一个作品）
    return request(`/api/tasks/${taskId}?confirm=true`, { method: 'DELETE' })
  },

  // 回收站：删除一直是软删除（目录移到 outputs/_trash/），这三条让它可见、可还原、可真删。
  // ⚠️ listTrash 会遍历每个条目的目录算占用空间，**别在进「我的作品」时顺手调** ——
  // 只在真正打开回收站时拉一次。
  listTrash() {
    return request('/api/trash')
  },

  restoreTrash(name) {
    return request(`/api/trash/${encodeURIComponent(name)}/restore`, { method: 'POST' })
  },

  purgeTrash(name) {
    // 同 deleteTask：彻底删除也要协议级确认闸，这一步恢复不了
    return request(`/api/trash/${encodeURIComponent(name)}?confirm=true`, { method: 'DELETE' })
  },

  regenerateShot(taskId, shotId, { regenPrompt = false, force = true } = {}) {
    const qs = new URLSearchParams({
      regen_prompt: String(regenPrompt),
      force: String(force),
    })
    return request(`/api/tasks/${taskId}/shots/${shotId}/regenerate?${qs}`, {
      method: 'POST',
    })
  },

  // ---------- 图生视频 ----------
  // 出一条视频要几分钟，后端是异步任务：POST 拿 job_id，GET 轮询到 succeeded/failed。
  generateVideo(taskId, shotId) {
    return request(`/api/tasks/${taskId}/shots/${shotId}/video`, {
      method: 'POST',
    })
  },

  videoJob(jobId) {
    return request(`/api/video-jobs/${jobId}`)
  },

  videoUrl(taskId, shotId, version) {
    const name = `shot_${String(shotId).padStart(2, '0')}.mp4`
    const suffix = version ? `?v=${version}` : ''
    return `/files/${taskId}/videos/${name}${suffix}`
  },

  // ---------- 分阶段流程 ----------
  // 阶段一 = /api/generate（导演 + 角色定妆照），之后每段完成后用户点「继续」。
  stageStoryboard(taskId) {
    return request(`/api/tasks/${taskId}/stage/storyboard`, { method: 'POST' })
  },

  stageImages(taskId) {
    return request(`/api/tasks/${taskId}/stage/images`, { method: 'POST' })
  },

  rerollCharacter(taskId, index) {
    return request(`/api/tasks/${taskId}/characters/${index}/reroll`, {
      method: 'POST',
    })
  },

  // 按一段自由描述生成角色定妆照（**不写回角色描述**）。
  // 描述可以先由后端交给角色设计 Agent 规范化再出图，所以这里写口语就行
  // （"酷一点的赛博女战士"），不必自己写成规范的视觉特征。
  // ratio 只作用于正面定妆照；后端还会另出一张 16:9 的四视图设定图（恒横版）。
  generateCharacterPortrait(taskId, index, prompt, ratio = '1:1') {
    return request(`/api/tasks/${taskId}/characters/${index}/portrait`, {
      method: 'POST',
      body: JSON.stringify({ prompt, ratio }),
    })
  },

  // 按一段自由描述生成**素材图**（场景空镜 / 道具三视图 / 其他图片的完整画面，
  // **不写回素材描述**）。描述会先由后端交给对应 Agent 扩写，所以写口语就行。
  // ratio 留空则跟随作品画幅。
  generateAssetImage(taskId, index, prompt, ratio = '') {
    return request(`/api/tasks/${taskId}/assets/${index}/generate`, {
      method: 'POST',
      body: JSON.stringify({ prompt, ratio }),
    })
  },

  // 「AI 生成音色」：性别 + 一段描述 → 配音 Agent 从**当前音频后端的音色池**里挑一个
  // → 合成样本并写回角色的 tts_voice。
  // ⚠️ 池子是预置音色（minimax 27 个 / edge 8 个），模型造不出新音色，
  // 所谓"生成"是从池子里挑最贴的那一个 —— 别在前端承诺"凭空造一个音色"。
  generateCharacterVoice(taskId, index, prompt, gender = '') {
    return request(`/api/tasks/${taskId}/characters/${index}/voice/generate`, {
      method: 'POST',
      body: JSON.stringify({ prompt, gender }),
    })
  },

  // 编辑角色：改名字或锚点提示词。锚点改了后旧定妆照会带 stale 标记，提示重新生成。
  patchCharacter(taskId, index, patch) {
    return request(`/api/tasks/${taskId}/characters/${index}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  },

  // 角色定妆照存 outputs/{task_id}/characters/，由 /files 挂载播放
  //
  // ⚠️ **version 是必需的 cache-busting**（2026-09-19 斌哥报的"要刷新浏览器才看到新图"）：
  //    重新生成是**就地覆盖同名文件**，URL 字符串不变 → 浏览器 `<img>` 认为图没变，
  //    **连请求都不发**，用户看到的还是旧图。带上 `?v=<后端每次生成新给的 version>`
  //    URL 就变了，浏览器自然会去取新图。version 变了就换 URL，这正是我们要的。
  characterImageUrl(taskId, imagePath, version = '') {
    const name = (imagePath || '').split(/[\\/]/).pop()
    const url = `/files/${taskId}/characters/${name}`
    return version ? `${url}?v=${encodeURIComponent(version)}` : url
  },

  // 音色样本与定妆照同目录（characters/voice_<角色名>.mp3），同样走 /files
  characterVoiceUrl(taskId, fileName) {
    const name = (fileName || '').split(/[\\/]/).pop()
    return name ? `/files/${taskId}/characters/${name}` : ''
  },

  // 当前可选的 TTS 音色清单。单一来源在后端 tts.VOICES，前端不要另存一份
  listVoices() {
    return request('/api/voices')
  },

  // 合成/重合成角色音色样本。voiceId 非空时同时把角色的音色改成它
  // —— 前端下拉选完调这一个接口就够，不用先 PATCH 再 POST 两趟
  rerollCharacterVoice(taskId, index, voiceId = '') {
    const q = voiceId ? `?voice_id=${encodeURIComponent(voiceId)}` : ''
    return request(`/api/tasks/${taskId}/characters/${index}/voice${q}`, {
      method: 'POST',
    })
  },

  // 单段生成：选中的素材 + 一段描述 → Ref2VA 六段式提示词（只编排，不出片）。
  // 传了 video_prompt（英文正文）就跳过 LLM，只做组装 —— 这是"不用任何文本 API"的那条路
  composeSegment(taskId, payload) {
    return request(`/api/tasks/${taskId}/compose`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  // 「让 AI 帮写」：中文口语描述 → **中文**画面描述（2026-09-19 加）。
  // 两步走的第一步 —— 用户看得懂、能改；点「生成视频」时才由 composeSegment 编成英文正文。
  optimizePrompt(taskId, payload) {
    return request(`/api/tasks/${taskId}/prompt/optimize`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  // 出片：三种旧模式合并后的唯一入口 —— 不再让用户选模式，
  // 由「选了什么素材 + 有没有勾首尾帧」决定走 I2VA / FL2VA / T2VA
  startSegmentVideo(taskId, payload) {
    return request(`/api/tasks/${taskId}/segment/video`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  segmentVideo(jobId) {
    return request(`/api/segment-jobs/${jobId}`)
  },

  // 这个作品出过的所有单段视频（「生成记录」面板用）。
  // ⚠️ 后端读的是磁盘上的 mp4，不是内存里的 job —— 刷新页面后 segJob 就没了，
  // 但文件还在，这个入口负责把它们找回来。
  listSegments(taskId) {
    return request(`/api/tasks/${taskId}/segments`)
  },

  // 某一段「用了什么」：素材 / 提示词 / 参数。
  // ⚠️ 后端读的是出片时写下的边车 json（seg_xxx.json）。2026-09-18 之前生成的片子
  // 没有这个文件，后端会返回 { found: false }，调用方要如实说明，别渲染成空字段。
  segmentDetail(taskId, name) {
    return request(`/api/tasks/${taskId}/segments/${encodeURIComponent(name)}`)
  },

  // ---------- 素材上传（2026-09-14 起：上传即素材，不走图生图） ----------
  // 让"没有任何图像/文本 API，只有 ComfyUI 地址"的用户也能自己备素材。
  uploadCharacterImage(taskId, index, file) {
    return uploadFile(`/api/tasks/${taskId}/characters/${index}/upload`, file)
  },

  uploadAssetFile(taskId, index, file) {
    return uploadFile(`/api/tasks/${taskId}/assets/${index}/upload`, file)
  },

  // ---------- 素材（道具 / 场景） ----------
  // 素材图存 outputs/{task_id}/assets/，与角色同由 /files 挂载。
  // version 同 `characterImageUrl`：就地覆盖 + URL 不变 = 浏览器不重新请求。
  assetImageUrl(taskId, imagePath, version = '') {
    const name = (imagePath || '').split(/[\\/]/).pop()
    const url = `/files/${taskId}/assets/${name}`
    return version ? `${url}?v=${encodeURIComponent(version)}` : url
  },

  // ---------- 生成历史（2026-09-19 加） ----------
  // 「这张图出过的每一版」：重新生成 / 覆盖上传之前都会先留一版快照。
  // kind ∈ character / scene / prop / image（音频没有版本，别传）。
  // 返回 { versions: [...], keep }，**第一条是当前这版**（current: true、name 为空），
  // 之后新的在前。每项自带 `thumb`（已拼好的 /files 相对路径），前端直接用。
  materialHistory(taskId, kind, index) {
    return request(`/api/tasks/${taskId}/materials/${kind}/${index}/history`)
  },

  // 切回某一版（覆盖回当前文件）。后端会**先把当前这版也存进历史**，
  // 所以切回去之后还能再切回来 —— 前端不需要自己备份。
  useMaterialHistory(taskId, kind, index, name) {
    return request(`/api/tasks/${taskId}/materials/${kind}/${index}/history/use`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    })
  },

  // 重新生成某个素材的全部视角（删旧图，判存逻辑只补这一个素材）
  rerollAsset(taskId, index) {
    return request(`/api/tasks/${taskId}/assets/${index}/reroll`, {
      method: 'POST',
    })
  },

  // ---------- 角色：手动增删 ----------
  // 手动添加角色；锚点/音色留空时由 AI 按故事设定设计
  addCharacter(taskId, payload) {
    return request(`/api/tasks/${taskId}/characters`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  deleteCharacter(taskId, index) {
    return request(`/api/tasks/${taskId}/characters/${index}`, { method: 'DELETE' })
  },

  // ---------- 素材：手动增删改 ----------
  addAsset(taskId, payload) {
    return request(`/api/tasks/${taskId}/assets`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  patchAsset(taskId, index, patch) {
    return request(`/api/tasks/${taskId}/assets/${index}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  },

  deleteAsset(taskId, index) {
    return request(`/api/tasks/${taskId}/assets/${index}`, { method: 'DELETE' })
  },

  // ---------- 分块流程（一块 = 一条 10s 视频） ----------
  // AI 生成下一块：后台线程（出块 + 写提示词两次文本调用），轮询任务状态拿结果
  aiNextBlock(taskId, instruction = '') {
    return request(`/api/tasks/${taskId}/blocks/ai-next`, {
      method: 'POST',
      body: JSON.stringify({ instruction }),
    })
  },

  // 手动添加一块（提示词可留空，出图前先让 AI 写或自己填）
  addBlock(taskId, payload) {
    return request(`/api/tasks/${taskId}/blocks`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  patchBlock(taskId, blockId, patch) {
    return request(`/api/tasks/${taskId}/blocks/${blockId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  },

  deleteBlock(taskId, blockId) {
    return request(`/api/tasks/${taskId}/blocks/${blockId}`, { method: 'DELETE' })
  },

  // AI 为单块写/重写提示词（同步，一次文本调用）
  writeBlockPrompt(taskId, blockId) {
    return request(`/api/tasks/${taskId}/blocks/${blockId}/prompt`, { method: 'POST' })
  },

  // 块首帧图（第 0 秒画面），同步出图
  blockImage(taskId, blockId, force = true) {
    return request(`/api/tasks/${taskId}/blocks/${blockId}/image?force=${force}`, {
      method: 'POST',
    })
  },

  // 块视频（异步 job）：承接上一块时起点是上一块的尾帧
  generateBlockVideo(taskId, blockId) {
    return request(`/api/tasks/${taskId}/blocks/${blockId}/video`, { method: 'POST' })
  },

  blockImageUrl(taskId, blockId, version) {
    const name = `block_${String(blockId).padStart(2, '0')}.png`
    const suffix = version ? `?v=${version}` : ''
    return `/files/${taskId}/images/${name}${suffix}`
  },

  blockVideoUrl(taskId, blockId, version) {
    const name = `block_${String(blockId).padStart(2, '0')}.mp4`
    const suffix = version ? `?v=${version}` : ''
    return `/files/${taskId}/videos/${name}${suffix}`
  },

  blockLastFrameUrl(taskId, blockId, version) {
    const name = `block_${String(blockId).padStart(2, '0')}_last.png`
    const suffix = version ? `?v=${version}` : ''
    return `/files/${taskId}/frames/${name}${suffix}`
  },

  // ---------- 一键批量 + 合并导出 ----------
  startBatchVideos(taskId) {
    return request(`/api/tasks/${taskId}/videos/batch`, { method: 'POST' })
  },

  videoBatch(jobId) {
    return request(`/api/video-batch/${jobId}`)
  },

  exportVideo(taskId) {
    return request(`/api/tasks/${taskId}/export`, { method: 'POST' })
  },

  // ---------- 服务配置（ComfyUI / 文本模型 / 图片模型） ----------
  // 全部参数由前端弹窗下发，后端持久化到 service_config.json 并实时生效。
  getConfig() {
    return request('/api/config')
  },

  setConfig(cfg) {
    return request('/api/config', {
      method: 'POST',
      body: JSON.stringify(cfg),
    })
  },

  // version 用来绕过浏览器对同名图片的缓存——重生成后文件名不变，
  // 不加这个参数会一直显示旧图。
  imageUrl(taskId, shotId, version) {
    const name = `shot_${String(shotId).padStart(2, '0')}.png`
    const suffix = version ? `?v=${version}` : ''
    return `/files/${taskId}/images/${name}${suffix}`
  },

  // ---------- 静态产物直链 ----------
  // 每个任务的产物落在 outputs/{task_id}/，由后端挂载在 /files 下。
  // 这些接口不需要新增后端路由，直接拿现成文件。
  fileUrl(taskId, filename) {
    return `/files/${taskId}/${filename}`
  },
}

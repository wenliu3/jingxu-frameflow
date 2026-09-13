// 后端接口封装。
// 开发期由 vite proxy 把 /api、/files 转发到 8000 端口，所以这里写相对路径即可；
// 生产是同源部署（FastAPI 同时挂载静态文件），相对路径同样成立。

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      detail = await res.text()
    }
    const err = new Error(detail || `请求失败（${res.status}）`)
    err.status = res.status
    throw err
  }
  return res.json()
}

export const api = {
  // 任务列表由后端扫磁盘给出，不读浏览器存储
  listTasks() {
    return request('/api/tasks')
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
    return request(`/api/tasks/${taskId}`, { method: 'DELETE' })
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

  // 编辑角色：改名字或锚点提示词。锚点改了后旧定妆照会带 stale 标记，提示重新生成。
  patchCharacter(taskId, index, patch) {
    return request(`/api/tasks/${taskId}/characters/${index}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  },

  // 角色定妆照存 outputs/{task_id}/characters/，由 /files 挂载播放
  characterImageUrl(taskId, imagePath) {
    const name = (imagePath || '').split(/[\\/]/).pop()
    return `/files/${taskId}/characters/${name}`
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

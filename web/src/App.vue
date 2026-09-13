<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { api } from './api'
import AppSidebar from './components/AppSidebar.vue'
import PromptComposer from './components/PromptComposer.vue'
import StageRail from './components/StageRail.vue'
import ProjectBrief from './components/ProjectBrief.vue'
import ShotBoard from './components/ShotBoard.vue'
import ImageLightbox from './components/ImageLightbox.vue'
import ToastStack from './components/ToastStack.vue'

// ---------------------------------------------------------------- 状态
const taskId = ref('')
const task = ref(null)
const busy = ref(false)
const errorMsg = ref('')
const showTrace = ref(false)
const regenBusy = ref(new Set())
// 图生视频进行中的分镜：shot_id -> { jobId }。视频一条要几分钟，
// 提交后靠轮询 videoJob 拿状态，完成时把 video_path 合进对应分镜。
const videoBusy = ref(new Map())
// 服务配置（ComfyUI / 文本模型 / 图片模型），弹窗里填，后端持久化实时生效
const cfg = ref({
  video_backend: 'comfyui',
  comfyui_url: '',
  text_base_url: '',
  text_api_key: '',
  text_model: '',
  image_base_url: '',
  image_api_key: '',
  image_model: '',
  video_megapixels: '0.5',
  video_steps: '4',
  video_lora: 'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors',
  video_api_url: '',
  video_api_key: '',
  video_api_model: '',
})
const showConfigPanel = ref(false)
const cfgSaving = ref(false)
const cfgReachable = ref(null)
// 一键批量：shot 间串行出片，轮询拿 done/total，每有新完成就刷新任务
const batchJob = ref(null)
const exportBusy = ref(false)
const history = ref([])
const historyError = ref('')
const historyLoading = ref(false)
const composerRef = ref(null)
const activeView = ref('create')
const search = ref('')
const filter = ref('all')
const configDialog = ref(null)
let configOpener = null
const filteredHistory = computed(() => history.value.filter((item) => {
  const matchesText = `${item.title || ''} ${item.idea || ''}`.toLowerCase().includes(search.value.trim().toLowerCase())
  return matchesText && (filter.value === 'all' || item.status === filter.value)
}))
const inspirations = [
  { title: '山海之间', tag: '治愈 · 电影感', className: 'mountain', prompt: '一个独自旅行的人，在山海之间寻找一封没有寄出的信。用写实电影风格，温柔的晨光与广阔的自然景观，讲述一个关于告别与重逢的短片。' },
  { title: '霓虹未眠', tag: '科幻 · 城市叙事', className: 'neon', prompt: '未来城市的最后一家深夜便利店，机器人店员遇见了一位寻找旧时记忆的客人。赛博朋克电影风格，雨夜、青绿霓虹、细腻的情感。' },
  { title: '夏日来信', tag: '动画 · 生活切片', className: 'summer', prompt: '海边小镇的夏天，少女收到一封来自未来的明信片，开始了寻找寄信人的旅程。手绘动画风格，明亮阳光、海风、橙色电车与青春的奇遇。' },
]

async function useInspiration(item) {
  activeView.value = 'create'
  await nextTick()
  composerRef.value?.setIdea(item.prompt)
  document.getElementById('creative-brief')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function openLibrary() {
  activeView.value = 'library'
  refreshTasks()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function formatDate(iso) {
  if (!iso) return '日期未知'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '日期未知' : date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

const lightbox = ref({ open: false, index: 0 })
const toasts = ref([])

let timer = null
let toastSeq = 0

// ---------------------------------------------------------------- 提示条
function toast(text, type = 'info', ttl = 4200) {
  const id = ++toastSeq
  toasts.value = [...toasts.value, { id, text, type }]
  window.setTimeout(() => dismissToast(id), ttl)
}
function dismissToast(id) {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

// ---------------------------------------------------------------- 轮询
// 用 setTimeout 递归而不是 setInterval：上一轮请求还没回来时不会叠新的请求。
// 1.2 秒一次，本地内存态查询，代价可以忽略。
function stopPolling() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

function schedule(delay = 1200) {
  stopPolling()
  timer = window.setTimeout(poll, delay)
}

async function poll() {
  if (!taskId.value) return
  const requestedId = taskId.value
  try {
    const data = await api.getTask(requestedId)
    if (taskId.value !== requestedId) return
    task.value = data

    if (data.status === 'running') {
      busy.value = true
      schedule()
      return
    }

    busy.value = false
    refreshTasks()   // 跑完了，同步左侧栏的状态点和镜数
    if (data.status === 'failed') {
      errorMsg.value = data.error || '生成失败'
      toast('生成失败，详情见页面提示', 'error', 6000)
    } else {
      const rendered = (data.shots || []).filter((s) => s.image_path).length
      const message = data.stage_state === 'directed' ? '故事与角色已就绪，确认后可继续编排分镜' : data.stage_state === 'storyboarded' ? '分镜与提示词已就绪，确认后可继续生成画面' : `分镜就绪，共 ${data.shots?.length || 0} 镜，已出图 ${rendered} 张`
      toast(message, 'ok', 5000)
    }
  } catch (err) {
    if (taskId.value !== requestedId) return
    busy.value = false
    stopPolling()
    if (err.status === 404) {
      errorMsg.value = '找不到这个任务，它的输出目录可能已经被删掉了。'
      refreshTasks()
      toast('任务不存在', 'error', 5000)
    } else {
      errorMsg.value = err.message
      toast(err.message, 'error')
    }
  }
}

// ---------------------------------------------------------------- 历史
// 任务列表以后端为准：后端启动时扫 outputs/ 重建，所以不依赖浏览器存储。
async function refreshTasks() {
  historyLoading.value = true
  try {
    history.value = await api.listTasks()
    historyError.value = ''
  } catch {
    historyError.value = '暂时无法连接作品库，请检查后端服务后重试。'
  } finally {
    historyLoading.value = false
  }
}

// ---------------------------------------------------------------- 生成
async function start(payload) {
  if (busy.value) return
  stopPolling()
  errorMsg.value = ''
  showTrace.value = false
  busy.value = true
  task.value = null
  regenBusy.value = new Set()

  try {
    const res = await api.generate(payload)
    taskId.value = res.task_id
    activeView.value = 'workspace'
    poll()
    refreshTasks()
  } catch (err) {
    busy.value = false
    errorMsg.value = err.message
    toast(err.message, 'error')
  }
}

async function openTask(id) {
  activeView.value = 'workspace'
  window.scrollTo({ top: 0, behavior: 'smooth' })
  if (id === taskId.value && task.value) return
  stopPolling()
  taskId.value = id
  task.value = null
  errorMsg.value = ''
  showTrace.value = false
  busy.value = true
  try {
    const data = await api.getTask(id)
    if (taskId.value !== id) return
    task.value = data
    if (data.status === 'running') schedule(400)
    else busy.value = false
  } catch (err) {
    if (taskId.value !== id) return
    busy.value = false
    errorMsg.value = err.status === 404 ? '找不到这个作品，它的输出目录可能已经被删掉了。' : `作品加载失败：${err.message}`
    refreshTasks()
    toast(errorMsg.value, 'error')
  }
}

async function createNew() {
  stopPolling()
  taskId.value = ''
  task.value = null
  errorMsg.value = ''
  showTrace.value = false
  busy.value = false
  activeView.value = 'create'
  await nextTick()
  composerRef.value?.reset()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// ---------------------------------------------------------------- 分镜操作
function applyShot(updated) {
  const list = task.value?.shots
  if (!list) return
  const i = list.findIndex((s) => Number(s.shot_id) === Number(updated.shot_id))
  if (i >= 0) list[i] = { ...list[i], ...updated }
}

async function onPatch({ shotId, patch }) {
  const ownerId = taskId.value
  try {
    const updated = await api.patchShot(ownerId, shotId, patch)
    if (taskId.value === ownerId) applyShot(updated)
  } catch (err) {
    toast(err.message, 'error')
    if (taskId.value === ownerId) poll()
  }
}

async function onRegen({ shot, regenPrompt }) {
  const id = shot.shot_id
  if (regenBusy.value.has(id)) return
  regenBusy.value = new Set(regenBusy.value).add(id)
  try {
    const updated = await api.regenerateShot(taskId.value, id, { regenPrompt })
    applyShot(updated)
    toast(`第 ${id} 镜已重出图`, 'ok', 2600)
  } catch (err) {
    toast(`第 ${id} 镜重出图失败：${err.message}`, 'error', 6000)
  } finally {
    const next = new Set(regenBusy.value)
    next.delete(id)
    regenBusy.value = next
  }
}

// ---------------------------------------------------------------- ComfyUI 地址
async function refreshConfig() {
  try {
    cfg.value = await api.getConfig()
  } catch {
    /* 拉不到不影响其它功能 */
  }
}

async function saveConfig() {
  cfgSaving.value = true
  try {
    const res = await api.setConfig(cfg.value)
    cfgReachable.value = res.comfyui_reachable
    if (cfg.value.video_backend === 'api') {
      toast('配置已保存（外接 API 模式不做连通性预检）', 'ok')
    } else if (!cfg.value.comfyui_url) {
      toast('已保存（ComfyUI 地址为空，视频功能不可用）', 'info')
    } else if (res.comfyui_reachable) {
      toast('配置已保存，ComfyUI 连接正常', 'ok')
    } else {
      toast('已保存，但 ComfyUI 连不上：确认实例在跑、start_comfyui.sh 执行过', 'error', 8000)
    }
  } catch (err) {
    toast(err.message, 'error')
  } finally {
    cfgSaving.value = false
  }
}

function toggleConfigPanel() {
  showConfigPanel.value = !showConfigPanel.value
  if (showConfigPanel.value) refreshConfig()
}

// ---------------------------------------------------------------- 生成视频
async function onVideo(shot) {
  const id = shot.shot_id
  if (videoBusy.value.has(id)) return
  try {
    const { job_id } = await api.generateVideo(taskId.value, id)
    videoBusy.value = new Map(videoBusy.value).set(id, { jobId: job_id })
    pollVideo(id)
  } catch (err) {
    toast(err.message, 'error', 8000)
  }
}

async function pollVideo(shotId) {
  const job = videoBusy.value.get(shotId)
  if (!job) return
  let res
  try {
    res = await api.videoJob(job.jobId)
  } catch (err) {
    toast(`第 ${shotId} 镜视频状态查询失败：${err.message}`, 'error')
    const m = new Map(videoBusy.value)
    m.delete(shotId)
    videoBusy.value = m
    return
  }
  if (res.status === 'running') {
    window.setTimeout(() => pollVideo(shotId), 4000)
    return
  }
  const m = new Map(videoBusy.value)
  m.delete(shotId)
  videoBusy.value = m
  if (res.status === 'succeeded') {
    applyShot({ shot_id: shotId, video_path: res.video_path, version: res.version })
    toast(`第 ${shotId} 镜视频已生成`, 'ok', 3200)
  } else {
    toast(`第 ${shotId} 镜视频生成失败：${res.error || '未知错误'}`, 'error', 8000)
  }
}

async function onRenameTask({ taskId: id, title }) {
  try {
    await api.renameTask(id, title)
    const it = history.value.find((t) => t.task_id === id)
    if (it) {
      it.title = title
      it.idea = ''   // 侧栏显示 title || idea，重命名后让新名字顶上去
    }
    if (task.value?.task_id === id && task.value.project) {
      task.value.project.title = title
    }
    toast('已重命名', 'ok', 2000)
  } catch (err) {
    toast(err.message, 'error')
  }
}

async function onDeleteTask(id) {
  const item = history.value.find((t) => t.task_id === id)
  const label = item?.title || item?.idea || id
  if (!window.confirm(`删除「${label}」？\n首帧图和视频会一起移入回收站（outputs/_trash/），不会立刻消失。`)) return
  try {
    await api.deleteTask(id)
    if (taskId.value === id) createNew()
    await refreshTasks()
    toast('已移入回收站', 'ok', 2400)
  } catch (err) {
    toast(err.message, 'error')
  }
}

// ---------------------------------------------------------------- 一键批量 + 导出
async function onBatchVideo() {
  if (batchJob.value || !taskId.value) return
  try {
    const res = await api.startBatchVideos(taskId.value)
    batchJob.value = { jobId: res.job_id, status: 'running', done: 0, total: res.total }
    toast(`开始批量生成 ${res.total} 镜视频`, 'info', 3000)
    pollBatch()
  } catch (err) {
    toast(err.message, 'error', 8000)
  }
}

async function pollBatch() {
  const job = batchJob.value
  if (!job) return
  let res
  try {
    res = await api.videoBatch(job.jobId)
  } catch (err) {
    toast(`批量进度查询失败：${err.message}`, 'error')
    batchJob.value = null
    return
  }
  const prevDone = job.done
  batchJob.value = { jobId: job.jobId, ...res }
  if (res.done !== prevDone && taskId.value) {
    // 每出一镜就拉一次任务，让对应分镜下方的视频即时出现
    task.value = await api.getTask(taskId.value)
  }
  if (res.status === 'running') {
    window.setTimeout(pollBatch, 4000)
    return
  }
  batchJob.value = null
  if (res.errors?.length) {
    toast(`批量完成，${res.total - res.errors.length}/${res.total} 镜成功，失败的在控制台看原因`, 'error', 8000)
  } else {
    toast(`全部 ${res.total} 镜视频生成完成`, 'ok', 5000)
  }
}

async function onExport() {
  if (exportBusy.value || !taskId.value) return
  exportBusy.value = true
  try {
    const res = await api.exportVideo(taskId.value)
    const title = task.value?.project?.title || taskId.value
    const a = document.createElement('a')
    a.href = api.fileUrl(taskId.value, 'export/final.mp4')
    a.download = `${title}.mp4`
    document.body.appendChild(a)
    a.click()
    a.remove()
    toast(`成片已合并（${res.clips} 镜，${(res.size / 1e6).toFixed(1)} MB），开始下载`, 'ok', 5000)
  } catch (err) {
    toast(err.message, 'error', 8000)
  } finally {
    exportBusy.value = false
  }
}

// ---------------------------------------------------------------- 分阶段验收
// 每个阶段完成后停在原地等用户点「继续」，不满意可以改了再继续。
const nextStage = computed(() => {
  const t = task.value
  if (!t || t.status === 'running') return null
  if (t.stage_state === 'directed')
    return {
      key: 'storyboard',
      label: '继续 · 拆解分镜与提示词',
      hint: '角色满意了就继续。下一步拆解分镜，并生成图片提示词、视频提示词和台词',
    }
  if (t.stage_state === 'storyboarded')
    return {
      key: 'images',
      label: '继续 · 生成图片',
      hint: '提示词和台词满意了就继续。只画还没有图片的分镜，改过提示词的分镜会重新画',
    }
  return null
})

async function runNextStage() {
  const st = nextStage.value
  if (!st || busy.value || !taskId.value) return
  busy.value = true
  errorMsg.value = ''
  try {
    if (st.key === 'storyboard') await api.stageStoryboard(taskId.value)
    else await api.stageImages(taskId.value)
    poll()   // 接回任务轮询，进度条会走到对应阶段
  } catch (err) {
    busy.value = false
    toast(err.message, 'error', 8000)
  }
}

const charBusy = ref('')
const charSaving = ref(-1)

async function onRerollCharacter({ index, name }) {
  if (charBusy.value) return
  charBusy.value = name
  try {
    const updated = await api.rerollCharacter(taskId.value, index)
    const chars = task.value?.project?.characters || []
    if (chars[index]) chars[index] = { ...chars[index], ...updated }
    toast(`「${name}」的定妆照已重新生成`, 'ok', 2600)
  } catch (err) {
    toast(`定妆照重生成失败：${err.message}`, 'error', 6000)
  } finally {
    charBusy.value = ''
  }
}

// 编辑角色的名字与锚点提示词。锚点是后续分镜提示词与定妆照的角色一致性来源，
// 改完服务端会标记 stale，前端提示重新生成定妆照。
async function onUpdateCharacter({ index, name, anchor }) {
  const ownerId = taskId.value
  if (charSaving.value >= 0) return
  charSaving.value = index
  try {
    const updated = await api.patchCharacter(ownerId, index, { name, anchor })
    if (taskId.value === ownerId) {
      const chars = task.value?.project?.characters || []
      if (chars[index]) chars[index] = { ...chars[index], ...updated }
    }
    toast(`「${updated.name || name}」的角色设定已保存`, 'ok', 2400)
  } catch (err) {
    toast(`角色设定保存失败：${err.message}`, 'error', 6000)
    if (taskId.value === ownerId) poll()   // 保存失败时拉一次服务端状态，卡片回到真实值
  } finally {
    if (charSaving.value === index) charSaving.value = -1
  }
}

// ---------------------------------------------------------------- 看图
const lightboxItems = computed(() =>
  (task.value?.shots || [])
    .filter((s) => s.image_path)
    .map((s) => ({
      shotId: s.shot_id,
      url: api.imageUrl(taskId.value, s.shot_id, s.version),
      camera: s.camera,
      motion: s.motion,
      duration: s.duration,
      dialogue: s.dialogue,
    })),
)

function openImage(shotId) {
  const i = lightboxItems.value.findIndex((it) => Number(it.shotId) === Number(shotId))
  if (i >= 0) lightbox.value = { open: true, index: i }
}

// ---------------------------------------------------------------- 导出
const canExport = computed(() => Boolean(task.value?.project))
const hasVideos = computed(() => (task.value?.shots || []).some((s) => s.video_path))

function openPreview() {
  window.open(api.fileUrl(taskId.value, 'preview.html'), '_blank')
}

function download(filename) {
  const a = document.createElement('a')
  a.href = api.fileUrl(taskId.value, filename)
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// ---------------------------------------------------------------- 顶栏状态
const statusLabel = computed(() => {
  const t = task.value
  if (!t) return '待开始'
  if (t.status === 'failed') return '失败'
  if (t.status === 'succeeded') return { directed: '待确认故事', storyboarded: '待确认分镜', imaged: '分镜已就绪' }[t.stage_state] || '阶段完成'
  return t.stage || '生成中'
})

const statusKind = computed(() => {
  const t = task.value
  if (!t) return 'idle'
  return t.status === 'running' ? 'running' : t.status === 'failed' ? 'failed' : 'ok'
})

// ---------------------------------------------------------------- 生命周期
onMounted(async () => {
  await refreshTasks()
  refreshConfig()
  // 刷新页面时如果还有任务在跑，自动接回轮询
  const running = history.value.find((t) => t.status === 'running')
  if (running) openTask(running.task_id)
})

onUnmounted(stopPolling)

// 标签页标题跟随当前项目，多标签工作时能认出来
const pageTitle = computed(() => activeView.value === 'library' ? '我的作品' : activeView.value === 'workspace' ? (task.value?.project?.title || '制作工作区') : '创作工作室')
watchEffect(() => {
  document.title = `${pageTitle.value} · 镜序 FRAMEFLOW`
})

watch(showConfigPanel, async (open) => {
  if (open) {
    configOpener = document.activeElement
    await nextTick()
    configDialog.value?.focus()
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
    configOpener?.focus?.()
  }
})

function onConfigKey(event) {
  if (event.key === 'Escape') showConfigPanel.value = false
  if (event.key !== 'Tab') return
  const controls = [...configDialog.value.querySelectorAll('button:not(:disabled), input, select, [tabindex="0"]')]
  const first = controls[0]
  const last = controls[controls.length - 1]
  if (event.shiftKey && (document.activeElement === first || document.activeElement === configDialog.value)) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

// 视频服务连接状态的小圆点：绿=已连接，红=连不上；外接 API 模式不做预检，不显示
const comfyuiDot = computed(() => {
  if (cfg.value.video_backend === 'api' || !cfg.value.comfyui_url) return ''
  return cfgReachable.value === true ? 'ok' : cfgReachable.value === false ? 'failed' : ''
})

const backendHint = computed(() => {
  if (cfg.value.video_backend === 'api') return '外接 API 模式，生成失败会有明确报错'
  return cfgReachable.value === true
    ? '已连接'
    : cfgReachable.value === false
      ? '连不上'
      : '未测试'
})
</script>

<template>
  <div class="shell">
    <AppSidebar
      :history="history"
      :active-id="activeView === 'workspace' ? taskId : ''"
      :active-view="activeView"
      :history-error="historyError"
      @select="openTask"
      @create="createNew"
      @library="openLibrary"
      @configure="toggleConfigPanel"
      @retry="refreshTasks"
      @delete="onDeleteTask"
      @rename="onRenameTask"
    />

    <main class="main">
      <header class="topbar">
        <div class="breadcrumb"><span>工作空间</span><span class="slash">/</span><strong>{{ pageTitle }}</strong></div>
        <div class="actions">
          <span class="collab-label"><svg class="small-spark" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M10 2v16M2 10h16M4.4 4.4l11.2 11.2m0-11.2L4.4 15.6"/></svg> 多智能体协作创作</span>
          <button class="settings-button" aria-label="打开服务配置" @click="toggleConfigPanel">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M3 6h14M3 14h14"/><circle cx="7" cy="6" r="2" fill="currentColor"/><circle cx="13" cy="14" r="2" fill="currentColor"/></svg>
            服务设置
          </button>
        </div>
      </header>

      <div class="content" :class="{ 'workspace-content': activeView === 'workspace' }">
        <template v-if="activeView === 'create'">
          <section class="welcome fade-in" aria-labelledby="welcome-title">
            <div class="welcome-copy">
              <div class="eyebrow"><span></span> FROM A SPARK TO A STORY</div>
              <h1 id="welcome-title">让想象，<br />成为<span class="accent-text">下一幕。</span></h1>
              <p>你的灵感，值得被看见。<br />与 AI 创作团队一起，把故事变成电影。</p>
              <div class="welcome-note"><span class="note-line"></span> 你来执导，镜序让每一帧就位。</div>
            </div>
            <div class="hero-art">
              <img src="/images/jingxu-landscape.webp" alt="晨雾山谷中，一位身着橙色外套的旅人站在山脊上" fetchpriority="high" />
              <div class="frame-corners" aria-hidden="true"></div>
              <span class="art-top mono">FRAME 001 <span>16:9</span></span>
              <div class="art-caption"><span>故事，从这一帧开始。</span><span class="mono">AI VISUAL CONCEPT</span></div>
              <span class="art-label">镜序 / 灵感视觉</span>
            </div>
          </section>

          <section id="creative-brief" class="creation-section fade-in" aria-labelledby="creation-title">
            <div class="section-heading"><h2 id="creation-title"><span class="section-number">01</span> 写下你的故事</h2><span>一个念头，就能开场</span></div>
            <PromptComposer ref="composerRef" :busy="busy" @submit="start" />
            <p class="composer-footnote">从故事设定、角色定妆到分镜画面，每个阶段都由你确认后继续。</p>
          </section>

          <section class="inspiration-section fade-in" aria-labelledby="inspiration-title">
            <div class="section-heading"><h2 id="inspiration-title"><span class="section-number">02</span> 给灵感一个起点</h2><span>选择一个方向，自由续写</span></div>
            <div class="inspiration-grid">
              <button v-for="(item, i) in inspirations" :key="item.title" class="inspiration-card" @click="useInspiration(item)">
                <div class="inspiration-image" :class="item.className"><span class="concept-label mono">CONCEPT / 0{{ i + 1 }}</span></div>
                <div class="inspiration-info"><div><h3>{{ item.title }}</h3><p>{{ item.tag }}</p></div><span class="card-arrow" aria-hidden="true">↗</span></div>
              </button>
            </div>
          </section>
          <footer class="home-footer"><span class="footer-brand">镜序 <span>FRAMEFLOW</span></span><span>故事设定 <i>→</i> 分镜编排 <i>→</i> 画面生成 <i>→</i> 视频成片</span><span class="footer-end">让创作，自然发生。</span></footer>
        </template>

        <template v-if="activeView === 'library'">
          <section class="library-header fade-in"><div><div class="eyebrow">YOUR CREATIVE COLLECTION</div><h1>每个故事，都在这里。</h1><p>回到你的创作现场，继续下一幕。</p></div><button class="primary" @click="createNew">＋ 新建作品</button></section>
          <div class="library-toolbar"><div class="filter-tabs" aria-label="作品状态筛选"><button v-for="option in [{key:'all',label:'全部作品'}, {key:'succeeded',label:'阶段完成'}, {key:'running',label:'生成中'}, {key:'failed',label:'需处理'}]" :key="option.key" :class="{ selected: filter === option.key }" :aria-pressed="filter === option.key" @click="filter = option.key">{{ option.label }}</button></div><input v-model="search" type="search" aria-label="搜索作品" placeholder="搜索作品名称…" /></div>
          <div v-if="historyError" class="error" role="alert">{{ historyError }} <button @click="refreshTasks">重试</button></div>
          <p v-if="historyLoading && !history.length" class="loading-state"><span class="spin"></span> 正在读取作品…</p>
          <div v-else-if="filteredHistory.length" class="project-grid">
            <article v-for="(item, i) in filteredHistory" :key="item.task_id" class="project-tile">
              <button class="project-open" @click="openTask(item.task_id)"><div class="project-cover"><span class="mono">FRAMEFLOW / PROJECT</span><b>{{ String(i + 1).padStart(2, '0') }}</b><span class="project-cover-bottom">{{ item.shots || 0 }} SHOTS <span>↗</span></span></div><div class="project-info"><h2>{{ item.title || item.idea || '未命名作品' }}</h2><div><span><i class="dot" :class="item.status === 'running' ? 'running' : item.status === 'failed' ? 'failed' : 'ok'"></i>{{ item.status === 'running' ? '生成中' : item.status === 'failed' ? '需要处理' : '阶段完成' }}</span><time>{{ formatDate(item.created_at) }}</time></div></div></button>
              <div class="project-tile-footer"><span>{{ item.shots || 0 }} 个分镜</span><button class="quiet danger" :aria-label="`删除作品${item.title || item.idea}`" @click="onDeleteTask(item.task_id)">移入回收站</button></div>
            </article>
          </div>
          <div v-else-if="!historyError" class="empty-library"><span class="empty-frame" aria-hidden="true">＋</span><h2>{{ search || filter !== 'all' ? '没有找到符合条件的作品' : '第一部作品，从一个念头开始' }}</h2><p>{{ search || filter !== 'all' ? '试试其他关键词，或切换作品状态。' : '写下灵感，镜序陪你完成从故事到画面的旅程。' }}</p><button v-if="!search && filter === 'all'" class="primary" @click="createNew">开始创作</button><button v-else @click="search = ''; filter = 'all'">查看全部作品</button></div>
        </template>

        <div v-if="errorMsg && activeView !== 'library'" class="error fade-in" role="alert">
          <div class="erow"><b>遇到一点问题</b><span class="emsg">{{ errorMsg }}</span><button v-if="task?.traceback" class="quiet tiny" @click="showTrace = !showTrace">{{ showTrace ? '收起详情' : '查看错误详情' }}</button></div>
          <pre v-if="showTrace && task?.traceback" class="trace">{{ task.traceback }}</pre>
        </div>

        <template v-if="activeView === 'workspace'">
          <div class="workspace-heading"><div><div class="eyebrow">DIRECTOR’S WORKSPACE</div><h1>{{ task?.project?.title || '正在准备你的故事' }}</h1></div><span class="status-pill" :class="statusKind"><i class="dot" :class="statusKind"></i>{{ statusLabel }}<span v-if="busy" class="spin"></span></span></div>
          <div v-if="!task && busy" class="loading-state"><span class="spin"></span> 正在连接创作现场…</div>
          <section v-if="task" class="statuscard fade-in"><StageRail :status="task.status" :stage="task.stage" :stage-state="task.stage_state || ''" :done="task.done" :total="task.total || task.shots?.length || 0" /></section>
          <section v-if="nextStage" class="statuscard fade-in nextstage"><div><span class="review-label">由你决定下一步</span><p class="nslabel">{{ nextStage.hint }}</p></div><button class="primary" :disabled="busy" @click="runNextStage">{{ nextStage.label }} <span aria-hidden="true">→</span></button></section>
          <ProjectBrief v-if="task?.project" :project="task.project" :task-id="taskId" :busy="task.status === 'running'" :char-busy="charBusy" :char-saving="charSaving" :shots="task.shots || []" :stats="task.stats || {}" @reroll-character="onRerollCharacter" @update-character="onUpdateCharacter" />
          <section v-if="canExport" class="production-toolbar"><div class="production-title"><span class="section-number">STUDIO</span><h2>分镜工作区</h2></div><div class="production-actions"><button class="quiet" @click="openPreview">预览</button><details class="download-menu"><summary>导出数据</summary><div><button @click="download('shots.json')">分镜 JSON</button><button @click="download('project.json')">故事设定 JSON</button></div></details><button :disabled="!hasVideos || exportBusy || busy" @click="onExport"><span v-if="exportBusy" class="spin"></span>{{ exportBusy ? '合并中…' : '合并导出' }}</button><button class="primary" :disabled="!!batchJob || busy || !(task?.shots || []).some(s => s.image_path)" @click="onBatchVideo"><span v-if="batchJob" class="spin"></span>{{ batchJob ? `生成中 ${batchJob.done || 0}/${batchJob.total || 0}` : '批量生成视频' }}</button></div></section>
          <ShotBoard v-if="task" :key="taskId" :shots="task.shots || []" :task-id="taskId" :regenerating="[...regenBusy]" :video-busy="[...videoBusy.keys()]" :running="task.status === 'running'" @patch="onPatch" @regen="onRegen" @video="onVideo" @open-image="openImage" />
        </template>
      </div>
    </main>

    <!-- 服务配置弹窗 -->
    <div v-if="showConfigPanel" class="modal-mask" @click.self="showConfigPanel = false">
      <div ref="configDialog" class="modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabindex="-1" @keydown="onConfigKey">
        <header class="mhead">
          <div><b id="settings-title">连接你的创作引擎</b><p>配置文字、画面与视频服务，为灵感做好准备。</p></div>
          <button class="quiet tiny" aria-label="关闭服务配置" @click="showConfigPanel = false">关闭</button>
        </header>

        <div class="mgroup">
          <div class="mgtitle">
            视频生成用哪个？
            <span class="dot inline" :class="comfyuiDot"></span>
            <em>{{ backendHint }}</em>
          </div>
          <div class="backend-cards">
            <label class="bcard" :class="{ on: cfg.video_backend === 'comfyui' }">
              <input v-model="cfg.video_backend" type="radio" value="comfyui" />
              <span class="bc-main">
                <b>用 ComfyUI 生成</b>
                <em>连接自建或租用的 GPU 实例，灵活调整画质与采样步数。</em>
              </span>
            </label>
            <label class="bcard" :class="{ on: cfg.video_backend === 'api' }">
              <input v-model="cfg.video_backend" type="radio" value="api" />
              <span class="bc-main">
                <b>用外接视频 API 生成</b>
                <em>云端按量付费，填地址和 Key 即可，不用自己部署 ComfyUI</em>
              </span>
            </label>
          </div>

          <template v-if="cfg.video_backend === 'comfyui'">
            <label class="mfield">
              <span>服务地址（实例跑 start_comfyui.sh 后 tunnel_url.txt 里的隧道地址）</span>
              <input
                v-model="cfg.comfyui_url"
                class="url-input mono"
                placeholder="https://xxxx.free.pinggy.net"
                @keydown.enter.prevent="saveConfig"
              />
            </label>
          </template>
          <template v-else>
            <label class="mfield">
              <span>API 地址（硅基流动风格的任务制协议）</span>
              <input v-model="cfg.video_api_url" class="url-input mono" placeholder="https://api.siliconflow.cn/v1" />
            </label>
            <label class="mfield">
              <span>API Key</span>
              <input v-model="cfg.video_api_key" class="url-input mono" type="password" placeholder="sk-..." />
            </label>
            <label class="mfield">
              <span>模型名称</span>
              <input v-model="cfg.video_api_model" class="url-input mono" placeholder="MiniMax/Hailuo-02" />
            </label>
            <p class="chint">支持硅基流动风格的异步任务接口，首帧图以 base64 提交。其他厂商需适配对应协议。</p>
          </template>
        </div>

        <div class="mgroup">
          <div class="mgtitle">文本模型 · DeepSeek</div>
          <label class="mfield">
            <span>API 地址</span>
            <input v-model="cfg.text_base_url" class="url-input" placeholder="https://api.deepseek.com" />
          </label>
          <label class="mfield">
            <span>API Key</span>
            <input v-model="cfg.text_api_key" class="url-input mono" type="password" autocomplete="off" placeholder="sk-..." />
          </label>
          <label class="mfield">
            <span>模型名称</span>
            <input v-model="cfg.text_model" class="url-input mono" placeholder="deepseek-flash" />
          </label>
        </div>

        <div class="mgroup">
          <div class="mgtitle">图片模型 · ModelScope</div>
          <label class="mfield">
            <span>API 地址</span>
            <input v-model="cfg.image_base_url" class="url-input mono" placeholder="https://api-inference.modelscope.cn/v1" />
          </label>
          <label class="mfield">
            <span>API Key（访问令牌）</span>
            <input v-model="cfg.image_api_key" class="url-input mono" type="password" autocomplete="off" placeholder="ms-..." />
          </label>
          <label class="mfield">
            <span>模型名称</span>
            <input v-model="cfg.image_model" class="url-input mono" placeholder="Tongyi-MAI/Z-Image-Turbo" />
          </label>
        </div>

        <div class="mgroup" v-if="cfg.video_backend === 'comfyui'">
          <div class="mgtitle">视频画质 · 速度与质量的取舍</div>
          <label class="mfield">
            <span>画质（总像素）— 越低越快</span>
            <select v-model="cfg.video_megapixels" class="url-input">
              <option value="0.4">0.4MP · 最快</option>
              <option value="0.5">0.5MP · 快（推荐）</option>
              <option value="0.7">0.7MP · 均衡</option>
              <option value="0.9">0.9MP · 标准</option>
              <option value="0.98">0.98MP · 最清晰</option>
            </select>
          </label>
          <label class="mfield">
            <span>采样步数 — 越少越快</span>
            <select v-model="cfg.video_steps" class="url-input">
              <option value="4">4 步 · 最快（配 4 步 LoRA）</option>
              <option value="6">6 步</option>
              <option value="8">8 步 · 标准（配 8 步 LoRA）</option>
              <option value="12">12 步 · 慢</option>
            </select>
          </label>
          <label class="mfield">
            <span>加速 LoRA</span>
            <select v-model="cfg.video_lora" class="url-input mono">
              <option value="minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors">4 步 LoRA · 最快</option>
              <option value="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors">8 步 LoRA · 均衡</option>
            </select>
          </label>
          <p class="chint">视频画幅自动跟随首帧图，最终尺寸以生成服务的支持范围为准。</p>
        </div>

        <footer class="mfoot">
          <span class="mhint">配置保存在项目根目录 service_config.json，保存后立即生效，无需重启服务。</span>
          <button class="primary" :disabled="cfgSaving" @click="saveConfig">
            <span v-if="cfgSaving" class="spin"></span>
            {{ cfgSaving ? '保存中…' : '保存并测试' }}
          </button>
        </footer>
      </div>
    </div>

    <ImageLightbox
      v-if="lightbox.open"
      :items="lightboxItems"
      :index="lightbox.index"
      @close="lightbox.open = false"
      @change="lightbox.index = $event"
    />

    <ToastStack :toasts="toasts" @dismiss="dismissToast" />
  </div>
</template>

<style scoped>
.shell { display: flex; align-items: flex-start; min-height: 100vh; }
.main { flex: 1; min-width: 0; }
.topbar { height: 74px; padding: 0 42px; display: flex; align-items: center; gap: 16px; border-bottom: 1px solid var(--line); }
.breadcrumb { display: flex; align-items: center; gap: 14px; font-size: 12px; color: var(--fg-3); min-width: 0; }
.breadcrumb strong { color: var(--fg); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.breadcrumb > span { flex-shrink: 0; }
.slash { color: #b9bdb2; }
.actions { margin-left: auto; display: flex; align-items: center; gap: 24px; flex-shrink: 0; }
.collab-label { display: flex; align-items: center; gap: 7px; color: var(--fg-2); font-size: 11px; }
.small-spark { color: var(--accent); width: 17px; height: 17px; }
.settings-button { display: flex; align-items: center; gap: 7px; background: transparent; font-size: 12px; padding: 7px 11px; }
.settings-button svg { width: 16px; height: 16px; }
.content { width: 100%; max-width: 1310px; margin: auto; padding: 40px 48px 24px; display: flex; flex-direction: column; gap: 32px; }
.welcome { display: grid; grid-template-columns: 1fr 1.15fr; align-items: center; gap: 40px; padding: 7px 0 17px; }
.eyebrow { font-family: var(--font-mono); font-size: 10px; font-weight: 500; letter-spacing: 1.8px; color: var(--fg-2); display: flex; align-items: center; gap: 9px; }
.eyebrow > span { width: 6px; height: 6px; background: var(--accent); border-radius: 50%; }
h1 { margin: 20px 0 18px; font-size: clamp(34px, 3.5vw, 54px); line-height: 1.38; letter-spacing: -2px; font-weight: 600; }
.accent-text { color: var(--accent); }
.welcome-copy > p { margin: 0; font-size: 13px; color: var(--fg-2); line-height: 1.95; }
.welcome-note { display: flex; align-items: center; gap: 8px; font-size: 10px; color: var(--fg-3); margin-top: 25px; }
.note-line { width: 21px; height: 1px; background: var(--accent); }
.hero-art { position: relative; aspect-ratio: 1.65; border-radius: 10px; background: #283933; margin: 8px 0 18px; isolation: isolate; box-shadow: 0 15px 35px #25382b12; }
.hero-art > img { width: 100%; height: 100%; object-fit: cover; display: block; border-radius: 10px; }
.hero-art::after { content: ''; position: absolute; inset: 0; border-radius: inherit; background: linear-gradient(180deg, #152b2620, transparent 40%, #0b211ad4); z-index: 0; }
.frame-corners { position: absolute; inset: 14px; border: 1px solid #ffffff45; z-index: 1; clip-path: polygon(0 0, 22px 0, 22px 1px, calc(100% - 22px) 1px, calc(100% - 22px) 0, 100% 0, 100% 22px, calc(100% - 1px) 22px, calc(100% - 1px) calc(100% - 22px), 100% calc(100% - 22px), 100% 100%, calc(100% - 22px) 100%, calc(100% - 22px) calc(100% - 1px), 22px calc(100% - 1px), 22px 100%, 0 100%, 0 calc(100% - 22px), 1px calc(100% - 22px), 1px 22px, 0 22px); }
.art-top { position: absolute; top: 24px; left: 26px; right: 26px; display: flex; justify-content: space-between; font-size: 9px; letter-spacing: 1.3px; color: #fff; z-index: 1; }
.art-caption { position: absolute; bottom: 27px; left: 27px; z-index: 1; color: #fff; display: flex; flex-direction: column; gap: 6px; font-size: 16px; letter-spacing: 2px; }
.art-caption .mono { font-size: 8px; opacity: .65; letter-spacing: 2px; }
.art-label { position: absolute; right: 0; bottom: -24px; font-size: 9px; color: var(--fg-3); letter-spacing: 1px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 15px; margin-bottom: 16px; }
.section-heading h2 { display: flex; align-items: center; gap: 11px; font-size: 15px; font-weight: 600; margin: 0; }
.section-number { color: var(--accent); font: 10px var(--font-mono); letter-spacing: 1px; }
.section-heading > span { font-size: 11px; color: var(--fg-3); }
.composer-footnote { margin: 11px 0 0; font-size: 10px; text-align: center; color: var(--fg-3); }
.inspiration-section { margin-top: 1px; }
.inspiration-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.inspiration-card { padding: 0; text-align: left; overflow: hidden; border-radius: 12px; background: var(--surface); }
.inspiration-card:hover:not(:disabled) { transform: translateY(-4px); background: var(--surface); border-color: #c4cabb; box-shadow: 0 8px 22px #25382b0d; }
.inspiration-image { height: 146px; background-size: cover; background-position: center; position: relative; }
.inspiration-image::after { content: ''; position: absolute; inset: 0; background: linear-gradient(#091e2820, transparent); }
.inspiration-image.mountain { background-image: url('/images/inspiration-mountain.webp'); background-position: center 51%; }
.inspiration-image.neon { background-image: url('/images/inspiration-neon.webp'); background-position: center 62%; }
.inspiration-image.summer { background-image: url('/images/inspiration-summer.webp'); background-position: center 52%; }
.concept-label { position: absolute; z-index: 1; left: 12px; top: 11px; color: white; font-size: 8px; letter-spacing: 1.4px; text-shadow: 0 1px 4px #0008; }
.inspiration-info { padding: 13px 16px 14px; display: flex; align-items: center; justify-content: space-between; }
.inspiration-info h3 { margin: 0 0 2px; font-size: 14px; font-weight: 600; }
.inspiration-info p { margin: 0; font-size: 10px; color: var(--fg-3); }
.card-arrow { width: 28px; height: 28px; border: 1px solid var(--line); border-radius: 50%; display: grid; place-items: center; color: var(--fg-2); font-size: 16px; }
.home-footer { padding: 22px 0 0; border-top: 1px solid var(--line); color: var(--fg-3); font-size: 10px; display: flex; align-items: center; justify-content: space-between; gap: 15px; }
.footer-brand { font-size: 13px; color: var(--fg-2); font-weight: 600; display: flex; gap: 9px; align-items: center; }
.footer-brand span { font: 8px var(--font-mono); letter-spacing: 1px; color: var(--fg-3); }
.home-footer i { font-style: normal; margin: 0 8px; color: #a7ada0; }
/* Actual projects, with typographic covers rather than fabricated generated images. */
.library-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 20px 0 12px; }
.library-header h1 { font-size: 34px; margin: 13px 0 8px; letter-spacing: -1px; }
.library-header p { margin: 0; color: var(--fg-2); font-size: 13px; }
.library-header > button { flex-shrink: 0; }
.library-toolbar { display: flex; justify-content: space-between; gap: 20px; align-items: center; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
.library-toolbar > input { width: 220px; font-size: 12px; background: transparent; }
.filter-tabs { display: flex; gap: 5px; }
.filter-tabs button { font-size: 12px; padding: 7px 12px; border-color: transparent; color: var(--fg-2); background: transparent; }
.filter-tabs button.selected { color: var(--accent); background: var(--accent-dim); }
.project-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 22px; }
.project-tile { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; background: var(--surface); }
.project-open { padding: 0; display: block; text-align: left; border: 0; border-radius: 0; width: 100%; background: transparent; }
.project-open:hover:not(:disabled) { background: #fcfcfa; }
.project-cover { padding: 20px; background: #e3e7dc; background-image: repeating-linear-gradient(90deg, transparent, transparent 39px, #83907012 40px), repeating-linear-gradient(0deg, transparent, transparent 39px, #83907012 40px); color: #4b5c42; display: flex; flex-direction: column; }
.project-cover > .mono { font-size: 8px; letter-spacing: 2px; }
.project-cover b { font-size: 66px; font-weight: 300; line-height: 1.5; font-family: Georgia, serif; }
.project-cover-bottom { display: flex; align-items: center; justify-content: space-between; font: 9px var(--font-mono); letter-spacing: 1px; }
.project-cover-bottom > span { font-size: 22px; }
.project-info { padding: 18px; }
.project-info h2 { font-size: 17px; margin: 0 0 14px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }
.project-info > div { display: flex; justify-content: space-between; gap: 10px; font-size: 11px; color: var(--fg-3); }
.project-info > div > span { display: flex; align-items: center; gap: 6px; }
.project-tile-footer { border-top: 1px solid var(--line-soft); margin: 0 18px; padding: 8px 0; display: flex; justify-content: space-between; align-items: center; font-size: 10px; color: var(--fg-3); }
.project-tile-footer button { font-size: 10px; padding: 4px 6px; }
.empty-library { padding: 72px 20px; text-align: center; color: var(--fg-2); }
.empty-library h2 { font-size: 19px; font-weight: 500; color: var(--fg); }
.empty-library p { font-size: 13px; margin-bottom: 24px; }
.empty-frame { display: grid; place-items: center; width: 72px; height: 58px; margin: auto; border: 1px solid #bcc5b3; border-radius: 10px; font-size: 26px; color: var(--accent); transform: rotate(-6deg); }
/* Production workspace */
.workspace-content { gap: 22px; max-width: 1450px; }
.workspace-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.workspace-heading h1 { font-size: 30px; margin: 10px 0 0; letter-spacing: -.7px; }
.status-pill { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border: 1px solid var(--line); border-radius: 30px; font-size: 11px; white-space: nowrap; }
.status-pill.ok { color: var(--mint); background: var(--mint-dim); border-color: transparent; }
.status-pill.failed { color: var(--danger); background: var(--danger-dim); border-color: transparent; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--fg-3); flex: none; }
.dot.running { background: var(--accent); }
.dot.ok { background: var(--mint); }
.dot.failed { background: var(--danger); }
.dot.inline { vertical-align: 1px; margin-left: 5px; }
.statuscard { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); padding: 23px 25px; }
.nextstage { display: flex; align-items: center; justify-content: space-between; gap: 24px; border-color: #edcbb8; background: #fcf4ec; }
.review-label { font-size: 12px; font-weight: 600; color: var(--accent); }
.nslabel { font-size: 12px; color: var(--fg-2); margin: 6px 0 0; }
.nextstage button { flex: none; font-size: 12px; }
.production-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 15px; margin-top: 8px; }
.production-title { display: flex; align-items: center; gap: 12px; }
.production-title h2 { font-size: 16px; margin: 0; white-space: nowrap; }
.production-actions { display: flex; align-items: center; gap: 8px; }
.production-actions button, .download-menu summary { font-size: 12px; padding: 7px 12px; }
.download-menu { position: relative; }
.download-menu summary { cursor: pointer; color: var(--fg-2); }
.download-menu > div { position: absolute; top: 100%; right: 0; min-width: 155px; padding: 6px; background: var(--surface); border: 1px solid var(--line); box-shadow: 0 6px 20px #20262118; border-radius: 10px; z-index: 5; display: grid; gap: 4px; }
.download-menu button { border: 0; text-align: left; white-space: nowrap; }
.loading-state { padding: 60px 20px; color: var(--fg-2); text-align: center; }
.error { padding: 16px 20px; border: 1px solid #eec9c0; border-radius: 12px; background: var(--danger-dim); font-size: 13px; }
.erow { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.erow b { color: var(--danger); font-size: 13px; }
.emsg { flex: 1; min-width: 0; word-break: break-word; }
.tiny { font-size: 11px; padding: 5px 9px; }
.trace { margin: 14px 0 0; padding: 14px; border-radius: 8px; background: #f8e4dd; font: 11px/1.8 var(--font-mono); max-height: 260px; overflow: auto; white-space: pre-wrap; }
/* Service settings */
.modal-mask { position: fixed; inset: 0; z-index: 50; padding: 40px 20px; background: #17221980; backdrop-filter: blur(5px); display: flex; align-items: flex-start; justify-content: center; overflow-y: auto; }
.modal { width: 100%; max-width: 720px; background: var(--bg); border: 1px solid var(--line); border-radius: 20px; padding: 28px; box-shadow: 0 30px 100px #0c190d30; outline: none; }
.mhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 22px; gap: 20px; }
.mhead b { font-size: 23px; font-weight: 600; }
.mhead p { margin: 3px 0 0; font-size: 12px; color: var(--fg-2); }
.mgroup { border: 1px solid var(--line); border-radius: 12px; padding: 20px; margin-top: 14px; background: var(--surface); }
.mgtitle { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px; margin-bottom: 14px; }
.mgtitle em { font-style: normal; font-weight: 400; font-size: 11px; color: var(--fg-3); }
.backend-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
.bcard { display: flex; align-items: flex-start; gap: 8px; padding: 13px; background: var(--surface); border: 1px solid var(--line); border-radius: 9px; cursor: pointer; }
.bcard.on { border-color: var(--accent); background: var(--accent-dim); }
.bcard input { width: 14px; height: 14px; margin: 3px 0 0; flex: none; }
.bc-main { display: flex; flex-direction: column; gap: 5px; }
.bc-main b { font-size: 12px; }
.bc-main em { font-size: 10px; font-style: normal; color: var(--fg-2); line-height: 1.8; }
.mfield { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
.mfield > span { font-size: 11px; color: var(--fg-2); }
.url-input { font-size: 12px; min-width: 0; background: #fdfdfa; }
.chint { font-size: 11px; margin: 12px 0 0; color: var(--fg-3); }
.mfoot { display: flex; align-items: center; justify-content: space-between; gap: 25px; margin-top: 23px; }
.mhint { font-size: 10px; color: var(--fg-3); }
.mfoot button { white-space: nowrap; }
@media (min-width: 1600px) { .content { padding-top: 50px; } .welcome { gap: 80px; } .hero-art { aspect-ratio: 1.8; } }
@media (max-width: 1150px) { .content { padding: 28px; } .topbar { padding: 0 28px; } .welcome { gap: 24px; grid-template-columns: 1fr 1fr; } h1 { font-size: 40px; } .hero-art { aspect-ratio: 1.3; } .footer-end { display: none; } .project-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .production-toolbar { flex-wrap: wrap; } .collab-label { display: none; } }
@media (max-width: 900px) { .welcome { gap: 20px; } h1 { font-size: 34px; } .eyebrow { font-size: 8px; letter-spacing: 1.1px; } .art-caption { font-size: 13px; left: 20px; } .welcome-note { font-size: 9px; } .inspiration-grid { gap: 12px; } .inspiration-image { height: 114px; } .inspiration-info { padding: 12px; } .card-arrow { display: none; } .nextstage { flex-wrap: wrap; } .home-footer { flex-wrap: wrap; } .filter-tabs button { padding: 6px 8px; } .library-toolbar { flex-wrap: wrap; } }
@media (max-width: 760px) { .shell { flex-direction: column; } .main { width: 100%; } .topbar { height: 56px; padding: 0 20px; } .content { padding: 26px 20px; gap: 28px; } .actions { gap: 8px; } .settings-button { font-size: 11px; padding: 6px 8px; } .welcome { grid-template-columns: 1fr 1fr; padding-top: 0; } .hero-art { aspect-ratio: 1.15; } .welcome-copy > p { font-size: 11px; } .welcome-note { display: none; } .eyebrow { font-size: 7px; letter-spacing: .7px; } h1 { margin: 16px 0 12px; font-size: 34px; } .art-caption { left: 18px; bottom: 19px; font-size: 11px; letter-spacing: 1px; } .art-caption .mono { font-size: 6px; } .art-top { left: 19px; right: 19px; top: 19px; font-size: 7px; } .section-heading h2 { font-size: 14px; } .section-heading > span { font-size: 10px; } .production-actions { flex-wrap: wrap; } .production-actions button { font-size: 11px; } .statuscard { padding: 18px; } .workspace-heading h1 { font-size: 24px; } .modal-mask { padding: 16px 10px; } .modal { padding: 20px; } .mgroup { padding: 16px; } .mfoot { gap: 12px; } .library-header h1 { font-size: 26px; } }
@media (max-width: 480px) { .welcome { grid-template-columns: 1fr; gap: 20px; } .welcome-copy { position: relative; } h1 { font-size: 42px; } .welcome-copy > p { font-size: 12px; } .eyebrow { font-size: 9px; letter-spacing: 1.3px; } .hero-art { aspect-ratio: 1.85; margin: 0 0 16px; } .art-caption { font-size: 13px; } .inspiration-grid { grid-template-columns: 1fr; gap: 12px; } .inspiration-card { display: flex; } .inspiration-image { width: 42%; height: 108px; flex: none; } .inspiration-info { flex: 1; padding: 16px; } .card-arrow { display: grid; } .inspiration-info h3 { font-size: 15px; } .home-footer { font-size: 9px; gap: 12px; } .home-footer i { margin: 0 4px; } .project-grid { grid-template-columns: 1fr; } .library-header { flex-direction: column; align-items: flex-start; } .library-toolbar > input { width: 100%; } .backend-cards { grid-template-columns: 1fr; } .mfoot { flex-direction: column; align-items: stretch; } .breadcrumb { gap: 8px; font-size: 11px; } .breadcrumb > span:first-child, .breadcrumb .slash { display: none; } .composer-footnote { text-align: left; line-height: 1.8; } }
</style>

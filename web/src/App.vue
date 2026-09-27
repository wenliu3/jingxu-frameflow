<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { api } from './api'
import AppSidebar from './components/AppSidebar.vue'
import CreateWorkbench from './components/CreateWorkbench.vue'
// AiAssistant.vue 暂时不挂：2026-09-17 斌哥要求把「新建作品」页右侧的 AI 助手撤掉。
// 组件文件保留，要恢复就把这一行 import 和 create 视图里那段 <AiAssistant> 一起加回来。
// MaterialStudio.vue 已停用：2026-09-15 起「素材工坊」与「新建作品」页共用 CreateWorkbench，
// 不再 import 它（留着会被打进包里）。文件本身保留备查。
import PromptComposer from './components/PromptComposer.vue'
import StageRail from './components/StageRail.vue'
import ProjectBrief from './components/ProjectBrief.vue'
import ShotBoard from './components/ShotBoard.vue'
import BlockBoard from './components/BlockBoard.vue'
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
// 分块流程的忙碌集合：块首帧图 / 块提示词 / 块视频各自一份，按 block_id 记。
const blockImgBusy = ref(new Set())
const blockPromptBusy = ref(new Set())
const blockVideoBusy = ref(new Map())
// 服务配置（ComfyUI / 文本模型 / 图片模型 / 音频模型），弹窗里填，后端持久化实时生效
const cfg = ref({
  video_backend: 'comfyui',
  video_mode: 'auto',
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
  video_workflow: 'i2v',
  video_timeout_s: '3600',
  video_api_url: '',
  video_api_key: '',
  video_api_model: '',
  audio_provider: 'edge',
  audio_base_url: '',
  audio_api_key: '',
  audio_model: '',
})
const showConfigPanel = ref(false)
const cfgSaving = ref(false)
const cfgReachable = ref(null)
// 音色清单的刷新信号：服务配置保存后 +1，角色卡据此重新拉 /api/voices
// （音频后端换了，音色池就不是同一批了）
const voiceEpoch = ref(0)
// 一键批量：shot 间串行出片，轮询拿 done/total，每有新完成就刷新任务
const batchJob = ref(null)
const exportBusy = ref(false)
const history = ref([])
const historyError = ref('')
const historyLoading = ref(false)
const composerRef = ref(null)
const activeView = ref('create')

// 工作台的三个 tab（`studioMode` 是历史名字，现在它是「工作台视图」而不是"模式"）：
//   studio     素材工坊 —— 先备素材 → 手选 → 手写一段描述 → 生成一段视频
//   storyboard 分镜工作台 —— 一句创意 → 自动拆镜 → 批量出图出片（原有流程）
//   records    生成记录 —— 这个作品出过的所有片，点开看每段用了什么素材/提示词/参数
//
// ⚠️ **为什么 records 要做成 tab 而不是标题行里的一颗按钮**（2026-09-18 斌哥定）：
// 那颗按钮夹在「模式切换」和「状态胶囊」中间，可那两样说的是"我在看什么 / 什么状态"，
// 而生成记录是"这个作品产出过什么" —— 三类东西挤一行，谁都不像谁。
// 做成第三个 tab 之后：tab 行本来就一直可见（满足"两个 tab 都要在"这条），
// 记录也能占满整个内容区，不用再把 01/02 往下推。
//
// 三者是同一份数据的三种看法，共用同一个作品。默认进素材工坊，随时可切。
const STUDIO_KEY = 'workspace.mode'
const STUDIO_MODES = ['studio', 'storyboard', 'records']
const studioMode = ref((() => {
  try {
    const v = localStorage.getItem(STUDIO_KEY)
    return STUDIO_MODES.includes(v) ? v : 'studio'
  } catch {
    return 'studio'
  }
})())

function setStudioMode(mode) {
  studioMode.value = STUDIO_MODES.includes(mode) ? mode : 'studio'
  try {
    localStorage.setItem(STUDIO_KEY, studioMode.value)
  } catch {
    /* 隐私模式写不了，忽略即可 */
  }
}

// 素材工坊里做完批量补齐后，任务的图/音色路径都变了，重新拉一次任务
async function onRefreshTask() {
  if (!taskId.value) return
  try {
    task.value = await api.getTask(taskId.value)
    await refreshTasks()
  } catch (err) {
    toast(`刷新素材状态失败：${err.message}`, 'error', 5000)
  }
}

// ---------------------------------------------------------------- 素材优先：空白作品
// 「新建作品」页是先攒素材、后写故事的流程，而素材（project.characters / assets）
// 必须有作品才挂得住。所以所有素材操作都先过这里：没有作品就建一个空白的。
//
// draftBusy 是防重入闸：连点两个按钮时不该建出两个作品（后端每次都会造一个新 task_id）。
const draftBusy = ref(false)
async function ensureDraft() {
  if (taskId.value && task.value) return taskId.value
  if (draftBusy.value) return ''
  draftBusy.value = true
  try {
    const res = await api.createDraft()
    taskId.value = res.task_id
    task.value = await api.getTask(res.task_id)
    await refreshTasks()
    return res.task_id
  } catch (err) {
    toast(`新建作品失败：${err.message}`, 'error', 6000)
    return ''
  } finally {
    draftBusy.value = false
  }
}

// 工作台场景下作品一定已存在，「素材工坊」里每次落库操作只需拿当前 id。
// （「新建作品」页用的是 ensureDraft：还没有作品就现建一个空白草稿。）
async function ensureWorkbenchTask() {
  return taskId.value
}

// 「新建作品」页里改视频后端 / 工作流地址：只改这两项，其余保持原样。
// 必须整体提交 —— /api/config 是整份覆盖，只发一个字段会把别的配置清空。
async function onPatchConfig(patch) {
  cfg.value = { ...cfg.value, ...patch }
  try {
    await api.setConfig(cfg.value)
  } catch (err) {
    toast(`配置保存失败：${err.message}`, 'error', 5000)
  }
}

// 素材工坊的「编辑」：完整的编辑表单（改名字/锚点/音色）只在分镜工作台里有，
// 所以这里是切过去而不是新造一套重复表单 —— 一套表单两处维护必然漂开。
function onEditMaterial({ name }) {
  setStudioMode('storyboard')
  toast(`编辑「${name}」的表单在分镜工作台，已替你切过去`, 'ok', 3600)
}
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
  // 灵感卡住在「我的作品」页的创意入口里，点了就往那个输入框里填，不再跳页
  activeView.value = 'library'
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

const lightbox = ref({ open: false, index: 0, kind: '' })
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
      let message
      if (data.flow === 'blocks') {
        message = data.stage_note
          || (data.stage_state === 'directed'
            ? '故事与资产已就绪，开始逐块编排（每块 10 秒）'
            : `块已就绪，共 ${data.blocks?.length || 0} 块`)
      } else {
        const rendered = (data.shots || []).filter((s) => s.image_path).length
        message = data.stage_state === 'directed' ? '故事与角色已就绪，确认后可继续编排分镜' : data.stage_state === 'storyboarded' ? '分镜与提示词已就绪，确认后可继续生成画面' : `分镜就绪，共 ${data.shots?.length || 0} 镜，已出图 ${rendered} 张`
      }
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
    blockImgBusy.value = new Set()
    blockPromptBusy.value = new Set()
    blockVideoBusy.value = new Map()
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

// ---------------------------------------------------------------- 分块操作
// 一块 = 一条 10s 视频。AI 下一块走任务级轮询（busy 由 poll 接管），
// 出图 / 提示词是同步接口按块加锁，视频是异步 job 单独轮询。

function applyBlock(updated) {
  const list = task.value?.blocks
  if (!list) return
  const i = list.findIndex((b) => Number(b.block_id) === Number(updated.block_id))
  if (i >= 0) list[i] = { ...list[i], ...updated }
  else list.push(updated)
  list.sort((a, b) => Number(a.block_id) - Number(b.block_id))
}

async function onAiNextBlock(instruction = '') {
  if (busy.value || !taskId.value) return
  errorMsg.value = ''
  busy.value = true
  try {
    await api.aiNextBlock(taskId.value, instruction)
    poll()   // 接回任务轮询：running → 分块完成自动刷新 blocks
  } catch (err) {
    busy.value = false
    toast(err.message, 'error', 8000)
  }
}

async function onAddBlock() {
  if (!taskId.value) return
  try {
    const created = await api.addBlock(taskId.value, { summary: '', duration: 10 })
    applyBlock(created)
  } catch (err) {
    toast(err.message, 'error', 8000)
  }
}

async function onPatchBlock({ blockId, patch }) {
  const ownerId = taskId.value
  try {
    const updated = await api.patchBlock(ownerId, blockId, patch)
    if (taskId.value === ownerId) applyBlock(updated)
  } catch (err) {
    toast(err.message, 'error')
    if (taskId.value === ownerId) poll()
  }
}

async function onDeleteBlock(block) {
  if (!window.confirm(`删除第 ${block.block_id} 块？\n它的首帧图、视频和尾帧会一起删除。`)) return
  try {
    await api.deleteBlock(taskId.value, block.block_id)
    const list = task.value?.blocks
    if (list) {
      const i = list.findIndex((b) => Number(b.block_id) === Number(block.block_id))
      if (i >= 0) list.splice(i, 1)
    }
    toast(`第 ${block.block_id} 块已删除`, 'ok', 2400)
  } catch (err) {
    toast(err.message, 'error', 6000)
  }
}

async function onBlockPrompt(block) {
  const id = block.block_id
  if (blockPromptBusy.value.has(id)) return
  blockPromptBusy.value = new Set(blockPromptBusy.value).add(id)
  try {
    const updated = await api.writeBlockPrompt(taskId.value, id)
    applyBlock(updated)
    toast(`第 ${id} 块提示词已生成`, 'ok', 2600)
  } catch (err) {
    toast(`第 ${id} 块提示词生成失败：${err.message}`, 'error', 8000)
  } finally {
    const next = new Set(blockPromptBusy.value)
    next.delete(id)
    blockPromptBusy.value = next
  }
}

async function onBlockImage(block) {
  const id = block.block_id
  if (blockImgBusy.value.has(id)) return
  blockImgBusy.value = new Set(blockImgBusy.value).add(id)
  try {
    const updated = await api.blockImage(taskId.value, id)
    applyBlock(updated)
    toast(`第 ${id} 块首帧图已生成`, 'ok', 2600)
  } catch (err) {
    toast(`第 ${id} 块出图失败：${err.message}`, 'error', 8000)
  } finally {
    const next = new Set(blockImgBusy.value)
    next.delete(id)
    blockImgBusy.value = next
  }
}

async function onBlockVideo(block) {
  const id = block.block_id
  if (blockVideoBusy.value.has(id)) return
  try {
    const { job_id } = await api.generateBlockVideo(taskId.value, id)
    blockVideoBusy.value = new Map(blockVideoBusy.value).set(id, { jobId: job_id })
    pollBlockVideo(id)
  } catch (err) {
    toast(err.message, 'error', 8000)
  }
}

async function pollBlockVideo(blockId) {
  const job = blockVideoBusy.value.get(blockId)
  if (!job) return
  let res
  try {
    res = await api.videoJob(job.jobId)
  } catch (err) {
    toast(`第 ${blockId} 块视频状态查询失败：${err.message}`, 'error')
    const m = new Map(blockVideoBusy.value)
    m.delete(blockId)
    blockVideoBusy.value = m
    return
  }
  if (res.status === 'running') {
    window.setTimeout(() => pollBlockVideo(blockId), 4000)
    return
  }
  const m = new Map(blockVideoBusy.value)
  m.delete(blockId)
  blockVideoBusy.value = m
  if (res.status === 'succeeded') {
    applyBlock({
      block_id: blockId,
      video_path: res.video_path,
      last_frame: res.last_frame || '',
      version: res.version,
    })
    toast(`第 ${blockId} 块视频已生成，尾帧已提取`, 'ok', 3600)
  } else {
    toast(`第 ${blockId} 块视频生成失败：${res.error || '未知错误'}`, 'error', 8000)
  }
}

// 块的看图灯箱：首帧图与尾帧各自成组，可以左右翻
function blockLightboxItems(field) {
  return (task.value?.blocks || [])
    .filter((b) => b[field])
    .map((b) => ({
      blockId: b.block_id,
      url: field === 'last_frame'
        ? api.blockLastFrameUrl(taskId.value, b.block_id, b.version)
        : api.blockImageUrl(taskId.value, b.block_id, b.version),
      camera: b.camera,
      motion: b.motion,
      duration: b.duration,
    }))
}

function openBlockImage(block) {
  const items = blockLightboxItems('image_path')
  const i = items.findIndex((it) => Number(it.blockId) === Number(block.block_id))
  if (i >= 0) lightbox.value = { open: true, index: i, kind: 'block-img' }
}

function openBlockFrame(block) {
  const items = blockLightboxItems('last_frame')
  const i = items.findIndex((it) => Number(it.blockId) === Number(block.block_id))
  if (i >= 0) lightbox.value = { open: true, index: i, kind: 'block-frame' }
}

// ---------------------------------------------------------------- ComfyUI 地址
async function refreshConfig() {
  try {
    // 合并而不是整体替换：后端进程比前端代码旧时（比如刚改完还没重启），
    // 返回的对象里没有新增字段，整体替换会把它们冲成 undefined，
    // 界面上就表现为「刚加的配置项不见了」。合并能让默认值兜住。
    cfg.value = { ...cfg.value, ...(await api.getConfig()) }
  } catch {
    /* 拉不到不影响其它功能 */
  }
}

async function saveConfig() {
  cfgSaving.value = true
  try {
    const res = await api.setConfig(cfg.value)
    cfgReachable.value = res.comfyui_reachable
    // 音频后端可能被换了（edge ↔ minimax），音色池跟着变 —— 让角色卡重新拉一次音色清单，
    // 否则下拉里还是上一个后端的音色，选了后端会认不出来
    voiceEpoch.value += 1
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

// 视频输入模式（三种，没有「自动」）。只影响本地怎么喂图，与连接无关，
// 所以单独存一次并给个轻提示；不走 saveConfig——那个会做 ComfyUI 探活，连不上要干等 8 秒超时。
const MODE_LABELS = { i2v: '分镜图生视频', flf: '首尾帧', portrait: '无分镜图生视频' }

// 工作流 ↔ LoRA/步数 是**配套**的（底模不同，混用不报错但出片糊）。
// ⚠️ 2026-09-19：这两张表必须**覆盖同一批 LoRA 名**，而且双向都要能对上 ——
//    斌哥一直是用「加速 LoRA」那个下拉来表达"我要 i2v"的（选项上就写着"（I2V / 首尾帧 专用）"），
//    只换 LoRA 不换工作流，就是他报的"我配置明明选了 i2v，界面还是 ref2va"。
//    后端 _lora_workflow_error 是最后一道网，两边规则保持一致。
const WF_TO_LORA = {
  i2v: { lora: 'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors', steps: '8' },
  ref2va: { lora: 'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors', steps: '4' },
}
const LORA_TO_WF = {
  'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors': { wf: 'i2v', steps: '4' },
  'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors': { wf: 'i2v', steps: '8' },
  'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors': { wf: 'ref2va', steps: '4' },
}

function wfLabel(wf) {
  return wf === 'ref2va' ? 'Ref2VA · 全能参考' : 'I2V · 首帧 / 首尾帧'
}

// 工作流 → LoRA/步数
// 步数要和 LoRA 的蒸散步数对上（2026-09-19 斌哥问"ref2v 只能 4 步吗"）：
// LoRA 是照着某个步数档蒸馏出来的，填别的步数**不会报错**，但通常更糊也更慢 ——
// 蒸馏件的权重是照那条 4/8 步轨迹调的，步数一多反而容易过曝/细节退化。
// 「不加载 LoRA」时没有这层约束：走底模原生区间，20 步以上。
const LORA_STEPS = {
  'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors': 4,
  'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors': 8,
  'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors': 4,
}
const stepsHint = computed(() => {
  const want = LORA_STEPS[cfg.value.video_lora]
  if (!want) return ''
  const now = Number.parseInt(cfg.value.video_steps, 10)
  if (!now || now === want) return ''
  // 时间直接按步数比例估（采样时间基本与步数成正比）——把"更慢"说成"约 3 倍时间"才有体感
  const times = (now / want).toFixed(Number.isInteger(now / want) ? 0 : 1)
  return `⚠️ 这份 LoRA 是 ${want} 步蒸馏的，现在填的是 ${now} 步（约 ${times} 倍时间）—— `
    + '不会报错，但蒸馏件照短轨迹调的，步数给多了通常更糊。想要更多步，请把 LoRA 选成'
    + '「不加载 LoRA」再跑 20 步以上（那是底模的原生区间，不是靠加步数硬堆）。'
})

function onWorkflowChange() {
  const preset = WF_TO_LORA[cfg.value.video_workflow]
  if (!preset) return
  cfg.value = { ...cfg.value, video_lora: preset.lora, video_steps: preset.steps }
  toast(`已切到 ${wfLabel(cfg.value.video_workflow)}，配套 LoRA 与步数已一起换好（记得点「保存并测试」）`, 'ok', 5000)
}

// LoRA → 工作流（反方向也要绑：用户是从这一项表达"我要 i2v / ref2va"的）
function onLoraChange() {
  const preset = LORA_TO_WF[cfg.value.video_lora]
  if (!preset) return          // 「不加载 LoRA」两种工作流都能用，不强制切
  const before = cfg.value.video_workflow
  cfg.value = { ...cfg.value, video_workflow: preset.wf, video_steps: preset.steps }
  toast(
    `LoRA 换成了 ${preset.wf === 'ref2va' ? 'Ref2V' : 'fl2v'} 那份 → 视频工作流`
    + `${before === preset.wf ? '不变' : `已一起切到 ${wfLabel(preset.wf)}`}（步数 ${preset.steps}）`,
    'ok',
    5000,
  )
}

async function onModeChange() {
  try {
    await api.setConfig(cfg.value)
    toast(`输入模式已切到「${MODE_LABELS[cfg.value.video_mode] || cfg.value.video_mode}」`, 'ok')
  } catch (err) {
    toast(err.message, 'error')
  }
}

// 首页创作区选的模式。子组件只 emit、不改父状态，所以先把值写回 cfg，再复用上面那套存法
async function onVideoModePick(mode) {
  cfg.value = { ...cfg.value, video_mode: mode }
  await onModeChange()
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
  if (!window.confirm(`删除「${label}」？\n会移入回收站，之后还能恢复；想彻底删掉去回收站里清除。`)) return
  try {
    await api.deleteTask(id)
    if (taskId.value === id) createNew()
    await refreshTasks()
    toast('已移入回收站', 'ok', 2400)
  } catch (err) {
    toast(err.message, 'error')
  }
}

// ---------------------------------------------------------------- 回收站
// 删除一直是**软删除**（目录移到 outputs/_trash/{时间戳}_{id}），但移进去就再也看不见了 ——
// 没有列表、没法还原、也没法真删。2026-09-19 斌哥要求给它出口：恢复 / 清除。
// ⚠️ `listTrash` 会遍历每个条目的目录算占用空间，**只在真正打开回收站时拉一次** ——
//    别在进「我的作品」时顺手调。
const trashEntries = ref([])
const trashBusy = ref(false)
const trashError = ref('')

function openTrash() {
  activeView.value = 'trash'
  window.scrollTo({ top: 0, behavior: 'smooth' })
  loadTrash()
}

async function loadTrash() {
  trashBusy.value = true
  trashError.value = ''
  try {
    const res = await api.listTrash()
    trashEntries.value = res.entries || []
  } catch (err) {
    trashError.value = err.message
  } finally {
    trashBusy.value = false
  }
}

async function onRestoreTrash(entry) {
  const label = entry.title || entry.task_id
  try {
    await api.restoreTrash(entry.name)
    trashEntries.value = trashEntries.value.filter((e) => e.name !== entry.name)
    await refreshTasks()
    toast(`「${label}」已回到作品列表`, 'ok', 2600)
  } catch (err) {
    toast(err.message, 'error', 6000)
  }
}

async function onPurgeTrash(entry) {
  const label = entry.title || entry.task_id
  const size = formatBytes(entry.bytes)
  if (!window.confirm(`彻底删除「${label}」？\n占 ${size}，会从磁盘上消失，恢复不了。`)) return
  try {
    await api.purgeTrash(entry.name)
    trashEntries.value = trashEntries.value.filter((e) => e.name !== entry.name)
    toast('已彻底删除', 'ok', 2400)
  } catch (err) {
    toast(err.message, 'error', 6000)
  }
}

function formatBytes(n) {
  const v = Number(n) || 0
  if (v < 1024) return `${v} B`
  if (v < 1048576) return `${Math.round(v / 1024)} KB`
  if (v < 1073741824) return `${(v / 1048576).toFixed(1)} MB`
  return `${(v / 1073741824).toFixed(2)} GB`
}

// 副标题：件数 + 占用空间。空态与加载态各有各的说法，别让用户看到「共 0 件」
const trashSummary = computed(() => {
  if (!trashEntries.value.length) {
    return trashBusy.value ? '正在读取…' : '被移进来的作品会先留在这里，可以恢复，也可以彻底删掉。'
  }
  const total = trashEntries.value.reduce((sum, e) => sum + (Number(e.bytes) || 0), 0)
  return `共 ${trashEntries.value.length} 件，占用 ${formatBytes(total)}。恢复会回到「我的作品」，彻底删除就从磁盘上消失。`
})

function formatDateTime(iso) {
  if (!iso) return '时间未知'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '时间未知'
  return d.toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
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
// 分块流程没有「全量下一阶段」：块工作台自己驱动（AI 生成下一块），
// 所以这里只给旧分镜流程发 Continue 卡。
const isBlockFlow = computed(() => task.value?.flow === 'blocks')

const nextStage = computed(() => {
  const t = task.value
  if (!t || t.status === 'running') return null
  if (t.flow === 'blocks') return null
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

// ---------------------------------------------------------------- 生成记录
// 工作台第三个 tab（2026-09-18 斌哥定，原来挤在标题行里）：这个作品出过的所有片。
//
// 为什么要有它：出片结果只活在 CreateWorkbench 的 segJob 里，而那是**内存**——
// 刷新页面 / 后端重启之后就再也看不到刚生成的东西了（文件其实还在磁盘上）。
// 所以这里读后端 /api/tasks/{id}/segments，它扫的是 outputs/{id}/segments/ 里的 mp4。
//
// 演进：先是只在「分镜工作台」出现的按钮 → 改成常驻标题行 → 最后独立成一个 tab。
// 前两次都在解决"看不到"，这一次解决的是"跟谁挤在一起都不像"（见上面 studioMode 的注释）。
const segRecords = ref([])
const segRecordsBusy = ref(false)

async function loadSegRecords() {
  if (!taskId.value) { segRecords.value = []; return }
  segRecordsBusy.value = true
  try {
    const res = await api.listSegments(taskId.value)
    segRecords.value = res.items || []
  } catch {
    segRecords.value = []     // 列表读不出来不值得弹错，tab 上显示 0 就好
  } finally {
    segRecordsBusy.value = false
  }
}

// ---------------------------------------------------------------- 一段用了什么
// 点某一段的「用了什么」→ 拉它的生成记录（素材 / 提示词 / 参数）。
// 再点一次收起 —— 和上面那颗按钮一样是开关。
//
// ⚠️ 这份记录是**出片时写下的边车文件**（segments/seg_xxx.json），不是内存。
// 2026-09-18 之前生成的片子没有这个文件，后端会回 found=false，
// 此时要如实说"这条没留下记录"，**不能**渲染成一堆空字段 —— 那看起来像 bug。
const segDetail = ref(null)
const KIND_LABEL = { character: '角色', scene: '场景', prop: '道具', image: '图片', audio: '音频' }
// ⚠️ 这里写的是这张图的**作用**，不是"用户勾了什么"。斌哥 2026-09-19 看着
// 「首帧」问"我都没选首尾帧怎么冒出个首帧" —— 因为没显式挑时，后端会自动把
// 第一张能当帧的素材当首帧（I2V 工作流必须有一张），标签得说清楚它是"被当成了首帧"。
const ROLE_LABEL = { first_frame: '首帧图', last_frame: '尾帧图', selected: '参考图' }
// megapixels → 人话（与 02 里「清晰度」chip 同一套口径，H3 上限就是 768p）
const MP_NOTE = {
  0.4: '约 864×480', 0.5: '约 960×544', 0.7: '约 1152×640',
  0.9: '约 1280×736（720P）', 0.98: '1344×768（模型上限）',
}
const BACKEND_LABEL = { comfyui: 'ComfyUI', api: '第三方 API' }

async function openSegDetail(it) {
  if (segDetail.value?.name === it.name) { segDetail.value = null; return }
  segDetail.value = { name: it.name, loading: true, found: false, data: null, error: '' }
  try {
    const res = await api.segmentDetail(taskId.value, it.name)
    if (segDetail.value?.name !== it.name) return   // 期间点了别的段，别把结果盖上去
    segDetail.value = { name: it.name, loading: false, found: !!res.found, data: res, error: '' }
  } catch (err) {
    if (segDetail.value?.name !== it.name) return
    segDetail.value = { name: it.name, loading: false, found: false, data: null, error: err.message }
  }
}

async function copyPrompt(text) {
  try {
    await navigator.clipboard.writeText(text || '')
    toast('提示词已复制')
  } catch {
    toast('这个浏览器不让直接写剪贴板，手动选中复制一下吧', 'error')
  }
}

// 整张卡也可以点开详情，但**视频本身除外** —— 点视频是在播放（controls 挂在它上面），
// 把它也当成"看详情"会让人没法正常播放。链接和按钮同理（各有各的动作）。
function onSegCardClick(ev, it) {
  if (ev.target.closest('video, a, button')) return
  openSegDetail(it)
}

// 记录是作品的属性 → 进工作空间就拉一次（tab 上的计数要准），离开就清空。
// 切到「生成记录」这个 tab 时**再拉一次**：可能刚在 02 出完新片。
// 出片完成时 CreateWorkbench 会 emit segment-done，走同一个函数。
watch([taskId, activeView], () => {
  if (activeView.value === 'workspace' && taskId.value) {
    loadSegRecords()
  } else {
    segRecords.value = []
    segDetail.value = null
  }
})

// 离开记录 tab 就把那一段的详情带走 —— 否则下次切回来会先闪出上一次的详情，
// 看起来像加载错了。
watch(studioMode, (mode) => {
  if (mode === 'records') loadSegRecords()
  else segDetail.value = null
})

// 记录里的时间戳是后端 os.path.getmtime 给的**秒**，不是毫秒
function fmtTime(sec) {
  if (!sec) return ''
  const d = new Date(sec * 1000)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function fmtSize(n) {
  if (!n) return ''
  return n < 1024 * 1024
    ? `${Math.max(1, Math.round(n / 1024))} KB`
    : `${(n / 1024 / 1024).toFixed(1)} MB`
}

const charBusy = ref('')
const charSaving = ref(-1)
const assetBusy = ref('')

// 素材（道具/场景）：重生成走「删旧图 + 判存补缺」，上传后自动按参考图重生成
async function onRerollAsset({ index, name }) {
  if (assetBusy.value) return
  assetBusy.value = name
  try {
    const updated = await api.rerollAsset(taskId.value, index)
    const assets = task.value?.project?.assets || []
    if (assets[index]) assets[index] = { ...assets[index], ...updated }
    toast(`「${name}」的素材图已重新生成`, 'ok', 2600)
  } catch (err) {
    toast(`素材重生成失败：${err.message}`, 'error', 6000)
  } finally {
    assetBusy.value = ''
  }
}

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

// 角色音色样本：合成一段 3-7 秒人声，作为 Ref2VA 的 ref_audios 参考件。
// 下拉换音色和点按钮重合成走同一个出口 —— 都带 voiceId，选完即出样本，少一次点击。
async function onRerollVoice({ index, name, voiceId }) {
  if (charBusy.value) return
  charBusy.value = name
  try {
    const updated = await api.rerollCharacterVoice(taskId.value, index, voiceId || '')
    const chars = task.value?.project?.characters || []
    if (chars[index]) chars[index] = { ...chars[index], ...updated }
    toast(`「${name}」的音色样本已生成`, 'ok', 2600)
  } catch (err) {
    // edge-tts 走微软在线服务，断网/被墙会走到这里，所以提示给长一点
    toast(`音色合成失败：${err.message}`, 'error', 8000)
  } finally {
    charBusy.value = ''
  }
}

// 编辑角色的名字与锚点提示词。锚点是后续分镜提示词与定妆照的角色一致性来源，
// 改完服务端会标记 stale，前端提示重新生成定妆照。
async function onUpdateCharacter({ index, name, anchor, voice }) {
  const ownerId = taskId.value
  if (charSaving.value >= 0) return
  charSaving.value = index
  try {
    const updated = await api.patchCharacter(ownerId, index, { name, anchor, voice })
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

// ---------------------------------------------------------------- 角色 / 素材管理
// 手动添加角色：锚点留空由 AI 设计；删除连同定妆照；参考图上传后点重生成换装。
async function onAddCharacter({ name, anchor }) {
  const ownerId = taskId.value
  if (charBusy.value) return
  charBusy.value = name
  try {
    const created = await api.addCharacter(ownerId, { name, anchor })
    ;(task.value?.project?.characters || []).push(created)
    toast(`角色「${created.name}」已添加${created.anchor ? '' : '（形象由 AI 设计）'}`, 'ok', 3200)
  } catch (err) {
    toast(err.message, 'error', 8000)
  } finally {
    charBusy.value = ''
  }
}

async function onDeleteCharacter({ index, name }) {
  if (!window.confirm(`删除角色「${name}」？\n四个视角的定妆照会一起删除；已生成的块里还会显示这个名字。`)) return
  try {
    await api.deleteCharacter(taskId.value, index)
    const chars = task.value?.project?.characters || []
    if (index < chars.length) chars.splice(index, 1)
    toast(`角色「${name}」已删除`, 'ok', 2400)
  } catch (err) {
    toast(err.message, 'error', 6000)
  }
}

async function onAddAsset({ kind, name, anchor }) {
  if (assetBusy.value) return
  assetBusy.value = name
  try {
    const created = await api.addAsset(taskId.value, { kind, name, anchor })
    ;(task.value?.project?.assets || []).push(created)
    toast(`素材「${created.name}」已添加${created.anchor ? '' : '（描述由 AI 设计）'}`, 'ok', 3200)
  } catch (err) {
    toast(err.message, 'error', 8000)
  } finally {
    assetBusy.value = ''
  }
}

async function onPatchAsset({ index, name, anchor }) {
  const ownerId = taskId.value
  if (assetBusy.value) return
  try {
    const updated = await api.patchAsset(ownerId, index, { name, anchor })
    const assets = task.value?.project?.assets || []
    if (assets[index]) assets[index] = { ...assets[index], ...updated }
    toast('素材已保存', 'ok', 2000)
  } catch (err) {
    toast(err.message, 'error', 6000)
  }
}

async function onDeleteAsset({ index, name }) {
  if (!window.confirm(`删除素材「${name}」？\n已生成的素材图会一起删除。`)) return
  if (assetBusy.value) return
  assetBusy.value = name
  try {
    await api.deleteAsset(taskId.value, index)
    const assets = task.value?.project?.assets || []
    if (index < assets.length) assets.splice(index, 1)
    toast(`素材「${name}」已删除`, 'ok', 2400)
  } catch (err) {
    toast(err.message, 'error', 6000)
  } finally {
    assetBusy.value = ''
  }
}

// ---------------------------------------------------------------- 看图
// 灯箱内容由 kind 决定：分镜首帧图（旧流程）/ 块首帧图 / 块尾帧，各自成组翻页。
const lightboxItems = computed(() => {
  if (lightbox.value.kind === 'block-img') return blockLightboxItems('image_path')
  if (lightbox.value.kind === 'block-frame') return blockLightboxItems('last_frame')
  return (task.value?.shots || [])
    .filter((s) => s.image_path)
    .map((s) => ({
      shotId: s.shot_id,
      url: api.imageUrl(taskId.value, s.shot_id, s.version),
      camera: s.camera,
      motion: s.motion,
      duration: s.duration,
      dialogue: s.dialogue,
    }))
})

function openImage(shotId) {
  const i = lightboxItems.value.findIndex((it) => Number(it.shotId) === Number(shotId))
  if (i >= 0) lightbox.value = { open: true, index: i }
}

// ---------------------------------------------------------------- 导出
const canExport = computed(() => Boolean(task.value?.project))
const hasVideos = computed(() =>
  (task.value?.shots || []).some((s) => s.video_path)
  || (task.value?.blocks || []).some((b) => b.video_path),
)

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
  if (!t) return '未开始'
  // 「素材优先」建出来的空白作品：素材还没攒、故事还没写
  if (t.stage_state === 'draft') return '未开始'
  if (t.status === 'failed') return '失败'
  if (t.status === 'succeeded') {
    if (t.flow === 'blocks') {
      return { directed: '资产就绪', blocks: '分块编排中' }[t.stage_state] || '阶段完成'
    }
    return { directed: '待确认故事', storyboarded: '待确认分镜', imaged: '分镜已就绪' }[t.stage_state] || '阶段完成'
  }
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
const pageTitle = computed(() => activeView.value === 'library' ? '我的作品' : activeView.value === 'trash' ? '回收站' : activeView.value === 'workspace' ? (task.value?.project?.title || '制作工作区') : '新建作品')
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

// 顶栏那颗引擎状态胶囊（设计稿里的「ComfyUI：未配置」）
const engineState = computed(() => {
  if (cfg.value.video_backend === 'api') return '外接 API'
  if (!cfg.value.comfyui_url) return '未配置'
  if (cfgReachable.value === true) return '已连接'
  if (cfgReachable.value === false) return '未连接'
  return '未测试'
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
        <div class="breadcrumb"><span>{{ activeView === 'create' ? '工作台' : '工作空间' }}</span><span class="slash">/</span><strong>{{ pageTitle }}</strong></div>
        <!-- 新建作品页的顶栏：引擎状态 + 预览 + 导出视频（服务设置挪到侧栏，与设计稿一致） -->
        <div v-if="activeView === 'create'" class="actions create-actions">
          <button class="engine-pill" type="button" title="点这里打开服务设置" @click="toggleConfigPanel">
            <i class="dot" :class="comfyuiDot"></i>
            ComfyUI：{{ engineState }}
          </button>
          <button class="quiet" type="button" :disabled="!taskId" @click="openPreview">预览</button>
          <button class="quiet" type="button" :disabled="!canExport || exportBusy || busy" @click="onExport">
            <span v-if="exportBusy" class="spin" aria-hidden="true"></span>
            <svg v-else viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M10 3.6v9.2M6.4 9.4 10 13l3.6-3.6M4 16.4h12" stroke-linecap="round" stroke-linejoin="round" /></svg>
            {{ exportBusy ? '合并中…' : '导出视频' }}
          </button>
        </div>
        <div v-else class="actions">
          <span class="collab-label"><svg class="small-spark" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M10 2v16M2 10h16M4.4 4.4l11.2 11.2m0-11.2L4.4 15.6"/></svg> 多智能体协作创作</span>
          <button class="settings-button" aria-label="打开服务配置" @click="toggleConfigPanel">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M3 6h14M3 14h14"/><circle cx="7" cy="6" r="2" fill="currentColor"/><circle cx="13" cy="14" r="2" fill="currentColor"/></svg>
            服务设置
          </button>
        </div>
      </header>

      <div class="content" :class="{ 'workspace-content': activeView === 'workspace', 'create-content': activeView === 'create' }">
        <!-- 新建作品：只管素材（图片 / 音频这些）。
             2026-09-17 斌哥定：视频创作挪到「分镜工作台」那边，所以这里 show-video=false；
             右侧的 AI 助手也先撤掉（组件文件保留，随时能挂回来）。 -->
        <template v-if="activeView === 'create'">
          <div class="create-shell fade-in">
            <div class="create-main">
              <header class="create-head">
                <h1>新建作品</h1>
                <span class="status-pill" :class="statusKind"><i class="dot" :class="statusKind"></i>{{ statusLabel }}<span v-if="busy || draftBusy" class="spin"></span></span>
              </header>
              <CreateWorkbench
                :project="task?.project || null"
                :task-id="taskId"
                :cfg="cfg"
                :busy="busy || draftBusy"
                :ensure-task="ensureDraft"
                :refresh="onRefreshTask"
                :notify="toast"
                :show-video="false"
                @save-config="onPatchConfig"
              />
            </div>
          </div>
        </template>

        <template v-if="activeView === 'library'">
          <section class="library-header fade-in"><div><div class="eyebrow">YOUR CREATIVE COLLECTION</div><h1>每个故事，都在这里。</h1><p>回到你的创作现场，继续下一幕。</p></div><div class="lib-actions"><button type="button" title="看看移进回收站的作品，可以恢复或彻底删掉" @click="openTrash">回收站</button><button class="primary" @click="createNew">＋ 新建作品</button></div></section>
          <!-- 2026-09-19 斌哥定：进「我的作品」是来看作品的，不是来写故事的 —— 整块撤掉。
               原来是「另一种起手式：一句创意 → 自动拆镜 → 批量出图出片」，素材优先流程上线后
               这条故事驱动的老路摆在这里，占了一整屏。页面标题「每个故事，都在这里。」保留。
               三张灵感卡片一并撤（它们点了是往 PromptComposer 里填内容，表单没了就是三个死按钮）。
               按惯例**只摘渲染、留代码**：PromptComposer.vue 组件文件、import、composerRef、
               inspirations / useInspiration()、start() / onVideoModePick()、.brief-section 与
               .inspiration-grid 系列样式全部留着没删。下面是可复原片段：

          <section id="creative-brief" class="brief-section fade-in" aria-labelledby="creation-title">
            <div class="section-heading"><h2 id="creation-title"><span class="section-number">01</span> 写下你的故事</h2><span>一个念头，就能开场</span></div>
            <PromptComposer ref="composerRef" :busy="busy" :video-mode="cfg.video_backend === 'comfyui' ? cfg.video_mode : ''" @submit="start" @video-mode="onVideoModePick" />
            <p class="composer-footnote">从故事设定、角色定妆到分镜画面，每个阶段都由你确认后继续。</p>
            <div class="inspiration-grid">
              <button v-for="(item, i) in inspirations" :key="item.title" class="inspiration-card" @click="useInspiration(item)">
                <div class="inspiration-image" :class="item.className"><span class="concept-label mono">CONCEPT / 0{{ i + 1 }}</span></div>
                <div class="inspiration-info"><div><h3>{{ item.title }}</h3><p>{{ item.tag }}</p></div><span class="card-arrow" aria-hidden="true">↗</span></div>
              </button>
            </div>
          </section>
          -->
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

        <!-- 回收站（2026-09-19 斌哥要求）：删除一直是**软删除**（目录移到 outputs/_trash/{时间戳}_{id}），
             但移进去就再也看不见了。这里给它出口：恢复 → 回到「我的作品」；彻底删除 → 从磁盘消失。
             用列表而不是作品网格 —— 这里只做两件事，不需要封面、分镜数那些信息。 -->
        <template v-if="activeView === 'trash'">
          <section class="library-header fade-in">
            <div>
              <div class="eyebrow">RECENTLY DELETED</div>
              <h1>回收站</h1>
              <p>{{ trashSummary }}</p>
            </div>
            <div class="lib-actions">
              <button type="button" :disabled="trashBusy" @click="loadTrash">刷新</button>
              <button type="button" @click="openLibrary">返回作品</button>
            </div>
          </section>

          <div v-if="trashError" class="error" role="alert">{{ trashError }} <button @click="loadTrash">重试</button></div>
          <p v-else-if="trashBusy && !trashEntries.length" class="loading-state"><span class="spin"></span> 正在读取回收站…</p>
          <div v-else-if="trashEntries.length" class="trash-list">
            <article v-for="entry in trashEntries" :key="entry.name" class="trash-item">
              <div class="trash-info">
                <h2>{{ entry.title || '未命名作品' }}</h2>
                <div class="trash-meta">
                  <span class="mono">{{ entry.task_id }}</span>
                  <span>{{ formatDateTime(entry.deleted_at) }} 移入</span>
                  <span>{{ formatBytes(entry.bytes) }}</span>
                </div>
              </div>
              <div class="trash-ops">
                <button class="quiet" type="button" :aria-label="`恢复${entry.title || entry.task_id}`" @click="onRestoreTrash(entry)">恢复</button>
                <button class="quiet danger" type="button" :aria-label="`彻底删除${entry.title || entry.task_id}`" @click="onPurgeTrash(entry)">彻底删除</button>
              </div>
            </article>
          </div>
          <div v-else class="empty-library">
            <span class="empty-frame" aria-hidden="true">∅</span>
            <h2>回收站是空的</h2>
            <p>在「我的作品」里点「移入回收站」的作品，会先放到这里。</p>
            <button @click="openLibrary">回到我的作品</button>
          </div>
        </template>

        <!-- 回收站有自己的错误条（trashError），别让作品的 errorMsg 在这里插一脚 -->
        <div v-if="errorMsg && activeView !== 'library' && activeView !== 'trash'" class="error fade-in" role="alert">
          <div class="erow"><b>遇到一点问题</b><span class="emsg">{{ errorMsg }}</span><button v-if="task?.traceback" class="quiet tiny" @click="showTrace = !showTrace">{{ showTrace ? '收起详情' : '查看错误详情' }}</button></div>
          <pre v-if="showTrace && task?.traceback" class="trace">{{ task.traceback }}</pre>
        </div>

        <template v-if="activeView === 'workspace'">
          <div class="workspace-heading"><div><div class="eyebrow">DIRECTOR’S WORKSPACE</div><h1>{{ task?.project?.title || '正在准备你的故事' }}</h1></div><div class="mode-switch" role="group" aria-label="工作台视图"><button type="button" :class="{ on: studioMode === 'studio' }" :aria-pressed="studioMode === 'studio'" title="先备素材，手选素材并写一段描述，生成一段视频" @click="setStudioMode('studio')">素材工坊</button><button type="button" :class="{ on: studioMode === 'storyboard' }" :aria-pressed="studioMode === 'storyboard'" title="一句创意自动拆镜，批量出图与出片" @click="setStudioMode('storyboard')">分镜工作台</button><button type="button" :class="{ on: studioMode === 'records' }" :aria-pressed="studioMode === 'records'" title="这个作品出过的所有片，点开看每段用了哪些素材、提示词和参数" @click="setStudioMode('records')">生成记录<b v-if="segRecords.length" class="mono scount">{{ segRecords.length }}</b></button></div><span class="status-pill" :class="statusKind"><i class="dot" :class="statusKind"></i>{{ statusLabel }}<span v-if="busy" class="spin"></span></span></div>

          <!-- 生成记录的内容在下面第三个分支里（studioMode === 'records'），
               2026-09-18 从"标题行下面就地展开的一条"改成了独立 tab。
               理由见上面 studioMode 那段注释。 -->

          <template v-if="studioMode === 'studio'">
            <!-- 「素材工坊」与「新建作品」页共用同一个组件，两处都**只显示 01 准备素材**。
                 2026-09-17 斌哥定：视频创作统一收在「分镜工作台」那一侧，所以这里 show-video=false。
                 差异只剩两处：标题由外层提供；作品一定已存在，所以 ensureTask 直接回 id。 -->
            <CreateWorkbench
              v-if="task?.project"
              :project="task.project"
              :task-id="taskId"
              :cfg="cfg"
              :busy="task.status === 'running' || busy"
              :ensure-task="ensureWorkbenchTask"
              :refresh="onRefreshTask"
              :notify="toast"
              :show-video="false"
              @save-config="onPatchConfig"
            />
            <div v-else-if="!task && busy" class="loading-state"><span class="spin"></span> 正在连接创作现场…</div>
          </template>
          <template v-else-if="studioMode === 'storyboard'">
          <div v-if="!task && busy" class="loading-state"><span class="spin"></span> 正在连接创作现场…</div>
          <!-- 2026-09-17 斌哥定：阶段进度条（StageRail）在「分镜工作台」里**撤掉** ——
               它画的是「故事设定 → 资产准备 → 分块编排 → 合并成片」那条四段流水线，
               而这条流水线正是已经被撤掉的那套（分块工作台 / 分镜工作区）。
               现在分镜工作台就一件事：选素材 → 写描述 → 生成视频，没有"第几阶段"可言。
               ⚠️ StageRail.vue 组件文件与上面那行 import **留着没删**，
               要恢复就把下面这行加回来：
               <section v-if="task" class="statuscard fade-in"><StageRail :status="task.status"
                 :stage="task.stage" :stage-state="task.stage_state || ''" :flow="task.flow || 'shots'"
                 :done="task.done" :total="task.total || task.shots?.length || task.blocks?.length || 0" /></section> -->
          <section v-if="nextStage" class="statuscard fade-in nextstage"><div><span class="review-label">由你决定下一步</span><p class="nslabel">{{ nextStage.hint }}</p></div><button class="primary" :disabled="busy" @click="runNextStage">{{ nextStage.label }} <span aria-hidden="true">→</span></button></section>
          <!-- 2026-09-17 斌哥定：素材那一大块（ProjectBrief）在「分镜工作台」里**撤掉** ——
               它（作品标题/logline/画幅 + 视觉风格 + 角色卡 + 素材卡 + 添加角色 + 统计）
               属于「素材工坊」，两个 tab 各摆一份只会让人分不清哪份是真的。
               分镜工作台现在就只剩：02 生成视频（阶段条与素材块都已在同一天撤掉）。
               ⚠️ ProjectBrief.vue 组件文件、上面那行 import、以及
               @reroll-character / @update-character / @reroll-asset / @reroll-voice /
               @add-character / @delete-character / @add-asset / @patch-asset / @delete-asset
               这一串 handler 与 charBusy / charSaving / assetBusy / voiceEpoch 状态
               都**留着没删** —— 要恢复就把下面这行加回来：
               <ProjectBrief v-if="task?.project" :project="task.project" :task-id="taskId"
                 :busy="task.status === 'running'" :char-busy="charBusy" :char-saving="charSaving"
                 :asset-busy="assetBusy" :voice-epoch="voiceEpoch" :shots="task.shots || []"
                 :blocks="task.blocks || []" :stats="task.stats || {}"
                 @reroll-character="onRerollCharacter" @update-character="onUpdateCharacter"
                 @reroll-asset="onRerollAsset" @reroll-voice="onRerollVoice"
                 @add-character="onAddCharacter" @delete-character="onDeleteCharacter"
                 @add-asset="onAddAsset" @patch-asset="onPatchAsset" @delete-asset="onDeleteAsset" /> -->
          <!-- 视频创作：手选素材 + 写一段描述 → 生成一段视频。
               2026-09-17 斌哥定：这块从「新建作品」页挪到「分镜工作台」，
               所以 show-materials=false —— 01 的素材卡片不在这儿重复一遍，
               02 自带一条紧凑的素材勾选条。
               ⚠️ @save-config 必须挂上：02 里的「清晰度」是服务配置「画质」的镜像，
               它 emit save-config，没人接就会「点了不生效」（迁过来时漏过一次，e2e 抓到的）。 -->
          <CreateWorkbench
            v-if="task?.project"
            :project="task.project"
            :task-id="taskId"
            :cfg="cfg"
            :busy="task.status === 'running' || busy"
            :ensure-task="ensureWorkbenchTask"
            :refresh="onRefreshTask"
            :notify="toast"
            :show-materials="false"
            @save-config="onPatchConfig"
            @segment-done="loadSegRecords"
          />
          <!-- 2026-09-17 斌哥定：这个位置的「分块工作台 / 分镜工作区」**整块撤掉**。
               原来是 production-toolbar（标题 + 预览/导出数据/合并导出/输入模式/批量生成视频）
               加下面 BlockBoard（isBlockFlow）与 ShotBoard（另一种 flow）两个分支。
               分镜工作台这一侧现在就只剩：02 生成视频
               （阶段进度条 StageRail、素材大块 ProjectBrief、这套分块流程，都已在同一天撤掉）。
               ⚠️ 组件文件（BlockBoard.vue / ShotBoard.vue）、相关 handler、
               canExport / isBlockFlow / batchJob 等状态都**留着没删** ——
               要恢复就把这三行加回来。 -->
          </template>
          <template v-else>
            <!-- 生成记录：这个作品出过的所有片，新的排前面。
                 ⚠️ 读的是后端磁盘产物（outputs/{id}/segments/），不是前端 segJob ——
                 后者是内存里的，刷新页面就没了，而文件其实一直在。
                 每张卡上的「用了什么」就地展开这一段的素材/提示词/参数（见 .sd）。 -->
            <section class="records-view fade-in">
              <header class="sr-head">
                <div>
                  <b>生成记录</b>
                  <p>{{ task?.project?.title || '这个作品' }} · 共 {{ segRecords.length }} 段{{ segRecordsBusy ? '，正在读取…' : '' }}</p>
                </div>
                <button class="quiet tiny" :disabled="segRecordsBusy" title="重新扫一遍磁盘上的产物" @click="loadSegRecords">刷新</button>
              </header>

              <div v-if="segRecords.length" class="segrec-list">
                <article v-for="(it, i) in segRecords" :key="it.name" class="segrec-item"
                         :class="{ on: segDetail?.name === it.name }" @click="onSegCardClick($event, it)">
                  <video :src="it.url" controls preload="metadata"></video>
                  <div class="si-meta">
                    <b class="mono">#{{ segRecords.length - i }}</b>
                    <span v-if="it.mode" class="tag mono">{{ String(it.mode).toUpperCase() }}</span>
                    <span class="si-when">{{ fmtTime(it.created) }}</span>
                    <span class="si-size mono">{{ fmtSize(it.size) }}</span>
                    <!-- 这一段用了哪些素材、哪句提示词、什么参数。
                         记录是出片时写下的边车 json；老片子没有，点了会如实说明。 -->
                    <button class="quiet tiny si-what" :class="{ on: segDetail?.name === it.name }"
                            :aria-expanded="segDetail?.name === it.name"
                            :title="it.has_detail ? '看看这一段用了哪些素材、提示词和参数' : '这一段是在记录功能上线前生成的，没有留下参数'"
                            @click.stop="openSegDetail(it)">
                      {{ segDetail?.name === it.name ? '收起' : '用了什么' }}
                    </button>
                    <a class="quiet tiny si-dl" :href="it.url" download @click.stop>下载</a>
                  </div>
                </article>
              </div>
              <p v-else class="segrec-empty">
                {{ segRecordsBusy ? '正在读取…' : '还没有生成过视频。切到「分镜工作台」，选好素材、写好提示词，点「生成视频」。' }}
              </p>

              <!-- 「用了什么」展开的一块 —— 不套弹窗。
                   ⚠️ 只有 found 为真才渲染素材/参数/提示词；老片子（found=false）
                   必须走下面那条说明，别渲染一堆空字段。 -->
              <div v-if="segDetail" class="sd fade-in">
                <p v-if="segDetail.loading" class="sd-wait"><span class="spin"></span> 正在读取这一段的记录…</p>
                <p v-else-if="segDetail.error" class="sd-wait">读取失败：{{ segDetail.error }}</p>
                <template v-else-if="segDetail.found">
                  <header class="sd-head">
                    <b>这一段是怎么生成的</b>
                    <span class="mono sd-file">{{ segDetail.name }}</span>
                    <button class="quiet tiny" @click="segDetail = null">收起</button>
                  </header>

                  <div class="sd-cols">
                    <section class="sd-block">
                      <h4>素材<em>{{ (segDetail.data.materials || []).length }} 项</em></h4>
                      <ul v-if="(segDetail.data.materials || []).length" class="sd-mats">
                        <li v-for="m in segDetail.data.materials" :key="m.ref">
                          <span class="tag mono">{{ KIND_LABEL[m.kind] || m.kind }}</span>
                          <b>{{ m.name }}</b>
                          <!-- 用的**哪个文件**：一个角色有 `莫卡.png`（单张定妆照）和
                               `莫卡_sheet.png`（四视图设定图）两个文件，哪张进模型跟工作流有关。
                               写出来用户就不用去 ComfyUI 里对着图比了（2026-09-19 斌哥就这么比过）。 -->
                          <span v-if="m.file" class="sd-file mono" :title="'实际送进模型的文件：' + m.file">{{ m.file }}</span>
                          <span class="sd-role" :class="m.role">{{ ROLE_LABEL[m.role] || m.role }}</span>
                        </li>
                      </ul>
                      <p v-else class="sd-none">这一段没用图片素材（纯文生视频）。</p>
                      <!-- 标签含义就地解释：这三颗标签说的是"这张图在这段里的作用"，
                           不是"你在选择器里挑了什么"（2026-09-19 斌哥问过这个）。 -->
                      <p v-if="(segDetail.data.materials || []).some((m) => m.role === 'first_frame')" class="sd-note">
                        标签是这张图在这一段里的<b>作用</b>：「首帧图」= 这一段拿它当开头那一帧 ——
                        你在上面显式挑过首帧就用挑的那张，没挑就自动取第一张能当帧的素材
                        （I2V 工作流只吃这一张，其余只参与写提示词）。
                      </p>
                      <p v-else-if="segDetail.data.video_workflow === 'ref2va'" class="sd-note">
                        Ref2VA 工作流：没有首尾帧概念，选中的图都当<b>参考图</b>，全部会进模型。
                      </p>
                    </section>

                    <section class="sd-block">
                      <h4>参数</h4>
                      <dl class="sd-params">
                        <div><dt>模式</dt><dd>{{ segDetail.data.video_workflow === 'ref2va'
                          ? 'Ref2VA · 多图参考'
                          : (segDetail.data.mode === 'flf' ? 'FLF · 首尾帧' : 'I2V · 图生视频') }}</dd></div>
                        <div><dt>生成时长</dt><dd>{{ segDetail.data.duration }} 秒</dd></div>
                        <div><dt>清晰度</dt><dd>{{ segDetail.data.megapixels }} MP<template v-if="MP_NOTE[segDetail.data.megapixels]"> · {{ MP_NOTE[segDetail.data.megapixels] }}</template></dd></div>
                        <div><dt>视频后端</dt><dd>{{ BACKEND_LABEL[segDetail.data.video_backend] || segDetail.data.video_backend || '—' }}<template v-if="segDetail.data.video_mode"> · {{ segDetail.data.video_mode }}</template></dd></div>
                        <div><dt>生成时间</dt><dd>{{ fmtTime(segDetail.data.created) }}</dd></div>
                      </dl>
                    </section>
                  </div>

                  <section class="sd-block sd-promptblock">
                    <h4>提示词<button class="quiet tiny" @click="copyPrompt(segDetail.data.prompt)">复制</button></h4>
                    <p v-if="segDetail.data.note" class="sd-note">你当时写的是：{{ segDetail.data.note }}</p>
                    <pre class="sd-prompt">{{ segDetail.data.prompt }}</pre>
                  </section>
                </template>
                <p v-else class="sd-none sd-nofound">
                  这一段没有留下生成记录 —— 它是在记录功能上线之前生成的。<br />
                  从今往后的每一段，都会自动记下用到的素材、提示词和参数。
                </p>
              </div>
            </section>
          </template>
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

        <div class="mgroup">
          <div class="mgtitle">音频模型 · 角色音色样本</div>
          <label class="mfield">
            <span>用哪个</span>
            <select v-model="cfg.audio_provider" class="url-input">
              <option value="edge">edge-tts · 免费（8 个音色）</option>
              <option value="minimax">MiniMax · 付费（27 个音色 · 3.5 元/万字符）</option>
            </select>
          </label>
          <template v-if="cfg.audio_provider === 'minimax'">
            <label class="mfield">
              <span>API 地址</span>
              <input v-model="cfg.audio_base_url" class="url-input mono" placeholder="https://api.minimaxi.com/v1" />
            </label>
            <label class="mfield">
              <span>API Key</span>
              <input v-model="cfg.audio_api_key" class="url-input mono" type="password" autocomplete="off" placeholder="在 MiniMax 开放平台「账户管理 → API Keys」获取" />
            </label>
            <label class="mfield">
              <span>模型名称</span>
              <input v-model="cfg.audio_model" class="url-input mono" placeholder="speech-2.8-hd" />
            </label>
          </template>
          <p class="chint">
            音色样本给 MiniMax H3 的 Ref2VA 当参考音频，用来锁住角色的声音。
            单段 5 秒（H3 限制：每段 2-15 秒，3 段合计 ≤15 秒）。
            ⚠️ 当前出片链路（I2VA/FL2VA）没有音频输入口，生成后出片暂时不会有变化。
          </p>
        </div>

        <div class="mgroup" v-if="cfg.video_backend === 'comfyui'">
          <!-- 2026-09-19 傍晚：**工作流选择补回面板**（这本来就是 docs/REF2VA.md 里的计划：
               "服务配置面板增加「视频模式」选项"）。原来这里没有它，用户只能从 LoRA 下拉里猜
               —— 斌哥就是这么"选了 i2v"的（其实只选了 I2V 那条的 LoRA），工作流还是 Ref2VA，
               于是 02 里照样能多选、照样没有首尾帧，看起来就像 bug。
               现在切工作流会**把配套的 LoRA 与步数一起带出来**，不用记哪个配哪个。
               ⚠️ 措辞用 docs/REF2VA.md 自己那套：首帧/首尾帧 vs 全能参考，别再造"单图参考"这种词。 -->
          <div class="mgtitle">视频流程 · 工作流与画质</div>
          <label class="mfield">
            <span>视频工作流</span>
            <select v-model="cfg.video_workflow" class="url-input" @change="onWorkflowChange">
              <option value="i2v">I2V · 首帧 / 首尾帧（图就是视频第一帧，构图被钉住）</option>
              <option value="ref2va">Ref2VA · 全能参考（图只当参考，最多 9 张，画面放开）</option>
            </select>
          </label>
          <p class="chint">
            <b>I2V</b>：选的图 = 视频的<b>第一帧</b>（再显式挑一张就是尾帧），最多两张，构图由那张图定死。
            <b>Ref2VA</b>：选的图 = <b>参考</b>（把人物 / 场景钉住，画面放开），最多九张，
            没有首尾帧一说。两者是不同权重、不同输入口，切换时配套的 LoRA 与步数会一起换。
          </p>
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
          <p class="chint">
            这一项决定 H3 的像素预算（工作流里的 megapixels），<b>全站只有这一个真值</b>。
            新建作品页的「清晰度」是它的另一个入口 —— 720P = 0.9MP，1080P = 0.98MP，
            两边显示同一个值，改哪边都一样，不会出现「改了不生效」。
            H3 输出上限是 768p，所以 1080P 这一档实际出的是 1344×768。
          </p>
          <!-- ⚠️ 顺序是**先 LoRA、后步数**（2026-09-19 调整）：LoRA 是"选路线"，步数是"跑多少步"，
               而步数**应该由 LoRA 决定**（蒸馏件照着某个步数档调的）。反过来的话用户会先看到
               "采样步数 4 步"，再看到下面某份"4 步 LoRA"，以为这是两个独立旋钮
               —— 斌哥就是这么问的："这两个有什么区别？" -->
          <label class="mfield">
            <span>加速 LoRA（选它就等于选了对应工作流）</span>
            <select v-model="cfg.video_lora" class="url-input mono" @change="onLoraChange">
              <option value="">不加载 LoRA · 标准画质（配 20 步，无蒸馏伪影，较慢）</option>
              <option value="minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors">4 步 LoRA · 最快（I2V / 首尾帧 专用，配 4 步）</option>
              <option value="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors">8 步 LoRA · 均衡（I2V / 首尾帧 专用，配 8 步）</option>
              <option value="minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors">Ref2V 4 步 LoRA（Ref2VA 多图参考 专用，配 4 步）</option>
            </select>
          </label>
          <label class="mfield">
            <span>采样步数 — 要和上面那份 LoRA 的档位一致</span>
            <select v-model="cfg.video_steps" class="url-input">
              <option value="4">4 步 · 最快（配 4 步 LoRA）</option>
              <option value="6">6 步</option>
              <option value="8">8 步 · 标准（配 8 步 LoRA）</option>
              <option value="12">12 步 · 慢</option>
            </select>
          </label>
          <p class="chint">
            这两个是一对：<b>LoRA 决定步数</b>（它是照某个步数档蒸馏的），选 LoRA 会**自动**把步数带上；
            只有「不加载 LoRA」时才自己定（底模原生区间 20 步以上）。
          </p>
          <!-- 步数和 LoRA 的蒸散步数对不上时提醒（不拦，只说清代价） -->
          <p v-if="stepsHint" class="chint warn">{{ stepsHint }}</p>
          <!-- Ref2VA 工作流切过去之后，LoRA 必须跟着换成 ref2v 那份（模板里的权重和 LoRA
               是配套蒸馏的，混着用会出糊片）。这一项留个输入口，省得每次都去改 json。
               下面那句"配套"提示是 2026-09-19 斌哥问"这两个 LoRA 有什么区别"之后加的：
               两边名字里都有"4 步"，但它们是两条工作流的配套件，不是同一个东西的两个档位。 -->
          <p class="chint warn">
            ⚠️ LoRA 要和「视频工作流」配套：<b>i2v</b> 那份配 fl2v 的 4 步 / 8 步 LoRA，
            <b>ref2va</b> 那份配 Ref2V 4 步 LoRA。名字里的"4 步"只是蒸散步数，两边底模不同 ——
            混用不会报错，但出片会糊；保存 / 出片前后端都会拦一下。
          </p>
          <label class="mfield">
            <span>出片超时（秒）— 一段视频最多等多久</span>
            <input v-model="cfg.video_timeout_s" class="url-input mono" inputmode="numeric" placeholder="3600" />
          </label>
          <p class="chint">视频画幅自动跟随首帧图；H3 原生分辨率 1344×768，画质选 0.98MP 才能吃满。每镜 2-10 秒由分镜师按节奏决定——落在 H3 官方训练区间 4-15 秒内更稳，低于 4 秒画面容易飘（成片会就近吸附到 17 帧网格档位，如 3.75 / 4.46 / 5.17 / 7.29 / 10.12 秒）。超时默认 3600 秒：实测一条 10 秒的片跑过 1868 秒，给短了就白等。</p>
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

    <!-- 「生成记录」的两次搬迁（2026-09-18）：先是**弹窗**撤掉（盖住 01/02，没法一边翻记录
         一边接着出片）→ 改成标题行下面就地展开的一条 → 最后独立成工作台第三个 tab
         （模板里搜 records-view）。内容一字未改，只是换了三次容器。 -->

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
/* 回收站：软删除的出口。用列表而不是作品网格 —— 这里只有两件事可做：恢复 / 清除 */
.lib-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.lib-actions button { font-size: 12px; padding: 8px 14px; }
.trash-list { display: flex; flex-direction: column; gap: 10px; }
.trash-item { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 20px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); }
.trash-info { min-width: 0; }
.trash-info h2 { font-size: 15px; margin: 0 0 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trash-meta { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11px; color: var(--fg-3); }
.trash-meta .mono { font-size: 11px; letter-spacing: .5px; }
.trash-ops { display: flex; gap: 6px; flex-shrink: 0; }
.trash-ops button { font-size: 12px; padding: 6px 12px; }
/* Production workspace */
.workspace-content { gap: 22px; max-width: 1450px; }
.workspace-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.workspace-heading h1 { font-size: 30px; margin: 10px 0 0; letter-spacing: -.7px; }
/* 工作区形态切换：素材工坊 / 分镜工作台。放在标题右侧、状态胶囊左边 */
.mode-switch { display: flex; gap: 4px; margin-left: auto; padding: 3px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm); }
.mode-switch button { font-size: 12px; padding: 5px 12px; border-color: transparent; background: transparent; color: var(--fg-2); }
.mode-switch button.on { background: var(--surface); border-color: var(--line); color: var(--accent); font-weight: 600; }
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
.mode-pick { font-size: 12px; padding: 6px 8px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-xs); color: var(--fg); cursor: pointer; }
.mode-pick:hover:not(:disabled) { border-color: var(--fg-3); }
.mode-pick:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.mode-pick:disabled { opacity: 0.55; cursor: not-allowed; }
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

/* 「生成记录」现在是工作台第三个 tab（2026-09-18），计数徽标挂在 tab 按钮里。
   ⚠️ 它以前是一颗独立按钮（.segrec，挤在模式切换和状态胶囊中间），
   并进 .mode-switch 之后那套样式全删了 —— 别再按"独立按钮"去写。
   徽标在选中态（白底）和未选中态（透明底）上都读得清，所以不需要额外覆盖。 */
.mode-switch button .scount { margin-left: 5px; font-size: 10px; font-weight: 600; line-height: 16px; color: var(--accent); background: var(--accent-dim); border-radius: 20px; padding: 0 6px; }

/* 生成记录这一整屏（第三个 tab 的内容区）：一段一张卡，视频在上、元信息在下。
   外壳沿用 .statuscard 那套（surface 底 + 细线 + --r-md），读起来像页面的一部分。 */
.records-view { margin-top: 22px; padding: 22px 24px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); }
.sr-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 16px; }
.sr-head b { font-size: 15px; font-weight: 600; }
.sr-head p { margin: 3px 0 0; font-size: 12px; color: var(--fg-2); }
/* 卡片网格。⚠️ 这里用 auto-fit 而不是 auto-fill：记录独立成 tab 之后容器宽了，
   auto-fill 会按最小 300px 铺满一整行轨道、条目少时右边空出一大块；
   auto-fit 会把空轨道收掉让卡片撑开。上限 400px 是防止只有一段时铺成一条巨幅。 */
.segrec-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 400px)); justify-content: start; gap: 14px; }
.segrec-item { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--surface); cursor: pointer; }
/* 展开详情的那张卡给个边框色，跟下面那块对上号 */
.segrec-item.on { border-color: var(--accent); }
.segrec-item video { display: block; width: 100%; max-height: 340px; background: #0d120d; cursor: auto; }
.si-meta { display: flex; align-items: center; gap: 8px; padding: 9px 11px; font-size: 11px; color: var(--fg-2); }
.si-meta b { color: var(--fg); font-weight: 600; }
.si-when { margin-left: auto; }
.si-size { color: var(--fg-3); }
.si-dl { text-decoration: none; }
/* 展开态要盖住 button.quiet 的 hover（同 .segrec.on 那个坑） */
.si-what.on, .si-what.on:hover:not(:disabled) { background: var(--accent-dim); border-color: #efd4c6; color: var(--accent); }
.segrec-empty { margin: 6px 0 2px; padding: 28px; text-align: center; font-size: 12px; color: var(--fg-3); border: 1px dashed var(--line); border-radius: 12px; }

/* 「这一段是怎么生成的」：素材 / 参数两列，提示词整行在下面。
   底色用 --bg（比面板的白稍微灰一点），这样它是"面板里的一块"而不是另一张卡。 */
.sd { margin-top: 16px; padding: 18px 20px; background: var(--bg); border: 1px solid var(--line); border-radius: 12px; }
.sd-head { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.sd-head b { font-size: 14px; font-weight: 600; }
.sd-file { font-size: 11px; color: var(--fg-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sd-head button { margin-left: auto; flex: none; }
.sd-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px; }
.sd-block h4 { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; font-size: 12px; font-weight: 600; color: var(--fg-2); }
.sd-block h4 em { font-style: normal; font-weight: 400; font-size: 11px; color: var(--fg-3); }
.sd-block h4 button { margin-left: auto; }
.sd-promptblock { margin-top: 18px; }
.sd-mats { list-style: none; margin: 0; padding: 0; display: grid; gap: 7px; }
.sd-mats li { display: flex; align-items: center; gap: 8px; font-size: 12px; min-width: 0; }
.sd-mats b { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sd-file { flex: none; font-size: 10.5px; color: var(--fg-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 42%; }
.sd-role { margin-left: auto; flex: none; font-size: 10px; padding: 1px 7px; border-radius: 20px; background: var(--surface-2); color: var(--fg-2); }
.sd-role.first_frame { background: var(--accent-dim); color: var(--accent); }
.sd-role.last_frame { background: var(--mint-dim); color: var(--mint); }
.sd-params { margin: 0; display: grid; gap: 7px; }
.sd-params > div { display: flex; gap: 10px; font-size: 12px; }
.sd-params dt { flex: none; width: 62px; color: var(--fg-3); }
.sd-params dd { margin: 0; min-width: 0; }
.sd-note { margin: 0 0 10px; font-size: 12px; color: var(--fg-2); }
.sd-prompt { margin: 0; padding: 12px 14px; background: var(--surface); border: 1px solid var(--line-soft); border-radius: 10px; font: 12px/1.85 var(--font-mono); white-space: pre-wrap; word-break: break-word; max-height: 260px; overflow: auto; }
.sd-none { margin: 0; font-size: 12px; color: var(--fg-3); }
.sd-nofound { padding: 14px; border: 1px dashed var(--line); border-radius: 10px; text-align: center; line-height: 1.9; }
.sd-wait { margin: 0; font-size: 12px; color: var(--fg-2); }
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
/* 「配套」这类警告：灰底小字里要能一眼看到它跟前一句不是一个性质 */
.chint.warn { color: var(--accent); }
.mfoot { display: flex; align-items: center; justify-content: space-between; gap: 25px; margin-top: 23px; }
.mhint { font-size: 10px; color: var(--fg-3); }
.mfoot button { white-space: nowrap; }
/* ---------------------------------------------------------------- 新建作品页（素材优先）
   2026-09-17 起这一页只剩「01 准备素材」：02 生成视频挪去了「分镜工作台」，
   右侧的 AI 助手也撤了 —— 所以是单列铺满，不再有吸顶侧栏。 */
.create-content { max-width: 1560px; padding: 24px 22px 32px; gap: 0; }
/* 2026-09-17：右侧的 AI 助手撤了，从两列改成单列 —— 留着 250px 会让右边空一块 */
.create-shell { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; align-items: start; }
.create-main { display: flex; flex-direction: column; gap: 15px; min-width: 0; }
.create-head { display: flex; align-items: center; gap: 12px; }
.create-head h1 { margin: 0; font-size: 25px; line-height: 1.35; letter-spacing: -.4px; }
/* 全局 .actions 的 24px 间距会把「导出视频」挤到换行，这页收紧并禁止折行。
   svg 必须显式给尺寸 —— 全局没有 button svg 规则，不写就会按 300×150 撑成巨图标。 */
.create-actions { gap: 10px; }
.create-actions button { white-space: nowrap; flex: none; }
.create-actions button svg { width: 15px; height: 15px; flex: none; }
.engine-pill { display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px; border: 1px solid var(--line); border-radius: 30px; background: var(--surface-2); color: var(--fg-2); font-size: 11px; white-space: nowrap; }
.engine-pill:hover:not(:disabled) { background: var(--surface-2); border-color: #c6cbbf; color: var(--fg); }
.engine-pill svg { width: 15px; height: 15px; }
/* 「我的作品」页里的故事入口：一句创意自动拆镜那条老路 */
.brief-section { display: flex; flex-direction: column; padding: 18px 20px 20px; border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); }
.brief-section .inspiration-grid { margin-top: 18px; }
@media (min-width: 1600px) { .content { padding-top: 50px; } .welcome { gap: 80px; } .hero-art { aspect-ratio: 1.8; } }
@media (max-width: 1150px) { .content { padding: 28px; } .topbar { padding: 0 28px; } .welcome { gap: 24px; grid-template-columns: 1fr 1fr; } h1 { font-size: 40px; } .hero-art { aspect-ratio: 1.3; } .footer-end { display: none; } .project-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .production-toolbar { flex-wrap: wrap; } .collab-label { display: none; } }
@media (max-width: 900px) { .welcome { gap: 20px; } h1 { font-size: 34px; } .eyebrow { font-size: 8px; letter-spacing: 1.1px; } .art-caption { font-size: 13px; left: 20px; } .welcome-note { font-size: 9px; } .inspiration-grid { gap: 12px; } .inspiration-image { height: 114px; } .inspiration-info { padding: 12px; } .card-arrow { display: none; } .nextstage { flex-wrap: wrap; } .home-footer { flex-wrap: wrap; } .filter-tabs button { padding: 6px 8px; } .library-toolbar { flex-wrap: wrap; } }
@media (max-width: 760px) { .shell { flex-direction: column; } .main { width: 100%; } .topbar { height: 56px; padding: 0 20px; } .content { padding: 26px 20px; gap: 28px; } .actions { gap: 8px; } .settings-button { font-size: 11px; padding: 6px 8px; } .welcome { grid-template-columns: 1fr 1fr; padding-top: 0; } .hero-art { aspect-ratio: 1.15; } .welcome-copy > p { font-size: 11px; } .welcome-note { display: none; } .eyebrow { font-size: 7px; letter-spacing: .7px; } h1 { margin: 16px 0 12px; font-size: 34px; } .art-caption { left: 18px; bottom: 19px; font-size: 11px; letter-spacing: 1px; } .art-caption .mono { font-size: 6px; } .art-top { left: 19px; right: 19px; top: 19px; font-size: 7px; } .section-heading h2 { font-size: 14px; } .section-heading > span { font-size: 10px; } .production-actions { flex-wrap: wrap; } .production-actions button { font-size: 11px; } .statuscard { padding: 18px; } .workspace-heading h1 { font-size: 24px; } .modal-mask { padding: 16px 10px; } .modal { padding: 20px; } .mgroup { padding: 16px; } .mfoot { gap: 12px; } .library-header h1 { font-size: 26px; } }
@media (max-width: 480px) { .welcome { grid-template-columns: 1fr; gap: 20px; } .welcome-copy { position: relative; } h1 { font-size: 42px; } .welcome-copy > p { font-size: 12px; } .eyebrow { font-size: 9px; letter-spacing: 1.3px; } .hero-art { aspect-ratio: 1.85; margin: 0 0 16px; } .art-caption { font-size: 13px; } .inspiration-grid { grid-template-columns: 1fr; gap: 12px; } .inspiration-card { display: flex; } .inspiration-image { width: 42%; height: 108px; flex: none; } .inspiration-info { flex: 1; padding: 16px; } .card-arrow { display: grid; } .inspiration-info h3 { font-size: 15px; } .home-footer { font-size: 9px; gap: 12px; } .home-footer i { margin: 0 4px; } .project-grid { grid-template-columns: 1fr; } .library-header { flex-direction: column; align-items: flex-start; } .library-toolbar > input { width: 100%; } .backend-cards { grid-template-columns: 1fr; } .mfoot { flex-direction: column; align-items: stretch; } .breadcrumb { gap: 8px; font-size: 11px; } .breadcrumb > span:first-child, .breadcrumb .slash { display: none; } .composer-footnote { text-align: left; line-height: 1.8; } }
/* 新建作品页的窄屏：助手栏从右栏落到主区下方，不再吸顶 */
@media (max-width: 1180px) {
  .create-content { padding: 24px 24px 28px; }
  .create-shell { grid-template-columns: minmax(0, 1fr); }
  .create-assistant { position: static; height: auto; min-height: 460px; max-height: 70vh; }
}
@media (max-width: 760px) {
  .create-content { padding: 20px; }
  .create-head h1 { font-size: 21px; }
  .engine-pill { font-size: 10px; padding: 5px 9px; }
}
</style>

<script setup>
import {
  computed,
  defineAsyncComponent,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  watch,
  watchEffect,
} from 'vue';
import { api } from './api';
import AppSidebar from './components/AppSidebar.vue';
import WorkspaceTabs from './components/WorkspaceTabs.vue';
import WorkflowIcon from './components/WorkflowIcon.vue';
import CreateWorkbench from './components/CreateWorkbench.vue';
import ProjectLibrary from './components/ProjectLibrary.vue';
import ToastStack from './components/ToastStack.vue';
import { matchesProject } from './projectLibrary';
import { appendStoryboard } from './workflowStudio';
import { applyTheme, readTheme } from './theme.js';
const WorkflowCanvas = defineAsyncComponent(() => import('./components/WorkflowCanvas.vue'));
const ServiceSettings = defineAsyncComponent(() => import('./components/ServiceSettings.vue'));

// ---------------------------------------------------------------- 状态
const taskId = ref('');
const task = ref(null);
const busy = ref(false);
const errorMsg = ref('');
const theme = ref(readTheme());
function toggleTheme() {
  theme.value = applyTheme(theme.value === 'dark' ? 'light' : 'dark');
}
const showTrace = ref(false);

// 服务配置（ComfyUI / 文本模型 / 图片模型 / 音频模型），弹窗里填，后端持久化实时生效
const cfg = ref({
  video_backend: 'comfyui',
  video_mode: 'i2v',
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
});
const showConfigPanel = ref(false);

let settingsVersion = 0;

const history = ref([]);
const historyError = ref('');
const historyLoading = ref(false);

const activeView = ref('create');
const navigationExpanded = ref(false);
const workspaceMode = computed(() => activeView.value === 'workspace');
const editorMode = computed(() => ['workspace', 'create', 'library', 'trash'].includes(activeView.value));
const createHasMaterials = computed(
  () => !!(task.value?.project?.characters?.length || task.value?.project?.assets?.length)
);
const canvasMode = computed(() => activeView.value === 'workspace' && studioMode.value === 'storyboard');
const contentContainer = ref(null);
const workspaceScroll = new Map();

// 作品共用素材、画布和生成记录三个视图；默认打开画布。
const STUDIO_KEY = 'workspace.mode';
const STUDIO_MODES = ['studio', 'storyboard', 'records'];
const studioMode = ref(
  (() => {
    try {
      const v = localStorage.getItem(STUDIO_KEY);
      return STUDIO_MODES.includes(v) ? v : 'storyboard';
    } catch {
      return 'storyboard';
    }
  })()
);

function setStudioMode(mode) {
  studioMode.value = STUDIO_MODES.includes(mode) ? mode : 'studio';
  try {
    localStorage.setItem(STUDIO_KEY, studioMode.value);
  } catch {
    /* 隐私模式写不了，忽略即可 */
  }
}

// 素材工坊里做完批量补齐后，任务的图/音色路径都变了，重新拉一次任务
async function onRefreshTask() {
  if (!taskId.value) return;
  try {
    task.value = await api.getTask(taskId.value);
    await refreshTasks();
  } catch (err) {
    toast(`刷新素材状态失败：${err.message}`, 'error', 5000);
  }
}

// ---------------------------------------------------------------- 素材优先：空白作品
// 「新建作品」页是先攒素材、后写故事的流程，而素材（project.characters / assets）
// 必须有作品才挂得住。所以所有素材操作都先过这里：没有作品就建一个空白的。
//
// draftBusy 是防重入闸：连点两个按钮时不该建出两个作品（后端每次都会造一个新 task_id）。
const draftBusy = ref(false);
const openingCanvas = ref(false);
const createSession = ref(0);
async function ensureDraft(title = '') {
  if (taskId.value && task.value) return taskId.value;
  if (draftBusy.value) return '';
  draftBusy.value = true;
  try {
    const res = await api.createDraft(typeof title === 'string' ? title.trim() : '');
    taskId.value = res.task_id;
    task.value = await api.getTask(res.task_id);
    await refreshTasks();
    return res.task_id;
  } catch (err) {
    toast(`新建作品失败：${err.message}`, 'error', 6000);
    return '';
  } finally {
    draftBusy.value = false;
  }
}

// 工作台场景下作品一定已存在，「素材工坊」里每次落库操作只需拿当前 id。
// （「新建作品」页用的是 ensureDraft：还没有作品就现建一个空白草稿。）
async function ensureWorkbenchTask() {
  return taskId.value;
}

async function enterCanvas(title = '') {
  if (draftBusy.value || busy.value || openingCanvas.value) return;
  openingCanvas.value = true;
  try {
    const options = title && typeof title === 'object' ? title : { title };
    const id = await ensureDraft(options.title || '');
    if (!id || activeView.value !== 'create' || taskId.value !== id) return;
    if (options.descriptions?.length) {
      try {
        const document = await api.getWorkflow(id);
        appendStoryboard(document, options.descriptions, { duration: options.duration || 5 });
        await api.saveWorkflow(id, document);
      } catch (err) {
        toast(`创建分镜失败：${err.message}`, 'error');
        return;
      }
    }
    setStudioMode('storyboard');
    activeView.value = 'workspace';
    navigationExpanded.value = false;
  } finally {
    openingCanvas.value = false;
  }
}

const search = ref('');
const filter = ref('all');
const filteredHistory = computed(() =>
  history.value.filter((item) => matchesProject(item, search.value, filter.value))
);

async function openLibrary() {
  activeView.value = 'library';
  navigationExpanded.value = false;
  refreshTasks();
  await nextTick();
  if (contentContainer.value) contentContainer.value.scrollTop = 0;
}

const toasts = ref([]);

let timer = null;
let toastSeq = 0;

// ---------------------------------------------------------------- 提示条
function toast(text, type = 'info', ttl = 4200) {
  const id = ++toastSeq;
  toasts.value = [...toasts.value, { id, text, type }];
  window.setTimeout(() => dismissToast(id), ttl);
}
function dismissToast(id) {
  toasts.value = toasts.value.filter((t) => t.id !== id);
}

// ---------------------------------------------------------------- 轮询
// 用 setTimeout 递归而不是 setInterval：上一轮请求还没回来时不会叠新的请求。
// 1.2 秒一次，本地内存态查询，代价可以忽略。
function stopPolling() {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
}

function schedule(delay = 1200) {
  stopPolling();
  timer = window.setTimeout(poll, delay);
}

async function poll() {
  if (!taskId.value) return;
  const requestedId = taskId.value;
  try {
    const data = await api.getTask(requestedId);
    if (taskId.value !== requestedId) return;
    task.value = data;

    if (data.status === 'running') {
      busy.value = true;
      schedule();
      return;
    }

    busy.value = false;
    refreshTasks(); // 跑完了，同步左侧栏的状态点和镜数
    if (data.status === 'failed') {
      errorMsg.value = data.error || '生成失败';
      toast('生成失败，详情见页面提示', 'error', 6000);
    } else {
      let message;
      if (data.flow === 'blocks') {
        message =
          data.stage_note ||
          (data.stage_state === 'directed'
            ? '故事与资产已就绪，开始逐块编排（每块 10 秒）'
            : `块已就绪，共 ${data.blocks?.length || 0} 块`);
      } else {
        const rendered = (data.shots || []).filter((s) => s.image_path).length;
        message =
          data.stage_state === 'directed'
            ? '故事与角色已就绪，确认后可继续编排分镜'
            : data.stage_state === 'storyboarded'
              ? '分镜与提示词已就绪，确认后可继续生成画面'
              : `分镜就绪，共 ${data.shots?.length || 0} 镜，已出图 ${rendered} 张`;
      }
      toast(message, 'ok', 5000);
    }
  } catch (err) {
    if (taskId.value !== requestedId) return;
    busy.value = false;
    stopPolling();
    if (err.status === 404) {
      errorMsg.value = '找不到这个任务，它的输出目录可能已经被删掉了。';
      refreshTasks();
      toast('任务不存在', 'error', 5000);
    } else {
      errorMsg.value = err.message;
      toast(err.message, 'error');
    }
  }
}

// ---------------------------------------------------------------- 历史
// 任务列表以后端为准：后端启动时扫 outputs/ 重建，所以不依赖浏览器存储。
async function refreshTasks() {
  historyLoading.value = true;
  try {
    history.value = await api.listTasks();
    historyError.value = '';
  } catch {
    historyError.value = '暂时无法连接作品库，请检查后端服务后重试。';
  } finally {
    historyLoading.value = false;
  }
}

async function openTask(id) {
  activeView.value = 'workspace';
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (id === taskId.value && task.value) return;
  stopPolling();
  taskId.value = id;
  task.value = null;
  errorMsg.value = '';
  showTrace.value = false;
  busy.value = true;
  try {
    const data = await api.getTask(id);
    if (taskId.value !== id) return;
    task.value = data;
    if (data.status === 'running') schedule(400);
    else busy.value = false;
  } catch (err) {
    if (taskId.value !== id) return;
    busy.value = false;
    errorMsg.value =
      err.status === 404 ? '找不到这个作品，它的输出目录可能已经被删掉了。' : `作品加载失败：${err.message}`;
    refreshTasks();
    toast(errorMsg.value, 'error');
  }
}

async function createNew() {
  createSession.value++;
  navigationExpanded.value = false;
  stopPolling();
  taskId.value = '';
  task.value = null;
  errorMsg.value = '';
  showTrace.value = false;
  busy.value = false;
  activeView.value = 'create';
  await nextTick();
  if (contentContainer.value) contentContainer.value.scrollTop = 0;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ---------------------------------------------------------------- ComfyUI 地址
async function refreshConfig() {
  const version = settingsVersion;
  try {
    // 合并而不是整体替换：后端进程比前端代码旧时（比如刚改完还没重启），
    // 返回的对象里没有新增字段，整体替换会把它们冲成 undefined，
    // 界面上就表现为「刚加的配置项不见了」。合并能让默认值兜住。
    const config = await api.getConfig();
    if (version === settingsVersion) cfg.value = { ...cfg.value, ...config };
  } catch {
    /* 拉不到不影响其它功能 */
  }
}

function onSettingsSaved(config) {
  settingsVersion++;
  cfg.value = { ...cfg.value, ...config };
  toast('服务设置已保存', 'ok');
}

async function toggleConfigPanel() {
  if (showConfigPanel.value) {
    showConfigPanel.value = false;
    return;
  }
  await refreshConfig();
  showConfigPanel.value = true;
}

async function onRenameTask({ taskId: id, title }) {
  try {
    await api.renameTask(id, title);
    const it = history.value.find((t) => t.task_id === id);
    if (it) {
      it.title = title;
      it.idea = ''; // 侧栏显示 title || idea，重命名后让新名字顶上去
    }
    if (task.value?.task_id === id && task.value.project) {
      task.value.project.title = title;
    }
    toast('已重命名', 'ok', 2000);
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function onDeleteTask(id) {
  const item = history.value.find((t) => t.task_id === id);
  const label = item?.title || item?.idea || id;
  if (!window.confirm(`删除「${label}」？\n会移入回收站，之后还能恢复；想彻底删掉去回收站里清除。`)) return;
  try {
    await api.deleteTask(id);
    if (taskId.value === id) createNew();
    await refreshTasks();
    toast('已移入回收站', 'ok', 2400);
  } catch (err) {
    toast(err.message, 'error');
  }
}

// ---------------------------------------------------------------- 回收站
// 删除一直是**软删除**（目录移到 outputs/_trash/{时间戳}_{id}），但移进去就再也看不见了 ——
// 没有列表、没法还原、也没法真删。2026-09-19 斌哥要求给它出口：恢复 / 清除。
// ⚠️ `listTrash` 会遍历每个条目的目录算占用空间，**只在真正打开回收站时拉一次** ——
//    别在进「我的作品」时顺手调。
const trashEntries = ref([]);
const trashBusy = ref(false);
const trashError = ref('');

async function openTrash() {
  activeView.value = 'trash';
  navigationExpanded.value = false;
  loadTrash();
  await nextTick();
  if (contentContainer.value) contentContainer.value.scrollTop = 0;
}

async function loadTrash() {
  trashBusy.value = true;
  trashError.value = '';
  try {
    const res = await api.listTrash();
    trashEntries.value = res.entries || [];
  } catch (err) {
    trashError.value = err.message;
  } finally {
    trashBusy.value = false;
  }
}

async function onRestoreTrash(entry) {
  const label = entry.title || entry.task_id;
  try {
    await api.restoreTrash(entry.name);
    trashEntries.value = trashEntries.value.filter((e) => e.name !== entry.name);
    await refreshTasks();
    toast(`「${label}」已回到作品列表`, 'ok', 2600);
  } catch (err) {
    toast(err.message, 'error', 6000);
  }
}

async function onPurgeTrash(entry) {
  const label = entry.title || entry.task_id;
  const size = formatBytes(entry.bytes);
  if (!window.confirm(`彻底删除「${label}」？\n占 ${size}，会从磁盘上消失，恢复不了。`)) return;
  try {
    await api.purgeTrash(entry.name);
    trashEntries.value = trashEntries.value.filter((e) => e.name !== entry.name);
    toast('已彻底删除', 'ok', 2400);
  } catch (err) {
    toast(err.message, 'error', 6000);
  }
}

function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1048576) return `${Math.round(v / 1024)} KB`;
  if (v < 1073741824) return `${(v / 1048576).toFixed(1)} MB`;
  return `${(v / 1073741824).toFixed(2)} GB`;
}

// 副标题：件数 + 占用空间。空态与加载态各有各的说法，别让用户看到「共 0 件」
const trashSummary = computed(() => {
  if (!trashEntries.value.length) {
    return trashBusy.value ? '正在读取…' : '被移进来的作品会先留在这里，可以恢复，也可以彻底删掉。';
  }
  const total = trashEntries.value.reduce((sum, e) => sum + (Number(e.bytes) || 0), 0);
  return `共 ${trashEntries.value.length} 件，占用 ${formatBytes(total)}。恢复会回到「我的作品」，彻底删除就从磁盘上消失。`;
});

function formatDateTime(iso) {
  if (!iso) return '时间未知';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '时间未知';
  return d.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// 生成记录读取磁盘产物；进入作品和生成完成后刷新数量。
const segRecords = ref([]);
const segRecordsBusy = ref(false);

let recordsRequest = 0;
async function loadSegRecords() {
  const requestId = ++recordsRequest;
  const id = taskId.value;
  if (!taskId.value) {
    segRecords.value = [];
    return;
  }
  segRecordsBusy.value = true;
  try {
    const res = await api.listSegments(id);
    if (requestId !== recordsRequest || id !== taskId.value) return;
    segRecords.value = res.items || [];
  } catch {
    if (requestId === recordsRequest && id === taskId.value) segRecords.value = [];
  } finally {
    if (requestId === recordsRequest) segRecordsBusy.value = false;
  }
}

// ---------------------------------------------------------------- 一段用了什么
// 点某一段的「用了什么」→ 拉它的生成记录（素材 / 提示词 / 参数）。
// 再点一次收起 —— 和上面那颗按钮一样是开关。
//
// ⚠️ 这份记录是**出片时写下的边车文件**（segments/seg_xxx.json），不是内存。
// 2026-09-18 之前生成的片子没有这个文件，后端会回 found=false，
// 此时要如实说"这条没留下记录"，**不能**渲染成一堆空字段 —— 那看起来像 bug。
const segDetail = ref(null);
const segDetailPanel = ref(null);
const KIND_LABEL = { character: '角色', scene: '场景', prop: '道具', image: '图片', audio: '音频' };
// ⚠️ 这里写的是这张图的**作用**，不是"用户勾了什么"。斌哥 2026-09-19 看着
// 「首帧」问"我都没选首尾帧怎么冒出个首帧" —— 因为没显式挑时，后端会自动把
// 第一张能当帧的素材当首帧（I2V 工作流必须有一张），标签得说清楚它是"被当成了首帧"。
const ROLE_LABEL = { first_frame: '首帧图', last_frame: '尾帧图', selected: '参考图' };
const BACKEND_LABEL = { comfyui: 'ComfyUI', api: '第三方 API' };

async function openSegDetail(it) {
  if (segDetail.value?.name === it.name) {
    segDetail.value = null;
    return;
  }
  const id = taskId.value;
  segDetail.value = { name: it.name, loading: true, found: false, data: null, error: '' };
  try {
    const res = await api.segmentDetail(id, it.name);
    if (id !== taskId.value) return;
    if (segDetail.value?.name !== it.name) return; // 期间点了别的段，别把结果盖上去
    segDetail.value = { name: it.name, loading: false, found: !!res.found, data: res, error: '' };
  } catch (err) {
    if (id !== taskId.value) return;
    if (segDetail.value?.name !== it.name) return;
    segDetail.value = { name: it.name, loading: false, found: false, data: null, error: err.message };
  } finally {
    await nextTick();
    if (id === taskId.value && studioMode.value === 'records' && segDetail.value?.name === it.name) {
      segDetailPanel.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
}

async function copyPrompt(text) {
  try {
    await navigator.clipboard.writeText(text || '');
    toast('提示词已复制');
  } catch {
    toast('这个浏览器不让直接写剪贴板，手动选中复制一下吧', 'error');
  }
}

// 整张卡也可以点开详情，但**视频本身除外** —— 点视频是在播放（controls 挂在它上面），
// 把它也当成"看详情"会让人没法正常播放。链接和按钮同理（各有各的动作）。
function onSegCardClick(ev, it) {
  if (ev.target.closest('video, a, button')) return;
  openSegDetail(it);
}

// 切换作品时清空详情；画布生成完成和进入记录页时重新读取。
watch([taskId, activeView], () => {
  segDetail.value = null;
  segRecords.value = [];
  if (activeView.value === 'workspace' && taskId.value) {
    loadSegRecords();
  }
});

// 离开记录 tab 就把那一段的详情带走 —— 否则下次切回来会先闪出上一次的详情，
// 看起来像加载错了。
watch(studioMode, (mode) => {
  if (mode === 'records') loadSegRecords();
  else segDetail.value = null;
});

// Each project view keeps its own position; scrolling materials never moves records.
watch([taskId, studioMode, activeView], async ([id, mode, view], [oldId, oldMode, oldView]) => {
  if (oldView === 'workspace' && oldId && contentContainer.value) {
    workspaceScroll.set(`${oldId}:${oldMode}`, contentContainer.value.scrollTop);
  }
  await nextTick();
  if (view === 'workspace' && id === taskId.value && mode === studioMode.value) {
    contentContainer.value.scrollTop = workspaceScroll.get(`${id}:${mode}`) || 0;
  }
});

// 记录里的时间戳是后端 os.path.getmtime 给的**秒**，不是毫秒
function fmtTime(sec) {
  if (!sec) return '';
  const d = new Date(sec * 1000);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function fmtSize(n) {
  if (!n) return '';
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// ---------------------------------------------------------------- 顶栏状态
const statusLabel = computed(() => {
  const t = task.value;
  if (!t) return '未开始';
  // 「素材优先」建出来的空白作品：素材还没攒、故事还没写
  if (t.stage_state === 'draft') return '未开始';
  if (t.status === 'failed') return '失败';
  if (t.status === 'succeeded') {
    if (t.flow === 'blocks') {
      return { directed: '资产就绪', blocks: '分块编排中' }[t.stage_state] || '阶段完成';
    }
    return (
      { directed: '待确认故事', storyboarded: '待确认分镜', imaged: '分镜已就绪' }[t.stage_state] ||
      '阶段完成'
    );
  }
  return t.stage || '生成中';
});

const statusKind = computed(() => {
  const t = task.value;
  if (!t) return 'idle';
  return t.status === 'running' ? 'running' : t.status === 'failed' ? 'failed' : 'ok';
});

// ---------------------------------------------------------------- 生命周期
onMounted(async () => {
  const location = new URLSearchParams(window.location.hash.slice(1));
  const selectedProject = location.get('project') || '';
  await refreshTasks();
  refreshConfig();
  if (location.get('view') === 'library') {
    await openLibrary();
    return;
  }
  if (location.get('view') === 'trash') {
    await openTrash();
    return;
  }
  if (/^[0-9a-f]{12}$/.test(selectedProject)) {
    if (STUDIO_MODES.includes(location.get('view'))) setStudioMode(location.get('view'));
    await openTask(selectedProject);
    return;
  }
  // 刷新页面时如果还有任务在跑，自动接回轮询
  const running = history.value.find((t) => t.status === 'running');
  if (running) openTask(running.task_id);
});

onUnmounted(stopPolling);

// Preserve the selected page as well as the project tab after a refresh.
watch([activeView, taskId, studioMode], () => {
  const hash =
    activeView.value === 'workspace' && taskId.value
      ? `#project=${encodeURIComponent(taskId.value)}&view=${studioMode.value}`
      : ['library', 'trash'].includes(activeView.value)
        ? `#view=${activeView.value}`
        : '';
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${hash}`);
});

// 标签页标题跟随当前项目，多标签工作时能认出来
const pageTitle = computed(() =>
  activeView.value === 'library'
    ? '我的作品'
    : activeView.value === 'trash'
      ? '回收站'
      : activeView.value === 'workspace'
        ? task.value?.project?.title || '制作工作区'
        : '新建作品'
);
watchEffect(() => {
  document.title = `${pageTitle.value} · 镜序 FRAMEFLOW`;
});
</script>

<template>
  <div
    class="shell"
    :class="{
      'workspace-shell': editorMode,
      'workspace-theme': editorMode,
      'new-project-shell': activeView === 'create',
      'collection-shell': activeView === 'library' || activeView === 'trash',
    }"
  >
    <button
      v-if="editorMode && navigationExpanded"
      class="navigation-backdrop"
      aria-label="关闭导航侧栏"
      @click="navigationExpanded = false"
    ></button>
    <AppSidebar
      :compact="editorMode && !navigationExpanded"
      :dark="editorMode"
      :theme="theme"
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
      @theme="toggleTheme"
    />

    <main class="main">
      <header class="topbar">
        <button
          v-if="editorMode"
          class="navigation-toggle"
          :aria-label="navigationExpanded ? '收起侧栏' : '展开侧栏'"
          :title="navigationExpanded ? '收起侧栏' : '展开侧栏'"
          @click="navigationExpanded = !navigationExpanded"
        >
          <WorkflowIcon name="sidebar" />
        </button>
        <div class="breadcrumb">
          <span>{{ activeView === 'create' ? '工作台' : '工作空间' }}</span
          ><span class="slash">/</span><strong>{{ pageTitle }}</strong>
        </div>
        <WorkspaceTabs
          v-if="workspaceMode"
          :mode="studioMode"
          :count="segRecords.length"
          dark
          @select="setStudioMode"
        />
        <div class="actions">
          <span v-if="!editorMode" class="collab-label"
            ><svg
              class="small-spark"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              aria-hidden="true"
            >
              <path d="M10 2v16M2 10h16M4.4 4.4l11.2 11.2m0-11.2L4.4 15.6" />
            </svg>
            多智能体协作创作</span
          >
          <button class="settings-button" aria-label="打开服务配置" @click="toggleConfigPanel">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <path d="M3 6h14M3 14h14" />
              <circle cx="7" cy="6" r="2" fill="currentColor" />
              <circle cx="13" cy="14" r="2" fill="currentColor" />
            </svg>
            <span>{{
              workspaceMode ? (cfg.video_backend === 'api' ? '视频 API' : 'ComfyUI') : '服务设置'
            }}</span>
          </button>
        </div>
      </header>

      <div
        ref="contentContainer"
        class="content"
        :class="{
          'workspace-content': editorMode,
          'create-content': activeView === 'create',
          'canvas-content': canvasMode,
        }"
      >
        <template v-if="activeView === 'create'">
          <div class="create-shell fade-in">
            <div class="create-main">
              <header v-if="createHasMaterials" class="create-head">
                <h1>准备你的作品</h1>
                <span class="status-pill" :class="statusKind"
                  ><i class="dot" :class="statusKind"></i>{{ statusLabel
                  }}<span v-if="busy || draftBusy" class="spin"></span
                ></span>
                <button class="primary continue-canvas" :disabled="busy || draftBusy" @click="enterCanvas">
                  进入创作画布<WorkflowIcon name="arrow" />
                </button>
              </header>
              <CreateWorkbench
                :key="createSession"
                workspace
                start
                :project="task?.project || null"
                :task-id="taskId"
                :cfg="cfg"
                :busy="busy || draftBusy || openingCanvas"
                :ensure-task="ensureDraft"
                :refresh="onRefreshTask"
                :notify="toast"
                :recent="history"
                @canvas="enterCanvas"
                @open="openTask"
                @configure="toggleConfigPanel"
              />
            </div>
          </div>
        </template>

        <ProjectLibrary
          v-if="activeView === 'library'"
          v-model:search="search"
          v-model:filter="filter"
          :items="filteredHistory"
          :total="history.length"
          :loading="historyLoading"
          :error="historyError"
          @open="openTask"
          @create="createNew"
          @trash="openTrash"
          @delete="onDeleteTask"
          @retry="refreshTasks"
        />

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

          <div v-if="trashError" class="error" role="alert">
            {{ trashError }} <button @click="loadTrash">重试</button>
          </div>
          <p v-else-if="trashBusy && !trashEntries.length" class="loading-state">
            <span class="spin"></span> 正在读取回收站…
          </p>
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
                <button
                  class="quiet"
                  type="button"
                  :aria-label="`恢复${entry.title || entry.task_id}`"
                  @click="onRestoreTrash(entry)"
                >
                  恢复
                </button>
                <button
                  class="quiet danger"
                  type="button"
                  :aria-label="`彻底删除${entry.title || entry.task_id}`"
                  @click="onPurgeTrash(entry)"
                >
                  彻底删除
                </button>
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

        <div
          v-if="errorMsg && activeView !== 'library' && activeView !== 'trash'"
          class="error fade-in"
          role="alert"
        >
          <div class="erow">
            <b>遇到一点问题</b><span class="emsg">{{ errorMsg }}</span
            ><button v-if="task?.traceback" class="quiet tiny" @click="showTrace = !showTrace">
              {{ showTrace ? '收起详情' : '查看错误详情' }}
            </button>
          </div>
          <pre v-if="showTrace && task?.traceback" class="trace">{{ task.traceback }}</pre>
        </div>

        <template v-if="activeView === 'workspace'">
          <template v-if="studioMode === 'studio'">
            <CreateWorkbench
              v-if="task?.project"
              workspace
              :project="task.project"
              :task-id="taskId"
              :cfg="cfg"
              :busy="task.status === 'running' || busy"
              :ensure-task="ensureWorkbenchTask"
              :refresh="onRefreshTask"
              :notify="toast"
            />
            <div v-else-if="!task && busy" class="loading-state">
              <span class="spin"></span> 正在连接创作现场…
            </div>
          </template>
          <template v-else-if="studioMode === 'storyboard'">
            <WorkflowCanvas
              v-if="task?.project"
              :key="taskId"
              :task-id="taskId"
              :project="task.project"
              :shots="task.shots || []"
              :blocks="task.blocks || []"
              :cfg="cfg"
              :notify="toast"
              @configure="toggleConfigPanel"
              @materials="setStudioMode('studio')"
              @segment-done="loadSegRecords"
              @refresh="onRefreshTask"
            />
            <div v-else-if="busy" class="loading-state"><span class="spin"></span> 正在连接创作现场…</div>
          </template>
          <template v-else>
            <section class="records-view fade-in">
              <header class="sr-head">
                <div>
                  <b>生成记录</b>
                  <p>共 {{ segRecords.length }} 段视频{{ segRecordsBusy ? '，正在读取…' : '' }}</p>
                </div>
                <button
                  class="quiet tiny"
                  :disabled="segRecordsBusy"
                  title="重新扫一遍磁盘上的产物"
                  @click="loadSegRecords"
                >
                  刷新
                </button>
              </header>

              <div v-if="segRecords.length" class="segrec-list">
                <article
                  v-for="(it, i) in segRecords"
                  :key="it.name"
                  class="segrec-item"
                  :class="{ on: segDetail?.name === it.name }"
                  @click="onSegCardClick($event, it)"
                >
                  <video :src="it.url" controls preload="metadata"></video>
                  <div class="si-meta">
                    <b class="mono">#{{ segRecords.length - i }}</b>
                    <span v-if="it.mode" class="tag mono">{{ String(it.mode).toUpperCase() }}</span>
                    <span class="si-when">{{ fmtTime(it.created) }}</span>
                    <span class="si-size mono">{{ fmtSize(it.size) }}</span>

                    <button
                      class="quiet tiny si-what"
                      :class="{ on: segDetail?.name === it.name }"
                      :aria-expanded="segDetail?.name === it.name"
                      :title="
                        it.has_detail
                          ? '看看这一段用了哪些素材、提示词和参数'
                          : '这一段是在记录功能上线前生成的，没有留下参数'
                      "
                      @click.stop="openSegDetail(it)"
                    >
                      {{ segDetail?.name === it.name ? '收起' : '用了什么' }}
                    </button>
                    <a class="quiet tiny si-dl" :href="it.url" download @click.stop>下载</a>
                  </div>
                </article>
              </div>
              <p v-else class="segrec-empty">
                {{
                  segRecordsBusy
                    ? '正在读取…'
                    : '还没有生成过视频。切到「创作画布」，选好素材、写好提示词，点「生成视频」。'
                }}
              </p>

              <div v-if="segDetail" ref="segDetailPanel" class="sd fade-in">
                <p v-if="segDetail.loading" class="sd-wait">
                  <span class="spin"></span> 正在读取这一段的记录…
                </p>
                <p v-else-if="segDetail.error" class="sd-wait">读取失败：{{ segDetail.error }}</p>
                <template v-else-if="segDetail.found">
                  <header class="sd-head">
                    <b>这一段是怎么生成的</b>
                    <span class="mono sd-file">{{ segDetail.name }}</span>
                    <button class="quiet tiny" @click="segDetail = null">收起</button>
                  </header>

                  <div class="sd-cols">
                    <section class="sd-block">
                      <template v-if="segDetail.data.video_sources?.length">
                        <h4>
                          视频来源<em>{{ segDetail.data.video_sources.length }} 项</em>
                        </h4>
                        <ul class="sd-mats">
                          <li
                            v-for="source in segDetail.data.video_sources"
                            :key="`${source.source_node}:${source.version_id}`"
                          >
                            <b>{{ source.label || source.file }}</b
                            ><span
                              >{{
                                source.usage === 'continue' ? '结尾续拍 · 尾帧引导开头' : '视频画面参考'
                              }}
                              · {{ source.start.toFixed(2) }}–{{ source.end.toFixed(2) }}s</span
                            ><small>{{ source.file }} · 固定版本 {{ source.version_id }}</small>
                          </li>
                        </ul>
                        <p class="sd-note">来源音轨未复用；生成时已固定所选成片与参考范围。</p>
                      </template>
                      <h4>
                        素材<em>{{ (segDetail.data.materials || []).length }} 项</em>
                      </h4>
                      <ul v-if="(segDetail.data.materials || []).length" class="sd-mats">
                        <li v-for="m in segDetail.data.materials" :key="m.ref">
                          <span class="tag mono">{{ KIND_LABEL[m.kind] || m.kind }}</span>
                          <b>{{ m.name }}</b>

                          <span v-if="m.file" class="sd-file mono" :title="'实际送进模型的文件：' + m.file">{{
                            m.file
                          }}</span>
                          <span class="sd-role" :class="m.role">{{ ROLE_LABEL[m.role] || m.role }}</span>
                        </li>
                      </ul>
                      <p v-else class="sd-none">这段记录未列出图片素材。</p>

                      <p v-if="segDetail.data.video_backend === 'api'" class="sd-note">
                        当前 API
                        协议只提交一张首帧图。旧记录中的其他素材引用不代表它们已作为图片输入传入模型。
                      </p>
                      <p
                        v-else-if="(segDetail.data.materials || []).some((m) => m.role === 'first_frame')"
                        class="sd-note"
                      >
                        「首帧图」用于开头画面；首尾帧流程还可提供「尾帧图」。这些标签表示素材在本次生成中的作用。
                      </p>
                      <p v-else-if="segDetail.data.video_workflow === 'ref2va'" class="sd-note">
                        Ref2VA
                        工作流：选中的素材图作为<b>视觉参考</b>；选择结尾续拍时，另外使用来源尾帧引导新片开头。
                      </p>
                    </section>

                    <section class="sd-block">
                      <h4>参数</h4>
                      <dl class="sd-params">
                        <div>
                          <dt>模式</dt>
                          <dd>
                            {{
                              segDetail.data.video_backend === 'api'
                                ? '视频 API · 单张首帧'
                                : segDetail.data.video_workflow === 'ref2va'
                                  ? 'Ref2VA · 图片 / 视频参考'
                                  : segDetail.data.mode === 'flf'
                                    ? 'FLF · 首尾帧'
                                    : 'I2V · 图生视频'
                            }}
                          </dd>
                        </div>
                        <div>
                          <dt>请求时长</dt>
                          <dd>
                            {{
                              segDetail.data.video_backend === 'api'
                                ? 'API 模型决定（此协议不接收时长）'
                                : segDetail.data.duration != null
                                  ? segDetail.data.duration + ' 秒'
                                  : '未记录'
                            }}
                          </dd>
                        </div>
                        <div>
                          <dt>像素预算</dt>
                          <dd>
                            {{
                              segDetail.data.video_backend === 'api'
                                ? 'API 模型决定（此协议不接收像素预算）'
                                : segDetail.data.megapixels != null
                                  ? segDetail.data.megapixels + ' MP'
                                  : '未记录'
                            }}<template
                              v-if="
                                segDetail.data.video_backend !== 'api' && segDetail.data.resolution_source
                              "
                            >
                              ·
                              {{
                                segDetail.data.resolution_source === 'preset'
                                  ? '清晰度预设'
                                  : segDetail.data.resolution_source === 'override'
                                    ? '本镜覆盖'
                                    : '服务默认'
                              }}</template
                            >
                          </dd>
                        </div>
                        <div v-if="segDetail.data.ratio">
                          <dt>画幅 / 清晰度</dt>
                          <dd>
                            {{ segDetail.data.ratio === 'auto' ? '跟随参考图' : segDetail.data.ratio }} ·
                            {{
                              segDetail.data.resolution === 'custom'
                                ? '像素预算'
                                : (segDetail.data.resolution || '').toUpperCase()
                            }}
                          </dd>
                        </div>
                        <div v-if="segDetail.data.width && segDetail.data.height">
                          <dt>输出尺寸</dt>
                          <dd>{{ segDetail.data.width }} × {{ segDetail.data.height }}</dd>
                        </div>
                        <div v-if="segDetail.data.actual_duration != null">
                          <dt>成片时长</dt>
                          <dd>
                            {{ Number(segDetail.data.actual_duration).toFixed(2) }} 秒{{
                              segDetail.data.exact_duration ? ' · 精确时长' : ''
                            }}
                          </dd>
                        </div>
                        <div v-if="segDetail.data.generate_audio != null">
                          <dt>视频音频</dt>
                          <dd>
                            {{
                              segDetail.data.has_audio != null
                                ? segDetail.data.has_audio
                                  ? '成片含音轨'
                                  : '成片无音轨'
                                : segDetail.data.generate_audio
                                  ? '保留模型音轨'
                                  : '关闭'
                            }}
                          </dd>
                        </div>
                        <div v-if="segDetail.data.candidate_count">
                          <dt>生成候选</dt>
                          <dd>
                            第 {{ (segDetail.data.candidate_index || 0) + 1 }} 个 / 共
                            {{ segDetail.data.candidate_count }} 个
                          </dd>
                        </div>
                        <div v-if="segDetail.data.seed != null">
                          <dt>随机种子</dt>
                          <dd>{{ segDetail.data.seed }}</dd>
                        </div>
                        <div v-if="segDetail.data.planned_duration != null">
                          <dt>计划节奏</dt>
                          <dd>{{ segDetail.data.planned_duration }} 秒（用于提示词）</dd>
                        </div>
                        <div>
                          <dt>视频后端</dt>
                          <dd>
                            {{
                              BACKEND_LABEL[segDetail.data.video_backend] ||
                              segDetail.data.video_backend ||
                              '—'
                            }}
                          </dd>
                        </div>
                        <div>
                          <dt>生成时间</dt>
                          <dd>{{ fmtTime(segDetail.data.created) }}</dd>
                        </div>
                      </dl>
                      <p class="sd-note">有成片测量记录时显示实际结果；旧记录仅保留提交参数。</p>
                    </section>
                  </div>

                  <section class="sd-block sd-promptblock">
                    <h4>
                      提示词<button class="quiet tiny" @click="copyPrompt(segDetail.data.prompt)">
                        复制
                      </button>
                    </h4>
                    <p v-if="segDetail.data.note" class="sd-note">你当时写的是：{{ segDetail.data.note }}</p>
                    <p
                      v-if="
                        (segDetail.data.prompt || '').includes('<Audio') &&
                        !(segDetail.data.materials || []).some((m) => m.kind === 'audio')
                      "
                      class="sd-note"
                    >
                      旧提示词包含音频标记，但记录中没有音频输入；这些标记不表示音色样本已传入模型。
                    </p>
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

    <ServiceSettings
      v-if="showConfigPanel"
      :cfg="cfg"
      @close="showConfigPanel = false"
      @saved="onSettingsSaved"
    />

    <ToastStack :toasts="toasts" @dismiss="dismissToast" />
  </div>
</template>

<style scoped src="./app-shell.css"></style>

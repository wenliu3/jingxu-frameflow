<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { api } from '../api';
import { videoInputs, videoSourcesError, sourceSnapshot, sourceContext } from '../videoSources.js';
import { creationSettings, defaultResolution, submissionResolution } from '../videoSettings';
import { videoSelectionError } from '../videoSettings.js';
import { displayPrompt, mentionsError } from '../promptMentions.js';
import WorkflowIcon from './WorkflowIcon.vue';
import WorkflowNode from './WorkflowNode.vue';
import WorkflowInspector from './WorkflowInspector.vue';
import VideoComposer from './VideoComposer.vue';
import {
  applyVideoProgress,
  currentVideo,
  recordSettings,
  unifyVideoNodes,
  videoVersions,
} from '../workflowVideo.js';
import StoryboardSequence from './StoryboardSequence.vue';
import WorkflowDialogs from './WorkflowDialogs.vue';
import {
  ACTIVE_STATUSES,
  STATUS_LABELS,
  appendStoryboard,
  mergeWorkflow,
  orderedShots,
  shotReadiness,
} from '../workflowStudio';
import {
  NODE_WIDTH,
  NODE_HEIGHT,
  KIND_LABELS,
  clone,
  uid,
  emptyWorkflow,
  makeNode,
  makeShot,
  seedWorkflow,
  graphBounds,
  arrange,
  canConnect,
  edgePath,
} from '../workflowGraph';

const props = defineProps({
  taskId: { type: String, required: true },
  project: { type: Object, required: true },
  cfg: { type: Object, default: () => ({}) },
  shots: { type: Array, default: () => [] },
  blocks: { type: Array, default: () => [] },
  notify: Function,
});
const emit = defineEmits(['configure', 'materials', 'segment-done', 'refresh']);
const graph = ref(emptyWorkflow());
const board = ref(null);
const ready = ref(false);
const loading = ref(true);
const loadError = ref('');
const saveError = ref('');
const saving = ref(false);
const dirty = ref(false);
const conflicted = ref(false);
const selectedId = ref('');
const selectedEdge = ref('');
const drawerOpen = ref(false);
const search = ref('');
const kindFilter = ref('all');
const expanded = ref(false);
const importing = ref(false);
const menuOpen = ref(false);
const addMenuOpen = ref(false);
const helpOpen = ref(false);
const connecting = ref('');
const pointer = ref({ x: 0, y: 0 });
const dimensions = ref({ width: 1000, height: 650 });
const composerHeight = ref(300);
const COMPOSER_GAP = 14;
const past = ref([]);
const future = ref([]);
const jobTimers = new Map();
const dialogMode = ref('');
const reviews = ref([]);
const queueOpen = ref(false);
const queuePaused = ref(true);
const sequenceCollapsed = ref(true);
const importInput = ref(null);
const uploadInput = ref(null);
const uploading = ref(false);
const exportJob = ref(null);
let exportTimer;
const exportItems = computed(() =>
  sequence.value
    .map((shot, index) => ({
      id: shot.id,
      title: shot.data.title,
      order: index + 1,
      duration: shot.data.duration,
      currentFile: currentVideo(shot)?.file,
      versions: videoVersions(shot)
        .filter((v) => v.status === 'succeeded' && v.url && v.file)
        .map((v) => ({
          id: v.id,
          file: v.file,
          url: v.url,
          duration: v.duration,
          actual_duration: v.actual_duration,
          width: v.width,
          height: v.height,
        })),
    }))
    .filter((item) => item.versions.length)
);
let dispatching = false;
let gesture = null;
let saveTimer, backupTimer, observer;
let disposed = false;
let savedContent = '';
const cacheKey = `frameflow.workflow.${props.taskId}`;
// Moving the camera should not reserialize every prompt and reference node.
const nodeContent = computed(() => JSON.stringify({ nodes: graph.value.nodes, edges: graph.value.edges }));
const content = () => `${nodeContent.value.slice(0, -1)},"viewport":${JSON.stringify(graph.value.viewport)}}`;
const nodesById = computed(() => new Map(graph.value.nodes.map((n) => [n.id, n])));
const inputsById = computed(() => {
  const index = new Map();
  graph.value.edges.forEach((edge) => {
    if (!index.has(edge.target)) index.set(edge.target, []);
    index.get(edge.target).push(edge.source);
  });
  return index;
});
const say = (text, kind = 'info') => props.notify?.(text, kind);
const selected = computed(() => nodesById.value.get(selectedId.value));
const composerWidth = computed(() => Math.min(780, Math.max(0, dimensions.value.width - 24)));
const composerPosition = computed(() => {
  const node = selected.value,
    view = graph.value.viewport;
  if (node?.type !== 'shot') return {};
  return {
    left: `${view.x + (node.x + NODE_WIDTH / 2) * view.zoom}px`,
    top: `${view.y + (node.y + NODE_HEIGHT) * view.zoom + COMPOSER_GAP}px`,
    width: `${composerWidth.value}px`,
  };
});
const running = computed(() => graph.value.nodes.some((n) => ACTIVE_STATUSES.includes(n.data.status)));
const sequence = computed(() => orderedShots(graph.value));
const queuedShots = computed(() => sequence.value.filter((n) => n.data.status === 'queued'));
const activeShots = computed(() =>
  sequence.value.filter((n) => ['running', 'composing'].includes(n.data.status))
);
const completedShots = computed(() => sequence.value.filter((n) => currentVideo(n)));
const pendingShots = computed(() =>
  sequence.value.filter((n) => !ACTIVE_STATUSES.includes(n.data.status) && n.data.status !== 'succeeded')
);
const serviceConfigured = computed(() =>
  props.cfg.video_backend === 'api'
    ? !!(props.cfg.video_api_url && props.cfg.video_api_key && props.cfg.video_api_model)
    : !!props.cfg.comfyui_url
);
const selectedInputs = computed(() => inputNodes(selectedId.value));
const shotCount = computed(() => graph.value.nodes.filter((n) => n.type === 'shot').length);
const totalDuration = computed(() =>
  graph.value.nodes
    .filter((n) => n.type === 'shot')
    .reduce((sum, n) => sum + (Number(n.data.duration) || 10), 0)
);
const bounds = computed(() => graphBounds(graph.value.nodes));
const mapViewbox = computed(
  () => `${bounds.value.x} ${bounds.value.y} ${bounds.value.width} ${bounds.value.height}`
);
const worldStyle = computed(() => ({
  transform: `translate(${graph.value.viewport.x}px, ${graph.value.viewport.y}px) scale(${graph.value.viewport.zoom})`,
}));
const viewportRect = computed(() => ({
  x: -graph.value.viewport.x / graph.value.viewport.zoom,
  y: -graph.value.viewport.y / graph.value.viewport.zoom,
  width: dimensions.value.width / graph.value.viewport.zoom,
  height: dimensions.value.height / graph.value.viewport.zoom,
}));
const materials = computed(() => {
  const result = [];
  for (const [index, c] of (props.project.characters || []).entries()) {
    const path = props.cfg.video_workflow === 'ref2va' ? c.sheet || c.image_path : c.image_path;
    result.push({
      kind: 'character',
      name: c.name,
      index,
      file: path?.split(/[\\/]/).pop() || '',
      url: path ? api.characterImageUrl(props.taskId, path, c.version) : '',
      description: c.anchor || '',
      ready: !!path,
    });
  }
  for (const [index, a] of (props.project.assets || []).entries()) {
    if (a.kind === 'audio') continue; // Reference audio is not wired into the video provider yet.
    const path =
      props.cfg.video_workflow === 'ref2va' || a.kind === 'prop' ? a.sheet || a.images?.[0] : a.images?.[0];
    result.push({
      kind: a.kind,
      name: a.name,
      index,
      file: path?.split(/[\\/]/).pop() || '',
      url: path ? api.assetImageUrl(props.taskId, path, a.version) : '',
      description: a.anchor || '',
      ready: !!path,
    });
  }
  return result;
});
const filteredMaterials = computed(() =>
  materials.value.filter(
    (m) =>
      (kindFilter.value === 'all' || m.kind === kindFilter.value) &&
      m.name.toLowerCase().includes(search.value.trim().toLowerCase())
  )
);
const materialsByName = computed(() => new Map(materials.value.map((m) => [`${m.kind}:${m.name}`, m])));
const materialsByFile = computed(
  () => new Map(materials.value.filter((m) => m.file).map((m) => [`${m.kind}:${m.file}`, m]))
);
function resolveMaterial(node) {
  if (!node || node.type !== 'material') return null;
  return (
    materialsByName.value.get(`${node.data.materialKind}:${node.data.materialName}`) ||
    materialsByFile.value.get(`${node.data.materialKind}:${node.data.materialFile}`) ||
    null
  );
}
function inputNodes(id) {
  return (inputsById.value.get(id) || []).map((source) => nodesById.value.get(source)).filter(Boolean);
}
const drawnEdges = computed(() =>
  graph.value.edges
    .map((edge) => {
      const source = nodesById.value.get(edge.source);
      const target = nodesById.value.get(edge.target);
      return source && target ? { ...edge, path: edgePath(source, target) } : null;
    })
    .filter(Boolean)
);
const loosePath = computed(() => {
  const source = nodesById.value.get(connecting.value);
  return source ? edgePath(source, { x: pointer.value.x, y: pointer.value.y - 46 }) : '';
});

function checkpoint() {
  const snapshot = JSON.stringify({ nodes: graph.value.nodes, edges: graph.value.edges });
  if (past.value.at(-1) !== snapshot) past.value = [...past.value.slice(-39), snapshot];
  future.value = [];
}
function restore(snapshot) {
  const parsed = JSON.parse(snapshot);
  graph.value.nodes = parsed.nodes;
  graph.value.edges = parsed.edges;
  selectedId.value = '';
  selectedEdge.value = '';
  connecting.value = '';
  addMenuOpen.value = false;
}
function undo() {
  if (!past.value.length || running.value) return;
  future.value.push(JSON.stringify({ nodes: graph.value.nodes, edges: graph.value.edges }));
  restore(past.value.pop());
}
function redo() {
  if (!future.value.length || running.value) return;
  past.value.push(JSON.stringify({ nodes: graph.value.nodes, edges: graph.value.edges }));
  restore(future.value.pop());
}
function backup() {
  if (!ready.value) return;
  try {
    localStorage.setItem(cacheKey, JSON.stringify({ document: graph.value, unsaved: dirty.value }));
  } catch {
    /* Server save still works if browser storage is full. */
  }
}
async function save() {
  clearTimeout(saveTimer);
  if (!ready.value || saving.value || conflicted.value || !dirty.value) return;
  saving.value = true;
  const payload = clone(graph.value);
  const sentContent = content();
  try {
    const response = await api.saveWorkflow(props.taskId, payload);
    graph.value.revision = response.revision;
    savedContent = sentContent;
    dirty.value = content() !== savedContent;
    saveError.value = '';
  } catch (err) {
    conflicted.value = err.status === 409;
    saveError.value =
      err.status === 404 ? '后端需要重启以启用画布保存。当前修改已暂存此浏览器。' : err.message;
  } finally {
    saving.value = false;
    backup();
    if (dirty.value && !saveError.value) {
      if (disposed) save();
      else saveTimer = setTimeout(save, 700);
    }
  }
}
watch(
  () => content(),
  (value) => {
    if (!ready.value) return;
    dirty.value = value !== savedContent;
    clearTimeout(saveTimer);
    clearTimeout(backupTimer);
    backupTimer = setTimeout(backup, 200);
    if (dirty.value && !conflicted.value) saveTimer = setTimeout(save, 700);
  }
);

async function load() {
  loading.value = true;
  loadError.value = '';
  ready.value = false;
  clearTimeout(saveTimer);
  try {
    const remote = await api.getWorkflow(props.taskId);
    let document = clone(remote);
    try {
      const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null');
      if (cached?.unsaved && cached.document?.revision === remote.revision) document = cached.document;
    } catch {
      /* Invalid browser cache does not replace the project file. */
    }
    if (!document.nodes.length) {
      let records = [];
      try {
        records = (await api.listSegments(props.taskId)).items || [];
      } catch {
        /* A new project can have no records. */
      }
      document = seedWorkflow(
        materials.value,
        [],
        props.cfg.video_backend !== 'api' && props.cfg.video_workflow === 'ref2va'
      );
      document.revision = remote.revision;
      await bringRecordsInto(document, records);
      const oldShots = props.blocks.length ? props.blocks : props.shots;
      if (oldShots.length) {
        const placeholders = new Set(
          document.nodes
            .filter((n) => n.type === 'shot' && n.data.status === 'draft' && !n.data.description)
            .map((n) => n.id)
        );
        document.nodes = document.nodes.filter((n) => !placeholders.has(n.id));
        document.edges = document.edges.filter(
          (e) => !placeholders.has(e.source) && !placeholders.has(e.target)
        );
        const startY = document.nodes.length
          ? graphBounds(document.nodes).y + graphBounds(document.nodes).height + 60
          : 60;
        oldShots.forEach((s, i) => {
          const node = makeShot(
            440,
            startY + i * 310,
            document.nodes.filter((n) => n.type === 'shot').length + 1
          );
          node.data.description = s.summary || s.scene_desc || '';
          node.data.duration = [5, 10, 15].includes(s.duration) ? s.duration : 10;
          document.nodes.push(node);
        });
      }
    }
    unifyVideoNodes(document);
    // Only project-local media URLs are displayed from saved node data.
    for (const node of document.nodes) {
      if (node.data.url && !node.data.url.startsWith(`/files/${props.taskId}/`)) node.data.url = '';
      for (const version of videoVersions(node)) {
        if (typeof version.url !== 'string' || !version.url.startsWith(`/files/${props.taskId}/`))
          version.url = '';
        if (version.status === 'composing' || (version.status === 'running' && !version.jobId))
          version.status = 'interrupted';
      }
      if (node.data.status === 'composing' || (node.data.status === 'running' && !node.data.jobId)) {
        node.data.status = 'interrupted';
        node.data.error = '上次编排已中断，确认描述后可以重新生成。';
      }
    }
    graph.value = document;
    savedContent = JSON.stringify({ nodes: remote.nodes, edges: remote.edges, viewport: remote.viewport });
    past.value = [];
    future.value = [];
    conflicted.value = false;
    saveError.value = '';
    ready.value = true;
    dirty.value = content() !== savedContent;
    selectedId.value =
      document.nodes.find((n) => n.type === 'shot' && !currentVideo(n))?.id ||
      document.nodes.find((n) => n.type === 'shot')?.id ||
      '';
    queueOpen.value = document.nodes.some((n) => n.data.status === 'queued');
    for (const node of document.nodes.filter(
      (n) => n.type === 'shot' && n.data.status === 'running' && n.data.jobId
    ))
      pollJob(node.id, node.data.jobId);
    if (dirty.value) saveTimer = setTimeout(save, 700);
  } catch (err) {
    loadError.value = err.status === 404 ? '当前后端还没有载入画布功能，请重启后端服务。' : err.message;
  } finally {
    loading.value = false;
  }
}
function reload() {
  if (dirty.value && !window.confirm('重新载入会放弃当前未保存的画布修改。可以先导出画布备份。继续吗？'))
    return;
  load();
}

async function recordDetails(records) {
  return Promise.all(
    records.map(async (r) => {
      try {
        return await api.segmentDetail(props.taskId, r.name);
      } catch {
        return null;
      }
    })
  );
}
async function bringRecordsInto(document, records, knownDetails = null) {
  const details = knownDetails || (await recordDetails(records));
  unifyVideoNodes(document);
  let added = 0;
  for (const [index, record] of records.entries()) {
    if (document.nodes.some((n) => videoVersions(n).some((v) => v.file === record.name))) continue;
    const detail = details[index];
    let shot =
      detail?.candidate_count > 1 && detail.job_id
        ? document.nodes.find(
            (n) => n.type === 'shot' && videoVersions(n).some((v) => v.jobId === detail.job_id)
          )
        : null;
    const settings = recordSettings(detail?.found ? detail : null);
    if (!shot) {
      if (document.nodes.length >= 300) break;
      shot = makeShot(460, 600 + added * 310, document.nodes.filter((n) => n.type === 'shot').length + 1);
      shot.data.title = `历史视频 ${String(records.length - index).padStart(2, '0')}`;
      Object.assign(shot.data, settings, { status: 'succeeded', versions: [] });
      document.nodes.push(shot);
      added += 1;
    }
    const version = {
      ...settings,
      id: uid(),
      file: record.name,
      url: record.url,
      status: 'succeeded',
      jobId: detail?.job_id,
      candidateIndex: detail?.candidate_index ?? 0,
      backend: detail?.video_backend,
    };
    for (const key of ['width', 'height', 'actual_duration', 'has_audio'])
      if (detail?.[key] != null) version[key] = detail[key];
    shot.data.versions.push(version);
    for (const used of detail?.materials || []) {
      if (used.role !== 'selected' && used.role !== 'first_frame') continue;
      const m = materials.value.find((m) => m.kind === used.kind && m.name === used.name);
      if (!m) continue;
      let source = document.nodes.find(
        (n) => n.type === 'material' && n.data.materialKind === m.kind && n.data.materialName === m.name
      );
      if (!source) {
        if (document.nodes.length >= 300) continue;
        source = makeNode(
          'material',
          70,
          60 + document.nodes.filter((n) => n.type === 'material').length * 290,
          { title: m.name, materialKind: m.kind, materialName: m.name, materialFile: m.file }
        );
        document.nodes.push(source);
      }
      if (!document.edges.some((e) => e.source === source.id && e.target === shot.id))
        document.edges.push({ id: uid(), source: source.id, target: shot.id });
    }
  }
  unifyVideoNodes(document);
  return added;
}
async function importRecords() {
  if (importing.value || !ready.value || running.value) return;
  importing.value = true;
  try {
    const records = (await api.listSegments(props.taskId)).items || [];
    const details = await recordDetails(records);
    if (disposed || running.value) return;
    const document = clone(graph.value);
    checkpoint();
    const count = await bringRecordsInto(document, records, details);
    graph.value.nodes = document.nodes;
    graph.value.edges = document.edges;
    fit();
    say(count ? `已还原 ${count} 张视频创作卡，同次候选合并为版本` : '已同步生成记录与候选版本', 'ok');
  } catch (err) {
    say(err.message, 'error');
  } finally {
    importing.value = false;
  }
}

function screenToWorld(event) {
  const rect = board.value.getBoundingClientRect();
  const v = graph.value.viewport;
  return { x: (event.clientX - rect.left - v.x) / v.zoom, y: (event.clientY - rect.top - v.y) / v.zoom };
}
function positionForNew() {
  const v = graph.value.viewport;
  return {
    x: Math.round((dimensions.value.width * 0.4 - v.x) / v.zoom),
    y: Math.round((dimensions.value.height * 0.3 - v.y) / v.zoom),
  };
}
function chooseAdd() {
  if (selected.value?.type === 'shot' && currentVideo(selected.value)) addMenuOpen.value = !addMenuOpen.value;
  else addShot();
}
function addShot(continuation = false) {
  addMenuOpen.value = false;
  if (!ready.value || graph.value.nodes.length >= 300) return;
  const inherited = continuation
    ? graph.value.edges.filter(
        (e) => e.target === selected.value?.id && nodesById.value.get(e.source)?.type === 'material'
      )
    : [];
  if (continuation && !currentVideo(selected.value)) return say('请先选中成功视频再续拍', 'error');
  if (graph.value.edges.length + inherited.length + (selected.value?.type === 'shot' ? 1 : 0) > 1200)
    return say('画布连线已达上限，请先整理', 'error');
  checkpoint();
  const p = positionForNew();
  const n = makeShot(p.x, p.y, shotCount.value + 1);
  if (selected.value?.type === 'shot') {
    n.x = selected.value.x + 365;
    n.y = selected.value.y;
    graph.value.edges.push({
      id: uid(),
      source: selected.value.id,
      target: n.id,
      usage: continuation ? 'continue' : 'text',
      sourceVersionId: continuation ? currentVideo(selected.value)?.id || '' : '',
    });
    if (continuation) {
      const version = currentVideo(selected.value);
      n.data.ratio = version?.ratio || 'auto';
      n.data.resolution = version?.resolution || '720p';
      inherited.forEach((e) => graph.value.edges.push({ ...clone(e), id: uid(), target: n.id }));
    }
  }
  graph.value.nodes.push(n);
  focusNode(n.id);
}
function buildStoryboard({ descriptions, duration }) {
  if (!ready.value || running.value) return;
  try {
    const next = clone(graph.value);
    const sources =
      selected.value?.type === 'shot'
        ? inputNodes(selectedId.value)
            .filter((n) => n.type === 'material')
            .map((n) => n.id)
        : [];
    const added = appendStoryboard(next, descriptions, { duration, sources });
    checkpoint();
    graph.value.nodes = next.nodes;
    graph.value.edges = next.edges;
    dialogMode.value = '';
    fit();
    selectedId.value = added[0]?.id || '';
    focusNode(added[0]?.id);
    say(`已创建 ${added.length} 张视频创作卡，可直接描述画面并生成`, 'ok');
  } catch (err) {
    say(err.message, 'error');
  }
}
async function importBackup(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file || !ready.value || running.value) return;
  try {
    if (file.size > 2_000_000) throw new Error('画布备份不能超过 2MB');
    const next = clone(graph.value);
    const count = mergeWorkflow(next, JSON.parse(await file.text()));
    if (disposed || running.value) return;
    checkpoint();
    graph.value.nodes = next.nodes;
    graph.value.edges = next.edges;
    fit();
    say(`已导入 ${count} 个节点。请核对当前作品的素材引用后再生成。`, 'ok');
  } catch (err) {
    say(`导入失败：${err.message}`, 'error');
  }
}
function addNote() {
  if (!ready.value || graph.value.nodes.length >= 300) return;
  checkpoint();
  const p = positionForNew(),
    n = makeNode('note', p.x, p.y, { title: '创作便签', text: '' });
  graph.value.nodes.push(n);
  selectedId.value = n.id;
}
function addMaterial(material) {
  if (!ready.value) return false;
  const target =
    selected.value?.type === 'shot' && !ACTIVE_STATUSES.includes(selected.value.data.status)
      ? selected.value
      : null;
  if (target) {
    const linked = inputNodes(target.id)
      .filter((n) => n.type === 'material')
      .map(resolveMaterial);
    if (linked.some((m) => m?.kind === material.kind && m.name === material.name)) return true;
    const error = videoSelectionError([...linked, material], props.cfg);
    if (error) {
      say(error, 'error');
      return false;
    }
    if (graph.value.edges.length >= 1200) {
      say('画布连线已达上限', 'error');
      return false;
    }
  }
  checkpoint();
  let node = graph.value.nodes.find(
    (n) =>
      n.type === 'material' && n.data.materialKind === material.kind && n.data.materialName === material.name
  );
  if (!node) {
    if (graph.value.nodes.length >= 300) {
      say('画布最多容纳 300 个节点', 'error');
      return false;
    }
    const p = target
      ? { x: target.x - 365, y: target.y + inputNodes(target.id).length * (NODE_HEIGHT + 28) }
      : positionForNew();
    node = makeNode('material', p.x, p.y, {
      title: material.name,
      materialKind: material.kind,
      materialName: material.name,
      materialFile: material.file,
    });
    graph.value.nodes.push(node);
  }
  const connectionError = target && canConnect(graph.value.nodes, graph.value.edges, node.id, target.id);
  if (connectionError) {
    say(connectionError, 'error');
    return false;
  }
  if (target) {
    graph.value.edges.push({ id: uid(), source: node.id, target: target.id });
  }
  if (!target) selectedId.value = node.id;
  else drawerOpen.value = false;
  return true;
}
function selectNode(id) {
  selectedId.value = id;
  selectedEdge.value = '';
  queueOpen.value = false;
}
// Frame the card and its editor together when selecting, never while panning or zooming.
function frameCreation(node, centered = false) {
  const rect = board.value?.getBoundingClientRect();
  if (!rect || disposed) return;
  const view = graph.value.viewport;
  const maxZoom = Math.max(
    0.15,
    Math.min(
      (rect.height - composerHeight.value - COMPOSER_GAP - 56) / NODE_HEIGHT,
      (rect.width - 24) / NODE_WIDTH
    )
  );
  const zoom = Math.min(centered ? Math.max(view.zoom, 0.75) : view.zoom, maxZoom);
  const height = NODE_HEIGHT * zoom + COMPOSER_GAP + composerHeight.value;
  const width = Math.max(NODE_WIDTH * zoom, composerWidth.value);
  let x = view.x,
    y = view.y;
  if (centered || zoom !== view.zoom) {
    x = rect.width / 2 - (node.x + NODE_WIDTH / 2) * zoom;
    y = Math.max(16, (rect.height - 32 - height) / 2) - node.y * zoom;
  } else {
    const left = x + (node.x + NODE_WIDTH / 2) * zoom - width / 2;
    const top = y + node.y * zoom;
    x += left < 12 ? 12 - left : left + width > rect.width - 12 ? rect.width - 12 - left - width : 0;
    y += top < 16 ? 16 - top : top + height > rect.height - 32 ? rect.height - 32 - top - height : 0;
  }
  graph.value.viewport = { x, y, zoom };
}
watch(
  [selectedId, dimensions, composerHeight],
  async () => {
    await nextTick();
    if (!gesture && selected.value?.type === 'shot') frameCreation(selected.value);
  },
  { flush: 'post' }
);
async function focusNode(id) {
  selectNode(id);
  await nextTick();
  const node = nodesById.value.get(id);
  if (!node || disposed || !board.value) return;
  if (node.type === 'shot') {
    frameCreation(node, true);
    return;
  }
  const rect = board.value.getBoundingClientRect();
  const zoom = Math.max(graph.value.viewport.zoom, 0.75);
  graph.value.viewport = {
    x: rect.width / 2 - (node.x + NODE_WIDTH / 2) * zoom,
    y:
      Math.max(140, (rect.height - (node.type === 'shot' ? 280 : 0)) / 2) - (node.y + NODE_HEIGHT / 2) * zoom,
    zoom,
  };
}
function beginLink(id) {
  connecting.value = connecting.value === id ? '' : id;
  const n = graph.value.nodes.find((n) => n.id === id);
  if (n) pointer.value = { x: n.x + NODE_WIDTH + 70, y: n.y + 46 };
}
function finishLink(target) {
  if (!connecting.value) return say('先点击来源节点右侧的连接点');
  if (ACTIVE_STATUSES.includes(nodesById.value.get(target)?.data.status))
    return say('请先暂停并移出队列，再修改镜头参考', 'error');
  if (graph.value.edges.length >= 1200) return say('连线已达上限，请先整理画布', 'error');
  const error = canConnect(graph.value.nodes, graph.value.edges, connecting.value, target);
  if (error) return say(error, 'error');
  checkpoint();
  graph.value.edges.push({ id: uid(), source: connecting.value, target });
  connecting.value = '';
  selectedId.value = target;
}
function editSource(edgeId, patch) {
  if (ACTIVE_STATUSES.includes(selected.value?.data.status)) return;
  const edge = graph.value.edges.find((e) => e.id === edgeId && e.target === selectedId.value);
  if (!edge) return;
  const next = clone(graph.value);
  Object.assign(
    next.edges.find((e) => e.id === edgeId),
    patch
  );
  const error = videoSourcesError(
    videoInputs(next, selectedId.value),
    props.cfg,
    next.nodes.map((n) => n.id)
  );
  if (error && !error.startsWith('等待')) return say(error, 'error');
  checkpoint();
  Object.assign(edge, patch);
}
function disconnect(source) {
  if (ACTIVE_STATUSES.includes(selected.value?.data.status))
    return say('请先等待生成完成或移出队列', 'error');
  checkpoint();
  graph.value.edges = graph.value.edges.filter(
    (e) => !(e.source === source && e.target === selectedId.value)
  );
}
function removeSelected() {
  const edge = graph.value.edges.find((e) => e.id === selectedEdge.value);
  if (
    edge &&
    [edge.source, edge.target].some((id) => ACTIVE_STATUSES.includes(nodesById.value.get(id)?.data.status))
  )
    return say('排队或生成中的连线暂时不能断开', 'error');
  if (selected.value && ACTIVE_STATUSES.includes(selected.value.data.status))
    return say('请先等待生成完成或移出队列', 'error');
  if (
    selected.value &&
    graph.value.edges.some(
      (e) =>
        e.source === selectedId.value && ACTIVE_STATUSES.includes(nodesById.value.get(e.target)?.data.status)
    )
  )
    return say('这个节点仍被生成队列引用，请先清空等待队列', 'error');
  checkpoint();
  if (selectedEdge.value) graph.value.edges = graph.value.edges.filter((e) => e.id !== selectedEdge.value);
  else if (selected.value) {
    const id = selectedId.value;
    graph.value.nodes = graph.value.nodes.filter((n) => n.id !== id);
    graph.value.edges = graph.value.edges.filter((e) => e.source !== id && e.target !== id);
  }
  selectedId.value = '';
  selectedEdge.value = '';
  connecting.value = '';
}
function duplicate() {
  if (!selected.value || running.value || graph.value.nodes.length >= 300) return;
  checkpoint();
  const n = clone(selected.value);
  n.id = uid();
  n.x += 35;
  n.y += 285;
  n.data.title += ' · 副本';
  if (n.type === 'shot') {
    delete n.data.jobId;
    delete n.data.error;
    delete n.data.activeVersionId;
    n.data.versions = [];
    n.data.status = 'draft';
    graph.value.edges
      .filter((e) => e.target === selected.value.id)
      .forEach((e) => graph.value.edges.push({ ...clone(e), id: uid(), target: n.id }));
  }
  graph.value.nodes.push(n);
  selectedId.value = n.id;
}
function editNode(patch) {
  if (!selected.value || ACTIVE_STATUSES.includes(selected.value.data.status)) return;
  Object.assign(selected.value.data, patch);
}
function startNodeDrag(event, node) {
  if (event.button !== 0) return;
  checkpoint();
  selectNode(node.id);
  gesture = {
    kind: 'node',
    id: node.id,
    clientX: event.clientX,
    clientY: event.clientY,
    x: node.x,
    y: node.y,
  };
  board.value.setPointerCapture(event.pointerId);
}
function startPan(event) {
  if (event.button !== 0 && event.button !== 1) return;
  event.preventDefault();
  menuOpen.value = false;
  addMenuOpen.value = false;
  helpOpen.value = false;
  if (event.button === 0) selectedEdge.value = '';
  gesture = {
    kind: 'pan',
    clientX: event.clientX,
    clientY: event.clientY,
    blank: event.target === board.value,
    moved: false,
    ...graph.value.viewport,
  };
  board.value.setPointerCapture(event.pointerId);
}
function movePointer(event) {
  if (connecting.value) pointer.value = screenToWorld(event);
  if (!gesture) return;
  const dx = event.clientX - gesture.clientX,
    dy = event.clientY - gesture.clientY;
  if (gesture.kind === 'pan') {
    if (Math.hypot(dx, dy) > 5) gesture.moved = true;
    graph.value.viewport.x = gesture.x + dx;
    graph.value.viewport.y = gesture.y + dy;
  } else {
    const n = graph.value.nodes.find((n) => n.id === gesture.id);
    if (n) {
      n.x = Math.round(gesture.x + dx / graph.value.viewport.zoom);
      n.y = Math.round(gesture.y + dy / graph.value.viewport.zoom);
    }
  }
}
function endGesture(event) {
  if (
    event.type === 'pointerup' &&
    event.button === 0 &&
    gesture?.kind === 'pan' &&
    gesture.blank &&
    !gesture.moved
  ) {
    selectedId.value = '';
    selectedEdge.value = '';
    connecting.value = '';
  }
  gesture = null;
  if (board.value?.hasPointerCapture(event.pointerId)) board.value.releasePointerCapture(event.pointerId);
}
function zoomAt(nextZoom, x, y) {
  const v = graph.value.viewport;
  const zoom = Math.max(0.15, Math.min(2, nextZoom));
  const factor = zoom / v.zoom;
  graph.value.viewport = { x: x - (x - v.x) * factor, y: y - (y - v.y) * factor, zoom };
}
function wheel(event) {
  const rect = board.value.getBoundingClientRect();
  zoomAt(
    graph.value.viewport.zoom * Math.exp(-event.deltaY * 0.0015),
    event.clientX - rect.left,
    event.clientY - rect.top
  );
}
function fit() {
  const b = bounds.value;
  const zoom = Math.min(
    1,
    Math.max(
      0.15,
      Math.min((dimensions.value.width - 110) / b.width, (dimensions.value.height - 140) / b.height)
    )
  );
  graph.value.viewport = {
    x: (dimensions.value.width - b.width * zoom) / 2 - b.x * zoom,
    y: (dimensions.value.height - b.height * zoom) / 2 - b.y * zoom,
    zoom,
  };
}
function tidy() {
  checkpoint();
  graph.value.nodes = arrange(graph.value.nodes, graph.value.edges);
  fit();
}
function centerMap(event) {
  const point = event.currentTarget.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const world = point.matrixTransform(event.currentTarget.getScreenCTM().inverse());
  graph.value.viewport.x = dimensions.value.width / 2 - world.x * graph.value.viewport.zoom;
  graph.value.viewport.y = dimensions.value.height / 2 - world.y * graph.value.viewport.zoom;
}
function exportGraph() {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(graph.value, null, 2)], { type: 'application/json' })
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = `${props.project.title || '分镜'}-画布.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function cacheExport(jobId) {
  try {
    localStorage.setItem(`${cacheKey}.export`, jobId);
  } catch {
    /* Polling works without local storage. */
  }
}
async function startExport({ files, aspect }) {
  if (exportJob.value?.status === 'running') return;
  dialogMode.value = '';
  exportJob.value = { status: 'running', done: 0, total: files.length };
  try {
    const job = await api.exportCanvas(props.taskId, files, aspect);
    cacheExport(job.job_id);
    if (!disposed) pollExport(job.job_id);
  } catch (err) {
    exportJob.value = { status: 'failed', error: err.message };
  }
}
async function pollExport(jobId) {
  if (disposed) return;
  try {
    const job = await api.canvasExportStatus(props.taskId, jobId);
    if (job.url && !job.url.startsWith(`/files/${props.taskId}/export/`))
      throw new Error('导出返回了无效的文件地址');
    exportJob.value = job;
    if (job.status !== 'running') {
      cacheExport('');
      say(
        job.status === 'succeeded' ? '成片已导出，可以下载了' : job.error,
        job.status === 'succeeded' ? 'ok' : 'error'
      );
      return;
    }
  } catch (err) {
    if (err.status === 404 || !err.status) {
      exportJob.value = { status: 'failed', error: err.message };
      cacheExport('');
      return;
    }
  }
  if (!disposed) exportTimer = setTimeout(() => pollExport(jobId), 1500);
}
function onKey(event) {
  if (event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  if (event.key === 'Escape') {
    connecting.value = '';
    drawerOpen.value = false;
    menuOpen.value = false;
    addMenuOpen.value = false;
    helpOpen.value = false;
    expanded.value = false;
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    event.shiftKey ? redo() : undo();
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd') {
    event.preventDefault();
    duplicate();
  }
  if (event.key.toLowerCase() === 'f') fit();
  if (event.key === 'Delete' || event.key === 'Backspace') {
    if (selectedId.value || selectedEdge.value) {
      event.preventDefault();
      removeSelected();
    }
  }
}

function selectVersion(id, versionId) {
  const node = nodesById.value.get(id);
  if (!node || !videoVersions(node).some((v) => v.id === versionId)) return;
  node.data.activeVersionId = versionId;
}
function generationError(node, queuedIds = []) {
  const refs = inputNodes(node.id)
    .filter((n) => n.type === 'material')
    .map(resolveMaterial);
  const inputs = videoInputs(graph.value, node.id);
  return (
    mentionsError(node.data.description, refs) ||
    videoSourcesError(inputs, props.cfg, queuedIds) ||
    shotReadiness(
      node,
      refs,
      props.cfg,
      inputs.some((i) => i.edge.usage && i.edge.usage !== 'text')
    )
  );
}
async function uploadImages(files) {
  if (!ready.value || uploading.value) return;
  const images = [...files];
  if (!images.length) return;
  if (
    images.length > 8 ||
    images.some(
      (file) => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 15_000_000
    )
  )
    return say('每次最多上传 8 张 PNG、JPG 或 WebP 图片，每张不超过 15MB', 'error');
  if (graph.value.nodes.length + images.length > 300) return say('画布节点已达上限', 'error');
  uploading.value = true;
  const position = positionForNew();
  const target =
    selected.value?.type === 'shot' && !ACTIVE_STATUSES.includes(selected.value.data.status)
      ? selected.value
      : null;
  checkpoint();
  try {
    for (const [index, file] of images.entries()) {
      const uploaded = await api.uploadMaterial(props.taskId, 'image', file);
      if (disposed) break;
      const source = makeNode(
        'material',
        target ? target.x - 365 : position.x + index * 285,
        target ? target.y + index * 310 : position.y,
        {
          title: uploaded.name,
          materialKind: 'image',
          materialName: uploaded.name,
          materialFile: uploaded.entry?.images?.[0]?.split(/[\\/]/).pop() || '',
        }
      );
      graph.value.nodes.push(source);
      const liveTarget = target && nodesById.value.get(target.id);
      if (liveTarget && !ACTIVE_STATUSES.includes(liveTarget.data.status))
        graph.value.edges.push({ id: uid(), source: source.id, target: liveTarget.id });
    }
    emit('refresh');
    say('图片已上传到素材库并加入画布', 'ok');
  } catch (err) {
    emit('refresh');
    say(`图片上传失败：${err.message}`, 'error');
  } finally {
    uploading.value = false;
  }
}
function requestGeneration(ids) {
  if (!ready.value) return;
  const requested = new Set(ids);
  reviews.value = sequence.value
    .filter((n) => requested.has(n.id) && !ACTIVE_STATUSES.includes(n.data.status))
    .map((node) => ({
      id: node.id,
      title: node.data.title,
      error: generationError(node, ids),
      duration: node.data.duration,
      refs: inputNodes(node.id).filter((n) => n.type === 'material').length,
      manual: node.data.manual,
      count: node.data.candidateCount || 1,
      resolution:
        props.cfg.video_backend === 'api'
          ? '模型默认分辨率'
          : node.data.resolution && node.data.resolution !== 'custom'
            ? node.data.resolution.toUpperCase()
            : `${node.data.megapixels ?? defaultResolution(props.cfg)}MP`,
    }));
  if (!reviews.value.length) return say('没有可加入队列的分镜');
  if (ids.length === 1) {
    if (reviews.value[0].error) return say(reviews.value[0].error, 'error');
    startQueue([reviews.value[0].id]);
    return;
  }
  dialogMode.value = 'review';
}
async function startQueue(ids) {
  dialogMode.value = '';
  queuePaused.value = true;
  if (ids.length > 1) queueOpen.value = true;
  past.value = [];
  future.value = [];
  ids.forEach((id) => {
    const node = nodesById.value.get(id);
    if (node && !ACTIVE_STATUSES.includes(node.data.status)) {
      node.data.status = 'queued';
      node.data.error = '';
    }
  });
  if (!(await flushQueue())) {
    queueOpen.value = true;
    return say('队列尚未保存，修复保存问题后可继续生成', 'error');
  }
  queuePaused.value = false;
  dispatchQueue();
}
async function flushQueue() {
  // Camera resize and a just-completed job can change the document during a save.
  // Drain those changes before paid work; never interpret a still-dirty save as success.
  for (let attempt = 0; attempt < 6 && !disposed; attempt++) {
    await nextTick();
    for (let i = 0; saving.value && i < 200 && !disposed; i++)
      await new Promise((resolve) => setTimeout(resolve, 50));
    if (disposed || saving.value || conflicted.value) return false;
    await save();
    await nextTick();
    if (!dirty.value && !saveError.value) return true;
    if (saveError.value) return false;
  }
  return false;
}
function cancelQueue() {
  queuePaused.value = true;
  queuedShots.value.forEach((n) => {
    n.data.status = 'draft';
    n.data.error = '';
  });
  say('已移除等待中的镜头，正在生成的镜头会继续完成');
}
async function dispatchQueue() {
  if (
    disposed ||
    !ready.value ||
    queuePaused.value ||
    dispatching ||
    activeShots.value.length ||
    !queuedShots.value.length
  )
    return;
  dispatching = true;
  const node = queuedShots.value[0];
  try {
    if (!(await flushQueue())) {
      queuePaused.value = true;
      say('队列保存失败，已暂停生成', 'error');
      return;
    }
    if (!disposed && !queuePaused.value && node.data.status === 'queued') await generate(node.id);
  } finally {
    dispatching = false;
    if (node.data.status === 'failed') queuePaused.value = true;
    if (!disposed && !queuePaused.value) dispatchQueue();
  }
}
watch(
  () =>
    `${ready.value}:${queuePaused.value}:${sequence.value.map((n) => `${n.id}:${n.data.status}`).join(',')}`,
  dispatchQueue
);
async function generate(id) {
  const node = graph.value.nodes.find((n) => n.id === id);
  if (!node || ['composing', 'running'].includes(node.data.status)) return;
  const error = generationError(node);
  if (error) {
    node.data.status = 'failed';
    node.data.error = error;
    say(error, 'error');
    return;
  }
  const materialNodes = inputNodes(id).filter((n) => n.type === 'material');
  const refs = materialNodes.map(resolveMaterial);
  const requestData = clone(node.data);
  const linkedVideos = videoInputs(graph.value, id);
  const videoSources = sourceSnapshot(linkedVideos);
  const context = [
    sourceContext(linkedVideos),
    ...inputNodes(id)
      .filter((n) => n.type === 'note')
      .map((n) => n.data.text),
  ]
    .filter(Boolean)
    .join('\n');
  const pixelBudget = submissionResolution(requestData.megapixels, props.cfg);
  const settings = creationSettings(requestData, props.cfg);
  past.value = [];
  future.value = [];
  // Every attempt lives inside this creation, including its prompt/settings snapshot.
  node.data.versions ||= [];
  const attemptIds = [];
  for (let index = 0; index < settings.candidate_count; index++) {
    const version = {
      videoSources: clone(videoSources),
      id: uid(),
      status: 'composing',
      candidateIndex: index,
      description: requestData.description,
      manual: requestData.manual,
      backend: props.cfg.video_backend,
      duration: requestData.duration,
      megapixels: pixelBudget ?? null,
      ratio: settings.ratio,
      resolution: settings.resolution,
      generateAudio: settings.generate_audio,
      exactDuration: settings.exact_duration,
      createdAt: new Date().toISOString(),
    };
    node.data.versions.push(version);
    attemptIds.push(version.id);
  }
  const attempts = node.data.versions.filter((v) => attemptIds.includes(v.id));
  node.data.progress = { done: 0, total: settings.candidate_count };
  node.data.status = 'composing';
  node.data.error = '';
  node.data.promptWarnings = [];
  try {
    const capabilities = await api.videoCapabilities();
    if (capabilities.controls_version !== 1) throw new Error('后端尚未支持新的生成设置，请更新并重启后端');
    if (videoSources.length && capabilities.video_sources_version !== 1)
      throw new Error('后端尚未支持视频参考，请重启更新后的后端');
    const payload = {
      characters: refs.filter((m) => m.kind === 'character').map((m) => m.name),
      props: refs.filter((m) => m.kind === 'prop').map((m) => m.name),
      scene: refs.find((m) => m.kind === 'scene')?.name || '',
      images: refs.filter((m) => m.kind === 'image').map((m) => m.name),
      duration: requestData.duration,
      use_voice: false,
      video_sources: videoSources,
    };
    if (requestData.manual) payload.video_prompt = requestData.description;
    else {
      payload.description = requestData.description;
      payload.context = context.slice(0, 4000);
    }
    const composed = await api.composeSegment(props.taskId, payload);
    node.data.promptWarnings = [...new Set((composed.warnings || []).filter((w) => typeof w === 'string'))];
    attempts.forEach((v) => {
      v.prompt = composed.prompt;
      v.promptWarnings = [...node.data.promptWarnings];
      v.videoSources = composed.video_sources || [];
    });
    const job = await api.startSegmentVideo(props.taskId, {
      video_sources: (composed.video_sources || videoSources).map(
        ({
          source_node,
          version_id,
          file,
          usage,
          start,
          end,
          tail_seconds,
          motion_reference,
          expected_sha256,
        }) => ({
          source_node,
          version_id,
          file,
          usage,
          start,
          end,
          tail_seconds,
          motion_reference,
          expected_sha256,
        })
      ),
      frames: refs.map((m) => `${m.kind}:${m.index}`),
      frame_names: Object.fromEntries(refs.map((m) => [`${m.kind}:${m.index}`, m.name])),
      prompt: composed.prompt,
      ...settings,
      soundscape: composed.soundscape || '',
      duration: requestData.duration,
      megapixels: pixelBudget,
      expected_backend: composed.video_backend,
      expected_workflow: composed.video_workflow,
      note: requestData.manual ? '' : displayPrompt(requestData.description),
    });
    node.data.jobId = job.job_id;
    node.data.status = 'running';
    attempts.forEach((v, i) => {
      v.jobId = job.job_id;
      v.status = i ? 'queued' : 'running';
      v.seed = job.candidates?.[i]?.seed;
      Object.assign(v, { width: job.settings?.width, height: job.settings?.height });
    });
    if (job.warning) say(job.warning, 'error');
    if (job.missing?.length) say(`这些素材未进入模型：${job.missing.join('、')}`, 'error');
    await save();
    if (!disposed) pollJob(id, job.job_id);
  } catch (err) {
    node.data.status = 'failed';
    node.data.error = err.message;
    attempts.forEach((v) => {
      v.status = 'failed';
      v.error = err.message;
    });
    say(err.message, 'error');
    if (disposed) {
      dirty.value = true;
      save();
    }
  }
}
function pollJob(nodeId, jobId) {
  if (disposed || jobTimers.has(jobId)) return;
  const poll = async () => {
    if (disposed) return;
    try {
      const job = await api.segmentVideo(jobId, props.taskId);
      const node = graph.value.nodes.find((n) => n.id === nodeId);
      if (!node || node.data.jobId !== jobId) return;
      applyVideoProgress(node, { ...job, job_id: jobId }, props.taskId);
      if (['succeeded', 'failed', 'interrupted'].includes(job.status)) {
        node.data.status = job.status;
        node.data.error = job.error || '';
        if (job.status !== 'succeeded') queuePaused.value = true;
        jobTimers.delete(jobId);
        past.value = [];
        future.value = [];
        emit('segment-done', job);
        say(
          job.status === 'succeeded'
            ? `「${node.data.title}」已生成 ${videoVersions(node).filter((v) => v.jobId === jobId && v.status === 'succeeded').length} 个候选${job.error ? `，${job.error}` : ''}`
            : job.error,
          job.error ? 'error' : 'ok'
        );
        return;
      }
    } catch (err) {
      if (err.status === 404) {
        const node = graph.value.nodes.find((n) => n.id === nodeId);
        if (node) {
          node.data.status = 'interrupted';
          node.data.error = '后端重启后无法继续查询任务。请查看生成记录，确认远端任务结果后再生成。';
        }
        queuePaused.value = true;
        videoVersions(node)
          .filter((v) => v.jobId === jobId)
          .forEach((v) => {
            v.status = 'interrupted';
          });
        jobTimers.delete(jobId);
        return;
      }
    }
    if (!disposed) jobTimers.set(jobId, setTimeout(poll, 3000));
  };
  jobTimers.set(jobId, setTimeout(poll, 300));
}

onMounted(async () => {
  await load();
  await nextTick();
  if (disposed) return;
  try {
    const jobId = localStorage.getItem(`${cacheKey}.export`);
    if (/^[a-f0-9]{12}$/.test(jobId || '')) pollExport(jobId);
  } catch {
    /* Browser storage can be disabled. */
  }
  let measured = false;
  let hadInspector = !!(queueOpen.value || (selected.value && selected.value.type !== 'shot'));
  observer = new ResizeObserver(([entry]) => {
    const next = { width: entry.contentRect.width, height: entry.contentRect.height };
    const hasInspector = !!(queueOpen.value || (selected.value && selected.value.type !== 'shot'));
    if (measured && hadInspector === hasInspector) {
      const dx = (next.width - dimensions.value.width) / 2;
      const dy = (next.height - dimensions.value.height) / 2;
      graph.value.viewport.x += dx;
      graph.value.viewport.y += dy;
      if (gesture?.kind === 'pan') {
        gesture.x += dx;
        gesture.y += dy;
      }
    }
    dimensions.value = next;
    hadInspector = hasInspector;
    measured = true;
  });
  if (board.value) observer.observe(board.value);
  if (selectedId.value) {
    const restoredQueue = queueOpen.value;
    await focusNode(selectedId.value);
    queueOpen.value = restoredQueue;
  }
});
onBeforeUnmount(() => {
  disposed = true;
  clearTimeout(saveTimer);
  clearTimeout(backupTimer);
  clearTimeout(exportTimer);
  observer?.disconnect();
  jobTimers.forEach(clearTimeout);
  jobTimers.clear();
  backup();
  save();
});
</script>

<template>
  <section
    class="workflow-editor"
    :class="{ expanded, overview: graph.viewport.zoom < 0.45 }"
    tabindex="0"
    aria-label="视频创作画布"
    @keydown="onKey"
  >
    <header class="flow-toolbar">
      <div class="flow-title">
        <span class="flow-mark"><WorkflowIcon name="shot" /></span>
        <div><b>创作画布</b><span>VIDEO CANVAS</span></div>
        <span class="flow-divider"></span
        ><span class="save-state" :class="{ error: saveError || loadError }"
          ><i></i
          >{{
            loading
              ? '正在载入'
              : saveError
                ? '待保存'
                : saving
                  ? '保存中'
                  : dirty
                    ? '有未保存修改'
                    : '已保存到作品'
          }}</span
        >
      </div>
      <div class="flow-actions">
        <button class="story-builder-button" :disabled="!ready || running" @click="dialogMode = 'story'">
          <WorkflowIcon name="note" /><span class="toolbar-label">剧本建镜</span>
        </button>
        <button title="撤销 Ctrl+Z" aria-label="撤销" :disabled="!past.length || running" @click="undo">
          <WorkflowIcon name="undo" /></button
        ><button
          title="重做 Ctrl+Shift+Z"
          aria-label="重做"
          :disabled="!future.length || running"
          @click="redo"
        >
          <WorkflowIcon name="redo" /></button
        ><span class="flow-divider"></span>
        <button title="自动整理节点位置" aria-label="整理画布" @click="tidy" :disabled="!ready">
          <WorkflowIcon name="layout" /><span class="toolbar-label">整理</span>
        </button>
        <button
          title="展开或收起画布"
          :aria-label="expanded ? '收起画布' : '展开画布'"
          @click="expanded = !expanded"
        >
          <WorkflowIcon name="fit" />
        </button>
        <div class="flow-more">
          <button aria-label="更多画布操作" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen">
            <WorkflowIcon name="more" />
          </button>
          <div v-if="menuOpen" class="flow-menu">
            <button
              :disabled="!ready || importing || running"
              @click="
                menuOpen = false;
                importRecords();
              "
            >
              <WorkflowIcon name="link" />{{ importing ? '正在导入…' : '导入生成记录' }}</button
            ><button
              :disabled="!ready"
              @click="
                menuOpen = false;
                exportGraph();
              "
            >
              <WorkflowIcon name="download" />导出画布备份</button
            ><button
              :disabled="!ready || running"
              @click="
                menuOpen = false;
                importInput.click();
              "
            >
              <WorkflowIcon name="upload" />导入画布备份</button
            ><button
              @click="
                menuOpen = false;
                helpOpen = !helpOpen;
              "
            >
              <WorkflowIcon name="help" />画布操作说明
            </button>
          </div>
        </div>
        <button
          class="queue-toggle"
          :class="{ active: queueOpen }"
          aria-label="打开生成队列"
          @click="queueOpen = !queueOpen"
        >
          <WorkflowIcon name="queue" /><span class="toolbar-label">队列</span
          ><small v-if="queuedShots.length || activeShots.length">{{
            queuedShots.length + activeShots.length
          }}</small>
        </button>
        <button
          class="export-button"
          :aria-label="exportJob?.status === 'running' ? '正在导出' : '导出成片'"
          :disabled="!exportItems.length || exportJob?.status === 'running'"
          @click="dialogMode = 'export'"
        >
          <WorkflowIcon name="download" /><span class="toolbar-label">{{
            exportJob?.status === 'running' ? '正在导出' : '导出成片'
          }}</span>
        </button>
        <button
          class="add-shot-button"
          :disabled="!ready || !pendingShots.length"
          @click="requestGeneration(pendingShots.map((n) => n.id))"
        >
          <WorkflowIcon name="play" /><span>批量生成</span>
        </button>
      </div>
    </header>
    <div v-if="saveError" class="flow-alert" role="alert">
      <span>{{ saveError }}</span
      ><button @click="conflicted ? reload() : save()">{{ conflicted ? '重新载入' : '重试保存' }}</button
      ><button @click="exportGraph">导出备份</button>
    </div>
    <div v-if="exportJob" class="export-status" role="status">
      <WorkflowIcon name="download" /><span>{{
        exportJob.status === 'running'
          ? `正在整理成片 · ${exportJob.done || 0} / ${exportJob.total} 镜`
          : exportJob.status === 'succeeded'
            ? '成片已就绪'
            : exportJob.error
      }}</span
      ><a v-if="exportJob.status === 'succeeded'" :href="exportJob.url" download>下载成片</a
      ><button v-if="exportJob.status !== 'running'" aria-label="收起导出结果" @click="exportJob = null">
        <WorkflowIcon name="close" />
      </button>
    </div>
    <div class="flow-body">
      <div
        ref="board"
        class="flow-board"
        :class="{ panning: gesture?.kind === 'pan', linking: connecting }"
        :style="{
          backgroundSize: `${24 * graph.viewport.zoom}px ${24 * graph.viewport.zoom}px`,
          backgroundPosition: `${graph.viewport.x}px ${graph.viewport.y}px`,
          backgroundImage: graph.viewport.zoom < 0.45 ? 'none' : undefined,
        }"
        @pointerdown="startPan"
        @pointermove="movePointer"
        @pointerup="endGesture"
        @pointercancel="endGesture"
        @wheel.prevent="wheel"
        @dragover.prevent
        @drop.prevent="uploadImages($event.dataTransfer.files)"
      >
        <div v-if="loading || loadError" class="flow-loading">
          <WorkflowIcon name="shot" />
          <h3>{{ loading ? '正在准备你的创作画布' : '画布暂时无法载入' }}</h3>
          <p>{{ loadError || '素材与分镜将在这里连接起来' }}</p>
          <button v-if="loadError" @pointerdown.stop @click="load">重试载入</button>
        </div>
        <template v-else>
          <div class="flow-world" :style="worldStyle">
            <svg class="flow-edges" overflow="visible" aria-label="节点连线">
              <g
                v-for="edge in drawnEdges"
                :key="edge.id"
                :class="{
                  chosen: selectedEdge === edge.id,
                  highlighted: edge.source === selectedId || edge.target === selectedId,
                }"
              >
                <path
                  class="edge-hit"
                  :d="edge.path"
                  @pointerdown.stop
                  @click.stop="
                    selectedEdge = edge.id;
                    selectedId = '';
                  "
                />
                <path class="edge-line" :d="edge.path" />
              </g>
              <path v-if="loosePath" class="loose-edge" :d="loosePath" />
            </svg>
            <WorkflowNode
              v-for="node in graph.nodes"
              :key="node.id"
              :node="node"
              :material="resolveMaterial(node)"
              :dimmed="false"
              :selected="selectedId === node.id"
              :connecting="!!connecting"
              :targetable="!!connecting && !canConnect(graph.nodes, graph.edges, connecting, node.id)"
              @select="selectNode"
              @focus="focusNode"
              @drag="startNodeDrag"
              @connect="beginLink"
              @finish-connect="finishLink"
              @generate="requestGeneration([$event])"
              @version="selectVersion"
            />
          </div>
          <div class="canvas-tools" @pointerdown.stop @wheel.stop>
            <button title="添加视频" aria-label="添加视频" @click="chooseAdd">
              <WorkflowIcon name="plus" />
            </button>
            <div v-if="addMenuOpen" class="add-video-menu" aria-label="添加视频选项">
              <button @click="addShot(false)">空白视频</button
              ><button @click="addShot(true)">续拍当前视频</button>
            </div>
            <span></span
            ><button
              title="素材库"
              aria-label="打开素材库"
              :class="{ active: drawerOpen }"
              @click="drawerOpen = !drawerOpen"
            >
              <WorkflowIcon name="grid" /></button
            ><button
              :disabled="uploading"
              title="上传参考图，也可拖入画布"
              aria-label="上传参考图片"
              @click="uploadInput.click()"
            >
              <WorkflowIcon name="upload" /></button
            ><button title="添加便签" aria-label="添加便签" @click="addNote">
              <WorkflowIcon name="note" /></button
            ><span></span
            ><button title="显示全部节点 F" aria-label="显示全部节点" @click="fit">
              <WorkflowIcon name="fit" />
            </button>
          </div>
          <div class="canvas-caption" @pointerdown.stop>
            <span
              >{{ shotCount }} 个视频<span class="caption-dot">·</span>{{ totalDuration }} 秒计划时长</span
            >
          </div>
          <aside
            v-if="drawerOpen"
            class="material-drawer"
            aria-label="画布素材库"
            @pointerdown.stop
            @wheel.stop
          >
            <header>
              <div>
                <b>素材库</b><span>{{ materials.length }} 项作品素材</span>
              </div>
              <button aria-label="关闭素材库" @click="drawerOpen = false">
                <WorkflowIcon name="close" />
              </button>
            </header>
            <div class="material-search">
              <WorkflowIcon name="search" /><input
                v-model="search"
                placeholder="搜索角色、场景、道具…"
                aria-label="搜索画布素材"
              />
            </div>
            <div class="material-filters">
              <button
                v-for="kind in ['all', 'character', 'scene', 'prop', 'image']"
                :key="kind"
                :class="{ active: kindFilter === kind }"
                @click="kindFilter = kind"
              >
                {{ kind === 'all' ? '全部' : KIND_LABELS[kind] }}
              </button>
            </div>
            <p class="drawer-hint">
              {{
                selected?.type === 'shot'
                  ? `点击素材，连接到「${selected.data.title}」`
                  : '点击素材，将它放入画布'
              }}
            </p>
            <div class="drawer-items">
              <button
                v-for="m in filteredMaterials"
                :key="`${m.kind}:${m.name}`"
                class="drawer-material"
                @click="addMaterial(m)"
              >
                <img v-if="m.url" :src="m.url" :alt="m.name" /><span v-else class="material-placeholder"
                  ><WorkflowIcon name="image" /></span
                ><span class="material-copy"
                  ><b>{{ m.name }}</b
                  ><span>{{ KIND_LABELS[m.kind] }} · {{ m.ready ? '已就绪' : '待准备' }}</span></span
                ><WorkflowIcon name="plus" />
              </button>
              <div v-if="!filteredMaterials.length" class="drawer-empty">
                {{ search ? '没有匹配的素材' : '还没有这类素材'
                }}<button @click="emit('materials')">前往素材工坊 <WorkflowIcon name="arrow" /></button>
              </div>
            </div>
            <footer>
              <button @click="emit('materials')">管理作品素材<WorkflowIcon name="arrow" /></button>
            </footer>
          </aside>
          <div v-if="connecting" class="connect-hint" @pointerdown.stop>
            <WorkflowIcon name="link" />点击目标节点左侧连接点<button @click="connecting = ''">取消</button>
          </div>
          <div v-if="selectedEdge" class="edge-toolbar" @pointerdown.stop>
            <WorkflowIcon name="link" /><span>已选中连线</span
            ><button @click="removeSelected"><WorkflowIcon name="trash" />断开</button>
          </div>
          <div class="zoom-control" @pointerdown.stop @wheel.stop>
            <button
              aria-label="缩小画布"
              @click="zoomAt(graph.viewport.zoom / 1.2, dimensions.width / 2, dimensions.height / 2)"
            >
              −</button
            ><button title="恢复 100% 缩放" @click="zoomAt(1, dimensions.width / 2, dimensions.height / 2)">
              {{ Math.round(graph.viewport.zoom * 100) }}%</button
            ><button
              aria-label="放大画布"
              @click="zoomAt(graph.viewport.zoom * 1.2, dimensions.width / 2, dimensions.height / 2)"
            >
              +</button
            ><span></span
            ><button title="显示全部节点" aria-label="适应画布" @click="fit">
              <WorkflowIcon name="fit" />
            </button>
          </div>
          <div class="minimap" @pointerdown.stop @wheel.stop>
            <svg :viewBox="mapViewbox" aria-label="画布小地图，点击定位" @click="centerMap">
              <path
                v-for="edge in drawnEdges"
                :key="edge.id"
                :d="edge.path"
                fill="none"
                stroke="#515863"
                stroke-width="4"
              />
              <rect
                v-for="node in graph.nodes"
                :key="node.id"
                :x="node.x"
                :y="node.y"
                :width="NODE_WIDTH"
                :height="NODE_HEIGHT"
                rx="16"
                :fill="
                  node.type === 'shot'
                    ? '#a58ac0'
                    : node.type === 'material'
                      ? '#6d9484'
                      : node.type === 'note'
                        ? '#b3a56d'
                        : '#a67c58'
                "
                :opacity="selectedId === node.id ? 1 : 0.7"
              />
              <rect
                v-bind="viewportRect"
                fill="#ffffff05"
                stroke="#d3d8e3"
                stroke-width="5"
                rx="8"
                pointer-events="none"
              /></svg
            ><span>全局视图</span>
          </div>
          <div class="canvas-bottom" @pointerdown.stop>
            <button class="help-entry" @click="helpOpen = !helpOpen">
              <WorkflowIcon name="help" />操作说明</button
            ><span><i></i>本地画布</span>
          </div>
          <div v-if="helpOpen" class="canvas-help" @pointerdown.stop>
            <header>
              <b>画布操作</b
              ><button aria-label="关闭操作说明" @click="helpOpen = false">
                <WorkflowIcon name="close" />
              </button>
            </header>
            <p>点击画布左侧或镜头序列中的加号添加视频。</p>
            <p>拖动标题栏移动节点，拖动空白平移画布，滚轮缩放。</p>
            <p>点击输出圆点，再点击目标输入圆点，即可连线。</p>
            <div>
              <span>撤销</span><kbd>Ctrl Z</kbd><span>重做</span><kbd>Ctrl Shift Z</kbd
              ><span>移除节点或连线</span><kbd>Delete</kbd>
            </div>
          </div>
          <VideoComposer
            v-if="selected?.type === 'shot'"
            :style="composerPosition"
            :anchor="composerPosition"
            @resize="composerHeight = $event"
            :node="selected"
            :inputs="selectedInputs"
            :video-inputs="videoInputs(graph, selectedId)"
            @source-change="editSource"
            :cfg="cfg"
            :error="generationError(selected)"
            :resolve-material="resolveMaterial"
            :materials="materials"
            @edit="editNode"
            @checkpoint="checkpoint"
            @close="selectedId = ''"
            @generate="requestGeneration([$event])"
            @references="drawerOpen = !drawerOpen"
            @upload="uploadInput.click()"
            @configure="emit('configure')"
            @duplicate="duplicate"
            @remove="removeSelected"
            @disconnect="disconnect"
            @version="selectVersion(selectedId, $event)"
          />
          <div v-if="!graph.nodes.length" class="empty-canvas" @pointerdown.stop>
            <WorkflowIcon name="shot" />
            <h3>给你的故事一个起点</h3>
            <p>添加一个视频，直接写下你想拍的画面。</p>
            <button @click="addShot()"><WorkflowIcon name="plus" />添加第一个视频</button>
          </div>
        </template>
      </div>
      <aside v-if="queueOpen" class="studio-overview" aria-label="创作概览和生成队列">
        <header>
          <b>{{ queueOpen ? '生成队列' : '创作概览' }}</b
          ><span>{{ queueOpen ? 'QUEUE' : 'OVERVIEW' }}</span
          ><button v-if="queueOpen" aria-label="关闭生成队列" @click="queueOpen = false">
            <WorkflowIcon name="close" />
          </button>
        </header>
        <div class="overview-scroll">
          <div class="overview-counts">
            <div>
              <b>{{ shotCount }}</b
              ><span>分镜</span>
            </div>
            <div>
              <b>{{ totalDuration }}<small>s</small></b
              ><span>计划时长</span>
            </div>
            <div>
              <b>{{ completedShots.length }}</b
              ><span>已完成</span>
            </div>
          </div>
          <div class="overview-section">
            <span class="overview-label">生成引擎</span
            ><button class="engine-card" @click="emit('configure')">
              <span class="engine-logo"><WorkflowIcon name="spark" /></span>
              <div>
                <b>{{ cfg.video_backend === 'api' ? '视频 API' : 'ComfyUI' }}</b
                ><span>{{
                  serviceConfigured
                    ? cfg.video_backend === 'api'
                      ? '单张首帧'
                      : cfg.video_workflow === 'ref2va'
                        ? 'Ref2VA · 多图参考'
                        : 'I2V · 首帧生成'
                    : '待配置连接地址'
                }}</span>
              </div>
              <WorkflowIcon name="settings" />
            </button>
            <p class="overview-hint">
              {{
                serviceConfigured
                  ? '已填写配置 · 连接可用性请在服务设置中测试'
                  : '可以先编排分镜，生成时再连接服务。'
              }}
            </p>
          </div>
          <template v-if="queuedShots.length || activeShots.length">
            <div class="queue-controls">
              <b>{{ queuePaused ? '队列已暂停' : '正在逐镜生成' }}</b
              ><button v-if="queuedShots.length" @click="queuePaused = !queuePaused">
                {{ queuePaused ? '继续队列' : '暂停队列' }}
              </button>
            </div>
            <div class="queue-list">
              <button
                v-for="node in [...activeShots, ...queuedShots]"
                :key="node.id"
                @click="
                  focusNode(node.id);
                  queueOpen = false;
                "
              >
                <i :class="node.data.status"></i>
                <div>
                  <b>{{ node.data.title }}</b
                  ><span>{{ STATUS_LABELS[node.data.status] }} · {{ node.data.duration }}s</span>
                </div>
                <WorkflowIcon name="arrow" />
              </button>
            </div>
            <button v-if="queuedShots.length" class="clear-queue" @click="cancelQueue">
              移除等待中的 {{ queuedShots.length }} 镜
            </button>
            <p class="overview-hint">暂停只影响未提交的镜头。当前生成会继续完成；队列遇到失败会暂停。</p>
          </template>
          <template v-else
            ><div class="overview-section">
              <span class="overview-label">创作步骤</span>
              <ol class="creation-checklist">
                <li :class="{ done: materials.some((m) => m.ready) }">
                  <WorkflowIcon name="image" />
                  <div><b>准备视觉素材</b><span>角色、场景、道具与参考图</span></div>
                  <button @click="emit('materials')"><WorkflowIcon name="arrow" /></button>
                </li>
                <li :class="{ done: sequence.some((n) => n.data.description) }">
                  <WorkflowIcon name="shot" />
                  <div><b>安排你的镜头</b><span>描述画面，连接参考素材</span></div>
                  <button :disabled="!ready || running" @click="dialogMode = 'story'">
                    <WorkflowIcon name="arrow" />
                  </button>
                </li>
                <li :class="{ done: completedShots.length }">
                  <WorkflowIcon name="video" />
                  <div><b>让画面动起来</b><span>逐镜生成，保留每次产出</span></div>
                </li>
              </ol>
            </div>
            <div class="queue-empty">
              <WorkflowIcon name="queue" /><b>队列暂时为空</b>
              <p>选中分镜生成，或点击顶部批量生成。检查通过后才会提交。</p>
            </div></template
          >
          <div v-if="sequence.some((n) => n.data.error)" class="overview-section">
            <span class="overview-label">需要处理</span
            ><button
              v-for="node in sequence.filter((n) => n.data.error)"
              :key="node.id"
              class="failed-item"
              @click="
                focusNode(node.id);
                queueOpen = false;
              "
            >
              <b>{{ node.data.title }}</b
              ><span>{{ node.data.error }}</span>
            </button>
          </div>
        </div>
        <footer><WorkflowIcon name="shield" />本地保存 · 你的创作由你掌控</footer>
      </aside>
      <WorkflowInspector
        v-else-if="selected && selected.type !== 'shot'"
        :node="selected"
        :material="resolveMaterial(selected)"
        :generating="ACTIVE_STATUSES.includes(selected.data.status)"
        @edit="editNode"
        @checkpoint="checkpoint"
        @close="selectedId = ''"
        @duplicate="duplicate"
        @remove="removeSelected"
      />
    </div>
    <StoryboardSequence
      :shots="sequence"
      :selected-id="selectedId"
      :output="(shot) => !!currentVideo(shot)"
      :collapsed="sequenceCollapsed"
      @select="
        (id) => {
          focusNode(id);
          queueOpen = false;
        }
      "
      @add="chooseAdd"
      @toggle="sequenceCollapsed = !sequenceCollapsed"
    />
    <WorkflowDialogs
      v-if="dialogMode"
      :mode="dialogMode"
      :reviews="reviews"
      :cfg="cfg"
      :exports="exportItems"
      :shot-count="shotCount"
      @close="dialogMode = ''"
      @confirm="startQueue"
      @storyboard="buildStoryboard"
      @export="startExport"
      @configure="
        dialogMode = '';
        emit('configure');
      "
    />
    <input ref="importInput" type="file" accept=".json,application/json" hidden @change="importBackup" />
    <input
      ref="uploadInput"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      multiple
      hidden
      @change="
        uploadImages($event.target.files);
        $event.target.value = '';
      "
    />
  </section>
</template>

<style scoped src="../canvas.css"></style>

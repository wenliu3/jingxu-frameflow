import { canConnect, clone, makeShot, uid } from './workflowGraph.js';
import { creationSettingsError, videoSelectionError } from './videoSettings.js';
import { unifyVideoNodes } from './workflowVideo.js';
import { videoInputs, videoSourcesError } from './videoSources.js';

export const STATUS_LABELS = {
  empty: '等待输出',
  draft: '待创作',
  queued: '排队中',
  composing: '编排提示词',
  running: '生成中',
  succeeded: '已完成',
  failed: '生成失败',
  interrupted: '已中断',
};
export const ACTIVE_STATUSES = ['queued', 'composing', 'running'];
export const STORY_TEMPLATES = [
  {
    id: 'film',
    title: '电影叙事',
    subtitle: '环境 → 人物 → 细节',
    image: 'mountain',
    shots: [
      '远景建立环境，晨光穿过山间薄雾，镜头缓缓推进。',
      '中景跟随主角走入画面，停下脚步，望向远处。',
      '特写主角的目光和手中物件，镜头静止，留下悬念。',
    ],
  },
  {
    id: 'ad',
    title: '产品短片',
    subtitle: '亮相 → 细节 → 定格',
    image: 'neon',
    shots: [
      '产品置于简洁场景中央，柔和侧光勾勒轮廓，镜头缓慢推进。',
      '微距展示产品材质和关键细节，浅景深，平稳横移。',
      '产品回到完整画面，光线缓缓变化，镜头拉远后静止。',
    ],
  },
  {
    id: 'life',
    title: '日常故事',
    subtitle: '开场 → 发现 → 回应',
    image: 'summer',
    shots: [
      '午后的街角，主角走过树影斑驳的人行道，镜头平稳跟拍。',
      '主角停下脚步，发现一封信，低头阅读，近景缓慢推进。',
      '主角抬头微笑，风轻轻吹过衣角，中景静止，温暖自然光。',
    ],
  },
];

// Stable topological order keeps linked shots in story order, independent of canvas placement.
export function orderedShots(graph) {
  const shots = graph.nodes.filter((n) => n.type === 'shot');
  const ids = new Set(shots.map((n) => n.id));
  const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const incoming = new Map(shots.map((n) => [n.id, edges.filter((e) => e.target === n.id).length]));
  const pending = [...shots],
    result = [];
  while (pending.length) {
    const index = pending.findIndex((n) => incoming.get(n.id) === 0);
    if (index < 0) return [...result, ...pending];
    const [node] = pending.splice(index, 1);
    result.push(node);
    edges
      .filter((e) => e.source === node.id)
      .forEach((e) => incoming.set(e.target, incoming.get(e.target) - 1));
  }
  return result;
}

export function shotReadiness(node, refs, cfg, hasVideo = false) {
  const settingError = creationSettingsError(node.data);
  if (settingError) return settingError;
  if (!node.data.description?.trim()) return '还没有画面描述';
  if (refs.some((r) => !r?.ready)) return '参考素材未就绪或已移除';
  const error = videoSelectionError(refs, cfg, hasVideo);
  if (error) return error;
  if (cfg.video_backend === 'api') {
    if (!cfg.video_api_url || !cfg.video_api_key || !cfg.video_api_model) return '请配置视频 API';
  } else if (!cfg.comfyui_url) return '请先配置 ComfyUI 地址';
  if (!node.data.manual && !cfg.text_api_key) return '请配置文本模型，或自己写英文提示词';
  return '';
}

export function scriptParagraphs(text) {
  return text
    .split(/\n\s*\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function appendStoryboard(graph, descriptions, { duration = 5, sources = [] } = {}) {
  if (!descriptions.length || descriptions.length > 30) throw new Error('每次请创建 1–30 个分镜');
  if (descriptions.some((text) => text.length > 1000)) throw new Error('每个分镜描述不能超过 1000 字');
  if (graph.nodes.length + descriptions.length > 300) throw new Error('画布最多容纳 300 个节点');
  if (graph.edges.length + descriptions.length * (sources.length + 1) - 1 > 1200)
    throw new Error('画布连线已达上限');
  const top = graph.nodes.length ? Math.max(...graph.nodes.map((n) => n.y)) + 330 : 80;
  const start = graph.nodes.filter((n) => n.type === 'shot').length;
  let previous;
  return descriptions.map((text, index) => {
    const shot = makeShot(430 + index * 365, top, start + index + 1);
    shot.data.description = text;
    shot.data.duration = duration;
    graph.nodes.push(shot);
    if (previous) graph.edges.push({ id: uid(), source: previous.id, target: shot.id });
    sources.forEach((source) => graph.edges.push({ id: uid(), source, target: shot.id }));
    previous = shot;
    return shot;
  });
}

// Import as new nodes: retain this project's revision and never restore another project's jobs/media.
export function mergeWorkflow(graph, document) {
  if (document?.version !== 1 || !Array.isArray(document.nodes) || !Array.isArray(document.edges))
    throw new Error('请选择有效的镜序画布备份');
  if (graph.nodes.length + document.nodes.length > 300 || graph.edges.length + document.edges.length > 1200)
    throw new Error('导入后超过画布容量');
  const groups = new Map();
  const ids = new Map(),
    nodes = [],
    edges = [];
  const offset = graph.nodes.length ? Math.max(...graph.nodes.map((n) => n.y)) + 350 : 0;
  const minY = document.nodes.length ? Math.min(...document.nodes.map((n) => n.y)) : 0;
  for (const raw of document.nodes) {
    if (
      !raw ||
      typeof raw.id !== 'string' ||
      !raw.id ||
      ids.has(raw.id) ||
      !['shot', 'material', 'note', 'video', 'footage'].includes(raw.type) ||
      !Number.isFinite(raw.x) ||
      !Number.isFinite(raw.y) ||
      !raw.data ||
      typeof raw.data !== 'object' ||
      Array.isArray(raw.data)
    )
      throw new Error('备份中有无效节点');
    const node = clone(raw);
    if (node.data.groupId != null) {
      if (
        typeof node.data.groupId !== 'string' ||
        !node.data.groupId ||
        (node.data.groupTitle != null && typeof node.data.groupTitle !== 'string')
      )
        throw new Error('备份中的素材组格式无效');
      if (!groups.has(node.data.groupId)) groups.set(node.data.groupId, uid());
      node.data.groupId = groups.get(node.data.groupId);
      node.data.groupTitle = (node.data.groupTitle || '素材组').slice(0, 80);
    }
    for (const key of ['title', 'description', 'text', 'materialName', 'materialKind', 'materialFile']) {
      if (node.data[key] != null && typeof node.data[key] !== 'string')
        throw new Error('备份中的节点文字格式无效');
    }
    if (node.type === 'material' && !['character', 'scene', 'prop', 'image'].includes(node.data.materialKind))
      throw new Error('备份中有不支持的素材类型');
    if (node.type === 'shot' && typeof node.data.manual !== 'boolean') node.data.manual = false;
    node.id = uid();
    ids.set(raw.id, node.id);
    node.y = raw.y - minY + offset;
    if (Math.abs(node.x) > 100000 || Math.abs(node.y) > 100000) throw new Error('导入节点位置超出画布范围');
    if (node.type === 'shot') {
      node.data.description ||= '';
      node.data.status = 'draft';
      delete node.data.jobId;
      delete node.data.error;
      node.data.versions = [];
      delete node.data.activeVersionId;
      if (!Number.isFinite(Number(node.data.duration)) || node.data.duration < 1 || node.data.duration > 15)
        node.data.duration = 5;
      if (creationSettingsError(node.data)) throw new Error('备份中的视频生成参数无效');
      if (
        node.data.megapixels != null &&
        (!Number.isFinite(Number(node.data.megapixels)) ||
          Number(node.data.megapixels) < 0.1 ||
          Number(node.data.megapixels) > 0.98)
      )
        node.data.megapixels = null;
    }
    if (node.type === 'footage') {
      node.data.versions = [];
      node.data.status = 'empty';
      delete node.data.activeVersionId;
    }
    if (node.type === 'video') {
      node.data.status = 'empty';
      delete node.data.url;
      delete node.data.file;
      delete node.data.jobId;
      delete node.data.error;
    }
    nodes.push(node);
  }
  for (const edge of document.edges) {
    const source = ids.get(edge.source),
      target = ids.get(edge.target);
    if (canConnect(nodes, edges, source, target)) throw new Error('备份中存在无效或循环连线');
    if (edge.usage && !['text', 'reference', 'continue'].includes(edge.usage))
      throw new Error('备份中的视频用途无效');
    edges.push({
      id: uid(),
      source,
      target,
      usage: edge.usage || 'text',
      sourceVersionId: '',
      start: edge.start ?? null,
      end: edge.end ?? null,
      tailSeconds: edge.tailSeconds ?? 3,
      motionReference: edge.motionReference !== false,
    });
  }
  for (const edge of edges) {
    if (
      edge.usage !== 'text' &&
      (!['shot', 'footage'].includes(nodes.find((n) => n.id === edge.source)?.type) ||
        nodes.find((n) => n.id === edge.target)?.type !== 'shot')
    )
      throw new Error('备份中的视频用途只能用于视频连线');
  }
  for (const node of nodes.filter((n) => n.type === 'shot')) {
    const error = videoSourcesError(
      videoInputs({ nodes, edges }, node.id),
      { video_workflow: 'ref2va' },
      nodes.map((n) => n.id)
    );
    if (error && !error.startsWith('请重新上传')) throw new Error(error);
  }
  if (
    JSON.stringify({ nodes: [...graph.nodes, ...nodes], edges: [...graph.edges, ...edges] }).length > 500000
  )
    throw new Error('备份内容过大，请减少节点或文字');
  const imported = unifyVideoNodes({ nodes, edges });
  graph.nodes.push(...imported.nodes);
  graph.edges.push(...imported.edges);
  return imported.nodes.length;
}

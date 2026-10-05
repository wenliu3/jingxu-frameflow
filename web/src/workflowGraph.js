export const NODE_WIDTH = 320;
export const NODE_HEIGHT = 282;
export const TYPE_LABELS = { material: '素材', shot: '视频', video: '视频', note: '便签' };
export const KIND_LABELS = { character: '角色', scene: '场景', prop: '道具', image: '图片', audio: '音频' };
export const clone = (value) => JSON.parse(JSON.stringify(value));
export const uid = () => globalThis.crypto.randomUUID();

export function emptyWorkflow() {
  return { version: 1, revision: 0, nodes: [], edges: [], viewport: { x: 60, y: 70, zoom: 0.8 } };
}

export function makeNode(type, x, y, data = {}) {
  return { id: uid(), type, x, y, data: { title: TYPE_LABELS[type], ...data } };
}

export function makeShot(x, y, number = 1) {
  return makeNode('shot', x, y, {
    title: `视频 ${String(number).padStart(2, '0')}`,
    description: '',
    versions: [],
    manual: false,
    duration: 5,
    ratio: 'auto',
    resolution: '720p',
    candidateCount: 1,
    generateAudio: true,
    exactDuration: true,
    seed: null,
    megapixels: null,
    status: 'draft',
  });
}

export function canConnect(nodes, edges, source, target) {
  const from = nodes.find((n) => n.id === source);
  const to = nodes.find((n) => n.id === target);
  if (!from || !to || source === target) return '不能连接到自身';
  if (!['material:shot', 'note:shot', 'shot:shot', 'shot:video'].includes(`${from.type}:${to.type}`)) {
    return '素材或便签连接视频，视频连接后续视频';
  }
  if (edges.some((e) => e.source === source && e.target === target)) return '这两个节点已经连接';
  const outgoing = new Map();
  for (const edge of edges) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge.target);
  }
  const queue = [target];
  const seen = new Set();
  while (queue.length) {
    const id = queue.pop();
    if (id === source) return '不能形成循环连线';
    if (seen.has(id)) continue;
    seen.add(id);
    queue.push(...(outgoing.get(id) || []));
  }
  return '';
}

export function arrange(nodes, edges) {
  const indegree = new Map(nodes.map((n) => [n.id, 0]));
  const levels = new Map(nodes.map((n) => [n.id, 0]));
  edges.forEach((e) => indegree.set(e.target, (indegree.get(e.target) || 0) + 1));
  const queue = nodes.filter((n) => !indegree.get(n.id)).map((n) => n.id);
  while (queue.length) {
    const id = queue.shift();
    for (const edge of edges.filter((e) => e.source === id)) {
      levels.set(edge.target, Math.max(levels.get(edge.target), levels.get(id) + 1));
      indegree.set(edge.target, indegree.get(edge.target) - 1);
      if (!indegree.get(edge.target)) queue.push(edge.target);
    }
  }
  const rows = new Map();
  return nodes.map((n) => {
    const level = levels.get(n.id);
    const row = rows.get(level) || 0;
    rows.set(level, row + 1);
    return { ...n, x: 80 + level * 365, y: 80 + row * 310 };
  });
}

export function graphBounds(nodes) {
  if (!nodes.length) return { x: 0, y: 0, width: 900, height: 600 };
  const x = Math.min(...nodes.map((n) => n.x)) - 50;
  const y = Math.min(...nodes.map((n) => n.y)) - 50;
  return {
    x,
    y,
    width: Math.max(...nodes.map((n) => n.x + NODE_WIDTH)) - x + 50,
    height: Math.max(...nodes.map((n) => n.y + NODE_HEIGHT)) - y + 50,
  };
}

export function edgePath(from, to) {
  const x1 = from.x + NODE_WIDTH,
    y1 = from.y + 46;
  const x2 = to.x,
    y2 = to.y + 46;
  const bend = Math.max(65, Math.abs(x2 - x1) * 0.45);
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
}

export function seedWorkflow(materials, records = [], multiReference = true) {
  const graph = emptyWorkflow();
  const starters = ['character', 'scene', 'prop'].flatMap((kind) =>
    materials.filter((m) => m.kind === kind).slice(0, 1)
  );
  graph.nodes = starters.map((m, i) =>
    makeNode('material', 70, 60 + i * 290, {
      title: m.name,
      materialKind: m.kind,
      materialName: m.name,
      materialFile: m.file,
    })
  );
  const shot = makeShot(440, 180);
  graph.nodes.push(shot);
  const materialNodes = graph.nodes.filter((n) => n.type === 'material');
  const connected = multiReference
    ? materialNodes
    : [materialNodes.find((n) => n.data.materialKind === 'scene') || materialNodes[0]].filter(Boolean);
  graph.edges = [...connected.map((n) => ({ id: uid(), source: n.id, target: shot.id }))];
  records.slice(0, 280).forEach((r, i) => {
    const history = makeShot(810 + (i % 2) * 365, 570 + Math.floor(i / 2) * 310);
    history.data.title = `历史视频 ${String(records.length - i).padStart(2, '0')}`;
    history.data.status = 'succeeded';
    history.data.versions = [{ id: uid(), url: r.url, file: r.name, status: 'succeeded' }];
    graph.nodes.push(history);
  });
  return graph;
}

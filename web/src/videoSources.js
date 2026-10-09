import { currentVideo, videoVersions } from './workflowVideo.js';
import { displayPrompt } from './promptMentions.js';

export const VIDEO_USES = [
  { value: 'text', label: '仅文字前情' },
  { value: 'reference', label: '视频参考' },
  { value: 'continue', label: '从结尾续拍' },
];
export function sourceVersion(node, edge) {
  return edge?.sourceVersionId
    ? videoVersions(node).find(
        (v) => v.id === edge.sourceVersionId && v.status === 'succeeded' && v.file && v.url
      )
    : currentVideo(node);
}
export function videoInputs(graph, target) {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  return graph.edges
    .filter((e) => e.target === target && ['shot', 'footage'].includes(nodes.get(e.source)?.type))
    .map((edge) => ({ edge, node: nodes.get(edge.source) }));
}
export function videoSourcesError(inputs, cfg, queuedIds = []) {
  const visual = inputs.filter(({ edge }) => edge.usage && edge.usage !== 'text');
  if (!visual.length) return '';
  if (cfg.video_backend === 'api' || cfg.video_workflow !== 'ref2va')
    return '视频参考和续拍需要切换到 ComfyUI · Ref2VA';
  if (visual.length > 3) return '最多引用三个视频来源';
  if (visual.filter(({ edge }) => edge.usage === 'continue').length > 1)
    return '请只保留一个续拍起点，其他来源可设为视频参考';
  for (const { node, edge } of visual) {
    if (!['reference', 'continue'].includes(edge.usage)) return '请选择有效的视频用途';
    if (
      !sourceVersion(node, edge)?.file &&
      !(node.type === 'shot' && queuedIds.includes(node.id) && !edge.sourceVersionId)
    )
      return node.type === 'footage'
        ? `请重新上传「${node.data.title}」的来源视频`
        : `等待「${node.data.title}」的成功视频，请先生成来源`;
    if (
      !Number.isFinite(Number(edge.tailSeconds ?? 3)) ||
      (edge.tailSeconds ?? 3) < 0.25 ||
      (edge.tailSeconds ?? 3) > 15
    )
      return '参考长度应为 0.25–15 秒';
    if (
      [edge.start, edge.end].some((n) => n != null && (!Number.isFinite(Number(n)) || n < 0)) ||
      (edge.start != null && edge.end != null && edge.start >= edge.end)
    )
      return '请设置有效的参考时间范围';
  }
  return '';
}
export function sourceSnapshot(inputs) {
  return inputs
    .filter(({ edge }) => edge.usage && edge.usage !== 'text')
    .map(({ node, edge }) => {
      const version = sourceVersion(node, edge);
      if (!version?.file) throw new Error(`等待「${node.data.title}」的成功视频`);
      return {
        source_node: node.id,
        version_id: version.id,
        file: version.file,
        usage: edge.usage,
        start: edge.start ?? null,
        end: edge.end ?? null,
        tail_seconds: Number(edge.tailSeconds ?? 3),
        motion_reference: edge.motionReference !== false,
      };
    });
}
export function sourceContext(inputs) {
  return inputs
    .map(({ node, edge }) => {
      const version = sourceVersion(node, edge);
      const description = displayPrompt(version?.description ?? node.data.description);
      return description
        ? `来源「${node.data.title}」（${VIDEO_USES.find((u) => u.value === (edge.usage || 'text'))?.label}）：${description}`
        : '';
    })
    .filter(Boolean)
    .join('\n');
}

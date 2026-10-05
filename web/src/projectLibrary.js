export function projectState(item) {
  if (item.status === 'running') return { key: 'running', label: '生成中' };
  if (item.status === 'failed') return { key: 'failed', label: '需处理' };
  if (item.video_count > 0) return { key: 'succeeded', label: '已有视频' };
  if (item.shots > 0) return { key: 'draft', label: '分镜编排' };
  if (item.material_count > 0) return { key: 'draft', label: '素材准备' };
  return { key: 'draft', label: '草稿' };
}

export function matchesProject(item, search, filter) {
  const text = `${item.title || ''} ${item.idea || ''}`.toLowerCase();
  return (
    text.includes(search.trim().toLowerCase()) && (filter === 'all' || projectState(item).key === filter)
  );
}

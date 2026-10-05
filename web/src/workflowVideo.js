import { clone, makeShot, uid } from './workflowGraph.js';

export function videoVersions(node) {
  return Array.isArray(node?.data?.versions) ? node.data.versions : [];
}

export function currentVideo(node) {
  const versions = videoVersions(node).filter((v) => v.status === 'succeeded' && v.url);
  return versions.find((v) => v.id === node.data.activeVersionId) || versions.at(-1) || null;
}

export function recordSettings(detail) {
  const duration = Number(detail?.planned_duration ?? detail?.duration ?? 5);
  return {
    description: (detail?.note || detail?.prompt || '').slice(0, detail?.note ? 1000 : 8000),
    manual: !detail?.note,
    duration: Number.isFinite(duration) ? Math.max(4, Math.min(15, duration)) : 5,
    megapixels: detail?.video_backend === 'api' ? null : (detail?.megapixels ?? null),
    ratio: detail?.ratio || 'auto',
    resolution: detail?.resolution || 'custom',
    candidateCount: [1, 2, 4].includes(detail?.candidate_count) ? detail.candidate_count : 1,
    generateAudio: detail?.generate_audio !== false,
    exactDuration: detail?.exact_duration === true,
    seed: detail?.seed ?? null,
    ...(Array.isArray(detail?.video_sources) ? { videoSources: clone(detail.video_sources) } : {}),
  };
}

export function applyVideoProgress(node, job, taskId) {
  const attempts = videoVersions(node).filter((v) => v.jobId === job.job_id);
  const results = job.results?.length
    ? job.results
    : [{ index: 0, status: job.status, video_url: job.video_url, error: job.error }];
  for (const result of results) {
    const version = attempts.find((v) => (v.candidateIndex ?? 0) === result.index);
    if (!version) continue;
    version.status = result.status;
    version.error = result.error || '';
    if (result.seed != null) version.seed = result.seed;
    for (const key of ['width', 'height', 'actual_duration', 'has_audio'])
      if (result[key] != null) version[key] = result[key];
    if (result.video_url?.startsWith(`/files/${taskId}/`)) {
      const newlyCompleted = !version.url;
      version.url = result.video_url;
      version.file = result.video_url.split('/').pop();
      // The first successful candidate is selected once; later polling never overrides a choice.
      if (newlyCompleted && !attempts.some((v) => v.id !== version.id && v.url))
        node.data.activeVersionId = version.id;
    }
  }
  if (Array.isArray(job.record?.video_sources))
    attempts.forEach((v) => {
      v.videoSources = clone(job.record.video_sources);
    });
  node.data.progress = {
    done: job.done ?? results.filter((r) => ['succeeded', 'failed'].includes(r.status)).length,
    total: job.total || attempts.length || 1,
  };
}

// Keep the storage type "shot" for compatibility with existing project files and APIs.
// A creation now owns its outputs: migrating never deletes the underlying media files.
export function unifyVideoNodes(graph) {
  const removed = new Set();
  for (const video of graph.nodes.filter((n) => n.type === 'video')) {
    const owners = graph.edges
      .filter((e) => e.target === video.id)
      .map((e) => graph.nodes.find((n) => n.id === e.source && n.type === 'shot'))
      .filter(Boolean);
    if (owners.length) {
      for (const owner of owners) {
        owner.data.versions ||= [];
        if (!owner.data.versions.some((v) => v.id === video.id || (v.file && v.file === video.data.file))) {
          if (
            video.data.url ||
            video.data.file ||
            video.data.jobId ||
            ['failed', 'interrupted', 'running', 'composing'].includes(video.data.status)
          ) {
            owner.data.versions.push({ ...clone(video.data), id: video.id });
          }
        }
      }
      removed.add(video.id);
    } else {
      const original = clone(video.data);
      const defaults = makeShot(video.x, video.y).data;
      video.type = 'shot';
      video.data = { ...defaults, ...original, versions: [{ ...original, id: uid() }] };
      if (video.data.status === 'empty') {
        video.data.status = 'draft';
        video.data.versions = [];
      }
      delete video.data.url;
      delete video.data.file;
    }
  }
  graph.nodes = graph.nodes.filter((n) => !removed.has(n.id));
  graph.edges = graph.edges.filter((e) => !removed.has(e.source) && !removed.has(e.target));
  for (const node of graph.nodes.filter((n) => n.type === 'shot')) {
    node.data.versions ||= [];
    // Earlier builds stored a ratio without applying it. Preserve their real behavior.
    if (node.data.resolution == null) {
      node.data.resolution = 'custom';
      node.data.ratio = 'auto';
    }
    // Only rename automatically generated labels; keep the author's custom titles.
    node.data.title =
      node.data.title?.replace(/^分镜 (\d+)$/, '视频 $1').replace(/^已生成分镜 (\d+)$/, '视频 $1') || '视频';
  }
  return graph;
}

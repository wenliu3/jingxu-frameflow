// A pixel budget is a resolution setting, not a promise of visual quality.
export const RESOLUTION_OPTIONS = [
  { mp: '0.4', label: '0.4MP · 快速预览' },
  { mp: '0.5', label: '0.5MP · 预览' },
  { mp: '0.7', label: '0.7MP · 均衡' },
  { mp: '0.9', label: '0.9MP · 高分辨率' },
  { mp: '0.98', label: '0.98MP · 最大预设' },
];
export const RATIO_OPTIONS = ['auto', '16:9', '4:3', '1:1', '3:4', '9:16', '21:9'];
export function creationSettings(data, cfg) {
  const api = cfg?.video_backend === 'api';
  return {
    ratio: api ? 'auto' : data.ratio || 'auto',
    resolution: api ? 'custom' : data.resolution || 'custom',
    candidate_count: data.candidateCount ?? 1,
    generate_audio: data.generateAudio !== false,
    exact_duration: !api && data.exactDuration !== false,
    ...(api || data.seed == null ? {} : { seed: Number(data.seed) }),
  };
}
export function creationSettingsError(data) {
  if (!Number.isFinite(Number(data.duration)) || data.duration < 4 || data.duration > 15)
    return '视频时长应为 4–15 秒';
  if (!RATIO_OPTIONS.includes(data.ratio || 'auto')) return '请选择有效的画幅比例';
  if (!['custom', '480p', '720p'].includes(data.resolution || 'custom')) return '请选择有效的清晰度';
  if (![1, 2, 4].includes(data.candidateCount ?? 1)) return '一次可生成 1、2 或 4 个候选';
  if (data.seed != null && (!Number.isInteger(Number(data.seed)) || data.seed < 0 || data.seed > 2147483647))
    return '随机种子应为 0–2147483647 的整数';
  return '';
}
export function resolutionLabel(value) {
  return RESOLUTION_OPTIONS.find((o) => Number(o.mp) === Number(value))?.label || `${value}MP · 自定义`;
}
export function defaultResolution(cfg) {
  const n = Number(cfg?.video_megapixels ?? 0.5);
  return Number.isFinite(n) && n >= 0.1 && n <= 0.98 ? n : 0.5;
}
export function submissionResolution(override, cfg) {
  if (cfg?.video_backend === 'api' || override === '' || override == null) return undefined;
  return Number(override);
}
export function videoSelectionError(refs, cfg, hasVideo = false) {
  if (!refs.length && !hasVideo) return '请先选择至少一张已就绪的图片素材';
  if (refs.some((r) => r.kind === 'audio' || r.kind === 'voice'))
    return '当前视频服务不接收参考音频，请只选择图片素材';
  const multi = cfg?.video_backend !== 'api' && cfg?.video_workflow === 'ref2va';
  if (refs.length > (multi ? 9 : 1))
    return multi ? 'Ref2VA 最多接受 9 张图片' : '当前视频服务只接受一张首帧图，请保留一项输入或切换 Ref2VA';
  if (refs.filter((r) => r.kind === 'scene').length > 1) return '一镜只能使用一个场景，请移除多余场景';
  return '';
}

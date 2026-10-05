export const LORAS = [
  { value: '', label: '标准模型 · 不加载 LoRA', workflow: null, steps: '20' },
  {
    value: 'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors',
    label: 'I2V · 4 步加速',
    workflow: 'i2v',
    steps: '4',
  },
  {
    value: 'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors',
    label: 'I2V · 8 步加速',
    workflow: 'i2v',
    steps: '8',
  },
  {
    value: 'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors',
    label: 'Ref2VA · 4 步加速',
    workflow: 'ref2va',
    steps: '4',
  },
];

export function settingsPatch(baseline, draft) {
  return Object.fromEntries(
    Object.entries(draft)
      .map(([key, value]) => [key, String(value ?? '')])
      .filter(([key, value]) => value !== String(baseline[key] ?? ''))
  );
}

export function workflowPreset(workflow) {
  const preset = LORAS.find((p) => p.workflow === workflow && p.steps === (workflow === 'i2v' ? '8' : '4'));
  return preset ? { video_workflow: workflow, video_lora: preset.value, video_steps: preset.steps } : {};
}

export function loraPreset(value) {
  const preset = LORAS.find((p) => p.value === value);
  if (!preset) return { video_lora: value };
  return {
    video_lora: value,
    video_steps: preset.steps,
    ...(preset.workflow ? { video_workflow: preset.workflow } : {}),
  };
}

export function settingsError(draft) {
  for (const [key, label, min, max] of [
    ['video_megapixels', '像素预算', 0.1, 0.98],
    ['video_steps', '采样步数', 1, 100],
    ['video_timeout_s', '等待时长', 30, 86400],
  ]) {
    const value = Number(draft[key]);
    if (!Number.isFinite(value) || value < min || value > max) return `${label}应在 ${min} 到 ${max} 之间`;
    if (key !== 'video_megapixels' && !Number.isInteger(value)) return `${label}请填写整数`;
  }
  const preset = LORAS.find((p) => p.value === draft.video_lora);
  if (draft.video_backend === 'comfyui' && preset?.workflow && preset.workflow !== draft.video_workflow)
    return '加速模型与视频工作流不匹配，请重新选择工作流';
  return '';
}

export const SPEECH_DEFAULTS = Object.freeze({
  model: '',
  speed: 1,
  volume: 1,
  pitch: 0,
  emotion: '',
  language: 'auto',
  effect_pitch: 0,
  intensity: 0,
  timbre: 0,
  sound_effect: '',
});
export const SPEECH_MODELS = [
  'speech-2.8-hd',
  'speech-2.8-turbo',
  'speech-2.6-hd',
  'speech-2.6-turbo',
  'speech-02-hd',
  'speech-02-turbo',
];
export const SPEECH_EMOTIONS = [
  { value: '', label: '自动情绪' },
  { value: 'happy', label: '开心' },
  { value: 'sad', label: '悲伤' },
  { value: 'angry', label: '愤怒' },
  { value: 'fearful', label: '害怕' },
  { value: 'disgusted', label: '厌恶' },
  { value: 'surprised', label: '惊讶' },
  { value: 'calm', label: '平静' },
];
export const SPEECH_EFFECTS = [
  { value: '', label: '无音效' },
  { value: 'spacious_echo', label: '空间回音' },
  { value: 'auditorium_echo', label: '礼堂广播' },
  { value: 'lofi_telephone', label: '电话失真' },
  { value: 'robotic', label: '电音' },
];
export const SPEECH_LANGUAGES = [
  ['auto', '自动识别'],
  ['Chinese', '普通话'],
  ['Chinese,Yue', '粤语'],
  ['English', '英语'],
  ['Japanese', '日语'],
  ['Korean', '韩语'],
  ['French', '法语'],
  ['German', '德语'],
  ['Spanish', '西班牙语'],
  ['Portuguese', '葡萄牙语'],
  ['Arabic', '阿拉伯语'],
  ['Russian', '俄语'],
  ['Turkish', '土耳其语'],
  ['Dutch', '荷兰语'],
  ['Ukrainian', '乌克兰语'],
  ['Vietnamese', '越南语'],
  ['Indonesian', '印尼语'],
  ['Italian', '意大利语'],
  ['Thai', '泰语'],
  ['Polish', '波兰语'],
  ['Romanian', '罗马尼亚语'],
  ['Greek', '希腊语'],
  ['Czech', '捷克语'],
  ['Finnish', '芬兰语'],
  ['Hindi', '印地语'],
  ['Bulgarian', '保加利亚语'],
  ['Danish', '丹麦语'],
  ['Hebrew', '希伯来语'],
  ['Malay', '马来语'],
  ['Persian', '波斯语'],
  ['Slovak', '斯洛伐克语'],
  ['Swedish', '瑞典语'],
  ['Croatian', '克罗地亚语'],
  ['Filipino', '菲律宾语'],
  ['Hungarian', '匈牙利语'],
  ['Norwegian', '挪威语'],
  ['Slovenian', '斯洛文尼亚语'],
  ['Catalan', '加泰罗尼亚语'],
  ['Nynorsk', '新挪威语'],
  ['Tamil', '泰米尔语'],
  ['Afrikaans', '南非荷兰语'],
].map(([value, label]) => ({ value, label }));
export const SPEECH_INTERJECTIONS = [
  { value: '(laughs)', label: '笑声' },
  { value: '(chuckle)', label: '轻笑' },
  { value: '(coughs)', label: '咳嗽' },
  { value: '(clear-throat)', label: '清嗓子' },
  { value: '(breath)', label: '正常换气' },
  { value: '(pant)', label: '喘气' },
  { value: '(sighs)', label: '叹气' },
  { value: '(gasps)', label: '倒吸气' },
  { value: '(humming)', label: '哼唱' },
  { value: '(emm)', label: '嗯' },
];
export function speechSettings(data, cfg) {
  const settings = Object.fromEntries(
    Object.entries(SPEECH_DEFAULTS).map(([key, value]) => [key, data.speechSettings?.[key] ?? value])
  );
  if (cfg?.audio_provider !== 'minimax') {
    for (const key of ['model', 'emotion', 'language', 'effect_pitch', 'intensity', 'timbre', 'sound_effect'])
      settings[key] = SPEECH_DEFAULTS[key];
    settings.volume = Math.min(settings.volume, 1);
  }
  return settings;
}
export function speechSettingsError(settings) {
  if (settings == null) return '';
  if (
    typeof settings !== 'object' ||
    Array.isArray(settings) ||
    Object.keys(settings).some((key) => !(key in SPEECH_DEFAULTS))
  )
    return '语音参数格式无效';
  for (const [key, min, max, integer] of [
    ['speed', 0.5, 2],
    ['volume', 0.01, 10],
    ['pitch', -12, 12, true],
    ['effect_pitch', -100, 100, true],
    ['intensity', -100, 100, true],
    ['timbre', -100, 100, true],
  ]) {
    if (
      settings[key] != null &&
      (typeof settings[key] !== 'number' ||
        !Number.isFinite(settings[key]) ||
        settings[key] < min ||
        settings[key] > max ||
        (integer && !Number.isInteger(settings[key])))
    )
      return '语音参数超出范围';
  }
  for (const [key, options] of [
    ['model', ['', ...SPEECH_MODELS]],
    ['emotion', SPEECH_EMOTIONS.map((item) => item.value)],
    ['language', SPEECH_LANGUAGES.map((item) => item.value)],
    ['sound_effect', SPEECH_EFFECTS.map((item) => item.value)],
  ]) {
    if (settings[key] != null && !options.includes(settings[key])) return '语音选项无效';
  }
  return '';
}

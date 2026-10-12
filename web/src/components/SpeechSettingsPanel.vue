<script setup>
import { computed } from 'vue';
import { SPEECH_DEFAULTS, SPEECH_EFFECTS, SPEECH_EMOTIONS, SPEECH_LANGUAGES } from '../speechSettings.js';
import ComposerSelect from './ComposerSelect.vue';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({ settings: Object, minimax: Boolean, busy: Boolean });
const emit = defineEmits(['change', 'checkpoint']);
const basics = computed(() => [
  { key: 'speed', label: '语速', min: 0.5, max: 2, step: 0.05, digits: 2 },
  { key: 'pitch', label: '声调', min: -12, max: 12, step: 1, digits: 0 },
  { key: 'volume', label: '音量', min: 0.1, max: props.minimax ? 10 : 1, step: 0.1, digits: 1 },
]);
const effects = [
  { key: 'effect_pitch', label: '音高', min: -100, max: 100, step: 1, digits: 0 },
  { key: 'intensity', label: '强度', min: -100, max: 100, step: 1, digits: 0 },
  { key: 'timbre', label: '音色', min: -100, max: 100, step: 1, digits: 0 },
];
const groups = computed(() => [
  { label: '基础调节', controls: basics.value },
  ...(props.minimax ? [{ label: '声音效果', controls: effects }] : []),
]);
function change(key, value) {
  if (!props.busy) emit('change', { [key]: value });
}
function numeric(control, event) {
  if (event.target.value === '') return;
  const value = Number(event.target.value);
  if (Number.isFinite(value))
    change(
      control.key,
      Number(
        (
          Math.round(Math.max(control.min, Math.min(control.max, value)) / control.step) * control.step
        ).toFixed(2)
      )
    );
}
function reset() {
  emit('checkpoint');
  // Model choice remains independent of performance and sound adjustments.
  const { model, ...controls } = SPEECH_DEFAULTS;
  emit('change', controls);
}
</script>

<template>
  <section class="speech-settings-panel" aria-label="语音调节面板">
    <header>
      <span>让声音更贴合你的表达</span
      ><button :disabled="busy" @click="reset"><WorkflowIcon name="undo" />重置调节</button>
    </header>
    <div class="speech-control-groups">
      <section v-for="group in groups" :key="group.label" class="speech-control-group">
        <h3>{{ group.label }}</h3>
        <div v-for="control in group.controls" :key="control.key" class="speech-range-row">
          <span>{{ control.label }}</span>
          <input
            type="range"
            :aria-label="control.label"
            :min="control.min"
            :max="control.max"
            :step="control.step"
            :value="settings[control.key]"
            :disabled="busy"
            @pointerdown="emit('checkpoint')"
            @keydown="emit('checkpoint')"
            @input="numeric(control, $event)"
          />
          <input
            type="number"
            :aria-label="control.label + '数值'"
            :min="control.min"
            :max="control.max"
            :step="control.step"
            :value="Number(settings[control.key]).toFixed(control.digits)"
            :disabled="busy"
            @focus="emit('checkpoint')"
            @change="numeric(control, $event)"
          />
        </div>
      </section>
    </div>
    <template v-if="minimax">
      <div class="speech-select-row">
        <div>
          <span>表达情绪</span
          ><ComposerSelect
            label="表达情绪"
            :model-value="settings.emotion"
            :options="SPEECH_EMOTIONS"
            :disabled="busy"
            @checkpoint="emit('checkpoint')"
            @update:model-value="change('emotion', $event)"
          />
        </div>
        <div>
          <span>朗读语言</span
          ><ComposerSelect
            label="朗读语言"
            :model-value="settings.language"
            :options="SPEECH_LANGUAGES"
            :disabled="busy"
            @checkpoint="emit('checkpoint')"
            @update:model-value="change('language', $event)"
          />
        </div>
      </div>
      <h3>声音环境</h3>
      <div class="speech-effects" role="group" aria-label="声音环境">
        <button
          v-for="effect in SPEECH_EFFECTS"
          :key="effect.value"
          :aria-pressed="settings.sound_effect === effect.value"
          :disabled="busy"
          @click="
            emit('checkpoint');
            change('sound_effect', effect.value);
          "
        >
          {{ effect.label }}
        </button>
      </div>
    </template>
    <p v-else class="speech-settings-note">
      Edge TTS 支持语速、相对声调和降低音量。切换到 MiniMax 可使用情绪、音效与语言调节。
    </p>
  </section>
</template>

<style scoped>
.speech-settings-panel {
  margin-top: 16px;
  padding-top: 13px;
  border-top: 1px solid var(--day-line-soft, #ffffff0e);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  color: var(--day-muted, #aaa6b6);
  font-size: 11px;
}
.speech-settings-panel header button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
  gap: 5px;
  padding: 4px 6px;
  font-size: 10px;
  cursor: pointer;
}
.speech-settings-panel header button svg {
  width: 14px;
  height: 14px;
}
.speech-control-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(235px, 1fr));
  gap: 20px;
}
h3 {
  margin: 0 0 8px;
  color: var(--day-faint, #9993a6);
  font-size: 10px;
  font-weight: 500;
}
.speech-range-row {
  display: grid;
  grid-template-columns: 34px minmax(60px, 1fr) 56px;
  align-items: center;
  gap: 10px;
  min-height: 35px;
  font-size: 11px;
}
input[type='range'] {
  width: 100%;
  height: 4px;
  margin: 0;
  padding: 0;
  accent-color: var(--day-accent, #c0aaf1);
  cursor: pointer;
  box-shadow: none;
}
input[type='number'] {
  width: 100%;
  min-width: 0;
  padding: 5px 4px;
  border: 1px solid var(--day-line-soft, #ffffff0c);
  border-radius: 7px;
  background: var(--day-inset, #ffffff06);
  color: var(--day-text, #ded8eb);
  font: inherit;
  text-align: center;
  box-shadow: none;
}
input:focus-visible {
  outline: 2px solid var(--day-accent, #c0aaf1);
  outline-offset: 3px;
}
.speech-select-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin: 13px 0;
}
.speech-select-row > div {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.speech-select-row > div > span {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--day-muted, #aaa6b6);
}
.speech-effects {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.speech-settings-panel .speech-effects button {
  padding: 7px 12px;
  border: 1px solid var(--day-line-soft, #ffffff12);
  border-radius: 8px;
  font-size: 10px;
  cursor: pointer;
}
.speech-settings-panel .speech-effects button[aria-pressed='true'] {
  background: var(--day-accent-soft, #b9a0f51c);
  border-color: var(--day-accent-line, #b9a0f555);
  color: var(--day-accent, #c0aaf1);
}
.speech-settings-note {
  margin: 10px 0 0;
  color: var(--day-muted, #aaa6b6);
  font-size: 10px;
  line-height: 1.7;
}
@media (max-width: 760px) {
  .speech-control-groups {
    gap: 14px;
  }
  .speech-select-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }
}
</style>

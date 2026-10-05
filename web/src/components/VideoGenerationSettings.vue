<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import { defaultResolution, RESOLUTION_OPTIONS, resolutionLabel, RATIO_OPTIONS } from '../videoSettings.js';
const props = defineProps({ node: Object, cfg: Object, anchor: Object });
const emit = defineEmits(['edit', 'open']);
const settingsOpen = ref(false),
  settingsTrigger = ref(null),
  panelStyle = ref({});
let panelObserver;
let outsidePress;
function outsideDown(event) {
  outsidePress =
    settingsOpen.value && event.button === 0 && !settingsTrigger.value?.parentElement.contains(event.target)
      ? { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false }
      : null;
}
function outsideMove(event) {
  if (
    outsidePress?.id === event.pointerId &&
    Math.hypot(event.clientX - outsidePress.x, event.clientY - outsidePress.y) > 5
  )
    outsidePress.moved = true;
}
function outsideUp(event) {
  if (outsidePress?.id === event.pointerId && !outsidePress.moved) close();
  outsidePress = null;
}
function outsideCancel() {
  outsidePress = null;
}
function measurePanel() {
  const trigger = settingsTrigger.value;
  const board = trigger?.closest('.flow-board');
  if (!board) return;
  const area = board.getBoundingClientRect(),
    anchor = trigger.getBoundingClientRect();
  const width = Math.min(350, area.width - 28);
  const left = Math.min(Math.max(anchor.left, area.left + 12), area.right - width - 12);
  panelStyle.value = {
    width: `${width}px`,
    left: `${left - anchor.left}px`,
    right: 'auto',
    maxHeight: `${Math.max(160, Math.min(650, anchor.top - area.top - 20))}px`,
  };
}
function close() {
  settingsOpen.value = false;
}
function toggleSettings() {
  measurePanel();
  settingsOpen.value = !settingsOpen.value;
  if (settingsOpen.value) emit('open');
}
function change(patch) {
  emit('edit', patch);
}
function choose(key, value) {
  change({ [key]: value });
}
const summary = computed(() =>
  [
    props.node.data.ratio === 'auto' || !props.node.data.ratio ? 'Auto' : props.node.data.ratio,
    props.cfg.video_backend === 'api'
      ? '模型默认'
      : props.node.data.resolution && props.node.data.resolution !== 'custom'
        ? props.node.data.resolution.toUpperCase()
        : `${props.node.data.megapixels ?? defaultResolution(props.cfg)}MP`,
    `${props.node.data.duration}s`,
    `${props.node.data.candidateCount || 1}个`,
  ].join(' · ')
);
watch(() => props.node.id, close);
watch(
  () => props.anchor,
  () => {
    if (settingsOpen.value) measurePanel();
  },
  { flush: 'post' }
);
onMounted(() => {
  const board = settingsTrigger.value?.closest('.flow-board');
  if (board) {
    panelObserver = new ResizeObserver(measurePanel);
    panelObserver.observe(board);
  }
  document.addEventListener('pointerdown', outsideDown, true);
  document.addEventListener('pointermove', outsideMove, true);
  document.addEventListener('pointerup', outsideUp, true);
  document.addEventListener('pointercancel', outsideCancel, true);
});
onBeforeUnmount(() => {
  panelObserver?.disconnect();
  document.removeEventListener('pointerdown', outsideDown, true);
  document.removeEventListener('pointermove', outsideMove, true);
  document.removeEventListener('pointerup', outsideUp, true);
  document.removeEventListener('pointercancel', outsideCancel, true);
});
defineExpose({ close });
</script>

<template>
  <div class="composer-popover-anchor resolution-anchor">
    <button
      ref="settingsTrigger"
      class="generation-settings-trigger"
      title="生成设置"
      aria-label="视频生成设置"
      :aria-expanded="settingsOpen"
      @click="toggleSettings"
    >
      <WorkflowIcon name="settings" /><span>{{ summary }}</span>
    </button>
    <section
      v-if="settingsOpen"
      class="composer-popover generation-settings"
      :style="panelStyle"
      aria-label="视频生成参数"
      @keydown.esc.stop="settingsOpen = false"
    >
      <header>
        <b>视频生成设置</b
        ><button aria-label="关闭视频生成设置" @click="settingsOpen = false">
          <WorkflowIcon name="close" />
        </button>
      </header>
      <label class="settings-label">比例<span v-if="cfg.video_backend === 'api'">API 跟随首帧</span></label>
      <div class="ratio-grid">
        <button
          v-for="ratio in RATIO_OPTIONS"
          :key="ratio"
          :disabled="cfg.video_backend === 'api'"
          :aria-pressed="(node.data.ratio || 'auto') === ratio"
          :class="{ chosen: (node.data.ratio || 'auto') === ratio }"
          @click="choose('ratio', ratio)"
        >
          <i :style="{ aspectRatio: ratio === 'auto' ? '16/10' : ratio.replace(':', '/') }"></i
          >{{ ratio === 'auto' ? 'Auto' : ratio }}
        </button>
      </div>
      <label class="settings-label">清晰度</label>
      <div class="setting-options">
        <button
          v-for="quality in ['480p', '720p', 'custom']"
          :key="quality"
          :disabled="cfg.video_backend === 'api'"
          :class="{ chosen: (node.data.resolution || 'custom') === quality }"
          :aria-pressed="(node.data.resolution || 'custom') === quality"
          @click="choose('resolution', quality)"
        >
          {{ quality === 'custom' ? '自定义' : quality.toUpperCase() }}
        </button>
      </div>
      <label v-if="cfg.video_backend !== 'api' && node.data.resolution === 'custom'" class="custom-resolution"
        >像素预算<select
          aria-label="视频分辨率"
          :value="node.data.megapixels ?? ''"
          @change="change({ megapixels: $event.target.value === '' ? null : Number($event.target.value) })"
        >
          <option value="">服务默认 · {{ defaultResolution(cfg) }}MP</option>
          <option v-for="o in RESOLUTION_OPTIONS" :key="o.mp" :value="o.mp">{{ o.label }}</option>
          <option
            v-if="
              node.data.megapixels != null &&
              !RESOLUTION_OPTIONS.some((o) => Number(o.mp) === Number(node.data.megapixels))
            "
            :value="node.data.megapixels"
          >
            {{ resolutionLabel(node.data.megapixels) }}
          </option>
        </select></label
      >
      <label class="settings-label"
        >{{ cfg.video_backend === 'api' ? '计划节奏' : '视频时长' }}<span>4–15 秒</span></label
      >
      <div class="duration-control">
        <input
          aria-label="视频时长滑块"
          type="range"
          min="4"
          max="15"
          step="0.5"
          :value="node.data.duration"
          @input="choose('duration', Number($event.target.value))"
        /><input
          aria-label="视频时长"
          type="number"
          min="4"
          max="15"
          step="0.5"
          :value="node.data.duration"
          @change="choose('duration', Number($event.target.value))"
        /><span>s</span>
      </div>
      <label class="settings-label">视频音频</label>
      <div class="setting-options">
        <button
          :class="{ chosen: node.data.generateAudio !== false }"
          :aria-pressed="node.data.generateAudio !== false"
          @click="choose('generateAudio', true)"
        >
          开启</button
        ><button
          :class="{ chosen: node.data.generateAudio === false }"
          :aria-pressed="node.data.generateAudio === false"
          @click="choose('generateAudio', false)"
        >
          关闭
        </button>
      </div>
      <label class="settings-label">生成数量<span>不同结果，逐个生成</span></label>
      <div class="setting-options">
        <button
          v-for="count in [1, 2, 4]"
          :key="count"
          :class="{ chosen: (node.data.candidateCount || 1) === count }"
          :aria-pressed="(node.data.candidateCount || 1) === count"
          @click="choose('candidateCount', count)"
        >
          {{ count }}个
        </button>
      </div>
      <details v-if="cfg.video_backend !== 'api'" class="seed-settings">
        <summary>高级设置</summary>
        <label
          >随机种子<input
            aria-label="视频随机种子"
            type="number"
            min="0"
            max="2147483647"
            step="1"
            :value="node.data.seed ?? ''"
            placeholder="留空，每次产生新结果"
            @change="
              choose('seed', $event.target.value === '' ? null : Number($event.target.value))
            " /></label
        ><label class="exact-duration"
          ><input
            type="checkbox"
            :checked="node.data.exactDuration !== false"
            @change="choose('exactDuration', $event.target.checked)"
          />
          精确控制成片时长</label
        >
      </details>
      <p class="settings-footnote">
        {{
          cfg.video_backend === 'api'
            ? '当前 API 的画幅、清晰度和实际时长由模型决定。'
            : 'H3 按帧数规则生成；精确时长会裁掉多出的尾帧，或补齐少量尾帧。清晰度是输出规格，不保证画面质量。'
        }}关闭音频后输出不含音轨。生成多个候选会执行多次生成。
      </p>
    </section>
  </div>
</template>

<style scoped>
.composer-popover-anchor {
  position: relative;
}
.composer-popover {
  position: absolute;
  bottom: calc(100% + 9px);
  left: 0;
  padding: 9px;
  border-radius: 10px;
  background: var(--day-accent-soft, #282731);
  border: 1px solid var(--day-line, #53505f);
  box-shadow: 0 12px 32px var(--day-shadow, #0006);
  z-index: 9;
}
button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: var(--day-muted, #bdbcc8);
  background: transparent;
  border: 0;
  padding: 5px 7px;
  border-radius: 6px;
  font-size: 10px;
}
button:hover:enabled {
  background: var(--day-hover, #ffffff0b);
  color: var(--day-text, #efe9ff);
}
svg {
  width: 14px;
  height: 14px;
}
select {
  padding: 5px 17px 5px 6px;
  margin: 0;
  font-size: 11px;
  color: var(--day-text, #c9c5d4);
  box-shadow: none;
}
option {
  background: var(--day-accent-soft, #24232b);
  color: var(--day-text, #ded7eb);
}
.generation-settings {
  width: 350px;
  left: auto;
  right: 0;
  padding: 16px;
  max-height: min(650px, calc(100dvh - 230px));
  overflow-y: auto;
  border-radius: 15px;
  background: var(--day-panel, #28282ded);
  backdrop-filter: blur(22px);
}
.generation-settings header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.generation-settings header b {
  font-size: 12px;
  font-weight: 500;
}
.settings-label {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--day-muted, #c0bdc9);
  margin: 14px 0 8px;
}
.settings-label span {
  font-size: 9px;
  color: var(--day-muted, #898492);
}
.ratio-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 7px;
}
.ratio-grid button {
  flex-direction: column;
  gap: 8px;
  height: 58px;
  border: 1px solid var(--day-line, #4c4b54);
  border-radius: 9px;
  font-size: 10px;
  padding: 7px 2px;
}
.ratio-grid i {
  display: block;
  height: 14px;
  width: auto;
  max-height: none;
  border: 1.5px solid var(--day-line, #aaa6b3);
  border-radius: 2px;
}
.setting-options {
  display: flex;
  gap: 7px;
}
.setting-options button {
  flex: 1;
  border: 1px solid var(--day-line, #4c4b54);
  padding: 8px;
  border-radius: 8px;
  font-size: 11px;
}
.generation-settings button.chosen {
  background: var(--day-accent-soft, #bca2ef1a);
  border-color: var(--day-accent-line, #cbb5f0);
  color: var(--day-text, #e3d5f8);
}
.duration-control {
  display: flex;
  align-items: center;
  gap: 8px;
}
.duration-control input[type='range'] {
  width: 100%;
  accent-color: var(--accent);
  padding: 0;
  border: 0;
  background: transparent;
}
.duration-control input[type='number'] {
  width: 60px;
  padding: 6px;
  background: var(--day-inset, #1c1c22);
  border: 1px solid var(--day-line, #48444f);
  border-radius: 6px;
  font-size: 11px;
  color: var(--day-text, #ded5eb);
}
.duration-control span {
  font-size: 10px;
  color: var(--day-muted, #aaa2b5);
}
.custom-resolution {
  display: block;
  font-size: 10px;
  margin-top: 10px;
  color: var(--day-muted, #aaa1b4);
}
.custom-resolution select {
  width: 100%;
  max-width: none;
  margin: 6px 0 0;
  padding: 8px;
  border: 1px solid var(--day-line, #48444f);
  background: var(--day-accent-soft, #1d1b23);
}
.seed-settings {
  margin-top: 13px;
  border-top: 1px solid var(--day-line-soft, #ffffff0b);
  padding-top: 9px;
}
.seed-settings summary {
  font-size: 10px;
  color: var(--day-muted, #a79cb8);
  cursor: pointer;
}
.seed-settings label {
  display: block;
  font-size: 10px;
  color: var(--day-muted, #aaa2b5);
  margin-top: 10px;
}
.seed-settings input[type='number'] {
  display: block;
  margin-top: 6px;
  font-size: 11px;
  color: var(--day-text, #c4b8d5);
  background: var(--day-accent-soft, #1d1b23);
  padding: 7px;
  border-radius: 6px;
  border: 1px solid var(--day-line, #48444f);
}
.seed-settings .exact-duration {
  display: flex;
  align-items: center;
  gap: 6px;
}
.exact-duration input {
  width: 12px;
  margin: 0;
}
.settings-footnote {
  color: var(--day-muted, #9890a4);
  font-size: 9px;
  line-height: 1.7;
  margin: 12px 0 0;
}
@media (max-width: 760px) {
  .resolution-anchor button {
    font-size: 9px;
    padding: 5px;
  }
  .generation-settings {
    width: min(350px, calc(100vw - 35px));
    right: -44px;
    max-height: calc(100dvh - 205px);
  }
  .generation-settings-trigger span {
    max-width: 165px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
.generation-settings header {
  position: sticky;
  top: -16px;
  z-index: 1;
  margin: -16px -16px 6px;
  padding: 12px 16px 8px;
  background: var(--day-panel, #28282d);
  border-bottom: 1px solid var(--day-line-soft, #ffffff0b);
}
</style>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { IMAGE_COUNTS, imageCount, imageMode, imageRatio } from '../imageModes.js';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({ node: Object, busy: Boolean });
const emit = defineEmits(['edit', 'checkpoint']);
const ratios = ['1:1', '9:16', '16:9', '3:4', '4:3'];
const ratio = computed(() => imageRatio(props.node.data));
const mode = computed(() => imageMode(props.node.data));
const count = computed(() => imageCount(props.node.data));
const trigger = ref(null),
  panel = ref(null),
  open = ref(false),
  position = ref({});
let observer;

function placePanel() {
  if (!open.value || !panel.value || !trigger.value) return;
  const rect = trigger.value.getBoundingClientRect();
  const width = Math.min(328, document.documentElement.clientWidth - 24);
  const height = Math.min(panel.value.scrollHeight + 2, window.innerHeight - 24);
  const above = rect.top - height - 10;
  const top = above >= 12 ? above : Math.min(rect.bottom + 10, window.innerHeight - height - 12);
  position.value = {
    width: width + 'px',
    left: Math.max(12, Math.min(rect.left, document.documentElement.clientWidth - width - 12)) + 'px',
    top: Math.max(12, top) + 'px',
    maxHeight: window.innerHeight - 24 + 'px',
  };
}
async function onToggle(event) {
  open.value = event.newState === 'open';
  if (!open.value) return;
  await nextTick();
  if (!open.value || !panel.value) return;
  placePanel();
  panel.value.querySelector('[aria-checked="true"]')?.focus();
}
function close(restoreFocus = false) {
  if (panel.value?.matches(':popover-open')) panel.value.hidePopover();
  if (restoreFocus) trigger.value?.focus();
}
function toggle() {
  if (open.value) close();
  else panel.value.showPopover();
}
function selectRatio(value) {
  if (props.busy || mode.value.sheet || value === ratio.value) return;
  emit('checkpoint');
  emit('edit', { ratio: value });
}
function moveChoice(event) {
  if (props.busy || mode.value.sheet) return;
  const direction = ['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1;
  const next = ratios[(ratios.indexOf(ratio.value) + direction + ratios.length) % ratios.length];
  selectRatio(next);
  panel.value.querySelector(`[data-ratio="${next}"]`)?.focus();
}
function selectCount(value) {
  if (props.busy || value === count.value) return;
  emit('checkpoint');
  emit('edit', { imageCount: value });
}
function moveCount(event) {
  if (props.busy) return;
  const direction = ['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1;
  const next =
    IMAGE_COUNTS[(IMAGE_COUNTS.indexOf(count.value) + direction + IMAGE_COUNTS.length) % IMAGE_COUNTS.length];
  selectCount(next);
  panel.value.querySelector(`[data-count="${next}"]`)?.focus();
}
watch(
  () => [props.node.id, props.busy, mode.value.value],
  () => close()
);
onMounted(() => {
  window.addEventListener('resize', placePanel);
  window.addEventListener('scroll', placePanel, true);
  observer = new ResizeObserver(placePanel);
  for (const element of [trigger.value, panel.value, trigger.value.closest('.flow-board')])
    if (element) observer.observe(element);
});
onBeforeUnmount(() => {
  close();
  observer?.disconnect();
  window.removeEventListener('resize', placePanel);
  window.removeEventListener('scroll', placePanel, true);
});
</script>

<template>
  <div class="image-settings">
    <button
      ref="trigger"
      class="settings-summary"
      aria-label="图片生成设置"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-controls="'image-settings-' + node.id"
      :disabled="busy"
      title="图片比例与数量"
      @click="toggle"
    >
      <WorkflowIcon name="ratio" /><span>{{ ratio }} · {{ count }} 张</span><span class="chevron">⌄</span>
    </button>
    <section
      :id="'image-settings-' + node.id"
      ref="panel"
      class="image-settings-panel"
      popover="auto"
      role="dialog"
      aria-label="图片生成设置"
      :style="position"
      @toggle="onToggle"
      @pointerdown.stop
      @wheel.stop
      @keydown.esc.stop.prevent="close(true)"
    >
      <header>
        <strong>图片比例</strong>
        <button aria-label="关闭图片生成设置" @click="close(true)"><WorkflowIcon name="close" /></button>
      </header>
      <div class="ratio-options" role="radiogroup" aria-label="图片比例">
        <button
          v-for="value in ratios"
          :key="value"
          role="radio"
          :aria-label="value"
          :aria-checked="ratio === value"
          :tabindex="ratio === value ? 0 : -1"
          :disabled="busy || (mode.sheet && value !== '16:9')"
          :data-ratio="value"
          @click="selectRatio(value)"
          @keydown.left.prevent="moveChoice"
          @keydown.right.prevent="moveChoice"
          @keydown.up.prevent="moveChoice"
          @keydown.down.prevent="moveChoice"
        >
          <span class="ratio-shape" :style="{ aspectRatio: value.replace(':', '/') }" />
          <span>{{ value }}</span>
        </button>
      </div>
      <p>
        {{
          mode.sheet
            ? '三视图固定使用 16:9。原生和场景图可自由选择比例。'
            : '按所选比例生成，实际尺寸以成品为准。'
        }}
      </p>
      <footer>
        <span>生成数量</span><strong>{{ count }} 张</strong>
      </footer>
      <div class="count-options" role="radiogroup" aria-label="生成数量">
        <button
          v-for="value in IMAGE_COUNTS"
          :key="value"
          role="radio"
          :aria-label="value + ' 张'"
          :aria-checked="count === value"
          :tabindex="count === value ? 0 : -1"
          :disabled="busy"
          :data-count="value"
          @click="selectCount(value)"
          @keydown.left.prevent="moveCount"
          @keydown.right.prevent="moveCount"
          @keydown.up.prevent="moveCount"
          @keydown.down.prevent="moveCount"
        >
          {{ value }} 张
        </button>
      </div>
      <p class="count-hint">
        {{
          mode.value === 'character'
            ? '每张都是完整人物设定图，单人参考图从中提取。'
            : '生成多个候选，完成后可切换使用。'
        }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.image-settings {
  min-width: 0;
}
.settings-summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 0 5px 10px;
  border: 0;
  border-left: 1px solid var(--day-line, #484852);
  color: var(--day-muted, #b8b7c4);
  background: transparent;
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}
.settings-summary:hover,
.settings-summary[aria-expanded='true'] {
  color: var(--day-accent, #c0aaf1);
}
.chevron {
  color: var(--day-faint, #777682);
}
.image-settings svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
.image-settings-panel {
  position: fixed;
  inset: auto;
  margin: 0;
  width: min(328px, calc(100vw - 24px));
  padding: 16px;
  border: 1px solid var(--day-line, #484852);
  border-radius: 16px;
  color: var(--day-text, #dce0ed);
  background: var(--day-panel, #26262b);
  box-shadow: 0 8px 28px var(--day-shadow, #0005);
  overflow-y: auto;
  overscroll-behavior: contain;
  font-size: 12px;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
header strong {
  font-weight: 500;
}
header button {
  display: flex;
  padding: 3px;
  border: 0;
  background: transparent;
  color: var(--day-muted, #b8b7c4);
  cursor: pointer;
}
.ratio-options {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 7px;
}
.ratio-options button {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 9px;
  min-width: 0;
  height: 70px;
  padding: 8px 3px;
  border: 1px solid var(--day-line, #484852);
  border-radius: 10px;
  color: var(--day-muted, #b8b7c4);
  background: transparent;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.ratio-options button[aria-checked='true'] {
  border-color: var(--day-accent-line, #a991d4);
  color: var(--day-accent, #c0aaf1);
  background: var(--day-accent-soft, #342a44);
}
.count-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin-top: 10px;
}
.count-options button {
  height: 34px;
  border: 1px solid var(--day-line, #484852);
  border-radius: 9px;
  color: var(--day-muted, #b8b7c4);
  background: transparent;
  font: inherit;
  cursor: pointer;
}
.count-options button[aria-checked='true'] {
  border-color: var(--day-accent-line, #a991d4);
  color: var(--day-accent, #c0aaf1);
  background: var(--day-accent-soft, #342a44);
}
.count-options button:disabled {
  opacity: 0.35;
  cursor: default;
}
.ratio-options button:disabled {
  opacity: 0.35;
  cursor: default;
}
.ratio-shape {
  display: block;
  height: 17px;
  max-width: 31px;
  border: 1.5px solid currentColor;
  border-radius: 2px;
}
p {
  margin: 12px 0 0;
  color: var(--day-muted, #aeb4c0);
  font-size: 11px;
  line-height: 1.7;
}
footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--day-line, #484852);
}
footer span {
  color: var(--day-muted, #aeb4c0);
}
footer strong {
  font-weight: 500;
}
.count-hint {
  margin-top: 6px;
}
button:focus-visible {
  outline: 2px solid var(--day-accent, #c0aaf1);
  outline-offset: 3px;
}
@media (max-width: 760px) {
  .settings-summary {
    padding-left: 7px;
    gap: 4px;
  }
}
</style>

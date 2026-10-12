<script setup>
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  label: { type: String, required: true },
  placeholder: { type: String, default: '请选择' },
  icon: { type: String, default: '' },
  disabled: Boolean,
});
const emit = defineEmits(['update:modelValue', 'checkpoint']);
const id = useId();
const trigger = ref(null),
  panel = ref(null),
  open = ref(false),
  position = ref({});
const selected = computed(() => props.options.find((option) => option.value === props.modelValue));
const enabledOptions = computed(() => props.options.filter((option) => !option.disabled));
let observer,
  search = '',
  lastTyped = 0;

function placePanel() {
  if (!panel.value?.matches(':popover-open') || !trigger.value) return;
  const rect = trigger.value.getBoundingClientRect();
  const width = Math.min(300, document.documentElement.clientWidth - 24);
  const height = Math.min(panel.value.scrollHeight + 2, 360, window.innerHeight - 24);
  const above = rect.top - height - 8;
  const top = above >= 12 ? above : Math.min(rect.bottom + 8, window.innerHeight - height - 12);
  position.value = {
    width: `${width}px`,
    left: `${Math.max(12, Math.min(rect.left, document.documentElement.clientWidth - width - 12))}px`,
    top: `${Math.max(12, top)}px`,
    maxHeight: `${Math.min(360, window.innerHeight - 24)}px`,
  };
}
function focusOption(value) {
  const option = Array.from(panel.value?.querySelectorAll('[role="option"]') || []).find(
    (element) => element.dataset.value === value
  );
  option?.focus({ preventScroll: true });
  option?.scrollIntoView({ block: 'nearest' });
}
function onToggle() {
  open.value = panel.value.matches(':popover-open');
  if (open.value) placePanel();
}
function close(restoreFocus = false) {
  if (panel.value?.matches(':popover-open')) panel.value.hidePopover();
  open.value = false;
  search = '';
  if (restoreFocus) trigger.value?.focus();
}
function show(value) {
  if (props.disabled || !enabledOptions.value.length) return;
  search = '';
  panel.value.showPopover();
  open.value = true;
  placePanel();
  focusOption(value ?? selected.value?.value ?? enabledOptions.value[0]?.value);
}
function toggle() {
  if (panel.value?.matches(':popover-open')) close(true);
  else show();
}
function choose(option) {
  if (props.disabled || option.disabled) return;
  if (option.value !== props.modelValue) {
    emit('checkpoint');
    emit('update:modelValue', option.value);
  }
  close(true);
}
function onTriggerKey(event) {
  if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();
    show(selected.value?.value ?? (event.key === 'ArrowUp' ? enabledOptions.value.at(-1)?.value : undefined));
  } else if (event.key === 'Escape' && open.value) {
    event.preventDefault();
    event.stopPropagation();
    close(true);
  }
}
function onPanelKey(event) {
  const options = enabledOptions.value;
  if (event.key === 'Escape') {
    event.preventDefault();
    close(true);
  } else if (event.key === 'Tab') {
    close();
  } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const current = options.findIndex((option) => option.value === document.activeElement?.dataset.value);
    const index =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? options.length - 1
          : (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
    focusOption(options[index]?.value);
  } else if (
    event.key.length === 1 &&
    event.key !== ' ' &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey
  ) {
    const now = Date.now();
    search = now - lastTyped > 700 ? event.key : search + event.key;
    lastTyped = now;
    const match = options.find((option) =>
      option.label.toLocaleLowerCase().startsWith(search.toLocaleLowerCase())
    );
    if (match) focusOption(match.value);
  }
}
watch(
  () => [props.disabled, props.modelValue, props.options],
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
  <div class="composer-select">
    <button
      ref="trigger"
      type="button"
      class="composer-select-trigger"
      :aria-label="label"
      :aria-controls="id"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :data-value="modelValue"
      :disabled="disabled || !enabledOptions.length"
      @click="toggle"
      @keydown="onTriggerKey"
    >
      <WorkflowIcon v-if="selected?.icon || icon" :name="selected?.icon || icon" />
      <span>{{ selected?.label || placeholder }}</span>
      <WorkflowIcon name="chevron-down" class="select-chevron" />
    </button>
    <div
      :id="id"
      ref="panel"
      class="composer-select-panel"
      popover="auto"
      :style="position"
      @toggle="onToggle"
      @pointerdown.stop
      @wheel.stop
      @keydown.stop="onPanelKey"
    >
      <div class="select-heading">{{ label }}</div>
      <div role="listbox" :aria-label="label" class="select-options">
        <button
          v-for="option in options"
          :key="option.value"
          type="button"
          role="option"
          class="select-option"
          :aria-label="option.label"
          :aria-description="option.description"
          :aria-selected="option.value === modelValue"
          :disabled="disabled || option.disabled"
          :data-value="option.value"
          tabindex="-1"
          @click="choose(option)"
        >
          <span v-if="option.icon || icon" class="option-icon"
            ><WorkflowIcon :name="option.icon || icon"
          /></span>
          <span class="option-copy">
            <span class="option-label">{{ option.label }}</span>
            <span v-if="option.description" class="option-description">{{ option.description }}</span>
          </span>
          <WorkflowIcon v-if="option.value === modelValue" name="check" class="option-check" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.composer-select {
  min-width: 0;
  max-width: 245px;
}
.composer-select .composer-select-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 7px;
  width: 100%;
  min-height: 32px;
  padding: 6px 9px;
  border: 1px solid var(--day-line-soft, #ffffff12);
  border-radius: 9px;
  background: var(--day-hover, #ffffff04);
  color: var(--day-muted, #b8b7c4);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
  transition:
    background 120ms ease,
    border-color 120ms ease,
    color 120ms ease;
}
.composer-select .composer-select-trigger:hover:enabled,
.composer-select .composer-select-trigger[aria-expanded='true'] {
  border-color: var(--day-accent-line, #a991d466);
  background: var(--day-accent-soft, #b9a0f514);
  color: var(--day-accent, #c0aaf1);
}
.composer-select-trigger > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.composer-select svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}
.select-chevron {
  margin-left: auto;
  color: var(--day-faint, #85838f);
  transition: transform 120ms ease;
}
[aria-expanded='true'] .select-chevron {
  transform: rotate(180deg);
}
.composer-select-panel {
  position: fixed;
  inset: auto;
  margin: 0;
  width: min(300px, calc(100vw - 24px));
  max-height: min(360px, calc(100dvh - 24px));
  padding: 7px;
  box-sizing: border-box;
  border: 1px solid var(--day-line, #44414e);
  border-radius: 14px;
  color: var(--day-text, #e4e1ed);
  background: var(--day-panel, #29282f);
  box-shadow:
    0 12px 36px var(--day-shadow, #0006),
    0 2px 8px var(--day-shadow, #0003);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}
.select-heading {
  padding: 5px 9px 9px;
  color: var(--day-faint, #96929f);
  font-size: 10px;
  line-height: 1.5;
}
.select-options {
  display: grid;
  gap: 3px;
}
.composer-select-panel .select-option {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  width: 100%;
  min-height: 52px;
  padding: 9px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: inherit;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.composer-select-panel .select-option:hover:enabled,
.composer-select-panel .select-option:focus-visible {
  background: var(--day-hover, #ffffff08);
  border-color: var(--day-line-soft, #ffffff10);
}
.composer-select-panel .select-option[aria-selected='true'] {
  background: var(--day-accent-soft, #b9a0f51c);
  color: var(--day-accent, #cbb6f7);
}
.option-icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border: 1px solid var(--day-line-soft, #ffffff0c);
  border-radius: 8px;
  background: var(--day-inset, #ffffff04);
  color: var(--day-muted, #b6b0c0);
}
.option-icon svg {
  width: 17px;
  height: 17px;
}
[aria-selected='true'] .option-icon {
  border-color: var(--day-accent-line, #b9a0f533);
  color: var(--day-accent, #cbb6f7);
  background: var(--day-accent-soft, #b9a0f510);
}
.option-copy {
  display: grid;
  gap: 4px;
  min-width: 0;
  line-height: 1.4;
}
.option-label {
  font-size: 12px;
  font-weight: 500;
}
.option-description {
  color: var(--day-muted, #a5a0af);
  font-size: 10px;
  font-weight: 400;
}
.option-check {
  margin-left: auto;
  width: 15px;
  height: 15px;
  flex: 0 0 auto;
  color: var(--day-accent, #cbb6f7);
}
.composer-select button:focus-visible {
  outline: 2px solid var(--day-accent, #c0aaf1);
  outline-offset: 2px;
}
.composer-select button:disabled {
  opacity: 0.4;
  cursor: default;
}
@media (max-width: 760px) {
  .composer-select {
    max-width: 180px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .composer-select-trigger,
  .select-chevron {
    transition: none;
  }
}
</style>

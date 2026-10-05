<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import { KIND_LABELS } from '../workflowGraph.js';
const props = defineProps({ material: Object, number: Number, name: String, disabled: Boolean });
const emit = defineEmits(['remove']);
const thumbnail = ref(null),
  preview = ref(null),
  visible = ref(false),
  position = ref({});
let hovering = false;
async function show(event) {
  if (event?.pointerType === 'touch' || !props.material?.url) return;
  visible.value = true;
  await nextTick();
  if (!thumbnail.value || !preview.value) return;
  const rect = thumbnail.value.getBoundingClientRect(),
    panel = preview.value.getBoundingClientRect();
  const above = rect.top - panel.height - 10;
  position.value = {
    left: `${Math.max(12, Math.min(rect.left + rect.width / 2 - panel.width / 2, window.innerWidth - panel.width - 12))}px`,
    top: `${Math.max(12, Math.min(above >= 12 ? above : rect.bottom + 10, window.innerHeight - panel.height - 12))}px`,
  };
}
function leave() {
  hovering = false;
  visible.value = false;
}
function blur(event) {
  if (!thumbnail.value?.contains(event.relatedTarget)) {
    if (!hovering) visible.value = false;
  }
}
function hide() {
  visible.value = false;
}
watch(() => [props.name, props.material?.url], hide);
window.addEventListener('resize', hide);
window.addEventListener('scroll', hide, true);
onBeforeUnmount(() => {
  window.removeEventListener('resize', hide);
  window.removeEventListener('scroll', hide, true);
});
</script>

<template>
  <div
    ref="thumbnail"
    class="reference-chip"
    :class="{ missing: !material?.ready }"
    role="group"
    :aria-label="`素材 ${number}：${name}`"
    tabindex="0"
    @pointerenter="
      hovering = $event.pointerType !== 'touch';
      show($event);
    "
    @pointerleave="leave"
    @focusin="show()"
    @focusout="blur"
    @keydown.esc="hide"
  >
    <img v-if="material?.url" :src="material.url" :alt="name" draggable="false" /><WorkflowIcon
      v-else
      name="image"
    />
    <span class="reference-number">{{ number }}</span>
    <button
      class="reference-remove"
      :disabled="disabled"
      :aria-label="`断开 ${name}`"
      @click="
        visible = false;
        emit('remove');
      "
    >
      <WorkflowIcon name="close" />
    </button>
    <Teleport to="body">
      <div
        v-if="visible"
        ref="preview"
        class="reference-hover-preview"
        role="tooltip"
        :aria-label="`${name} 放大预览`"
        :style="position"
      >
        <img :src="material.url" :alt="name" />
        <div>
          <b>图片 {{ number }} · {{ name }}</b
          ><span>{{ KIND_LABELS[material.kind] }}</span>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.reference-chip {
  position: relative;
  width: 54px;
  height: 54px;
  flex: 0 0 54px;
  border: 1px solid var(--day-line, #4c4b57);
  border-radius: 8px;
  padding: 0;
  background: var(--day-inset, #19191f);
  outline: none;
}
.reference-chip > img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 7px;
  display: block;
}
.reference-chip > svg {
  width: 22px;
  height: 22px;
  margin: 15px;
  color: var(--day-accent, #ac92d0);
}
.reference-chip:hover,
.reference-chip:focus-within {
  border-color: var(--day-accent-line, #bba4ee);
  box-shadow: 0 0 0 2px var(--day-shadow, #bba4ee22);
}
.reference-chip.missing {
  border-color: var(--day-danger-line, #b86a63);
}
.reference-number {
  position: absolute;
  top: 2px;
  left: 2px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  display: grid;
  place-items: center;
  background: #15151cdb;
  border: 1px solid #ffffff55;
  color: #fff;
  border-radius: 50%;
  font-size: 10px;
  line-height: 1;
  pointer-events: none;
}
.reference-chip .reference-remove {
  position: absolute;
  right: 2px;
  bottom: 2px;
  width: 18px;
  height: 18px;
  padding: 2px;
  display: grid;
  place-items: center;
  border: 1px solid #ffffff38;
  background: #17171ddc;
  color: #e5deee;
  border-radius: 50%;
  opacity: 0;
}
.reference-remove svg {
  width: 10px;
  height: 10px;
}
.reference-chip:hover .reference-remove,
.reference-chip:focus-within .reference-remove {
  opacity: 1;
}
.reference-hover-preview {
  position: fixed;
  z-index: 110;
  width: min(246px, calc(100vw - 24px));
  padding: 5px;
  border: 1px solid var(--day-line, #6b6377);
  border-radius: 11px;
  color: var(--day-text, #e6e0ef);
  background: var(--day-accent-soft, #25242c);
  box-shadow: 0 12px 35px var(--day-shadow, #0009);
  pointer-events: none;
}
.reference-hover-preview > img {
  display: block;
  width: 100%;
  height: 154px;
  object-fit: contain;
  background: var(--day-inset, #17171c);
  border-radius: 7px;
}
.reference-hover-preview > div {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 5px 3px;
}
.reference-hover-preview b {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  font-weight: 400;
}
.reference-hover-preview span {
  color: var(--day-muted, #a39cae);
  font-size: 10px;
}
@media (hover: none) {
  .reference-chip .reference-remove {
    opacity: 1;
  }
}
</style>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import MediaComposer from './MediaComposer.vue';
import { STATUS_LABELS } from '../workflowStudio.js';

const props = defineProps({
  node: Object,
  material: Object,
  variants: Array,
  busy: Boolean,
  cfg: Object,
});
const emit = defineEmits([
  'edit',
  'checkpoint',
  'close',
  'duplicate',
  'remove',
  'generate',
  'configure',
  'resize',
  'variant',
]);
const composer = ref(null);
const kind = computed(() =>
  props.node.type === 'note' ? '文本' : props.node.data.creationKind === 'audio' ? '语音' : '图片'
);
let sizeObserver;
onMounted(() => {
  sizeObserver = new ResizeObserver(() => emit('resize', composer.value.getBoundingClientRect().height));
  sizeObserver.observe(composer.value);
});
onBeforeUnmount(() => sizeObserver?.disconnect());
</script>

<template>
  <section
    ref="composer"
    class="creation-composer"
    :aria-label="kind + '创作输入框'"
    @pointerdown.stop
    @wheel.stop
    @keydown.esc.stop="emit('close')"
  >
    <header class="creation-heading">
      <WorkflowIcon
        :name="node.type === 'note' ? 'note' : node.data.creationKind === 'audio' ? 'audio' : 'image'"
      />
      <input
        :aria-label="kind + '名称'"
        :value="node.data.title"
        :disabled="busy"
        maxlength="80"
        @focus="emit('checkpoint')"
        @input="emit('edit', { title: $event.target.value })"
      />
      <span v-if="node.type !== 'note'" class="creation-status" :class="node.data.status">{{
        STATUS_LABELS[node.data.status]
      }}</span>
      <button :aria-label="'关闭' + kind + '输入框'" title="关闭输入框" @click="emit('close')">
        <WorkflowIcon name="close" />
      </button>
    </header>
    <div class="creation-body">
      <template v-if="node.type === 'note'">
        <textarea
          class="note-editor"
          aria-label="创作说明"
          :value="node.data.text"
          maxlength="3000"
          placeholder="剧情、对白、运镜想法…"
          @focus="emit('checkpoint')"
          @input="emit('edit', { text: $event.target.value })"
        />
      </template>
      <MediaComposer
        v-else
        :node="node"
        :material="material"
        :variants="variants"
        :cfg="cfg"
        :busy="busy"
        @edit="emit('edit', $event)"
        @checkpoint="emit('checkpoint')"
        @generate="emit('generate', $event)"
        @configure="emit('configure')"
        @variant="emit('variant', $event)"
      />
    </div>
    <footer class="creation-actions">
      <span v-if="node.type === 'note'">连接视频后，说明会加入创作上下文。</span>
      <span v-else-if="busy" role="status">收起输入框后，仍会继续生成。</span>
      <span v-else>{{ material?.ready ? '成品已保存到作品和素材库' : '成品自动保存到作品和素材库' }}</span>
      <button :disabled="busy" title="复制节点" aria-label="复制节点" @click="emit('duplicate')">
        <WorkflowIcon name="copy" />
      </button>
      <button :disabled="busy" title="移除节点" aria-label="移除节点" @click="emit('remove')">
        <WorkflowIcon name="trash" />
      </button>
    </footer>
  </section>
</template>

<style scoped>
.creation-composer {
  position: absolute;
  transform: translateX(-50%);
  min-width: 0;
  max-height: min(440px, calc(100dvh - 240px));
  display: flex;
  flex-direction: column;
  border: 1px solid var(--day-line, #484852);
  border-radius: 20px;
  padding: 14px 18px 10px;
  color: var(--day-text, #d9dae2);
  background: var(--day-panel, #26262bee);
  backdrop-filter: blur(22px);
  box-shadow:
    0 8px 28px var(--day-shadow, #0005),
    0 2px 5px var(--day-shadow, #0002);
  z-index: 5;
  cursor: default;
}
.creation-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 9px;
  flex-shrink: 0;
}
.creation-heading > svg {
  color: var(--day-accent, #c0aaf1);
}
.creation-heading input {
  flex: 1;
  min-width: 0;
  width: 180px;
  border: 0;
  background: transparent;
  color: var(--day-muted, #b8b7c4);
  font-size: 11px;
  padding: 3px 0;
  box-shadow: none;
}
.creation-status {
  font-size: 9px;
  color: var(--day-muted, #999aa6);
  white-space: nowrap;
}
.creation-status.running,
.creation-status.queued {
  color: var(--day-warn, #e3bb7d);
}
.creation-status.failed,
.creation-status.interrupted {
  color: var(--day-danger, #e4a29b);
}
.creation-composer button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--day-muted, #bdbcc8);
  border: 0;
  border-radius: 6px;
  padding: 5px 7px;
}
.creation-composer button:hover:enabled {
  background: var(--day-hover, #ffffff0b);
  color: var(--day-text, #efe9ff);
}
.creation-composer button:disabled {
  opacity: 0.4;
}
.creation-composer svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
.creation-body {
  overflow-y: auto;
  overscroll-behavior: contain;
  min-height: 0;
}
.note-editor {
  width: 100%;
  height: 150px;
  border: 0;
  background: transparent;
  color: var(--day-text, #d9dae2);
  font: inherit;
  font-size: 13px;
  line-height: 1.85;
  padding: 9px 0;
  resize: none;
  box-shadow: none;
}
.note-editor:focus {
  outline: none;
}
.note-editor::placeholder {
  color: var(--day-faint, #727987);
}
.creation-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--day-line-soft, #ffffff08);
}
.creation-actions span {
  margin-right: auto;
  color: var(--day-muted, #999aa6);
  font-size: 10px;
  line-height: 1.7;
}
@media (max-width: 760px) {
  .creation-composer {
    border-radius: 13px;
    padding: 10px 12px;
  }
  .note-editor {
    height: 115px;
    font-size: 12px;
  }
  .creation-actions span {
    font-size: 9px;
  }
}
</style>

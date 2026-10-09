<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import { currentVideo, videoVersions } from '../workflowVideo.js';
import { STATUS_LABELS } from '../workflowStudio.js';
const props = defineProps({
  node: Object,
  material: Object,
  selected: Boolean,
  connecting: Boolean,
  targetable: Boolean,
  linkHovered: Boolean,
  dimmed: Boolean,
});
const emit = defineEmits([
  'drag',
  'select',
  'focus',
  'connect',
  'finish-connect',
  'generate',
  'version',
  'hover',
]);
const result = computed(() => currentVideo(props.node));
const versions = computed(() => videoVersions(props.node).filter((v) => v.status === 'succeeded' && v.url));
const card = ref(null),
  mediaVisible = ref(false),
  mediaSize = ref(null);
const isVideo = computed(() => ['shot', 'footage'].includes(props.node.type));
const busy = computed(() => ['running', 'composing', 'queued'].includes(props.node.data.status));
const failed = computed(() => ['failed', 'interrupted'].includes(props.node.data.status));
const sizeLabel = computed(() => {
  const size =
    mediaSize.value ||
    (result.value?.width && result.value?.height ? [result.value.width, result.value.height] : null);
  return size ? `${size[0]} × ${size[1]}` : '';
});
const durationLabel = computed(() => {
  const duration = props.node.type === 'footage' ? result.value?.actual_duration : props.node.data.duration;
  return duration ? `${Number(Number(duration).toFixed(2))}s` : '';
});
let observer;
onMounted(() => {
  observer = new IntersectionObserver(
    ([entry]) => {
      if (entry.isIntersecting) {
        mediaVisible.value = true;
        observer.disconnect();
      }
    },
    { root: card.value.closest('.flow-board'), rootMargin: '120px' }
  );
  observer.observe(card.value);
});
onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <article
    ref="card"
    class="flow-node"
    :class="[node.type, { selected, targetable, dimmed, 'link-hovered': linkHovered }]"
    :style="{ left: `${node.x}px`, top: `${node.y}px` }"
    :data-node-id="node.id"
    @pointerdown.stop="emit('drag', $event, node)"
    @pointerenter="emit('hover', node.id)"
    @pointerleave="emit('hover', '')"
  >
    <button
      v-if="node.type === 'shot' || node.type === 'video'"
      class="port input-port"
      :class="{ waiting: connecting }"
      title="输入连接点：接收连线"
      aria-label="连接到此节点"
      @pointerdown.stop
      @click.stop="emit('finish-connect', node.id)"
    ></button>
    <header class="node-header" @pointerdown.stop="emit('drag', $event, node)">
      <WorkflowIcon :name="isVideo ? 'video' : node.type === 'note' ? 'note' : 'image'" />
      <strong :title="node.data.title">{{ node.data.title }}</strong>
      <span v-if="node.type === 'shot'" class="node-state" :title="STATUS_LABELS[node.data.status]">
        <i :class="node.data.status"></i>
      </span>
      <span class="node-meta"
        ><span v-if="sizeLabel">{{ sizeLabel }}</span
        ><span v-if="isVideo">{{ durationLabel }}</span></span
      >
    </header>
    <div class="node-preview" @dblclick.stop="emit('focus', node.id)">
      <video
        v-if="isVideo && result && mediaVisible"
        :key="result.id"
        :src="result.url"
        :poster="result.poster"
        controls
        preload="metadata"
        @pointerdown.stop
        @loadedmetadata="mediaSize = [$event.target.videoWidth, $event.target.videoHeight]"
      ></video>
      <img
        v-else-if="node.type === 'material' && material?.url"
        :src="material.url"
        :alt="material.name"
        draggable="false"
        loading="lazy"
        decoding="async"
        @load="mediaSize = [$event.target.naturalWidth, $event.target.naturalHeight]"
      />
      <div v-else-if="node.type === 'note'" class="note-content">
        {{ node.data.text || '写下剧情、对白或灵感…' }}
      </div>
      <div v-else class="node-placeholder"><WorkflowIcon :name="isVideo ? 'play' : 'image'" /></div>
      <div v-if="isVideo && result" class="video-drag-area" aria-hidden="true"></div>
      <span v-if="busy || failed" class="generation-badge" :class="{ failed }" :title="node.data.error">
        <i v-if="busy"></i>{{ STATUS_LABELS[node.data.status]
        }}{{ node.data.progress?.total > 1 ? ` ${node.data.progress.done}/${node.data.progress.total}` : '' }}
      </span>
      <div v-if="isVideo && result" class="node-media-actions" @pointerdown.stop>
        <select
          v-if="node.type === 'shot' && versions.length > 1"
          class="version-select"
          aria-label="视频版本"
          :value="result.id"
          @change="emit('version', node.id, $event.target.value)"
        >
          <option v-for="(v, i) in versions" :key="v.id" :value="v.id">版本 {{ i + 1 }}</option>
        </select>
        <a
          :href="result.url"
          :download="result.file || 'video.mp4'"
          title="下载当前视频"
          aria-label="下载当前视频版本"
          ><WorkflowIcon name="download"
        /></a>
      </div>
      <button
        v-if="connecting && ['shot', 'video'].includes(node.type)"
        class="node-connect-area"
        aria-label="使用此视频接收参考素材"
        @pointerdown.stop="emit('drag', $event, node)"
        @click.stop
      >
        <span><WorkflowIcon name="link" />{{ targetable ? '点击连接参考' : '无法连接此视频' }}</span>
      </button>
    </div>
    <button
      v-if="node.type !== 'video'"
      class="port output-port"
      :class="{ active: connecting }"
      title="输出连接点：点击后再点目标视频框"
      aria-label="从此节点开始连接"
      @pointerdown.stop
      @click.stop="emit('connect', node.id)"
    ></button>
  </article>
</template>

<style scoped>
.flow-node {
  position: absolute;
  width: 320px;
  height: 218px;
  background: transparent;
  color: var(--day-text, #eceef1);
  user-select: none;
}
.node-header {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 28px;
  padding: 0 2px 4px;
  cursor: grab;
  touch-action: none;
  color: var(--day-muted, #a4a5ae);
}
.node-header:active {
  cursor: grabbing;
}
.node-header > svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  opacity: 0.7;
}
.node-header strong {
  min-width: 0;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.node-meta {
  display: flex;
  gap: 8px;
  margin-left: auto;
  flex-shrink: 0;
  font-size: 9px;
  color: var(--day-muted, #83848c);
  font-variant-numeric: tabular-nums;
}
.node-preview {
  position: relative;
  cursor: grab;
  touch-action: none;
  height: 180px;
  border-radius: 9px;
  overflow: hidden;
  background: var(--day-panel, #25262b);
  border: 1px solid var(--day-line-soft, #ffffff0a);
  transition:
    outline-color 0.15s,
    box-shadow 0.15s;
}
.node-preview:active {
  cursor: grabbing;
}
.node-preview video {
  cursor: default;
  touch-action: auto;
}
.video-drag-area {
  position: absolute;
  inset: 0 0 48px;
  cursor: grab;
  touch-action: none;
}
.video-drag-area:active {
  cursor: grabbing;
}
.selected .node-preview {
  outline: 1.5px solid var(--day-canvas-selection, #a88ee0);
  outline-offset: 2px;
}
.targetable.link-hovered .node-preview {
  outline: 2px solid var(--day-canvas-edge-flow, #b9a0f5);
  outline-offset: 2px;
  box-shadow: 0 0 0 6px var(--day-canvas-selection-fill, #b9a0f510);
}
.workflow-editor .node-connect-area {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  padding: 0;
  border: 0;
  border-radius: inherit;
  background: transparent;
  cursor: crosshair;
  z-index: 4;
}
.workflow-editor .node-connect-area:hover {
  background: transparent;
  border: 0;
}
.node-connect-area span {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  font-size: 11px;
  border-radius: 8px;
  color: var(--day-text, #e6e6ee);
  background: var(--day-panel, #24252de8);
  border: 1px solid var(--day-line, #51515c);
  opacity: 0;
  transform: translateY(4px);
  transition:
    opacity 160ms,
    transform 160ms;
}
.node-connect-area:hover span,
.node-connect-area:focus-visible span {
  opacity: 1;
  transform: none;
}
.dimmed {
  opacity: 0.4;
}
.dimmed:hover {
  opacity: 0.85;
}
.node-preview img,
.node-preview video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.node-placeholder {
  display: grid;
  place-items: center;
  height: 100%;
  color: var(--day-muted, #999ba4);
}
.node-placeholder svg {
  width: 32px;
  height: 32px;
  opacity: 0.45;
}
.node-state {
  display: inline-flex;
  align-items: center;
}
.node-state i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: transparent;
}
.node-state i.succeeded {
  background: var(--day-mint, #85b9a0);
}
.node-state i.failed,
.node-state i.interrupted {
  background: var(--day-danger, #e2877e);
}
.node-state i.running,
.node-state i.composing,
.node-state i.queued {
  background: var(--day-warn, #edb86f);
  animation: node-pulse 1.4s infinite;
}
.node-media-actions {
  position: absolute;
  z-index: 1;
  right: 8px;
  top: 8px;
  display: flex;
  gap: 5px;
  align-items: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
}
.flow-node:hover .node-media-actions,
.selected .node-media-actions,
.flow-node:focus-within .node-media-actions {
  opacity: 1;
  pointer-events: auto;
}
.node-media-actions a,
.version-select {
  background: #18191dd9;
  color: #f1f1f3;
  border: 1px solid #ffffff15;
  border-radius: 5px;
  padding: 5px;
}
.node-media-actions a {
  display: flex;
}
.node-media-actions svg {
  width: 12px;
  height: 12px;
}
.version-select {
  width: auto;
  font-size: 10px;
  box-shadow: none;
}
.version-select option {
  background: #25262b;
}
.port {
  position: absolute;
  top: 112px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  padding: 0;
  z-index: 3;
  background: var(--day-panel, #34373e);
  border: 2px solid var(--day-line, #999ca6);
  touch-action: none;
  opacity: 0;
  transition: opacity 0.15s;
}
.flow-node:hover .port,
.selected .port,
.port.waiting,
.port.active,
.targetable .port,
.flow-node:focus-within .port {
  opacity: 1;
}
.input-port {
  left: -6px;
}
.output-port {
  right: -6px;
}
.port:hover,
.port.waiting,
.port.active {
  background: var(--day-accent, #b9a0f5);
  border-color: var(--day-accent-line, #e0d1ff);
  transform: scale(1.2);
}
.note .node-preview {
  background: var(--day-warn-soft, #302f26);
  border-color: var(--day-warn-line, #56513a);
}
.note-content {
  white-space: pre-wrap;
  padding: 14px;
  font-size: 12px;
  line-height: 1.8;
  color: var(--day-text, #d8d1b5);
  overflow: auto;
  height: 100%;
}
.generation-badge {
  position: absolute;
  top: 9px;
  left: 9px;
  padding: 5px 8px;
  display: flex;
  gap: 6px;
  align-items: center;
  background: var(--day-panel, #20202ee6);
  border: 1px solid var(--day-line-soft, #ffffff12);
  border-radius: 6px;
  font-size: 10px;
}
.generation-badge.failed {
  color: var(--day-danger, #e2877e);
}
.generation-badge i {
  width: 4px;
  height: 4px;
  background: var(--day-accent, #cbb0ff);
  border-radius: 50%;
  animation: node-pulse 1.4s infinite;
}
.media-kind {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 3px 6px;
  border-radius: 4px;
  background: #18191dc9;
  color: #dedfe4;
  font-size: 9px;
  pointer-events: none;
}
@keyframes node-pulse {
  50% {
    opacity: 0.3;
  }
}
@media (hover: none) {
  .port,
  .node-media-actions {
    opacity: 1;
    pointer-events: auto;
  }
}
</style>

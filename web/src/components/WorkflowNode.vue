<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import { KIND_LABELS, TYPE_LABELS } from '../workflowGraph';
import { ACTIVE_STATUSES, STATUS_LABELS } from '../workflowStudio';
import { currentVideo, videoVersions } from '../workflowVideo.js';
import { displayPrompt } from '../promptMentions.js';
const props = defineProps({
  node: Object,
  material: Object,
  selected: Boolean,
  connecting: Boolean,
  targetable: Boolean,
  dimmed: Boolean,
});
const emit = defineEmits(['drag', 'select', 'focus', 'connect', 'finish-connect', 'generate', 'version']);
const result = computed(() => currentVideo(props.node));
const versions = computed(() => videoVersions(props.node).filter((v) => v.status === 'succeeded' && v.url));
const statuses = STATUS_LABELS;
const card = ref(null);
const mediaVisible = ref(false);
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
    :class="[node.type, { selected, targetable, dimmed }]"
    :style="{ left: `${node.x}px`, top: `${node.y}px` }"
    :data-node-id="node.id"
    @pointerdown.stop="emit('select', node.id)"
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
      <WorkflowIcon
        :name="
          node.type === 'material'
            ? 'image'
            : node.type === 'shot'
              ? 'shot'
              : node.type === 'note'
                ? 'note'
                : 'video'
        "
      />
      <span>{{
        node.type === 'material' ? KIND_LABELS[node.data.materialKind] : TYPE_LABELS[node.type]
      }}</span>
      <span v-if="node.type === 'shot'" class="node-number">{{ node.data.duration || 10 }}s</span>
      <span v-else class="node-number">{{
        node.type === 'material'
          ? material?.ready
            ? '已就绪'
            : '待准备'
          : node.type === 'video'
            ? 'MP4'
            : '创作笔记'
      }}</span>
    </header>
    <div class="node-preview" title="双击预览可定位并放大节点" @dblclick.stop="emit('focus', node.id)">
      <video
        v-if="node.type === 'shot' && result && mediaVisible"
        :key="result.id"
        :src="result.url"
        controls
        preload="metadata"
        @pointerdown.stop
      ></video>
      <img
        v-else-if="node.type === 'material' && material?.url"
        :src="material.url"
        :alt="material.name"
        draggable="false"
        loading="lazy"
        decoding="async"
      />
      <div v-else-if="node.type === 'note'" class="note-content">
        {{ node.data.text || '写下剧情、对白或灵感，让它连接到你的分镜。' }}
      </div>
      <div v-else class="node-placeholder">
        <WorkflowIcon :name="node.type === 'shot' ? 'play' : 'image'" /><span>{{
          node.type === 'shot' ? '选中后，在下方描述你的画面' : '素材图片待准备'
        }}</span>
      </div>
      <span
        v-if="node.type === 'shot' && ['running', 'composing', 'queued'].includes(node.data.status)"
        class="generation-badge"
        ><i></i>{{ statuses[node.data.status]
        }}{{
          node.data.progress?.total > 1 ? ` ${node.data.progress.done}/${node.data.progress.total}` : ''
        }}</span
      >
    </div>
    <div class="node-footer">
      <strong :title="node.data.title">{{ node.data.title }}</strong>
      <p v-if="node.type === 'shot'">
        {{ displayPrompt(node.data.description) || '点击开始创作 · 描述、素材与视频都在这一镜' }}
      </p>
      <p v-else-if="node.type === 'material'">
        {{ material?.description || (material ? '可复用的视觉参考' : '素材已移除，请在右侧重新指定') }}
      </p>
      <p v-else-if="node.type === 'note'">连接到分镜，作为创作说明</p>
      <p v-else>{{ statuses[node.data.status] || '视频输出' }}</p>
      <div v-if="node.type === 'shot'" class="node-state">
        <i :class="node.data.status"></i><span>{{ statuses[node.data.status] || '待编排' }}</span>
        <select
          v-if="versions.length"
          class="version-select"
          aria-label="视频版本"
          :value="result?.id"
          @pointerdown.stop
          @change="emit('version', node.id, $event.target.value)"
        >
          <option v-for="(v, i) in versions" :key="v.id" :value="v.id">版本 {{ i + 1 }}</option>
        </select>
        <a
          v-if="result"
          :href="result.url"
          :download="result.file || 'video.mp4'"
          title="下载当前版本"
          aria-label="下载当前视频版本"
          @pointerdown.stop
          ><WorkflowIcon name="download"
        /></a>
        <button
          class="node-run"
          title="生成视频"
          :disabled="ACTIVE_STATUSES.includes(node.data.status)"
          @pointerdown.stop
          @click.stop="emit('generate', node.id)"
        >
          <WorkflowIcon name="play" />
        </button>
      </div>
    </div>
    <button
      v-if="node.type !== 'video'"
      class="port output-port"
      :class="{ active: connecting }"
      title="输出连接点：点击后再点目标输入点"
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
  height: 282px;
  border: 1px solid var(--day-line, #3b3e44);
  border-radius: 10px;
  background: var(--day-panel, #23242c);
  color: var(--day-text, #eceef1);
  box-shadow: 0 8px 24px var(--day-shadow, #0002);
  user-select: none;
  border-color: var(--day-line, #42424e);
}
.flow-node.selected {
  border-color: var(--day-accent-line, #b9a0f5);
  box-shadow:
    0 0 0 2px var(--day-shadow, #b9a0f530),
    0 12px 28px var(--day-shadow, #0004);
}
.flow-node.dimmed {
  opacity: 0.4;
}
.flow-node.dimmed:hover {
  opacity: 0.85;
}
.flow-node.targetable {
  border-color: var(--day-mint-line, #8fb8a8);
}
.node-header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 12px;
  color: var(--day-muted, #b4b5c4);
  font-size: 10px;
  cursor: grab;
  border-bottom: 1px solid var(--day-line-soft, #ffffff08);
  touch-action: none;
}
.node-header:active {
  cursor: grabbing;
}
.node-header svg {
  width: 14px;
  height: 14px;
  color: var(--day-accent, #cdb1ed);
}
.material .node-header svg {
  color: var(--day-mint, #9bbbaf);
}
.video .node-header svg {
  color: var(--day-warn, #e8b97e);
}
.note .node-header svg {
  color: var(--day-warn, #d8cd86);
}
.node-number {
  margin-left: auto;
  font-size: 10px;
  color: var(--day-muted, #858b97);
}
.node-preview {
  position: relative;
  height: 150px;
  background: var(--day-inset, #14151a);
  margin: 8px 8px 0;
  border-radius: 6px;
  overflow: hidden;
}
.node-preview img,
.node-preview video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.node-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 10px;
  font-size: 11px;
  color: var(--day-muted, #9498ab);
  background: linear-gradient(140deg, var(--day-panel, #252531), var(--day-inset, #181920));
}
.node-placeholder svg {
  width: 27px;
  height: 27px;
  opacity: 0.65;
}
.node-footer {
  padding: 9px 12px 8px;
}
.node-footer strong {
  display: block;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.node-footer p {
  margin: 3px 0 0;
  font-size: 10px;
  color: var(--day-muted, #a0a3b5);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.node-state {
  display: flex;
  gap: 5px;
  align-items: center;
  font-size: 9px;
  color: var(--day-muted, #9399ad);
  height: 23px;
}
.node-state i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--day-faint, #707888);
}
.node-state i.succeeded {
  background: var(--day-mint, #85b9a0);
}
.node-state i.failed {
  background: var(--day-danger, #e2877e);
}
.node-state i.running,
.node-state i.composing {
  background: var(--day-warn, #edb86f);
  animation: node-pulse 1.4s infinite;
}
.node-run {
  margin-left: auto;
  display: grid;
  place-items: center;
  border: 0;
  padding: 2px 7px;
  background: var(--day-hover, #ffffff0b);
  color: var(--day-accent, #b9a0f5);
  border-radius: 4px;
}
.node-run svg {
  width: 12px;
  height: 12px;
}
.port {
  position: absolute;
  top: 40px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  padding: 0;
  z-index: 3;
  background: var(--day-panel, #34373e);
  border: 2px solid var(--day-line, #999ca6);
  touch-action: none;
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
  transform: scale(1.3);
}
.note {
  background: var(--day-warn-soft, #302f26);
  border-color: var(--day-warn-line, #56513a);
}
.note .node-preview {
  background: var(--day-warn-soft, #302f26);
}
.note-content {
  white-space: pre-wrap;
  padding: 6px;
  font-size: 12px;
  line-height: 1.9;
  color: var(--day-text, #d8d1b5);
  overflow: hidden;
  height: 128px;
}
.flow-node.material {
  border-top: 2px solid var(--day-mint-line, #85baa8);
}
.flow-node.shot {
  border-top: 2px solid var(--day-accent-line, #b8a0ed);
}
.flow-node.video {
  border-top: 2px solid var(--day-warn-line, #cba87b);
}
.node-state i.queued {
  background: var(--day-warn, #d9b783);
}
@keyframes node-pulse {
  50% {
    opacity: 0.3;
  }
}

.version-select {
  width: auto;
  margin: 0 0 0 auto;
  padding: 2px 14px 2px 4px;
  background-color: var(--day-hover, #ffffff05);
  border: 0;
  box-shadow: none;
  border-radius: 4px;
  font-size: 9px;
  color: var(--day-muted, #c0b3d2);
}
.version-select option {
  background: var(--day-panel, #25242d);
}
.node-state a {
  display: inline-flex;
  color: var(--day-accent, #bca7e3);
  padding: 3px;
}
.node-state a svg {
  width: 12px;
  height: 12px;
}
.node-state:has(.version-select) .node-run {
  margin-left: 0;
}
.generation-badge {
  position: absolute;
  top: 7px;
  left: 7px;
  padding: 4px 7px;
  display: flex;
  gap: 5px;
  align-items: center;
  background: var(--day-accent-soft, #20202ee6);
  border: 1px solid var(--day-accent-line, #7b6893);
  border-radius: 5px;
  font-size: 9px;
  color: var(--day-accent, #dbc4ff);
}
.generation-badge i {
  width: 4px;
  height: 4px;
  background: var(--day-accent, #cbb0ff);
  border-radius: 50%;
  animation: node-pulse 1.4s infinite;
}
</style>

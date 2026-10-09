<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import VideoCompare from './VideoCompare.vue';
import VideoSourceChip from './VideoSourceChip.vue';
import VideoGenerationSettings from './VideoGenerationSettings.vue';
import PromptEditor from './PromptEditor.vue';
import ReferenceThumbnail from './ReferenceThumbnail.vue';
import { displayPrompt, numberMaterials } from '../promptMentions.js';
import { ACTIVE_STATUSES, STATUS_LABELS } from '../workflowStudio.js';
import { currentVideo, videoVersions } from '../workflowVideo.js';
const props = defineProps({
  node: Object,
  inputs: Array,
  cfg: Object,
  error: String,
  resolveMaterial: Function,
  materials: Array,
  anchor: Object,
  videoInputs: { type: Array, default: () => [] },
});
const emit = defineEmits([
  'edit',
  'checkpoint',
  'close',
  'generate',
  'duplicate',
  'remove',
  'disconnect',
  'references',
  'upload',
  'upload-video',
  'configure',
  'version',
  'resize',
  'source-change',
]);
const composer = ref(null);
let sizeObserver;
onMounted(() => {
  sizeObserver = new ResizeObserver(() => emit('resize', composer.value.getBoundingClientRect().height));
  sizeObserver.observe(composer.value);
});
onBeforeUnmount(() => sizeObserver?.disconnect());
const cameraOpen = ref(false),
  versionsOpen = ref(false);
const settingsPanel = ref(null);
const compareOpen = ref(false);
const busy = computed(() => ACTIVE_STATUSES.includes(props.node.data.status));
const references = computed(() => props.inputs.filter((n) => n.type === 'material'));
const connectedMaterials = computed(() =>
  numberMaterials(
    references.value.map((n) => ({
      ...(props.resolveMaterial(n) || {
        kind: n.data.materialKind,
        name: n.data.materialName || n.data.title,
        ready: false,
      }),
      nodeId: n.id,
    }))
  )
);
const versions = computed(() => videoVersions(props.node).filter((v) => v.status === 'succeeded' && v.url));
const presets = [
  { label: '缓慢推进', zh: '镜头缓慢推进。', en: 'Slow camera push-in.' },
  { label: '平稳跟拍', zh: '镜头平稳跟随主体移动。', en: 'Smooth tracking shot following the subject.' },
  { label: '环绕运镜', zh: '镜头缓慢环绕主体。', en: 'Slow orbit camera around the subject.' },
  { label: '静态特写', zh: '静态特写，浅景深。', en: 'Static close-up with shallow depth of field.' },
];
watch(
  () => props.node.id,
  () => {
    cameraOpen.value = false;
    versionsOpen.value = false;
  }
);
function change(patch) {
  emit('checkpoint');
  emit('edit', patch);
}
function camera(preset) {
  const text = props.node.data.manual ? preset.en : preset.zh;
  if (!props.node.data.description?.includes(text))
    change({
      description: [props.node.data.description, text]
        .filter(Boolean)
        .join('\n')
        .slice(0, props.node.data.manual ? 8000 : 1000),
    });
  cameraOpen.value = false;
}
</script>

<template>
  <section ref="composer" class="video-composer" aria-label="视频创作输入框" @pointerdown.stop @wheel.stop>
    <header class="composer-heading">
      <WorkflowIcon name="video" /><input
        aria-label="视频名称"
        :value="node.data.title"
        :disabled="busy"
        maxlength="80"
        @focus="emit('checkpoint')"
        @input="emit('edit', { title: $event.target.value })"
      />
      <span class="composer-status" :class="node.data.status"
        >{{ STATUS_LABELS[node.data.status]
        }}{{
          busy && node.data.progress?.total > 1
            ? ` ${node.data.progress.done}/${node.data.progress.total}`
            : ''
        }}</span
      >
      <button aria-label="关闭视频输入框" title="关闭输入框" @click="emit('close')">
        <WorkflowIcon name="close" />
      </button>
    </header>
    <fieldset :disabled="busy">
      <div class="composer-tools">
        <button @click="emit('references')">
          <WorkflowIcon name="plus" />参考素材 <small v-if="references.length">{{ references.length }}</small>
        </button>
        <button @click="emit('upload')"><WorkflowIcon name="upload" />上传图片</button>
        <button @click="emit('upload-video')"><WorkflowIcon name="video" />上传视频</button>
        <div class="composer-popover-anchor">
          <button
            :class="{ active: cameraOpen }"
            :aria-expanded="cameraOpen"
            @click="
              cameraOpen = !cameraOpen;
              settingsPanel?.close();
            "
          >
            <WorkflowIcon name="video" />运镜
          </button>
          <div v-if="cameraOpen" class="composer-popover camera-menu">
            <button v-for="preset in presets" :key="preset.label" @click="camera(preset)">
              {{ preset.label }}
            </button>
          </div>
        </div>
        <button title="复制描述和素材，创建新视频" @click="emit('duplicate')">
          <WorkflowIcon name="copy" />复制创作
        </button>
      </div>
      <div v-if="connectedMaterials.length" class="composer-references">
        <ReferenceThumbnail
          v-for="material in connectedMaterials"
          :key="`${node.id}:${material.nodeId}`"
          :material="material"
          :number="material.number"
          :name="material.name"
          :disabled="busy"
          @remove="emit('disconnect', material.nodeId)"
        />
      </div>
      <div v-if="inputs.some((n) => n.type === 'note')" class="composer-notes">
        <div v-for="n in inputs.filter((n) => n.type === 'note')" :key="n.id">
          <WorkflowIcon name="note" /><span>{{ n.data.title }}</span
          ><button :aria-label="`断开 ${n.data.title}`" @click="emit('disconnect', n.id)">
            <WorkflowIcon name="close" />
          </button>
        </div>
      </div>
      <div v-if="videoInputs.length" class="composer-video-inputs">
        <VideoSourceChip
          v-for="input in videoInputs"
          :key="input.edge.id"
          :node="input.node"
          :edge="input.edge"
          @change="emit('source-change', input.edge.id, $event)"
          @disconnect="emit('disconnect', input.node.id)"
        />
      </div>
      <PromptEditor
        :key="node.id"
        :model-value="node.data.description"
        :materials="materials"
        :connected="connectedMaterials"
        :manual="node.data.manual"
        :disabled="busy"
        @checkpoint="emit('checkpoint')"
        @update:model-value="emit('edit', { description: $event })"
        @generate="emit('generate', node.id)"
      />
      <div class="composer-footer">
        <button class="engine-choice" title="更改生成服务" @click="emit('configure')">
          <WorkflowIcon name="spark" /><span>{{ cfg.video_backend === 'api' ? '视频 API' : 'ComfyUI' }}</span>
        </button>
        <select
          aria-label="提示词模式"
          :value="node.data.manual ? 'manual' : 'ai'"
          @change="change({ manual: $event.target.value === 'manual' })"
        >
          <option value="ai">AI 编排</option>
          <option value="manual">英文直出</option>
        </select>
        <VideoGenerationSettings
          ref="settingsPanel"
          :node="node"
          :cfg="cfg"
          :anchor="anchor"
          @edit="change"
          @open="cameraOpen = false"
        />
        <span class="composer-spacer"></span>
        <span class="composer-length">{{ displayPrompt(node.data.description).length }}</span>
        <button
          class="composer-submit"
          :disabled="busy || !node.data.description?.trim()"
          :aria-label="busy ? '视频正在生成' : '生成视频'"
          :title="busy ? STATUS_LABELS[node.data.status] : '生成视频 · Ctrl + Enter'"
          @click="emit('generate', node.id)"
        >
          <WorkflowIcon :name="busy ? 'spark' : 'arrow'" />
        </button>
      </div>
    </fieldset>
    <details v-if="currentVideo(node)?.videoSources?.length" class="composer-warnings">
      <summary>当前成片引用了 {{ currentVideo(node).videoSources.length }} 个视频来源</summary>
      <p
        v-for="source in currentVideo(node).videoSources"
        :key="`${source.source_node}:${source.version_id}`"
      >
        {{ source.label || source.file }} · {{ source.usage === 'continue' ? '从结尾续拍' : '视频参考' }} ·
        {{ source.start?.toFixed(2) }}–{{ source.end?.toFixed(2) }}s · 版本 {{ source.version_id }}
      </p>
    </details>
    <details v-if="node.data.promptWarnings?.length" class="composer-warnings">
      <summary>编排提醒 · {{ node.data.promptWarnings.length }} 项</summary>
      <p v-for="warning in node.data.promptWarnings" :key="warning">{{ warning }}</p>
    </details>
    <footer v-if="node.data.error || error || versions.length" class="composer-message">
      <span :class="{ error: node.data.error }">{{
        node.data.error || error || '重新生成会保留已有版本'
      }}</span>
      <div v-if="versions.length" class="composer-popover-anchor">
        <button :aria-expanded="versionsOpen" @click="versionsOpen = !versionsOpen">
          <WorkflowIcon name="video" />{{ versions.length }} 个版本
        </button>
        <div v-if="versionsOpen" class="composer-popover version-menu" aria-label="视频历史版本">
          <b>这个视频的历史版本</b>
          <article
            v-for="(v, index) in versions"
            :key="v.id"
            :class="{ current: currentVideo(node)?.id === v.id }"
          >
            <button @click="emit('version', v.id)">
              <WorkflowIcon name="play" />版本 {{ index + 1
              }}<small
                >{{ v.duration ? `${v.duration}s` : '' }}
                {{ currentVideo(node)?.id === v.id ? '当前' : '' }}</small
              >
            </button>
            <p v-if="v.description">{{ displayPrompt(v.description) }}</p>
            <div>
              <a :href="v.url" :download="v.file || 'video.mp4'">下载</a
              ><button
                v-if="v.description"
                :disabled="busy"
                @click="
                  change({
                    description: v.description,
                    manual: !!v.manual,
                    duration: v.duration || node.data.duration,
                    megapixels: v.megapixels ?? null,
                    ratio: v.ratio || 'auto',
                    resolution: v.resolution || 'custom',
                    generateAudio: v.generateAudio !== false,
                    exactDuration: v.exactDuration !== false,
                  });
                  versionsOpen = false;
                "
              >
                使用这个版本的描述
              </button>
            </div>
          </article>
        </div>
      </div>
      <button v-if="versions.length > 1" @click="compareOpen = true">对比候选</button>
      <button
        v-if="!busy"
        title="移除视频卡，保留生成记录中的原文件"
        aria-label="移除视频卡"
        @click="emit('remove')"
      >
        <WorkflowIcon name="trash" />
      </button>
    </footer>
    <VideoCompare
      v-if="compareOpen"
      :node="node"
      @close="compareOpen = false"
      @select="emit('version', $event)"
    />
  </section>
</template>

<style scoped>
.video-composer {
  position: absolute;
  transform: translateX(-50%);
  min-width: 0;
  border: 1px solid var(--day-line, #484852);
  border-radius: 18px;
  padding: 13px 17px 11px;
  color: var(--day-text, #d9dae2);
  background: var(--day-panel, #26262bee);
  backdrop-filter: blur(22px);
  box-shadow: 0 20px 80px var(--day-shadow, #0007);
  z-index: 5;
  cursor: default;
}
.composer-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 9px;
}
.composer-heading > svg {
  color: var(--day-accent, #c0aaf1);
  width: 14px;
}
.composer-heading input {
  border: 0;
  background: transparent;
  color: var(--day-muted, #b8b7c4);
  font-size: 11px;
  padding: 3px 0;
  width: 180px;
  min-width: 0;
  box-shadow: none;
}
.composer-heading > button {
  margin-left: 4px;
}
.composer-status {
  margin-left: auto;
  font-size: 9px;
  color: var(--day-muted, #999aa6);
}
.composer-status.running,
.composer-status.composing,
.composer-status.queued {
  color: var(--day-warn, #e3bb7d);
}
.composer-status.failed {
  color: var(--day-danger, #e4a29b);
}
fieldset {
  padding: 0;
  margin: 0;
  border: 0;
  min-width: 0;
}
fieldset:disabled {
  opacity: 0.7;
}
.video-composer button {
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
.video-composer button:hover:enabled {
  background: var(--day-hover, #ffffff0b);
  color: var(--day-text, #efe9ff);
}
.video-composer svg {
  width: 14px;
  height: 14px;
}
.composer-tools {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.composer-tools > button,
.composer-tools .composer-popover-anchor > button {
  border: 1px solid var(--day-line-soft, #ffffff0a);
  border-radius: 20px;
  background: var(--day-hover, #ffffff05);
}
.composer-tools button.active {
  color: var(--day-accent, #cbb2ff);
  background: var(--day-accent-soft, #c6aaff14);
}
.composer-tools small {
  font-size: 9px;
  color: var(--day-accent, #cbb2ff);
}
.composer-video-inputs {
  max-height: 180px;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.composer-references {
  display: flex;
  gap: 7px;
  overflow-x: auto;
  padding-top: 10px;
}
.composer-notes {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  padding-top: 7px;
}
.composer-notes > div {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 7px;
  border: 1px solid var(--day-line, #45414d);
  border-radius: 6px;
  color: var(--day-text, #bdb3cf);
  font-size: 10px;
}
.composer-notes button {
  padding: 2px;
}

.composer-footer {
  display: flex;
  align-items: center;
  gap: 7px;
  padding-top: 4px;
}
.composer-footer select {
  width: auto;
  max-width: 110px;
  padding: 5px 17px 5px 6px;
  margin: 0;
  font-size: 11px;
  border: 0;
  background-color: transparent;
  color: var(--day-text, #c9c5d4);
  box-shadow: none;
}
.composer-footer option {
  background: var(--day-accent-soft, #24232b);
  color: var(--day-text, #ded7eb);
}
.engine-choice {
  color: var(--day-accent, #c8b0f6) !important;
}
.engine-choice svg {
  color: var(--day-accent, #bda3ef);
}
.composer-spacer {
  flex: 1;
}
.composer-length {
  color: var(--day-faint, #777682);
  font-size: 9px;
}
.video-composer .composer-submit {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  background: var(--day-accent, #cab4ff);
  color: var(--day-on-accent, #27212f);
  flex-shrink: 0;
}
.video-composer .composer-submit:hover:enabled {
  background: var(--day-accent, #e2d3ff);
  color: var(--day-on-accent, #27212f);
}
.composer-submit svg {
  transform: rotate(-90deg);
  width: 18px;
  height: 18px;
}
.composer-submit:disabled {
  opacity: 0.35;
}
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
.camera-menu {
  width: 118px;
  display: flex;
  flex-direction: column;
}
.camera-menu button {
  padding: 9px;
}
.composer-message {
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid var(--day-line-soft, #ffffff08);
  margin-top: 8px;
  padding-top: 6px;
  font-size: 9px;
  color: var(--day-muted, #9591a2);
}
.composer-message > span {
  flex: 1;
  overflow-wrap: anywhere;
}
.composer-message .error {
  color: var(--day-danger, #e5a29b);
}
.composer-warnings {
  margin-top: 8px;
  color: var(--day-warn, #d4bd8e);
  font-size: 11px;
  line-height: 1.6;
}
.composer-warnings summary {
  cursor: pointer;
}
.composer-warnings p {
  margin: 5px 0;
  overflow-wrap: anywhere;
}
.composer-message > button {
  flex-shrink: 0;
  font-size: 9px;
}
.version-menu {
  left: auto;
  right: 0;
  width: 290px;
  max-height: 310px;
  overflow-y: auto;
  padding: 13px;
}
.version-menu > b {
  display: block;
  font-size: 11px;
  font-weight: 500;
  padding: 0 0 10px;
}
.version-menu article {
  padding: 8px;
  background: var(--day-hover, #ffffff03);
  border: 1px solid var(--day-accent-line, #45404f);
  border-radius: 6px;
  margin-top: 5px;
}
.version-menu article.current {
  border-color: var(--day-accent-line, #b79ad5);
}
.version-menu article > button {
  width: 100%;
  justify-content: flex-start;
  padding: 3px;
}
.version-menu small {
  margin-left: auto;
  font-size: 9px;
  color: var(--day-accent, #b29aca);
}
.version-menu p {
  color: var(--day-muted, #a8a1b5);
  font-size: 10px;
  margin: 7px 0;
  line-height: 1.6;
  overflow-wrap: anywhere;
  max-height: 65px;
  overflow: auto;
}
.version-menu article > div {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.version-menu a {
  color: var(--day-accent, #cdb3f0);
  font-size: 9px;
  padding: 4px;
}
.version-menu div > button {
  font-size: 9px;
}
@media (max-width: 1100px) {
  .composer-footer {
    gap: 2px;
  }
}
@media (max-width: 760px) {
  .video-composer {
    border-radius: 13px;
    padding: 10px 12px;
  }
  .composer-heading input {
    width: 135px;
  }
  .composer-tools {
    gap: 3px;
  }
  .composer-tools button {
    font-size: 9px;
    padding: 4px 6px;
  }
  .composer-tools > button:last-child {
    display: none;
  }
  textarea {
    height: 87px;
    font-size: 12px;
  }
  .composer-footer select {
    font-size: 10px;
    max-width: 82px;
    padding-left: 3px;
  }
  .composer-footer .engine-choice span {
    display: none;
  }
  .composer-footer .engine-choice {
    padding: 5px;
  }
  .composer-length {
    display: none;
  }
  .composer-message {
    font-size: 8px;
  }
}
</style>

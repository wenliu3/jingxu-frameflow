<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { api } from '../api.js';
import { IMAGE_MODES, imageCount, imageMode } from '../imageModes.js';
import ImageGenerationSettings from './ImageGenerationSettings.vue';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({
  node: Object,
  material: Object,
  variants: { type: Array, default: () => [] },
  cfg: Object,
  busy: Boolean,
});
const emit = defineEmits(['edit', 'checkpoint', 'generate', 'configure', 'variant']);
const audio = computed(() => props.node.data.creationKind === 'audio');
const mode = computed(() => imageMode(props.node.data));
const voices = ref([]),
  voiceError = ref(''),
  voicesLoading = ref(false);
const configured = computed(() =>
  audio.value
    ? props.cfg.audio_provider !== 'minimax' || !!props.cfg.audio_api_key
    : !!props.cfg.image_api_key && (mode.value.value === 'native' || !!props.cfg.text_api_key)
);
const canGenerate = computed(
  () =>
    configured.value &&
    props.node.data.prompt?.trim() &&
    (!audio.value || voices.value.some((v) => v.id === props.node.data.voiceId)) &&
    !props.busy
);
function generate() {
  if (canGenerate.value) emit('generate', props.node.id);
}
let voiceRequest = 0;
onBeforeUnmount(() => {
  voiceRequest++;
});
watch(
  () => [props.node.id, audio.value, props.cfg.audio_provider],
  async () => {
    const serial = ++voiceRequest;
    voices.value = [];
    voiceError.value = '';
    voicesLoading.value = false;
    if (!audio.value) return;
    voicesLoading.value = true;
    try {
      const result = await api.mediaVoices();
      if (serial !== voiceRequest) return;
      voices.value = result;
      if (!props.node.data.voiceId && !props.busy && result[0]) emit('edit', { voiceId: result[0].id });
    } catch (err) {
      if (serial === voiceRequest) voiceError.value = err.message;
    } finally {
      if (serial === voiceRequest) voicesLoading.value = false;
    }
  },
  { immediate: true }
);
</script>

<template>
  <section
    class="media-composer"
    :aria-label="audio ? '语音生成' : '图片生成'"
    @keydown.ctrl.enter.prevent="generate"
    @keydown.meta.enter.prevent="generate"
  >
    <div v-if="material?.url" class="media-result">
      <audio v-if="audio" :src="material.url" controls preload="metadata" aria-label="试听生成的语音" />
      <a :href="material.url" :download="material.file"
        ><WorkflowIcon name="download" />下载{{ audio ? '语音' : '图片' }}</a
      >
    </div>
    <div v-if="!audio && variants.length > 1" class="image-variants" role="group" aria-label="生成的候选图片">
      <button
        v-for="(variant, index) in variants"
        :key="variant.file"
        :aria-label="'使用第 ' + (index + 1) + ' 张图片'"
        :aria-pressed="node.data.materialFile === variant.file"
        :disabled="busy"
        @click="emit('variant', index)"
      >
        <img :src="variant.url" alt="" loading="lazy" /><span>{{ index + 1 }}</span>
      </button>
      <span>选择要使用的图片</span>
    </div>
    <fieldset :disabled="busy">
      <label class="prompt-field">
        <textarea
          :aria-label="audio ? '台词 / 旁白' : '画面描述'"
          :value="node.data.prompt"
          maxlength="3000"
          rows="5"
          :placeholder="audio ? '输入想要朗读的内容，生成配音或旁白…' : mode.placeholder"
          @focus="emit('checkpoint')"
          @input="emit('edit', { prompt: $event.target.value })"
        />
      </label>
      <div class="media-controls">
        <span v-if="audio" class="media-kind"><WorkflowIcon name="audio" />语音生成</span>
        <button
          v-else
          class="model-choice"
          aria-label="设置图片模型"
          :title="cfg.image_model || '在服务设置中选择图片模型'"
          @click="emit('configure')"
        >
          <WorkflowIcon name="spark" /><span>{{ cfg.image_model || '图片模型' }}</span
          ><span class="model-chevron">⌄</span>
        </button>
        <label v-if="audio" class="setting-field"
          >音色
          <select
            aria-label="音色"
            :value="node.data.voiceId || ''"
            :disabled="voicesLoading || !!voiceError"
            @focus="emit('checkpoint')"
            @change="emit('edit', { voiceId: $event.target.value })"
          >
            <option value="" disabled>{{ voicesLoading ? '正在读取音色…' : '请选择音色' }}</option>
            <option v-for="voice in voices" :key="voice.id" :value="voice.id">
              {{ voice.cn }} · {{ voice.locale
              }}{{ voice.gender ? ` · ${voice.gender === 'female' ? '女声' : '男声'}` : '' }}
            </option>
          </select>
        </label>
        <ImageGenerationSettings
          v-else
          :node="node"
          :busy="busy"
          @checkpoint="emit('checkpoint')"
          @edit="emit('edit', $event)"
        />
        <label v-if="!audio" class="mode-choice"
          ><WorkflowIcon name="image" />
          <select
            aria-label="图片类型"
            :value="mode.value"
            @focus="emit('checkpoint')"
            @change="emit('edit', { imageMode: $event.target.value })"
          >
            <option v-for="item in IMAGE_MODES" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <span class="media-length">{{ node.data.prompt?.length || 0 }}</span>
        <button
          class="media-generate"
          :disabled="!canGenerate"
          :aria-label="busy ? '生成中…' : material?.ready ? '重新生成' : audio ? '生成语音' : '生成图片'"
          :title="busy ? '生成中…' : (audio ? '生成语音' : '生成图片') + ' · Ctrl + Enter'"
          @click="generate"
        >
          <WorkflowIcon :name="busy ? 'spark' : 'arrow'" />
        </button>
      </div>
    </fieldset>
    <p v-if="!audio && mode.value !== 'native'" class="media-hint image-mode-hint">{{ mode.hint }}</p>
    <p v-if="voiceError || node.data.error" class="media-error" role="alert">
      {{ node.data.error || voiceError }}
    </p>
    <p v-if="!configured" class="media-hint">
      {{
        audio
          ? '请先连接语音服务。'
          : mode.value === 'native'
            ? '请先连接图片模型。'
            : '请先连接图片和文本模型。'
      }}<button @click="emit('configure')">服务设置</button>
    </p>
    <p v-if="busy" class="media-hint" role="status">
      {{
        audio ? '正在生成语音。' : `正在生成 ${imageCount(node.data)} 张图片。`
      }}可以收起面板继续创作，完成后会自动回到这个节点。
    </p>
  </section>
</template>

<style scoped>
.image-variants {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0 12px;
  flex-wrap: wrap;
}
.image-variants button {
  position: relative;
  width: 64px;
  height: 48px;
  padding: 2px;
  border: 1px solid var(--day-line, #484852);
  border-radius: 7px;
  background: transparent;
  cursor: pointer;
}
.image-variants button[aria-pressed='true'] {
  border: 2px solid var(--day-accent, #c0aaf1);
}
.image-variants button:focus-visible {
  outline: 2px solid var(--day-accent, #c0aaf1);
  outline-offset: 2px;
}
.image-variants button:disabled {
  cursor: default;
  opacity: 0.5;
}
.image-variants img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  border-radius: 4px;
}
.image-variants button span {
  position: absolute;
  right: 2px;
  bottom: 2px;
  border-radius: 3px;
  padding: 0 3px;
  color: #fff;
  background: #0009;
  font-size: 10px;
}
.image-variants > span {
  color: var(--day-muted, #aeb4c0);
  font-size: 11px;
}
fieldset {
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
label {
  display: block;
  font-size: 12px;
  color: var(--day-muted, #aeb4c0);
  margin: 0;
}
textarea,
select {
  padding: 7px 9px;
  background: transparent;
  border: 0;
  color: var(--day-text, #dce0ed);
  font: inherit;
  font-size: 12px;
  border-radius: 6px;
}
textarea {
  width: 100%;
  height: 145px;
  padding: 16px 2px;
  font-size: 13px;
  line-height: 1.85;
  resize: none;
  box-shadow: none;
}
textarea:focus {
  outline: none;
}
textarea::placeholder {
  color: var(--day-faint, #727987);
}
.media-controls {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.media-kind {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--day-accent, #c0aaf1);
  font-size: 11px;
}
.setting-field {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  font-size: 10px;
  white-space: nowrap;
}
.setting-field select {
  max-width: 245px;
  min-width: 0;
  padding: 5px;
  background: transparent;
}
.mode-choice {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 0;
  border: 0;
  background: transparent;
  color: var(--day-muted, #b8b7c4);
  font-size: 11px;
  white-space: nowrap;
}
.mode-choice select {
  padding: 0;
  font-size: 11px;
  cursor: pointer;
  max-width: 125px;
}
.mode-choice option {
  background: var(--day-panel, #26262b);
}
.model-choice {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: 260px;
  padding: 5px 0;
  border: 0;
  background: transparent;
  color: var(--day-text, #dce0ed);
  cursor: pointer;
  font: inherit;
  font-size: 11px;
}
.model-choice > span:first-of-type {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.model-choice > svg {
  color: var(--day-accent, #c0aaf1);
}
.model-chevron {
  color: var(--day-faint, #777682);
}
.model-choice + .setting-field {
  border-left: 1px solid var(--day-line, #484852);
  padding-left: 10px;
}
.mode-choice :focus-visible,
.model-choice:focus-visible {
  outline: 2px solid var(--day-accent, #c0aaf1);
  outline-offset: 3px;
}
.setting-field option {
  background: var(--day-panel, #26262b);
}
.media-length {
  margin-left: auto;
  color: var(--day-faint, #777682);
  font-size: 9px;
}
.media-composer svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
.media-hint {
  font-size: 11px;
  line-height: 1.8;
  color: var(--day-muted, #9a9eb0);
}
.media-hint button {
  font: inherit;
  color: var(--day-accent, #c5acf4);
  border: 0;
  background: transparent;
  cursor: pointer;
}
.media-error {
  color: var(--day-danger, #dd9991);
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.media-result {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 4px 0 7px;
}
.media-result audio {
  width: min(360px, 70%);
  height: 36px;
}
.media-result a {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 11px;
  color: var(--day-accent, #c5acf4);
}
.media-composer .media-generate {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 5px;
  border: 0;
  border-radius: 12px;
  background: var(--day-accent, #cab4ff);
  color: var(--day-on-accent, #27212f);
  cursor: pointer;
}
.media-generate:disabled {
  opacity: 0.35;
  cursor: default;
}
.media-generate svg {
  transform: rotate(-90deg);
  width: 18px;
  height: 18px;
}
@media (max-width: 760px) {
  textarea {
    height: 110px;
    font-size: 12px;
  }
  .media-controls {
    gap: 6px;
  }
  .setting-field select {
    max-width: 150px;
  }
  .media-kind {
    font-size: 10px;
  }
  .model-choice {
    max-width: 150px;
  }
  .media-length {
    display: none;
  }
  .media-generate {
    margin-left: auto;
  }
}
</style>

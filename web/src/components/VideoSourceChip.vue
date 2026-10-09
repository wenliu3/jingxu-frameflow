<script setup>
import { computed } from 'vue';
import { videoVersions } from '../workflowVideo.js';
import { sourceVersion, VIDEO_USES } from '../videoSources.js';
const props = defineProps({ node: Object, edge: Object });
const emit = defineEmits(['change', 'disconnect']);
const versions = computed(() =>
  videoVersions(props.node).filter((v) => v.status === 'succeeded' && v.url && v.file)
);
const version = computed(() => sourceVersion(props.node, props.edge));
function range(key, value) {
  emit('change', { [key]: value === '' ? null : Number(value) });
}
</script>
<template>
  <article class="video-source" :aria-label="`视频来源 ${node.data.title}`">
    <div class="source-heading">
      <video
        v-if="version?.url"
        :key="version.id"
        :src="version.url"
        :poster="version.poster"
        preload="metadata"
        controls
        muted
      /><span v-else class="waiting">等待来源视频完成</span><b>{{ node.data.title }}</b
      ><button :aria-label="`断开 ${node.data.title}`" @click="emit('disconnect')">×</button>
    </div>
    <div class="source-options">
      <select
        :aria-label="`${node.data.title} 来源用途`"
        :value="edge.usage || 'text'"
        @change="emit('change', { usage: $event.target.value, start: null, end: null })"
      >
        <option v-for="use in VIDEO_USES" :key="use.value" :value="use.value">{{ use.label }}</option>
      </select>
      <select
        v-if="node.type !== 'footage' && versions.length > 1"
        :aria-label="`${node.data.title} 来源版本`"
        :value="edge.sourceVersionId || ''"
        @change="emit('change', { sourceVersionId: $event.target.value, start: null, end: null })"
      >
        <option value="">生成时选用当前版本</option>
        <option v-for="(v, index) in versions" :key="v.id" :value="v.id">
          固定版本 {{ index + 1 }} · {{ v.actual_duration || v.duration }}s
        </option>
      </select>
    </div>
    <details v-if="edge.usage && edge.usage !== 'text'" class="source-advanced">
      <summary>{{ edge.usage === 'continue' ? '续拍设置' : '参考范围' }}</summary>
      <div class="range-options">
        <template v-if="edge.usage === 'reference'"
          ><label
            >开始
            <input
              type="number"
              min="0"
              step="0.1"
              :value="edge.start"
              placeholder="自动"
              :aria-label="`${node.data.title} 参考开始秒`"
              @change="range('start', $event.target.value)" /></label
          ><label
            >结束
            <input
              type="number"
              min="0"
              step="0.1"
              :value="edge.end"
              placeholder="片尾"
              :aria-label="`${node.data.title} 参考结束秒`"
              @change="range('end', $event.target.value)" /></label
        ></template>
        <label v-else
          ><input
            type="checkbox"
            :checked="edge.motionReference !== false"
            @change="emit('change', { motionReference: $event.target.checked })"
          />同时参考结尾动作</label
        >
        <label
          >参考长度
          <input
            type="number"
            min="0.25"
            max="15"
            step="0.25"
            :value="edge.tailSeconds ?? 3"
            :aria-label="`${node.data.title} 参考长度秒`"
            @change="emit('change', { tailSeconds: Number($event.target.value) })"
          />秒</label
        >
      </div>
      <small
        >{{
          (edge.usage || 'text') === 'text'
            ? '仅提供文字前情'
            : edge.usage === 'continue'
              ? '尾帧作为新片开头 · 时长表示新增内容'
              : '借鉴画面与动作 · 不锁定新片开头'
        }}{{ edge.usage !== 'text' && edge.usage ? ' · 不复用来源音轨' : '' }}</small
      >
    </details>
  </article>
</template>
<style scoped>
.video-source {
  padding: 8px;
  background: var(--day-inset, #1d1d23);
  border: 1px solid var(--day-accent-line, #44404f);
  border-radius: 10px;
  margin-top: 9px;
}
.source-heading,
.source-options,
.range-options {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
}
.source-heading video {
  width: 80px;
  height: 46px;
  border-radius: 6px;
  object-fit: cover;
  background: #121216;
}
.source-heading b {
  font-size: 11px;
}
.source-heading button {
  margin-left: auto;
  border: 0;
  background: none;
  color: var(--day-accent, #b5a6cb);
  cursor: pointer;
}
.source-options {
  margin: 7px 0;
}
.source-advanced summary {
  cursor: pointer;
  font-size: 10px;
  color: var(--day-muted, #a4a5b4);
}
.source-advanced[open] .range-options {
  margin: 9px 0;
}
.video-source select {
  width: auto;
  max-width: 100%;
  min-width: 110px;
}
.range-options label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: auto;
  margin: 0;
}
.range-options input[type='checkbox'] {
  width: auto;
  margin: 0;
}
.video-source select,
.video-source input[type='number'] {
  color: var(--day-text, #d7cbe8);
  background: var(--day-accent-soft, #26242e);
  border: 1px solid var(--day-accent-line, #4b4359);
  border-radius: 5px;
  padding: 5px;
  font-size: 10px;
}
.range-options {
  font-size: 10px;
  color: var(--day-muted, #aaa3b5);
}
.range-options input[type='number'] {
  width: 57px;
}
.video-source small {
  display: block;
  color: var(--day-muted, #8f879b);
  font-size: 9px;
  margin-top: 7px;
}
.waiting {
  font-size: 10px;
  color: var(--day-accent, #b4a1cc);
}
</style>

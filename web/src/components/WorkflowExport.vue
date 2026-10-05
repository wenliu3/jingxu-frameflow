<script setup>
import { computed, ref, watch } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({
  items: { type: Array, default: () => [] },
  shotCount: { type: Number, default: 0 },
});
const emit = defineEmits(['close', 'export']);
const choices = ref({});
const previewId = ref('');
const previewError = ref(false);
const measuredDurations = ref({});
const aspect = ref('16:9');
const formats = [
  { value: '16:9', label: '横屏', size: '1280 × 720', shape: 'landscape' },
  { value: '9:16', label: '竖屏', size: '720 × 1280', shape: 'portrait' },
  { value: '1:1', label: '方形', size: '720 × 720', shape: 'square' },
];
const format = computed(() => formats.find((item) => item.value === aspect.value));
const skippedCount = computed(() => Math.max(0, props.shotCount - props.items.length));
const defaultFile = (item) =>
  item.versions.find((version) => version.file === item.currentFile)?.file || item.versions.at(-1)?.file;

watch(
  () => props.items,
  (items) => {
    choices.value = Object.fromEntries(
      items.map((item) => [
        item.id,
        item.versions.some((version) => version.file === choices.value[item.id])
          ? choices.value[item.id]
          : defaultFile(item),
      ])
    );
    if (!items.some((item) => item.id === previewId.value)) previewId.value = items[0]?.id || '';
  },
  { immediate: true }
);

const selectedVersion = (item) => item.versions.find((version) => version.file === choices.value[item.id]);
const positiveDuration = (value) => (Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : 0);
const actualDuration = (version) =>
  positiveDuration(version?.actual_duration) || positiveDuration(measuredDurations.value[version?.url]);
const clipDuration = (item, version = selectedVersion(item)) =>
  actualDuration(version) || positiveDuration(version?.duration) || positiveDuration(item.duration);
const totalDuration = computed(() => props.items.reduce((sum, item) => sum + clipDuration(item), 0));
const durationEstimated = computed(() => props.items.some((item) => !actualDuration(selectedVersion(item))));
const durationUnknown = computed(() => props.items.some((item) => !clipDuration(item)));
const changedChoices = computed(() =>
  props.items.some((item) => choices.value[item.id] !== defaultFile(item))
);
const previewItem = computed(() => props.items.find((item) => item.id === previewId.value));
const previewVersion = computed(() => (previewItem.value ? selectedVersion(previewItem.value) : null));
watch(
  () => previewVersion.value?.url,
  () => (previewError.value = false)
);

function secondsLabel(value) {
  return value ? `${Number(value.toFixed(1))} 秒` : '时长待确认';
}
function totalLabel(value) {
  if (!value || durationUnknown.value) return '时长待确认';
  const rounded = Math.round(value * 10) / 10;
  const minutes = Math.floor(rounded / 60),
    seconds = Number((rounded % 60).toFixed(1));
  return `${durationEstimated.value ? '约 ' : ''}${minutes ? `${minutes} 分 ` : ''}${seconds} 秒`;
}
function versionLabel(item, version, index) {
  return `版本 ${index + 1} · ${secondsLabel(clipDuration(item, version))}${version.file === item.currentFile ? ' · 画布选用' : ''}`;
}
function resetChoices() {
  choices.value = Object.fromEntries(props.items.map((item) => [item.id, defaultFile(item)]));
}
function rememberDuration(event, url) {
  const duration = positiveDuration(event.target.duration);
  if (duration) measuredDurations.value[url] = duration;
}
function startExport() {
  const files = props.items.map((item) => selectedVersion(item)?.file);
  if (files.length && files.every(Boolean)) emit('export', { files, aspect: aspect.value });
}
</script>

<template>
  <div class="export-workspace">
    <div class="export-overview">
      <p>按镜头序列拼接，默认使用画布选中的版本。</p>
      <div class="export-summary" aria-live="polite">
        <span
          ><b>{{ items.length }}</b> 个镜头</span
        >
        <span class="summary-divider" aria-hidden="true"></span>
        <span>{{ totalLabel(totalDuration) }}</span>
      </div>
    </div>
    <p v-if="skippedCount" class="export-warning" role="status">
      <WorkflowIcon name="help" />还有 {{ skippedCount }} 镜没有可用视频，本次将跳过。可取消导出，补齐后再试。
    </p>

    <div class="export-body">
      <section class="export-sequence" aria-labelledby="export-sequence-title">
        <div class="section-heading">
          <div>
            <h3 id="export-sequence-title">成片镜头</h3>
            <p>点击镜头预览 · 多个版本时可切换</p>
          </div>
          <button v-if="changedChoices" class="reset-choices" @click="resetChoices">恢复画布选用</button>
        </div>
        <div class="export-list">
          <article
            v-for="(item, index) in items"
            :key="item.id"
            class="export-shot"
            :class="{ 'is-previewing': previewId === item.id }"
          >
            <button
              class="shot-preview"
              :aria-label="`预览 ${item.title}`"
              :aria-pressed="previewId === item.id"
              @click="previewId = item.id"
            >
              <span class="shot-order">{{ String(item.order || index + 1).padStart(2, '0') }}</span>
              <span class="shot-copy">
                <b :title="item.title">{{ item.title }}</b>
                <small>
                  {{ actualDuration(selectedVersion(item)) || !clipDuration(item) ? '' : '约 '
                  }}{{ secondsLabel(clipDuration(item)) }}
                  <template v-if="selectedVersion(item)?.width && selectedVersion(item)?.height">
                    · {{ selectedVersion(item).width }} × {{ selectedVersion(item).height }}
                  </template>
                </small>
              </span>
              <WorkflowIcon name="play" />
            </button>
            <select
              v-if="item.versions.length > 1"
              v-model="choices[item.id]"
              class="shot-version"
              :aria-label="`${item.title}导出版本`"
              @change="previewId = item.id"
            >
              <option v-for="(version, i) in item.versions" :key="version.id" :value="version.file">
                {{ versionLabel(item, version, i) }}
              </option>
            </select>
            <span v-else class="single-version"><WorkflowIcon name="check" />已选用</span>
          </article>
          <div v-if="!items.length" class="export-empty">
            <WorkflowIcon name="video" />
            <b>还没有可导出的镜头</b>
            <p>先生成视频，再回来拼接成片。</p>
          </div>
        </div>
      </section>

      <aside class="export-settings" aria-label="镜头预览和导出设置">
        <div class="preview-section">
          <h3>镜头预览</h3>
          <div class="export-preview">
            <video
              v-if="previewVersion?.url && !previewError"
              :key="previewVersion.url"
              :src="previewVersion.url"
              :aria-label="`${previewItem.title}镜头预览`"
              controls
              preload="metadata"
              playsinline
              @loadedmetadata="rememberDuration($event, previewVersion.url)"
              @error="previewError = true"
            ></video>
            <p v-else>{{ previewError ? '暂时无法预览，请检查视频文件。' : '生成镜头后可在这里预览' }}</p>
          </div>
          <p class="preview-caption" :title="previewItem?.title">
            {{ previewItem?.title || '未选择镜头' }}
            <template v-if="previewVersion">
              · 版本
              {{ previewItem.versions.findIndex((version) => version.file === previewVersion.file) + 1 }}
            </template>
          </p>
        </div>
        <div class="output-section">
          <h3 id="export-format-title">成片画幅</h3>
          <div class="aspect-options" role="group" aria-labelledby="export-format-title">
            <button
              v-for="option in formats"
              :key="option.value"
              :class="{ selected: aspect === option.value }"
              :aria-pressed="aspect === option.value"
              :aria-label="`${option.label} ${option.value}`"
              @click="aspect = option.value"
            >
              <span class="aspect-shape" :class="option.shape" aria-hidden="true"></span>
              <b>{{ option.label }}</b>
              <small>{{ option.value }}</small>
            </button>
          </div>
          <dl class="export-specs">
            <div>
              <dt>输出规格</dt>
              <dd>{{ format.size }} · 24 fps</dd>
            </div>
            <div>
              <dt>视频格式</dt>
              <dd>MP4 · 保留原音轨</dd>
            </div>
          </dl>
          <p class="export-hint">画面等比缩放，比例不同时补黑边。原视频会保留。</p>
          <p v-if="durationEstimated && items.length" class="export-hint">
            总时长按已知视频和生成时长估算，以成片为准。
          </p>
        </div>
      </aside>
    </div>

    <footer class="export-footer">
      <span class="export-local"><WorkflowIcon name="shield" />本地合并，无需调用模型</span>
      <div class="export-actions">
        <button @click="emit('close')">取消</button>
        <button class="export-confirm" :disabled="!items.length" @click="startExport">
          <WorkflowIcon name="download" />导出 {{ items.length }} 镜成片
        </button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.export-workspace {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.export-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 26px;
  flex-shrink: 0;
}
.export-overview p,
.section-heading p,
.export-hint,
.preview-caption {
  margin: 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--day-muted, #a1a2b2);
}
.export-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  white-space: nowrap;
}
.export-summary b {
  color: var(--day-accent, #c7b1f5);
  font-size: 17px;
  margin-right: 3px;
}
.summary-divider {
  height: 14px;
  width: 1px;
  background: var(--day-line, #41434e);
}
.export-warning {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 26px 14px;
  padding: 10px 12px;
  border: 1px solid var(--day-warn-line, #66563b);
  border-radius: 8px;
  color: var(--day-warn, #dfbf89);
  background: var(--day-warn-soft, #3a3225);
  font-size: 11px;
  flex-shrink: 0;
}
.export-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  flex: 1;
  min-height: 0;
  border-top: 1px solid var(--day-line, #393a44);
}
.export-sequence {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 22px 20px 26px;
}
h3 {
  font-size: 12px;
  font-weight: 600;
  margin: 0 0 6px;
}
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.export-workspace button,
.shot-version {
  font-size: 11px;
  color: var(--day-text, #d4d4e0);
  border: 1px solid var(--day-line, #42434f);
  border-radius: 7px;
  background: var(--day-panel, #282932);
}
.export-workspace button:focus-visible,
.shot-version:focus-visible {
  outline: 2px solid var(--day-accent, #baa2f4);
  outline-offset: 3px;
}
.export-workspace .reset-choices {
  padding: 5px 8px;
  font-size: 10px;
  color: var(--day-accent, #c7b1f5);
  white-space: nowrap;
}
.export-list {
  overflow-y: auto;
  min-height: 0;
  flex: 1;
  padding: 3px 6px 3px 3px;
  margin: -3px;
  overscroll-behavior: contain;
}
.export-shot {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  border: 1px solid var(--day-line, #393d44);
  background: var(--day-panel, #25272e);
  border-radius: 9px;
  padding: 12px;
  margin-bottom: 8px;
}
.export-shot.is-previewing {
  border-color: var(--day-accent-line, #9278b8);
  background: var(--day-accent-soft, #baa2f40c);
}
.export-workspace .shot-preview {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
  text-align: left;
  padding: 0;
  border: 0;
  background: transparent;
}
.shot-order {
  display: grid;
  place-items: center;
  width: 34px;
  height: 38px;
  flex-shrink: 0;
  border-radius: 6px;
  background: var(--day-inset, #1b1c23);
  color: var(--day-muted, #a1a2b2);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.is-previewing .shot-order {
  color: var(--day-accent, #c7b1f5);
}
.shot-copy {
  min-width: 0;
  flex: 1;
}
.shot-copy b,
.shot-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.shot-copy b {
  font-size: 12px;
  font-weight: 500;
}
.shot-copy small {
  margin-top: 4px;
  font-size: 10px;
  color: var(--day-muted, #a1a2b2);
}
.shot-preview > svg {
  width: 13px;
  height: 13px;
  color: var(--day-accent, #c7b1f5);
  opacity: 0.6;
}
.shot-version {
  width: 172px;
  flex-shrink: 0;
  padding: 7px;
  margin: 0;
  background: var(--day-inset, #1c1d25);
}
.single-version {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: var(--day-muted, #a1a2b2);
  white-space: nowrap;
}
.single-version svg {
  width: 13px;
  height: 13px;
  color: var(--day-mint, #99bea9);
}
.export-settings {
  overflow-y: auto;
  min-height: 0;
  min-width: 0;
  padding: 20px;
  border-left: 1px solid var(--day-line, #393a44);
  background: var(--day-panel, #23242c);
  overscroll-behavior: contain;
}
.export-preview {
  display: grid;
  place-items: center;
  background: #08090c;
  border-radius: 9px;
  overflow: hidden;
  aspect-ratio: 16 / 9;
  margin-top: 12px;
}
.export-preview video {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 0;
  object-fit: contain;
}
.export-preview p {
  padding: 16px;
  text-align: center;
  font-size: 11px;
  color: #a1a2b2;
}
.preview-caption {
  margin-top: 9px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.output-section {
  margin-top: 26px;
}
.aspect-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}
.export-workspace .aspect-options button {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 12px 5px 9px;
}
.export-workspace .aspect-options button.selected {
  background: var(--day-accent-soft, #baa2f412);
  border-color: var(--day-accent-line, #9278b8);
  color: var(--day-accent, #c7b1f5);
}
.aspect-options b {
  font-weight: 500;
}
.aspect-options small {
  font-size: 10px;
  opacity: 0.75;
}
.aspect-shape {
  display: block;
  border: 1.5px solid currentColor;
  border-radius: 3px;
  height: 20px;
  width: 30px;
  margin-bottom: 3px;
  opacity: 0.8;
}
.aspect-shape.portrait {
  width: 13px;
}
.aspect-shape.square {
  width: 20px;
}
.export-specs {
  margin: 18px 0 12px;
  font-size: 11px;
}
.export-specs > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 9px;
}
.export-specs dt {
  color: var(--day-muted, #a1a2b2);
}
.export-specs dd {
  margin: 0;
  text-align: right;
}
.export-hint + .export-hint {
  margin-top: 8px;
}
.export-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 18px 26px;
  border-top: 1px solid var(--day-line, #393a44);
  flex-shrink: 0;
}
.export-local,
.export-actions,
.export-actions button {
  display: flex;
  align-items: center;
  gap: 8px;
}
.export-local {
  font-size: 11px;
  color: var(--day-muted, #a1a2b2);
}
.export-local svg {
  width: 14px;
  height: 14px;
}
.export-actions button {
  padding: 10px 14px;
  font-size: 12px;
  white-space: nowrap;
}
.export-workspace .export-confirm {
  background: var(--day-accent, #baa2f4);
  border-color: var(--day-accent-line, #baa2f4);
  color: var(--day-on-accent, #1e172e);
  font-weight: 600;
}
.export-workspace .export-confirm:hover:not(:disabled) {
  background: var(--day-accent, #c7b1f5);
}
.export-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 180px;
  height: 100%;
  color: var(--day-muted, #a1a2b2);
  font-size: 12px;
}
.export-empty > svg {
  width: 32px;
  height: 32px;
}
.export-empty p {
  font-size: 11px;
  margin: 0;
}
@media (max-width: 800px) {
  .export-overview {
    flex-wrap: wrap;
    gap: 8px;
    padding: 12px 18px;
  }
  .export-warning {
    margin: 0 18px 12px;
  }
  .export-body {
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .export-sequence {
    flex-shrink: 0;
    padding: 18px;
  }
  .export-list {
    overflow: visible;
  }
  .export-settings {
    order: -1;
    flex-shrink: 0;
    overflow: visible;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 18px;
    border-left: 0;
    border-bottom: 1px solid var(--day-line, #393a44);
    padding: 18px;
  }
  .output-section {
    margin-top: 0;
  }
  .export-footer {
    padding: 14px 18px;
  }
}
@media (max-width: 480px) {
  .export-settings {
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
  }
  .preview-section {
    order: 1;
  }
  .export-preview {
    max-height: 160px;
  }
  .export-shot {
    flex-wrap: wrap;
  }
  .export-workspace .shot-preview {
    flex-basis: 100%;
  }
  .shot-version {
    width: calc(100% - 46px);
    margin-left: 46px;
  }
  .single-version {
    margin-left: 46px;
  }
  .export-local {
    display: none;
  }
  .export-actions {
    width: 100%;
    justify-content: space-between;
  }
}
</style>

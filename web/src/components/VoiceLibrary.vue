<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { api } from '../api.js';
import ComposerSelect from './ComposerSelect.vue';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({
  voices: Array,
  selected: String,
  provider: String,
  loading: Boolean,
  notice: String,
  error: String,
  busy: Boolean,
  taskId: String,
  settings: Object,
});
const emit = defineEmits(['select', 'refresh']);
const dialog = ref(null),
  trigger = ref(null),
  player = ref(null),
  query = ref(''),
  category = ref('all'),
  gender = ref(''),
  locale = ref(''),
  page = ref(1),
  favorites = ref([]);
const previewId = ref(''),
  previewUrl = ref(''),
  previewError = ref(''),
  auditioning = ref('');
const favoriteKey = computed(() => `frameflow.voice-favorites.${props.provider}`);
const active = computed(() => props.voices?.find((voice) => voice.id === props.selected));
const locales = computed(() => [
  { value: '', label: '全部语言' },
  ...Array.from(new Set((props.voices || []).map((voice) => voice.locale).filter(Boolean))).map((value) => ({
    value,
    label: value,
  })),
]);
const filtered = computed(() => {
  const term = query.value.trim().toLocaleLowerCase();
  return (props.voices || []).filter(
    (voice) =>
      (category.value !== 'favorites' || favorites.value.includes(voice.id)) &&
      (!gender.value || voice.gender === gender.value) &&
      (!locale.value || voice.locale === locale.value) &&
      (!term || [voice.cn, voice.id, voice.hint, voice.locale].join(' ').toLocaleLowerCase().includes(term))
  );
});
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 20)));
const visible = computed(() => filtered.value.slice((page.value - 1) * 20, page.value * 20));
const tabs = [
  { value: 'all', label: '全部音色' },
  { value: 'favorites', label: '收藏音色' },
];
let previewSequence = 0;
watch(
  favoriteKey,
  () => {
    try {
      const stored = JSON.parse(localStorage.getItem(favoriteKey.value) || '[]');
      favorites.value = Array.isArray(stored) ? stored.filter((id) => typeof id === 'string') : [];
    } catch {
      favorites.value = [];
    }
  },
  { immediate: true }
);
watch([query, category, gender, locale, () => props.voices], () => {
  page.value = 1;
});
watch(
  () => [props.provider, props.busy],
  () => close()
);
function show() {
  if (!props.busy) dialog.value.showModal();
}
function stopPreview() {
  previewSequence++;
  player.value?.pause();
  auditioning.value = '';
}
function close() {
  stopPreview();
  previewUrl.value = '';
  previewId.value = '';
  previewError.value = '';
  dialog.value?.close();
}
function select(voice) {
  emit('select', voice.id);
  close();
}
function toggleFavorite(id) {
  favorites.value = favorites.value.includes(id)
    ? favorites.value.filter((value) => value !== id)
    : [...favorites.value, id];
  try {
    localStorage.setItem(favoriteKey.value, JSON.stringify(favorites.value));
  } catch {
    /* Favorites remain usable for this session. */
  }
}
async function audition(voice) {
  stopPreview();
  const serial = previewSequence;
  auditioning.value = voice.id;
  previewError.value = '';
  try {
    const result = await api.previewSpeech(props.taskId, {
      voice_id: voice.id,
      speech_settings: props.settings,
    });
    if (serial !== previewSequence || !dialog.value?.open) return;
    previewId.value = voice.id;
    previewUrl.value = result.url;
    await nextTick();
    if (serial === previewSequence) await player.value?.play().catch(() => {});
  } catch (error) {
    if (serial === previewSequence) previewError.value = error.message;
  } finally {
    if (serial === previewSequence) auditioning.value = '';
  }
}
onBeforeUnmount(stopPreview);
</script>

<template>
  <div class="voice-library">
    <button
      ref="trigger"
      type="button"
      class="voice-library-trigger"
      aria-label="音色"
      aria-haspopup="dialog"
      :disabled="busy || loading"
      @click="show"
    >
      <WorkflowIcon name="audio" /><span>{{ loading ? '读取音色…' : active?.cn || '选择音色' }}</span
      ><WorkflowIcon name="chevron-down" />
    </button>
    <dialog
      ref="dialog"
      class="voice-library-dialog"
      aria-label="音色库"
      @pointerdown.stop
      @wheel.stop
      @keydown.stop
      @cancel.prevent="close"
      @close="stopPreview"
      @click="if ($event.target === dialog) close();"
    >
      <header class="voice-library-heading">
        <div>
          <h2>选择声音</h2>
          <p>{{ provider === 'minimax' ? 'MiniMax' : 'Edge TTS' }} · {{ voices?.length || 0 }} 个可用音色</p>
        </div>
        <button aria-label="关闭音色库" @click="close"><WorkflowIcon name="close" /></button>
      </header>
      <div class="voice-library-toolbar">
        <div class="voice-tabs" role="group" aria-label="音色分类">
          <button
            v-for="tab in tabs"
            :key="tab.value"
            :aria-pressed="category === tab.value"
            @click="category = tab.value"
          >
            {{ tab.label }}
          </button>
        </div>
        <label class="voice-search"
          ><WorkflowIcon name="search" /><input
            v-model="query"
            autofocus
            type="search"
            aria-label="搜索音色"
            placeholder="搜索名称、语言或声音特点"
        /></label>
        <button class="refresh-voices" :disabled="loading" aria-label="刷新音色库" @click="emit('refresh')">
          <WorkflowIcon name="redo" />
        </button>
      </div>
      <div class="voice-library-filters">
        <div role="group" aria-label="音色性别">
          <button
            v-for="item in [
              { value: '', label: '全部' },
              { value: 'female', label: '女声' },
              { value: 'male', label: '男声' },
            ]"
            :key="item.value"
            :aria-pressed="gender === item.value"
            @click="gender = item.value"
          >
            {{ item.label }}
          </button>
        </div>
        <ComposerSelect
          label="筛选音色语言"
          :model-value="locale"
          :options="locales"
          @update:model-value="locale = $event"
        />
        <span>{{ filtered.length }} 个匹配</span>
      </div>
      <p v-if="error || notice" class="voice-catalog-notice" :role="error ? 'alert' : 'status'">
        {{ error || notice }}
      </p>
      <div class="voice-library-list" aria-label="可用音色">
        <div v-if="loading" class="voice-empty" role="status">正在同步音色库…</div>
        <template v-else>
          <article
            v-for="voice in visible"
            :key="voice.id"
            class="voice-card"
            :class="{ selected: selected === voice.id }"
          >
            <button
              class="voice-audition"
              :disabled="!!auditioning || busy"
              :aria-label="'试听' + voice.cn"
              @click="audition(voice)"
            >
              <WorkflowIcon :name="auditioning === voice.id ? 'audio' : 'play'" />
            </button>
            <div class="voice-copy">
              <strong>{{ voice.cn }}</strong>
              <p>{{ voice.hint || voice.id }}</p>
              <div class="voice-tags">
                <span v-if="voice.locale && voice.locale !== '未标注'">{{ voice.locale }}</span
                ><span v-if="voice.gender">{{ voice.gender === 'female' ? '女声' : '男声' }}</span
                ><span v-if="voice.source === 'cloned'">克隆音色</span
                ><span v-if="voice.source === 'designed'">设计音色</span>
              </div>
            </div>
            <button
              class="voice-select-button"
              :aria-label="'选择' + voice.cn"
              :aria-pressed="selected === voice.id"
              @click="select(voice)"
            >
              <WorkflowIcon v-if="selected === voice.id" name="check" />{{
                selected === voice.id ? '已选' : '选择'
              }}
            </button>
            <button
              class="voice-favorite"
              :aria-label="'收藏' + voice.cn"
              :aria-pressed="favorites.includes(voice.id)"
              @click="toggleFavorite(voice.id)"
            >
              <WorkflowIcon name="star" />
            </button>
          </article>
          <div v-if="!visible.length" class="voice-empty">
            {{
              category === 'favorites'
                ? '还没有匹配的收藏音色。点击星标，留下常用的声音。'
                : '没有找到匹配的音色，试试其他关键词或筛选条件。'
            }}
          </div>
        </template>
      </div>
      <footer class="voice-library-footer">
        <div class="voice-pagination">
          <span>共 {{ filtered.length }} 个</span
          ><button aria-label="上一页音色" :disabled="page <= 1" @click="page--">上一页</button
          ><span>{{ page }} / {{ pages }}</span
          ><button aria-label="下一页音色" :disabled="page >= pages" @click="page++">下一页</button>
        </div>
        <p class="audition-note">
          {{
            provider === 'minimax'
              ? '试听生成短句，按 MiniMax 服务规则计费；重复试听优先使用缓存。'
              : '使用短句试听当前声音和调节效果。'
          }}
        </p>
        <p v-if="previewError" class="voice-preview-error" role="alert">{{ previewError }}</p>
        <div v-if="previewUrl" class="voice-preview-player">
          <span>{{ voices?.find((voice) => voice.id === previewId)?.cn }} · 试听</span
          ><audio ref="player" :src="previewUrl" controls aria-label="音色试听播放器" />
        </div>
      </footer>
    </dialog>
  </div>
</template>

<style scoped>
.voice-library {
  min-width: 0;
  max-width: 200px;
}
.voice-library .voice-library-trigger {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 32px;
  max-width: 100%;
  padding: 6px 9px;
  border: 1px solid var(--day-line-soft, #ffffff12);
  border-radius: 9px;
  background: var(--day-hover, #ffffff04);
  color: var(--day-muted, #b8b7c4);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.voice-library-trigger span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.voice-library-trigger svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
.voice-library-dialog {
  width: min(820px, calc(100vw - 32px));
  max-height: min(740px, calc(100dvh - 48px));
  margin: auto;
  padding: 0;
  box-sizing: border-box;
  border: 1px solid var(--day-line, #484350);
  border-radius: 22px;
  color: var(--day-text, #e6e0ef);
  background: var(--day-panel, #24232b);
  box-shadow: 0 24px 80px var(--day-shadow, #0008);
  overflow: hidden;
}
.voice-library-dialog[open] {
  display: flex;
  flex-direction: column;
}
.voice-library-dialog::backdrop {
  background: var(--day-backdrop, #0b0a107a);
  backdrop-filter: blur(4px);
}
.voice-library-dialog button {
  font: inherit;
  cursor: pointer;
}
.voice-library-dialog button:disabled {
  opacity: 0.4;
  cursor: default;
}
.voice-library-dialog button:focus-visible,
.voice-search:focus-within {
  outline: 2px solid var(--day-accent, #c0aaf1);
  outline-offset: 2px;
}
.voice-library-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 22px 24px 18px;
  border-bottom: 1px solid var(--day-line-soft, #ffffff0c);
}
h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}
.voice-library-heading p {
  margin: 6px 0 0;
  color: var(--day-muted, #aaa4b5);
  font-size: 11px;
}
.voice-library-heading button {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  flex-shrink: 0;
}
.voice-library-heading button svg {
  width: 18px;
  height: 18px;
}
.voice-library-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 24px 12px;
}
.voice-tabs {
  display: flex;
  gap: 3px;
  flex-shrink: 0;
  padding: 3px;
  border-radius: 10px;
  background: var(--day-inset, #ffffff05);
}
.voice-library-dialog .voice-tabs button {
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 11px;
}
.voice-library-dialog .voice-tabs button[aria-pressed='true'] {
  color: var(--day-accent, #d0baff);
  background: var(--day-accent-soft, #b9a0f51c);
}
.voice-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  padding: 7px 10px;
  border: 1px solid var(--day-line-soft, #ffffff10);
  border-radius: 10px;
  background: var(--day-inset, #ffffff04);
}
.voice-search svg {
  width: 16px;
  height: 16px;
  color: var(--day-faint, #9992a6);
}
.voice-search input {
  min-width: 0;
  width: 100%;
  border: 0;
  padding: 0;
  font: inherit;
  font-size: 11px;
  color: inherit;
  background: transparent;
  outline: none;
  box-shadow: none;
}
.voice-search input::placeholder {
  color: var(--day-faint, #9992a6);
}
.voice-library-filters {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 24px 14px;
}
.voice-library-filters > div:first-child {
  display: flex;
  gap: 4px;
}
.voice-library-dialog .voice-library-filters button {
  padding: 6px 10px;
  font-size: 10px;
}
.voice-library-dialog .voice-library-filters button[aria-pressed='true'] {
  color: var(--day-accent, #c0aaf1);
  background: var(--day-accent-soft, #b9a0f51c);
}
.voice-library-filters > span {
  margin-left: auto;
  color: var(--day-faint, #9992a6);
  font-size: 10px;
}
.voice-catalog-notice {
  margin: 0 24px 12px;
  padding: 9px 12px;
  border-radius: 9px;
  background: var(--day-warn-soft, #dcb97c12);
  color: var(--day-warn, #dcb97c);
  font-size: 11px;
  line-height: 1.6;
}
.voice-library-list {
  min-height: 120px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 0 24px 12px;
  scrollbar-width: thin;
}
.voice-card {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px;
  padding: 13px 14px;
  border: 1px solid var(--day-line-soft, #ffffff08);
  border-radius: 13px;
  background: var(--day-inset, #ffffff04);
}
.voice-card.selected {
  background: var(--day-accent-soft, #b9a0f514);
  border-color: var(--day-accent-line, #b9a0f54a);
}
.voice-library-dialog .voice-audition {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  padding: 8px;
  flex-shrink: 0;
  border: 1px solid var(--day-line-soft, #ffffff0c);
  border-radius: 10px;
  background: var(--day-panel, #ffffff06);
  color: var(--day-muted, #c0b6cf);
}
.voice-audition svg {
  width: 17px;
  height: 17px;
}
.voice-copy {
  flex: 1;
  min-width: 0;
}
.voice-copy strong {
  font-size: 12px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.voice-copy p {
  margin: 4px 0;
  color: var(--day-muted, #a9a2b3);
  font-size: 10px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.voice-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 6px;
}
.voice-tags span {
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--day-hover, #ffffff07);
  color: var(--day-muted, #b8b0c3);
  font-size: 9px;
}
.voice-library-dialog .voice-select-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  gap: 5px;
  padding: 6px 12px;
  border: 1px solid var(--day-accent-line, #b9a0f54a);
  border-radius: 8px;
  color: var(--day-accent, #c0aaf1);
  font-size: 10px;
}
.voice-library-dialog .voice-select-button[aria-pressed='true'] {
  background: var(--day-accent-soft, #b9a0f520);
}
.voice-library-dialog .voice-favorite {
  flex-shrink: 0;
  padding: 6px;
}
.voice-favorite svg {
  width: 17px;
  height: 17px;
}
.voice-library-dialog .voice-favorite[aria-pressed='true'] {
  color: var(--day-accent, #c0aaf1);
}
.voice-favorite[aria-pressed='true'] svg {
  fill: currentColor;
}
.voice-empty {
  padding: 35px 24px;
  text-align: center;
  color: var(--day-muted, #aaa4b5);
  font-size: 12px;
  line-height: 1.8;
}
.voice-library-footer {
  flex-shrink: 0;
  border-top: 1px solid var(--day-line-soft, #ffffff0c);
  padding: 12px 24px 17px;
}
.voice-pagination {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  font-size: 10px;
}
.voice-pagination > span:first-child {
  margin-right: auto;
  color: var(--day-muted, #aaa4b5);
}
.voice-library-dialog .voice-pagination button {
  padding: 6px 8px;
  font-size: 10px;
}
.audition-note {
  margin: 9px 0 0;
  color: var(--day-faint, #9992a6);
  font-size: 10px;
  line-height: 1.6;
}
.voice-preview-error {
  color: var(--day-danger, #e4a29b);
  font-size: 11px;
}
.voice-preview-player {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 9px;
  color: var(--day-muted, #aaa4b5);
  font-size: 10px;
}
.voice-preview-player audio {
  width: 260px;
  max-width: 65%;
  height: 30px;
}
@media (max-width: 760px) {
  .voice-library-dialog {
    border-radius: 16px;
  }
  .voice-library-heading {
    padding: 18px 16px 14px;
  }
  .voice-library-toolbar {
    padding: 14px 16px 10px;
    flex-wrap: wrap;
    gap: 8px;
  }
  .voice-tabs {
    width: 100%;
  }
  .voice-tabs button {
    flex: 1;
  }
  .voice-library-filters {
    padding: 0 16px 12px;
    gap: 6px;
    flex-wrap: wrap;
  }
  .voice-library-list {
    padding: 0 12px 10px;
  }
  .voice-card {
    padding: 11px 9px;
    gap: 8px;
  }
  .voice-library-dialog .voice-audition {
    width: 32px;
    height: 32px;
  }
  .voice-library-dialog .voice-select-button {
    padding: 5px 8px;
  }
  .voice-library-footer {
    padding: 10px 16px 14px;
  }
  .voice-preview-player {
    flex-wrap: wrap;
  }
  .voice-preview-player audio {
    max-width: 100%;
    width: 100%;
  }
}
</style>

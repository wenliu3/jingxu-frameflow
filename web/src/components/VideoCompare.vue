<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import { currentVideo, videoVersions } from '../workflowVideo.js';
import { displayPrompt } from '../promptMentions.js';
const props = defineProps({ node: Object });
const emit = defineEmits(['close', 'select']);
const dialog = ref(null);
const versions = computed(() => videoVersions(props.node).filter((v) => v.status === 'succeeded' && v.url));
const choices = ref(versions.value.slice(-4).map((v) => v.id));
const displayed = computed(() =>
  choices.value.map((id) => versions.value.find((v) => v.id === id)).filter(Boolean)
);
let previousFocus;
function key(event) {
  if (event.key === 'Escape') {
    event.stopPropagation();
    emit('close');
  }
  if (event.key === 'Tab') {
    const elements = [...dialog.value.querySelectorAll('button, select, a[href]')].filter(
      (el) => !el.disabled
    );
    if (event.shiftKey && document.activeElement === elements[0]) {
      event.preventDefault();
      elements.at(-1)?.focus();
    }
    if (!event.shiftKey && document.activeElement === elements.at(-1)) {
      event.preventDefault();
      elements[0]?.focus();
    }
  }
}
onMounted(() => {
  previousFocus = document.activeElement;
  dialog.value.focus();
});
onBeforeUnmount(() => previousFocus?.focus());
</script>
<template>
  <Teleport to="body"
    ><div class="compare-backdrop" @pointerdown.self="emit('close')">
      <section
        ref="dialog"
        class="compare-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="对比视频候选"
        tabindex="-1"
        @keydown="key"
      >
        <header>
          <div>
            <b>{{ node.data.title }} · 候选对比</b>
            <p>并排看画面与动作，选择满意的版本用于成片。</p>
          </div>
          <button aria-label="关闭候选对比" @click="emit('close')"><WorkflowIcon name="close" /></button>
        </header>
        <div class="compare-grid">
          <article
            v-for="(v, index) in displayed"
            :key="index"
            :class="{ chosen: currentVideo(node)?.id === v.id }"
          >
            <select aria-label="选择对比版本" v-model="choices[index]">
              <option v-for="(option, i) in versions" :key="option.id" :value="option.id">
                版本 {{ i + 1 }} · {{ option.backend === 'api' && !option.actual_duration ? '计划 ' : ''
                }}{{ option.actual_duration ?? option.duration }}s{{
                  option.width ? ` · ${option.width}×${option.height}` : ''
                }}
              </option>
            </select>
            <video :key="v.id" :src="v.url" controls preload="metadata" playsinline></video>
            <p>{{ displayPrompt(v.description) || '历史视频' }}</p>
            <footer>
              <span>{{
                currentVideo(node)?.id === v.id
                  ? '当前选用'
                  : v.ratio && v.ratio !== 'auto'
                    ? v.ratio
                    : '自动画幅'
              }}</span
              ><a :href="v.url" :download="v.file">下载</a
              ><button @click="emit('select', v.id)">
                {{ currentVideo(node)?.id === v.id ? '已选用' : '使用这个版本' }}
              </button>
            </footer>
          </article>
        </div>
      </section>
    </div></Teleport
  >
</template>
<style scoped>
.compare-backdrop {
  position: fixed;
  inset: 0;
  background: var(--day-backdrop, #09090de0);
  backdrop-filter: blur(10px);
  display: grid;
  place-items: center;
  padding: 24px;
  z-index: 210;
}
.compare-dialog {
  width: min(1050px, 100%);
  max-height: calc(100dvh - 48px);
  overflow-y: auto;
  padding: 22px;
  background: var(--day-panel, #202027);
  border: 1px solid var(--day-accent-line, #504658);
  border-radius: 17px;
  color: var(--day-text, #e1dae9);
  box-shadow: 0 30px 100px var(--day-shadow, #0008);
  outline: none;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
header b {
  font-size: 16px;
  font-weight: 500;
}
header p {
  font-size: 11px;
  color: var(--day-muted, #9e95ab);
  margin: 6px 0 0;
}
button {
  background: var(--day-hover, #ffffff05);
  color: var(--day-text, #c6b7db);
  border: 1px solid var(--day-accent-line, #51445c);
  padding: 7px 10px;
  border-radius: 7px;
  cursor: pointer;
}
header button svg {
  width: 16px;
  height: 16px;
}
.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
article {
  padding: 10px;
  border: 1px solid var(--day-line, #42404c);
  border-radius: 10px;
  background: var(--day-accent-soft, #27252e);
  min-width: 0;
}
article.chosen {
  border-color: var(--day-accent-line, #bca4df);
}
select {
  display: block;
  width: 100%;
  background: var(--day-accent-soft, #1b1922);
  color: var(--day-text, #c6b7db);
  border: 1px solid var(--day-accent-line, #51445c);
  padding: 7px;
  font-size: 11px;
  border-radius: 6px;
  margin-bottom: 9px;
}
video {
  width: 100%;
  height: 210px;
  background: var(--day-inset, #101016);
  border-radius: 5px;
  object-fit: contain;
}
article p {
  font-size: 11px;
  color: var(--day-muted, #aaa0b7);
  line-height: 1.7;
  height: 38px;
  overflow-y: auto;
  margin: 9px 0;
}
footer {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 10px;
}
footer span {
  flex: 1;
  color: var(--day-accent, #9588a4);
}
footer a {
  color: var(--day-accent, #ccb4ef);
}
footer button {
  font-size: 10px;
}
@media (max-width: 760px) {
  .compare-backdrop {
    padding: 10px;
  }
  .compare-dialog {
    padding: 14px;
    max-height: calc(100dvh - 20px);
  }
  .compare-grid {
    grid-template-columns: 1fr;
  }
  video {
    height: 190px;
  }
  header b {
    font-size: 13px;
  }
}
</style>

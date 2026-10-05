<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import { STORY_TEMPLATES, scriptParagraphs } from '../workflowStudio.js';
const props = defineProps({
  mode: String,
  reviews: Array,
  cfg: Object,
  exports: { type: Array, default: () => [] },
  shotCount: Number,
});
const emit = defineEmits(['close', 'confirm', 'storyboard', 'configure', 'export']);
const text = ref(''),
  duration = ref(5),
  dialog = ref(null);
const paragraphs = computed(() => scriptParagraphs(text.value));
const eligible = computed(() => (props.reviews || []).filter((r) => !r.error));
const aspect = ref('16:9');
const exportChoices = ref(
  Object.fromEntries(props.exports.map((item) => [item.id, item.currentFile || item.versions.at(-1)?.file]))
);
let previousFocus;
function keys(event) {
  if (event.key === 'Escape') emit('close');
  if (event.key !== 'Tab') return;
  const focusable = [...dialog.value.querySelectorAll('button:not(:disabled), textarea, select')];
  const first = focusable[0],
    last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
onMounted(async () => {
  previousFocus = document.activeElement;
  await nextTick();
  dialog.value?.focus();
});
onBeforeUnmount(() => previousFocus?.focus());
</script>
<template>
  <Teleport to="body"
    ><div class="studio-dialog-mask workspace-theme" @pointerdown.self="emit('close')">
      <section
        ref="dialog"
        class="studio-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="studio-dialog-title"
        tabindex="-1"
        @keydown="keys"
      >
        <header>
          <div>
            <span>{{
              mode === 'review' ? 'GENERATION QUEUE' : mode === 'export' ? 'FINAL CUT' : 'STORY BUILDER'
            }}</span>
            <h2 id="studio-dialog-title">
              {{
                mode === 'review' ? '检查并开始生成' : mode === 'export' ? '导出你的成片' : '把故事放进画布'
              }}
            </h2>
          </div>
          <button aria-label="关闭对话框" @click="emit('close')"><WorkflowIcon name="close" /></button>
        </header>
        <template v-if="mode === 'review'">
          <p class="dialog-intro">
            {{ cfg.video_backend === 'api' ? '视频 API' : 'ComfyUI' }} 将按镜头顺序逐个生成。{{
              cfg.video_backend === 'api' ? '实际时长由 API 模型决定。' : '每一镜使用自己的时长和像素预算。'
            }}已完成的镜头不会被覆盖。
          </p>
          <div class="review-list">
            <article v-for="item in reviews" :key="item.id" :class="{ blocked: item.error }">
              <WorkflowIcon :name="item.error ? 'help' : 'check'" />
              <div>
                <b>{{ item.title }}</b>
                <p>
                  {{
                    item.error ||
                    `${item.refs} 张参考图 · ${item.duration} 秒 · ${item.resolution} · ${item.count || 1} 个候选 · ${item.manual ? '手写提示词' : 'AI 编排'}`
                  }}
                </p>
              </div>
              <span>{{ item.error ? '需补充' : '可生成' }}</span>
            </article>
          </div>
          <p class="dialog-note">
            只会将检查通过的
            {{ eligible.length }}
            镜加入队列。生成会调用你配置的模型服务；关闭页面会暂停尚未提交的镜头，重新打开后可继续队列。
          </p>
          <footer>
            <button @click="emit('configure')"><WorkflowIcon name="settings" />服务设置</button
            ><button
              class="confirm"
              :disabled="!eligible.length"
              @click="
                emit(
                  'confirm',
                  eligible.map((r) => r.id)
                )
              "
            >
              <WorkflowIcon name="play" />开始生成 {{ eligible.length }} 镜
            </button>
          </footer>
        </template>
        <template v-else-if="mode === 'story'">
          <p class="dialog-intro">
            选择一个分镜结构，或粘贴已经写好的镜头描述。这里只创建可编辑的分镜，不调用模型。
          </p>
          <div class="story-templates">
            <button
              v-for="template in STORY_TEMPLATES"
              :key="template.id"
              @click="emit('storyboard', { descriptions: template.shots, duration })"
            >
              <img :src="`/images/inspiration-${template.image}.webp`" alt="" /><b>{{ template.title }}</b
              ><span>{{ template.subtitle }}</span>
            </button>
          </div>
          <label class="script-label"
            >镜头描述<textarea
              v-model="text"
              aria-label="分段镜头描述"
              rows="6"
              maxlength="30000"
              placeholder="镜头一：晨光透过窗户，镜头缓缓推进…&#10;&#10;镜头二：主角拿起桌上的信，低头阅读…&#10;&#10;每个镜头之间空一行。"
            ></textarea>
          </label>
          <div class="script-options">
            <span>{{ paragraphs.length }} 个分镜 · 每段最多 1000 字 · 最多 30 镜</span
            ><label
              >每镜时长<select v-model.number="duration">
                <option :value="5">5 秒</option>
                <option :value="10">10 秒</option>
                <option :value="15">15 秒</option>
              </select></label
            >
          </div>
          <footer>
            <button @click="emit('close')">取消</button
            ><button
              class="confirm"
              :disabled="
                !paragraphs.length || paragraphs.length > 30 || paragraphs.some((p) => p.length > 1000)
              "
              @click="emit('storyboard', { descriptions: paragraphs, duration })"
            >
              <WorkflowIcon name="plus" />创建 {{ paragraphs.length }} 个分镜
            </button>
          </footer>
        </template>
        <template v-else>
          <p class="dialog-intro">
            按镜头序列拼接已生成的视频，每镜可选择一个版本。统一画幅、帧率和音频，保留原片，画面按比例适配并补边。
          </p>
          <div class="review-list">
            <article v-for="(item, index) in exports" :key="item.id">
              <span>{{ String(index + 1).padStart(2, '0') }}</span>
              <div>
                <b>{{ item.title }}</b
                ><select v-model="exportChoices[item.id]" :aria-label="`${item.title}导出版本`">
                  <option v-for="(version, i) in item.versions" :key="version.id" :value="version.file">
                    版本 {{ i + 1 }} · {{ version.file }}
                  </option>
                </select>
              </div>
            </article>
          </div>
          <p class="dialog-note">
            本次包含 {{ exports.length }} 镜。{{
              shotCount > exports.length
                ? `还有 ${shotCount - exports.length} 镜没有可用视频，不会进入成片。`
                : '所有分镜都有可用视频。'
            }}合并在本地进行，不调用模型服务。
          </p>
          <label class="export-aspect"
            >成片画幅<select v-model="aspect">
              <option value="16:9">横屏 16:9 · 1280 × 720</option>
              <option value="9:16">竖屏 9:16 · 720 × 1280</option>
              <option value="1:1">方形 1:1 · 720 × 720</option>
            </select></label
          >
          <footer>
            <button @click="emit('close')">取消</button
            ><button
              class="confirm"
              :disabled="!exports.length"
              @click="emit('export', { files: exports.map((item) => exportChoices[item.id]), aspect })"
            >
              <WorkflowIcon name="download" />导出 {{ exports.length }} 镜成片
            </button>
          </footer>
        </template>
      </section>
    </div></Teleport
  >
</template>
<style scoped>
.studio-dialog-mask {
  position: fixed;
  inset: 0;
  z-index: 130;
  background: var(--day-backdrop, #090a10bd);
  backdrop-filter: blur(8px);
  display: grid;
  place-items: center;
  padding: 22px;
}
.studio-dialog {
  width: min(650px, 100%);
  max-height: 90dvh;
  overflow-y: auto;
  padding: 25px;
  border: 1px solid var(--day-accent-line, #46414f);
  border-radius: 16px;
  background: var(--day-inset, #1e1f26);
  color: var(--day-text, #e4e4ed);
  box-shadow: 0 30px 100px var(--day-shadow, #0007);
  outline: none;
}
.studio-dialog header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: center;
}
.studio-dialog header span {
  font-size: 9px;
  color: var(--day-accent, #b6a1ec);
  letter-spacing: 2px;
}
.studio-dialog h2 {
  font-size: 21px;
  font-weight: 500;
  margin: 6px 0;
}
.studio-dialog button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  padding: 9px 12px;
  background: var(--day-panel, #282932);
  border-color: var(--day-line, #42434f);
  color: var(--day-text, #c9c9d4);
  border-radius: 7px;
}
.dialog-intro,
.dialog-note {
  color: var(--day-muted, #a1a2b2);
  font-size: 12px;
  line-height: 1.8;
  margin: 14px 0 20px;
}
.dialog-note {
  color: var(--day-muted, #8e91a1);
  font-size: 11px;
}
.review-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.review-list article {
  display: flex;
  align-items: center;
  gap: 13px;
  border: 1px solid var(--day-line, #393d44);
  background: var(--day-panel, #25272e);
  border-radius: 8px;
  padding: 13px;
}
.review-list article > svg {
  color: var(--day-mint, #8fcbba);
  width: 18px;
}
.review-list article > div {
  flex: 1;
}
.review-list b {
  font-size: 12px;
  font-weight: 500;
}
.review-list p {
  margin: 5px 0 0;
  font-size: 11px;
  color: var(--day-muted, #9298a9);
}
.review-list article > span {
  font-size: 10px;
  color: var(--day-mint, #9acbb8);
}
.review-list .blocked {
  border-color: var(--day-danger-line, #62433e);
}
.review-list .blocked > svg,
.review-list .blocked > span {
  color: var(--day-warn, #daab8f);
}
.studio-dialog footer {
  display: flex;
  justify-content: space-between;
  border-top: 1px solid var(--day-line, #393a44);
  margin-top: 20px;
  padding-top: 18px;
}
.studio-dialog button.confirm {
  background: var(--day-accent, #baa2f4);
  border-color: var(--day-accent-line, #baa2f4);
  color: var(--day-on-accent, #1e172e);
  font-weight: 600;
}
.studio-dialog button:disabled {
  opacity: 0.35;
}
.story-templates {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.story-templates button {
  display: block;
  padding: 0 0 12px;
  overflow: hidden;
  text-align: left;
}
.story-templates img {
  width: 100%;
  height: 88px;
  object-fit: cover;
}
.story-templates b,
.story-templates span {
  display: block;
  padding: 0 10px;
}
.story-templates b {
  margin-top: 9px;
  font-size: 12px;
  font-weight: 500;
}
.story-templates span {
  font-size: 9px;
  color: var(--day-muted, #a5a4b3);
  margin-top: 5px;
}
.script-label {
  display: block;
  font-size: 12px;
  margin: 23px 0 12px;
}
.script-label textarea {
  display: block;
  margin-top: 9px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.8;
  background: var(--day-inset, #15161c);
  color: var(--day-text, #dcdde7);
  border-color: var(--day-line, #41434e);
}
.script-options {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--day-muted, #999cac);
  font-size: 10px;
}
.script-options label {
  display: flex;
  align-items: center;
  gap: 8px;
}
.script-options select {
  width: 75px;
  background: var(--day-panel, #252730);
  font-size: 11px;
  padding: 6px;
}
@media (max-width: 620px) {
  .studio-dialog {
    padding: 18px;
  }
  .script-options {
    flex-wrap: wrap;
  }
  .story-templates {
    gap: 7px;
  }
  .story-templates img {
    height: 60px;
  }
  .story-templates span {
    font-size: 8px;
  }
  .review-list article > span {
    display: none;
  }
}
.review-list select {
  margin-top: 8px;
  background: var(--day-inset, #1c1d25);
  border-color: var(--day-line, #42434f);
  color: var(--day-text, #c9cbd9);
  font-size: 11px;
  padding: 7px;
}
.export-aspect {
  font-size: 11px;
  color: var(--day-muted, #b5b8c9);
}
.export-aspect select {
  margin-top: 8px;
  background: var(--day-panel, #252630);
  font-size: 12px;
  padding: 9px;
}
</style>

<script setup>
import { computed, ref } from 'vue'
import BlockCard from './BlockCard.vue'

// 分块工作台。一块 = 一条 10s 视频，纵向排列即成片顺序。
// 顶栏是整个流程的主驱动：「AI 生成下一块」（可带一句指令纠偏），
// 也可以手动添加空块自己填。AI 只出「下一块」，满意了再要下一块。
const props = defineProps({
  blocks: { type: Array, default: () => [] },
  taskId: { type: String, required: true },
  project: { type: Object, default: () => null },
  aiBusy: { type: Boolean, default: false },       // AI 正在生成下一块（任务级 running）
  imageBusy: { type: Array, default: () => [] },   // 正在出首帧图的块 id
  promptBusy: { type: Array, default: () => [] },  // 正在写提示词的块 id
  videoBusy: { type: Array, default: () => [] },   // 正在出视频的块 id
  stageNote: { type: String, default: '' },        // 「故事已收尾」等来自后端的提示
})

const emit = defineEmits(['ai-next', 'add', 'patch', 'del', 'prompt', 'image', 'video', 'open-image', 'open-frame'])

const instruction = ref('')

const totalSec = computed(() =>
  props.blocks.reduce((sum, b) => sum + (Number(b.duration) || 10), 0),
)
const withVideo = computed(() => props.blocks.filter((b) => b.video_path).length)

function submitNext() {
  if (props.aiBusy) return
  emit('ai-next', instruction.value.trim())
  instruction.value = ''
}
</script>

<template>
  <section class="board">
    <div class="bar">
      <div class="summary">
        <span class="count mono">{{ blocks.length }} 块 · 约 {{ Math.round(totalSec) }}s</span>
        <span class="sep" aria-hidden="true"></span>
        <span class="count mono">{{ withVideo }} 已出片</span>
      </div>

      <div class="right">
        <input
          v-model="instruction"
          class="instruct"
          type="text"
          maxlength="2000"
          placeholder="给下一块的指令（可空）：接下来写他在天台等到日出…"
          :disabled="aiBusy"
          @keydown.enter.prevent="submitNext"
        />
        <button class="primary next" :disabled="aiBusy" @click="submitNext">
          <span v-if="aiBusy" class="spin"></span>
          {{ aiBusy ? 'AI 分块中…' : 'AI 生成下一块' }}
        </button>
        <button class="quiet" :disabled="aiBusy" @click="emit('add')">手动添加一块</button>
      </div>
    </div>

    <div v-if="stageNote" class="note">{{ stageNote }}</div>

    <div v-if="!blocks.length" class="waiting">
      <template v-if="aiBusy">
        <span class="spin"></span>
        <p>分块师正在读故事、盘资产，产出第一块（含分秒节拍与提示词）…</p>
      </template>
      <template v-else>
        <span class="empty-frame" aria-hidden="true">①</span>
        <p>资产都就绪了。点「AI 生成下一块」开始第一块，<br />也可以在指令框里点名开场怎么拍。</p>
        <p class="hint">每块是一条 10 秒视频：AI 会写清每几秒在干什么、镜头怎么动、不该出现什么；满意再要下一块。</p>
      </template>
    </div>

    <div v-else class="list">
      <BlockCard
        v-for="(b, i) in blocks"
        :key="b.block_id"
        :block="b"
        :task-id="taskId"
        :project="project"
        :busy="imageBusy.includes(b.block_id)"
        :prompt-busy="promptBusy.includes(b.block_id)"
        :video-busy="videoBusy.includes(b.block_id)"
        :is-first="i === 0"
        @patch="emit('patch', $event)"
        @del="emit('del', $event)"
        @prompt="emit('prompt', $event)"
        @image="emit('image', $event)"
        @video="emit('video', $event)"
        @open-image="emit('open-image', $event)"
        @open-frame="emit('open-frame', $event)"
      />
    </div>

    <!-- 景别可选值（BeatRow 的镜头输入引用 list="camera-options"） -->
    <datalist id="camera-options">
      <option v-for="c in ['远景', '全景', '中景', '近景', '特写']" :key="c" :value="c" />
    </datalist>
  </section>
</template>

<style scoped>
.board {
  min-width: 0;
  background: var(--surface);
  color: var(--fg);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  overflow: hidden;
}
.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 20px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.summary { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.count { font-size: 12px; color: var(--fg-2); white-space: nowrap; }
.sep { width: 1px; height: 12px; background: var(--line); }
.right { min-width: 0; margin-left: auto; display: flex; flex: 1 1 480px; justify-content: flex-end; flex-wrap: wrap; align-items: center; gap: 8px; }
.instruct {
  flex: 1 1 240px;
  min-width: 0;
  max-width: 460px;
  font-size: 12px;
  background: #fdfdfa;
}
.next { flex: none; }
.note {
  padding: 10px 20px;
  font-size: 12px;
  color: var(--accent);
  background: var(--accent-dim);
  border-bottom: 1px solid var(--line);
}

.waiting {
  padding: 56px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--fg-2);
  font-size: 13px;
  text-align: center;
}
.waiting p { margin: 0; line-height: 1.8; }
.waiting .hint { font-size: 11px; color: var(--fg-3); }
.empty-frame { display: grid; place-items: center; width: 56px; height: 46px; border: 1px solid #bcc5b3; border-radius: 10px; font-size: 20px; color: var(--accent); transform: rotate(-4deg); }

.list { display: grid; gap: 18px; padding: 18px 20px 22px; background: var(--surface-2); }

@media (max-width: 760px) {
  .bar { padding: 14px; }
  .right { flex-basis: 100%; justify-content: stretch; }
  .instruct { max-width: none; }
  .list { padding: 12px; gap: 14px; }
}
</style>

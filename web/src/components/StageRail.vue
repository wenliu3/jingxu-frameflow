<script setup>
import { computed } from 'vue'

// 四阶段进度。运行中按 label 反查当前步骤；停下时按 stage_state
// （directed / storyboarded / imaged）判定各步的完成情况——分段验收流程里
// 停在中间阶段是常态，不能把 succeeded 一律当全部完成。
const props = defineProps({
  status: { type: String, default: 'idle' },
  stage: { type: String, default: '' },
  stageState: { type: String, default: '' },
  done: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
})

const STEPS = [
  { label: '故事设定', role: '导演' },
  { label: '拆解分镜', role: '分镜师' },
  { label: '提示词', role: '翻译' },
  { label: '出图', role: '图像模型' },
]

// 各阶段停下来的位置：directed=完成1步，storyboarded=完成3步（分镜+提示词），imaged=全部
const DONE_STEPS = { directed: 1, storyboarded: 3, imaged: 4 }

const finished = computed(
  () => props.stageState === 'imaged' && props.status === 'succeeded',
)
const failed = computed(() => props.status === 'failed')

// -1 = 尚未开始（排队中）
const current = computed(() => {
  const s = props.stage || ''
  if (finished.value) return STEPS.length
  if (s.includes('生成故事设定')) return 0
  if (s.includes('拆解分镜')) return 1
  if (s.includes('生成提示词')) return 2
  if (s.includes('出图')) return 3
  return -1
})

const skipped = computed(() => (props.stage || '').includes('跳过出图'))

function stateOf(i) {
  if (failed.value && i === current.value) return 'fail'
  if (props.status === 'running') {
    if (i < current.value) return 'done'
    if (i === current.value) return 'active'
    return 'todo'
  }
  if (finished.value) return 'done'
  const doneCount = DONE_STEPS[props.stageState] ?? 0
  return i < doneCount ? 'done' : 'todo'
}
</script>

<template>
  <ol class="rail" aria-label="制作阶段">
    <li
      v-for="(step, i) in STEPS"
      :key="step.label"
      :class="[stateOf(i)]"
      :aria-current="stateOf(i) === 'active' ? 'step' : undefined"
    >
      <span class="node" aria-hidden="true">
        <svg v-if="stateOf(i) === 'done'" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2.5 6.2l2.3 2.3L9.5 3.8" fill="none" stroke="currentColor"
                stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <svg v-else-if="stateOf(i) === 'fail'" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M3.2 3.2l5.6 5.6M8.8 3.2l-5.6 5.6" fill="none" stroke="currentColor"
                stroke-width="1.8" stroke-linecap="round" />
        </svg>
        <i v-else-if="stateOf(i) === 'active'" class="spin"></i>
        <span v-else class="idx mono">{{ i + 1 }}</span>
      </span>
      <span class="text">
        <b>{{ step.label }}</b>
        <em>{{ step.role }}</em>
        <span class="sr-only">{{ { done: '已完成', active: '进行中', fail: '失败', todo: '待进行' }[stateOf(i)] }}</span>
      </span>
      <span v-if="i === 3 && total && !finished" class="counter mono">{{ done }}/{{ total }}</span>
      <span v-else-if="i === 3 && skipped" class="counter mono">跳过</span>
      <span class="wire" v-if="i < STEPS.length - 1"></span>
    </li>
  </ol>
</template>

<style scoped>
.rail {
  list-style: none;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: start;
  margin: 0;
  padding: 20px 24px;
  gap: 24px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}
li { display: grid; grid-template-columns: 36px minmax(0, 1fr); align-items: center; gap: 4px 10px; position: relative; min-width: 0; }
.node {
  box-sizing: border-box;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--fg-2);
  background: var(--surface-2);
  transition: border-color 0.2s var(--ease), background-color 0.2s var(--ease);
}
.node svg { width: 15px; height: 15px; }
.node .spin { width: 15px; height: 15px; border-width: 1.5px; }
.idx { font-size: 12px; }
li.done .node { border-color: var(--mint); color: var(--mint); background: #edf5ef; }
li.active .node { border-color: var(--accent); color: var(--accent-ink); background: var(--accent); }
li.fail .node { border-color: #b53d3d; color: #b53d3d; background: #fff1ef; }
.text { display: flex; flex-direction: column; gap: 4px; line-height: 1.4; min-width: 0; overflow-wrap: anywhere; }
.text b { font-size: 13px; font-weight: 500; color: var(--fg-2); }
.text em { font-style: normal; font-size: 11px; color: var(--fg-2); }
li.done .text b { color: var(--fg); }
li.active .text b { color: var(--fg); font-weight: 600; }
li.fail .text b { color: #b53d3d; }
.counter { grid-column: 2; color: var(--fg-2); font-size: 11px; overflow-wrap: anywhere; }
.wire { position: absolute; right: -18px; top: 18px; width: 12px; height: 1px; background: var(--line); }
li.done .wire { background: var(--mint); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

@media (max-width: 720px) {
  .rail { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px 24px; }
  li:nth-child(2) .wire { display: none; }
}
@media (max-width: 480px) {
  .rail { padding: 16px; gap: 20px 12px; }
  li { grid-template-columns: 32px minmax(0, 1fr); column-gap: 8px; }
  .node { width: 32px; height: 32px; }
  .text b { font-size: 12px; }
  .wire { display: none; }
}
@media (prefers-reduced-motion: reduce) { .node { transition: none; } }
</style>

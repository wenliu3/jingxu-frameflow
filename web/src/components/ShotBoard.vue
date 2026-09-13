<script setup>
import { computed, ref, watch } from 'vue'
import { loadView, saveView } from '../storage'
import ShotRow from './ShotRow.vue'
import ShotCard from './ShotCard.vue'

const props = defineProps({
  shots: { type: Array, default: () => [] },
  taskId: { type: String, required: true },
  regenerating: { type: Array, default: () => [] },
  videoBusy: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
})

const emit = defineEmits(['patch', 'regen', 'video', 'open-image'])

const view = ref(loadView())     // table | gallery，偏好持久化
watch(view, saveView)

// 两种提示词默认展开：图片提示词决定首帧图长什么样，
// 视频提示词决定这张图动起来是什么样，都是要送去模型的东西，不该藏在开关后面。
// 嫌行太长可以关掉。
const showPrompts = ref(true)

const rendered = computed(() => props.shots.filter((s) => s.image_path).length)

function isBusy(shot) {
  return props.regenerating.includes(shot.shot_id)
}

function isVideoBusy(shot) {
  return props.videoBusy.includes(shot.shot_id)
}
</script>

<template>
  <section class="board">
    <div class="bar">
      <div class="summary">
        <span class="count mono">{{ shots.length }} 个分镜</span>
        <span class="sep" aria-hidden="true"></span>
        <span class="count mono">{{ rendered }} 已出图</span>
      </div>

      <div class="right">
        <label class="switch">
          <input v-model="showPrompts" type="checkbox" />
          显示提示词
        </label>
        <div class="seg" role="group" aria-label="分镜视图">
          <button type="button" :class="{ on: view === 'table' }" :aria-pressed="view === 'table'" @click="view = 'table'" title="表格视图">
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <path d="M1.5 2.5h11M1.5 7h11M1.5 11.5h11" stroke="currentColor"
                    stroke-width="1.4" stroke-linecap="round" />
            </svg>
            表格
          </button>
          <button type="button" :class="{ on: view === 'gallery' }" :aria-pressed="view === 'gallery'" @click="view = 'gallery'" title="画廊视图">
            <svg viewBox="0 0 14 14" aria-hidden="true">
              <rect x="1.5" y="1.5" width="4.5" height="4.5" rx="1" fill="none"
                    stroke="currentColor" stroke-width="1.4" />
              <rect x="8" y="1.5" width="4.5" height="4.5" rx="1" fill="none"
                    stroke="currentColor" stroke-width="1.4" />
              <rect x="1.5" y="8" width="4.5" height="4.5" rx="1" fill="none"
                    stroke="currentColor" stroke-width="1.4" />
              <rect x="8" y="8" width="4.5" height="4.5" rx="1" fill="none"
                    stroke="currentColor" stroke-width="1.4" />
            </svg>
            画廊
          </button>
        </div>
      </div>
    </div>

    <div v-if="!shots.length" class="waiting">
      <span v-if="running" class="spin"></span>
      <p>{{ running ? '分镜正在生成，等待分镜师返回镜头列表…' : '还没有分镜数据' }}</p>
    </div>

    <div v-else-if="view === 'table'" class="tablewrap" role="region" aria-label="分镜表格，可横向滚动" tabindex="0">
      <table aria-label="可编辑分镜列表">
        <thead>
          <tr>
            <th scope="col" class="w-num">镜号</th>
            <th scope="col" class="w-pic">画面</th>
            <th scope="col" class="w-dur">时长</th>
            <th scope="col" class="w-cam">景别</th>
            <th scope="col" class="w-mot">运镜</th>
            <th scope="col">提示词 · 台词</th>
            <th scope="col" class="w-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <ShotRow
            v-for="s in shots"
            :key="s.shot_id"
            :shot="s"
            :task-id="taskId"
            :busy="isBusy(s)"
            :video-busy="isVideoBusy(s)"
            :show-prompts="showPrompts"
            @patch="emit('patch', $event)"
            @regen="emit('regen', $event)"
            @video="emit('video', $event)"
            @open="emit('open-image', $event)"
          />
        </tbody>
      </table>
    </div>

    <div v-else class="gallery">
      <ShotCard
        v-for="s in shots"
        :key="s.shot_id"
        :shot="s"
        :task-id="taskId"
        :busy="isBusy(s)"
        :video-busy="isVideoBusy(s)"
        :show-prompts="showPrompts"
        @patch="emit('patch', $event)"
        @regen="emit('regen', $event)"
        @video="emit('video', $event)"
        @open="emit('open-image', $event)"
      />
    </div>

    <!-- 景别 / 运镜的可选值，来源见 agents.py 的分镜 System Prompt。全页只能有一份。 -->
    <datalist id="camera-options">
      <option v-for="c in ['远景', '全景', '中景', '近景', '特写']" :key="c" :value="c" />
    </datalist>
    <datalist id="motion-options">
      <option v-for="m in ['固定', '缓慢推镜', '缓慢拉镜', '横移', '跟随']" :key="m" :value="m" />
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
.right { min-width: 0; margin-left: auto; display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px; }
.switch { min-height: 36px; display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--fg-2); cursor: pointer; user-select: none; white-space: nowrap; }
.switch input { width: 16px; height: 16px; margin: 0; accent-color: var(--accent); padding: 0; cursor: pointer; }
.switch input:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.seg { display: flex; gap: 3px; padding: 3px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm); }
.seg button {
  min-height: 34px;
  border: 1px solid transparent;
  border-radius: var(--r-xs);
  background: transparent;
  color: var(--fg-2);
  font-size: 12px;
  padding: 6px 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.seg button svg { width: 14px; height: 14px; }
.seg button:hover { background: var(--surface); color: var(--fg); }
.seg button.on { background: var(--surface); border-color: var(--line); color: var(--accent); font-weight: 600; box-shadow: 0 1px 3px rgba(38, 41, 37, 0.06); }
.seg button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

.waiting { padding: 64px 24px; display: flex; flex-direction: column; align-items: center; gap: 14px; color: var(--fg-2); font-size: 13px; text-align: center; }
.waiting p { margin: 0; line-height: 1.7; }
.tablewrap { max-width: 100%; overflow-x: auto; }
.tablewrap:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
table { width: 100%; min-width: 1080px; border-collapse: collapse; }
thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-2);
  border-bottom: 1px solid var(--line);
  padding: 13px 10px;
  text-align: left;
  font-size: 12px;
  font-weight: 500;
  color: var(--fg-2);
  letter-spacing: 0.2px;
  white-space: nowrap;
}
.w-num { width: 52px; text-align: center; }
.w-pic { width: 168px; }
.w-dur { width: 74px; text-align: center; }
.w-cam { width: 92px; text-align: center; }
.w-mot { width: 100px; text-align: center; }
.w-ops { width: 156px; }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap: 20px; padding: 20px; background: var(--surface-2); }

@media (max-width: 640px) {
  .bar { padding: 14px; gap: 12px; }
  .right { width: 100%; margin-left: 0; justify-content: space-between; gap: 8px 12px; }
  .gallery { padding: 12px; gap: 14px; }
}
</style>

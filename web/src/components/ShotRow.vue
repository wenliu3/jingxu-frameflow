<script setup>
import { ref } from 'vue'
import { api } from '../api'

// 表格视图的一行。
//
// 编辑用「本地草稿 + 失焦提交」而不是直接双向绑定：
// 生成过程中前端每 2 秒轮询一次，后端返回的是全新的 shot 对象，
// 直接 v-model 会把用户正在敲的半截文字冲掉。

const props = defineProps({
  shot: { type: Object, required: true },
  taskId: { type: String, required: true },
  busy: { type: Boolean, default: false },
  videoBusy: { type: Boolean, default: false },
  showPrompts: { type: Boolean, default: false },
})

const emit = defineEmits(['patch', 'regen', 'video', 'open'])

const CAMERAS = ['远景', '全景', '中景', '近景', '特写']
const MOTIONS = ['固定', '缓慢推镜', '缓慢拉镜', '横移', '跟随']

const local = ref({})

function val(field) {
  return field in local.value ? local.value[field] : (props.shot[field] ?? '')
}

function onInput(field, event) {
  local.value = { ...local.value, [field]: event.target.value }
}

function commit(field) {
  if (!(field in local.value)) return
  const raw = local.value[field]
  const next = { ...local.value }
  delete next[field]
  local.value = next

  const value = field === 'duration' ? Number(raw) || props.shot[field] : raw
  if (value === props.shot[field]) return
  emit('patch', { shotId: props.shot.shot_id, patch: { [field]: value } })
}
</script>

<template>
  <tr :class="{ stale: shot.stale }">
    <td class="num">
      <span class="id mono">{{ String(shot.shot_id).padStart(2, '0') }}</span>
    </td>

    <td class="pic">
      <button v-if="shot.image_path" type="button" class="thumb" :aria-label="`放大第 ${shot.shot_id} 镜图片`" @click="emit('open', shot.shot_id)">
        <img :src="api.imageUrl(taskId, shot.shot_id, shot.version)" :alt="`第 ${shot.shot_id} 镜`" />
        <span class="zoom">放大</span>
      </button>
      <div v-else class="noimg">未出图</div>

      <!-- 生成的视频挂在图片正下方；有 video_path 才渲染播放器 -->
      <video
        v-if="shot.video_path"
        class="vid"
        :src="api.videoUrl(taskId, shot.shot_id, shot.version)"
        :aria-label="`第 ${shot.shot_id} 镜视频预览`"
        controls
        preload="metadata"
        playsinline
      ></video>
      <div v-else-if="videoBusy" class="noimg vidwait">视频生成中…</div>
    </td>

    <td class="dur">
      <input
        class="cell-input center mono"
        inputmode="decimal"
        :aria-label="`第 ${shot.shot_id} 镜时长（秒）`"
        :value="val('duration')"
        @input="onInput('duration', $event)"
        @blur="commit('duration')"
        @keydown.enter.prevent="commit('duration')"
      />
    </td>

    <td>
      <input
        class="cell-input center"
        list="camera-options"
        :aria-label="`第 ${shot.shot_id} 镜景别`"
        :value="val('camera')"
        @input="onInput('camera', $event)"
        @blur="commit('camera')"
        @keydown.enter.prevent="commit('camera')"
      />
    </td>

    <td>
      <input
        class="cell-input center"
        list="motion-options"
        :aria-label="`第 ${shot.shot_id} 镜运镜`"
        :value="val('motion')"
        @input="onInput('motion', $event)"
        @blur="commit('motion')"
        @keydown.enter.prevent="commit('motion')"
      />
    </td>

    <td class="desc">
      <div v-if="shot.character_refs?.length" class="refs">
        <span v-for="r in shot.character_refs" :key="r" class="tag">{{ r }}</span>
      </div>

      <div class="line">
        <span class="k">衔接</span>
        <select
          class="cell-input"
          :aria-label="`第 ${shot.shot_id} 镜与上一镜的衔接`"
          :value="val('transition')"
          @change="onInput('transition', $event)"
          @blur="commit('transition')"
        >
          <option value="cut">cut · 独立镜头</option>
          <option value="continue">continue · 承接上镜尾帧</option>
        </select>
      </div>

      <div class="line">
        <span class="k">台词</span>
        <input
          class="cell-input"
          placeholder="无"
          :aria-label="`第 ${shot.shot_id} 镜台词`"
          :value="val('dialogue')"
          @input="onInput('dialogue', $event)"
          @blur="commit('dialogue')"
          @keydown.enter.prevent="commit('dialogue')"
        />
      </div>

      <template v-if="showPrompts">
        <div class="line col">
          <span class="k promptk">图片提示词 · 出首帧图</span>
          <textarea
            class="cell-area mono"
            rows="4"
            :aria-label="`第 ${shot.shot_id} 镜图片提示词 · 出首帧图`"
            :value="val('visual_prompt')"
            @input="onInput('visual_prompt', $event)"
            @blur="commit('visual_prompt')"
          ></textarea>
        </div>
        <div class="line col">
          <span class="k promptk">视频提示词 · 驱动画面</span>
          <textarea
            class="cell-area mono"
            rows="3"
            :aria-label="`第 ${shot.shot_id} 镜视频提示词 · 驱动画面`"
            :value="val('video_prompt')"
            @input="onInput('video_prompt', $event)"
            @blur="commit('video_prompt')"
          ></textarea>
        </div>
        <div class="line col">
          <span class="k promptk">声音设计 · 随视频生成</span>
          <textarea
            class="cell-area"
            rows="2"
            :aria-label="`第 ${shot.shot_id} 镜声音设计`"
            :value="val('audio')"
            @input="onInput('audio', $event)"
            @blur="commit('audio')"
          ></textarea>
        </div>
      </template>

      <p v-if="shot.stale" class="stalehint">提示词已改，当前画面还是旧的</p>
    </td>

    <td class="ops">
      <button
        type="button"
        :disabled="busy"
        :aria-label="`${busy ? '正在生成' : shot.image_path ? '重新生成' : '生成'}第 ${shot.shot_id} 镜图片`"
        :aria-busy="busy"
        title="使用当前提示词生成图片"
        @click="emit('regen', { shot, regenPrompt: false })"
      >
        <span v-if="busy" class="spin" aria-hidden="true"></span>{{ busy ? '图片生成中' : shot.image_path ? '重新生成图片' : '生成图片' }}
      </button>
      <button
        type="button"
        :disabled="busy"
        :aria-label="`改写第 ${shot.shot_id} 镜提示词并生成图片`"
        title="改写提示词并生成图片"
        @click="emit('regen', { shot, regenPrompt: true })"
      >改写提示词并出图</button>
      <button
        type="button"
        class="vidbtn"
        :disabled="busy || videoBusy || !shot.image_path"
        :aria-label="`${videoBusy ? '正在生成' : '生成'}第 ${shot.shot_id} 镜视频${!shot.image_path ? '，需先生成图片' : ''}`"
        :aria-busy="videoBusy"
        :title="!shot.image_path ? '请先生成图片' : '使用当前图片和视频提示词生成视频'"
        @click="emit('video', shot)"
      >
        <span v-if="videoBusy" class="spin" aria-hidden="true"></span>{{ videoBusy ? '视频生成中' : '生成视频' }}
      </button>
    </td>
  </tr>
</template>

<!--
  景别/运镜的可选值由 ShotBoard 以单个 datalist 提供（id 必须全页唯一，
  放在每一行里会产生 N 个同名 datalist）。取值约束来自 agents.py 的分镜 System Prompt。
-->

<style scoped>
td { border-bottom: 1px solid var(--line); padding: 16px 10px; vertical-align: top; background: var(--surface); color: var(--fg); }
tr:hover td { background: #fafaf7; }
tr.stale td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
.num { width: 52px; text-align: center; }
.id { color: var(--fg-2); font-size: 13px; }
.pic { width: 168px; }
.thumb { padding: 0; border: 1px solid var(--line); border-radius: var(--r-sm); overflow: hidden; display: block; width: 100%; position: relative; background: var(--surface-2); cursor: zoom-in; }
.thumb img { width: 100%; display: block; aspect-ratio: 16 / 9; object-fit: cover; }
.thumb .zoom { position: absolute; right: 6px; bottom: 6px; background: var(--surface); color: var(--fg); border: 1px solid var(--line); border-radius: var(--r-xs); font-size: 11px; padding: 3px 7px; opacity: 0; transition: opacity 0.16s var(--ease); }
.thumb:hover .zoom, .thumb:focus-visible .zoom { opacity: 1; }
.thumb:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.noimg { aspect-ratio: 16 / 9; border: 1px dashed var(--line); border-radius: var(--r-sm); display: flex; align-items: center; justify-content: center; font-size: 12px; color: var(--fg-2); background: var(--surface-2); }
.vid { width: 100%; margin-top: 8px; display: block; border: 1px solid var(--line); border-radius: var(--r-sm); background: #161b18; }
.vidwait { margin-top: 8px; color: var(--mint); }
.dur { width: 74px; }
.cell-input, .cell-area { box-sizing: border-box; width: 100%; min-width: 0; color: var(--fg); background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-xs); }
.cell-input { min-height: 36px; padding: 7px; font-size: 12.5px; }
.cell-input:hover, .cell-area:hover { border-color: #c8cec2; }
.cell-input:focus, .cell-area:focus { background: var(--surface); border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }
.center { text-align: center; }
.cell-input::placeholder { color: var(--fg-3); }
.cell-area {
  padding: 9px 10px;
  font-size: 12.5px;
  line-height: 1.7;
  resize: vertical;
  /* 保留内容自适应高度，不支持 field-sizing 时回退到 rows。 */
  field-sizing: content;
  min-height: 72px;
  max-height: 260px;
}
.desc { min-width: 320px; }
.refs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.refs .tag { white-space: normal; overflow-wrap: anywhere; }
.line { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.line.col { flex-direction: column; align-items: stretch; gap: 7px; }
.k { flex: none; font-size: 12px; color: var(--fg-2); width: 40px; }
.line.col .k { width: auto; }
.promptk { font-weight: 500; }
.mono { font-family: var(--font-mono); font-size: 12px; }
.stalehint { margin: 10px 0 0; font-size: 12px; color: var(--accent); }
.ops { width: 156px; }
.ops button { display: block; width: 100%; min-height: 38px; margin-bottom: 8px; font-size: 12px; padding: 8px; text-align: center; color: var(--fg-2); background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-sm); white-space: normal; }
.ops button:hover:not(:disabled) { color: var(--fg); background: var(--surface-2); border-color: #c8cec2; }
.ops button.vidbtn { color: var(--mint); background: var(--surface-2); }
.ops button.vidbtn:hover:not(:disabled) { color: var(--mint); border-color: var(--mint); }
.ops button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ops button:disabled { opacity: 1; color: var(--fg-3); background: var(--surface-2); cursor: not-allowed; }
.ops button:last-child { margin-bottom: 0; }
.ops .spin { width: 12px; height: 12px; border-width: 1.5px; margin-right: 5px; }

@media (hover: none) { .thumb .zoom { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .thumb .zoom { transition: none; } }
</style>

<script setup>
import { ref } from 'vue'
import { api } from '../api'

// 画廊视图的一张卡。画面优先，元数据与编辑退居其次。
// 与表格行共用同一套「本地草稿 + 失焦提交」的编辑策略。

const props = defineProps({
  shot: { type: Object, required: true },
  taskId: { type: String, required: true },
  busy: { type: Boolean, default: false },
  videoBusy: { type: Boolean, default: false },
  showPrompts: { type: Boolean, default: false },
})

const emit = defineEmits(['patch', 'regen', 'video', 'open'])

const local = ref({})
const editing = ref(false)

function val(field) {
  return field in local.value ? local.value[field] : (props.shot[field] ?? '')
}

function onInput(field, event) {
  local.value = { ...local.value, [field]: event.target.value }
}

function commit(field) {
  if (!(field in local.value)) return
  const value = local.value[field]
  const next = { ...local.value }
  delete next[field]
  local.value = next
  if (value === props.shot[field]) return
  emit('patch', { shotId: props.shot.shot_id, patch: { [field]: value } })
}
</script>

<template>
  <article class="card" :class="{ stale: shot.stale }">
    <button v-if="shot.image_path" type="button" class="pic" :aria-label="`放大第 ${shot.shot_id} 镜图片`" @click="emit('open', shot.shot_id)">
      <img :src="api.imageUrl(taskId, shot.shot_id, shot.version)" :alt="`第 ${shot.shot_id} 镜`" />
      <span class="hud mono">#{{ String(shot.shot_id).padStart(2, '0') }}</span>
      <span class="zoom">点击放大</span>
    </button>
    <div v-else class="pic empty">
      <span class="mono">#{{ String(shot.shot_id).padStart(2, '0') }}</span>
      <span>未出图</span>
    </div>

    <!-- 生成的视频挂在图片正下方 -->
    <video
      v-if="shot.video_path"
      class="vid"
      :src="api.videoUrl(taskId, shot.shot_id, shot.version)"
      :aria-label="`第 ${shot.shot_id} 镜视频预览`"
      controls
      preload="metadata"
      playsinline
    ></video>
    <div v-else-if="videoBusy" class="pic empty vidwait">
      <span class="spin"></span>
      <span>视频生成中…</span>
    </div>

    <div class="meta">
      <span class="tag">{{ shot.camera || '—' }}</span>
      <span class="tag">{{ shot.motion || '—' }}</span>
      <span class="tag mono">{{ shot.duration }}s</span>
      <span v-if="shot.transition === 'continue'" class="tag accent">承接上镜</span>
      <span v-if="shot.character_refs?.length" class="tag accent">
        {{ shot.character_refs.join(' / ') }}
      </span>
      <span v-if="shot.stale" class="tag warn">图待更新</span>
    </div>

    <p v-if="shot.dialogue" class="dia">
      <span class="k">台词</span>{{ shot.dialogue }}
    </p>

    <template v-if="showPrompts">
      <label class="promptblock">
        <span class="plabel">图片提示词 · 出首帧图</span>
        <textarea
          class="parea mono"
          rows="4"
          :value="val('visual_prompt')"
          @input="onInput('visual_prompt', $event)"
          @blur="commit('visual_prompt')"
        ></textarea>
      </label>
      <label class="promptblock">
        <span class="plabel">视频提示词 · 驱动画面</span>
        <textarea
          class="parea mono"
          rows="3"
          :value="val('video_prompt')"
          @input="onInput('video_prompt', $event)"
          @blur="commit('video_prompt')"
        ></textarea>
      </label>

      <label class="promptblock">
        <span class="plabel">声音设计 · 随视频生成（环境音/音色，正向描述）</span>
        <textarea
          class="parea"
          rows="2"
          :value="val('audio')"
          @input="onInput('audio', $event)"
          @blur="commit('audio')"
        ></textarea>
      </label>
    </template>

    <div class="ops">
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
    </div>
  </article>
</template>

<style scoped>
.card {
  min-width: 0;
  background: var(--surface);
  color: var(--fg);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: border-color 0.18s var(--ease), box-shadow 0.18s var(--ease);
}
.card:hover { border-color: #c8cec2; box-shadow: 0 6px 20px rgba(38, 41, 37, 0.05); }
.card.stale { border-color: var(--accent); }
.pic {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  position: relative;
  background: var(--surface-2);
  cursor: zoom-in;
}
.pic img { width: 100%; display: block; aspect-ratio: 16 / 9; object-fit: cover; }
.pic.empty { aspect-ratio: 16 / 9; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--fg-2); font-size: 12px; cursor: default; }
.vid { width: 100%; display: block; background: #161b18; border-bottom: 1px solid var(--line); }
.pic.vidwait { color: var(--mint); }
.vidwait .spin { width: 14px; height: 14px; border-width: 1.5px; }
.hud { position: absolute; top: 12px; left: 12px; background: var(--surface); color: var(--fg); padding: 4px 9px; border: 1px solid var(--line); border-radius: var(--r-xs); font-size: 11px; }
.zoom { position: absolute; right: 12px; bottom: 12px; padding: 6px 10px; font-size: 12px; color: var(--fg); background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-xs); opacity: 0; transition: opacity 0.18s var(--ease); }
.pic:hover .zoom, .pic:focus-visible .zoom { opacity: 1; }
.pic:focus-visible { outline: 3px solid var(--accent); outline-offset: -3px; }
.meta { display: flex; flex-wrap: wrap; gap: 6px; padding: 16px 16px 0; }
.meta .tag { max-width: 100%; white-space: normal; overflow-wrap: anywhere; }
.meta .tag.warn { color: var(--accent); background: var(--surface-2); border-color: var(--line); }
.dia { margin: 12px 16px 0; font-size: 13px; line-height: 1.7; color: var(--fg-2); display: flex; gap: 8px; overflow-wrap: anywhere; }
.k { color: var(--fg-2); font-size: 12px; flex: none; }
.promptblock { display: flex; flex-direction: column; gap: 7px; padding: 14px 16px 0; }
.plabel { font-size: 12px; font-weight: 500; color: var(--fg-2); letter-spacing: 0.1px; }
.parea {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--fg);
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  resize: vertical;
  /* 保留自适应内容高度，不支持时回退到 rows。 */
  field-sizing: content;
  min-height: 72px;
  max-height: 240px;
}
.parea:hover { border-color: #c8cec2; }
.parea:focus { background: var(--surface); border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }
.ops { display: flex; flex-wrap: wrap; gap: 8px; padding: 16px; margin-top: auto; }
.ops button { flex: 1 1 45%; min-width: 0; min-height: 38px; font-size: 12px; padding: 8px; color: var(--fg-2); background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-sm); white-space: normal; }
.ops button:hover:not(:disabled) { color: var(--fg); background: var(--surface-2); border-color: #c8cec2; }
.ops button.vidbtn { flex-basis: 100%; color: var(--mint); background: var(--surface-2); }
.ops button.vidbtn:hover:not(:disabled) { color: var(--mint); border-color: var(--mint); }
.ops button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ops button:disabled { opacity: 1; color: var(--fg-3); background: var(--surface-2); cursor: not-allowed; }
.ops .spin { width: 12px; height: 12px; border-width: 1.5px; margin-right: 5px; }

@media (hover: none) { .zoom { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .card, .zoom { transition: none; } }
</style>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { api } from '../api'

// 全局设定面板。
// 角色锚点必须显出来：它是跨镜头保持人物一致的唯一手段，
// 用户看不懂画面为什么"长得不一样"时，答案就在这几行里。
// 阶段一会为每个角色生成定妆照：点铅笔直接改名字和锚点提示词，
// 不满意再点旁边的刷新按钮重新生成定妆照。
const props = defineProps({
  project: { type: Object, required: true },
  taskId: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  charBusy: { type: String, default: '' },
  charSaving: { type: Number, default: -1 },
  shots: { type: Array, default: () => [] },
  stats: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['reroll-character', 'update-character'])

const totalSec = computed(() =>
  props.shots.reduce((sum, s) => sum + (Number(s.duration) || 0), 0),
)

const rendered = computed(() => props.shots.filter((s) => s.image_path).length)

function fmtSec(sec) {
  const s = Math.round(sec)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

// ---------------------------------------------------------------- 角色编辑
// 同一时刻只编辑一个角色；保存走乐观关闭——提交后立即收起，
// 失败由上层 toast 提示，卡片继续显示服务端的原值。
const editing = ref(-1)
const draftName = ref('')
const draftAnchor = ref('')
// v-for 里的模板 ref 会被收进数组，直接用函数 ref 拿单个元素
let editNameEl = null
let editAnchorEl = null

function startEdit(i, c) {
  editing.value = i
  draftName.value = c.name || ''
  draftAnchor.value = c.anchor || ''
  nextTick(() => {
    const el = editAnchorEl || editNameEl
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  })
}

function cancelEdit() {
  editing.value = -1
}

function saveEdit() {
  const i = editing.value
  const c = props.project.characters?.[i]
  if (!c || props.charSaving === i) return
  const name = draftName.value.trim()
  if (!name) return
  const anchor = draftAnchor.value.trim()
  editing.value = -1
  if (name === c.name && anchor === (c.anchor || '').trim()) return
  emit('update-character', { index: i, name, anchor })
}
</script>

<template>
  <section class="brief">
    <header class="head">
      <div class="left">
        <h2>{{ project.title }}</h2>
        <p class="logline">{{ project.logline }}</p>
      </div>
      <span class="tag accent mono">{{ project.aspect_ratio }}</span>
    </header>

    <p class="style">
      <span class="k">视觉风格</span>
      {{ project.style }}
    </p>

    <div v-if="project.characters?.length" class="chars">
      <div v-for="(c, i) in project.characters" :key="i" class="char">
        <div class="charmedia">
          <img
            v-if="c.image_path"
            :src="api.characterImageUrl(taskId, c.image_path)"
            :alt="`${c.name} 定妆照`"
          />
          <div v-else class="cempty">
            {{ charBusy === c.name ? '生成中…' : '待生成' }}
          </div>
          <div class="charops">
            <button
              type="button"
              class="cbtn"
              :disabled="busy || charSaving === i"
              :aria-busy="charSaving === i"
              :aria-label="`编辑${c.name}的名字与角色提示词`"
              title="编辑名字与角色提示词"
              @click="startEdit(i, c)"
            >
              <span v-if="charSaving === i" class="spin" aria-hidden="true"></span>
              <svg v-else viewBox="0 0 14 14" aria-hidden="true">
                <path d="M9.8 1.9l2.3 2.3-7.4 7.4-3 .7.7-3 7.4-7.4z"
                      fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
              </svg>
            </button>
            <button
              type="button"
              class="cbtn"
              :disabled="busy || charBusy === c.name || !c.anchor"
              :aria-label="`${charBusy === c.name ? '正在生成' : c.image_path ? '重新生成' : '生成'}${c.name}的定妆照`"
              :aria-busy="charBusy === c.name"
              :title="!c.anchor ? '需先填写角色锚点' : c.image_path ? '重新生成定妆照' : '生成定妆照'"
              @click="emit('reroll-character', { index: i, name: c.name })"
            >
              <span v-if="charBusy === c.name" class="spin" aria-hidden="true"></span>
              <svg v-else viewBox="0 0 24 24" aria-hidden="true">
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                <path d="M21 3.6v6.4h-6.4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </div>
        </div>
        <template v-if="editing === i">
          <input
            :ref="(el) => (editNameEl = el)"
            v-model="draftName"
            class="ename"
            maxlength="40"
            :aria-label="`${c.name} 的角色名`"
            @keydown.enter.prevent="saveEdit"
            @keydown.esc.prevent="cancelEdit"
          />
          <textarea
            :ref="(el) => (editAnchorEl = el)"
            v-model="draftAnchor"
            class="eanchor"
            rows="4"
            maxlength="2000"
            :aria-label="`${c.name} 的角色锚点提示词`"
            placeholder="外形、发型、服装、气质…这段锚点会用于定妆照与分镜提示词，保持人物前后一致"
            @keydown.enter.ctrl.prevent="saveEdit"
            @keydown.enter.meta.prevent="saveEdit"
            @keydown.esc.prevent="cancelEdit"
          ></textarea>
          <div class="eops">
            <span class="ehint">锚点改了记得重新生成定妆照</span>
            <button type="button" class="ecancel" @click="cancelEdit">取消</button>
            <button type="button" class="esave" :disabled="!draftName.trim() || charSaving === i" @click="saveEdit">保存</button>
          </div>
        </template>
        <template v-else>
          <span class="cname">
            {{ c.name }}
            <i v-if="c.stale" class="cwarn" title="角色提示词已修改，重新生成定妆照后将与新形象一致">图待更新</i>
          </span>
          <span class="canchor">{{ c.anchor }}</span>
        </template>
      </div>
    </div>

    <div v-if="shots.length" class="stats">
      <span class="stat"><b class="mono">{{ shots.length }}</b>个分镜</span>
      <span class="sep"></span>
      <span class="stat"><b class="mono">{{ fmtSec(totalSec) }}</b>总时长</span>
      <span class="sep"></span>
      <span class="stat"><b class="mono">{{ rendered }}/{{ shots.length }}</b>已出图</span>
      <template v-if="stats.api || stats.cache">
        <span class="sep"></span>
        <span class="stat mint"><b class="mono">{{ stats.api }}</b>新生成</span>
        <span class="stat mint"><b class="mono">{{ stats.cache }}</b>缓存命中</span>
      </template>
    </div>
  </section>
</template>

<style scoped>
.brief {
  min-width: 0;
  background: var(--surface);
  color: var(--fg);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.head { display: flex; align-items: flex-start; gap: 16px; }
.left { min-width: 0; flex: 1; }
.head > .tag { flex: none; }
h2 { margin: 0 0 8px; font-size: 21px; font-weight: 600; letter-spacing: -0.4px; overflow-wrap: anywhere; }
.logline { margin: 0; font-size: 13px; line-height: 1.8; color: var(--fg-2); overflow-wrap: anywhere; }

.style {
  margin: 0;
  padding: 12px 14px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-size: 13px;
  line-height: 1.7;
  color: var(--fg-2);
  display: flex;
  gap: 12px;
  align-items: baseline;
  overflow-wrap: anywhere;
}
.k { flex: none; font-size: 12px; font-weight: 600; color: var(--fg); }

.chars {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 240px), 300px));
  align-items: start;
  gap: 12px;
}
.char {
  min-width: 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 12px;
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 12px;
}
.charmedia { position: relative; width: 96px; height: 96px; }
.charmedia img { width: 100%; height: 100%; display: block; object-fit: cover; border-radius: var(--r-sm); }
.cempty {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  padding-bottom: 20px;
  background: var(--surface-2);
  border: 1px dashed var(--line);
  border-radius: var(--r-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--fg-2);
}
.charops {
  position: absolute;
  bottom: 4px;
  right: 4px;
  display: flex;
  gap: 4px;
}
.cbtn {
  width: 32px;
  height: 32px;
  min-height: 0;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--fg);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  box-shadow: 0 2px 6px rgba(38, 41, 37, 0.08);
}
.cbtn svg { width: 14px; height: 14px; }
.cbtn:hover:not(:disabled) { color: var(--accent); background: var(--surface-2); border-color: var(--accent); }
.cbtn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cbtn:disabled { opacity: 1; color: var(--fg-3); cursor: not-allowed; }
.cbtn .spin { width: 13px; height: 13px; border-width: 1.5px; }
.ename { grid-column: 1 / -1; font-weight: 600; background: #fdfdfa; }
.eanchor {
  grid-column: 1 / -1;
  font-size: 12px;
  line-height: 1.7;
  min-height: 76px;
  background: #fdfdfa;
}
.eops {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 8px;
}
.ehint { flex: 1; min-width: 0; font-size: 10px; color: var(--fg-3); }
.ecancel { font-size: 12px; padding: 6px 12px; }
.esave { font-size: 12px; padding: 6px 16px; }
.cname { align-self: center; font-size: 14px; font-weight: 600; color: var(--fg); overflow-wrap: anywhere; }
.cwarn {
  font-style: normal;
  margin-left: 6px;
  padding: 2px 7px;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 500;
  color: var(--danger);
  background: var(--danger-dim);
  vertical-align: 1px;
  white-space: nowrap;
}
.canchor { grid-column: 1 / -1; font-size: 12px; color: var(--fg-2); line-height: 1.7; white-space: pre-line; overflow-wrap: anywhere; }

.stats { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 16px; padding-top: 18px; border-top: 1px solid var(--line); }
.stat { font-size: 12px; color: var(--fg-2); display: flex; align-items: baseline; gap: 6px; }
.stat b { font-size: 14px; color: var(--fg); font-weight: 600; font-family: var(--font-mono); }
.stat.mint b { color: var(--mint); }
.sep { width: 1px; height: 12px; background: var(--line); }

@media (max-width: 480px) {
  .brief { padding: 24px 16px; }
  h2 { font-size: 19px; }
  .style { flex-direction: column; gap: 4px; }
  .stats { gap: 10px 14px; }
  .stats .sep { display: none; }
}
</style>

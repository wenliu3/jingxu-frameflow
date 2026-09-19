<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'

// 一块 = 一条 10 秒视频。卡片自上而下：媒体（首帧图/视频/尾帧）→
// 剧情梗概与节拍时间轴 → 选用的资产 → 提示词 → 操作。
// 剧情编辑走「展开面板 + 保存提交」，提示词走「失焦即提交」（与 ShotCard 同策略）。
const props = defineProps({
  block: { type: Object, required: true },
  taskId: { type: String, required: true },
  project: { type: Object, default: () => null },
  busy: { type: Boolean, default: false },        // 出图中
  promptBusy: { type: Boolean, default: false },  // AI 写提示词中
  videoBusy: { type: Boolean, default: false },   // 视频生成中
  showPrompts: { type: Boolean, default: true },
  isFirst: { type: Boolean, default: false },
})

const emit = defineEmits(['patch', 'del', 'prompt', 'image', 'video', 'open-image', 'open-frame'])

// ---------------------------------------------------------------- 展示数据
const CAMERAS = ['远景', '全景', '中景', '近景', '特写']
const MOTIONS = [
  'Static Shot', 'Push In', 'Pull Out', 'Zoom In', 'Zoom Out',
  'Pan Left', 'Pan Right', 'Tilt Up', 'Tilt Down',
  'Truck Left', 'Truck Right', 'Pedestal Up', 'Pedestal Down',
  'Arc Shot', 'Tracking Shot', 'POV', 'Shake Slightly',
]

const characterNames = computed(() =>
  (props.project?.characters || []).map((c) => c.name).filter(Boolean),
)
const propNames = computed(() =>
  (props.project?.assets || []).filter((a) => a.kind === 'prop').map((a) => a.name),
)
const sceneNames = computed(() =>
  (props.project?.assets || []).filter((a) => a.kind === 'scene').map((a) => a.name),
)

function fmtRange(b) {
  const s = Number(b.start) || 0
  const e = Number(b.end) || 0
  return `${s.toFixed(0)}–${e.toFixed(0)}s`
}

// ---------------------------------------------------------------- 提示词编辑
const local = ref({})
const promptFields = ['visual_prompt', 'video_prompt', 'audio']

function val(field) {
  return field in local.value ? local.value[field] : (props.block[field] ?? '')
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
  if (value === (props.block[field] ?? '')) return
  emit('patch', { blockId: props.block.block_id, patch: { [field]: value } })
}
watch(
  () => props.block,
  () => {
    local.value = {}   // 服务端回写后丢弃本地草稿，以服务端为准
  },
)

// ---------------------------------------------------------------- 剧情编辑面板
const editing = ref(false)
const draft = ref(null)

// 手动添加的空块（没有 summary）直接进编辑态，少一次点击
if (!props.block.summary) startEdit()

function startEdit() {
  draft.value = {
    summary: props.block.summary || '',
    characters: [...(props.block.characters || [])],
    props: [...(props.block.props || [])],
    scene: props.block.scene || '',
    dialogue: props.block.dialogue || '',
    camera: props.block.camera || '',
    motion: props.block.motion || '',
    avoid: props.block.avoid || '',
    continue_last: Boolean(props.block.continue_last),
    beats: (props.block.beats || []).map((b) => ({ ...b })),
  }
  if (!draft.value.beats.length) draft.value.beats = emptyBeats()
  editing.value = true
}

// 默认节拍：把 10 秒切成三段，用户在行内改就行
function emptyBeats() {
  return [
    { start: 0, end: 3, action: '', camera: '' },
    { start: 3, end: 6, action: '', camera: '' },
    { start: 6, end: 10, action: '', camera: '' },
  ]
}

function addBeat() {
  const beats = draft.value.beats
  const last = beats[beats.length - 1]
  const start = last ? Number(last.end) || 0 : 0
  beats.push({ start, end: Math.min(10, start + 3), action: '', camera: '' })
}

function removeBeat(i) {
  draft.value.beats.splice(i, 1)
}

function toggleIn(list, name) {
  const i = list.indexOf(name)
  if (i >= 0) list.splice(i, 1)
  else list.push(name)
}

function cancelEdit() {
  editing.value = false
  draft.value = null
}

function saveEdit() {
  if (!draft.value) return
  const patch = {}
  for (const key of ['summary', 'scene', 'dialogue', 'camera', 'motion', 'avoid']) {
    patch[key] = String(draft.value[key] || '').trim()
  }
  patch.characters = [...draft.value.characters]
  patch.props = [...draft.value.props]
  patch.continue_last = draft.value.continue_last && !props.isFirst
  patch.beats = draft.value.beats
    .map((b) => ({
      start: Number(b.start) || 0,
      end: Number(b.end) || 0,
      action: String(b.action || '').trim(),
      camera: String(b.camera || '').trim(),
    }))
    .filter((b) => b.action || b.camera)
  editing.value = false
  draft.value = null
  emit('patch', { blockId: props.block.block_id, patch })
}

// ---------------------------------------------------------------- 操作
const summaryEditing = ref(false)
const summaryDraft = ref('')

function startSummaryEdit() {
  summaryDraft.value = props.block.summary || ''
  summaryEditing.value = true
}

function saveSummary() {
  summaryEditing.value = false
  const v = summaryDraft.value.trim()
  if (v === (props.block.summary || '')) return
  emit('patch', { blockId: props.block.block_id, patch: { summary: v } })
}
</script>

<template>
  <article class="card" :class="{ stale: block.prompt_stale || block.image_stale }">
    <!-- 媒体区：视频 > 首帧图 > 占位 -->
    <div class="media">
      <button
        v-if="block.image_path && !block.video_path"
        type="button"
        class="frame"
        :aria-label="`放大第 ${block.block_id} 块首帧图`"
        @click="emit('open-image', block)"
      >
        <img :src="api.blockImageUrl(taskId, block.block_id, block.version)" :alt="`第 ${block.block_id} 块首帧图`" />
        <span class="badge mono">首帧</span>
        <span v-if="block.image_stale" class="badge warn">图待更新</span>
      </button>
      <video
        v-else-if="block.video_path"
        class="vid"
        :src="api.blockVideoUrl(taskId, block.block_id, block.version)"
        :aria-label="`第 ${block.block_id} 块视频`"
        controls
        preload="metadata"
        playsinline
      ></video>
      <div v-else class="frame empty">
        <span class="mono">#{{ String(block.block_id).padStart(2, '0') }}</span>
        <span>{{ busy ? '首帧图生成中…' : '还没有首帧图' }}</span>
      </div>

      <div v-if="block.last_frame" class="tail">
        <button
          type="button"
          class="tailbtn"
          title="本块最后一帧 —— 下一块「承接上一块」时从这一帧接着演"
          @click="emit('open-frame', block)"
        >
          <img :src="api.blockLastFrameUrl(taskId, block.block_id, block.version)" alt="本块尾帧" />
          <span class="badge mono">尾帧</span>
        </button>
      </div>
      <div v-else-if="videoBusy" class="tail tailwait">
        <span class="spin"></span>
        <span>视频生成中<br />完成后自动提取尾帧</span>
      </div>
    </div>

    <!-- 标题行 -->
    <div class="head">
      <span class="num mono">#{{ String(block.block_id).padStart(2, '0') }}</span>
      <template v-if="summaryEditing">
        <input
          v-model="summaryDraft"
          class="suminput"
          maxlength="500"
          :aria-label="`第 ${block.block_id} 块的剧情梗概`"
          @keydown.enter.prevent="saveSummary"
          @keydown.esc.prevent="summaryEditing = false"
          @blur="saveSummary"
        />
      </template>
      <template v-else>
        <button
          type="button"
          class="summary"
          :title="block.summary || '点击填写这一块讲什么'"
          @click="startSummaryEdit"
        >{{ block.summary || '点击填写这一块讲什么…' }}</button>
      </template>
      <span class="tag mono">10s</span>
      <span v-if="block.continue_last" class="tag accent" title="本块从上一块的最后一帧接着演">承接上块</span>
      <span v-if="block.prompt_stale" class="tag warn" title="剧情改过了，提示词还是旧的">提示词待更新</span>
      <button type="button" class="iconbtn del" :aria-label="`删除第 ${block.block_id} 块`" title="删除这一块" @click="emit('del', block)">
        <svg viewBox="0 0 14 14" aria-hidden="true">
          <path d="M2.5 3.5h9M5.5 3.5v-1h3v1M4 3.5l.6 8h4.8l.6-8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>

    <!-- 节拍时间轴（只读展示） -->
    <ol v-if="block.beats?.length && !editing" class="beats" :aria-label="`第 ${block.block_id} 块的分秒节拍`">
      <li v-for="(b, i) in block.beats" :key="i">
        <span class="btime mono">{{ fmtRange(b) }}</span>
        <span class="baction">{{ b.action }}</span>
        <span v-if="b.camera" class="bcam">{{ b.camera }}</span>
      </li>
    </ol>

    <!-- 台词 -->
    <p v-if="block.dialogue && !editing" class="dia"><span class="k">台词</span>{{ block.dialogue }}</p>
    <p v-if="block.avoid && !editing" class="dia avoid"><span class="k">不做</span>{{ block.avoid }}</p>

    <!-- 资产选用（只读展示） -->
    <div v-if="!editing" class="refs">
      <span v-if="block.scene" class="tag scene" title="所在场景">◎ {{ block.scene }}</span>
      <span v-for="c in block.characters" :key="'c' + c" class="tag person">👤 {{ c }}</span>
      <span v-for="p in block.props" :key="'p' + p" class="tag prop">▣ {{ p }}</span>
      <button type="button" class="editlink" @click="startEdit">编辑剧情与选用</button>
    </div>

    <!-- 剧情编辑面板 -->
    <div v-if="editing && draft" class="editor">
      <label class="frow">
        <span>剧情梗概</span>
        <input v-model="draft.summary" maxlength="500" placeholder="这一块讲什么（一句话）" />
      </label>

      <div class="frow">
        <span>出场角色</span>
        <div class="chips">
          <button
            v-for="n in characterNames"
            :key="n"
            type="button"
            class="chip"
            :class="{ on: draft.characters.includes(n) }"
            :aria-pressed="draft.characters.includes(n)"
            @click="toggleIn(draft.characters, n)"
          >{{ n }}</button>
          <span v-if="!characterNames.length" class="nonehint">项目还没有角色</span>
        </div>
      </div>

      <div class="frow">
        <span>出现的道具</span>
        <div class="chips">
          <button
            v-for="n in propNames"
            :key="n"
            type="button"
            class="chip"
            :class="{ on: draft.props.includes(n) }"
            :aria-pressed="draft.props.includes(n)"
            @click="toggleIn(draft.props, n)"
          >{{ n }}</button>
          <span v-if="!propNames.length" class="nonehint">项目还没有道具素材</span>
        </div>
      </div>

      <label class="frow">
        <span>所在场景</span>
        <select v-model="draft.scene">
          <option value="">（无明确环境）</option>
          <option v-for="n in sceneNames" :key="n" :value="n">{{ n }}</option>
        </select>
      </label>

      <div class="frow beats-edit">
        <span>分秒节拍 · 铺满 0–10s</span>
        <div v-for="(b, i) in draft.beats" :key="i" class="beatrow">
          <input v-model.number="b.start" type="number" min="0" max="10" step="0.5" class="bnum" :aria-label="`节拍${i + 1}开始秒`" />
          <span class="bdash">–</span>
          <input v-model.number="b.end" type="number" min="0" max="10" step="0.5" class="bnum" :aria-label="`节拍${i + 1}结束秒`" />
          <input v-model="b.action" class="baction-in" placeholder="这几秒谁在做什么" :aria-label="`节拍${i + 1}的动作`" />
          <input v-model="b.camera" class="bcam-in" placeholder="镜头（可空）" list="camera-options" :aria-label="`节拍${i + 1}的镜头`" />
          <button type="button" class="iconbtn" :aria-label="`删除节拍${i + 1}`" @click="removeBeat(i)">✕</button>
        </div>
        <button type="button" class="addbeat" @click="addBeat">＋ 加一拍</button>
      </div>

      <label class="frow">
        <span>台词 / 旁白</span>
        <textarea v-model="draft.dialogue" rows="2" placeholder="旁白：……（每行一条，说话人写行首）"></textarea>
      </label>

      <div class="frow two">
        <label>
          <span>主景别</span>
          <select v-model="draft.camera">
            <option value="">（未指定）</option>
            <option v-for="c in CAMERAS" :key="c" :value="c">{{ c }}</option>
          </select>
        </label>
        <label>
          <span>主运镜</span>
          <select v-model="draft.motion">
            <option value="">（未指定）</option>
            <option v-for="m in MOTIONS" :key="m" :value="m">{{ m }}</option>
          </select>
        </label>
      </div>

      <label class="frow">
        <span>不该干什么（画面禁忌）</span>
        <input v-model="draft.avoid" maxlength="1000" placeholder="如：不要切换场景，不要出现其他路人" />
      </label>

      <label v-if="!isFirst" class="frow checkline">
        <input v-model="draft.continue_last" type="checkbox" />
        <span>承接上一块的尾帧（一段戏 10 秒没演完时勾上，画面从上一块最后一帧接着演）</span>
      </label>

      <div class="eops">
        <button type="button" class="ecancel" @click="cancelEdit">取消</button>
        <button type="button" class="esave" @click="saveEdit">保存</button>
      </div>
    </div>

    <!-- 提示词 -->
    <template v-if="showPrompts && !editing">
      <label class="promptblock">
        <span class="plabel" title="给图像模型，产出本块第 0 秒的画面">图片提示词 · 首帧图</span>
        <textarea class="parea mono" rows="3" :value="val('visual_prompt')" @input="onInput('visual_prompt', $event)" @blur="commit('visual_prompt')"></textarea>
      </label>
      <label class="promptblock">
        <span class="plabel" title="H3 画面字段正文，英文写作，按秒交代节拍，台词保留原语言">视频提示词 · 英文（含分秒节拍）</span>
        <textarea class="parea mono" rows="4" :value="val('video_prompt')" @input="onInput('video_prompt', $event)" @blur="commit('video_prompt')"></textarea>
      </label>
      <label class="promptblock">
        <span class="plabel" title="H3 声音字段：只写画内环境音；台词在视频提示词里">环境音 · 英文</span>
        <textarea class="parea" rows="2" :value="val('audio')" @input="onInput('audio', $event)" @blur="commit('audio')"></textarea>
      </label>
    </template>

    <!-- 操作区 -->
    <div class="ops">
      <button
        type="button"
        :disabled="promptBusy || busy || videoBusy"
        :aria-busy="promptBusy"
        :title="block.video_prompt ? '按当前剧情重写提示词' : '由 AI 按剧情、角色锚点与音色写提示词'"
        @click="emit('prompt', block)"
      >
        <span v-if="promptBusy" class="spin" aria-hidden="true"></span>{{ promptBusy ? '提示词生成中' : block.video_prompt ? '重写提示词' : 'AI 写提示词' }}
      </button>
      <button
        type="button"
        :disabled="busy || promptBusy || videoBusy || !block.visual_prompt"
        :aria-busy="busy"
        :title="!block.visual_prompt ? '请先写好图片提示词' : '使用当前图片提示词生成第 0 秒画面'"
        @click="emit('image', block)"
      >
        <span v-if="busy" class="spin" aria-hidden="true"></span>{{ busy ? '出图中' : block.image_path ? '重新出首帧图' : '生成首帧图' }}
      </button>
      <button
        type="button"
        class="vidbtn"
        :disabled="busy || promptBusy || videoBusy"
        :aria-busy="videoBusy"
        title="生成这一块的 10 秒视频；完成后自动提取尾帧"
        @click="emit('video', block)"
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
}
.card.stale { border-color: #e5c9a8; }

/* 媒体区：视频铺满宽度，尾帧小图叠在右下角 */
.media { position: relative; background: #161b18; }
.frame {
  display: flex;
  width: 100%;
  aspect-ratio: 16 / 9;
  padding: 0;
  border: 0;
  border-radius: 0;
  position: relative;
  background: var(--surface-2);
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 8px;
  color: var(--fg-2);
  font-size: 12px;
  cursor: zoom-in;
}
.frame img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.frame.empty { cursor: default; color: var(--fg-3); }
.vid { width: 100%; display: block; aspect-ratio: 16 / 9; background: #161b18; }
.badge {
  position: absolute;
  top: 10px;
  left: 10px;
  padding: 3px 8px;
  font-size: 10px;
  background: #17221acc;
  color: #fff;
  border-radius: 6px;
  z-index: 1;
}
.badge.warn { left: auto; right: 10px; background: #b3542fd9; }
.tail { position: absolute; right: 10px; bottom: 10px; z-index: 2; }
.tailbtn { position: relative; padding: 0; border: 2px solid #ffffffb0; border-radius: 6px; overflow: hidden; width: 96px; box-shadow: 0 4px 14px #0008; cursor: zoom-in; }
.tailbtn img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; }
.tailbtn .badge { top: 4px; left: 4px; padding: 1px 6px; font-size: 9px; }
.tailwait {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #17221acc;
  color: #cfe0d4;
  font-size: 10px;
  border-radius: 8px;
  line-height: 1.6;
}
.tailwait .spin { width: 13px; height: 13px; border-width: 1.5px; }

/* 标题行 */
.head { display: flex; align-items: center; gap: 8px; padding: 12px 16px 0; }
.num { font-size: 13px; font-weight: 600; color: var(--accent); flex: none; }
.summary {
  flex: 1;
  min-width: 0;
  text-align: left;
  font-size: 14px;
  font-weight: 600;
  padding: 2px 4px;
  border: 1px dashed transparent;
  border-radius: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
  background: transparent;
}
.summary:hover { border-color: var(--line); background: var(--surface-2); }
.suminput { flex: 1; min-width: 0; font-size: 13px; font-weight: 600; background: #fdfdfa; }
.tag {
  flex: none;
  padding: 2px 8px;
  font-size: 11px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--fg-2);
  white-space: nowrap;
}
.tag.accent { color: var(--accent); border-color: var(--accent); background: var(--accent-dim); }
.tag.warn { color: #b3542f; border-color: #e5c9a8; background: #fdf6ec; }
.iconbtn {
  flex: none;
  width: 30px;
  height: 30px;
  min-height: 0;
  padding: 0;
  display: grid;
  place-items: center;
  color: var(--fg-3);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
}
.iconbtn svg { width: 14px; height: 14px; }
.iconbtn:hover { color: var(--danger); border-color: var(--line); background: var(--surface-2); }

/* 节拍时间轴 */
.beats { list-style: none; margin: 10px 16px 0; padding: 10px 12px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm); display: grid; gap: 6px; }
.beats li { display: flex; align-items: baseline; gap: 10px; font-size: 12px; line-height: 1.6; }
.btime { flex: none; color: var(--accent); font-size: 11px; min-width: 52px; }
.baction { color: var(--fg-2); min-width: 0; overflow-wrap: anywhere; }
.bcam { margin-left: auto; flex: none; font-size: 11px; color: var(--fg-3); }

.dia { margin: 10px 16px 0; font-size: 13px; line-height: 1.7; color: var(--fg-2); display: flex; gap: 8px; overflow-wrap: anywhere; white-space: pre-line; }
.dia.avoid { color: #a05a2c; }
.k { color: var(--fg-2); font-size: 12px; flex: none; font-weight: 600; }

/* 资产选用 */
.refs { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 16px 0; align-items: center; }
.refs .tag.person { color: var(--accent); border-color: var(--accent); }
.refs .tag.scene { color: #4b7a5a; border-color: #bcd6c4; }
.editlink { margin-left: auto; font-size: 11px; padding: 4px 8px; color: var(--fg-2); background: transparent; border: 0; text-decoration: underline; text-underline-offset: 3px; }
.editlink:hover { color: var(--accent); }

/* 编辑面板 */
.editor { display: grid; gap: 12px; padding: 14px 16px; margin-top: 12px; border-top: 1px solid var(--line); background: var(--surface-2); }
.frow { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--fg-2); }
.frow > span { font-size: 11px; font-weight: 600; color: var(--fg-2); }
.frow input[type="text"], .frow input:not([type]), .frow textarea, .frow select {
  font-size: 12px;
  background: #fdfdfa;
}
.frow.two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.frow.two label { display: flex; flex-direction: column; gap: 6px; }
.checkline { flex-direction: row; align-items: center; gap: 8px; }
.checkline input { width: 15px; height: 15px; accent-color: var(--accent); }
.checkline span { font-weight: 400; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 12px; padding: 4px 10px; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; color: var(--fg-2); }
.chip.on { color: var(--accent); border-color: var(--accent); background: var(--accent-dim); font-weight: 600; }
.nonehint { font-size: 11px; color: var(--fg-3); }
.beats-edit { gap: 8px; }
.beatrow { display: grid; grid-template-columns: 52px 12px 52px minmax(0, 1fr) 110px 30px; gap: 6px; align-items: center; }
.bnum { text-align: center; }
.bdash { text-align: center; color: var(--fg-3); }
.bcam-in { font-size: 11px; }
.addbeat { justify-self: start; font-size: 11px; padding: 5px 10px; color: var(--fg-2); }
.addbeat:hover { color: var(--accent); border-color: var(--accent); }
.eops { display: flex; justify-content: flex-end; gap: 8px; }
.ecancel { font-size: 12px; padding: 6px 12px; }
.esave { font-size: 12px; padding: 6px 16px; }

/* 提示词 */
.promptblock { display: flex; flex-direction: column; gap: 6px; padding: 12px 16px 0; }
.plabel { font-size: 12px; font-weight: 500; color: var(--fg-2); }
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
  field-sizing: content;
  min-height: 64px;
  max-height: 260px;
}
.parea:focus { background: var(--surface); border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }

/* 操作区 */
.ops { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 16px 16px; margin-top: auto; }
.ops button { flex: 1 1 30%; min-width: 0; min-height: 38px; font-size: 12px; padding: 8px; color: var(--fg-2); background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-sm); white-space: normal; }
.ops button:hover:not(:disabled) { color: var(--fg); background: var(--surface-2); border-color: #c8cec2; }
.ops button.vidbtn { color: var(--mint); background: var(--surface-2); }
.ops button.vidbtn:hover:not(:disabled) { color: var(--mint); border-color: var(--mint); }
.ops button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ops button:disabled { opacity: 1; color: var(--fg-3); background: var(--surface-2); cursor: not-allowed; }
.ops .spin { width: 12px; height: 12px; border-width: 1.5px; margin-right: 5px; }

@media (prefers-reduced-motion: reduce) { .card, .chip { transition: none; } }
</style>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
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
  assetBusy: { type: String, default: '' },
  voiceEpoch: { type: Number, default: 0 },
  shots: { type: Array, default: () => [] },
  blocks: { type: Array, default: () => [] },
  stats: { type: Object, default: () => ({}) },
})

const emit = defineEmits([
  'reroll-character', 'update-character', 'reroll-asset', 'reroll-voice',
  'add-character', 'delete-character',
  'add-asset', 'patch-asset', 'delete-asset',
])

// ---------------------------------------------------------------- TTS 音色
// 音色清单从后端拉（单一来源 tts.py 的音色池，**按当前音频后端返回**：
// edge 是 8 个，MiniMax 是 27 个），不在前端硬编码 —— 否则后端加了音色这边看不到，
// 两边还会慢慢漂开。
// 这是给 H3 的 Ref2VA 造参考音频用的，当前 I2VA/FL2VA 链路吃不到，
// 所以界面上要如实标明"它暂时不影响出片"，别让人以为生成了就该有变化。
const voices = ref([])

async function loadVoices() {
  try {
    voices.value = await api.listVoices()
  } catch {
    // 清单拉不到只影响下拉能不能换音色，不影响其他功能，静默降级
    voices.value = []
  }
}

onMounted(loadVoices)
// 服务配置保存后父组件会 +1：音频后端可能被换过，音色池就换了一批，得重拉
watch(() => props.voiceEpoch, loadVoices)

function voiceLabelOf(id) {
  const v = voices.value.find((x) => x.id === id)
  if (!v) return id || ''
  const g = v.gender === 'female' ? '女' : '男'
  return v.locale && v.locale !== '普通话' ? `${v.cn} · ${g} · ${v.locale}` : `${v.cn} · ${g}`
}

// 选完音色直接重合成 —— 一步到位，不用"选完还要再点生成"两次操作
function onPickVoice(e, index, name) {
  emit('reroll-voice', { index, name, voiceId: e.target.value })
}


const totalSec = computed(() =>
  props.blocks.length
    ? props.blocks.reduce((sum, b) => sum + (Number(b.duration) || 10), 0)
    : props.shots.reduce((sum, s) => sum + (Number(s.duration) || 0), 0),
)

const rendered = computed(() => props.shots.filter((s) => s.image_path).length)

function fmtSec(sec) {
  const s = Math.round(sec)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

// ---------------------------------------------------------------- 定妆照四视角
// 阶段一为每个角色出四个视角（正面/侧面/背面/全身）。主图一次只显示一张，
// 下面一排缩略图用来切换。
// 为什么要四个视角：切到 Ref2VA（全能参考）后，参考图要能覆盖面部特征、身体轮廓
// 和体型比例，单个正面半身像不足以让模型在侧向、背向构图里保持同一个人。
// 当前的 I2VA 出图链路只用得上正面那张——所以这排缩略图现在是给你核对形象用的。
const VIEW_LABELS = ['正面', '侧面', '背面', '全身']
// 按角色下标记录正在查看第几个视角
const viewAt = ref({})

function viewsOf(c) {
  // 兼容旧任务：只有单张 image_path 时退化成单元素数组，缩略图排自然不渲染
  if (Array.isArray(c.images) && c.images.length) return c.images
  return c.image_path ? [c.image_path] : []
}

function currentView(c, i) {
  const list = viewsOf(c)
  if (!list.length) return ''
  return list[Math.min(viewAt.value[i] ?? 0, list.length - 1)]
}

// ---------------------------------------------------------------- 角色编辑
// 同一时刻只编辑一个角色；保存走乐观关闭——提交后立即收起，
// 失败由上层 toast 提示，卡片继续显示服务端的原值。
const editing = ref(-1)
const draftName = ref('')
const draftAnchor = ref('')
const draftVoice = ref('')
// v-for 里的模板 ref 会被收进数组，直接用函数 ref 拿单个元素
let editNameEl = null
let editAnchorEl = null

function startEdit(i, c) {
  editing.value = i
  draftName.value = c.name || ''
  draftAnchor.value = c.anchor || ''
  draftVoice.value = c.voice || ''
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
  const voice = draftVoice.value.trim()
  editing.value = -1
  if (name === c.name && anchor === (c.anchor || '').trim() && voice === (c.voice || '').trim()) return
  emit('update-character', { index: i, name, anchor, voice })
}

// ---------------------------------------------------------------- 素材（道具/场景）
// 对标 LibTV 的资产层：道具与场景作为独立素材，可 AI 生成、可上传参考图。
// 道具两张（主视图/侧视图），场景一张全景（不是分镜画面，是故事发生地的环境设定）。
// 上传后自动按参考图重新生成：道具主视图直接复用上传图，侧视图拿它当参考走图生图。
const ASSET_GROUP_LABELS = { prop: '道具', scene: '场景' }

const assetGroups = computed(() => {
  const groups = []
  for (const kind of ['prop', 'scene']) {
    const items = (props.project.assets || [])
      .map((a, index) => ({ asset: a, index }))
      .filter(({ asset }) => asset.kind === kind)
    if (items.length) groups.push({ kind, label: ASSET_GROUP_LABELS[kind], items })
  }
  return groups
})

// 道具与 CreateWorkbench / 后端 `Asset.primary_image` 同一口径：优先「三视图设定图」
// （sheet）—— 「AI 生成」给道具出的就是它，`images` 恒为空（2026-09-29 修）。
// 只读 images[0] 会让已生成好的道具显示成"待生成"，按钮文案也跟着错。
function mainImage(a) {
  return a.sheet || (a.images && a.images[0]) || ''
}

function viewsOfAsset(a) {
  return a.images || []
}

// ---------------------------------------------------------------- 手动增删（角色）
// 「＋ 添加角色」展开行内表单：名字必填，锚点留空由 AI 按故事设计。
const addingChar = ref(false)
const newCharName = ref('')
const newCharAnchor = ref('')

function submitChar() {
  const name = newCharName.value.trim()
  if (!name) return
  emit('add-character', { name, anchor: newCharAnchor.value.trim() })
  addingChar.value = false
  newCharName.value = ''
  newCharAnchor.value = ''
}

// ---------------------------------------------------------------- 手动增删改（素材）
const addingKind = ref('')   // 'prop' | 'scene' | ''
const newAssetName = ref('')
const newAssetAnchor = ref('')

function submitAsset() {
  const name = newAssetName.value.trim()
  if (!name || !addingKind.value) return
  emit('add-asset', { kind: addingKind.value, name, anchor: newAssetAnchor.value.trim() })
  addingKind.value = ''
  newAssetName.value = ''
  newAssetAnchor.value = ''
}

const editingAsset = ref(-1)
const draftAssetName = ref('')
const draftAssetAnchor = ref('')
let editAssetNameEl = null
let editAssetAnchorEl = null

function startAssetEdit(i, a) {
  editingAsset.value = i
  draftAssetName.value = a.name || ''
  draftAssetAnchor.value = a.anchor || ''
  nextTick(() => {
    const el = editAssetNameEl
    if (el) {
      el.focus()
      el.setSelectionRange(el.value.length, el.value.length)
    }
  })
}

function cancelAssetEdit() {
  editingAsset.value = -1
}

function saveAssetEdit() {
  const i = editingAsset.value
  const a = props.project.assets?.[i]
  if (!a) return
  const name = draftAssetName.value.trim()
  if (!name) return
  const anchor = draftAssetAnchor.value.trim()
  editingAsset.value = -1
  if (name === a.name && anchor === (a.anchor || '').trim()) return
  emit('patch-asset', { index: i, name, anchor })
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
          <div class="cmain">
            <img
              v-if="currentView(c, i)"
              :src="api.characterImageUrl(taskId, currentView(c, i))"
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
                :aria-label="`编辑${c.name}的名字与角色提示词`"
                :aria-busy="charSaving === i"
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
                :title="!c.anchor ? '需先填写角色锚点' : c.image_path ? '重新生成四个视角的定妆照' : '生成四个视角的定妆照'"
                @click="emit('reroll-character', { index: i, name: c.name })"
              >
                <span v-if="charBusy === c.name" class="spin" aria-hidden="true"></span>
                <svg v-else viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M20.49 15a9 9 0 1 1-2.12-9.36" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                  <path d="M21 3.6v6.4h-6.4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
              <button
                type="button"
                class="cbtn cdel"
                :disabled="busy || charSaving === i"
                :aria-label="`删除角色${c.name}`"
                title="删除这个角色（连同定妆照）"
                @click="emit('delete-character', { index: i, name: c.name })"
              >
                <svg viewBox="0 0 14 14" aria-hidden="true">
                  <path d="M2.5 3.5h9M5.5 3.5v-1h3v1M4 3.5l.6 8h4.8l.6-8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
            </div>
          </div>
          <div v-if="viewsOf(c).length > 1" class="cviews">
            <button
              v-for="(v, vi) in viewsOf(c)"
              :key="vi"
              type="button"
              class="cview"
              :class="{ on: (viewAt[i] ?? 0) === vi }"
              :aria-label="`查看${c.name}的${VIEW_LABELS[vi] || `第${vi + 1}个`}视角`"
              :title="VIEW_LABELS[vi] || ''"
              @click="viewAt[i] = vi"
            >
              <img :src="api.characterImageUrl(taskId, v)" alt="" />
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
          <input
            v-model="draftVoice"
            class="ename"
            maxlength="200"
            :aria-label="`${c.name} 的音色`"
            placeholder="音色：清亮的少女音、低沉沙哑…会作为该角色所有镜头的配音基调"
            @keydown.enter.prevent="saveEdit"
            @keydown.esc.prevent="cancelEdit"
          />
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
          <span v-if="c.voice" class="cvoice">音色描述：{{ c.voice }}</span>
          <div class="voicerow">
            <span
              class="vtag"
              title="配音音色决定角色用哪个声音说话。样本是给 Ref2VA 的 ref_audios 用的参考件——当前 I2VA/FL2VA 链路没有音频输入口，暂时吃不到它，所以生成后出片不会立刻有变化"
            >配音音色</span>
            <select
              class="vpick"
              :disabled="busy || charBusy === c.name"
              :aria-label="`${c.name} 的配音音色`"
              :value="c.tts_voice || ''"
              @change="onPickVoice($event, i, c.name)"
            >
              <option value="">（未设定）</option>
              <option v-for="v in voices" :key="v.id" :value="v.id">{{ voiceLabelOf(v.id) }}</option>
            </select>
            <button
              type="button"
              class="cbtn"
              :disabled="busy || charBusy === c.name"
              :aria-label="`${c.voice_sample ? '重新生成' : '生成'}${c.name}的音色样本`"
              :title="c.voice_sample ? '用当前音色重新合成样本' : '合成一段 3-7 秒的音色样本（要联网）'"
              @click="emit('reroll-voice', { index: i, name: c.name, voiceId: c.tts_voice || '' })"
            >
              <span v-if="charBusy === c.name" class="spin" aria-hidden="true"></span>
              <svg v-else viewBox="0 0 24 24" aria-hidden="true">
                <path d="M4 10v4M8 6v12M12 3v18M16 6v12M20 10v4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
              </svg>
            </button>
            <audio
              v-if="c.voice_sample"
              class="vplay"
              :src="api.characterVoiceUrl(taskId, c.voice_sample)"
              controls
              preload="none"
            ></audio>
            <span v-else class="vhint">未生成样本</span>
          </div>
        </template>
      </div>
    </div>

    <!-- 添加角色：名字必填，锚点留空由 AI 按故事设计 -->
    <div class="addrow">
      <template v-if="addingChar">
        <input v-model="newCharName" class="aname" maxlength="40" placeholder="角色名（必填）" :aria-label="'新角色名'" @keydown.enter.prevent="submitChar" />
        <input v-model="newCharAnchor" class="aanchor" maxlength="2000" placeholder="外貌锚点（可空，留空由 AI 设计：年龄/发型/服装/特征…）" :aria-label="'新角色锚点'" @keydown.enter.prevent="submitChar" />
        <button type="button" class="quiet" @click="addingChar = false; newCharName = ''; newCharAnchor = ''">取消</button>
        <button type="button" class="primary" :disabled="!newCharName.trim() || busy || !!charBusy" @click="submitChar">添加</button>
      </template>
      <button v-else type="button" class="addcard" @click="addingChar = true">＋ 添加角色</button>
    </div>

    <div v-for="group in assetGroups" :key="group.kind" class="agroup">
      <span class="akind">{{ group.label }}素材</span>
      <div class="chars">
        <div v-for="{ asset: a, index } in group.items" :key="index" class="char">
          <div class="charmedia">
            <div class="cmain">
              <img
                v-if="mainImage(a)"
                :src="api.assetImageUrl(taskId, mainImage(a))"
                :alt="`${a.name} 素材图`"
              />
              <div v-else class="cempty">
                {{ assetBusy === a.name ? '生成中…' : '待生成' }}
              </div>
              <div class="charops">
                <button
                  type="button"
                  class="cbtn"
                  :disabled="busy || assetBusy === a.name"
                  :aria-label="`编辑素材${a.name}的名字与描述`"
                  title="编辑名字与描述"
                  @click="startAssetEdit(index, a)"
                >
                  <svg viewBox="0 0 14 14" aria-hidden="true">
                    <path d="M9.8 1.9l2.3 2.3-7.4 7.4-3 .7.7-3 7.4-7.4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="cbtn"
                  :disabled="busy || assetBusy === a.name || !a.anchor"
                  :aria-label="`${assetBusy === a.name ? '正在生成' : mainImage(a) ? '重新生成' : '生成'}${a.name}的素材图`"
                  :title="!a.anchor ? '需先填写素材描述' : mainImage(a) ? '重新生成素材图' : '生成素材图'"
                  @click="emit('reroll-asset', { index, name: a.name })"
                >
                  <span v-if="assetBusy === a.name" class="spin" aria-hidden="true"></span>
                  <svg v-else viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                    <path d="M21 3.6v6.4h-6.4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="cbtn cdel"
                  :disabled="busy || assetBusy === a.name"
                  :aria-label="`删除素材${a.name}`"
                  title="删除这个素材（连同素材图）"
                  @click="emit('delete-asset', { index, name: a.name })"
                >
                  <svg viewBox="0 0 14 14" aria-hidden="true">
                    <path d="M2.5 3.5h9M5.5 3.5v-1h3v1M4 3.5l.6 8h4.8l.6-8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
              </div>
            </div>
            <div v-if="viewsOfAsset(a).length > 1" class="cviews">
              <button
                v-for="(v, vi) in viewsOfAsset(a)"
                :key="vi"
                type="button"
                class="cview"
                :class="{ on: vi === 0 }"
                :aria-label="`查看${a.name}第${vi + 1}个视角`"
                @click="api.assetImageUrl(taskId, v) && null"
              >
                <img :src="api.assetImageUrl(taskId, v)" alt="" />
              </button>
            </div>
          </div>
          <template v-if="editingAsset === index">
            <input
              :ref="(el) => (editAssetNameEl = el)"
              v-model="draftAssetName"
              class="ename"
              maxlength="40"
              :aria-label="`${a.name} 的素材名`"
              @keydown.enter.prevent="saveAssetEdit"
              @keydown.esc.prevent="cancelAssetEdit"
            />
            <textarea
              :ref="(el) => (editAssetAnchorEl = el)"
              v-model="draftAssetAnchor"
              class="eanchor"
              rows="3"
              maxlength="2000"
              :aria-label="`${a.name} 的素材描述`"
              placeholder="材质/颜色/形状/磨损…场景写空间结构、光源方向，禁止人物"
              @keydown.enter.ctrl.prevent="saveAssetEdit"
              @keydown.enter.meta.prevent="saveAssetEdit"
              @keydown.esc.prevent="cancelAssetEdit"
            ></textarea>
            <div class="eops">
              <span class="ehint">描述改了记得重新生成素材图</span>
              <button type="button" class="ecancel" @click="cancelAssetEdit">取消</button>
              <button type="button" class="esave" :disabled="!draftAssetName.trim()" @click="saveAssetEdit">保存</button>
            </div>
          </template>
          <template v-else>
            <span class="cname">
              {{ a.name }}
              <i v-if="a.stale" class="cwarn" title="素材描述已修改，重新生成后素材图将与新描述一致">图待更新</i>
            </span>
            <span class="canchor">{{ a.anchor }}</span>
          </template>
        </div>
      </div>
      <!-- 添加素材（按组分入口：道具/场景各一个） -->
      <div class="addrow">
        <template v-if="addingKind === group.kind">
          <input v-model="newAssetName" class="aname" maxlength="40" :placeholder="`${group.label}名（必填）`" :aria-label="`新${group.label}名`" @keydown.enter.prevent="submitAsset" />
          <input v-model="newAssetAnchor" class="aanchor" maxlength="2000" :placeholder="`${group.label}描述（可空，留空由 AI 设计）`" :aria-label="`新${group.label}描述`" @keydown.enter.prevent="submitAsset" />
          <button type="button" class="quiet" @click="addingKind = ''; newAssetName = ''; newAssetAnchor = ''">取消</button>
          <button type="button" class="primary" :disabled="!newAssetName.trim() || busy || !!assetBusy" @click="submitAsset">添加</button>
        </template>
        <button v-else type="button" class="addcard" @click="addingKind = group.kind; newAssetName = ''; newAssetAnchor = ''">＋ 添加{{ group.label }}</button>
      </div>
    </div>

    <!-- 一个素材都没有时给个总入口 -->
    <div v-if="!assetGroups.length" class="addrow">
      <template v-if="addingKind">
        <span class="akind">{{ addingKind === 'prop' ? '道具' : '场景' }}</span>
        <input v-model="newAssetName" class="aname" maxlength="40" placeholder="名字（必填）" :aria-label="'新素材名'" @keydown.enter.prevent="submitAsset" />
        <input v-model="newAssetAnchor" class="aanchor" maxlength="2000" placeholder="描述（可空，留空由 AI 设计）" :aria-label="'新素材描述'" @keydown.enter.prevent="submitAsset" />
        <button type="button" class="quiet" @click="addingKind = ''">取消</button>
        <button type="button" class="primary" :disabled="!newAssetName.trim() || busy" @click="submitAsset">添加</button>
      </template>
      <template v-else>
        <button type="button" class="addcard" @click="addingKind = 'prop'">＋ 添加道具</button>
        <button type="button" class="addcard" @click="addingKind = 'scene'">＋ 添加场景</button>
      </template>
    </div>

    <div v-if="blocks.length || shots.length" class="stats">
      <template v-if="blocks.length">
        <span class="stat"><b class="mono">{{ blocks.length }}</b>个块</span>
        <span class="sep"></span>
        <span class="stat"><b class="mono">{{ fmtSec(totalSec) }}</b>总时长</span>
        <span class="sep"></span>
        <span class="stat"><b class="mono">{{ blocks.filter((b) => b.video_path).length }}/{{ blocks.length }}</b>已出片</span>
      </template>
      <template v-else>
        <span class="stat"><b class="mono">{{ shots.length }}</b>个分镜</span>
        <span class="sep"></span>
        <span class="stat"><b class="mono">{{ fmtSec(totalSec) }}</b>总时长</span>
        <span class="sep"></span>
        <span class="stat"><b class="mono">{{ rendered }}/{{ shots.length }}</b>已出图</span>
      </template>
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

.agroup { display: flex; flex-direction: column; gap: 8px; }
.akind { font-size: 12px; font-weight: 600; color: var(--fg-2); }

/* 手动增删的入口行与表单 */
.addrow { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.addcard {
  font-size: 12px;
  padding: 9px 14px;
  color: var(--fg-2);
  background: transparent;
  border: 1px dashed #bcc5b3;
  border-radius: var(--r-sm);
}
.addcard:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-dim); }
.aname { width: 160px; font-size: 12px; background: #fdfdfa; }
.aanchor { flex: 1; min-width: 200px; font-size: 12px; background: #fdfdfa; }
.cbtn.cdel:hover:not(:disabled) { color: var(--danger); border-color: #eec9c0; background: var(--danger-dim); }

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
.charmedia { width: 96px; display: flex; flex-direction: column; gap: 4px; }
.cmain { position: relative; width: 96px; height: 96px; }
.cmain img { width: 100%; height: 100%; display: block; object-fit: cover; border-radius: var(--r-sm); }
.cviews { display: flex; gap: 3px; }
.cview {
  flex: 1;
  min-width: 0;
  min-height: 0;
  height: 22px;
  padding: 0;
  overflow: hidden;
  background: none;
  border: 1px solid var(--line);
  border-radius: 3px;
}
.cview img { width: 100%; height: 100%; display: block; object-fit: cover; }
.cview:hover { border-color: var(--fg-3); }
.cview.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.cview:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
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
.cvoice { grid-column: 1 / -1; font-size: 11px; color: var(--fg-3); line-height: 1.6; overflow-wrap: anywhere; }

/* 配音音色行：下拉改音色 → 按钮合成样本 → 就地试听。
   audio 用 preload="none"，一屏好几个角色时不预取音频（样本虽小也没必要）。 */
.voicerow { grid-column: 1 / -1; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.vtag { font-size: 11px; color: var(--fg-3); flex: none; }
.vpick {
  font-size: 11px;
  padding: 5px 8px;
  max-width: 190px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  color: var(--fg);
  cursor: pointer;
}
.vpick:hover:not(:disabled) { border-color: var(--fg-3); }
.vpick:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.vpick:disabled { opacity: 0.55; cursor: not-allowed; }
.vplay { height: 30px; max-width: 240px; }
.vhint { font-size: 11px; color: var(--fg-3); }

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

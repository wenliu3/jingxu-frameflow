<script setup>
// ⚠️ 已停用（2026-09-15）：本组件不再被 import。
//
// 它曾经是「工作台 → 素材工坊」的界面，与「新建作品」页的 CreateWorkbench.vue 是
// **两套平行实现** —— 同一件事写了两遍，改一边忘一边必然漂开（两处的文案、卡片按钮、
// 添加素材的流程都曾不一致，甚至出现过"照着截图改错了组件"）。
//
// 现在两个入口统一用 CreateWorkbench；本文件独有的能力（手写提示词模式、首尾帧勾选、
// 清晰度、六段式提示词预览）已经移植过去，所以这里理论上可以直接删。
// 保留文件只是因为删代码有风险，且它的注释较密（增量数据、编排分岔等）可作参考。
//
// 要恢复：把 App.vue 里 studioMode === 'studio' 那段换回 <MaterialStudio …> 并补 import。

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'

// 素材工坊（中栏）：01 准备材料 + 02 生成视频。
//
// 与分镜工作台的关系：**并存，不替换**。分镜工作台走「一句创意 → 自动拆镜 → 批量出图出片」，
// 这里是「先备素材 → 手选 → 手写一段描述 → 生成一段视频」，两种产品逻辑。
// 共用一个作品的数据（角色/素材/音色），所以这里的编辑操作直接复用分镜工作台的同一批事件。
//
// 视觉：全部走 style.css 的 :root 令牌（暖纸 + 朱红），组件内不写死色值。
const props = defineProps({
  project: { type: Object, required: true },
  taskId: { type: String, default: '' },
  cfg: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
  charBusy: { type: String, default: '' },
  assetBusy: { type: String, default: '' },
})

const emit = defineEmits([
  'reroll-character', 'reroll-asset', 'reroll-voice',
  'delete-character', 'delete-asset',
  'add-character', 'add-asset',
  'edit-material', 'refresh-task',
])

// ---------------------------------------------------------------- 素材清单
// 六组，与设计稿一致。后两组（其他图片 / 其他音频）目前**后端没有对应数据源** ——
// assets 只有 prop / scene 两种 kind。这里如实显示空态，不假装有。
const GROUPS = [
  { key: 'character', label: '角色', empty: '还没有角色。点「添加素材」或让它随故事自动生成。' },
  { key: 'scene', label: '场景', empty: '还没有场景设定图。' },
  { key: 'prop', label: '道具', empty: '还没有道具设定图。' },
  { key: 'otherImage', label: '其他图片', empty: '暂无额外图片（后端还没有这类素材）。' },
  { key: 'voice', label: '角色音色', empty: '还没有音色样本。点角色上的「音色」生成。' },
  { key: 'otherAudio', label: '其他音频', empty: '暂无额外音频（后端还没有这类素材）。' },
]

const chars = computed(() => props.project?.characters || [])
const assets = computed(() => props.project?.assets || [])

const items = computed(() => {
  const out = { character: [], scene: [], prop: [], otherImage: [], voice: [], otherAudio: [] }

  chars.value.forEach((c, i) => {
    // 有「四视图设定图」就优先显示它（与「新建作品」页同规则）——
    // 它不进 images，只作总览；出片用的仍是 image_path（裁出来的单人正面图）。
    const sheetUrl = c.sheet ? api.characterImageUrl(props.taskId, c.sheet) : ''
    out.character.push({
      key: `char:${i}`, kind: 'character', index: i, name: c.name,
      desc: c.images?.length ? '定妆照已就绪' : '定妆照待生成',
      url: sheetUrl || (c.image_path ? api.characterImageUrl(props.taskId, c.image_path) : ''),
      ready: !!(c.image_path || c.images?.length),
      ref: c,
    })
    if (c.voice_sample) {
      out.voice.push({
        key: `voice:${i}`, kind: 'voice', index: i, name: c.name,
        desc: `音色样本 · ${c.tts_voice || '未设定'}`,
        url: api.characterVoiceUrl(props.taskId, c.voice_sample),
        ready: true,
        ref: c,
      })
    }
  })

  assets.value.forEach((a, i) => {
    // 后端素材四种 kind：prop/scene 是"要生成的设定件"，image/audio 是用户上传的原始素材。
    // 上传素材不参与"AI 生成"，所以它的空态文案是"待上传"而不是"待生成"。
    const bucket = ['prop', 'scene', 'image', 'audio'].includes(a.kind) ? a.kind : 'prop'
    const target = bucket === 'image' ? 'otherImage' : bucket === 'audio' ? 'otherAudio' : bucket
    const ready = !!a.images?.length
    out[target].push({
      key: `${bucket}:${i}`, kind: bucket, index: i, name: a.name,
      desc: a.kind === 'audio'
        ? (ready ? '音频已就绪' : '音频待上传')
        : (ready ? '素材图已就绪' : '素材图待上传'),
      url: ready ? api.assetImageUrl(props.taskId, a.images[0]) : '',
      ready,
      ref: a,
    })
  })
  return out
})

const groups = computed(() => GROUPS.map((g) => ({ ...g, items: items.value[g.key] || [] })))
const materialTotal = computed(() =>
  groups.value.reduce((n, g) => n + (g.key === 'otherImage' || g.key === 'otherAudio' ? 0 : g.items.length), 0)
)
// 缺东西的项（用于「一键生成全部」）
const missing = computed(() => ({
  characters: items.value.character.filter((x) => !x.ready).map((x) => x.index),
  assets: [...items.value.scene, ...items.value.prop].filter((x) => !x.ready).map((x) => x.index),
  voices: chars.value.map((c, i) => (c.voice_sample ? -1 : i)).filter((i) => i >= 0),
}))
const missingCount = computed(() =>
  missing.value.characters.length + missing.value.assets.length + missing.value.voices.length
)

// ---------------------------------------------------------------- 添加素材
// 必须走行内表单：直接 emit 一个空名字会让后端拿空名去建角色（会被 422 挡掉，
// 但用户看到的是"点了没反应"）。五种类型合一个入口，**只填名字**。
// 2026-09-15 斌哥定：这里不要描述输入框 —— 添加动作只负责"起个名字占位"，
// 描述留空由 AI 按故事设计；要手写描述去分镜工作台的编辑表单。anchor 恒传空串。
const adding = ref('')          // '' | 'character' | 'prop' | 'scene' | 'image' | 'audio'
const newName = ref('')

const ADD_KINDS = [
  { key: 'character', label: '角色' },
  { key: 'prop', label: '道具' },
  { key: 'scene', label: '场景' },
  { key: 'image', label: '其他图片' },
  { key: 'audio', label: '其他音频' },
]

function openAdd() {
  adding.value = 'character'
  newName.value = ''
}

function cancelAdd() {
  adding.value = ''
  newName.value = ''
}

function submitAdd() {
  const name = newName.value.trim()
  if (!name || !adding.value) return
  const anchor = ''
  if (adding.value === 'character') {
    emit('add-character', { name, anchor })
  } else {
    emit('add-asset', { kind: adding.value, name, anchor })
  }
  cancelAdd()
}

// ---------------------------------------------------------------- 选择
const picked = ref(new Set())

function isPicked(it) {
  return picked.value.has(it.key)
}
// 角色音色不单独选 —— 选了角色就自动带上它的音色（这正是"角色声音一致"的做法）
function togglePick(it) {
  if (it.kind === 'voice') return
  const next = new Set(picked.value)
  if (next.has(it.key)) {
    next.delete(it.key)
  } else {
    if (it.kind === 'scene' && [...next].some((k) => k.startsWith('scene:'))) {
      // 场景至多一个：参考图里两个环境会把空间搅乱
      ;[...next].filter((k) => k.startsWith('scene:')).forEach((k) => next.delete(k))
    }
    next.add(it.key)
  }
  picked.value = next
}

const pickedItems = computed(() => {
  const all = [
    ...items.value.character, ...items.value.scene, ...items.value.prop,
    ...items.value.otherImage, ...items.value.otherAudio,
  ]
  return all.filter((it) => picked.value.has(it.key))
})

const selCharacters = computed(() => pickedItems.value.filter((x) => x.kind === 'character').map((x) => x.name))
const selProps = computed(() => pickedItems.value.filter((x) => x.kind === 'prop').map((x) => x.name))
const selScene = computed(() => pickedItems.value.find((x) => x.kind === 'scene')?.name || '')
const selImages = computed(() => pickedItems.value.filter((x) => x.kind === 'image').map((x) => x.name))
const selAudios = computed(() => pickedItems.value.filter((x) => x.kind === 'audio').map((x) => x.name))
const selVoiceCount = computed(() =>
  pickedItems.value.filter((x) => x.kind === 'character' && x.ref?.voice_sample).length
)

// 新任务/切换作品时清空选择，避免把上一个作品的角色名带过去
watch(() => props.taskId, () => { picked.value = new Set() })

// ---------------------------------------------------------------- 视频设置
const RATIOS = ['16:9', '9:16', '1:1', '4:3']
const DURATIONS = [5, 10, 15, 30]
const ratio = ref('16:9')
const duration = ref(10)
const quality = ref('1080P')

// 画幅跟作品走（项目级设定），改了会同时影响出图与出片，所以这里只做展示同步
watch(() => props.project?.aspect_ratio, (v) => { if (v && RATIOS.includes(v)) ratio.value = v }, { immediate: true })

// ---------------------------------------------------------------- 编排
// 提示词有两条路，对应"有没有文本 API"两种用户：
//   AI 帮我写   —— 中文描述 → LLM 按素材编号写成英文正文（要 DEEPSEEK Key）
//   我自己写    —— 直接写英文正文，**全程不碰任何模型接口**（只要 ComfyUI 就能用）
const manualPrompt = ref(false)
const videoPromptDraft = ref('')
const description = ref('')          // AI 模式下的中文描述（手写模式下不用）
const composing = ref(false)
const result = ref(null)
const errorMsg = ref('')

async function onCompose() {
  if (!pickedItems.value.length) {
    errorMsg.value = '先在上面的素材里勾选至少一项（角色或场景/道具/图片）'
    return
  }
  if (manualPrompt.value && !videoPromptDraft.value.trim()) {
    errorMsg.value = '自己写模式下需要填提示词正文（至少写出这一段要发生什么）'
    return
  }
  if (!manualPrompt.value && !description.value.trim()) {
    errorMsg.value = '先写一段描述，或切到「我自己写」直接填英文提示词'
    return
  }
  errorMsg.value = ''
  composing.value = true
  try {
    const payload = {
      characters: selCharacters.value,
      props: selProps.value,
      scene: selScene.value,
      images: selImages.value,
      audios: selAudios.value,
      duration: duration.value,
      use_voice: true,
    }
    // 两条路在这里分岔：手写走直通组装（零模型调用），AI 写走 LLM
    if (manualPrompt.value) payload.video_prompt = videoPromptDraft.value
    else payload.description = description.value
    result.value = await api.composeSegment(props.taskId, payload)
  } catch (err) {
    result.value = null
    errorMsg.value = err.message
  } finally {
    composing.value = false
  }
}

// ---------------------------------------------------------------- 出片
// 三种旧模式在这里合并：不再让用户选 i2v / flf / portrait，
// 由「选了什么素材 + 有没有勾首尾帧」决定走 I2VA / FL2VA / T2VA：
//   选 1 张图 → I2VA｜选 2 张图 → FL2VA｜一张不选 → T2VA（自定义，"没选素材用这个"）
const useLastFrame = ref(false)
const segJob = ref(null)
let segPoll = null

// 能当首帧的已选素材（角色用定妆照，场景/道具/其他图片用它们的图；音频不算）
const frameItems = computed(() =>
  pickedItems.value.filter((x) => ['character', 'scene', 'prop', 'image'].includes(x.kind))
)
// 素材引用串（"character:0"），按点选顺序 —— 第 1 个能出图的当首帧，勾了首尾帧第 2 个当尾帧
const frameRefs = computed(() => frameItems.value.map((x) => `${x.kind}:${x.index}`))

async function onGenerate() {
  errorMsg.value = ''
  // 先编排：没编排过就编一次（已编排过就直接用，不重复花 LLM 的钱）
  if (!result.value) {
    await onCompose()
    if (!result.value) return
  }
  try {
    const started = await api.startSegmentVideo(props.taskId, {
      frames: frameRefs.value,
      use_last_frame: useLastFrame.value,
      prompt: result.value.prompt,
      duration: duration.value,
    })
    segJob.value = started
    pollSegment(started.job_id)
  } catch (err) {
    segJob.value = { status: 'failed', error: err.message }
  }
}

function pollSegment(jobId) {
  clearInterval(segPoll)
  segPoll = setInterval(async () => {
    try {
      const st = await api.segmentVideo(jobId)
      segJob.value = st
      if (st.status === 'succeeded' || st.status === 'failed') {
        clearInterval(segPoll)
        if (st.status === 'failed') errorMsg.value = `出片失败：${st.error}`
      }
    } catch {
      /* 单次轮询失败忽略，下一轮再试 */
    }
  }, 3000)
}

onBeforeUnmount(() => clearInterval(segPoll))

// 一键生成缺的素材：逐个调已有的重生成接口（**不新增后端**），跑完让上层刷新任务。
// 串行是故意的：出图走 ModelScope 日额度，并行打光就没法回滚了。
const bulkBusy = ref(false)
const bulkNote = ref('')

async function onGenerateAll() {
  const m = missing.value
  const total = missingCount.value
  if (!total) {
    bulkNote.value = '素材都已就绪，没有要补的'
    return
  }
  bulkBusy.value = true
  let done = 0
  const fail = []
  const tick = (label) => { done += 1; bulkNote.value = `${label}（${done}/${total}）` }
  try {
    for (const i of m.characters) {
      try { await api.rerollCharacter(props.taskId, i) } catch (e) { fail.push(`角色 #${i + 1}：${e.message}`) }
      tick('生成定妆照')
    }
    for (const i of m.assets) {
      try { await api.rerollAsset(props.taskId, i) } catch (e) { fail.push(`素材 #${i + 1}：${e.message}`) }
      tick('生成素材图')
    }
    for (const i of m.voices) {
      try { await api.rerollCharacterVoice(props.taskId, i, '') } catch (e) { fail.push(`音色 #${i + 1}：${e.message}`) }
      tick('合成音色样本')
    }
    bulkNote.value = fail.length ? `完成 ${total - fail.length}/${total}，失败：${fail.join('；')}` : `已补齐 ${total} 项素材`
  } finally {
    bulkBusy.value = false
    emit('refresh-task')
  }
}

// ---------------------------------------------------------------- 上传
// 「没有任何图像/文本 API，只有 ComfyUI 地址」的用户全靠这条路：自己备素材，
// 程序不能反过来去调模型。上传即素材 —— 不走图生图，不调 LLM。
const uploading = ref('')

async function onUpload(e, it) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file) return
  uploading.value = it.key
  errorMsg.value = ''
  try {
    if (it.kind === 'character') {
      await api.uploadCharacterImage(props.taskId, it.index, file)
    } else {
      await api.uploadAssetFile(props.taskId, it.index, file)
    }
    bulkNote.value = `「${it.name}」的文件已上传并就绪`
    emit('refresh-task')
  } catch (err) {
    errorMsg.value = `上传失败：${err.message}`
  } finally {
    uploading.value = ''
  }
}

const STEPS = computed(() => [
  { label: '素材', on: !pickedItems.value.length },
  { label: '选择', on: !!pickedItems.value.length && !description.value },
  { label: '提示词', on: !!description.value && !result.value },
  { label: '生成', on: !!result.value },
])
</script>

<template>
  <div class="studio">
    <!-- 01 准备材料 -->
    <section class="stage-block fade-in">
      <header class="block-head">
        <span class="bnum">01</span>
        <div class="btitle">
          <h2>准备材料</h2>
          <p>管理角色、场景、道具、音色素材，支持一键生成完整视频</p>
        </div>
        <div class="bactions">
          <span class="bcount">已准备 {{ materialTotal }} 项素材</span>
          <button class="primary" type="button" :disabled="busy || bulkBusy" @click="onGenerateAll">
            <span v-if="bulkBusy" class="spin" aria-hidden="true"></span>
            {{ bulkBusy ? '生成中…' : 'AI 一键生成全部素材' }}
          </button>
          <button type="button" :disabled="busy" @click="adding ? cancelAdd() : openAdd()">{{ adding ? '取消添加' : '添加素材' }}</button>
        </div>
      </header>

      <div v-if="adding" class="addform">
        <div class="seg">
          <button v-for="k in ADD_KINDS" :key="k.key" type="button" :class="{ on: adding === k.key }" @click="adding = k.key">{{ k.label }}</button>
        </div>
        <input v-model="newName" maxlength="40" placeholder="名字（必填）" aria-label="新素材名字" @keydown.enter.prevent="submitAdd" />
        <button class="primary" type="button" :disabled="!newName.trim() || busy" @click="submitAdd">添加</button>
      </div>
      <p v-if="bulkNote" class="bulknote">{{ bulkNote }}</p>

      <div class="mgroups">
        <section v-for="g in groups" :key="g.key" class="mgroup">
          <div class="mgroup-head">
            <span class="mlabel">{{ g.label }}</span>
            <span class="mcount">{{ g.items.length }} 项</span>
          </div>
          <div v-if="g.items.length" class="mgrid">
            <article
              v-for="it in g.items"
              :key="it.key"
              class="mcard"
              :class="{ picked: isPicked(it), voicing: it.kind === 'voice' }"
              @click="togglePick(it)"
            >
              <div class="mthumb" :class="{ audio: it.kind === 'voice' || it.kind === 'audio' }">
                <!-- 图片类用 img；音频类（角色音色 / 其他音频）必须是 <audio> ——
                     用 <img> 指向 mp3 会渲染失败只剩 alt 文本（实测踩过） -->
                <img v-if="it.url && it.kind !== 'voice' && it.kind !== 'audio'" :src="it.url" :alt="`${it.name} 素材图`" />
                <audio v-else-if="it.kind === 'voice' || it.kind === 'audio'" class="vaudio" :src="it.url" controls preload="none"></audio>
                <div v-else class="cempty">{{ busy ? '生成中…' : '待生成' }}</div>
                <span v-if="isPicked(it)" class="mcheck" aria-hidden="true">✓</span>
              </div>
              <div class="mmeta">
                <strong>{{ it.name }}</strong>
                <p>{{ it.desc }}</p>
              </div>
              <div class="mops">
                <label v-if="it.kind !== 'voice'" class="quiet tiny mup" :title="`上传${it.name}的文件（直接作为素材，不走生成）`">
                  <input type="file" hidden :accept="it.kind === 'audio' ? 'audio/*' : 'image/*'" :disabled="busy || uploading === it.key" @change="onUpload($event, it)" />
                  {{ uploading === it.key ? '上传中' : '上传' }}
                </label>
                <button type="button" class="quiet tiny" :disabled="busy" @click.stop="emit('edit-material', it)">编辑</button>
                <button
                  type="button"
                  class="quiet tiny"
                  :disabled="busy || it.kind === 'voice'"
                  @click.stop="it.kind === 'character' ? emit('delete-character', { index: it.index, name: it.name }) : it.kind === 'voice' ? null : emit('delete-asset', { index: it.index, name: it.name })"
                >删除</button>
                <!-- 「重新生成」只对能生成的四类显示：角色 / 场景 / 道具 / 音色。
                     图片与其他音频是上传型素材，后端 /reroll 也会拒绝（那里会先删旧图再补，
                     而对上传型素材补不出来 → 等于把用户传的图删了），所以这里直接不给入口。 -->
                <button
                  v-if="['character', 'scene', 'prop', 'voice'].includes(it.kind)"
                  type="button"
                  class="quiet tiny"
                  :disabled="busy"
                  @click.stop="it.kind === 'character' ? emit('reroll-character', { index: it.index, name: it.name }) : it.kind === 'voice' ? emit('reroll-voice', { index: it.index, name: it.name, voiceId: it.ref?.tts_voice || '' }) : emit('reroll-asset', { index: it.index, name: it.name })"
                >{{ it.kind === 'voice' ? '重合成' : '重新生成' }}</button>
              </div>
            </article>
          </div>
          <p v-else class="mempty">{{ g.empty }}</p>
        </section>
      </div>
    </section>

    <!-- 02 生成视频 -->
    <section class="stage-block fade-in">
      <header class="block-head">
        <span class="bnum">02</span>
        <div class="btitle">
          <h2>生成视频</h2>
          <p>从上方已准备的素材中选择，或直接拖动到视频素材区</p>
        </div>
      </header>

      <div class="compose">
        <div class="picked-col">
          <div class="colhead">
            <span>本次视频素材</span>
            <span class="ccount">已选择 {{ pickedItems.length }} 项{{ selVoiceCount ? ` · 含 ${selVoiceCount} 个音色` : '' }}</span>
          </div>
          <div v-if="pickedItems.length" class="pickgrid">
            <div v-for="it in pickedItems" :key="it.key" class="pickcard">
              <img v-if="it.kind !== 'voice'" :src="it.url" :alt="it.name" />
              <div class="pmeta"><strong>{{ it.name }}</strong><span>{{ it.kind === 'character' ? '角色' : it.kind === 'scene' ? '场景' : '道具' }}</span></div>
              <button type="button" class="unpick" :aria-label="`移除 ${it.name}`" @click="togglePick(it)">×</button>
            </div>
            <article v-for="it in items.voice.filter((v) => selCharacters.includes(v.name))" :key="it.key" class="pickcard audio">
              <div class="pmeta"><strong>{{ it.name }}</strong><span>音色</span></div>
            </article>
          </div>
          <p v-else class="pickempty">还没有选素材 —— 点上面任意卡片勾选。音色会跟着角色自动带上。</p>
        </div>

        <div class="desc-col">
          <div class="colhead">
            <span>描述这一段视频</span>
            <button
              type="button"
              class="pmode"
              :class="{ manual: manualPrompt }"
              :title="manualPrompt ? '直接写英文正文，全程不碰任何模型接口（只要 ComfyUI 就能用）' : '用中文写，由文本模型按素材编号翻成英文正文（需要文本 API）'"
              @click="manualPrompt = !manualPrompt"
            >{{ manualPrompt ? '我自己写提示词' : '让 AI 帮我写' }}</button>
          </div>
          <textarea
            v-if="!manualPrompt"
            v-model="description"
            maxlength="1000"
            placeholder="用日常话讲清这一段要发生什么，例如：雨夜，小七撑着伞走进便利店，镜头慢慢推进，冷暖灯光交错…"
            aria-label="这一段视频的中文描述"
          ></textarea>
          <textarea
            v-else
            v-model="videoPromptDraft"
            maxlength="6000"
            placeholder="直接写 H3 的英文正文。要用上素材编号：<Subject 1> / <Picture 1> / <Audio 1>…&#10;[Shot 1] Live-action, cinematic, ...&#10;[Shot 2] At 00:05.000, ..."
            aria-label="这一段视频的英文提示词"
          ></textarea>
          <div class="descfoot">
            <span class="charcount">{{ (manualPrompt ? videoPromptDraft : description).length }} / {{ manualPrompt ? 6000 : 1000 }}</span>
            <span class="vmodel">{{ manualPrompt ? '手写模式 · 不调用文本模型' : (cfg?.video_backend === 'api' ? (cfg?.video_api_model || '外接 API') : 'AI 写 · 需要文本 API') }}</span>
          </div>
        </div>

        <div class="settings-col">
          <div class="srow"><span>视频比例</span>
            <div class="seg"><button v-for="r in RATIOS" :key="r" type="button" :class="{ on: ratio === r }" @click="ratio = r">{{ r }}</button></div>
          </div>
          <div class="srow"><span>生成时长</span>
            <div class="seg"><button v-for="d in DURATIONS" :key="d" type="button" :class="{ on: duration === d }" @click="duration = d">{{ d }}s</button></div>
          </div>
          <div class="srow"><span>清晰度</span>
            <select v-model="quality" aria-label="清晰度"><option value="720P">720P</option><option value="1080P">1080P（推荐）</option></select>
          </div>
          <div class="srow"><span>首尾帧</span>
            <label class="flf" :title="frameItems.length < 2 ? '勾选首尾帧需要两张图片素材' : '用选中的两张图锁定这段视频的开头与结尾'">
              <input type="checkbox" v-model="useLastFrame" :disabled="frameItems.length < 2" />
              用选中的两张图锁定开头与结尾
            </label>
          </div>
          <p v-if="frameItems.length < 2" class="flfhint">首尾帧需要两张图片素材（现在 {{ frameItems.length }} 张）</p>
          <button class="generate" type="button" :disabled="composing || busy || segJob?.status === 'running'" @click="onGenerate">
            <span v-if="composing || segJob?.status === 'running'" class="spin" aria-hidden="true"></span>
            {{ segJob?.status === 'succeeded' ? '再生成一条' : composing ? '编排中…' : segJob?.status === 'running' ? '出片中…' : '生成视频' }}
          </button>
        </div>
      </div>

      <p v-if="errorMsg" class="compose-err" role="alert">{{ errorMsg }}</p>

      <div v-if="result" class="result">
        <div class="result-head">
          <span class="tag accent">六段式提示词已生成</span>
          <span class="rhint">这是 Ref2VA 的完整编排结果 —— 参考槽位已编号，可直接照着往 ComfyUI 上连</span>
        </div>
        <pre class="manifest">{{ result.manifest }}</pre>
        <pre class="prompt">{{ result.prompt }}</pre>
        <ul v-if="result.warnings?.length" class="warns">
          <li v-for="(w, i) in result.warnings" :key="i">{{ w }}</li>
        </ul>
        <p class="next-hint">
          提示词这一步已经通了。<b>出片那一步还没接</b>：需要云端下 Ref2VA 权重（<code>minimax_h3_ref2va_pruned_int8_convrot</code>）+
          R2V 专属 LoRA，并把 <code>comfyui/h3_r2v_ui.json</code> 导成 API 格式。
        </p>
      </div>

      <div v-if="segJob" class="segment-out fade-in">
        <span class="tag" :class="segJob.status === 'succeeded' ? 'mint' : segJob.status === 'failed' ? 'danger' : 'accent'">
          {{ segJob.status === 'succeeded' ? '出片完成' : segJob.status === 'failed' ? '出片失败' : '出片中' }}
          · {{ (segJob.mode || '').toUpperCase() }}
        </span>
        <video v-if="segJob.video_url" :src="segJob.video_url" controls class="segvideo"></video>
        <p v-if="segJob.status === 'failed'" class="segerr">{{ segJob.error }}</p>
      </div>
    </section>

    <ol class="steps" aria-label="流程">
      <li v-for="s in STEPS" :key="s.label" :class="{ on: s.on }"><i aria-hidden="true"></i>{{ s.label }}</li>
    </ol>
  </div>
</template>

<style scoped>
.studio { display: flex; flex-direction: column; gap: 22px; }
.stage-block { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); padding: 18px 20px; }
.block-head { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 16px; }
.bnum { font-family: var(--font-mono); font-size: 13px; color: var(--accent); padding-top: 2px; }
.btitle { flex: 1; min-width: 0; }
.btitle h2 { margin: 0; font-size: 16px; font-weight: 600; }
.btitle p { margin: 3px 0 0; font-size: 12px; color: var(--fg-2); }
.bactions { display: flex; align-items: center; gap: 8px; flex: none; }
.bcount { font-size: 12px; color: var(--fg-3); margin-right: 2px; }
.bulknote { margin: -6px 0 12px; font-size: 12px; color: var(--mint); }
/* 添加素材的行内表单：类型 + 名字 + 添加。只有名字一个输入框（描述 2026-09-15 移除），
   所以让它占满剩余宽度，不再需要 last-of-type 那条为描述框预留的加宽规则 */
.addform { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: -6px 0 14px; padding: 10px 12px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm); }
.addform input { flex: 1 1 260px; min-width: 160px; }

.mgroups { display: flex; flex-direction: column; gap: 16px; }
.mgroup-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.mlabel { font-size: 13px; font-weight: 600; }
.mcount { font-size: 11px; color: var(--fg-3); }
/* 列宽下限 200px：三颗操作按钮（编辑/删除/重新生成）在 168px 下会被挤到换行竖排。
   auto-fill + 1fr 会按 200px 算列数，所以容器窄时列数自己减少，不会溢出。 */
.mgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 10px; }
.mcard { border: 1px solid var(--line); border-radius: var(--r-sm); overflow: hidden; background: var(--surface); cursor: pointer; transition: border-color .18s, box-shadow .18s; }
.mcard:hover { border-color: var(--accent); }
.mcard.picked { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-dim); }
.mcard.voicing { cursor: default; }
.mthumb { position: relative; aspect-ratio: 16 / 10; background: var(--surface-3); display: flex; align-items: center; justify-content: center; overflow: hidden; }
.mthumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.mthumb.audio { background: var(--accent-dim); }
.cempty { font-size: 12px; color: var(--fg-3); }
.mcheck { position: absolute; top: 6px; right: 6px; width: 20px; height: 20px; border-radius: 50%; background: var(--accent); color: var(--accent-ink); font-size: 12px; display: flex; align-items: center; justify-content: center; }
.mmeta { padding: 8px 10px 4px; }
.mmeta strong { display: block; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.mmeta p { margin: 2px 0 0; font-size: 11px; color: var(--fg-3); overflow-wrap: anywhere; }
.mops { display: flex; gap: 4px; padding: 2px 6px 7px; }
.mops button { white-space: nowrap; }
.tiny { font-size: 11px; padding: 4px 9px; }
/* 上传入口做成 label+隐藏 input：样式跟其他操作按钮一致，点击弹出选文件 */
.mup { cursor: pointer; color: var(--mint); }
.mup:hover { background: var(--mint-dim); }
.mup input { display: none; }
/* 提示词模式切换：手写模式高亮，提示"不依赖任何文本 API" */
.pmode { font-size: 11px; padding: 4px 9px; border-radius: var(--r-xs); }
.pmode.manual { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.flf { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--fg-2); cursor: pointer; user-select: none; }
.flf input { width: auto; margin: 0; }
.flf.off { opacity: .55; cursor: not-allowed; }
.flfhint { margin: -6px 0 0 70px; font-size: 11px; color: var(--fg-3); }
.segment-out { margin-top: 14px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.segvideo { max-width: 340px; width: 100%; border-radius: var(--r-sm); border: 1px solid var(--line); }
.segerr { margin: 0; flex: 1; min-width: 200px; font-size: 12px; color: var(--danger); }
.vaudio { width: 100%; height: 34px; }
.mempty { margin: 0; font-size: 12px; color: var(--fg-3); padding: 10px 2px; }

.compose { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); grid-template-areas: "picked picked" "desc settings"; gap: 14px; }
.picked-col { grid-area: picked; }
.desc-col { grid-area: desc; display: flex; flex-direction: column; }
.settings-col { grid-area: settings; display: flex; flex-direction: column; gap: 10px; }
.colhead { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 8px; font-size: 12px; font-weight: 600; }
.ccount { font-weight: 400; color: var(--fg-3); }
.pickgrid { display: flex; flex-wrap: wrap; gap: 8px; }
.pickcard { position: relative; width: 116px; border: 1px solid var(--line); border-radius: var(--r-sm); overflow: hidden; background: var(--surface-2); }
.pickcard img { width: 100%; height: 66px; object-fit: cover; display: block; }
.pickcard.audio { height: 66px; background: var(--accent-dim); }
.pmeta { padding: 5px 7px 6px; }
.pmeta strong { display: block; font-size: 12px; font-weight: 600; }
.pmeta span { font-size: 10px; color: var(--fg-3); }
.unpick { position: absolute; top: 4px; right: 4px; width: 18px; height: 18px; padding: 0; border-radius: 50%; font-size: 12px; line-height: 1; background: #0000008c; color: #fff; border-color: transparent; }
.pickempty { margin: 0; font-size: 12px; color: var(--fg-3); }
.desc-col textarea { flex: 1; min-height: 132px; }
.descfoot { display: flex; justify-content: space-between; margin-top: 6px; font-size: 11px; color: var(--fg-3); }
.srow { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.srow > span { flex: none; width: 60px; color: var(--fg-2); }
.seg { display: flex; gap: 4px; flex-wrap: wrap; }
.seg button { font-size: 12px; padding: 5px 10px; }
.seg button.on { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.generate { margin-top: auto; padding: 13px; font-size: 14px; background: var(--accent); border-color: var(--accent); color: var(--accent-ink); font-weight: 600; }
.generate:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
.compose-err { margin: 12px 0 0; padding: 9px 12px; font-size: 12px; color: var(--danger); background: var(--danger-dim); border: 1px solid #efccc8; border-radius: var(--r-sm); }

.result { margin-top: 16px; border-top: 1px solid var(--line-soft); padding-top: 14px; }
.result-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-bottom: 10px; }
.rhint { font-size: 11px; color: var(--fg-3); }
.manifest, .prompt { margin: 0 0 10px; padding: 12px 14px; font-family: var(--font-mono); font-size: 11.5px; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: var(--r-sm); }
.manifest { background: var(--mint-dim); border: 1px solid #c9dfd2; color: var(--mint); }
.prompt { background: var(--surface-2); border: 1px solid var(--line); color: var(--fg); max-height: 340px; overflow: auto; }
.warns { margin: 0 0 10px; padding-left: 18px; font-size: 12px; color: var(--warn); }
.next-hint { margin: 0; font-size: 11.5px; color: var(--fg-2); line-height: 1.8; }
.next-hint code { font-family: var(--font-mono); font-size: 11px; background: var(--surface-3); padding: 1px 5px; border-radius: var(--r-xs); }

.steps { display: flex; gap: 26px; justify-content: center; margin: 4px 0 0; padding: 0; list-style: none; }
.steps li { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--fg-3); }
.steps li i { width: 7px; height: 7px; border-radius: 50%; background: var(--line); }
.steps li.on { color: var(--accent); }
.steps li.on i { background: var(--accent); }

@media (max-width: 1080px) {
  .compose { grid-template-columns: minmax(0, 1fr); grid-template-areas: "picked" "desc" "settings"; }
}
</style>

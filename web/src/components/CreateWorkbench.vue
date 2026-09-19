<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'

// 「新建作品」主体页：01 准备素材 + 02 生成视频。
//
// 两个区块**可以分开用**（2026-09-17 斌哥定）：
//   - 「新建作品」页 + 工作台的「素材工坊」tab → 只显示 01（这两处就是"备素材"的地方）
//   - 工作台的「分镜工作台」tab → 只显示 02（视频创作归到那边去）
// 所以 01/02 各由一个 prop 控制，默认都显示，由上层决定这一处要哪一段。
//
// 素材要有作品才挂得住（后端 project.characters / project.assets），所以本组件
// 不自己建作品：所有需要落库的操作都先过 ensureTask()，由上层决定"没有就建个空白的"。
const props = defineProps({
  project: { type: Object, default: null },
  taskId: { type: String, default: '' },
  cfg: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
  ensureTask: { type: Function, required: true },
  refresh: { type: Function, default: null },
  notify: { type: Function, default: null },
  // 01 准备素材 / 02 生成视频 是否渲染。默认都渲染 —— 不渲染的那一段由别的页面承担
  showMaterials: { type: Boolean, default: true },
  showVideo: { type: Boolean, default: true },
})

const emit = defineEmits(['save-config', 'segment-done'])

function say(text, kind = 'info') {
  if (props.notify) props.notify(text, kind)
}

// 当前是不是 Ref2VA（多图参考）工作流。两处要用，所以提到最前面声明：
//   ① 素材卡片显示哪张图（Ref2VA 用设定图、I2V 用单张 —— 见 materialGroups）
//   ② 02 里「首尾帧」那一行要不要摆（Ref2VA 没有尾帧口）
const isRef2va = computed(() => String(props.cfg?.video_workflow || '') === 'ref2va')

// 所有落库操作都套这一层：先确保有作品，再把错误统一转成提示
async function withTask(fn) {
  try {
    const id = await props.ensureTask()
    if (!id) return null
    return await fn(id)
  } catch (err) {
    say(err.message, 'error')
    return null
  }
}

async function refreshTask() {
  if (props.refresh) await props.refresh()
}

// ---------------------------------------------------------------- 六组素材
// label / empty 的写法对齐作品工作台的「素材工坊」（MaterialStudio.vue），
// 两个页面是同一套版式，文案风格也要一致。
// 不带图标：素材工坊的分组标题就是「标签 + 计数」两个词，加图标就又分叉了。
// 也不带 accept / upload：分类级的「上传图片」按钮已删，加素材走顶部「添加素材」
// 或把文件直接拖到这一组上（见模板里 .mgroup 的 dragover / drop）。
const GROUPS = [
  { key: 'character', label: '角色', empty: '还没有角色。点「添加素材」，或把图片拖到这里。' },
  { key: 'scene', label: '场景', empty: '还没有场景设定图。' },
  { key: 'prop', label: '道具', empty: '还没有道具设定图。' },
  { key: 'otherImage', label: '其他图片', empty: '暂无额外图片。' },
  { key: 'voice', label: '角色音频', empty: '还没有音色样本，点角色卡片上的「录制」生成。' },
  { key: 'otherAudio', label: '其他音频', empty: '暂无额外音频。' },
]

const chars = computed(() => props.project?.characters || [])
const assets = computed(() => props.project?.assets || [])

function assetIndex(a) {
  return assets.value.indexOf(a)
}

const items = computed(() => {
  const out = { character: [], scene: [], prop: [], otherImage: [], voice: [], otherAudio: [] }

  chars.value.forEach((c, i) => {
    // 有「四视图设定图」就优先显示它 —— 一眼能看全四个视角，比单张正面信息量大。
    // 它不进 images，只作总览与留档；下游出片用的仍是 image_path（裁出来的单人正面图）。
    // ⚠️ 两处都带 `c.version` 做 cache-busting：重新生成是**就地覆盖同名文件**，
    //    不带版本号的话 URL 不变，浏览器 `<img>` 连请求都不发 → 用户必须手动刷新才看到新图
    //    （2026-09-19 斌哥报的）。后端每次生成/切版都会换一个新的 version。
    const sheetUrl = c.sheet ? api.characterImageUrl(props.taskId, c.sheet, c.version) : ''
    // 「出片时真正送进模型的那张」= 正面定妆照（单张）。卡片上显示的是四视图设定图
    // （信息量大、便于核对形象）—— 两张是**两个文件**，模型还可能把服装画得不一样。
    // 所以已选素材那一侧必须显示这张，否则用户会对着设定图问"进模型的怎么是另一张"
    // （2026-09-19 斌哥问过：他在 ComfyUI 里看到白上衣+棕裙，卡片上却是格纹裙）。
    const faceUrl = c.image_path ? api.characterImageUrl(props.taskId, c.image_path, c.version) : ''
    out.character.push({
      key: `character:${i}`, kind: 'character', index: i, name: c.name,
      desc: c.images?.length ? '定妆照已就绪' : '定妆照待生成',
      url: sheetUrl || faceUrl,
      // 出片时会送进模型的**那一张**（后端 _resolve_material_ref 是同一套规则，两边必须一致）：
      //   Ref2VA → 四视图设定图当参考图（信息最全，斌哥要的就是这个）
      //   I2V    → 正面定妆照（那张要当首帧；拼图当首帧＝第一秒四个人并排）
      sendUrl: isRef2va.value ? (sheetUrl || faceUrl) : (faceUrl || sheetUrl),
      ready: !!(c.image_path || c.images?.length),
      ref: c,
    })
    // 角色音频组按「角色」列行而不是按「样本」—— 没样本的角色也要出现在这里，
    // 否则用户没有入口给它上传/录制音色
    out.voice.push({
      key: `voice:${i}`, kind: 'voice', index: i, name: c.name,
      desc: c.voice_sample ? `音色样本 · ${c.tts_voice || '未设定'}` : '音色待生成',
      url: c.voice_sample ? api.characterVoiceUrl(props.taskId, c.voice_sample) : '',
      ready: !!c.voice_sample,
      ref: c,
    })
  })

  assets.value.forEach((a, i) => {
    const bucket = ['prop', 'scene', 'image', 'audio'].includes(a.kind) ? a.kind : 'prop'
    const target = bucket === 'image' ? 'otherImage' : bucket === 'audio' ? 'otherAudio' : bucket
    const ready = !!a.images?.length
    // 道具的「三视图设定图」（sheet）优先显示 —— 它信息量最大；
    // 还没出三视图但有单件图时退回单件图。它不进 images，只作总览。
    // version 同角色：就地覆盖 + URL 不变 = 浏览器不重新请求（见上面 character 那段注释）。
    const sheetUrl = a.sheet ? api.assetImageUrl(props.taskId, a.sheet, a.version) : ''
    out[target].push({
      key: `${bucket}:${i}`, kind: bucket, index: i, name: a.name,
      desc: bucket === 'audio'
        ? (ready ? '音频已就绪' : '音频待上传')
        : (ready ? '素材图已就绪' : (bucket === 'image' ? '图片待上传或生成' : '素材图待上传')),
      url: bucket === 'audio' ? '' : (sheetUrl || (ready ? api.assetImageUrl(props.taskId, a.images[0], a.version) : '')),
      audioUrl: ready && bucket === 'audio' ? `/files/${props.taskId}/assets/${String(a.images[0]).split(/[\\/]/).pop()}` : '',
      ready,
      ref: a,
    })
  })
  return out
})

const groups = computed(() => GROUPS.map((g) => ({ ...g, items: items.value[g.key] || [] })))

// 六个分组里能选/能编辑的全部条目（音色不在里面：它跟着角色自动带上）
const allItems = computed(() => [
  ...items.value.character, ...items.value.scene, ...items.value.prop,
  ...items.value.otherImage, ...items.value.otherAudio,
])

// 音色 / 其他音频传的是音频文件，其余传图片。accept 与按钮文案都跟着这个走。
function isAudioKind(kind) {
  return kind === 'voice' || kind === 'audio'
}

// 图片 / 音频类是用户自己传的原始素材，默认走「待上传」而不是「待生成」。
// ⚠️ image 现在也有 AI 生成入口了（2026-09-17），所以实际文案由 emptyText 决定 ——
// 这里保留 image 是为了别的地方按"上传型"判断时语义不反转。
function needsUpload(kind) {
  return kind === 'image' || kind === 'audio'
}

// 卡片还没图时的空态文案。
// 「其他图片」是个例外：它既能上传、也能 AI 生成（2026-09-17 起），
// 只写「待上传」会让用户以为它没法生成，所以两样都写出来。
function emptyText(it) {
  if (it.ready) return '已就绪'
  if (it.kind === 'image') return '待上传 / 待生成'
  return needsUpload(it.kind) ? '待上传' : '待生成'
}

// ---------------------------------------------------------------- 选中的素材
// ⚠️ 2026-09-19 定稿：**选中只在 02 那一侧做**（`.picklist` + 已选面板）。
//    01 的素材卡片上曾经有两个入口（先是"点整张卡片"，后是"图片右上角的小圆圈"），
//    斌哥都让撤了，第二句原话："不用选这个，因为选了也不知道能有什么功能"。
//    他说得对：01 和「分镜工作台」的 02 是同一个组件的两个实例，picked 是组件内部状态，
//    在 01 里选完切过去就丢了 —— 一个选完看不见结果、还会被丢掉的开关，留着只会误导。
//    音色不单独选：选了角色就自动带上它的音色（这正是"角色声音一致"的做法）。
const picked = ref(new Set())
watch(() => props.taskId, () => { picked.value = new Set() })

function isPicked(it) {
  return picked.value.has(it.key)
}

// 「图片类」素材（角色/场景/道具/其他图片）—— 用于判断"只能选一张"那条规矩
const IMAGE_KINDS = ['character', 'scene', 'prop', 'image']

function togglePick(it) {
  // 音色不单独选：选了角色就自动带上它的音色。
  // 其他音频可以选（会作为 <Audio 1> 进参考槽位），只是不能当首帧。
  if (it.kind === 'voice') return
  const next = new Set(picked.value)
  if (next.has(it.key)) {
    next.delete(it.key)
  } else {
    // ⚠️ I2V 工作流只吃**一张**图（那个 H3 节点只有一个 first_frame 口）。这时候还让人
    //    多选，等于骗人：多出来的图进不了模型，写提示词那一步却会引用模型根本收不到的
    //    <Picture 2>。所以 I2V 下勾第二张就把上一张顶掉 —— 灰底那句提示（见模板 .pl-head）
    //    会说明这件事。（2026-09-19 斌哥："如果没选 ref2v 就不能在那里选多个图片，就要提示"。）
    //    Ref2VA 不受影响：那份工作流有 ref_image_0/1/2 三个参考口，最多三张。
    if (!isRef2va.value && IMAGE_KINDS.includes(it.kind)) {
      ;[...next]
        .filter((k) => IMAGE_KINDS.some((p) => k.startsWith(`${p}:`)))
        .forEach((k) => next.delete(k))
    }
    if (it.kind === 'scene' && [...next].some((k) => k.startsWith('scene:'))) {
      // 场景至多一个：参考图里两个环境会把空间搅乱
      ;[...next].filter((k) => k.startsWith('scene:')).forEach((k) => next.delete(k))
    }
    next.add(it.key)
  }
  picked.value = next
}

const pickedItems = computed(() => allItems.value.filter((it) => picked.value.has(it.key)))

// 01 不显示时（分镜工作台那一侧只挂 02），02 得自带素材选择 —— 否则用户没有任何
// 入口把素材勾进这一段视频。音色不在其中：它跟着角色自动带上，不能单独选（见 togglePick）。
const selectableItems = computed(() => allItems.value.filter((it) => it.kind !== 'voice'))

const selCharacters = computed(() => pickedItems.value.filter((x) => x.kind === 'character').map((x) => x.name))
const selProps = computed(() => pickedItems.value.filter((x) => x.kind === 'prop').map((x) => x.name))
const selScene = computed(() => pickedItems.value.find((x) => x.kind === 'scene')?.name || '')
const selImages = computed(() => pickedItems.value.filter((x) => x.kind === 'image').map((x) => x.name))
const selAudios = computed(() => pickedItems.value.filter((x) => x.kind === 'audio').map((x) => x.name))
const selVoiceCount = computed(() =>
  pickedItems.value.filter((x) => x.kind === 'character' && x.ref?.voice_sample).length
)
// 能当首帧的已选素材（音频不算）
const frameItems = computed(() =>
  pickedItems.value.filter((x) => ['character', 'scene', 'prop', 'image'].includes(x.kind))
)
const frameRefs = computed(() => frameItems.value.map((x) => `${x.kind}:${x.index}`))

// ---------------------------------------------------------------- 上传
const uploading = ref('')
const dropKey = ref('')

async function uploadTo(kind, file, target) {
  if (!file) return
  uploading.value = target?.key || kind
  try {
    await withTask(async (id) => {
      if (target && target.kind === 'voice') {
        await api.uploadCharacterVoice(id, target.index, file)
      } else if (target && target.kind === 'character') {
        await api.uploadCharacterImage(id, target.index, file)
      } else if (target) {
        await api.uploadAssetFile(id, target.index, file)
      } else {
        // 组级上传：后端按 kind 自动建条目，名字取文件名
        await api.uploadMaterial(id, kind, file)
      }
      await refreshTask()
      say(`「${file.name || '录音'}」已上传并就绪`, 'ok')
    })
  } finally {
    uploading.value = ''
    dropKey.value = ''
  }
}

function onPickFile(event, g, target) {
  const file = event.target.files && event.target.files[0]
  event.target.value = ''
  uploadTo(g.key, file, target)
}

function onDrop(event, g) {
  dropKey.value = ''
  const file = event.dataTransfer?.files?.[0]
  if (!file) return
  // 音频组必须先有角色/条目才知道挂给谁，拖拽这里只支持图片类
  if (g.key === 'voice') {
    const first = chars.value.length === 1 ? items.value.voice[0] : null
    if (!first) { say('先确定给哪个角色配：角色多于 1 个时请点角色行上的「上传」'); return }
    uploadTo('voice', file, first)
    return
  }
  uploadTo(g.key, file, null)
}

// ---------------------------------------------------------------- 录音
// 浏览器 MediaRecorder → Blob → base64 走上传接口。全程不碰模型接口。
const recording = ref('')
let recorder = null
let chunks = []

async function startRecord(target) {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    say('当前浏览器不支持录音，请改用「上传音频」')
    return
  }
  if (recording.value) { stopRecord(); return }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    chunks = []
    recorder = new MediaRecorder(stream)
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data) }
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop())
      const type = (recorder && recorder.mimeType) || 'audio/webm'
      const blob = new Blob(chunks, { type })
      const ext = type.includes('ogg') ? '.ogg' : type.includes('mp4') ? '.m4a' : type.includes('wav') ? '.wav' : '.webm'
      recording.value = ''
      if (!blob.size) return
      await withTask(async (id) => {
        if (target.kind === 'voice') {
          await api.uploadCharacterVoice(id, target.index, blob, `voice${ext}`)
        } else {
          await api.uploadAssetFile(id, target.index, blob, `rec${ext}`)
        }
        await refreshTask()
        say('录音已保存', 'ok')
      })
    }
    recorder.start()
    recording.value = target.key
    say('正在录音…再点一次「停止录音」结束')
  } catch (err) {
    say(`拿不到麦克风权限：${err.message}`, 'error')
  }
}

function stopRecord() {
  if (recorder && recorder.state !== 'inactive') recorder.stop()
  recorder = null
}

onBeforeUnmount(() => { stopRecord() })

// 注：原来的 recordOtherAudio()（「其他音频」分组上的「录制音频」按钮，
// 没条目时先造一个空条目再录）2026-09-15 随那排按钮一起删了。
// 现在录其他音频：顶部「添加素材」选「其他音频」建条目 → 点卡片上的「录制」。

// ---------------------------------------------------------------- 添加 / 编辑 / 删除
// 添加流程（2026-09-15 斌哥定，见需求原话）：
//   点「添加素材」→ 只弹 5 个类型选项 → 选一个，**下面对应那一组里立刻多一张空卡片**
//   → 名字和内容（上传）都在那张卡片上填。
// 所以这里没有名字/描述输入框、也没有「添加」按钮 —— 表单只负责选类型。
const adding = ref(false)

const ADD_KINDS = [
  { key: 'character', label: '角色' },
  { key: 'scene', label: '场景' },
  { key: 'prop', label: '道具' },
  { key: 'image', label: '其他图片' },
  { key: 'audio', label: '其他音频' },
]

function openAdd() {
  adding.value = true
}

function cancelAdd() {
  adding.value = false
}

// 选中类型 → 落一张空条目 → 刷新 → 把那张新卡片直接推进编辑态
// （编辑态里有名字输入框 + 上传按钮，正是"在那里填名字、传内容"）
async function createEntry(kind) {
  adding.value = false
  const label = ADD_KINDS.find((k) => k.key === kind)?.label || '素材'
  await withTask(async (id) => {
    // design: false —— 只建条目。不能让后端顺手跑一次 AI 设计锚点：
    // 这次点击是"占个位"，锚点/名字由用户自己在卡片上填，不该花模型额度。
    const created = kind === 'character'
      ? await api.addCharacter(id, { name: '', anchor: '', design: false })
      : await api.addAsset(id, { kind, name: '', anchor: '', design: false })
    await refreshTask()
    // 刷新后按占位名反查刚落的那张卡片（后端保证占位名唯一，所以这一步是准的）
    const it = allItems.value.find((x) => x.name === created?.name)
    if (it) {
      freshKey.value = it.key
      startEdit(it, { blank: true })
    }
    say(`已新增一个${label}，填个名字再上传内容`)
    focusEditName()
  })
}

const editing = ref('')
const editName = ref('')
// 刚建出来、还没保存过的那张空卡片的 key。
// 它决定编辑态里那颗按钮是「取消」还是「删除」—— 见 cancelEdit()。
const freshKey = ref('')

// 换作品时别把上一部作品的编辑态带过去
watch(() => props.taskId, () => { editing.value = ''; freshKey.value = '' })

// blank=true 用于"刚新建的空卡片"：名字框留空让用户直接打，
// 而不是把后端补的占位名（未命名角色）塞进去让人先删一遍。
// 没传 blank 但确实是刚建的那张（比如取消后又点了一次「编辑」），也一样留空。
function startEdit(it, opts = {}) {
  editing.value = it.key
  editName.value = opts.blank || isFresh(it) ? '' : it.name
}

function isFresh(it) {
  return freshKey.value === it.key
}

// 编辑态的退出按钮：
//   老卡片 → 只是关掉表单（取消编辑）
//   刚建的空卡片 → 取消就等于放弃这张卡片，直接删掉。
//     否则每次「添加素材」手滑点一下都会在库里留一张「未命名角色」，
//     白占素材区，还会被当成真素材勾进视频。
function cancelEdit(it) {
  editing.value = ''
  if (!isFresh(it)) return
  freshKey.value = ''
  return removeItem(it, { silent: true })
}

// 新建后光标直接落到名字框 —— 少一次点击。
// 用 querySelector 而不是模板 ref：同时只会有一张卡片处于编辑态
// （editing 是单值），而 .edit-form 是 v-for 里 v-if 出来的，模板 ref 会变成数组。
async function focusEditName() {
  await nextTick()
  const el = document.querySelector('.workbench .edit-form input')
  if (el) el.focus()
}

async function commitEdit(it) {
  const name = editName.value.trim()
  // 描述/锚点已不在编辑表单里（2026-09-15），原样带回 —— 刚建的空卡片本来就是空串，
  // 老卡片则保持它已有的锚点，改名字不会把描述弄丢。
  const anchor = it.ref?.anchor || ''
  if (!name) return
  await withTask(async (id) => {
    if (it.kind === 'character') await api.patchCharacter(id, it.index, { name, anchor })
    else if (it.kind === 'voice') await api.patchCharacter(id, it.index, { name })
    else await api.patchAsset(id, it.index, { name, anchor })
    editing.value = ''
    freshKey.value = ''
    await refreshTask()
    say('已保存', 'ok')
  })
}

// silent=true 用于"刚建的空卡片被取消"：那张卡片是这一轮才建的，
// 没有引用关系要核对，再弹一次确认框纯属多余。
async function removeItem(it, opts = {}) {
  const label = it.kind === 'character' || it.kind === 'voice' ? '角色' : '素材'
  if (!opts.silent && !window.confirm(`删除${label}「${it.name}」？引用它的地方需要你自己核对。`)) return
  await withTask(async (id) => {
    if (it.kind === 'character' || it.kind === 'voice') await api.deleteCharacter(id, it.index)
    else await api.deleteAsset(id, it.index)
    if (editing.value === it.key) editing.value = ''
    if (freshKey.value === it.key) freshKey.value = ''
    await refreshTask()
    say(opts.silent ? '已放弃这张新卡片' : `已删除「${it.name}」`)
  })
}

// ---------------------------------------------------------------- 素材图预览
// 点卡片上的图 → 看大图 + 下载。素材图原先没有预览（灯箱只服务分镜/块那条链），
// 而"把生成的定妆照/设定图存下来，之后当素材复用"是刚需。
// ⚠️ 01 的卡片是**只读展示 + 操作**：点图片=看大图，点按钮=上传/编辑/删除/生成历史/AI 生成，
//    其它地方什么都不做（选中在 02 那一侧，见上面「选中的素材」那段）。
const previewItem = ref(null)

function onThumbClick(event, it) {
  // 没有图、或音频类（音频卡片自带播放器）→ 不接管，点了不做任何事
  if (!it?.url || isAudioKind(it.kind)) return
  event.stopPropagation()
  previewItem.value = it
}

// 下载文件名取 URL 最后一段（先截 query，再解码中文名）
function downloadName(it) {
  const base = String(it?.url || '').split('?')[0].split('/').pop()
  return base ? decodeURIComponent(base) : 'material.png'
}

// ---------------------------------------------------------------- AI 生成（五类共用一个弹窗）
// 弹窗里填一段自由描述（中文、口语都行），后端按类型交给不同 Agent 处理：
//   角色 → 角色 Agent（compose_portrait_sheet）→ 正面定妆照 + 四视图设定图
//   场景 → 场景 Agent（design_asset）→ 单张空镜（环境 + 镜头 + 光影，画面里没有人物）
//   道具 → 道具 Agent（compose_prop_sheet）→ 三视图设定图（恒 16:9）
//   其他图片 → 画面 Agent（design_asset 的 image 分支）→ 一整张完整画面（常直接当首/尾帧）
//   音色 → 配音 Agent（design_voice）→ 从音频池里挑一个音色 id 再合成样本
// 直接拿"酷一点的赛博女战士""雨夜的旧书店"进模型，结果会飘，所以要过 Agent 这一层。
// 2026-09-15 斌哥定：图片类这段描述**只用于当次出图，不写回卡片** —— 素材工坊是
// "选图 → 出视频"，有图能用就够，不需要维护一段描述。
const aiFor = ref(null)        // 正在弹窗的那个 item（null = 不显示）
const aiPrompt = ref('')
const aiBusy = ref(false)
const aiRatio = ref('1:1')     // 角色默认 1:1；场景/其他图片默认跟作品画幅
const aiGender = ref('female') // 只对音色弹窗有意义（性别是硬约束，必须显式选）
// 键必须与 image_provider.SIZE_TABLE 一致，别在这里自己造比例
const AI_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4']
// 音色描述比画面描述短得多。500 与后端 VoiceGenerateBody.prompt 的 max_length 对齐 ——
// 前端放 1000 而后端收 500 的话，用户写到 600 字就会吃一个莫名其妙的 422。
const aiMaxLen = computed(() => (aiFor.value?.kind === 'voice' ? 500 : 1000))
// 弹窗文案里对五类素材的称呼与示例
const AI_LABEL = { character: '形象图', scene: '场景图', prop: '三视图', image: '图片', voice: '音色' }
const AI_PLACEHOLDER = {
  character: '例如：赛博朋克风格的女战士，银灰色短发，左眼角有青色发光的植入体，黑色战术夹克，冷峻干练，写实电影质感',
  scene: '例如：雨夜的老旧书店，木质书架顶到天花板，暖黄吊灯，地面积水反光，安静略带灰尘的空气感',
  prop: '例如：掌心大小的黄铜罗盘，盘面刻星宿纹，指针氧化发黑，边缘有磕碰缺口，配一根深棕色皮绳',
  image: '例如：黄昏的天台，主角背对镜头站在栏杆边，风吹起风衣下摆，远处城市灯火初上，逆光剪影，中景低机位',
  voice: '例如：低沉沙哑的中年男声，语速偏慢，带一点疲惫和烟嗓，说话时尾音往下沉',
}
// 音色弹窗的性别选择。**性别是硬约束**（女主配男声是硬错），所以让用户显式选，
// 不交给模型猜 —— 后端也会拿这个值再校验一遍 Agent 挑回来的音色。
const GENDERS = [
  { key: 'female', label: '女声' },
  { key: 'male', label: '男声' },
]

function openAi(it) {
  aiFor.value = it
  aiPrompt.value = ''
  aiGender.value = 'female'
  // 场景是全景环境、其他图片常被直接当首帧 —— 两者都跟随作品画幅最自然；
  // 角色定妆照按惯例是 1:1（音色用不到比例）
  aiRatio.value = ['scene', 'image'].includes(it?.kind) ? (props.project?.aspect_ratio || '16:9') : '1:1'
}

function closeAi() {
  if (aiBusy.value) return
  aiFor.value = null
  aiPrompt.value = ''
}

async function submitAi() {
  const it = aiFor.value
  const text = aiPrompt.value.trim()
  if (!it || !text || aiBusy.value) return
  aiBusy.value = true
  try {
    // withTask 已把错误转成提示；失败时**不关弹窗**，用户改两句就能重试
    const res = await withTask((id) => {
      if (it.kind === 'character') return api.generateCharacterPortrait(id, it.index, text, aiRatio.value)
      if (it.kind === 'voice') return api.generateCharacterVoice(id, it.index, text, aiGender.value)
      return api.generateAssetImage(id, it.index, text, aiRatio.value)
    })
    if (res) {
      await refreshTask()
      if (it.kind === 'voice') {
        // 音色池是**预置的**，只能"挑"不能"造" —— 把挑中的 id 明说出来，
        // 用户才好核对，不满意就换个性别重生成
        say(`「${it.name}」的音色已生成：${res.tts_voice || '已就绪'}`, 'ok')
      } else {
        // 按 kind 说清生成的是什么：道具出的是三视图、其他图片出的是一张完整画面，
        // 一律说"场景图已生成"会让人以为生成错了东西
        const what = { character: '形象', scene: '场景图', prop: '三视图', image: '图片' }[it.kind] || '素材图'
        say(`「${it.name}」的${what}已生成`, 'ok')
      }
      aiFor.value = null
      aiPrompt.value = ''
    }
  } finally {
    aiBusy.value = false
  }
}

// ---------------------------------------------------------------- 生成历史（2026-09-19）
// 「这张图出过的每一版」。重新生成 / 覆盖上传都是**就地覆盖同名文件**，旧版本该被顶掉 ——
// 后端在每次写图**之前**先留一份快照（outputs/{id}/history/{kind}/{safe}/{时间戳}_{version}/），
// 这里只负责列出来 + 切回去。斌哥的原话：
//   「有一些人生成了之后看见不好，又生成，发现前面那个好，但是找不了」
//
// ⚠️ 只对**图片类**四类开放：音频没有"版本"一说，后端 `_HISTORY_KINDS` 里也没有 audio，
//    传进去只会吃一个 422。按钮的 v-if 与这里共用 HISTORY_KINDS，别只改一处。
const HISTORY_KINDS = ['character', 'scene', 'prop', 'image']
const historyFor = ref(null)      // 正在看历史的那个 item（null = 弹窗不显示）
const historyList = ref([])       // 后端给的 versions，**第一条是当前这版**（current: true）
const historyKeep = ref(0)        // 最多保留几版（后端给，别在前端写死）
const historyBusy = ref(false)    // 正在拉列表
const historyUsing = ref('')      // 正在切的那一版的 name（空串 = 没有在切）

// 版本目录名 20260919_051122_ab12cd 里的时间戳是 UTC，后端转成带时区的 ISO 给过来，
// 交给 Date 换成本地时间再显示 —— 直接截字符串会差 8 小时。
function historyStamp(v) {
  if (!v?.created_at) return '当前'
  const d = new Date(v.created_at)
  if (Number.isNaN(d.getTime())) return v.name || ''
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function humanSize(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

async function openHistory(it) {
  if (!HISTORY_KINDS.includes(it?.kind)) return
  historyFor.value = it
  historyList.value = []
  historyKeep.value = 0
  historyBusy.value = true
  try {
    // withTask 已把错误转成提示；失败时列表留空，弹窗自己会显示空态
    const res = await withTask((id) => api.materialHistory(id, it.kind, it.index))
    if (res) {
      historyList.value = res.versions || []
      historyKeep.value = res.keep || 0
    }
  } finally {
    historyBusy.value = false
  }
}

function closeHistory() {
  if (historyUsing.value) return
  historyFor.value = null
  historyList.value = []
}

// 切回某一版。后端会**先把当前这版也存进历史**再覆盖 —— 所以"切回去又后悔"还能再切回来，
// 前端不需要自己备份。切完必须重拉列表：当前这版换了，刚被顶掉的那版要出现在历史里。
// ⚠️ 同时**刚点的那一版会从列表里消失**（它就变成"当前这版"了，后端会把它的目录收掉）——
//    这是 2026-09-19 修掉"点多一张一样的"之后的样子，不是历史丢了，别当 bug 再改回去。
async function useHistory(v) {
  const it = historyFor.value
  if (!it || !v?.name || historyUsing.value) return
  historyUsing.value = v.name
  try {
    const res = await withTask((id) => api.useMaterialHistory(id, it.kind, it.index, v.name))
    if (res) {
      await refreshTask()
      const again = await withTask((id) => api.materialHistory(id, it.kind, it.index))
      if (again) {
        historyList.value = again.versions || []
        historyKeep.value = again.keep || 0
      }
      say(`「${it.name}」已切回 ${historyStamp(v)} 那一版`, 'ok')
    }
  } finally {
    historyUsing.value = ''
  }
}

// ---------------------------------------------------------------- 批量补齐
// 「AI 一键生成全部素材」= 让助手先按当前作品设定补齐条目，再逐项出图 / 合成音色。
// 两步都是现成接口：/api/assistant 补条目，reroll 系列出图/合成音色。
//
// ⚠️ **那颗按钮 2026-09-19 已从界面上撤掉**（斌哥：「你啥都没有，点这个意义不大」），
// 但这一整套（generateAll / fillMissing / groupBusy / progressNote）**留着没删** ——
// 模板里注释掉了按钮本体，要恢复照那段注释加回来即可。
//
// 分类级的「AI 生成 / 上传图片 / 录制音频」按钮 2026-09-15 已删（斌哥要求，对齐素材工坊）。
// 想单独补一类，走右侧 AI 助手对话，或顶部「添加素材」+ 卡片上的「上传」。
const groupBusy = ref('')
const progressNote = ref('')

// 给某一组（或全部）缺东西的项补齐：出图 / 合成音色。串行是故意的 ——
// 出图走日额度，并行打光就没法回滚了。
async function fillMissing(id, only = 'all') {
  const want = (k) => only === 'all' || only === k
  const jobs = []
  if (want('character')) {
    items.value.character.filter((x) => !x.ready).forEach((x) => jobs.push(['生成定妆照', () => api.rerollCharacter(id, x.index)]))
  }
  if (want('scene') || want('prop')) {
    ;[...items.value.scene, ...items.value.prop]
      .filter((x) => !x.ready && want(x.kind))
      .forEach((x) => jobs.push(['生成素材图', () => api.rerollAsset(id, x.index)]))
  }
  if (want('voice')) {
    items.value.voice.filter((x) => !x.ready).forEach((x) => jobs.push(['合成音色样本', () => api.rerollCharacterVoice(id, x.index, '')]))
  }
  if (!jobs.length) return 0

  let done = 0
  const fail = []
  for (const [label, run] of jobs) {
    try {
      await run()
    } catch (err) {
      fail.push(err.message)
    }
    done += 1
    progressNote.value = `${label}（${done}/${jobs.length}）`
  }
  progressNote.value = ''
  await refreshTask()
  if (fail.length) say(`完成 ${jobs.length - fail.length}/${jobs.length}，失败：${fail.join('；')}`, 'error')
  else say(`已补齐 ${jobs.length} 项素材`, 'ok')
  return jobs.length
}

async function generateAll() {
  groupBusy.value = 'all'
  try {
    await withTask(async (id) => {
      if (!chars.value.length && !assets.value.length) {
        await api.assistantChat({ taskId: id, message: '帮我为这部作品准备一套基础素材：1-2 个角色、1 个场景、1 个关键道具。' })
        await refreshTask()
      }
      const n = await fillMissing(id, 'all')
      if (!n) say('素材都已就绪，没有要补的')
    })
  } finally {
    groupBusy.value = ''
  }
}

// ---------------------------------------------------------------- 视频设置
const RATIOS = ['16:9', '9:16', '1:1']
const ratio = ref('16:9')

// 时长：可选可手写，区间 4–15 秒。
// 为什么下限是 4 而不是 0：H3 官方训练区间就是 4-15 秒，低于 4 秒画面容易飘，
// 后端本来也会把请求夹到 ≥4（start_segment_video 里那行 min(max(...))）。
// 输入框如果允许 0-3，用户填 2 会**被后端悄悄改成 4** —— 输入框就骗人了，
// 所以这里直接按真实可用的区间夹。
const DURATION_MIN = 4
const DURATION_MAX = 15
const DURATIONS = [5, 10, 15]
const duration = ref(10)
const durationText = ref('10')

function pickDuration(v) {
  duration.value = v
  durationText.value = String(v)
}

// 手写：回车 / 失焦时才归一化。边打字边夹会把想输入的 "15" 在打 "1" 时就改成 4，没法填。
function commitDuration() {
  const n = Number.parseFloat(durationText.value)
  if (!Number.isFinite(n)) {
    durationText.value = String(duration.value)
    return
  }
  const clamped = Math.min(DURATION_MAX, Math.max(DURATION_MIN, Math.round(n)))
  duration.value = clamped
  durationText.value = String(clamped)
}

// 清晰度 = 服务配置里「画质（总像素）」的镜像。两者是**同一个参数**（H3 的像素预算
// megapixels），所以这里不做第二个真值：点一下就写回服务配置，出片时也把当前值随请求下发
// （配置保存失败时，这一次出片仍按你眼前选的那档走，不会退回旧值）。
// 标签沿用设计稿的 720P / 1080P；H3 输出上限是 768p，所以 1080P 这档实际是 1344×768。
const QUALITY = [
  { mp: '0.4', label: '0.4MP', note: '最快 · 约 864×480' },
  { mp: '0.5', label: '0.5MP', note: '快 · 约 960×544' },
  { mp: '0.7', label: '0.7MP', note: '均衡 · 约 1152×640' },
  { mp: '0.9', label: '720P', note: '0.9MP · 约 1280×736' },
  { mp: '0.98', label: '1080P', note: '0.98MP · 1344×768（模型上限）' },
]
const quality = computed(() => String(props.cfg?.video_megapixels ?? '0.9'))
// 默认只露设计稿里的两档；配置里选了更快的档（0.4/0.5/0.7）就把它补成第一个，
// 否则会出现"一个 chip 都不高亮"，看起来像没生效。
const qualityOptions = computed(() => {
  const base = QUALITY.filter((q) => q.mp === '0.9' || q.mp === '0.98')
  const cur = QUALITY.find((q) => q.mp === quality.value)
  return cur && !base.includes(cur) ? [cur, ...base] : base
})
const qualityNote = computed(() => QUALITY.find((q) => q.mp === quality.value)?.note || '')

function pickQuality(mp) {
  emit('save-config', { video_megapixels: mp })
}

watch(() => props.project?.aspect_ratio, (v) => { if (v && RATIOS.includes(v)) ratio.value = v }, { immediate: true })

// 「视频模型 / 工作流」那一行连同 backend / workflowUrl / saveBackend / useWorkflowUrl
// 一起删了（2026-09-15 斌哥要求）：素材工坊的 02 里没有这一行，改后端和 ComfyUI 地址
// 走右上角「服务设置」。新建作品页不再重复这套入口。

// ---------------------------------------------------------------- 编排 + 出片
// 提示词两条路，对应"有没有文本 API"两种用户（与工作台同源）：
//   AI 帮我写 —— 中文描述 → LLM 按素材编号写成英文正文（要文本 API）
//   我自己写  —— 直接写英文正文，**全程不碰任何模型接口**（只要 ComfyUI 就能用）
const manualPrompt = ref(false)
const videoPrompt = ref('')          // AI 模式下的中文描述
const videoPromptDraft = ref('')     // 手写模式下的英文正文
const composing = ref(false)
const segJob = ref(null)
const composed = ref(null)           // 上一次编排结果（六段式正文 + 素材清单），用于预览
const errorMsg = ref('')
let segPoll = null

// ---------------------------------------------------------------- 首尾帧选择器
// 2026-09-19 斌哥定：首尾帧改成**显式挑**，不再是"勾一下就拿选中的第 2 张当尾帧"
// （那样尾帧是哪张完全由点选顺序决定，用户控制不了）。规则：
//   只挑首帧 → 以它开头｜只挑尾帧 → 以它收尾｜都挑 → 一头一尾｜都不挑 → 没有首尾帧
// 可挑的只有**场景**和**其他图片**：人物/道具的图是"参考"，不是"这一帧画面"，
// 拿定妆照当视频结尾会出来一张四视图或者一张正面照，没意义。
const framePickOpen = ref(false)
const framePick = ref({ first: null, last: null })   // 存素材 item（.key 就是 "scene:0" 这种引用）
const frameTarget = ref('first')                     // 当前在往哪个槽里放
// ⚠️ 切到 Ref2VA 工作流之后，首尾帧这一套**整个不成立**：那份工作流的 H3 节点
//    只有 ref_images.ref_image_N 参考口，没有 last_frame。挑了也是白挑
//    （后端会弹提醒，但更好的做法是干脆别摆出这个控件）。isRef2va 在最上面声明。
const FRAME_SLOTS = [
  { key: 'first', label: '首帧', hint: '没选 —— 视频从选中的素材自己起头' },
  { key: 'last', label: '尾帧', hint: '没选 —— 不锁定结尾' },
]
const framePickables = computed(() =>
  allItems.value.filter((it) => (it.kind === 'scene' || it.kind === 'image') && it.ready),
)
const framePickText = computed(() => {
  const { first, last } = framePick.value
  if (!first && !last) return '选择首尾帧（可选）'
  return [first && `首帧：${first.name}`, last && `尾帧：${last.name}`].filter(Boolean).join(' · ')
})
const framePickOn = computed(() => !!(framePick.value.first || framePick.value.last))

function openFramePick() {
  // 打开时把目标槽定位到"还没填的那个"，两个都填了就停在首帧
  frameTarget.value = framePick.value.first ? (framePick.value.last ? 'first' : 'last') : 'first'
  framePickOpen.value = true
}
function setFrameTarget(key) {
  frameTarget.value = key
}
function assignFrame(it) {
  const cur = frameTarget.value
  // 同一张图已经在另一个槽里 → 先把它从那边挪过来（一张图不能同时当首尾帧）
  for (const k of ['first', 'last']) {
    if (framePick.value[k]?.key === it.key) framePick.value[k] = null
  }
  framePick.value[cur] = it
}
function clearFrame(key) {
  framePick.value[key] = null
}

// 「让 AI 帮写」：把中文口语描述扩写成**中文**画面描述（2026-09-19 斌哥定）。
// 两步走的第一步 —— 用户看得懂、能改；点「生成视频」时才由 composeSegment 编成英文正文。
// 出片路径一个字没改：`description` 一直是"中文进、英文出"。
const optimizing = ref(false)

async function onOptimize() {
  errorMsg.value = ''
  if (!videoPrompt.value.trim()) {
    errorMsg.value = '先写一句想拍什么，再来让 AI 帮你写'
    return
  }
  if (!props.cfg?.text_api_key) {
    errorMsg.value = '还没有文本 API：去右上角「服务设置」填上文本模型的 Key'
    return
  }
  optimizing.value = true
  await withTask(async (id) => {
    try {
      const res = await api.optimizePrompt(id, {
        characters: selCharacters.value,
        props: selProps.value,
        scene: selScene.value,
        images: selImages.value,
        audios: selAudios.value,
        description: videoPrompt.value,
        duration: duration.value,
        use_voice: true,
      })
      // 直接覆盖输入框：用户拿到的是一段可以接着改的中文，而不是又一层只读预览
      videoPrompt.value = res.description || videoPrompt.value
      say('提示词已优化，可以接着改', 'ok')
    } catch (err) {
      errorMsg.value = err.message
    } finally {
      optimizing.value = false
    }
  })
  optimizing.value = false
}

async function onGenerate() {
  errorMsg.value = ''
  if (!(manualPrompt.value ? videoPromptDraft.value.trim() : videoPrompt.value.trim())) {
    errorMsg.value = manualPrompt.value
      ? '自己写模式下要填英文正文（至少写出这一段要发生什么）'
      : '先写一段描述：这一段视频要发生什么'
    return
  }
  if (!frameItems.value.length) {
    errorMsg.value = '先在 01 里点选至少一张图片素材（角色 / 场景 / 道具 / 其他图片）当首帧'
    return
  }
  composing.value = true
  await withTask(async (id) => {
    try {
      // 编排：中文描述 → Ref2VA 六段式英文正文。
      // 手写模式下后端走**直通组装**（全程零模型调用），与工作台一致。
      const payload = {
        characters: selCharacters.value,
        props: selProps.value,
        scene: selScene.value,
        images: selImages.value,
        audios: selAudios.value,
        duration: duration.value,
        use_voice: true,
      }
      if (manualPrompt.value) payload.video_prompt = videoPromptDraft.value
      else payload.description = videoPrompt.value
      composed.value = await api.composeSegment(id, payload)
      const started = await api.startSegmentVideo(id, {
        frames: frameRefs.value,
        // 首尾帧是**显式挑**的（空串 = 没挑那一头）。见上面 framePick 的说明。
        first_frame: framePick.value.first?.key || '',
        last_frame: framePick.value.last?.key || '',
        prompt: composed.value.prompt,
        // 基础模式（I2V / 首尾帧）出片时靠这句拼 overall_soundscape；
        // Ref2VA 的六段式自带那一段，传了也会被忽略。
        soundscape: composed.value.soundscape || '',
        duration: duration.value,
        megapixels: Number.parseFloat(quality.value) || undefined,
        // 只给「生成记录」留底：过几天回看时，一段英文正文（prompt）是看不懂
        // 自己当初想要什么的。手写模式下没有这一层，留空。
        note: manualPrompt.value ? '' : videoPrompt.value.trim(),
      })
      segJob.value = started
      // 选了却没带上的素材（没图 / 引用对不上）：后端现在会明说，别让它无声无息地少一张。
      // 2026-09-19 的坑：场景被静默丢掉，用户到 ComfyUI 里才发现只有一张角色图。
      if (started.missing?.length) {
        say(`这些素材这次没带上（还没有图）：${started.missing.join('、')}`, 'error', 8000)
      }
      // 工作流吃不下的参考图（I2V 工作流只吃首帧那一张）：也要说，别让人对着 ComfyUI 猜
      // "为什么场景图传上去了却没连上"。
      if (started.warning) say(started.warning, 'error', 10000)
      pollSegment(started.job_id)
    } catch (err) {
      errorMsg.value = err.message
    } finally {
      composing.value = false
    }
  })
  composing.value = false
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
        else say('视频已生成', 'ok')
        // 通知外层：磁盘上多了一段片，「生成记录」那个计数该刷新了。
        // 成没成都发 —— 失败的原因也在那条记录旁边看得到。
        emit('segment-done', st)
      }
    } catch {
      /* 单次轮询失败忽略，下一轮再试 */
    }
  }, 3000)
}

onBeforeUnmount(() => clearInterval(segPoll))

const running = computed(() => composing.value || segJob.value?.status === 'running')
</script>

<template>
  <div class="workbench">
    <!-- 01 准备素材 -->
    <section v-if="showMaterials" class="step">
      <header class="step-head">
        <span class="num">01</span>
        <div class="titles">
          <h2>准备素材</h2>
          <p>上传或添加你需要的素材，支持图片、音频等多种类型。</p>
        </div>
        <div class="head-actions">
          <!-- 「AI 一键生成全部素材（可选）」2026-09-19 撤掉。
               斌哥：「这个好像在这里也没用了，因为你啥都没有，点这个意义不大」——
               01 刚进来时六组都是空的，让 AI"补齐"等于凭空造一整套素材出来，
               跟用户自己想拍什么没关系。真要 AI 出素材，走单张卡片上的「AI 生成」，
               或者右侧 AI 助手对话（那里能说清要什么）。
               ⚠️ 只摘掉渲染：`generateAll()` / `fillMissing()` / `groupBusy` /
               `progressNote` 与下面那行 .progress-note 都**留着没删**，要恢复把下面这段加回来：

               <button class="btn-primary" type="button" :disabled="busy || !!groupBusy" @click="generateAll">
                 <span v-if="groupBusy === 'all'" class="spin" aria-hidden="true"></span>
                 <svg v-else viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 3.2l1.5 3.9 3.9 1.5-3.9 1.5L10 14l-1.5-3.9L4.6 8.6l3.9-1.5L10 3.2Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /><path d="M15.4 13.4l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z" fill="currentColor" /></svg>
                 {{ groupBusy === 'all' ? '生成中…' : 'AI 一键生成全部素材（可选）' }}
               </button> -->
          <button class="btn-ghost" type="button" :disabled="busy" @click="adding ? cancelAdd() : openAdd()">
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 4.5v11M4.5 10h11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
            {{ adding ? '取消添加' : '添加素材' }}
          </button>
        </div>
      </header>

      <!-- 添加素材：只有 5 个类型可选。点一个就立刻在下面那一组里新建一张空卡片，
           名字和上传都在那张卡片上做（所以这里没有名字框，也没有「添加」按钮）。 -->
      <div v-if="adding" class="addform">
        <div class="af-head">
          <span class="af-title">新增哪一类素材？</span>
          <button class="btn-ghost xs" type="button" @click="cancelAdd">取消</button>
        </div>
        <div class="seg af-kinds">
          <button
            v-for="k in ADD_KINDS"
            :key="k.key"
            type="button"
            :disabled="busy"
            @click="createEntry(k.key)"
          >{{ k.label }}</button>
        </div>
        <p class="af-hint">选一类，下面那一组里就会多一张卡片，名字和内容都在那张卡片上填。</p>
      </div>

      <p v-if="progressNote" class="progress-note">{{ progressNote }}</p>

      <!-- 素材分组：与作品工作台的「素材工坊」是同一套版式 ——
           一行小标题 + 卡片网格，空态就一行灰字。改这里必须同步改 MaterialStudio.vue。 -->
      <div class="mgroups">
        <section
          v-for="g in groups"
          :key="g.key"
          class="mgroup"
          :class="{ over: dropKey === g.key }"
          @dragover.prevent="dropKey = g.key"
          @dragleave="dropKey = ''"
          @drop.prevent="onDrop($event, g)"
        >
          <div class="mgroup-head">
            <span class="mlabel">{{ g.label }}</span>
            <span class="mcount">{{ g.items.length }} 项</span>
          </div>

          <div v-if="g.items.length" class="mgrid">
            <article
              v-for="it in g.items"
              :key="it.key"
              class="mcard"
              :class="{ voicing: it.kind === 'voice' }"
              :title="it.kind === 'voice' ? '音色跟着角色自动带上，不用单独选' : undefined"
            >
              <!-- 编辑态：整张卡片换成表单，点表单本身不要触发选中。
                   新建的空卡片也走这里（名字框 + 上传按钮都在这），
                   所以编辑态里必须留着「上传」—— 否则刚建完还得先保存一次才能传内容。
                   2026-09-15 斌哥定：这里**只填名字**，不要描述框。
                   ⚠️ 那之后两条"补描述/锚点"的路都断了：分镜工作台的 ProjectBrief
                   2026-09-17 撤掉、01 的「AI 一键生成全部素材」2026-09-19 也撤掉。
                   现在锚点只能由卡片上的「AI 生成」间接产出（Agent 会设计方案），
                   全应用再没有直接编辑锚点的地方。2026-09-17 问过斌哥，
                   他说"没事，先按照我说的做"= 暂不补编辑入口，别自作主张加。
                   锚点原值由 commitEdit 原样带回，不会被这次改动清掉。 -->
              <div v-if="editing === it.key" class="edit-form" @click.stop>
                <input v-model="editName" maxlength="40" placeholder="名字" aria-label="素材名字" @keydown.enter.prevent="commitEdit(it)" />
                <div class="edit-ops">
                  <label class="tiny mup" :title="`上传${isAudioKind(it.kind) ? '音频' : '图片'}（直接作为素材，不走生成）`">
                    <input type="file" hidden :accept="isAudioKind(it.kind) ? 'audio/*' : 'image/*'" :disabled="busy || !!uploading" @change="onPickFile($event, g, it)" />
                    {{ uploading === it.key ? '上传中…' : '上传' }}
                  </label>
                  <button class="btn-ghost xs" :class="{ danger: isFresh(it) }" type="button" @click="cancelEdit(it)">{{ isFresh(it) ? '删除' : '取消' }}</button>
                  <button class="btn-primary xs" type="button" :disabled="!editName.trim() || busy" @click="commitEdit(it)">保存</button>
                </div>
              </div>

              <template v-else>
                <div
                  class="mthumb"
                  :class="{ audio: isAudioKind(it.kind) }"
                  :title="it.url && !isAudioKind(it.kind) ? '点击看大图，可下载' : ''"
                  @click="onThumbClick($event, it)"
                >
                  <!-- 图片类用 img；音频类必须是 <audio> —— 用 <img> 指向 mp3
                       会渲染失败只剩 alt 文本（素材工坊那边踩过同一个坑） -->
                  <img v-if="it.url && it.kind !== 'voice' && it.kind !== 'audio'" :src="it.url" :alt="`${it.name} 素材图`" />
                  <audio
                    v-else-if="(it.kind === 'voice' || it.kind === 'audio') && (it.audioUrl || it.url)"
                    class="vaudio"
                    :src="it.audioUrl || it.url"
                    controls
                    preload="none"
                    @click.stop
                  ></audio>
                  <div v-else class="cempty">{{ emptyText(it) }}</div>
                  <!-- ⚠️ 这里**没有**任何选中开关（2026-09-19 斌哥定，改了两轮）：
                       第一轮去掉了"点整张卡片就选中"（点图片下面那块会莫名被选上），
                       第二轮把这个小圆圈也去掉了 —— 他原话："不用选这个，因为选了也不知道能有什么功能"。
                       确实如此：01 里选中的状态**切到「分镜工作台」就丢了**（同组件的两个实例，
                       picked 是组件内部状态），选完看不见任何结果。所以选中这件事统一收到
                       02 的「本次视频素材」那一侧（见下面 .picklist），那边选完立刻能看见效果。 -->
                </div>
                <div class="mmeta">
                  <strong>{{ it.name }}</strong>
                  <p>{{ it.desc }}</p>
                </div>
                <div class="mops">
                  <label class="tiny mup" :title="`上传${isAudioKind(it.kind) ? '音频' : '图片'}（直接作为素材，不走生成）`">
                    <input type="file" hidden :accept="isAudioKind(it.kind) ? 'audio/*' : 'image/*'" :disabled="busy || !!uploading" @change="onPickFile($event, g, it)" />
                    {{ uploading === it.key ? '上传中' : '上传' }}
                  </label>
                  <button v-if="isAudioKind(it.kind)" class="quiet tiny" type="button" :disabled="busy" @click.stop="startRecord(it)">{{ recording === it.key ? '停止录音' : '录制' }}</button>
                  <button v-else class="quiet tiny" type="button" :disabled="busy" @click.stop="startEdit(it)">编辑</button>
                  <button class="quiet tiny" type="button" :disabled="busy" @click.stop="removeItem(it)">删除</button>
                  <!-- 「生成历史」：重新生成是就地覆盖同名文件，旧的那版只有这里能找回来。
                       只给图片类四类 —— 音频没有版本（见 HISTORY_KINDS 那段注释）。 -->
                  <button
                    v-if="HISTORY_KINDS.includes(it.kind)"
                    class="quiet tiny"
                    type="button"
                    :disabled="busy"
                    :title="`看「${it.name}」出过的每一版，可以切回前面某一版`"
                    @click.stop="openHistory(it)"
                  >生成历史</button>
                  <!-- 五类素材的 AI 生成入口：写一段描述，交给对应 Agent 处理。
                       角色 → 形象图 + 四视图设定图；场景 → 单张空镜；道具 → 三视图设定图；
                       其他图片 → 一整张完整画面（常直接当首/尾帧）；
                       音色 → 配音 Agent 从音频池里挑一个音色再合成样本 -->
                  <button
                    v-if="['character', 'scene', 'prop', 'image', 'voice'].includes(it.kind)"
                    class="quiet tiny"
                    type="button"
                    :disabled="busy || aiBusy"
                    :title="`用一段描述生成「${it.name}」的${AI_LABEL[it.kind] || '素材'}`"
                    @click.stop="openAi(it)"
                  >AI 生成</button>
                </div>
              </template>
            </article>
          </div>
          <p v-else class="mempty">{{ g.empty }}</p>
        </section>
      </div>
    </section>

    <!-- 02 生成视频 -->
    <section v-if="showVideo" class="step">
      <header class="step-head">
        <span class="num">02</span>
        <div class="titles">
          <h2>生成视频</h2>
          <p>选择素材、设置参数和模型提示词，即可生成视频。</p>
        </div>
        <!-- 当前工作流常驻在抬头（2026-09-19）：多选 / 首尾帧这些规矩**全由它决定**，
             不摆出来用户就只能猜"为什么还能多选"（斌哥的原话就是这个）。 -->
        <span
          class="wf-chip"
          :class="{ on: isRef2va }"
          :title="isRef2va
            ? '当前工作流：Ref2VA · 全能参考 —— 选中的图只当参考（最多 3 张），没有首尾帧；要改去右上角「服务设置」'
            : '当前工作流：I2V · 首帧 / 首尾帧 —— 选中的图就是视频第一帧（只吃 1 张），可另挑一张尾帧；要改去右上角「服务设置」'"
        >{{ isRef2va ? 'Ref2VA · 全能参考' : 'I2V · 首帧 / 首尾帧' }}</span>
      </header>

      <div class="compose">
        <div class="panel picked-panel">
          <header class="panel-head">
            <span>本次视频素材</span>
            <span class="pcount">已选 {{ pickedItems.length }} 项{{ selVoiceCount ? ` · 含 ${selVoiceCount} 个音色` : '' }}</span>
          </header>
          <div v-if="pickedItems.length" class="picked-grid">
            <div
              v-for="it in pickedItems"
              :key="it.key"
              class="picked-card"
              :title="it.kind === 'character'
                ? (isRef2va
                  ? 'Ref2VA：进模型的就是这张四视图设定图（当参考图）'
                  : 'I2V：进模型的是正面定妆照 —— 这张四视图设定图只作总览（拼图当首帧会变成四个人并排）。'
                    + '要用这张多视角图当参考，请在右上角「服务设置」把视频工作流切成 Ref2VA')
                : ''"
            >
              <!-- 角色这一侧显示**会进模型的那张**（单张定妆照），不是卡片上的设定图：
                   sendUrl 就是为这件事加的，见 allItems 里的注释。 -->
              <img v-if="it.sendUrl || it.url" :src="it.sendUrl || it.url" :alt="it.name" />
              <div v-else-if="it.kind === 'audio'" class="ph">
                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M2.6 10h1.5l1.3-3.8 1.9 7.6 1.9-10.4 1.9 13.2 1.7-8.4 1.2 1.8h2.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </div>
              <div v-else class="ph">图</div>
              <div class="picked-meta"><strong>{{ it.name }}</strong><span>{{ { character: '角色', scene: '场景', prop: '道具', image: '图片', audio: '音频' }[it.kind] }}</span></div>
              <button class="unpick" type="button" :aria-label="`移除 ${it.name}`" @click="togglePick(it)">×</button>
            </div>
          </div>
          <div v-else class="picked-empty">
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2" stroke="currentColor" stroke-width="1.3" /><path d="M3.5 16.2 8.6 11l4 3.7 3.3-3 4.6 4.3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /><circle cx="8.6" cy="9.4" r="1.3" stroke="currentColor" stroke-width="1.2" /></svg>
            <strong>{{ selectableItems.length ? '还没有选素材' : '还没有素材' }}</strong>
            <span>{{ selectableItems.length ? '在下面点一下，把素材勾进这一段视频' : '先去「素材工坊」添几个角色 / 场景 / 道具 / 音频' }}</span>
          </div>

          <!-- 02 自带的素材选择入口，也是**全应用唯一的勾选处**（2026-09-19 定稿）。
               01 卡片上的选中入口全都撤了：在 01 里选完、切到分镜工作台就丢了
               （同一个组件的两个实例，picked 是组件内部状态），用户只会觉得"选了没用"。
               这里选完立刻能在上面的「本次视频素材」看见 —— 效果是明摆着的。
               音色不在其中：它跟着角色自动带上，不用选。 -->
          <div v-if="selectableItems.length" class="picklist">
            <!-- 这句要随工作流变：I2V 只吃一张图，多选没用；Ref2VA 能接三张。
                 灰底一行不起眼，但它是"为什么我只能勾一张"的唯一解释。 -->
            <span class="pl-head">
              <template v-if="isRef2va">
                点一下勾选素材，最多 3 张图一起进模型（音色跟着角色自动带上，不用选）
              </template>
              <template v-else>
                点一下勾选素材 —— 当前 I2V 工作流<b>只吃一张图</b>（这张就是视频的<b>第一帧</b>），
                再勾一张会替掉上一张；要让多张图都进模型，去「服务配置」把视频工作流切成 Ref2VA
              </template>
            </span>
            <div class="pl-grid">
              <button
                v-for="it in selectableItems"
                :key="it.key"
                type="button"
                class="pl-item"
                :class="{ on: isPicked(it) }"
                :title="isPicked(it) ? '取消选择' : '选进这一段视频'"
                @click="togglePick(it)"
              >
                <img v-if="it.url" :src="it.url" :alt="it.name" />
                <span v-else class="pl-ph">{{ { character: '角', scene: '景', prop: '道', image: '图', audio: '音' }[it.kind] || '素' }}</span>
                <em>{{ it.name }}</em>
              </button>
            </div>
          </div>
        </div>

        <div class="right-col">
          <div class="panel">
            <header class="panel-head">
              <span>视频提示词</span>
              <div class="pvhead">
                <!-- 「示例」按钮 2026-09-19 去掉（斌哥：「感觉不需要这个」）。
                     它做的事只是往输入框里塞一段写死的文案，占着标题行还容易让人
                     误以为是"示例结果"。SAMPLE_PROMPT 常量也一并删了。 -->
                <!-- 2026-09-19 斌哥定：这个按钮原来是"模式切换"（AI 帮我写 ↔ 我自己写），
                     可它顶着「让 AI 帮我写」的文案却从不真的帮写 —— 点一下只是切模式。
                     现在它变成真正的**优化提示词**：点了拿回一段中文，填进输入框供用户
                     过目、接着改；点「生成视频」时后端才把它编成英文正文（出片路径没变）。
                     手写英文那条路（给没有文本 API 的用户兜底）没删，降级成右边的小链接。 -->
                <button
                  v-if="!manualPrompt"
                  class="poptim"
                  type="button"
                  :disabled="optimizing || !videoPrompt.trim()"
                  :title="props.cfg?.text_api_key ? '把上面那段话写成更具体的画面描述（中文），拿到后你可以接着改' : '还没有文本 API：去右上角「服务设置」填上文本模型的 Key'"
                  @click="onOptimize"
                >
                  <span v-if="optimizing" class="spin" aria-hidden="true"></span>
                  <svg v-else viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M9 3.4l1.35 3.25L13.6 8l-3.25 1.35L9 12.6 7.65 9.35 4.4 8l3.25-1.35L9 3.4Z" fill="currentColor" /><path d="M15 12.2l.7 1.7 1.7.7-1.7.7-.7 1.7-.7-1.7-1.7-.7 1.7-.7.7-1.7Z" fill="currentColor" /></svg>
                  {{ optimizing ? '优化中…' : '让 AI 帮写' }}
                </button>
                <!-- 2026-09-19 斌哥定：这颗「我自己写英文」撤掉 ——
                     「不用自己写英文啊，因为你用中文写完提示词后，点击生成视频，后端会整理这些内容，
                       会把你提示词翻译成英文，而你自己看不到，然后他那边再生成视频」。
                     标题行现在只剩「让 AI 帮写」一颗。
                     按惯例**只摘渲染、留代码**：`manualPrompt` / `videoPromptDraft`、手写模式那个
                     textarea（`v-else` 分支）、`onGenerate` 里的分支逻辑与 `.pmode` 样式全部留着。
                     下面是可复原片段：

                <button
                  class="pmode"
                  type="button"
                  :class="{ manual: manualPrompt }"
                  :title="manualPrompt ? '回到 AI 帮写：用中文写，出片时由文本模型编成英文正文' : '直接写英文正文，全程不碰任何模型接口（只要 ComfyUI 就能用）'"
                  @click="manualPrompt = !manualPrompt"
                >{{ manualPrompt ? '回到 AI 帮写' : '我自己写英文' }}</button>
                -->
              </div>
            </header>
            <textarea
              v-if="!manualPrompt"
              v-model="videoPrompt"
              maxlength="1000"
              placeholder="描述一下你视频想讲什么…"
              aria-label="视频提示词"
            ></textarea>
            <textarea
              v-else
              v-model="videoPromptDraft"
              maxlength="6000"
              placeholder="直接写 H3 的英文正文，用素材编号引用：&lt;Subject 1&gt; / &lt;Picture 1&gt; / &lt;Audio 1&gt;…&#10;[Shot 1] Live-action, cinematic, …&#10;[Shot 2] At 00:05.000, …"
              aria-label="视频英文提示词"
            ></textarea>
            <div class="panel-foot">
              <span class="hint">{{ manualPrompt ? '手写模式 · 不调用文本模型' : (props.cfg?.text_api_key ? '由文本模型按素材编号翻成英文正文' : '还没有文本 API：去右上角「服务设置」填上文本模型的 Key') }}</span>
              <span class="counter">{{ (manualPrompt ? videoPromptDraft : videoPrompt).length }} / {{ manualPrompt ? 6000 : 1000 }}</span>
            </div>
          </div>

          <div class="panel settings">
            <div class="srow">
              <span class="slabel">视频比例</span>
              <div class="seg"><button v-for="r in RATIOS" :key="r" type="button" :class="{ on: ratio === r }" @click="ratio = r">{{ r }}</button></div>
            </div>
            <div class="srow">
              <span class="slabel">生成时长</span>
              <div class="seg"><button v-for="d in DURATIONS" :key="d" type="button" :class="{ on: duration === d }" @click="pickDuration(d)">{{ d }}s</button></div>
              <label class="dur-input" title="也可以自己填，超出区间会自动收到边界值">
                <input
                  v-model="durationText"
                  type="text"
                  inputmode="numeric"
                  aria-label="自定义生成时长（秒）"
                  @keydown.enter.prevent="commitDuration"
                  @blur="commitDuration"
                />
                <span>秒</span>
              </label>
              <span class="qhint">4–15 秒</span>
            </div>
            <div class="srow">
              <span class="slabel">清晰度</span>
              <div class="seg">
                <button
                  v-for="q in qualityOptions"
                  :key="q.mp"
                  type="button"
                  :class="{ on: quality === q.mp }"
                  :title="`${q.note}（同一个值也写在服务配置的「画质」里）`"
                  @click="pickQuality(q.mp)"
                >{{ q.label }}</button>
              </div>
              <span class="qhint">{{ qualityNote }}</span>
            </div>
            <!-- 「首尾帧」这一行 2026-09-19 从界面上摘掉了（斌哥：「ref2v 用不了首尾帧，留着没用」）。
                 背景：首尾帧只在 I2V / fl2va 那条链路上成立（Ref2VA 那份工作流的节点只有
                 ref_images 参考口，没有 last_frame）。他固定用 Ref2VA，所以这一行对他永远是死的。
                 **按惯例只摘渲染、留代码** —— framePick / openFramePick / 首尾帧弹窗（下面那个
                 Teleport）与 onGenerate 里的 first_frame/last_frame 全都还在，要恢复就把这一段加回来：

                   <div class="srow">
                     <span class="slabel">首尾帧</span>
                     <button class="flfbtn" type="button" :class="{ on: framePickOn }"
                             title="只在需要锁定这一段的开头或结尾时才用；不选就没有首尾帧"
                             @click="openFramePick">{{ framePickText }}</button>
                     <span class="qhint">{{ framePickOn ? '点一下改' : '没挑就用你勾的第一张图当首帧' }}</span>
                   </div>

                 "第一张图当首帧"这条信息没丢，挪到下面勾选条的提示里了。 -->
            <button class="generate" type="button" :disabled="running || busy" @click="onGenerate">
              <span v-if="running" class="spin" aria-hidden="true"></span>
              <svg v-else viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M7 5.4v9.2l7.4-4.6L7 5.4Z" fill="currentColor" /></svg>
              {{ segJob?.status === 'succeeded' ? '再生成一条' : composing ? '编排中…' : segJob?.status === 'running' ? '出片中…' : '生成视频' }}
            </button>
          </div>
        </div>
      </div>

      <p v-if="errorMsg" class="err" role="alert">{{ errorMsg }}</p>

      <!-- 编排结果预览：出片前能核对正文与素材对应关系，出问题不必等视频跑完才发现。
           ⚠️ 2026-09-18 斌哥定：**六段式全文与 ComfyUI 连线单一律不进界面**。
           它们必须原样存在于发给模型的报文里（H3 官方格式要求，删了参考一致性就掉），
           但摆在界面上是一大坨编号声明，观感很差。
           界面只留三样，全是人话：
             ① 素材对应关系 —— 后端 slots[].human（「图片 1 作为「林晚」的外貌锁定」）
             ② 正文 —— 真正描述画面那一段
             ③ 提醒 —— 后端返回的 warnings
           想看"发给模型的原样"去「生成记录」里那一格的「用了什么」—— 边车 json 存了整份。
           "谁对应谁"是**后端算的**（选素材时它就知道名字/图片/音色），前端不参与推导。 -->
      <div v-if="composed" class="promptout fade-in">
        <header class="po-head">
          <span class="tag accent">提示词已编排</span>
          <span class="po-hint">出片前核对一下：下面这段就是模型要照着拍的</span>
          <button class="link" type="button" @click="composed = null">收起</button>
        </header>

        <ul v-if="(composed.slots || []).length" class="po-slots">
          <li v-for="s in composed.slots" :key="s.tag">{{ s.human }}</li>
        </ul>

        <pre class="po-prompt">{{ composed.video_prompt || composed.prompt }}</pre>

        <ul v-if="(composed.warnings || []).length" class="po-warn">
          <li v-for="(w, i) in composed.warnings" :key="i">{{ w }}</li>
        </ul>
      </div>

      <div v-if="segJob" class="out fade-in">
        <span class="tag" :class="segJob.status === 'succeeded' ? 'mint' : segJob.status === 'failed' ? 'danger' : 'accent'">
          {{ segJob.status === 'succeeded' ? '出片完成' : segJob.status === 'failed' ? '出片失败' : '出片中' }}
          <template v-if="segJob.mode"> · {{ String(segJob.mode).toUpperCase() }}</template>
        </span>
        <video v-if="segJob.video_url" :src="segJob.video_url" controls class="segvideo"></video>
        <p v-if="segJob.status === 'failed'" class="segerr">{{ segJob.error }}</p>
      </div>
    </section>

    <!-- AI 生成：一段描述 → 对应 Agent 处理。
         角色出「正面定妆照 + 四视图设定图」，场景出「单张空镜（无人物）」，
         道具出「三视图」，其他图片出「一整张完整画面（常直接当首/尾帧）」，
         音色则由配音 Agent 从音频池里挑一个 id 再合成样本（多一个性别选择）。
         图片类描述只服务于这次出图，不写回卡片。Ctrl/⌘+Enter 也能提交。
         ⚠️ 必须 Teleport 到 body：外层 .create-shell 带 fade-in 动画（transform），
         会给 position: fixed 造出一个新的包含块，弹窗就会被钉在长文档底部而不是
         盖在视口上（实测踩过：y 坐标跑到 838，视口外）。 -->
    <Teleport to="body">
      <div v-if="aiFor" class="pmask" @click.self="closeAi">
        <div class="pdialog" role="dialog" aria-modal="true" aria-label="AI 生成素材图">
          <header class="pd-head">
            <h3>AI 生成「{{ aiFor.name }}」的{{ AI_LABEL[aiFor.kind] || '素材图' }}</h3>
            <button class="btn-ghost xs" type="button" :disabled="aiBusy" @click="closeAi">关闭</button>
          </header>
          <p v-if="aiFor.kind === 'character'" class="pd-hint">
            描述你想要的角色长什么样 —— 风格、年龄、发型发色、服装、气质都可以写。
            这段话只用于这次出图，不会改写角色卡片上的内容。
            出图时会一次给两张：<b>正面定妆照</b>（用你选的比例，出片拿它当首帧）+
            <b>四视图设定图</b>（固定 16:9 横版：最左一张面部特写，右边依次正面 / 标准侧面 / 背面全身）。
          </p>
          <p v-else-if="aiFor.kind === 'scene'" class="pd-hint">
            描述你想要的场景 —— 是什么地方、空间与材质、想要的光线氛围。
            会先交给场景 Agent 扩写成「环境 + 镜头 + 光影」的空镜描述，画面里不会出现人物。
          </p>
          <p v-else-if="aiFor.kind === 'image'" class="pd-hint">
            描述你要的画面 —— 画面里有什么、在做什么、处在什么环境、想要的光线与机位。
            会先交给画面 Agent 扩写成一段完整画面描述，再出图。这类图常被直接当首帧 / 尾帧用，
            所以写得越具体，接戏越顺。
          </p>
          <p v-else-if="aiFor.kind === 'prop'" class="pd-hint">
            描述这件道具 —— 是什么、大致尺寸、形状结构、材质与颜色、新旧磨损。
            会先交给道具 Agent 扩写，再出一张三视图（正视 / 侧视 / 后视横向并排）。这段话只用于这次出图。
          </p>
          <p v-else class="pd-hint">
            描述你想要的声音 —— 年龄感、音色（清亮 / 沙哑 / 低沉 / 甜美）、语速、气质都可以写。
            会先交给配音 Agent，从当前音频后端的音色池里挑一个最贴的，再用它合成一段样本。
            注意音色是「预置的」，只能在池子里挑，不会凭空造出一个新音色。
          </p>
          <textarea
            v-model="aiPrompt"
            :maxlength="aiMaxLen"
            :disabled="aiBusy"
            :placeholder="AI_PLACEHOLDER[aiFor.kind] || ''"
            :aria-label="`${AI_LABEL[aiFor.kind] || '素材图'}描述`"
            @keydown.enter.ctrl.prevent="submitAi"
            @keydown.enter.meta.prevent="submitAi"
          ></textarea>
          <footer class="pd-foot">
            <!-- 三视图固定 16:9（三个视角要横向并排），所以道具不给比例选择，改说明一句 -->
            <span v-if="aiFor.kind === 'prop'" class="pd-count">三视图固定 16:9（三个视角横向并排）</span>
            <!-- 音色用不到比例，这里换成性别选择。性别是硬约束（女主配男声是硬错），
                 所以显式选，后端也会拿它校验 Agent 挑回来的音色。 -->
            <div v-else-if="aiFor.kind === 'voice'" class="pd-ratio">
              <span class="pd-rlabel">性别</span>
              <div class="seg">
                <button
                  v-for="g in GENDERS"
                  :key="g.key"
                  type="button"
                  :class="{ on: aiGender === g.key }"
                  :disabled="aiBusy"
                  title="音色池按性别分，选错会配到明显不符的声音"
                  @click="aiGender = g.key"
                >{{ g.label }}</button>
              </div>
            </div>
            <div v-else class="pd-ratio">
              <span class="pd-rlabel">比例</span>
              <div class="seg">
                <button
                  v-for="r in AI_RATIOS"
                  :key="r"
                  type="button"
                  :class="{ on: aiRatio === r }"
                  :disabled="aiBusy"
                  :title="aiFor.kind === 'character'
                    ? `正面定妆照用 ${r}。四视图设定图固定 16:9 —— 四个视角要并排，竖版排不下`
                    : `图片用 ${r}（默认跟随作品画幅）`"
                  @click="aiRatio = r"
                >{{ r }}</button>
              </div>
            </div>
            <span class="pd-count">{{ aiPrompt.length }} / {{ aiMaxLen }}</span>
            <button class="btn-primary" type="button" :disabled="!aiPrompt.trim() || aiBusy" @click="submitAi">
              <span v-if="aiBusy" class="spin" aria-hidden="true"></span>
              {{ aiBusy ? '生成中…' : 'AI 生成' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>

    <!-- 首尾帧选择器（2026-09-19）。同样 Teleport 到 body —— 外层 .create-shell
         带 fade-in 动画（transform），直接放组件里 position: fixed 会被困在长文档底部。
         交互：上面两个槽（首帧 / 尾帧），点一个把它变成"当前在填的"，再点下面的图放进去。
         只挑一头也合法 —— 只挑首帧就以它开头，只挑尾帧就以它收尾，都不挑就没有首尾帧。 -->
    <Teleport to="body">
      <div v-if="framePickOpen" class="pmask" @click.self="framePickOpen = false">
        <div class="fdialog" role="dialog" aria-modal="true" aria-label="选择首尾帧">
          <header class="pd-head">
            <h3>首尾帧（可选）</h3>
            <button class="btn-ghost xs" type="button" @click="framePickOpen = false">关闭</button>
          </header>
          <p class="pd-hint">
            只在需要锁定这一段的<b>开头</b>或<b>结尾</b>时才用 —— 不选就没有首尾帧，视频按选中的素材自己演。
            可选的是<b>场景</b>和<b>其他图片</b>：人物和道具的图不能当首尾帧（它们是"参考"，
            不是"这一帧画面"，拿定妆照当结尾会出来一张设定图）。
          </p>

          <div class="fslots">
            <!-- 槽用 div+role 而不是 button：里面还有个清空的 × 按钮，
                 button 套 button 是非法 HTML（浏览器会把内层拆出去）。 -->
            <div v-for="slot in FRAME_SLOTS" :key="slot.key"
                 class="fslot" :class="{ active: frameTarget === slot.key, filled: !!framePick[slot.key] }"
                 role="button" tabindex="0" :aria-pressed="frameTarget === slot.key"
                 @click="setFrameTarget(slot.key)"
                 @keydown.enter.prevent="setFrameTarget(slot.key)"
                 @keydown.space.prevent="setFrameTarget(slot.key)">
              <span class="fslabel">{{ slot.label }}</span>
              <template v-if="framePick[slot.key]">
                <img v-if="framePick[slot.key].url" :src="framePick[slot.key].url" :alt="framePick[slot.key].name" />
                <em>{{ framePick[slot.key].name }}</em>
                <button class="fx" type="button" :aria-label="`清空${slot.label}`"
                        @click.stop="clearFrame(slot.key)">×</button>
              </template>
              <span v-else class="fsempty">{{ slot.hint }}</span>
            </div>
          </div>

          <div class="fgrid">
            <button v-for="it in framePickables" :key="it.key" type="button"
                    class="fcard"
                    :class="{ on: framePick.first?.key === it.key || framePick.last?.key === it.key }"
                    :title="`放进「${frameTarget === 'first' ? '首帧' : '尾帧'}」`"
                    @click="assignFrame(it)">
              <img v-if="it.url" :src="it.url" :alt="it.name" />
              <span v-else class="fph">{{ it.kind === 'scene' ? '景' : '图' }}</span>
              <em>{{ it.name }}</em>
              <span class="ftag">{{ it.kind === 'scene' ? '场景' : '其他图片' }}</span>
            </button>
            <p v-if="!framePickables.length" class="fempty">
              这个作品里还没有可用的场景图或其他图片 —— 先在 01 里加一个。
            </p>
          </div>

          <footer class="ffoot">
            <span class="fhint">
              当前往「{{ frameTarget === 'first' ? '首帧' : '尾帧' }}」里放
            </span>
            <button class="btn-ghost xs" type="button" @click="clearFrame('first'); clearFrame('last')">清空首尾帧</button>
            <button class="btn-primary xs" type="button" @click="framePickOpen = false">完成</button>
          </footer>
        </div>
      </div>
    </Teleport>

    <!-- 素材图预览：看大图 + 下载。同样 Teleport 到 body（外层有 fade-in 动画，
         直接放组件里 position: fixed 会被困在长文档底部）。 -->
    <Teleport to="body">
      <div v-if="previewItem" class="pmask" @click.self="previewItem = null">
        <div class="pdialog pv-dialog" role="dialog" aria-modal="true" :aria-label="`${previewItem.name} 素材图预览`">
          <header class="pd-head">
            <h3>{{ previewItem.name }}</h3>
            <button class="btn-ghost xs" type="button" @click="previewItem = null">关闭</button>
          </header>
          <!-- 角色有两张图（单张定妆照 / 四视图设定图），哪张进模型取决于工作流。
               不说明白的话，用户在 ComfyUI 里看到另一张会以为传错了（2026-09-19 斌哥问过）。 -->
          <p v-if="previewItem.kind === 'character' && previewItem.sendUrl" class="pd-hint pv-note">
            <template v-if="isRef2va">
              这是四视图设定图，<b>出片时进模型的就是这一张</b>（Ref2VA 当参考图）——
              一张里带齐大特写和正 / 侧 / 背三个全身，参考信息最全。
            </template>
            <template v-else>
              这是<b>四视图设定图</b>（给人核对形象用）。当前是 I2V 工作流，进模型的是同一张卡片的
              <b>正面定妆照</b>（单张）—— 拼图当首帧会变成"画面里有四个人"。
              想让这张设定图也进模型，把「服务配置」里的视频工作流切成 Ref2VA。
            </template>
          </p>
          <img class="pv-img" :src="previewItem.url" :alt="`${previewItem.name} 素材图`" />
          <footer class="pd-foot">
            <span class="pd-count">{{ previewItem.desc }}</span>
            <!-- 用 <a download> 而不是 JS 触发：同源静态文件，浏览器直接落盘，
                 文件名就是服务器上的名字（角色名 / 角色名_sheet） -->
            <a
              class="pv-dl"
              :href="previewItem.url"
              :download="downloadName(previewItem)"
              title="把这张图存到本地，之后可以当素材直接用"
            >
              <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 3.2v8.6m0 0 3.4-3.4M10 11.8 6.6 8.4M4 15.4h12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
              下载图片
            </a>
          </footer>
        </div>
      </div>
    </Teleport>
    <!-- 生成历史（2026-09-19）。Teleport 到 body 的理由同上面两个弹窗：
         外层 .create-shell 带 fade-in 动画（transform），position: fixed 会被困在长文档底部。 -->
    <Teleport to="body">
      <div v-if="historyFor" class="pmask" @click.self="closeHistory">
        <div class="pdialog hd-dialog" role="dialog" aria-modal="true" :aria-label="`${historyFor.name} 生成历史`">
          <header class="pd-head">
            <h3>「{{ historyFor.name }}」的生成历史</h3>
            <button class="btn-ghost xs" type="button" :disabled="!!historyUsing" @click="closeHistory">关闭</button>
          </header>
          <p class="pd-hint">
            每次重新生成、或覆盖上传一张，旧的那版都会先存下来（最多留最近 {{ historyKeep || 10 }} 版）。
            点「用这版」就把它换回来：它自己成了「当前这版」（列表里不会再重复一条），
            被换下去的那版会存进历史，所以换回来之后还能再换过去。
          </p>
          <p v-if="historyBusy" class="hd-empty">正在读取…</p>
          <p v-else-if="!historyList.length" class="hd-empty">
            还没有历史版本 —— 这一版是第一次生成。重新生成一次之后，这里就会出现可以切回的旧版。
          </p>
          <div v-else class="hdgrid">
            <div v-for="v in historyList" :key="v.name || 'current'" class="hdcard" :class="{ on: v.current }">
              <img v-if="v.thumb" :src="v.thumb" :alt="`${historyFor.name} ${v.current ? '当前这版' : historyStamp(v)}`" />
              <div v-else class="hdph">无图</div>
              <div class="hdmeta">
                <strong>{{ v.current ? '当前这版' : historyStamp(v) }}</strong>
                <span>{{ humanSize(v.bytes) }} · {{ (v.files || []).length }} 张</span>
              </div>
              <button
                v-if="!v.current"
                class="btn-ghost xs hduse"
                type="button"
                :disabled="!!historyUsing || busy"
                @click="useHistory(v)"
              >{{ historyUsing === v.name ? '切换中…' : '用这版' }}</button>
              <span v-else class="hdon">正在使用</span>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.workbench { display: flex; flex-direction: column; gap: 20px; }
.step { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); padding: 18px 16px 18px; }
.step-head { display: flex; align-items: flex-start; gap: 11px; margin-bottom: 16px; }
.num { font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--accent); padding-top: 3px; }
.titles { flex: 1; min-width: 0; }
/* 02 抬头的工作流标签：一眼知道"现在按哪套规矩来"（能不能多选、有没有首尾帧都看它） */
.wf-chip { flex: none; align-self: flex-start; font-size: 11px; padding: 3px 9px; border-radius: 20px; background: var(--surface-2); color: var(--fg-2); border: 1px solid var(--line-soft); white-space: nowrap; }
.wf-chip.on { background: var(--accent-dim); color: var(--accent); border-color: var(--accent); }
.titles h2 { margin: 0; font-size: 16px; font-weight: 600; }
.titles p { margin: 3px 0 0; font-size: 12px; color: var(--fg-2); }
.head-actions { display: flex; align-items: center; gap: 8px; flex: none; }
.btn-primary { display: inline-flex; align-items: center; gap: 7px; padding: 9px 15px; border: 1px solid var(--accent); border-radius: var(--r-sm); background: var(--accent); color: var(--accent-ink); font-size: 12.5px; font-weight: 600; cursor: pointer; }
.btn-primary:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-primary svg { width: 15px; height: 15px; }
.btn-primary.sm { padding: 8px 13px; }
.btn-ghost { display: inline-flex; align-items: center; gap: 6px; padding: 9px 14px; border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--surface); color: var(--fg); font-size: 12.5px; cursor: pointer; }
.btn-ghost:hover:not(:disabled) { background: var(--surface-2); border-color: #c6cbbf; }
.btn-ghost:disabled { opacity: .5; cursor: not-allowed; }
.btn-ghost svg { width: 14px; height: 14px; }
.btn-ghost.xs { padding: 5px 10px; font-size: 11.5px; }
/* 添加素材：只有「标题 + 取消」和 5 个类型按钮两行 —— 没有输入框，
   所以整体是竖向的，不再是原来那条横向表单 */
.addform { display: flex; flex-direction: column; gap: 9px; margin: -6px 0 14px; padding: 11px 12px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm); }
.af-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.af-title { font-size: 12.5px; font-weight: 600; }
.af-hint { margin: 0; font-size: 11px; color: var(--fg-3); }
.af-kinds button { padding: 6px 13px; font-size: 12px; }
.progress-note { margin: -6px 0 12px; font-size: 12px; color: var(--mint); }

/* 素材分组：版式与作品工作台的「素材工坊」（MaterialStudio.vue）逐条对齐 ——
   一行小标题（标签 + 计数）+ 卡片网格，空态就一行灰字。
   素材区不该有自己的长相：用户在「新建作品」和「素材工坊」之间来回切，
   同一件事应该长得一样。
   ⚠️ 改这一块必须同步改 MaterialStudio.vue 里的同名规则，否则两边又分叉。 */
.mgroups { display: flex; flex-direction: column; gap: 16px; }
/* 与素材工坊同值：标题只有「标签 + 计数」两个词，按基线对齐 */
.mgroup-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.mlabel { font-size: 13px; font-weight: 600; }
.mcount { font-size: 11px; color: var(--fg-3); }
/* 拖文件到某一类上时给点反馈（分组级的上传按钮已删，拖拽是这一层唯一的批量入口） */
.mgroup.over .mlabel, .mgroup.over .mempty { color: var(--accent); }
.mgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 10px; }
/* 01 的卡片**整张都不可点**（2026-09-19 定稿）：点图片=看大图，点按钮=操作，其余不响应。
   历史：卡片级 @click=togglePick（点图片下面那块会被选中）→ 图片右上角小圆圈 → 都撤了。
   选中统一在 02 的 .picklist 上做。 */
.mcard { border: 1px solid var(--line); border-radius: var(--r-sm); overflow: hidden; background: var(--surface); cursor: default; transition: border-color .18s, box-shadow .18s; }
.mcard:hover { border-color: var(--accent); }
.mcard.voicing { cursor: default; }
.mthumb { position: relative; aspect-ratio: 16 / 10; background: var(--surface-3); display: flex; align-items: center; justify-content: center; overflow: hidden; }
.mthumb img { width: 100%; height: 100%; object-fit: cover; display: block; cursor: pointer; }
.mthumb.audio { background: var(--accent-dim); }
.cempty { font-size: 12px; color: var(--fg-3); }
.mmeta { padding: 8px 10px 4px; }
.mmeta strong { display: block; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.mmeta p { margin: 2px 0 0; font-size: 11px; color: var(--fg-3); overflow-wrap: anywhere; }
.mops { display: flex; gap: 4px; padding: 2px 6px 7px; flex-wrap: wrap; }
.mops button { white-space: nowrap; }
.mup { cursor: pointer; color: var(--mint); }
.mup:hover { background: var(--mint-dim); }
.mup input { display: none; }
.vaudio { width: 100%; height: 34px; }
.mempty { margin: 0; font-size: 12px; color: var(--fg-3); padding: 10px 2px; }
.mcard .edit-form { padding: 10px; }

/* 「上传 / 编辑 / 删除」这排小按钮的尺寸，与素材工坊的 .tiny 一致 */
.tiny { font-size: 11px; padding: 4px 9px; }

.edit-form { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.edit-form input { padding: 6px 9px; font-size: 11.5px; }
/* 「上传」靠左、「取消 / 保存」靠右：margin-right:auto 把后半排挤到右边去 */
.edit-ops { display: flex; align-items: center; gap: 6px; justify-content: flex-end; }
.edit-ops .mup { margin-right: auto; }

/* 02：左素材 / 右提示词 + 参数 */
.compose { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); gap: 14px; align-items: start; }
.panel { border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--surface); padding: 12px 14px; }
.picked-panel { display: flex; flex-direction: column; min-height: 100%; }
.panel-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 10px; font-size: 12.5px; font-weight: 600; }
.pcount { font-weight: 400; font-size: 11px; color: var(--fg-3); }
.link { padding: 0; border: 0; background: none; color: var(--accent); font-size: 11.5px; cursor: pointer; }
/* 全局 button:hover 是 (0,2,1)，这里必须补上背景/边框，否则「示例」会变成一块灰底按钮 */
.link:hover { background: none; border-color: transparent; text-decoration: underline; }
.picked-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.picked-card { position: relative; width: 104px; border: 1px solid var(--line); border-radius: var(--r-xs); overflow: hidden; background: var(--surface-2); }
.picked-card img { width: 100%; height: 60px; object-fit: cover; display: block; }
.picked-card .ph { height: 60px; display: grid; place-items: center; font-size: 11px; color: var(--fg-3); background: var(--surface-3); }
.picked-card .ph svg { width: 20px; height: 20px; }
.picked-meta { padding: 4px 6px 5px; }
.picked-meta strong { display: block; font-size: 11.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picked-meta span { font-size: 10px; color: var(--fg-3); }
.unpick { position: absolute; top: 3px; right: 3px; width: 17px; height: 17px; padding: 0; border-radius: 50%; border-color: transparent; background: #0000008c; color: #fff; font-size: 11px; line-height: 1; }
.unpick:hover:not(:disabled) { background: #000000c4; border-color: transparent; }
.picked-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px; min-height: 186px; margin-top: 2px; border: 1px dashed var(--line); border-radius: var(--r-sm); background: var(--surface-2); color: var(--fg-3); text-align: center; padding: 18px; }
.picked-empty svg { width: 28px; height: 28px; }
.picked-empty strong { font-size: 12.5px; color: var(--fg-2); font-weight: 600; }
.picked-empty span { font-size: 11px; }
/* 只挂 02 时的素材勾选条（分镜工作台那一侧用）。与 .picked-grid 是一对：
   上面显示"已选了什么"，下面负责"去选"。 */
.picklist { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line-soft); }
.pl-head { display: block; margin-bottom: 9px; font-size: 11.5px; color: var(--fg-3); }
.pl-grid { display: flex; flex-wrap: wrap; gap: 7px; }
.pl-item { display: inline-flex; align-items: center; gap: 7px; padding: 4px 10px 4px 4px; border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--surface); color: var(--fg); font-size: 12px; cursor: pointer; transition: border-color 0.15s var(--ease), background 0.15s var(--ease); }
.pl-item:hover { border-color: var(--fg-3); }
.pl-item.on { border-color: var(--accent); background: var(--accent-dim); }
.pl-item img { display: block; width: 26px; height: 26px; border-radius: var(--r-xs); object-fit: cover; }
.pl-ph { display: grid; place-items: center; width: 26px; height: 26px; border-radius: var(--r-xs); background: var(--surface-2); color: var(--fg-3); font-size: 11px; }
.pl-item em { font-style: normal; max-width: 132px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.right-col { display: flex; flex-direction: column; gap: 12px; }
.right-col textarea { min-height: 132px; }
.panel-foot { display: flex; justify-content: space-between; gap: 10px; margin-top: 6px; font-size: 11px; color: var(--fg-3); }
.hint { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.counter { flex: none; font-variant-numeric: tabular-nums; }
.settings { display: flex; flex-direction: column; gap: 10px; }
.srow { display: flex; align-items: center; gap: 10px; font-size: 12px; flex-wrap: wrap; }
.slabel { flex: none; width: 100px; color: var(--fg-2); white-space: nowrap; }
.seg { display: flex; gap: 4px; flex-wrap: wrap; }
.seg button { padding: 5px 11px; font-size: 11.5px; border-radius: var(--r-xs); }
.seg button.on { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.srow select { width: auto; min-width: 148px; padding: 6px 30px 6px 10px; font-size: 11.5px; }
/* 时长手写框：跟 preset 按钮同高，窄一点，输入框本体不抢宽度 */
.dur-input { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--fg-2); }
.dur-input input { width: 52px; padding: 5px 8px; font-size: 11.5px; text-align: center; font-variant-numeric: tabular-nums; }
.qhint { font-size: 10.5px; color: var(--fg-3); }
.generate { display: inline-flex; align-items: center; justify-content: center; gap: 7px; margin-top: 2px; padding: 12px; border: 1px solid var(--accent); border-radius: var(--r-sm); background: var(--accent); color: var(--accent-ink); font-size: 13.5px; font-weight: 600; cursor: pointer; }
.generate:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
.generate:disabled { opacity: .55; cursor: not-allowed; }
.generate svg { width: 15px; height: 15px; }
.err { margin: 12px 0 0; padding: 9px 12px; font-size: 12px; color: var(--danger); background: var(--danger-dim); border: 1px solid #efccc8; border-radius: var(--r-sm); }
.out { margin-top: 14px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.segvideo { max-width: 380px; width: 100%; border-radius: var(--r-sm); border: 1px solid var(--line); }
.segerr { margin: 0; flex: 1; min-width: 200px; font-size: 12px; color: var(--danger); }

@media (max-width: 1180px) {
  .compose { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 760px) {
  .step-head { flex-wrap: wrap; }
  .head-actions { width: 100%; }
  .head-actions .btn-primary, .head-actions .btn-ghost { flex: 1; justify-content: center; }
  .slabel { width: 100%; }
}
/* AI 生成角色图的弹窗。
   fixed 铺满 + 自己一套类名：这个入口只有「新建作品」页有（另一个平行组件
   MaterialStudio 没这个按钮），不为了一个弹窗去动全局 dialog 体系。 */
.pmask { position: fixed; inset: 0; z-index: 60; background: rgba(32, 26, 22, .42); display: flex; align-items: center; justify-content: center; padding: 24px; }
.pdialog { width: min(560px, 100%); display: flex; flex-direction: column; gap: 10px; padding: 16px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); box-shadow: 0 18px 48px rgba(32, 26, 22, .22); }
.pd-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.pd-head h3 { margin: 0; font-size: 14px; }
/* 说明文案要写清"这段描述不落库"，否则用户会以为角色卡片上的描述被改了 */
.pd-hint { margin: 0; font-size: 11.5px; line-height: 1.65; color: var(--fg-3); }
.pdialog textarea { min-height: 104px; resize: vertical; font-size: 12.5px; line-height: 1.6; font-family: inherit; }
.pd-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.pd-count { font-size: 11px; color: var(--fg-3); }
/* 比例选择靠左（margin-right:auto 把字数与按钮挤到右边），窄屏会自动折行 */
.pd-ratio { display: flex; align-items: center; gap: 6px; margin-right: auto; }
.pd-rlabel { font-size: 11px; color: var(--fg-3); }
.pd-ratio .seg button { font-size: 11px; padding: 4px 9px; }
/* 素材图预览：比提示词弹窗宽，图按容器缩放不溢出 */
.pv-dialog { width: min(880px, 100%); }
.pv-img { display: block; width: 100%; max-height: 62vh; object-fit: contain; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm); }
/* 下载用 <a>，不能指望 button 的样式，这里自己写全 */
.pv-dl { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; font-size: 12.5px; font-weight: 600; color: #fff; background: var(--accent); border: 1px solid var(--accent); border-radius: var(--r-sm); text-decoration: none; cursor: pointer; }
.pv-dl:hover { filter: brightness(1.07); }
.pv-dl svg { width: 15px; height: 15px; }

/* 生成历史弹窗：版本网格。比提示词弹窗宽 —— 一屏要放下好几版才好对比。
   当前这版给强调色边框，一屏里得一眼看出"我现在用的是哪一版"。 */
.hd-dialog { width: min(760px, 100%); max-height: 86vh; overflow: auto; }
.hdgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.hdcard { display: flex; flex-direction: column; gap: 6px; padding: 7px; border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--surface); }
.hdcard.on { border-color: var(--accent); background: var(--accent-dim); }
.hdcard img { display: block; width: 100%; aspect-ratio: 16 / 10; object-fit: cover; border-radius: 6px; background: var(--surface-2); }
.hdph { display: grid; place-items: center; aspect-ratio: 16 / 10; border-radius: 6px; background: var(--surface-2); color: var(--fg-3); font-size: 12px; }
.hdmeta { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.hdmeta strong { font-size: 11.5px; }
.hdmeta span { font-size: 10.5px; color: var(--fg-3); }
.hduse { width: 100%; justify-content: center; }
.hdon { padding: 5px 0; font-size: 11px; font-weight: 600; color: var(--accent); text-align: center; }
.hd-empty { margin: 0; padding: 22px; font-size: 11.5px; line-height: 1.7; color: var(--fg-3); text-align: center; }

/* 视频提示词区的头：示例按钮 + 模式开关同排 */
.pvhead { display: flex; align-items: center; gap: 8px; }
.pmode { font-size: 11px; padding: 4px 9px; border-radius: var(--r-xs); }
.pmode.manual { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); font-weight: 600; }
/* 「让 AI 帮写」是这一块的主按钮（2026-09-19）：它在做实事，所以给它 accent 描边；
   右边那颗「我自己写英文」是模式切换，保持安静。 */
.poptim { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; padding: 4px 9px; border-radius: var(--r-xs); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.poptim:hover:not(:disabled) { background: var(--accent-dim); }
.poptim:disabled { opacity: .5; cursor: not-allowed; }
.poptim svg { width: 12px; height: 12px; flex: none; }
/* 首尾帧按钮（2026-09-19 换掉原来的复选框）。挑了哪一头要一眼看得见，所以展开态给强调色 */
.flfbtn { font-size: 11.5px; padding: 5px 11px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.flfbtn.on, .flfbtn.on:hover:not(:disabled) { background: var(--accent-dim); border-color: #efd4c6; color: var(--accent); font-weight: 600; }

/* 首尾帧选择器弹窗 */
.fdialog { width: 100%; max-width: 620px; max-height: 86vh; overflow: auto; padding: 22px; background: var(--bg); border: 1px solid var(--line); border-radius: 18px; box-shadow: 0 30px 100px #0c190d30; }
.fslots { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 14px 0 16px; }
.fslot { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; min-height: 112px; padding: 14px 12px 12px; border: 1px dashed var(--line); border-radius: 12px; background: var(--surface); cursor: pointer; }
.fslot:hover { border-color: var(--fg-3); }
.fslot.active { border-style: solid; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
.fslot img { width: 100%; max-height: 62px; object-fit: cover; border-radius: 8px; }
.fslot em { font-style: normal; font-size: 11.5px; color: var(--fg); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fsempty { font-size: 11px; color: var(--fg-3); text-align: center; line-height: 1.7; }
.fslabel { position: absolute; top: 7px; left: 9px; font-size: 10px; font-weight: 600; color: var(--fg-3); }
.fslot.active .fslabel { color: var(--accent); }
.fx { position: absolute; top: 5px; right: 7px; width: 20px; height: 20px; padding: 0; line-height: 1; border-radius: 50%; font-size: 13px; color: var(--fg-2); background: var(--surface-2); border-color: transparent; }
.fx:hover { background: var(--danger-dim); color: var(--danger); }
.fgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 9px; }
.fcard { display: flex; flex-direction: column; gap: 5px; padding: 7px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); cursor: pointer; text-align: left; }
.fcard.on { border-color: var(--accent); background: var(--accent-dim); }
.fcard img { width: 100%; aspect-ratio: 16 / 10; object-fit: cover; border-radius: 6px; }
.fph { display: grid; place-items: center; aspect-ratio: 16 / 10; border-radius: 6px; background: var(--surface-2); color: var(--fg-3); font-size: 12px; }
.fcard em { font-style: normal; font-size: 11px; color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ftag { font-size: 10px; color: var(--fg-3); }
.fempty { grid-column: 1 / -1; margin: 0; padding: 20px; text-align: center; font-size: 11.5px; color: var(--fg-3); }
.ffoot { display: flex; align-items: center; gap: 10px; margin-top: 16px; }
.fhint { flex: 1; font-size: 11px; color: var(--fg-3); }
/* 编排结果预览：出片前先核对正文与素材对应关系，省得等视频跑完才发现不对。
   ⚠️ 2026-09-18 起界面上**只有人话** —— 六段式全文与 ComfyUI 连线单彻底不进界面
   （原来收在 .po-full 折叠里，斌哥说连点开能看都不要）。要查原样去「生成记录」。 */
.promptout { margin-top: 14px; border-top: 1px solid var(--line-soft); padding-top: 12px; }
.po-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-bottom: 10px; }
.po-hint { flex: 1; font-size: 11px; color: var(--fg-3); }
.po-prompt { margin: 0 0 10px; padding: 12px 14px; font-family: var(--font-mono); font-size: 11.5px; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--fg); max-height: 340px; overflow: auto; }
/* 素材对应关系：一行一条，来自后端 slots[].human（「图片 1 作为「林晚」的外貌锁定」） */
.po-slots { list-style: none; margin: 0 0 10px; padding: 0; display: grid; gap: 4px; }
.po-slots li { display: flex; align-items: baseline; gap: 7px; font-size: 11.5px; color: var(--fg-2); }
.po-slots li::before { content: ""; flex: none; width: 4px; height: 4px; border-radius: 50%; background: var(--accent); }
.po-warn { list-style: none; margin: 0 0 10px; padding: 9px 12px; display: grid; gap: 4px; font-size: 11.5px; color: var(--warn); background: var(--warn-dim); border: 1px solid #ecd9ae; border-radius: var(--r-sm); }
</style>

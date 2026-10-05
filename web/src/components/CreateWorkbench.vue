<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { api } from '../api';
import CreateStart from './CreateStart.vue';

// 素材工作台：新建作品与作品素材页共用，视频创作由 WorkflowCanvas 承担。
const props = defineProps({
  project: { type: Object, default: null },
  taskId: { type: String, default: '' },
  cfg: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
  ensureTask: { type: Function, required: true },
  refresh: { type: Function, default: null },
  notify: { type: Function, default: null },
  workspace: Boolean,
  start: Boolean,
  recent: { type: Array, default: () => [] },
});

const emit = defineEmits(['canvas', 'open', 'configure']);
const starterTitle = ref('');

function say(text, kind = 'info') {
  if (props.notify) props.notify(text, kind);
}

// 素材预览说明当前工作流会使用定妆照还是四视图。
const isRef2va = computed(() => String(props.cfg?.video_workflow || '') === 'ref2va');

// 所有落库操作都套这一层：先确保有作品，再把错误统一转成提示
async function withTask(fn) {
  try {
    const id = await props.ensureTask(props.start ? starterTitle.value : undefined);
    if (!id) return null;
    return await fn(id);
  } catch (err) {
    say(err.message, 'error');
    return null;
  }
}

async function refreshTask() {
  if (props.refresh) await props.refresh();
}

// ---------------------------------------------------------------- 六组素材
// label / empty 的写法对齐作品工作台的「素材工坊」（同一组件的工作台视图），
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
];
const IMAGE_KINDS = ['character', 'scene', 'prop', 'image'];

const chars = computed(() => props.project?.characters || []);
const assets = computed(() => props.project?.assets || []);

const items = computed(() => {
  const out = { character: [], scene: [], prop: [], otherImage: [], voice: [], otherAudio: [] };

  chars.value.forEach((c, i) => {
    // 有「四视图设定图」就优先显示它 —— 一眼能看全四个视角，比单张正面信息量大。
    // 它不进 images，只作总览与留档；下游出片用的仍是 image_path（裁出来的单人正面图）。
    // ⚠️ 两处都带 `c.version` 做 cache-busting：重新生成是**就地覆盖同名文件**，
    //    不带版本号的话 URL 不变，浏览器 `<img>` 连请求都不发 → 用户必须手动刷新才看到新图
    //    （2026-09-19 斌哥报的）。后端每次生成/切版都会换一个新的 version。
    const sheetUrl = c.sheet ? api.characterImageUrl(props.taskId, c.sheet, c.version) : '';
    // 「出片时真正送进模型的那张」= 正面定妆照（单张）。卡片上显示的是四视图设定图
    // （信息量大、便于核对形象）—— 两张是**两个文件**，模型还可能把服装画得不一样。
    // 所以已选素材那一侧必须显示这张，否则用户会对着设定图问"进模型的怎么是另一张"
    // （2026-09-19 斌哥问过：他在 ComfyUI 里看到白上衣+棕裙，卡片上却是格纹裙）。
    const faceUrl = c.image_path ? api.characterImageUrl(props.taskId, c.image_path, c.version) : '';
    out.character.push({
      key: `character:${i}`,
      kind: 'character',
      index: i,
      name: c.name,
      desc: c.images?.length ? '定妆照已就绪' : '定妆照待生成',
      // 提示词＝这条素材的锚点。以前卡片上**看不到锚点**（唯一能改它的入口 09-17 撤了），
      // 所以「AI 助手攒好素材、你核对完再生成」这条流程根本没法核对。
      // 这里只**显示**（两行截断 + hover 看全文），不新增编辑入口 —— 改提示词走 AI 助手对话。
      prompt: c.anchor || '',
      // 出图比例：文档里写了就照抄，没写就是 16:9（后端 plan 落的 ratio）。
      // 老素材 / 手动添加的条目上没有这个键 → 空串，弹窗里按默认 16:9 显示。
      ratio: c.ratio || '',
      url: sheetUrl || faceUrl,
      // 出片时会送进模型的**那一张**（后端 _resolve_material_ref 是同一套规则，两边必须一致）：
      //   Ref2VA → 四视图设定图当参考图（信息最全，斌哥要的就是这个）
      //   I2V    → 正面定妆照（那张要当首帧；拼图当首帧＝第一秒四个人并排）
      sendUrl: isRef2va.value ? sheetUrl || faceUrl : faceUrl || sheetUrl,
      ready: !!(c.image_path || c.images?.length),
      ref: c,
    });
    // 角色音频组按「角色」列行而不是按「样本」—— 没样本的角色也要出现在这里，
    // 否则用户没有入口给它上传/录制音色
    out.voice.push({
      key: `voice:${i}`,
      kind: 'voice',
      index: i,
      name: c.name,
      desc: c.voice_sample ? `音色样本 · ${c.tts_voice || '未设定'}` : '音色待生成',
      prompt: c.voice || '',
      ratio: '',
      url: c.voice_sample ? api.characterVoiceUrl(props.taskId, c.voice_sample) : '',
      ready: !!c.voice_sample,
      ref: c,
    });
  });

  assets.value.forEach((a, i) => {
    const bucket = ['prop', 'scene', 'image', 'audio'].includes(a.kind) ? a.kind : 'prop';
    const target = bucket === 'image' ? 'otherImage' : bucket === 'audio' ? 'otherAudio' : bucket;
    // ⚠️ 道具的 ready **不能只看 images**（2026-09-29 修）：道具走「AI 生成」产出的
    // 是**三视图设定图**，只落 `sheet`、`images` 恒为空 —— 从前这里写 `!!a.images?.length`，
    // 于是已生成好的道具一直显示「还没有素材图」，出片时还会被后端判成"没带上"。
    // 与后端 `_resolve_material_ref` 的口径保持一致：道具看 sheet 或 images，其余看 images。
    const ready = bucket === 'prop' ? !!(a.sheet || a.images?.length) : !!a.images?.length;
    // 道具的「三视图设定图」（sheet）优先显示 —— 它信息量最大；
    // 还没出三视图但有单件图时退回单件图。它不进 images，只作总览。
    // version 同角色：就地覆盖 + URL 不变 = 浏览器不重新请求（见上面 character 那段注释）。
    const sheetUrl = a.sheet ? api.assetImageUrl(props.taskId, a.sheet, a.version) : '';
    out[target].push({
      key: `${bucket}:${i}`,
      kind: bucket,
      index: i,
      name: a.name,
      desc:
        bucket === 'audio'
          ? ready
            ? '音频已就绪'
            : '音频待上传'
          : ready
            ? '素材图已就绪'
            : bucket === 'image'
              ? '图片待上传或生成'
              : '素材图待上传',
      prompt: a.anchor || '',
      ratio: a.ratio || '',
      url:
        bucket === 'audio'
          ? ''
          : sheetUrl || (ready ? api.assetImageUrl(props.taskId, a.images[0], a.version) : ''),
      audioUrl:
        ready && bucket === 'audio'
          ? `/files/${props.taskId}/assets/${String(a.images[0]).split(/[\\/]/).pop()}`
          : '',
      ready,
      ref: a,
    });
  });
  return out;
});

const groups = computed(() => GROUPS.map((g) => ({ ...g, items: items.value[g.key] || [] })));
const emptyStart = computed(() => props.start && !chars.value.length && !assets.value.length);
const visibleGroups = computed(() =>
  props.start ? groups.value.filter((g) => g.items.length) : groups.value
);

// 素材编辑条目；视频选择器另行筛选图片，不将音频送入当前视频模型。
const allItems = computed(() => groups.value.flatMap((group) => group.items));

// 音色 / 其他音频传的是音频文件，其余传图片。accept 与按钮文案都跟着这个走。
function isAudioKind(kind) {
  return kind === 'voice' || kind === 'audio';
}

// 图片 / 音频类是用户自己传的原始素材，默认走「待上传」而不是「待生成」。
// ⚠️ image 现在也有 AI 生成入口了（2026-09-17），所以实际文案由 emptyText 决定 ——
// 这里保留 image 是为了别的地方按"上传型"判断时语义不反转。
function needsUpload(kind) {
  return kind === 'image' || kind === 'audio';
}

// 卡片还没图时的空态文案。
// 「其他图片」是个例外：它既能上传、也能 AI 生成（2026-09-17 起），
// 只写「待上传」会让用户以为它没法生成，所以两样都写出来。
function emptyText(it) {
  if (it.ready) return '已就绪';
  if (it.kind === 'image') return '待上传 / 待生成';
  return needsUpload(it.kind) ? '待上传' : '待生成';
}

// 批量生成使用素材提示词；与视频画布的参考图选择独立。
const GEN_KINDS = ['character', 'scene', 'prop', 'image', 'voice'];

// 图像和音色分别并发，音色请求至少间隔 3.2 秒，避免集中触发限流。
const IMG_POOL = 4;
const VOICE_POOL = 2;
const VOICE_GAP_MS = 3200;

const genPicked = ref(new Set());
const genRunning = ref(false);
const genStop = ref(false); // 点了「停止」→ 已发出去的这几项跑完就收工（HTTP 请求没法撤回）
const genNow = ref(new Set()); // 正在生成的那几张卡的 key（并发下是多个；卡片盖"生成中"层）
const genNote = ref(''); // 进度 / 结果文字

// 音色请求的"发车时刻表"：先**同步占位**再等待 —— 占位发生在 await 之前，
// 两个 worker 不会因竞态挤在同一毫秒发车。
let voiceNextAt = 0;
async function voiceSlot() {
  const at = Math.max(Date.now(), voiceNextAt);
  voiceNextAt = at + VOICE_GAP_MS;
  const wait = at - Date.now();
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
}

// 并发池：limit 个 worker 从同一个队列取活。停止 = 不再取新活，
// 已经在跑的那几项照常跑完（结果照样统计、照样刷新）。
async function runPool(items, limit, run) {
  const queue = [...items];
  const workers = Array.from({ length: Math.max(1, Math.min(limit, queue.length)) }, async () => {
    for (;;) {
      if (genStop.value) return;
      const it = queue.shift();
      if (!it) return;
      await run(it);
    }
  });
  await Promise.all(workers);
}

// 能被自动生成的两个条件：① 这一类有 AI 生成入口 ② **卡片上有提示词**
// （没提示词就没东西可出 —— 卡片上也就没有勾选框，一眼能看出"这条还没攒提示词"）
function canAutoGen(it) {
  return GEN_KINDS.includes(it.kind) && !!String(it.prompt || '').trim();
}

const genSelectable = computed(() => allItems.value.filter(canAutoGen));
const genPickedItems = computed(() => genSelectable.value.filter((it) => genPicked.value.has(it.key)));

function isGenPicked(it) {
  return genPicked.value.has(it.key);
}

function toggleGenPick(it) {
  if (!canAutoGen(it) || genRunning.value) return;
  const next = new Set(genPicked.value);
  if (next.has(it.key)) next.delete(it.key);
  else next.add(it.key);
  genPicked.value = next;
}

function clearGenPick() {
  genPicked.value = new Set();
}

// 分组级全选：14 个场景一个个点太累（这是 01 里唯一的批量入口）
function groupSelectable(g) {
  return g.items.filter(canAutoGen);
}

function groupAllPicked(g) {
  const s = groupSelectable(g);
  return s.length > 0 && s.every((it) => genPicked.value.has(it.key));
}

function toggleGroupPick(g) {
  if (genRunning.value) return;
  const next = new Set(genPicked.value);
  const all = groupAllPicked(g);
  for (const it of groupSelectable(g)) {
    if (all) next.delete(it.key);
    else next.add(it.key);
  }
  genPicked.value = next;
}

// 换作品时清掉（勾选是"这一部作品这一批"的事，跟过去就错了）
watch(
  () => props.taskId,
  () => {
    genPicked.value = new Set();
    genNote.value = '';
  }
);

async function autoGenerate() {
  const items = genPickedItems.value;
  if (!items.length || genRunning.value) return;
  // 这是**真花钱**的动作（每一项都是一次出图，角色一项还出两张），
  // 所以按项目里既有的做法给一道确认闸 —— 别的删除/彻底删除也都是 window.confirm。
  if (
    !window.confirm(
      `自动生成这 ${items.length} 项？\n` +
        '会用每张卡片上的提示词出图（角色出定妆照 + 四视图，共 2 张）。\n' +
        '图像 4 路并发、音色 2 路并发，中途可以停。'
    )
  )
    return;

  genRunning.value = true;
  genStop.value = false;
  genNow.value = new Set();
  voiceNextAt = 0; // 这一批从"现在"开始排发车时刻
  const failed = [];
  const failedKeys = [];
  const doneKeys = [];
  let done = 0;

  const markNow = (key, on) => {
    const next = new Set(genNow.value);
    if (on) next.add(key);
    else next.delete(key);
    genNow.value = next;
  };

  try {
    const id = await props.ensureTask(props.start ? starterTitle.value : undefined);
    if (!id) return;

    // 一项一份活：成功记 done，失败留着（最后汇总），跑完刷一次任务快照。
    // 并发下 genNote 只报"已完成 X/Y"，不再指认"正在跑哪张" ——
    // 正在跑的那几张由卡片上的"生成中"层负责显示。
    const one = async (it) => {
      markNow(it.key, true);
      try {
        if (it.kind === 'voice') await voiceSlot(); // 音色先等发车时刻（RPM 节流）
        if (it.kind === 'character') {
          await api.generateCharacterPortrait(id, it.index, it.prompt, ratioOf(it));
        } else if (it.kind === 'voice') {
          // 音色没有画幅，也不给性别 —— 让后端按提示词里的线索挑（后端会用
          // tts.pick_voice 兜底，挑不出性别就留空，不会硬猜）
          await api.generateCharacterVoice(id, it.index, it.prompt, '');
        } else {
          await api.generateAssetImage(id, it.index, it.prompt, ratioOf(it));
        }
        done += 1;
        doneKeys.push(it.key);
      } catch (err) {
        // 单项失败不该拖垮整批 —— 记下来接着跑，最后一起说
        failed.push(`${it.name}（${err.message}）`);
        failedKeys.push(it.key);
      } finally {
        markNow(it.key, false);
        // "已处理"= 成功 + 失败（失败项也走完了，不该让进度条看着卡住）
        genNote.value = `已处理 ${done + failed.length}/${items.length}`;
      }
      await refreshTask(); // 出一张刷一张，别等全跑完才看见
    };

    // 图像与音色**各自排队**：两个通道的额度体系不同（魔搭按天 500 次/模型、
    // MiniMax 按分钟 20 RPM），混在一个池里会让慢的那类白占快的那类的并发位。
    await Promise.all([
      runPool(
        items.filter((it) => it.kind !== 'voice'),
        IMG_POOL,
        one
      ),
      runPool(
        items.filter((it) => it.kind === 'voice'),
        VOICE_POOL,
        one
      ),
    ]);
  } catch (err) {
    say(err.message, 'error');
  } finally {
    genRunning.value = false;
    genNow.value = new Set();
    const stopped = genStop.value ? '（已手动停止）' : '';
    // 跑完勾选留哪些，三条规矩：
    //   ① 成功的**取消** —— 免得手一抖又白出一遍
    //   ② 失败的**留着** —— 改完提示词直接再点一次就能重试
    //   ③ 点了「停止」时**还没跑的也留着** —— 停下来的意思就是"先不跑了"，不是"放弃剩下的"
    const keep = new Set(failedKeys);
    if (genStop.value) {
      for (const it of items) {
        if (!doneKeys.includes(it.key) && !failedKeys.includes(it.key)) keep.add(it.key);
      }
    }
    genPicked.value = keep;
    if (failed.length) {
      genNote.value = `完成 ${done} 项，失败 ${failed.length} 项${stopped}`;
      say(`自动生成：成功 ${done} 项，失败 ${failed.length} 项 —— ${failed.slice(0, 3).join('；')}`, 'error');
    } else if (done) {
      genNote.value = '';
      say(`自动生成完成：${done} 项${stopped}`, 'ok');
    } else {
      genNote.value = '';
    }
  }
}

// ---------------------------------------------------------------- 上传
const uploading = ref('');
const dropKey = ref('');

async function uploadTo(kind, file, target) {
  if (!file) return;
  uploading.value = target?.key || kind;
  try {
    await withTask(async (id) => {
      if (target && target.kind === 'voice') {
        await api.uploadCharacterVoice(id, target.index, file);
      } else if (target && target.kind === 'character') {
        await api.uploadCharacterImage(id, target.index, file);
      } else if (target) {
        await api.uploadAssetFile(id, target.index, file);
      } else {
        // 组级上传：后端按 kind 自动建条目，名字取文件名
        await api.uploadMaterial(id, kind, file);
      }
      await refreshTask();
      say(`「${file.name || '录音'}」已上传并就绪`, 'ok');
    });
  } finally {
    uploading.value = '';
    dropKey.value = '';
  }
}

function onPickFile(event, g, target) {
  const file = event.target.files && event.target.files[0];
  event.target.value = '';
  uploadTo(g.key, file, target);
}

function onDrop(event, g) {
  dropKey.value = '';
  const file = event.dataTransfer?.files?.[0];
  if (!file) return;
  // 音频组必须先有角色/条目才知道挂给谁，拖拽这里只支持图片类
  if (g.key === 'voice') {
    const first = chars.value.length === 1 ? items.value.voice[0] : null;
    if (!first) {
      say('先确定给哪个角色配：角色多于 1 个时请点角色行上的「上传」');
      return;
    }
    uploadTo('voice', file, first);
    return;
  }
  uploadTo(g.key, file, null);
}

// ---------------------------------------------------------------- 录音
// 浏览器 MediaRecorder → Blob → base64 走上传接口。全程不碰模型接口。
const recording = ref('');
let recorder = null;
let chunks = [];

async function startRecord(target) {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    say('当前浏览器不支持录音，请改用「上传音频」');
    return;
  }
  if (recording.value) {
    stopRecord();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size) chunks.push(e.data);
    };
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const type = (recorder && recorder.mimeType) || 'audio/webm';
      const blob = new Blob(chunks, { type });
      const ext = type.includes('ogg')
        ? '.ogg'
        : type.includes('mp4')
          ? '.m4a'
          : type.includes('wav')
            ? '.wav'
            : '.webm';
      recording.value = '';
      if (!blob.size) return;
      await withTask(async (id) => {
        if (target.kind === 'voice') {
          await api.uploadCharacterVoice(id, target.index, blob, `voice${ext}`);
        } else {
          await api.uploadAssetFile(id, target.index, blob, `rec${ext}`);
        }
        await refreshTask();
        say('录音已保存', 'ok');
      });
    };
    recorder.start();
    recording.value = target.key;
    say('正在录音…再点一次「停止录音」结束');
  } catch (err) {
    say(`拿不到麦克风权限：${err.message}`, 'error');
  }
}

function stopRecord() {
  if (recorder && recorder.state !== 'inactive') recorder.stop();
  recorder = null;
}

onBeforeUnmount(() => {
  stopRecord();
});

// 注：原来的 recordOtherAudio()（「其他音频」分组上的「录制音频」按钮，
// 没条目时先造一个空条目再录）2026-09-15 随那排按钮一起删了。
// 现在录其他音频：顶部「添加素材」选「其他音频」建条目 → 点卡片上的「录制」。

// ---------------------------------------------------------------- 添加 / 编辑 / 删除
// 添加流程（2026-09-15 斌哥定，见需求原话）：
//   点「添加素材」→ 只弹 5 个类型选项 → 选一个，**下面对应那一组里立刻多一张空卡片**
//   → 名字和内容（上传）都在那张卡片上填。
// 所以这里没有名字/描述输入框、也没有「添加」按钮 —— 表单只负责选类型。
const adding = ref(false);

const ADD_KINDS = [
  { key: 'character', label: '角色' },
  { key: 'scene', label: '场景' },
  { key: 'prop', label: '道具' },
  { key: 'image', label: '其他图片' },
  { key: 'audio', label: '其他音频' },
];

function openAdd() {
  adding.value = true;
}

function cancelAdd() {
  adding.value = false;
}

// 选中类型 → 落一张空条目 → 刷新 → 把那张新卡片直接推进编辑态
// （编辑态里有名字输入框 + 上传按钮，正是"在那里填名字、传内容"）
async function createEntry(kind) {
  adding.value = false;
  const label = ADD_KINDS.find((k) => k.key === kind)?.label || '素材';
  await withTask(async (id) => {
    // design: false —— 只建条目。不能让后端顺手跑一次 AI 设计锚点：
    // 这次点击是"占个位"，锚点/名字由用户自己在卡片上填，不该花模型额度。
    const created =
      kind === 'character'
        ? await api.addCharacter(id, { name: '', anchor: '', design: false })
        : await api.addAsset(id, { kind, name: '', anchor: '', design: false });
    await refreshTask();
    // 刷新后按占位名反查刚落的那张卡片（后端保证占位名唯一，所以这一步是准的）
    const it = allItems.value.find((x) => x.name === created?.name);
    if (it) {
      freshKey.value = it.key;
      startEdit(it, { blank: true });
    }
    say(`已新增一个${label}，填个名字再上传内容`);
    focusEditName();
  });
}

const editing = ref('');
const editName = ref('');
// 刚建出来、还没保存过的那张空卡片的 key。
// 它决定编辑态里那颗按钮是「取消」还是「删除」—— 见 cancelEdit()。
const freshKey = ref('');

// 换作品时别把上一部作品的编辑态带过去
watch(
  () => props.taskId,
  () => {
    editing.value = '';
    freshKey.value = '';
  }
);

// blank=true 用于"刚新建的空卡片"：名字框留空让用户直接打，
// 而不是把后端补的占位名（未命名角色）塞进去让人先删一遍。
// 没传 blank 但确实是刚建的那张（比如取消后又点了一次「编辑」），也一样留空。
function startEdit(it, opts = {}) {
  editing.value = it.key;
  editName.value = opts.blank || isFresh(it) ? '' : it.name;
}

function isFresh(it) {
  return freshKey.value === it.key;
}

// 编辑态的退出按钮：
//   老卡片 → 只是关掉表单（取消编辑）
//   刚建的空卡片 → 取消就等于放弃这张卡片，直接删掉。
//     否则每次「添加素材」手滑点一下都会在库里留一张「未命名角色」，
//     白占素材区，还会被当成真素材勾进视频。
function cancelEdit(it) {
  editing.value = '';
  if (!isFresh(it)) return;
  freshKey.value = '';
  return removeItem(it, { silent: true });
}

// 新建后光标直接落到名字框 —— 少一次点击。
// 用 querySelector 而不是模板 ref：同时只会有一张卡片处于编辑态
// （editing 是单值），而 .edit-form 是 v-for 里 v-if 出来的，模板 ref 会变成数组。
async function focusEditName() {
  await nextTick();
  const el = document.querySelector('.workbench .edit-form input');
  if (el) el.focus();
}

async function commitEdit(it) {
  const name = editName.value.trim();
  // 描述/锚点已不在编辑表单里（2026-09-15），原样带回 —— 刚建的空卡片本来就是空串，
  // 老卡片则保持它已有的锚点，改名字不会把描述弄丢。
  const anchor = it.ref?.anchor || '';
  if (!name) return;
  await withTask(async (id) => {
    if (it.kind === 'character') await api.patchCharacter(id, it.index, { name, anchor });
    else if (it.kind === 'voice') await api.patchCharacter(id, it.index, { name });
    else await api.patchAsset(id, it.index, { name, anchor });
    editing.value = '';
    freshKey.value = '';
    await refreshTask();
    say('已保存', 'ok');
  });
}

// silent=true 用于"刚建的空卡片被取消"：那张卡片是这一轮才建的，
// 没有引用关系要核对，再弹一次确认框纯属多余。
async function removeItem(it, opts = {}) {
  const label = it.kind === 'character' || it.kind === 'voice' ? '角色' : '素材';
  if (!opts.silent && !window.confirm(`删除${label}「${it.name}」？引用它的地方需要你自己核对。`)) return;
  await withTask(async (id) => {
    if (it.kind === 'character' || it.kind === 'voice') await api.deleteCharacter(id, it.index);
    else await api.deleteAsset(id, it.index);
    if (editing.value === it.key) editing.value = '';
    if (freshKey.value === it.key) freshKey.value = '';
    await refreshTask();
    say(opts.silent ? '已放弃这张新卡片' : `已删除「${it.name}」`);
  });
}

// ---------------------------------------------------------------- 素材图预览
// 点卡片上的图 → 看大图 + 下载。素材图原先没有预览（灯箱只服务分镜/块那条链），
// 而"把生成的定妆照/设定图存下来，之后当素材复用"是刚需。
// ⚠️ 01 的卡片是**只读展示 + 操作**：点图片=看大图，点按钮=上传/编辑/删除/生成历史/AI 生成，
//    其它区域不改变素材状态。
const previewItem = ref(null);

function onThumbClick(event, it) {
  // 没有图、或音频类（音频卡片自带播放器）→ 不接管，点了不做任何事
  if (!it?.url || isAudioKind(it.kind)) return;
  event.stopPropagation();
  previewItem.value = it;
}

// 下载文件名取 URL 最后一段（先截 query，再解码中文名）
function downloadName(it) {
  const base = String(it?.url || '')
    .split('?')[0]
    .split('/')
    .pop();
  return base ? decodeURIComponent(base) : 'material.png';
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
const aiFor = ref(null); // 正在弹窗的那个 item（null = 不显示）
const aiPrompt = ref('');
const aiBusy = ref(false);
const aiRatio = ref('1:1'); // 角色默认 1:1；场景/其他图片默认跟作品画幅
const aiGender = ref('female'); // 只对音色弹窗有意义（性别是硬约束，必须显式选）
// 键必须与 image_provider.SIZE_TABLE 一致，别在这里自己造比例
const AI_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4'];
// 音色描述比画面描述短得多。500 与后端 VoiceGenerateBody.prompt 的 max_length 对齐 ——
// 前端放 1000 而后端收 500 的话，用户写到 600 字就会吃一个莫名其妙的 422。
const aiMaxLen = computed(() => (aiFor.value?.kind === 'voice' ? 500 : 1000));
// 弹窗文案里对五类素材的称呼与示例
const AI_LABEL = { character: '形象图', scene: '场景图', prop: '三视图', image: '图片', voice: '音色' };
const AI_PLACEHOLDER = {
  character:
    '例如：赛博朋克风格的女战士，银灰色短发，左眼角有青色发光的植入体，黑色战术夹克，冷峻干练，写实电影质感',
  scene: '例如：雨夜的老旧书店，木质书架顶到天花板，暖黄吊灯，地面积水反光，安静略带灰尘的空气感',
  prop: '例如：掌心大小的黄铜罗盘，盘面刻星宿纹，指针氧化发黑，边缘有磕碰缺口，配一根深棕色皮绳',
  image: '例如：黄昏的天台，主角背对镜头站在栏杆边，风吹起风衣下摆，远处城市灯火初上，逆光剪影，中景低机位',
  voice: '例如：低沉沙哑的中年男声，语速偏慢，带一点疲惫和烟嗓，说话时尾音往下沉',
};
// 音色弹窗的性别选择。**性别是硬约束**（女主配男声是硬错），所以让用户显式选，
// 不交给模型猜 —— 后端也会拿这个值再校验一遍 Agent 挑回来的音色。
const GENDERS = [
  { key: 'female', label: '女声' },
  { key: 'male', label: '男声' },
];

function openAi(it) {
  aiFor.value = it;
  // 上次对这个素材生成时填的描述 / 比例 / 性别 → **填回弹窗**（2026-09-27 斌哥提的：
  // 出一版不满意、想改两句重出，弹窗却是空的，只能从头重打一遍）。
  // 后端每次「AI 生成」成功都会在素材条目上留一份 `ai_last`（见 app._remember_ai_input），
  // 与 `version` / `stale` 同一条路传到页面；上传型素材没有这个字段，走下面的默认值。
  // ⚠️ 按**弹窗种类**取槽：角色条目上挂着形象图与音色两个弹窗，各存各的，
  //    不分开的话生成完音色会把形象图那次的描述顶掉。
  const last = (it?.ref?.ai_last || {})[it?.kind] || {};
  aiPrompt.value = typeof last.prompt === 'string' ? last.prompt : '';
  aiGender.value = last.gender === 'male' ? 'male' : 'female';
  // 存过的比例要**过一遍白名单**再进状态：脏值（手改过 project.json / 换过图片模型）
  // 会让所有比例 chip 都不高亮，用户看到的是"一个都没选中"，比不给默认值更费解
  // 优先级：① 上次在这个弹窗里实际选的（`ai_last`，用户最近的意图）
  //         ② AI 助手读文档时落下的（`entry.ratio` —— 文档说了 9:16 就该默认 9:16）
  //         ③ 16:9（2026-09-27 斌哥定的统一默认，不再按类型分叉）
  // ③ 与 `ratioOf()` 共用同一个函数，保证"提示词弹窗里显示的比例"和这里选中的一模一样。
  aiRatio.value = AI_RATIOS.includes(last.ratio) ? last.ratio : ratioOf(it);
}

function closeAi() {
  if (aiBusy.value) return;
  aiFor.value = null;
  aiPrompt.value = '';
}

async function submitAi() {
  const it = aiFor.value;
  const text = aiPrompt.value.trim();
  if (!it || !text || aiBusy.value) return;
  aiBusy.value = true;
  try {
    // withTask 已把错误转成提示；失败时**不关弹窗**，用户改两句就能重试
    const res = await withTask((id) => {
      if (it.kind === 'character') return api.generateCharacterPortrait(id, it.index, text, aiRatio.value);
      if (it.kind === 'voice' || it.kind === 'audio')
        return api.generateCharacterVoice(id, it.index, text, aiGender.value);
      return api.generateAssetImage(id, it.index, text, aiRatio.value);
    });
    if (res) {
      await refreshTask();
      if (it.kind === 'voice') {
        // 音色池是**预置的**，只能"挑"不能"造" —— 把挑中的 id 明说出来，
        // 用户才好核对，不满意就换个性别重生成
        say(`「${it.name}」的音色已生成：${res.tts_voice || '已就绪'}`, 'ok');
      } else {
        // 按 kind 说清生成的是什么：道具出的是三视图、其他图片出的是一张完整画面，
        // 一律说"场景图已生成"会让人以为生成错了东西
        const what =
          { character: '形象', scene: '场景图', prop: '三视图', image: '图片' }[it.kind] || '素材图';
        say(`「${it.name}」的${what}已生成`, 'ok');
      }
      aiFor.value = null;
      aiPrompt.value = '';
    }
  } finally {
    aiBusy.value = false;
  }
}

// ---------------------------------------------------------------- 提示词 / 出图比例（2026-09-27）
// 卡片上那两行提示词是**截断**的，长提示词看不全、核对不了；斌哥要的是点一下看全文。
// 出图的四类（角色 / 场景 / 道具 / 其他图片）在同一个弹窗里顺带把**出图比例**显示出来 ——
// 比例是 AI 助手读文档时落的（`entry.ratio`，文档没写就是 16:9），点「AI 生成」时会默认选它。
//
// ⚠️ **这个弹窗是可编辑的**（2026-09-27 斌哥第二句话："为啥点进去提示词和比例不能修改，
//    应该可以修改才对啊"）。我第一版做成了只读，理由是"锚点的编辑入口 09-17 / 09-19 各撤过一次，
//    别自作主张补" —— 但那两次撤的是**卡片上**的编辑入口，而这次是斌哥**明确要**的。
//    教训：拿"以前撤过"当理由拒绝新需求之前，先分清"他当时不想要"和"我猜他不想要"。
// ⚠️ `IMAGE_KINDS` 上面（"选中的素材"那一节）早就有了 —— 别在这里再声明一遍，会编译不过。
const promptFor = ref(null); // 正在看提示词的那个 item（null = 弹窗不显示）
const pdText = ref(''); // 弹窗里那份**可编辑**的提示词
const pdRatio = ref(''); // 弹窗里选中的比例
const pdBusy = ref(false);

// 提示词存在哪个字段：出图类在 `anchor`（外貌 / 画面锚点），音色类在 `voice`（声音设计）。
// 两者都是后端早就支持的 PATCH 字段，所以这里不用改接口。
function promptFieldOf(it) {
  return it?.kind === 'voice' ? 'voice' : 'anchor';
}

function promptValueOf(it) {
  const ref_ = it?.ref || {};
  return String((promptFieldOf(it) === 'voice' ? ref_.voice : ref_.anchor) || '');
}

// 条目上**真的**存了比例吗（＝AI 助手从文档里读到的）。手动添加 / 老素材没有这个键。
function ratioFromDoc(it) {
  return AI_RATIOS.includes(String(it?.ratio || '').trim());
}

// 这条素材出图时会用的比例。必须与 openAi() 里那条优先级 **同一套规则** ——
// 弹窗显示的要是和点「AI 生成」时选中的不一样，用户会以为显示错了。
// ⚠️ 默认值统一是 **16:9**（2026-09-27 斌哥定："文档里没给明图片比例，就默认 16:9"）。
//    以前这里按类型分叉（场景/图片跟作品画幅、角色 1:1），现在只有一条规则，
//    因为 AI 助手落下来的条目**一定带 ratio**，走到这个默认值的只剩手动添加 / 老素材。
function ratioOf(it) {
  if (ratioFromDoc(it)) return String(it.ratio).trim();
  return '16:9';
}

function openPrompt(it) {
  promptFor.value = it;
  pdText.value = promptValueOf(it);
  pdRatio.value = ratioOf(it);
}

function closePrompt() {
  if (pdBusy.value) return;
  promptFor.value = null;
}

// 有没有真的改动过 —— 没改就不给点「保存」，省得白跑一次 PATCH + 刷新
const pdDirty = computed(() => {
  const it = promptFor.value;
  if (!it) return false;
  return (
    pdText.value.trim() !== promptValueOf(it).trim() ||
    (IMAGE_KINDS.includes(it.kind) && pdRatio.value !== ratioOf(it))
  );
});

async function savePrompt() {
  const it = promptFor.value;
  if (!it || pdBusy.value) return;
  const text = pdText.value.trim();
  if (promptFieldOf(it) === 'anchor' && !text) {
    say('提示词不能是空的', 'error');
    return;
  }
  const patch = {};
  const field = promptFieldOf(it);
  if (text !== promptValueOf(it).trim()) patch[field] = text;
  if (IMAGE_KINDS.includes(it.kind) && pdRatio.value !== ratioOf(it)) patch.ratio = pdRatio.value;
  if (!Object.keys(patch).length) {
    promptFor.value = null;
    return;
  }
  pdBusy.value = true;
  try {
    // 角色与音色是同一个条目上的两个弹窗，都走 PATCH characters/{index}；
    // 场景 / 道具 / 其他图片走 PATCH assets/{index}
    const res = await withTask((id) =>
      it.kind === 'character' || it.kind === 'voice'
        ? api.patchCharacter(id, it.index, patch)
        : api.patchAsset(id, it.index, patch)
    );
    // withTask 已经把错误弹出来了；失败时**不关弹窗**，用户改两句就能重试
    if (!res) return;
    await refreshTask();
    // 改了锚点 → 后端会标 stale；改了音色描述 → 后端会重挑 TTS 音色并作废旧样本。
    // 两句都说出来，免得用户以为"改个描述怎么样本没了"。
    say(field === 'voice' ? '音色描述已保存（旧样本已作废，重新点「AI 生成」合成）' : '提示词已保存', 'ok');
    promptFor.value = null;
  } finally {
    pdBusy.value = false;
  }
}

// ---------------------------------------------------------------- 素材规划助手（2026-09-27）
// 斌哥的原话：一个一个手填素材太慢 —— 想丢一份文档进去，让 AI 把角色/场景/道具/音色
// **连同各自的提示词**先攒好，但**先别生成**，他核对完再自己点生成。
//
// 一轮 = 一次 POST /api/tasks/{id}/assistant/plan。与右侧那个「AI 创作助手」的分工：
//   那边是闲聊式、**凭空**帮你想素材；这边读你**上传的文档**，而且同名条目会被**改写**
//   （所以"把深山古道改成黄昏"能落下去，不是再建一条）。
// 两条路都只写条目 + 锚点，**不出图、不出音、不占出图/出片额度** —— 生成永远由用户点。
//
// ⚠️ 弹窗同样必须 Teleport 到 body：外层 .create-shell 的 fade-in 是 transform，
//    会给 position: fixed 造一个新的包含块，弹窗会被钉在长文档底部（踩过）。
const AS_DOC_ACCEPT = '.md,.markdown,.txt,.text,.json,.csv,.tsv,.yaml,.yml,.srt,.log,.docx,.pdf';
const AS_GREETING =
  '把剧本 / 设定文档拖进这个窗口（md、txt、docx、pdf 都行），我就按它把角色、场景、道具和各自的提示词先攒好，直接落进下面的素材区。这一步只写名字和提示词，不出图也不出音 —— 你逐条核对，确认了再自己点生成。';
const AS_QUICK = [
  { label: '按文档把素材攒齐', prompt: '按这份文档把素材都攒齐' },
  { label: '只抽角色 + 音色', prompt: '只抽角色和他们的音色，场景道具先不管' },
  { label: '场景再补两个', prompt: '再补两个文档里出现过、但我还没建的场景' },
];

const asOpen = ref(false);
const asDocs = ref([]); // [{name, ext, bytes, mtime}] —— 这个作品下已上传的文档
const asMsgs = ref([]); // 气泡。第一条是问候语，回传历史时要把它滤掉
const asInput = ref('');
const asBusy = ref(false); // 正在等模型
const asUploading = ref(false);
const asDrag = ref(false);
const asSuggest = ref([]); // 上一轮模型给的下一步建议（点一下就发出去）
const asScroller = ref(null);
const asFileInput = ref(null);

// 没有文本模型时助手干不了活 —— 提前在头部标出来，别让用户传完文档才发现
const asOnline = computed(() => !!String(props.cfg?.text_api_key || '').trim());
const asChips = computed(() => (asSuggest.value.length ? asSuggest.value : []));
const asShowQuick = computed(() => !asSuggest.value.length && asMsgs.value.length <= 1);

function asStamp() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

async function asScroll() {
  await nextTick();
  const el = asScroller.value;
  if (el) el.scrollTop = el.scrollHeight;
}

function asPush(role, text, kind = '') {
  asMsgs.value.push({ role, text, kind, time: asStamp() });
  asScroll();
}

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(1)} MB`;
}

async function loadDocs() {
  if (!props.taskId) {
    asDocs.value = [];
    return;
  }
  try {
    const res = await api.listDocs(props.taskId);
    asDocs.value = res.docs || [];
  } catch {
    // 作品还没建出来时后端会 404 —— 这里静默，用户传第一个文件时 withTask 会把作品建好
    asDocs.value = [];
  }
}

function openAssistant() {
  asOpen.value = true;
  if (!asMsgs.value.length) {
    asMsgs.value = [{ role: 'assistant', text: AS_GREETING, kind: 'greet', time: asStamp() }];
  }
  asScroll();
  loadDocs();
}

function closeAssistant() {
  // 传文件 / 等模型的过程中不让关：关了也拦不住请求，只会让用户以为没生效
  if (asBusy.value || asUploading.value) return;
  asOpen.value = false;
}

function asPickFiles(e) {
  const files = Array.from(e.target.files || []);
  e.target.value = ''; // 清空才能让「同一个文件再选一次」也触发 change
  asUpload(files);
}

function asDrop(e) {
  asDrag.value = false;
  const files = Array.from(e.dataTransfer?.files || []);
  if (files.length) asUpload(files);
}

async function asUpload(files) {
  if (!files.length || asUploading.value || asBusy.value) return;
  asUploading.value = true;
  try {
    const id = await props.ensureTask(props.start ? starterTitle.value : undefined);
    if (!id) return;
    for (const f of files) {
      try {
        const res = await api.uploadDoc(id, f);
        asPush(
          'assistant',
          `已读入《${res.name}》：${res.chars} 字${res.replaced ? '（覆盖了同名的那一份）' : ''}。`
        );
      } catch (err) {
        // 读不出来的原因（扫描件 / 格式不支持）后端说得很具体，原样转给用户
        asPush('assistant', `《${f.name}》没读进来：${err.message}`, 'bad');
      }
    }
    await loadDocs();
  } catch (err) {
    say(err.message, 'error');
  } finally {
    asUploading.value = false;
    asScroll();
  }
}

async function asRemoveDoc(name) {
  if (!props.taskId || asBusy.value || asUploading.value) return;
  try {
    const res = await api.deleteDoc(props.taskId, name);
    asDocs.value = res.docs || [];
  } catch (err) {
    say(err.message, 'error');
  }
}

async function asSend(text) {
  if (asBusy.value || asUploading.value) return;
  const msg = String(text ?? asInput.value).trim();
  if (!msg && !asDocs.value.length) {
    say('先传一份文档，或者写一句你要什么', 'error');
    return;
  }
  if (!asOnline.value) {
    say('还没配文本模型：去「服务设置」填 DeepSeek Key，助手才能干活', 'error');
    return;
  }
  // 历史要在**推入这一句之前**取，否则同一句话会既在历史里又在 message 里
  const hist = asMsgs.value
    .filter((m) => m.kind !== 'greet')
    .slice(-8)
    .map((m) => ({ role: m.role, text: m.text }));

  asInput.value = '';
  asSuggest.value = [];
  asPush('user', msg || '（按这些文档把素材攒齐）');
  asBusy.value = true;
  try {
    const id = await props.ensureTask(props.start ? starterTitle.value : undefined);
    if (!id) return;
    // ⚠️ 显式带上当前附件清单：用户把 chip 全删了就是空数组 ——
    //    不传这个字段后端才会"读全部"，那正是我们不想在"已清空附件"时发生的
    const res = await api.assistantPlan(id, {
      message: msg,
      history: hist,
      docs: asDocs.value.map((d) => d.name),
    });
    asPush('assistant', res.reply || '（没有回复）');
    asSuggest.value = res.suggestions || [];
    const added = (res.created?.characters?.length || 0) + (res.created?.assets?.length || 0);
    const changed = (res.updated?.characters?.length || 0) + (res.updated?.assets?.length || 0);
    if (added || changed) {
      await refreshTask();
      say(`已落进素材区：新增 ${added} 条、改写 ${changed} 条 —— 都还没生成`, 'ok');
    }
  } catch (err) {
    asPush('assistant', `这一轮没成功：${err.message}`, 'bad');
  } finally {
    asBusy.value = false;
    asScroll();
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
const HISTORY_KINDS = ['character', 'scene', 'prop', 'image'];
const historyFor = ref(null); // 正在看历史的那个 item（null = 弹窗不显示）
const historyList = ref([]); // 后端给的 versions，**第一条是当前这版**（current: true）
const historyKeep = ref(0); // 最多保留几版（后端给，别在前端写死）
const historyBusy = ref(false); // 正在拉列表
const historyUsing = ref(''); // 正在切的那一版的 name（空串 = 没有在切）

// 版本目录名 20260919_051122_ab12cd 里的时间戳是 UTC，后端转成带时区的 ISO 给过来，
// 交给 Date 换成本地时间再显示 —— 直接截字符串会差 8 小时。
function historyStamp(v) {
  if (!v?.created_at) return '当前';
  const d = new Date(v.created_at);
  if (Number.isNaN(d.getTime())) return v.name || '';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function humanSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function openHistory(it) {
  if (!HISTORY_KINDS.includes(it?.kind)) return;
  historyFor.value = it;
  historyList.value = [];
  historyKeep.value = 0;
  historyBusy.value = true;
  try {
    // withTask 已把错误转成提示；失败时列表留空，弹窗自己会显示空态
    const res = await withTask((id) => api.materialHistory(id, it.kind, it.index));
    if (res) {
      historyList.value = res.versions || [];
      historyKeep.value = res.keep || 0;
    }
  } finally {
    historyBusy.value = false;
  }
}

function closeHistory() {
  if (historyUsing.value) return;
  historyFor.value = null;
  historyList.value = [];
}

// 切回某一版。后端会**先把当前这版也存进历史**再覆盖 —— 所以"切回去又后悔"还能再切回来，
// 前端不需要自己备份。切完必须重拉列表：当前这版换了，刚被顶掉的那版要出现在历史里。
// ⚠️ 同时**刚点的那一版会从列表里消失**（它就变成"当前这版"了，后端会把它的目录收掉）——
//    这是 2026-09-19 修掉"点多一张一样的"之后的样子，不是历史丢了，别当 bug 再改回去。
async function useHistory(v) {
  const it = historyFor.value;
  if (!it || !v?.name || historyUsing.value) return;
  historyUsing.value = v.name;
  try {
    const res = await withTask((id) => api.useMaterialHistory(id, it.kind, it.index, v.name));
    if (res) {
      await refreshTask();
      const again = await withTask((id) => api.materialHistory(id, it.kind, it.index));
      if (again) {
        historyList.value = again.versions || [];
        historyKeep.value = again.keep || 0;
      }
      say(`「${it.name}」已切回 ${historyStamp(v)} 那一版`, 'ok');
    }
  } finally {
    historyUsing.value = '';
  }
}
</script>

<template>
  <div class="workbench" :class="{ 'project-workbench': workspace }">
    <CreateStart
      v-if="emptyStart && !adding"
      v-model:title="starterTitle"
      :busy="busy"
      :recent="recent"
      @assistant="openAssistant"
      @materials="openAdd"
      @canvas="emit('canvas', { title: starterTitle, ...$event })"
      @open="emit('open', $event)"
      @configure="emit('configure')"
    />

    <section v-if="!emptyStart || adding" class="step">
      <header class="step-head">
        <span class="num">01</span>
        <div class="titles">
          <h2>{{ workspace && !start ? '素材工坊' : '准备素材' }}</h2>
          <p>上传或添加你需要的素材，支持图片、音频等多种类型。</p>
        </div>
        <div class="head-actions">
          <button
            v-if="genRunning"
            class="btn-ghost stopbtn"
            type="button"
            title="不再开新项；已发出去的这几项跑完就停，已经出来的不会撤"
            @click="genStop = true"
          >
            <span class="spin" aria-hidden="true"></span>
            停止
          </button>
          <button
            v-else
            class="btn-ghost"
            type="button"
            :disabled="busy || !genPickedItems.length"
            :title="
              genPickedItems.length
                ? `按每张卡片上的提示词出图（已选 ${genPickedItems.length} 项；图像 4 路 / 音色 2 路并发）`
                : '先在卡片左上角勾选要生成的素材（没有提示词的卡片不能勾，先用「提示词」攒一段）'
            "
            @click="autoGenerate"
          >
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M10 3.2l1.5 3.9 3.9 1.5-3.9 1.5L10 14l-1.5-3.9L4.6 8.6l3.9-1.5L10 3.2Z"
                stroke="currentColor"
                stroke-width="1.3"
                stroke-linejoin="round"
              />
              <path
                d="M15.4 13.4l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z"
                fill="currentColor"
              />
            </svg>
            自动生成<span v-if="genPickedItems.length" class="gnum">{{ genPickedItems.length }}</span>
          </button>
          <button
            v-if="genPickedItems.length && !genRunning"
            class="quiet tiny"
            type="button"
            title="取消所有勾选"
            @click="clearGenPick"
          >
            清空
          </button>

          <button
            class="btn-ghost ai-assistant"
            type="button"
            :disabled="busy"
            title="传一份剧本/设定文档，让它把角色、场景、道具、音色和各自的提示词先攒好 —— 只落条目，不出图不出音"
            @click="openAssistant"
          >
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M10 3.2l1.5 3.9 3.9 1.5-3.9 1.5L10 14l-1.5-3.9L4.6 8.6l3.9-1.5L10 3.2Z"
                stroke="currentColor"
                stroke-width="1.3"
                stroke-linejoin="round"
              />
              <path
                d="M15.4 13.4l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z"
                fill="currentColor"
              />
            </svg>
            AI 助手
          </button>

          <button
            class="btn-ghost add-material"
            type="button"
            :disabled="busy"
            @click="adding ? cancelAdd() : openAdd()"
          >
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M10 4.5v11M4.5 10h11"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
              />
            </svg>
            {{ adding ? '取消添加' : '添加素材' }}
          </button>
        </div>
      </header>

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
          >
            {{ k.label }}
          </button>
        </div>
        <p class="af-hint">选一类，下面那一组里就会多一张卡片，名字和内容都在那张卡片上填。</p>
      </div>

      <p v-if="genNote" class="progress-note">{{ genNote }}</p>

      <div v-if="visibleGroups.length" class="mgroups">
        <section
          v-for="g in visibleGroups"
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

            <button
              v-if="groupSelectable(g).length"
              class="gall"
              type="button"
              :disabled="genRunning"
              :title="`${groupAllPicked(g) ? '取消' : ''}勾选本组能自动生成的 ${groupSelectable(g).length} 项`"
              @click="toggleGroupPick(g)"
            >
              {{ groupAllPicked(g) ? '取消全选' : '全选' }}
            </button>
          </div>

          <div v-if="g.items.length" class="mgrid">
            <article
              v-for="it in g.items"
              :key="it.key"
              class="mcard"
              :class="{ voicing: it.kind === 'voice', picked: isGenPicked(it) }"
              :title="it.kind === 'voice' ? '音色样本仅供素材准备，当前视频服务不接收参考音频' : undefined"
            >
              <div v-if="editing === it.key" class="edit-form" @click.stop>
                <input
                  v-model="editName"
                  maxlength="40"
                  placeholder="名字"
                  aria-label="素材名字"
                  @keydown.enter.prevent="commitEdit(it)"
                />
                <div class="edit-ops">
                  <label
                    class="tiny mup"
                    :title="`上传${isAudioKind(it.kind) ? '音频' : '图片'}（直接作为素材，不走生成）`"
                  >
                    <input
                      type="file"
                      hidden
                      :accept="isAudioKind(it.kind) ? 'audio/*' : 'image/*'"
                      :disabled="busy || !!uploading"
                      @change="onPickFile($event, g, it)"
                    />
                    {{ uploading === it.key ? '上传中…' : '上传' }}
                  </label>
                  <button
                    class="btn-ghost xs"
                    :class="{ danger: isFresh(it) }"
                    type="button"
                    @click="cancelEdit(it)"
                  >
                    {{ isFresh(it) ? '删除' : '取消' }}
                  </button>
                  <button
                    class="btn-primary xs"
                    type="button"
                    :disabled="!editName.trim() || busy"
                    @click="commitEdit(it)"
                  >
                    保存
                  </button>
                </div>
              </div>

              <template v-else>
                <div
                  class="mthumb"
                  :class="{ audio: isAudioKind(it.kind), gennow: genNow.has(it.key) }"
                  :title="it.url && !isAudioKind(it.kind) ? '点击看大图，可下载' : ''"
                  @click="onThumbClick($event, it)"
                >
                  <label
                    v-if="canAutoGen(it)"
                    class="gpick"
                    :class="{ on: isGenPicked(it), busy: genRunning }"
                    :title="isGenPicked(it) ? '取消勾选' : '勾上它，之后可以点「自动生成」批量出图'"
                    @click.stop.prevent="toggleGenPick(it)"
                  >
                    <input type="checkbox" :checked="isGenPicked(it)" :disabled="genRunning" tabindex="-1" />
                    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                      <path
                        d="M5 10.4l3.3 3.3L15 6.8"
                        stroke="currentColor"
                        stroke-width="2.2"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                    </svg>
                  </label>
                  <div v-if="genNow.has(it.key)" class="gnow">
                    <span class="spin" aria-hidden="true"></span>生成中…
                  </div>

                  <img
                    v-if="it.url && it.kind !== 'voice' && it.kind !== 'audio'"
                    :src="it.url"
                    :alt="`${it.name} 素材图`"
                    loading="lazy"
                    decoding="async"
                  />
                  <audio
                    v-else-if="(it.kind === 'voice' || it.kind === 'audio') && (it.audioUrl || it.url)"
                    class="vaudio"
                    :src="it.audioUrl || it.url"
                    controls
                    preload="none"
                    @click.stop
                  ></audio>
                  <div v-else class="cempty">{{ emptyText(it) }}</div>
                </div>
                <div class="mmeta">
                  <strong>{{ it.name }}</strong>
                  <p>{{ it.desc }}</p>
                </div>
                <div class="mops">
                  <label
                    class="tiny mup"
                    :title="`上传${isAudioKind(it.kind) ? '音频' : '图片'}（直接作为素材，不走生成）`"
                  >
                    <input
                      type="file"
                      hidden
                      :accept="isAudioKind(it.kind) ? 'audio/*' : 'image/*'"
                      :disabled="busy || !!uploading"
                      @change="onPickFile($event, g, it)"
                    />
                    {{ uploading === it.key ? '上传中' : '上传' }}
                  </label>
                  <button
                    v-if="isAudioKind(it.kind)"
                    class="quiet tiny"
                    type="button"
                    :disabled="busy"
                    @click.stop="startRecord(it)"
                  >
                    {{ recording === it.key ? '停止录音' : '录制' }}
                  </button>
                  <button
                    v-else
                    class="quiet tiny"
                    type="button"
                    :disabled="busy"
                    @click.stop="startEdit(it)"
                  >
                    编辑
                  </button>
                  <button class="quiet tiny" type="button" :disabled="busy" @click.stop="removeItem(it)">
                    删除
                  </button>

                  <button
                    v-if="HISTORY_KINDS.includes(it.kind)"
                    class="quiet tiny"
                    type="button"
                    :disabled="busy"
                    :title="`看「${it.name}」出过的每一版，可以切回前面某一版`"
                    @click.stop="openHistory(it)"
                  >
                    生成历史
                  </button>

                  <button
                    v-if="GEN_KINDS.includes(it.kind)"
                    class="quiet tiny"
                    type="button"
                    :title="
                      it.prompt
                        ? `看 / 改「${it.name}」的提示词${IMAGE_KINDS.includes(it.kind) ? '和出图比例' : ''}`
                        : `「${it.name}」还没有提示词 —— 点这里写一段，之后就能自动生成`
                    "
                    @click.stop="openPrompt(it)"
                  >
                    提示词
                  </button>

                  <button
                    v-if="['character', 'scene', 'prop', 'image', 'voice'].includes(it.kind)"
                    class="quiet tiny"
                    type="button"
                    :disabled="busy || aiBusy"
                    :title="`用一段描述生成「${it.name}」的${AI_LABEL[it.kind] || '素材'}`"
                    @click.stop="openAi(it)"
                  >
                    AI 生成
                  </button>
                </div>
              </template>
            </article>
          </div>
          <p v-else class="mempty">{{ g.empty }}</p>
        </section>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="asOpen" class="pmask" :class="{ 'workspace-theme': workspace }" @click.self="closeAssistant">
        <div
          class="as-dialog"
          role="dialog"
          aria-modal="true"
          aria-label="素材规划助手"
          @dragover.prevent="asDrag = true"
          @dragleave.prevent="asDrag = false"
          @drop.prevent="asDrop"
        >
          <header class="pd-head">
            <h3>AI 助手 · 从文档攒素材</h3>
            <span class="as-badge" :class="{ off: !asOnline }">{{
              asOnline ? '文本模型已就绪' : '未配文本模型'
            }}</span>
            <button
              class="btn-ghost xs"
              type="button"
              :disabled="asBusy || asUploading"
              @click="closeAssistant"
            >
              关闭
            </button>
          </header>

          <div class="as-docs">
            <button
              class="as-add"
              type="button"
              :disabled="asUploading || asBusy"
              @click="asFileInput && asFileInput.click()"
            >
              {{ asUploading ? '读入中…' : '＋ 传文档' }}
            </button>
            <input
              ref="asFileInput"
              class="as-file"
              type="file"
              multiple
              :accept="AS_DOC_ACCEPT"
              @change="asPickFiles"
            />
            <span v-for="d in asDocs" :key="d.name" class="as-chip">
              <b>{{ d.name }}</b>
              <i>{{ d.ext }} · {{ fmtBytes(d.bytes) }}</i>
              <button
                type="button"
                :disabled="asBusy || asUploading"
                title="从这一轮的附件里去掉（不会动素材区的任何东西）"
                @click="asRemoveDoc(d.name)"
              >
                ×
              </button>
            </span>
            <span v-if="!asDocs.length" class="as-nodoc"
              >还没有文档 —— 把文件拖进这个窗口，或点「＋ 传文档」</span
            >
          </div>

          <div ref="asScroller" class="athread">
            <div v-for="(m, i) in asMsgs" :key="i" class="row" :class="m.role">
              <div class="bubble" :class="{ bad: m.kind === 'bad' }">{{ m.text }}</div>
            </div>
            <div v-if="asBusy" class="row assistant">
              <div class="bubble typing">
                <span class="spin" aria-hidden="true"></span>正在读文档、抽素材…
              </div>
            </div>
          </div>

          <div v-if="asChips.length || asShowQuick" class="chips">
            <template v-if="asChips.length">
              <button
                v-for="s in asChips"
                :key="s"
                class="chip"
                type="button"
                :disabled="asBusy"
                @click="asSend(s)"
              >
                {{ s }}
              </button>
            </template>
            <template v-else>
              <button
                v-for="q in AS_QUICK"
                :key="q.label"
                class="chip"
                type="button"
                :disabled="asBusy"
                @click="asSend(q.prompt)"
              >
                {{ q.mp }}MP
              </button>
            </template>
          </div>

          <footer class="afoot">
            <div class="composer">
              <textarea
                v-model="asInput"
                rows="1"
                :disabled="asBusy || asUploading"
                placeholder="想怎么抽就说一句（也可以什么都不说，直接点发送）"
                aria-label="给素材规划助手的话"
                @keydown.enter.exact.prevent="asSend()"
              ></textarea>
              <button class="btn-primary" type="button" :disabled="asBusy || asUploading" @click="asSend()">
                <span v-if="asBusy" class="spin" aria-hidden="true"></span>
                {{ asBusy ? '处理中…' : '发送' }}
              </button>
            </div>
            <p class="as-note">
              这一轮会读：{{
                asDocs.length ? asDocs.map((d) => d.name).join('、') : '（没有文档，只按你这句话）'
              }}
              · 只落条目和提示词，不出图不出音
            </p>
          </footer>

          <div v-if="asDrag" class="as-dropmask">松手就把文档读进来</div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="promptFor" class="pmask" :class="{ 'workspace-theme': workspace }" @click.self="closePrompt">
        <div class="pdialog td-dialog" role="dialog" aria-modal="true" aria-label="提示词">
          <header class="pd-head">
            <h3>「{{ promptFor.name }}」的提示词</h3>
            <button class="btn-ghost xs" type="button" :disabled="pdBusy" @click="closePrompt">关闭</button>
          </header>
          <p class="pd-hint">
            <template v-if="promptFor.kind === 'voice'">
              这是配音时用来挑音色的描述 —— 它只决定声音，不影响画面。
              改完保存后，原来的音色样本会作废（它对应的是旧音色），重新点「AI 生成」合成一段新的。
            </template>
            <template v-else>
              出图时用的就是这段描述 —— 想让它更准就直接在这里改。 也可以回「AI
              助手」里说一句（比如"把深山古道改成黄昏"），它会就地改写这一条。
            </template>
          </p>
          <textarea
            v-model="pdText"
            class="td-prompt"
            :disabled="pdBusy"
            maxlength="2000"
            :placeholder="
              promptFor.kind === 'voice'
                ? '例如：清亮的少女音，语速偏快'
                : '例如：十八岁少女，乌黑及腰长发用红绳束起，月白交领襦裙'
            "
            :aria-label="`${promptFor.name} 的提示词`"
            @keydown.enter.ctrl.prevent="savePrompt"
            @keydown.enter.meta.prevent="savePrompt"
          ></textarea>

          <div v-if="IMAGE_KINDS.includes(promptFor.kind)" class="td-ratio">
            <span class="pd-rlabel">出图比例</span>
            <div class="seg">
              <button
                v-for="r in AI_RATIOS"
                :key="r"
                type="button"
                :class="{ on: r === pdRatio }"
                :disabled="pdBusy"
                @click="pdRatio = r"
              >
                {{ r }}
              </button>
            </div>
            <span class="pd-count">
              {{ ratioFromDoc(promptFor) ? '从文档里读到的比例，可以改' : '文档里没写比例，默认 16:9' }}
            </span>
          </div>

          <footer class="pd-foot">
            <span class="pd-count">{{ pdText.length }} / 2000</span>
            <button class="btn-ghost xs" type="button" :disabled="pdBusy" @click="closePrompt">取消</button>
            <button class="btn-primary xs" type="button" :disabled="pdBusy || !pdDirty" @click="savePrompt">
              <span v-if="pdBusy" class="spin" aria-hidden="true"></span>
              {{ pdBusy ? '保存中…' : '保存' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="aiFor" class="pmask" :class="{ 'workspace-theme': workspace }" @click.self="closeAi">
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
            描述你想要的场景 —— 是什么地方、空间与材质、想要的光线氛围。 会先交给场景 Agent 扩写成「环境 +
            镜头 + 光影」的空镜描述，画面里不会出现人物。
          </p>
          <p v-else-if="aiFor.kind === 'image'" class="pd-hint">
            描述你要的画面 —— 画面里有什么、在做什么、处在什么环境、想要的光线与机位。 会先交给画面 Agent
            扩写成一段完整画面描述，再出图。这类图常被直接当首帧 / 尾帧用， 所以写得越具体，接戏越顺。
          </p>
          <p v-else-if="aiFor.kind === 'prop'" class="pd-hint">
            描述这件道具 —— 是什么、大致尺寸、形状结构、材质与颜色、新旧磨损。 会先交给道具 Agent
            扩写，再出一张三视图（正视 / 侧视 / 后视横向并排）。这段话只用于这次出图。
          </p>
          <p v-else class="pd-hint">
            描述你想要的声音 —— 年龄感、音色（清亮 / 沙哑 / 低沉 / 甜美）、语速、气质都可以写。 会先交给配音
            Agent，从当前音频后端的音色池里挑一个最贴的，再用它合成一段样本。
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
            <span v-if="aiFor.kind === 'prop'" class="pd-count">三视图固定 16:9（三个视角横向并排）</span>

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
                >
                  {{ g.label }}
                </button>
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
                  :title="
                    aiFor.kind === 'character'
                      ? `正面定妆照用 ${r}。四视图设定图固定 16:9 —— 四个视角要并排，竖版排不下`
                      : `图片用 ${r}（默认跟随作品画幅）`
                  "
                  @click="aiRatio = r"
                >
                  {{ r }}
                </button>
              </div>
            </div>
            <span class="pd-count">{{ aiPrompt.length }} / {{ aiMaxLen }}</span>
            <button
              class="btn-primary"
              type="button"
              :disabled="!aiPrompt.trim() || aiBusy"
              @click="submitAi"
            >
              <span v-if="aiBusy" class="spin" aria-hidden="true"></span>
              {{ aiBusy ? '生成中…' : 'AI 生成' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="previewItem"
        class="pmask"
        :class="{ 'workspace-theme': workspace }"
        @click.self="previewItem = null"
      >
        <div
          class="pdialog pv-dialog"
          role="dialog"
          aria-modal="true"
          :aria-label="`${previewItem.name} 素材图预览`"
        >
          <header class="pd-head">
            <h3>{{ previewItem.name }}</h3>
            <button class="btn-ghost xs" type="button" @click="previewItem = null">关闭</button>
          </header>

          <p v-if="previewItem.kind === 'character' && previewItem.sendUrl" class="pd-hint pv-note">
            <template v-if="isRef2va">
              这是四视图设定图，<b>出片时进模型的就是这一张</b>（Ref2VA 当参考图）—— 一张里带齐大特写和正 / 侧
              / 背三个全身，参考信息最全。
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

            <a
              class="pv-dl"
              :href="previewItem.url"
              :download="downloadName(previewItem)"
              title="把这张图存到本地，之后可以当素材直接用"
            >
              <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <path
                  d="M10 3.2v8.6m0 0 3.4-3.4M10 11.8 6.6 8.4M4 15.4h12"
                  stroke="currentColor"
                  stroke-width="1.4"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              下载图片
            </a>
          </footer>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="historyFor"
        class="pmask"
        :class="{ 'workspace-theme': workspace }"
        @click.self="closeHistory"
      >
        <div
          class="pdialog hd-dialog"
          role="dialog"
          aria-modal="true"
          :aria-label="`${historyFor.name} 生成历史`"
        >
          <header class="pd-head">
            <h3>「{{ historyFor.name }}」的生成历史</h3>
            <button class="btn-ghost xs" type="button" :disabled="!!historyUsing" @click="closeHistory">
              关闭
            </button>
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
            <div
              v-for="v in historyList"
              :key="v.name || 'current'"
              class="hdcard"
              :class="{ on: v.current }"
            >
              <img
                v-if="v.thumb"
                :src="v.thumb"
                :alt="`${historyFor.name} ${v.current ? '当前这版' : historyStamp(v)}`"
              />
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
              >
                {{ historyUsing === v.name ? '切换中…' : '用这版' }}
              </button>
              <span v-else class="hdon">正在使用</span>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped src="../material-studio.css"></style>

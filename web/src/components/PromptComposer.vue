<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  busy: { type: Boolean, default: false },
  // 保留旧调用方的 props；视频与导出操作已移至工作区工具条。
  taskOpen: { type: Boolean, default: false },
  batchBusy: { type: Boolean, default: false },
  batchProgress: { type: Object, default: null },
  exportBusy: { type: Boolean, default: false },
  hasVideos: { type: Boolean, default: false },
})

const emit = defineEmits(['submit', 'batch-video', 'export'])
const idea = ref('')
const ideaEl = ref(null)
const mode = ref('idea')
const shotCount = ref('auto')
const ratio = ref('')
const concurrency = ref(2)
const advanced = ref(false)
const composing = ref(false)
const chars = ref([])
const showChars = ref(false)
const characterEditor = ref(null)
let characterSequence = 0

const validCharacters = computed(() =>
  chars.value
    .filter((character) => character.name.trim())
    .map((character) => ({ name: character.name.trim(), anchor: character.anchor.trim() })),
)

const fileInput = ref(null)
const sourceName = ref('')
const sourceText = ref('')
const fileError = ref('')
const fileBusy = ref(false)
const maxFileSize = 10 * 1024 * 1024
let fileVersion = 0

function chooseFile() {
  mode.value = 'adapt'
  fileInput.value?.click()
}

// 读取版本号防止「移除 / 重置 / 换文件」后旧异步读取重新写回内容。
async function onFile(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const version = ++fileVersion
  fileError.value = ''
  fileBusy.value = false

  if (!/\.(txt|md|markdown)$/i.test(file.name)) {
    fileError.value = '请选择 TXT 或 Markdown 格式的剧本文件。'
    return
  }
  if (file.size > maxFileSize) {
    fileError.value = '文件超过 10 MB，请精简内容后再导入。'
    return
  }

  fileBusy.value = true
  try {
    const buffer = await file.arrayBuffer()
    if (version !== fileVersion) return
    let text
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(buffer)
    } catch {
      text = new TextDecoder('gbk').decode(buffer)
    }
    if (!text.trim()) {
      fileError.value = '这个文件没有文字内容，请选择其他剧本。'
      return
    }
    sourceName.value = file.name
    sourceText.value = text
    mode.value = 'adapt'
    if (!idea.value.trim()) {
      idea.value = `把《${file.name.replace(/\.[^.]+$/, '')}》改编成短片`
    }
  } catch {
    if (version === fileVersion) fileError.value = '无法读取这个文件，请确认文件可用后重新导入。'
  } finally {
    if (version === fileVersion) fileBusy.value = false
  }
}

function removeFile() {
  fileVersion += 1
  sourceName.value = ''
  sourceText.value = ''
  fileError.value = ''
  fileBusy.value = false
  if (fileInput.value) fileInput.value.value = ''
}

async function addCharacter() {
  showChars.value = true
  chars.value.push({ id: ++characterSequence, name: '', anchor: '' })
  await nextTick()
  const inputs = characterEditor.value?.querySelectorAll('[data-character-name]')
  inputs?.[inputs.length - 1]?.focus()
}

function toggleCharacters() {
  if (!showChars.value && !chars.value.length) {
    addCharacter()
  } else {
    showChars.value = !showChars.value
  }
}

const canSubmit = computed(() => Boolean(idea.value.trim()) && !props.busy && !fileBusy.value)

function submit() {
  if (!canSubmit.value || composing.value) return
  emit('submit', {
    idea: idea.value.trim(),
    shots: shotCount.value === 'auto' ? null : Number(shotCount.value),
    ratio: ratio.value || null,
    concurrency: Number(concurrency.value),
    characters: validCharacters.value.length ? validCharacters.value : null,
    source_text: sourceText.value || null,
  })
}

function onIdeaKeydown(event) {
  if (event.isComposing || composing.value || event.keyCode === 229) return
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    if (!event.repeat) submit()
  }
}

async function focus() {
  await nextTick()
  ideaEl.value?.focus()
}

function setIdea(text) {
  idea.value = String(text ?? '')
  return focus()
}

function reset() {
  idea.value = ''
  chars.value = []
  showChars.value = false
  removeFile()
  mode.value = 'idea'
  shotCount.value = 'auto'
  ratio.value = ''
  concurrency.value = 2
  advanced.value = false
  composing.value = false
}

onBeforeUnmount(() => { fileVersion += 1 })
defineExpose({ focus, setIdea, reset })
</script>

<template>
  <section class="composer" aria-label="新作品创作简报" :aria-busy="busy">
    <header class="composer-heading">
      <div class="mode-switch" role="group" aria-label="创作方式">
        <button class="mode-button" :class="{ selected: mode === 'idea' }" type="button" :aria-pressed="mode === 'idea'" @click="mode = 'idea'">
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M7 13a5 5 0 1 1 6 0v2H7v-2ZM8 18h4M10 6v3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
          灵感创作
        </button>
        <button class="mode-button" :class="{ selected: mode === 'adapt' }" type="button" :aria-pressed="mode === 'adapt'" @click="mode = 'adapt'">
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M11.5 2.5H5a1.5 1.5 0 0 0-1.5 1.5v12A1.5 1.5 0 0 0 5 17.5h10a1.5 1.5 0 0 0 1.5-1.5V7.5l-5-5ZM11.5 2.5v5h5M7 11h6M7 14h4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
          剧本改编
        </button>
      </div>
      <span class="brief-label">CREATIVE BRIEF</span>
    </header>

    <label class="idea-label">
      <span class="sr-only">{{ mode === 'adapt' ? '剧本改编方向' : '创作灵感' }}</span>
      <textarea
        ref="ideaEl"
        v-model="idea"
        class="idea"
        rows="3"
        :aria-label="mode === 'adapt' ? '剧本改编方向' : '创作灵感'"
        aria-keyshortcuts="Control+Enter Meta+Enter"
        placeholder="一个画面、一段故事，或者一个天马行空的念头…"
        @keydown="onIdeaKeydown"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      ></textarea>
    </label>

    <div v-if="mode === 'adapt'" class="adapt-area">
      <svg class="adapt-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6ZM14 3v6h6M12 17v-5m-2 2 2-2 2 2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
      <div class="adapt-copy">
        <strong>让文字，成为画面</strong>
        <p>上传小说或剧本，也可以在上方写下改编方向。</p>
        <span>TXT / Markdown · 最大 10 MB</span>
      </div>
      <button class="upload-button" type="button" @click="chooseFile">{{ sourceName ? '更换剧本' : '选择文件' }}</button>
    </div>

    <div class="composer-tools">
      <button class="tool-button" type="button" :aria-expanded="showChars" @click="toggleCharacters">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="7.5" cy="6" r="3" stroke="currentColor" stroke-width="1.4" /><path d="M2.5 16v-1.5a5 5 0 0 1 10 0V16M15.5 6.5v6M12.5 9.5h6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
        {{ showChars ? '收起角色' : chars.length ? `角色设定（${chars.length}）` : '添加角色' }}
      </button>
      <button class="tool-button" type="button" @click="chooseFile">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m7 10 5-5a3 3 0 0 1 4.2 4.2l-6.5 6.5a4 4 0 0 1-5.6-5.6l6.2-6.2M7 10l-1 1a2 2 0 0 0 2.8 2.8l5.5-5.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
        {{ sourceName ? '更换剧本' : '导入剧本' }}
      </button>
      <input ref="fileInput" class="file-input" type="file" accept=".txt,.md,.markdown" aria-label="导入 TXT 或 Markdown 剧本，最大 10 MB" @change="onFile" />
      <span class="shortcut-hint">Ctrl / Cmd + Enter 开始创作</span>
    </div>

    <div v-if="fileBusy" class="file-loading" role="status">
      <span class="composer-spinner" aria-hidden="true"></span>
      正在读取剧本…
      <button class="text-button" type="button" @click="removeFile">取消导入</button>
    </div>
    <div v-if="sourceName" class="source-file">
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M11 2.5H5a1.5 1.5 0 0 0-1.5 1.5v12A1.5 1.5 0 0 0 5 17.5h10a1.5 1.5 0 0 0 1.5-1.5V8L11 2.5ZM11 2.5V8h5.5M7 11h6M7 14h4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
      <span class="source-name" :title="sourceName">{{ sourceName }}</span>
      <span class="source-length">{{ sourceText.length.toLocaleString() }} 字符</span>
      <button class="icon-button" type="button" title="移除剧本文件" :aria-label="`移除剧本文件 ${sourceName}`" @click="removeFile">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
      </button>
    </div>
    <p v-if="fileError" class="file-error" role="alert">{{ fileError }}</p>

    <div v-if="showChars" ref="characterEditor" class="character-editor" aria-label="角色设定">
      <p class="character-hint">设定故事中的主角。填写名字即可，外观留空由 AI 设计。</p>
      <div v-for="(character, index) in chars" :key="character.id" class="character-row">
        <input v-model="character.name" data-character-name class="character-name" :aria-label="`角色 ${index + 1} 的名字`" placeholder="角色名字" />
        <input v-model="character.anchor" class="character-appearance" :aria-label="`角色 ${index + 1} 的外观描述`" placeholder="外观描述，如年龄、发型、服装（可选）" />
        <button class="icon-button remove-character" type="button" title="移除角色" :aria-label="`移除角色 ${character.name || index + 1}`" @click="chars.splice(index, 1)">
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
        </button>
      </div>
      <button class="text-button add-character" type="button" @click="addCharacter">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 5v10M5 10h10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
        再添一位角色
      </button>
    </div>

    <div v-if="advanced" class="advanced-settings">
      <label class="concurrency-field">
        <span>生成并发</span>
        <select v-model="concurrency" aria-label="生成并发数量">
          <option :value="1">1 · 节省额度</option>
          <option :value="2">2 · 推荐</option>
          <option :value="3">3</option>
          <option :value="4">4 · 更快</option>
        </select>
      </label>
      <p>根据模型服务的并发限制选择，默认同时生成 2 镜。</p>
    </div>

    <footer class="composer-controls">
      <div class="parameters">
        <label class="parameter">
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="3" y="5" width="14" height="12" rx="1.5" stroke="currentColor" stroke-width="1.3" /><path d="M6 2.5h11M7 5v12M13 5v12M3 9h4M3 13h4M13 9h4M13 13h4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" /></svg>
          <span>镜数</span>
          <select v-model="shotCount" aria-label="分镜镜数">
            <option value="auto">AI 自动</option>
            <option v-for="count in [4, 6, 8, 10, 12, 16, 20]" :key="count" :value="count">{{ count }} 镜</option>
          </select>
        </label>
        <label class="parameter">
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="2.5" y="5" width="15" height="10" rx="1.5" stroke="currentColor" stroke-width="1.4" /></svg>
          <span>画幅</span>
          <select v-model="ratio" aria-label="画面比例">
            <option value="">AI 自动</option>
            <option value="16:9">16:9 横屏</option>
            <option value="9:16">9:16 竖屏</option>
            <option value="1:1">1:1 方屏</option>
          </select>
        </label>
        <button class="icon-button more-settings" :class="{ expanded: advanced }" type="button" :aria-expanded="advanced" :title="advanced ? '收起更多设置' : '更多设置：生成并发'" aria-label="更多设置：生成并发" @click="advanced = !advanced">
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 6h14M3 14h14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /><circle cx="7" cy="6" r="2" fill="var(--surface, #fff)" stroke="currentColor" stroke-width="1.4" /><circle cx="13" cy="14" r="2" fill="var(--surface, #fff)" stroke="currentColor" stroke-width="1.4" /></svg>
        </button>
      </div>
      <button class="create-button" type="button" :disabled="!canSubmit" @click="submit">
        <span v-if="busy" class="composer-spinner" aria-hidden="true"></span>
        {{ busy ? '创作中…' : '开始创作' }}
        <svg v-if="!busy" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M5 15 15 5M5 5h10v10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </footer>
  </section>
</template>

<style scoped>
.composer { box-sizing: border-box; width: 100%; min-width: 0; padding: 22px 24px 0; border: 1px solid var(--line, #e4e5dd); border-radius: 18px; background: var(--surface, #fff); color: var(--fg, #262925); color-scheme: light; box-shadow: 0 5px 22px rgb(38 41 37 / 3%); }
.composer button { font: inherit; box-shadow: none; }
.composer svg { width: 18px; height: 18px; flex: none; }
.composer button:focus-visible, .composer select:focus-visible, .character-row input:focus-visible { outline: 2px solid var(--accent, #dc603e); outline-offset: 3px; }
.composer-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.mode-switch { display: inline-flex; gap: 3px; padding: 3px; background: var(--surface-2, #f4f2ed); border-radius: 8px; }
.composer .mode-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 7px 11px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--fg-2, #72766e); font-size: 12px; white-space: nowrap; }
.mode-button svg { width: 15px; height: 15px; }
.composer .mode-button:hover { background: var(--surface-3, #eae7df); border-color: transparent; color: var(--fg, #262925); }
.composer .mode-button.selected { background: var(--surface, #fff); border-color: var(--line, #e4e5dd); color: var(--fg, #262925); box-shadow: 0 1px 3px rgb(38 41 37 / 4%); }
.mode-button.selected svg { color: var(--accent, #dc603e); }
.brief-label { font-size: 9px; font-weight: 500; letter-spacing: 1.8px; color: var(--fg-3, #85897f); white-space: nowrap; }
.idea-label { display: block; margin: 18px 0 9px; }
.composer .idea { display: block; box-sizing: border-box; width: 100%; min-height: 112px; padding: 7px 0; border: 0; border-radius: 0; background: transparent; color: var(--fg, #262925); font-size: 16px; line-height: 1.8; resize: vertical; box-shadow: none; outline: none; }
.composer .idea:focus { background: transparent; border: 0; box-shadow: none; }
.composer .idea:focus-visible { outline: 1px solid var(--line, #e4e5dd); outline-offset: 5px; border-radius: 4px; }
.idea::placeholder { color: var(--fg-3, #85897f); }
.composer-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 13px; margin: 0 0 18px; }
.composer .tool-button { display: inline-flex; align-items: center; gap: 6px; min-height: 34px; padding: 5px 0; border: 1px solid transparent; border-radius: 5px; background: transparent; color: var(--fg-2, #72766e); font-size: 12px; }
.composer .tool-button:hover { background: transparent; color: var(--accent, #dc603e); border-color: transparent; }
.tool-button svg { width: 16px; height: 16px; }
.shortcut-hint { margin-left: auto; color: var(--fg-3, #85897f); font-size: 10px; }
.file-input { display: none; }
.adapt-area { display: flex; align-items: center; gap: 13px; margin: 0 0 14px; padding: 16px; border: 1px dashed var(--line, #e4e5dd); border-radius: 10px; background: var(--surface-2, #f4f2ed); }
.adapt-area .adapt-icon { width: 28px; height: 28px; color: var(--accent, #dc603e); }
.adapt-copy { flex: 1; min-width: 0; }
.adapt-copy strong { font-size: 13px; font-weight: 500; }
.adapt-copy p { margin: 4px 0; font-size: 12px; color: var(--fg-2, #72766e); line-height: 1.6; }
.adapt-copy > span { font-size: 10px; color: var(--fg-3, #85897f); }
.composer .upload-button { flex: none; padding: 8px 12px; border: 1px solid var(--line, #e4e5dd); border-radius: 7px; background: var(--surface, #fff); color: var(--fg, #262925); font-size: 12px; }
.composer .upload-button:hover { border-color: var(--accent, #dc603e); background: var(--surface, #fff); color: var(--accent, #dc603e); }
.source-file { display: flex; align-items: center; gap: 8px; margin-bottom: 15px; padding: 8px 10px; background: var(--surface-2, #f4f2ed); border: 1px solid var(--line, #e4e5dd); border-radius: 8px; color: var(--mint, #3f8268); }
.source-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg, #262925); font-size: 12px; }
.source-length { margin-left: auto; font-size: 10px; color: var(--fg-2, #72766e); white-space: nowrap; }
.file-loading { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; color: var(--fg-2, #72766e); font-size: 12px; }
.file-error { margin: 0 0 16px; padding: 10px 12px; border: 1px solid #edc3b8; border-radius: 8px; background: #fff4f0; color: #a13f2b; font-size: 12px; line-height: 1.6; }
.character-editor { margin-bottom: 18px; padding: 13px; border: 1px solid var(--line, #e4e5dd); border-radius: 10px; background: var(--surface-2, #f4f2ed); }
.character-hint { margin: 0 0 10px; color: var(--fg-2, #72766e); font-size: 11px; line-height: 1.7; }
.character-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.character-row input { width: 100%; min-width: 0; padding: 9px 10px; border: 1px solid var(--line, #e4e5dd); border-radius: 6px; background: var(--surface, #fff); color: var(--fg, #262925); font-size: 12px; }
.character-row input:focus { border-color: var(--accent, #dc603e); box-shadow: none; }
.character-name { flex: 0 1 128px; }
.character-appearance { flex: 1; }
.composer .text-button { display: inline-flex; align-items: center; gap: 4px; padding: 4px 0; border: 1px solid transparent; border-radius: 4px; background: transparent; color: var(--fg-2, #72766e); font-size: 11px; }
.composer .text-button:hover { color: var(--accent, #dc603e); background: transparent; border-color: transparent; }
.text-button svg { width: 14px; height: 14px; }
.composer .icon-button { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 32px; height: 32px; padding: 0; border: 1px solid transparent; border-radius: 7px; background: transparent; color: var(--fg-2, #72766e); }
.composer .icon-button:hover { background: var(--surface-3, #eae7df); color: var(--fg, #262925); border-color: transparent; }
.advanced-settings { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 18px; padding: 12px 0 16px; border-top: 1px solid var(--line, #e4e5dd); }
.concurrency-field { display: inline-flex; align-items: center; gap: 10px; color: var(--fg-2, #72766e); font-size: 12px; }
.concurrency-field select { width: auto; background-color: var(--surface-2, #f4f2ed); }
.advanced-settings p { margin: 0; font-size: 11px; color: var(--fg-3, #85897f); }
.composer-controls { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 16px 0; border-top: 1px solid var(--line, #e4e5dd); }
.parameters { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.parameter { display: inline-flex; align-items: center; gap: 6px; padding: 0 8px; border: 1px solid var(--line, #e4e5dd); border-radius: 7px; background: var(--surface, #fff); color: var(--fg-2, #72766e); font-size: 11px; }
.parameter svg { width: 15px; height: 15px; }
.parameter > span { white-space: nowrap; }
.composer select { color: var(--fg, #262925); font-size: 12px; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='m3 4.5 3 3 3-3' fill='none' stroke='%2372766e' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 7px center; }
.parameter select { width: auto; max-width: 120px; min-height: 35px; padding: 7px 22px 7px 2px; border: 0; border-radius: 4px; background-color: transparent; }
.parameter select:focus { box-shadow: none; background-color: transparent; }
.composer .more-settings { width: 36px; height: 37px; }
.composer .more-settings.expanded { background: var(--accent-dim, #fbede5); color: var(--accent, #dc603e); }
.composer .create-button { display: inline-flex; align-items: center; justify-content: center; gap: 12px; min-height: 42px; margin-left: auto; padding: 10px 18px; border: 1px solid var(--accent, #dc603e); border-radius: 8px; background: var(--accent, #dc603e); color: var(--accent-ink, #fff); font-size: 13px; font-weight: 600; white-space: nowrap; }
.composer .create-button:hover:not(:disabled) { background: #c95233; border-color: #c95233; }
.composer .create-button:disabled { opacity: .5; cursor: not-allowed; }
.create-button svg { width: 16px; height: 16px; }
.composer-spinner { width: 13px; height: 13px; flex: none; border: 1.5px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: composer-spin .8s linear infinite; }
@keyframes composer-spin { to { transform: rotate(360deg); } }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
@media (max-width: 760px) {
  .composer { padding: 17px 18px 0; }
  .brief-label { font-size: 8px; letter-spacing: 1px; }
  .parameters { gap: 6px; }
}
@media (max-width: 520px) {
  .composer { padding: 14px 15px 0; }
  .composer-heading { align-items: flex-start; flex-direction: column; gap: 9px; }
  .brief-label { align-self: flex-end; }
  .idea-label { margin-top: 10px; }
  .composer .idea { font-size: 16px; }
  .shortcut-hint { display: none; }
  .composer-controls { gap: 13px; }
  .parameters { width: 100%; }
  .parameter { gap: 4px; padding-inline: 6px; }
  .parameter svg { display: none; }
  .parameter select { max-width: 108px; }
  .composer .create-button { width: 100%; }
  .adapt-area { flex-wrap: wrap; gap: 10px; padding: 12px; }
  .adapt-copy { flex-basis: calc(100% - 40px); }
  .composer .upload-button { margin-left: 38px; }
  .character-row { display: grid; grid-template-columns: minmax(0, 1fr) 32px; gap: 7px; margin-bottom: 12px; }
  .character-name { grid-column: 1; grid-row: 1; }
  .character-appearance { grid-column: 1 / -1; grid-row: 2; }
  .remove-character { grid-column: 2; grid-row: 1; }
  .source-length { display: none; }
  .source-file .icon-button { margin-left: auto; }
}
@media (prefers-reduced-motion: reduce) {
  .composer-spinner { animation: none; }
}
</style>

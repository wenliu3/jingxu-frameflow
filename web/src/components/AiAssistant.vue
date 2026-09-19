<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../api'

// 右侧「AI 创作助手」。
//
// 一轮对话就是一次 /api/assistant 调用：助手回的 JSON 里除了给用户看的话，
// 还带角色/场景/道具的结构化产出，后端直接写进作品 —— 所以这里聊完必须
// 让上层重新拉一次任务（否则左侧素材区还是旧的）。
//
// 会话不落库：气泡就是页面状态，历史每次随请求带上最近几轮，刷新页面重开。
const props = defineProps({
  taskId: { type: String, default: '' },
  cfg: { type: Object, default: () => ({}) },
  ensureTask: { type: Function, required: true },
  refresh: { type: Function, default: null },
  notify: { type: Function, default: null },
})

const QUICK = [
  { label: '生成角色', prompt: '帮我生成一个角色' },
  { label: '生成场景', prompt: '帮我生成一个场景' },
  { label: '生成分镜', prompt: '帮我把这个故事拆成分镜，每行一个镜头' },
  { label: '优化视频提示词', prompt: '帮我把 02 里那段视频提示词优化得更具体' },
]

const GREETING = '你好！我是你的 AI 创作助手。你可以直接上传素材，编写提示词生成视频，也可以让我帮你生成故事、角色、场景、提示词和分镜。'

const messages = ref([{ role: 'assistant', text: GREETING, time: '' }])
const input = ref('')
const sending = ref(false)
const scroller = ref(null)

// 没有文本 API 时助手不可用 —— 提前在头部标出来，别让用户打了一句话才发现
const online = computed(() => !!String(props.cfg?.text_api_key || '').trim())

function stamp() {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function scrollToEnd() {
  nextTick(() => {
    const el = scroller.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

onMounted(scrollToEnd)

async function send(text) {
  const msg = String(text ?? input.value).trim()
  if (!msg || sending.value) return
  if (!online.value) {
    if (props.notify) props.notify('还没配文本模型：去「服务设置」填 DeepSeek Key，助手才能对话', 'error')
    return
  }
  input.value = ''
  messages.value.push({ role: 'user', text: msg, time: stamp() })
  sending.value = true
  scrollToEnd()
  try {
    const id = await props.ensureTask()
    // 只回传最近几轮：助手每轮都会拿到「当前作品状态」，历史给太长反而打架
    const history = messages.value
      .slice(-8)
      .map((m) => ({ role: m.role, text: m.text }))
    const res = await api.assistantChat({ taskId: id, message: msg, history })
    const created = res.created || {}
    const n = (created.characters?.length || 0) + (created.assets?.length || 0)
    messages.value.push({
      role: 'assistant',
      text: res.reply,
      time: stamp(),
      note: n ? `已为你准备了 ${n} 项素材，可以在左侧「准备素材」区域查看和编辑。` : '',
      chips: res.suggestions || [],
    })
    // 作品被改了才刷新，纯闲聊不惊动左边
    if (n && props.refresh) await props.refresh()
  } catch (err) {
    messages.value.push({ role: 'assistant', text: `调用失败：${err.message}`, time: stamp(), error: true })
  } finally {
    sending.value = false
    scrollToEnd()
  }
}

function onKey(event) {
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

function reset() {
  messages.value = [{ role: 'assistant', text: GREETING, time: '' }]
  input.value = ''
}
</script>

<template>
  <aside class="assistant" aria-label="AI 创作助手">
    <header class="ahead">
      <span class="avatar" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none"><rect x="4.5" y="6.5" width="15" height="12" rx="3.4" stroke="currentColor" stroke-width="1.6" /><circle cx="9.4" cy="12.2" r="1.25" fill="currentColor" /><circle cx="14.6" cy="12.2" r="1.25" fill="currentColor" /><path d="M12 6.5V3.6M10 2.6h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
      </span>
      <div class="atitle">
        <strong>AI 创作助手</strong>
        <span class="status" :class="{ off: !online }">
          <i aria-hidden="true"></i>{{ online ? '在线' : '未配置文本模型' }}
        </span>
      </div>
      <button class="icon-btn" type="button" title="清空对话" aria-label="清空对话" @click="reset">
        <svg viewBox="0 0 20 20" fill="none"><path d="M4.6 5.6h10.8M8.2 5.6V3.9h3.6v1.7M6 5.6l.6 9.5h6.8l.6-9.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </header>

    <div ref="scroller" class="athread">
      <template v-for="(m, i) in messages" :key="i">
        <div class="row" :class="m.role">
          <div v-if="m.role === 'assistant'" class="mini-avatar" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none"><rect x="4.5" y="6.5" width="15" height="12" rx="3.4" stroke="currentColor" stroke-width="1.6" /><circle cx="9.4" cy="12.2" r="1.25" fill="currentColor" /><circle cx="14.6" cy="12.2" r="1.25" fill="currentColor" /></svg>
          </div>
          <div class="bubble-wrap">
            <div class="bubble" :class="{ bad: m.error }">{{ m.text }}</div>
            <p v-if="m.note" class="note">
              <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="7.2" stroke="currentColor" stroke-width="1.3" /><path d="M10 9v4.2M10 6.6v.1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
              {{ m.note }}
            </p>
            <div v-if="m.chips && m.chips.length" class="chips">
              <button v-for="c in m.chips" :key="c" class="chip" type="button" :disabled="sending" @click="send(c)">{{ c }}</button>
            </div>
            <time v-if="m.time" class="stamp">{{ m.time }}</time>
          </div>
        </div>

        <!-- 快捷提示跟在问候语后面，与设计稿一致 -->
        <div v-if="i === 0" class="quick">
          <p class="quick-label">你可以试试：</p>
          <div class="chips">
            <button v-for="q in QUICK" :key="q.label" class="chip" type="button" :disabled="sending" @click="send(q.prompt)">{{ q.label }}</button>
          </div>
        </div>
      </template>

      <div v-if="sending" class="row assistant">
        <div class="mini-avatar" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none"><rect x="4.5" y="6.5" width="15" height="12" rx="3.4" stroke="currentColor" stroke-width="1.6" /><circle cx="9.4" cy="12.2" r="1.25" fill="currentColor" /><circle cx="14.6" cy="12.2" r="1.25" fill="currentColor" /></svg>
        </div>
        <div class="bubble-wrap"><div class="bubble typing"><span class="spin" aria-hidden="true"></span>正在想…</div></div>
      </div>
    </div>

    <footer class="afoot">
      <div class="composer">
        <textarea
          v-model="input"
          rows="1"
          maxlength="2000"
          placeholder="和你的想要什么？例如：生成一个角色、场景、分镜"
          aria-label="给 AI 创作助手发消息"
          @keydown="onKey"
        ></textarea>
        <button class="send" type="button" :disabled="sending || !input.trim()" title="发送" aria-label="发送" @click="send()">
          <span v-if="sending" class="spin" aria-hidden="true"></span>
          <svg v-else viewBox="0 0 20 20" fill="none"><path d="M3.4 10 16.6 3.8 13.9 16.2 10 11.9 3.4 10Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" /><path d="M10 11.9 16.6 3.8" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" /></svg>
        </button>
      </div>
      <p class="tip">Enter 发送 · Shift + Enter 换行</p>
    </footer>
  </aside>
</template>

<style scoped>
.assistant { display: flex; flex-direction: column; min-height: 0; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); overflow: hidden; }
.ahead { flex: none; display: flex; align-items: center; gap: 10px; padding: 13px 15px; border-bottom: 1px solid var(--line-soft); }
.avatar { flex: none; width: 32px; height: 32px; display: grid; place-items: center; border-radius: 9px; background: var(--accent); color: #fff; }
.avatar svg { width: 19px; height: 19px; }
.atitle { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.atitle strong { font-size: 13px; font-weight: 600; }
.status { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; color: var(--mint); }
.status i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.status.off { color: var(--warn); }
.icon-btn { flex: none; width: 28px; height: 28px; display: grid; place-items: center; padding: 0; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--fg-3); cursor: pointer; }
.icon-btn:hover:not(:disabled) { background: var(--surface-2); border-color: transparent; color: var(--fg-2); }
.icon-btn svg { width: 15px; height: 15px; }

.athread { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 15px 6px; display: flex; flex-direction: column; gap: 13px; scrollbar-width: thin; }
.row { display: flex; gap: 8px; align-items: flex-start; }
.row.user { justify-content: flex-end; }
.mini-avatar { flex: none; width: 24px; height: 24px; display: grid; place-items: center; border-radius: 7px; background: var(--accent-dim); color: var(--accent); }
.mini-avatar svg { width: 15px; height: 15px; }
.bubble-wrap { min-width: 0; max-width: 100%; display: flex; flex-direction: column; gap: 6px; }
.row.user .bubble-wrap { align-items: flex-end; max-width: 86%; }
.bubble { padding: 9px 12px; border-radius: 11px; background: var(--surface-2); color: var(--fg); font-size: 12.5px; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.bubble.bad { background: var(--danger-dim); color: var(--danger); }
.row.user .bubble { background: var(--accent); color: var(--accent-ink); border-bottom-right-radius: 3px; }
.row.assistant .bubble { border-bottom-left-radius: 3px; }
.bubble.typing { display: inline-flex; align-items: center; gap: 7px; color: var(--fg-2); }
.note { display: flex; gap: 6px; align-items: flex-start; margin: 0; font-size: 11px; color: var(--fg-3); line-height: 1.7; }
.note svg { width: 13px; height: 13px; flex: none; margin-top: 2px; }
.stamp { font-size: 10px; color: var(--fg-3); }
.quick { display: flex; flex-direction: column; gap: 6px; }
.quick-label { margin: 0; font-size: 11.5px; color: var(--fg-2); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 5px 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface); color: var(--fg-2); font-size: 11px; cursor: pointer; }
.chip:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.chip:disabled { opacity: .5; cursor: not-allowed; }

.afoot { flex: none; padding: 10px 13px 12px; border-top: 1px solid var(--line-soft); }
.composer { display: flex; align-items: flex-end; gap: 8px; padding: 7px 7px 7px 12px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-2); }
.composer:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
.composer textarea { flex: 1; min-height: 22px; max-height: 96px; padding: 4px 0; border: 0; background: transparent; font-size: 12.5px; line-height: 1.6; resize: none; }
.composer textarea:focus { outline: none; box-shadow: none; border-color: transparent; }
.composer textarea:hover { border-color: transparent; }
.send { flex: none; width: 30px; height: 30px; display: grid; place-items: center; padding: 0; border: 1px solid var(--accent); border-radius: 9px; background: var(--accent); color: var(--accent-ink); cursor: pointer; }
.send:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
.send:disabled { opacity: .45; cursor: not-allowed; }
.send svg { width: 16px; height: 16px; }
.tip { margin: 6px 2px 0; font-size: 10px; color: var(--fg-3); }
</style>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { api } from '../api';
import WorkflowIcon from './WorkflowIcon.vue';
import { createMuseInputQueue, museFrameInterval } from '../musePanel';

const emit = defineEmits(['close', 'connected', 'status']);
const frame = ref(null);
const screen = ref(null);
const keyboard = ref(null);
const error = ref('');
const frameError = ref('');
const loading = ref(true);
const working = ref(false);
let disposed = false,
  reading = false,
  timer,
  composing = false,
  activeUntil = 0,
  lastFrameAt = 0;
let latestRead = Promise.resolve(),
  opener;
const host = computed(() => {
  try {
    return new URL(frame.value?.url).hostname;
  } catch {
    return 'muse.ai';
  }
});
const canInteract = computed(() => !!frame.value && !frame.value.busy && !working.value);

const inputs = createMuseInputQueue(
  async (input) => {
    if (disposed) return;
    if (frame.value?.busy) throw new Error('视频正在生成，暂时不能操作 Muse 聊天');
    await api.museInput({ ...input, frame_id: frame.value.frame_id });
    if (!disposed)
      emit('status', {
        connected: false,
        browser_open: true,
        background: frame.value.background,
        message: '选择聊天后，点击使用当前聊天',
      });
  },
  {
    onError: (err) => {
      error.value = err.message;
    },
    afterInput: async () => {
      // Show progress during sustained typing instead of waiting for every
      // queued key to finish. Only one screenshot can be in flight at a time.
      if (Date.now() - lastFrameAt >= 160) await refresh(true);
    },
    onIdle: () => scheduleRefresh(),
  }
);

function scheduleRefresh() {
  clearTimeout(timer);
  if (disposed || working.value || document.hidden) return;
  const interval = museFrameInterval({
    active: Date.now() < activeUntil,
    busy: frame.value?.busy,
    failed: !!frameError.value,
  });
  timer = setTimeout(
    () => {
      if (inputs.pending) scheduleRefresh();
      else void refresh();
    },
    Math.max(40, interval - (Date.now() - lastFrameAt))
  );
}

async function refresh(duringInput = false) {
  if (disposed || reading || (!duringInput && inputs.pending) || working.value || document.hidden) return;
  clearTimeout(timer);
  reading = true;
  try {
    const request = api.museFrame();
    latestRead = request.catch(() => {});
    const result = await request;
    if (!disposed) {
      frame.value = result;
      frameError.value = '';
    }
  } catch (err) {
    if (!disposed) frameError.value = err.message;
  } finally {
    reading = false;
    loading.value = false;
    lastFrameAt = Date.now();
    scheduleRefresh();
  }
}
function send(input) {
  if (!canInteract.value || disposed) return;
  error.value = '';
  activeUntil = Date.now() + 1800;
  inputs.enqueue(input);
}
function point(event) {
  const rect = screen.value.getBoundingClientRect();
  return {
    x: Math.min(
      frame.value.width - 1,
      Math.max(0, ((event.clientX - rect.left) / rect.width) * frame.value.width)
    ),
    y: Math.min(
      frame.value.height - 1,
      Math.max(0, ((event.clientY - rect.top) / rect.height) * frame.value.height)
    ),
  };
}
function click(event) {
  if (!canInteract.value) return;
  keyboard.value?.focus({ preventScroll: true });
  send({ kind: 'click', ...point(event) });
}
function wheel(event) {
  if (!canInteract.value) return;
  const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? frame.value.height : 1;
  send({
    kind: 'wheel',
    ...point(event),
    dx: Math.max(-2000, Math.min(2000, event.deltaX * scale)),
    dy: Math.max(-2000, Math.min(2000, event.deltaY * scale)),
  });
}
function keydown(event) {
  event.stopPropagation();
  if (event.isComposing || composing) return;
  // Let the paste event carry the clipboard text, including Chinese text.
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') return;
  event.preventDefault();
  if (!canInteract.value) return;
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    send({ kind: 'text', text: event.key });
    return;
  }
  if (
    ![
      'Enter',
      'Tab',
      'Escape',
      'Backspace',
      'Delete',
      'ArrowLeft',
      'ArrowRight',
      'ArrowUp',
      'ArrowDown',
      'Home',
      'End',
      'PageUp',
      'PageDown',
    ].includes(event.key) &&
    !/^[a-z]$/i.test(event.key)
  )
    return;
  const modifiers = [
    event.ctrlKey && 'Control',
    event.metaKey && 'Meta',
    event.altKey && 'Alt',
    event.shiftKey && 'Shift',
  ].filter(Boolean);
  send({
    kind: 'key',
    key: [...modifiers, event.key.length === 1 ? event.key.toLowerCase() : event.key].join('+'),
  });
}
function paste(event) {
  event.preventDefault();
  const text = event.clipboardData?.getData('text/plain');
  if (text) send({ kind: 'text', text });
}
function compositionEnd(event) {
  composing = false;
  if (event.data) send({ kind: 'text', text: event.data });
  if (keyboard.value) keyboard.value.value = '';
}
async function connect() {
  working.value = true;
  error.value = '';
  try {
    await inputs.flush();
    await latestRead;
    const status = await api.connectMuse();
    if (!disposed) {
      emit('connected', status);
      emit('close');
    }
  } catch (err) {
    if (!disposed) error.value = err.message;
  } finally {
    working.value = false;
    scheduleRefresh();
  }
}
async function officialWindow() {
  working.value = true;
  error.value = '';
  try {
    await inputs.flush();
    await latestRead;
    const status = await api.openMuseWindow();
    if (!disposed) {
      frame.value = null;
      emit('status', status);
    }
  } catch (err) {
    if (!disposed) error.value = err.message;
  } finally {
    working.value = false;
    void refresh();
  }
}
async function background() {
  working.value = true;
  error.value = '';
  try {
    await inputs.flush();
    await latestRead;
    const status = await api.openMuse();
    emit('status', status);
    frame.value = null;
    await resize();
  } catch (err) {
    if (!disposed) error.value = err.message;
  } finally {
    working.value = false;
    void refresh();
  }
}
async function resize() {
  await api.museResize({
    width: Math.max(360, Math.min(1280, window.innerWidth - 64)),
    height: Math.max(300, Math.min(850, window.innerHeight - 188)),
  });
}
function close() {
  if (!working.value) emit('close');
}
function visibilityChanged() {
  clearTimeout(timer);
  if (!document.hidden) void refresh();
}
onMounted(async () => {
  opener = document.activeElement;
  document.addEventListener('visibilitychange', visibilityChanged);
  try {
    const status = await api.museStatus();
    if (status.background && !status.busy) await resize();
  } catch (err) {
    error.value = err.message;
  }
  if (disposed) return;
  await refresh();
  await nextTick();
  keyboard.value?.focus({ preventScroll: true });
});
onBeforeUnmount(() => {
  disposed = true;
  clearTimeout(timer);
  inputs.dispose();
  document.removeEventListener('visibilitychange', visibilityChanged);
  opener?.focus?.();
});
</script>

<template>
  <Teleport to="body">
    <section
      class="muse-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="muse-panel-title"
      @keydown.stop
    >
      <header class="muse-panel-header">
        <div>
          <h2 id="muse-panel-title">Muse</h2>
          <span class="muse-host">{{ host }}</span>
        </div>
        <div class="muse-panel-actions">
          <span class="muse-background">{{
            frame?.background === false ? '官方登录窗口已打开' : '后台运行'
          }}</span>
          <button v-if="frame?.background === false" :disabled="working || frame?.busy" @click="background">
            返回项目面板
          </button>
          <button
            v-else
            class="muse-connect"
            :disabled="loading || working || frame?.busy || !frame"
            @click="connect"
          >
            {{ working ? '处理中…' : '使用当前聊天' }}
          </button>
          <button :disabled="working" aria-label="收起 Muse 面板" @click="close">
            <WorkflowIcon name="close" />
          </button>
        </div>
      </header>
      <p class="muse-panel-hint">
        在下方直接登录 Muse，选择或新建一个旁聊，再点击「使用当前聊天」。收起面板后继续在画布生成视频。
      </p>
      <div class="muse-screen-wrap">
        <div
          v-if="frame"
          ref="screen"
          class="muse-screen"
          :class="{ locked: !canInteract }"
          @click="click"
          @wheel.prevent="wheel"
        >
          <img
            :src="`data:image/jpeg;base64,${frame.image}`"
            :width="frame.width"
            :height="frame.height"
            alt="Muse 官方页面"
            draggable="false"
          />
        </div>
        <p v-else class="muse-placeholder">{{ loading ? '正在载入 Muse…' : 'Muse 页面暂时不可用' }}</p>
        <textarea
          ref="keyboard"
          class="muse-keyboard"
          aria-label="Muse 页面键盘输入"
          autocomplete="off"
          autocorrect="off"
          spellcheck="false"
          @keydown="keydown"
          @paste="paste"
          @compositionstart="composing = true"
          @compositionend="compositionEnd"
        />
      </div>
      <footer class="muse-panel-footer">
        <span role="status" :class="{ 'muse-error': error || frameError }">{{
          error ||
          frameError ||
          (frame?.busy ? '视频正在生成，暂时不能切换聊天' : '点击页面即可操作；支持键盘输入、粘贴和滚动。')
        }}</span>
        <div>
          <button :disabled="working || reading" @click="refresh()">刷新页面预览</button
          ><button :disabled="working || frame?.busy" @click="officialWindow">
            登录遇到问题？使用官方窗口
          </button>
        </div>
      </footer>
    </section>
  </Teleport>
</template>

<style scoped>
.muse-panel {
  position: fixed;
  inset: 20px;
  z-index: 180;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 18px 80px #0007;
  overflow: hidden;
}
.muse-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
}
.muse-panel-header > div {
  display: flex;
  align-items: center;
  gap: 12px;
}
h2 {
  font-size: 18px;
  margin: 0;
}
.muse-host,
.muse-background {
  font-size: 12px;
  color: var(--fg-2);
}
.muse-background {
  color: var(--mint);
}
button {
  padding: 8px 12px;
  background: var(--surface);
  color: var(--fg);
  border: 1px solid var(--line);
  border-radius: 7px;
  font-size: 12px;
}
button svg {
  display: block;
  width: 16px;
  height: 16px;
}
.muse-connect {
  background: var(--accent);
  color: var(--accent-ink);
}
.muse-panel-hint {
  margin: 0;
  padding: 10px 20px;
  font-size: 12px;
  color: var(--fg-2);
  line-height: 1.7;
}
.muse-screen-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: grid;
  place-items: center;
  background: var(--surface);
}
.muse-screen {
  width: min(100%, 1280px);
  line-height: 0;
  cursor: default;
  touch-action: none;
}
.muse-screen img {
  display: block;
  width: 100%;
  height: auto;
  user-select: none;
}
.muse-screen.locked {
  pointer-events: none;
}
.muse-placeholder {
  padding: 24px;
  color: var(--fg-2);
  font-size: 14px;
}
.muse-keyboard {
  position: absolute;
  top: 0;
  left: 0;
  width: 1px;
  height: 1px;
  opacity: 0;
  padding: 0;
  border: 0;
  resize: none;
}
.muse-panel-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  padding: 10px 20px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  color: var(--fg-2);
}
.muse-panel-footer > div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.muse-error {
  color: var(--danger);
}
@media (max-width: 760px) {
  .muse-panel {
    inset: 8px;
  }
  .muse-panel-header {
    padding: 12px;
  }
  .muse-panel-header > div {
    gap: 8px;
  }
  .muse-host {
    display: none;
  }
  .muse-panel-hint,
  .muse-panel-footer {
    padding: 10px 12px;
  }
  .muse-panel-actions button {
    padding: 8px;
  }
}
@media (max-width: 480px) {
  .muse-background {
    display: none;
  }
}
</style>

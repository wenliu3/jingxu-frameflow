<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

// 全屏看图。分镜图在表格里只有 168px 宽，判断构图、人脸、手部崩没崩必须放大看。
const props = defineProps({
  items: { type: Array, default: () => [] },  // [{ shotId, url, camera, motion, duration, dialogue }]
  index: { type: Number, default: 0 },
})

const emit = defineEmits(['close', 'change'])

const current = computed(() => props.items[props.index] || null)

function step(delta) {
  const next = props.index + delta
  if (next < 0 || next >= props.items.length) return
  emit('change', next)
}

const dialog = ref(null)
const closeButton = ref(null)
let previousFocus = null

function onKey(event) {
  if (event.key === 'Tab') {
    const buttons = dialog.value?.querySelectorAll('button:not(:disabled)')
    if (!buttons?.length) return
    const first = buttons[0]
    const last = buttons[buttons.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    } else if (!dialog.value.contains(document.activeElement)) {
      event.preventDefault()
      first.focus()
    }
    return
  }
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    step(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    step(1)
  }
}

onMounted(() => {
  previousFocus = document.activeElement
  closeButton.value?.focus({ preventScroll: true })
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
})
</script>

<template>
  <div ref="dialog" class="mask" role="dialog" aria-modal="true" aria-label="分镜图片预览" @click.self="emit('close')">
    <button ref="closeButton" type="button" class="close" aria-label="关闭图片预览（Esc）" @click="emit('close')" title="关闭（Esc）">×</button>

    <button
      type="button"
      class="nav prev"
      :disabled="index <= 0"
      aria-label="上一镜（左方向键）"
      @click.stop="step(-1)"
      title="上一镜（←）"
    >‹</button>

    <figure v-if="current" class="stage" @click.stop>
      <img :src="current.url" :alt="`第 ${current.shotId} 镜`" />
      <figcaption aria-live="polite" aria-atomic="true">
        <span class="id mono">#{{ String(current.shotId).padStart(2, '0') }}</span>
        <span class="tag">{{ current.camera || '—' }}</span>
        <span class="tag">{{ current.motion || '—' }}</span>
        <span class="tag mono">{{ current.duration }}s</span>
        <span v-if="current.dialogue" class="dia">{{ current.dialogue }}</span>
        <span class="pos mono">{{ index + 1 }} / {{ items.length }}</span>
      </figcaption>
    </figure>

    <button
      type="button"
      class="nav next"
      :disabled="index >= items.length - 1"
      aria-label="下一镜（右方向键）"
      @click.stop="step(1)"
      title="下一镜（→）"
    >›</button>
  </div>
</template>

<style scoped>
.mask {
  /* 灯箱是刻意保留的深色画面区，不继承浅色工作区的深色文字。 */
  --lightbox-fg: #f6f5f1;
  --lightbox-muted: #c6cbc2;
  --lightbox-line: rgba(246, 245, 241, 0.24);
  --lightbox-surface: rgba(246, 245, 241, 0.1);
  box-sizing: border-box;
  position: fixed;
  inset: 0;
  z-index: 60;
  color: var(--lightbox-fg);
  background: rgba(20, 24, 21, 0.96);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 72px 88px;
  overflow-y: auto;
  overscroll-behavior: contain;
  animation: fade 0.18s var(--ease) both;
}
.stage { margin: 0; max-width: 100%; max-height: 100%; min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 16px; }
.stage img { max-width: 100%; max-height: calc(100vh - 210px); max-height: calc(100dvh - 210px); min-height: 0; object-fit: contain; border-radius: var(--r-sm); border: 1px solid var(--lightbox-line); display: block; }
figcaption { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: center; flex-shrink: 0; line-height: 1.6; }
.id { color: var(--lightbox-fg); font-size: 13px; margin-right: 4px; }
.tag { color: var(--lightbox-fg); background: var(--lightbox-surface); border: 1px solid var(--lightbox-line); white-space: normal; overflow-wrap: anywhere; }
.dia { font-size: 13px; color: var(--lightbox-muted); overflow-wrap: anywhere; }
.pos { margin-left: 8px; color: var(--lightbox-muted); font-size: 12px; white-space: nowrap; }
.nav, .close { position: absolute; display: flex; align-items: center; justify-content: center; padding: 0; line-height: 1; color: var(--lightbox-fg); background: var(--lightbox-surface); border: 1px solid var(--lightbox-line); }
.nav { top: 50%; transform: translateY(-50%); width: 48px; height: 64px; font-size: 30px; border-radius: var(--r-sm); }
.nav:hover:not(:disabled), .close:hover { background: rgba(246, 245, 241, 0.2); color: var(--lightbox-fg); border-color: var(--lightbox-muted); }
.nav:disabled { opacity: 1; color: #92998f; background: rgba(246, 245, 241, 0.04); cursor: not-allowed; }
.nav:focus-visible, .close:focus-visible { outline: 2px solid var(--lightbox-fg); outline-offset: 4px; }
.prev { left: 20px; }
.next { right: 20px; }
.close { top: 16px; right: 20px; width: 44px; height: 44px; font-size: 26px; border-radius: 50%; }

@media (max-width: 600px) {
  .mask { padding: 72px 16px 96px; }
  .stage img { max-height: calc(100vh - 270px); max-height: calc(100dvh - 270px); }
  .nav { top: auto; bottom: max(20px, env(safe-area-inset-bottom)); transform: none; width: 56px; height: 44px; }
  .close { right: 16px; }
  .dia { flex-basis: 100%; text-align: center; }
}
@media (prefers-reduced-motion: reduce) { .mask { animation: none; } }
</style>

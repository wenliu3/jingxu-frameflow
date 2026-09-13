<script setup>
// 轻量提示条。用于「重出图完成」「任务已失效」这类一次性反馈。
defineProps({
  toasts: { type: Array, default: () => [] },
})

const emit = defineEmits(['dismiss'])

const ICON = { error: '!', ok: '✓', info: 'i' }
</script>

<template>
  <div class="stack" role="region" aria-label="操作通知" aria-live="polite" aria-relevant="additions text">
    <TransitionGroup name="toast">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type" aria-atomic="true">
        <span class="ico" aria-hidden="true">{{ ICON[t.type] || 'i' }}</span>
        <span class="text">{{ t.text }}</span>
        <button type="button" class="x" :aria-label="`关闭通知：${t.text}`" @click="emit('dismiss', t.id)">×</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.stack {
  position: fixed;
  right: 20px;
  bottom: max(20px, env(safe-area-inset-bottom));
  z-index: 80;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(380px, calc(100vw - 40px));
  max-height: calc(100dvh - 40px);
  overflow-y: auto;
  padding: 4px;
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px;
  border-radius: var(--r-sm);
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: 0 6px 20px rgba(38, 41, 37, 0.08);
  font-size: 13px;
  line-height: 1.7;
  pointer-events: auto;
}
.ico { width: 22px; height: 22px; flex: none; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; margin-top: 3px; background: var(--surface-2); color: var(--fg-2); }
.text { flex: 1; min-width: 0; padding-top: 2px; overflow-wrap: anywhere; color: var(--fg); }
.x { width: 32px; height: 32px; min-height: 0; border: 1px solid transparent; border-radius: var(--r-xs); background: transparent; color: var(--fg-2); font-size: 20px; line-height: 1; padding: 0; flex: none; display: flex; align-items: center; justify-content: center; }
.x:hover { color: var(--fg); background: var(--surface-2); border-color: var(--line); }
.x:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.toast.error { border-left: 3px solid #b53d3d; }
.toast.error .ico { background: #b53d3d; color: #fff; }
.toast.ok { border-left: 3px solid var(--mint); }
.toast.ok .ico { background: var(--mint); color: #fff; }
.toast.info { border-left: 3px solid var(--accent); }
.toast-enter-active, .toast-leave-active { transition: opacity 0.22s var(--ease), transform 0.22s var(--ease); }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(12px); }

@media (max-width: 480px) {
  .stack { right: 12px; width: calc(100vw - 24px); bottom: max(12px, env(safe-area-inset-bottom)); }
}
@media (prefers-reduced-motion: reduce) {
  .toast-enter-active, .toast-leave-active { transition: none; }
}
</style>

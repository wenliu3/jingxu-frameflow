<script setup>
import { nextTick, ref } from 'vue'

// 数据操作由 App 承接，侧栏只维护重命名草稿与导航交互。
defineProps({
  history: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  activeView: { type: String, default: 'create' },
  historyError: { type: String, default: '' },
})

const emit = defineEmits(['select', 'create', 'delete', 'rename', 'library', 'configure', 'retry'])
const renamingId = ref('')
const draft = ref('')

function startRename(item) {
  renamingId.value = item.task_id
  draft.value = item.title || item.idea || ''
}

function commitRename(item) {
  if (renamingId.value !== item.task_id) return
  const title = draft.value.trim()
  renamingId.value = ''
  if (title && title !== (item.title || item.idea)) {
    emit('rename', { taskId: item.task_id, title })
  }
}

function onRenameKey(event, item) {
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'Enter') {
    event.preventDefault()
    commitRename(item)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    renamingId.value = ''
  }
}

function focusRename(el) {
  if (!el) return
  nextTick(() => {
    if (document.activeElement !== el) {
      el.focus()
      el.select()
    }
  })
}

function relTime(iso) {
  if (!iso) return ''
  const timestamp = new Date(iso).getTime()
  if (!Number.isFinite(timestamp)) return ''
  const min = Math.floor(Math.max(0, Date.now() - timestamp) / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  return `${Math.floor(hour / 24)} 天前`
}

const statusLabels = { running: '创作中', succeeded: '已完成', failed: '生成失败' }
</script>

<template>
  <aside class="rail" aria-label="镜序工作室导航">
    <div class="brand">
      <svg class="brand-mark" viewBox="0 0 36 36" fill="none" aria-hidden="true">
        <path d="M4 13V5h8M24 5h8v8M32 24v8h-8M12 32H4v-8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
        <rect x="10" y="10" width="13" height="13" rx="2" stroke="currentColor" stroke-width="1.5" opacity=".5" />
        <rect x="15" y="15" width="13" height="13" rx="2" fill="var(--rail-bg)" stroke="currentColor" stroke-width="1.5" />
      </svg>
      <div class="brand-names">
        <strong>镜序</strong>
        <span>FRAMEFLOW</span>
      </div>
    </div>

    <button class="new-project" type="button" title="新建作品" aria-label="新建作品" @click="emit('create')">
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 4v12M4 10h12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>
      <span>新建作品</span>
    </button>

    <nav class="navigation" aria-label="主导航">
      <button class="nav-item" :class="{ active: activeView === 'create' }" :aria-current="activeView === 'create' ? 'page' : undefined" type="button" @click="emit('create')">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 8V4a1 1 0 0 1 1-1h4M12 3h4a1 1 0 0 1 1 1v4M17 12v4a1 1 0 0 1-1 1h-4M8 17H4a1 1 0 0 1-1-1v-4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /><path d="m8 7 5 3-5 3V7Z" fill="currentColor" /></svg>
        <span>创作工作室</span>
      </button>
      <button class="nav-item" :class="{ active: activeView === 'library' }" :aria-current="activeView === 'library' ? 'page' : undefined" type="button" @click="emit('library')">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="3" y="3" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.4" /><rect x="11.5" y="3" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.4" /><rect x="3" y="11.5" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.4" /><rect x="11.5" y="11.5" width="5.5" height="5.5" rx="1.2" stroke="currentColor" stroke-width="1.4" /></svg>
        <span>我的作品</span>
      </button>
    </nav>

    <section class="history" aria-label="近期作品">
      <div class="section-heading">
        <span>近期作品</span>
        <span v-if="history.length" class="history-count">{{ history.length }}</span>
      </div>
      <div class="history-list">
        <div v-if="historyError" class="history-error" role="alert">
          <p>{{ historyError }}</p>
          <button class="retry" type="button" @click="emit('retry')">
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M16 8a6 6 0 1 0-.5 5M16 3v5h-5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
            重新加载
          </button>
        </div>
        <p v-if="!history.length && !historyError" class="empty-history">故事从一个念头开始。<br />你的作品会出现在这里。</p>
        <div v-for="item in history" :key="item.task_id" class="history-row" :class="{ selected: item.task_id === activeId }">
          <div v-if="renamingId === item.task_id" class="rename-wrap">
            <input :ref="focusRename" v-model="draft" class="rename-input" aria-label="作品新名称" placeholder="输入作品名称" @keydown="onRenameKey($event, item)" @blur="commitRename(item)" />
          </div>
          <template v-else>
            <button class="history-item" type="button" :title="item.title || item.idea || '未命名作品'" :aria-current="item.task_id === activeId ? 'true' : undefined" @click="emit('select', item.task_id)">
              <span class="status-dot" :class="item.status" aria-hidden="true"></span>
              <span class="history-body">
                <span class="history-title">{{ item.title || item.idea || '未命名作品' }}</span>
                <span class="history-meta">
                  <span v-if="statusLabels[item.status]" class="sr-only">{{ statusLabels[item.status] }}，</span>
                  {{ item.shots ? item.shots + ' 镜' : '' }}{{ item.shots && relTime(item.created_at) ? ' · ' : '' }}{{ relTime(item.created_at) }}
                </span>
              </span>
            </button>
            <div class="history-actions">
              <button class="history-action" type="button" title="重命名作品" :aria-label="`重命名「${item.title || item.idea || '未命名作品'}」`" @click.stop="startRename(item)">
                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m12.5 4 3.5 3.5M4 12l9.5-9.5 4 4L8 16l-5 1 1-5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" /></svg>
              </button>
              <button class="history-action delete-action" type="button" title="删除作品（移入回收站）" :aria-label="`删除「${item.title || item.idea || '未命名作品'}」（移入回收站）`" @click.stop="emit('delete', item.task_id)">
                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 5.5h14M7 5.5V3h6v2.5M5 5.5l.8 11h8.4l.8-11M8 9v4.5M12 9v4.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </button>
            </div>
          </template>
        </div>
      </div>
    </section>

    <footer class="rail-footer">
      <button class="nav-item settings-entry" :class="{ active: activeView === 'configure' }" type="button" title="服务设置" aria-label="服务设置" @click="emit('configure')">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 3v14M10 3v14M16 3v14" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /><path d="M2 7h4M8 13h4M14 7h4" stroke="var(--rail-bg)" stroke-width="5" /><path d="M2 7h4M8 13h4M14 7h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
        <span>服务设置</span>
      </button>
      <div class="local-note">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.5 16 5v4.5c0 3.5-3.2 6.6-6 8-2.8-1.4-6-4.5-6-8V5l6-2.5Z" stroke="currentColor" stroke-width="1.3" /><path d="m7 9.5 2 2 4-4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
        <p>作品保存在本地 outputs/<br /><span>重启服务后仍可继续创作</span></p>
      </div>
    </footer>
  </aside>
</template>

<style scoped>
.rail {
  --rail-bg: #202621;
  --rail-surface: #2b332d;
  --rail-fg: #f3f3ec;
  --rail-muted: #a1aa9e;
  --rail-line: #394139;
  --rail-accent: #dc603e;
  --rail-mint: #80b499;
  --rail-danger: #f29a89;
  /* 原生控件、选区也使用侧栏自己的深色主题。 */
  --bg: var(--rail-bg);
  --surface: var(--rail-bg);
  --surface-2: var(--rail-surface);
  --surface-3: var(--rail-surface);
  --fg: var(--rail-fg);
  --fg-2: var(--rail-muted);
  --fg-3: var(--rail-muted);
  --line: var(--rail-line);
  --accent: var(--rail-accent);
  --accent-dim: #43352b;
  box-sizing: border-box;
  color-scheme: dark;
  width: 232px;
  flex: 0 0 232px;
  height: 100vh;
  height: 100dvh;
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  padding: 0 14px;
  background: var(--rail-bg);
  color: var(--rail-fg);
  border-right: 1px solid var(--rail-line);
}
.rail button { font: inherit; cursor: pointer; box-shadow: none; }
.rail button:focus-visible, .rename-input:focus-visible { outline: 2px solid var(--rail-accent); outline-offset: 3px; }
.rail svg { width: 18px; height: 18px; flex: none; }
.brand { display: flex; align-items: center; gap: 12px; padding: 31px 12px 30px; }
.brand .brand-mark { width: 36px; height: 36px; color: var(--rail-accent); }
.brand-names { display: flex; flex-direction: column; gap: 3px; }
.brand-names strong { font-size: 23px; font-weight: 600; line-height: 1.25; letter-spacing: 4px; color: var(--rail-fg); }
.brand-names span { font-size: 9px; letter-spacing: 2.2px; color: var(--rail-muted); }
.rail .new-project { display: flex; align-items: center; justify-content: center; gap: 9px; min-height: 43px; padding: 10px 14px; border: 1px solid var(--rail-accent); border-radius: 9px; background: var(--rail-accent); color: #fff; font-size: 13px; font-weight: 600; }
.rail .new-project:hover { background: #c95233; border-color: #c95233; }
.navigation { display: flex; flex-direction: column; gap: 5px; margin: 24px 0 27px; }
.rail .nav-item { width: 100%; display: flex; align-items: center; gap: 11px; padding: 11px 12px; min-height: 43px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--rail-muted); text-align: left; font-size: 13px; white-space: nowrap; }
.rail .nav-item:hover { background: var(--rail-surface); color: var(--rail-fg); border-color: transparent; }
.rail .nav-item.active { background: var(--rail-surface); color: var(--rail-fg); }
.nav-item.active svg { color: var(--rail-accent); }
.history { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.section-heading { display: flex; justify-content: space-between; align-items: center; padding: 0 12px 12px; color: var(--rail-muted); font-size: 11px; letter-spacing: .5px; }
.history-count { font-size: 10px; font-variant-numeric: tabular-nums; opacity: .75; }
.history-list { flex: 1; overflow-y: auto; padding: 3px 0 16px; scrollbar-width: thin; scrollbar-color: var(--rail-line) transparent; }
.empty-history { margin: 6px 12px; color: var(--rail-muted); font-size: 12px; line-height: 1.9; }
.history-row { position: relative; display: flex; align-items: center; margin-bottom: 5px; border-radius: 8px; }
.history-row:hover, .history-row:focus-within, .history-row.selected { background: var(--rail-surface); }
.rail .history-item { flex: 1; min-width: 0; display: flex; align-items: flex-start; gap: 8px; padding: 11px 5px 11px 10px; border: 1px solid transparent; background: transparent; border-radius: 8px; text-align: left; color: var(--rail-fg); }
.rail .history-item:hover { background: transparent; border-color: transparent; }
.history-body { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.history-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; line-height: 1.5; }
.history-meta { color: var(--rail-muted); font-size: 10px; line-height: 1.5; }
.status-dot { width: 5px; height: 5px; flex: none; margin-top: 7px; border-radius: 50%; background: var(--rail-muted); }
.status-dot.running { background: var(--rail-accent); }
.status-dot.succeeded { background: var(--rail-mint); }
.status-dot.failed { background: var(--rail-danger); }
.history-actions { display: flex; gap: 1px; flex: none; padding-right: 4px; opacity: .65; }
.history-row:hover .history-actions, .history-row:focus-within .history-actions { opacity: 1; }
.rail .history-action { width: 26px; height: 30px; display: grid; place-items: center; padding: 0; border: 1px solid transparent; border-radius: 5px; color: var(--rail-muted); background: transparent; }
.history-action svg { width: 13px; height: 13px; }
.rail .history-action:hover { color: var(--rail-fg); background: var(--rail-bg); border-color: var(--rail-line); }
.rail .delete-action:hover { color: var(--rail-danger); }
.rename-wrap { width: 100%; padding: 8px; }
.rename-input { box-sizing: border-box; width: 100%; padding: 7px; font-size: 12px; color: var(--rail-fg); background: var(--rail-bg); border: 1px solid var(--rail-line); border-radius: 5px; }
.rename-input:focus { background: var(--rail-bg); border-color: var(--rail-accent); box-shadow: none; }
.history-error { margin: 0 6px 12px; padding: 10px; border: 1px solid var(--rail-line); border-radius: 8px; }
.history-error p { margin: 0 0 8px; font-size: 12px; color: var(--rail-danger); overflow-wrap: anywhere; }
.rail .retry { display: inline-flex; gap: 5px; align-items: center; padding: 5px 7px; font-size: 11px; border: 1px solid var(--rail-line); border-radius: 5px; background: var(--rail-surface); color: var(--rail-fg); }
.rail .retry:hover { background: var(--rail-bg); border-color: var(--rail-muted); }
.retry svg { width: 13px; height: 13px; }
.rail-footer { flex: none; padding: 14px 0 21px; border-top: 1px solid var(--rail-line); }
.local-note { display: flex; align-items: flex-start; gap: 8px; padding: 17px 11px 0; color: var(--rail-muted); }
.local-note svg { width: 14px; height: 14px; margin-top: 2px; }
.local-note p { margin: 0; font-size: 10px; line-height: 1.8; }
.local-note p span { opacity: .8; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
@media (hover: none), (pointer: coarse) {
  .history-actions { opacity: 1; }
  .rail .history-action { width: 30px; height: 36px; }
}
@media (max-width: 760px) {
  .rail { position: relative; width: 100%; flex: 0 0 auto; height: auto; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px 12px; padding: 15px 18px 12px; border-right: 0; border-bottom: 1px solid var(--rail-line); }
  .brand { padding: 0; gap: 9px; }
  .brand .brand-mark { width: 30px; height: 30px; }
  .brand-names strong { font-size: 19px; }
  .brand-names span { font-size: 8px; letter-spacing: 1.7px; }
  .rail .new-project { min-height: 40px; padding: 8px 13px; }
  .navigation { flex-direction: row; gap: 4px; margin: 0; }
  .rail .nav-item { width: auto; min-height: 40px; gap: 7px; padding: 8px 9px; font-size: 12px; }
  .history, .local-note { display: none; }
  .rail-footer { padding: 0; border: 0; justify-self: end; align-self: center; }
}
@media (max-width: 360px) {
  .rail { padding-inline: 12px; column-gap: 6px; }
  .rail .nav-item { padding-inline: 7px; gap: 5px; }
  .nav-item svg { width: 15px; height: 15px; }
  .navigation .nav-item:first-child svg { display: none; }
}
</style>

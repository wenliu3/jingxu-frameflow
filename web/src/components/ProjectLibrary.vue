<script setup>
import { ref } from 'vue';
import WorkflowIcon from './WorkflowIcon.vue';
import { projectState } from '../projectLibrary';
defineProps({ items: Array, total: Number, loading: Boolean, error: String, search: String, filter: String });
defineEmits(['open', 'create', 'trash', 'delete', 'retry', 'update:search', 'update:filter']);
const failedCovers = ref(new Set());
const options = [
  { key: 'all', label: '全部作品' },
  { key: 'draft', label: '准备中' },
  { key: 'succeeded', label: '已有视频' },
  { key: 'running', label: '生成中' },
  { key: 'failed', label: '需处理' },
];
function date(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? '日期未知'
    : d.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' });
}
function failCover(url) {
  failedCovers.value = new Set([...failedCovers.value, url]);
}
</script>

<template>
  <section class="project-library" aria-labelledby="library-title">
    <header class="collection-heading">
      <div>
        <p class="collection-eyebrow">
          创作空间 <span>{{ total }} 个作品</span>
        </p>
        <h1 id="library-title">我的作品</h1>
        <p>找到你的故事，继续下一镜。</p>
      </div>
      <div class="collection-actions">
        <button @click="$emit('trash')"><WorkflowIcon name="trash" />回收站</button
        ><button class="primary" @click="$emit('create')"><WorkflowIcon name="plus" />新建作品</button>
      </div>
    </header>
    <div class="collection-toolbar">
      <div class="collection-filters" aria-label="作品状态筛选">
        <button
          v-for="option in options"
          :key="option.key"
          :class="{ selected: filter === option.key }"
          :aria-pressed="filter === option.key"
          @click="$emit('update:filter', option.key)"
        >
          {{ option.label }}
        </button>
      </div>
      <label class="collection-search"
        ><WorkflowIcon name="search" /><input
          type="search"
          :value="search"
          aria-label="搜索作品"
          placeholder="搜索作品名称…"
          @input="$emit('update:search', $event.target.value)"
      /></label>
    </div>
    <div v-if="error" class="collection-error" role="alert">
      {{ error }}<button @click="$emit('retry')">重新加载</button>
    </div>
    <p v-else-if="loading && !total" class="collection-empty">正在读取作品…</p>
    <div v-else-if="items.length" class="collection-grid">
      <article v-for="item in items" :key="item.task_id" class="collection-card">
        <button
          class="collection-open"
          :aria-label="`继续创作：${item.title || item.idea || '未命名作品'}`"
          @click="$emit('open', item.task_id)"
        >
          <div class="collection-cover">
            <img
              v-if="item.cover_url && !failedCovers.has(item.cover_url)"
              :src="item.cover_url"
              alt=""
              loading="lazy"
              decoding="async"
              @error="failCover(item.cover_url)"
            />
            <div v-else class="collection-placeholder">
              <WorkflowIcon name="shot" /><span>暂无封面</span><small>准备画面素材后，会显示在这里</small>
            </div>
            <span class="collection-state" :class="projectState(item).key"
              ><i></i>{{ projectState(item).label }}</span
            >
          </div>
          <div class="collection-info">
            <h2 :title="item.title || item.idea">{{ item.title || item.idea || '未命名作品' }}</h2>
            <p>
              {{ item.material_count || 0 }} 素材<span>·</span>{{ item.shots || 0 }} 分镜<span>·</span
              >{{ item.video_count || 0 }} 视频
            </p>
            <div>
              <time :datetime="item.created_at">{{ date(item.created_at) }} 创建</time
              ><span class="collection-continue">继续创作<WorkflowIcon name="arrow" /></span>
            </div>
          </div>
        </button>
        <footer>
          <button
            :aria-label="`移入回收站：${item.title || item.idea || '未命名作品'}`"
            @click="$emit('delete', item.task_id)"
          >
            <WorkflowIcon name="trash" />移入回收站
          </button>
        </footer>
      </article>
    </div>
    <div v-else class="collection-empty">
      <WorkflowIcon :name="search || filter !== 'all' ? 'search' : 'shot'" />
      <h2>{{ search || filter !== 'all' ? '没有找到符合条件的作品' : '开始你的第一部作品' }}</h2>
      <p>
        {{ search || filter !== 'all' ? '换个关键词，或查看全部作品。' : '从剧本、素材或空白分镜画布开始。' }}
      </p>
      <button v-if="!search && filter === 'all'" class="primary" @click="$emit('create')">新建作品</button
      ><button
        v-else
        @click="
          $emit('update:search', '');
          $emit('update:filter', 'all');
        "
      >
        查看全部作品
      </button>
    </div>
  </section>
</template>

<style scoped>
.project-library {
  width: min(1240px, 100%);
  margin: 0 auto;
  padding: 28px 24px 40px;
}
.collection-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  margin-bottom: 28px;
}
.collection-eyebrow {
  font-size: 11px;
  color: var(--fg-3);
  margin: 0 0 10px;
}
.collection-eyebrow span {
  margin-left: 12px;
  padding-left: 12px;
  border-left: 1px solid var(--line);
}
.collection-heading h1 {
  font-size: 26px;
  font-weight: 500;
  margin: 0 0 9px;
  letter-spacing: -0.5px;
}
.collection-heading > div > p:last-child {
  font-size: 12px;
  color: var(--fg-2);
  margin: 0;
}
.collection-actions {
  display: flex;
  gap: 10px;
}
.collection-actions button {
  display: flex;
  gap: 7px;
  align-items: center;
  font-size: 12px;
  padding: 9px 13px;
}
.collection-actions svg {
  width: 14px;
  height: 14px;
}
.collection-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}
.collection-filters {
  display: flex;
  gap: 4px;
}
.collection-filters button {
  background: transparent;
  border-color: transparent;
  font-size: 12px;
  color: var(--fg-2);
  padding: 7px 11px;
}
.collection-filters button.selected {
  background: var(--accent-dim);
  border-color: var(--day-warn-line, #6b5140);
  color: var(--accent);
}
.collection-search {
  display: flex;
  align-items: center;
  position: relative;
  width: 240px;
}
.collection-search svg {
  position: absolute;
  width: 15px;
  height: 15px;
  left: 11px;
  color: var(--fg-3);
  pointer-events: none;
}
.collection-search input {
  padding: 9px 12px 9px 33px;
  font-size: 12px;
  border-radius: 7px;
  background: var(--day-panel, #20252a);
}
.collection-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
  gap: 20px;
}
.collection-card {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 11px;
  overflow: hidden;
  background: var(--surface);
  transition: border-color 0.15s;
}
.collection-card:hover {
  border-color: var(--day-line, #65717b);
}
.collection-open {
  width: 100%;
  border: 0;
  border-radius: 0;
  display: block;
  text-align: left;
  padding: 0;
  background: transparent;
}
.collection-open:hover:not(:disabled) {
  background: var(--day-panel, #292e34);
}
.collection-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--day-panel, #20252a);
  overflow: hidden;
}
.collection-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.collection-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 9px;
  color: var(--fg-3);
  font-size: 12px;
  background: radial-gradient(ellipse at center, var(--day-panel, #2b3138), var(--day-panel, #1e242a));
}
.collection-placeholder svg {
  width: 28px;
  height: 28px;
  color: var(--day-muted, #a59b8f);
}
.collection-placeholder small {
  font-size: 10px;
}
.collection-state {
  position: absolute;
  top: 12px;
  left: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  padding: 4px 8px;
  background: var(--day-panel, #1a202be6);
  border: 1px solid var(--day-line, #46515b);
  border-radius: 5px;
  color: var(--day-text, #c1ccd4);
}
.collection-state i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--day-panel, #99a4b1);
}
.collection-state.succeeded i {
  background: var(--mint);
}
.collection-state.running i {
  background: var(--warn);
}
.collection-state.failed i {
  background: var(--danger);
}
.collection-info {
  padding: 17px 18px 15px;
}
.collection-info h2 {
  font-size: 15px;
  line-height: 1.5;
  margin: 0 0 11px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collection-info > p {
  margin: 0 0 17px;
  font-size: 11px;
  color: var(--fg-2);
}
.collection-info > p span {
  margin: 0 8px;
  color: var(--fg-3);
}
.collection-info > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 10px;
  color: var(--fg-3);
}
.collection-continue {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--accent);
}
.collection-continue svg {
  width: 12px;
  height: 12px;
}
.collection-card footer {
  border-top: 1px solid var(--line-soft);
  padding: 8px 13px;
  display: flex;
  justify-content: flex-end;
}
.collection-card footer button {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: var(--fg-3);
  background: transparent;
  padding: 4px 6px;
  border-color: transparent;
}
.collection-card footer button:hover:not(:disabled) {
  color: var(--danger);
  background: var(--danger-dim);
}
.collection-card footer svg {
  width: 12px;
  height: 12px;
}
.collection-error {
  color: var(--danger);
  padding: 18px;
  background: var(--danger-dim);
  border-radius: 8px;
  font-size: 12px;
}
.collection-error button {
  margin-left: 12px;
}
.collection-empty {
  padding: 65px 16px;
  text-align: center;
  color: var(--fg-3);
  font-size: 12px;
}
.collection-empty > svg {
  width: 30px;
  height: 30px;
}
.collection-empty h2 {
  color: var(--fg);
  font-size: 18px;
  font-weight: 500;
  margin: 18px 0 9px;
}
.collection-empty p {
  margin: 0 0 24px;
}
@media (max-width: 900px) {
  .project-library {
    padding: 22px 12px;
  }
  .collection-toolbar {
    flex-wrap: wrap;
  }
}
@media (max-width: 620px) {
  .project-library {
    padding: 16px 8px 28px;
  }
  .collection-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 18px;
    margin-bottom: 22px;
  }
  .collection-heading h1 {
    font-size: 23px;
  }
  .collection-actions button {
    font-size: 11px;
  }
  .collection-toolbar {
    gap: 13px;
  }
  .collection-search {
    width: 100%;
    order: -1;
  }
  .collection-filters {
    flex-wrap: wrap;
  }
  .collection-filters button {
    padding: 6px 9px;
    font-size: 11px;
  }
  .collection-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
  }
}
</style>

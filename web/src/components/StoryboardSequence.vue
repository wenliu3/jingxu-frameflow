<script setup>
import WorkflowIcon from './WorkflowIcon.vue';
import { STATUS_LABELS } from '../workflowStudio.js';
defineProps({ shots: Array, selectedId: String, output: Function, collapsed: Boolean });
defineEmits(['select', 'add', 'toggle']);
</script>
<template>
  <section class="story-sequence" :class="{ collapsed }" aria-label="镜头序列">
    <header>
      <button class="sequence-toggle" :aria-expanded="!collapsed" @click="$emit('toggle')">
        <WorkflowIcon name="shot" /><b>镜头序列</b><span>{{ shots.length }} 镜</span>
      </button>
      <p>按镜头连接排序 · 点击定位画布</p>
      <button aria-label="在序列中添加视频" @click="$emit('add')"><WorkflowIcon name="plus" /></button>
    </header>
    <div v-if="!collapsed" class="sequence-track">
      <button
        v-for="(shot, index) in shots"
        :key="shot.id"
        class="sequence-shot"
        :class="{ current: selectedId === shot.id }"
        :aria-label="`定位 ${shot.data.title}`"
        @click="$emit('select', shot.id)"
      >
        <div class="sequence-image">
          <WorkflowIcon :name="output(shot) ? 'video' : 'shot'" /><span>{{
            String(index + 1).padStart(2, '0')
          }}</span
          ><small>{{ shot.data.duration }}s</small>
        </div>
        <div class="sequence-copy">
          <b>{{ shot.data.title }}</b
          ><i :class="shot.data.status" :title="STATUS_LABELS[shot.data.status]"></i>
        </div>
      </button>
      <button class="sequence-add" @click="$emit('add')">
        <WorkflowIcon name="plus" /><span>下一镜</span>
      </button>
      <p v-if="!shots.length" class="sequence-empty">添加视频，开始安排你的故事。</p>
    </div>
  </section>
</template>
<style scoped>
.story-sequence {
  flex-shrink: 0;
  background: var(--day-inset, #17181d);
  border-top: 1px solid var(--day-line, #303139);
  color: var(--day-text, #dadbe4);
}
header {
  height: 39px;
  padding: 0 18px;
  display: flex;
  align-items: center;
  gap: 14px;
}
header p {
  margin: 0;
  color: var(--day-muted, #8d909f);
  font-size: 10px;
  flex: 1;
}
header button {
  display: flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: 0;
  color: var(--day-muted, #a4a5b3);
  padding: 4px;
}
.sequence-toggle b {
  font-size: 11px;
  font-weight: 500;
}
.sequence-toggle span {
  font-size: 10px;
  color: var(--day-muted, #858897);
}
header svg {
  width: 14px;
  height: 14px;
}
.sequence-track {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding: 0 18px 13px;
}
.sequence-shot {
  flex: 0 0 128px;
  padding: 3px;
  border: 1px solid var(--day-line, #34353f);
  border-radius: 7px;
  background: var(--day-panel, #202127);
  color: var(--day-text, #c5c7d3);
  text-align: left;
}
.sequence-shot.current {
  border-color: var(--day-accent-line, #ad95f7);
  background: var(--day-accent-soft, #30263f);
}
.sequence-image {
  position: relative;
  height: 60px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--day-inset, #111217);
  display: grid;
  place-items: center;
  color: var(--day-faint, #5e6173);
}
.sequence-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.sequence-image span,
.sequence-image small {
  position: absolute;
  padding: 2px 5px;
  background: #161620cf;
  font-size: 9px;
  color: #fff;
  border-radius: 3px;
}
.sequence-image span {
  top: 4px;
  left: 4px;
}
.sequence-image small {
  bottom: 4px;
  right: 4px;
}
.sequence-copy {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 3px 2px;
}
.sequence-copy b {
  font-size: 10px;
  font-weight: 400;
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.sequence-copy i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--day-panel, #676b7b);
}
.sequence-copy i.succeeded {
  background: var(--day-mint, #8ecfbc);
}
.sequence-copy i.running,
.sequence-copy i.composing,
.sequence-copy i.queued {
  background: var(--day-warn, #d6b57a);
}
.sequence-copy i.failed,
.sequence-copy i.interrupted {
  background: var(--day-danger, #ec9b98);
}
.sequence-add {
  flex: 0 0 76px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px dashed var(--day-line, #43434f);
  background: none;
  color: var(--day-muted, #9092a3);
  border-radius: 7px;
  font-size: 10px;
}
.sequence-empty {
  align-self: center;
  font-size: 11px;
  color: var(--day-muted, #84899c);
}
@media (max-width: 760px) {
  header p {
    display: none;
  }
  header {
    justify-content: space-between;
  }
  .sequence-shot {
    flex-basis: 108px;
  }
  .sequence-image {
    height: 48px;
  }
}
</style>

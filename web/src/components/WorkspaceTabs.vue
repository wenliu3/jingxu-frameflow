<script setup>
defineProps({ mode: String, count: { type: Number, default: 0 }, dark: Boolean });
defineEmits(['select']);
const tabs = [
  { id: 'studio', label: '素材工坊' },
  { id: 'storyboard', label: '创作画布' },
  { id: 'records', label: '生成记录' },
];
</script>
<template>
  <nav class="workspace-tabs" :class="{ dark }" aria-label="工作台视图">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      type="button"
      :class="{ on: mode === tab.id }"
      :aria-current="mode === tab.id ? 'page' : undefined"
      @click="$emit('select', tab.id)"
    >
      {{ tab.label }}<span v-if="tab.id === 'records' && count">{{ count }}</span>
    </button>
  </nav>
</template>
<style scoped>
.workspace-tabs {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
  flex-shrink: 0;
}
.workspace-tabs button {
  background: transparent;
  border: 1px solid transparent;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--fg-3);
  white-space: nowrap;
  border-radius: 5px;
}
.workspace-tabs button.on {
  background: var(--surface);
  border-color: var(--line);
  color: var(--accent);
  box-shadow: 0 1px 3px var(--day-shadow, #00000004);
}
.workspace-tabs button span {
  margin-left: 7px;
  padding: 0 5px;
  border-radius: 8px;
  font-size: 10px;
  color: var(--fg-3);
}
.workspace-tabs.dark {
  border: 0;
  background: transparent;
  gap: 5px;
  padding: 0;
}
.workspace-tabs.dark button {
  padding: 8px 14px;
  color: var(--day-muted, #87939d);
}
.workspace-tabs.dark button.on {
  color: var(--day-warn, #e9bc9e);
  border-color: var(--day-warn-line, #51463f);
  background: var(--day-warn-soft, #3a30281f);
  box-shadow: none;
}
.workspace-tabs.dark button span {
  color: var(--day-muted, #7d8a95);
  background: var(--day-hover, #ffffff08);
}
@media (max-width: 1000px) {
  .workspace-tabs.dark button {
    padding: 7px 10px;
    font-size: 11px;
  }
}
</style>

<script setup>
import WorkflowIcon from './WorkflowIcon.vue';
import { TYPE_LABELS, KIND_LABELS } from '../workflowGraph';
defineProps({ node: Object, material: Object, generating: Boolean });
const emit = defineEmits(['edit', 'checkpoint', 'close', 'duplicate', 'remove']);
</script>
<template>
  <aside class="flow-inspector" aria-label="节点编辑面板">
    <header>
      <span>{{ TYPE_LABELS[node.type] }}设置</span
      ><button aria-label="关闭节点设置" @click="emit('close')"><WorkflowIcon name="close" /></button>
    </header>
    <div class="inspector-scroll">
      <fieldset :disabled="generating">
        <label
          >节点名称<input
            :value="node.data.title"
            maxlength="80"
            @focus="emit('checkpoint')"
            @input="emit('edit', { title: $event.target.value })"
        /></label>
        <template v-if="node.type === 'note'"
          ><label
            >创作说明<textarea
              :value="node.data.text"
              maxlength="3000"
              rows="12"
              placeholder="剧情、对白、运镜想法…"
              @focus="emit('checkpoint')"
              @input="emit('edit', { text: $event.target.value })"
            ></textarea>
          </label>
          <p class="field-hint">连接到视频后，这段说明会加入编排上下文。</p></template
        >
        <template v-else-if="node.type === 'material'"
          ><img v-if="material?.url" class="material-large" :src="material.url" :alt="material.name" />
          <div class="material-info">
            <span>{{ KIND_LABELS[node.data.materialKind] }}</span
            ><b>{{ material?.name || node.data.materialName }}</b>
            <p>{{ material?.description || '素材尚未就绪，请前往素材工坊准备。' }}</p>
          </div>
          <p class="field-hint">同一份素材可以连接多个视频。移除画布节点会保留原素材。</p></template
        >
      </fieldset>
      <div class="node-actions">
        <button :disabled="generating" @click="emit('duplicate')"><WorkflowIcon name="copy" />复制节点</button
        ><button :disabled="generating" class="remove-node" @click="emit('remove')">
          <WorkflowIcon name="trash" />移除节点
        </button>
      </div>
    </div>
  </aside>
</template>
<style scoped>
.flow-inspector {
  width: 310px;
  flex: 0 0 310px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--day-inset, #1b1c23);
  border-left: 1px solid var(--day-line, #30313b);
  z-index: 5;
  color: var(--day-text, #d7dbe3);
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 18px;
  height: 51px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--day-line-soft, #ffffff09);
  font-size: 12px;
}
.flow-inspector button {
  padding: 5px;
  color: var(--day-muted, #b5bac5);
  border: 0;
  background: transparent;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
}
.flow-inspector button:hover {
  background: var(--day-hover, #ffffff09);
  color: var(--day-text, #fff);
}
header svg {
  width: 16px;
  height: 16px;
}
.inspector-scroll {
  padding: 20px 18px;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}
label {
  display: block;
  font-size: 12px;
  color: var(--day-muted, #aeb4c0);
  margin-bottom: 17px;
}
input,
textarea {
  margin-top: 8px;
  padding: 9px 10px;
  background: var(--day-inset, #14151c);
  border-color: var(--day-line, #3c3d4a);
  color: var(--day-text, #dce0ed);
  font-size: 12px;
  border-radius: 6px;
}
input:focus,
textarea:focus {
  border-color: var(--day-accent-line, #a78bd8);
  box-shadow: 0 0 0 2px var(--day-shadow, #a78bd820);
}
textarea {
  line-height: 1.85;
  resize: vertical;
  min-height: 120px;
}
input::placeholder,
textarea::placeholder {
  color: var(--day-faint, #727987);
}
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
fieldset:disabled input,
fieldset:disabled textarea {
  opacity: 0.6;
}
.field-hint {
  font-size: 10px;
  line-height: 1.7;
  color: var(--day-muted, #9a9eb0);
  margin: -8px 0 16px;
}
.node-actions {
  display: flex;
  gap: 8px;
  margin-top: 15px;
  padding-top: 15px;
  border-top: 1px solid var(--day-line-soft, #ffffff0a);
}
.node-actions button {
  flex: 1;
  font-size: 10px;
}
.node-actions svg {
  width: 13px;
  height: 13px;
}
.flow-inspector .remove-node {
  color: var(--day-danger, #d29891);
}
.material-large {
  width: 100%;
  background: var(--day-inset, #121418);
  border-radius: 6px;
}
.material-info {
  font-size: 12px;
  margin: 15px 0 25px;
}
.material-info span {
  color: var(--day-mint, #8bb8a5);
  font-size: 10px;
}
.material-info b {
  display: block;
  margin-top: 8px;
}
.material-info p {
  color: var(--day-muted, #a0a7b4);
  font-size: 11px;
  line-height: 1.9;
}
@media (max-width: 900px) {
  .flow-inspector {
    width: 280px;
    flex-basis: 280px;
  }
}
</style>

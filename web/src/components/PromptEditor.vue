<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { KIND_LABELS } from '../workflowGraph.js';
import {
  connectedMentionChoices,
  displayPrompt,
  mentionToken,
  mentionQuery,
  promptParts,
} from '../promptMentions.js';

const props = defineProps({
  modelValue: { type: String, default: '' },
  materials: { type: Array, default: () => [] },
  connected: { type: Array, default: () => [] },
  disabled: Boolean,
  manual: Boolean,
});
const emit = defineEmits(['update:modelValue', 'checkpoint', 'generate']);
const editor = ref(null),
  menu = ref(null),
  query = ref(null),
  active = ref(0),
  position = ref({});
let composing = false;
const limit = computed(() => (props.manual ? 8000 : 1000));
const isLinked = (m) => props.connected.some((c) => c?.kind === m.kind && c.name === m.name);
const choices = computed(() => connectedMentionChoices(props.connected, query.value?.query || ''));
const available = computed(() => choices.value.filter((m) => m.ready));

function read(node) {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return '';
  if (node.dataset?.token) return node.dataset.token;
  if (node.nodeName === 'BR') return '\n';
  if (node === editor.value && node.childNodes.length === 1 && node.firstChild.nodeName === 'BR') return '';
  let text = '';
  for (const child of node.childNodes) {
    if (['DIV', 'P'].includes(child.nodeName) && text && !text.endsWith('\n')) text += '\n';
    text += read(child);
  }
  return text;
}
function caret() {
  const selection = window.getSelection();
  if (!selection.rangeCount || !editor.value.contains(selection.anchorNode)) return null;
  const range = selection.getRangeAt(0).cloneRange();
  range.selectNodeContents(editor.value);
  range.setEnd(selection.anchorNode, selection.anchorOffset);
  return read(range.cloneContents()).length;
}
function point(offset) {
  let remaining = offset;
  function locate(parent) {
    let text = '';
    for (const [index, node] of [...parent.childNodes].entries()) {
      if (['DIV', 'P'].includes(node.nodeName) && text && !text.endsWith('\n')) {
        if (remaining === 0) return [parent, index];
        remaining--;
        text += '\n';
      }
      const length = read(node).length;
      if (node.nodeType === Node.TEXT_NODE && remaining <= length) return [node, remaining];
      if ((node.dataset?.token || node.nodeName === 'BR') && remaining < length) return [parent, index];
      if (
        node.nodeType === Node.ELEMENT_NODE &&
        !node.dataset?.token &&
        node.nodeName !== 'BR' &&
        remaining <= length
      ) {
        return locate(node) || [node, node.childNodes.length];
      }
      remaining -= length;
      text += read(node);
    }
  }
  return locate(editor.value) || [editor.value, editor.value.childNodes.length];
}
function selectRange(start, end = start) {
  const range = document.createRange();
  range.setStart(...point(start));
  range.setEnd(...point(end));
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}
function chip(part) {
  const material = props.materials.find((m) => m.kind === part.kind && m.name === part.name);
  const element = document.createElement('span');
  element.className = `prompt-mention${!material?.ready || !isLinked(part) ? ' missing' : ''}`;
  element.contentEditable = 'false';
  element.dataset.token = part.token;
  element.title = `${KIND_LABELS[part.kind]} · ${part.name}${!isLinked(part) ? ' · 未连接' : ''}`;
  if (material?.url) {
    const image = document.createElement('img');
    image.src = material.url;
    image.alt = '';
    image.draggable = false;
    element.append(image);
  }
  element.append(document.createTextNode(`@${part.name}`));
  return element;
}
function render(text, offset = null) {
  const fragment = document.createDocumentFragment();
  for (const part of promptParts(text))
    fragment.append(part.token ? chip(part) : document.createTextNode(part.text));
  editor.value.replaceChildren(fragment);
  if (offset !== null) selectRange(Math.min(offset, text.length));
}
function publish() {
  const text = read(editor.value);
  if (text.length > limit.value) {
    render(props.modelValue, Math.min(caret() ?? 0, props.modelValue.length));
    return;
  }
  emit('update:modelValue', text);
}
async function updateQuery() {
  if (composing || props.disabled) return;
  const offset = caret();
  query.value = offset === null ? null : mentionQuery(read(editor.value), offset);
  active.value = 0;
  if (!query.value) return;
  await nextTick();
  const rect = window.getSelection().getRangeAt(0).getBoundingClientRect();
  const fallback = editor.value.getBoundingClientRect();
  const x = rect.width || rect.height ? rect.left : fallback.left;
  const y = rect.height ? rect.top : fallback.top;
  const height = Math.min(menu.value?.offsetHeight || 300, window.innerHeight - 24);
  position.value = {
    left: `${Math.max(12, Math.min(x, window.innerWidth - 312))}px`,
    top: `${Math.max(12, Math.min(y - height - 8, window.innerHeight - height - 12))}px`,
  };
}
function choose(material) {
  if (!material?.ready || !isLinked(material) || props.disabled || !query.value) return;
  const token = mentionToken(material),
    text = read(editor.value),
    { start, end } = query.value;
  if (text.length - (end - start) + token.length + 1 > limit.value) return;
  editor.value.focus();
  selectRange(start, end);
  // Browser insertion retains native typing undo and treats each mention as one unit.
  const element = chip({ token, kind: material.kind, name: material.name });
  document.execCommand('insertHTML', false, `${element.outerHTML} `);
  query.value = null;
  publish();
}
function key(event) {
  if (composing || event.isComposing) return;
  if (query.value && ['ArrowDown', 'ArrowUp', 'Enter', 'Escape', 'Tab'].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Escape') query.value = null;
    else if (event.key === 'Enter' || event.key === 'Tab') choose(available.value[active.value]);
    else {
      active.value =
        (active.value + (event.key === 'ArrowDown' ? 1 : -1) + available.value.length) %
        (available.value.length || 1);
      nextTick(() =>
        menu.value?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
      );
    }
  } else if (event.key === 'Enter') {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) emit('generate');
    else document.execCommand('insertText', false, '\n');
  }
}
function paste(event) {
  event.preventDefault();
  const text =
    event.clipboardData.getData('application/x-frameflow-prompt') ||
    event.clipboardData.getData('text/plain');
  const selection = window.getSelection();
  const selected = selection.rangeCount ? read(selection.getRangeAt(0).cloneContents()).length : 0;
  const room = limit.value - read(editor.value).length + selected;
  if (room > 0) document.execCommand('insertText', false, text.slice(0, room));
  const offset = caret();
  render(read(editor.value), offset);
  publish();
  updateQuery();
}
function copy(event, cut = false) {
  const selection = window.getSelection();
  if (!selection.rangeCount || selection.isCollapsed) return;
  event.preventDefault();
  const text = read(selection.getRangeAt(0).cloneContents());
  event.clipboardData.setData('text/plain', displayPrompt(text));
  event.clipboardData.setData('application/x-frameflow-prompt', text);
  if (cut && !props.disabled) document.execCommand('delete');
}
function outside(event) {
  if (!editor.value?.contains(event.target) && !menu.value?.contains(event.target)) query.value = null;
}
watch(
  () => props.modelValue,
  (text) => {
    if (editor.value && !composing && read(editor.value) !== text)
      render(text, document.activeElement === editor.value ? caret() : null);
  }
);
watch(
  () => [props.materials, props.connected],
  () => {
    if (editor.value && !composing)
      render(read(editor.value), document.activeElement === editor.value ? caret() : null);
  },
  { deep: true }
);
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) query.value = null;
  }
);
onMounted(() => {
  render(props.modelValue);
  document.addEventListener('pointerdown', outside);
});
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>

<template>
  <div class="prompt-editor-wrap">
    <div
      ref="editor"
      class="prompt-editor"
      role="textbox"
      aria-label="视频画面描述"
      aria-multiline="true"
      :aria-disabled="disabled"
      :contenteditable="!disabled"
      :data-placeholder="
        manual ? '写下英文提示词，输入 @ 引用素材…' : '描述人物动作、环境和运镜，输入 @ 引用素材…'
      "
      @focus="emit('checkpoint')"
      @input="
        publish();
        updateQuery();
      "
      @click="updateQuery"
      @keyup="!['ArrowUp', 'ArrowDown', 'Enter', 'Escape', 'Tab'].includes($event.key) && updateQuery()"
      @keydown="key"
      @paste="paste"
      @copy="copy($event)"
      @cut="copy($event, true)"
      @drop.prevent
      @compositionstart="composing = true"
      @compositionend="
        composing = false;
        publish();
        updateQuery();
      "
    ></div>
    <p class="prompt-hint">输入 <b>@</b> 选择素材 · 引用会自动对应实际素材 · Ctrl + Enter 生成</p>
    <Teleport to="body">
      <div
        v-if="query"
        ref="menu"
        class="mention-menu"
        :style="position"
        @pointerdown.stop.prevent
        @wheel.stop
      >
        <header>
          引用素材 <small>{{ query.query ? `搜索：${query.query}` : '当前视频已连接的素材' }}</small>
        </header>
        <div role="listbox" aria-label="选择提示词素材">
          <button
            v-for="material in choices"
            :key="`${material.kind}:${material.name}`"
            role="option"
            :aria-selected="available[active] === material"
            :disabled="!material.ready"
            @click="choose(material)"
          >
            <span class="mention-image"
              ><img v-if="material.url" :src="material.url" alt="" /><span v-else class="mention-icon">@</span
              ><i>{{ material.number }}</i></span
            >
            <span
              ><b>{{ material.name }}</b
              ><small
                >{{ KIND_LABELS[material.kind] }} ·
                {{ !material.ready ? '待准备' : `图片 ${material.number}` }}</small
              ></span
            >
          </button>
          <p v-if="!choices.length">
            {{ connected.length ? '没有匹配的素材，试试其他名称' : '先从「参考素材」为这个视频添加图片' }}
          </p>
          <p v-else-if="!available.length">请先在素材工坊准备图片</p>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style>
.prompt-editor {
  width: 100%;
  height: 116px;
  overflow-y: auto;
  padding: 13px 2px 7px;
  color: var(--day-text, #e4e2ec);
  background: transparent;
  border: 0;
  outline: none;
  font-size: 13px;
  line-height: 2;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  cursor: text;
  user-select: text;
}
.prompt-editor:empty::before {
  content: attr(data-placeholder);
  color: var(--day-muted, #85838f);
  pointer-events: none;
}
.prompt-editor[aria-disabled='true'] {
  opacity: 0.65;
}
.prompt-hint {
  margin: 2px 0 8px;
  color: var(--day-muted, #90909f);
  font-size: 10px;
}
.prompt-hint b {
  color: var(--day-accent, #c7b3ef);
}
.prompt-mention {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  vertical-align: middle;
  max-width: 100%;
  padding: 0 6px 0 3px;
  margin: 0 2px;
  border: 1px solid var(--day-accent-line, #76668f);
  border-radius: 5px;
  background: var(--day-accent-soft, #b9a0f51a);
  color: var(--day-text, #e2d5ff);
  line-height: 23px;
  font-size: 12px;
  user-select: all;
  white-space: normal;
}
.prompt-mention img {
  width: 20px;
  height: 20px;
  object-fit: cover;
  border-radius: 3px;
  pointer-events: none;
}
.prompt-mention.missing {
  border-color: var(--day-danger-line, #c78079);
  color: var(--day-danger, #e9aaa4);
}
.mention-menu {
  position: fixed;
  z-index: 100;
  width: min(300px, calc(100vw - 24px));
  max-height: min(330px, calc(100vh - 24px));
  display: flex;
  flex-direction: column;
  padding: 7px;
  border: 1px solid var(--day-line, #51505e);
  border-radius: 12px;
  background: var(--day-panel, #292930);
  color: var(--day-text, #e7e5ee);
  box-shadow: 0 12px 45px var(--day-shadow, #0009);
}
.mention-menu header {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 5px 7px 9px;
  font-size: 12px;
  flex-shrink: 0;
}
.mention-menu small {
  color: var(--day-muted, #a2a0b1);
  font-size: 10px;
}
.mention-menu [role='listbox'] {
  overflow-y: auto;
  overscroll-behavior: contain;
}
.mention-menu button {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  text-align: left;
  color: inherit;
}
.mention-menu button:hover:enabled,
.mention-menu button[aria-selected='true'] {
  background: var(--day-accent-soft, #b9a0f522);
}
.mention-menu button:disabled {
  opacity: 0.45;
}
.mention-menu button img,
.mention-icon {
  width: 36px;
  height: 36px;
  border-radius: 5px;
  object-fit: cover;
  flex-shrink: 0;
}
.mention-image {
  position: relative;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
}
.mention-image img {
  display: block;
}
.mention-image i {
  position: absolute;
  top: 1px;
  left: 1px;
  min-width: 14px;
  height: 14px;
  display: grid;
  place-items: center;
  padding: 0 3px;
  border-radius: 50%;
  background: var(--day-inset, #18181cde);
  color: var(--day-text, #fff);
  font-size: 9px;
  font-style: normal;
  line-height: 1;
}
.mention-icon {
  display: grid;
  place-items: center;
  background: var(--day-inset, #1d1d25);
  color: var(--day-accent, #bba0ed);
}
.mention-menu button > span:last-child {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.mention-menu b {
  font-size: 12px;
  overflow-wrap: anywhere;
  font-weight: 500;
}
.mention-menu p {
  padding: 8px;
  color: var(--day-muted, #b8b5c6);
  font-size: 11px;
}
</style>

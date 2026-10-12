<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { SPEECH_INTERJECTIONS } from '../speechSettings.js';
import { readSpeechDOM, speechParts } from '../speechText.js';
import WorkflowIcon from './WorkflowIcon.vue';

const props = defineProps({
  modelValue: String,
  disabled: Boolean,
  markers: Boolean,
  interjections: Boolean,
});
const emit = defineEmits(['update:modelValue', 'checkpoint']);
const editor = ref(null),
  menu = ref(null),
  menuKind = ref(''),
  position = ref({}),
  customSeconds = ref(0.5),
  menuError = ref('');
const pauses = [0.25, 0.5, 1, 1.5];
let range,
  editingToken,
  anchor,
  composing = false,
  observer;
const past = [],
  future = [];

function tokenElement(part) {
  if (part.type === 'text') return document.createTextNode(part.raw);
  const element = document.createElement('span');
  element.className = `speech-token ${part.type}`;
  element.dataset.speechToken = part.raw;
  element.textContent = part.label;
  element.contentEditable = 'false';
  element.setAttribute('role', 'button');
  element.setAttribute('tabindex', props.disabled ? '-1' : '0');
  element.setAttribute(
    'aria-label',
    part.type === 'pause' ? `编辑停顿 ${part.seconds} 秒` : `编辑语气词 ${part.label}`
  );
  return element;
}
function fragment(text) {
  const result = document.createDocumentFragment();
  const parts = props.markers ? speechParts(text) : [{ type: 'text', raw: text }];
  for (const part of parts) result.append(tokenElement(part));
  return result;
}
function render(text) {
  editor.value.replaceChildren(fragment(text));
}
function remember() {
  const selection = window.getSelection();
  if (
    selection?.rangeCount &&
    editor.value.contains(selection.anchorNode) &&
    editor.value.contains(selection.focusNode)
  )
    range = selection.getRangeAt(0).cloneRange();
}
function rememberUndo() {
  const text = readSpeechDOM(editor.value);
  if (past.at(-1) !== text) past.push(text);
  if (past.length > 100) past.shift();
  future.length = 0;
}
function notify() {
  if (composing) return;
  const text = readSpeechDOM(editor.value);
  if (text.length > 9999) {
    render(props.modelValue || '');
    return;
  }
  emit('update:modelValue', text);
  remember();
}
function caretEnd() {
  range = document.createRange();
  range.selectNodeContents(editor.value);
  range.collapse(false);
}
function restoreCaret() {
  editor.value.focus({ preventScroll: true });
  if (!range || !editor.value.contains(range.commonAncestorContainer)) caretEnd();
  window.getSelection().removeAllRanges();
  window.getSelection().addRange(range);
}
function close(restore = false) {
  if (menu.value?.matches(':popover-open')) menu.value.hidePopover();
  menuKind.value = '';
  if (restore) restoreCaret();
}
function placeMenu() {
  if (!anchor || !menu.value?.matches(':popover-open')) return;
  const rect = anchor.getBoundingClientRect(),
    height = Math.min(menu.value.scrollHeight, 340);
  const width = menuKind.value === 'pause' ? 200 : 170;
  position.value = {
    width: `${width}px`,
    left: `${Math.max(12, Math.min(window.innerWidth - width - 12, rect.left))}px`,
    top: `${Math.max(12, rect.top >= height + 20 ? rect.top - height - 8 : Math.min(rect.bottom + 8, window.innerHeight - height - 12))}px`,
  };
}
async function show(kind, target, token = null) {
  if (props.disabled || !props.markers || (kind === 'expression' && !props.interjections)) return;
  remember();
  editingToken = token;
  anchor = target;
  menuKind.value = kind;
  menuError.value = '';
  customSeconds.value = token ? speechParts(token.dataset.speechToken)[0]?.seconds || 0.5 : 0.5;
  await nextTick();
  menu.value.showPopover();
  placeMenu();
  menu.value.querySelector('button')?.focus({ preventScroll: true });
}
function replace(text) {
  if (props.disabled) return;
  const current = readSpeechDOM(editor.value);
  const replaced =
    editingToken?.dataset.speechToken.length || (range ? readSpeechDOM(range.cloneContents()).length : 0);
  if (current.length - replaced + text.length > 9999) return;
  rememberUndo();
  emit('checkpoint');
  restoreCaret();
  if (editingToken?.isConnected) {
    range = document.createRange();
    range.selectNode(editingToken);
  }
  const content = fragment(text),
    last = content.lastChild;
  range.deleteContents();
  range.insertNode(content);
  if (last) range.setStartAfter(last);
  range.collapse(true);
  close();
  editingToken = null;
  restoreCaret();
  notify();
}
function pause(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0.01 || value > 99.99) {
    menuError.value = '请输入 0.01–99.99 秒';
    return;
  }
  replace(`<#${Number(value.toFixed(2))}#>`);
}
function chip(event) {
  const token = event.target.closest('[data-speech-token]');
  if (token && editor.value.contains(token)) {
    event.preventDefault();
    show(token.classList.contains('pause') ? 'pause' : 'expression', token, token);
  }
}
function beforeInput(event) {
  if (props.disabled) {
    event.preventDefault();
    return;
  }
  if (!composing) rememberUndo();
}
function onKey(event) {
  if (['Enter', ' '].includes(event.key) && event.target.dataset?.speechToken) {
    chip(event);
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    event.stopPropagation();
    const from = event.shiftKey ? future : past,
      to = event.shiftKey ? past : future;
    if (!from.length) return;
    to.push(readSpeechDOM(editor.value));
    emit('checkpoint');
    render(from.pop());
    caretEnd();
    restoreCaret();
    notify();
  }
}
function paste(event) {
  event.preventDefault();
  if (props.disabled) return;
  remember();
  editingToken = null;
  const selectedLength = range ? readSpeechDOM(range.cloneContents()).length : 0;
  const allowed = 9999 - readSpeechDOM(editor.value).length + selectedLength;
  replace((event.clipboardData.getData('text/plain') || '').replace(/\r\n?/g, '\n').slice(0, allowed));
}
function copy(event, cut = false) {
  remember();
  if (!range || range.collapsed) return;
  event.preventDefault();
  event.clipboardData.setData('text/plain', readSpeechDOM(range.cloneContents()));
  if (cut && !props.disabled) {
    editingToken = null;
    replace('');
  }
}
watch(
  () => [props.modelValue, props.markers, props.disabled],
  () => {
    if (!editor.value || composing) return;
    if (readSpeechDOM(editor.value) !== (props.modelValue || '')) render(props.modelValue || '');
    if (props.disabled) close();
  }
);
onMounted(() => {
  render(props.modelValue || '');
  window.addEventListener('resize', placeMenu);
  window.addEventListener('scroll', placeMenu, true);
  observer = new ResizeObserver(placeMenu);
  observer.observe(menu.value);
});
onBeforeUnmount(() => {
  close();
  observer?.disconnect();
  window.removeEventListener('resize', placeMenu);
  window.removeEventListener('scroll', placeMenu, true);
});
</script>

<template>
  <div class="speech-text-editor">
    <div v-if="markers" class="speech-token-tools" role="group" aria-label="台词表达工具">
      <button
        type="button"
        aria-label="停顿"
        :disabled="disabled"
        title="插入停顿，选择或输入秒数"
        @pointerdown.prevent
        @click="show('pause', $event.currentTarget)"
      >
        <span class="token-symbol">&lt;#&gt;</span>停顿
      </button>
      <button
        v-if="interjections"
        type="button"
        aria-label="插入语气词"
        :disabled="disabled"
        @pointerdown.prevent
        @click="show('expression', $event.currentTarget)"
      >
        <span class="token-symbol">()</span>语气词
      </button>
    </div>
    <div
      ref="editor"
      class="speech-editable"
      role="textbox"
      aria-label="台词 / 旁白"
      aria-multiline="true"
      :aria-readonly="disabled"
      :contenteditable="disabled ? 'false' : 'true'"
      spellcheck="false"
      data-placeholder="输入台词或旁白，用停顿和语气词调整表达…"
      @focus="emit('checkpoint')"
      @beforeinput="beforeInput"
      @input="notify"
      @paste="paste"
      @copy="copy"
      @cut="copy($event, true)"
      @click="chip"
      @keyup="remember"
      @mouseup="remember"
      @blur="remember"
      @keydown="onKey"
      @compositionstart="
        rememberUndo();
        composing = true;
      "
      @compositionend="
        composing = false;
        notify();
      "
    />
    <div
      ref="menu"
      popover="auto"
      class="speech-token-menu"
      :style="position"
      role="dialog"
      :aria-label="menuKind === 'pause' ? '设置停顿' : '选择语气词'"
      @pointerdown.stop
      @wheel.stop
      @keydown.stop
      @keydown.esc.prevent="close(true)"
      @toggle="if (!$event.newState || $event.newState === 'closed') menuKind = '';"
    >
      <template v-if="menuKind === 'pause'">
        <button v-for="seconds in pauses" :key="seconds" @click="pause(seconds)">{{ seconds }}s</button>
        <form class="custom-pause" @submit.prevent="pause(customSeconds)">
          <label
            >自定义秒数<input
              v-model="customSeconds"
              type="number"
              aria-label="停顿秒数"
              min="0.01"
              max="99.99"
              step="0.01"
              required
          /></label>
          <button type="submit" aria-label="应用停顿"><WorkflowIcon name="check" /></button>
        </form>
        <p v-if="menuError" role="alert">{{ menuError }}</p>
      </template>
      <template v-else-if="menuKind === 'expression'">
        <button
          v-for="expression in SPEECH_INTERJECTIONS"
          :key="expression.value"
          @click="replace(expression.value)"
        >
          {{ expression.label }}
        </button>
      </template>
      <button v-if="editingToken" class="remove-token" @click="replace('')">删除标签</button>
    </div>
  </div>
</template>

<style scoped>
.speech-token-tools {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 2px 0 5px;
}
.speech-token-tools button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 8px;
  border: 0;
  border-radius: 6px;
  background: var(--day-inset, #ffffff06);
  color: var(--day-muted, #bbb8c5);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.token-symbol {
  font-size: 12px;
  font-weight: 500;
}
.speech-editable {
  min-height: 134px;
  max-height: 230px;
  overflow: auto;
  padding: 12px 1px 20px;
  font-size: 14px;
  line-height: 2;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  outline: none;
  color: var(--day-text, #dce0ed);
  cursor: text;
}
.speech-editable:empty::before {
  content: attr(data-placeholder);
  color: var(--day-faint, #777987);
  pointer-events: none;
}
.speech-editable :deep(.speech-token) {
  display: inline;
  margin: 0 3px;
  padding: 3px 7px;
  border-radius: 5px;
  background: var(--day-inset, #ffffff06);
  color: var(--day-cyan, #7ed6ea);
  white-space: nowrap;
  font-size: 12px;
  cursor: pointer;
  user-select: all;
}
.speech-editable :deep(.speech-token:hover),
.speech-editable :deep(.speech-token:focus-visible) {
  outline: 1px solid var(--day-cyan, #7ed6ea);
  outline-offset: 1px;
}
.speech-token-menu {
  position: fixed;
  inset: auto;
  margin: 0;
  padding: 5px;
  max-height: 340px;
  overflow-y: auto;
  box-sizing: border-box;
  color: var(--day-text, #dedce7);
  background: var(--day-panel, #28272f);
  border: 1px solid var(--day-line, #484350);
  border-radius: 12px;
  box-shadow: 0 8px 30px var(--day-shadow, #0005);
}
.speech-token-menu > button {
  display: flex;
  align-items: center;
  width: 100%;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 10px 12px;
  font: inherit;
  font-size: 13px;
  border-radius: 7px;
  cursor: pointer;
}
.speech-token-menu button:hover,
.speech-token-menu button:focus-visible {
  background: var(--day-hover, #ffffff0b);
  outline: none;
  color: var(--day-accent, #cab4ff);
}
.custom-pause {
  display: flex;
  align-items: flex-end;
  gap: 5px;
  padding: 10px 7px 5px;
  border-top: 1px solid var(--day-line-soft, #ffffff0a);
}
.custom-pause label {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--day-muted, #aaa4b5);
}
.custom-pause input {
  display: block;
  width: 100%;
  margin-top: 6px;
  padding: 7px 8px;
  font: inherit;
  font-size: 12px;
  color: var(--day-text, #dedce7);
  border: 1px solid var(--day-line, #484350);
  border-radius: 6px;
  background: var(--day-inset, #ffffff05);
  box-shadow: none;
}
.custom-pause button {
  display: grid;
  place-items: center;
  height: 32px;
  width: 32px;
  padding: 0;
  color: var(--day-accent, #cab4ff);
  background: var(--day-accent-soft, #b9a0f514);
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}
.speech-token-menu .remove-token {
  color: var(--day-danger, #e4a29b);
  margin-top: 5px;
  border-top: 1px solid var(--day-line-soft, #ffffff0c);
}
.speech-token-menu p {
  font-size: 10px;
  color: var(--day-danger, #e4a29b);
  padding: 0 7px;
}
@media (max-width: 760px) {
  .speech-editable {
    min-height: 104px;
    max-height: 180px;
    font-size: 13px;
  }
}
</style>

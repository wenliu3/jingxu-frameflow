import { SPEECH_INTERJECTIONS } from './speechSettings.js';

const expressions = new Map(SPEECH_INTERJECTIONS.map((item) => [item.value, item.label]));
export function speechParts(value = '') {
  const parts = [];
  const pattern = /<#(\d+(?:\.\d{1,2})?)#>|\([a-z-]+\)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    const seconds = match[1] == null ? null : Number(match[1]);
    const label = expressions.get(match[0]);
    if (!(seconds >= 0.01 && seconds <= 99.99) && !label) continue;
    if (match.index > cursor) parts.push({ type: 'text', raw: value.slice(cursor, match.index) });
    parts.push(
      seconds == null
        ? { type: 'expression', raw: match[0], label: `(${label})` }
        : { type: 'pause', raw: match[0], seconds, label: `${seconds}s` }
    );
    cursor = match.index + match[0].length;
  }
  if (cursor < value.length) parts.push({ type: 'text', raw: value.slice(cursor) });
  return parts;
}

// Visible Chinese labels are never sent to the speech service; retain the original API markers.
export function readSpeechDOM(node) {
  if (node.nodeType === 3) return node.textContent;
  if (node.dataset?.speechToken) return node.dataset.speechToken;
  if (node.nodeName === 'BR') return node.parentNode.childNodes.length === 1 ? '' : '\n';
  let text = '';
  for (const child of node.childNodes) {
    if (['DIV', 'P'].includes(child.nodeName) && text && !text.endsWith('\n')) text += '\n';
    text += readSpeechDOM(child);
  }
  return text;
}

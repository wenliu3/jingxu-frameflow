// Store material identity, never a transient Picture/Subject ordinal.
const pattern = /@\{(character|prop|scene|image):([^{}]+)\}/g;
// Match the backend's Picture order while retaining selection order within a kind.
export function numberMaterials(materials) {
  const kinds = ['character', 'prop', 'scene', 'image'];
  return [...materials]
    .sort((a, b) => kinds.indexOf(a.kind) - kinds.indexOf(b.kind))
    .map((material, index) => ({ ...material, number: index + 1 }));
}
export function connectedMentionChoices(connected, query = '') {
  return connected.filter((m) => m.name.toLowerCase().includes(query.toLowerCase()));
}
export function mentionToken(material) {
  return `@{${material.kind}:${encodeURIComponent(material.name)}}`;
}
export function promptParts(text = '') {
  const parts = [];
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > end) parts.push({ text: text.slice(end, match.index) });
    try {
      parts.push({ token: match[0], kind: match[1], name: decodeURIComponent(match[2]) });
    } catch {
      parts.push({ text: match[0] });
    }
    end = match.index + match[0].length;
  }
  if (end < text.length) parts.push({ text: text.slice(end) });
  return parts;
}
export function displayPrompt(text = '') {
  return promptParts(text)
    .map((p) => (p.token ? `@${p.name}` : p.text))
    .join('');
}
export function mentionsError(text, materials) {
  const missing = promptParts(text).find(
    (p) => p.token && !materials.some((m) => m?.kind === p.kind && m.name === p.name && m.ready)
  );
  return missing ? `引用的素材「${missing.name}」未连接或未就绪，请重新选择或删除这处引用` : '';
}
export function mentionQuery(text, caret) {
  const prefix = text.slice(0, caret);
  const match = /@([^@\n{}]{0,40})$/.exec(prefix);
  // Avoid opening the material menu in an email address.
  if (!match || /[\w.]/.test(prefix[match.index - 1] || '')) return null;
  return { start: match.index, end: caret, query: match[1].trim() };
}

// All catalog, audition, generation and project requests use fixtures.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { emptyWorkflow, makeNode } from '../../web/src/workflowGraph.js';
import { SPEECH_DEFAULTS } from '../../web/src/speechSettings.js';
import { serveAudioFixture } from './audio_fixture.mjs';
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const taskId = 'abcdef123456', base = `/api/tasks/${taskId}`;
let graph = emptyWorkflow();
const node = makeNode('material', 300, 80, { title: '语音 01 · 故事旁白', creationKind: 'audio', materialKind: 'audio', status: 'draft', prompt: '穿过晨光与薄雾，新的故事正在展开。', voiceId: 'young-girl' });
graph.nodes.push(node);
const project = { title: '声音工作室 · 创作练习', aspect_ratio: '16:9', characters: [], assets: [] };
const cfg = { video_backend: 'muse', audio_provider: 'minimax', audio_api_key: 'fixture', audio_model: 'speech-2.8-hd' };
const voices = [
  { id: 'young-girl', cn: '少女', gender: 'female', locale: '普通话', hint: '清亮年轻的女声，适合少女与活泼的角色', source: 'system' },
  { id: 'mature-woman', cn: '御姐', gender: 'female', locale: '普通话', hint: '成熟有气场的女声，适合人物对白', source: 'system' },
  { id: 'sweet-girl', cn: '甜美女性', gender: 'female', locale: '普通话', hint: '甜美温柔，适合亲切的旁白', source: 'system' },
  { id: 'young-man', cn: '青涩青年', gender: 'male', locale: '普通话', hint: '清朗温和的青年男声', source: 'system' },
  { id: 'storyteller', cn: '沉稳讲述', gender: 'male', locale: '普通话', hint: '低沉稳定，适合有声书与叙事', source: 'system' },
  { id: 'my-voice', cn: '我的故事声音', gender: '', locale: '未标注', hint: '账户中已启用的克隆音色', source: 'cloned' },
  ...Array.from({ length: 20 }, (_, i) => ({ id: `english-${i}`, cn: `英文示例 ${i + 1}`, gender: i % 2 ? 'female' : 'male', locale: '英语', hint: '用于分页和筛选的测试音色', source: 'system' })),
];
const errors = [], unexpected = [], previews = [], submissions = [];
let catalogReads = 0;
let finishGeneration = false;
page.on('pageerror', error => errors.push(error.message));
await page.route('**/files/**', serveAudioFixture);
await page.route('**/api/**', route => {
  const request = route.request(), pathname = new URL(request.url()).pathname;
  const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
  if (pathname === '/api/config') return json(cfg);
  if (pathname === '/api/video-capabilities') return json({ controls_version: 1, video_sources_version: 1 });
  if (pathname === '/api/tasks') return json([]);
  if (pathname === '/api/audio/voices') { catalogReads++; return json({ provider: cfg.audio_provider, source: 'synced', items: voices }); }
  if (pathname === base) return json({ task_id: taskId, status: 'succeeded', project, shots: [], blocks: [] });
  if (pathname.endsWith('/segments')) return json({ items: [] });
  if (pathname.endsWith('/workflow')) {
    if (request.method() === 'GET') return json(graph);
    graph = request.postDataJSON(); graph.revision++;
    return json({ ok: true, revision: graph.revision });
  }
  if (pathname.endsWith('/speech-preview')) { previews.push(request.postDataJSON()); return json({ url: `/files/${taskId}/voice_previews/fixture.wav`, cached: false }); }
  if (pathname.endsWith('/media-jobs')) {
    submissions.push(request.postDataJSON());
    return json({ job_id: submissions.at(-1).request_id, status: 'running' });
  }
  if (pathname.includes('/media-jobs/')) {
    if (!finishGeneration) return json({ job_id: pathname.split('/').at(-1), status: 'running' });
    project.assets = [{ name: '故事旁白', kind: 'audio', images: ['fixture.wav'] }];
    return json({ job_id: pathname.split('/').at(-1), status: 'succeeded', result: { asset_name: '故事旁白', kind: 'audio', file: 'fixture.wav', index: 0 } });
  }
  unexpected.push(pathname);
  return route.fulfill({ status: 404, body: '' });
});
const dialog = page.getByRole('dialog', { name: '音色库', exact: true });
const voiceButton = page.getByRole('button', { name: '音色', exact: true });
const controlsButton = page.getByRole('button', { name: '语音调节', exact: true });
const prompt = page.getByRole('textbox', { name: '台词 / 旁白', exact: true });
async function saved() { await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存')); }
async function openEditor() {
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  await page.locator(`[data-node-id="${node.id}"] .node-header`).click();
  await voiceButton.waitFor();
}
async function selectOption(label, value) {
  await page.getByRole('button', { name: label, exact: true }).click();
  await page.getByRole('listbox', { name: label, exact: true }).locator(`[data-value="${value}"]`).click();
}
const captureDir = fileURLToPath(new URL('../../docs/_shots/speech-studio', import.meta.url));
fs.mkdirSync(captureDir, { recursive: true });
async function assertAnchored() {
  const card = await page.locator(`[data-node-id="${node.id}"]`).boundingBox();
  const editor = await page.locator('.creation-composer').boundingBox();
  assert.ok(Math.abs(editor.y - card.y - card.height - 14) < 1, 'editor stays below its node');
  assert.ok(Math.abs(editor.x + editor.width / 2 - card.x - card.width / 2) < 1, 'editor stays centered on its node');
  return editor;
}
async function captureStack(filename) {
  const editor = await assertAnchored();
  const card = await page.locator(`[data-node-id="${node.id}"]`).boundingBox();
  const x = Math.max(0, Math.min(editor.x, card.x) - 12), y = Math.max(0, card.y - 12);
  await page.screenshot({ path: path.join(captureDir, filename), clip: {
    x, y, width: Math.max(editor.x + editor.width, card.x + card.width) - x + 12,
    height: editor.y + editor.height - y + 12,
  } });
}
try {
  await page.goto((process.env.E2E_URL || 'http://127.0.0.1:5173') + `/?browser-test=speech#project=${taskId}&view=storyboard`);
  await page.locator('.flow-node').waitFor();
  await openEditor();
  await voiceButton.click();
  await dialog.waitFor();
  assert.equal(await dialog.getByRole('article').count(), 20, 'voice catalog is paginated');
  await dialog.getByRole('button', { name: '下一页音色' }).click();
  assert.equal(await dialog.getByRole('article').count(), 6);
  await dialog.getByRole('button', { name: '上一页音色' }).click();
  await dialog.getByRole('button', { name: '收藏御姐', exact: true }).click();
  await dialog.getByRole('button', { name: '收藏音色', exact: true }).click();
  assert.equal(await dialog.getByRole('article').count(), 1);
  assert.equal(await dialog.getByRole('button', { name: '我的音色', exact: true }).count(), 0);
  await dialog.getByRole('button', { name: '全部音色', exact: true }).click();
  await dialog.getByRole('button', { name: '女声', exact: true }).click();
  await dialog.getByRole('searchbox', { name: '搜索音色' }).fill('温柔');
  assert.equal(await dialog.getByRole('article').count(), 1);
  await dialog.getByRole('button', { name: '试听甜美女性', exact: true }).click();
  await dialog.getByLabel('音色试听播放器').waitFor();
  assert.equal(previews.length, 1);
  assert.equal(previews[0].voice_id, 'sweet-girl');
  assert.equal(submissions.length, 0, 'auditions never generate a canvas material');
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await page.locator('.creation-composer').count(), 1);
  await voiceButton.click();
  await dialog.getByRole('searchbox', { name: '搜索音色' }).fill('');
  await dialog.getByRole('button', { name: '全部', exact: true }).click();
  await dialog.screenshot({ path: path.join(captureDir, 'minimax-voice-library.png') });
  await dialog.getByRole('button', { name: '选择御姐', exact: true }).click();
  await saved();
  assert.equal(graph.nodes[0].data.voiceId, 'mature-woman');
  await selectOption('语音模型', 'speech-2.8-turbo');
  const camera = await page.locator('.flow-world').getAttribute('style');
  const collapsed = await assertAnchored();
  await controlsButton.click();
  await page.getByRole('spinbutton', { name: '语速数值' }).waitFor();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('.flow-world').getAttribute('style'), camera, 'expanding controls preserves the entire viewport');
  const expanded = await assertAnchored();
  assert.equal(expanded.x, collapsed.x);
  assert.equal(expanded.y, collapsed.y, 'expansion never lifts the editor above its node');
  assert.ok(expanded.height > collapsed.height + 100, 'controls grow downward');
  for (const [label, value] of [['语速', '1.2'], ['声调', '-2'], ['音量', '1.5'], ['音高', '15'], ['强度', '-10'], ['音色', '20']]) {
    const field = page.getByRole('spinbutton', { name: label + '数值', exact: true });
    await field.fill(value); await field.press('Tab');
  }
  await selectOption('表达情绪', 'calm');
  await selectOption('朗读语言', 'Chinese');
  await page.getByRole('button', { name: '电话失真', exact: true }).click();
  await saved();
  assert.equal(graph.nodes[0].data.speechSettings.speed, 1.2);
  await page.locator('.creation-composer').screenshot({ path: path.join(captureDir, 'minimax-speech-controls.png') });
  await prompt.fill('你好世界');
  await prompt.evaluate(input => {
    const range = document.createRange(); range.setStart(input.firstChild, 2); range.collapse(true);
    window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
  });
  await page.getByRole('button', { name: '停顿', exact: true }).click();
  const pauseMenu = page.getByRole('dialog', { name: '设置停顿', exact: true });
  await pauseMenu.screenshot({ path: path.join(captureDir, 'pause-menu.png') });
  await pauseMenu.getByRole('button', { name: '0.5s', exact: true }).click();
  await saved(); assert.equal(graph.nodes[0].data.prompt, '你好<#0.5#>世界');
  await prompt.getByRole('button', { name: '编辑停顿 0.5 秒', exact: true }).click();
  await pauseMenu.getByRole('spinbutton', { name: '停顿秒数' }).fill('0.25');
  await pauseMenu.getByRole('button', { name: '应用停顿' }).click();
  await saved(); assert.equal(graph.nodes[0].data.prompt, '你好<#0.25#>世界');
  await page.getByRole('button', { name: '插入语气词', exact: true }).click();
  const expressionMenu = page.getByRole('dialog', { name: '选择语气词', exact: true });
  await expressionMenu.screenshot({ path: path.join(captureDir, 'expression-menu.png') });
  await expressionMenu.getByRole('button', { name: '轻笑', exact: true }).click();
  await saved();
  assert.equal(graph.nodes[0].data.prompt, '你好<#0.25#>(chuckle)世界');
  assert.equal(await prompt.locator('.speech-token').count(), 2);
  await prompt.getByRole('button', { name: '编辑语气词 (轻笑)', exact: true }).click();
  await expressionMenu.getByRole('button', { name: '咳嗽', exact: true }).click();
  await saved(); assert.equal(graph.nodes[0].data.prompt, '你好<#0.25#>(coughs)世界');
  await prompt.getByRole('button', { name: '编辑语气词 (咳嗽)', exact: true }).click();
  await expressionMenu.getByRole('button', { name: '删除标签', exact: true }).click();
  await saved(); assert.equal(graph.nodes[0].data.prompt, '你好<#0.25#>世界');
  await prompt.press('Control+z');
  await saved(); assert.equal(graph.nodes[0].data.prompt, '你好<#0.25#>(coughs)世界', 'undo restores the API marker');
  await prompt.press('Control+Shift+z');
  await saved(); assert.equal(graph.nodes[0].data.prompt, '你好<#0.25#>世界', 'redo removes the entire marker');
  await prompt.evaluate(input => {
    const range = document.createRange(); range.selectNodeContents(input);
    window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
    const data = new DataTransfer(); input.dispatchEvent(new ClipboardEvent('copy', { clipboardData: data, bubbles: true, cancelable: true }));
    window.testCopiedSpeech = data.getData('text/plain');
  });
  assert.equal(await page.evaluate(() => window.testCopiedSpeech), '你好<#0.25#>世界', 'copying preserves raw markers');
  await prompt.evaluate(input => {
    const data = new DataTransfer(); data.setData('text/plain', '非常不错(chuckle)<#0.25#>完美\n新的一天。');
    data.setData('text/html', '<img src="invalid" onerror="throw Error()">');
    input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  });
  await saved(); assert.equal(graph.nodes[0].data.prompt, '非常不错(chuckle)<#0.25#>完美\n新的一天。');
  assert.equal(await prompt.locator('img').count(), 0, 'paste accepts plain text only');
  await page.locator('.creation-composer').screenshot({ path: path.join(captureDir, 'speech-tags-editor.png') });
  await page.reload();
  await page.locator('.flow-node').waitFor();
  await openEditor();
  assert.equal(await prompt.locator('.speech-token').count(), 2, 'saved tags render again after reload');
  await voiceButton.click();
  await dialog.getByRole('button', { name: '收藏音色', exact: true }).click();
  assert.equal(await dialog.getByRole('article').count(), 1, 'favorites survive reload');
  await dialog.getByRole('button', { name: '刷新音色库', exact: true }).click();
  await dialog.getByRole('article').first().waitFor();
  assert.ok(catalogReads >= 3);
  await page.keyboard.press('Escape');
  await controlsButton.click();
  assert.equal(await page.getByRole('spinbutton', { name: '语速数值' }).inputValue(), '1.20', 'controls survive reload');
  await page.setViewportSize({ width: 420, height: 900 });
  await voiceButton.click();
  const bounds = await dialog.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 420 && bounds.y + bounds.height <= 900);
  await dialog.screenshot({ path: path.join(captureDir, 'mobile-voice-library.png') });
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: '切换白天模式', exact: true }).click();
  await page.setViewportSize({ width: 420, height: 900 });
  await page.waitForFunction(() => {
    const composer = document.querySelector('.creation-composer')?.getBoundingClientRect();
    const board = document.querySelector('.flow-board')?.getBoundingClientRect();
    return composer && board && composer.top >= board.top && composer.bottom <= board.bottom - 75;
  });
  await assertAnchored();
  await page.locator('.creation-composer').screenshot({ path: path.join(captureDir, 'mobile-speech-controls.png') });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('.creation-composer').screenshot({ path: path.join(captureDir, 'day-speech-controls.png') });
  assert.equal(submissions.length, 0, 'searching and adjusting never start generation');
  const submitted = page.waitForResponse(response => response.url().endsWith('/media-jobs') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '生成语音', exact: true }).click();
  await submitted;
  await page.waitForFunction(() => document.querySelector('.media-composer [role="status"]'));
  assert.equal(submissions.length, 1);
  assert.deepEqual(submissions[0].speech_settings, { ...SPEECH_DEFAULTS, model: 'speech-2.8-turbo', speed: 1.2, pitch: -2, volume: 1.5, effect_pitch: 15, intensity: -10, timbre: 20, emotion: 'calm', language: 'Chinese', sound_effect: 'lofi_telephone' });
  assert.equal(await voiceButton.isDisabled(), true);
  finishGeneration = true;
  const resultPlayer = page.locator(`[data-node-id="${node.id}"] .audio-waveform-player`);
  await resultPlayer.locator('.waveform-svg').waitFor();
  await resultPlayer.locator('audio').evaluate(audio => audio.readyState >= 3 ? undefined : new Promise(resolve => audio.addEventListener('canplay', resolve, { once: true })));
  assert.equal(await resultPlayer.locator('.waveform-bars rect').count(), 96);
  const progressSlider = resultPlayer.getByRole('slider', { name: '音频播放进度' });
  const progressBounds = await progressSlider.boundingBox();
  await progressSlider.click({ position: { x: progressBounds.width * 0.48, y: progressBounds.height / 2 } });
  await page.waitForFunction(() => document.querySelector('.flow-node audio')?.currentTime > 1);
  await resultPlayer.getByRole('button', { name: '切换播放速度' }).click();
  assert.equal(await resultPlayer.locator('audio').evaluate(audio => audio.playbackRate), 1.25);
  await resultPlayer.getByRole('button', { name: '播放音频', exact: true }).click();
  await resultPlayer.getByRole('button', { name: '暂停音频', exact: true }).waitFor();
  await resultPlayer.getByRole('button', { name: '暂停音频', exact: true }).click();
  assert.equal(await resultPlayer.locator('audio').evaluate(audio => audio.paused), true);
  assert.ok(await resultPlayer.getByRole('link', { name: '下载音频' }).getAttribute('download'));
  assert.equal(await page.locator('.creation-composer .audio-waveform-player, .creation-composer audio').count(), 0, 'the result has a single player on the node');
  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await controlsButton.click();
  await page.waitForFunction(() => {
    const editor = document.querySelector('.creation-composer')?.getBoundingClientRect();
    const generate = document.querySelector('.media-generate')?.getBoundingClientRect();
    return editor && generate && generate.bottom < editor.bottom - 25;
  });
  const collapsedResult = await assertAnchored();
  const resultCamera = await page.locator('.flow-world').getAttribute('style');
  await captureStack('anchored-speech-collapsed.png');
  await controlsButton.click();
  await page.getByRole('spinbutton', { name: '语速数值' }).waitFor();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const expandedResult = await assertAnchored();
  assert.equal(expandedResult.x, collapsedResult.x);
  assert.equal(expandedResult.y, collapsedResult.y);
  assert.equal(await page.locator('.flow-world').getAttribute('style'), resultCamera);
  await captureStack('anchored-speech-expanded.png');
  await page.getByRole('button', { name: '关闭语音输入框', exact: true }).click();
  await page.locator('.flow-node .waveform-svg').waitFor();
  await page.locator('.flow-node').screenshot({ path: path.join(captureDir, 'generated-audio-node.png') });
  assert.deepEqual(unexpected, []);
  assert.deepEqual(errors, []);
  console.log('PASS: stable camera, searchable voices, favorites, auditions, editable speech tags and clipboard, saved controls, actual waveform decoding, playback and seeking, mobile and theme layouts.');
} finally { await context.close(); await browser.close(); }

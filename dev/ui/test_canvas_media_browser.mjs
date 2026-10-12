// Every API/media request is intercepted; no user data or remote model calls.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { emptyWorkflow, makeNode, makeShot } from '../../web/src/workflowGraph.js';
import { serveAudioFixture } from './audio_fixture.mjs';
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const taskId = '123456abcdef', base = '/api/tasks/' + taskId;
let graph = emptyWorkflow();
const target = makeShot(500, 100, 1);
const importedImage = makeNode('material', 80, 100, { title: '已导入图片', materialKind: 'image', materialName: '已导入图片', materialFile: 'imported.png' });
const importedVideo = makeNode('footage', 80, 440, { title: '已导入视频', versions: [{ id: 'fixture-video', status: 'succeeded', file: 'fixture.mp4', url: '/files/' + taskId + '/video/fixture.mp4', width: 1280, height: 720, actual_duration: 10 }] });
graph.nodes.push(target, importedImage, importedVideo);
const project = { title: '多类型画布测试', style: '作品原有风格', aspect_ratio: '16:9', characters: [], assets: [{ name: '已导入图片', kind: 'image', images: ['imported.png'] }] };
const jobs = new Map(), submissions = [], uploads = [], errors = [], unexpected = [];
let finishJobs = false;
page.on('pageerror', error => errors.push(error.message));
await page.route('**/files/**', route => new URL(route.request().url()).pathname.endsWith('.mp3') ? serveAudioFixture(route) : route.fulfill({
  contentType: 'image/png',
  body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5EsAAAAASUVORK5CYII=', 'base64'),
}));
await page.route('**/api/**', async route => {
  const request = route.request(), path = new URL(request.url()).pathname;
  const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  if (path === '/api/config') return json({ video_backend: 'muse', image_api_key: 'fixture', text_api_key: 'fixture', audio_provider: 'edge' });
  if (path === '/api/video-capabilities') return json({ controls_version: 1, video_sources_version: 1 });
  if (path === '/api/audio/voices') return json({ provider: 'edge', source: 'preset', items: [{ id: 'voice-a', cn: '温柔女声', locale: '普通话', gender: 'female' }, { id: 'voice-b', cn: '沉稳男声', locale: '普通话', gender: 'male' }] });
  if (path === '/api/tasks') return json([]);
  if (path === base) return json({ task_id: taskId, status: 'succeeded', project, shots: [], blocks: [] });
  if (path.endsWith('/segments')) return json({ items: [] });
  if (path.endsWith('/workflow')) {
    if (request.method() === 'GET') return json(graph);
    const document = request.postDataJSON();
    assert.equal(document.revision, graph.revision);
    graph = document; graph.revision++;
    return json({ ok: true, revision: graph.revision });
  }
  if (path.endsWith('/media-jobs')) {
    const body = request.postDataJSON();
    submissions.push(body);
    const kind = body.kind === 'image' && body.image_mode !== 'native' ? body.image_mode : body.kind;
    const job = { job_id: body.request_id, asset_name: body.title + '_' + kind + '_fixture', status: 'running', input: body, kind };
    jobs.set(body.request_id, job);
    const entries = kind === 'character' ? project.characters : project.assets;
    if (!entries.some(a => a.name === job.asset_name && (kind === 'character' || a.kind === kind)))
      entries.push({ name: job.asset_name, kind, images: [] });
    return json(job);
  }
  if (path.includes('/media-jobs/')) {
    const job = jobs.get(path.split('/').at(-1));
    assert.ok(job);
    if (finishJobs && job.status === 'running') {
      const input = job.input;
      const entries = job.kind === 'character' ? project.characters : project.assets;
      const results = Array.from({ length: input.count || 1 }, (_, index) => {
        const name = job.asset_name + (index ? '_candidate' + (index + 1) : '');
        const file = (input.kind === 'image' ? 'img_' : 'snd_') + name + (['character', 'prop'].includes(job.kind) ? '_sheet' : '') + '.' + (input.kind === 'image' ? 'png' : 'mp3');
        let asset = entries.find(a => a.name === name && (job.kind === 'character' || a.kind === job.kind));
        if (!asset) { asset = { name, kind: job.kind, images: [] }; entries.push(asset); }
        asset.images = [file]; asset.version = '123abc';
        if (job.kind === 'prop') { asset.sheet = file; asset.images = []; }
        if (job.kind === 'character') { asset.sheet = file; asset.image_path = file.replace('_sheet', '_portrait'); asset.images = [asset.image_path]; }
        return { asset_name: name, kind: job.kind, file, version: '123abc', index: entries.indexOf(asset) };
      });
      Object.assign(job, { status: 'succeeded', result: { ...results[0], results } });
    }
    return json(job);
  }
  if (path.endsWith('/materials/upload')) {
    const kind = new URL(request.url()).searchParams.get('kind');
    uploads.push({ kind, ...request.postDataJSON() });
    const entry = { name: '上传的音频', kind, images: ['snd_uploaded.mp3'] };
    project.assets.push(entry);
    return json({ name: entry.name, entry });
  }
  unexpected.push(request.method() + ' ' + path);
  return route.fulfill({ status: 404, body: '' });
});
async function saved() {
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
}
async function add(kind) {
  await page.getByRole('button', { name: '添加节点', exact: true }).click();
  await page.getByRole('menuitem', { name: new RegExp('^' + kind) }).click();
}
const nodeSelector = id => '[data-node-id="' + id + '"]';
async function chooseNode(id) {
  const close = page.getByRole('button', { name: '关闭视频输入框', exact: true });
  if (await close.count()) await close.click();
  const creationClose = page.getByRole('button', { name: /^关闭(图片|语音|文本)输入框$/ });
  if (await creationClose.count()) await creationClose.click();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  await page.locator(nodeSelector(id) + ' .node-header').click();
}
async function inlineEditor(id, selector = '.creation-composer') {
  await page.waitForFunction(({ id, selector }) => {
    const editor = document.querySelector(selector)?.getBoundingClientRect();
    const node = document.querySelector('[data-node-id="' + id + '"]')?.getBoundingClientRect();
    const board = document.querySelector('.flow-board')?.getBoundingClientRect();
    return editor && node && board &&
      editor.left >= board.left && editor.right <= board.right && editor.bottom <= board.bottom - 75;
  }, { id, selector });
  assert.equal(await page.locator('.flow-inspector').count(), 0);
}
const settingsButton = page.getByRole('button', { name: '图片生成设置', exact: true });
const settingsPanel = page.getByRole('dialog', { name: '图片生成设置', exact: true });
async function chooseRatio(value) {
  await settingsButton.click();
  await settingsPanel.waitFor();
  await settingsPanel.getByRole('radio', { name: value, exact: true }).click();
  await page.getByRole('button', { name: '关闭图片生成设置', exact: true }).click();
  await settingsPanel.waitFor({ state: 'hidden' });
}
async function chooseCount(value) {
  await settingsButton.click();
  await settingsPanel.waitFor();
  await settingsPanel.getByRole('radio', { name: value + ' 张', exact: true }).click();
  await page.getByRole('button', { name: '关闭图片生成设置', exact: true }).click();
  await settingsPanel.waitFor({ state: 'hidden' });
}
const modes = page.getByRole('button', { name: '图片类型', exact: true });
const typeMenu = page.getByRole('listbox', { name: '图片类型', exact: true });
async function chooseType(value) {
  await modes.click();
  await typeMenu.locator(`[data-value="${value}"]`).click();
  await typeMenu.waitFor({ state: 'hidden' });
}
try {
  await page.goto((process.env.E2E_URL || 'http://127.0.0.1:5173') + '/?browser-test=media#project=' + taskId + '&view=storyboard');
  await page.locator('.flow-node').first().waitFor();
  await page.getByRole('button', { name: '添加节点', exact: true }).click();
  assert.equal(await page.getByRole('menuitem').count(), 7);
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('menu').count(), 0);
  for (const node of [importedImage, importedVideo]) {
    await chooseNode(node.id);
    assert.equal(await page.locator('.flow-inspector, .creation-composer, .video-composer').count(), 0, 'imported media selects without opening an editor');
    assert.equal(await page.locator(nodeSelector(node.id) + '.selected').count(), 1);
  }
  await add('文本');
  const noteId = await page.locator('.flow-node.note').last().getAttribute('data-node-id');
  await inlineEditor(noteId);
  await page.getByRole('textbox', { name: '创作说明' }).fill('这一幕发生在黎明，主角走向远处。');
  await saved();
  assert.equal(graph.nodes.filter(n => n.type === 'note').length, 1);
  assert.equal(submissions.length, 0, 'adding a node never starts generation');

  await add('图片');
  const imageId = await page.locator('.flow-node.material').last().getAttribute('data-node-id');
  await inlineEditor(imageId);
  await page.getByRole('textbox', { name: '画面描述', exact: true }).fill('水彩风格的山间小路，晨光透过树林。');
  await chooseRatio('9:16');
  assert.equal(await modes.getAttribute('data-value'), 'native');
  await modes.press('ArrowDown');
  await typeMenu.waitFor();
  assert.deepEqual(await typeMenu.getByRole('option').evaluateAll(options => options.map(option => option.getAttribute('aria-label'))), ['原生', '人物三视图', '场景图', '道具图']);
  assert.equal(await typeMenu.locator('[aria-selected="true"]').getAttribute('data-value'), 'native');
  await page.keyboard.press('ArrowDown');
  assert.equal(await typeMenu.locator('[data-value="character"]').evaluate(el => el === document.activeElement), true);
  assert.equal(await modes.getAttribute('data-value'), 'native', 'browsing types keeps the current choice until confirmed');
  await page.keyboard.press('Enter');
  await typeMenu.waitFor({ state: 'hidden' });
  assert.equal(await modes.getAttribute('data-value'), 'character');
  assert.equal(await modes.evaluate(el => el === document.activeElement), true, 'choosing a type returns focus');
  await modes.click();
  await typeMenu.waitFor();
  await page.keyboard.press('End');
  assert.equal(await typeMenu.locator('[data-value="prop"]').evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Home');
  assert.equal(await typeMenu.locator('[data-value="native"]').evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Escape');
  await typeMenu.waitFor({ state: 'hidden' });
  assert.equal(await modes.evaluate(el => el === document.activeElement), true);
  assert.equal(await page.locator('.creation-composer').count(), 1, 'Escape only closes the type menu');
  await modes.click();
  await typeMenu.waitFor();
  await page.keyboard.press('Tab');
  await typeMenu.waitFor({ state: 'hidden' });
  assert.equal(await page.locator('.media-generate').evaluate(el => el === document.activeElement), true, 'Tab continues to the next composer control');
  await modes.click();
  await typeMenu.waitFor();
  await page.getByRole('textbox', { name: '画面描述', exact: true }).click({ position: { x: 4, y: 4 } });
  await typeMenu.waitFor({ state: 'hidden' });
  await chooseType('native');
  for (const mode of ['character', 'prop', 'scene', 'native']) {
    await chooseType(mode);
    assert.equal(await page.getByRole('textbox', { name: '画面描述', exact: true }).inputValue(), '水彩风格的山间小路，晨光透过树林。');
    const sheet = ['character', 'prop'].includes(mode);
    assert.ok((await settingsButton.textContent()).includes(sheet ? '16:9' : '9:16'));
    await settingsButton.click();
    await settingsPanel.waitFor();
    assert.equal(await settingsPanel.getByRole('radio', { name: '9:16', exact: true }).isDisabled(), sheet);
    assert.equal(await settingsPanel.getByRole('radio', { name: sheet ? '16:9' : '9:16', exact: true }).getAttribute('aria-checked'), 'true');
    assert.equal(await settingsPanel.getByRole('radio', { name: '1 张', exact: true }).getAttribute('aria-checked'), 'true');
    assert.deepEqual((await settingsPanel.getByRole('radiogroup', { name: '生成数量', exact: true }).getByRole('radio').allTextContents()).map(text => text.trim()), ['1 张', '2 张', '4 张']);
    await page.getByRole('button', { name: '关闭图片生成设置', exact: true }).click();
  }
  assert.equal(submissions.length, 0, 'switching image types never starts generation');
  assert.equal(await page.getByRole('button', { name: '选择图片风格', exact: true }).count(), 0);
  const footerOrder = await page.locator('.media-controls').evaluate(el => ({
    prompt: el.parentElement.querySelector('textarea').getBoundingClientRect().bottom,
    model: el.querySelector('.model-choice').getBoundingClientRect(),
    ratio: el.querySelector('.settings-summary').getBoundingClientRect(),
    type: el.querySelector('.mode-choice').getBoundingClientRect(),
  }));
  assert.ok(footerOrder.type.y >= footerOrder.prompt && Math.abs(footerOrder.type.y - footerOrder.model.y) < 6, 'image types live below the description, beside the model');
  assert.ok(footerOrder.type.x > footerOrder.ratio.x && footerOrder.ratio.x > footerOrder.model.x);
  await settingsButton.click();
  await settingsPanel.waitFor();
  await settingsPanel.getByRole('radio', { name: '9:16', exact: true }).press('ArrowRight');
  assert.equal(await settingsPanel.getByRole('radio', { name: '16:9', exact: true }).getAttribute('aria-checked'), 'true', 'arrow keys select a ratio');
  await settingsPanel.getByRole('radio', { name: '16:9', exact: true }).press('ArrowLeft');
  await settingsPanel.getByRole('radio', { name: '9:16', exact: true }).press('Escape');
  await settingsPanel.waitFor({ state: 'hidden' });
  assert.equal(await settingsButton.evaluate(el => el === document.activeElement), true, 'Escape returns focus to the settings trigger');
  assert.equal(await page.locator('.creation-composer').count(), 1, 'Escape only closes the ratio panel');
  await settingsButton.click();
  await settingsPanel.waitFor();
  await page.getByRole('textbox', { name: '画面描述', exact: true }).click({ position: { x: 4, y: 4 } });
  await settingsPanel.waitFor({ state: 'hidden' });
  await chooseCount(4);
  await settingsButton.click();
  await settingsPanel.waitFor();
  await settingsPanel.getByRole('radio', { name: '4 张', exact: true }).press('ArrowLeft');
  assert.equal(await settingsPanel.getByRole('radio', { name: '2 张', exact: true }).getAttribute('aria-checked'), 'true');
  await page.getByRole('button', { name: '关闭图片生成设置', exact: true }).click();
  await saved();
  // An existing saved node may still carry the retired style field. New submissions must ignore it.
  graph.nodes.find(n => n.id === imageId).data.imageStyle = { id: 'watercolor', label: '旧风格', prompt: 'Must never be appended to new requests' };
  await page.reload();
  await page.locator('.flow-node').first().waitFor();
  await chooseNode(imageId);
  assert.equal(submissions.length, 0, 'changing settings never generates');
  assert.ok((await settingsButton.textContent()).includes('2 张'), 'count survives refresh before submission');
  await page.getByRole('button', { name: '生成图片', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.media-composer [role="status"]'));
  assert.equal(await modes.isDisabled(), true, 'types cannot change during generation');
  await saved();
  assert.equal(submissions[0].kind, 'image');
  assert.equal(submissions[0].image_mode, 'native');
  assert.equal(submissions[0].ratio, '9:16');
  assert.equal(submissions[0].count, 2);
  assert.equal(submissions[0].style_prompt, '', 'retired styles are never sent with new generations');
  assert.equal(submissions[0].prompt, '水彩风格的山间小路，晨光透过树林。');
  assert.equal(graph.nodes.find(n => n.id === imageId).data.status, 'running');
  // Reload while busy: resume the original job, never submit again.
  await page.reload();
  await page.locator('.flow-node').first().waitFor();
  finishJobs = true;
  await page.waitForFunction(() => document.querySelectorAll('.generation-badge').length === 0);
  await saved();
  assert.equal(submissions.length, 1);
  assert.equal(graph.nodes.find(n => n.id === imageId).data.status, 'succeeded');
  await chooseNode(imageId);
  assert.ok((await settingsButton.textContent()).includes('9:16'), 'ratio survives refresh');
  await page.getByRole('link', { name: '下载图片', exact: true }).waitFor();
  assert.ok((await page.getByRole('link', { name: '下载图片' }).getAttribute('href')).includes('/assets/'));
  await inlineEditor(imageId);
  const variants = graph.nodes.find(n => n.id === imageId).data.mediaVariants;
  assert.equal(variants.length, 2);
  await page.getByRole('button', { name: '使用第 2 张图片', exact: true }).click();
  await saved();
  assert.equal(graph.nodes.find(n => n.id === imageId).data.materialName, variants[1].asset_name);
  assert.ok((await page.locator(nodeSelector(imageId) + ' img').getAttribute('src')).includes(encodeURIComponent(variants[1].file)));
  assert.ok((await page.getByRole('link', { name: '下载图片', exact: true }).getAttribute('href')).includes(encodeURIComponent(variants[1].file)));
  await page.reload();
  await page.locator('.flow-node').first().waitFor();
  await chooseNode(imageId);
  assert.equal(await page.getByRole('button', { name: '使用第 2 张图片', exact: true }).getAttribute('aria-pressed'), 'true', 'selected candidate survives refresh');
  assert.equal(submissions.length, 1, 'switching candidates never generates again');
  await page.getByRole('button', { name: '关闭图片输入框', exact: true }).click();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  await page.locator(nodeSelector(imageId)).getByRole('button', { name: '从此节点开始连接', exact: true }).click();
  await page.locator(nodeSelector(target.id)).getByRole('button', { name: '使用此视频接收参考素材', exact: true }).click();
  await saved();
  assert.ok(graph.edges.some(e => e.source === imageId && e.target === target.id), 'image can reference a video');
  await chooseNode(target.id);
  assert.ok((await page.locator('.video-composer img').evaluateAll(images => images.map(img => img.getAttribute('src')))).some(src => src.includes(encodeURIComponent(variants[1].file))), 'video reference follows the selected candidate');

  const originalImage = graph.nodes.find(n => n.id === imageId).data.materialFile;
  for (const mode of ['character', 'scene', 'prop']) {
    await chooseNode(imageId);
    const previousFile = graph.nodes.find(n => n.id === imageId).data.materialFile;
    await chooseType(mode);
    await chooseCount(1);
    await saved();
    assert.equal(graph.nodes.find(n => n.id === imageId).data.materialFile, previousFile, 'switching type preserves the visible previous output');
    await page.getByRole('button', { name: '重新生成', exact: true }).click();
    await page.waitForFunction(({ id, kind }) => {
      const node = document.querySelector('[data-node-id="' + id + '"]');
      return node && !node.querySelector('.generation-badge') && node.querySelector('img')?.src.includes('_' + kind + '_fixture');
    }, { id: imageId, kind: mode });
    await saved();
    const node = graph.nodes.find(n => n.id === imageId);
    assert.equal(submissions.at(-1).image_mode, mode);
    assert.equal(submissions.at(-1).count, 1);
    assert.equal(submissions.at(-1).style_prompt, '');
    assert.equal(submissions.at(-1).ratio, mode === 'scene' ? '9:16' : '16:9');
    assert.equal(node.data.materialKind, mode);
    assert.equal(node.data.prompt, '水彩风格的山间小路，晨光透过树林。');
    await inlineEditor(imageId);
    if (mode === 'character') {
      assert.ok((await page.locator(nodeSelector(imageId) + ' img').getAttribute('src')).includes('_sheet.png'), 'creator previews the character sheet');
      assert.ok((await page.getByRole('link', { name: '下载图片', exact: true }).getAttribute('href')).includes('/characters/'));
      assert.ok((await page.locator('.image-mode-hint').textContent()).includes('从同张设定图提取'));
      await page.reload();
      await page.locator('.flow-node').first().waitFor();
      await chooseNode(imageId);
      assert.equal(await modes.getAttribute('data-value'), 'character');
      assert.ok((await page.locator(nodeSelector(imageId) + ' img').getAttribute('src')).includes('_sheet.png'), 'sheet preview survives refresh');
      await chooseNode(target.id);
      assert.ok((await page.locator('.video-composer img').evaluateAll(images => images.map(img => img.getAttribute('src')))).some(src => src.includes('_portrait.png')), 'Muse video reference uses the single-person image');
    }
  }
  assert.ok(project.assets.some(asset => asset.kind === 'image' && asset.images[0] === originalImage), 'original native image stays in the library');
  assert.equal(project.characters.length, 1);
  assert.equal(project.assets.filter(a => ['scene', 'prop'].includes(a.kind)).length, 2);
  assert.equal(submissions.length, 4);

  await add('语音');
  const audioId = await page.locator('.flow-node.material').last().getAttribute('data-node-id');
  await inlineEditor(audioId);
  await page.getByRole('textbox', { name: '台词 / 旁白', exact: true }).fill('清晨，新的故事开始了。');
  await page.getByRole('button', { name: '音色', exact: true }).click();
  await page.getByRole('button', { name: '选择沉稳男声', exact: true }).click();
  await page.getByRole('button', { name: '生成语音', exact: true }).click();
  await page.locator(nodeSelector(audioId)).getByRole('link', { name: '下载音频', exact: true }).waitFor();
  await saved();
  assert.equal(submissions.at(-1).kind, 'audio');
  assert.equal(submissions.at(-1).style_prompt, '');
  assert.equal(submissions.at(-1).voice_id, 'voice-b');
  assert.equal(submissions.at(-1).prompt, '清晨，新的故事开始了。');
  const audio = graph.nodes.find(n => n.data.creationKind === 'audio');
  assert.equal(audio.data.status, 'succeeded');
  assert.equal(await page.locator(nodeSelector(audio.id) + ' .output-port').count(), 0);
  await page.locator(nodeSelector(audio.id) + ' .waveform-svg').waitFor();
  assert.equal(await page.locator('.creation-composer .waveform-svg').count(), 0);
  await page.reload();
  await page.locator('.flow-node').first().waitFor();
  await chooseNode(audio.id);
  await inlineEditor(audio.id);
  await page.locator(nodeSelector(audio.id)).getByRole('link', { name: '下载音频', exact: true }).waitFor();
  assert.equal(submissions.length, 5, 'audio survives reload without another generation');

  await page.getByRole('button', { name: '添加节点', exact: true }).click();
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('menuitem', { name: /^上传文件/ }).click();
  await (await chooser).setFiles({ name: 'narration.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('fixture audio') });
  await page.getByText('素材已上传并加入画布', { exact: true }).waitFor();
  await saved();
  assert.equal(uploads[0].kind, 'audio');
  assert.equal(graph.nodes.filter(n => n.data.materialKind === 'audio').length, 2);
  assert.equal(graph.edges.length, 1);
  const uploadedAudio = graph.nodes.find(n => n.data.materialName === '上传的音频');
  await chooseNode(uploadedAudio.id);
  assert.equal(await page.locator('.flow-inspector, .creation-composer, .video-composer').count(), 0);
  await chooseNode(target.id);
  await inlineEditor(target.id, '.video-composer');
  await page.getByRole('button', { name: '关闭视频输入框', exact: true }).click();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  const imageHeader = await page.locator(nodeSelector(imageId) + ' .node-header').boundingBox();
  await page.mouse.move(imageHeader.x + 20, imageHeader.y + imageHeader.height / 2);
  await page.mouse.down();
  await page.locator('.creation-composer').waitFor();
  await page.mouse.up();
  await inlineEditor(imageId);
  await page.setViewportSize({ width: 420, height: 900 });
  await chooseNode(imageId);
  await inlineEditor(imageId);
  await settingsButton.click();
  await settingsPanel.waitFor();
  const mobileDialog = await settingsPanel.boundingBox();
  assert.ok(mobileDialog.x >= 0 && mobileDialog.x + mobileDialog.width <= 420 && mobileDialog.y >= 0 && mobileDialog.y + mobileDialog.height <= 900, 'mobile ratio panel fits viewport');
  await page.getByRole('button', { name: '关闭图片生成设置', exact: true }).click();
  await modes.click();
  await typeMenu.waitFor();
  const mobileTypes = await typeMenu.locator('..').boundingBox();
  assert.ok(mobileTypes.x >= 12 && mobileTypes.x + mobileTypes.width <= 408 && mobileTypes.y >= 12 && mobileTypes.y + mobileTypes.height <= 888, 'mobile type menu fits viewport without being clipped by the editor');
  await typeMenu.locator('[data-value="native"]').click();
  await chooseRatio('1:1');
  await chooseCount(4);
  await saved();
  assert.equal(graph.nodes.find(n => n.id === imageId).data.ratio, '1:1');
  assert.equal(graph.nodes.find(n => n.id === imageId).data.imageCount, 4);
  await inlineEditor(imageId);
  const imageSubmit = await page.getByRole('button', { name: '重新生成', exact: true }).boundingBox();
  const imageComposer = await page.locator('.creation-composer').boundingBox();
  assert.ok(imageSubmit.x >= imageComposer.x && imageSubmit.x + imageSubmit.width <= imageComposer.x + imageComposer.width && imageSubmit.y + imageSubmit.height <= imageComposer.y + imageComposer.height, 'mobile image mode controls stay inside the composer');
  const prompt = page.getByRole('textbox', { name: '画面描述', exact: true });
  assert.equal(await prompt.inputValue(), '水彩风格的山间小路，晨光透过树林。');
  await prompt.press('Escape');
  assert.equal(await page.locator('.creation-composer').count(), 0, 'Escape closes the editor without losing the prompt');
  await chooseNode(audio.id);
  await inlineEditor(audio.id);
  const submit = await page.getByRole('button', { name: '重新生成', exact: true }).boundingBox();
  const composer = await page.locator('.creation-composer').boundingBox();
  assert.ok(submit.x >= composer.x && submit.x + submit.width <= composer.x + composer.width && submit.y + submit.height <= composer.y + composer.height, 'mobile generation button stays inside the composer');
  assert.equal(submissions.length, 5, 'switching, resizing and closing editors do not generate');
  assert.deepEqual(unexpected, []);
  assert.deepEqual(errors, []);
  console.log('PASS: imported media selection, inline desktop/mobile editors, text/image/speech, ratios, progress recovery, downloads, image references and audio upload; no user project or models touched.');
} finally {
  await context.close();
  await browser.close();
}

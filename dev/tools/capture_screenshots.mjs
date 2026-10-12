// Capture the actual UI with intercepted demo data; no user projects or model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { emptyWorkflow, makeNode, makeShot } from '../../web/src/workflowGraph.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const captureDir = process.env.CAPTURE_DIR ? path.resolve(process.env.CAPTURE_DIR) : path.join(root, 'docs/images');
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const errors = [], unexpected = [], captured = [];
page.on('pageerror', error => errors.push(error.message));
const taskId = '123456abcdef', base = `/api/tasks/${taskId}`;
const url = process.env.E2E_URL || 'http://127.0.0.1:5173';
const images = {
  'morning.webp': 'inspiration-mountain.webp',
  'summer.webp': 'inspiration-summer.webp',
  'city.webp': 'inspiration-neon.webp',
};
const project = {
  title: '山海之间 · 创作练习',
  aspect_ratio: '16:9',
  characters: [],
  assets: [
    { name: '山海晨光', kind: 'image', anchor: '晨光穿过山间薄雾，水彩质感，开阔远景。', images: ['morning.webp'] },
    { name: '夏日海岸', kind: 'image', anchor: '夏日海岸与远处的山脉，柔和自然光。', images: ['summer.webp'] },
    { name: '雨夜街景', kind: 'scene', anchor: '雨夜霓虹映照街道，电影感远景。', images: ['city.webp'] },
    { name: '清晨旁白', kind: 'audio', images: ['narration.wav'] },
  ],
};
let graph = emptyWorkflow();
const image = makeNode('material', 100, 80, {
  title: '图片 01 · 山海晨光', creationKind: 'image', imageMode: 'native', imageCount: 2,
  materialKind: 'image', materialName: '山海晨光', materialFile: 'morning.webp', status: 'succeeded',
  prompt: '晨光穿过山间薄雾，远处群山与海岸相接。水彩质感，柔和自然光，开阔的电影构图。', ratio: '16:9',
  mediaVariants: [
    { asset_name: '山海晨光', kind: 'image', file: 'morning.webp', index: 0 },
    { asset_name: '夏日海岸', kind: 'image', file: 'summer.webp', index: 1 },
  ],
});
const shot = makeShot(630, 80);
shot.data.description = '晨光穿过薄雾，镜头缓慢推进。沿着山海之间的小路，开启新的旅程。';
const note = makeNode('note', 100, 410, {
  title: '故事与运镜', text: '第一幕：山海晨光\n\n远景建立环境，镜头缓慢推进。\n旁白：每一次出发，都从一个小小的念头开始。',
});
const audio = makeNode('material', 630, 410, {
  title: '语音 01 · 清晨旁白', creationKind: 'audio', materialKind: 'audio',
  materialName: '清晨旁白', materialFile: 'narration.wav', status: 'succeeded',
  prompt: '每一次出发，都从一个小小的念头开始。穿过晨光与薄雾，新的故事正在山海之间展开。', voiceId: 'voice-a',
});
graph.nodes.push(image, shot, note, audio);
graph.edges.push(
  { id: 'image-reference', source: image.id, target: shot.id },
  { id: 'story-context', source: note.id, target: shot.id },
);
const cfg = {
  video_backend: 'muse', video_workflow: 'ref2va', comfyui_url: 'http://127.0.0.1:8188',
  image_api_key: 'demo', text_api_key: 'demo', image_model: '图片模型', audio_provider: 'edge',
  video_megapixels: '0.5',
};

// A silent WAV keeps the demo audio controls usable without including personal recordings.
const wav = Buffer.alloc(44 + 32000);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(32000, 40);
await context.route('**/files/**', route => {
  const filename = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
  if (filename === 'narration.wav') return route.fulfill({ contentType: 'audio/wav', body: wav });
  assert.ok(images[filename], `Unknown demo image: ${filename}`);
  return route.fulfill({ contentType: 'image/webp', body: fs.readFileSync(path.join(root, 'web/public/images', images[filename])) });
});
await context.route('**/api/**', route => {
  const request = route.request(), pathname = new URL(request.url()).pathname;
  const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  if (pathname === '/api/config') return json(cfg);
  if (pathname === '/api/video-capabilities') return json({ controls_version: 1, video_sources_version: 1 });
  if (pathname === '/api/audio/voices') return json({ provider: cfg.audio_provider, source: 'synced', items: [
    { id: 'voice-a', cn: '温柔女声', locale: '普通话', gender: 'female' },
    { id: 'voice-b', cn: '沉稳男声', locale: '普通话', gender: 'male' },
  ] });
  if (pathname === '/api/muse/status') return json({ connected: false, browser_open: false, message: '尚未连接 Muse' });
  if (pathname === '/api/tasks') return json([]);
  if (pathname === base) return json({ task_id: taskId, status: 'succeeded', project, shots: [], blocks: [] });
  if (pathname.endsWith('/segments')) return json({ items: [] });
  if (pathname.endsWith('/workflow')) {
    if (request.method() === 'GET') return json(graph);
    graph = request.postDataJSON(); graph.revision++;
    return json({ ok: true, revision: graph.revision });
  }
  unexpected.push(`${request.method()} ${pathname}`);
  return route.fulfill({ status: 404, body: '' });
});

async function saved() {
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
}
async function capture(name) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => [...document.images].every(img => img.complete && img.naturalWidth > 0));
  await page.mouse.move(1420, 80);
  await page.waitForTimeout(200);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(captureDir, name + '.png'), animations: 'disabled' });
  captured.push(name);
}
async function select(node) {
  const close = page.getByRole('button', { name: /^关闭(图片|语音|文本|视频)输入框$/ });
  if (await close.count()) await close.click();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  await page.locator(`[data-node-id="${node.id}"] .node-header`).click();
  await page.locator(node.type === 'shot' ? '.video-composer' : '.creation-composer').waitFor();
  await saved();
}

async function chooseType(value) {
  await page.getByRole('button', { name: '图片类型', exact: true }).click();
  await page.getByRole('listbox', { name: '图片类型', exact: true }).locator(`[data-value="${value}"]`).click();
}
fs.mkdirSync(captureDir, { recursive: true });
try {
  await page.goto(`${url}/?browser-test=screenshots#project=${taskId}&view=storyboard`);
  await page.locator('.flow-node').first().waitFor();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  await saved();
  const videoClose = page.getByRole('button', { name: '关闭视频输入框', exact: true });
  if (await videoClose.count()) await videoClose.click();
  await capture('canvas-multimedia');
  await page.getByRole('button', { name: '添加节点', exact: true }).click();
  await page.getByRole('menu').waitFor();
  await capture('canvas-node-menu');
  await page.keyboard.press('Escape');

  await select(shot);
  await capture('video-composer');
  await page.getByRole('button', { name: '视频生成设置', exact: true }).click();
  await page.locator('.generation-settings').waitFor();
  await capture('muse-canvas-settings');
  await page.getByRole('button', { name: '关闭视频生成设置', exact: true }).click();
  cfg.video_backend = 'comfyui';
  await page.reload();
  await page.locator('.flow-node').first().waitFor();
  await select(shot);
  await page.getByRole('button', { name: '视频生成设置', exact: true }).click();
  await page.locator('.generation-settings').waitFor();
  await capture('video-generation-settings');
  await page.getByRole('button', { name: '关闭视频生成设置', exact: true }).click();
  cfg.video_backend = 'muse';
  await page.reload();
  await page.locator('.flow-node').first().waitFor();
  await select(image);
  await page.getByRole('button', { name: '使用第 2 张图片', exact: true }).waitFor();
  await capture('canvas-image-composer');
  await page.getByRole('button', { name: '图片生成设置', exact: true }).click();
  await page.getByRole('dialog', { name: '图片生成设置', exact: true }).waitFor();
  await capture('canvas-image-settings');
  await page.getByRole('button', { name: '关闭图片生成设置', exact: true }).click();
  await chooseType('character');
  await saved();
  await capture('canvas-character-mode');
  await page.getByRole('button', { name: '图片类型', exact: true }).click();
  await page.getByRole('listbox', { name: '图片类型', exact: true }).waitFor();
  await capture('canvas-image-type-menu');
  await page.locator('.creation-composer').screenshot({ path: path.join(captureDir, 'image-type-menu-detail.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  await chooseType('native');
  await select(note);
  await capture('canvas-text-composer');
  await select(audio);
  await page.getByRole('button', { name: '音色', exact: true }).click();
  await page.getByRole('button', { name: '选择温柔女声', exact: true }).click();
  await capture('canvas-speech-composer');
  await page.getByRole('button', { name: '音色', exact: true }).click();
  await page.getByRole('dialog', { name: '音色库', exact: true }).waitFor();
  await capture('canvas-voice-menu');
  await page.keyboard.press('Escape');

  await page.goto(`${url}/?browser-test=screenshots-studio#project=${taskId}&view=studio`);
  await page.locator('.mcard').first().waitFor();
  await capture('material-studio');
  await page.getByRole('button', { name: '切换白天模式', exact: true }).click();
  await capture('day-material-studio');

  await page.goto(`${url}/?browser-test=screenshots-mobile#project=${taskId}&view=storyboard`);
  await page.locator('.flow-node').first().waitFor();
  await select(image);
  await capture('day-image-composer');
  await page.getByRole('button', { name: '图片类型', exact: true }).click();
  await page.getByRole('listbox', { name: '图片类型', exact: true }).waitFor();
  await capture('day-image-type-menu');
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 420, height: 900 });
  await select(image);
  await capture('mobile-image-composer');
  await page.getByRole('button', { name: '图片类型', exact: true }).click();
  await page.getByRole('listbox', { name: '图片类型', exact: true }).waitFor();
  await capture('mobile-image-type-menu');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '图片生成设置', exact: true }).click();
  await page.getByRole('dialog', { name: '图片生成设置', exact: true }).waitFor();
  await capture('mobile-image-settings');
  await page.getByRole('button', { name: '关闭图片生成设置', exact: true }).click();
  await select(audio);
  await capture('mobile-speech-composer');
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log(`Captured ${captured.length} current UI screenshots in ${captureDir}; no live model calls.`);
} finally {
  await context.close();
  await browser.close();
}

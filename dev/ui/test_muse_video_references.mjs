// Browser fixtures are fully intercepted; never opens the user's Muse account.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { MuseBrowser, generationMessage, requestKey } from '../../server/muse_browser.mjs';
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const root = await fs.mkdtemp(path.join(os.tmpdir(), 'muse-video-fixture-'));
const outputs = path.join(root, 'outputs');
await fs.mkdir(outputs);
const image = path.join(outputs, 'reference.png'), video = path.join(outputs, 'reference.mp4');
await fs.writeFile(image, 'image'); await fs.writeFile(video, 'video');
const payload = { images: [image], videos: [video], prompt: '参考视频 1：上一段成片。接续奔跑动作。', duration: 5, generate_audio: false, ratio: '16:9', resolution: '720p' };
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ acceptDownloads: true });
  await context.route('**/*', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: `
    <div>镜序视频附件测试</div><input type="file" accept="image/*,video/*" multiple>
    <form onsubmit="return false"><textarea aria-label="消息"></textarea><button aria-label="发送">发送</button></form><div id="results"></div>
    <script>document.querySelector('button').onclick = () => {
      const input=document.querySelector('textarea'), text=input.value, token=text.match(/muse_[a-f0-9]{12}/)[0];
      document.body.dataset.message=text; document.body.dataset.files=JSON.stringify([...document.querySelector('input').files].map(f=>[f.name,f.type]));
      document.body.dataset.sends=Number(document.body.dataset.sends||0)+1; input.value='';
      const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob(['fixture-video'],{type:'video/mp4'})); a.download=token+'.mp4'; a.textContent=token+'.mp4'; document.querySelector('#results').append(a);
    };</script>` }));
  const page = await context.newPage(); await page.goto('https://muse.ai/');
  const driver = new MuseBrowser(root, path.join(root, 'downloads')); driver.context = context; driver.page = page; await driver.connect();
  await driver.generate({ ...payload, journal_path: path.join(outputs, 'task.muse.json'), raw_path: path.join(outputs, 'result.mp4'), timeout_s: 30 });
  assert.deepEqual(JSON.parse(await page.getAttribute('body', 'data-files')), [['reference.png', 'image/png'], ['reference.mp4', 'video/mp4']]);
  const sent = await page.getAttribute('body', 'data-message');
  assert.match(sent, /1 张图片、1 个视频附件/); assert.match(sent, /16:9/); assert.match(sent, /720P/);
  assert.match(sent, /读取视频附件的画面和动作/); assert.equal(await page.getAttribute('body', 'data-sends'), '1');
  const originalKey = await requestKey(payload);
  assert.notEqual(originalKey, await requestKey({ ...payload, ratio: '9:16' }));
  assert.notEqual(originalKey, await requestKey({ ...payload, resolution: '480p' }));
  await fs.writeFile(video, 'changed video'); assert.notEqual(originalKey, await requestKey(payload));
  const oldPayload = { images: [image], prompt: 'old', duration: 5, generate_audio: true };
  const oldKey = crypto.createHash('sha256').update(JSON.stringify({ images: [[path.resolve(image), crypto.createHash('sha256').update(await fs.readFile(image)).digest('hex')]], prompt: 'old', duration: 5, generate_audio: true, audio: '' })).digest('hex');
  assert.equal(await requestKey({ ...oldPayload, videos: [], ratio: 'auto', resolution: 'custom' }), oldKey);
  assert.equal(generationMessage(oldPayload, 'muse_123456abcdef'), generationMessage({ ...oldPayload, videos: [], ratio: 'auto', resolution: 'custom' }, 'muse_123456abcdef'));
  await page.locator('input').evaluate(el => { el.accept='image/*'; });
  await assert.rejects(driver.generate({ ...payload, journal_path: path.join(outputs, 'blocked.muse.json'), raw_path: path.join(outputs, 'blocked.mp4') }), /视频.*上传入口/);
  assert.equal(await page.getAttribute('body', 'data-sends'), '1');
  await page.locator('input').evaluate(el => { el.accept='video/*'; });
  await page.locator('textarea').fill('');
  await driver.generate({ ...payload, images: [], journal_path: path.join(outputs, 'video-only.muse.json'), raw_path: path.join(outputs, 'video-only.mp4'), timeout_s: 30 });
  assert.deepEqual(JSON.parse(await page.getAttribute('body', 'data-files')), [['reference.mp4', 'video/mp4']]);
  await context.close();
  console.log('PASS Muse uploads actual image/video attachments in order, forwards ratio/quality requirements, fingerprints video content and protects unsupported uploads/default journals');
} finally { await browser.close(); await fs.rm(root, { recursive: true, force: true }); }

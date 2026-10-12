// Muse settings and canvas end-to-end using intercepted local app requests only.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { emptyWorkflow, makeShot } from '../../web/src/workflowGraph.js';
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
process.chdir(fileURLToPath(new URL('../../', import.meta.url)));
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();
const errors = [], unexpected = [], starts = [], compositions = [], patches = [], recoveries = [], imports = [];
let complete = true;
let opened = false, connected = false, openCount = 0, remoteBusy = false;
let panelSize = {width:1100,height:720};
const panelInputs = [], panelResizes = [];
let inputDelay = 0, frameReads = 0, framesInFlight = 0, maxFramesInFlight = 0;
let probeStart = null, probeLength = 0, intermediateFrames = 0;
const panelImage = await page.evaluate(() => {
  const canvas=document.createElement('canvas');canvas.width=1100;canvas.height=720;
  const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,1100,720);
  ctx.fillStyle='#161a20';ctx.font='28px sans-serif';ctx.fillText('Muse · 主要聊天',40,60);ctx.fillText('旁聊 +',40,140);
  ctx.fillText('镜序画布',300,180);ctx.fillText('消息',300,640);
  return canvas.toDataURL('image/jpeg').split(',')[1];
});
const cfg = { video_backend: 'comfyui', video_workflow: 'ref2va', comfyui_url: '', text_api_key: '', audio_provider: 'edge', video_megapixels: '0.5', video_steps: '4', video_timeout_s: '3600', video_lora: '' };
const taskId = '123456abcdef';
let graph = emptyWorkflow();
const shot = makeShot(380, 50);
Object.assign(shot.data, { title: 'Muse 创作', description: '清晨的竹林里，一只熊猫缓缓散步。', duration: 5, ratio: '16:9', resolution: '720p', seed: 42, candidateCount: 4 });
graph.nodes.push(shot);
page.on('pageerror', (error) => errors.push(error.message));
await page.route('**/files/**', (route) => route.fulfill({ status: 404, body: '' }));
await page.route('**/api/**', async (route) => {
  const request = route.request(), pathname = new URL(request.url()).pathname;
  const json = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  if (pathname === '/api/config') {
    if (request.method() === 'GET') return json(cfg);
    const patch = request.postDataJSON(); patches.push(patch); Object.assign(cfg, patch);
    return json({ ok: true, config: cfg });
  }
  if (pathname === '/api/muse/status') return json({ connected, browser_open: opened, background: opened, busy: remoteBusy, message: connected ? 'Muse 当前聊天已连接 · 后台运行' : '尚未连接 Muse' });
  if (pathname === '/api/muse/open') { opened = true; openCount++; return json({ connected: false, background:true, browser_open: true, message: '请在面板登录后使用当前聊天' }); }
  if (pathname === '/api/muse/frame') {
    frameReads++; framesInFlight++; maxFramesInFlight = Math.max(maxFramesInFlight, framesInFlight);
    if (probeStart !== null) {
      const chars = panelInputs.slice(probeStart).filter(i => i.kind === 'text').reduce((sum, i) => sum + i.text.length, 0);
      if (chars > 0 && chars < probeLength) intermediateFrames++;
    }
    try {
      await new Promise(resolve => setTimeout(resolve, 25));
      return await json({frame_id:`fixture-${frameReads}`,image:panelImage,...panelSize,url:'https://muse.ai/',background:true,busy:remoteBusy});
    } finally { framesInFlight--; }
  }
  if (pathname === '/api/muse/resize') { panelSize=request.postDataJSON();panelResizes.push(panelSize);return json({ok:true}); }
  if (pathname === '/api/muse/input') {
    panelInputs.push(request.postDataJSON()); connected=false;
    if (inputDelay) await new Promise(resolve => setTimeout(resolve, inputDelay));
    return json({ok:true});
  }
  if (pathname === '/api/muse/connect') { connected = true; return json({ connected: true, background:true, browser_open: true, message: 'Muse 当前聊天已连接 · 后台运行' }); }
  if (pathname === '/api/tasks') return json([]);
  if (pathname === `/api/tasks/${taskId}`) return json({ task_id: taskId, status: 'succeeded', project: { title: 'Muse 接入测试', characters: [], assets: [] }, shots: [], blocks: [] });
  if (pathname.endsWith('/workflow')) {
    if (request.method() === 'GET') return json(graph);
    graph = request.postDataJSON(); graph.revision++; return json({ ok: true, revision: graph.revision });
  }
  if (pathname.endsWith('/segments')) return json({ items: [] });
  if (pathname === '/api/video-capabilities') return json({ controls_version: 1, video_sources_version: 1, video_references: true, candidate_counts: [1], text_to_video: true });
  if (pathname.endsWith('/compose')) { const body = request.postDataJSON(); compositions.push(body); return json({ prompt: body.description, video_backend: 'muse', video_workflow: 'muse', video_sources: body.video_sources }); }
  if (pathname.endsWith('/segment/video')) {
    starts.push(request.postDataJSON());
    return json({ job_id: 'abcdef123456', total: 1, candidates: [{ index: 0, seed: null, file: 'seg_abcdef123456.mp4' }] });
  }
  if (pathname.endsWith('/muse/recover')) { recoveries.push(pathname); complete = true; return json({job_id:'abcdef123456',status:'running'}); }
  if (pathname.endsWith('/muse/import')) {
    imports.push({ filename: new URL(request.url()).searchParams.get('filename'), bytes: request.postDataBuffer().length, type: request.headers()['content-type'] });
    complete = true; return json({job_id:'abcdef123456',status:'succeeded'});
  }
  if (pathname.startsWith('/api/segment-jobs/')) return json({ job_id: 'abcdef123456', status: complete ? 'succeeded' : 'interrupted', done: complete ? 1 : 0, total: 1, results: [{ index: 0, status: complete ? 'succeeded' : 'interrupted', file: 'seg_abcdef123456.mp4', video_url: complete ? `/files/${taskId}/segments/seg_abcdef123456.mp4` : '', actual_duration: recoveries.length ? 10.03 : 5, width: 64, height: 64 }] });
  unexpected.push(`${request.method()} ${pathname}`);
  return route.fulfill({ status: 404, body: '' });
});
try {
  await page.goto(`${process.env.E2E_URL || 'http://127.0.0.1:5173'}/?browser-test=muse#project=${taskId}&view=storyboard`);
  await page.locator('.workflow-editor').waitFor();
  await page.getByRole('button', { name: '打开服务配置', exact: true }).click();
  await page.locator('.settings-dialog input[value="muse"]').check();
  await page.getByText('尚未连接 Muse', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', {name:'使用当前聊天',exact:true}).count(), 0, 'chat selection belongs only to the Muse panel');
  assert.equal(openCount, 0, 'selecting the provider never opens a browser automatically');
  await fs.mkdir('docs/_shots', { recursive: true });
  await page.screenshot({ path: 'docs/_shots/muse-service-settings.png' });
  await page.getByRole('button', { name: '打开 Muse 面板', exact: true }).click();
  await page.locator('.muse-screen img').waitFor();
  assert.equal(await page.getByRole('button', {name:'使用当前聊天',exact:true}).count(), 1);
  assert.equal(panelResizes.length, 1);
  await page.locator('.muse-screen').click({position:{x:180,y:120}});
  await page.getByLabel('Muse 页面键盘输入').pressSequentially('hello');
  await page.getByLabel('Muse 页面键盘输入').evaluate(el => {
    el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
    el.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true,data:'中文旁聊'}));
  });
  await page.getByLabel('Muse 页面键盘输入').press('Backspace');
  await page.locator('.muse-screen').dispatchEvent('wheel',{clientX:200,clientY:300,deltaY:450,deltaX:0});
  await page.getByRole('button', { name: '使用当前聊天', exact: true }).last().click();
  await page.locator('.muse-panel').waitFor({state:'detached'});
  await page.getByText('Muse 当前聊天已连接 · 后台运行', { exact: true }).waitFor();
  assert.equal(panelInputs[0].kind,'click');
  assert.ok(panelInputs[0].x>0 && panelInputs[0].x<panelSize.width);
  assert.equal(panelInputs.filter(i=>i.kind==='text').map(i=>i.text).join(''),'hello中文旁聊');
  assert.equal(panelInputs.find(i=>i.kind==='key').key,'Backspace');
  assert.equal(panelInputs.at(-1).kind,'wheel');
  assert.equal(openCount,1);
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.settings-footer')?.textContent.includes('已保存'));
  assert.deepEqual(patches, [{ video_backend: 'muse' }]);
  await page.locator('.settings-dialog .icon-button').click();
  await page.locator('.flow-node.shot').click();
  await page.locator('.video-composer').waitFor();
  assert.equal(await page.getByLabel('提示词模式').count(), 0);
  await page.getByRole('button', { name: '视频生成设置', exact: true }).click();
  assert.equal(await page.locator('.generation-settings .ratio-grid button:not(:disabled)').count(), 7);
  assert.equal(await page.locator('.generation-settings .seed-settings').count(), 0);
  assert.equal(await page.locator('.generation-settings .setting-options').last().getByRole('button').count(), 1);
  await page.screenshot({ path: 'docs/_shots/muse-canvas-settings.png' });
  await page.getByRole('button', { name: '生成视频', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.flow-node.shot .node-state i.succeeded'));
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
  assert.equal(starts.length, 1);
  assert.equal(compositions[0].description, '清晨的竹林里，一只熊猫缓缓散步。');
  assert.equal(starts[0].expected_backend, 'muse');
  assert.equal(starts[0].candidate_count, 1);
  assert.deepEqual(starts[0].frames, []);
  for (const key of ['seed', 'megapixels']) assert.ok(!(key in starts[0]));
  assert.equal(starts[0].exact_duration, false);
  assert.equal(starts[0].ratio, '16:9');
  assert.equal(starts[0].resolution, '720p');
  const version = graph.nodes[0].data.versions[0];
  assert.equal(version.status, 'succeeded');
  assert.equal(version.backend, 'muse');
  assert.equal(version.file, 'seg_abcdef123456.mp4');
  complete = false;
  graph.nodes[0].data.status = 'interrupted';
  version.status = 'interrupted'; delete version.url; delete version.file;
  await page.reload();
  await page.locator('.flow-node.shot').click();
  await page.getByRole('button', { name: '取回 Muse 成片', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.flow-node.shot .node-state i.succeeded'));
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
  assert.equal(starts.length, 1); assert.equal(compositions.length, 1); assert.equal(recoveries.length, 1);
  assert.equal(graph.nodes[0].data.versions.length, 1);
  assert.equal(graph.nodes[0].data.versions[0].actual_duration, 10.03);
  await page.getByRole('button', { name: '1 个版本', exact: true }).click();
  assert.match(await page.locator('.version-menu').innerText(), /10\.03s/);
  await page.getByRole('button', { name: '1 个版本', exact: true }).click();
  const originalVersionId = graph.nodes[0].data.versions[0].id;
  complete = false;
  graph.nodes[0].data.status = 'interrupted';
  graph.nodes[0].data.versions[0].status = 'interrupted';
  delete graph.nodes[0].data.versions[0].url;
  await page.reload();
  await page.locator('.flow-node.shot').click();
  await page.getByRole('button', { name: '导入已下载成片', exact: true }).waitFor();
  await page.getByLabel('选择 Muse 已下载成片').setInputFiles({ name: 'muse_112233445566.mp4', mimeType: 'video/mp4', buffer: Buffer.from('downloaded-video') });
  await page.waitForFunction(() => document.querySelector('.flow-node.shot .node-state i.succeeded'));
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
  assert.deepEqual(imports, [{filename:'muse_112233445566.mp4',bytes:16,type:'application/octet-stream'}]);
  assert.equal(graph.nodes[0].data.versions.length, 1);
  assert.equal(graph.nodes[0].data.versions[0].id, originalVersionId);
  assert.equal(starts.length, 1); assert.equal(compositions.length, 1);
  const continuation = makeShot(720, 80);
  Object.assign(continuation.data, { title: 'Muse 视频参考', description: '' });
  graph.nodes.push(continuation);
  graph.edges.push({ id: 'video-link', source: shot.id, target: continuation.id });
  await page.reload();
  await page.locator(`[data-node-id="${continuation.id}"]`).click();
  await page.getByRole('textbox', { name: '视频画面描述' }).fill('@');
  assert.equal(await page.getByRole('option').filter({ hasText: 'Muse 创作' }).isEnabled(), true, JSON.stringify(graph.nodes[0].data));
  await page.getByRole('option').filter({ hasText: 'Muse 创作' }).click();
  await page.waitForFunction(() => document.querySelector('.source-name small')?.textContent.includes('视频附件'));
  assert.equal(await page.locator('.source-heading video[controls]').count(), 0);
  assert.equal(await page.locator('.prompt-mention.missing').count(), 0);
  await page.getByRole('textbox', { name: '视频画面描述' }).press('End');
  await page.getByRole('textbox', { name: '视频画面描述' }).pressSequentially('接续散步动作。');
  await page.getByRole('button', { name: '视频生成设置', exact: true }).click();
  await page.locator('.ratio-grid').getByRole('button', { name: '9:16', exact: true }).click();
  await page.getByRole('button', { name: '480P', exact: true }).click();
  await page.getByRole('button', { name: '生成视频', exact: true }).click();
  await page.waitForFunction(id => document.querySelector(`[data-node-id="${id}"] .node-state i.succeeded`), continuation.id);
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
  assert.equal(starts.length, 2);
  assert.match(compositions[1].description, /@\{video:/);
  assert.equal(starts[1].video_sources[0].source_node, shot.id);
  assert.equal(starts[1].video_sources[0].version_id, originalVersionId);
  assert.equal(starts[1].video_sources[0].file, 'seg_abcdef123456.mp4');
  assert.equal(starts[1].video_sources[0].usage, 'reference');
  assert.equal(starts[1].ratio, '9:16');
  assert.equal(starts[1].resolution, '480p');
  assert.deepEqual(starts[1].frames, []);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '打开服务配置', exact: true }).click();
  assert.equal(await page.locator('.provider-picker').evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
  await page.getByRole('button', { name: '打开 Muse 面板', exact: true }).click();
  await page.locator('.muse-screen img').waitFor();
  assert.equal(await page.locator('.muse-panel').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  inputDelay = 180;
  probeStart = panelInputs.length;
  const burst = 'abcdefghijklmnopqrstuvwxyz'.repeat(4);
  probeLength = burst.length;
  await page.getByLabel('Muse 页面键盘输入').pressSequentially(burst, {delay:4});
  await page.getByLabel('Muse 页面键盘输入').press('Backspace');
  for (let i = 0; i < 12; i++) await page.locator('.muse-screen').dispatchEvent('wheel', {clientX:180,clientY:300,deltaY:75,deltaX:0});
  await page.getByRole('button',{name:'使用当前聊天',exact:true}).click();
  await page.locator('.muse-panel').waitFor({state:'detached'});
  const burstInputs = panelInputs.slice(probeStart);
  assert.equal(burstInputs.filter(i=>i.kind==='text').map(i=>i.text).join(''), burst, 'all typed characters survive compaction');
  assert.ok(burstInputs.filter(i=>i.kind==='text').length < burst.length / 4, 'slow inputs do not create one request per character');
  assert.equal(burstInputs.find(i=>i.kind==='key').key, 'Backspace', 'editing key follows the batched text');
  assert.equal(burstInputs.filter(i=>i.kind==='wheel').reduce((sum,i)=>sum+i.dy,0), 900, 'quick scroll gestures retain their full distance');
  assert.ok(intermediateFrames > 0, 'continuous typing gets preview updates before the input burst finishes');
  assert.equal(maxFramesInFlight, 1, 'preview requests never pile up');
  inputDelay = 0; probeStart = null;
  const closedReads = frameReads;
  await page.waitForTimeout(400);
  assert.equal(frameReads, closedReads, 'closing stops preview polling');
  await page.getByRole('button',{name:'打开 Muse 面板',exact:true}).click();
  await page.locator('.muse-screen img').waitFor();
  remoteBusy=true;
  await page.getByRole('button',{name:'刷新页面预览',exact:true}).click();
  await page.getByText('视频正在生成，暂时不能切换聊天',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'使用当前聊天',exact:true}).last().isDisabled(),true);
  const priorInputs=panelInputs.length;
  await page.getByLabel('Muse 页面键盘输入').press('Enter');
  assert.equal(panelInputs.length,priorInputs);
  await page.getByRole('button',{name:'收起 Muse 面板',exact:true}).click();
  assert.equal(opened,true,'closing the panel keeps the background browser running');
  await page.screenshot({ path: 'docs/_shots/muse-settings-mobile.png' });
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log('PASS Muse settings, canvas generation, recovery and local download import into the same version without a new generation request');
  console.log('PASS one chat-selection button, slow input compaction without lost text/scroll, preview during continuous input, bounded requests and polling cleanup');
} finally { await context.close(); await browser.close(); }

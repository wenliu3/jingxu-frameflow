// All APIs are mocked: delayed responses never touch user projects or paid models.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import path from 'node:path';
import { emptyWorkflow, makeShot } from '../../web/src/workflowGraph.js';

const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = process.env.FRAMEFLOW_BROWSER_MODULES
  ? require(path.join(process.env.FRAMEFLOW_BROWSER_MODULES, 'playwright'))
  : require('playwright');
const browser = await chromium.launch({
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
  headless: true,
});
const taskId = '123456abcdef';
const url = process.env.E2E_URL || 'http://127.0.0.1:5173';
const cacheKey = `frameflow.workflow.${taskId}`;

async function scenario({ graph = emptyWorkflow(), records = [], workflow, detail, job, exportStatus, exportId } = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [], unexpected = [], saves = [];
  page.on('pageerror', (error) => errors.push(error.message));
  if (exportId) await page.addInitScript(({ key, id }) => localStorage.setItem(key, id), { key: `${cacheKey}.export`, id: exportId });
  await page.route('**/files/**', (route) => route.fulfill({ status: 404, body: '' }));
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const json = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    if (pathname === '/api/config') return json({ video_backend: 'comfyui', video_workflow: 'i2v' });
    if (pathname === '/api/video-capabilities') return json({ controls_version: 1 });
    if (pathname === '/api/tasks') return json([]);
    if (pathname === `/api/tasks/${taskId}`) return json({ task_id: taskId, status: 'succeeded', project: { title: '延迟响应测试', characters: [], assets: [] }, shots: [], blocks: [] });
    if (pathname.endsWith('/workflow')) {
      if (request.method() === 'GET') return workflow ? workflow(json) : json(graph);
      saves.push(request.postDataJSON());
      graph = structuredClone(saves.at(-1));
      graph.revision++;
      return json({ ok: true, revision: graph.revision });
    }
    if (pathname.endsWith('/segments')) return json({ items: records });
    if (pathname.includes('/segments/')) return detail ? detail(pathname.split('/').at(-1), json) : json({ found: false });
    if (pathname.startsWith('/api/segment-jobs/')) return job(json);
    if (pathname.includes('/canvas-export/')) return exportStatus(json);
    unexpected.push(`${request.method()} ${pathname}`);
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto(`${url}/?browser-test=lifecycle#project=${taskId}&view=storyboard`);
  await page.locator('.workflow-editor').waitFor();
  return { page, saves, async close() { assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); await context.close(); } };
}

async function leave(page) {
  await page.getByRole('button', { name: '新建作品', exact: true }).click();
  await page.getByRole('heading', { name: '你的下一部作品，从这里开始。' }).waitFor();
}

try {
  // A late initial load must not seed and save a canvas that has already closed.
  let releaseWorkflow;
  const loading = await scenario({ workflow: (json) => new Promise((resolve) => { releaseWorkflow = async () => { await json(emptyWorkflow()); resolve(); }; }) });
  await leave(loading.page);
  await releaseWorkflow();
  await loading.page.waitForTimeout(1000);
  assert.equal(loading.saves.length, 0);
  assert.equal(await loading.page.evaluate((key) => localStorage.getItem(key), cacheKey), null);
  await loading.close();

  // Import many records with responses arriving in reverse order within each batch.
  const records = Array.from({ length: 18 }, (_, index) => ({ name: `history_${index}.mp4`, url: `/files/${taskId}/segments/history_${index}.mp4` }));
  const waiting = [];
  let active = 0, peak = 0;
  const importing = await scenario({ records, detail: (name, json) => new Promise((resolve) => {
    peak = Math.max(peak, ++active);
    waiting.push(async () => { active--; await json({ found: true, note: name, duration: 6.5 }); resolve(); });
  }) });
  for (let batch = 0; batch < 3; batch++) {
    for (let attempt = 0; waiting.length < (batch + 1) * 6 && attempt < 100; attempt++) await importing.page.waitForTimeout(20);
    assert.equal(waiting.length, (batch + 1) * 6);
    assert.equal(active, 6);
    for (const release of waiting.slice(batch * 6, (batch + 1) * 6).reverse()) await release();
  }
  await importing.page.waitForFunction(() => document.querySelectorAll('.flow-node.shot .node-state i.succeeded').length === 18);
  await importing.page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
  assert.equal(peak, 6);
  assert.deepEqual(importing.saves.at(-1).nodes.filter((node) => node.type === 'shot' && node.data.versions?.length).map((node) => node.data.versions[0].file), records.map((record) => record.name));
  await importing.close();

  // Navigating away stops the remaining reads in an unfinished history import.
  const pending = [];
  const abandoned = await scenario({ records, detail: (_, json) => new Promise((resolve) => { pending.push(async () => { await json({ found: false }); resolve(); }); }) });
  for (let attempt = 0; pending.length < 6 && attempt < 100; attempt++) await abandoned.page.waitForTimeout(20);
  assert.equal(pending.length, 6);
  await leave(abandoned.page);
  for (const release of pending) await release();
  await abandoned.page.waitForTimeout(1000);
  assert.equal(pending.length, 6);
  assert.equal(abandoned.saves.length, 0);
  await abandoned.close();

  // Export progress arriving after navigation stays recoverable on the next visit.
  const exportId = 'abcdef123456';
  let releaseExport;
  const graph = emptyWorkflow();
  graph.nodes.push(makeShot(100, 100, 1));
  const exporting = await scenario({ graph, exportId, exportStatus: (json) => new Promise((resolve) => { releaseExport = async () => { await json({ status: 'succeeded', url: `/files/${taskId}/export/final.mp4` }); resolve(); }; }) });
  for (let attempt = 0; !releaseExport && attempt < 100; attempt++) await exporting.page.waitForTimeout(20);
  assert.ok(releaseExport);
  await leave(exporting.page);
  await releaseExport();
  await exporting.page.waitForTimeout(200);
  assert.equal(await exporting.page.evaluate((key) => localStorage.getItem(key), `${cacheKey}.export`), exportId);
  assert.equal(await exporting.page.getByText('成片已导出，可以下载了', { exact: true }).count(), 0);
  await exporting.close();

  // A completed job on the old canvas must not show a success toast in the new project.
  let releaseJob;
  const running = emptyWorkflow();
  const shot = makeShot(100, 100, 1);
  shot.data.status = 'running';
  shot.data.jobId = 'abcdef123456';
  shot.data.versions = [{ id: 'attempt', jobId: shot.data.jobId, status: 'running' }];
  running.nodes.push(shot);
  const polling = await scenario({ graph: running, job: (json) => new Promise((resolve) => { releaseJob = async () => { await json({ status: 'succeeded', results: [], done: 1, total: 1 }); resolve(); }; }) });
  for (let attempt = 0; !releaseJob && attempt < 100; attempt++) await polling.page.waitForTimeout(20);
  assert.ok(releaseJob);
  await leave(polling.page);
  await releaseJob();
  await polling.page.waitForTimeout(300);
  assert.equal(await polling.page.locator('.toast').filter({ hasText: '已生成' }).count(), 0);
  await polling.close();
  console.log('PASS: bounded, ordered history imports; delayed canvas, export and generation responses after navigation. No live API calls.');
} finally {
  await browser.close();
}

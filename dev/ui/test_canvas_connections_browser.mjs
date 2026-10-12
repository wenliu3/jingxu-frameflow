// Pointer gestures against intercepted fixture data; no user project or model calls.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { emptyWorkflow, makeNode, makeShot } from '../../web/src/workflowGraph.js';

const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const taskId = '123456abcdef';
let graph = emptyWorkflow();
const source = makeNode('material', 80, 100, { title: '参考图片', materialKind: 'image', materialName: '参考图片', materialFile: 'image.webp' });
const note = makeNode('note', 80, 460, { title: '剧情便签', text: '参考前情' });
const target = makeShot(540, 100, 1), next = makeShot(540, 460, 2), busy = makeShot(980, 100, 3);
graph.nodes.push(source, note, target, next, busy);
const positions = graph.nodes.map(({ id, x, y }) => ({ id, x, y }));
const errors = [], unexpected = [];
page.on('pageerror', (error) => errors.push(error.message));
await page.route('**/files/**', (route) => route.fulfill({ status: 404, body: '' }));
await page.route('**/api/**', async (route) => {
  const request = route.request(), pathname = new URL(request.url()).pathname;
  const json = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  if (pathname === '/api/config') return json({ video_backend: 'muse', text_api_key: '' });
  if (pathname === '/api/video-capabilities') return json({ controls_version: 1, video_sources_version: 1 });
  if (pathname === '/api/tasks') return json([]);
  if (pathname === `/api/tasks/${taskId}`) return json({ task_id: taskId, status: 'succeeded', project: { title: '拖动连线测试', characters: [], assets: [{ kind: 'image', name: '参考图片', images: ['image.webp'] }] }, shots: [], blocks: [] });
  if (pathname.endsWith('/segments')) return json({ items: [] });
  if (pathname.endsWith('/workflow')) {
    if (request.method() === 'GET') return json(graph);
    assert.equal(request.postDataJSON().revision, graph.revision);
    graph = request.postDataJSON(); graph.revision++;
    return json({ ok: true, revision: graph.revision });
  }
  unexpected.push(`${request.method()} ${pathname}`);
  return route.fulfill({ status: 404, body: '' });
});
const node = (id) => page.locator(`[data-node-id="${id}"]`);
const port = (id) => node(id).getByRole('button', { name: '从此节点开始连接', exact: true });
async function center(locator) {
  const box = await locator.boundingBox();
  assert.ok(box);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}
async function saved() {
  await page.waitForFunction(() => document.querySelector('.save-state')?.textContent.includes('已保存'));
}
async function dismissComposer() {
  const close = page.getByRole('button', { name: '关闭视频输入框', exact: true });
  if (await close.count()) await close.click();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
}
async function dragStart(id, destination) {
  const start = await center(port(id));
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(destination.x, destination.y, { steps: 10 });
}
async function dragTo(id, destination) {
  await dragStart(id, destination);
  await page.mouse.up();
  await saved();
}
async function undo() {
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await dismissComposer();
  await saved();
}
async function blankPoint() {
  return page.locator('.flow-board').evaluate((board) => {
    const rect = board.getBoundingClientRect();
    for (let y = rect.bottom - 130; y > rect.top + 70; y -= 35) {
      for (let x = rect.right - 80; x > rect.left + 100; x -= 35) {
        if (document.elementFromPoint(x, y) === board) return { x, y };
      }
    }
    throw new Error('No empty point');
  });
}
async function noLink() {
  assert.equal(await page.locator('.loose-edge').count(), 0);
  assert.equal(await page.locator('.connect-hint').count(), 0);
  assert.deepEqual(graph.nodes.map(({ id, x, y }) => ({ id, x, y })), positions, 'connecting never moves nodes');
}
try {
  await page.goto(`${process.env.E2E_URL || 'http://127.0.0.1:5173'}/?browser-test=connections#project=${taskId}&view=storyboard`);
  await node(source.id).waitFor();
  await dismissComposer();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  await saved();
  const toTarget = () => center(node(target.id).locator('.node-preview'));

  await dragStart(source.id, await toTarget());
  await port(source.id).dispatchEvent('lostpointercapture', { pointerId: 1, bubbles: true });
  assert.equal(await node(target.id).locator('.node-preview').evaluate((preview) => preview.closest('.flow-node').classList.contains('link-hovered')), true, 'pointer capture still highlights the hovered target');
  assert.equal(await page.locator('.loose-edge').count(), 1);
  await page.mouse.up();
  await saved();
  assert.deepEqual(graph.edges.map(({ source, target }) => ({ source, target })), [{ source: source.id, target: target.id }]);
  await noLink();
  await dismissComposer();

  // A second drag to the same target cannot duplicate the first edge.
  await dragTo(source.id, await toTarget());
  await page.getByText('这两个节点已经连接', { exact: true }).waitFor();
  assert.equal(graph.edges.length, 1);
  await noLink();
  await undo();
  assert.equal(graph.edges.length, 0);

  // Small stationary presses retain click-to-connect and toggle-to-cancel.
  await port(source.id).click();
  assert.equal(await page.locator('.loose-edge').count(), 1);
  await port(source.id).click();
  await noLink();
  await port(source.id).click();
  await node(target.id).getByRole('button', { name: '使用此视频接收参考素材', exact: true }).click();
  await saved();
  assert.equal(graph.edges.length, 1);
  await undo();

  // Drag to the input port, not just the frame body.
  await dragTo(source.id, await center(node(target.id).getByRole('button', { name: '连接到此节点', exact: true })));
  assert.equal(graph.edges.length, 1);
  await noLink();
  await undo();

  // Empty drops, Escape and pointer cancellation stop a gesture without edits.
  await dragTo(source.id, await blankPoint());
  assert.equal(graph.edges.length, 0);
  await noLink();
  await dragStart(source.id, await toTarget());
  await page.keyboard.press('Escape');
  await page.mouse.up();
  assert.equal(graph.edges.length, 0);
  await noLink();
  await dragStart(source.id, await toTarget());
  await page.locator('.flow-board').dispatchEvent('pointercancel', { pointerId: 1, button: 0 });
  await page.mouse.up();
  assert.equal(graph.edges.length, 0);
  await noLink();

  await dragTo(source.id, await center(node(source.id).locator('.node-preview')));
  await page.getByText('不能连接到自身', { exact: true }).waitFor();
  assert.equal(graph.edges.length, 0);
  await noLink();

  // Video-to-video uses the same cycle checks as click connections.
  await dragTo(target.id, await center(node(next.id).locator('.node-preview')));
  assert.equal(graph.edges.length, 1);
  await dismissComposer();
  await dragTo(next.id, await toTarget());
  await page.getByText('不能形成循环连线', { exact: true }).waitFor();
  assert.equal(graph.edges.length, 1);
  await noLink();
  await undo();

  // Enter still activates the output port for keyboard users.
  await port(source.id).press('Enter');
  assert.equal(await page.locator('.loose-edge').count(), 1);
  await node(target.id).getByRole('button', { name: '连接到此节点', exact: true }).press('Enter');
  await saved();
  assert.equal(graph.edges.length, 1);
  await noLink();

  // Queued nodes keep their existing edit protection; set it only after undo tests.
  graph.nodes.find((n) => n.id === busy.id).data.status = 'queued';
  await page.reload();
  await node(source.id).waitFor();
  await page.getByRole('button', { name: '关闭生成队列', exact: true }).click();
  await dismissComposer();
  await page.getByRole('button', { name: '显示全部节点', exact: true }).click();
  await saved();
  await dragTo(source.id, await center(node(busy.id).locator('.node-preview')));
  await page.getByText('请先暂停并移出队列，再修改镜头参考', { exact: true }).waitFor();
  assert.equal(graph.edges.length, 1);
  await noLink();
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log('PASS: drag/drop to frame and input, hover under pointer capture, click and keyboard fallback, undo, duplicate/self/cycle/busy checks, empty/Escape/cancel. No live API calls.');
} finally {
  await context.close();
  await browser.close();
}

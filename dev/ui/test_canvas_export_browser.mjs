// Offline export acceptance: API requests are intercepted; no real projects or model calls.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { emptyWorkflow, makeShot } from '../../web/src/workflowGraph.js'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const require = createRequire(new URL('../../web/package.json', import.meta.url))
const { chromium } = process.env.FRAMEFLOW_BROWSER_MODULES
  ? require(path.join(process.env.FRAMEFLOW_BROWSER_MODULES, 'playwright'))
  : require('playwright')
function videoFixture(duration) {
  return execFileSync(process.env.PYTHON || 'python', ['-c', `import imageio_ffmpeg,subprocess,tempfile,pathlib,sys
with tempfile.TemporaryDirectory() as d:
 p=pathlib.Path(d)/'fixture.mp4'
 subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-f','lavfi','-i','color=c=0x293646:s=320x180:r=24','-t',sys.argv[1],'-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(p)],check=True)
 sys.stdout.buffer.write(p.read_bytes())`, String(duration)])
}
const video = videoFixture(1), video2 = videoFixture(2)
const browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
const taskId = 'aabbcc123456', url = process.env.E2E_URL || 'http://127.0.0.1:5173'
const errors = [], unexpected = [], exported = []
let failPackage = ''
const titles = ['夜行的侧脸', '晨光已经在窗外', '老屋还在', '树影落在泥地上', '土路通向家', '到家前停步回望', '灶边的奶奶', '饭已经做好']
let graph = emptyWorkflow()
const shots = Array.from({ length: 18 }, (_, i) => {
  const shot = makeShot(80 + (i % 6) * 365, 80 + Math.floor(i / 6) * 310, i + 1)
  shot.data.title = titles[i % titles.length]
  shot.data.description = '离线验收镜头'
  shot.data.manual = true
  if (![2, 11].includes(i)) {
    shot.data.status = 'succeeded'
    shot.data.versions = [1, ...(i === 1 ? [2] : [])].map(index => ({
      id: `version-${i}-${index}`, status: 'succeeded',
      file: `seg_${i}_${index}.mp4`, url: `/files/${taskId}/segments/seg_${i}_${index}.mp4`,
      duration: 5, actual_duration: index === 2 ? 2 : 1, width: 320, height: 180,
    }))
    shot.data.activeVersionId = shot.data.versions[0].id
  }
  return shot
})
graph.nodes = shots
graph.edges = shots.slice(1).map((shot, i) => ({ id: `edge-${i}`, source: shots[i].id, target: shot.id }))
const project = { title: '归家 · 导出验收', characters: [], assets: [] }
page.on('pageerror', error => errors.push(error.message))
await page.route('**/files/**', route => route.fulfill({
  contentType: route.request().url().endsWith('.zip') ? 'application/zip' : 'video/mp4',
  body: route.request().url().endsWith('.zip') ? Buffer.from('504b0506000000000000000000000000000000000000', 'hex') : route.request().url().endsWith('_2.mp4') ? video2 : video,
}))
await page.route('**/api/**', async route => {
  const request = route.request(), pathname = new URL(request.url()).pathname
  const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) })
  if (pathname === '/api/config') return json({ video_backend: 'comfyui', comfyui_url: 'http://mock', video_workflow: 'i2v' })
  if (pathname === '/api/tasks') return json([])
  if (pathname === `/api/tasks/${taskId}`) return json({ task_id: taskId, project, status: 'succeeded', shots: [], blocks: [] })
  if (pathname.endsWith('/workflow')) {
    if (request.method() === 'GET') return json(graph)
    graph = structuredClone(request.postDataJSON())
    graph.revision++
    return json({ ok: true, revision: graph.revision })
  }
  if (pathname.endsWith('/segments') || pathname.endsWith('/docs')) return json({ items: [] })
  if (pathname.endsWith('/canvas-export/clips') && request.method() === 'POST') {
    if (failPackage) return route.fulfill({ status: failPackage === 'old' ? 405 : 404, contentType: 'application/json', body: JSON.stringify({ detail: failPackage === 'old' ? 'Method Not Allowed' : '视频片段不存在：seg_0_1.mp4' }) })
    exported.push({ mode: 'clips', ...request.postDataJSON() })
    return json({ job_id: 'abcdef123456', mode: 'clips' })
  }
  if (pathname.endsWith('/canvas-export') && request.method() === 'POST') {
    exported.push({ mode: 'merge', ...request.postDataJSON() })
    return json({ job_id: 'abcdef123456', mode: 'merge' })
  }
  if (pathname.includes('/canvas-export/')) {
    const mode = exported.at(-1)?.mode || 'merge'
    return json({ status: 'succeeded', mode, done: 16, total: 16, download_name: mode === 'clips' ? '归家_镜头素材.zip' : '归家_成片.mp4', url: `/files/${taskId}/export/${mode === 'clips' ? 'clips.zip' : 'final.mp4'}` })
  }
  unexpected.push(`${request.method()} ${pathname}`)
  return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Unexpected API call' }) })
})

async function openExport() {
  await page.getByRole('button', { name: '导出视频', exact: true }).click()
  await page.getByRole('dialog').waitFor()
}
async function assertFooterVisible() {
  const footer = await page.locator('.export-footer').boundingBox()
  const button = await page.locator('.export-confirm').boundingBox()
  const viewport = page.viewportSize()
  assert.ok(footer.y >= 0 && footer.y + footer.height <= viewport.height, 'footer stays in the viewport')
  assert.ok(button.x >= 0 && button.x + button.width <= viewport.width, 'export button stays in the viewport')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
}

try {
  await page.goto(`${url}/?browser-test=export#project=${taskId}&view=storyboard`)
  await page.locator('.flow-node.shot').first().waitFor()
  await openExport()
  const dialog = page.getByRole('dialog')
  assert.equal(await dialog.locator('.export-shot').count(), 16)
  assert.equal(await dialog.getByRole('combobox').count(), 1, 'single-version shots have no redundant selector')
  assert.equal(await dialog.getByRole('button', { name: /^打包镜头/ }).getAttribute('aria-pressed'), 'true', 'original clips are the default export')
  assert.equal(await dialog.locator('.aspect-options').count(), 0, 'packaging has no re-encoding settings')
  assert.ok((await dialog.locator('.export-warning').textContent()).includes('2 镜'))
  assert.deepEqual(await dialog.locator('.shot-order').allTextContents(), ['01', '02', '04', '05', '06', '07', '08', '09', '10', '11', '13', '14', '15', '16', '17', '18'])
  assert.ok(!(await dialog.textContent()).includes('seg_'), 'technical filenames are not used as labels')
  await assertFooterVisible()
  assert.ok((await dialog.locator('.export-summary').textContent()).includes('16 秒'))
  const choice = dialog.getByRole('combobox', { name: `${titles[1]}导出版本` })
  assert.equal(await choice.inputValue(), 'seg_1_1.mp4', 'default follows the canvas choice rather than the newest version')
  await choice.selectOption('seg_1_2.mp4')
  assert.ok((await dialog.locator('.export-summary').textContent()).includes('17 秒'))
  assert.ok((await dialog.locator('video').getAttribute('src')).endsWith('seg_1_2.mp4'))
  await dialog.getByRole('button', { name: '恢复画布选用' }).click()
  assert.equal(await choice.inputValue(), 'seg_1_1.mp4')
  await choice.selectOption('seg_1_2.mp4')
  await dialog.getByRole('button', { name: '清空选择', exact: true }).click()
  assert.equal(await dialog.locator('.export-confirm').isDisabled(), true, 'empty selection cannot export')
  await dialog.getByRole('checkbox', { name: '全选', exact: true }).check()
  await dialog.locator('.export-shot').nth(2).getByRole('checkbox').uncheck()
  assert.equal(await dialog.getByRole('checkbox', { name: '全选', exact: true }).evaluate(element => element.indeterminate), true)
  assert.equal(await dialog.getByRole('button', { name: '下载 15 镜 ZIP', exact: true }).count(), 1)
  assert.ok((await dialog.locator('.package-files').textContent()).includes('01_夜行的侧脸.mp4'))
  assert.ok(!(await dialog.locator('.package-files').textContent()).includes('04_'))
  await assertFooterVisible()
  fs.mkdirSync(path.join(root, 'docs/_shots'), { recursive: true })
  await page.waitForFunction(() => document.querySelector('.export-preview video')?.readyState >= 2)
  await page.screenshot({ path: path.join(root, 'docs/_shots/canvas-export-package.png'), animations: 'disabled' })
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    dialog.getByRole('button', { name: '下载 15 镜 ZIP', exact: true }).click(),
  ])
  assert.equal(download.suggestedFilename(), '归家_镜头素材.zip', 'ZIP download starts from the export action')
  await page.getByRole('link', { name: '下载素材包', exact: true }).waitFor()
  const expectedClips = shots.flatMap((shot, index) => shot.data.versions.length && index !== 3
    ? [{ file: index === 1 ? 'seg_1_2.mp4' : shot.data.versions[0].file, order: index + 1, title: shot.data.title }]
    : [])
  assert.deepEqual(exported[0], { mode: 'clips', clips: expectedClips }, 'ZIP contains only checked shots and the selected versions, retaining original sequence numbers')
  assert.equal(graph.nodes[1].data.activeVersionId, 'version-1-1')
  await openExport()
  assert.equal(await dialog.getByRole('button', { name: '下载 16 镜 ZIP', exact: true }).count(), 1)
  assert.equal(await choice.inputValue(), 'seg_1_1.mp4')
  await choice.selectOption('seg_1_2.mp4')
  await dialog.getByRole('button', { name: /^合成成片/ }).click()
  await dialog.getByRole('button', { name: '竖屏 9:16', exact: true }).click()
  assert.ok((await dialog.locator('.export-specs').textContent()).includes('720 × 1280'))
  await dialog.locator('.export-list').evaluate(element => (element.scrollTop = element.scrollHeight))
  await assertFooterVisible()
  fs.mkdirSync(path.join(root, 'docs/_shots'), { recursive: true })
  await dialog.locator('.export-list').evaluate(element => (element.scrollTop = 0))
  await page.screenshot({ path: path.join(root, 'docs/_shots/canvas-export-desktop.png'), animations: 'disabled' })
  await dialog.getByRole('button', { name: '导出 16 镜成片', exact: true }).click()
  await page.getByRole('link', { name: '下载成片' }).waitFor()
  const expected = shots.filter(shot => shot.data.versions.length).map(shot => shot.data.versions[0].file)
  expected[1] = 'seg_1_2.mp4'
  assert.deepEqual(exported[1], { mode: 'merge', files: expected, aspect: '9:16' }, 'selected versions are submitted in storyboard order')
  assert.equal(graph.nodes[1].data.activeVersionId, 'version-1-1', 'export-only choices do not rewrite the canvas')

  // Reopening resets export-only changes. Focus remains inside the modal and returns to its trigger.
  await openExport()
  assert.equal(await choice.inputValue(), 'seg_1_1.mp4')
  await page.keyboard.press('Shift+Tab')
  assert.equal(await page.evaluate(() => document.activeElement.classList.contains('export-confirm')), true)
  await page.keyboard.press('Tab')
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), '关闭对话框')
  await page.keyboard.press('Escape')
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), '导出视频')

  await page.setViewportSize({ width: 390, height: 844 })
  await openExport()
  await assertFooterVisible()
  assert.ok((await dialog.locator('.export-shot').first().boundingBox()).y < (await dialog.locator('.export-footer').boundingBox()).y, 'mobile starts with visible shot selection')
  await page.screenshot({ path: path.join(root, 'docs/_shots/canvas-export-package-mobile.png'), animations: 'disabled' })
  await dialog.getByRole('button', { name: /^合成成片/ }).click()
  await page.getByRole('button', { name: '方形 1:1', exact: true }).click()
  await page.locator('.export-body').evaluate(element => (element.scrollTop = element.scrollHeight))
  await assertFooterVisible()
  await page.locator('.export-body').evaluate(element => (element.scrollTop = 0))
  await page.screenshot({ path: path.join(root, 'docs/_shots/canvas-export-mobile.png'), animations: 'disabled' })
  await page.keyboard.press('Escape')

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.evaluate(() => { localStorage.setItem('frameflow.theme', 'light'); document.documentElement.dataset.theme = 'light' })
  await openExport()
  assert.equal(await page.locator('.export-settings').evaluate(element => getComputedStyle(element).backgroundColor), 'rgb(255, 255, 255)')
  await page.screenshot({ path: path.join(root, 'docs/_shots/canvas-export-light.png'), animations: 'disabled' })
  await page.keyboard.press('Escape')

  // Older projects have planned timing only: label estimates and update from the preview's real metadata.
  graph = emptyWorkflow()
  graph.nodes = shots.slice(0, 2).map(shot => structuredClone(shot))
  graph.nodes.forEach(shot => shot.data.versions.forEach(version => delete version.actual_duration))
  await page.evaluate(() => localStorage.clear())
  await page.reload()
  await page.locator('.flow-node.shot').first().waitFor()
  await openExport()
  await page.waitForFunction(() => document.querySelector('.export-preview video')?.readyState >= 1)
  assert.ok((await page.locator('.export-summary').textContent()).includes('约 6 秒'))
  await dialog.getByRole('button', { name: /^合成成片/ }).click()
  assert.ok((await page.locator('.export-hint').last().textContent()).includes('估算'))
  await dialog.getByRole('button', { name: /^打包镜头/ }).click()
  failPackage = 'old'
  await dialog.locator('.export-confirm').click()
  await page.getByText('后端尚未支持镜头打包，请更新并重启后端服务。', { exact: true }).waitFor()
  await openExport()
  failPackage = 'missing'
  await dialog.locator('.export-confirm').click()
  await page.getByText('视频片段不存在：seg_0_1.mp4', { exact: true }).waitFor()
  assert.deepEqual(errors, [])
  assert.deepEqual(unexpected, [])
  console.log('PASS: selected original clips/ZIP downloads, filenames/order, empty/all/partial selection, version choices, merge/aspect settings, fixed actions, focus, mobile/day layouts, estimated timing and old backend feedback. No live model calls.')
} catch (error) {
  fs.mkdirSync(path.join(root, 'docs/_shots'), { recursive: true })
  await page.screenshot({ path: path.join(root, 'docs/_shots/canvas-export-failure.png') })
  console.error({ errors, unexpected })
  throw error
} finally {
  await browser.close()
}

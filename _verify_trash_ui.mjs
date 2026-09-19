// 回收站界面验证（2026-09-19 斌哥要求）：
//   入口 → 「我的作品」页头部那颗「回收站」按钮
//   内容 → 列表（标题 / 作品 id / 移入时间 / 占用空间）+ 每条「恢复」「彻底删除」
//   动作 → 恢复调 POST /api/trash/{name}/restore；彻底删除调 DELETE /api/trash/{name}?confirm=true
//   空态 → 没有条目时给「回收站是空的」
// 跑法：node _verify_trash_ui.mjs
//      验生产包：E2E_URL=http://127.0.0.1:8000/ node _verify_trash_ui.mjs
//
// ⚠️ 不烧任何额度、也不碰真实回收站：拦截 /api/trash* 全部回罐头数据。
//    真实回收站里现在有 200+ 条历史测试目录，真拉一次要遍历目录算空间，很慢。
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:5173/'
const OUT = 'docs/_shots'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function check(name, cond, extra = '') {
  results.push(!!cond)
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? `\n        → ${extra}` : ''))
}

const TRASH = [
  { name: '20260919_023031_fc04e44b889c', task_id: 'fc04e44b889c', title: '雨夜便利店', deleted_at: '2026-09-19T02:30:31', bytes: 9437184 },
  { name: '20260918_113045_21e072c11fe3', task_id: '21e072c11fe3', title: '', deleted_at: '2026-09-18T11:30:45', bytes: 524288 },
]

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1100 },
})
const page = await browser.newPage()
const pageErrors = []
page.on('pageerror', (e) => { pageErrors.push(e.message); console.log('[pageerror]', e.message) })
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

// 「彻底删除」要过 window.confirm，自动接受
page.on('dialog', (d) => d.accept())

const restoreCalls = []
const purgeCalls = []
let emptyMode = false
let purgeFails = false      // 模拟后端 500（纯文本响应体）

await page.setRequestInterception(true)
page.on('request', (req) => {
  const url = req.url()
  const json = (body) => req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  if (url.includes('/api/trash') && req.method() === 'GET') {
    return json({ entries: emptyMode ? [] : TRASH })
  }
  if (/\/api\/trash\/[^/]+\/restore/.test(url)) {
    restoreCalls.push(url)
    return json({ restored: url.match(/trash\/([^/]+)\/restore/)[1] })
  }
  if (url.includes('/api/trash/') && req.method() === 'DELETE') {
    purgeCalls.push(url)
    if (purgeFails) {
      // 复现真实故障：Starlette 的未捕获异常返回的就是 **纯文本** "Internal Server Error"。
      // 原来的 api.js 会先 res.json() 失败、再 res.text() → 抛
      // 「body stream already read」，把这句话顶掉。
      return req.respond({ status: 500, contentType: 'text/plain; charset=utf-8', body: 'Internal Server Error' })
    }
    return json({ purged: url.match(/trash\/([^/?]+)/)[1] })
  }
  req.continue()
})

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)
  console.log('='.repeat(74))
  console.log('回收站 · 前端 UI 验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  await page.$$eval('.nav-item', (els) => els.find((e) => e.textContent.includes('我的作品')).click())
  await sleep(1200)

  // ---------------------------------------------------------------- 1 入口
  const entry = await page.evaluate(() => {
    const box = document.querySelector('.library-header .lib-actions')
    if (!box) return null
    return {
      按钮: [...box.querySelectorAll('button')].map((b) => b.textContent.trim()),
      title: box.querySelector('button')?.getAttribute('title') || '',
    }
  })
  check('「我的作品」头部有回收站入口（在 .lib-actions 里）',
    !!entry && entry.按钮.includes('回收站'), entry ? entry.按钮.join(' / ') : '没找到 .lib-actions')
  check('入口按钮带 title 说明', !!entry && entry.title.includes('回收站'), entry?.title)

  // ---------------------------------------------------------------- 2 进回收站
  await page.$$eval('.library-header .lib-actions button', (els) => els.find((b) => b.textContent.trim() === '回收站').click())
  await sleep(900)

  const view = await page.evaluate(() => {
    const items = [...document.querySelectorAll('.trash-item')]
    return {
      标题: document.querySelector('.library-header h1')?.textContent.trim() || '',
      副标题: document.querySelector('.library-header p')?.textContent.trim() || '',
      文档标题: document.title,
      条数: items.length,
      首条: items[0]
        ? {
            标题: items[0].querySelector('h2')?.textContent.trim() || '',
            元信息: [...items[0].querySelectorAll('.trash-meta > span')].map((s) => s.textContent.trim()),
            按钮: [...items[0].querySelectorAll('.trash-ops button')].map((b) => b.textContent.trim()),
          }
        : null,
      次条标题: items[1]?.querySelector('h2')?.textContent.trim() || '',
      作品网格: document.querySelectorAll('.project-grid, .project-tile').length,
      工具栏: document.querySelectorAll('.library-toolbar').length,
      返回按钮: [...document.querySelectorAll('.lib-actions button')].map((b) => b.textContent.trim()),
    }
  })
  console.log('探针:', JSON.stringify(view, null, 2), '\n')

  check('视图切到回收站，标题为「回收站」', view.标题 === '回收站', view.标题)
  check('浏览器标签页标题跟着变', view.文档标题.includes('回收站'), view.文档标题)
  check('列表渲染出 2 条', view.条数 === 2, `条数 = ${view.条数}`)
  check('副标题给出件数与占用空间',
    view.副标题.includes('共 2 件') && view.副标题.includes('9.5 MB'), view.副标题)
  check('条目显示标题', view.首条?.标题 === '雨夜便利店', view.首条?.标题)
  check('没有标题的条目落到「未命名作品」', view.次条标题 === '未命名作品', view.次条标题)
  check('条目显示作品 id / 移入时间 / 占用空间',
    view.首条?.元信息.length === 3
      && view.首条.元信息[0] === 'fc04e44b889c'
      && view.首条.元信息[1].includes(':')
      && view.首条.元信息[2] === '9.0 MB',
    view.首条?.元信息.join(' | '))
  check('条目有「恢复」与「彻底删除」两颗按钮',
    view.首条?.按钮.join('/') === '恢复/彻底删除', view.首条?.按钮.join('/'))
  check('回收站里不该出现作品网格与筛选栏',
    view.作品网格 === 0 && view.工具栏 === 0, `网格 = ${view.作品网格}，工具栏 = ${view.工具栏}`)
  check('回收站头部有「刷新」「返回作品」',
    view.返回按钮.join('/') === '刷新/返回作品', view.返回按钮.join('/'))

  // ---------------------------------------------------------------- 3 恢复
  await page.$$eval('.trash-item .trash-ops button', (els) => {
    const b = els.find((x) => x.textContent.trim() === '恢复')
    b.click()
  })
  await sleep(900)
  const afterRestore = await page.evaluate(() => ({
    条数: document.querySelectorAll('.trash-item').length,
    标题: [...document.querySelectorAll('.trash-item h2')].map((h) => h.textContent.trim()),
    提示: document.querySelector('.toast')?.textContent.trim() || '',
  }))
  check('恢复后该条从回收站列表消失',
    afterRestore.条数 === 1 && !afterRestore.标题.includes('雨夜便利店'),
    `剩 ${afterRestore.条数} 条：${afterRestore.标题.join(' / ')}`)
  check('恢复打到 POST /api/trash/{name}/restore',
    restoreCalls.length === 1 && restoreCalls[0].includes('/trash/20260919_023031_fc04e44b889c/restore'),
    restoreCalls.join(' | '))

  // ---------------------------------------------------------------- 3.5 失败要显示真实原因
  // 2026-09-19 真事故：后端 500 返回纯文本 "Internal Server Error"，
  // 而 api.js 先 res.json()（失败但吃掉 body）再 res.text() → 抛 body-stream 错误，
  // 用户看到的是「Failed to execute 'text' on 'Response': body stream already read」。
  purgeFails = true
  await page.$$eval('.trash-item .trash-ops button', (els) => {
    els.find((x) => x.textContent.trim() === '彻底删除').click()
  })
  await sleep(900)
  const failed = await page.evaluate(() => ({
    提示: [...document.querySelectorAll('.toast .text')].map((t) => t.textContent.trim()),
    条数: document.querySelectorAll('.trash-item').length,
  }))
  const toastText = failed.提示.join(' | ')
  check('后端 500 时把真实原因显示出来（而不是 body-stream 那句鬼话）',
    toastText.includes('Internal Server Error'), toastText || '(没有 toast)')
  check('绝不能再出现「body stream already read」',
    !toastText.includes('body stream already read'), toastText)
  check('删除失败时该条还留在列表里（不能假装删掉了）', failed.条数 === 1, `条数 = ${failed.条数}`)
  purgeFails = false

  // ---------------------------------------------------------------- 4 彻底删除
  await page.$$eval('.trash-item .trash-ops button', (els) => {
    const b = els.find((x) => x.textContent.trim() === '彻底删除')
    b.click()
  })
  await sleep(900)
  const afterPurge = await page.evaluate(() => ({
    条数: document.querySelectorAll('.trash-item').length,
    空态: document.querySelectorAll('.empty-library').length,
    空态文案: document.querySelector('.empty-library h2')?.textContent.trim() || '',
  }))
  check('彻底删除后列表空了并落到空态',
    afterPurge.条数 === 0 && afterPurge.空态 === 1, `条数 = ${afterPurge.条数}，空态 = ${afterPurge.空态}`)
  check('空态文案是「回收站是空的」', afterPurge.空态文案 === '回收站是空的', afterPurge.空态文案)
  check('彻底删除打到 DELETE /api/trash/{name}?confirm=true（两次：先失败后成功）',
    purgeCalls.length === 2
      && purgeCalls[1].includes('/trash/20260918_113045_21e072c11fe3')
      && purgeCalls[1].includes('confirm=true'),
    purgeCalls.join(' | '))

  // ---------------------------------------------------------------- 5 返回作品
  await page.$$eval('.lib-actions button', (els) => {
    const b = els.find((x) => x.textContent.trim() === '返回作品')
    b.click()
  })
  await sleep(1200)
  const back = await page.evaluate(() => ({
    标题: document.querySelector('.library-header h1')?.textContent.trim() || '',
    网格或空态: document.querySelectorAll('.project-grid, .empty-library').length,
    回收站条目: document.querySelectorAll('.trash-item').length,
  }))
  check('「返回作品」回到「我的作品」',
    back.标题 === '每个故事，都在这里。' && back.网格或空态 >= 1 && back.回收站条目 === 0,
    `${back.标题} / 网格或空态 = ${back.网格或空态} / 残留条目 = ${back.回收站条目}`)

  // ---------------------------------------------------------------- 6 空回收站
  emptyMode = true
  await page.$$eval('.library-header .lib-actions button', (els) => els.find((b) => b.textContent.trim() === '回收站').click())
  await sleep(900)
  const empty = await page.evaluate(() => ({
    标题: document.querySelector('.library-header h1')?.textContent.trim() || '',
    副标题: document.querySelector('.library-header p')?.textContent.trim() || '',
    空态: document.querySelectorAll('.empty-library').length,
    空态文案: document.querySelector('.empty-library h2')?.textContent.trim() || '',
    空态按钮: document.querySelector('.empty-library button')?.textContent.trim() || '',
  }))
  check('真的没有条目时显示空态（而不是「共 0 件」）',
    empty.空态 === 1 && empty.空态文案 === '回收站是空的' && !empty.副标题.includes('共 0 件'),
    `${empty.空态文案} / ${empty.副标题}`)
  check('空态给一条回作品的路', empty.空态按钮 === '回到我的作品', empty.空态按钮)

  check('全程没有 JS 报错', pageErrors.length === 0, pageErrors.join(' | '))

  await sleep(300)
  await page.screenshot({ path: `${OUT}/trash-empty.png` })
  emptyMode = false
  await page.$$eval('.library-header .lib-actions button', (els) => els.find((b) => b.textContent.trim() === '刷新').click())
  await sleep(800)
  await page.screenshot({ path: `${OUT}/trash-list.png` })
  console.log('\n  shot ->', `${OUT}/trash-list.png`, '|', `${OUT}/trash-empty.png`)
} finally {
  await browser.close()
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

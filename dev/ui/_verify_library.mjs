// 核对「我的作品」页顶部的区块（2026-09-19 斌哥定）：
//   保留 → 页面标题「每个故事，都在这里。」+「＋ 新建作品」按钮、筛选栏、作品网格/空态
//   撤掉 → 01 写下你的故事创作区（PromptComposer）+ 三张灵感卡片
// 跑法：E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_library.mjs
//      验生产包换成 E2E_URL=http://127.0.0.1:8000/
//
// ⚠️ 不烧任何额度：只切视图 + 读 DOM，不点生成、不建作品、不动配置。
// 这条脚本的价值在**反向断言** —— 撤掉的东西最容易在后续改动里自己长回来。
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

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)
  console.log('='.repeat(74))
  console.log('「我的作品」页顶部区块 · 前端 UI 验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  await page.$$eval('.nav-item', (els) => els.find((e) => e.textContent.includes('我的作品')).click())
  await sleep(1400)

  const probe = await page.evaluate(() => ({
    标题区: document.querySelectorAll('.library-header').length,
    标题文案: document.querySelector('.library-header h1')?.textContent.trim() || '',
    头部按钮: [...document.querySelectorAll('.library-header .lib-actions button')].map((b) => b.textContent.trim()),
    新建按钮: !!document.querySelector('.library-header .lib-actions button.primary'),
    创作区: document.querySelectorAll('#creative-brief, .brief-section').length,
    编排器: document.querySelectorAll('.composer').length,
    灵感卡片: document.querySelectorAll('.inspiration-card, .inspiration-grid').length,
    筛选栏: document.querySelectorAll('.library-toolbar').length,
    筛选标签: [...document.querySelectorAll('.filter-tabs button')].map((b) => b.textContent.trim()),
    列表或空态: document.querySelectorAll('.project-grid, .empty-library').length,
    回收站条目: document.querySelectorAll('.trash-item').length,
    // 这些文案只可能来自被撤掉的那块（PromptComposer + 它的标题与脚注）
    残留文案: ['写下你的故事', '一个念头，就能开场', '灵感创作', '剧本改编', 'CREATIVE BRIEF',
      '生成并发', '添加角色', '每个阶段都由你确认后继续']
      .filter((t) => document.body.innerText.includes(t)),
  }))
  console.log('探针:', JSON.stringify(probe, null, 2), '\n')

  check('页面标题「每个故事，都在这里。」保留',
    probe.标题区 === 1 && probe.标题文案 === '每个故事，都在这里。', probe.标题文案)
  check('「＋ 新建作品」按钮保留', probe.新建按钮)
  check('头部按钮是「回收站」+「＋ 新建作品」（回收站是 2026-09-19 加的入口）',
    probe.头部按钮.join('/') === '回收站/＋ 新建作品', probe.头部按钮.join(' / '))
  check('回收站视图没被误渲染在作品页', probe.回收站条目 === 0, `数量 = ${probe.回收站条目}`)
  check('创作区（#creative-brief / .brief-section）已撤',
    probe.创作区 === 0, `数量 = ${probe.创作区}`)
  check('PromptComposer 不再渲染', probe.编排器 === 0, `.composer 数量 = ${probe.编排器}`)
  check('灵感卡片已撤（含 .inspiration-grid 容器）',
    probe.灵感卡片 === 0, `数量 = ${probe.灵感卡片}`)
  check('被撤区块的文案一个都不该残留',
    probe.残留文案.length === 0, probe.残留文案.join(' / '))
  check('筛选栏还在且仍是 4 个标签',
    probe.筛选栏 === 1 && probe.筛选标签.length === 4, probe.筛选标签.join(' / '))
  check('作品网格或空态恰好有一个', probe.列表或空态 === 1, `数量 = ${probe.列表或空态}`)
  check('页面没有 JS 报错（组件已注释，composerRef 为 null 也不能抛错）',
    pageErrors.length === 0, pageErrors.join(' | '))

  await sleep(400)
  await page.screenshot({ path: `${OUT}/library.png` })
  console.log('\n  shot ->', `${OUT}/library.png`)
} finally {
  await browser.close()
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

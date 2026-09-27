// 核对「其他图片」的 AI 生成入口：卡片上要有「AI 生成」、点开是画面弹窗、
// 音频类不该有这颗按钮、角色/场景/道具的老入口不能掉。
// 跑法：E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_other_image_ui.mjs
//      （默认打 5173 dev；验生产 dist 用 E2E_URL=http://127.0.0.1:8000/）
//
// ⚠️ 只开到弹窗出现为止，**不点提交**，所以不烧任何出图额度。
// 会真建一个 draft 作品，脚本自己记下 task_id 并在 finally 里 DELETE 掉。
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
  defaultViewport: { width: 1440, height: 1200 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

let createdTask = ''
page.on('response', async (res) => {
  if (res.url().endsWith('/api/tasks/draft') && res.request().method() === 'POST') {
    try { createdTask = (await res.json()).task_id || createdTask } catch { /* ignore */ }
  }
})

async function shot(name) {
  await sleep(400)
  const el = await page.$('.step')
  if (el) { await el.screenshot({ path: `${OUT}/${name}.png` }); console.log('  shot ->', `${OUT}/${name}.png`) }
}

async function pickKind(label) {
  if (!(await page.$('.addform'))) {
    await page.click('.head-actions .btn-ghost')
    await sleep(250)
  }
  await page.$$eval('.addform .af-kinds button',
    (els, k) => els.find((e) => e.textContent.trim() === k).click(), label)
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await sleep(300)
}

// 第 n 组（1 起）里第一张卡片的操作按钮文案 + 空态文案
async function cardInfo(n) {
  return page.evaluate((idx) => {
    const g = document.querySelector(`.mgroups .mgroup:nth-child(${idx})`)
    const card = g?.querySelector('.mcard')
    return {
      组: g?.querySelector('.mlabel')?.textContent.trim() || '',
      卡片名: card?.querySelector('.mmeta strong')?.textContent.trim() || '',
      说明: card?.querySelector('.mmeta p')?.textContent.trim() || '',
      空态: card?.querySelector('.cempty')?.textContent.trim() || '',
      操作: [...(card?.querySelectorAll('.mops button') || [])].map((b) => b.textContent.trim()),
    }
  }, n)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)
  console.log('='.repeat(74))
  console.log('「其他图片」AI 生成 · 前端 UI 验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // 1) 建一张「其他图片」卡片并保存（保存只 PATCH 名字，不出图）
  await pickKind('其他图片')

  // 2026-09-19：「AI 一键生成全部素材」从 01 的标题行撤掉了
  // （斌哥：「你啥都没有，点这个意义不大」）。head-actions 里应该只剩「添加素材」。
  const headBtns = await page.evaluate(() =>
    [...document.querySelectorAll('.head-actions button')].map((b) => b.textContent.trim()))
  check('01 标题行里没有「一键生成全部素材」了',
    !headBtns.some((t) => t.includes('一键生成')), JSON.stringify(headBtns))
  check('「添加素材」还在（只摘渲染，别误伤）',
    headBtns.some((t) => t.includes('添加素材')), JSON.stringify(headBtns))
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '天台黄昏')
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1500)
  const img = await cardInfo(4)
  console.log('探针 1 其他图片卡片:', JSON.stringify(img))
  check('其他图片卡片上有「AI 生成」', img.操作.includes('AI 生成'), img.操作.join(' / '))
  check('空态文案两种途径都写了',
    img.空态.includes('待上传') && img.空态.includes('待生成'), img.空态)
  check('卡片说明也写了"上传或生成"', img.说明.includes('上传或生成'), img.说明)
  await shot('otherimage_card')

  // 2) 点开弹窗：标题 / 提示 / 占位符都该是"画面"那套
  await page.evaluate(() => {
    const card = document.querySelector('.mgroups .mgroup:nth-child(4) .mcard')
    ;[...card.querySelectorAll('.mops button')].find((b) => b.textContent.trim() === 'AI 生成').click()
  })
  await page.waitForSelector('.pdialog', { timeout: 8000 })
  await sleep(400)
  const dlg = await page.evaluate(() => ({
    标题: document.querySelector('.pd-head h3')?.textContent.trim() || '',
    提示: document.querySelector('.pd-hint')?.textContent.trim() || '',
    占位: document.querySelector('.pdialog textarea')?.getAttribute('placeholder') || '',
    选中比例: document.querySelector('.pd-ratio .seg button.on')?.textContent.trim() || '',
    有比例选择: !!document.querySelector('.pd-ratio'),
  }))
  console.log('探针 2 弹窗:', JSON.stringify(dlg, null, 0))
  check('弹窗标题说"图片"', dlg.标题.includes('图片'), dlg.标题)
  check('提示语讲的是完整画面 / 首尾帧', dlg.提示.includes('画面') && dlg.提示.includes('首帧'), dlg.提示)
  check('占位符是画面示例（不是道具/场景那套）', dlg.占位.includes('黄昏的天台'), dlg.占位)
  check('给了比例选择（道具才固定 16:9）', dlg.有比例选择 && !!dlg.选中比例, dlg.选中比例)
  await shot('otherimage_dialog')

  // 关掉弹窗，别让它挡住后面的截图
  await page.click('.pd-head .btn-ghost')
  await sleep(400)

  // 3) 老入口回归：角色 / 场景 / 道具都还得有「AI 生成」
  for (const [n, label] of [[1, '角色'], [2, '场景'], [3, '道具']]) {
    await pickKind(label)
    await page.type('.mgrid .edit-form input[aria-label="素材名字"]', `${label}回归`)
    await page.click('.mgrid .edit-form .btn-primary')
    await sleep(1500)
    const info = await cardInfo(n)
    check(`${label}卡片仍有「AI 生成」`, info.操作.includes('AI 生成'), info.操作.join(' / '))
  }

  // 4) 音频类不该有「AI 生成」（后端也会拒）
  await pickKind('其他音频')
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '回归音频')
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1500)
  const aud = await cardInfo(6)
  console.log('探针 4 其他音频卡片:', JSON.stringify(aud))
  check('其他音频卡片没有「AI 生成」', !aud.操作.includes('AI 生成'), aud.操作.join(' / '))
  await sleep(6000) // 等提示条退掉再截图
  await shot('after_all')
} finally {
  await browser.close()
  if (createdTask) {
    const r = await fetch(`http://127.0.0.1:8000/api/tasks/${createdTask}?confirm=true&purge=true`, { method: 'DELETE' })
    console.log('\n清理测试作品', createdTask, '->', r.status)
  } else {
    console.log('\n⚠️ 没抓到 task_id，请手动 GET /api/tasks 检查有没有残留 draft')
  }
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

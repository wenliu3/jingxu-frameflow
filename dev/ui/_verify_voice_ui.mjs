// 核对「角色音频」的 AI 生成入口：卡片上要有「AI 生成」、弹窗里是性别选择（不是比例）、
// 提示语要说明"音色是预置的、只能挑不能造"。
// 跑法：E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_voice_ui.mjs
//
// ⚠️ 只开到弹窗出现为止，**不点提交**，所以不会真调模型。
// 会真建一个 draft 作品（+ 一个空角色），脚本自己记下 task_id 并在 finally 里 DELETE 掉。
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
    await page.click('.head-actions .add-material')
    await sleep(250)
  }
  await page.$$eval('.addform .af-kinds button',
    (els, k) => els.find((e) => e.textContent.trim() === k).click(), label)
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await sleep(300)
}

async function cardInfo(n) {
  return page.evaluate((idx) => {
    const g = document.querySelector(`.mgroups .mgroup:nth-child(${idx})`)
    const card = g?.querySelector('.mcard')
    return {
      组: g?.querySelector('.mlabel')?.textContent.trim() || '',
      卡片名: card?.querySelector('.mmeta strong')?.textContent.trim() || '',
      说明: card?.querySelector('.mmeta p')?.textContent.trim() || '',
      操作: [...(card?.querySelectorAll('.mops button') || [])].map((b) => b.textContent.trim()),
    }
  }, n)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)
  console.log('='.repeat(74))
  console.log('「AI 生成音色」· 前端 UI 验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // 1) 先建一个角色 —— 角色音频组是按「角色」列行的，没角色就没有卡片
  await pickKind('角色')
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '验证角色')
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1600)

  // 2) 角色音频组（第 5 组）的卡片
  const voice = await cardInfo(5)
  console.log('探针 1 角色音频卡片:', JSON.stringify(voice))
  check('角色音频卡片上有「AI 生成」', voice.操作.includes('AI 生成'), voice.操作.join(' / '))
  await shot('voice_card')

  // 3) 点开弹窗：标题、提示、性别选择
  await page.evaluate(() => {
    const card = document.querySelector('.mgroups .mgroup:nth-child(5) .mcard')
    ;[...card.querySelectorAll('.mops button')].find((b) => b.textContent.trim() === 'AI 生成').click()
  })
  await page.waitForSelector('.pdialog', { timeout: 8000 })
  await sleep(400)
  const dlg = await page.evaluate(() => ({
    标题: document.querySelector('.pd-head h3')?.textContent.trim() || '',
    提示: document.querySelector('.pd-hint')?.textContent.trim() || '',
    占位: document.querySelector('.pdialog textarea')?.getAttribute('placeholder') || '',
    标签: document.querySelector('.pd-ratio .pd-rlabel')?.textContent.trim() || '',
    选项: [...document.querySelectorAll('.pd-ratio .seg button')].map((b) => b.textContent.trim()),
    选中: document.querySelector('.pd-ratio .seg button.on')?.textContent.trim() || '',
    上限: document.querySelector('.pd-foot .pd-count:last-of-type')?.textContent.trim() || '',
  }))
  console.log('探针 2 弹窗:', JSON.stringify(dlg, null, 0))
  check('弹窗标题说"音色"', dlg.标题.includes('音色'), dlg.标题)
  check('提示语讲的是"从音色池里挑"而不是凭空造',
    dlg.提示.includes('预置') && dlg.提示.includes('挑'), dlg.提示)
  check('占位符是声音示例', dlg.占位.includes('沙哑') || dlg.占位.includes('男声'), dlg.占位)
  check('选项是性别（女声 / 男声），不是比例',
    dlg.标签 === '性别' && dlg.选项.join(',') === '女声,男声', `${dlg.标签} ${dlg.选项.join('/')}`)
  check('默认选中一个性别（不空着）', ['女声', '男声'].includes(dlg.选中), dlg.选中)
  check('字数上限按音色的 500 走（不是图片的 1000）', dlg.上限.includes('500'), dlg.上限)
  await shot('voice_dialog')

  // 4) 切到男声，确认状态真的变了（不是个死按钮）
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll('.pd-ratio .seg button')]
    btns.find((b) => b.textContent.trim() === '男声').click()
  })
  await sleep(300)
  const picked = await page.$eval('.pd-ratio .seg button.on', (b) => b.textContent.trim())
  check('点「男声」能切过去', picked === '男声', picked)

  await page.click('.pd-head .btn-ghost')
  await sleep(400)

  // 5) 回归：其他音频仍然没有「AI 生成」（音频池是给角色的，其他音频是上传型）
  await pickKind('其他音频')
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '回归音频')
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1500)
  const aud = await cardInfo(6)
  console.log('探针 3 其他音频卡片:', JSON.stringify(aud))
  check('其他音频卡片没有「AI 生成」', !aud.操作.includes('AI 生成'), aud.操作.join(' / '))

  // 6) 回归：图片类的弹窗还得是"比例"，不能被性别选择顶掉
  await pickKind('其他图片')
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '回归图片')
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1500)
  await page.evaluate(() => {
    const card = document.querySelector('.mgroups .mgroup:nth-child(4) .mcard')
    ;[...card.querySelectorAll('.mops button')].find((b) => b.textContent.trim() === 'AI 生成').click()
  })
  await page.waitForSelector('.pdialog', { timeout: 8000 })
  await sleep(400)
  const imgDlg = await page.evaluate(() => ({
    标签: document.querySelector('.pd-ratio .pd-rlabel')?.textContent.trim() || '',
    选项: [...document.querySelectorAll('.pd-ratio .seg button')].map((b) => b.textContent.trim()),
    上限: document.querySelector('.pd-foot .pd-count:last-of-type')?.textContent.trim() || '',
  }))
  console.log('探针 4 图片弹窗:', JSON.stringify(imgDlg))
  check('图片弹窗仍是比例选择', imgDlg.标签 === '比例' && imgDlg.选项.includes('16:9'), imgDlg.选项.join('/'))
  check('图片弹窗字数上限仍是 1000', imgDlg.上限.includes('1000'), imgDlg.上限)

  await sleep(5000)
  await shot('voice_after_all')
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

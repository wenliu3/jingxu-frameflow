// 验证「让 AI 帮写」按钮的接线（2026-09-19 加）。
//
// 斌哥的要求：点这个按钮要**先优化提示词、给出中文**（用户只看中文）；
// 点「生成视频」时后端再把它编成英文正文 —— 那一步本来就是"中文进、英文出"，没动。
//
// 这个脚本卡的是**接线**，不是模型质量（质量在 _verify_optimize_prompt.py 里看）：
//   ① 标题行只有「让 AI 帮写」一颗 —— 「我自己写英文」已撤
//      （斌哥：「不用自己写英文啊…后端会把你提示词翻译成英文，而你自己看不到」）
//   ② 没写描述时按钮禁用；写了就能点
//   ③ 点一下**只调一次** /prompt/optimize，返回值**直接写回输入框**（不是又一层只读预览）
//   ④ 页面任何地方都不该再出现手写模式的字样（「我自己写英文」/「回到 AI 帮写」/ 英文输入框）
//
// ⚠️ **不烧额度**：拦截 /prompt/optimize 回罐头响应，不调文本模型；
//    /segment/video 也拦掉 —— 这个脚本根本不点生成。
//
// 跑法：node dev/ui/_verify_prompt_ui.mjs                （默认打 8000 生产包）
//       E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_prompt_ui.mjs
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const API = process.env.E2E_API || 'http://127.0.0.1:8000'
const FACE = 'D:/Pychrom Project/ai_video_multiagent/dev/e2e/_e2e_face.png'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function check(name, cond, extra = '') {
  results.push(!!cond)
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? `\n        → ${extra}` : ''))
}

// 罐头响应：形状与 /prompt/optimize 一致（server/app.py）
const CANNED_ZH = '【罐头】黄昏的海边，一个人站在湿沙上，面朝大海。镜头从侧后方中景缓慢往前推，' +
  '推到他半个侧脸装满画面下缘时停住。'
const CANNED = { description: CANNED_ZH, materials: ['角色「林晚」'] }

let taskId = ''
try {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--disable-gpu', '--no-sandbox'],
    defaultViewport: { width: 1440, height: 1000 },
  })
  const page = await browser.newPage()
  const pageErrors = []
  page.on('pageerror', (e) => { pageErrors.push(e.message); console.log('[pageerror]', e.message) })

  await page.setRequestInterception(true)
  let optimizeHits = 0
  page.on('request', (req) => {
    const u = req.url()
    if (u.includes('/prompt/optimize') && req.method() === 'POST') {
      optimizeHits += 1
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(CANNED) })
    }
    if (u.includes('/segment/video')) {
      return req.respond({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: '（验证脚本拦下的）' }) })
    }
    return req.continue()
  })

  console.log('='.repeat(74))
  console.log('「让 AI 帮写」按钮接线验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 0) 建作品 + 传一张素材（走 API，免费） ----------
  const draft = await (await fetch(`${API}/api/tasks/draft`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '优化提示词UI验证' }),
  })).json()
  taskId = draft.task_id
  const fs = await import('node:fs')
  const dataB64 = fs.readFileSync(FACE).toString('base64')
  for (const [kind, name] of [['character', '林晚.png'], ['scene', '海边.png']]) {
    await fetch(`${API}/api/tasks/${taskId}/materials/upload?kind=${kind}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: name, data_b64: dataB64 }),
    })
  }
  check('建出测试作品并传了两张素材', !!taskId, taskId)

  // ---------- 1) 进工作台 · 分镜工作台 ----------
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(800)
  const clickByText = (sel, text) => page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
    if (el) { el.click(); return true }
    return false
  }, sel, text)

  await clickByText('.nav-item', '我的作品')
  await sleep(1200)
  const tiles = await page.$$('.project-open')
  await tiles[0].click()
  await sleep(2200)
  await clickByText('.mode-switch button', '分镜工作台')
  await sleep(1600)
  await page.evaluate(() => {
    document.querySelectorAll('.picklist .pl-item').forEach((b) => b.click())
  })
  await sleep(600)

  // ---------- 2) 标题行只剩一颗按钮 ----------
  const head = await page.evaluate(() => {
    const popt = document.querySelector('.poptim')
    return {
      优化按钮: popt ? popt.textContent.trim() : '(没有)',
      优化禁用: popt ? popt.disabled : null,
      标题行按钮数: document.querySelectorAll('.pvhead button').length,
      手写按钮数: document.querySelectorAll('.pmode').length,
      输入框: [...document.querySelectorAll('.compose textarea')].map((t) => t.getAttribute('aria-label')),
      手写字样: ['我自己写英文', '回到 AI 帮写', '我自己写提示词'].filter((t) => document.body.innerText.includes(t)),
    }
  })
  console.log('探针 初始:', JSON.stringify(head, null, 2))
  check('标题行只有「让 AI 帮写」一颗', head.优化按钮 === '让 AI 帮写' && head.标题行按钮数 === 1,
    `${head.优化按钮} / 共 ${head.标题行按钮数} 颗`)
  check('「我自己写英文」已撤', head.手写按钮数 === 0, `.pmode 数量 ${head.手写按钮数}`)
  check('页面不再出现手写模式的字样', head.手写字样.length === 0, head.手写字样.join(' / '))
  check('没写描述时优化按钮是禁用的', head.优化禁用 === true, String(head.优化禁用))
  check('输入框是中文那个（英文那个没入口了）', head.输入框.includes('视频提示词'), head.输入框.join(' / '))

  // ---------- 3) 写一句 → 点优化 → 中文写回输入框 ----------
  await page.type('textarea[aria-label="视频提示词"]', '一个人在海边等日落')
  await sleep(300)
  const enabled = await page.$eval('.poptim', (el) => !el.disabled)
  check('写了描述后优化按钮可点', enabled)

  await page.click('.poptim')
  await sleep(1800)
  const after = await page.evaluate(() => ({
    值: document.querySelector('textarea[aria-label="视频提示词"]')?.value || '',
    优化按钮: document.querySelector('.poptim')?.textContent.trim() || '',
  }))
  console.log('探针 点完之后:', JSON.stringify(after))
  check('只调了一次 /prompt/optimize', optimizeHits === 1, `命中 ${optimizeHits} 次`)
  check('返回值直接写回输入框（用户能接着改）', after.值 === CANNED_ZH, after.值.slice(0, 40))
  check('按钮文案复原成「让 AI 帮写」', after.优化按钮 === '让 AI 帮写', after.优化按钮)

  // ---------- 4) 手写那条路的入口确实没了 ----------
  const noManual = await page.evaluate(() => ({
    英文框数: document.querySelectorAll('textarea[aria-label="视频英文提示词"]').length,
    手写字样: ['我自己写英文', '回到 AI 帮写', '手写模式'].filter((t) => document.body.innerText.includes(t)),
    中文框的值: document.querySelector('textarea[aria-label="视频提示词"]')?.value || '',
    优化按钮: document.querySelector('.poptim')?.textContent.trim() || '(没有)',
  }))
  console.log('探针 手写入口:', JSON.stringify(noManual, null, 2))
  check('英文输入框不再渲染', noManual.英文框数 === 0, `数量 ${noManual.英文框数}`)
  check('页面上没有手写模式的字样', noManual.手写字样.length === 0, noManual.手写字样.join(' / '))
  check('优化后的中文仍在框里、按钮还在', noManual.优化按钮 === '让 AI 帮写' && noManual.中文框的值 === CANNED_ZH,
    `${noManual.优化按钮} / 值长度 ${noManual.中文框的值.length}`)

  await sleep(300)
  await page.screenshot({ path: 'docs/_shots/prompt_optimize.png' })
  console.log('\n  shot -> docs/_shots/prompt_optimize.png')
  check('页面没有 JS 报错', pageErrors.length === 0, pageErrors.join(' | '))

  await browser.close()
} finally {
  if (taskId) {
    const r = await fetch(`${API}/api/tasks/${taskId}?confirm=true&purge=true`, { method: 'DELETE' })
    console.log('\n清理测试作品', taskId, '->', r.status)
  } else {
    console.log('\n⚠️ 没抓到 task_id，请手动 GET /api/tasks 检查有没有残留 draft')
  }
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

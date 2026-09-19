// 核对「添加素材」新流程：点添加 → 只弹 5 个类型 → 选一个就在那一组里新建一张空卡片
// → 在那张卡片上填名字 + 传内容。
// 跑法：E2E_URL=http://127.0.0.1:5173/ node _shots_add.mjs
//
// ⚠️ 会真在磁盘上建一个 draft 作品（素材要有作品才挂得住）。脚本自己记下 task_id
// 并在 finally 里 DELETE 掉，不留垃圾。全程 design:false，不烧任何模型额度。
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:5173/'
const OUT = 'docs/_shots'
// 只用来验「新卡片上能直接传内容」，随便一张现成的图
const IMG = 'vibe_images/jingxu-cinematic-landscape_1789278811511_37a92e98.png'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1200 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

// 记下自动建出来的 draft 作品，跑完清掉
let createdTask = ''
page.on('response', async (res) => {
  if (res.url().endsWith('/api/tasks/draft') && res.request().method() === 'POST') {
    try { createdTask = (await res.json()).task_id || createdTask } catch { /* ignore */ }
  }
})

async function shot(name) {
  await sleep(400)
  const el = await page.$('.step')
  await el.screenshot({ path: `${OUT}/${name}.png` })
  console.log('shot ->', `${OUT}/${name}.png`)
}

// 点「添加素材」→ 选类型（模拟真人点，不走表单填值）
async function pickKind(label) {
  const t0 = Date.now()
  // 已经开着就别再点一下（那个按钮是开关，会把它关掉）
  if (!(await page.$('.addform'))) {
    await page.click('.head-actions .btn-ghost')
    await sleep(250)
  }
  await page.$$eval('.addform .af-kinds button',
    (els, k) => els.find((e) => e.textContent.trim() === k).click(), label)
  // 等编辑态出现（= 后端已经落好条目、列表也刷新完了）
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await sleep(300)
  return Date.now() - t0
}

// 第 n 组（1 起）里的卡片信息
async function groupInfo(n) {
  return page.evaluate((idx) => {
    const g = document.querySelector(`.mgroups .mgroup:nth-child(${idx})`)
    if (!g) return null
    const form = g.querySelector('.edit-form')
    const inputs = form ? [...form.querySelectorAll('input')] : []
    const nameBox = inputs.find((i) => i.type !== 'file')
    return {
      组: g.querySelector('.mlabel')?.textContent.trim(),
      卡片数: g.querySelectorAll('.mcard').length,
      编辑态: !!form,
      名字框: !!nameBox,
      名字框是空的: nameBox ? nameBox.value === '' : null,
      名字框已聚焦: nameBox ? document.activeElement === nameBox : null,
      有描述框: inputs.filter((i) => i.type !== 'file').length > 1,
      有上传入口: !!form?.querySelector('label.mup input[type="file"]'),
      卡片名: g.querySelector('.mmeta strong')?.textContent.trim() || '',
    }
  }, n)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)

  // 1) 点「添加素材」：只应该有 5 个选择，且没有名字/描述输入框
  await page.click('.head-actions .btn-ghost')
  await sleep(300)
  const picker = await page.evaluate(() => ({
    选项: [...document.querySelectorAll('.addform .af-kinds button')].map((b) => b.textContent.trim()),
    表单里的输入框: document.querySelectorAll('.addform input').length,
    有添加按钮: !!document.querySelector('.addform .btn-primary'),
  }))
  console.log('探针 1 类型选择器:', JSON.stringify(picker, null, 0))
  await shot('add_picker')

  // 2) 选「场景」→ 场景组（第 2 组）应立刻多一张空卡片，且直接进编辑态
  const t = await pickKind('场景')
  console.log(`探针 2 选「场景」到卡片出现耗时 ${t}ms（不含模型调用才可能这么快）`)
  console.log('探针 2 场景组:', JSON.stringify(await groupInfo(2)))
  await shot('add_newcard')

  // 3) 就在这张新卡片上：填名字 + 传一张图
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '雨夜便利店')
  const fileInput = await page.$('.mgrid .edit-form label.mup input[type="file"]')
  await fileInput.uploadFile(IMG)
  await sleep(2500)
  console.log('探针 3 上传后（仍在编辑态）:', JSON.stringify(await groupInfo(2)))

  // 4) 保存 → 名字落库、缩略图出来
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1600)
  const after = await page.evaluate(() => {
    const g = document.querySelector('.mgroups .mgroup:nth-child(2) .mcard')
    return {
      卡片名: g?.querySelector('.mmeta strong')?.textContent.trim() || '',
      说明: g?.querySelector('.mmeta p')?.textContent.trim() || '',
      有图: !!g?.querySelector('.mthumb img'),
      还在编辑态: !!g?.querySelector('.edit-form'),
    }
  })
  console.log('探针 4 保存后:', JSON.stringify(after))
  await shot('add_named')

  // 5) 角色组：新卡片该有「描述」框；其他音频组：不该有描述框（原始素材没有锚点）
  await pickKind('角色')
  console.log('探针 5 角色组:', JSON.stringify(await groupInfo(1)))
  const cancelLabel = await page.$eval('.mgrid .edit-form .btn-ghost', (b) => b.textContent.trim())
  await page.click('.mgrid .edit-form .btn-ghost')
  await sleep(1400)
  console.log('探针 5 空卡片上那颗退出按钮的文案:', cancelLabel)
  console.log('探针 5 点完它之后的角色组:', JSON.stringify(await groupInfo(1)))

  await pickKind('其他音频')
  console.log('探针 5 其他音频组:', JSON.stringify(await groupInfo(6)))
  await sleep(7000)  // 等提示条自己退掉，别挡版式
  await shot('add_audio_newcard')
} finally {
  await browser.close()
  if (createdTask) {
    const r = await fetch(`http://127.0.0.1:8000/api/tasks/${createdTask}?confirm=true&purge=true`, { method: 'DELETE' })
    console.log('清理测试作品', createdTask, '->', r.status)
  } else {
    console.log('⚠️ 没抓到 task_id，请手动 GET /api/tasks 检查有没有残留 draft')
  }
}

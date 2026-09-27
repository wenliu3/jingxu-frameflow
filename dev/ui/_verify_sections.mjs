// 核对 01/02 两个区块的新分布（2026-09-17 斌哥定）：
//   新建作品页        → 只有 01 准备素材（且右侧 AI 助手已撤）
//   工作台·素材工坊 tab → 只有 01
//   工作台·分镜工作台 tab → 只有 02 生成视频，且自带一条素材勾选条
//                          （阶段进度条 StageRail、素材大块 ProjectBrief 都已撤）
// 跑法：E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_sections.mjs
//
// ⚠️ 全程不点「生成视频」、不点 AI 生成，不烧任何额度。
// 会真建一个 draft 作品（+ 一张素材）来验分镜工作台那侧的勾选条，跑完自己 DELETE。
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
  defaultViewport: { width: 1440, height: 1400 },
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
  await sleep(500)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log('  shot ->', `${OUT}/${name}.png`)
}

// 当前页面上「01 / 02」两块的大标题
async function steps() {
  return page.evaluate(() =>
    [...document.querySelectorAll('.step .step-head h2')].map((e) => e.textContent.trim()))
}

async function assistantCount() {
  return page.evaluate(() => document.querySelectorAll('.create-assistant').length)
}

async function picklistInfo() {
  return page.evaluate(() => {
    const box = document.querySelector('.picklist')
    return {
      有勾选条: !!box,
      条目: box ? [...box.querySelectorAll('.pl-item em')].map((e) => e.textContent.trim()) : [],
    }
  })
}

async function switchTab(label) {
  await page.evaluate((t) => {
    const btn = [...document.querySelectorAll('.mode-switch button')].find((b) => b.textContent.trim() === t)
    if (btn) btn.click()
  }, label)
  await sleep(1600)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)
  console.log('='.repeat(74))
  console.log('01 / 02 区块分布 · 前端 UI 验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // 先建一张「其他图片」素材 —— 分镜工作台那侧的勾选条要有东西可勾
  await page.click('.head-actions .btn-ghost')
  await sleep(300)
  await page.$$eval('.addform .af-kinds button',
    (els) => els.find((e) => e.textContent.trim() === '其他图片').click())
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '分段验证图')
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1800)

  // ---------- 1) 新建作品页 ----------
  const s1 = await steps()
  console.log('探针 1 新建作品页区块:', JSON.stringify(s1))
  check('新建作品页只有 01 准备素材', s1.length === 1 && s1[0] === '准备素材', s1.join(' / '))
  check('新建作品页没有 02 生成视频', !s1.includes('生成视频'), s1.join(' / '))
  const a1 = await assistantCount()
  check('右侧 AI 助手已撤掉', a1 === 0, `.create-assistant 数量 = ${a1}`)
  const shell = await page.evaluate(() => {
    const el = document.querySelector('.create-shell')
    return { 子元素数: el ? el.children.length : -1, 列数: el ? getComputedStyle(el).gridTemplateColumns.split(' ').length : -1 }
  })
  console.log('探针 1 外壳:', JSON.stringify(shell))
  check('外壳变成单列（不再留 250px 空位）', shell.列数 === 1, shell.列数 + ' 列')
  await shot('sections_create')

  // ---------- 2) 工作台 · 素材工坊 tab ----------
  await page.$$eval('.nav-item', (els) => els.find((e) => e.textContent.includes('我的作品')).click())
  await sleep(1200)
  const tiles = await page.$$('.project-open')
  if (!tiles.length) {
    console.log('  ⚠️ 作品列表为空，后面两项验不了')
    results.push(false)
  } else {
    await tiles[0].click()   // 列表按创建时间倒序，第一个就是刚建的 draft
    await sleep(2500)
    await switchTab('素材工坊')
    const s2 = await steps()
    console.log('探针 2 素材工坊 tab 区块:', JSON.stringify(s2))
    check('素材工坊 tab 只有 01 准备素材', s2.length === 1 && s2[0] === '准备素材', s2.join(' / '))
    check('素材工坊 tab 没有 02', !s2.includes('生成视频'), s2.join(' / '))
    await shot('sections_studio')

    // ---------- 3) 工作台 · 分镜工作台 tab ----------
    await switchTab('分镜工作台')
    const s3 = await steps()
    console.log('探针 3 分镜工作台 tab 区块:', JSON.stringify(s3))
    check('分镜工作台里有 02 生成视频', s3.includes('生成视频'), s3.join(' / '))
    check('分镜工作台里没有 01 准备素材（不在两处重复）', !s3.includes('准备素材'), s3.join(' / '))
    const gone = await page.evaluate(() => ({
      工具条: document.querySelectorAll('.production-toolbar').length,
      残留文案: ['分块工作台', '分镜工作区', 'AI 生成下一块', '批量生成视频', '手动添加一块']
        .filter((t) => document.body.innerText.includes(t)),
    }))
    console.log('探针 3 已撤掉的区块:', JSON.stringify(gone))
    check('「分块工作台 / 分镜工作区」整块已撤',
      gone.工具条 === 0 && gone.残留文案.length === 0,
      `工具条=${gone.工具条} 残留=${gone.残留文案.join(' / ')}`)
    // 素材那一大块（ProjectBrief）也撤了 —— 它属于「素材工坊」，两个 tab 各摆一份
    // 只会让人分不清哪份是真的。.brief 是 ProjectBrief 的根 class，
    // 别和资料库页的 .brief-section 搞混（那个是另一个 class）。
    const brief = await page.evaluate(() => ({
      素材大块: document.querySelectorAll('.brief').length,
      残留文案: ['视觉风格', '添加角色', '角色提示词'].filter((t) => document.body.innerText.includes(t)),
    }))
    console.log('探针 3 已撤掉的素材大块:', JSON.stringify(brief))
    check('素材大块（ProjectBrief）已从分镜工作台撤掉',
      brief.素材大块 === 0 && brief.残留文案.length === 0,
      `数量=${brief.素材大块} 残留=${brief.残留文案.join(' / ')}`)
    // 阶段进度条（StageRail）也撤了 —— 它画的是「故事设定 → 资产准备 → 分块编排 → 合并成片」
    // 那条流水线，正是已经被撤掉的那套。根节点是 <ol class="rail" aria-label="制作阶段">。
    const rail = await page.evaluate(() => ({
      阶段条: document.querySelectorAll('[aria-label="制作阶段"]').length,
      残留文案: ['分块编排', '合并成片'].filter((t) => document.body.innerText.includes(t)),
    }))
    console.log('探针 3 已撤掉的阶段条:', JSON.stringify(rail))
    check('阶段进度条（StageRail）已从分镜工作台撤掉',
      rail.阶段条 === 0 && rail.残留文案.length === 0,
      `数量=${rail.阶段条} 残留=${rail.残留文案.join(' / ')}`)
    const pl = await picklistInfo()
    console.log('探针 3 素材勾选条:', JSON.stringify(pl))
    check('02 自带素材勾选条', pl.有勾选条, JSON.stringify(pl.条目))
    check('刚建的素材出现在勾选条里', pl.条目.includes('分段验证图'), pl.条目.join(' / '))

    // 勾一下：确认这条链路真的能选中（不点生成）
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll('.picklist .pl-item')]
        .find((b) => b.textContent.includes('分段验证图'))
      if (btn) btn.click()
    })
    await sleep(700)
    const picked = await page.evaluate(() => ({
      已选计数: document.querySelector('.picked-panel .pcount')?.textContent.trim() || '',
      已选卡片: [...document.querySelectorAll('.picked-card strong')].map((e) => e.textContent.trim()),
    }))
    console.log('探针 3 勾选后:', JSON.stringify(picked))
    check('勾选后进入「本次视频素材」', picked.已选卡片.includes('分段验证图'), picked.已选卡片.join(' / '))
    await shot('sections_storyboard')
  }
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

// 「AI 生成」弹窗记住上次填的描述 / 比例 / 性别 —— **前端**验证。
//
// 跑法：node dev/ui/_verify_ai_memory_ui.mjs
//      （默认打 5173 dev；验生产 dist 用 E2E_URL=http://127.0.0.1:8000/）
//
// ⚠️ **不烧额度**：只开到弹窗出现为止，**不点提交**，全程不调模型。
//
// 后端那一半（`ai_last` 什么时候写、写什么、上传/自动配音不该写）由
// `dev/api/_verify_ai_memory.py` 覆盖。这里只验**前端读了它之后有没有正确回填**，
// 所以用 puppeteer 把 `GET /api/tasks/{id}` 的响应**改写**成带 `ai_last` 的样子 ——
// 否则要么真跑一次生成（烧额度），要么重启后端（脚本自己做不到）。
//
// 要验的几件事：
//   ① 五类弹窗各自读**自己那个槽**（角色条目上形象图与音色是两个槽，别串）
//   ② 比例回填；道具弹窗没有比例行
//   ③ 存了脏比例（手改过 json）→ 回落默认值，且**必须有一颗 chip 高亮**
//      （脏值直接塞进 v-model 的话，用户看到的是"一个都没选中"）
//   ④ 没生成过的素材 → 描述为空、比例是默认值（不该凭空填东西）
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PAGE_URL = process.env.E2E_URL || 'http://127.0.0.1:5173/'
const OUT = 'docs/_shots'

const CH = '银灰短发的女战士，黑色高领（上次填的形象图描述）'
const VO = '低沉沙哑的中年男声（上次填的音色描述）'
const SC = '雨夜的老旧书店，暖黄吊灯（上次填的场景描述）'
const PR = '掌心大小的黄铜罗盘（上次填的道具描述）'
const IM = '黄昏的天台，主角背对镜头（上次填的图片描述）'
const AT = '2026-09-27T11:39:08.205950+00:00'

// 按**素材名字**决定注入哪一份 ai_last。没列到的名字 = 没生成过，不注入。
const SEED = {
  记忆角色: { character: { prompt: CH, ratio: '16:9', at: AT }, voice: { prompt: VO, gender: 'male', at: AT } },
  记忆场景: { scene: { prompt: SC, ratio: '4:3', at: AT } },
  记忆道具: { prop: { prompt: PR, at: AT } },
  记忆图片: { image: { prompt: IM, ratio: '9:16', at: AT } },
  脏值角色: { character: { prompt: '脏比例的角色', ratio: '7:3', at: AT } },
  脏值场景: { scene: { prompt: '脏比例的场景', ratio: '7:3', at: AT } },
}

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

// ---- 响应改写：给 GET /api/tasks/{12hex} 的响应补上 ai_last ----
await page.setRequestInterception(true)
let seeded = 0
page.on('request', async (req) => {
  const path = new URL(req.url()).pathname
  if (req.method() !== 'GET' || !/^\/api\/tasks\/[0-9a-f]{12}$/.test(path)) {
    return req.continue()
  }
  try {
    const res = await fetch(req.url())
    const body = await res.json()
    const proj = body.project || {}
    for (const c of proj.characters || []) {
      const s = SEED[c.name]
      if (s) { c.ai_last = s; seeded += 1 }
    }
    for (const a of proj.assets || []) {
      const s = SEED[a.name]
      if (s) { a.ai_last = s; seeded += 1 }
    }
    req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  } catch (e) {
    console.log('[seed] 改写失败，放行原响应:', e.message)
    req.continue()
  }
})

let createdTask = ''
page.on('response', async (res) => {
  if (res.url().endsWith('/api/tasks/draft') && res.request().method() === 'POST') {
    try { createdTask = (await res.json()).task_id || createdTask } catch { /* ignore */ }
  }
})

async function shot(name) {
  await sleep(300)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log('  shot ->', `${OUT}/${name}.png`)
}

// 「添加素材」→ 选类型 → 填名字 → 保存（保存只落条目，不出图）
async function addCard(kindLabel, name) {
  if (!(await page.$('.addform'))) {
    await page.click('.head-actions .btn-ghost')
    await sleep(250)
  }
  await page.$$eval('.addform .af-kinds button',
    (els, k) => els.find((e) => e.textContent.trim() === k).click(), kindLabel)
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await sleep(250)
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', name)
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(1400)
}

// 按名字找到卡片上的某颗按钮并点开弹窗
async function openAiFor(name, label = 'AI 生成') {
  const ok = await page.evaluate((nm, lab) => {
    const card = [...document.querySelectorAll('.mgroups .mcard')]
      .find((c) => c.querySelector('.mmeta strong')?.textContent.trim() === nm)
    if (!card) return false
    const btn = [...card.querySelectorAll('.mops button')].find((b) => b.textContent.trim() === lab)
    if (!btn) return false
    btn.click()
    return true
  }, name, label)
  if (!ok) throw new Error(`找不到「${name}」卡片上的「${label}」按钮`)
  await page.waitForSelector('.pdialog', { timeout: 8000 })
  await sleep(350)
}

async function readDialog() {
  return page.evaluate(() => {
    const dlg = document.querySelector('.pdialog')
    return {
      标题: dlg.querySelector('.pd-head h3')?.textContent.trim() || '',
      描述: dlg.querySelector('textarea')?.value ?? null,
      有比例行: !!dlg.querySelector('.pd-ratio'),
      选中: dlg.querySelector('.pd-ratio .seg button.on')?.textContent.trim() || '',
      高亮数: dlg.querySelectorAll('.pd-ratio .seg button.on').length,
    }
  })
}

async function closeDialog() {
  await page.evaluate(() => {
    const dlg = document.querySelector('.pdialog')
    const b = [...dlg.querySelectorAll('.pd-head button')].find((x) => x.textContent.trim() === '关闭')
    ;(b || dlg.querySelector('.pd-head button'))?.click()
  })
  await sleep(350)
}

// 走一遍：开弹窗 → 读 → 关
async function probe(name, label = 'AI 生成') {
  await openAiFor(name, label)
  const d = await readDialog()
  console.log(`  探针「${name}」${label}:`, JSON.stringify(d))
  await closeDialog()
  return d
}

try {
  await page.goto(PAGE_URL, { waitUntil: 'networkidle2' })
  await sleep(900)
  console.log('='.repeat(74))
  console.log('「AI 生成弹窗记忆」前端验证   ' + PAGE_URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 0) 建卡片（每张卡片名决定注入哪份 ai_last） ----------
  await addCard('角色', '记忆角色')
  await addCard('角色', '脏值角色')
  await addCard('角色', '空白角色')
  await addCard('场景', '记忆场景')
  await addCard('场景', '脏值场景')
  await addCard('道具', '记忆道具')
  await addCard('其他图片', '记忆图片')
  check('卡片都建出来了，响应改写也命中了',
    seeded >= 6, `注入 ${seeded} 次（记忆角色/记忆场景/记忆道具/记忆图片/脏值角色/脏值场景）`)

  // ---------- 1) 角色形象：描述 + 比例 ----------
  console.log('\n  —— 角色形象弹窗 ——')
  let d = await probe('记忆角色')
  check('角色形象弹窗回填了上次的描述', d.描述 === CH, String(d.描述))
  check('角色形象弹窗回填了比例 16:9', d.选中 === '16:9', d.选中)
  check('恰好一颗比例 chip 高亮', d.高亮数 === 1, String(d.高亮数))

  // ---------- 2) 音色：同一个角色条目，但**另一个槽** ----------
  console.log('\n  —— 角色音频弹窗（与形象图共用同一条目） ——')
  // ⚠️ 音色卡片在「角色音频」组（第 5 组），名字与角色组那张相同 ——
  //    按名字全局找会先命中角色组那张，所以这里显式限定第 5 组。
  await page.evaluate((nm) => {
    const g = document.querySelectorAll('.mgroups .mgroup')[4]
    const card = [...g.querySelectorAll('.mcard')]
      .find((c) => c.querySelector('.mmeta strong')?.textContent.trim() === nm)
    ;[...card.querySelectorAll('.mops button')].find((b) => b.textContent.trim() === 'AI 生成').click()
  }, '记忆角色')
  await page.waitForSelector('.pdialog', { timeout: 8000 })
  await sleep(350)
  const vd = await readDialog()
  console.log('  探针「音色」:', JSON.stringify(vd))
  await closeDialog()
  check('音色弹窗回填的是**音色那次**的描述（没被形象图顶掉）', vd.描述 === VO, String(vd.描述))
  check('音色弹窗的性别回填成 男声', vd.选中 === '男声', vd.选中)
  check('音色弹窗给的是性别（不是比例）', vd.有比例行 && ['男声', '女声'].includes(vd.选中), vd.选中)

  // ---------- 3) 场景 / 其他图片：各自的描述 + 比例 ----------
  console.log('\n  —— 场景 / 其他图片弹窗 ——')
  d = await probe('记忆场景')
  check('场景弹窗回填了描述', d.描述 === SC, String(d.描述))
  check('场景弹窗回填了比例 4:3', d.选中 === '4:3', d.选中)
  d = await probe('记忆图片')
  check('其他图片弹窗回填了描述', d.描述 === IM, String(d.描述))
  check('其他图片弹窗回填了比例 9:16', d.选中 === '9:16', d.选中)

  // ---------- 4) 道具：只有描述，没有比例行 ----------
  console.log('\n  —— 道具弹窗 ——')
  d = await probe('记忆道具')
  check('道具弹窗回填了描述', d.描述 === PR, String(d.描述))
  check('道具弹窗**没有**比例行（恒 16:9，改说明一句）', d.有比例行 === false)
  // 留一张「弹窗里已经填好上次内容」的截图给人看（必须是**弹窗打开时**拍）
  await openAiFor('记忆场景')
  await page.screenshot({ path: `${OUT}/aimem_dialog_filled.png` })
  console.log('  shot ->', `${OUT}/aimem_dialog_filled.png`)
  await closeDialog()

  // ---------- 5) 脏比例 → 回落默认，且必须有 chip 高亮 ----------
  // 这是最容易漏的一条：脏值直接塞进 aiRatio 的话五颗 chip 全不高亮，
  // 用户看到的是"一个都没选中"，比不给默认值更费解。
  console.log('\n  —— 脏比例（手改过 project.json） ——')
  d = await probe('脏值角色')
  check('脏值角色：描述照常回填', d.描述 === '脏比例的角色', String(d.描述))
  check('脏值角色：比例回落成默认 1:1（不是原样 7:3）', d.选中 === '1:1', d.选中)
  check('脏值角色：仍然恰好一颗 chip 高亮', d.高亮数 === 1, String(d.高亮数))
  d = await probe('脏值场景')
  check('脏值场景：比例回落成作品画幅 16:9', d.选中 === '16:9', d.选中)
  check('脏值场景：仍然恰好一颗 chip 高亮', d.高亮数 === 1, String(d.高亮数))

  // ---------- 6) 没生成过 → 空的，不凭空填 ----------
  console.log('\n  —— 没生成过的素材 ——')
  d = await probe('空白角色')
  check('没生成过的角色：描述是空的', d.描述 === '', JSON.stringify(d.描述))
  check('没生成过的角色：比例是默认 1:1', d.选中 === '1:1', d.选中)

  // ---------- 7) 关掉再打开，值不该被上一次清掉 ----------
  console.log('\n  —— 关掉再打开 ——')
  await openAiFor('记忆场景')
  const before = await readDialog()
  await closeDialog()
  await openAiFor('记忆场景')
  const again = await readDialog()
  await closeDialog()
  check('关掉再打开仍然是回填过的值（不会被清空）',
    again.描述 === before.描述 && again.选中 === before.选中,
    `${before.选中} -> ${again.选中}`)
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

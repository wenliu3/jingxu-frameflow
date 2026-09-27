// 「素材规划助手」弹窗 —— **前端**验证（2026-09-27 加）。
//
// 跑法：node dev/ui/_verify_assistant_ui.mjs
//      （默认打 5173 dev；验生产 dist 用 E2E_URL=http://127.0.0.1:8000/）
//
// ⚠️ **不烧额度**：只有 `POST /api/tasks/{id}/assistant/plan`（唯一会调模型的那一步）
//    被 puppeteer 改写成罐头响应。建 draft、传文档、删文档都走**真后端**（这三步不花钱），
//    所以这条链路是真接通的，不是全靠 mock 摆样子。
//    `GET /api/tasks/{id}` 也被改写：往 project 里补上罐头回复声称"已落库"的那几条素材 ——
//    否则前端刷新后素材区还是空的（模型没真跑，磁盘上就没有那些条目），
//    这样才验得了「发完一轮 → 素材区真的多出带提示词的卡片」。
//
// 要验的几件事：
//   ① 按钮在 01 标题行里、「添加素材」左边
//   ② 弹窗能开、在视口内（fixed 没被外层 transform 困住）、能关
//   ③ 传文档 → chip + 线程里"已读入 xx 字" + 底部"这一轮会读"
//   ④ 发送 → 请求体带对（message / history / docs）、气泡回显、建议 chip
//   ⑤ 刷新后素材区多出卡片，且**每张都显示提示词**、状态是「待生成」
//   ⑥ 删 chip → 附件清单清空，底部说明跟着变
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PAGE_URL = process.env.E2E_URL || 'http://127.0.0.1:5173/'
const OUT = 'docs/_shots'
const API_BASE = process.env.E2E_API || 'http://127.0.0.1:8000'

const DOC_NAME = '剧本.md'
const DOC_TEXT = '# 深山古道\n\n林晚走进古道，遇见书生云舒。\n\n道具：青铜匕首一把。\n'
const ASK = '按这份文档把素材都攒齐'
const REPLY = '从文档里抽到 2 个角色、1 个场景、1 个道具。这些只是条目和提示词，还没出图。'
const SUGGEST = ['核对一下哪条描述要改', '补上传缺的角色']

// 罐头回复声称"已落库"的条目 —— GET /api/tasks/{id} 被改写成真的带上它们
const SEED_CHARS = [
  // 文档写了竖屏 → ratio 9:16
  { name: '林晚', anchor: '十八岁少女，乌黑及腰长发用红绳束起，月白交领襦裙', voice: '清亮的少女音，语速偏快', ratio: '9:16' },
  { name: '云舒', anchor: '二十出头的年轻书生，青灰长衫，束发戴方巾', voice: '温和的男声，语速平缓', ratio: '16:9' },
  // 没有锚点 / 音色 → 卡片上不该出现「提示词」按钮（5d 用它验）
  { name: '无描述角色', anchor: '', voice: '' },
]
const SEED_ASSETS = [
  { kind: 'scene', name: '深山古道', anchor: '青石铺就的山道，两侧古木夹道，晨雾漫过路面，冷调侧光', ratio: '4:3' },
  // ⚠️ 这条**故意不给 ratio** —— 模拟手动添加 / 老素材，弹窗里要显示默认 16:9
  { kind: 'prop', name: '青铜匕首', anchor: '巴掌长的青铜匕首，刃口有细微缺口，柄缠深褐麻绳' },
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function skip(name, why = '') {
  // 环境限制导致这一步验不了 —— 不计入通过率，但**必须打印出来**（别假装通过）
  console.log('  SKIP  ' + name + (why ? `\n        → ${why}` : ''))
}
function check(name, cond, extra = '') {
  results.push(!!cond)
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? `\n        → ${extra}` : ''))
}

const tmpDoc = path.join(os.tmpdir(), DOC_NAME)
fs.writeFileSync(tmpDoc, DOC_TEXT, 'utf-8')

let createdTask = ''
let injectReady = false          // 第一轮发送之后才让"素材区多出卡片"
let planPayload = null           // 捕获前端发出去的 plan 请求体
const patches = []               // 捕获前端发出去的 PATCH（「提示词」弹窗的保存）
const genCalls = []              // 捕获批量「自动生成」发出去的出图请求（顺序即执行顺序）
const confirms = []              // 捕获 window.confirm 的文案
let SLOW = false                 // true → 出图罐头慢 600ms 才回（用来验「生成中…」和「停止」）
let FAIL_PROMPT = ''             // 非空时：prompt 里含这个串的那一项回 500（验单项失败不拖垮整批）

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1000 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
// 批量自动生成前有一道 window.confirm（花钱的动作）。这里自动接受，并把文案记下来断言。
page.on('dialog', async (d) => {
  confirms.push(d.message())
  try { await d.accept() } catch { /* ignore */ }
})
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

await page.setRequestInterception(true)
page.on('request', async (req) => {
  const url = new URL(req.url())
  const p = url.pathname

  // ① 唯一会烧额度的那一步 → 罐头
  if (req.method() === 'POST' && /^\/api\/tasks\/[0-9a-f]{12}\/assistant\/plan$/.test(p)) {
    try { planPayload = JSON.parse(req.postData() || '{}') } catch { planPayload = {} }
    injectReady = true
    return req.respond({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: p.split('/')[3],
        reply: REPLY,
        docs_used: [DOC_NAME],
        created: { characters: ['林晚', '云舒'], assets: ['深山古道', '青铜匕首'] },
        updated: { characters: [], assets: [] },
        rejected: [],
        suggestions: SUGGEST,
      }),
    })
  }

  // ② PATCH 角色/素材（「提示词」弹窗里的保存）→ 并进本地 SEED，**吞掉不打真后端**。
  //    理由同 plan：真作品里根本没有这些条目（模型没真跑），打到后端只会 404。
  //    前端要验的是"请求体对不对、刷新后卡片有没有变"，后端那半边由
  //    `_verify_assistant_plan.py` 的【3c】覆盖。
  if (req.method() === 'PATCH') {
    const m = p.match(/^\/api\/tasks\/[0-9a-f]{12}\/(characters|assets)\/(\d+)$/)
    if (m) {
      let patch = {}
      try { patch = JSON.parse(req.postData() || '{}') } catch { /* ignore */ }
      patches.push({ kind: m[1], index: Number(m[2]), patch })
      const list = m[1] === 'characters' ? SEED_CHARS : SEED_ASSETS
      const target = list[Number(m[2])]
      if (target) Object.assign(target, patch)
      return req.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...(target || {}), ...patch, stale: true }),
      })
    }
  }

  // ③ 出图三个接口 → 罐头。**绝不真调** —— 一次真出图就要花钱，批量更不用说。
  //    批量「自动生成」走的就是这三个接口（和单张「AI 生成」完全同一套）。
  if (req.method() === 'POST') {
    const g = p.match(/^\/api\/tasks\/[0-9a-f]{12}\/(characters|assets)\/(\d+)\/(portrait|generate|voice\/generate)$/)
    if (g) {
      let body = {}
      try { body = JSON.parse(req.postData() || '{}') } catch { /* ignore */ }
      genCalls.push({ path: p, body })
      if (SLOW) await new Promise((r) => setTimeout(r, 600))
      if (FAIL_PROMPT && String(body.prompt || '').includes(FAIL_PROMPT)) {
        return req.respond({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: '罐头：这一项故意失败' }),
        })
      }
      return req.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      })
    }
  }

  // ④ 作品详情 → 补上罐头说"已落库"的条目（含提示词），前端才看得见
  if (req.method() === 'GET' && /^\/api\/tasks\/[0-9a-f]{12}$/.test(p)) {
    if (!injectReady) return req.continue()
    try {
      const body = await (await fetch(req.url())).json()
      const proj = body.project || (body.project = {})
      const chars = proj.characters || (proj.characters = [])
      for (const c of SEED_CHARS) {
        if (!chars.some((x) => x.name === c.name)) {
          const row = {
            name: c.name, anchor: c.anchor, anchor_en: '', voice: c.voice,
            tts_voice: '', voice_sample: '', image_path: '', images: [],
          }
          if (c.ratio) row.ratio = c.ratio
          chars.push(row)
        }
      }
      const assets = proj.assets || (proj.assets = [])
      for (const a of SEED_ASSETS) {
        if (!assets.some((x) => x.name === a.name && x.kind === a.kind)) {
          const row = { kind: a.kind, name: a.name, anchor: a.anchor, anchor_en: '', images: [] }
          if (a.ratio) row.ratio = a.ratio
          assets.push(row)
        }
      }
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    } catch (e) {
      console.log('[seed] 改写失败，放行原响应:', e.message)
      return req.continue()
    }
  }

  return req.continue()
})

let docDelete = null
page.on('response', async (res) => {
  if (res.url().endsWith('/api/tasks/draft') && res.request().method() === 'POST') {
    try { createdTask = (await res.json()).task_id || createdTask } catch { /* ignore */ }
  }
  // 删文档那条要看**响应体**：被沙箱安全删除护栏拦下时，必须给一句人话而不是
  // text/plain 的 Internal Server Error（2026-09-27 修的就是这个）。
  if (res.request().method() === 'DELETE' && new URL(res.url()).pathname.includes('/docs/')) {
    let body = ''
    try { body = await res.text() } catch { /* ignore */ }
    docDelete = { status: res.status(), body }
  }
})

async function shot(name) {
  await sleep(300)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log('  shot ->', `${OUT}/${name}.png`)
}

// 弹窗内容快照
async function readDialog() {
  return page.evaluate(() => {
    const dlg = document.querySelector('.as-dialog')
    if (!dlg) return null
    const r = dlg.getBoundingClientRect()
    return {
      标题: dlg.querySelector('.pd-head h3')?.textContent.trim() || '',
      徽标: dlg.querySelector('.as-badge')?.textContent.trim() || '',
      附件: [...dlg.querySelectorAll('.as-chip b')].map((e) => e.textContent.trim()),
      空态: dlg.querySelector('.as-nodoc')?.textContent.trim() || '',
      气泡: [...dlg.querySelectorAll('.athread .bubble')].map((e) => e.textContent.trim()),
      建议: [...dlg.querySelectorAll('.chips .chip')].map((e) => e.textContent.trim()),
      底部说明: dlg.querySelector('.as-note')?.textContent.replace(/\s+/g, ' ').trim() || '',
      在视口内: r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight + 1,
    }
  })
}

// 素材区所有卡片：分组 / 名字 / 状态文案 / 提示词
// ⚠️ 必须按**可见性**过滤：SPA 里切走的那份视图还挂在 DOM 上（display:none），
//    祖先隐藏时子元素 rect 高度就是 0（这个坑在别的脚本上白跑过好几轮）。
//    同名的卡片不止一张（角色「林晚」和它的音色卡片同名），所以按「组+名字」定位。
async function readCards() {
  return page.evaluate(() => {
    const out = []
    for (const g of document.querySelectorAll('.mgroups .mgroup')) {
      const r0 = g.getBoundingClientRect()
      if (r0.height <= 0) continue
      const label = g.querySelector('.mgroup-head .mlabel')?.textContent.trim() || ''
      for (const c of g.querySelectorAll('.mcard')) {
        if (c.getBoundingClientRect().height <= 0) continue
        out.push({
          组: label,
          名字: c.querySelector('.mmeta strong')?.textContent.trim() || '',
          状态: c.querySelector('.mmeta p')?.textContent.trim() || '',
          // 2026-09-27 起卡片上**不显示**提示词了（斌哥："点提示词那个按钮可以看到这些，
          // 所以这里的这些就可以不用显示了"）。所以这里只记"有没有那颗按钮"。
          有提示词按钮: [...c.querySelectorAll('.mops button')]
            .some((b) => b.textContent.trim() === '提示词'),
        })
      }
    }
    return out
  })
}

// 可见的那个 01 标题行（页面上可能有多个 CreateWorkbench 实例）
async function clickAssistantButton() {
  const ok = await page.evaluate(() => {
    const heads = [...document.querySelectorAll('.step-head .head-actions')]
    const head = heads.find((h) => h.getBoundingClientRect().height > 0)
    if (!head) return false
    const b = [...head.querySelectorAll('button')].find((x) => x.textContent.trim() === 'AI 助手')
    if (!b) return false
    b.click()
    return true
  })
  if (!ok) throw new Error('找不到可见的「AI 助手」按钮')
}

// ⚠️ 按「分组 + 名字」定位卡片，别用 `audio.vaudio` 去区分角色卡与音色卡 ——
//    音色卡片在**还没有样本**时渲染的是 `.cempty`（"音色待生成"），根本没有 <audio> 元素，
//    用它当判据会找不到卡片（第一版就是这么踩的，白跑一轮）。
async function opsOf(group, name) {
  return page.evaluate((g, nm) => {
    const grp = [...document.querySelectorAll('.mgroups .mgroup')]
      .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === g)
    if (!grp) return null
    const c = [...grp.querySelectorAll('.mcard')]
      .find((x) => x.querySelector('.mmeta strong')?.textContent.trim() === nm)
    return c ? [...c.querySelectorAll('.mops button')].map((b) => b.textContent.trim()) : null
  }, group, name)
}

async function clickInDialog(text) {
  const ok = await page.evaluate((t) => {
    const dlg = document.querySelector('.as-dialog')
    if (!dlg) return false
    const b = [...dlg.querySelectorAll('button')].find((x) => x.textContent.trim() === t)
    if (!b) return false
    b.click()
    return true
  }, text)
  if (!ok) throw new Error(`弹窗里找不到按钮「${text}」`)
  await sleep(400)
}

try {
  await page.goto(PAGE_URL, { waitUntil: 'networkidle2' })
  await sleep(900)
  console.log('='.repeat(74))
  console.log('「素材规划助手」前端验证   ' + PAGE_URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 1) 按钮位置 ----------
  console.log('  —— 入口按钮 ——')
  const btnInfo = await page.evaluate(() => {
    const heads = [...document.querySelectorAll('.step-head .head-actions')]
    const head = heads.find((h) => h.getBoundingClientRect().height > 0)
    if (!head) return null
    const btns = [...head.querySelectorAll('button')].map((b) => b.textContent.trim())
    return { 全部: btns, 助手下标: btns.indexOf('AI 助手'), 添加下标: btns.indexOf('添加素材') }
  })
  check('01 标题行里有「AI 助手」按钮', !!btnInfo && btnInfo.助手下标 >= 0, JSON.stringify(btnInfo))
  check('它排在「添加素材」左边', !!btnInfo && btnInfo.助手下标 >= 0
    && btnInfo.助手下标 < btnInfo.添加下标, JSON.stringify(btnInfo))

  // ---------- 2) 打开弹窗 ----------
  console.log('\n  —— 打开弹窗 ——')
  await clickAssistantButton()
  await page.waitForSelector('.as-dialog', { timeout: 8000 })
  await sleep(400)
  let d = await readDialog()
  check('弹窗出现了', !!d)
  check('标题正确', d.标题 === 'AI 助手 · 从文档攒素材', d.标题)
  check('弹窗在视口内（Teleport 到 body 生效，没被困在长文档底部）', d.在视口内, JSON.stringify(d.在视口内))
  check('空态提示「还没有文档」', d.空态.includes('还没有文档'), d.空态)
  check('底部说明：没有文档时只按这句话', d.底部说明.includes('（没有文档，只按你这句话）'), d.底部说明)
  check('还没发过话 → 给的是快速建议 chip', d.建议.includes('按文档把素材攒齐'), JSON.stringify(d.建议))
  await shot('assistant-open')

  // ---------- 3) 传文档 ----------
  console.log('\n  —— 传文档（真后端，不花钱） ——')
  const fileInput = await page.$('.as-dialog .as-file')
  check('有隐藏的 file input 供「＋ 传文档」用', !!fileInput)
  await fileInput.uploadFile(tmpDoc)
  await sleep(1600)
  d = await readDialog()
  check('附件 chip 出现，名字取文件名', d.附件.includes(DOC_NAME), JSON.stringify(d.附件))
  check('线程里报「已读入」并带上字数', d.气泡.some((b) => b.includes(`已读入《${DOC_NAME}》`) && /：\d+ 字/.test(b)),
    JSON.stringify(d.气泡.slice(-2)))
  check('底部说明改成「这一轮会读：剧本.md」', d.底部说明.includes(`这一轮会读：${DOC_NAME}`), d.底部说明)
  check('空态提示消失', !d.空态, d.空态)
  await shot('assistant-doc')

  // ---------- 4) 发送（plan 被打成罐头） ----------
  console.log('\n  —— 发送一轮（plan 是罐头，不烧额度） ——')
  const before = await readCards()
  check('发送前素材区还没有这四条', !before.some((c) => c.名字 === '林晚'), JSON.stringify(before.map((c) => c.名字)))

  await page.type('.as-dialog .composer textarea', ASK)
  await clickInDialog('发送')
  await sleep(1800)
  d = await readDialog()
  check('用户那句话进了线程', d.气泡.includes(ASK), JSON.stringify(d.气泡))
  check('助手的回复进了线程', d.气泡.includes(REPLY), JSON.stringify(d.气泡.slice(-1)))
  check('建议 chip 换成后端给的那两条',
    d.建议.join('|') === SUGGEST.join('|'), JSON.stringify(d.建议))
  check('请求体带上了这句话', planPayload?.message === ASK, JSON.stringify(planPayload))
  check('请求体带上了当前附件清单（显式 docs，不是"读全部"）',
    JSON.stringify(planPayload?.docs) === JSON.stringify([DOC_NAME]), JSON.stringify(planPayload?.docs))
  check('请求体带上了历史（首轮只有附件那条系统消息）',
    Array.isArray(planPayload?.history) && planPayload.history.length >= 1, JSON.stringify(planPayload?.history))
  check('输入框被清空', (await page.$eval('.as-dialog .composer textarea', (e) => e.value)) === '')
  await shot('assistant-sent')

  // ---------- 5) 刷新后素材区多了卡片，且带提示词 ----------
  console.log('\n  —— 素材区：多出来的卡片 + 提示词 ——')
  const after = await readCards()
  const at = (group, name) => after.find((c) => c.组 === group && c.名字 === name)
  check('发送前素材区还没有这四条', !before.some((c) => c.名字 === '林晚'), JSON.stringify(before.map((c) => c.名字)))
  check('素材区出现「林晚」（角色组）', !!at('角色', '林晚'), JSON.stringify(after.map((c) => `${c.组}/${c.名字}`)))
  check('素材区出现「云舒」（角色组）', !!at('角色', '云舒'))
  check('素材区出现「深山古道」（场景组）', !!at('场景', '深山古道'))
  check('素材区出现「青铜匕首」（道具组）', !!at('道具', '青铜匕首'))
  check('角色卡片状态是「定妆照待生成」（＝还没生成）',
    at('角色', '林晚')?.状态 === '定妆照待生成', at('角色', '林晚')?.状态)
  check('场景卡片状态是「待…」（还没有图）',
    (at('场景', '深山古道')?.状态 || '').includes('待'), at('场景', '深山古道')?.状态)
  // 2026-09-27：提示词**不再显示在卡片上**（只在「提示词」弹窗里看 / 改），
  // 所以这里改成验"卡片上没有提示词文本了 + 那颗按钮在位"。
  check('卡片上不再显示提示词文本（只剩名字 + 状态）',
    await page.evaluate(() => {
      const c = [...document.querySelectorAll('.mgroups .mcard')]
        .find((x) => x.querySelector('.mmeta strong')?.textContent.trim() === '林晚')
      return c.querySelectorAll('.mmeta p').length === 1
    }))
  check('有提示词的卡片上有一颗「提示词」按钮（入口还在）',
    at('角色', '林晚')?.有提示词按钮 === true, JSON.stringify(at('角色', '林晚')))
  check('场景 / 道具 / 音色卡片同理（有提示词 → 有按钮）',
    at('场景', '深山古道')?.有提示词按钮 === true
    && at('道具', '青铜匕首')?.有提示词按钮 === true
    && at('角色音频', '林晚')?.有提示词按钮 === true,
    JSON.stringify([at('场景', '深山古道')?.有提示词按钮, at('道具', '青铜匕首')?.有提示词按钮,
      at('角色音频', '林晚')?.有提示词按钮]))
  check('卡片按钮集合 = 编辑 / 删除 / 生成历史 / 提示词 / AI 生成（没有多出编辑提示词的入口）',
    JSON.stringify(await opsOf('角色', '林晚'))
      === JSON.stringify(['编辑', '删除', '生成历史', '提示词', 'AI 生成']),
    JSON.stringify(await opsOf('角色', '林晚')))
  await shot('assistant-cards')

  // ---------- 5b) 「提示词」按钮 + 弹窗（含出图比例） ----------
  console.log('\n  —— 提示词按钮 / 弹窗 / 出图比例 ——')
  check('角色卡片上有「提示词」按钮', (await opsOf('角色', '林晚'))?.includes('提示词'), JSON.stringify(await opsOf('角色', '林晚')))
  check('它排在「AI 生成」左边',
    (await opsOf('角色', '林晚'))?.indexOf('提示词') === (await opsOf('角色', '林晚'))?.indexOf('AI 生成') - 1,
    JSON.stringify(await opsOf('角色', '林晚')))
  check('音色卡片上也有「提示词」按钮（看的是音色描述）',
    (await opsOf('角色音频', '林晚'))?.includes('提示词'), JSON.stringify(await opsOf('角色音频', '林晚')))

  // 打开某张卡片的提示词弹窗
  async function openPromptFor(group, name) {
    const ok = await page.evaluate((g, nm) => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === g)
      if (!grp) return false
      const c = [...grp.querySelectorAll('.mcard')]
        .find((x) => x.querySelector('.mmeta strong')?.textContent.trim() === nm)
      if (!c) return false
      const b = [...c.querySelectorAll('.mops button')].find((x) => x.textContent.trim() === '提示词')
      if (!b) return false
      b.click()
      return true
    }, group, name)
    if (!ok) throw new Error(`找不到「${group}/${name}」卡片上的「提示词」按钮`)
    await page.waitForSelector('.td-dialog', { timeout: 8000 })
    await sleep(350)
    return page.evaluate(() => {
      const d = document.querySelector('.td-dialog')
      const r = d.getBoundingClientRect()
      const ta = d.querySelector('textarea.td-prompt')
      const save = [...d.querySelectorAll('.pd-foot button')]
        .find((b) => b.textContent.includes('保存'))
      return {
        标题: d.querySelector('.pd-head h3')?.textContent.trim() || '',
        正文: ta ? ta.value : '',
        是输入框: !!ta,
        只读: ta ? (ta.readOnly || ta.disabled) : null,
        保存按钮: save ? save.textContent.trim() : '',
        保存可用: save ? !save.disabled : null,
        有比例行: !!d.querySelector('.td-ratio'),
        比例可点: [...d.querySelectorAll('.td-ratio .seg button')].every((b) => !b.disabled),
        高亮: d.querySelector('.td-ratio .seg button.on')?.textContent.trim() || '',
        高亮数: d.querySelectorAll('.td-ratio .seg button.on').length,
        比例说明: d.querySelector('.td-ratio .pd-count')?.textContent.replace(/\s+/g, ' ').trim() || '',
        在视口内: r.top >= 0 && r.bottom <= window.innerHeight + 1,
      }
    })
  }

  // 把 textarea 清空再重新输入（v-model 要收到 input 事件才更新）
  async function retypePrompt(text) {
    await page.$eval('.td-dialog textarea.td-prompt', (el) => {
      el.value = ''
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await page.type('.td-dialog textarea.td-prompt', text)
    await sleep(150)
  }

  async function clickRatioChip(r) {
    const ok = await page.evaluate((label) => {
      const b = [...document.querySelectorAll('.td-dialog .td-ratio .seg button')]
        .find((x) => x.textContent.trim() === label)
      if (!b) return false
      b.click()
      return true
    }, r)
    if (!ok) throw new Error(`比例 chip 里没有「${r}」`)
    await sleep(150)
  }

  async function clickSave() {
    const ok = await page.evaluate(() => {
      const b = [...document.querySelectorAll('.td-dialog .pd-foot button')]
        .find((x) => x.textContent.includes('保存'))
      if (!b || b.disabled) return false
      b.click()
      return true
    })
    if (!ok) throw new Error('「保存」按钮不存在或还是禁用的')
    await sleep(1400)
  }
  async function closePromptDialog() {
    await page.evaluate(() => {
      const d = document.querySelector('.td-dialog')
      const b = [...d.querySelectorAll('.pd-head button')].find((x) => x.textContent.trim() === '关闭')
      ;(b || d.querySelector('.pd-head button'))?.click()
    })
    await sleep(300)
  }

  let t = await openPromptFor('角色', '林晚')
  check('弹窗标题带素材名', t.标题 === '「林晚」的提示词', t.标题)
  check('弹窗里是**完整**提示词（卡片上那两行是截断的）', t.正文 === SEED_CHARS[0].anchor, t.正文)
  check('弹窗在视口内', t.在视口内, JSON.stringify(t.在视口内))
  check('出图素材有比例行', t.有比例行)
  check('比例高亮的是文档里读到的 9:16', t.高亮 === '9:16', t.高亮)
  check('比例只高亮一颗', t.高亮数 === 1, String(t.高亮数))
  check('并说明这个比例是从文档里来的', t.比例说明.includes('从文档里读到'), t.比例说明)
  await shot('assistant-prompt')
  await closePromptDialog()

  t = await openPromptFor('道具', '青铜匕首')
  check('没存过比例的素材 → 显示默认 16:9（斌哥定：文档没写就 16:9）', t.高亮 === '16:9', t.高亮)
  check('并说明这是"文档里没写"的默认值', t.比例说明.includes('没写比例'), t.比例说明)
  await closePromptDialog()

  t = await openPromptFor('角色音频', '林晚')
  check('音色弹窗**没有**比例行（音频不按比例出）', !t.有比例行, JSON.stringify(t.有比例行))
  check('音色弹窗显示的是音色描述', t.正文 === SEED_CHARS[0].voice, t.正文)
  await closePromptDialog()

  // ⚠️ 这一段必须在 5b2（编辑测试）**之前**跑：那些测试会把林晚的比例改成 4:3 / 1:1，
  //    之后再验「默认选中文档给的 9:16」就永远假 FAIL（顺序踩过一次）。
  // ---------- 5c) 「AI 生成」弹窗要默认选中文档里的比例 ----------
  console.log('\n  —— 点「AI 生成」：比例默认选中文档那个 ——')
  await page.evaluate(() => {
    const grp = [...document.querySelectorAll('.mgroups .mgroup')]
      .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === '角色')
    const c = [...grp.querySelectorAll('.mcard')]
      .find((x) => x.querySelector('.mmeta strong')?.textContent.trim() === '林晚')
    ;[...c.querySelectorAll('.mops button')].find((x) => x.textContent.trim() === 'AI 生成').click()
  })
  await page.waitForSelector('.pdialog', { timeout: 8000 })
  await sleep(350)
  const aiSel = await page.evaluate(() => {
    const d = document.querySelector('.pdialog')
    return {
      选中: d.querySelector('.pd-ratio .seg button.on')?.textContent.trim() || '',
      高亮数: d.querySelectorAll('.pd-ratio .seg button.on').length,
    }
  })
  check('「AI 生成」里默认选中的就是文档给的 9:16（不是老的 1:1）',
    aiSel.选中 === '9:16', JSON.stringify(aiSel))
  check('仍然恰好一颗高亮', aiSel.高亮数 === 1, String(aiSel.高亮数))
  await page.evaluate(() => {
    const d = document.querySelector('.pdialog')
    ;[...d.querySelectorAll('.pd-head button')].find((x) => x.textContent.trim() === '关闭')?.click()
  })
  await sleep(300)

  // ---------- 5b2) 弹窗里能改提示词和比例（2026-09-27 斌哥第二句要的） ----------
  console.log('\n  —— 弹窗可编辑：改提示词 / 改比例 ——')
  t = await openPromptFor('角色', '林晚')
  check('提示词是**可编辑的输入框**（不是只读文本）', t.是输入框 && t.只读 === false,
    JSON.stringify({ 是输入框: t.是输入框, 只读: t.只读 }))
  check('比例 chip 可以点（不是 disabled）', t.比例可点, JSON.stringify(t.比例可点))
  check('还没改动时「保存」是禁用的（不白跑一次 PATCH）', t.保存可用 === false, JSON.stringify(t.保存可用))

  await retypePrompt('改过的锚点：银白短发，深灰立领风衣')
  t = await page.evaluate(() => {
    const d = document.querySelector('.td-dialog')
    const save = [...d.querySelectorAll('.pd-foot button')].find((b) => b.textContent.includes('保存'))
    return { 保存可用: !save.disabled }
  })
  check('改了文字 → 「保存」变成可用', t.保存可用 === true, JSON.stringify(t))

  await clickRatioChip('4:3')
  const before4 = await page.evaluate(() =>
    document.querySelector('.td-dialog .td-ratio .seg button.on')?.textContent.trim())
  check('点比例 chip → 高亮跟着走', before4 === '4:3', before4)

  patches.length = 0
  await clickSave()
  check('保存后弹窗关掉了', !(await page.$('.td-dialog')))
  check('发出去的 PATCH 打的是 characters/{index}',
    patches[0]?.kind === 'characters' && patches[0]?.index === 0, JSON.stringify(patches[0]))
  check('PATCH 里带了改后的提示词（写进 anchor）',
    patches[0]?.patch?.anchor === '改过的锚点：银白短发，深灰立领风衣', JSON.stringify(patches[0]?.patch))
  check('PATCH 里带了改后的比例', patches[0]?.patch?.ratio === '4:3', JSON.stringify(patches[0]?.patch))
  // 卡片上不显示提示词了，所以"刷新后生效"要**重开弹窗**确认
  {
    const again = await openPromptFor('角色', '林晚')
    check('刷新后重开弹窗：提示词是改后的那一版', again.正文 === '改过的锚点：银白短发，深灰立领风衣',
      again.正文)
    await closePromptDialog()
  }

  // 重开确认比例也存住了
  t = await openPromptFor('角色', '林晚')
  check('重开弹窗：比例是刚改的 4:3', t.高亮 === '4:3', t.高亮)
  check('重开弹窗：说明变成「从文档里读到的比例，可以改」', t.比例说明.includes('可以改'), t.比例说明)
  await shot('assistant-prompt-edit')
  await closePromptDialog()

  // 只改比例、不动文字，也应该能存
  t = await openPromptFor('角色', '林晚')
  await clickRatioChip('1:1')
  patches.length = 0
  await clickSave()
  check('只改比例也能保存（patch 里只有 ratio）',
    JSON.stringify(Object.keys(patches[0]?.patch || {})) === '["ratio"]'
    && patches[0]?.patch?.ratio === '1:1', JSON.stringify(patches[0]?.patch))

  // 取消不该写任何东西
  t = await openPromptFor('角色', '林晚')
  await retypePrompt('这段不该被保存')
  patches.length = 0
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('.td-dialog .pd-foot button')]
      .find((x) => x.textContent.trim() === '取消')
    b.click()
  })
  await sleep(400)
  check('点「取消」不发 PATCH', patches.length === 0, JSON.stringify(patches))
  check('点「取消」弹窗也关掉', !(await page.$('.td-dialog')))
  t = await openPromptFor('角色', '林晚')
  check('取消后重开还是原值（没被写进去）',
    t.正文 === '改过的锚点：银白短发，深灰立领风衣', t.正文)
  await closePromptDialog()

  // 音色卡片：改的是 voice 字段
  t = await openPromptFor('角色音频', '林晚')
  await retypePrompt('低沉沙哑的中年男声，语速偏慢')
  patches.length = 0
  await clickSave()
  check('音色卡片改的是 voice 字段（不是 anchor）',
    patches[0]?.patch?.voice === '低沉沙哑的中年男声，语速偏慢'
    && patches[0]?.patch?.anchor === undefined, JSON.stringify(patches[0]?.patch))
  check('音色弹窗不发 ratio（音频没有画幅）',
    patches[0]?.patch?.ratio === undefined, JSON.stringify(patches[0]?.patch))

  // ---------- 5d) 没提示词的素材不该有这颗按钮 ----------
  console.log('\n  —— 没提示词的素材 ——')
  // ⚠️ 2026-09-27 改过：没有提示词的卡片上，「提示词」按钮**必须在** ——
  //    否则手填的素材永远拿不到提示词（AI 生成按设计不写回锚点）。
  //    **没有勾选框**才是对的：没提示词就出不了图。
  const bare = await opsOf('角色', '无描述角色')
  check('没有提示词的卡片上**有**「提示词」按钮（要能写一段进去）',
    !!bare && bare.includes('提示词'), JSON.stringify(bare))
  check('但它**没有**勾选框（没提示词就出不了图）',
    (await hasPick('角色', '无描述角色')) === false, String(await hasPick('角色', '无描述角色')))

  // ---------- 6) 删附件 ----------
  console.log('\n  —— 去掉附件 ——')
  await page.evaluate(() => {
    const c = document.querySelector('.as-dialog .as-chip')
    c.querySelector('button').click()
  })
  await sleep(900)
  d = await readDialog()
  if (!d.附件.includes(DOC_NAME)) {
    check('chip 被删掉', true)
    check('底部说明回到「没有文档」', d.底部说明.includes('（没有文档，只按你这句话）'), d.底部说明)
  } else {
    // ⚠️ 沙箱「安全删除」护栏会拦下 os.remove（`raise SystemExit`），额度按**这一轮对话**
    //    累计（约 50 个文件），跑几个脚本就用光了。这时该验的不是"删没删掉"，而是
    //    **拦下时必须给一句可读的原因** —— 否则前端只看到 "Internal Server Error"。
    let detail = String(docDelete?.body || '')
    try { detail = JSON.parse(docDelete.body).detail } catch { /* 纯文本就原样用 */ }
    check('被沙箱护栏拦下时，前端拿到的是可读原因（不是 Internal Server Error）',
      docDelete?.status === 500 && String(detail).includes('沙箱'),
      `${docDelete?.status} ${String(detail).slice(0, 160)}`)
    check('被拦下时 chip 留在原地（东西不能凭空消失）', d.附件.includes(DOC_NAME), JSON.stringify(d.附件))
    skip('点 × 真的把文档去掉 + 底部说明跟着变', '本会话被沙箱安全删除护栏拦下，属环境限制')
  }

  // ---------- 7) 关弹窗 ----------
  console.log('\n  —— 关闭 ——')
  await clickInDialog('关闭')
  await sleep(400)
  check('弹窗关掉了', !(await page.$('.as-dialog')))

  console.log('\n  —— 再打开：线程不该被清空（会话是页面状态） ——')
  await clickAssistantButton()
  await page.waitForSelector('.as-dialog', { timeout: 8000 })
  await sleep(400)
  d = await readDialog()
  check('重新打开还能看到上一轮的气泡', d.气泡.includes(ASK) && d.气泡.includes(REPLY),
    JSON.stringify(d.气泡.slice(-2)))
  await shot('assistant-reopen')
  await clickInDialog('关闭')

  // ---------- 8) 批量「自动生成」（2026-09-27 斌哥要的） ----------
  // ⚠️ **不烧出图额度**：三个出图接口全被拦成罐头（见文件顶部那段拦截）。
  console.log('\n  —— 批量自动生成：勾选 ——')

  async function pickCount() {
    return page.evaluate(() => document.querySelectorAll('.mgroups .mcard.picked').length)
  }
  async function hasPick(group, name) {
    return page.evaluate((g, nm) => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === g)
      if (!grp) return null
      const c = [...grp.querySelectorAll('.mcard')]
        .find((x) => x.querySelector('.mmeta strong')?.textContent.trim() === nm)
      return c ? !!c.querySelector('.gpick') : null
    }, group, name)
  }
  async function pickCard(group, name) {
    const ok = await page.evaluate((g, nm) => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === g)
      const c = [...grp.querySelectorAll('.mcard')]
        .find((x) => x.querySelector('.mmeta strong')?.textContent.trim() === nm)
      const b = c?.querySelector('.gpick')
      if (!b) return false
      b.click()
      return true
    }, group, name)
    if (!ok) throw new Error(`勾不上「${group}/${name}」`)
    await sleep(150)
  }
  async function clickCardBody(group, name) {
    await page.evaluate((g, nm) => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === g)
      const c = [...grp.querySelectorAll('.mcard')]
        .find((x) => x.querySelector('.mmeta strong')?.textContent.trim() === nm)
      // 点缩略图（＝卡片主体，会触发"看大图"那条路）
      c.querySelector('.mthumb')?.click()
    }, group, name)
    await sleep(300)
    // 大图弹窗可能被打开，关掉它
    await page.evaluate(() => {
      const m = document.querySelector('.pmask .pv-dialog, .pmask .pv-mask')
      const btn = document.querySelector('.pv-dialog .pd-head button, .pv-dialog button')
      if (btn) btn.click()
      else document.querySelector('.pmask')?.click()
    })
    await sleep(200)
  }
  async function headBtn(label) {
    return page.evaluate((t) => {
      const heads = [...document.querySelectorAll('.step-head .head-actions')]
      const head = heads.find((h) => h.getBoundingClientRect().height > 0)
      const b = [...head.querySelectorAll('button')].find((x) => x.textContent.trim().startsWith(t))
      return b ? { 文案: b.textContent.replace(/\s+/g, ''), 禁用: b.disabled } : null
    }, label)
  }
  async function clickHead(label) {
    const ok = await page.evaluate((t) => {
      const heads = [...document.querySelectorAll('.step-head .head-actions')]
      const head = heads.find((h) => h.getBoundingClientRect().height > 0)
      const b = [...head.querySelectorAll('button')].find((x) => x.textContent.trim().startsWith(t))
      if (!b || b.disabled) return false
      b.click()
      return true
    }, label)
    if (!ok) throw new Error(`标题行按钮「${label}」不存在或禁用`)
  }
  async function clickGroupAll(label) {
    await page.evaluate((g) => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === g)
      grp.querySelector('.gall')?.click()
    }, label)
    await sleep(200)
  }
  const genNow = () => page.evaluate(() =>
    [...document.querySelectorAll('.mgroups .gnow')].map((e) => e.textContent.trim()))

  const headOrder = await page.evaluate(() => {
    const heads = [...document.querySelectorAll('.step-head .head-actions')]
    const head = heads.find((h) => h.getBoundingClientRect().height > 0)
    return [...head.querySelectorAll('button')].map((b) => b.textContent.replace(/\s+/g, ''))
  })
  check('标题行有「自动生成」，且排在「AI 助手」左边',
    headOrder.findIndex((t) => t.startsWith('自动生成')) >= 0
    && headOrder.findIndex((t) => t.startsWith('自动生成')) < headOrder.findIndex((t) => t.startsWith('AI助手')),
    JSON.stringify(headOrder))

  check('有提示词的卡片有勾选框', await hasPick('角色', '林晚') === true)
  check('没提示词的卡片**没有**勾选框（没提示词就出不了图）', await hasPick('角色', '无描述角色') === false)
  // ⚠️ 这个夹具里没有「其他音频」卡片（hasPick 返回 null）—— 所以断言写 `!== true`：
  //    真要出现勾选框就 FAIL，没这张卡也不算错。
  check('上传型的「其他音频」没有勾选框', (await hasPick('其他音频', '回归音频')) !== true)

  check('一个都没勾时按钮是禁用的', (await headBtn('自动生成'))?.禁用 === true,
    JSON.stringify(await headBtn('自动生成')))

  // ⚠️ 点卡片主体**不该**选中 —— 09-19 斌哥否过"点整张卡片就选中"（"点图片下面那块会莫名被选上"）
  await clickCardBody('角色', '林晚')
  check('点卡片主体不触发选中（09-19 否过的那条别再回来）', await pickCount() === 0,
    `${await pickCount()} 张被选中`)

  await pickCard('角色', '林晚')
  check('勾一张 → 卡片有选中态', await pickCount() === 1, String(await pickCount()))
  await pickCard('场景', '深山古道')
  const btn2 = await headBtn('自动生成')
  check('勾两张 → 按钮上带数量 2', btn2?.文案 === '自动生成2' && btn2?.禁用 === false, JSON.stringify(btn2))
  check('勾上之后出现「清空」', !!(await headBtn('清空')), JSON.stringify(await headBtn('清空')))
  await shot('assistant-genpick')

  // 「清空」就在这里测（此时勾着 2 项）—— 别挪到下面：
  // 全选/取消全选那两步跑完勾选已经是空的，「清空」按钮会自己消失（它只在有勾选时渲染）。
  await clickHead('清空')
  await sleep(200)
  check('「清空」把勾选全去掉', await pickCount() === 0, String(await pickCount()))

  await clickGroupAll('场景')
  check('场景组「全选」把本组能生成的都勾上',
    await page.evaluate(() => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === '场景')
      const picks = [...grp.querySelectorAll('.gpick')]
      return picks.length > 0 && picks.every((p) => p.classList.contains('on'))
    }))
  await clickGroupAll('场景')
  check('再点一次「取消全选」把本组清掉',
    await page.evaluate(() => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === '场景')
      return [...grp.querySelectorAll('.gpick')].every((p) => !p.classList.contains('on'))
    }))

  console.log('\n  —— 批量自动生成：真的跑一轮（出图接口是罐头） ——')
  await pickCard('角色', '林晚')
  await pickCard('角色', '云舒')
  await pickCard('场景', '深山古道')
  genCalls.length = 0
  confirms.length = 0
  SLOW = true                     // 让罐头慢 600ms 才回，否则「生成中…」一闪而过看不见
  await clickHead('自动生成')
  await sleep(260)
  check('跑之前弹了确认（花钱的动作要一道闸）', confirms.length === 1, JSON.stringify(confirms))
  check('确认文案里写了项数', String(confirms[0] || '').includes('3'), String(confirms[0] || '').slice(0, 60))
  check('跑的时候卡片上显示「生成中…」', (await genNow()).length === 1, JSON.stringify(await genNow()))
  await shot('assistant-genrun')
  await sleep(4000)
  SLOW = false

  check('逐项发请求，一项一次（3 项 = 3 次）', genCalls.length === 3, JSON.stringify(genCalls.map((c) => c.path)))
  check('角色打的是 /characters/{i}/portrait',
    genCalls[0]?.path.includes('/characters/0/portrait'), genCalls[0]?.path)
  check('场景打的是 /assets/{i}/generate',
    genCalls[2]?.path.includes('/assets/') && genCalls[2]?.path.includes('/generate'), genCalls[2]?.path)
  check('出图提示词＝那张卡片上的提示词（这就是"自动对应那个素材"）',
    genCalls[0]?.body?.prompt === SEED_CHARS[0].anchor, JSON.stringify(genCalls[0]?.body?.prompt))
  check('场景用的也是它自己的提示词',
    genCalls[2]?.body?.prompt === SEED_ASSETS[0].anchor, JSON.stringify(genCalls[2]?.body?.prompt))
  check('比例跟着那条素材走（云舒是 16:9，没被前面的编辑测试影响）',
    genCalls[1]?.body?.ratio === '16:9', JSON.stringify(genCalls[1]?.body))
  check('跑完成功的自动取消勾选（免得手一抖再出一遍）', await pickCount() === 0, String(await pickCount()))

  console.log('\n  —— 批量自动生成：单项失败 + 中途停止 ——')
  // 让「云舒」那一项失败：罐头按 prompt 内容决定回 500
  FAIL_PROMPT = SEED_CHARS[1].anchor
  await pickCard('角色', '林晚')
  await pickCard('角色', '云舒')
  genCalls.length = 0
  await clickHead('自动生成')
  await sleep(3500)
  check('某一项失败不拖垮整批（后面那项照样跑）', genCalls.length === 2, JSON.stringify(genCalls.map((c) => c.path)))
  check('失败的项被留着勾选（改完提示词直接重试）',
    await page.evaluate(() => {
      const grp = [...document.querySelectorAll('.mgroups .mgroup')]
        .find((x) => x.querySelector('.mgroup-head .mlabel')?.textContent.trim() === '角色')
      const on = [...grp.querySelectorAll('.mcard')].filter((c) => c.classList.contains('picked'))
      return on.map((c) => c.querySelector('.mmeta strong')?.textContent.trim())
    }).then((names) => JSON.stringify(names) === JSON.stringify(['云舒'])),
    JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('.mgroups .mcard.picked')]
      .map((c) => c.querySelector('.mmeta strong')?.textContent.trim()))))
  check('进度行说明了成功几项失败几项',
    /\d/.test(await page.evaluate(() => document.querySelector('.progress-note')?.textContent || '')),
    await page.evaluate(() => document.querySelector('.progress-note')?.textContent || ''))
  FAIL_PROMPT = ''
  await clickHead('清空')
  await sleep(200)

  // 停止：勾 3 项，跑到第 1 项时点停止
  await pickCard('角色', '林晚')
  await pickCard('角色', '云舒')
  await pickCard('场景', '深山古道')
  genCalls.length = 0
  SLOW = true
  await clickHead('自动生成')
  await sleep(200)
  const stopBtn = await headBtn('停止')
  check('跑的时候按钮变成「停止」', stopBtn?.文案 === '停止', JSON.stringify(stopBtn))
  await clickHead('停止')
  await sleep(2500)
  SLOW = false
  check('点了停止之后不再往后跑（只出了 1 项）', genCalls.length === 1, JSON.stringify(genCalls.map((c) => c.path)))
  // 停止后：成功的那项取消勾选，**还没跑的留着**（停下来的意思是"先不跑了"，不是"放弃剩下的"）
  check('停止后：成功的那项取消、还没跑的留着（2 项）',
    await pickCount() === 2, String(await pickCount()))
  await clickHead('清空')
  await sleep(200)
} catch (err) {
  console.log('\n!! 中途中断：', err.message)
  results.push(false)
} finally {
  await browser.close()
  try { fs.unlinkSync(tmpDoc) } catch { /* ignore */ }
  if (createdTask) {
    const r = await fetch(`${API_BASE}/api/tasks/${createdTask}?confirm=true&purge=true`, { method: 'DELETE' })
    console.log('\n清理测试作品', createdTask, '->', r.status)
    // 沙箱护栏可能拦下彻底删除 → 兜底再试一次回收站里的那条
    if (r.status !== 200) {
      const list = await (await fetch(`${API_BASE}/api/trash`)).json().catch(() => ({ entries: [] }))
      for (const e of list.entries || []) {
        if (e.task_id === createdTask) {
          await fetch(`${API_BASE}/api/trash/${e.name}?confirm=true`, { method: 'DELETE' })
        }
      }
    }
  } else {
    console.log('\n⚠️ 没抓到 task_id，请手动 GET /api/tasks 检查有没有残留 draft')
  }
  // ⚠️ 汇总与 exit 必须在 finally 里**最后**做，但绝不能被上面的异常顶掉 —— 所以先做完清理再报
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

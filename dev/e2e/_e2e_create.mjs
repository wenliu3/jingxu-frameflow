// 前端联调冒烟测试：真点一遍「素材优先」那条链路（2026-09-17 迁移版）。
//
// 为什么需要它：页面上所有素材操作都要先「按需建一个空白作品」（ensureDraft），
// 这条链路只有真点才知道通不通 —— 单看代码看不出来 ensureTask 这个函数 prop
// 有没有正确调用、刷新有没有把左侧素材区更新出来。
//
// ⚠️ 2026-09-17 版式调整后必须跨页导航，这就是"迁移"的全部内容：
//     新建作品页              → 只有 01 准备素材（右侧 AI 助手已撤、02 已挪走）
//     侧栏「我的作品」→ 第一张卡 → 进工作台
//     工作台 ·「分镜工作台」tab → 只有 02 生成视频，自带 .picklist 素材勾选条
//   01 的选中状态跨页会丢，所以 02 那一段一律用 .picklist 重新勾。
//
// 跑法：node dev/e2e/_e2e_create.mjs
//        默认打 8000（后端同源伺服 web/dist，即用户实际拿到的那份）。
//        要打 dev server：E2E_URL=http://127.0.0.1:5173/ node dev/e2e/_e2e_create.mjs
//
// 项目硬约定：**默认不烧额度** —— 会真调模型的步骤一律 SKIP，E2E_LIVE=1 才跑。
// 跑完自己 DELETE 掉建的测试作品，并把被改过的 video_megapixels 还原。
//
// puppeteer-core 装在托管 node 工作区里（不污染项目依赖），所以用 createRequire
// 从那个目录解析 —— ESM 的 NODE_PATH 是不生效的。
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
// 收尾清理走 HTTP，不依赖页面；dev server 跑在 5173 时后端仍在 8000
const API = process.env.E2E_API || 'http://127.0.0.1:8000'

const errors = []
const results = []
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

// 项目硬约定：**改代码阶段不要擅自触发出图/出视频**（ModelScope 日额度 + GPU 时间）。
// 所以凡是会真调模型/真出片的步骤一律默认跳过 —— 跑一次全量测试不该花掉额度。
// 需要真跑这些步骤时：E2E_LIVE=1 node dev/e2e/_e2e_create.mjs
const LIVE = process.env.E2E_LIVE === '1'
function skip(name, why) {
  results.push({ name, ok: true, skipped: true })
  console.log(`SKIP  ${name}  （${why}；要真跑就 E2E_LIVE=1）`)
}

function check(name, ok, extra = '') {
  results.push({ name, ok, extra })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`)
}

// 脚本自己写的提示词。以前靠点「示例」按钮自动填，那颗按钮 2026-09-19 撤了（见下方第 9 步）。
const PROMPT = '林晚站在雨夜的便利店门口，抬头看着招牌，霓虹在水洼里晃。'

// 收尾要用的两件事：测试建的 draft、跑之前的服务配置
let createdTask = ''
let cfgBefore = null

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1000 },
})
const page = await browser.newPage()
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
page.on('request', (r) => {
  if (r.method() !== 'GET') console.log(`   [net] ${r.method()} ${r.url().replace(URL.replace(/\/$/, ''), '')}`)
})
// 抓 draft 的 task_id 用于收尾删除 —— 前端不把这个 id 显示在任何稳定位置，
// 只能从建它的那次响应里拿
page.on('response', async (res) => {
  if (res.url().endsWith('/api/tasks/draft') && res.request().method() === 'POST') {
    try { createdTask = (await res.json()).task_id || createdTask } catch { /* ignore */ }
  }
})

async function textOf(sel) {
  return page.$eval(sel, (el) => el.textContent.trim()).catch(() => '')
}

// 按文案点：Vue 里这些按钮都没有稳定的 class 区分（.nav-item / .mode-switch button 等）
async function clickByText(sel, text) {
  return page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
    if (el) { el.click(); return true }
    return false
  }, sel, text)
}

// 切工作台内的形态（素材工坊 / 分镜工作台）
async function gotoStudioTab(label) {
  const ok = await clickByText('.mode-switch button', label)
  await wait(1600)
  return ok
}

// 从任意页面进工作台：侧栏「我的作品」→ 列表里第一张卡（按创建时间倒序 = 刚建的那个）
async function openNewestProject() {
  await clickByText('.nav-item', '我的作品')
  await wait(1200)
  const tiles = await page.$$('.project-open')
  if (!tiles.length) return false
  await tiles[0].click()
  await wait(2500)
  return true
}

try {
  // 第 7c 步会点「清晰度」把 video_megapixels 写回服务配置，先记下原值
  cfgBefore = await (await fetch(`${API}/api/config`)).json()

  await page.goto(URL, { waitUntil: 'networkidle2' })
  await wait(800)

  // ---------- 1) 新建作品页：只有 01 ----------
  const cards = await page.$$eval('.mgroups .mgroup .mlabel', (els) => els.map((e) => e.textContent.trim()))
  check('01 六组素材齐全', cards.length === 6, JSON.stringify(cards))
  check('新建作品页没有 02 生成视频（已挪到分镜工作台）', (await page.$('.generate')) === null)
  check('新建作品页右侧没有 AI 助手', (await page.$('.create-assistant')) === null)
  check('顶栏引擎状态胶囊存在', (await textOf('.engine-pill')).includes('ComfyUI'))
  check('页面无 JS 报错（初次加载）', errors.length === 0, errors.join(' | '))

  // ---------- 2) 没有作品时点「添加素材」→ 应该自动建一个空白作品 ----------
  //    添加流程：点按钮只弹 5 个类型 → 选「角色」→ 下面角色组里直接多一张编辑态的卡片。
  const before = await page.$$eval('.history-row', (els) => els.length)
  await page.click('.head-actions .btn-ghost')
  await wait(300)
  const kindCount = await page.$$eval('.addform .af-kinds button', (els) => els.length)
  check('「添加素材」只给 5 个类型可选', kindCount === 5, `实际 ${kindCount} 个`)
  await page.$$eval('.addform .af-kinds button',
    (els) => els.find((e) => e.textContent.trim() === '角色').click())
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await wait(300)
  const newCard = await page.evaluate(() => {
    const g = document.querySelector('.mgroups .mgroup:nth-child(1)')
    const form = g?.querySelector('.edit-form')
    return {
      卡片数: g?.querySelectorAll('.mcard').length || 0,
      在编辑态: !!form,
      名字框是空的: form ? form.querySelector('input[aria-label="素材名字"]').value === '' : null,
      有上传入口: !!form?.querySelector('label.mup input[type="file"]'),
    }
  })
  check('选类型后那一组里立刻多一张编辑态的空卡片', newCard.卡片数 === 1 && newCard.在编辑态 && newCard.名字框是空的, JSON.stringify(newCard))
  check('新卡片上直接能上传内容', newCard.有上传入口 === true)
  // ⚠️ 编辑态里**只有名字框**（2026-09-15 起删掉了描述框）。
  // 描述/锚点留空也没关系：这一步走的是 PATCH（只改字段），不会触发设计锚点的 LLM 调用。
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '林晚')
  const typed = await page.$eval('.mgrid .edit-form input[aria-label="素材名字"]', (el) => el.value)
  console.log('   [debug] 输入框值 =', JSON.stringify(typed))
  await page.click('.mgrid .edit-form .btn-primary')
  await wait(2500)

  const toasts = await page.$$eval('.toast .text', (els) => els.map((e) => e.textContent.trim()))
  if (toasts.length) console.log('   [toast]', JSON.stringify(toasts))
  console.log('   [debug] addform 仍打开 =', (await page.$('.addform')) !== null)

  const after = await page.$$eval('.history-row', (els) => els.length)
  check('点「添加素材」自动建出空白作品（侧栏 +1）', after === before + 1, `${before} -> ${after}`)
  check('抓到了 draft 的 task_id（收尾要靠它删）', !!createdTask, createdTask || '（没抓到）')

  // ---------- 3) 素材区应该出现这个角色（说明刷新链路通了） ----------
  const charItems = await page.$$eval('.mcard .mmeta strong', (els) => els.map((e) => e.textContent.trim()))
  check('角色已出现在素材区', charItems.includes('林晚'), JSON.stringify(charItems))

  // ---------- 4) 标题状态应该是「未开始」（draft 态） ----------
  check('状态显示「未开始」', (await textOf('.create-head .status-pill')).includes('未开始'))

  // ---------- 5) 01 里的选中开关已经**撤掉了**（2026-09-19 斌哥定，改了两轮） ----------
  //    原断言是「01 里点卡片能选中」（查 .mcard.picked）。09-19 先去掉"点整张卡片选中"
  //    （点图片下面那块会莫名被选上），又去掉了那个小圆圈 —— 原话
  //    "不用选这个，因为选了也不知道能有什么功能"。所以这里改成**反向断言**：
  //    点一下，卡片也不该出现任何选中态。真正给 02 选素材靠「分镜工作台」里
  //    02 自带的 .picklist（见第 7 步）。
  //    ⚠️ 撤 UI 时必须同步扫 _*.mjs —— 留着旧断言的话，它测的是一个**已经不存在的行为**，
  //       报出来的 FAIL 会把人引去查一个早就改好的地方。
  await page.click('.mgroups .mgroup:nth-child(1) .mcard')
  await wait(300)
  const onCreate = await page.$$eval('.mgroups .mgroup:nth-child(1) .mcard',
    (els) => els.map((e) => e.classList.contains('picked')))
  check('01 里点卡片不再有选中态（09-19 撤的，别再找回来）',
    onCreate.length > 0 && onCreate.every((x) => x === false), JSON.stringify(onCreate))
  await page.click('.mgroups .mgroup:nth-child(1) .mcard')
  await wait(250)

  // ---------- 6) 进工作台 · 切「分镜工作台」tab ----------
  const opened = await openNewestProject()
  check('能从「我的作品」进到刚建的作品', opened)
  await gotoStudioTab('分镜工作台')
  check('分镜工作台里 02 生成视频在位', (await page.$('.generate')) !== null)
  check('分镜工作台里不重复渲染 01', (await page.$('.mgroups')) === null)

  // ---------- 7) 02 自带勾选条：把素材勾进这一段视频 ----------
  const pl = await page.$$eval('.picklist .pl-item em', (els) => els.map((e) => e.textContent.trim()))
  check('02 自带素材勾选条', pl.length > 0, JSON.stringify(pl))
  check('刚建的角色出现在勾选条里', pl.includes('林晚'), JSON.stringify(pl))
  await clickByText('.picklist .pl-item', '林晚')
  await wait(500)
  const picked = await page.$$eval('.picked-card strong', (els) => els.map((e) => e.textContent.trim()))
  check('勾选后进入本次视频素材', picked.includes('林晚'), JSON.stringify(picked))

  // ---------- 8) 出片按钮在有素材+提示词前应报错提示（而不是静默失败） ----------
  await page.click('.generate')
  await wait(400)
  const err = await textOf('.err')
  check('缺提示词时给出明确报错', err.includes('描述'), err)

  // ---------- 9) 提示词：脚本自己写（原来靠「示例」按钮，那颗 09-19 撤了） ----------
  // ⚠️ 这里以前是 `await page.click('.panel-head .link')` —— 指向「示例」按钮。
  //    那颗按钮 2026-09-19 被斌哥撤掉（「感觉不需要这个」，SAMPLE_PROMPT 也删了），
  //    元素不存在时 `page.click` 会**直接抛异常**，于是第 9 步之后的
  //    时长夹取 / 清晰度写配置 / 工作台回归**一步都跑不到**（脚本还只报 20/21，看不出少了多少）。
  check('「示例」按钮已经不在了（09-19 撤的，别再找它）', (await page.$('.panel-head .link')) === null)
  await page.type('textarea[aria-label="视频提示词"]', PROMPT)
  await wait(200)
  const ta = await page.$eval('textarea[aria-label="视频提示词"]', (el) => el.value)
  check('提示词能写进输入框', ta === PROMPT, `${ta.slice(0, 18)}…`)

  // ---------- 9b) 生成时长：手写要能填、超区间要被夹回来 ----------
  const durSel = 'input[aria-label="自定义生成时长（秒）"]'
  await page.$eval(durSel, (el) => { el.value = '' })
  await page.click(durSel)
  await page.type(durSel, '12')
  await page.keyboard.press('Enter')
  await wait(200)
  check('时长可手写 12 秒', (await page.$eval(durSel, (el) => el.value)) === '12')

  await page.$eval(durSel, (el) => { el.value = '' })
  await page.click(durSel)
  await page.type(durSel, '99')
  await page.keyboard.press('Enter')
  await wait(200)
  const clampedHigh = await page.$eval(durSel, (el) => el.value)
  check('超上限 99 被夹到 15', clampedHigh === '15', `得到 ${clampedHigh}`)

  await page.$eval(durSel, (el) => { el.value = '' })
  await page.click(durSel)
  await page.type(durSel, '1')
  await page.keyboard.press('Enter')
  await wait(200)
  const clampedLow = await page.$eval(durSel, (el) => el.value)
  check('低于下限 1 被夹到 4', clampedLow === '4', `得到 ${clampedLow}`)

  // ---------- 9c) 清晰度是服务配置「画质」的镜像 ----------
  // 点一下应该写回配置，并且只有它高亮。按 .slabel 文案定位那一行 ——
  // .settings 里有多行 .seg（视频比例也有），直接取 .settings .seg 会点到「视频比例」上去。
  // ⚠️ 这一步会改服务配置，收尾会还原（见 finally）。
  const readQ = () => page.evaluate(() => {
    const row = [...document.querySelectorAll('.settings .srow')]
      .find((r) => r.querySelector('.slabel')?.textContent.includes('清晰度'))
    return {
      on: row ? [...row.querySelectorAll('button.on')].map((b) => b.textContent.trim()) : [],
      all: row ? [...row.querySelectorAll('button')].map((b) => b.textContent.trim()) : [],
    }
  })
  const qBefore = (await readQ()).on[0]
  await page.evaluate(() => {
    const row = [...document.querySelectorAll('.settings .srow')]
      .find((r) => r.querySelector('.slabel')?.textContent.includes('清晰度'))
    const other = [...row.querySelectorAll('button')].find((b) => !b.classList.contains('on'))
    other?.click()
  })
  await wait(900)
  const cfgMp = await page.evaluate(async () => {
    const r = await fetch('/api/config')
    return (await r.json()).video_megapixels
  })
  const qAfter = await readQ()
  check('切换清晰度写回了服务配置', qBefore !== qAfter.on[0], `${qBefore} -> ${qAfter.on[0]}，配置 = ${cfgMp}`)
  check('清晰度只有一个档高亮', qAfter.on.length === 1, `高亮=${JSON.stringify(qAfter.on)} 全部=${JSON.stringify(qAfter.all)}`)

  // ---------- 10) 真实出片（默认 SKIP） ----------
  //   这一段会真调编排模型 + 真提交 ComfyUI 出片，烧额度，所以只在 E2E_LIVE=1 时跑。
  if (!LIVE) {
    skip('真实出片（编排调模型 + ComfyUI 占 GPU）', '会花文本模型额度 + GPU 时间')
    check('生成视频按钮已解禁', await page.$eval('.generate', (el) => !el.disabled))
  } else {
    await page.click('.generate')
    await wait(1000)
    const busyLabel = await textOf('.generate')
    check('点下后进入编排/出片态', /编排中|出片中/.test(busyLabel) || (await page.$('.err')) !== null, busyLabel)
    const deadline = Date.now() + 180000
    let err2 = ''
    let out = ''
    while (Date.now() < deadline) {
      await wait(2500)
      err2 = await textOf('.err')
      out = await textOf('.out')
      if (err2 || /出片完成|出片失败/.test(out)) break
    }
    console.log('   [出片终态]', err2 ? `错误：${err2.slice(0, 200)}` : out ? out.slice(0, 160) : '（超时仍未结束）')
    check('出片给出明确终态（成功或失败原因）', !!(err2 || /出片完成|出片失败/.test(out)))
  }

  check('全程无 JS 报错', errors.length === 0, errors.join(' | '))
} catch (err) {
  check('测试脚本执行完成', false, err.message)
} finally {
  // 失败时留一张现场图。要兜住异常：页面根本没加载起来时 screenshot 会抛
  // ProtocolError（Not attached to an active page），那会把真正的失败原因盖掉。
  if (results.some((r) => !r.ok)) {
    const name = '_e2e_failed'
    try {
      await page.screenshot({ path: `docs/_shots/${name}.png`, fullPage: false })
      console.log(`失败现场 -> docs/_shots/${name}.png`)
    } catch (shotErr) {
      console.log(`（留不了现场图：${shotErr.message}）`)
    }
  }
  await browser.close()

  // ---------- 收尾 1：删掉测试作品 ----------
  if (createdTask) {
    try {
      const r = await fetch(`${API}/api/tasks/${createdTask}?confirm=true&purge=true`, { method: 'DELETE' })
      console.log(`\n清理测试作品 ${createdTask} -> ${r.status}`)
    } catch (e) {
      console.log(`\n⚠️ 删测试作品失败：${e.message}；请手动 DELETE /api/tasks/${createdTask}?confirm=true`)
    }
  } else {
    console.log('\n⚠️ 没抓到 task_id，请手动 GET /api/tasks 检查有没有残留 draft')
  }

  // ---------- 收尾 2：还原被 9c 改过的 video_megapixels ----------
  // ⚠️ 必须整份 POST 回去：后端 set_config 是 `json.dump(body.model_dump())`，
  // 字段默认全是 ""，只发一个字段会把其它配置（含 API key）清空。
  if (cfgBefore) {
    try {
      const now = await (await fetch(`${API}/api/config`)).json()
      if (String(now.video_megapixels) !== String(cfgBefore.video_megapixels)) {
        const r = await fetch(`${API}/api/config`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cfgBefore),
        })
        console.log(`还原 video_megapixels ${now.video_megapixels} -> ${cfgBefore.video_megapixels}（HTTP ${r.status}）`)
      } else {
        console.log(`video_megapixels 未变（${cfgBefore.video_megapixels}），无需还原`)
      }
    } catch (e) {
      console.log(`⚠️ 还原配置失败：${e.message}`)
    }
  }
}

const failed = results.filter((r) => !r.ok)
console.log(`\n=== ${results.length - failed.length}/${results.length} 通过 ===`)
process.exit(failed.length ? 1 : 0)

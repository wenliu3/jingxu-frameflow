// 收尾验证：把 02「生成视频」这条链路真点一遍，并确认工作台两个形态没被共享改动带坏。
//
// ⚠️ 2026-09-17 版式调整后必须跨页导航（这就是"迁移"的全部内容）：
//   02 已从「新建作品」页挪到工作台的「分镜工作台」tab，所以备完素材要
//   侧栏「我的作品」→ 第一张卡 → 切「分镜工作台」，再用 02 自带的 .picklist
//   勾选条选素材（01 的选中状态跨页会丢，那条勾选条就是为此加的）。
//   02 内部的选择器本身没变。
//
// 为什么单独写一个：_e2e_create.mjs 只测到"缺提示词会报错"，出片本身从没跑过；
// 而这次动过 ToastStack（全局）和 App.vue 骨架，工作台那两个形态也得看一眼。
//
// 跑法：node dev/e2e/_e2e_video.mjs   （后端 8000 + web/dist）
//        要打 dev server：E2E_URL=http://127.0.0.1:5173/ node dev/e2e/_e2e_video.mjs
// 项目硬约定：默认不烧额度 —— 出片那一段 SKIP，E2E_LIVE=1 才真跑。跑完自己 DELETE 测试作品。
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
// 默认打 8000（后端同源伺服 web/dist，即生产产物）：不依赖 vite dev server 是否在跑，
// 而且测的就是用户实际拿到的那份。要打 dev server 就 E2E_URL=http://127.0.0.1:5173/ node ...
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const API = process.env.E2E_API || 'http://127.0.0.1:8000'
// ⚠️ 这张图曾经丢过一次，表现是「上传后缩略图指向角色图」FAIL 且 thumbSrc 为空 ——
// 而 uploadFile() 对不存在的路径**不报错**，只静默塞个空文件，极易误判成前端回归。
// 再看到这条 FAIL 先 ls dev/e2e/_e2e_face.png。
const IMG = 'D:/Pychrom Project/ai_video_multiagent/dev/e2e/_e2e_face.png'

const errors = []
const results = []
let createdTask = ''

// 项目硬约定：**改代码阶段不要擅自触发出图/出视频**（ModelScope 日额度 + GPU 时间）。
// 这个脚本的「生成视频」那一段会真调编排模型 + 真提交 ComfyUI 出片，所以默认跳过；
// 前面那些步骤（建素材 / 上传 / 选中 / 填提示词 / 工作台渲染）都不烧额度，照跑。
// 要连出片一起验：E2E_LIVE=1 node dev/e2e/_e2e_video.mjs
const LIVE = process.env.E2E_LIVE === '1'
function skip(name, why) {
  results.push({ name, ok: true, skipped: true })
  console.log(`SKIP  ${name}  （${why}；要真跑就 E2E_LIVE=1）`)
}

function check(name, ok, extra = '') {
  results.push({ name, ok, extra })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`)
}

// 脚本自己写的提示词。以前靠点「示例」按钮自动填，那颗按钮 2026-09-19 撤了（见下方第 5 步）。
// LIVE 模式点「生成视频」要求提示词非空，所以必须自己写。
const PROMPT = '林晚站在雨夜的便利店门口，抬头看着招牌，霓虹在水洼里晃。'

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1180 },
})
const page = await browser.newPage()
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
const net = []
page.on('request', (r) => { if (r.method() !== 'GET') net.push(`${r.method()} ${r.url().replace(URL.replace(/\/$/, ''), '')}`) })
// 抓 draft 的 task_id 用于收尾删除
page.on('response', async (res) => {
  if (res.url().endsWith('/api/tasks/draft') && res.request().method() === 'POST') {
    try { createdTask = (await res.json()).task_id || createdTask } catch { /* ignore */ }
  }
})

const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const textOf = (sel) => page.$eval(sel, (el) => el.textContent.trim()).catch(() => '')

// 按文案点：.nav-item / .mode-switch button / .picklist .pl-item 都没有可依赖的稳定 class
async function clickByText(sel, text) {
  return page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
    if (el) { el.click(); return true }
    return false
  }, sel, text)
}

async function gotoStudioTab(label) {
  const ok = await clickByText('.mode-switch button', label)
  await wait(1600)
  return ok
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await wait(700)

  // 1) 建一个角色。添加流程：点「添加素材」→ 选「角色」→ 卡片直接出现在角色组里且是编辑态，
  //    在上面填名字（编辑态只有名字框，2026-09-15 起描述框已删），再保存。
  await page.click('.head-actions .btn-ghost')
  await wait(300)
  await page.$$eval('.addform .af-kinds button',
    (els) => els.find((e) => e.textContent.trim() === '角色').click())
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', '林晚')
  await page.click('.mgrid .edit-form .btn-primary')
  await wait(2500)
  check('角色已创建', (await page.$$('.mcard')).length === 2, `卡片数=${(await page.$$('.mcard')).length}（角色 + 角色音频 各一张）`)
  check('抓到了 draft 的 task_id（收尾要靠它删）', !!createdTask, createdTask || '（没抓到）')

  // 2) 给角色上传一张真图（走「上传即素材」那条路）
  const fileInput = await page.$('.mops input[type="file"]')
  check('素材条目有上传入口', fileInput !== null)
  await fileInput.uploadFile(IMG)
  await wait(2500)
  const thumbSrc = await page.$eval('.mcard .mthumb img', (el) => el.getAttribute('src')).catch(() => '')
  check('上传后缩略图指向角色图', /characters\//.test(thumbSrc), thumbSrc.slice(0, 60))

  // 3) 进工作台 · 切「分镜工作台」—— 02 现在只在那儿
  await clickByText('.nav-item', '我的作品')
  await wait(1200)
  const tiles = await page.$$('.project-open')
  check('作品列表里有刚建的作品', tiles.length > 0, `${tiles.length} 个`)
  await tiles[0].click()
  await wait(2500)
  await gotoStudioTab('分镜工作台')
  check('分镜工作台里 02 生成视频在位', (await page.$('.generate')) !== null)

  // 4) 用 02 自带的勾选条把素材选进这一段视频（01 的选中跨页会丢）
  const pl = await page.$$eval('.picklist .pl-item em', (els) => els.map((e) => e.textContent.trim()))
  check('02 自带素材勾选条', pl.length > 0, JSON.stringify(pl))
  await clickByText('.picklist .pl-item', '林晚')
  await wait(500)
  check('已进入本次视频素材', (await page.$$('.picked-card')).length === 1, `${(await page.$$('.picked-card')).length} 张`)

  // 5) 提示词：脚本自己写（原来靠「示例」按钮，那颗 09-19 撤了）
  // ⚠️ 这里以前是 `await page.click('.panel-head .link')` —— 指向「示例」按钮。
  //    2026-09-19 斌哥把它撤了（「感觉不需要这个」），元素不存在时 `page.click`
  //    会**直接抛异常**，第 5 步之后的步骤（含 LIVE 出片）**一步都跑不到**。
  check('「示例」按钮已经不在了（09-19 撤的，别再找它）', (await page.$('.panel-head .link')) === null)
  await page.type('textarea[aria-label="视频提示词"]', PROMPT)
  await wait(200)
  const ta = await page.$eval('textarea[aria-label="视频提示词"]', (el) => el.value)
  check('提示词能写进输入框', ta === PROMPT, `${ta.slice(0, 18)}…`)

  // 6) 点「生成视频」—— 会先编排（真调文本模型）再提交出片（真占 GPU）。
  //    这一步烧额度，默认跳过；跳过时改为只验证「按钮可点」。
  if (!LIVE) {
    skip('真实出片（编排调模型 + ComfyUI 占 GPU）', '会花文本模型额度 + GPU 时间')
    check('生成视频按钮已解禁', await page.$eval('.generate', (el) => !el.disabled))
  } else {
    await page.click('.generate')
    await wait(1000)
    const busyLabel = await textOf('.generate')
    check('点下后进入编排/出片态', /编排中|出片中/.test(busyLabel) || (await page.$('.err')) !== null, busyLabel)

    // 等到**终态**：出片完成 / 出片失败 / 界面报错。不能一看到 .out 就跳出 ——
    // .out 在「出片中」时就已经渲染了，那只是中间态。
    const deadline = Date.now() + 180000
    let err = ''
    let out = ''
    while (Date.now() < deadline) {
      await wait(2500)
      err = await textOf('.err')
      out = await textOf('.out')
      if (err || /出片完成|出片失败/.test(out)) break
    }
    console.log('   [出片终态]', err ? `错误：${err.slice(0, 200)}` : out ? out.slice(0, 160) : '（超时仍未结束）')
    console.log('   [网络]', net.filter((n) => /compose|segment|video/.test(n)).join(' | ') || '无相关请求')

    const composed = net.some((n) => n.includes('/compose'))
    const submitted = net.some((n) => n.includes('/segment/video'))
    check('编排请求已发出', composed)
    check('出片请求已发出', submitted)
    // 出片成不成取决于 ComfyUI 通不通（外部基础设施），所以这里只断言"有明确终态"，
    // 不断言成功 —— 隧道断了还能看到失败原因，才算错误处理是好的。
    check('出片给出明确终态（成功或失败原因）', !!(err || /出片完成|出片失败/.test(out)))
  }

  // 7) 工作台各 tab 的回归：共享改动（ToastStack / App.vue 骨架）没把这里带坏。
  //    ⚠️ 工作台根节点是 .workbench（MaterialStudio.vue 里的 .studio 自 2026-09-15 起已停用，
  //    别再拿 .studio 当选择器 —— 它恒为空，断言会永远"通过"其实什么都没测）。
  //    ⚠️ 2026-09-19 起 tab 从两个变三个（多了「生成记录」= 出片结果面板）。
  //    这里断言**名字集合**而不是只数个数 —— 只数长度的话"名字被换掉"照样能过。
  const modes = await page.$$eval('.mode-switch button', (els) => els.map((e) => e.textContent.trim()))
  check('工作台三个 tab 都在位（素材工坊 / 分镜工作台 / 生成记录）',
    JSON.stringify(modes) === JSON.stringify(['素材工坊', '分镜工作台', '生成记录']), JSON.stringify(modes))

  await gotoStudioTab('素材工坊')
  check('素材工坊渲染正常（01 在）', (await page.$('.workbench .mgroups')) !== null)
  await page.screenshot({ path: 'docs/_shots/view_workspace_studio.png' })

  await gotoStudioTab('分镜工作台')
  check('能切回分镜工作台', (await page.$('.generate')) !== null)
  check('分镜工作台不重复渲染 01', (await page.$('.mgroups')) === null)
  await page.screenshot({ path: 'docs/_shots/view_workspace_storyboard.png' })

  check('全程无 JS 报错', errors.length === 0, errors.join(' | '))
} catch (err) {
  check('验证脚本执行完成', false, err.message)
} finally {
  // 失败时留一张现场图。要兜住异常：页面根本没加载起来时 screenshot 会抛
  // ProtocolError（Not attached to an active page），那会把真正的失败原因盖掉。
  if (results.some((r) => !r.ok)) {
    const name = '_e2e_video_failed'
    try {
      await page.screenshot({ path: `docs/_shots/${name}.png`, fullPage: false })
      console.log(`失败现场 -> docs/_shots/${name}.png`)
    } catch (shotErr) {
      console.log(`（留不了现场图：${shotErr.message}）`)
    }
  }
  await browser.close()

  // 收尾：删掉测试作品（项目硬约定，别在作品列表里留垃圾）
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
}

const failed = results.filter((r) => !r.ok)
console.log(`\n=== ${results.length - failed.length}/${results.length} 通过 ===`)
process.exit(failed.length ? 1 : 0)

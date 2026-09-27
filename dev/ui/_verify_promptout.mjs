// 验证「编排结果预览」的展示层（2026-09-18 改版，当天又收紧一次）。
//
// 斌哥的要求：素材对应关系（谁对应谁）**后端算**，前端**只用人话展示** ——
// 六段式里的编号声明（<Picture 1> / <Subject 1>）、保留标记、ComfyUI 连线单
// 必须原样发给模型（官方格式要求），但**不该出现在界面里**。
// 第一次做成"收进折叠"，斌哥看到展开态截图后说连点开能看都不要，于是彻底拿掉。
// 要查"发给模型的原样"去「生成记录」里那一段的「用了什么」——边车 json 存了整份。
//
// 所以这个脚本卡的核心是三条：
//   ① 默认可见区只有三样：素材对应（人话）+ 正文 + 提醒
//   ② **整块 DOM 里**都不许出现机器文字（不只是"可见区"—— 折叠内容也在 DOM 里，
//      所以断言必须用 textContent 而不是 offsetParent 那套）
//   ③ 显示的就是发送的（正文 === 后端返回的 video_prompt）
//
// ⚠️ **不烧额度**：拦截 /compose 直接回一份罐头响应，/segment/video 也拦掉 ——
// 既不调文本模型，也不碰 ComfyUI。
//
// 跑法：node dev/ui/_verify_promptout.mjs                （默认打 8000 生产包）
//       E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_promptout.mjs
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

// 罐头响应：形状与 server/app.py 的 /compose 返回值一致
const BODY = '[Shot 1] Live-action, cinematic: 35mm film grain, low-key night exterior. ' +
  'She steps through the sliding doors and stops mid-step. ' +
  '[Shot 2] At 00:03.333, a medium close-up as her eyes fix on something off-screen.'
const FULL = [
  'subject_definitions: <Subject 1> is the person shown in <Picture 1>: a young woman. ' +
    '<Subject 2> is the environment shown in <Picture 2>: a wet street.',
  'summary: [reference generation] A 10-second clip featuring <Subject 1>.',
  'retention_analysis: <Subject 1> (appears in every shot): fully_preserved - facial features ' +
    'stay identical to <Picture 1> throughout.',
  'detailed_description: ' + BODY,
  'overall_soundscape: Steady rain on glass.',
  'non_diegetic_music: N/A',
].join('\n\n')
const CANNED = {
  video_prompt: BODY,
  soundscape: 'Steady rain on glass.',
  prompt: FULL,
  manifest: '图片 1 → ref_images.ref_image_0（林晚 的外貌锁定）\n图片 2 → ref_images.ref_image_1（雨夜街道 的环境锁定）',
  warnings: ['示例提醒：多人物时只有第一张图会真正发出去'],
  slots: [
    { tag: '<Picture 1>', kind: 'image', role: 'character', label: '林晚', human: '图片 1 作为「林晚」的外貌锁定' },
    { tag: '<Picture 2>', kind: 'image', role: 'scene', label: '雨夜街道', human: '图片 2 作为「雨夜街道」的环境锁定' },
  ],
}

let taskId = ''
try {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--disable-gpu', '--no-sandbox'],
    defaultViewport: { width: 1440, height: 1000 },
  })
  const page = await browser.newPage()
  page.on('pageerror', (e) => console.log('[pageerror]', e.message))
  await page.setRequestInterception(true)
  let composeHits = 0
  page.on('request', (req) => {
    const u = req.url()
    if (u.includes('/compose') && req.method() === 'POST') {
      composeHits += 1
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(CANNED) })
    }
    if (u.includes('/segment/video')) {
      // 拦住出片：这个脚本只验展示，真出片会占 GPU
      return req.respond({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: '（验证脚本拦下的，没有真的出片）' }) })
    }
    return req.continue()
  })

  console.log('='.repeat(74))
  console.log('「编排结果预览」展示验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 0) 建作品 + 传两张图（走 API，免费） ----------
  const draft = await (await fetch(`${API}/api/tasks/draft`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '编排预览验证' }),
  })).json()
  taskId = draft.task_id
  const fs = await import('node:fs')
  const dataB64 = fs.readFileSync(FACE).toString('base64')
  for (const [kind, name] of [['character', '林晚.png'], ['scene', '雨夜街道.png']]) {
    await fetch(`${API}/api/tasks/${taskId}/materials/upload?kind=${kind}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: name, data_b64: dataB64 }),
    })
  }
  check('建出测试作品并传了两张素材', !!taskId)

  // ---------- 1) 进工作台 · 分镜工作台 · 勾素材 · 写描述 ----------
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

  const picked = await page.evaluate(() => {
    const items = [...document.querySelectorAll('.picklist .pl-item')]
    items.forEach((b) => b.click())
    return items.length
  })
  await sleep(600)
  check('勾上了素材', picked > 0, `${picked} 项`)

  await page.type('textarea[aria-label="视频提示词"]', '雨夜，女孩走进便利店，忽然停住。')
  await sleep(300)
  await page.click('button.generate')
  await sleep(1500)
  check('真的走到了 /compose（拦下来的是罐头响应）', composeHits === 1, `compose 命中 ${composeHits} 次`)

  // ---------- 2) 整块预览里只有人话 ----------
  const view = await page.evaluate(() => {
    const root = document.querySelector('.promptout')
    if (!root) return { 有: false }
    const body = root.querySelector('.po-prompt')
    return {
      有: true,
      素材行: [...root.querySelectorAll('.po-slots li')].map((li) => li.textContent.trim()),
      正文: (body?.textContent || '').trim(),
      // ⚠️ 用整块 DOM 的 textContent，不是"可见区" —— 折叠/隐藏内容也在 DOM 里，
      //    只查可见区会漏掉"其实还在、只是 display:none"的情况。
      整块文本: root.textContent,
      有折叠: !!root.querySelector('details'),
      提醒条: [...root.querySelectorAll('.po-warn li')].map((li) => li.textContent.trim()),
    }
  })
  console.log('探针 编排预览:', JSON.stringify({
    ...view, 正文: view.正文?.slice(0, 50), 整块文本: view.整块文本?.replace(/\s+/g, ' ').slice(0, 130),
  }))

  check('编排结果渲染出来了', view.有 === true)
  check('素材对应关系用人话列出（后端 slots[].human）',
    view.素材行?.join(' | ') === '图片 1 作为「林晚」的外貌锁定 | 图片 2 作为「雨夜街道」的环境锁定',
    JSON.stringify(view.素材行))
  check('显示的是正文，不是六段式全文',
    (view.正文 || '').startsWith('[Shot 1]') && !(view.正文 || '').includes('subject_definitions'),
    (view.正文 || '').slice(0, 50))
  check('正文与后端返回的 video_prompt 一字不差',
    view.正文 === BODY, '（显示的就是发送的）')
  check('整块 DOM 里没有 <Picture N> / <Subject N> 编号声明',
    !/<(Picture|Subject|Audio)\s*\d>/.test(view.整块文本 || ''),
    '整块文本：' + (view.整块文本 || '').replace(/\s+/g, ' ').slice(0, 130))
  check('整块 DOM 里没有 subject_definitions / retention_analysis / non_diegetic_music',
    !/subject_definitions|retention_analysis|non_diegetic_music/.test(view.整块文本 || ''))
  check('整块 DOM 里没有 ComfyUI 连线单（ref_image_0）',
    !/ref_image_0/.test(view.整块文本 || ''))
  check('连"点开能看"的折叠都没有了', view.有折叠 === false)
  check('后端给的 warnings 有展示（原来被丢掉了）',
    view.提醒条?.length === 1 && view.提醒条[0].includes('多人物'), JSON.stringify(view.提醒条))

  await page.screenshot({ path: 'docs/_shots/promptout.png', fullPage: true })

  // ---------- 3) 但"发给模型的原样"不能丢 —— 它得能在生成记录里查到 ----------
  // （边车 json 的 prompt 字段存的就是整份六段式，见 _verify_segment_record.py）
  check('正文里保留了 <Shot N> 时间轴（模型要靠它切镜）',
    /\[Shot 1\]/.test(view.正文 || '') && /At 00:03\.333/.test(view.正文 || ''))

  await browser.close()
} catch (err) {
  check('验证脚本执行完成', false, err.message)
} finally {
  if (taskId) {
    try {
      const r = await fetch(`${API}/api/tasks/${taskId}?confirm=true&purge=true`, { method: 'DELETE' })
      console.log(`\n清理测试作品 ${taskId} -> ${r.status}`)
    } catch (e) {
      console.log(`\n⚠️ 清理失败：${e.message}`)
    }
  }
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

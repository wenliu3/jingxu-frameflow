// 验证两件小事（2026-09-19）：
//   ① 「示例」按钮已经从「视频提示词」头里去掉（斌哥：「感觉不需要这个」）
//   ② 服务设置里多了「视频工作流」下拉，且**真的能存进去**
//
// ⚠️ ② 会真写一次配置，但**开头 GET 整份、结尾原样 POST 回去** ——
//    ServiceConfigPatch 所有字段默认 ""，只发一个字段会把 API key 之类全清空
//    （MEMORY 里记过这个坑）。finally 里保证还原。
//
// 跑法：node dev/ui/_verify_settings.mjs                （默认打 8000 生产包）
//       E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_settings.mjs
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const API = process.env.E2E_API || 'http://127.0.0.1:8000'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function check(name, cond, extra = '') {
  results.push(!!cond)
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? `\n        → ${extra}` : ''))
}

const getConfig = async () => (await fetch(`${API}/api/config`)).json()
const putConfig = async (body) => (await fetch(`${API}/api/config`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})).json()

let original = null
try {
  console.log('='.repeat(74))
  console.log('「示例」按钮 + 视频工作流下拉   ' + URL)
  console.log('='.repeat(74) + '\n')

  original = await getConfig()
  check('开头拿到整份配置（用于还原）', !!original && 'video_workflow' in original,
    `video_workflow=${original?.video_workflow}`)
  // ⚠️ 只验"是合法值"，**不验"等于 i2v"** —— 默认值确实是 i2v（`SERVICE_DEFAULTS`），
  //    但斌哥自己在「服务设置」里切成 ref2va 是完全正常的用法，写死成 i2v 的话
  //    他一改这边就报 FAIL，看着像产品坏了。想盯默认值请去看 `SERVICE_DEFAULTS`。
  check('video_workflow 是合法值（i2v / ref2va）',
    ['i2v', 'ref2va'].includes(String(original?.video_workflow)),
    String(original?.video_workflow))

  // ---------- 后端：字段能不能存进去 ----------
  const r2v = await putConfig({ ...original, video_workflow: 'ref2va' })
  check('存 ref2va 后 GET 回来是 ref2va', (await getConfig()).video_workflow === 'ref2va',
    `POST 回包=${r2v?.video_workflow}`)
  await putConfig(original)
  check('还原回**跑之前那个值**成功（不是写死回 i2v）',
    (await getConfig()).video_workflow === original.video_workflow,
    `期望 ${original.video_workflow}`)
  check('还原后 API key 没被清空（整份 POST 的意义）',
    (await getConfig()).text_api_key === original.text_api_key
    && (await getConfig()).audio_api_key === original.audio_api_key)

  // ---------- 前端 ----------
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--disable-gpu', '--no-sandbox'],
    defaultViewport: { width: 1440, height: 1000 },
  })
  const page = await browser.newPage()
  page.on('pageerror', (e) => console.log('[pageerror]', e.message))
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)

  // ① 示例按钮：进一个作品的「分镜工作台」才看得到 02
  const clickByText = (sel, text) => page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
    if (el) { el.click(); return true }
    return false
  }, sel, text)

  const draft = await (await fetch(`${API}/api/tasks/draft`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '设置验证' }),
  })).json()
  const taskId = draft.task_id
  try {
    await clickByText('.nav-item', '我的作品')
    await sleep(1200)
    const tiles = await page.$$('.project-open')
    await tiles[0].click()
    await sleep(2200)
    await clickByText('.mode-switch button', '分镜工作台')
    await sleep(1500)

    const head = await page.evaluate(() => {
      const h = document.querySelector('.pvhead')
      if (!h) return { 有: false }
      return {
        有: true,
        按钮: [...h.querySelectorAll('button')].map((b) => b.textContent.trim()),
      }
    })
    console.log('探针 提示词头:', JSON.stringify(head))
    check('视频提示词头还在', head.有 === true)
    check('「示例」按钮没了', !(head.按钮 || []).some((t) => t.includes('示例')),
      JSON.stringify(head.按钮))
    // 文案是「让 AI 帮写」（2026-09-19 那颗按钮从"切模式"改成真的做中文优化，
    // 标签同时由「让 AI 帮我写」缩短为「让 AI 帮写」）。这里只要求它还在，
    // 别误伤 —— 具体接线由 _verify_prompt_ui.mjs 盯着。
    check('「让 AI 帮写」还在（只删示例，别误伤）',
      (head.按钮 || []).some((t) => t.includes('让 AI 帮写')), JSON.stringify(head.按钮))

    // ② 「单图参考」那个下拉已经撤掉（2026-09-19 斌哥：「我从来没让你弄什么单图参考」）
    await clickByText('.nav-item', '服务设置')
    await sleep(1000)
    const modal = await page.evaluate(() => {
      const m = document.querySelector('.modal')
      const labels = [...(m?.querySelectorAll('.mfield') || [])]
      const wfField = labels.find((l) => l.textContent.includes('视频工作流'))
      const sel = wfField?.querySelector('select')
      return {
        有弹窗: !!m,
        文本: (m?.textContent || '').replace(/\s+/g, ' '),
        字段: labels.map((l) => l.textContent.trim().slice(0, 16)),
        有工作流下拉: !!sel,
        工作流选项: [...(sel?.options || [])].map((o) => o.textContent.trim()),
      }
    })
    console.log('探针 服务设置:', JSON.stringify({ ...modal, 文本: modal.文本.slice(0, 90) }))
    check('服务设置打得开', modal.有弹窗 === true)
    // ⚠️ 只断言"没有「单图参考」这个**控件**"，**别把整段弹窗文案拿去正则匹配** ——
    //    「多图参考」现在是正常措辞（"Ref2VA 多图参考 专用"之类），
    //    对整份 textContent 做 /单图参考|多图参考/ 会一直误报（2026-09-27 踩过）。
    //    09-19 撤掉的是我当时自造的那个「单图参考」下拉，不是这个词本身。
    check('没有「单图参考」这个下拉/选项（09-19 撤的是它）',
      !(modal.工作流选项 || []).some((t) => t.includes('单图参考'))
      && !(modal.字段 || []).some((t) => t.includes('单图参考')),
      JSON.stringify(modal.工作流选项))
    // ⚠️ 09-19 撤掉的是我当时自己造的那个「单图参考」下拉；后来**又正经加了一个
    //    「视频工作流」下拉**（i2v / ref2va）。原来的断言只查旧标签的字面量，
    //    于是"下拉明明在、断言却写着它不在"也能过 —— 陈旧断言，2026-09-27 改成正面断言。
    check('「视频工作流」下拉在位（撤的是自造的「单图参考」，不是它）',
      modal.有工作流下拉 === true, JSON.stringify(modal.字段))
    check('两个选项都在，且 Ref2VA 那条写的是「最多 9 张」',
      (modal.工作流选项 || []).length === 2
      && modal.工作流选项.some((t) => t.includes('I2V'))
      && modal.工作流选项.some((t) => t.includes('Ref2VA') && t.includes('最多 9 张')),
      JSON.stringify(modal.工作流选项))
    check('视频画质那组还在（只撤了新增的，别误伤）',
      modal.字段.some((t) => t.includes('画质')), JSON.stringify(modal.字段))
    // 弹窗很长，默认停在顶部 —— 先把「视频工作流」那一行滚到视野中间再截图，
    // 否则截出来根本看不到这次要核对的那行。
    await page.evaluate(() => {
      const f = [...document.querySelectorAll('.modal .mfield')]
        .find((l) => l.textContent.includes('视频工作流'))
      f?.scrollIntoView({ block: 'center' })
    })
    await sleep(400)
    await page.screenshot({ path: 'docs/_shots/settings_workflow.png' })
  } finally {
    await fetch(`${API}/api/tasks/${taskId}?confirm=true&purge=true`, { method: 'DELETE' })
    await browser.close()
  }
} catch (err) {
  check('验证脚本执行完成', false, err.message)
} finally {
  if (original) {
    try {
      await putConfig(original)
      const back = await getConfig()
      console.log(`\n配置已还原：video_workflow=${back.video_workflow} / `
        + `text_api_key ${back.text_api_key === original.text_api_key ? '完好' : '⚠️ 丢了'}`)
    } catch (e) {
      console.log(`\n⚠️ 还原失败，请手动检查 service_config.json：${e.message}`)
    }
  }
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

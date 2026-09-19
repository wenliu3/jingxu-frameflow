// 验证两件小事（2026-09-19）：
//   ① 「示例」按钮已经从「视频提示词」头里去掉（斌哥：「感觉不需要这个」）
//   ② 服务设置里多了「视频工作流」下拉，且**真的能存进去**
//
// ⚠️ ② 会真写一次配置，但**开头 GET 整份、结尾原样 POST 回去** ——
//    ServiceConfigPatch 所有字段默认 ""，只发一个字段会把 API key 之类全清空
//    （MEMORY 里记过这个坑）。finally 里保证还原。
//
// 跑法：node _verify_settings.mjs                （默认打 8000 生产包）
//       E2E_URL=http://127.0.0.1:5173/ node _verify_settings.mjs
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
  check('默认是单图参考 i2v（不能悄悄切走）', original?.video_workflow === 'i2v',
    String(original?.video_workflow))

  // ---------- 后端：字段能不能存进去 ----------
  const r2v = await putConfig({ ...original, video_workflow: 'ref2va' })
  check('存 ref2va 后 GET 回来是 ref2va', (await getConfig()).video_workflow === 'ref2va',
    `POST 回包=${r2v?.video_workflow}`)
  await putConfig(original)
  check('还原回 i2v 成功', (await getConfig()).video_workflow === 'i2v')
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
      return {
        有弹窗: !!m,
        文本: (m?.textContent || '').replace(/\s+/g, ' '),
        字段: labels.map((l) => l.textContent.trim().slice(0, 16)),
        有工作流下拉: labels.some((l) => l.textContent.includes('用哪份 ComfyUI 工作流')),
      }
    })
    console.log('探针 服务设置:', JSON.stringify({ ...modal, 文本: modal.文本.slice(0, 90) }))
    check('服务设置打得开', modal.有弹窗 === true)
    check('「单图参考 / 多图参考」字样已经没了', !/单图参考|多图参考/.test(modal.文本))
    check('那个工作流下拉也撤掉了', modal.有工作流下拉 === false, JSON.stringify(modal.字段))
    check('视频画质那组还在（只撤了新增的，别误伤）',
      modal.字段.some((t) => t.includes('画质')), JSON.stringify(modal.字段))
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

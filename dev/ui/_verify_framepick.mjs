// 验证「首尾帧选择器」（2026-09-19 加）。
//
// 斌哥的规格：
//   · 一个按钮，点开是弹窗，两个地方放照片（首帧 / 尾帧）
//   · 只挑首帧 → 以它开头｜只挑尾帧 → 以它收尾｜都挑 → 一头一尾｜都不挑 → 没有首尾帧
//   · 可挑的只有**场景**和**其他图片**；人物和道具的图**选不了**
//
// 最后一条是这个脚本的重点：列表里混进一张定妆照，用户就能把视频结尾锁成一张
// 设定图 —— 那是明显的错，而且从代码上看不出来，得靠 UI 断言卡住。
//
// ⚠️ 不烧额度：只点到弹窗为止，不点「生成视频」。
//
// 跑法：node dev/ui/_verify_framepick.mjs                （默认打 8000 生产包）
//       E2E_URL=http://127.0.0.1:5173/ node dev/ui/_verify_framepick.mjs
import { createRequire } from 'node:module'
import fs from 'node:fs'

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

let taskId = ''
try {
  console.log('='.repeat(74))
  console.log('首尾帧选择器验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 0) 建作品 + 四类素材各来一个 ----------
  taskId = (await (await fetch(`${API}/api/tasks/draft`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '首尾帧验证' }),
  })).json()).task_id
  const b64 = fs.readFileSync(FACE).toString('base64')
  for (const [kind, name] of [['character', '林晚.png'], ['scene', '雨夜街道.png'],
                              ['prop', '电击器.png'], ['image', '参考画面.png']]) {
    await fetch(`${API}/api/tasks/${taskId}/materials/upload?kind=${kind}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: name, data_b64: b64 }),
    })
  }
  check('建出作品并传了四类素材（角色/场景/道具/其他图片）', !!taskId)

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--disable-gpu', '--no-sandbox'],
    defaultViewport: { width: 1440, height: 1000 },
  })
  const page = await browser.newPage()
  page.on('pageerror', (e) => console.log('[pageerror]', e.message))
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(800)
  const clickByText = (sel, text) => page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
    if (el) { el.click(); return true }
    return false
  }, sel, text)

  try {
    await clickByText('.nav-item', '我的作品')
    await sleep(1200)
    await (await page.$$('.project-open'))[0].click()
    await sleep(2200)
    await clickByText('.mode-switch button', '分镜工作台')
    await sleep(1500)

    const btn = () => page.evaluate(() => {
      const b = document.querySelector('button.flfbtn')
      return { 有: !!b, 文案: (b?.textContent || '').trim(), 高亮: !!b?.classList.contains('on') }
    })
    check('有「首尾帧」按钮（不再是复选框）',
      (await btn()).有 === true && (await page.$('.flf')) === null, JSON.stringify(await btn()))
    check('没挑时文案是「选择首尾帧（可选）」', (await btn()).文案 === '选择首尾帧（可选）',
      (await btn()).文案)

    // ---------- 打开弹窗 ----------
    await page.click('button.flfbtn')
    await sleep(600)
    const dlg = await page.evaluate(() => {
      const d = document.querySelector('.fdialog')
      if (!d) return { 有: false }
      return {
        有: true,
        槽: [...d.querySelectorAll('.fslot')].map((s) => ({
          标签: s.querySelector('.fslabel')?.textContent.trim(),
          空态: s.querySelector('.fsempty')?.textContent.trim() || '',
          激活: s.classList.contains('active'),
        })),
        卡片: [...d.querySelectorAll('.fcard')].map((c) => ({
          名: c.querySelector('em')?.textContent.trim(),
          类: c.querySelector('.ftag')?.textContent.trim(),
        })),
      }
    })
    console.log('探针 弹窗:', JSON.stringify(dlg))
    check('弹窗打开了', dlg.有 === true)
    check('两个槽：首帧 / 尾帧',
      (dlg.槽 || []).map((s) => s.标签).join('/') === '首帧/尾帧',
      JSON.stringify((dlg.槽 || []).map((s) => s.标签)))
    check('默认在填「首帧」', (dlg.槽 || [])[0]?.激活 === true)
    check('两个槽一开始都是空的', (dlg.槽 || []).every((s) => s.空态.length > 0),
      JSON.stringify((dlg.槽 || []).map((s) => s.空态)))

    // ---------- 可选范围：只有场景 + 其他图片 ----------
    const names = (dlg.卡片 || []).map((c) => c.名)
    const kinds = [...new Set((dlg.卡片 || []).map((c) => c.类))]
    check('列表里只有「场景」和「其他图片」两类', kinds.sort().join('/') === '其他图片/场景',
      JSON.stringify(kinds))
    check('场景和图片都在（雨夜街道 / 参考画面）',
      names.includes('雨夜街道') && names.includes('参考画面'), JSON.stringify(names))
    check('人物选不了（林晚不在列表里）', !names.includes('林晚'), JSON.stringify(names))
    check('道具选不了（电击器不在列表里）', !names.includes('电击器'), JSON.stringify(names))
    await page.screenshot({ path: 'docs/_shots/framepick.png' })

    // ---------- 只挑首帧 ----------
    const clickCard = (name) => page.evaluate((n) => {
      const c = [...document.querySelectorAll('.fdialog .fcard')]
        .find((x) => x.querySelector('em')?.textContent.trim() === n)
      if (c) { c.click(); return true }
      return false
    }, name)
    check('点到「雨夜街道」', await clickCard('雨夜街道'))
    await sleep(400)
    const afterFirst = await page.evaluate(() =>
      [...document.querySelectorAll('.fdialog .fslot')].map((s) =>
        s.querySelector('em')?.textContent.trim() || ''))
    check('它进了首帧槽', afterFirst[0] === '雨夜街道', JSON.stringify(afterFirst))
    check('尾帧槽还空着', afterFirst[1] === '', JSON.stringify(afterFirst))

    // ---------- 再挑尾帧 ----------
    await page.evaluate(() => document.querySelectorAll('.fdialog .fslot')[1].click())
    await sleep(300)
    check('点尾帧槽后它变成激活的',
      await page.evaluate(() => document.querySelectorAll('.fdialog .fslot')[1].classList.contains('active')))
    check('点到「参考画面」', await clickCard('参考画面'))
    await sleep(400)
    const afterBoth = await page.evaluate(() =>
      [...document.querySelectorAll('.fdialog .fslot')].map((s) =>
        s.querySelector('em')?.textContent.trim() || ''))
    check('两张分别落在首帧和尾帧槽', afterBoth.join('/') === '雨夜街道/参考画面',
      JSON.stringify(afterBoth))

    // ---------- 关闭后按钮文案 ----------
    await clickByText('.fdialog .pd-head button', '关闭')
    await sleep(400)
    check('关掉后按钮显示挑了哪两张',
      (await btn()).文案 === '首帧：雨夜街道 · 尾帧：参考画面', (await btn()).文案)
    check('挑了之后按钮是高亮的', (await btn()).高亮 === true)

    // ---------- 清空 ----------
    await page.click('button.flfbtn')
    await sleep(500)
    await clickByText('.fdialog .ffoot button', '清空首尾帧')
    await sleep(400)
    await clickByText('.fdialog .pd-head button', '关闭')
    await sleep(400)
    check('清空后回到「选择首尾帧（可选）」', (await btn()).文案 === '选择首尾帧（可选）',
      (await btn()).文案)
    check('清空后不再高亮', (await btn()).高亮 === false)
  } finally {
    await browser.close()
  }
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

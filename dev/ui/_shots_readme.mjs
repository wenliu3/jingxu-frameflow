// README 配图生成（2026-09-28）。
//
// 与 dev/ui/_shots*.mjs 的分工：那几个截到 docs/_shots/（人工核对用，已 gitignore）；
// 这个把**要进仓库的** README 配图截到 docs/images/ —— README.md 直接引用那几张，
// 界面改版后重跑这个脚本，图就不会和新界面对不上。
//
// 两条纪律：
//   1. **不点任何「生成」按钮** —— 只截界面。视频那段依赖 ComfyUI，没开就不碰
//      （本脚本只走到「分镜工作台」的选素材 / 提示词面板，不触发出片）；
//   2. **Key 自动打码** —— README 是公开的，截服务设置前先把所有密钥输入框掩掉。
//
// 跑法：node dev/ui/_shots_readme.mjs [作品名关键词]
//   默认打开标题含「糯糯」的作品（本地素材最全：8 角色 + 20 素材 + 音色），
//   想在别的作品上出图就传关键词，例如 node dev/ui/_shots_readme.mjs 米下
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const require = createRequire(import.meta.url)
const puppeteer = require(
  path.join(os.homedir(), '.workbuddy-ai/binaries/node/workspace/node_modules/puppeteer-core'),
)

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
// 默认打 8000（后端同源伺服 web/dist 生产包）；要打 dev server：E2E_URL=http://127.0.0.1:5173/
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const OUT = 'docs/images'
const KEYWORD = process.argv[2] || '糯糯'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
fs.mkdirSync(OUT, { recursive: true })

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1080 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))

const clickByText = (sel, text) => page.evaluate((s, t) => {
  const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
  if (el) { el.click(); return true }
  return false
}, sel, text)

async function shot(name, el = null) {
  await sleep(600)
  await (el || page).screenshot({ path: `${OUT}/${name}.png` })
  console.log('shot ->', `${OUT}/${name}.png`)
}

// 敏感信息掩码（README 是公开的，这条是硬要求）：
//   · type=password 的输入框 → 圆点；
//   · 长得像 key 的文本（sk- / ms- / ey…）→ 只留前缀；
//   · 隧道 / 内网地址（pinggy / ngrok / cloudflare / 含 IP 的 URL）→ 整段掩掉
//     （ComfyUI 的 pinggy 地址会过期，但没必要出现在仓库里；官方 API 域名不受影响）。
async function maskSecrets() {
  const n = await page.evaluate(() => {
    let hit = 0
    for (const el of document.querySelectorAll('input')) {
      if (el.type === 'password' && el.value) { el.value = '••••••••'; hit += 1; continue }
      const v = String(el.value || '')
      if (v.length >= 20 && /^(sk|ms|ey|ghp|hf|api)[-_]/i.test(v)) {
        el.value = `${v.slice(0, 3)}••••••••••••`
        hit += 1
        continue
      }
      if (/^https?:/i.test(v) && /pinggy|ngrok|trycloudflare|(\d{1,3}\.){3}\d{1,3}/i.test(v)) {
        el.value = 'https://•••••••••••••••'
        hit += 1
      }
    }
    return hit
  })
  if (n) console.log(`  （已给 ${n} 个敏感输入框打码）`)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)

  // ---------- 1) 我的作品（README 第一格「创作工作室」） ----------
  await clickByText('.nav-item', '我的作品')
  await page.waitForSelector('.project-open', { timeout: 15000 })
  await sleep(1200)
  await shot('home')

  // ---------- 2) 打开目标作品 → 素材工坊 ----------
  const idx = await page.evaluate((kw) => {
    const tiles = [...document.querySelectorAll('.project-open')]
    return tiles.findIndex((t) => (t.textContent || '').includes(kw))
  }, KEYWORD)
  if (idx < 0) throw new Error(`作品列表里找不到含「${KEYWORD}」的作品，换一个关键词再跑`)
  const tiles = await page.$$('.project-open')
  await tiles[idx].click()
  console.log(`  打开作品：第 ${idx + 1} 格（关键词「${KEYWORD}」）`)
  await sleep(2600)
  await clickByText('.mode-switch button', '素材工坊')
  await page.waitForSelector('.mcard', { timeout: 20000 })
  await sleep(1500)                       // 等素材缩略图加载完
  await shot('workspace-characters')

  // ---------- 3) 分镜工作台：选素材（分组 + 搜索）+ 视频提示词面板 ----------
  // ⚠️ 只截图，不点「生成视频」—— 视频依赖 ComfyUI，这里不碰。
  await clickByText('.mode-switch button', '分镜工作台')
  await page.waitForSelector('.picklist .pl-item', { timeout: 20000 })
  await sleep(900)
  // 勾两个素材（角色 + 场景）：「本次视频素材」有内容，图更接近真实用法。
  // 纯前端的勾选动作，不触发任何生成；刷掉页面就没了，不会留状态。
  await page.evaluate(() => {
    const pick = (label) => {
      const g = [...document.querySelectorAll('.picklist .pl-group')]
        .find((x) => (x.querySelector('.pl-ghead b')?.textContent || '').trim() === label)
      g?.querySelector('.pl-item')?.click()
    }
    pick('角色')
    pick('场景')
  })
  await sleep(700)
  await shot('storyboard-gallery')

  // ---------- 4) 服务设置（弹窗单独截元素，Key 已打码） ----------
  await page.click('.settings-button')
  await page.waitForSelector('.modal', { timeout: 10000 })
  await sleep(800)
  // 弹窗（实测 2200+ px 高）比视口长，而 .modal-mask 是 fixed + overflow 的滚动容器 ——
  // **视口外的部分截出来就是一片空白**。所以先把视口临时撑到内容高度，截完再还原。
  const needH = await page.evaluate(() =>
    Math.ceil(document.querySelector('.modal-mask').scrollHeight) + 20)
  await page.setViewport({ width: 1440, height: needH, deviceScaleFactor: 1 })
  await sleep(700)
  await maskSecrets()
  // 只裁到「音频模型」块结束：涵盖四个模型配置块，图不至于长成 1:3 的窄条
  //（再往下的「视频流程 / 画质」是 ComfyUI 细节，README 里另有文字章节讲）。
  const box = await page.evaluate(() => {
    const m = document.querySelector('.modal')
    const r = m.getBoundingClientRect()
    const groups = [...m.querySelectorAll('.mgroup')]
    const anchor = groups.find((g) => (g.querySelector('.mgtitle')?.textContent || '').includes('音频模型'))
      || groups[groups.length - 1]
    const ar = anchor.getBoundingClientRect()
    return { x: r.left, y: r.top, width: r.width, height: ar.bottom - r.top + 28 }
  })
  await sleep(300)
  await page.screenshot({
    path: `${OUT}/service-settings.png`,
    clip: { x: box.x, y: box.y, width: box.width, height: box.height },
  })
  console.log('shot ->', `${OUT}/service-settings.png`)
  await page.setViewport({ width: 1440, height: 1080, deviceScaleFactor: 1 })
  await clickByText('.modal .mhead button', '关闭')

  console.log('\n完成。以上 4 张直接对应 README.md 顶部表格的引用。')
} finally {
  await browser.close()
}

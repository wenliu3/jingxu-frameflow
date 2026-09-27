// 打开某个作品的工作台（素材工坊）截图，用于和「新建作品」页对比版式。
// 跑法：node dev/ui/_shots_workspace.mjs [taskId]
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:5173/'
const OUT = 'docs/_shots'
const WANT = process.argv[2] || ''

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1400 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(900)

  await page.$$eval('.nav-item', (els) => els.find((e) => e.textContent.includes('我的作品')).click())
  await sleep(900)

  const tiles = await page.$$('.project-open')
  console.log('作品数:', tiles.length)
  // 默认打开最后一个（列表按创建时间倒序，最后 = 最早的那个）
  const idx = WANT ? Math.max(0, tiles.length - 1) : 0
  await tiles[idx].click()
  await sleep(2000)

  await page.screenshot({ path: `${OUT}/workspace_studio.png` })
  console.log('shot ->', `${OUT}/workspace_studio.png`)

  const info = await page.evaluate(() => {
    const h1 = document.querySelector('.workspace-heading h1')?.textContent?.trim()
    const secs = [...document.querySelectorAll('.mcard, .mat-sec, section')].length
    return { title: h1, sectionCount: secs }
  })
  console.log('探针:', JSON.stringify(info))
} finally {
  await browser.close()
}

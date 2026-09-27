// 截图脚本：把「新建作品」页在两种状态（空 / 有素材）和「我的作品」页各截一张，
// 用于人工核对版式。跑法：node dev/ui/_shots.mjs
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
// 默认打 8000（后端同源伺服 web/dist，即生产产物）：不依赖 vite dev server 是否在跑，
// 而且测的就是用户实际拿到的那份。要打 dev server 就 E2E_URL=http://127.0.0.1:5173/ node ...
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const OUT = 'docs/_shots'

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1180 },
})
const page = await browser.newPage()

async function shot(name) {
  await new Promise((r) => setTimeout(r, 500))
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log('shot ->', `${OUT}/${name}.png`)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await shot('view_create_empty')

  // 加一个角色（填描述，跳过 LLM 设计锚点，快且确定）
  await page.click('.head-actions .btn-ghost')
  await page.type('.addform input[aria-label="新素材名字"]', '林晚')
  await page.type('.addform input[aria-label="新素材描述"]', '22 岁女性，齐肩黑发，米色针织外套')
  await page.click('.addform .btn-primary')
  await new Promise((r) => setTimeout(r, 2500))

  // 再加一个场景，并选中两个素材，看「本次视频素材」有内容时的样子
  await page.click('.head-actions .btn-ghost')
  await new Promise((r) => setTimeout(r, 250))
  await page.$$eval('.addform .seg button', (els) => els.find((e) => e.textContent.trim() === '场景').click())
  await page.type('.addform input[aria-label="新素材名字"]', '便利店内部')
  await page.type('.addform input[aria-label="新素材描述"]', '凌晨便利店，冷白顶灯，玻璃门有雨痕')
  await page.click('.addform .btn-primary')
  await new Promise((r) => setTimeout(r, 2500))

  await page.$$eval('.item .thumb', (els) => els.slice(0, 2).forEach((e) => e.click()))
  await page.click('.panel-head .link')
  await shot('view_create_filled')

  // 「我的作品」页：故事驱动的入口搬到了这里
  await page.$$eval('.nav-item', (els) => els.find((e) => e.textContent.includes('我的作品')).click())
  await new Promise((r) => setTimeout(r, 900))
  await shot('view_library')

  // 服务配置弹窗：画质 / 步数 / LoRA 那一组，用于核对说明文案
  await page.$$eval('.nav-item', (els) => els.find((e) => e.textContent.includes('服务设置')).click())
  await new Promise((r) => setTimeout(r, 600))
  await page.evaluate(() => {
    const t = [...document.querySelectorAll('.mgtitle')].find((e) => e.textContent.includes('视频画质'))
    t?.closest('.mgroup')?.scrollIntoView({ block: 'start' })
  })
  await shot('view_config_quality')
} finally {
  await browser.close()
}

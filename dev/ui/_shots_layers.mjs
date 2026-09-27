// 核对「01 准备素材」的「一行一层」版式：空态 / 有素材 / 带播放器，各截一张。
// 跑法：E2E_URL=http://127.0.0.1:5173/ node dev/ui/_shots_layers.mjs
//
// 注意：会真在磁盘上建一个 draft 作品（素材要有作品才挂得住），
// 跑完请清掉：GET /api/tasks 找到新 id → DELETE /api/tasks/{id}?confirm=true
import { createRequire } from 'node:module'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:5173/'
const OUT = 'docs/_shots'
// 只用来验「播放器独占一行」，随便一段现成的音色样本即可
const MP3 = 'outputs/_trash/20260914_150417_fedcba098765/characters/voice_小七.mp3'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1200 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

async function shot(name) {
  await sleep(400)
  const el = await page.$('.step')
  await el.screenshot({ path: `${OUT}/${name}.png` })
  console.log('shot ->', `${OUT}/${name}.png`)
}

// 02 生成视频：单独截，并顺手断言「视频模型 / 工作流」那一行确实没了
async function shotStep02(name) {
  await sleep(400)
  const steps = await page.$$('.step')
  await steps[1].screenshot({ path: `${OUT}/${name}.png` })
  const probe = await page.evaluate(() => {
    const txt = document.body.innerText
    return {
      urlWrap: document.querySelectorAll('.url-wrap').length,
      hasModelRow: txt.includes('视频模型 / 工作流'),
    }
  })
  console.log('shot ->', `${OUT}/${name}.png`, '| 02 探针:', JSON.stringify(probe))
}

// 加一条素材。填了描述就不会去调文本模型设计锚点，快且不烧额度。
async function addOne(kind, name, desc) {
  await page.click('.head-actions .add-material')
  await sleep(200)
  if (kind !== 'character') {
    await page.$$eval('.addform .seg button', (els, k) => els.find((e) => e.textContent.trim() === k).click(), kind)
    await sleep(120)
  }
  await page.type('.addform input[aria-label="新素材名字"]', name)
  await page.type('.addform input[aria-label="新素材描述"]', desc)
  await page.click('.addform .btn-primary')
  await sleep(1400)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(800)
  await shot('layers_empty')
  await shotStep02('layers_step02')

  await addOne('character', '林晚', '22 岁女性，齐肩黑发，米色针织外套')
  await addOne('character', '陈默', '35 岁男性，寸头，深灰风衣')
  await addOne('场景', '便利店内部', '凌晨便利店，冷白顶灯，玻璃门有雨痕')
  await addOne('道具', '旧怀表', '黄铜外壳，表盘有划痕')

  // 选中前三个，看「本次视频素材」有内容时的样子
  await page.$$eval('.item .thumb', (els) => els.slice(0, 3).forEach((e) => e.click()))
  await sleep(300)
  await shot('layers_filled')

  // 角色音频是第 5 组：用它卡片里真实的「上传」入口喂一段 mp3，
  // 验播放器在 16:10 的缩略图框里有没有被压坏
  const inputs = await page.$$('.mgroups .mgroup:nth-child(5) .mops input[type="file"]')
  await inputs[0].uploadFile(MP3)
  await sleep(2500)
  await sleep(6000) // 等提示条自己退掉，别挡版式

  const probe = await page.evaluate(() => {
    const card = document.querySelector('.mgroups .mgroup:nth-child(5) .mcard')
    const a = card?.querySelector('audio')
    return {
      hasAudio: !!a,
      audioW: a ? Math.round(a.getBoundingClientRect().width) : 0,
      thumbW: card ? Math.round(card.querySelector('.mthumb').getBoundingClientRect().width) : 0,
    }
  })
  console.log('探针:', JSON.stringify(probe))
  await shot('layers_audio')
} finally {
  await browser.close()
}

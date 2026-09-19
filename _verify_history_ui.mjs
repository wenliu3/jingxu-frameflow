// 验证「生成历史」这个入口 + 「重生成后就地换图」这两个需求（2026-09-19 加）。
//
// 斌哥报的两件事：
//   ① 「这个人人物重新生成出来的时候，把旧的那个内容给替换，而不是刷新浏览器才看到被替换」
//      → 后端是**就地覆盖同名文件**，URL 不变，浏览器 <img> 连请求都不发。
//        修法是 URL 带 `?v=<后端每次新给的 version>`（见 api.characterImageUrl / assetImageUrl）。
//   ② 「这里增加一个按钮，就是生成历史……有一些人生成了之后看见不好，又生成，
//        发现前面那个好，但是找不了」
//      → 卡片 .mops 里加「生成历史」，弹窗列出版本、能切回某一版。
//
// ⚠️ 全程**不烧任何额度**：不点 AI 生成、不点重新生成、不碰 ComfyUI。
// 图是用真 PNG 走**上传接口**造出来的（上传本身也存历史，顺带把那条路径验了），
// 三个版本颜色不同 → 断言"当前文件到底是哪一版"时能按**字节**认，不靠位置猜。
// 会真建一个 draft 作品，跑完自己 DELETE 掉。
//
// 跑法：node _verify_history_ui.mjs            （默认打 8000，即生产包）
//       E2E_URL=http://127.0.0.1:5173/ node _verify_history_ui.mjs   （打 dev server）
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const API = process.env.E2E_API || 'http://127.0.0.1:8000'
const ROOT = 'D:/Pychrom Project/ai_video_multiagent'
const CHAR = '历史界面角色'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function check(name, cond, extra = '') {
  results.push(!!cond)
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? `\n        → ${extra}` : ''))
}

// ---------------------------------------------------------------- 造真 PNG（不依赖 Pillow / canvas）
// 三个版本给三种颜色：断言"当前文件是哪一版"时直接比字节，不靠列表位置猜。
function pngSolid(w, h, [r, g, b]) {
  const stride = w * 3 + 1
  const raw = Buffer.alloc(stride * h)
  for (let y = 0; y < h; y += 1) {
    const off = y * stride
    raw[off] = 0                                  // filter type: none
    for (let x = 0; x < w; x += 1) {
      const p = off + 1 + x * 3
      raw[p] = r; raw[p + 1] = g; raw[p + 2] = b
    }
  }
  const chunk = (tag, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(tag, 'ascii'), data])
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body) >>> 0)
    return Buffer.concat([len, body, crc])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4)
  ihdr[8] = 8; ihdr[9] = 2                        // 8bit, truecolor
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
  ])
}

const V1 = pngSolid(240, 150, [201, 74, 58])      // 朱红
const V2 = pngSolid(240, 150, [58, 132, 201])     // 蓝
const V3 = pngSolid(240, 150, [86, 168, 96])      // 绿
const bytesOf = async (url) => Buffer.from(await (await fetch(API + url)).arrayBuffer())
const jpost = (p, body) => fetch(API + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
}).then((r) => r.json())
const uploadChar = (id, i, png) => jpost(`/api/tasks/${id}/characters/${i}/upload`, {
  filename: 'v.png', data_b64: png.toString('base64'),
})
const historyOf = (id, i) =>
  fetch(`${API}/api/tasks/${id}/materials/character/${i}/history`).then((r) => r.json())

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1000 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

// 记下所有 /files 请求 —— C1 的判据是"浏览器真的重新去取了图"，
// 只看 src 字符串变了还不够（万一是浏览器压根没请求呢）。
const fileReqs = []
page.on('request', (r) => { if (r.url().includes('/files/')) fileReqs.push(r.url()) })

let createdTask = ''
page.on('response', async (res) => {
  if (res.url().endsWith('/api/tasks/draft') && res.request().method() === 'POST') {
    try { createdTask = (await res.json()).task_id || createdTask } catch { /* ignore */ }
  }
})

const clickByText = (sel, text) => page.evaluate((s, t) => {
  const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
  if (el) { el.click(); return true }
  return false
}, sel, text)

// 探针：卡片。⚠️ 必须**只看当前可见的那一份** ——
//   「新建作品」页切到工作台之后仍然挂在 DOM 里（隐藏），它的 CreateWorkbench 还是
//   进工作台**之前**那份旧数据；不按可见性过滤就会查到那份旧卡（曾因此假报"卡片没有图"）。
//   另外同一个角色名会出现两次：角色组的卡 + 「角色音频」组的卡（音色卡也用角色名），
//   所以下面还要再排掉音频组那一张（它的空态文案是"音色待生成"）。
const cardQuery = (name, what) => page.evaluate((n, w) => {
  const cards = [...document.querySelectorAll('.mcard')]
    .filter((c) => c.getBoundingClientRect().height > 0)
    .filter((c) => (c.querySelector('.mmeta strong')?.textContent || '').trim() === n)
  if (!cards.length) return { 有: false }
  // 音频组那张卡里没有 .mthumb img 也没有「生成历史」，拿它当角色卡会一直"没图"
  const card = cards.find((c) => !c.closest('.mgroup')?.querySelector('.mlabel')?.textContent.includes('音频'))
    || cards[0]
  if (w === 'probe') {
    const img = card.querySelector('.mthumb img')
    return {
      有: true,
      图src: img?.getAttribute('src') || '',
      图完整: img ? img.complete && img.naturalWidth > 0 : false,
      按钮: [...card.querySelectorAll('.mops button')].map((b) => b.textContent.trim()),
      空态: card.querySelector('.cempty')?.textContent.trim() || '',
    }
  }
  if (w === 'openHistory') {
    const b = [...card.querySelectorAll('.mops button')].find((x) => x.textContent.trim() === '生成历史')
    if (!b) return { 有: true, 点了: false }
    b.click()
    return { 有: true, 点了: true }
  }
  return { 有: true }
}, name, what)

const cardProbe = () => cardQuery(CHAR, 'probe')

// 音频组的卡（「其他音频」那张，名字叫「历史界面音频」）
const audioCardBtns = () => page.evaluate(() => {
  const card = [...document.querySelectorAll('.mcard')]
    .filter((c) => c.getBoundingClientRect().height > 0)
    .find((c) => (c.querySelector('.mmeta strong')?.textContent || '').trim() === '历史界面音频')
  return card ? [...card.querySelectorAll('.mops button')].map((b) => b.textContent.trim()) : null
})

// 探针：历史弹窗
const dialogProbe = () => page.evaluate(() => {
  const d = document.querySelector('.hd-dialog')
  if (!d) return { 有: false }
  return {
    有: true,
    标题: d.querySelector('.pd-head h3')?.textContent.trim() || '',
    说明: d.querySelector('.pd-hint')?.textContent.replace(/\s+/g, ' ').trim() || '',
    空态: d.querySelector('.hd-empty')?.textContent.replace(/\s+/g, ' ').trim() || '',
    版本数: d.querySelectorAll('.hdcard').length,
    版本: [...d.querySelectorAll('.hdcard')].map((c) => ({
      标签: c.querySelector('.hdmeta strong')?.textContent.trim() || '',
      体积: c.querySelector('.hdmeta span')?.textContent.trim() || '',
      图src: c.querySelector('img')?.getAttribute('src') || '',
      当前: c.classList.contains('on'),
      按钮: c.querySelector('.hduse')?.textContent.trim() || '',
      在用: c.querySelector('.hdon')?.textContent.trim() || '',
    })),
  }
})

// 点某一版历史卡上的「用这版」（按图 src 里的版本目录名认，不按位置）
const clickUse = (versionName) => page.evaluate((v) => {
  const card = [...document.querySelectorAll('.hd-dialog .hdcard')]
    .find((c) => (c.querySelector('img')?.getAttribute('src') || '').includes(`/${v}/`))
  if (!card) return false
  card.querySelector('.hduse').click()
  return true
}, versionName)

// 从磁盘上认：哪个历史目录里装的是哪一版（按字节比，不靠目录名猜）
const versionDirOf = (taskId, png) => {
  const root = path.join(ROOT, 'outputs', taskId, 'history', 'character', CHAR)
  if (!fs.existsSync(root)) return ''
  return fs.readdirSync(root).find((d) => {
    const f = path.join(root, d, `${CHAR}.png`)
    return fs.existsSync(f) && fs.readFileSync(f).equals(png)
  }) || ''
}
const liveFile = (taskId) => path.join(ROOT, 'outputs', taskId, 'characters', `${CHAR}.png`)

let pageUrls = []
try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(800)
  console.log('='.repeat(74))
  console.log('「生成历史」+「重生成就地换图」验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 0) 建一个空白作品（走真实的「添加素材」流程） ----------
  await page.click('.head-actions .btn-ghost')
  await sleep(300)
  await page.$$eval('.addform .af-kinds button',
    (els) => els.find((e) => e.textContent.trim() === '角色').click())
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', CHAR)
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(2500)
  check('建出测试作品并抓到 task_id', !!createdTask, createdTask || '（没抓到）')
  if (!createdTask) throw new Error('拿不到 task_id，后面没法验')

  // 再加一个音频素材：用来验「生成历史」按钮**不给音频**
  await jpost(`/api/tasks/${createdTask}/assets`, { kind: 'audio', name: '历史界面音频', design: false })

  // ---------- 1) 连传三版，造出两条历史 ----------
  // 上传是**覆盖同名文件**，所以每传一版，上一版会被后端先存进历史。
  await uploadChar(createdTask, 0, V1)
  await uploadChar(createdTask, 0, V2)
  await uploadChar(createdTask, 0, V3)
  check('磁盘上的当前图是第 3 版（绿）', fs.readFileSync(liveFile(createdTask)).equals(V3))

  const h = await historyOf(createdTask, 0)
  check('接口层：历史共 3 条（当前 + 2 版）', h.versions.length === 3,
    h.versions.map((v) => v.name || '(当前)').join(' / '))
  check('接口层：当前这版排第一、带 ?v=', h.versions[0].current && h.versions[0].thumb.includes('?v='),
    h.versions[0].thumb)
  const dirRed = versionDirOf(createdTask, V1)
  const dirBlue = versionDirOf(createdTask, V2)
  check('接口层：红（第1版）与蓝（第2版）都在历史目录里', !!dirRed && !!dirBlue,
    `红=${dirRed} 蓝=${dirBlue}`)
  const curVersion = h.versions[0].version

  // ---------- 2) 进工作台 · 素材工坊（卡片就是这里的那批） ----------
  // ⚠️ **必须刷新一次页面再进工作台。**
  //    上面那几次上传是 Node 直接打接口改的，页面并不知道；而 `openTask` 开头有一句
  //    `if (id === taskId.value && task.value) return` —— 当前作品已经是这个 draft 了，
  //    点作品卡会**直接返回、不重新拉数据**，于是工作台一直显示"加图之前"那份旧 project
  //    （表现就是卡片空态「待生成」、刚加的音频素材压根不出现）。
  //    这不是产品 bug：真实用户在界面上传图会走 refreshTask()，页面自己就是新的。
  //    是"脚本绕过界面改数据"才会撞上，所以刷新一下把前端拉回同一起点。
  await page.reload({ waitUntil: 'networkidle2' })
  await sleep(1000)
  await clickByText('.nav-item', '我的作品')
  await sleep(1200)
  const tiles = await page.$$('.project-open')
  check('作品列表里有它', tiles.length > 0, `${tiles.length} 个`)
  // 刚建的 draft 是列表里最新的那条，排在第一格
  await tiles[0].click()
  await sleep(2600)
  await clickByText('.mode-switch button', '素材工坊')
  // 等**可见的**那张角色卡出现 —— 固定 sleep 容易赶在切换动画/取数据之前
  await page.waitForFunction((n) => [...document.querySelectorAll('.mcard')]
    .some((c) => c.getBoundingClientRect().height > 0
      && (c.querySelector('.mmeta strong')?.textContent || '').trim() === n), { timeout: 15000 }, CHAR)
  await sleep(800)
  const tabNow = await page.evaluate(() => [...document.querySelectorAll('.mode-switch button')]
    .map((b) => b.textContent.trim() + (b.classList.contains('on') ? '(on)' : '')))
  check('已经在工作台的「素材工坊」tab 上', tabNow.some((t) => t.startsWith('素材工坊') && t.endsWith('(on)')),
    tabNow.join(' / '))

  const card0 = await cardProbe()
  console.log('探针 卡片:', JSON.stringify(card0))
  check('角色卡片在位且图能渲染', card0.有 && card0.图完整, JSON.stringify(card0))
  // ⚠️ 这一条就是需求①的判据：URL 不带版本号的话，重新生成后浏览器不会重新取图
  check('卡片图 URL 带 ?v=<当前版本>（cache-busting）',
    card0.图src.includes(`?v=${curVersion}`), card0.图src)
  check('卡片上有「生成历史」按钮', card0.按钮.includes('生成历史'), card0.按钮.join(' / '))

  // 音频卡片不该有这个按钮（音频没有版本一说，后端也会 422）
  const audioBtns = await audioCardBtns()
  check('音频卡片上没有「生成历史」（只给图片类四类）',
    !!audioBtns && !audioBtns.includes('生成历史'), JSON.stringify(audioBtns))

  // ---------- 3) 打开历史弹窗 ----------
  const opened = await cardQuery(CHAR, 'openHistory')
  check('点到了「生成历史」按钮', opened.点了 === true, JSON.stringify(opened))
  await page.waitForSelector('.hd-dialog', { timeout: 10000 })
  await sleep(600)
  let dlg = await dialogProbe()
  console.log('探针 弹窗:', JSON.stringify(dlg, null, 0).slice(0, 700))
  check('弹窗标题带素材名', dlg.标题.includes(CHAR), dlg.标题)
  check('说明里写清最多留 10 版', dlg.说明.includes('10'), dlg.说明.slice(0, 90))
  check('列出 3 版（当前 + 2 个历史）', dlg.版本数 === 3, `${dlg.版本数} 版`)
  check('第一条是「当前这版」且带「正在使用」',
    dlg.版本[0].当前 && dlg.版本[0].在用 === '正在使用', JSON.stringify(dlg.版本[0]))
  check('历史那两版各有「用这版」按钮',
    dlg.版本.slice(1).every((v) => v.按钮 === '用这版'), dlg.版本.slice(1).map((v) => v.按钮).join('/'))
  check('历史版本的缩略图指向 history 目录',
    dlg.版本.slice(1).every((v) => v.图src.includes('/history/character/')),
    dlg.版本[1]?.图src || '')
  check('每版都标了体积与张数', dlg.版本.every((v) => /\d/.test(v.体积)), dlg.版本[1]?.体积 || '')

  // ---------- 4) 切回第 1 版（红） ----------
  pageUrls = fileReqs.slice()
  check('能点到红那一版的「用这版」', await clickUse(dirRed), dirRed)
  await sleep(2500)

  check('磁盘上的当前图真的变回第 1 版（红）',
    fs.readFileSync(liveFile(createdTask)).equals(V1),
    `${fs.statSync(liveFile(createdTask)).size} 字节`)
  const after = await historyOf(createdTask, 0)
  check('切版后 version 换了', after.versions[0].version !== curVersion,
    `${curVersion} → ${after.versions[0].version}`)
  check('接口层：切版后历史变 4 条（被顶掉的那版也留下了）', after.versions.length === 4,
    after.versions.map((v) => v.name || '(当前)').join(' / '))

  // ⚠️ 需求①的**核心断言**：没刷新浏览器，卡片上的图就换成新的了。
  const card1 = await cardProbe()
  console.log('探针 切版后卡片:', JSON.stringify(card1))
  check('没刷新页面，卡片图 URL 自己变了（这就是需求①）',
    card1.图src.includes(`?v=${after.versions[0].version}`) && card1.图src !== card0.图src,
    `${card0.图src}\n          → ${card1.图src}`)
  // 光 URL 变了不算数 —— 浏览器得真的重新去取了那张图
  const fetched = fileReqs.slice(pageUrls.length).filter((u) => u.includes(`?v=${after.versions[0].version}`))
  check('浏览器真的重新请求了新 URL（不是只改了字符串）', fetched.length > 0,
    fetched[0] || `（切版后 /files 请求：${JSON.stringify(fileReqs.slice(pageUrls.length))}）`)
  check('新 URL 取回来的字节就是第 1 版（红）',
    (await bytesOf(card1.图src.replace(API, ''))).equals(V1))
  check('图能正常渲染（不是裂图）', card1.图完整 === true, JSON.stringify(card1))

  // ---------- 5) 弹窗自己也要刷新 ----------
  dlg = await dialogProbe()
  check('切版后弹窗列表跟着刷新（还是 4 条）', dlg.版本数 === 4, `${dlg.版本数} 版`)
  check('切完第一版变成「当前这版」',
    dlg.版本[0].当前 && dlg.版本[0].图src.includes('/files/'), JSON.stringify(dlg.版本[0]))
  check('刚被顶掉的绿那版出现在历史里（还能再切回去）',
    dlg.版本.slice(1).some((v) => v.图src.includes('/history/character/')), '')

  await page.screenshot({ path: 'docs/_shots/history_dialog.png', fullPage: true })
  console.log('\n  截图：docs/_shots/history_dialog.png')

  // ---------- 6) 关掉弹窗，卡片还在（别把页面弄坏） ----------
  await clickByText('.hd-dialog .pd-head button', '关闭')
  await sleep(500)
  check('关掉弹窗后卡片还在', (await cardProbe()).有 === true)
  check('关掉弹窗后没有残留遮罩', (await page.$('.pmask')) === null)
} finally {
  if (createdTask) {
    const del = await fetch(`${API}/api/tasks/${createdTask}?confirm=true`, { method: 'DELETE' })
      .then((r) => r.status).catch(() => 0)
    check('收尾：测试作品已删掉', del === 200, String(del))
    try {
      const t = await fetch(`${API}/api/trash`).then((r) => r.json())
      for (const e of t.entries || []) {
        if (e.task_id === createdTask) {
          await fetch(`${API}/api/trash/${encodeURIComponent(e.name)}?confirm=true`, { method: 'DELETE' })
        }
      }
    } catch { /* ignore */ }
    // 目录已进回收站（不在 outputs/ 下了），不用再动磁盘
    check('收尾：outputs/ 下没留目录',
      !fs.existsSync(path.join(ROOT, 'outputs', createdTask)))
  }
  await browser.close()
  const ok = results.filter(Boolean).length
  console.log(`\n${ok}/${results.length}`)
  process.exit(ok === results.length ? 0 : 1)
}

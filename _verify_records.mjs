// 验证「生成记录」这个入口（2026-09-17 加；2026-09-18 一天连改三次）。
//   现在是工作台第三个 tab，切过去就是这个作品出过的所有片。
//   数据源是后端磁盘产物 outputs/{id}/segments/，不是前端 segJob ——
//   后者是内存里的，刷新页面就没了，而文件其实一直在。
//
// 三次改版（脚本已跟着改）：
//   ① 按钮常驻（原来只在分镜工作台出现）
//   ② 点开不再弹窗，改成就地展开
//   ③ 最后独立成 .mode-switch 里的**第三个 tab**，容器 .records-view，
//      三个 tab 互斥（站在记录 tab 上时 01/02 都不在 DOM 里）
//   ⚠️ 找它别再按类名 .segrec —— 它现在是 tab，和其它两个同类，按文案找。
//
// 跑法：node _verify_records.mjs        （默认打 8000）
//       要打 dev server：E2E_URL=http://127.0.0.1:5173/ node _verify_records.mjs
//
// ⚠️ 全程不点「生成视频」、不点 AI 生成，**不烧任何额度**。
// 出片产物用现成的 mp4 复制过去伪造（_trash 里有 44 个），这样能验完整条链路：
// 后端扫盘 → 接口 → 按钮计数 → 面板列表 → 空态。
// 会真建一个 draft 作品，跑完自己 DELETE 掉。
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const require = createRequire('C:/Users/紊流/.workbuddy-ai/binaries/node/workspace/')
const puppeteer = require('puppeteer-core')

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const API = process.env.E2E_API || 'http://127.0.0.1:8000'
const ROOT = 'D:/Pychrom Project/ai_video_multiagent'
// 伪造出片产物用的源文件（随便两个真 mp4 就行，我们只验"能不能列出来/播起来"）
const SRC = [
  `${ROOT}/outputs/_trash/20260914_150418_a0fcfd944b0e/videos/shot_01.mp4`,
  `${ROOT}/outputs/_trash/20260914_150418_a0fcfd944b0e/videos/shot_02.mp4`,
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function check(name, cond, extra = '') {
  results.push(!!cond)
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? `\n        → ${extra}` : ''))
}

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1000 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

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

async function switchTab(label) {
  await clickByText('.mode-switch button', label)
  await sleep(1600)
}

// 探针：tab 行里有哪几个 tab、计数是多少
// ⚠️ 「生成记录」从"标题行里一颗独立按钮（.segrec）"变成了 .mode-switch 里的第三个 tab
//    （2026-09-18）。这里不再用类名找它，而是按文案找 —— 它现在和其它两个 tab 同类。
const tabProbe = () => page.evaluate(() => {
  const tabs = [...document.querySelectorAll('.mode-switch button')]
  const rec = tabs.find((b) => b.textContent.includes('生成记录'))
  return {
    tab数: tabs.length,
    标签: tabs.map((b) => b.textContent.replace(/\s+/g, ' ').trim()),
    有记录tab: !!rec,
    选中: rec ? rec.classList.contains('on') : false,
    计数: rec?.querySelector('.scount')?.textContent.trim() || '',
  }
})

// 探针：内容区里列了什么
// ⚠️ 容器从 .segrec-modal（弹窗）→ .segrec-panel（就地展开）→ .records-view（独立 tab），
//    标题一直是 .sr-head b / .sr-head p。
const panelProbe = () => page.evaluate(() => {
  const panel = document.querySelector('.records-view')
  if (!panel) return { 面板: false }
  return {
    面板: true,
    标题: panel.querySelector('.sr-head b')?.textContent.trim() || '',
    段数文案: panel.querySelector('.sr-head p')?.textContent.trim() || '',
    卡片数: panel.querySelectorAll('.segrec-item').length,
    空态: panel.querySelector('.segrec-empty')?.textContent.trim() || '',
    第一张: (() => {
      const it = panel.querySelector('.segrec-item')
      if (!it) return null
      return {
        视频源: it.querySelector('video')?.getAttribute('src') || '',
        序号: it.querySelector('.si-meta b')?.textContent.trim() || '',
        时间: it.querySelector('.si-when')?.textContent.trim() || '',
        体积: it.querySelector('.si-size')?.textContent.trim() || '',
        下载: it.querySelector('.si-dl')?.getAttribute('href') || '',
      }
    })(),
  }
})

// 三个 tab 是**互斥**的：站在记录 tab 上时，01/02 都不该在 DOM 里（反之亦然）。
const exclusiveProbe = () => page.evaluate(() => ({
  记录: !!document.querySelector('.records-view'),
  素材01: !!document.querySelector('.workbench .step'),
  生成视频02: [...document.querySelectorAll('.workbench .step-head h2')]
    .some((h) => h.textContent.includes('生成视频')),
}))

// 点某一段的「用了什么」。卡片按 mtime 倒序排，不能按位置点，所以按 video 的 src 找。
const clickWhat = (file) => page.evaluate((f) => {
  const card = [...document.querySelectorAll('.segrec-item')]
    .find((c) => (c.querySelector('video')?.getAttribute('src') || '').endsWith(f))
  if (!card) return false
  card.querySelector('.si-what').click()
  return true
}, file)

// 探针：下面那块「这一段是怎么生成的」列了什么
const detailProbe = () => page.evaluate(() => {
  const sd = document.querySelector('.records-view .sd')
  if (!sd) return { 有: false }
  return {
    有: true,
    标题: sd.querySelector('.sd-head b')?.textContent.trim() || '',
    文件名: sd.querySelector('.sd-file')?.textContent.trim() || '',
    素材: [...sd.querySelectorAll('.sd-mats li')].map((li) => ({
      名: li.querySelector('b')?.textContent.trim() || '',
      角色: li.querySelector('.sd-role')?.textContent.trim() || '',
    })),
    参数: [...sd.querySelectorAll('.sd-params > div')].map((d) =>
      `${d.querySelector('dt')?.textContent.trim()}=${d.querySelector('dd')?.textContent.trim()}`),
    中文: sd.querySelector('.sd-note')?.textContent.trim() || '',
    提示词: sd.querySelector('.sd-prompt')?.textContent.trim() || '',
    无记录: (sd.querySelector('.sd-nofound')?.textContent || '').trim(),
    // 素材行必须是整行宽的（名字撑开、角色标签顶到右端），否则两列会显得空一半
    几何: (() => {
      const ul = sd.querySelector('.sd-mats')
      const li = ul?.querySelector('li')
      if (!ul || !li) return null
      return {
        列表宽: Math.round(ul.getBoundingClientRect().width),
        行宽: Math.round(li.getBoundingClientRect().width),
        行display: getComputedStyle(li).display,
        标签距右: Math.round(li.getBoundingClientRect().right - li.querySelector('.sd-role').getBoundingClientRect().right),
      }
    })(),
  }
})

async function makeDraft(kindLabel, name) {
  await page.click('.head-actions .btn-ghost')
  await sleep(300)
  await page.$$eval('.addform .af-kinds button',
    (els, t) => els.find((e) => e.textContent.trim() === t).click(), kindLabel)
  await page.waitForSelector('.mgrid .edit-form', { timeout: 15000 })
  await page.type('.mgrid .edit-form input[aria-label="素材名字"]', name)
  await page.click('.mgrid .edit-form .btn-primary')
  await sleep(2500)
}

const segDir = () => path.join(ROOT, 'outputs', createdTask, 'segments')

try {
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(800)
  console.log('='.repeat(74))
  console.log('「生成记录」入口验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 0) 先建一个空白作品 ----------
  await makeDraft('角色', '记录验证角色')
  check('建出测试作品并抓到 task_id', !!createdTask, createdTask || '（没抓到）')
  if (!createdTask) throw new Error('拿不到 task_id，后面没法验')

  // ---------- 1) 伪造两段出片产物 ----------
  fs.mkdirSync(segDir(), { recursive: true })
  SRC.forEach((src, i) => {
    fs.copyFileSync(src, path.join(segDir(), `seg_fake${i + 1}.mp4`))
  })
  // 只给 fake1 配一份「生成记录」边车（seg_fake1.json），fake2 **故意留空** ——
  // 这样"有记录"和"老片子没记录"两条路都覆盖到了。
  // 真实边车由后端在出片时写下，形状见 _verify_segment_record.py。
  fs.writeFileSync(path.join(segDir(), 'seg_fake1.json'), JSON.stringify({
    job_id: 'fake1',
    file: 'seg_fake1.mp4',
    created: Math.floor(Date.now() / 1000),
    mode: 'flf',
    duration: 7,
    megapixels: 0.9,
    video_backend: 'comfyui',
    video_mode: 'portrait',
    note: '雨夜，女孩撑着伞走进便利店',
    prompt: 'A woman walks into a convenience store at night; slow push-in, rain on glass.',
    materials: [
      { ref: 'character:0', kind: 'character', name: '林晚', role: 'first_frame' },
      { ref: 'scene:0', kind: 'scene', name: '雨夜街道', role: 'last_frame' },
    ],
  }, null, 2), 'utf8')
  const onDisk = fs.readdirSync(segDir()).filter((f) => f.endsWith('.mp4'))
  check('已往磁盘塞进 2 段假产物', onDisk.length === 2, onDisk.join(' / '))

  // 接口层先过一遍（前端坏了也能分清是哪一层的问题）
  const apiRes = await (await fetch(`${API}/api/tasks/${createdTask}/segments`)).json()
  check('GET /segments 列出 2 段', apiRes.count === 2, JSON.stringify(apiRes.items?.map((x) => x.name)))
  check('接口给了可播放的 url',
    (apiRes.items || []).every((x) => x.url === `/files/${createdTask}/segments/${x.name}`),
    apiRes.items?.[0]?.url || '')

  // ---------- 2) 进工作台 ----------
  await clickByText('.nav-item', '我的作品')
  await sleep(1200)
  const tiles = await page.$$('.project-open')
  check('作品列表里有它', tiles.length > 0, `${tiles.length} 个`)
  await tiles[0].click()
  await sleep(2500)

  // 2026-09-18 改：它成了 .mode-switch 里的**第三个 tab**（原来是标题行里一颗独立按钮）。
  // tab 行本来就一直可见，所以"两个 tab 都要能看到它"这条自动成立。
  const tabs = await tabProbe()
  console.log('探针 tab 行:', JSON.stringify(tabs))
  check('tab 行里有三个 tab', tabs.tab数 === 3, JSON.stringify(tabs.标签))
  check('第三个是「生成记录」', (tabs.标签[2] || '').startsWith('生成记录'), tabs.标签[2])
  check('tab 上显示已生成段数 2', tabs.计数 === '2', `计数="${tabs.计数}"`)

  await switchTab('素材工坊')
  check('在素材工坊时记录 tab 依然在', (await tabProbe()).有记录tab === true)
  const studioExcl = await exclusiveProbe()
  check('素材工坊时不渲染记录内容', studioExcl.记录 === false)
  // ⚠️ 这两条是防"加分支把 01/02 弄坏"的：三个 tab 是 v-if / v-else-if / v-else 的兄弟，
  //    改条件时很容易让某一支变成空屏，而空屏从截图上看和"还没加载完"一模一样。
  check('素材工坊该渲染 01 准备素材', studioExcl.素材01 === true, JSON.stringify(studioExcl))
  await page.screenshot({ path: 'docs/_shots/segrec_tab_studio.png', fullPage: true })

  await switchTab('分镜工作台')
  check('在分镜工作台时记录 tab 依然在', (await tabProbe()).有记录tab === true)
  const boardExcl = await exclusiveProbe()
  check('分镜工作台时不渲染记录内容', boardExcl.记录 === false)
  check('分镜工作台该渲染 02 生成视频', boardExcl.生成视频02 === true, JSON.stringify(boardExcl))
  await page.screenshot({ path: 'docs/_shots/segrec_tab_board.png', fullPage: true })

  // ---------- 3) 切到记录 tab ----------
  await switchTab('生成记录')
  const panel = await panelProbe()
  console.log('探针 生成记录:', JSON.stringify(panel))
  check('切过去出现记录内容', panel.面板 === true)
  check('是页面里的一块、不是弹窗', (await page.$('.modal-mask')) === null,
    '页面上不该出现 .modal-mask')
  check('tab 进入选中态', (await tabProbe()).选中 === true)
  const excl = await exclusiveProbe()
  check('三个 tab 互斥：记录 tab 上不渲染 01/02',
    excl.素材01 === false && excl.生成视频02 === false, JSON.stringify(excl))
  check('标题是「生成记录」', panel.标题 === '生成记录', panel.标题)
  check('列出 2 段', panel.卡片数 === 2, `卡片数=${panel.卡片数}`)
  check('每张卡都有可播的 video（指向 /files/…/segments/）',
    /\/files\/[0-9a-f]{12}\/segments\/seg_fake\d\.mp4$/.test(panel.第一张?.视频源 || ''),
    panel.第一张?.视频源 || '')
  check('卡片显示生成时间', (panel.第一张?.时间 || '').length > 0, panel.第一张?.时间)
  check('卡片显示文件体积', /KB|MB/.test(panel.第一张?.体积 || ''), panel.第一张?.体积)
  check('卡片有下载链接', (panel.第一张?.下载 || '').endsWith('.mp4'), panel.第一张?.下载)
  await page.screenshot({ path: 'docs/_shots/segrec_panel.png', fullPage: true })

  // ---------- 3b) 「用了什么」：这一段是怎么生成的 ----------
  check('点到了 seg_fake1 的「用了什么」', await clickWhat('seg_fake1.mp4'))
  await sleep(800)
  const det = await detailProbe()
  console.log('探针 生成详情:', JSON.stringify(det))
  check('就地展开「这一段是怎么生成的」', det.有 === true && det.标题 === '这一段是怎么生成的', det.标题)
  check('素材列出了 2 项、名字是用户认得的',
    (det.素材 || []).map((m) => m.名).join('/') === '林晚/雨夜街道',
    JSON.stringify(det.素材))
  check('标了首帧 / 尾帧', (det.素材 || []).map((m) => m.角色).join('/') === '首帧/尾帧',
    JSON.stringify((det.素材 || []).map((m) => m.角色)))
  // 靠肉眼在缩略图上看这条会看错（我第一遍就以为没顶到右边），所以用几何量卡死
  check('素材行是整行宽的、角色标签顶到右端',
    det.几何?.行display === 'flex' && det.几何.行宽 === det.几何.列表宽 && det.几何.标签距右 === 0,
    JSON.stringify(det.几何))
  check('参数里有模式 / 时长 / 清晰度 / 后端',
    ['模式=FLF · 首尾帧', '生成时长=7 秒', '清晰度=0.9 MP · 约 1280×736（720P）', '视频后端=ComfyUI · portrait']
      .every((x) => (det.参数 || []).includes(x)),
    JSON.stringify(det.参数))
  check('带出了用户当时写的中文描述', (det.中文 || '').includes('雨夜，女孩撑着伞走进便利店'), det.中文)
  check('提示词原文在', (det.提示词 || '').startsWith('A woman walks into a convenience store'), (det.提示词 || '').slice(0, 40))
  await page.screenshot({ path: 'docs/_shots/segrec_detail.png', fullPage: true })

  // 再点一次收起；然后点 fake2 —— 它没有边车，必须走"没留下记录"那条说明
  await clickWhat('seg_fake1.mp4')
  await sleep(500)
  check('再点一次收起详情', (await detailProbe()).有 === false)
  await clickWhat('seg_fake2.mp4')
  await sleep(800)
  const legacy = await detailProbe()
  console.log('探针 老片子详情:', JSON.stringify(legacy))
  check('没有边车的老片子给的是说明文案，不是空字段',
    (legacy.无记录 || '').includes('没有留下生成记录'), legacy.无记录)

  // 卡片本身（点视频以外的地方）也能开详情 —— 但点视频是在播放，不能抢
  await clickWhat('seg_fake2.mp4')     // 先收起来
  await sleep(400)
  await page.evaluate(() => document.querySelector('.segrec-item .si-when').click())
  await sleep(700)
  check('点卡片（视频以外）也能开详情', (await detailProbe()).有 === true)

  // 切走再切回来：详情必须被带走（否则会先闪出上一次那一段的内容，像加载错了）
  await switchTab('分镜工作台')
  check('切走后记录内容消失', (await page.$('.records-view')) === null)
  await switchTab('生成记录')
  check('切回来记录内容还在', (await page.$('.records-view')) !== null)
  check('切回来不残留上一次的详情', (await detailProbe()).有 === false)

  // 头部那颗「刷新」：重新扫一遍磁盘
  await clickByText('.records-view .sr-head button', '刷新')
  await sleep(800)
  check('「刷新」后仍然是 2 段', (await panelProbe()).卡片数 === 2)

  // ---------- 4) 空态 ----------
  // ⚠️ 2026-09-18 起 watcher 只在**切到这个 tab** 时重拉（两个 tab 共用同一批数据），
  //    所以这里靠"切走再切回来"触发重读 —— 点「刷新」也一样。
  fs.rmSync(segDir(), { recursive: true, force: true })
  await switchTab('分镜工作台')
  await switchTab('生成记录')
  const afterClear = await tabProbe()
  check('产物清掉后 tab 上不再显示计数', afterClear.计数 === '', JSON.stringify(afterClear))
  const empty = await panelProbe()
  console.log('探针 空态:', JSON.stringify(empty))
  check('没生成过时给空态文案', (empty.空态 || '').includes('还没有生成过视频'), empty.空态)
  await page.screenshot({ path: 'docs/_shots/segrec_empty.png', fullPage: true })
} catch (err) {
  check('验证脚本执行完成', false, err.message)
} finally {
  await browser.close()
  if (createdTask) {
    try {
      // 顺手把伪造的产物清掉，再删作品
      if (fs.existsSync(segDir())) fs.rmSync(segDir(), { recursive: true, force: true })
      const r = await fetch(`${API}/api/tasks/${createdTask}?confirm=true&purge=true`, { method: 'DELETE' })
      console.log(`\n清理测试作品 ${createdTask} -> ${r.status}`)
    } catch (e) {
      console.log(`\n⚠️ 清理失败：${e.message}`)
    }
  } else {
    console.log('\n⚠️ 没抓到 task_id，请手动检查有没有残留 draft')
  }
  const bad = results.filter((x) => !x).length
  console.log('='.repeat(74))
  console.log(`结果：${results.length - bad}/${results.length} 通过` + (bad ? '  ← 有 FAIL' : ''))
  console.log('='.repeat(74))
  process.exit(bad ? 1 : 0)
}

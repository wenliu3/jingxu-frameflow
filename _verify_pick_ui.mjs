// 验证「素材勾选」这件事现在长什么样（2026-09-19，改了两轮）。
//
// 斌哥第一句：「我点击对应图片的下面那个地方，会选上并出现 icon 图标，这个没有用的，不要这个功能」
//   → 病根是整张 .mcard 挂着 @click=togglePick，图片以外点哪儿都算选中。撤掉卡片级点击。
// 斌哥第二句：「不用选这个，因为选了也不知道能有什么功能，所以不要这个」
//   → 图片右上角那个小圆圈也撤掉（他图上用红框圈了它）。
//   查下来他说得对：01（素材工坊/新建作品）与「分镜工作台」的 02 是同一个组件的两个实例，
//   picked 是组件内部状态 —— 在 01 里选完、切到分镜工作台就丢了。选完看不见结果还会被丢，
//   这种开关留着只会误导。
//
// 定稿的规矩：
//   01 的卡片   ：点图片=看大图；点按钮=上传/编辑/删除/生成历史/AI 生成；其它地方**都不响应**
//                 卡片上**没有**任何选中开关
//   02 的素材条 ：**全应用唯一的勾选处**，选完立刻在上面的「本次视频素材」看见效果
//
// ⚠️ 全程**不烧任何额度**：不点 AI 生成、不点重新生成、不碰 ComfyUI。
// 图是用真 PNG 走**上传接口**造的（上传本身也顺手验了）。
// 真建一个 draft 作品，跑完自己 DELETE 掉。
//
// 跑法：node _verify_pick_ui.mjs                       （默认打 8000，即正式包）
//       E2E_URL=http://127.0.0.1:5173/ node _verify_pick_ui.mjs   （打 dev server）
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import zlib from 'node:zlib'

const require = createRequire(import.meta.url)
const puppeteer = require(
  path.join(os.homedir(), '.workbuddy-ai/binaries/node/workspace/node_modules/puppeteer-core'),
)

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const URL = process.env.E2E_URL || 'http://127.0.0.1:8000/'
const API = process.env.E2E_API || 'http://127.0.0.1:8000'
const ROOT = 'D:/Pychrom Project/ai_video_multiagent'
const CHAR = '勾选验证角色'
const SCENE = '勾选验证场景'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function check(name, cond, extra = '') {
  results.push(!!cond)
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? `\n        → ${extra}` : ''))
}

// ---------------------------------------------------------------- 造真 PNG（不依赖 Pillow / canvas）
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

const jpost = (p, body) => fetch(API + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
}).then((r) => r.json())

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--disable-gpu', '--no-sandbox'],
  defaultViewport: { width: 1440, height: 1000 },
})
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()) })

const clickByText = (sel, text) => page.evaluate((s, t) => {
  const el = [...document.querySelectorAll(s)].find((e) => e.textContent.includes(t))
  if (el) { el.click(); return true }
  return false
}, sel, text)

// ---------------------------------------------------------------- 探针：01 的卡片
// ⚠️ 同一屏里「角色」和「角色音频」两组各有一张同名卡（音色卡也用角色名），
//    按「组标题」再分一次；没有 .mthumb 的那张是音色卡。
const cardOf = (name, groupWord = '') => page.evaluate((n, g) => {
  const cards = [...document.querySelectorAll('.mcard')]
    .filter((c) => c.getBoundingClientRect().height > 0)
    .filter((c) => (c.querySelector('.mmeta strong')?.textContent || '').trim() === n)
  const card = g
    ? cards.find((c) => (c.closest('.mgroup')?.querySelector('.mlabel')?.textContent || '').includes(g))
    : cards.find((c) => !!c.querySelector('.mthumb'))
  if (!card) return { 有: false }
  const meta = card.querySelector('.mmeta')
  const thumb = card.querySelector('.mthumb')
  const mr = meta?.getBoundingClientRect()
  const tr = thumb?.getBoundingClientRect()
  return {
    有: true,
    有选中开关: !!card.querySelector('.mcheck'),
    picked类: card.classList.contains('picked'),
    图src: card.querySelector('.mthumb img')?.getAttribute('src') || '',
    meta点: mr ? { x: mr.left + mr.width / 2, y: mr.top + mr.height / 2 } : null,
    thumb点: tr ? { x: tr.left + tr.width / 2, y: tr.top + tr.height / 2 } : null,
  }
}, name, groupWord)

const opsBtnPoint = (name, label) => page.evaluate((n, t) => {
  const card = [...document.querySelectorAll('.mcard')]
    .filter((c) => c.getBoundingClientRect().height > 0)
    .filter((c) => (c.querySelector('.mmeta strong')?.textContent || '').trim() === n)
    .find((c) => !!c.querySelector('.mthumb'))
  if (!card) return null
  const btn = [...card.querySelectorAll('.mops button')].find((b) => b.textContent.trim() === t)
  if (!btn) return null
  const r = btn.getBoundingClientRect()
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
}, name, label)

const switchCount = () => page.evaluate(() => document.querySelectorAll('.mcheck').length)

// ---------------------------------------------------------------- 探针：02 的素材条
const pickProbe = () => page.evaluate(() => ({
  有素材条: !!document.querySelector('.picklist'),
  项: [...document.querySelectorAll('.picklist .pl-item')].map((b) => ({
    名: (b.querySelector('em')?.textContent || '').trim(),
    选中: b.classList.contains('on'),
  })),
  已选: [...document.querySelectorAll('.picked-panel .picked-card')]
    .map((c) => (c.querySelector('.picked-meta strong')?.textContent || '').trim()),
  计数: (document.querySelector('.picked-panel .pcount')?.textContent || '').replace(/\s+/g, ' ').trim(),
  空态: (document.querySelector('.picked-panel .picked-empty')?.textContent || '').replace(/\s+/g, ' ').trim(),
}))

const clickPickItem = (name) => page.evaluate((n) => {
  const b = [...document.querySelectorAll('.picklist .pl-item')]
    .find((x) => (x.querySelector('em')?.textContent || '').trim() === n)
  if (!b) return false
  b.click()
  return true
}, name)

const dialogProbe = () => page.evaluate(() => {
  const d = document.querySelector('.pdialog')
  return d ? { 开着: true, 图: d.querySelector('.pv-img')?.getAttribute('src') || '' } : { 开着: false }
})

let createdTask = ''
try {
  console.log('='.repeat(74))
  console.log('「素材怎么勾选」界面验证   ' + URL)
  console.log('='.repeat(74) + '\n')

  // ---------- 0) 造作品：一个角色 + 一个场景（走接口，省得依赖表单） ----------
  createdTask = (await jpost('/api/tasks/draft', { title: '验证_勾选' })).task_id
  check('建出测试作品', !!createdTask, createdTask || '（失败）')
  if (!createdTask) throw new Error('拿不到 task_id，后面没法验')
  // 工作流决定"角色进模型的是哪张图"（Ref2VA＝四视图设定图 / I2V＝正面定妆照），
  // 下面的断言要按它分叉。
  const wfCfg = await fetch(`${API}/api/config`).then((r) => r.json())
  const isR2v = String(wfCfg.video_workflow || '') === 'ref2va'
  await jpost(`/api/tasks/${createdTask}/characters`, { name: CHAR, design: false })
  await jpost(`/api/tasks/${createdTask}/assets`, { kind: 'scene', name: SCENE, design: false })
  const png = pngSolid(240, 150, [201, 74, 58])
  await jpost(`/api/tasks/${createdTask}/characters/0/upload`, {
    filename: 'v.png', data_b64: png.toString('base64'),
  })
  // 再给这个角色造一张「四视图设定图」：手搓一个历史版本、切上去，它就会落到 sheet 字段。
  // 卡片/预览显示的是这张（蓝），而出片喂给模型的是单张定妆照（红）—— 两个不同文件，
  // 下面那条"已选素材要显示会进模型的那张"才验得出来。
  const sheetVer = '20260101_000000_abcdef'
  const sheetDir = path.join(ROOT, 'outputs', createdTask, 'history', 'character', CHAR, sheetVer)
  fs.mkdirSync(sheetDir, { recursive: true })
  fs.writeFileSync(path.join(sheetDir, `${CHAR}.png`), png)
  fs.writeFileSync(path.join(sheetDir, `${CHAR}_sheet.png`), pngSolid(240, 150, [58, 132, 201]))
  await jpost(`/api/tasks/${createdTask}/materials/character/0/history/use`, { name: sheetVer })

  // ---------- 1) 进工作台 · 素材工坊（01） ----------
  await page.goto(URL, { waitUntil: 'networkidle2' })
  await sleep(800)
  await clickByText('.nav-item', '我的作品')
  await sleep(1200)
  const tiles = await page.$$('.project-open')
  check('作品列表里有它', tiles.length > 0, `${tiles.length} 个`)
  await tiles[0].click()                       // 刚建的 draft 是最新那条，排第一格
  await sleep(2600)
  await clickByText('.mode-switch button', '素材工坊')
  await page.waitForFunction((n) => [...document.querySelectorAll('.mcard')]
    .some((c) => c.getBoundingClientRect().height > 0
      && (c.querySelector('.mmeta strong')?.textContent || '').trim() === n), { timeout: 15000 }, CHAR)
  await sleep(800)

  // ---------- 2) 01 的卡片：没有选中开关 ----------
  let c = await cardOf(CHAR)
  check('角色卡片在位、图能渲染', c.有 === true && c.图src.includes('/files/'), JSON.stringify(c))
  check('01 卡片显示的是四视图设定图（_sheet.png）', c.图src.includes('_sheet.png'), c.图src)
  check('卡片上**没有**选中开关（小圆圈已撤）', c.有选中开关 === false && (await switchCount()) === 0)

  // ---------- 3) 点「图片下面那块」→ 什么都不该发生（斌哥第一句） ----------
  await page.mouse.click(c.meta点.x, c.meta点.y)
  await sleep(400)
  let c2 = await cardOf(CHAR)
  check('点图片下面那块：没有被选中（没有 ✓、没有 picked 描边）',
    c2.picked类 === false && c2.有选中开关 === false && (await switchCount()) === 0,
    JSON.stringify({ picked类: c2.picked类, 开关数: await switchCount() }))

  // 场景卡（没图）同样不该被点选中
  const s = await cardOf(SCENE)
  check('场景卡片在位', s.有 === true, JSON.stringify(s))
  if (s.有 && s.meta点) {
    await page.mouse.click(s.meta点.x, s.meta点.y)
    await sleep(400)
    check('点场景卡「图片下面那块」也没有任何反应', (await switchCount()) === 0)
  }

  // ---------- 4) 点「编辑」→ 表单开，不牵扯选中 ----------
  const editPt = await opsBtnPoint(CHAR, '编辑')
  check('卡片上有「编辑」按钮', !!editPt, JSON.stringify(editPt))
  if (editPt) {
    await page.mouse.click(editPt.x, editPt.y)
    await sleep(500)
    const editing = await page.evaluate(() => [...document.querySelectorAll('.mgrid .edit-form')]
      .some((e) => e.getBoundingClientRect().height > 0))
    check('点「编辑」：表单开了（按钮照常工作）', editing === true)
    await clickByText('.mgrid .edit-form button', '取消')
    await sleep(400)
  }

  // ---------- 5) 点图片 → 看大图（01 唯一的图片交互） ----------
  const c3 = await cardOf(CHAR)
  await page.mouse.click(c3.thumb点.x, c3.thumb点.y)
  await sleep(600)
  const dlg = await dialogProbe()
  check('点图片：预览弹窗打开了', dlg.开着 === true, JSON.stringify(dlg))
  check('预览里就是这张图', dlg.图.includes('/files/'), dlg.图)
  await clickByText('.pdialog .pd-head button', '关闭')
  await sleep(400)
  check('关掉预览后没有残留遮罩', (await page.$('.pmask')) === null)

  // ---------- 6) 切到「分镜工作台」：勾选只在这里做 ----------
  await clickByText('.mode-switch button', '分镜工作台')
  await page.waitForSelector('.picklist .pl-item', { timeout: 15000 })
  await sleep(600)
  let p = await pickProbe()
  check('02 有素材勾选条', p.有素材条 === true)
  check('勾选条里有角色和场景', p.项.some((x) => x.名 === CHAR) && p.项.some((x) => x.名 === SCENE),
    JSON.stringify(p.项))
  check('音色不在勾选条里（跟着角色自动带上）', !p.项.some((x) => x.名.includes('音') || x.名.includes('声')),
    JSON.stringify(p.项.map((x) => x.名)))
  check('一开始一个都没选，空态在提示去哪儿点', p.已选.length === 0 && p.空态.includes('点一下'), p.空态)

  check('点一下勾选条里那张角色卡', await clickPickItem(CHAR) === true)
  await sleep(500)
  p = await pickProbe()
  check('选完之后：该项高亮', p.项.find((x) => x.名 === CHAR)?.选中 === true, JSON.stringify(p.项))
  check('选完之后：「本次视频素材」里出现它（这就是"选了有什么用"的答案）',
    p.已选.includes(CHAR), JSON.stringify(p.已选))
  // 角色有两张图：卡片上是设定图，进模型的是单张定妆照。已选那一侧必须显示后者，
  // 否则用户在 ComfyUI 里看到另一张会以为传错了（2026-09-19 斌哥就是这么问的）。
  const pickImg = await page.evaluate(() =>
    document.querySelector('.picked-panel .picked-card img')?.getAttribute('src') || '')
  // ⚠️ 地址里的中文名**不做 URL 编码**（characterImageUrl 直接把文件名拼进去，浏览器自己会编），
  //    所以这里按原样比，别手痒套 encodeURIComponent。
  // 角色进模型的是哪张，跟工作流绑死：Ref2VA＝四视图设定图，I2V＝正面定妆照。
  if (isR2v) {
    check('Ref2VA：已选素材显示的是**会进模型的那张**（四视图设定图）',
      pickImg.includes('_sheet.png'), pickImg)
  } else {
    check('I2V：已选素材显示的是**会进模型的那张**（正面定妆照，不是设定图）',
      pickImg.includes(`${CHAR}.png`) && !pickImg.includes('_sheet.png'), pickImg)
  }
  check('选完之后：计数跟着走', p.计数.includes('1 项'), p.计数)

  check('再点一下取消', await clickPickItem(CHAR) === true)
  await sleep(500)
  p = await pickProbe()
  check('取消之后：从「本次视频素材」里消失', p.已选.length === 0 && p.项.every((x) => !x.选中),
    JSON.stringify(p.已选))

  // 02 那个「×」也要能移出（选上再点 ×）
  await clickPickItem(CHAR)
  await sleep(400)
  await page.evaluate(() => document.querySelector('.picked-panel .picked-card .unpick')?.click())
  await sleep(500)
  p = await pickProbe()
  check('已选卡上的「×」能移出', p.已选.length === 0, JSON.stringify(p.已选))

  // 02 抬头那个工作流标签：多选 / 首尾帧这些规矩全由它决定，得摆在明面上
  const chip = await page.evaluate(() => (document.querySelector('.wf-chip')?.textContent || '').trim())
  if (isR2v) {
    check('02 抬头标着「Ref2VA · 全能参考」', chip.includes('Ref2VA'), chip || '（没找到标签）')
  } else {
    check('02 抬头标着「I2V · 首帧 / 首尾帧」', chip.includes('I2V'), chip || '（没找到标签）')
  }

  // ---------- 6b) 能选几张图，也要看工作流的脸色 ----------
  // Ref2VA 有 ref_image_0/1/2 三个参考口，可以多选；I2V 只有一个 first_frame 口，
  // 多选出来的图进不了模型 —— 所以 I2V 下勾第二张要把上一张顶掉（并有一句提示说明）。
  const plHead = await page.evaluate(() =>
    (document.querySelector('.picklist .pl-head')?.textContent || '').replace(/\s+/g, ' ').trim())
  if (isR2v) {
    check('勾选条提示写明了"最多 3 张图"', plHead.includes('3 张'), plHead)
  } else {
    check('勾选条提示写明了"I2V 只吃一张、再勾会替掉"', plHead.includes('只吃一张'), plHead)
  }
  await clickPickItem(CHAR)
  await sleep(300)
  await clickPickItem(SCENE)
  await sleep(500)
  p = await pickProbe()
  if (isR2v) {
    check('Ref2VA：两个图片素材能同时选中（多图参考）',
      p.已选.includes(CHAR) && p.已选.includes(SCENE), JSON.stringify(p.已选))
  } else {
    check('I2V：勾第二张会顶掉第一张（只吃一张图）',
      !p.已选.includes(CHAR) && p.已选.includes(SCENE), JSON.stringify(p.已选))
  }
  // 收尾：清空已选，后面的断言从干净状态开始
  await clickPickItem(SCENE)
  await sleep(300)

  // ---------- 7) 「首尾帧」这一行已经整行摘掉（2026-09-19 斌哥定） ----------
  // 理由：首尾帧只对 I2V / fl2va 成立，Ref2VA 那份工作流的节点没有尾帧口 —— 他固定用 Ref2VA，
  // 这一行对他永远是死的。现在**两种工作流下都不显示**（组件里代码没删，注释写了怎么加回来；
  // "第一张图当首帧"那条信息挪到勾选条的提示里了）。
  const frameRow = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.srow')]
    const row = rows.find((r) => (r.querySelector('.slabel')?.textContent || '').trim() === '首尾帧')
    return { 有: !!row, 控件: row ? !!row.querySelector('.flfbtn') : false }
  })
  check('02 里不再有「首尾帧」这一行', frameRow.有 === false, JSON.stringify(frameRow))

  // ---------- 8) 服务配置面板：工作流能选，配套的 LoRA/步数要跟着换 ----------
  // 2026-09-19 的现实教训：面板里只有 LoRA、没有"工作流"这一项，斌哥就以为"选了 8 步 LoRA
  // = 选了 i2v"，其实工作流还是 Ref2VA —— 于是 02 里照样多选、照样没有首尾帧，像 bug。
  await page.click('.settings-button')
  await page.waitForSelector('.modal select', { timeout: 10000 })
  await sleep(600)
  const readPanel = () => page.evaluate(() => {
    const sels = [...document.querySelectorAll('.modal select')]
    const wf = sels.find((s) => [...s.options].some((o) => o.value === 'i2v')
      && [...s.options].some((o) => o.value === 'ref2va'))
    const lora = sels.find((s) => [...s.options].some((o) => String(o.value).includes('ref2v_turbo')))
    const steps = sels.find((s) => [...s.options].some((o) => o.value === '4')
      && [...s.options].some((o) => o.value === '12'))
    return {
      wf: wf ? wf.value : '(没有工作流选择)',
      lora: lora ? lora.value : '',
      steps: steps ? steps.value : '',
    }
  })
  const setWf = (want) => page.evaluate((v) => {
    const sels = [...document.querySelectorAll('.modal select')]
    const wf = sels.find((s) => [...s.options].some((o) => o.value === 'i2v')
      && [...s.options].some((o) => o.value === 'ref2va'))
    if (!wf) return false
    wf.value = v
    wf.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  }, want)

  const panel0 = await readPanel()
  check('面板里有「视频工作流」选择', panel0.wf === 'ref2va' || panel0.wf === 'i2v', JSON.stringify(panel0))
  await setWf('i2v')
  await sleep(400)
  const panelI2v = await readPanel()
  check('切到 I2V：LoRA 自动换成 fl2v 那份、步数变 8',
    panelI2v.lora.includes('fl2v_turbo_8step') && panelI2v.steps === '8', JSON.stringify(panelI2v))
  await setWf('ref2va')
  await sleep(400)
  const panelR2v = await readPanel()
  check('切回 Ref2VA：LoRA 自动换成 Ref2V 那份、步数变 4',
    panelR2v.lora.includes('ref2v_turbo_4step') && panelR2v.steps === '4', JSON.stringify(panelR2v))
  // 反方向：LoRA → 工作流。斌哥就是从这个下拉表达"我要 i2v"的（选项上写着"（I2V / 首尾帧 专用）"），
  // 只换 LoRA 不换工作流，就是他报的"配置选了 i2v、界面还是 ref2va"。
  const setLora = (want) => page.evaluate((v) => {
    const sels = [...document.querySelectorAll('.modal select')]
    const lora = sels.find((s) => [...s.options].some((o) => String(o.value).includes('ref2v_turbo')))
    if (!lora) return false
    lora.value = v
    lora.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  }, want)
  await setLora('minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors')
  await sleep(400)
  const panelL2 = await readPanel()
  check('选 Ref2V 那份 LoRA：工作流自动切到 ref2va、步数 4',
    panelL2.wf === 'ref2va' && panelL2.steps === '4', JSON.stringify(panelL2))
  await setLora('minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors')
  await sleep(400)
  const panelL3 = await readPanel()
  check('选 fl2v 那份 LoRA：工作流自动切回 i2v、步数 8',
    panelL3.wf === 'i2v' && panelL3.steps === '8', JSON.stringify(panelL3))
  // 步数与 LoRA 蒸散步数对不上时要有提醒（不拦，只说清代价）
  const setSteps = (want) => page.evaluate((v) => {
    const sels = [...document.querySelectorAll('.modal select')]
    const s = sels.find((x) => [...x.options].some((o) => o.value === '4')
      && [...x.options].some((o) => o.value === '12'))
    if (!s) return false
    s.value = v
    s.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  }, want)
  // 先选 4 步蒸馏的那份 Ref2V LoRA（步数会被带成 4），再手动把步数拨到 8 → 应该出现提醒
  await setLora('minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors')
  await sleep(300)
  await setSteps('8')
  await sleep(300)
  const warnText = await page.evaluate(() => [...document.querySelectorAll('.modal .chint.warn')]
    .map((p) => p.textContent.replace(/\s+/g, ' ').trim()).join(' | '))
  check('步数与 LoRA 不配套时有提醒（4 步蒸馏的 LoRA 填了 8 步）',
    warnText.includes('步蒸馏'), warnText.slice(0, 120))

  // 还原成刚打开面板时的样子（先工作流、再 LoRA —— 两个都是双向绑定，顺序反了会互相带）
  await setWf(panel0.wf === 'i2v' || panel0.wf === 'ref2va' ? panel0.wf : 'i2v')
  await setLora(panel0.lora)

  // 关掉面板（**别点保存**——那会把测试期间的状态写进服务配置）
  await clickByText('.modal .mhead button', '关闭')
  await sleep(400)

  await page.screenshot({ path: 'docs/_shots/pick_switch.png', fullPage: true })
  console.log('\n  截图：docs/_shots/pick_switch.png')
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
    check('收尾：outputs/ 下没留目录',
      !fs.existsSync(path.join(ROOT, 'outputs', createdTask)))
  }
  await browser.close()
  const ok = results.filter(Boolean).length
  console.log(`\n${ok}/${results.length}`)
  process.exit(ok === results.length ? 0 : 1)
}

// Public Muse UI only. A dedicated profile keeps the user's regular Chrome untouched.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import readline from 'node:readline';
import crypto from 'node:crypto';
import os from 'node:os';
import { mediaLinks as readMediaLinks, resultArtifact as findResultArtifact, resultDownload as findResultDownload, replyDownload, taskPosted } from './muse_dom.mjs';

const require = createRequire(new URL('../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function bounded(promise, ms) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Muse 视频下载超时')), ms);
    })]);
  } finally { clearTimeout(timer); }
}
const COMPOSER = 'textarea, [contenteditable="true"][role="textbox"], [contenteditable="true"][data-lexical-editor="true"]';

export async function composer(page) {
  const inputs = page.locator(COMPOSER);
  const available = [];
  for (let i = 0; i < await inputs.count(); i++) {
    const input = inputs.nth(i);
    if (await input.isVisible() && await input.isEditable()) available.push(input);
  }
  if (available.length !== 1) throw new Error('尚未找到唯一的 Muse 消息输入框。请完成登录，进入专用旁聊后连接。');
  return available[0];
}

export const mediaLinks = readMediaLinks;
export async function resultArtifact(page, taskId) {
  return findResultArtifact(page, taskId, crypto.randomUUID());
}
export async function resultDownload(page, artifact, taskId) {
  return findResultDownload(page, artifact, taskId, crypto.randomUUID());
}

export function generationMessage(payload, taskId) {
  return [
    `镜序画布独立视频任务 ${taskId}。请直接生成一条视频，无需再次询问是否开始。`,
    (payload.videos?.length
      ? `仅按本条消息和随附的 ${payload.images.length} 张图片、${payload.videos.length} 个视频附件创作，不沿用其他任务的剧情。先上传图片，再上传视频；各自顺序对应下方参考图、参考视频编号。必须读取视频附件的画面和动作，不能只看缩略图。`
      : `仅按本条消息和随附的 ${payload.images.length} 张图片创作，不沿用其他任务的剧情。图片顺序对应下方参考图编号。`),
    `计划时长 ${payload.duration} 秒；如能力不支持该时长，请明确说明，不要改成图片或网页动画。`,
    payload.ratio && payload.ratio !== 'auto' ? `输出画面比例要求：${payload.ratio}。请选择相应视频生成规格；如不支持，请明确说明实际比例，不要通过拉伸变形满足要求。` : '',
    payload.resolution && payload.resolution !== 'custom' ? `视频清晰度要求：${payload.resolution.toUpperCase()}。请选择相应生成清晰度；如不支持，请明确说明实际规格，不要将放大处理冒充原生生成清晰度。` : '',
    payload.prompt,
    payload.generate_audio ? (payload.audio ? `音效要求：${payload.audio}` : '按镜头内容生成合适音效。') : '生成无音轨视频。',
    `请返回可下载的 MP4 文件，文件名必须包含 ${taskId}（建议 ${taskId}.mp4），方便镜序准确识别本次结果。`,
  ].filter(Boolean).join('\n\n');
}

const draftText = (text) => String(text || '').replace(/\r\n/g, '\n').trim();
const inputText = (input) => input.evaluate((el) => el.value ?? el.textContent);

export async function requestKey(payload) {
  const images = [];
  for (const filename of payload.images) {
    images.push([path.resolve(filename), crypto.createHash('sha256').update(await fs.readFile(filename)).digest('hex')]);
  }
  const settings = { images, prompt: payload.prompt,
    duration: Number(payload.duration), generate_audio: !!payload.generate_audio,
    audio: payload.generate_audio ? payload.audio || '' : '' };
  // Keep existing default/image-only journals resumable after this upgrade.
  if (payload.videos?.length) settings.videos = await Promise.all(payload.videos.map(async filename =>
    [path.resolve(filename), crypto.createHash('sha256').update(await fs.readFile(filename)).digest('hex')]));
  if (payload.ratio && payload.ratio !== 'auto') settings.ratio = payload.ratio;
  if (payload.resolution && payload.resolution !== 'custom') settings.resolution = payload.resolution;
  return crypto.createHash('sha256').update(JSON.stringify(settings)).digest('hex');
}

// Muse's icon-only arrow has no accessible name on some layouts. Find it within
// the composer's own controls; Enter may insert a newline and is never a fallback.
export async function sendButton(page, input) {
  const marker = crypto.randomUUID();
  const found = await input.evaluate((el, id) => {
    const visible = (node) => {
      const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    for (let region = el.parentElement, depth = 0; region && region !== document.body && depth < 8; region = region.parentElement, depth++) {
      if (region.querySelectorAll('textarea, [contenteditable="true"]').length !== 1) continue;
      const buttons = [...region.querySelectorAll('button, [role="button"]')].filter(visible);
      if (!buttons.length) continue;
      const named = buttons.filter((button) => /^(发送(?:消息)?|提交|send(?:\s+(?:a\s+)?(?:message|prompt))?|submit)(?:\s|[（(:]|$)/i.test(
        [button.getAttribute('aria-label'), button.getAttribute('title'), button.getAttribute('data-tooltip-content'), button.textContent].filter(Boolean).join(' ').trim()));
      const submit = buttons.filter((button) => button.matches('button[type="submit"]'));
      const box = region.getBoundingClientRect();
      const arrows = buttons.filter((button) => {
        const rect = button.getBoundingClientRect();
        const label = [button.getAttribute('aria-label'), button.getAttribute('title'), button.textContent].filter(Boolean).join('').trim();
        return !label && button.querySelector('svg') && rect.width <= 72 && rect.height <= 72
          && rect.x + rect.width / 2 > box.x + box.width * 0.75
          && rect.y + rect.height / 2 > box.y + box.height * 0.5;
      });
      const candidates = named.length ? named : submit.length ? submit : arrows;
      if (candidates.length > 1) return 'ambiguous';
      if (candidates.length === 1) {
        const button = candidates[0];
        if (button.disabled || button.getAttribute('aria-disabled') === 'true') return 'disabled';
        button.setAttribute('data-frameflow-send', id);
        return 'found';
      }
    }
    return 'missing';
  }, marker);
  if (found !== 'found') return { state: found };
  return { state: found, button: page.locator(`[data-frameflow-send="${marker}"]`) };
}

async function journalFiles(directory) {
  const files = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true }).catch(() => [])) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory() && !entry.name.startsWith('.')) files.push(...await journalFiles(filename));
    else if (entry.isFile() && entry.name.endsWith('.muse.json')) files.push(filename);
  }
  return files;
}

async function journal(filename, data) {
  const temporary = `${filename}.${crypto.randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporary, JSON.stringify(data, null, 2), { encoding: 'utf8', flag: 'wx' });
    await fs.rename(temporary, filename);
  } finally {
    await fs.unlink(temporary).catch(() => {});
  }
}

export class MuseBrowser {
  constructor(root, downloadDirectory = path.join(os.homedir(), 'Downloads', 'Muse')) {
    this.root = path.resolve(root);
    this.context = null;
    this.page = null;
    this.binding = '';
    this.busy = false;
    this.starting = null;
    this.phase = '';
    this.taskId = '';
    this.downloadDirectory = path.resolve(downloadDirectory);
    this.downloadsPending = new Set();
    this.downloadPages = new WeakSet();
    this.capturedDownloads = new WeakSet();
    this.lastDownload = '';
    this.downloadError = '';
    this.browserEngine = '';
    this.closing = false;
    this.headless = true;
    this.viewPage = null;
    this.panelQueue = Promise.resolve();
    this.panelPending = 0;
    this.frames = new Map();
  }

  async diagnostic(event) {
    const directory = path.join(this.root, '.local');
    try {
      await fs.mkdir(directory, { recursive: true });
      await fs.appendFile(path.join(directory, 'muse-browser-events.log'),
        JSON.stringify({ time: new Date().toISOString(), event, engine: this.browserEngine,
          phase: this.phase, task_id: this.taskId }) + '\n');
    } catch { /* Diagnostics must not interrupt a transfer. */ }
  }

  watchDownloads(page) {
    if (this.downloadPages.has(page)) return;
    this.downloadPages.add(page);
    page.on('download', (file) => {
      // Capture listeners run synchronously in this event. Defer the fallback
      // until they have identified downloads owned by a project transfer.
      const saving = Promise.resolve().then(() => {
        if (!this.capturedDownloads.has(file)) return this.keepDownload(file);
      }).catch(() => {
        this.downloadError = '下载文件另存失败，请保持 Muse 窗口打开，检查本机下载目录';
      }).finally(() => this.downloadsPending.delete(saving));
      this.downloadsPending.add(saving);
    });
  }

  async keepDownload(file) {
    // Playwright's native downloads use temporary GUID paths, which disappear
    // on browser close. Keep a separate, named copy in the user's Downloads.
    const staging = path.join(this.root, '.local', 'muse-download-cache');
    await fs.mkdir(staging, { recursive: true });
    await fs.mkdir(this.downloadDirectory, { recursive: true });
    const temporary = path.join(staging, `${crypto.randomUUID()}.part`);
    let destination = '', published = false;
    try {
      await bounded(file.saveAs(temporary), 120000);
      const size = (await fs.stat(temporary)).size;
      if (!size || size > 200 * 1024 * 1024) throw new Error('Muse 下载文件为空或超过 200MB');
      const handle = await fs.open(temporary, 'r');
      const header = Buffer.alloc(12);
      try { await handle.read(header, 0, header.length, 0); } finally { await handle.close(); }
      const mp4 = header.toString('ascii', 4, 8) === 'ftyp';
      let name = path.basename(String(file.suggestedFilename() || '').replaceAll('\\', '/'))
        .replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[. ]+$/, '').slice(0, 180);
      if (!name || /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(name)) name = 'muse-download';
      const suffix = mp4 ? '.mp4' : path.extname(name) || '.bin';
      const stem = path.basename(name, path.extname(name));
      for (let index = 0; index < 10000; index++) {
        destination = path.join(this.downloadDirectory, `${stem}${index ? ` (${index})` : ''}${suffix}`);
        try { const reservation = await fs.open(destination, 'wx'); await reservation.close(); break; }
        catch (error) { if (error.code !== 'EEXIST') throw error; destination = ''; }
      }
      if (!destination) throw new Error('下载目录中同名文件过多');
      await fs.copyFile(temporary, destination);
      published = true;
      this.lastDownload = path.basename(destination);
      this.downloadError = '';
      await file.delete().catch(() => {});
    } finally {
      await fs.unlink(temporary).catch(() => {});
      if (destination && !published) await fs.unlink(destination).catch(() => {});
    }
  }

  async open() {
    return this.launch(true);
  }

  async openWindow() {
    return this.launch(false);
  }

  async createContext(profile, options) {
    return chromium.launchPersistentContext(profile,
      this.browserEngine === 'chromium' ? options : { ...options, channel: this.browserEngine });
  }

  async launch(headless) {
    if (this.starting) return this.starting;
    if (this.page && !this.page.isClosed()) {
      if (this.headless === headless) {
        if (!headless) await this.page.bringToFront();
        return this.status();
      }
      if (this.busy || this.panelPending) throw new Error('Muse 正在处理任务，暂时不能切换运行方式');
    }
    this.starting = (async () => {
      // A persistent profile may only have one owner. Cookies remain in place;
      // switching presentation deliberately requires selecting the chat again.
      if (this.context) await this.close();
      // Keep Chromium and system Chrome profiles separate: a newer Chrome
      // profile cannot safely be opened by the bundled browser.
      const bundled = await fs.access(chromium.executablePath()).then(() => true, () => false);
      const edgePath = path.join(process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)',
        'Microsoft', 'Edge', 'Application', 'msedge.exe');
      const edge = process.platform === 'win32' && await fs.access(edgePath).then(() => true, () => false);
      this.browserEngine = edge ? 'msedge' : bundled ? 'chromium' : 'chrome';
      this.closing = false;
      const profile = path.join(this.root, '.local', edge ? 'muse-browser-edge' : bundled ? 'muse-browser-chromium' : 'muse-browser');
      await fs.mkdir(this.downloadDirectory, { recursive: true });
      const downloadCache = path.join(this.root, '.local', 'muse-download-cache');
      await fs.mkdir(downloadCache, { recursive: true });
      this.headless = headless;
      const options = { headless, acceptDownloads: true, downloadsPath: downloadCache,
        viewport: headless ? { width: 1100, height: 720 } : null };
      try {
        this.context = await this.createContext(profile, options);
      } catch (error) {
        await this.diagnostic('launch_failed');
        throw new Error(`无法打开 Muse 浏览器，请安装浏览器组件（npm run browser:install --prefix web）。${error.message}`);
      }
      await this.diagnostic('opened');
      this.binding = '';
      const context = this.context;
      context.on('close', () => {
        if (this.context !== context) return;
        void this.diagnostic(this.closing ? 'closed_by_service' : 'browser_disconnected');
        this.binding = ''; this.page = null; this.viewPage = null; this.context = null;
        this.frames.clear();
      });
      const observe = (page) => {
        this.watchDownloads(page);
        page.on('crash', () => { void this.diagnostic('page_crashed'); });
      };
      this.context.on('page', observe);
      this.context.pages().forEach(observe);
      this.page = this.context.pages()[0] || await this.context.newPage();
      this.viewPage = this.page;
      this.frames.clear();
      context.on('page', (page) => {
        // Authentication popups are shown inside the project panel. Generation
        // download popups continue to belong to the transfer machinery.
        if (!this.busy) this.viewPage = page;
      });
      await this.page.goto('https://muse.ai/', { waitUntil: 'domcontentloaded', timeout: 30000 });
      return this.status();
    })();
    try { return await this.starting; } finally { this.starting = null; }
  }

  async status() {
    const open = !!this.page && !this.page.isClosed();
    let connected = open && !!this.binding && this.page.url() === this.binding;
    if (connected) {
      try { await composer(this.page); } catch { connected = false; }
    }
    return { browser_open: open, connected, background: open && this.headless, busy: this.busy, phase: this.phase, task_id: this.taskId,
      download_dir: this.downloadDirectory, last_download: this.lastDownload, download_error: this.downloadError,
      message: this.busy ? `Muse 正在${this.phase || '生成视频'}` : connected ? `Muse 当前聊天已连接${this.headless ? ' · 后台运行' : ''}` : open ? '请在 Muse 面板登录、选择旁聊，再点击使用当前聊天' : '请打开 Muse 面板，登录后选择聊天' };
  }

  panelOperation(operation, readOnly = false) {
    if (this.starting) throw new Error('Muse 正在启动，请稍后操作');
    if (!readOnly) this.panelPending++;
    const task = this.panelQueue.then(operation);
    this.panelQueue = task.catch(() => {});
    return task.finally(() => { if (!readOnly) this.panelPending--; });
  }

  panelPage() {
    if (!this.context || !this.page || this.page.isClosed()) throw new Error('请先打开 Muse 面板');
    if (!this.viewPage || this.viewPage.isClosed()) this.viewPage = this.page;
    return this.viewPage;
  }

  async frame() {
    return this.panelOperation(async () => {
      const page = this.panelPage();
      const viewport = page.viewportSize() || await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
      const bytes = await page.screenshot({ type: 'jpeg', quality: 78, timeout: 8000 });
      const id = crypto.randomUUID();
      this.frames.set(id, { page, url: page.url(), ...viewport, time: Date.now() });
      while (this.frames.size > 8) this.frames.delete(this.frames.keys().next().value);
      // No screenshots or typed account details are written to disk or journals.
      return { frame_id: id, image: bytes.toString('base64'), ...viewport,
        url: page.url(), busy: this.busy, background: this.headless };
    }, true);
  }

  async input(payload) {
    return this.panelOperation(async () => {
      if (this.busy) throw new Error('视频正在生成，暂时不能操作 Muse 聊天');
      const page = this.panelPage();
      const frame = this.frames.get(payload.frame_id);
      const viewport = page.viewportSize() || await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
      if (!frame || frame.page !== page || frame.url !== page.url() || Date.now() - frame.time > 15000
          || frame.width !== viewport.width || frame.height !== viewport.height) throw new Error('Muse 页面已变化，请刷新面板后操作');
      const kind = payload.kind;
      if (!['click', 'wheel', 'key', 'text'].includes(kind)) throw new Error('不支持的 Muse 页面操作');
      if (kind === 'click' || kind === 'wheel') {
        if (![payload.x, payload.y].every(Number.isFinite) || payload.x < 0 || payload.y < 0
            || payload.x >= viewport.width || payload.y >= viewport.height) throw new Error('Muse 操作位置超出页面');
      }
      if (kind === 'text' && (typeof payload.text !== 'string' || !payload.text.length || payload.text.length > 10000)) throw new Error('Muse 输入文字长度无效');
      if (kind === 'key' && !/^(?:(?:Control|Meta|Alt|Shift)\+){0,3}(?:Enter|Tab|Escape|Backspace|Delete|ArrowLeft|ArrowRight|ArrowUp|ArrowDown|Home|End|PageUp|PageDown|[a-z])$/.test(payload.key || '')) throw new Error('不支持的 Muse 按键');
      if (kind === 'wheel' && ![payload.dx, payload.dy].every(value => Number.isFinite(value) && Math.abs(value) <= 2000)) throw new Error('Muse 滚动距离无效');
      // Muse can change conversations without changing its URL. Any manual
      // interaction invalidates the binding until the user confirms this chat.
      this.binding = '';
      if (kind === 'click') await page.mouse.click(payload.x, payload.y);
      if (kind === 'wheel') { await page.mouse.move(payload.x, payload.y); await page.mouse.wheel(payload.dx, payload.dy); }
      if (kind === 'text') await page.keyboard.insertText(payload.text);
      if (kind === 'key') await page.keyboard.press(payload.key);
      return { ok: true };
    });
  }

  async resize(payload) {
    return this.panelOperation(async () => {
      if (this.busy) throw new Error('视频正在生成，暂时不能改变 Muse 页面尺寸');
      if (!this.headless) throw new Error('请先返回项目面板');
      if (!Number.isInteger(payload.width) || payload.width < 360 || payload.width > 1920
          || !Number.isInteger(payload.height) || payload.height < 300 || payload.height > 1200) throw new Error('Muse 页面尺寸无效');
      await this.panelPage().setViewportSize({ width: payload.width, height: payload.height });
      this.frames.clear();
      return { ok: true };
    });
  }

  async connect() {
    return this.panelOperation(async () => {
      if (this.busy) throw new Error('有视频正在生成，暂时不能切换 Muse 聊天');
      const current = this.viewPage;
      if (current && !current.isClosed() && new URL(current.url()).hostname === 'muse.ai') this.page = current;
      if (!this.headless) throw new Error('登录完成后，请先返回项目面板，再选择聊天');
      if (!this.page || this.page.isClosed() || new URL(this.page.url()).hostname !== 'muse.ai')
        throw new Error('请先打开并登录 Muse 官方页面');
      const input = await composer(this.page);
      const text = await inputText(input);
      if (draftText(text) && !await this.ownedDraft(text, this.page.url())) throw new Error('Muse 输入框已有草稿，请先处理草稿再连接');
      this.binding = this.page.url();
      return this.status();
    });
  }

  async ownedDraft(text, chatUrl, payload = null) {
    const token = draftText(text).match(/^镜序画布独立视频任务 (muse_[a-f0-9]{12})。/)?.[1];
    if (!token) return null;
    const key = payload ? await requestKey(payload) : null;
    for (const filename of await journalFiles(path.join(this.root, 'outputs'))) {
      const record = await fs.readFile(filename, 'utf8').then(JSON.parse).catch(() => null);
      if (!record || record.task_id !== token || record.chat_url !== chatUrl || !['draft', 'submitting'].includes(record.stage)) continue;
      let expected = record.draft_text;
      if (!expected) {
        // Read the matching canvas sidecar for drafts left by the first adapter.
        // A task token alone is insufficient to authorize replacing/sending text.
        const side = await fs.readFile(filename.replace(/\.muse\.json$/, '.json'), 'utf8').then(JSON.parse).catch(() => null);
        if (!side || side.video_backend !== 'muse' || !side.prompt || !Array.isArray(side.materials)) continue;
        const legacy = { images: side.materials.map((item) => item.file), prompt: side.prompt,
          duration: side.planned_duration, generate_audio: side.generate_audio, audio: side.audio || '' };
        expected = generationMessage(legacy, token);
        if (payload) {
          const project = path.dirname(path.dirname(filename));
          if (legacy.images.length !== payload.images.length || legacy.images.some((file, i) => {
            const relative = path.relative(project, path.resolve(payload.images[i]));
            return relative.startsWith('..') || path.isAbsolute(relative) || path.basename(payload.images[i]) !== file;
          })) continue;
        }
      }
      if (draftText(expected) !== draftText(text)) continue;
      if (payload && (draftText(generationMessage(payload, token)) !== draftText(text) || (record.request_key && record.request_key !== key))) continue;
      return { filename, record: { ...record, draft_text: expected, ...(key ? { request_key: key } : {}) } };
    }
    return null;
  }

  async upload(attachments) {
    if (!attachments.length) return;
    let files = this.page.locator('input[type="file"]');
    if (!await files.count()) {
      const buttons = this.page.getByRole('button', { name: /^(添加|添加附件|添加文件|上传文件|附件|\+|Add|Attach|Attach files|Add files|Upload|Add attachment|Add attachments)$/i });
      if (await buttons.count() !== 1) throw new Error('未识别到 Muse 的素材上传入口，请在 Muse 打开附件菜单后重试');
      await buttons.click();
      const item = this.page.getByRole('menuitem', { name: /上传|文件|upload|files/i });
      if (await item.count() === 1) await item.click();
      files = this.page.locator('input[type="file"]');
    }
    const accepts = (accept, filename) => {
      const extension = path.extname(filename).toLowerCase();
      const mime = ({ '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm', '.mkv': 'video/x-matroska', '.m4v': 'video/mp4', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' })[extension];
      return !accept.trim() || accept.split(',').some(item => {
        const value = item.trim().toLowerCase();
        return value === '*/*' || value === extension || value === mime || (mime && value === `${mime.split('/')[0]}/*`);
      });
    };
    const groups = [];
    const allInputs = [];
    for (let i = 0; i < await files.count(); i++) allInputs.push({ input: files.nth(i), accept: await files.nth(i).getAttribute('accept') || '' });
    const combined = allInputs.filter(item => attachments.every(file => accepts(item.accept, file)));
    if (combined.length === 1) groups.push({ input: combined[0].input, files: attachments });
    else if (!combined.length) {
      for (const type of ['image', 'video']) {
        const group = attachments.filter(file => /\.(mp4|mov|webm|mkv|m4v)$/i.test(file) === (type === 'video'));
        if (!group.length) continue;
        const candidates = allInputs.filter(item => group.every(file => accepts(item.accept, file)));
        if (candidates.length !== 1) throw new Error(`Muse 未提供明确的${type === 'video' ? '视频' : '图片'}上传入口，任务未发送`);
        groups.push({ input: candidates[0].input, files: group });
      }
    } else throw new Error('Muse 附件控件不明确，未发送生成任务');
    for (const group of groups) {
      const multiple = await group.input.evaluate(el => el.multiple);
      if (group.files.length > 1 && !multiple) throw new Error('当前 Muse 上传入口不支持多个附件，任务未发送');
    }
    for (const group of groups) {
      await group.input.setInputFiles(group.files);
      await this.waitForUploads();
    }
  }

  async waitForUploads() {
    // Wait for the actual upload UI to finish; never submit while attachments are pending.
    const uploading = this.page.getByRole('progressbar');
    const deadline = Date.now() + 120000;
    let settled = 0;
    while (Date.now() < deadline) {
      const pending = await uploading.count() > 0 && await uploading.first().isVisible();
      settled = pending ? 0 : settled + 1;
      if (settled >= 3) return;
      await pause(500);
    }
    throw new Error('Muse 素材上传尚未完成，任务未发送');
  }

  async download(asset, rawPath) {
    const url = new URL(asset.url);
    if (!['https:', 'blob:'].includes(url.protocol)) throw new Error('Muse 返回了不支持的视频下载地址');
    const temporary = `${rawPath}.part`;
    try {
      const anchor = this.page.locator('a[href]').filter({ hasText: asset.name });
      if (asset.name && await anchor.count() === 1) {
        if (await this.downloadControl(anchor, rawPath)) return;
      }
      if (url.protocol !== 'https:') throw new Error('Muse 视频需要在页面点击下载，请在当前旁聊检查结果');
      const response = await this.context.request.get(url.href, { timeout: 120000 });
      if (!response.ok()) throw new Error('Muse 视频下载失败，请在旁聊检查成片');
      const type = response.headers()['content-type'] || '';
      if (!/video\/|octet-stream/i.test(type)) throw new Error('Muse 返回的链接不是视频文件，任务结果未回填');
      if (Number(response.headers()['content-length'] || 0) > 200 * 1024 * 1024) throw new Error('Muse 视频超过 200MB，请手动下载后导入');
      const buffer = await response.body();
      if (!buffer.length || buffer.length > 200 * 1024 * 1024) throw new Error('Muse 视频为空或超过 200MB');
      await fs.writeFile(temporary, buffer, { flag: 'wx' });
      await fs.rename(temporary, rawPath);
    } finally { await fs.unlink(temporary).catch(() => {}); }
  }

  captureDownload(owner = this.page, timeout = 20000) {
    // The browser close callback clears this.context. Keep the emitter that
    // owns these listeners so cleanup cannot mask an interrupted download.
    const context = this.context;
    const pages = new Set([owner]);
    let resolve, finished = false;
    const promise = new Promise((done) => { resolve = done; });
    const receive = (file) => { if (!finished) { this.capturedDownloads.add(file); finished = true; resolve(file); } };
    const closed = () => { if (!finished) { finished = true; resolve(null); } };
    const attach = (page) => { if (!pages.has(page)) { pages.add(page); page.on('download', receive); } };
    owner.on('download', receive);
    const opened = async (page) => {
      const opener = await page.opener().catch(() => null);
      if (!finished && pages.has(opener)) attach(page);
    };
    context.on('page', opened);
    context.on('close', closed);
    owner.on('close', closed);
    const timer = setTimeout(() => { if (!finished) { finished = true; resolve(null); } }, timeout);
    return { promise, dispose: () => {
      clearTimeout(timer);
      finished = true;
      context.off('page', opened);
      context.off('close', closed);
      owner.off('close', closed);
      pages.forEach((page) => page.off('download', receive));
      resolve(null);
    } };
  }

  async downloadControl(button, rawPath, owner = this.page) {
    const context = this.context;
    const capture = this.captureDownload(owner);
    try {
      await button.click({ timeout: 10000 });
      const file = await capture.promise;
      if (!file) {
        if (owner.isClosed() || this.context !== context) throw new Error('Muse 下载窗口已关闭');
        return false;
      }
      await this.saveDownload(file, rawPath);
      return true;
    } catch (error) {
      if (owner.isClosed() || this.context !== context)
        throw new Error('下载期间 Muse 窗口已关闭，请重新连接后取回原任务成片；不会重新生成');
      throw error;
    } finally { capture.dispose(); }
  }

  async saveDownload(file, rawPath) {
    const temporary = `${rawPath}.part`;
    try {
      await bounded(file.saveAs(temporary), 120000);
      if (!(await fs.stat(temporary)).size) throw new Error('Muse 返回空视频');
      await fs.rename(temporary, rawPath);
      // rawPath is inside the project's staging directory; the provider
      // validates and publishes it, then removes that directory.
      await file.delete().catch(() => {});
    } finally { await fs.unlink(temporary).catch(() => {}); }
  }

  async waitForResult(payload, record, saveRecord) {
    this.phase = '取回成片';
    const oldUrls = new Set(record.old_urls || []);
    const deadline = Date.now() + payload.timeout_s * 1000;
    const previousPages = new Set(this.context.pages());
    let opened = false;
    while (Date.now() < deadline) {
      if (!this.page || this.page.isClosed() || this.page.url() !== this.binding) throw new Error('Muse 页面已关闭或切换，请先检查原旁聊的任务结果');
      const assets = (await mediaLinks(this.page, record.task_id)).filter((item) => !oldUrls.has(item.url));
      if (assets.length > 1) throw new Error('Muse 返回多个同编号视频，请在旁聊确认成片后手动导入');
      if (assets.length === 1 && assets[0].url.startsWith('https:')) {
        this.phase = '下载成片';
        await this.download(assets[0], payload.raw_path);
      } else {
        const directReplyDownload = await replyDownload(this.page, record.task_id, crypto.randomUUID());
        if (directReplyDownload) {
          this.phase = '下载成片';
          try {
            if (!await this.downloadControl(directReplyDownload, payload.raw_path)) throw new Error('Muse 成片下载按钮没有返回文件，请检查原聊天');
          } finally { await directReplyDownload.evaluate((el) => el.removeAttribute('data-frameflow-result-download')).catch(() => {}); }
          record.stage = 'downloaded'; delete record.error; await saveRecord();
          return { task_id: record.task_id, chat_url: record.chat_url };
        }
        const artifact = !opened && await resultArtifact(this.page, record.task_id);
        if (artifact) {
          const replyDownload = await resultDownload(this.page, artifact, record.task_id);
          if (replyDownload) {
            this.phase = '下载成片';
            try {
              if (!await this.downloadControl(replyDownload, payload.raw_path)) throw new Error('Muse 成片旁的下载按钮没有返回文件，请检查原聊天');
            } finally { await replyDownload.evaluate((el) => el.removeAttribute('data-frameflow-result-download')).catch(() => {}); }
            record.stage = 'downloaded'; delete record.error; await saveRecord();
            return { task_id: record.task_id, chat_url: record.chat_url };
          }
          this.phase = '打开成片';
          const capture = this.captureDownload(this.page, 1500);
          let downloaded;
          try { await artifact.click({ timeout: 10000 }); opened = true; downloaded = await capture.promise; }
          finally { capture.dispose(); }
          if (downloaded) {
            await this.saveDownload(downloaded, payload.raw_path);
            record.stage = 'downloaded'; delete record.error; await saveRecord();
            return { task_id: record.task_id, chat_url: record.chat_url };
          }
        }
        // A viewer belongs to this task only after opening its matched artifact.
        const viewers = [];
        if (opened) for (const page of this.context.pages()) {
          if (!previousPages.has(page) && !page.isClosed() && await page.opener() === this.page) viewers.push(page);
        }
        if (viewers.length > 1) throw new Error('Muse 打开多个成片窗口，请确认需要取回的视频');
        const viewer = viewers[0] || this.page;
        const dialogs = viewer.getByRole('dialog').filter({ visible: true });
        const scope = await dialogs.count() === 1 ? dialogs : viewer;
        const downloadName = /^(下载(?:视频|文件)?|download(?:\s+(?:video|file))?|save video|保存视频)(?:\s|$)/i;
        const controls = scope.getByRole('button', { name: downloadName })
          .or(scope.getByRole('link', { name: downloadName })).or(scope.locator('a[download]')).filter({ visible: true });
        if (opened && !assets.length && await dialogs.count() === 1 && await dialogs.locator('video').count() === 1) {
          const url = await dialogs.locator('video').evaluate((el) => el.currentSrc || el.src || el.querySelector('source')?.src || '');
          if (/^(https:|blob:)/.test(url)) assets.push({ url, name: `${record.task_id}.mp4` });
        }
        if (opened && await controls.count() === 1) {
          this.phase = '下载成片';
          if (!await this.downloadControl(controls, payload.raw_path, viewer)) throw new Error('Muse 下载按钮没有返回文件，请检查成片窗口');
        } else if (assets.length === 1 && assets[0].url.startsWith('https:')) {
          this.phase = '下载成片';
          await this.download(assets[0], payload.raw_path);
        } else if (assets.length === 1) {
          this.phase = '下载成片';
          // Browser-local video blobs have no HTTP endpoint. Export the observed
          // public media URL using the browser's native download mechanism.
          const marker = crypto.randomUUID();
          await this.page.evaluate(({ url, filename, marker }) => {
            const link = document.createElement('a'); link.href = url; link.download = filename;
            link.textContent = filename; link.setAttribute('data-frameflow-download', marker);
            document.body.append(link);
          }, { url: assets[0].url, filename: `${record.task_id}.mp4`, marker });
          const link = this.page.locator(`[data-frameflow-download="${marker}"]`);
          try {
            if (!await this.downloadControl(link, payload.raw_path)) throw new Error('Muse 视频地址无法下载，请在成片窗口检查下载入口');
          } finally { await link.evaluate((el) => el.remove()).catch(() => {}); }
        } else { this.phase = opened ? '等待成片下载入口' : '等待 Muse 成片卡片'; await pause(1000); continue; }
      }
      record.stage = 'downloaded';
      delete record.error;
      await saveRecord();
      return { task_id: record.task_id, chat_url: record.chat_url };
    }
    throw new Error(`未取回 ${record.task_id} 的视频文件。请在 Muse 打开成片，确认有下载入口后点击“取回 Muse 成片”；不会重新生成。`);
  }

  async recover(payload) {
    if (this.busy || this.starting || this.panelPending) throw new Error('Muse 正在处理另一项操作，请等待当前操作结束');
    if (!(await this.status()).connected) throw new Error('Muse 未连接，请打开原聊天并连接后取回成片');
    if (this.busy || this.starting || this.panelPending) throw new Error('Muse 正在处理另一项操作，请等待当前操作结束');
    for (const filename of [payload.raw_path, payload.journal_path]) {
      const relative = path.relative(path.join(this.root, 'outputs'), path.resolve(filename));
      if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Muse 文件必须属于本机作品目录');
    }
    const record = JSON.parse(await fs.readFile(payload.journal_path, 'utf8'));
    if (!/^muse_[a-f0-9]{12}$/.test(record.task_id) || record.chat_url !== this.binding
        || !['submitted', 'submitting', 'downloaded'].includes(record.stage)) throw new Error('该 Muse 任务没有可取回的提交记录，请检查原旁聊');
    if (this.busy || this.starting || this.panelPending || !this.binding) throw new Error('Muse 聊天状态已变化，请重新使用当前聊天');
    this.busy = true;
    this.taskId = record.task_id;
    const saveRecord = () => journal(payload.journal_path, record);
    try { return await this.waitForResult(payload, record, saveRecord); }
    catch (error) { record.error = error.message; await saveRecord().catch(() => {}); throw error; }
    finally { this.busy = false; this.phase = ''; }
  }

  async resumeChat(payload) {
    if (this.busy || this.starting || this.panelPending) throw new Error('Muse 正在处理任务，请等待当前操作结束');
    if (this.context && !this.headless) throw new Error('请先返回项目里的 Muse 面板');
    const filename = path.resolve(payload.journal_path || '');
    const relative = path.relative(path.join(this.root, 'outputs'), filename);
    if (relative.startsWith('..') || path.isAbsolute(relative) || !filename.endsWith('.muse.json')) throw new Error('Muse 提交记录必须属于本机作品目录');
    const record = JSON.parse(await fs.readFile(filename, 'utf8'));
    if (!/^muse_[a-f0-9]{12}$/.test(record.task_id) || !['submitted', 'submitting', 'downloaded'].includes(record.stage)
        || !/^https:\/\/muse\.ai\/thread\/[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(record.chat_url || '')) throw new Error('原任务没有独立聊天地址，请在 Muse 面板选择原旁聊后连接');
    if (this.busy || this.starting || this.panelPending) throw new Error('Muse 正在处理任务，请等待当前操作结束');
    this.busy = true;
    this.phase = '重新连接原聊天';
    this.binding = '';
    try {
      await this.open();
      await this.page.goto(record.chat_url, {waitUntil:'domcontentloaded',timeout:30000});
      this.viewPage = this.page;
      this.frames.clear();
      const deadline = Date.now() + 20000;
      let input;
      while (Date.now() < deadline) {
        if (new URL(this.page.url()).hostname !== 'muse.ai') throw new Error('Muse 登录已失效，请在 Muse 面板登录后选择原旁聊');
        if (this.page.url() === record.chat_url) {
          try { input = await composer(this.page); break; } catch { /* Wait for the public chat to render. */ }
        }
        await pause(500);
      }
      if (!input) throw new Error('原 Muse 聊天尚未就绪，请在面板检查登录和聊天状态');
      const text = await inputText(input);
      if (draftText(text) && !await this.ownedDraft(text, record.chat_url)) throw new Error('Muse 输入框已有其他草稿，请先处理再连接');
      this.binding = record.chat_url;
      return { connected:true, browser_open:true, background:true, busy:false, message:'Muse 原任务聊天已连接 · 后台运行' };
    } finally { this.busy = false; this.phase = ''; }
  }

  async generate(payload) {
    if (this.busy || this.starting || this.panelPending) throw new Error('Muse 正在处理另一个操作，请等待当前操作完成');
    if (!(await this.status()).connected) throw new Error('Muse 未连接，请在服务设置打开 Muse 面板并使用当前聊天');
    if (this.busy || this.starting || this.panelPending) throw new Error('Muse 正在处理另一个操作，请等待当前操作完成');
    // All filesystem paths originate from project-owned material resolution, and stay local.
    for (const filename of [...payload.images, ...(payload.videos || []), payload.raw_path, payload.journal_path]) {
      const relative = path.relative(path.join(this.root, 'outputs'), path.resolve(filename));
      if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Muse 文件必须属于本机作品目录');
    }
    try {
      await fs.access(payload.journal_path);
      throw new Error('该任务已有 Muse 提交记录，请先检查旁聊结果，不能自动重复发送');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (this.busy || this.starting || this.panelPending || !this.binding) throw new Error('Muse 聊天状态已变化，请重新使用当前聊天');
    this.busy = true;
    this.taskId = `muse_${crypto.randomBytes(6).toString('hex')}`;
    let record = { task_id: this.taskId, chat_url: this.binding, stage: 'preparing', created: Date.now() };
    let previousDraft = null;
    const saveRecord = async () => {
      await journal(payload.journal_path, record);
      if (previousDraft) await journal(previousDraft.filename, { ...record, recovered_by: payload.journal_path });
    };
    try {
      let input = await composer(this.page);
      const text = await inputText(input);
      if (draftText(text)) {
        previousDraft = await this.ownedDraft(text, this.binding, payload);
        if (!previousDraft) throw new Error('Muse 输入框已有其他草稿；请先处理草稿。相同画布任务留下的草稿可以继续发送。');
        record = previousDraft.record;
        this.taskId = record.task_id;
        if (await taskPosted(this.page, this.taskId)) {
          record.stage = 'submitted';
          throw new Error('该 Muse 任务已出现在聊天中，请检查原任务结果；不会重复发送');
        }
        // The existing matching draft already has its attachments. Do not upload twice.
      } else {
        this.phase = '上传素材';
        await this.upload([...payload.images, ...(payload.videos || [])]);
        input = await composer(this.page);
        if (draftText(await inputText(input))) throw new Error('Muse 输入框出现了新的草稿，未发送任务');
        record.request_key = await requestKey(payload);
        record.draft_text = generationMessage(payload, this.taskId);
        record.stage = 'draft';
        await saveRecord();
        await input.fill(record.draft_text);
      }
      const oldUrls = new Set(record.old_urls || (await mediaLinks(this.page)).map((item) => item.url));
      record.old_urls = [...oldUrls];
      this.phase = '提交任务';
      const buttonDeadline = Date.now() + 15000;
      let send;
      while (!(send = await sendButton(this.page, input)).button) {
        if (send.state !== 'disabled' || Date.now() >= buttonDeadline) throw new Error('未识别到唯一可用的 Muse 发送按钮。内容已保留，请在 Muse 点击右下角发送箭头；相同画布任务可重试。');
        await pause(500);
      }
      try {
        if (draftText(await inputText(input)) !== draftText(record.draft_text)) throw new Error('Muse 草稿内容已变动，任务未发送');
        record.stage = 'submitting';
        delete record.error;
        await saveRecord();
        // A retry requires this exact owned draft to remain unsent in the composer.
        await send.button.click({ timeout: 10000 });
      }
      finally { await send.button.evaluate((el) => el.removeAttribute('data-frameflow-send')).catch(() => {}); }
      const sendDeadline = Date.now() + 15000;
      while (draftText(await inputText(input))) {
        if (Date.now() > sendDeadline) throw new Error('Muse 未确认消息已发送，请检查旁聊；不会自动再次提交');
        await pause(500);
      }
      record.stage = 'submitted';
      await saveRecord();
      return await this.waitForResult(payload, record, saveRecord);
    } catch (error) {
      if (record.stage !== 'preparing') {
        record.error = error.message;
        await saveRecord().catch(() => {});
        throw new Error(`${error.message}（Muse 任务 ${record.task_id}，请先检查旁聊结果）`);
      }
      throw error;
    } finally { this.busy = false; this.phase = ''; }
  }

  async close() {
    this.closing = true;
    await this.diagnostic('service_shutdown');
    await bounded(Promise.allSettled([...this.downloadsPending]), 30000).catch(() => {});
    await this.context?.close();
  }
}

async function main() {
  const driver = new MuseBrowser(process.argv[2]);
  const lines = readline.createInterface({ input: process.stdin });
  lines.on('line', async (line) => {
    let request;
    try {
      request = JSON.parse(line);
      if (!['open', 'openWindow', 'status', 'connect', 'frame', 'input', 'resize', 'resumeChat', 'generate', 'recover'].includes(request.command)) throw new Error('未知 Muse 操作');
      const result = await driver[request.command](request.payload);
      process.stdout.write(JSON.stringify({ id: request.id, result }) + '\n');
    } catch (error) {
      process.stdout.write(JSON.stringify({ id: request?.id, error: error.message }) + '\n');
    }
  });
  lines.on('close', async () => { await driver.close(); process.exit(0); });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();

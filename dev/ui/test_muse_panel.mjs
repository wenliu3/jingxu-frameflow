// Background browser and project panel controls against intercepted local pages.
// No account, remote login, credentials or generation service is accessed.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { MuseBrowser } from '../../server/muse_browser.mjs';
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const root = await fs.mkdtemp(path.join(os.tmpdir(), 'frameflow-muse-panel-'));
const launches = [];
class FixtureBrowser extends MuseBrowser {
  async createContext(profile, options) {
    launches.push({ profile, ...options });
    const context = await chromium.launchPersistentContext(profile, options);
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: `
      <style>body{margin:0}button{width:180px;height:60px}textarea{display:block;width:500px;height:150px}section{height:1800px}</style>
      <button onclick="localStorage.chat='new-chat'; document.querySelector('textarea').value=''">新建旁聊</button>
      <button onclick="window.open('/login', 'login')">登录</button>
      <textarea aria-label="消息"></textarea><section></section><script>
      document.body.dataset.chat=localStorage.chat||'main';
      document.querySelector('textarea').addEventListener('input',e=>document.body.dataset.text=e.target.value);
      </script>` }));
    return context;
  }
}
const driver = new FixtureBrowser(root, path.join(root, 'downloads'));
try {
  const opened = await driver.open();
  assert.equal(opened.background, true);
  assert.equal(launches.length, 1);
  assert.equal(launches[0].headless, true);
  await driver.open();
  assert.equal(launches.length, 1, 'reopening the panel reuses the existing background browser');
  await driver.resize({width:960,height:600});
  let frame = await driver.frame();
  assert.equal(frame.width, 960);
  assert.equal(frame.height, 600);
  assert.equal(Buffer.from(frame.image,'base64').subarray(0,2).toString('hex'), 'ffd8');
  assert.equal(await driver.page.locator('textarea').inputValue(), '');
  await driver.input({kind:'click',frame_id:frame.frame_id,x:40,y:30});
  frame = await driver.frame();
  await driver.input({kind:'click',frame_id:frame.frame_id,x:80,y:100});
  await driver.input({kind:'text',frame_id:frame.frame_id,text:'中文输入与粘贴'});
  await driver.input({kind:'key',frame_id:frame.frame_id,key:'End'});
  await driver.input({kind:'key',frame_id:frame.frame_id,key:'Backspace'});
  assert.equal(await driver.page.locator('textarea').inputValue(), '中文输入与粘');
  await assert.rejects(driver.connect(), /已有草稿/);
  await driver.input({kind:'key',frame_id:frame.frame_id,key:'Control+a'});
  await driver.input({kind:'key',frame_id:frame.frame_id,key:'Backspace'});
  assert.equal((await driver.connect()).connected, true);
  await assert.rejects(driver.input({kind:'click',frame_id:frame.frame_id,x:-1,y:40}), /超出/);
  await assert.rejects(driver.input({kind:'key',frame_id:frame.frame_id,key:'F12'}), /不支持/);
  await assert.rejects(driver.input({kind:'text',frame_id:frame.frame_id,text:'a'.repeat(10001)}), /长度/);
  assert.equal((await driver.status()).connected, true, 'rejected actions do not invalidate the chat');
  await driver.input({kind:'wheel',frame_id:frame.frame_id,x:500,y:400,dx:0,dy:500});
  assert.equal((await driver.status()).connected, false, 'manual interaction requires confirming the chat even when its URL is unchanged');
  await driver.page.waitForFunction(() => scrollY > 0);
  await driver.page.evaluate(() => scrollTo(0,0));
  await driver.page.waitForFunction(() => scrollY === 0);
  frame = await driver.frame();
  const popupReady = driver.context.waitForEvent('page');
  await driver.input({kind:'click',frame_id:frame.frame_id,x:220,y:30});
  const popup = await popupReady;
  await popup.waitForURL('**/login');
  const popupFrame = await driver.frame();
  assert.match(popupFrame.url, /\/login$/);
  await assert.rejects(driver.input({kind:'click',frame_id:frame.frame_id,x:10,y:10}), /页面已变化/);
  await driver.viewPage.close();
  assert.equal((await driver.frame()).url, 'https://muse.ai/');
  driver.busy = true;
  frame = await driver.frame();
  assert.equal(frame.busy, true);
  await assert.rejects(driver.input({kind:'click',frame_id:frame.frame_id,x:20,y:20}), /视频正在生成/);
  await assert.rejects(driver.resize({width:960,height:600}), /视频正在生成/);
  await assert.rejects(driver.openWindow(), /暂时不能切换/);
  driver.busy = false;
  await driver.close();
  await driver.open();
  assert.equal(launches.length, 2);
  assert.equal(await driver.page.evaluate(() => localStorage.chat), 'new-chat', 'profile survives background browser restarts');
  assert.equal((await driver.status()).connected, false, 'relaunch never guesses which chat to bind');
  frame = await driver.frame();
  await driver.resize({width:800,height:500});
  await assert.rejects(driver.input({kind:'click',frame_id:frame.frame_id,x:20,y:20}), /页面已变化/);
  await assert.rejects(driver.resize({width:10,height:600}), /尺寸/);
  await driver.connect();
  const files = await fs.readdir(path.join(root, '.local'));
  assert.ok(!files.some(name=> /\.(?:jpe?g|png)$/.test(name)), 'account screenshots are never persisted');
  await driver.close();
  const resumeJournal = path.join(root,'outputs','segments','seg_112233445566.muse.json');
  await fs.mkdir(path.dirname(resumeJournal),{recursive:true});
  const record = {task_id:'muse_112233445566',stage:'submitted',chat_url:'https://muse.ai/thread/024908e9-49c8-40ca-9bfd-bc36c3fdf90e'};
  await fs.writeFile(resumeJournal,JSON.stringify(record));
  const resumed = await driver.resumeChat({journal_path:resumeJournal});
  assert.equal(resumed.background,true);
  assert.equal(resumed.connected,true);
  assert.equal(driver.page.url(),record.chat_url);
  assert.equal(await driver.page.locator('textarea').inputValue(),'');
  assert.equal(JSON.parse(await fs.readFile(resumeJournal,'utf8')).stage,'submitted','reconnecting never changes or resubmits the task');
  await assert.rejects(driver.resumeChat({journal_path:path.join(root,'external.muse.json')}),/作品目录/);
  await fs.writeFile(resumeJournal,JSON.stringify({...record,chat_url:'https://muse.ai/'}));
  await assert.rejects(driver.resumeChat({journal_path:resumeJournal}),/独立聊天地址/);
  assert.equal(driver.page.url(),record.chat_url);
  console.log('PASS recovery reconnects only the original persisted unique Muse thread in the background, without uploads or prompt submission');
  console.log('PASS headless Muse profile reuse, in-project screenshots/input/IME text, popup navigation, bounds, stale frames, drafts and generation isolation');
} finally {
  await driver.close();
  if (path.dirname(path.resolve(root)) !== path.resolve(os.tmpdir()) || !path.basename(root).startsWith('frameflow-muse-panel-')) throw new Error('Unexpected fixture directory');
  await fs.rm(root, {recursive:true,force:true});
}

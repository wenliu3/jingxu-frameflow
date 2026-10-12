// Real browser against a fully intercepted fixture; never visits an account or model.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { MuseBrowser, composer, generationMessage, mediaLinks, resultArtifact, sendButton } from '../../server/muse_browser.mjs';
const require = createRequire(new URL('../../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const root = await fs.mkdtemp(path.join(os.tmpdir(), 'frameflow-muse-test-'));
const outputs = path.join(root, 'outputs');
await fs.mkdir(outputs);
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ acceptDownloads: true });
  await context.route('**/*', (route) => route.fulfill({ contentType: 'text/html; charset=utf-8', body: `
    <style>#composer { position:relative; width:640px; height:240px; } textarea {width:550px;height:170px}
    #send {position:absolute; right:10px;bottom:10px;width:40px;height:40px} #attach {position:absolute;left:10px;bottom:10px}</style>
    <div>旁聊：镜序画布</div><button onclick="document.body.dataset.wrong='true'">Send message</button>
    <input type="file" multiple><div id="results"><a href="https://cdn.example/old.mp4">old.mp4</a></div>
    <div id="composer"><textarea placeholder="消息"></textarea><button id="attach">+</button>
    <button id="send"><svg width="24" height="24"><path d="M12 19V5 M5 12l7-7 7 7"/></svg></button></div><script>
    let sends = 0;
    document.querySelector('input').addEventListener('change', () => {
      document.body.dataset.uploads = Number(document.body.dataset.uploads || 0) + 1;
    });
    document.querySelector('textarea').addEventListener('keydown', e => {
      if (e.key === 'Enter') document.body.dataset.enters = Number(document.body.dataset.enters || 0) + 1;
    });
    document.querySelector('#send').addEventListener('click', () => {
      sends++;
      const input = document.querySelector('textarea');
      const message = input.value;
      const token = message.match(/muse_[a-f0-9]{12}/)[0];
      input.value = '';
      document.body.dataset.sends = sends;
      document.body.dataset.uploaded = document.querySelector('input').files.length;
      document.body.dataset.message = message;
      const add = name => {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob(['fixture-video'], {type:'video/mp4'}));
        link.download = name; link.textContent = name;
        document.querySelector('#results').append(link);
      };
      add('unrelated_background.mp4');
      add(token + '.mp4');
    });
    </script>` }));
  const page = await context.newPage();
  await page.goto('https://muse.ai/');
  const driver = new MuseBrowser(root);
  driver.context = context;
  driver.page = page;
  assert.equal((await driver.status()).connected, false);
  await driver.connect();
  assert.equal((await driver.status()).connected, true);
  const image = path.join(outputs, 'photo.png');
  await fs.writeFile(image, 'fixture-photo');
  const payload = { images: [image], prompt: '参考图 1：主角\n主角在竹林散步。', duration: 5, generate_audio: false,
    raw_path: path.join(outputs, 'raw.mp4'), journal_path: path.join(outputs, 'job.muse.json'), timeout_s: 4 };
  const result = await driver.generate(payload);
  assert.match(result.task_id, /^muse_[a-f0-9]{12}$/);
  assert.equal(await fs.readFile(payload.raw_path, 'utf8'), 'fixture-video');
  assert.equal((await mediaLinks(page, result.task_id)).length, 1);
  const observed = await page.evaluate(() => ({ ...document.body.dataset }));
  assert.equal(observed.sends, '1');
  assert.equal(observed.enters, undefined);
  assert.equal(observed.wrong, undefined);
  assert.equal(observed.uploaded, '1');
  assert.match(observed.message, /主角在竹林散步/);
  assert.match(observed.message, /无音轨/);
  assert.equal(JSON.parse(await fs.readFile(payload.journal_path, 'utf8')).stage, 'downloaded');
  await assert.rejects(driver.generate(payload), /已有 Muse 提交记录/);
  assert.equal(await page.getAttribute('body', 'data-sends'), '1');
  console.log('PASS Muse clicks the icon-only composer arrow, never Enter or unrelated Send buttons, and downloads only the matching clip');

  // A missing button leaves an owned draft. Retrying the same request, even in a
  // replacement driver, resumes its original token without uploading twice.
  const failed = { ...payload, journal_path: path.join(outputs, 'pending.muse.json'), raw_path: path.join(outputs, 'pending.mp4') };
  await page.locator('#send').evaluate(el => { el.hidden = true; });
  await assert.rejects(driver.generate(failed), /发送按钮/);
  const pending = JSON.parse(await fs.readFile(failed.journal_path, 'utf8'));
  assert.equal(pending.stage, 'draft');
  const pendingText = await page.locator('textarea').inputValue();
  const sendsBefore = await page.getAttribute('body', 'data-sends');
  const uploadsBefore = await page.getAttribute('body', 'data-uploads');
  await assert.rejects(driver.generate({ ...failed, prompt: '完全不同的任务', journal_path: path.join(outputs, 'different.muse.json') }), /草稿/);
  assert.equal(await page.locator('textarea').inputValue(), pendingText);
  assert.equal(await page.getAttribute('body', 'data-sends'), sendsBefore);
  await page.locator('textarea').fill(pendingText + '\n');
  await page.locator('#send').evaluate(el => { el.hidden = false; });
  const replacement = new MuseBrowser(root);
  replacement.context = context;
  replacement.page = page;
  await replacement.connect();
  const recovered = { ...failed, journal_path: path.join(outputs, 'recovered.muse.json'), raw_path: path.join(outputs, 'recovered.mp4') };
  const continued = await replacement.generate(recovered);
  assert.equal(continued.task_id, pending.task_id);
  assert.equal(await page.getAttribute('body', 'data-sends'), '2');
  assert.equal(await page.getAttribute('body', 'data-uploads'), uploadsBefore);
  assert.equal(JSON.parse(await fs.readFile(failed.journal_path, 'utf8')).stage, 'downloaded');
  await assert.rejects(replacement.generate(recovered), /已有 Muse 提交记录/);
  console.log('PASS matching owned drafts resume after reconnect with one send, the original task ID and no duplicate attachment');

  // Migrate the precise draft from the first adapter's submitting journal.
  const token = 'muse_a93277b2d41b';
  const legacyPath = path.join(outputs, 'legacy.muse.json');
  await fs.writeFile(legacyPath, JSON.stringify({ task_id: token, chat_url: page.url(), stage: 'submitting' }));
  await fs.writeFile(path.join(outputs, 'legacy.json'), JSON.stringify({ video_backend: 'muse',
    prompt: payload.prompt, planned_duration: payload.duration, generate_audio: payload.generate_audio,
    materials: [{ file: path.basename(image) }] }));
  await page.locator('textarea').fill(generationMessage(payload, token) + '\n');
  await replacement.connect();
  const legacy = await replacement.generate({ ...payload, journal_path: path.join(outputs, 'legacy-retry.muse.json') });
  assert.equal(legacy.task_id, token);
  assert.equal(await page.getAttribute('body', 'data-sends'), '3');
  assert.equal(await page.getAttribute('body', 'data-uploads'), uploadsBefore);
  console.log('PASS legacy unsent drafts recover only when their canvas sidecar, images and full prompt match');

  // An already posted task must not be sent again even if an identical draft remains.
  await fs.writeFile(path.join(outputs, 'posted.muse.json'), JSON.stringify({ task_id: token,
    chat_url: page.url(), stage: 'submitting', draft_text: generationMessage(payload, token) }));
  await page.locator('textarea').fill(generationMessage(payload, token));
  await page.locator('#results').evaluate((el, text) => {
    const posted = document.createElement('p'); posted.id = 'posted'; posted.textContent = text; el.append(posted);
  }, generationMessage(payload, token));
  await assert.rejects(replacement.generate({ ...payload, journal_path: path.join(outputs, 'posted-retry.muse.json') }), /已出现在聊天/);
  assert.equal(await page.getAttribute('body', 'data-sends'), '3');
  await page.locator('#posted').evaluate(el => el.remove());
  console.log('PASS posted messages are never submitted again');

  await page.locator('textarea').fill('用户未发送的草稿');
  await assert.rejects(driver.connect(), /已有草稿/);
  await assert.rejects(driver.generate({ ...payload, journal_path: path.join(outputs, 'draft.muse.json') }), /草稿/);
  assert.equal(await page.getAttribute('body', 'data-sends'), '3');
  console.log('PASS Muse preserves existing drafts instead of overwriting them');

  await page.locator('#send').evaluate(el => { el.parentElement.append(el.cloneNode(true)); });
  assert.equal((await sendButton(page, await composer(page))).state, 'ambiguous');
  await page.locator('#composer > button').last().evaluate(el => el.remove());
  console.log('PASS ambiguous composer buttons stop without falling back to Enter');

  await page.locator('textarea').fill('');
  await page.locator('textarea').evaluate(el => {
    const replacement = document.createElement('div');
    replacement.setAttribute('role', 'textbox'); replacement.contentEditable = 'true';
    el.replaceWith(replacement);
  });
  assert.ok(await composer(page));
  await page.locator('body').evaluate(el => {
    const extra = document.createElement('textarea'); el.append(extra);
  });
  await assert.rejects(composer(page), /唯一/);
  assert.equal((await driver.status()).connected, false);
  console.log('PASS Muse supports editable chat composers and rejects ambiguous/login UI');
  await context.close();

  const artifactContext = await browser.newContext({ acceptDownloads: true });
  const resultToken = 'muse_112233445566';
  await artifactContext.route('**/*', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: `
    <div><p>镜序画布独立视频任务 ${resultToken}。建议 ${resultToken}.mp4</p><img width="200" height="160"></div>
    <section><p>视频做好了，文件名 muse_ffffffffffff.mp4。</p><img width="200" height="160"></section>
    <section><p>视频做好了，文件名 ${resultToken}.mp4。</p><div><img id="result" width="200" height="160"></div></section>
    <div role="dialog" hidden><button aria-label="Download video">下载</button></div>
    <div><textarea></textarea><button id="send">Send</button></div><input type="file">
    <script>
      document.querySelector('#send').onclick = () => document.body.dataset.sends = '1';
      document.querySelector('#result').onclick = () => {
        document.body.dataset.opens = Number(document.body.dataset.opens || 0) + 1;
        document.querySelector('[role="dialog"]').hidden = false;
      };
      document.querySelector('[aria-label="Download video"]').onclick = () => {
        document.body.dataset.downloads = Number(document.body.dataset.downloads || 0) + 1;
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['completed-video'], {type:'video/mp4'}));
        a.download = 'video.mp4'; a.click();
      };
    </script>` }));
  const artifactPage = await artifactContext.newPage();
  await artifactPage.goto('https://muse.ai/');
  assert.equal((await mediaLinks(artifactPage, resultToken)).length, 0);
  assert.ok(await resultArtifact(artifactPage, resultToken));
  const recoverDriver = new MuseBrowser(root);
  recoverDriver.context = artifactContext; recoverDriver.page = artifactPage;
  await recoverDriver.connect();
  const artifactPayload = { raw_path: path.join(outputs, 'completed.mp4'), journal_path: path.join(outputs, 'artifact.muse.json'), timeout_s: 4 };
  await fs.writeFile(artifactPayload.journal_path, JSON.stringify({ task_id: resultToken, chat_url: artifactPage.url(), stage: 'submitted' }));
  const retrieved = await recoverDriver.recover(artifactPayload);
  assert.equal(retrieved.task_id, resultToken);
  assert.equal(await fs.readFile(artifactPayload.raw_path, 'utf8'), 'completed-video');
  const activity = await artifactPage.evaluate(() => ({ ...document.body.dataset }));
  assert.equal(activity.opens, '1'); assert.equal(activity.downloads, '1'); assert.equal(activity.sends, undefined);
  assert.equal(JSON.parse(await fs.readFile(artifactPayload.journal_path, 'utf8')).stage, 'downloaded');
  console.log('PASS filename in a separate reply opens only its matching result card and downloads from the viewer, without resending');

  await artifactPage.setContent(`<section><p>视频做好了，文件名 ${resultToken}.mp4。</p><div><video src="blob:https://muse.ai/opaque-result"></video></div></section>`);
  assert.equal((await mediaLinks(artifactPage, resultToken)).length, 1);
  await artifactPage.locator('section').evaluate((el, token) => {
    const a = document.createElement('a'); a.href = 'blob:https://muse.ai/opaque-result'; a.download = `${token}.mp4`; a.textContent = `${token}.mp4`; el.append(a);
  }, resultToken);
  assert.equal((await mediaLinks(artifactPage, resultToken)).length, 1);
  console.log('PASS adjacent response filenames associate opaque video sources and duplicate DOM representations count as one result');
  await artifactPage.setContent(`<main><div>镜序画布独立视频任务 ${resultToken}。建议 ${resultToken}.mp4</div>
    <div><p>视频做好了，文件名 ${resultToken}.mp4。</p></div><div><img width="200" height="160"></div></main>`);
  assert.ok(await resultArtifact(artifactPage, resultToken));
  console.log('PASS result cards in a following reply bubble match without crossing the original request');
  await artifactPage.setContent(`<main><div>镜序画布独立视频任务 ${resultToken}。建议 ${resultToken}.mp4</div>
    <section><p>视频做好了，文件名 <strong>${resultToken.slice(0, 10)}</strong><span>${resultToken.slice(10)}</span><span>.mp4</span>。</p><img id="split-result" width="200" height="160"></section>
    <section><p>视频做好了，文件名 muse_ffffffffffff.mp4。</p><img width="200" height="160"></section></main>`);
  const splitResult = await resultArtifact(artifactPage, resultToken);
  assert.equal(await splitResult.getAttribute('id'), 'split-result');
  await artifactPage.setContent(`<main><div>镜序画布独立视频任务 <strong>${resultToken}</strong><span>.mp4</span><img width="200" height="160"></div></main>`);
  assert.equal(await resultArtifact(artifactPage, resultToken), null);
  console.log('PASS split rich-text filenames match the assistant result without importing the request image or another task');
  await artifactPage.setContent(`<main>
    <section><p>视频做好了，文件名 muse_ffffffffffff.mp4。</p><img width="200" height="160"><button title="Download video">旧下载</button></section>
    <section><p>视频做好了，文件名 <b>${resultToken}</b><span>.mp4</span>。</p><div id="reply"><img id="thumbnail" width="200" height="160"><div><button title="React">表情</button><button title="Reply">回复</button><button title="下载" id="reply-download">↓</button></div></div></section>
    <textarea></textarea></main><script>
      document.querySelector('#thumbnail').onclick = () => document.body.dataset.opened = '1';
      document.querySelector('#reply-download').onclick = () => {
        document.body.dataset.downloads = Number(document.body.dataset.downloads || 0) + 1;
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['direct-reply-video'], {type:'video/mp4'}));
        a.download = '${resultToken}.mp4'; a.click();
      };
    </script>`);
  await fs.writeFile(artifactPayload.journal_path, JSON.stringify({ task_id: resultToken, chat_url: artifactPage.url(), stage: 'submitted' }));
  await recoverDriver.recover(artifactPayload);
  assert.equal(await fs.readFile(artifactPayload.raw_path, 'utf8'), 'direct-reply-video');
  assert.equal(await artifactPage.getAttribute('body', 'data-downloads'), '1');
  assert.equal(await artifactPage.getAttribute('body', 'data-opened'), null);
  console.log('PASS the matching reply download action retrieves the file without opening the thumbnail or clicking another task');
  await artifactPage.setContent(`<style>.result{width:240px}.result img{width:200px;height:160px}.result button{display:none;width:24px;height:24px}.result:hover button{display:block}</style><main>
    <section>镜序画布独立视频任务 ${resultToken}。建议 ${resultToken}.mp4<img width="200" height="160"></section>
    <section><p>任务 muse_ffffffffffff 已完成，视频如下。</p><img width="200" height="160"><button title="下载" onclick="document.body.dataset.wrong='1'">旧文件</button></section>
    <section><p>任务 <b>${resultToken}</b> 已完成，视频已按本条消息和随附素材独立生成。</p><p>可下载的 MP4 文件如下，文件名包含任务编号。</p>
      <div class="result"><img id="completed-result"><button id="completed-download"><svg width="24" height="24" viewBox="0 0 24 24"><path d="M12 3v12 M7 10l5 5 5-5 M5 17v4h14v-4"/></svg></button></div></section>
    <aside><p>生成视频 ${resultToken}</p><p>生成并保存了16:9的${resultToken}.mp4视频</p></aside><textarea></textarea></main><script>
      document.querySelector('#completed-download').onclick=()=>{
        document.body.dataset.downloads=Number(document.body.dataset.downloads||0)+1;
        const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['completed-bare-token-video'],{type:'video/mp4'}));
        a.download='random-download-uuid';a.click();
      };
    </script>`);
  assert.equal(await (await resultArtifact(artifactPage,resultToken)).getAttribute('id'),'completed-result');
  await fs.writeFile(artifactPayload.journal_path, JSON.stringify({task_id:resultToken,chat_url:artifactPage.url(),stage:'submitted'}));
  await recoverDriver.recover(artifactPayload);
  assert.equal(await fs.readFile(artifactPayload.raw_path,'utf8'),'completed-bare-token-video');
  assert.equal(await artifactPage.getAttribute('body','data-downloads'),'1');
  assert.equal(await artifactPage.getAttribute('body','data-wrong'),null);
  assert.equal(await artifactPage.getAttribute('body','data-sends'),null);
  await artifactPage.setContent(`<main><section><p>任务 ${resultToken} 已完成，视频可下载。</p><video src="blob:https://muse.ai/opaque-bare-result"></video></section></main>`);
  assert.deepEqual(await mediaLinks(artifactPage,resultToken),[{url:'blob:https://muse.ai/opaque-bare-result',name:`${resultToken}.mp4`}]);
  for (const message of [`任务 ${resultToken} 视频正在生成。`,`任务 ${resultToken} 视频尚未完成。`,`任务 ${resultToken} 视频生成失败，未生成可下载文件。`]) {
    await artifactPage.setContent(`<section><p>${message}</p><img width="200" height="160"></section>`);
    assert.equal(await resultArtifact(artifactPage,resultToken),null);
  }
  await artifactPage.setContent(`<main><section><p>任务 ${resultToken} 已完成，视频可下载。</p><img width="200" height="160"></section><section><p>任务 ${resultToken} 已完成，视频如下。</p><img width="200" height="160"></section></main>`);
  await assert.rejects(resultArtifact(artifactPage,resultToken),/多个同编号/);
  console.log('PASS completed task-ID replies without an inline filename retrieve the matching hover download, reject incomplete replies and preserve ambiguity protection');
  await artifactContext.close();

  const shadowContext = await browser.newContext({ acceptDownloads: true });
  await shadowContext.route('**/*', (route) => route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<div id="app"></div>' }));
  const shadowPage = await shadowContext.newPage();
  await shadowPage.goto('https://muse.ai/');
  await shadowPage.evaluate(() => {
    const root = document.querySelector('#app').attachShadow({mode:'open'});
    root.innerHTML = `<style>.compose {position:relative;width:640px;height:240px}textarea{width:550px;height:170px}.send{position:absolute;right:10px;bottom:10px;width:40px;height:40px}</style>
      <div id="history"><p>视频做好了，文件名 muse_ffffffffffff.mp4。</p><video src="blob:https://muse.ai/old"></video></div>
      <div class="compose"><textarea placeholder="消息"></textarea><button class="send"><svg width="24" height="24"><path d="M12 19V5 M5 12l7-7 7 7"/></svg></button></div>`;
    root.querySelector('button').onclick = () => {
      document.body.dataset.sends = Number(document.body.dataset.sends || 0) + 1;
      const input = root.querySelector('textarea'), message = input.value, token = message.match(/muse_[a-f0-9]{12}/)[0];
      input.value = '';
      const posted = document.createElement('p'); posted.textContent = message; root.querySelector('#history').append(posted);
      const host = document.createElement('muse-result'); root.querySelector('#history').append(host);
      const card = host.attachShadow({mode:'open'});
      card.innerHTML = `<style>.thumbnail {width:200px;height:160px;background-image:linear-gradient(blue,green)}.action {display:inline-block;width:24px;height:24px;cursor:pointer}</style>
        <section><div id="name"></div><div class="thumbnail"></div>
          <div class="action" id="upload"><svg viewBox="0 0 24 24" width="24" height="24"><path d="M12 15V3 M7 8l5-5 5 5 M5 17v4h14v-4"/></svg></div>
          <div class="action" id="reply"><svg viewBox="0 0 24 24" width="24" height="24"><path d="M9 10l-5 5 5 5 M4 15h14"/></svg></div>
          <div class="action" id="download"><svg viewBox="0 0 24 24" width="24" height="24"><path d="M12 3v12 M7 10l5 5 5-5 M5 17v4h14v-4"/></svg></div>
        </section>`;
      let target = card.querySelector('#name');
      for (let i = 0; i < 12; i++) { const div = document.createElement('div'); target.append(div); target = div; }
      target.innerHTML = '视频做好了，文件名 <b>' + token.slice(0, 10) + '</b><span>' + token.slice(10) + '</span><span>.mp4</span>。';
      card.querySelector('#upload').onclick = card.querySelector('#reply').onclick = () => document.body.dataset.wrong = '1';
      card.querySelector('#download').onclick = () => {
        document.body.dataset.downloads = Number(document.body.dataset.downloads || 0) + 1;
        const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob(['automatic-shadow-video'], {type:'video/mp4'}));
        link.download = '40d3fec5-475d-44b0-9ba6-2eafa9b70e3a'; link.click();
      };
    };
  });
  const shadowDriver = new MuseBrowser(root, path.join(root, 'shadow-downloads'));
  shadowDriver.context = shadowContext; shadowDriver.page = shadowPage;
  await shadowDriver.connect();
  const shadowPayload = { ...payload, images: [], raw_path: path.join(outputs, 'shadow.mp4'), journal_path: path.join(outputs, 'shadow.muse.json') };
  const shadowResult = await shadowDriver.generate(shadowPayload);
  assert.equal(await fs.readFile(shadowPayload.raw_path, 'utf8'), 'automatic-shadow-video');
  assert.equal(await shadowPage.getAttribute('body', 'data-sends'), '1');
  assert.equal(await shadowPage.getAttribute('body', 'data-downloads'), '1');
  assert.equal(await shadowPage.getAttribute('body', 'data-wrong'), null);
  assert.equal(JSON.parse(await fs.readFile(shadowPayload.journal_path, 'utf8')).stage, 'downloaded');
  assert.ok(await resultArtifact(shadowPage, shadowResult.task_id));
  await shadowPage.locator('textarea').fill(generationMessage(shadowPayload, shadowResult.task_id));
  await fs.writeFile(path.join(outputs, 'shadow-pending.muse.json'), JSON.stringify({task_id:shadowResult.task_id,chat_url:shadowPage.url(),stage:'submitting',draft_text:generationMessage(shadowPayload,shadowResult.task_id)}));
  await assert.rejects(shadowDriver.generate({...shadowPayload,journal_path:path.join(outputs,'shadow-retry.muse.json')}), /已出现在聊天/);
  assert.equal(await shadowPage.getAttribute('body', 'data-sends'), '1');
  console.log('PASS one-click submission finds deeply nested web-component replies and CSS previews, clicks only the unlabeled download SVG, and captures a randomly named video without manual actions or duplicate sends');
  await shadowContext.close();

  const popupContext = await browser.newContext({ acceptDownloads: true });
  await popupContext.route('**/*', (route) => route.fulfill({ contentType: 'text/html; charset=utf-8', body:
    route.request().url().endsWith('/owned-download') ? `<script>
      setTimeout(() => {
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['popup-video'], {type:'video/mp4'}));
        a.download = 'random-popup-id'; a.click();
      }, 200);
    </script>` : `<section><p>视频做好了，文件名 ${resultToken}.mp4。</p>
      <button title="Download video" onclick="window.open('/owned-download')">下载</button></section><textarea></textarea>` }));
  const popupPage = await popupContext.newPage();
  await popupPage.goto('https://muse.ai/');
  const unrelatedPage = await popupContext.newPage();
  await unrelatedPage.goto('https://muse.ai/');
  const popupDriver = new MuseBrowser(root);
  popupDriver.context = popupContext; popupDriver.page = popupPage;
  await popupDriver.connect();
  const popupPayload = { raw_path: path.join(outputs, 'popup.mp4'), journal_path: path.join(outputs, 'popup.muse.json'), timeout_s: 4 };
  await fs.writeFile(popupPayload.journal_path, JSON.stringify({ task_id: resultToken, chat_url: popupPage.url(), stage: 'submitted' }));
  const listenersBefore = popupContext.listenerCount('page');
  await unrelatedPage.evaluate(() => {
    setTimeout(() => {
      const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['unrelated-file'], {type:'video/mp4'}));
      a.download = 'unrelated.mp4'; a.click();
    }, 50);
  });
  await popupDriver.recover(popupPayload);
  assert.equal(await fs.readFile(popupPayload.raw_path, 'utf8'), 'popup-video');
  assert.equal(popupContext.listenerCount('page'), listenersBefore);
  assert.equal(popupPage.listenerCount('download'), 0);
  for (const page of popupContext.pages()) assert.equal(page.listenerCount('download'), 0);
  assert.equal(JSON.parse(await fs.readFile(popupPayload.journal_path, 'utf8')).stage, 'downloaded');
  console.log('PASS a reply download captures its own popup file, ignores downloads from another tab, and removes all temporary listeners');
  await popupContext.close();

  const interruptedContext = await browser.newContext({ acceptDownloads: true });
  const interruptedPage = await interruptedContext.newPage();
  await interruptedPage.setContent(`<button>下载</button><script>document.querySelector('button').onclick = () => {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['video'], {type:'video/mp4'}));
    a.download = 'video.mp4'; a.click();
  };</script>`);
  const interruptedDriver = new MuseBrowser(root);
  interruptedDriver.context = interruptedContext; interruptedDriver.page = interruptedPage;
  interruptedContext.on('close', () => { interruptedDriver.context = null; interruptedDriver.page = null; });
  const transferInterrupted = interruptedPage.waitForEvent('download');
  const closingButton = { click: async () => {
    await interruptedPage.locator('button').click(); await transferInterrupted; await interruptedContext.close();
  } };
  await assert.rejects(interruptedDriver.downloadControl(closingButton, path.join(outputs, 'interrupted.mp4')), /下载期间 Muse 窗口已关闭/);
  assert.equal(interruptedContext.listenerCount('page'), 0);
  assert.equal(interruptedPage.listenerCount('download'), 0);
  assert.equal(interruptedPage.listenerCount('close'), 0);
  const abandonedContext = await browser.newContext();
  const abandonedPage = await abandonedContext.newPage();
  interruptedDriver.context = abandonedContext; interruptedDriver.page = abandonedPage;
  const waitingTransfer = interruptedDriver.captureDownload(abandonedPage, 30000);
  await abandonedPage.close();
  assert.equal(await waitingTransfer.promise, null);
  interruptedDriver.context = null;
  waitingTransfer.dispose();
  assert.equal(abandonedContext.listenerCount('page'), 0);
  await abandonedContext.close();
  console.log('PASS closing Muse during download reports a recoverable interruption, releases waiting transfers immediately, and never masks the error with null cleanup');

  const savedDirectory = path.join(root, 'saved-downloads');
  await fs.mkdir(savedDirectory);
  const downloadContext = await chromium.launchPersistentContext(path.join(root, 'download-test-profile'), {
    headless: true, acceptDownloads: true, downloadsPath: savedDirectory,
  });
  const downloadPage = downloadContext.pages()[0];
  const downloadDriver = new MuseBrowser(root, savedDirectory);
  downloadDriver.context = downloadContext; downloadDriver.page = downloadPage;
  downloadDriver.watchDownloads(downloadPage);
  downloadDriver.watchDownloads(downloadPage);
  await fs.writeFile(path.join(savedDirectory, `${resultToken}.mp4`), 'existing-file');
  const mp4Bytes = Buffer.from('000000186674797069736f6d0000000069736f6d6d703432', 'hex');
  await downloadPage.setContent(`<button id="download">下载</button><script>
    document.querySelector('button').onclick = () => {
      const bytes = new Uint8Array(${JSON.stringify([...mp4Bytes])});
      const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([bytes], {type:'video/mp4'}));
      a.download = '${resultToken}.mp4'; a.click();
    };
  </script>`);
  const transfer = downloadPage.waitForEvent('download');
  await downloadPage.locator('button').click();
  await (await transfer).path();
  await Promise.all([...downloadDriver.downloadsPending]);
  assert.equal((await fs.readdir(savedDirectory)).length, 2);
  const projectRaw = path.join(outputs, 'auto-project.mp4');
  assert.equal(await downloadDriver.downloadControl(downloadPage.locator('button'), projectRaw), true);
  await Promise.all([...downloadDriver.downloadsPending]);
  assert.deepEqual(await fs.readFile(projectRaw), mp4Bytes);
  assert.equal((await fs.readdir(savedDirectory)).length, 2);
  await downloadDriver.close();
  assert.equal(await fs.readFile(path.join(savedDirectory, `${resultToken}.mp4`), 'utf8'), 'existing-file');
  assert.deepEqual(await fs.readFile(path.join(savedDirectory, `${resultToken} (1).mp4`)), mp4Bytes);
  assert.equal(downloadDriver.lastDownload, `${resultToken} (1).mp4`);
  assert.equal(downloadDriver.downloadError, '');
  assert.equal((await fs.readdir(savedDirectory)).length, 2);
  console.log('PASS manual downloads keep a named copy after the browser closes, preserving existing files without duplicate listeners');
} finally {
  await browser.close();
  const target = path.resolve(root), parent = path.resolve(os.tmpdir());
  if (path.dirname(target) !== parent || !path.basename(target).startsWith('frameflow-muse-test-')) throw new Error('Unsafe test cleanup target');
  await fs.rm(target, { recursive: true, force: true });
}

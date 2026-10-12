// Read only public, rendered UI. Open web-component roots are part of that UI.
// Keep the helpers self-contained so Playwright can serialize them into a page.
function publicDom() {
  const parent = (el) => el.parentElement || el.getRootNode()?.host || null;
  const roots = (root = document) => {
    const result = [root], seen = new Set(result);
    for (let i = 0; i < result.length; i++) {
      const scope = result[i];
      for (const el of [scope, ...scope.querySelectorAll('*')]) {
        if (el.shadowRoot && !seen.has(el.shadowRoot)) { result.push(el.shadowRoot); seen.add(el.shadowRoot); }
      }
    }
    return result;
  };
  const all = (selector, root = document, includeRoot = false) => [...new Set(roots(root).flatMap((scope) => [
    ...(includeRoot && scope.matches?.(selector) ? [scope] : []), ...scope.querySelectorAll(selector),
  ]))];
  const text = (scope) => roots(scope).map((root) => root.textContent || '').join(' ');
  const visible = (el) => {
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) return false;
    for (let scope = el; scope; scope = parent(scope)) {
      const css = getComputedStyle(scope);
      if (css.display === 'none' || css.visibility === 'hidden' || css.opacity === '0' || scope.getAttribute('aria-hidden') === 'true') return false;
    }
    return true;
  };
  const boundary = (value, token) => value.includes('镜序画布独立视频任务') || /muse_[a-f0-9]{12}/.test(value.replaceAll(token, ''));
  const resultText = (value, token) => {
    if (value.includes(`${token}.mp4`)) return true;
    // Muse can label a completed reply with the task ID while putting the
    // actual filename only in its activity sidebar. A bare ID is insufficient:
    // require an explicit completion/download statement in that same reply.
    return value.includes(token) && /视频|mp4|video/i.test(value)
      && /已完成|已生成|生成完成|生成成功|视频做好|可下载|completed|finished|ready|downloadable/i.test(value)
      && !/未完成|尚未完成|还未完成|未生成|尚未生成|生成失败|无法生成|not (?:completed|ready)|failed/i.test(value);
  };
  const editor = (el) => {
    for (let scope = el; scope; scope = parent(scope)) if (scope.matches('textarea, [contenteditable="true"], script, style')) return true;
    return false;
  };
  const replies = (token) => {
    const result = new Set();
    for (const root of roots()) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        if (!/muse_|mp4/i.test(walker.currentNode.textContent)) continue;
        let scope = walker.currentNode.parentElement;
        if (!scope || !visible(scope) || editor(scope)) continue;
        for (let depth = 0; scope && scope !== document.body && depth < 20; depth++, scope = parent(scope)) {
          const value = text(scope);
          if (boundary(value, token)) break;
          if (resultText(value, token)) { result.add(scope); break; }
        }
      }
    }
    return [...result];
  };
  const scopes = (start, token) => {
    const result = [];
    for (let el = start, depth = 0; el && el !== document.body && depth < 20; el = parent(el), depth++) {
      if (boundary(text(el), token)) break;
      result.push(el);
    }
    return result;
  };
  const following = (el, token) => {
    const result = [];
    for (let sibling = el.nextElementSibling, i = 0; sibling && i < 3; sibling = sibling.nextElementSibling, i++) {
      const value = text(sibling);
      if (boundary(value, token)) break;
      result.push(sibling);
      if (value.trim().length > 100) break;
    }
    return result;
  };
  const media = (scope) => all('video, img, canvas, iframe, [role="img"], svg, *', scope, true).filter((el) => {
    const box = el.getBoundingClientRect();
    if (!visible(el) || box.width < 100 || box.height < 80) return false;
    if (!el.matches('video, img, canvas, iframe, [role="img"], svg') && getComputedStyle(el).backgroundImage === 'none') return false;
    return !all('video, img, canvas, iframe, [role="img"]', el).length;
  });
  const clickable = (el) => {
    for (let node = el, depth = 0; node && depth < 5; node = parent(node), depth++) {
      if (node.matches('button, a, [role="button"], [tabindex]')) return node;
      const box = node.getBoundingClientRect();
      if (getComputedStyle(node).cursor === 'pointer' && box.width <= 72 && box.height <= 72) return node;
    }
    return el;
  };
  // Recognize a downward arrow above a tray, not an upload/share/reply icon.
  // Parse public SVG line geometry; never guess a button from screen position.
  const downloadGlyph = (svg) => {
    const width = svg.viewBox.baseVal.width || 24;
    const lines = [];
    const add = (x, y, nx, ny) => lines.push({ x, y, nx, ny });
    for (const shape of svg.querySelectorAll('line, polyline, polygon, path, rect')) {
      const tag = shape.tagName.toLowerCase();
      if (tag === 'line') add(shape.x1.baseVal.value, shape.y1.baseVal.value, shape.x2.baseVal.value, shape.y2.baseVal.value);
      else if (tag === 'polyline' || tag === 'polygon') {
        for (let i = 1; i < shape.points.numberOfItems; i++) { const a = shape.points.getItem(i - 1), b = shape.points.getItem(i); add(a.x, a.y, b.x, b.y); }
      } else if (tag === 'rect') {
        const x = shape.x.baseVal.value, y = shape.y.baseVal.value, w = shape.width.baseVal.value, h = shape.height.baseVal.value;
        add(x, y + h, x + w, y + h);
      } else {
        const parts = (shape.getAttribute('d') || '').match(/[a-z]|[-+]?(?:\d*\.\d+|\d+)(?:e[-+]?\d+)?/gi) || [];
        const sizes = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7 };
        let x = 0, y = 0, sx = 0, sy = 0, command = '', index = 0;
        while (index < parts.length) {
          if (/^[a-z]$/i.test(parts[index])) command = parts[index++];
          const upper = command.toUpperCase(), count = sizes[upper];
          if (upper === 'Z') { add(x, y, sx, sy); x = sx; y = sy; command = ''; continue; }
          if (!count || index + count > parts.length || parts.slice(index, index + count).some((part) => /^[a-z]$/i.test(part))) break;
          const values = parts.slice(index, index + count).map(Number); index += count;
          const relative = command !== upper;
          let nx = x, ny = y;
          if (upper === 'H') nx = values[0] + (relative ? x : 0);
          else if (upper === 'V') ny = values[0] + (relative ? y : 0);
          else { nx = values[count - 2] + (relative ? x : 0); ny = values[count - 1] + (relative ? y : 0); }
          if (['L', 'H', 'V'].includes(upper)) add(x, y, nx, ny);
          if (upper === 'M') { sx = nx; sy = ny; command = relative ? 'l' : 'L'; }
          x = nx; y = ny;
        }
      }
    }
    const close = (a, b) => Math.abs(a - b) < width * 0.03;
    const legs = lines.flatMap((line) => [line, { x: line.nx, y: line.ny, nx: line.x, ny: line.y }])
      .filter((line) => line.ny - line.y >= width * 0.12 && Math.abs(line.nx - line.x) >= width * 0.12);
    for (const left of legs) for (const right of legs) {
      if (!(left.x < left.nx && right.x > right.nx && close(left.nx, right.nx) && close(left.ny, right.ny))) continue;
      const tipX = left.nx, tipY = left.ny;
      if (Math.abs(tipX - width / 2) > width * 0.2 || !close(left.y, right.y)) continue;
      const shaft = lines.some((line) => close(line.x, line.nx) && Math.abs(line.x - tipX) <= width * 0.2
        && Math.min(line.y, line.ny) < left.y - width * 0.05 && Math.max(line.y, line.ny) >= left.y - width * 0.03
        && Math.max(line.y, line.ny) <= tipY + width * 0.03);
      const tray = lines.some((line) => close(line.y, line.ny) && line.y > tipY + width * 0.05 && Math.abs(line.nx - line.x) >= width * 0.2);
      if (shaft && tray) return true;
    }
    return false;
  };
  const controls = (scope) => [...new Set(all('button, a, [role="button"], [tabindex], svg', scope, true).map(clickable))].filter((el) => {
    const box = el.getBoundingClientRect();
    return visible(el) && box.width <= 100 && box.height <= 80;
  });
  const downloads = (scope) => controls(scope).filter((el) => {
    const label = [el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('data-tooltip-content'),
      el.textContent, ...all('svg, use', el, true).flatMap((svg) => [svg.getAttribute('href'), svg.getAttribute('data-icon'), svg.getAttribute('class')])].filter(Boolean).join(' ');
    if (/下载|download|保存视频/i.test(label) || el.matches('a[download]')) return true;
    // A control with an explicit different action must not be reinterpreted.
    if ([el.getAttribute('aria-label'), el.getAttribute('title'), el.textContent].some((value) => value?.trim())) return false;
    return all('svg', el, true).some(downloadGlyph);
  });
  return { all, roots, parent, text, visible, boundary, resultText, editor, replies, scopes, following, media, controls, downloads, clickable };
}

function inspect(page, reader, arg) {
  return page.evaluate(`((arg) => { const dom = (${publicDom.toString()})(); return (${reader.toString()})(arg, dom); })(${JSON.stringify(arg)})`);
}

export async function mediaLinks(page, token = '') {
  return inspect(page, (token, dom) => {
    const found = [];
    for (const link of dom.all('a[href]')) {
      const name = `${dom.text(link)} ${link.getAttribute('download') || ''} ${link.href}`;
      if (/\.mp4(?:\b|[?#])/i.test(name) && (!token || name.includes(token))) found.push({ url: link.href, name: dom.text(link).trim() });
    }
    for (const video of dom.all('video')) {
      const url = video.currentSrc || video.src || video.querySelector('source')?.src || '';
      if (!url) continue;
      const direct = `${url} ${video.getAttribute('aria-label') || ''}`;
      if (!token || direct.includes(token) || dom.scopes(dom.parent(video), token).some((scope) => dom.resultText(dom.text(scope), token) && dom.all('video', scope).length === 1)) found.push({ url, name: token ? `${token}.mp4` : '' });
    }
    return [...new Map(found.map((item) => [item.url, item])).values()];
  }, token);
}

export async function resultArtifact(page, token, marker) {
  const count = await inspect(page, ({ token, marker }, dom) => {
    const found = new Set();
    for (const reply of dom.replies(token)) {
      for (const scope of dom.scopes(reply, token)) {
        const previews = dom.media(scope);
        if (previews.length > 1) break;
        if (previews.length === 1) { found.add(previews[0].closest('button, a, [role="button"]') || previews[0]); break; }
        let matched = false;
        for (const sibling of dom.following(scope, token)) {
          const next = dom.media(sibling);
          if (next.length > 1) break;
          if (next.length === 1) { found.add(next[0].closest('button, a, [role="button"]') || next[0]); matched = true; break; }
        }
        if (matched) break;
      }
    }
    if (found.size === 1) [...found][0].setAttribute('data-frameflow-result', marker);
    return found.size;
  }, { token, marker });
  if (count > 1) throw new Error('Muse 返回多个同编号成片卡片，请先确认需要取回的视频');
  return count === 1 ? page.locator(`[data-frameflow-result="${marker}"]`) : null;
}

export async function replyDownload(page, token, marker) {
  const count = await inspect(page, ({ token, marker }, dom) => {
    const found = new Set();
    for (const reply of dom.replies(token)) {
      for (const scope of dom.scopes(reply, token)) {
        const regions = [scope, ...dom.following(scope, token)];
        const matches = [...new Set(regions.flatMap(dom.downloads))];
        if (matches.length) { matches.forEach((button) => found.add(button)); break; }
      }
    }
    if (found.size === 1) [...found][0].setAttribute('data-frameflow-result-download', marker);
    return found.size;
  }, { token, marker });
  if (count > 1) throw new Error('Muse 成片旁有多个下载入口，请确认需要取回的文件');
  return count === 1 ? page.locator(`[data-frameflow-result-download="${marker}"]`) : null;
}

export async function resultDownload(page, artifact, token, marker) {
  await artifact.hover({ timeout: 10000 });
  const direct = await replyDownload(page, token, marker);
  if (direct) return direct;
  const count = await inspect(page, ({ token, marker }, dom) => {
    const media = dom.all(`[data-frameflow-result="${marker}"]`)[0];
    if (!media) return 0;
    const actions = new Set();
    for (const scope of dom.scopes(dom.parent(media), token)) for (const action of dom.controls(scope)) if (action !== media) actions.add(action);
    [...actions].forEach((action, i) => action.setAttribute('data-frameflow-result-action', `${marker}-${i}`));
    return actions.size;
  }, { token, marker: await artifact.getAttribute('data-frameflow-result') });
  const artifactMarker = await artifact.getAttribute('data-frameflow-result');
  try {
    for (let i = 0; i < count; i++) {
      const action = page.locator(`[data-frameflow-result-action="${artifactMarker}-${i}"]`);
      await action.hover({ timeout: 3000 });
      const tooltip = page.getByRole('tooltip').filter({ visible: true });
      await tooltip.waitFor({ state: 'visible', timeout: 700 }).catch(() => {});
      if (await tooltip.count() === 1 && /^(下载(?:视频|文件)?|download(?:\s+(?:video|file))?|保存视频)$/i.test((await tooltip.innerText()).trim())) {
        await action.evaluate((el, marker) => el.setAttribute('data-frameflow-result-download', marker), marker);
        return page.locator(`[data-frameflow-result-download="${marker}"]`);
      }
    }
  } finally {
    await page.locator(`[data-frameflow-result-action^="${artifactMarker}-"]`).evaluateAll((nodes) => nodes.forEach((el) => el.removeAttribute('data-frameflow-result-action')));
  }
  return null;
}

export async function taskPosted(page, token) {
  return inspect(page, (token, dom) => dom.roots().some((root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode, parent = node.parentElement;
      if (node.textContent.includes(token) && parent && dom.visible(parent) && !dom.editor(parent)) return true;
    }
    return false;
  }), token);
}

"""The keyboard bridge served on the authorized content origin and injected into HTML.

Every previewed HTML document gets :data:`RUNTIME_SCRIPT` inserted by the content
host (see :func:`inject_runtime_script`), so keyboard forwarding and ESC work in an
ordinary report the page author never wired. A page may still include the script
itself; injection leaves it alone when it already does.
"""

import re

#: 快照那一层不出网的判据，由平台这一侧写死并附上。写快照的接口按字节存内容，谁都能
#: 写，所以「这一份 HTML 正好带着一条 `default-src 'none'`」不能当承诺用——只有我们自
#: 己插进去的那条才算数（浏览器对多条 CSP 取交集，页面自带更松的也压不过它）。
#:
#: 只在这里写一次：页面里生成快照时由下面的脚本把它插进 head（脚本里的
#: ``__SNAPSHOT_CSP__`` 占位符在模块导入时被替掉），收到平台上时由
#: :func:`seal_snapshot` 再插一遍，两处不可能写成两条不一样的。
SNAPSHOT_CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:"
)

RUNTIME_SCRIPT = r"""(() => {
  'use strict';
  let hello = null;
  let state = null;
  let keys = [];
  let escapeWindow = false;
  let pageErrors = [];
  const PAGE_ERROR_LIMIT = 5;
  // 页面自己抛的错。宿主原来只认「应用说它 ready / failed」，页面里一个未捕获的异常它
  // 看不见：读者看到的是页面照常显示、连通链三个绿勾，点提交却没反应。收最近几条、按内容
  // 去重，报回宿主，由它摆出「N 个错误」并支持把这几条附进批注。
  function noteError(message, source, line) {
    const text = String(message == null ? '' : message).replace(/\s+/g, ' ').trim().slice(0, 300);
    if (!text) return;
    const where = String(source == null ? '' : source).slice(0, 200);
    const at = Number(line) || 0;
    if (pageErrors.some(function (item) { return item.message === text && item.source === where && item.line === at; })) {
      return;
    }
    pageErrors = pageErrors.concat([{ message: text, source: where, line: at }]).slice(-PAGE_ERROR_LIMIT);
    post({ type: 'errors', items: pageErrors });
  }
  // 捕获段挂：页面自己的 onerror 在冒泡段，不抢它的；setTimeout 里抛的、被拒的 promise
  // 各走这两条。
  window.addEventListener('error', function (event) {
    if (event && event.message) noteError(event.message, event.filename, event.lineno);
  }, true);
  window.addEventListener('unhandledrejection', function (event) {
    const reason = event && event.reason;
    noteError(reason && reason.message ? reason.message : reason, '', 0);
  });
  function publish() {
    if (!hello || !state) return;
    window.parent.postMessage({
      channel: 'cheese-preview-runtime', version: 1,
      sessionId: hello.sessionId, ...state,
    }, hello.origin);
  }
  function usableKey(key) {
    if (key === null || typeof key !== 'object') return false;
    return typeof key.id === 'string' && /^[a-z0-9_.-]{1,64}$/i.test(key.id) &&
      typeof key.code === 'string' && key.code.length <= 32 &&
      typeof key.mod === 'boolean' && typeof key.shift === 'boolean' &&
      typeof key.alt === 'boolean';
  }
  function matchKey(event) {
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      if (key.code !== event.code) continue;
      if (key.mod !== (event.metaKey || event.ctrlKey)) continue;
      if (key.shift !== event.shiftKey) continue;
      if (key.alt !== event.altKey) continue;
      return key;
    }
    return null;
  }
  function frameDocument() {
    try { return typeof document === 'undefined' ? null : document; }
    catch (e) { return null; }
  }
  // 一个元素是不是「正在输入」的地方：能打字的 input、textarea、select、
  // contenteditable、开了 designMode 的文档。打字的地方按下的裸键是文字，不是宿主
  // 的快捷键。按钮、滑块、取色这类 input 不算，在那里按的键照常是快捷键。
  const TEXT_INPUTS = ['', 'text', 'search', 'email', 'url', 'tel', 'password',
    'number', 'date', 'datetime-local', 'month', 'week', 'time'];
  function editable(node) {
    if (!node || node.nodeType !== 1 || !node.tagName) return false;
    const name = node.tagName.toLowerCase();
    if (name === 'textarea' || name === 'select') return true;
    if (name === 'input') {
      const type = String(
        (node.getAttribute && node.getAttribute('type')) || node.type || ''
      ).toLowerCase();
      return TEXT_INPUTS.indexOf(type) !== -1;
    }
    return node.isContentEditable === true;
  }
  function typing(event) {
    if (editable(event.target)) return true;
    const doc = frameDocument();
    if (doc && String(doc.designMode).toLowerCase() === 'on') return true;
    return !!(doc && editable(doc.activeElement));
  }
  // 页面自己开着的浮层（`<dialog open>`、可见的 `aria-modal`）收 ESC：这一下归页面，
  // 不归宿主——在页面自己的模态框里按 ESC，它先关自己那一层。
  function modalOpen() {
    const doc = frameDocument();
    if (!doc || !doc.querySelectorAll) return false;
    try {
      if (doc.querySelector('dialog[open]')) return true;
      const modals = doc.querySelectorAll('[aria-modal="true"]');
      for (let i = 0; i < modals.length; i++) {
        const node = modals[i];
        if (node.hidden) continue;
        if (typeof node.getBoundingClientRect !== 'function') return true;
        const rect = node.getBoundingClientRect();
        if (rect.width > 0 || rect.height > 0) return true;
      }
    } catch (e) { return false; }
    return false;
  }
  // 抓取阶段先认出可能是宿主的那个键，等这一轮任务跑完再报：在那之前页面还能对
  // 同一个事件 preventDefault，而页面自己处理掉的键不归宿主。帧只报 id，不报键名。
  // 输入法正在拼字时按下的键归输入法：ESC 是取消这次拼写，不是交回宿主。
  function composing(event) {
    return event.isComposing === true || event.keyCode === 229;
  }
  function forwardKey(event) {
    if (!hello || event.repeat || event.defaultPrevented || composing(event)) return;
    const key = matchKey(event);
    if (key === null) return;
    // 在可编辑的地方，不带 mod 也不带 alt 的键是文字不是快捷键。带修饰键的照报。
    if (!key.mod && !key.alt && typing(event)) return;
    const session = hello.sessionId;
    setTimeout(() => {
      if (event.defaultPrevented || !hello || session !== hello.sessionId) return;
      window.parent.postMessage({
        channel: 'cheese-preview-runtime', version: 1,
        sessionId: session, type: 'key', id: key.id,
      }, hello.origin);
    }, 0);
  }
  // ESC 交回宿主。它不走键表——不属于任何命令，只负责把控制权还回去。判据和
  // forwardKey 一样（页面先处理、按住不放不报），另外页面自己的浮层开着时不报，
  // 而且 500 毫秒内只报一次：连按不该让宿主连着退好几层。
  function forwardEscape(event) {
    if (!hello || event.repeat || event.defaultPrevented || composing(event)) return;
    if (modalOpen()) return;
    const session = hello.sessionId;
    setTimeout(() => {
      if (event.defaultPrevented || !hello || session !== hello.sessionId) return;
      if (escapeWindow) return;
      escapeWindow = true;
      setTimeout(() => { escapeWindow = false; }, 500);
      window.parent.postMessage({
        channel: 'cheese-preview-runtime', version: 1,
        sessionId: session, type: 'escape',
      }, hello.origin);
    }, 0);
  }
  function onKeydown(event) {
    // ESC 从键表外面走；其余照旧按表认。
    if (event.key === 'Escape') forwardEscape(event);
    else forwardKey(event);
  }
  // ---- 圈选 ----
  // 宿主打开圈选后，帧把鼠标收上来：悬停给元素描边，点一个元素、或者选一段文字，就把
  // 这一处报回宿主。页面自己收不到这一次点击（捕获阶段 preventDefault），圈选期间不会
  // 误触页面上的链接和按钮。报完自动关掉——一次圈选只指一处。
  let pick = false;
  let pickBox = null;
  let swallowClick = false;
  function pickDoc() { return frameDocument(); }
  // 走到最近一个有 id 的祖先、或文档根为止，每层带 tag；同级同名时补 nth-of-type。
  // 不能半路停在某个层数上限：停在半路的 `div > div > p` 是条相对选择器，querySelector
  // 会拿它匹配文档里第一个对上的子树，页面结构一重复就指到别处去了。走到 body 或某个
  // id 才算落在一处唯一的地方。总长封顶 256（后端字段的上限），超了从里层往外丢、留住
  // 锚点那一头，绝不截断半个层级。
  function escapeIdent(name) {
    try {
      if (window.CSS && typeof window.CSS.escape === 'function') {
        return window.CSS.escape(String(name));
      }
    } catch (e) { /* fall through to the simple escaper */ }
    return String(name).replace(/[^a-zA-Z0-9_-]/g, function (ch) { return '\\' + ch; });
  }
  function buildSelector(node) {
    const doc = pickDoc();
    const root = doc && doc.documentElement;
    const parts = [];
    let current = node;
    // 层级兜底只为防一条坏链（parentElement 成环）走不完；正常链走到 body 就停。
    let guard = 0;
    while (current && current.nodeType === 1 && current !== root && guard < 64) {
      if (current.id) { parts.unshift('#' + escapeIdent(current.id)); break; }
      const tag = current.tagName ? current.tagName.toLowerCase() : '';
      if (!tag) break;
      let part = tag;
      const parent = current.parentElement;
      if (parent && parent.children) {
        // 数同级时跳过自己插进去的那个描边框：它是圈选期间才有的，混进来会让同一
        // 处元素在圈选和不圈选时得到两个不同的选择器。
        let sameTag = 0;
        let index = 0;
        for (let i = 0; i < parent.children.length; i++) {
          const sibling = parent.children[i];
          if (sibling.hasAttribute &&
              sibling.hasAttribute('data-cheese-pick')) continue;
          if (sibling.tagName !== current.tagName) continue;
          sameTag++;
          if (sibling === current) index = sameTag;
        }
        // 只在真有同名兄弟时才补 nth-of-type，路径上不挂没用的序号。
        if (sameTag > 1 && index > 0) part += ':nth-of-type(' + index + ')';
      }
      parts.unshift(part);
      current = parent;
      guard++;
    }
    // 长度封顶：保住锚点（最外层）那一头，从里层往外丢，别把选择器截成半个层级。
    while (parts.length > 1 && parts.join(' > ').length > 256) parts.pop();
    return parts.join(' > ').slice(0, 256);
  }
  function boxOf(node) {
    try {
      const rect = node.getBoundingClientRect();
      if (rect) return { x: rect.left, y: rect.top, w: rect.width, h: rect.height };
    } catch (e) { /* no layout engine: report a zero-size box */ }
    return { x: 0, y: 0, w: 0, h: 0 };
  }
  function viewportOf() {
    return {
      w: typeof window.innerWidth === 'number' ? window.innerWidth : 0,
      h: typeof window.innerHeight === 'number' ? window.innerHeight : 0,
    };
  }
  // 文字里的空白折成一个空格：跨越换行的一段话，原样带着换行读起来不像「一句话」。
  function visibleText(node) {
    return String(node.innerText || node.textContent || '')
      .replace(/\s+/g, ' ').trim().slice(0, 500);
  }
  // 给人看的名字：这一处自己是标题就用它；里面有标题用第一个；有无障碍名字用它；
  // 都没有就往前找最近的一个标题（「在『二、时间安排』这一节里」）。找不到答空串，
  // 宿主按标签名说「一处段落 / 区块」。选择器是给芝士回到这一处的凭据，不给人读。
  const HEADING = 'h1,h2,h3,h4,h5,h6';
  function shortText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  }
  function headingBefore(node) {
    let current = node;
    let guard = 0;
    while (current && current.nodeType === 1 && guard < 400) {
      let sibling = current.previousElementSibling;
      while (sibling && guard < 400) {
        guard++;
        if (sibling.matches && sibling.matches(HEADING)) return sibling;
        const inner = sibling.querySelectorAll ? sibling.querySelectorAll(HEADING) : [];
        if (inner.length) return inner[inner.length - 1];
        sibling = sibling.previousElementSibling;
      }
      current = current.parentElement;
      guard++;
    }
    return null;
  }
  function labelOf(node) {
    if (!node || node.nodeType !== 1) return '';
    try {
      if (node.matches && node.matches(HEADING)) return shortText(node.textContent);
      const own = node.getAttribute && (node.getAttribute('aria-label') ||
        node.getAttribute('alt') || node.getAttribute('title'));
      if (own) return shortText(own);
      const inner = node.querySelector && node.querySelector(HEADING);
      if (inner) return shortText(inner.textContent);
      // 点在标题里面（`<h2><a>…</a></h2>`）：包着它的那个标题就是名字，再往前找会把
      // 上一节的标题安到它头上。
      const enclosing = node.closest && node.closest(HEADING);
      if (enclosing) return shortText(enclosing.textContent);
      const before = headingBefore(node);
      return before ? shortText(before.textContent) : '';
    } catch (e) { return ''; }
  }
  function postPick(payload) {
    if (!hello) return;
    window.parent.postMessage({
      channel: 'cheese-preview-runtime', version: 1,
      sessionId: hello.sessionId, type: 'pick', ...payload,
    }, hello.origin);
  }
  function clearHover() {
    if (pickBox) pickBox.style.display = 'none';
  }
  function onPickMove(event) {
    if (!pick) return;
    const node = event.target && event.target.nodeType === 1 ? event.target : null;
    if (pickBox && node) {
      const box = boxOf(node);
      pickBox.style.display = 'block';
      pickBox.style.left = box.x + 'px';
      pickBox.style.top = box.y + 'px';
      pickBox.style.width = box.w + 'px';
      pickBox.style.height = box.h + 'px';
    }
  }
  function pickElement(node) {
    postPick({
      selection: false,
      selector: buildSelector(node) || 'html',
      tag: node.tagName ? node.tagName.toLowerCase() : '',
      label: labelOf(node),
      text: visibleText(node),
      prefix: '', suffix: '',
      rect: boxOf(node),
      viewport: viewportOf(),
    });
    setPick(false);
  }
  // 选中的一段：原文、两侧各 32 个字符，还有它落在哪个元素里。分辨同一句话在页面上
  // 的哪一处，靠的正是这两侧。
  function pickSelection(selection) {
    let range = null;
    try { range = selection.getRangeAt(0); } catch (e) { return false; }
    const text = String(selection.toString() || '')
      .replace(/\s+/g, ' ').trim().slice(0, 500);
    if (!text) return false;
    let node = range.commonAncestorContainer;
    if (node && node.nodeType === 3) node = node.parentElement;
    if (!node || node.nodeType !== 1) node = null;
    let prefix = '';
    let suffix = '';
    if (range.startContainer && range.startContainer.nodeType === 3) {
      const data = String(range.startContainer.data || '');
      prefix = data.slice(Math.max(0, range.startOffset - 32), range.startOffset);
    }
    if (range.endContainer && range.endContainer.nodeType === 3) {
      suffix = String(range.endContainer.data || '').slice(
        range.endOffset, range.endOffset + 32);
    }
    let rect = null;
    try { rect = range.getBoundingClientRect(); } catch (e) { rect = null; }
    postPick({
      selection: true,
      selector: node ? (buildSelector(node) || 'html') : 'html',
      tag: node && node.tagName ? node.tagName.toLowerCase() : '',
      label: labelOf(node),
      text: text,
      prefix: prefix, suffix: suffix,
      rect: rect
        ? { x: rect.left, y: rect.top, w: rect.width, h: rect.height }
        : (node ? boxOf(node) : { x: 0, y: 0, w: 0, h: 0 }),
      viewport: viewportOf(),
    });
    setPick(false);
    return true;
  }
  function selectionText() {
    try {
      if (typeof window.getSelection !== 'function') return null;
      const selection = window.getSelection();
      if (!selection || !selection.rangeCount || selection.isCollapsed) return null;
      return String(selection.toString() || '').trim();
    } catch (e) { return null; }
  }
  function onPickUp(event) {
    if (!pick) return;
    if (selectionText() === null) return;
    // 选中了就得比选之前先取下来：这一下不打开链接、不触发页面自己的点击。
    const selection = window.getSelection();
    if (pickSelection(selection)) swallowClick = true;
  }
  function onPickClick(event) {
    if (!pick) return;
    event.preventDefault();
    event.stopPropagation();
    // 选一段文字的那一下 mouseup 之后还会跟一个 click：别把它当成点元素。
    if (swallowClick) { swallowClick = false; return; }
    const node = event.target && event.target.nodeType === 1 ? event.target : null;
    if (node) pickElement(node);
  }
  function setPick(on) {
    const enable = on === true;
    const doc = pickDoc();
    if (!doc || !doc.addEventListener) { pick = false; return; }
    pick = enable;
    if (pick) {
      if (!pickBox && typeof doc.createElement === 'function') {
        pickBox = doc.createElement('div');
        pickBox.setAttribute('data-cheese-pick', '');
        pickBox.style.cssText = 'position:fixed;left:0;top:0;pointer-events:none;' +
          'z-index:2147483647;border:2px solid #3b82f6;' +
          'background:rgba(59,130,246,0.12);border-radius:2px;display:none;';
        (doc.body || doc.documentElement).appendChild(pickBox);
      }
      doc.addEventListener('mousemove', onPickMove, true);
      doc.addEventListener('mouseup', onPickUp, true);
      doc.addEventListener('click', onPickClick, true);
      if (doc.documentElement && doc.documentElement.style) {
        doc.documentElement.style.cursor = 'crosshair';
      }
    } else {
      doc.removeEventListener('mousemove', onPickMove, true);
      doc.removeEventListener('mouseup', onPickUp, true);
      doc.removeEventListener('click', onPickClick, true);
      if (doc.documentElement && doc.documentElement.style) {
        doc.documentElement.style.cursor = '';
      }
      clearHover();
      if (pickBox && pickBox.parentNode) pickBox.parentNode.removeChild(pickBox);
      pickBox = null;
      swallowClick = false;
    }
  }
  // ---- 小浏览器 ----
  // 宿主的地址栏、后退前进、离线快照都靠这一段：页面此刻在哪一页由帧自己报（应用里
  // 点链接、SPA 改路由，宿主隔着源都看不见）；后退前进、刷新、跳到一个地址由宿主发
  // 命令、帧自己执行——跨源的宿主碰不到帧的 history。
  function post(payload) {
    if (!hello) return;
    window.parent.postMessage({
      channel: 'cheese-preview-runtime', version: 1,
      sessionId: hello.sessionId, ...payload,
    }, hello.origin);
  }
  function currentHref() {
    try { return window.location ? String(window.location.href) : ''; }
    catch (e) { return ''; }
  }
  // 能不能后退前进：Navigation API 说得出就照它说，说不出就不报，由宿主自己数。
  function historyFlags() {
    try {
      const nav = window.navigation;
      if (nav && typeof nav.canGoBack === 'boolean') {
        return { canBack: nav.canGoBack, canForward: nav.canGoForward === true };
      }
    } catch (e) { /* no Navigation API */ }
    return {};
  }
  let lastHref = '';
  function publishLocation(force) {
    if (!hello) return;
    const href = currentHref();
    if (!href || (!force && href === lastHref)) return;
    lastHref = href;
    const doc = frameDocument();
    let title = '';
    try { title = String((doc && doc.title) || '').slice(0, 200); } catch (e) { title = ''; }
    post({ type: 'location', href: href.slice(0, 2048), title: title, ...historyFlags() });
  }
  function locationChanged() { setTimeout(() => publishLocation(false), 0); }
  try {
    const history = window.history;
    if (history) {
      ['pushState', 'replaceState'].forEach((name) => {
        const original = history[name];
        if (typeof original !== 'function') return;
        history[name] = function () {
          const result = original.apply(this, arguments);
          locationChanged();
          return result;
        };
      });
    }
  } catch (e) { /* a locked-down history keeps the entry address only */ }
  function runHistory(action) {
    try {
      if (action === 'back') window.history.back();
      else if (action === 'forward') window.history.forward();
      else if (action === 'reload') window.location.reload();
    } catch (e) { /* nothing to traverse */ }
  }
  // 地址栏里敲的地址只在这个源里跳：别的站点不归预览管，也不该从这里被打开。
  function navigateTo(path) {
    if (typeof path !== 'string' || path.length > 2048) return;
    try {
      const target = new URL(path, currentHref());
      if (target.origin !== new URL(currentHref()).origin) return;
      window.location.assign(target.href);
    } catch (e) { /* not an address */ }
  }
  // ---- 静态快照 ----
  // 应用睡着时宿主拿这一份顶上：此刻渲染出来的 DOM，样式内联进去，脚本拿掉，读得到的
  // 图片换成 data:。它在宿主那边放进不跑脚本、不出网的框里，所以只求「看上去一样」。
  // 上限按**编码后的字节**数算，不是字符数：平台上那一侧（`SNAPSHOT_MAX_BYTES`）也按
  // utf-8 字节判，两边得是同一把尺子。中文页面按字符算能过、按字节算过不去，差三倍。
  const SNAPSHOT_MAX = 2000000;
  const IMAGE_MAX = 400000;
  function encodedLength(text) {
    try { return new TextEncoder().encode(text).length; } catch (e) { return text.length; }
  }
  function inlineImage(img) {
    try {
      if (!img.complete || !img.naturalWidth) return '';
      const doc = frameDocument();
      const canvas = doc.createElement('canvas');
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext('2d').drawImage(img, 0, 0);
      const data = canvas.toDataURL('image/png');
      return data.length <= IMAGE_MAX ? data : '';
    } catch (e) { return ''; }
  }
  function snapshotHtml() {
    const doc = frameDocument();
    if (!doc || !doc.documentElement || typeof doc.querySelectorAll !== 'function') return '';
    const sheets = [];
    const inlined = new Set();
    try {
      const list = doc.styleSheets || [];
      for (let i = 0; i < list.length; i++) {
        try {
          const rules = list[i].cssRules;
          let text = '';
          for (let j = 0; j < rules.length; j++) text += rules[j].cssText + '\n';
          sheets.push(text);
          if (list[i].ownerNode) inlined.add(list[i].ownerNode);
        } catch (e) { /* a cross-origin sheet keeps its <link> */ }
      }
    } catch (e) { /* no CSSOM */ }
    const owners = Array.from(doc.querySelectorAll('style, link[rel~="stylesheet"]'));
    const fields = Array.from(doc.querySelectorAll('input, textarea, select'));
    const images = Array.from(doc.querySelectorAll('img'));
    const canvases = Array.from(doc.querySelectorAll('canvas'));
    const clone = doc.documentElement.cloneNode(true);
    const cloneOwners = Array.from(clone.querySelectorAll('style, link[rel~="stylesheet"]'));
    const cloneFields = Array.from(clone.querySelectorAll('input, textarea, select'));
    const cloneImages = Array.from(clone.querySelectorAll('img'));
    const cloneCanvases = Array.from(clone.querySelectorAll('canvas'));
    owners.forEach((node, i) => { if (inlined.has(node) && cloneOwners[i]) cloneOwners[i].remove(); });
    fields.forEach((node, i) => {
      const copy = cloneFields[i];
      if (!copy) return;
      if (node.tagName === 'TEXTAREA') copy.textContent = node.value;
      else if (node.tagName === 'SELECT') {
        Array.from(copy.options || []).forEach((option, k) => {
          if (node.options[k] && node.options[k].selected) option.setAttribute('selected', '');
          else option.removeAttribute('selected');
        });
      } else if (node.type === 'checkbox' || node.type === 'radio') {
        if (node.checked) copy.setAttribute('checked', ''); else copy.removeAttribute('checked');
      } else if (node.type !== 'password' && node.type !== 'file') {
        copy.setAttribute('value', node.value);
      }
    });
    images.forEach((node, i) => {
      const copy = cloneImages[i];
      if (!copy) return;
      const data = inlineImage(node);
      copy.removeAttribute('srcset');
      copy.removeAttribute('loading');
      if (data) copy.setAttribute('src', data);
    });
    canvases.forEach((node, i) => {
      const copy = cloneCanvases[i];
      if (!copy) return;
      let data = '';
      try { data = node.toDataURL('image/png'); } catch (e) { data = ''; }
      const img = doc.createElement('img');
      if (data && data.length <= IMAGE_MAX) img.setAttribute('src', data);
      img.setAttribute('style', copy.getAttribute('style') || '');
      img.setAttribute('width', String(node.width));
      img.setAttribute('height', String(node.height));
      copy.replaceWith(img);
    });
    Array.from(clone.querySelectorAll('script, noscript, iframe, object, embed, [data-cheese-pick], base, meta[http-equiv]'))
      .forEach((node) => node.remove());
    Array.from(clone.querySelectorAll('*')).forEach((node) => {
      Array.from(node.attributes).forEach((attr) => {
        if (/^on/i.test(attr.name)) node.removeAttribute(attr.name);
      });
    });
    let head = clone.querySelector('head');
    if (!head) {
      head = doc.createElement('head');
      clone.insertBefore(head, clone.firstChild);
    }
    const style = doc.createElement('style');
    style.textContent = sheets.join('\n');
    head.appendChild(style);
    // 快照不出网：外链样式、字体、没换成 data: 的图都不去取，换来的是零请求和不泄露。
    const csp = doc.createElement('meta');
    csp.setAttribute('http-equiv', 'Content-Security-Policy');
    csp.setAttribute('content', "__SNAPSHOT_CSP__");
    head.insertBefore(csp, head.firstChild);
    const html = '<!DOCTYPE html>' + clone.outerHTML;
    // 太大就留不下——但要说清是哪一种：一张空快照和「这一页太大」在面板上不是一回事。
    return encodedLength(html) <= SNAPSHOT_MAX ? html : null;
  }
  function postSnapshot() {
    // `reason` 只有「太大」这一种：拍不出（异常）和留不下（超上限）分开报，卡上照实说。
    let html = '';
    let reason = '';
    try {
      const shot = snapshotHtml();
      if (shot === null) reason = 'too-large';
      else html = shot;
    } catch (e) {
      html = '';
    }
    const doc = frameDocument();
    let title = '';
    try { title = String((doc && doc.title) || '').slice(0, 200); } catch (e) { title = ''; }
    post({
      type: 'snapshot', html: html, reason: reason,
      href: currentHref().slice(0, 2048), title: title, viewport: viewportOf(),
    });
  }
  try {
    if (window.navigation && typeof window.navigation.addEventListener === 'function') {
      window.navigation.addEventListener('navigatesuccess', locationChanged);
    }
  } catch (e) { /* no Navigation API */ }
  window.addEventListener('keydown', onKeydown, true);
  window.addEventListener('popstate', locationChanged);
  window.addEventListener('hashchange', locationChanged);
  window.addEventListener('message', (event) => {
    const data = event.data;
    if (event.source !== window.parent || event.origin !== __PLATFORM_ORIGIN__ ||
        !data || data.channel !== 'cheese-preview-runtime' ||
        data.version !== 1) return;
    if (data.type === 'hello') {
      if (typeof data.sessionId !== 'string' || data.sessionId.length > 128) return;
      hello = { sessionId: data.sessionId, origin: event.origin };
      // 键表随握手过来，最多 16 条；不认识的条目丢掉，不认识的键就不报。
      keys = Array.isArray(data.keys) ? data.keys.filter(usableKey).slice(0, 16) : [];
      publish();
      publishLocation(true);
      // 握手之前就抛出来的那些错（脚本插在文档开头，页面自己的脚本马上就跑）在这里补报：
      // `post` 那时还没有会话，报出去没人收。
      if (pageErrors.length) post({ type: 'errors', items: pageErrors });
      return;
    }
    // 圈选开关只认当前会话：换了一帧、上一个会话的迟到消息，都不算。
    if (!hello || data.sessionId !== hello.sessionId) return;
    if (data.type === 'pick-mode') setPick(data.on === true);
    else if (data.type === 'history') runHistory(data.action);
    else if (data.type === 'navigate') navigateTo(data.path);
    else if (data.type === 'snapshot') postSnapshot();
  });
  window.CheesePreviewRuntime = Object.freeze({
    ready() { state = { type: 'ready' }; publish(); },
    error(message) {
      state = { type: 'error', message: String(message).slice(0, 1000) };
      publish();
    },
  });
  // The app can load this script after the parent's navigation-load hello.
  // Request that document's existing session; this is not a readiness signal.
  window.parent.postMessage({
    channel: 'cheese-preview-runtime', version: 1, type: 'hello-request',
  }, __PLATFORM_ORIGIN__);
})();
"""

#: 脚本里那条 CSP 在这里填进去，不用各处的发脚本那一层再替换一次（`__PLATFORM_ORIGIN__`
#: 是逐个请求才知道的，这条 CSP 不是）。填完以后发出去的脚本里不再有占位符。
RUNTIME_SCRIPT = RUNTIME_SCRIPT.replace("__SNAPSHOT_CSP__", SNAPSHOT_CSP)

#: 装运行时的那几种文档：房间文件和应用的网页都按它判（`preview_host`、`preview_relay`）。
HTML_MEDIA = frozenset({"text/html", "application/xhtml+xml"})

# 插进 <head> 之后的那一行。纯 ASCII：只要页面本身的编码是 ASCII 兼容的（UTF-8、
# latin-1、GBK……），按字节插进去就不会错位——ASCII 字节在那里就是它自己。
RUNTIME_PATH = "/_cheese/runtime.js"
RUNTIME_TAG = f'<script src="{RUNTIME_PATH}"></script>'.encode()

# 这些编码里 ASCII 字节不是一个 ASCII 字符（UTF-16 一个字符两字节），按字节插进去
# 会把插入点之后整体错位。遇到就整份不插，让这一页照旧自己 opt-in。
_NON_ASCII_COMPATIBLE = ("utf-16", "utf-32", "ucs-2", "ucs-4", "utf-7", "ebcdic")
_CHARSET = re.compile(rb"charset\s*=\s*[\"']?\s*([a-z0-9_-]+)")
_HTML = re.compile(rb"<html(?=[\s/>])", re.IGNORECASE)
_HEAD = re.compile(rb"<head(?=[\s/>])", re.IGNORECASE)
_SPACE = b" \t\n\r\f"


def _open_tag_end(data: bytes, start: int) -> int | None:
    """从 `<` 起，跳过属性引号里的 `>`，返回开标签结束（含 `>`）后的下标。"""
    i = start + 1
    quote = 0
    while i < len(data):
        byte = data[i]
        if quote:
            if byte == quote:
                quote = 0
        elif byte in (0x22, 0x27):  # " '
            quote = byte
        elif byte == 0x3E:  # >
            return i + 1
        i += 1
    return None


def _skip_prolog(data: bytes, i: int) -> int | None:
    """跳过空白、注释、doctype、`<?xml ...?>`；停在第一个别的东西上。

    注释没收口时答 None：后面全是注释，插哪儿都不对。
    """
    while True:
        while i < len(data) and data[i] in _SPACE:
            i += 1
        if data.startswith(b"<!--", i):
            end = data.find(b"-->", i + 4)
            if end == -1:
                return None
            i = end + 3
        elif data.startswith(b"<!", i) or data.startswith(b"<?", i):
            end = data.find(b">", i + 2)
            if end == -1:
                return None
            i = end + 1
        else:
            return i


def _insertion_point(data: bytes) -> int | None:
    """文档开头那一段里，脚本该落的位置。

    只看文档最前面：BOM、空白、注释、doctype，然后可有可无的 `<html ...>`，再然后
    可有可无的 `<head ...>`。插在走到的那一处。不往后搜 `<head`：后面的
    `<head` 可能在 `<script>` 的字符串里、`<template>` 里、属性值里，插进去会把页面
    自己的脚本截断。省略了 html/head 标签的文档，脚本落在第一个别的东西之前，HTML
    解析器照样把它放进隐含的 head，doctype 也还在最前面，页面不会掉进怪异模式。
    """
    i = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    for tag in (_HTML, _HEAD):
        at = _skip_prolog(data, i)
        if at is None:
            return None
        i = at
        if tag.match(data, i):
            end = _open_tag_end(data, i)
            if end is None:
                return None
            i = end
    return i


def _declares_non_ascii_compatible(data: bytes) -> bool:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 BOM / UTF-32 LE BOM
        return True
    if data[:4] == b"\x00\x00\xfe\xff":  # UTF-32 BE BOM
        return True
    match = _CHARSET.search(data[:2048].lower())
    if match is None:
        return False
    return match.group(1).decode("ascii", "ignore").startswith(_NON_ASCII_COMPATIBLE)


def inject_runtime_script(data: bytes) -> bytes:
    """在 HTML 文档里插进运行时脚本；插不了就把原字节原样还回去。

    这是产品定下的那一件事：每一份被预览的 HTML 页面都装上键盘桥，普通报告里 ESC
    和快捷键才回得到宿主。插在 `<head ...>` 开标签之后；没有 head 就插在 `<html ...>`
    之后；两个都没有就插在 doctype 和开头注释之后、第一个别的东西之前（见
    `_insertion_point`）。doctype、注释、BOM 都还留在原地。

    三条不插（都宁可让这一页照旧 opt-in，也不插错）：
    - 已经引了 `/_cheese/runtime.js` 的页面不插第二次。
    - 编码不是 ASCII 兼容的（UTF-16/32、带对应 BOM、或 meta 声明了它）不插：ASCII
      字节在那套编码里不是 ASCII 字符，字节级插入会让文档从插入点起整体错位。
    - 其余情况按字节插：ASCII 兼容的编码里，插入的那一行纯 ASCII 就是它自己，所以
      解不了码（不是合法 UTF-8）也不会插错，不必先解码。
    """
    if RUNTIME_PATH.encode() in data:
        return data
    if _declares_non_ascii_compatible(data):
        return data
    at = _insertion_point(data)
    if at is None:
        return data
    return data[:at] + RUNTIME_TAG + data[at:]


_SNAPSHOT_CSP_META = (
    f'<meta http-equiv="Content-Security-Policy" content="{SNAPSHOT_CSP}">'.encode()
)


def seal_snapshot(data: bytes) -> bytes | None:
    """把一份快照 HTML 封成「不出网」的样子：自己那条 CSP 插在文档最前面。

    和装运行时用的是同一处插入点（doctype、注释、`<html>`、`<head>` 之后），所以这条
    meta 一定排在任何内容之前——CSP 只管它后面的东西，放晚了等于没放。答 None 表示这
    一份封不了（编码不是 ASCII 兼容的，按字节插会把整份弄错位），调用方该拒收而不是
    存下一份挡不住出网的快照。
    """
    if _declares_non_ascii_compatible(data):
        return None
    at = _insertion_point(data)
    if at is None:
        return None
    return data[:at] + _SNAPSHOT_CSP_META + data[at:]

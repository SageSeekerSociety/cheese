"""The keyboard bridge served on the authorized content origin and injected into HTML.

Every previewed HTML document gets :data:`RUNTIME_SCRIPT` inserted by the content
host (see :func:`inject_runtime_script`), so keyboard forwarding and ESC work in an
ordinary report the page author never wired. A page may still include the script
itself; injection leaves it alone when it already does.
"""

import re

RUNTIME_SCRIPT = r"""(() => {
  'use strict';
  let hello = null;
  let state = null;
  let keys = [];
  let escapeWindow = false;
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
  window.addEventListener('keydown', onKeydown, true);
  window.addEventListener('message', (event) => {
    const data = event.data;
    if (event.source !== window.parent || event.origin !== __PLATFORM_ORIGIN__ ||
        !data || data.channel !== 'cheese-preview-runtime' || data.version !== 1 ||
        data.type !== 'hello' || typeof data.sessionId !== 'string' ||
        data.sessionId.length > 128) return;
    hello = { sessionId: data.sessionId, origin: event.origin };
    // 键表随握手过来，最多 16 条；不认识的条目丢掉，不认识的键就不报。
    keys = Array.isArray(data.keys) ? data.keys.filter(usableKey).slice(0, 16) : [];
    publish();
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

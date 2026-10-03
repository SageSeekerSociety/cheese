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
  // 一个元素是不是「正在输入」的地方：input（按钮、勾选框、单选框除外）、textarea、
  // select、contenteditable。打字的地方按下的裸键是文字，不是宿主的快捷键。
  function editable(node) {
    if (!node || node.nodeType !== 1 || !node.tagName) return false;
    const name = node.tagName.toLowerCase();
    if (name === 'textarea' || name === 'select') return true;
    if (name === 'input') {
      const type = String(
        (node.getAttribute && node.getAttribute('type')) || node.type || ''
      ).toLowerCase();
      return type !== 'button' && type !== 'checkbox' && type !== 'radio';
    }
    return node.isContentEditable === true;
  }
  function typing(event) {
    if (editable(event.target)) return true;
    const doc = frameDocument();
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
  function forwardKey(event) {
    if (!hello || event.repeat || event.defaultPrevented) return;
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
    if (!hello || event.repeat || event.defaultPrevented) return;
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
_TAGS = {
    b"head": re.compile(rb"<head(?=[\s/>])", re.IGNORECASE),
    b"html": re.compile(rb"<html(?=[\s/>])", re.IGNORECASE),
}


def _mask_comments(data: bytes) -> bytes:
    """注释体换成空格（换行留着），标签的位置就还在原地。

    这样再找 `<head` 时不会命中注释里写着的那个，也不必把位置映射回原文。
    """
    out = bytearray(data)
    low = data.lower()
    i = 0
    while True:
        start = low.find(b"<!--", i)
        if start == -1:
            return bytes(out)
        end = low.find(b"-->", start + 4)
        stop = len(data) if end == -1 else end + 3
        for k in range(start, stop):
            if out[k] not in (0x0A, 0x0D):
                out[k] = 0x20
        if end == -1:
            return bytes(out)
        i = end + 3


def _open_tag_end(masked: bytes, start: int) -> int | None:
    """从 `<` 起，跳过属性引号里的 `>`，返回开标签结束（含 `>`）后的下标。"""
    i = start + 1
    quote = 0
    while i < len(masked):
        byte = masked[i]
        if quote:
            if byte == quote:
                quote = 0
        elif byte in (0x22, 0x27):  # " '
            quote = byte
        elif byte == 0x3E:  # >
            return i + 1
        i += 1
    return None


def _find_tag_end(masked: bytes, name: bytes) -> int | None:
    match = _TAGS[name].search(masked)
    if match is None:
        return None
    return _open_tag_end(masked, match.start())


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
    之后；两个都没有就插在最前面。doctype、注释、BOM 都还留在原地。

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
    masked = _mask_comments(data)
    head = _find_tag_end(masked, b"head")
    if head is not None:
        return data[:head] + RUNTIME_TAG + data[head:]
    html = _find_tag_end(masked, b"html")
    if html is not None:
        return data[:html] + RUNTIME_TAG + data[html:]
    return RUNTIME_TAG + data

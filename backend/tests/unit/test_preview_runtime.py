"""Run the injected bridge in a JS context; prove the injection function."""

import json
import shutil
import subprocess

import pytest

from app.api.preview_runtime import RUNTIME_SCRIPT, RUNTIME_TAG, inject_runtime_script


def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the injected JavaScript bridge")
    return node


def test_injection_lands_after_head_html_or_at_the_front():
    tag = RUNTIME_TAG
    # head 之后（带属性、大小写混着来也一样）。
    assert inject_runtime_script(b"<head><title>x</title></head>") == (
        b"<head>" + tag + b"<title>x</title></head>"
    )
    assert inject_runtime_script(b'<HEAD class="a">x') == (
        b'<HEAD class="a">' + tag + b"x"
    )
    # head 里带引号包着的 `>`，结束位置要认引号里的那一个才算对。
    assert inject_runtime_script(b'<head data-x="a>b"></head>') == (
        b'<head data-x="a>b">' + tag + b"</head>"
    )
    # 没有 head 就插在 html 之后。
    assert inject_runtime_script(b'<html lang="en"><body>x</body></html>') == (
        b'<html lang="en">' + tag + b"<body>x</body></html>"
    )
    # 两个都没有就插在最前面。
    assert inject_runtime_script(b"<p>hello</p>") == tag + b"<p>hello</p>"


def test_injection_keeps_a_tagless_document_in_standards_mode():
    # 省略了 html/head 的 HTML5 文档：脚本落在 doctype 之后。落在 doctype 之前，
    # 浏览器会把整页按怪异模式排版。
    assert inject_runtime_script(b"<!doctype html><title>x</title><p>hi") == (
        b"<!doctype html>" + RUNTIME_TAG + b"<title>x</title><p>hi"
    )


def test_injection_never_lands_inside_page_script_template_or_attribute():
    # 只认文档开头那一段的 html/head；后面写着的 `<head`（脚本字符串里、template
    # 里、属性值里）一概不碰。插进 `<script>` 字符串，注入的 `</script>` 会把页面
    # 自己的脚本截断。
    for later in (
        b'<script>var s="<head>";</script>',
        b"<template><head></head></template>",
        b"<style>/* <head> */</style>",
        b'<div title="<head>"></div>',
    ):
        document = b"<!doctype html>" + later
        assert inject_runtime_script(document) == (
            b"<!doctype html>" + RUNTIME_TAG + later
        )
    # 注释没收口：后面全是注释，不插。
    assert inject_runtime_script(b"<!-- never closed <head>") == (
        b"<!-- never closed <head>"
    )


def test_injection_keeps_doctype_comments_and_bom_in_place():
    tag = RUNTIME_TAG
    document = b"\xef\xbb\xbf<!-- a note --><!DOCTYPE html><html><head></head></html>"
    injected = inject_runtime_script(document)
    assert injected == (
        b"\xef\xbb\xbf<!-- a note --><!DOCTYPE html><html><head>"
        + tag
        + b"</head></html>"
    )
    assert injected.startswith(b"\xef\xbb\xbf")  # BOM 还在最前面
    # 注释里写着的 `<head` 不算数，插在真正的那个之后。
    assert inject_runtime_script(b"<!-- <head> fake --><head></head>") == (
        b"<!-- <head> fake --><head>" + tag + b"</head>"
    )


def test_injection_is_skipped_when_already_present_or_not_ascii_compatible():
    tag = RUNTIME_TAG
    # 已经引了脚本的页面不动。
    already = b'<head><script src="/_cheese/runtime.js"></script></head>'
    assert inject_runtime_script(already) == already
    # 不是合法 UTF-8 但 ASCII 兼容（latin-1 的 é）照样按字节插，不会错位。
    latin1 = b"<head>caf\xe9</head>"
    assert inject_runtime_script(latin1) == b"<head>" + tag + b"caf\xe9</head>"
    # UTF-16 的 BOM：ASCII 字节在那里不是 ASCII 字符，整份不插。
    utf16 = b"\xff\xfe<\x00h\x00e\x00a\x00d\x00>\x00"
    assert inject_runtime_script(utf16) == utf16
    # meta 声明了非 ASCII 兼容的编码：同样不插。
    declared = b'<meta charset="utf-16le"><head></head>'
    assert inject_runtime_script(declared) == declared


def test_bridge_caches_early_ready_and_checks_parent_origin_session():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the opt-in JavaScript bridge")
    script = RUNTIME_SCRIPT.replace(
        "__PLATFORM_ORIGIN__", json.dumps("https://platform.example")
    )
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const messages = [];
let receive;
const parent = { postMessage: (data, origin) => messages.push({data, origin}) };
const listeners = {};
const window = { parent, addEventListener: (type, handler) => {
  listeners[type] = handler;
  if (type === 'message') receive = handler;
} };
vm.runInNewContext(fs.readFileSync(0, 'utf8'), { window, setTimeout: () => {} });
assert.equal(messages.length, 1);
assert.equal(messages[0].data.type, 'hello-request');
assert.equal(messages[0].origin, 'https://platform.example');
messages.length = 0;
const hello = {
  channel: 'cheese-preview-runtime', version: 1, type: 'hello', sessionId: 'first'
};
window.CheesePreviewRuntime.ready();
assert.equal(messages.length, 0);
for (const event of [
  {source: {}, origin: 'https://platform.example', data: hello},
  {source: parent, origin: 'https://attacker.example', data: hello},
  {source: parent, origin: 'https://platform.example', data: {...hello, version: 2}},
  {source: parent, origin: 'https://platform.example',
   data: {...hello, sessionId: 'x'.repeat(129)}},
]) receive(event);
assert.equal(messages.length, 0);
receive({source: parent, origin: 'https://platform.example', data: hello});
assert.equal(messages.length, 1);
assert.equal(messages[0].data.type, 'ready');
assert.equal(messages[0].data.sessionId, 'first');
assert.equal(messages[0].origin, 'https://platform.example');
window.CheesePreviewRuntime.error('x'.repeat(1200));
assert.equal(messages[1].data.type, 'error');
assert.equal(messages[1].data.message.length, 1000);
receive({source: parent, origin: 'https://platform.example',
         data: {...hello, sessionId: 'second'}});
assert.equal(messages[2].data.sessionId, 'second');
assert.equal(messages[2].data.type, 'error');
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_late_bridge_completes_handshake_after_the_load_hello_was_missed():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the opt-in JavaScript bridge")
    script = RUNTIME_SCRIPT.replace(
        "__PLATFORM_ORIGIN__", json.dumps("https://platform.example")
    )
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const messages = [];
let receive;
// The parent's navigation-load hello preceded registration of this listener.
const parent = { postMessage(data, origin) {
  messages.push({data, origin});
  if (data.type === 'hello-request') {
    assert.equal(origin, 'https://platform.example');
    assert.equal(typeof receive, 'function');
    receive({source: parent, origin, data: {
      channel: 'cheese-preview-runtime', version: 1,
      type: 'hello', sessionId: 'loaded-document'
    }});
  }
}};
const window = { parent, addEventListener: (type, handler) => {
  if (type === 'message') receive = handler;
} };
vm.runInNewContext(fs.readFileSync(0, 'utf8'), { window, setTimeout: () => {} });
window.CheesePreviewRuntime.ready();
const ready = messages.find(message => message.data.type === 'ready');
assert.ok(ready, 'a late-loaded bridge must confirm readiness without a page reload');
assert.equal(ready.data.sessionId, 'loaded-document');
assert.equal(ready.origin, 'https://platform.example');
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_frame_reports_the_id_of_a_host_key_the_page_did_not_handle():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the opt-in JavaScript bridge")
    script = RUNTIME_SCRIPT.replace(
        "__PLATFORM_ORIGIN__", json.dumps("https://platform.example")
    )
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const messages = [];
const listeners = {};
const pending = [];
const parent = { postMessage: (data, origin) => messages.push({data, origin}) };
const window = {
  parent,
  addEventListener: (type, handler) => { listeners[type] = handler; },
};
vm.runInNewContext(fs.readFileSync(0, 'utf8'), {
  window, setTimeout: (run) => { pending.push(run); },
});
const flush = () => pending.splice(0).forEach((run) => run());
const press = (overrides) => ({
  code: 'Digit1', metaKey: true, ctrlKey: false, shiftKey: false, altKey: false,
  repeat: false, defaultPrevented: false, ...overrides,
});
listeners.message({source: parent, origin: 'https://platform.example', data: {
  channel: 'cheese-preview-runtime', version: 1, type: 'hello', sessionId: 'first',
  keys: [
    {id: 'rail.1', mod: true, shift: false, alt: false, code: 'Digit1'},
    {id: 'bad id', mod: true, shift: false, alt: false, code: 'Digit2'},
    {id: 'library.upload', mod: true, shift: true, alt: false, code: 'KeyF'},
  ],
}});
messages.length = 0;
// 表里没有的键、页面已经处理掉的、按住不放的重复事件：都不报。
listeners.keydown(press({code: 'KeyQ'}));
listeners.keydown(press({defaultPrevented: true}));
listeners.keydown(press({repeat: true}));
flush();
assert.equal(messages.length, 0);
// 报的是 id，不是键名；抓取阶段先让页面过一手，这一轮任务跑完才发出去。
listeners.keydown(press({}));
assert.equal(messages.length, 0);
flush();
assert.equal(messages.length, 1);
assert.equal(messages[0].data.type, 'key');
assert.equal(messages[0].data.id, 'rail.1');
assert.equal(messages[0].data.sessionId, 'first');
assert.equal(messages[0].origin, 'https://platform.example');
// 页面在这之间 preventDefault 了，就不报。
const handled = press({});
listeners.keydown(handled);
handled.defaultPrevented = true;
flush();
assert.equal(messages.length, 1);
// 表里第二条 id 不合法，随握手就被丢掉了：按它不报。
listeners.keydown(press({code: 'Digit2'}));
flush();
assert.equal(messages.length, 1);
// 第三条合法，报得出来。
listeners.keydown(press({code: 'KeyF', shiftKey: true}));
flush();
assert.equal(messages[1].data.id, 'library.upload');
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_bridge_forwards_escape_with_page_modal_and_coalescing_rules():
    node = _node()
    script = RUNTIME_SCRIPT.replace(
        "__PLATFORM_ORIGIN__", json.dumps("https://platform.example")
    )
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const messages = [];
const listeners = {};
const timers = [];
const modal = { open: false, aria: [] };
const parent = { postMessage: (data, origin) => messages.push({data, origin}) };
const doc = {
  activeElement: null,
  querySelector: (sel) => (modal.open && sel === 'dialog[open]' ? {} : null),
  querySelectorAll: (sel) => (sel === '[aria-modal="true"]' ? modal.aria : []),
};
const window = {
  parent,
  addEventListener: (type, handler) => { listeners[type] = handler; },
};
vm.runInNewContext(fs.readFileSync(0, 'utf8'), {
  window, document: doc,
  setTimeout: (run, delay) => { timers.push({ run, delay: delay || 0 }); },
});
// 到点的那一批先取下来再跑，跑的过程里新排的（比如 500ms 窗口）留到下一批。
const flush = (delay = 0) => {
  const due = timers.filter((t) => (t.delay || 0) === delay);
  due.forEach((t) => timers.splice(timers.indexOf(t), 1));
  due.forEach((t) => t.run());
};
listeners.message({ source: parent, origin: 'https://platform.example', data: {
  channel: 'cheese-preview-runtime', version: 1, type: 'hello', sessionId: 's',
  keys: [],
}});
messages.length = 0;
const esc = (over) => ({
  key: 'Escape', code: 'Escape', metaKey: false, ctrlKey: false, shiftKey: false,
  altKey: false, repeat: false, defaultPrevented: false, ...over,
});
// 一次 ESC 报一次 escape，带当前 session 和平台 origin。
listeners.keydown(esc({}));
flush();
assert.equal(messages.length, 1);
assert.equal(messages[0].data.type, 'escape');
assert.equal(messages[0].data.sessionId, 's');
assert.equal(messages[0].origin, 'https://platform.example');
// 500ms 窗口内连按不报；窗口过期后再按才报。
listeners.keydown(esc({}));
flush();
assert.equal(messages.length, 1);
flush(500);
listeners.keydown(esc({}));
flush();
assert.equal(messages.length, 2);
flush(500);
// 按住不放的重复事件不报。
listeners.keydown(esc({ repeat: true }));
flush();
assert.equal(messages.length, 2);
// 页面自己的 <dialog open> 开着时不报。
modal.open = true;
listeners.keydown(esc({}));
flush();
assert.equal(messages.length, 2);
modal.open = false;
// 可见的 aria-modal 元素也一样。
modal.aria = [
  { hidden: false, getBoundingClientRect: () => ({ width: 10, height: 10 }) },
];
listeners.keydown(esc({}));
flush();
assert.equal(messages.length, 2);
modal.aria = [];
// 页面在这一轮任务里 preventDefault 了，就不报。
const handled = esc({});
listeners.keydown(handled);
handled.defaultPrevented = true;
flush();
assert.equal(messages.length, 2);
// 输入法拼字时按的 ESC 是取消拼写，不报。
listeners.keydown(esc({ isComposing: true }));
flush();
assert.equal(messages.length, 2);
listeners.keydown(esc({ keyCode: 229 }));
flush();
assert.equal(messages.length, 2);
// 通道没被前几次按下关掉：再来一次仍然报得出去。
listeners.keydown(esc({}));
flush();
assert.equal(messages.length, 3);
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_bridge_does_not_forward_plain_keys_while_typing():
    node = _node()
    script = RUNTIME_SCRIPT.replace(
        "__PLATFORM_ORIGIN__", json.dumps("https://platform.example")
    )
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const messages = [];
const listeners = {};
const timers = [];
const parent = { postMessage: (data, origin) => messages.push({data, origin}) };
const doc = { activeElement: null };
const window = {
  parent,
  addEventListener: (type, handler) => { listeners[type] = handler; },
};
vm.runInNewContext(fs.readFileSync(0, 'utf8'), {
  window, document: doc,
  setTimeout: (run) => { timers.push({ run, delay: 0 }); },
});
const flush = () => { timers.splice(0).forEach((t) => t.run()); };
listeners.message({ source: parent, origin: 'https://platform.example', data: {
  channel: 'cheese-preview-runtime', version: 1, type: 'hello', sessionId: 's',
  keys: [
    {id: 'go', mod: true, shift: false, alt: false, code: 'Digit1'},
    {id: 'type-m', mod: false, shift: false, alt: false, code: 'KeyM'},
    {id: 'mod-m', mod: true, shift: false, alt: false, code: 'KeyM'},
  ],
}});
messages.length = 0;
const key = (over) => ({
  key: 'm', code: 'KeyM', metaKey: false, ctrlKey: false, shiftKey: false,
  altKey: false, repeat: false, defaultPrevented: false, ...over,
});
const input = (type) => ({
  nodeType: 1,
  tagName: 'INPUT',
  getAttribute: (name) => (name === 'type' ? type : null),
});
// 在输入框里打 m：裸键是文字，不报。
listeners.keydown(key({ target: input('text') }));
flush();
assert.equal(messages.length, 0);
// 按钮、勾选框、滑块、提交这类 input 不是「在打字」，裸键照报。
const notTyping = ['button', 'checkbox', 'radio', 'range', 'submit', 'color', 'file'];
for (const type of notTyping) {
  listeners.keydown(key({ target: input(type) }));
  flush();
  assert.equal(messages.length, 1, type);
  assert.equal(messages[0].data.id, 'type-m');
  messages.length = 0;
}
// 带 mod 的键在输入框里也报——那是快捷键，不是文字。
listeners.keydown(key({ target: input('text'), metaKey: true }));
flush();
assert.equal(messages.length, 1);
assert.equal(messages[0].data.id, 'mod-m');
messages.length = 0;
// textarea、select、contenteditable 一样是「在打字」。
for (const node of [
  { nodeType: 1, tagName: 'TEXTAREA' },
  { nodeType: 1, tagName: 'SELECT' },
  { nodeType: 1, tagName: 'DIV', isContentEditable: true },
]) {
  listeners.keydown(key({ target: node }));
  flush();
  assert.equal(messages.length, 0);
}
// 不写 type、写 search 的 input 也是在打字。
for (const type of [null, 'search']) {
  listeners.keydown(key({ target: input(type) }));
  flush();
  assert.equal(messages.length, 0, String(type));
}
// 整份文档开了 designMode 也是在打字。
doc.designMode = 'on';
listeners.keydown(key({ target: { nodeType: 1, tagName: 'BODY' } }));
flush();
assert.equal(messages.length, 0);
doc.designMode = 'off';
// 输入法拼字时按的键不报，带 mod 的也一样。
listeners.keydown(key({ target: { nodeType: 1, tagName: 'BODY' }, isComposing: true }));
listeners.keydown(key({ metaKey: true, keyCode: 229 }));
flush();
assert.equal(messages.length, 0);
// 焦点落在帧里的可编辑元素上（事件自己没带 target）也要挡住。
doc.activeElement = { nodeType: 1, tagName: 'TEXTAREA' };
listeners.keydown(key({}));
flush();
assert.equal(messages.length, 0);
// 不在打字的地方，裸键照报。
doc.activeElement = null;
listeners.keydown(key({ target: { nodeType: 1, tagName: 'BODY' } }));
flush();
assert.equal(messages.length, 1);
assert.equal(messages[0].data.id, 'type-m');
// ESC 不受可编辑过滤影响：只要页面没处理过，还是报出去。
messages.length = 0;
listeners.keydown({
  key: 'Escape', code: 'Escape', metaKey: false, ctrlKey: false, shiftKey: false,
  altKey: false, repeat: false, defaultPrevented: false, target: input('text'),
});
flush();
assert.equal(messages.length, 1);
assert.equal(messages[0].data.type, 'escape');
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_bridge_picks_an_element_or_a_selection_and_reports_it():
    """圈选：宿主打开后，帧报的是选择器、标签、文字和位置，报完自动关掉。

    这一处报错读不出来（比如把 nth-of-type 算成兄弟总数）就会指错地方，而宿主
    只当它是对的那一处发出去——所以定位、文字归一化、以及「一次只指一处」都要在
    真正的 JS 里跑一遍。
    """
    node = _node()
    script = RUNTIME_SCRIPT.replace(
        "__PLATFORM_ORIGIN__", json.dumps("https://platform.example")
    )
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const messages = [];
const windowListeners = {};
const docListeners = {};
const parent = { postMessage: (data, origin) => messages.push({data, origin}) };
function el(tag, opts) {
  const node = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    id: (opts && opts.id) || '',
    innerText: (opts && opts.text) || '',
    parentElement: (opts && opts.parent) || null,
    children: (opts && opts.children) || [],
    previousElementSibling: null,
    getBoundingClientRect: () => (opts && opts.rect) ||
      {left: 0, top: 0, width: 10, height: 20},
    appendChild(child) { child.parentNode = node; node.children.push(child); },
    removeChild(child) { child.parentNode = null; },
    style: {},
  };
  return node;
}
const root = el('html');
const body = el('body', {parent: root});
root.children = [body];
const para = el('p', {parent: body, text: '  Hello  world  ',
  rect: {left: 5, top: 6, width: 50, height: 20}});
body.children = [para];
const doc = {
  documentElement: root,
  body,
  addEventListener: (type, handler) => { docListeners[type] = handler; },
  removeEventListener: (type, handler) => {
    if (docListeners[type] === handler) delete docListeners[type];
  },
  createElement: () => ({
    style: {},
    attrs: {},
    setAttribute(name) { this.attrs[name] = ''; },
    hasAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name);
    },
    parentNode: null,
  }),
};
const selection = {
  rangeCount: 1,
  isCollapsed: false,
  toString: () => 'Hello',
  getRangeAt: () => ({
    startContainer: {nodeType: 3, data: 'xxHello'},
    startOffset: 2,
    endContainer: {nodeType: 3, data: 'worldyy'},
    endOffset: 5,
    commonAncestorContainer: {nodeType: 3, data: 'Hello', parentElement: para},
    getBoundingClientRect: () => ({left: 1, top: 2, width: 30, height: 10}),
  }),
};
const window = {
  parent,
  innerWidth: 1024,
  innerHeight: 768,
  addEventListener: (type, handler) => { windowListeners[type] = handler; },
  getSelection: () => selection,
};
vm.runInNewContext(fs.readFileSync(0, 'utf8'), {
  window, document: doc, setTimeout: () => {},
});
const receive = (data, origin) => windowListeners.message({
  source: parent, origin: origin || 'https://platform.example',
  data: {channel: 'cheese-preview-runtime', version: 1, ...data},
});
receive({type: 'hello', sessionId: 's'});
messages.length = 0;

// 还没开圈选：页面上就没挂鼠标监听，鼠标动静都不会报。
assert.equal(docListeners.mousemove, undefined);

// 换一个会话的迟到开关不算：监听器一个也不挂。
receive({type: 'pick-mode', sessionId: 'other', on: true});
assert.equal(docListeners.mousemove, undefined);

// 本会话打开：捕获阶段挂上鼠标三件事，光标变成十字。
receive({type: 'pick-mode', sessionId: 's', on: true});
assert.equal(typeof docListeners.mousemove, 'function');
assert.equal(typeof docListeners.mouseup, 'function');
assert.equal(typeof docListeners.click, 'function');
assert.equal(root.style.cursor, 'crosshair');

// 悬停给元素描边，框跟着元素的位置和大小走。
docListeners.mousemove({target: para});
const box = body.children.find((c) => c.setAttribute);
assert.ok(box, 'hovering must add a highlight box');
assert.equal(box.style.display, 'block');
assert.equal(box.style.left, '5px');
assert.equal(box.style.top, '6px');
assert.equal(box.style.width, '50px');
assert.equal(box.style.height, '20px');

// 点一个元素：报选择器、标签、归一化后的文字、矩形和视口。
let clickHandled = false;
docListeners.click({target: para,
  preventDefault() { clickHandled = true; }, stopPropagation() {}});
assert.ok(clickHandled, 'the page must not also receive this click');
assert.equal(messages.length, 1);
const picked = messages[0].data;
assert.equal(picked.type, 'pick');
assert.equal(picked.sessionId, 's');
assert.equal(messages[0].origin, 'https://platform.example');
assert.equal(picked.selection, false);
assert.equal(picked.selector, 'body > p');
assert.equal(picked.tag, 'p');
assert.equal(picked.text, 'Hello world');
assert.equal(JSON.stringify(picked.rect), JSON.stringify({x: 5, y: 6, w: 50, h: 20}));
assert.equal(JSON.stringify(picked.viewport), JSON.stringify({w: 1024, h: 768}));

// 报完自动关掉：监听器摘掉、光标还原、悬停框收走。
assert.equal(docListeners.mousemove, undefined);
assert.equal(root.style.cursor, '');
assert.equal(box.style.display, 'none');

// 再开一次，选一段文字：报 selection:true，带两侧各 32 字和选中段落所在的元素。
messages.length = 0;
receive({type: 'pick-mode', sessionId: 's', on: true});
docListeners.mouseup({target: para});
assert.equal(messages.length, 1);
const sel = messages[0].data;
assert.equal(sel.selection, true);
assert.equal(sel.selector, 'body > p');
assert.equal(sel.text, 'Hello');
assert.equal(sel.prefix, 'xx');
assert.equal(sel.suffix, 'yy');
assert.equal(JSON.stringify(sel.rect), JSON.stringify({x: 1, y: 2, w: 30, h: 10}));
// 选完那一下之后还会跟一个 click：监听器已经摘掉，不会再报一处。
assert.equal(docListeners.click, undefined);
assert.equal(messages.length, 1);
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr

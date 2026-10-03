"""Execute the actual opt-in bridge in a JS context with controlled parent events."""

import json
import shutil
import subprocess

import pytest

from app.api.preview_runtime import RUNTIME_SCRIPT


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

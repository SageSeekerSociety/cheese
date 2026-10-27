"""The bridge's half of the panel's little browser: address, history, snapshot.

The address bar cannot read a cross-origin frame's location, and the host cannot
touch its history; the frame reports the one and runs the other on command. The
snapshot is what the panel shows once the app's machine has gone to sleep.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.api.preview_runtime import RUNTIME_SCRIPT

ORIGIN = "https://platform.example"
SCRIPT = RUNTIME_SCRIPT.replace("__PLATFORM_ORIGIN__", json.dumps(ORIGIN))

# A fake window just big enough for the bridge: a location the page can move,
# a history the bridge wraps, and listeners we can fire by hand.
FAKE_WINDOW = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const messages = [];
const listeners = {};
const parent = { postMessage: (data, origin) => messages.push({data, origin}) };
const calls = [];
const location = {
  href: 'https://app.example/signup',
  assign(url) { calls.push(['assign', url]); },
  reload() { calls.push(['reload']); },
};
const history = {
  pushState(state, title, url) { location.href = new URL(url, location.href).href; },
  replaceState(state, title, url) { location.href = new URL(url, location.href).href; },
  back() { calls.push(['back']); },
  forward() { calls.push(['forward']); },
};
const timers = [];
const window = {
  parent, location, history,
  navigation: { canGoBack: true, canGoForward: false, addEventListener() {} },
  addEventListener: (type, handler) => { listeners[type] = handler; },
};
const document = { title: '报名' };
vm.runInNewContext(fs.readFileSync(0, 'utf8'), {
  window, document, URL, setTimeout: (fn) => timers.push(fn),
});
const flush = () => { while (timers.length) timers.shift()(); };
const send = (data, origin = 'https://platform.example') => listeners.message({
  source: parent, origin,
  data: {channel: 'cheese-preview-runtime', version: 1, ...data},
});
// The bridge runs in its own realm: compare plain data, not its objects.
const located = () => JSON.parse(JSON.stringify(
  messages.filter(m => m.data.type === 'location').map(m => m.data)));
const plain = (value) => JSON.parse(JSON.stringify(value));
"""


def _run(body: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the injected JavaScript bridge")
    result = subprocess.run(
        [node, "-e", FAKE_WINDOW + body],
        input=SCRIPT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_the_frame_reports_where_it_is_on_hello_and_after_each_move():
    _run(r"""
assert.equal(located().length, 0, 'nothing is reported before a session exists');
send({type: 'hello', sessionId: 's'});
assert.deepEqual(located(), [{
  channel: 'cheese-preview-runtime', version: 1, sessionId: 's', type: 'location',
  href: 'https://app.example/signup', title: '报名', canBack: true, canForward: false,
}]);
// SPA 改路由：pushState 之后报一次新地址。
window.history.pushState({}, '', '/signup?step=2');
flush();
assert.equal(located().at(-1).href, 'https://app.example/signup?step=2');
// 地址没变的事件不重报。
const before = located().length;
listeners.popstate({});
flush();
assert.equal(located().length, before);
location.href = 'https://app.example/faq';
listeners.hashchange({});
flush();
assert.equal(located().at(-1).href, 'https://app.example/faq');
// 换一次握手（页面重载后新会话）要把当前地址再报一遍，哪怕没变。
send({type: 'hello', sessionId: 't'});
assert.equal(located().at(-1).sessionId, 't');
assert.equal(located().at(-1).href, 'https://app.example/faq');
""")


def test_history_and_navigation_commands_need_the_current_session_and_origin():
    _run(r"""
send({type: 'hello', sessionId: 's'});
send({type: 'history', sessionId: 'old', action: 'back'});
send({type: 'history', sessionId: 's', action: 'back'}, 'https://attacker.example');
assert.deepEqual(calls, []);
send({type: 'history', sessionId: 's', action: 'back'});
send({type: 'history', sessionId: 's', action: 'forward'});
send({type: 'history', sessionId: 's', action: 'reload'});
send({type: 'history', sessionId: 's', action: 'go'});
assert.deepEqual(plain(calls), [['back'], ['forward'], ['reload']]);
calls.length = 0;
send({type: 'navigate', sessionId: 's', path: '/faq?x=1'});
send({type: 'navigate', sessionId: 's', path: 'https://app.example/a'});
// 别的站点不从这里打开。
send({type: 'navigate', sessionId: 's', path: 'https://evil.example/'});
send({type: 'navigate', sessionId: 's', path: '//evil.example/x'});
send({type: 'navigate', sessionId: 's', path: 'javascript:alert(1)'});
assert.deepEqual(plain(calls), [
  ['assign', 'https://app.example/faq?x=1'],
  ['assign', 'https://app.example/a'],
]);
""")


def _jsdom() -> Path | None:
    root = Path(__file__).resolve().parents[3] / "frontend" / "node_modules" / "jsdom"
    return root if root.exists() else None


@pytest.mark.skipif(_jsdom() is None, reason="jsdom from frontend/node_modules")
def test_the_snapshot_is_the_rendered_page_without_scripts_or_network():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the injected JavaScript bridge")
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require(process.env.JSDOM);
const dom = new JSDOM(`<!doctype html><html><head>
  <style>h1 { color: rgb(1, 2, 3); }</style>
  <script>window.ran = true</script>
</head><body>
  <h1 onclick="steal()">第 2 步</h1>
  <input id="name" value=""><textarea id="note"></textarea>
  <input id="pw" type="password">
  <select id="grade"><option>请选择</option><option>大一</option></select>
  <iframe src="https://elsewhere.example/"></iframe>
</body></html>`, { url: 'https://app.example/signup', runScripts: 'outside-only' });
const { window } = dom;
const messages = [];
const parent = { postMessage: (data, origin) => messages.push({data, origin}) };
Object.defineProperty(window, 'parent', { value: parent });
window.document.getElementById('name').value = '张三';
window.document.getElementById('note').value = '下午有空';
window.document.getElementById('pw').value = 'secret';
window.document.getElementById('grade').selectedIndex = 1;
window.eval(fs.readFileSync(0, 'utf8'));
const send = (data) => window.dispatchEvent(new window.MessageEvent('message', {
  source: parent, origin: 'https://platform.example',
  data: {channel: 'cheese-preview-runtime', version: 1, ...data},
}));
send({type: 'hello', sessionId: 's'});
send({type: 'snapshot', sessionId: 's'});
const shot = messages.find(m => m.data.type === 'snapshot').data;
assert.equal(shot.href, 'https://app.example/signup');
const html = shot.html;
assert.ok(html.startsWith('<!DOCTYPE html>'));
assert.ok(html.includes('rgb(1, 2, 3)'), 'styles are inlined');
assert.ok(!/<script/i.test(html), 'scripts are dropped');
assert.ok(!/<iframe/i.test(html), 'nested frames are dropped');
assert.ok(!/onclick/i.test(html), 'inline handlers are dropped');
assert.ok(html.includes("default-src 'none'"), 'the snapshot cannot reach the network');
const snap = new JSDOM(html).window.document;
assert.equal(snap.getElementById('name').getAttribute('value'), '张三');
assert.equal(snap.getElementById('note').textContent, '下午有空');
assert.equal(snap.getElementById('pw').getAttribute('value'), null, 'passwords stay out');
assert.equal(snap.querySelector('#grade option[selected]').textContent, '大一');
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=SCRIPT,
        text=True,
        capture_output=True,
        timeout=20,
        env={**os.environ, "JSDOM": str(_jsdom())},
    )
    assert result.returncode == 0, result.stderr

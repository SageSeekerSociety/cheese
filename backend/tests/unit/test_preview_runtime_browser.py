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

from app.api import preview_runtime
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
    """The jsdom to render the page with: the one CI installs, or the frontend's.

    jsdom belongs to the frontend's devDependencies and the pure layer has no
    frontend install, so CI installs it and names it in CHEESE_TEST_JSDOM (see
    test.yml). Locally the frontend's own copy is what an install already left
    in the tree.
    """
    named = os.environ.get("CHEESE_TEST_JSDOM")
    if named:
        return Path(named)
    root = Path(__file__).resolve().parents[3] / "frontend" / "node_modules" / "jsdom"
    return root if root.exists() else None


def test_the_snapshot_is_the_rendered_page_without_scripts_or_network():
    jsdom = _jsdom()
    if jsdom is None:
        pytest.skip("jsdom: the frontend install has none and CI names none")
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
        env={**os.environ, "JSDOM": str(jsdom)},
    )
    assert result.returncode == 0, result.stderr


def test_a_snapshot_is_sealed_with_the_platform_csp_before_anything_else():
    """seal_snapshot puts our own CSP first, whatever the writer sent.

    The frame that shows a stored snapshot runs no script (sandbox="") and reaches
    no network only if the bytes carry `default-src 'none'`; the writer does not
    get to decide that. Two regardless-sources: a page with no CSP at all, and one
    whose own CSP is looser than ours (browsers intersect, so ours still wins).
    """
    plain = b"<!doctype html><html><head><title>t</title></head><body>hi</body></html>"
    sealed = preview_runtime.seal_snapshot(plain)
    assert sealed is not None
    csp = preview_runtime.SNAPSHOT_CSP.encode()
    assert csp in sealed
    assert sealed.index(csp) < sealed.index(b"<body>")
    assert sealed.index(csp) < sealed.index(b"hi")

    loose = (
        b'<html><head><meta http-equiv="Content-Security-Policy" content="img-src *">'
        b"</head><body>x</body></html>"
    )
    sealed_loose = preview_runtime.seal_snapshot(loose)
    assert sealed_loose is not None
    # Our meta lands before theirs, so both apply and ours is the stricter half.
    assert sealed_loose.index(csp) < sealed_loose.index(b"img-src *")


def test_the_bridge_and_the_platform_spell_the_csp_the_same_way():
    """One string, two places that must agree.

    The bridge writes the CSP into the snapshot it hands the parent, and the
    platform writes it again on the way into storage. If they drift, the
    snapshot a reader sees before the upload stops being the one the platform
    promises. The script carries a placeholder, filled at import from
    SNAPSHOT_CSP, so a second spelling cannot appear — this test keeps it that way.
    """
    assert "__SNAPSHOT_CSP__" not in RUNTIME_SCRIPT
    assert f'"{preview_runtime.SNAPSHOT_CSP}"' in RUNTIME_SCRIPT


def test_a_snapshot_in_an_encoding_we_cannot_seal_is_refused_not_stored():
    utf16 = "<?xml version='1.0' encoding='utf-16'?>".encode("utf-16")
    assert preview_runtime.seal_snapshot(utf16) is None

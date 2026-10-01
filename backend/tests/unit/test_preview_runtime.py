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
const window = { parent, addEventListener: (_, handler) => { receive = handler; } };
vm.runInNewContext(fs.readFileSync(0, 'utf8'), { window });
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

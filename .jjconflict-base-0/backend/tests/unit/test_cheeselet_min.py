"""The minimal cheeselet must drive input using ONLY what its runtime provides.

The runtime is a bare goja VM whose single global is `cheese` (terminal + own +
watch + expose + call + log) — there are no browser/Node globals. A cheeselet
that reaches for one throws on every terminal tick, which is how the device path
silently stopped submitting prompts: `setTimeout` was undefined, so the Enter
never ran AND the guards after it never got set, re-pasting the prompt forever.

These tests run the real cheeselet source against a stub of that exact runtime.
"""

import json
import subprocess

import pytest

from app.domain.agent.device_launch import cheeselet_source

# Anything outside the `cheese` global (cli/internal/runtime/api.go) is a crash
# on the device — the runtime is a bare goja VM with no browser/Node globals.
_BANNED = (
    "setTimeout",
    "setInterval",
    "queueMicrotask",
    "requestAnimationFrame",
    "Promise",
    "fetch",
    "require(",
)


def test_cheeselet_uses_no_timer_globals():
    """The runtime has no timers; using one throws on every tick (dev-box outage)."""
    src = cheeselet_source()
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )
    for name in _BANNED:
        assert name not in code, f"cheeselet calls {name}, which its runtime lacks"


def test_cheeselet_survives_a_throwing_write():
    """A write that throws must not strand the prompt: the guard is set BEFORE the
    write, so the next tick never pastes a second copy (the pile-up seen live)."""
    src = cheeselet_source()
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )
    paste_line = next(
        i for i, ln in enumerate(code.splitlines()) if "write(PASTE_START" in ln
    )
    guard_line = next(
        i for i, ln in enumerate(code.splitlines()) if "pasted = true" in ln
    )
    assert guard_line < paste_line, "set the pasted guard before writing the paste"


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not available to execute the cheeselet",
)
def test_paste_then_submit_across_two_ticks():
    """Behavioral: the prompt is pasted on one tick and submitted on a LATER one,
    exactly once — no pile-up, and the Enter actually happens."""
    harness = """
    const writes = [];
    let screen = "starting…";
    let onChange = null;
    const exposed = {};
    globalThis.cheese = {
      term: {
        read: () => screen,
        write: (b) => writes.push(b),
        onChange: (fn) => { onChange = fn; },
      },
      own: () => ({}),
      watch: () => ({}),
      expose: (name, fn) => { exposed[name] = fn; },
      call: () => {},
      log: () => {},
    };
    __SRC__
    screen = "\\u276f ";               // input box painted
    exposed.prompt("hello world");     // server delivers the turn's prompt
    for (let i = 0; i < 5; i++) onChange();   // terminal ticks
    console.log(JSON.stringify(writes));
    """
    src = cheeselet_source()
    script = harness.replace("__SRC__", src)
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    writes = json.loads(out.stdout.strip().splitlines()[-1])

    pastes = [w for w in writes if "hello world" in w]
    enters = [w for w in writes if w == "\r"]
    assert len(pastes) == 1, f"prompt pasted {len(pastes)}x (pile-up): {writes}"
    assert len(enters) == 1, f"expected exactly one submit, got {writes}"
    assert writes.index(pastes[0]) < writes.index(enters[0]), "Enter preceded the paste"

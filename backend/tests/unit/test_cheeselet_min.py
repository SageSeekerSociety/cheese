"""The minimal cheeselet must drive input using ONLY what its runtime provides,
and must not TRUST that runtime's writes.

The runtime is a bare goja VM whose single global is `cheese` (terminal + own +
watch + expose + call + log) — there are no browser/Node globals. A cheeselet
that reaches for one throws on every terminal tick, which is how the device path
silently stopped submitting prompts once: `setTimeout` was undefined, so the
Enter never ran AND the guards after it never got set, re-pasting the prompt
forever.

The second hazard is subtler (#430): `cheese.term.write` is fire-and-forget — a
failed send-keys is logged connector-side and never surfaces in JS. The old
open-loop driver "confirmed" its own writes blind: a lost paste was followed by
an Enter on an empty composer and reported as submitted (claude sat idle, the
turn died as 300s-zero-output), and a swallowed Enter left the prompt sitting
in the composer forever. So the driver must verify every step against the
screen and re-issue what did not visibly take effect.

These tests run the real cheeselet source against a stub of that exact runtime,
plus a tiny composer model so verification has something real to read.
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


_HAS_NODE = subprocess.run(["which", "node"], capture_output=True).returncode == 0

# A minimal Claude-Code-composer model: writes either land or are DROPPED (the
# fire-and-forget failure this suite exists for). A landed paste puts the body
# on the `❯` line; a landed Enter clears it. `drop` names write indices (0-based,
# counting every attempted write) that the "terminal" swallows silently.
_HARNESS = """
const writes = [];
const calls = [];
let composer = "";            // text sitting in the input box
let claudeReady = false;      // has the ❯ prompt been painted
let dropped = new Set(__DROP__);
let onChange = null;
const exposed = {};
const logs = [];
function screen() {
  if (!claudeReady) return "starting…";
  return "some scrollback\\n❯ " + composer;
}
globalThis.cheese = {
  term: {
    read: () => screen(),
    write: (b) => {
      const i = writes.length;
      writes.push(b);
      if (dropped.has(i)) return;              // swallowed: no screen effect
      if (b.indexOf("\\u001b[200~") !== -1) {
        composer = b.replace("\\u001b[200~", "").replace("\\u001b[201~", "");
      } else if (b === "\\r") {
        composer = "";
      }
    },
    onChange: (fn) => { onChange = fn; },
  },
  own: () => ({}),
  watch: () => ({}),
  expose: (name, fn) => { exposed[name] = fn; },
  call: (...a) => { calls.push(a); },
  log: (m) => logs.push(String(m)),
};
__SRC__
const slowBoot = __SLOWBOOT__;   // ticks before the ❯ prompt paints (0 = already up)
claudeReady = slowBoot === 0;
exposed.prompt(__PROMPT__);
for (let i = 0; i < __TICKS__; i++) {
  if (slowBoot > 0 && i === slowBoot) claudeReady = true;
  onChange();   // change/heartbeat ticks
}
console.log(JSON.stringify({writes, composer, logs, calls}));
"""


def _drive(prompt: str, drop: list[int], ticks: int = 12, slow_boot: int = 0) -> dict:
    script = (
        _HARNESS.replace("__SRC__", cheeselet_source())
        .replace("__DROP__", json.dumps(drop))
        .replace("__PROMPT__", json.dumps(prompt))
        .replace("__TICKS__", str(ticks))
        .replace("__SLOWBOOT__", str(slow_boot))
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not _HAS_NODE, reason="node not available to run the cheeselet")
def test_clean_delivery_pastes_once_submits_once():
    """Happy path: one paste, one Enter, composer empty, submit logged."""
    got = _drive("hello world\nsecond line", drop=[])
    pastes = [w for w in got["writes"] if "hello world" in w]
    enters = [w for w in got["writes"] if w == "\r"]
    assert len(pastes) == 1, f"prompt pasted {len(pastes)}x (pile-up): {got['writes']}"
    assert len(enters) == 1, f"expected exactly one submit: {got['writes']}"
    assert got["writes"].index(pastes[0]) < got["writes"].index(enters[0])
    assert got["composer"] == ""
    assert any("submitted" in m for m in got["logs"])


@pytest.mark.skipif(not _HAS_NODE, reason="node not available to run the cheeselet")
def test_a_lost_paste_is_repasted_not_blind_submitted():
    """The #430 zero-output shape: the paste write is silently swallowed. The
    driver must notice the composer never received the body and paste again —
    never send an Enter to an empty composer and call it submitted."""
    got = _drive("hello world", drop=[0])
    pastes = [w for w in got["writes"] if "hello world" in w]
    enters = [w for w in got["writes"] if w == "\r"]
    assert len(pastes) >= 2, f"lost paste was never retried: {got['writes']}"
    assert enters, f"delivery never completed: {got['writes']}"
    first_landed = got["writes"].index(pastes[1])
    assert all(got["writes"].index(e) > first_landed for e in enters), (
        f"an Enter was sent before any paste had landed: {got['writes']}"
    )
    assert got["composer"] == ""
    assert any("submitted" in m for m in got["logs"])


@pytest.mark.skipif(not _HAS_NODE, reason="node not available to run the cheeselet")
def test_a_swallowed_enter_is_resent_until_the_composer_lets_go():
    """The #430 stuck-composer shape: the Enter is swallowed while the TUI is
    still ingesting the paste. The driver must see the body still sitting in
    the composer and send the Enter again."""
    got = _drive("hello world", drop=[1])  # write 0 = paste, write 1 = first Enter
    enters = [w for w in got["writes"] if w == "\r"]
    assert len(enters) >= 2, f"swallowed Enter was never re-sent: {got['writes']}"
    assert got["composer"] == ""
    assert any("submitted" in m for m in got["logs"])


@pytest.mark.skipif(not _HAS_NODE, reason="node not available to run the cheeselet")
def test_a_slow_first_boot_does_not_burn_the_retry_budget():
    """A fresh screen's launcher + claude first boot takes well over a minute —
    far more ticks than the retry budget. Waiting for the ❯ prompt must cost
    nothing: the prompt is held and delivered the moment the box paints
    (measured live 2026-08-16: the driver gave up in phase paste while the
    pane was still booting, and the turn died as 300s zero-output)."""
    got = _drive("run the tool", drop=[], ticks=140, slow_boot=120)
    assert not any("giving up" in m for m in got["logs"]), (
        f"the boot wait burned the budget: {got['logs']}"
    )
    assert any("submitted" in m for m in got["logs"]), (
        f"the held prompt was never delivered after boot: {got['logs']}"
    )
    assert got["composer"] == ""


@pytest.mark.skipif(not _HAS_NODE, reason="node not available to run the cheeselet")
def test_a_dead_terminal_is_given_up_not_hammered_forever():
    """Every write swallowed: the driver retries for a bounded number of ticks,
    then stops touching the terminal (the server-side retry re-drives it)."""
    got = _drive("hello world", drop=list(range(200)), ticks=80)
    assert len(got["writes"]) < 60, f"unbounded retry: {len(got['writes'])} writes"
    assert any("giving up" in m for m in got["logs"])

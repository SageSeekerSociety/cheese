// Functional harness for the Claude Code cheeselet driver (claude.js).
//
// It stubs the minimal `cheese` runtime API (term, own/watch/expose/call/log),
// loads the REAL served driver source, invokes `say`, then drives onChange
// heartbeats — exactly as the Go runtime does — and records the write sequence.
//
// Asserts the fix for the bracketed-paste vs Enter race: the paste body and the
// submitting Enter (\r) are emitted as SEPARATE writes, with the Enter deferred to
// a LATER frame (never in the same write / same step as the paste).
//
// Run: node backend/tests/unit/claude_driver_harness.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import vm from 'node:vm'
import assert from 'node:assert/strict'

const here = dirname(fileURLToPath(import.meta.url))
const driverPath = resolve(here, '../../app/agent/cheeselets/claude.js')
const source = readFileSync(driverPath, 'utf8')

const writes = []
let onChangeFn = null
let screen = 'idle\n? for shortcuts\n'
const exposed = {}
const watched = {}
const owned = {}

function makeVar(initial) {
  let v = initial
  return { get: () => v, set: (nv) => { v = nv }, onChange: () => {}, _set: (nv) => { v = nv } }
}

const cheese = {
  term: {
    read: () => screen,
    write: (s) => { writes.push(s) },
    onChange: (fn) => { onChangeFn = fn },
  },
  own: (name, initial) => (owned[name] = makeVar(initial)),
  watch: (name) => (watched[name] = watched[name] || makeVar(name === 'viewerLevel' ? 'passive' : undefined)),
  expose: (name, fn) => { exposed[name] = fn },
  call: () => Promise.resolve({}),
  log: () => {},
}

vm.runInNewContext(source, { cheese, console })

// Simulate a long multiline chat message being delivered.
const longMsg = Array.from({ length: 17 }, (_, i) => `line ${i + 1}`).join('\n')
const before = writes.length
exposed.say(longMsg)

// After say(): exactly one write (the atomic bracketed paste), NO Enter yet.
const sayWrites = writes.slice(before)
assert.equal(sayWrites.length, 1, `say() must emit exactly one write (the paste), got ${sayWrites.length}`)
const paste = sayWrites[0]
assert.ok(paste.includes('\x1b[200~') && paste.includes('\x1b[201~'), 'body must be wrapped in bracketed-paste markers')
assert.ok(paste.includes(longMsg), 'paste must contain the full message body')
assert.ok(!paste.includes('\r'), 'the paste write must NOT contain the submitting CR (would race the paste)')

// Now drive onChange frames (terminal-change + heartbeat), as the Go runtime does.
// The submitting Enter (\r) must appear on a LATER frame, as its own separate write
// — never bundled with the paste. (Other keystrokes like Shift+Tab auto-mode are
// orthogonal noise; we only track the CR that submits the message.)
const enters = () => writes.slice(before).filter((w) => w === '\r').length
screen = '[Pasted text #1 +17 lines]\n? for shortcuts\n' // paste landed as placeholder
onChangeFn() // frame 1: settle (submitIn 2 -> 1), no Enter yet
assert.equal(enters(), 0, 'no Enter should fire on the first settle frame')
onChangeFn() // frame 2: submitIn 1 -> 0, press Enter
assert.equal(enters(), 1, 'the deferred Enter must fire on a later frame, once settled')

// It must not keep pressing Enter on subsequent frames.
onChangeFn()
onChangeFn()
assert.equal(enters(), 1, 'Enter must be pressed exactly once, not repeatedly')

console.log('OK: paste and Enter are emitted as separate steps (deferred submit).')

// --- busy/idle: running subagents must read as busy even with a quiet foreground ---
// viewerLevel stays 'passive' (ground-truth sampling), so status reflects the screen.
const status = owned.status
const setScreen = (s) => { screen = s; onChangeFn() }

// 1) Fully idle: foreground prompt visible, no spinner / running task → idle.
setScreen('> \n\n? for shortcuts\n')
assert.equal(status.get(), 'idle', `fully-idle screen must be idle, got ${status.get()}`)

// 2) Foreground idle BUT the bottom subagent BAND lists ≥1 running subagent (one row
//    each) — the prompt is quiet (no "esc to interrupt" on the main turn) yet the band
//    below the input shows running subagents → must be busy, and subagents counts rows.
const band2 = [
  '> ',
  '',
  '╭─ Running agents ─────────────╮',
  '│ ⠹ general-purpose   Running… (34s)',
  '│ ⠋ code-reviewer     Running… (12s)',
  '╰──────────────────────────────╯',
  '? for shortcuts',
  '',
].join('\n')
setScreen(band2)
assert.equal(status.get(), 'busy', `quiet foreground with a subagent band must be busy, got ${status.get()}`)
assert.equal(owned.subagents.get(), 2, `subagents must count the 2 band rows, got ${owned.subagents.get()}`)

// 3) Foreground busy (classic "esc to interrupt") still works.
setScreen('✻ Working… (6m 45s · ↓ 19.2k tokens · esc to interrupt)\n? for shortcuts\n')
assert.equal(status.get(), 'busy', `foreground working must be busy, got ${status.get()}`)

// 4) Back to fully idle (no band) → idle again, and subagents resets to 0.
setScreen('> \n\n? for shortcuts\n')
assert.equal(status.get(), 'idle', `must return to idle when nothing is working, got ${status.get()}`)
assert.equal(owned.subagents.get(), 0, `subagents must reset to 0 with no band, got ${owned.subagents.get()}`)

// 5) REGRESSION: a COMPLETED tool result lingering in the tail (static ⏺ marker with a
//    "(5s)" duration) must NOT read as busy — only an animated spinner means working.
setScreen('⏺ Bash(ls -la)\n  ⎿  ran in (5s)\n> \n? for shortcuts\n')
assert.equal(status.get(), 'idle', `a finished ⏺ tool result must stay idle, got ${status.get()}`)
assert.equal(owned.subagents.get(), 0, `finished tool result is not a subagent, got ${owned.subagents.get()}`)

// 6) REGRESSION: a blank-braille (U+2800) spacer in the idle chrome must NOT read as
//    busy (the old anywhere-braille check reported busy forever on this).
setScreen('⠀ ? for shortcuts\n> \n⠀\n')
assert.equal(status.get(), 'idle', `blank-braille spacer must stay idle, got ${status.get()}`)

// 7) A single running subagent row (animated spinner, no "esc to interrupt") → busy, 1.
setScreen('> \n  ⠙ general-purpose  Running… (8s)\n? for shortcuts\n')
assert.equal(status.get(), 'busy', `one running subagent row must be busy, got ${status.get()}`)
assert.equal(owned.subagents.get(), 1, `one subagent row must count 1, got ${owned.subagents.get()}`)

console.log('OK: spinner-only busy; finished ⏺ results and blank-braille spacers stay idle.')

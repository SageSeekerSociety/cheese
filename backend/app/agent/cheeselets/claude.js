// claude.js — the cheeselet that drives a Claude Code screen.
//
// All domain knowledge lives here, derived from what Claude Code actually paints
// (verified against real v2.1.x screens). The hosting `cheese` CLI has no idea it
// is watching an AI; it only offers a terminal, variables, and functions.

// A-class variables (this script owns; the server mirrors):
const status = cheese.own('status', 'starting')       // starting|busy|waiting|idle|dead
const elapsed = cheese.own('elapsed', '')             // e.g. "6m45s" — how long Claude has been working
const tokens = cheese.own('tokens', '')               // e.g. "19.2k" — tokens used so far this turn
const question = cheese.own('question', '')            // dialog prompt when waiting
const choices = cheese.own('choices', [])              // [{n, label}] when waiting on a choice
const compact = cheese.own('compact', '')              // '' | 'running' | 'done'
const compactPct = cheese.own('compactPct', 0)         // 0..100 while compacting

// B-class variables (server owns; we observe):
const label = cheese.watch('label')                    // human label for the screen
label.onChange((v) => cheese.log('screen labelled: ' + v))

// viewerLevel: what the human viewer is doing right now, pushed by the UI:
//   'passive' — read-only or auto: NOT scrolling/typing, so the screen sits at
//               the live bottom and busy/idle can be read directly (ground truth);
//   'scroll'  — scrolling history: the footer may be off-screen, don't trust it;
//   'full'    — typing: the screen is changing under the human's hands.
// isActive() means scroll or full (unreliable to sample); isFull() also gates
// command buffering so the driver never types over a human.
const viewerLevel = cheese.watch('viewerLevel')
const isActive = () => { const l = viewerLevel.get(); return l === 'scroll' || l === 'full' }
const isFull = () => viewerLevel.get() === 'full'

// --- keystrokes ------------------------------------------------------------
const ESC = '\x1b'
const UP = ESC + '[A', DOWN = ESC + '[B', ENTER = '\r', PGDN = ESC + '[6~'

function pickOption(n) {
  for (let i = 0; i < 9; i++) cheese.term.write(UP)   // go firmly to the top
  for (let i = 1; i < n; i++) cheese.term.write(DOWN) // step down to option n
  cheese.term.write(ENTER)
}

const clean = (l) => l.replace(/[│╭╮╰╯]/g, '')
function parseOption(l) {
  const m = clean(l).match(/^\s*[❯>]?\s*(\d+)\.\s+(.*\S)\s*$/)
  return m ? { n: parseInt(m[1], 10), label: m[2].trim() } : null
}

// --- observe (tail only; scrollback can never trip a match) ----------------
// Returns the directly-observable shape of the screen. The busy/idle decision
// is made in onChange, because it depends on viewer state and history.
function observe(screen) {
  const lines = screen.split('\n')
  const tail = lines.slice(-16)
  const tailStr = tail.join('\n')

  if (/Pane is dead \(status/.test(tailStr)) return { kind: 'dead' }

  // A real choice dialog: a "❯ N." selection cursor + a dialog footer (or trust)
  // + a contiguous 1,2,3,… option block. This rejects stray numbered lists (e.g.
  // a "1./2./3." list inside a prompt), which have neither cursor nor footer.
  const trust = /Do you trust/i.test(tailStr)
  const hasFooter = /Esc to cancel|to amend|ctrl\+e to explain|Enter to (confirm|continue)|Esc to exit/i.test(tailStr)
  const selIdx = tail.findIndex((l) => /❯\s+\d+\.\s/.test(clean(l)))
  if (selIdx >= 0 && (hasFooter || trust)) {
    let start = selIdx
    while (start - 1 >= 0 && parseOption(tail[start - 1])) start--
    const opts = []
    for (let i = start; i < tail.length; i++) {
      const p = parseOption(tail[i])
      if (!p || p.n !== opts.length + 1) break
      opts.push(p)
    }
    if (opts.length >= 2 && opts[0].n === 1) {
      let q = ''
      for (let i = 0; i < start; i++) { const t = clean(tail[i]).trim(); if (t.endsWith('?')) q = t }
      return { kind: 'waiting', question: q, choices: opts }
    }
  }

  // The orange signature line: Claude shows "esc to interrupt" only while working.
  const orange = /esc to interrupt/.test(tailStr)
  const hasUI = /\? for shortcuts|for agents/.test(tailStr) || tail.filter((l) => l.trim()).length > 3
  return { kind: 'open', orange, hasUI }
}

// --- busy/idle state machine ------------------------------------------------
let stableBusy = false   // busy/idle from the last reliable (passive) sample
let cmdSince = false     // a command was emitted since that sample
let wasActive = false    // to detect the active -> passive transition
let trustFrames = 0
let autoModeDone = false // Claude switched into auto-accept (⏵⏵) mode
let tabTries = 0
let frame = 0

cheese.term.onChange(() => {
  frame++
  const screen = cheese.term.read()
  const tailStr = screen.split('\n').slice(-16).join('\n')

  // Auto-trust the "Do you trust this folder?" gate. It can sit static, so lean
  // on the heartbeat to retry pressing the default (Yes) until it's gone.
  if (/Do you trust/i.test(tailStr) && !isFull()) {
    if (trustFrames % 4 === 0) cheese.term.write(ENTER) // press the default (Yes)
    trustFrames++
    return
  }
  trustFrames = 0

  // When the human just stopped scrolling/typing, snap Claude back to the live
  // bottom so the next passive sample sees the real footer.
  const active = isActive()
  if (wasActive && !active) for (let i = 0; i < 12; i++) cheese.term.write(PGDN)
  wasActive = active

  const o = observe(screen)
  let st = o.kind, verb = ''
  if (o.kind === 'open') {
    let busy
    if (!active) {
      // Passive: the footer is trustworthy — this is the ground-truth sample.
      busy = o.orange
      stableBusy = busy
      cmdSince = false
    } else {
      // Active: can't trust the live view. Hold the last passive verdict, but a
      // command emitted since then has likely started work, so treat that as busy.
      busy = stableBusy || cmdSince
    }
    st = busy ? 'busy' : (o.hasUI ? 'idle' : 'starting')
  }

  // One-time: cycle Claude into "auto mode" via Shift+Tab, so routine tool use —
  // including shell commands — runs without a permission prompt. Shift+Tab cycles
  // through several modes (default → accept edits → auto mode → plan → …); both
  // "accept edits" and "auto mode" show the ⏵⏵ marker, so we must match the exact
  // "auto mode" TEXT, not the glyph, and keep pressing past "accept edits" until
  // it appears. Throttled (footer needs a frame to update) and capped so it can
  // never loop forever. Only when idle and no human is driving.
  if (!autoModeDone && st === 'idle' && !isFull()) {
    if (/auto[- ]?mode/i.test(tailStr)) autoModeDone = true
    else if (tabTries < 12 && frame % 2 === 0) { cheese.term.write('\x1b[Z'); tabTries++ }
  }

  status.set(st)
  question.set(o.kind === 'waiting' ? (o.question || '') : '')
  choices.set(o.kind === 'waiting' ? (o.choices || []) : [])

  // While working, Claude shows "…(6m 45s · ↓ 19.2k tokens)" — the genuinely
  // useful reassurance: how long it's been going and how many tokens it's used.
  // Update from that line when busy; clear when not. (Only update on a match, so a
  // scrolled-away spinner doesn't blank it mid-turn.)
  if (st === 'busy') {
    const paren = tailStr.match(/…\s*\(([^)]*)\)/)
    if (paren) {
      const tm = paren[1].match(/(?:\d+h\s*)?(?:\d+m\s*)?\d+s|\d+m\b/)
      if (tm) elapsed.set(tm[0].replace(/\s+/g, ''))
      const tk = paren[1].match(/([\d.]+k?)\s*tokens/i)
      if (tk) tokens.set(tk[1])
    }
  } else { elapsed.set(''); tokens.set('') }

  const cm = tailStr.match(/Compacting conversation[^%]*?(\d+)\s*%/)
  if (cm || /Compacting conversation/.test(tailStr)) {
    compact.set('running'); compactPct.set(cm ? parseInt(cm[1], 10) : 0)
  } else if (/Compacted \(|Not enough messages to compact/i.test(tailStr)) {
    compact.set('done'); compactPct.set(100)
  } else { compact.set(''); compactPct.set(0) }
})

// --- command buffering ------------------------------------------------------
// While a human is typing (full mode) server commands would collide with their
// keystrokes, so we queue them and flush the moment the human hands control back.
// Every executed command marks cmdSince so the state machine treats the screen as
// busy until the next reliable sample.
let queue = []
function gated(fn) {
  return function () {
    const args = Array.prototype.slice.call(arguments)
    const run = () => { cmdSince = true; return fn.apply(null, args) }
    if (isFull()) { queue.push(run); return 'buffered' }
    return run()
  }
}
viewerLevel.onChange(() => {
  if (!isFull() && queue.length) { const q = queue; queue = []; q.forEach((f) => f()) }
})

// --- functions the server may call -----------------------------------------
cheese.expose('snapshot', () => cheese.term.read())
cheese.expose('say', gated((text) => { cheese.term.write(text); cheese.term.write(ENTER); return true }))
cheese.expose('choose', gated((n) => { pickOption(parseInt(n, 10) || 1); return true }))
cheese.expose('compact', gated(() => { cheese.term.write('/compact'); cheese.term.write(ENTER); return true }))

cheese.call('screenReady', { driver: 'claude', version: 5 }).then((ack) => {
  cheese.log('server acked screenReady: ' + JSON.stringify(ack))
})

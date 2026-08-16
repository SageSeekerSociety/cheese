// claude_min.js — the MINIMAL cheeselet for a device-hosted Claude Code screen.
//
// Unlike the reference claude.js (which reads the screen to INFER agent state), we
// perceive the agent through Claude Code *hooks* (structured events POSTed back to
// the backend). So this cheeselet does exactly one job the hooks can't: drive input.
// It exposes a single server-callable function, `prompt(text)`, that types one turn's
// prompt into the interactive `claude` running in this screen.
//
// It reads the terminal for TWO mechanical purposes. First, a readiness gate: type
// only once Claude Code's input box (the `❯` prompt) is painted, so the paste is
// never dropped into a splash/onboarding frame. Second — and this is the #430
// lesson — VERIFICATION: `cheese.term.write` is fire-and-forget (a failed
// send-keys is logged connector-side and never surfaces here), so an open-loop
// paste-then-Enter can silently lose either half. A lost paste made the driver
// "submit" an empty composer and report success while claude sat idle (the
// 300s zero-output turns); a swallowed Enter left the prompt sitting in the
// composer forever. So every write is CONFIRMED against the screen before the
// state machine advances, and re-issued until it visibly took effect.
//
// The runtime has NO timers (setTimeout is not defined — using one threw here
// every tick, which both skipped the Enter AND left the guards unset, so every
// tick re-pasted the prompt). The terminal's change/heartbeat cadence is the
// clock: the poller fires on every screen change and at least every ~1.2s even
// on a static screen, so each retry below is at most a heartbeat away.
//
// The runtime does not await a Promise returned from an exposed function, so
// `prompt` is synchronous: it records the pending text and returns immediately;
// the driving happens on subsequent ticks. Hot-reload safe (fresh VM each load).

const ESC = '\x1b'
const ENTER = '\r'
// Ctrl+U: clears the whole composer, including a `[Pasted text …]` widget.
// Measured on 2.1.233: a no-op when the box is empty, and unlike Esc/Ctrl+C it
// carries no "press again" arming or exit semantics — safe to send blind.
const KILL_LINE = '\x15'
// Bracketed-paste markers so the TUI ingests a multiline body as one atomic paste
// (embedded newlines are not interpreted as submits); the Enter is sent separately.
const PASTE_START = ESC + '[200~'
const PASTE_END = ESC + '[201~'

let pending = null // the prompt text waiting to be typed
let snippet = '' // screen-verifiable fragment of the prompt (first line's head)
// idle -> paste (need to write the body) -> sent (body written, confirm it's in
// the composer) -> submit (Enter written, confirm the composer let go) -> idle
let phase = 'idle'
let tries = 0
// Heartbeat guarantees a tick at least every ~1.2s, so this bounds the whole
// delivery at well under the server's own turn-delivery timeout; past it we
// stop touching the terminal and let the server-side retry re-drive us.
const MAX_TRIES = 40

function norm(s) {
  // Whitespace AND the composer's box-drawing borders stripped. The composer
  // soft-wraps at the pane width, so on screen the body is interleaved with
  // newlines, row padding and `│` borders — and CJK chars take 2 columns each,
  // so a 24-char CJK snippet needs 48 columns of one row. The production 现场
  // pane had 46: any check that reads a single row can NEVER match it, which
  // made every 【平台】-prefixed prompt re-paste forever (2026-08-16 outage).
  // All matching therefore happens on this flattened text. Must stay in
  // lockstep with tmux_provider's _flatten/composer_holds_body — both backends
  // judge "did my keystrokes take" the same way.
  return s.replace(/[\s│╭╮╰╯─]+/g, '')
}

function composerRegion() {
  // The composer is everything from the LAST `❯` on screen to the end: Claude
  // Code renders history user messages with `>`, menus are excluded by ready(),
  // so the last `❯` opens the input box. Returns null when no input box is
  // painted (splash, or the TUI replaced it while running a turn).
  const s = cheese.term.read()
  const i = s.lastIndexOf('❯')
  if (i === -1) return null
  return s.slice(i)
}

function bodyInComposer() {
  const r = composerRegion()
  if (r === null) return false
  const flat = norm(r)
  // Large pastes render as a "[Pasted text #N +N lines]" widget instead of the
  // literal body — the widget is just as much proof the paste arrived.
  if (flat.indexOf('[Pastedtext') !== -1) return true
  return snippet !== '' && flat.indexOf(snippet) !== -1
}

function ready() {
  // The `❯` input box means Claude Code is at the prompt and will accept a paste.
  // Guard against first-launch MENUS that also render a `❯` selector (the trust and
  // bypass dialogs) — typing a prompt into those would answer the menu, not run a
  // turn. Those gates are pre-accepted in ~/.claude, so they normally never appear;
  // this is belt-and-suspenders against any other machine's stray gate.
  const s = cheese.term.read()
  if (s.indexOf('❯') === -1) return false
  if (s.indexOf('trust this folder') !== -1) return false
  if (s.indexOf('bypass permissions?') !== -1) return false
  return true
}

function tryType() {
  if (phase === 'idle' || pending === null) return
  if (phase === 'paste' && !ready()) {
    // Waiting for the input box costs NOTHING against the retry budget: a
    // fresh screen's launcher + claude first boot takes well over a minute,
    // and burning the budget on that wait made the driver abandon the prompt
    // before claude could even accept it (measured live 2026-08-16: "giving
    // up in phase paste after 40 ticks" while the pane was still booting;
    // the turn then sat until the server's 300s retry and read as
    // zero-output). The prompt is held until the box paints; the server's
    // own turn retry remains the outer bound.
    return
  }
  tries += 1
  if (tries > MAX_TRIES) {
    cheese.log('claude_min: giving up in phase ' + phase + ' after ' + MAX_TRIES + ' ticks')
    // Report the give-up to the SERVER (#445), not just the local journal:
    // the backend re-sends immediately and shows the room what happened,
    // instead of everyone waiting out the 300s no-output bound.
    cheese.call('deliveryFailed', phase, tries)
    phase = 'idle'
    pending = null
    return
  }
  if (phase === 'paste') {
    if (bodyInComposer()) {
      // Residue of a FAILED earlier send is visibly in the box (this driver is
      // the screen's only writer). Clear it as its OWN write and re-check next
      // tick: pasting on top would stack bodies (44 widgets deep in prod,
      // 2026-08-17), and the `sent` verification could then match an OLD
      // widget instead of this paste.
      cheese.term.write(KILL_LINE)
      return
    }
    // The KILL_LINE prefix still rides along for residue the check above
    // cannot see (literal text of a different message) — no-op when empty.
    cheese.term.write(KILL_LINE + PASTE_START + String(pending) + PASTE_END)
    // Not an advance to "submitted" — the next tick VERIFIES the body actually
    // reached the composer before the Enter goes anywhere near it.
    phase = 'sent'
    return
  }
  if (phase === 'sent') {
    if (bodyInComposer()) {
      // The body is visibly in the composer. A tick has passed since the paste,
      // so the TUI has ingested it — submit.
      cheese.term.write(ENTER)
      phase = 'submit'
      cheese.log('claude_min: prompt pasted, submitting')
      return
    }
    // The write was lost (send-keys failures never surface here) — repaste on
    // the next ready tick instead of "confirming" a blank composer.
    phase = 'paste'
    return
  }
  if (phase === 'submit') {
    if (!bodyInComposer()) {
      // The composer let go of the body (or the input box gave way to a running
      // turn) — the submit took.
      if (tries > 8) {
        // Delivery succeeded but needed a conspicuous number of re-issues —
        // the pane's input path is flaky. Tell the server (#445) so a
        // wobbling machine is seen before it produces a dead turn. The
        // threshold is above any healthy delivery (paste + verify + submit
        // + verify = 4 ticks) with margin for a slow TUI.
        cheese.call('deliveryRetried', 'submit', tries)
      }
      phase = 'idle'
      pending = null
      cheese.log('claude_min: prompt submitted')
      return
    }
    // The composer still holds the body: the Enter was swallowed (it rode too
    // close to the paste, or the write was lost) — send it again. An extra
    // Enter on an already-empty composer is a no-op, so over-sending is safe.
    cheese.term.write(ENTER)
    return
  }
}

// Drive on every screen change and on the poller's static-screen heartbeat.
cheese.term.onChange(tryType)

// The one server→cheeselet function: record the turn's prompt and try immediately.
// Returns synchronously (the runtime does not await a returned Promise); driving
// is gated on readiness via onChange above.
cheese.expose('prompt', (text) => {
  pending = String(text)
  // The verification anchor: the head of the first non-blank line, flattened
  // the same way the screen is (norm) so soft-wrap and pane width can never
  // break the match.
  const lines = pending.split('\n')
  let first = ''
  for (let i = 0; i < lines.length; i++) {
    if (norm(lines[i]) !== '') {
      first = lines[i]
      break
    }
  }
  snippet = norm(first).slice(0, 24)
  phase = 'paste'
  tries = 0
  tryType()
  return { ok: true, ready: ready() }
})

cheese.log('claude_min cheeselet loaded')

// claude_min.js — the MINIMAL cheeselet for a device-hosted Claude Code screen.
//
// Unlike the reference claude.js (which reads the screen to INFER agent state), we
// perceive the agent through Claude Code *hooks* (structured events POSTed back to
// the backend). So this cheeselet does exactly one job the hooks can't: drive input.
// It exposes a single server-callable function, `prompt(text)`, that types one turn's
// prompt into the interactive `claude` running in this screen.
//
// It reads the terminal for ONE mechanical purpose — a readiness gate: type only once
// Claude Code's input box (the `❯` prompt) is painted, so the paste is never dropped
// into a splash/onboarding frame. This mirrors the tmux backend's `pane_ready`
// handshake; it infers no agent state and drives no platform behavior.
//
// The runtime does not await a Promise returned from an exposed function, so `prompt`
// is synchronous: it records the pending text and returns immediately; the actual
// typing happens on the next terminal change once the input box is ready (a `sent`
// guard makes it fire exactly once). Hot-reload safe (fresh VM each load).

const ESC = '\x1b'
const ENTER = '\r'
// Bracketed-paste markers so the TUI ingests a multiline body as one atomic paste
// (embedded newlines are not interpreted as submits); the Enter is sent separately.
const PASTE_START = ESC + '[200~'
const PASTE_END = ESC + '[201~'

let pending = null // the prompt text waiting to be typed
let sent = false // becomes true once we have typed the pending prompt

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
  if (sent || pending === null) return
  if (!ready()) return
  cheese.term.write(PASTE_START + String(pending) + PASTE_END)
  cheese.term.write(ENTER)
  sent = true
  pending = null
  cheese.log('claude_min: prompt typed')
}

// Type when the screen reaches the input box (or right away if already there).
cheese.term.onChange(tryType)

// The one server→cheeselet function: record the turn's prompt and try immediately.
// Returns synchronously (the runtime does not await a returned Promise); typing is
// gated on readiness via onChange above.
cheese.expose('prompt', (text) => {
  pending = String(text)
  sent = false
  tryType()
  return { ok: true, ready: ready() }
})

cheese.log('claude_min cheeselet loaded')

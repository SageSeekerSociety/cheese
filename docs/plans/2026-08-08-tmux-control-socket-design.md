# Verifiable Prompt Delivery (tmux control socket) Design

## The problem this fixes

A human message reaches 芝士 by being typed into the terminal she runs in. That
channel is the right one — it is the only one that produces a genuine user turn,
which is what keeps a person's words carrying a person's authority — but today
we drive it blind.

`TmuxProvider._send_prompt` issues three `docker exec … tmux` commands
(`load-buffer`, `paste-buffer`, `send-keys Enter`) and discards every return
code. Readiness is decided by capturing the pane and looking for `❯`, a
heuristic the launcher's own comment records as unreliable because a dialog's
menu renders the same glyph. Nothing anywhere establishes that the text became
a prompt.

The failure mode this produces was measured on dev on 2026-08-08: a topic's
pane died, the backend kept pasting into it, tmux reported success each time,
and the turn sat until the 900-second ceiling before reporting a timeout. The
user saw fifteen minutes of "正在思考" for a message that was never received by
anything.

## What the measurements say

Three facts, each measured rather than assumed, decide the design.

**tmux is already a client/server over a unix socket, and its control mode
reports outcomes.** A single `tmux -C attach` client wraps every command in
`%begin <id>` / `%end <id>`, turns failures into `%error <id>` with the reason
on the preceding line (`can't find pane: %999`), and streams the pane's own
output as `%output %<pane> <data>` — which means our injected text is visible
coming back. That replaces three fire-and-forget process spawns with one
connection that answers.

**Pane death is knowable but not pushed.** `#{pane_dead}` flips to `1` the
instant the pane's process exits, under both `remain-on-exit` settings. tmux
does not notify a control client when that happens, and — decisively — a
`send-keys` into a dead pane still returns `%end`, i.e. success. tmux guarantees
that bytes reached the pane, never that a program read them. Liveness therefore
has to be asked for, before each send.

**Claude Code confirms consumption through a hook.** `UserPromptSubmit` fires
for text that arrived by paste exactly as for text a human typed, and its
payload carries the prompt and a unique `prompt_id`. That is the missing
receipt: not "the keystrokes were delivered" but "the program turned them into a
user prompt".

The two layers are complementary, not alternatives. tmux can attest to the
transport; only the hook can attest to the consumer, because tmux's
responsibility ends at the terminal's byte stream.

## The design

Each tmux-backed topic gets one long-lived control-mode client instead of a
process spawn per keystroke batch. The client owns the request/response
correlation (`%begin`/`%end`/`%error` keyed by tmux's own command id), exposes
`send(command) -> Result`, and surfaces `%output` to callers that want it. It
reconnects on its own; a connection that cannot be re-established is a screen
failure, reported as such rather than absorbed.

Sending a prompt becomes a sequence with a decision after each step. Liveness is
checked first (`list-panes -F '#{pane_dead}'`), because pasting into a dead pane
is the failure we are eliminating and it is cheap to rule out. The paste and the
Enter are issued over the control connection, and a `%error` from either aborts
immediately with the tmux reason attached instead of continuing to the next
step.

Consumption is confirmed in the shared turn loop rather than at the transport,
because both backends need it and neither can see it from its own side. The
loop's first wait is short: until something comes back there is no evidence the
prompt was received at all. `UserPromptSubmit` is the direct receipt, and any
other hook counts too, since activity proves delivery just as well and older
sessions may predate the receipt hook. When the window passes with nothing at
all, the turn ends as an error saying the message did not reach her and that
re-summoning will retry — in seconds, instead of being indistinguishable from
"still working" for a quarter of an hour.

A finer escalation (a second Enter for the known swallowed-Enter case, then a
session rebuild) is deliberately left for later: it needs per-prompt correlation
and a retry that cannot double-send, and the coarse version already converts the
silent failure into a reported one.

Readiness stops depending on the `❯` heuristic where a better signal exists. A
`SessionStart` hook already fires when the session comes up, and the control
connection's `%output` carries what the pane is actually showing; the glyph
check remains only as the last-resort fallback for a session that predates both.

## Why not the alternatives

Two other channels were measured and rejected, and the reasoning belongs in the
record because both look attractive from the outside.

Claude Code's cross-session inbox socket delivers a message that the runtime
marks `origin.kind=peer`, wraps in `<cross-session-message …>`, and follows with
an instruction telling the receiver the text was not typed by its user and must
not be treated as its user's approval. The `origin` is established from the
socket's verified peer pid, so a sender cannot claim to be a human. For messages
that genuinely come from a person, that framing is wrong at the source. (For the
platform's own notifications — deploys, gate results — it is exactly right, and
that is a separate future use.)

Claude Code's background-session daemon does expose a control socket that
carries genuine user input, which is how agent view steers a session with no
terminal attached. Its protocol is undocumented, and adopting it would mean
handing session hosting to that daemon and rebuilding the terminal mirror. It is
worth revisiting when the protocol or an equivalent command is published.

Headless mode (`-p`, `--input-format stream-json`) accepts user messages
programmatically, but is unavailable to this deployment for reasons outside this
document.

## Testing

The control-mode client is exercised against a real tmux, because a protocol
parser tested only against a fake proves nothing about tmux: a successful
command, a failing command, the pane-death case where tmux reports success into
a dead pane, `%output` reaching a subscriber, and a socket with no server
surfacing as a typed error rather than a client that looks connected.

The initial silence window is covered in the shared turn loop: an accepted
write without a hook triggers a process probe, a live process can still deliver
a late receipt, and a confirmed dead process ends the turn. Ordinary agent
activity also ends the initial silence window for sessions without the hook. The
env stamp is covered as a pure function — stable across turns, changing when the
model route changes, and never containing the credential it digests.

## Container env drift

The same class of blindness sits one level down. A topic's container fixes its
env at creation and lives for the topic's lifetime, and `_ensure_container`
rebuilt it only when the IMAGE changed — so a box created against one model
route kept that route no matter how the backend was later reconfigured. On
2026-08-08 a corrected gateway URL had no effect for exactly this reason: the
running `claude` still held the old one, and nothing in the system noticed the
disagreement.

The container is therefore labelled with a digest of the routing-relevant env
(base URL, auth token, model names, API base), and a turn that finds a different
digest rebuilds the box the same way an image switch does. The digest is a hash
rather than the values because one of them is a credential, and per-turn values
are excluded so an ordinary turn never rebuilds anything.

## Out of scope

Forwarding `PostToolUse` results to 现场 (registered on the machine already,
dropped in `hook_events`) needs a new event type and frontend rendering, and
replacing the turn's wall-clock ceiling with a silence watchdog changes turn
semantics. Each is separable and gets its own change.

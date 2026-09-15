"""平台的事件转发器：任何 harness 都用它把一条事件送回后端。

``cheese-hook`` is a shell script rather than a module because its callers are
whatever the machine has: a harness's hook configuration, the ``cheese`` CLI
reporting a sync, a launcher reporting its own failure. It is on PATH in every
session this platform starts, and reading JSON on stdin is the only interface
all three can agree on.

It lives here, not beside a harness, for the same reason the drain does: the
spool it writes and the drain that empties it are how THIS platform delivers
events, and a second harness does not get a second copy of that.
"""

# The forwarder: reads an event's JSON on stdin, durably spools it (when
# CHEESE_HOOK_SPOOL is set — the local/tmux backend only) so the event survives a
# backend outage, then best-effort POSTs it with the screen's token + a stable
# per-event id (X-Cheese-Event-Id, used to dedup the spool backfill against the live
# delivery). Exit 0 + empty stdout = "no decision" → the tool proceeds. The device
# backend writes this via its launcher; the local (tmux) image bakes the same script
# (kept identical so sensing can't drift); the spool block no-ops without the env.
#
# The spooled name is `<seq>.<eid>`, and `seq` is claimed the way event_spool.py
# claims it — an O_EXCL create under `set -C`, seeded from a `.seq` hint. That
# module's docstring says why the clock was not good enough; the short of it is
# that a `date` without `%N` (any BSD userland, i.e. a device on macOS) sorts a
# whole second's events at random, and a `date` that fails at all produces a
# name starting with `.`, which every reader skips forever.
# NOTE: the device launcher embeds this in a <<'SH' heredoc — never add a line
# consisting of just `SH` here or the heredoc would silently truncate.
CHEESE_HOOK_SCRIPT = """#!/bin/sh
body="$(cat)"
# Claude Code's resume stamp, cleared by the two hook names that prove the
# session is live. Absent the file — every other harness — this is a no-op.
if [ -f "$HOME/.claude/cheese-resume.attempt" ]; then
  printf '%s' "$body" | python3 -c '
import json, pathlib, sys
event = json.load(sys.stdin)
if event.get("hook_event_name") in ("UserPromptSubmit", "Stop"):
    (pathlib.Path.home() / ".claude/cheese-resume.attempt").unlink(missing_ok=True)
' 2>/dev/null || true
fi
if ! { IFS= read -r eid < /proc/sys/kernel/random/uuid; } 2>/dev/null; then
  eid="$$-$(date +%s%N)"
fi
if [ -n "$CHEESE_HOOK_SPOOL" ]; then
  mkdir -p "$CHEESE_HOOK_SPOOL" 2>/dev/null || true
  # Shared bind mount: node (sandbox uid 1000) writes while cheese (backend uid
  # 1001) reads, parks, and prunes events. Keep the directory shared even if it
  # had to be recreated after session setup.
  chmod 0777 "$CHEESE_HOOK_SPOOL" 2>/dev/null || true
  _n=""
  # The hint has no trailing newline; read sets _n even when it returns EOF.
  { IFS= read -r _n < "$CHEESE_HOOK_SPOOL/.seq"; } 2>/dev/null || true
  case "$_n" in
    ''|*[!0-9]*)
      # No usable hint. The glob expands in ascending order and every name is
      # the same width, so the last one that parses is the highest.
      _n=0
      for _f in "$CHEESE_HOOK_SPOOL"/[0-9]*; do
        [ -e "$_f" ] || continue
        _b="${_f##*/}"
        _b="${_b%%.*}"
        case "$_b" in ''|*[!0-9]*) continue;; esac
        _n="$_b"
      done
      # $(( )) reads a zero-padded number as octal; strip the pad first.
      while :; do case "$_n" in 0?*) _n="${_n#0}";; *) break;; esac; done
      ;;
  esac
  while :; do
    _n=$((_n + 1))
    _key="$(printf '%019d' "$_n")"
    # noclobber makes this O_EXCL: one writer owns the number, the rest retry.
    if (set -C; : > "$CHEESE_HOOK_SPOOL/.n$_key") 2>/dev/null; then break; fi
  done
  printf '%s' "$_n" > "$CHEESE_HOOK_SPOOL/.seqt.$$" 2>/dev/null &&
    mv "$CHEESE_HOOK_SPOOL/.seqt.$$" "$CHEESE_HOOK_SPOOL/.seq" 2>/dev/null ||
    rm -f "$CHEESE_HOOK_SPOOL/.seqt.$$" 2>/dev/null
  _tmp="$CHEESE_HOOK_SPOOL/.tmp.$eid"
  if printf '%s' "$body" > "$_tmp" 2>/dev/null; then
    mv "$_tmp" "$CHEESE_HOOK_SPOOL/$_key.$eid" 2>/dev/null || rm -f "$_tmp" 2>/dev/null
  fi
fi
# On the device a background drainer is the sole sender (CHEESE_HOOK_SPOOL_ONLY set);
# locally we curl inline for low latency (the backend reconciles the spool for gaps).
if [ -z "$CHEESE_HOOK_SPOOL_ONLY" ] && [ -n "$CHEESE_HOOK_URL" ]; then
  printf '%s' "$body" | curl -s -m 10 -X POST \\
    -H 'Content-Type: application/json' \\
    -H "X-Cheese-Token: $CHEESE_TOKEN" \\
    -H "X-Cheese-Event-Id: $eid" \\
    --data-binary @- "$CHEESE_HOOK_URL" >/dev/null 2>&1 || true
fi
exit 0
"""

"""哪个二进制、带哪几条硬拒绝——两条传输启动的是同一个 claude。

Kept apart from everything else so both launchers can reach it without either
reaching the other: a leaf with no imports is the only shape that lets the
sensing side (settings.json's deny list) and the starting side (the argv's
``--disallowedTools``) state the SAME refusal without one importing the other.
"""

# AskUserQuestion ("向用户提问") is the one that bites: the interactive `claude`
# draws its option picker INSIDE the screen, where no user can ever reach it —
# the model then waits for a keypress that will never come and the turn hangs
# until the wedged-turn safety net kills it. `cheese ask` is the platform's
# equivalent (real buttons in the conversation, the answer arrives on the next
# turn), so the native tool is denied outright rather than left as a trap.
# WebFetch: it can hang a turn open with no way out. Measured on this platform,
# two of two attempts on one page ran 1,028 s and 390 s and were ended by hand,
# while a LARGER page returned in 5 s — so it is not size, and it is not
# reproducible on demand. The mechanism is a step with no deadline: reading the
# binary (2.1.224 and 2.1.261, confirmed against a published reading of the
# unminified source), the page fetch is bounded at 60 s and the domain preflight
# at 10 s, but the model call made on the extracted text has no timeout at all.
# A stall there never returns and never errors, and nothing on our side reaches
# it — upstream anthropics/claude-code#86910 is open.
#
# `cheese fetch` replaces it and is strictly better here: measured end to end on
# 20 real sites it reads 19, every rung is bounded, and on the very page that
# hung for 17 minutes it answers in 10 s. So this is not a capability being
# taken away — it is the same capability on a path that can fail.
DISALLOWED_TOOLS = ["AskUserQuestion", "WebFetch"]

# The interactive `claude` every screen launches. The deny travels WITH the
# command, not only in settings.json: --dangerously-skip-permissions waves
# through permission prompts, and an explicit --disallowedTools is what keeps
# this tool out regardless.
CLAUDE_BASE_CMD = "claude --dangerously-skip-permissions --disallowedTools " + " ".join(
    DISALLOWED_TOOLS
)

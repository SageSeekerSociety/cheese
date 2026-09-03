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
DISALLOWED_TOOLS = ["AskUserQuestion"]

# The interactive `claude` every screen launches. The deny travels WITH the
# command, not only in settings.json: --dangerously-skip-permissions waves
# through permission prompts, and an explicit --disallowedTools is what keeps
# this tool out regardless.
CLAUDE_BASE_CMD = "claude --dangerously-skip-permissions --disallowedTools " + " ".join(
    DISALLOWED_TOOLS
)

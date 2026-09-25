"""哪几条硬拒绝、以哪种模式起——每个房间启动的是同一个 claude。

Kept apart from everything else so the launcher, the settings file and the
runner test can all reach it: a leaf with no imports is the only shape that lets
the settings file's deny list and the argv's ``--disallowedTools`` state the SAME
refusal without one importing the other.
"""

# AskUserQuestion is not offered at all. A question the agent asks reaches a
# person through `cheese_ask` (real buttons in the room, the answer arrives on
# the next turn); the build's own question would arrive on the driver's stdin as
# a request nothing in the room can answer, and the turn would wait on it.
PLATFORM_MANAGED_TOOLS = [
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "CronCreate",
    "CronDelete",
    "CronList",
    "ScheduleWakeup",
]
DISALLOWED_TOOLS = ["AskUserQuestion", *PLATFORM_MANAGED_TOOLS]

# How the runner drives Claude Code: headless, stream-json on both pipes, every
# input echoed back once it is read (the echo is the delivery receipt), and no
# permission request ever reaching the driver. Pinned by
# `scripts/remote_execution/headless_contract.py` against the pinned build.
LAUNCH_ARGS = [
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--replay-user-messages",
    "--permission-mode",
    "bypassPermissions",
    "--disallowedTools",
    *DISALLOWED_TOOLS,
]

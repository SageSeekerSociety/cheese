# Agent chat publication

Cheese chat is a timeline of collaboration: accepting work, sharing findings,
asking for decisions, and handing over results. A room's living document is the
current overview for someone who has not followed that timeline. It includes
the goal, necessary background, conclusions and next steps.

The terminal agent publishes chat explicitly. MessageDisplay, Stop and recovered
terminal output stay in activity records. Tool activity, running state, platform
errors and decision cards remain visible through their existing interfaces.
Tool-free private chat publishes its model response directly because it has no
CLI. Native RC controls are independent of this publication path.

## Sending a message

```bash
cheese chat send 'I will check the current message flow, then connect explicit publication.'
cheese chat send --file ./update.txt
cheese chat send 'The retry check passed.' --reply-to <message-id>
```

The command uses the current room and agent credentials. It calls
`POST /topics/{topic_id}/messages` with `content`, a UUID `request_id`, and an
optional `reply_to`. The backend verifies the agent and room access, stores the
message and mention notifications, then broadcasts an `assistant_block`. It does
not enqueue user input or start another model turn. The response contains the
stored message and its ID.

If delivery is uncertain, repeat the unchanged request with the printed
`--request-id`, preserving `--reply-to`. A retry returns the same stored message;
changing its content or reply target returns a conflict. Parameter and permission
errors need correction before retrying. Multiline content can come from a UTF-8
file or stdin with `--file -`.

## Communication instructions

The launcher installs `cheese-chat` and `cheese-docs` as native user-level skills
under the session's `$CLAUDE_CONFIG_DIR/skills/`.
It writes no skills or settings into the hosted project or the owner's Claude
configuration. The system prompt carries a short publication contract and asks
the agent to load `cheese-chat` through the Skill tool before responding. Native
discovery lists the description; the body enters context when invoked. This is
an instruction to the model, not a deterministic invocation gate.

The chat skill teaches the lead agent to answer user messages directly
when possible, announce work before doing it, and publish progress and results.
Queued messages and messages received during work follow the same rules. Each
new or merged terminal input also reminds the agent that ordinary text is not
sent. Subagents report to their lead; scheduled inspections use the heartbeat
notification rules.

The `cheese-docs` skill handles durable overviews and updates when the underlying
state changes. It does not require a document edit for every chat turn. Both
skills adapt the explanation to the reader; a reader without conversation
history may still have professional expertise.

The launch configuration changes so existing terminals reopen through the
session-resume path at the next task boundary and receive the updated CLI and
instructions.

For room work answering a person, the backend checks chat silence every 15
seconds. While a response remains active, the backend queues a reminder after
`CHAT_PROGRESS_REMINDER_AFTER_S` seconds without a published message. The default
is 600 seconds (10 minutes), measured from the start of the response or its last
publication. Set this backend environment variable to a positive number of
seconds and restart the backend to change it. Each silent stretch produces one
reminder; a new publication starts the clock again. Raw terminal output and
retries of an existing publication do not reset it. A Stop ends eligibility.
Private chat and background inspections retain their own notification policy.

The reminder goes only to the agent; the agent chooses the update to publish.
It does not create a system waiting message in chat, interrupt work, or
automatically background tools. Native RC controls let a person background
supported tasks or interrupt the current turn. A blocked tool can still delay
the agent's own progress message. A delayed reminder tells the agent to ignore
it if the response has already finished.

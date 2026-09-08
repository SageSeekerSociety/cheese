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

The chat skill is loaded with room conversations and teaches the lead agent to
send an opening message before work, meaningful progress updates, and a result
before ending. Each new or merged terminal input also reminds the agent that
ordinary text is not sent. Subagents report to their lead; scheduled inspections
use the heartbeat notification rules.

The doc-form skill handles durable overviews and updates when the underlying
state changes. It does not require a document edit for every chat turn. Both
skills adapt the explanation to the reader; a reader without conversation
history may still have professional expertise.

The launch configuration changes so existing terminals reopen through the
session-resume path at the next task boundary and receive the updated CLI and
instructions. Progress timing is a model instruction, not an independent timer:
a blocked tool can still delay a message. Runtime status and failure reporting
remain available independently of whether the agent publishes a chat message.

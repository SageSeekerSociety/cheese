---
name: chat
title: 协作聊天
scenarios: [chat]
description: Guide the room's lead agent when responding to user messages, including queued messages and messages received during work, sharing progress, or delivering results. Publish through chat; ordinary terminal output, including the final response, is not a chat message. Subagents report to their lead. Heartbeat uses its own notification rules. Use doc-form for durable context and overviews.
---

# Chat as a collaborator's timeline

People use chat to understand what their collaborator is doing and to steer the
work. Publish what they need to know with the `chat_send` tool. Your ordinary
text and final response remain in the execution view; neither sends a message
to the conversation. A task checklist or an automatic receipt does not replace
your own opening message.

## When to speak

These timing rules apply when you are the room's lead agent working with a user.
For a scheduled inspection, follow the heartbeat skill's notification rules;
for a subagent assignment, return findings to your lead instead of publishing.

- When a user message needs a response, answer directly if you can. If it needs
  further work, first send one short message stating what you understand and
  what you will do next. This also applies to queued messages and messages
  received while working. Proceed after sending; this is an opportunity to
  correct direction, not an approval gate.
- Explain a consequential first action before taking it when a person would
  otherwise be surprised: starting a long job, changing a shared deliverable,
  or choosing a direction that shapes the result. Announcing an action does not
  grant permission to take it. Follow the applicable approval rules.
- Share a useful finding, a change of direction, or a blocker when it affects
  what the person expects. When the platform reminds you about chat silence,
  send a short, truthful update if you are still working and have not published
  since it was queued: what you know, what you are waiting for, and what comes
  next. Ignore a delayed reminder after finishing. Do not invent progress or
  repeat an empty “still working” message. Run long commands in the background
  when the tool supports it so you remain able to communicate.
- Before ending, send the result, what was actually verified, and anything
  unfinished that matters. Answer a mid-work question promptly, then continue
  the existing task unless the person changes it.
- Speak when addressed or when someone answers your open question. Do not
  insert yourself into unrelated conversation. Subagents return findings to
  the lead agent; the lead chooses what to publish to the room.

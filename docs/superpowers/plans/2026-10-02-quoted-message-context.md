# Structured quoted message context

The Slides page question currently sends the page text as message content.
A page containing `@评审` or `<@cheese-reviewer>` can therefore name a
recipient the person did not ask. Prepending the current AI still names both.
Fences do not exclude mentions. Quoted text must be message data.

## Contract

- Keep authored `content` as the sole input to all mention parsers.
- Add optional `quoted_context`: `kind: slide-page`, `path`, `source:
  live | committed`, `version`, optional `task_id`, positive `page`, `text`.
- Preserve text/path exactly. Source identity belongs to the frozen, verified
  reader snapshot; never substitute current file metadata.
- Save the quote in the ordinary message block's meta and original idempotency
  result. A retried human request returns the first saved quote.
- Initial and live input use the same quote renderer, after recipient selection.
  Quotes never create a separate block, queue, summon authority or model path.
- Bound the combined authored content and quote strings by the existing
  100000-character message budget. Do not silently truncate quotes.
- Old clients may omit the field. Deploy the backend to all serving instances
  before frontend PR #2392 becomes Ready/merges; old Pydantic schemas ignore this
  new field. Never fall back to flattening.

## Implementation and verification

- [ ] Extract the typed input schema into block/message_input.py; test validation
  and exact preservation without importing the Windows-incompatible server.
- [ ] Add the shared quote renderer in block/quoted_context.py and use it in
  harness prompt_line, live input and already-selected mention deliveries.
- [ ] Thread typed data through the existing route/broker/persistence; reuse the
  existing authored-content mention parsers and request identity.
- [ ] Extend the existing HTTP/mention/real-time DB suites: inert friendly and
  canonical quoted mentions, preserved paths, no notifications, deliberate body
  mentions, first-quote retries, initial/live input, live receipt and replay.
- [ ] Run local pure checks/negative control; normal Draft CI runs real PG cases.
  No local fake PG pass and no production model/platform messages.

## Existing harness basis

The existing harness prompt_line and reply_quote already distinguish authored
speech from contextual data. prompt_line is provider-independent; both initial
and live messages go through publication_prompt and the existing compute/harness
send, including Claude Code and Codex. This change adds no provider-specific
message channel. Existing file attachments are inert paths but cannot preserve
inline page text: offered_attachments only embeds raster images; other files are
read later through tools and can change or disappear.

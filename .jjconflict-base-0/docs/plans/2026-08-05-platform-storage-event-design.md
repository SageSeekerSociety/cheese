# Platform Storage Event Design

## Intent and event contract

An exhausted runtime filesystem is a platform incident, not an AI answer and not a generic provider failure. The backend will recognize `ENOSPC` from both exception chains and provider-result text (`errno 28`, `ENOSPC`, or “No space left on device”). It will stop transient retries, because three immediate retries cannot create disk space, and persist one system event block with structured metadata: `event_type=platform_error`, `code=storage_exhausted`, `severity=error`, a stable title, and `retryable=true`. The human-readable fallback says the turn paused, project files and completed changes remain, automatic cleanup is running, and the user should continue later. Raw internal paths and exception dumps stay in logs rather than the conversation.

The normal `event_block` transport remains the envelope, so the incident survives reload and older clients still show its fallback text. The terminal `error` frame also carries the stable code for observability, but `persisted=true` prevents duplicate banners. Generic exceptions retain their current behavior. Runtime exceptions are classified through the same helper and do not schedule the normal five-second auto-resume when storage is exhausted.

## Frontend treatment

The conversation renders structured platform errors before ordinary event/action rows. The visual direction is restrained industrial utility: a compact warm-red rail, hard-drive warning glyph, small “平台资源” eyebrow, strong title, plain-language body, and an amber recovery status row. It is prominent enough to explain why nothing streamed without resembling a destructive modal or an AI chat bubble. The card uses existing application tokens, remains legible in light/dark themes, has `role=alert`, and requires no new dependency.

Unknown future platform-error codes degrade to the backend title/content, while old unstructured events remain centered gray lines. A small pure presentation helper makes metadata validation testable without mounting the large chat component. Backend regression tests prove ENOSPC is classified, persisted with metadata, sanitized, and not retried; frontend tests prove only structured platform errors become cards.

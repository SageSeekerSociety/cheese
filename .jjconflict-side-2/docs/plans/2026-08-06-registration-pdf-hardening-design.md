# Registration and PDF Import Hardening

## Goal

Close the gaps found while reconciling the archived frontend with the fused
CheeseX application: registration configuration must be enforced end to end,
PDF import must have bounded cost, and frontend error paths must remain safe
and understandable.

## Design

### PDF import

- Reject PDFs above a configurable 20-page ceiling before extracting pages.
- Process at most three pages concurrently.
- Pass `maxTasks` into the domain service and stop scheduling new page batches
  once enough drafts exist. Slice each batch result before uploading images so
  unused drafts do not create storage objects.
- Keep partial-page failure behavior, but fail when every attempted page fails.

This bounds CPU, temporary disk, concurrent LLM calls, and storage writes while
preserving the current preview/confirm API shape.

### Invite-only registration

- The public registration-config endpoint remains the source of truth.
- When invite-only mode is enabled, registration rejects a missing code before
  doing email or account work.
- Validate the code for an early useful error, then consume it after account
  creation with one conditional `UPDATE ... RETURNING`. A concurrent request
  that loses the final use fails, and the surrounding registration transaction
  rolls back the new account.
- The signup page loads the configuration, shows a required invite-code field
  only in invite-only mode, and carries the value through email verification to
  final registration. Email verification remains mandatory in both modes.

### Frontend safety and feedback

- Markdown renderer failures HTML-escape the source before returning fallback
  markup.
- Authentication pages use one helper that surfaces `BusinessError` messages,
  including API validation errors, and uses a stable fallback for unexpected
  failures.
- Space management controls render only for administrators; members see a
  non-interactive space heading instead of an empty menu.

## Verification

- Unit tests prove PDF page limits, concurrency, early stopping, and invite-code
  atomic-update outcomes.
- Frontend tests prove Markdown fallback escaping and business-error mapping.
- Backend focused tests, frontend Vitest, type checking/build, then live API,
  web, and real agent-start smoke tests after direct deployment.

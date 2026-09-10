# Central room sessions implementation plan

## Goal

Run private chats and ordinary-room Claude Code sessions on a configured central device. Keep task checkouts, shell, environment scripts, custom MCP processes and preview on the room's existing execution device. Preserve conversation history when moving an existing session.

## Placement and transport

Record the session device separately from the execution binding. Keep the existing room resource generation as the boundary for delayed requests and cleanup. The initial implementation uses one configured session host; scheduling a pool remains part of RFC #927.

Execution calls use the existing authenticated backend and device connector. The backend derives the device and resource from the room record. A central process cannot choose an arbitrary device or state directory through its request. Tool request IDs retain the executor's existing duplicate-write protection.

## Work items

1. Reconcile the experimental branch with main in a separate worktree. Preserve per-teammate private chats, environment preparation, warm startup and durable archived-room cleanup. Verify the private container follows the resource generation after reopening.
2. Extend `backend/app/domain/topic/models.py` and add an Alembic migration for session placement. Update `backend/app/domain/agent/device_provider.py` and `cloud_provider.py` to keep execution selection independent from session launch. Recovery must subscribe once per room and use the session device's connection.
3. Extend `backend/app/domain/agent/harness/claude_code/remote_execution/client.py` with connector-backed transport through a scoped backend endpoint. Reuse the executor's MCP bridge for custom stdio servers. Test authorization, interrupted calls and duplicate mutation IDs before wiring room launch.
4. Add execution bootstrap beside the existing launch helpers. Reuse the environment runner, task workspace CLI and preview helpers. Ship the execution runtime to the selected machine and refresh scoped credentials without copying model credentials there. Keep project hooks in the execution environment.
5. Route attachments, file controls, background task controls, project publication and preview to the execution device. Keep image prompt staging on the central session host as well. Pin controls to the running resource generation.
6. Move existing conversation files before resuming centrally. Stop the old worker before central ownership begins, preserve originals, and fail explicitly if the old host cannot supply the session. Extend transcript authorization and archived-room cleanup to cover both recorded locations.
7. Run backend regression checks and the existing CI acceptance with actual Docker and native Claude Code/RC. Add an ordinary-room acceptance covering two turns, file changes, custom MCP, preview and recovery through distinct session/execution locations. Update operational documentation to describe the resulting placement.

## Completion and deployment

Completion requires the updated branch to pass its relevant checks and a PR to expose the complete change for review. A merge to main triggers deployment: the configured central device, pinned Claude Code build, private executor image and existing-session transfer must be ready before that cutover. No pool scheduler or new cloud fleet is provisioned by this implementation.

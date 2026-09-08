# Project environment implementation plan

Goal: replace the project image selector with a shared script configuration and observable preparation on Cloud and Hosted Machine.

1. Add validated environment configuration in `backend/app/domain/project/environment.py`, a pinned configuration on Topic, and an Alembic migration. Migrate the known cheesex-dev selection to explicit scripts. Test revision stability, reserved variables, and configuration pinning.
2. Add a standard-library runner in `backend/app/domain/agent/environment_runner.py`. It executes Bash initialization and startup, writes per-attempt status and logs, reuses successful initialization, and terminates children on cancellation. Test real subprocesses, failure, retry, concurrent starts, and environment replacement.
3. Pass the room configuration through ChatService into the shared DeviceChannel launcher. Run preparation only on new agent processes, await its result before task delivery, and preserve live-process reattachment. Test the launch command and channel behavior together.
4. Replace sandbox-image routes with configuration, room status/log, and apply/retry endpoints. Apply only when the room is idle. Replace the frontend image controls with script fields and room status actions. Test authorization, room/project scope, and active-room refusal.
5. Delete the obsolete image configuration and dedicated image workflow. Update the environment paragraph in docs/spec.md and the design's implementation status. Run targeted pytest, migration checks, backend lint/type checks, frontend lint/type checks/build, and the applicable repository checks. Open a PR with verified behavior and the Hosted Sandbox limitation.

Hosted Sandbox remains unavailable. No production machines or databases are changed during implementation.

## Environment recovery and reviewed interface

Goal: show preparation in the room and give failed setup an executor.

1. Update `ProjectEnvironmentSettings.vue` with the reviewed purpose labels, timing, ordinary-variable visibility, and collapsed technical details. Add a room status card and distinguish a stopped agent from failed preparation.
2. Carry structured setup failures from `device_provider.py` into `chat.py`. After closing the failed turn, record a durable recovery event and summon the project overview once. The overview uses the base environment so a project script cannot prevent diagnosis.
3. Add overview-token-only inspection and repair endpoints beside the existing environment routes. Reuse the idle-room reset operation, compare the failed revision and attempt, change only that room, and allow one automatic restart per incident. Preserve the pending user message.
4. Test real runner exit states, failed-turn handoff, scoped recovery access, stale and active-room refusal, and bounded retries. Run relevant frontend and backend checks, then refresh the PDF and PR.

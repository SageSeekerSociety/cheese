# Project environment implementation plan

Goal: replace the project image selector with a shared script configuration and observable preparation on Cloud and Hosted Machine.

1. Add validated environment configuration in `backend/app/domain/project/environment.py`, a pinned configuration on Topic, and an Alembic migration. Migrate the known cheesex-dev selection to explicit scripts. Test revision stability, reserved variables, and configuration pinning.
2. Add a standard-library runner in `backend/app/domain/agent/environment_runner.py`. It executes Bash initialization and startup, writes per-attempt status and logs, reuses successful initialization, and terminates children on cancellation. Test real subprocesses, failure, retry, concurrent starts, and environment replacement.
3. Pass the room configuration through ChatService into the shared DeviceChannel launcher. Run preparation only on new agent processes, await its result before task delivery, and preserve live-process reattachment. Test the launch command and channel behavior together.
4. Replace sandbox-image routes with configuration, room status/log, and apply/retry endpoints. Apply only when the room is idle. Replace the frontend image controls with script fields and room status actions. Test authorization, room/project scope, and active-room refusal.
5. Delete the obsolete image configuration and dedicated image workflow. Update the environment paragraph in docs/spec.md and the design's implementation status. Run targeted pytest, migration checks, backend lint/type checks, frontend lint/type checks/build, and the applicable repository checks. Open a PR with verified behavior and the Hosted Sandbox limitation.

Hosted Sandbox remains unavailable. No production machines or databases are changed during implementation.

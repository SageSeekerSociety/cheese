# Project environment setup across machine modes

Status: implemented for the shared Cloud and Hosted Machine launch path; Hosted Sandbox and filesystem snapshots remain unavailable. Local tests cover scripts, tmux reuse, configuration access, and migration. Deployment on real Cloud and Hosted Machine installations remains unverified.

## Scope

A project has one environment configuration with an initialization script, a workspace startup script, and environment variables. Cloud, Hosted Sandbox, and Hosted Machine use the same configuration and execution sequence. Hosted repositories do not need Cheese-specific files. A script may call an existing repository script.

This design covers environment preparation. It preserves machine selection, ownership, visibility, model routing, and retirement rules. Hosted Sandbox remains unavailable until its existing isolation work is completed; this change does not enable it or substitute host access.

## Machine mapping

| Mode | Execution boundary | Installation behavior |
| --- | --- | --- |
| Cloud | A platform-provisioned machine dedicated to the room | Initialize tools in the room directory; run startup in each task checkout. The platform may reclaim the machine under existing lifecycle rules. |
| Hosted Sandbox | An OS-level boundary around the room's processes and writable files | Run both scripts inside that same boundary. No Docker dependency is introduced. |
| Hosted Machine | The enrolled machine, with its existing execution identity and host access | Initialize tools in the room directory; run startup in each task checkout. The platform never restores a snapshot over the host or uninstalls its software. |

The boundary is established before either user script runs and remains in force for their child processes. Setup gets no additional host access or privilege escalation. A separate HOME is an installation location, not an OS security boundary.

## Shared execution sequence

1. Resolve the machine using existing supply and visibility rules.
2. Establish the execution boundary and prepare the room's HOME and conversation directory. Task code is checked out separately when a task is opened.
3. Resolve the environment revision pinned to the room. Inject project variables while preserving platform-owned routing, identity, HOME, and workspace variables.
4. Run the initialization script if that revision has not completed successfully in this environment instance, or restore an equivalent supported cache. Record success only after exit zero.
5. Mark the room environment ready after initialization succeeds and start the agent. Persist its pinned configuration for task preparation.
6. On `cheese split` or `cheese worktree`, prepare the task checkout, then run the workspace startup script in that checkout before returning its path. Reopening a task reruns startup against its current files. Failure returns a nonzero exit code and a log location, retaining the files and the running room agent for diagnosis.

Initialization runs as an explicit Bash subprocess in the conversation directory; startup runs in the task checkout root. Both use the room's HOME, PATH conventions, environment variables, and execution identity. Shell exports do not propagate to later stages. Persistent variables belong in project configuration; tools installed under the room's local bin directory are added to PATH by the launcher. Scripts must tolerate reruns after interruption. Empty scripts succeed without spawning an installer.

Initialization installs reusable tools. Workspace startup synchronizes dependencies for the checked-out branch and starts required services. For this repository, startup can run `uv sync --frozen` in backend and `pnpm install --frozen-lockfile` in frontend. The platform does not infer dependency freshness from an unchanged initialization script.

A Bash interface does not translate Linux package commands into macOS commands. Platform defaults use portable installation paths; custom scripts can inspect the OS when required. Unsupported commands fail visibly without changing the selected machine or its permissions.

## Revisions and caching

Processes already running when this feature is deployed continue until their next restart. They have no preparation receipt, so settings show them as pending. Applying a configuration takes effect on the next agent start and preserves the room's files.

Store both scripts and variables in project settings as a versioned environment configuration. New rooms use the latest revision. Existing rooms retain their revision until an explicit apply action at an idle boundary; saving project settings never changes an active room. Applying a revision preserves task checkouts and uncommitted files, reruns initialization, and uses the new startup script when each task is next opened. It does not promise to undo earlier script side effects on a retained machine.

A successful initialization receipt is scoped to the actual environment instance and revision. It is not a project-wide assertion that every machine is prepared. A replacement machine or deleted environment invalidates the receipt. Concurrent starts for the same room share one preparation attempt; interrupted or failed attempts never count as ready.

Package caches and filesystem snapshots may accelerate preparation. Snapshot reuse additionally requires a compatible OS, architecture, runtime baseline, and visibility boundary. Snapshots must exclude credentials, agent session data, and unrelated room files. Unsupported snapshot restore runs the same initialization script normally. No whole-host snapshot is taken or restored for Hosted Machine, and no UI image selector is needed. Cross-machine snapshot support is not required for identical script semantics; its availability must not be reported as implemented until verified.

Snapshots retain files, not live services. Workspace startup therefore runs even after a successful restore. A configuration hash alone does not prove that a service is running or that branch dependencies are current.

## Failure reporting

The UI reports preparing, ready, stopped, or failed, with the stage, pinned revision, timestamps, exit status, and a persistent log. Start the log before running the script. Failed setup prevents ordinary task delivery and creates a recovery event for the overview agent. Overview uses the base environment without project scripts or variables, so project setup cannot block diagnosis. Its scoped API can inspect the affected room, revise only that room's configuration, and restart it once per incident. The restart retains pending user messages. A repeated failure stops automatic dispatch; a human apply action closes the incident. Overview can record the assistance it needs without restarting. Project defaults and other rooms are unchanged by a repair.

Do not print the injected variable dictionary into logs. Project variables are ordinary configuration visible to project members and the agent; arbitrary project secret storage is not implemented. The interface explains this visibility beside the variables.

Cancellation terminates the script and its children. A disconnected backend must reconcile the existing attempt before starting another. Preparation status is distinct from machine-online status and agent-turn progress.

## Code integration and acceptance

`CloudChannel` inherits `DeviceChannel`. Their shared machine preparation runs initialization, and the task CLI invokes the same runner for startup after creating or reopening a checkout. Keep Claude-specific SessionStart hooks out of the project configuration contract. Hosted Sandbox must eventually wrap setup and startup as well as the agent.

Replace the sandbox-image API and UI with environment configuration and preparation status. Remove the unused `sandbox_image` parameter and the obsolete cheesex-dev image workflow when the replacement is wired. Migrate any existing selected project to explicit scripts, preserving its intended dependencies. As part of implementation, update `docs/spec.md` to describe the script configuration and the conditions for reusing a cache.

Acceptance covers cold initialization, reuse, branch dependency changes, script failure, cancellation, reconnect without duplicate installation, configuration edits during active work, and recreation after machine loss. Exercise the same script fixtures on Cloud and Hosted Machine. Hosted Sandbox acceptance must prove that both scripts and their children stay inside the boundary before that mode is enabled. Until then, its integration remains unverified, and no release claims all three modes run.

## Sources

- [Cheese supply and visibility decisions](https://github.com/SageSeekerSociety/cheese/issues/282).
- [Hosted Sandbox scope and deferral](https://github.com/SageSeekerSociety/cheese/issues/358).
- [Codex cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environment): setup, maintenance scripts, and container caching.
- [Claude cloud environments](https://code.claude.com/docs/en/cloud-environments): setup snapshots and per-session project initialization.
- Current implementation: `backend/app/domain/agent/cloud_provider.py`, `backend/app/domain/agent/device_provider.py`, `backend/app/domain/agent/harness/claude_code/device_launch.py`, and `backend/app/domain/device/supply.py`.

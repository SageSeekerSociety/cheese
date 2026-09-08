# Claude terminal controls

Cheese keeps Claude Code in its persistent terminal and uses its RC v2 transport
for controls. Ordinary chat input still uses the existing RV socket. The control
bar sits above the terminal; permission requests and questions also appear above
the chat composer.

Subscription-backed device launches enable `--remote-control Cheese`. API-key
launches keep their existing behavior: the native CLI does not enable RC in that
mode. Controls remain unavailable until a worker connects. Existing terminals
adopt the new launch contract at the next task boundary, with the existing
session-resume path.

## Available operations

| Purpose | Native request subtype |
| --- | --- |
| Read session state and pending questions | `initialize` |
| Move a running command or subagent to the background | `background_tasks` |
| Interrupt the current turn | `interrupt` |
| Stop a task by its ID | `stop_task` |
| Change model or permission mode | `set_model`, `set_permission_mode` |
| Change effort or thinking budget | `apply_flag_settings`, `set_max_thinking_tokens` |
| Rename or recolor the session | `rename_session`, `set_color` |
| Find and read files | `file_suggestions`, `read_file` |
| Read workspace changes | `get_workspace_diff` |
| Read context and account usage | `get_context_usage`, `get_usage` |
| Inspect and reconnect MCP servers | `mcp_status`, `mcp_reconnect` |
| Start and finish MCP authorization | `mcp_authenticate`, `mcp_oauth_callback_url` |

The worker's response determines success. `backgrounded: false` means the task
was not backgrounded. A foreground WebFetch cannot be backgrounded through this
interface. An adaptive-thinking model may ignore a numeric thinking budget.
MCP authorization depends on the server's transport and authentication support.
`mcp_set_servers` is excluded because the interactive CLI does not register that
handler. A blocked CLI event loop still blocks its RC handler.

## Controller API

These routes require a human Cheese identity with access to the room, including
when the older flag controlling the rollout of topic permissions is disabled. Agent credentials
cannot approve their own tools or change their permission mode. Task places use
their parent room's access policy.

All paths below start with `/topics/{place_id}/agent` and return the standard
Cheese `{code, data, message}` envelope.

| Method and suffix | Result |
| --- | --- |
| `GET /control` | Current RC session ID, connection state, controls, tasks, pending questions |
| `POST /control` | Queue a native control; wait up to 15 seconds by default (`?wait=0` queues immediately) |
| `GET /control/{request_id}?session_id=…` | Delivery status and native result |
| `POST /answer` | Answer a pending native permission request or question |
| `POST /message` | Send human text through RC when explicitly requested by a controller |
| `GET /events?session_id=…&cursor=0-0` | Read up to 200 worker events with resumable cursors |

For example, after reading the current `id` from `GET /control`, post this body
to `POST /control` to perform the same background action as Ctrl+B:

```json
{
  "session_id": "cse_from_the_state_response",
  "request_id": "controller-generated-unique-id",
  "request": {"subtype": "background_tasks"}
}
```

Specify `tool_use_id` inside `request` to target a particular running tool.
Use a stable request ID when retrying the HTTP request; a different payload with
the same ID returns a conflict. Read the result before interpreting delivery as
execution. These operation IDs also appear in the existing `cheese api` command
catalog; no second CLI transport is required.

An answer body contains `session_id`, `request_id`, and `response`. Allow a tool
with `{"behavior":"allow","updatedInput":{...}}`, or deny it with
`{"behavior":"deny","message":"reason"}`. For AskUserQuestion, preserve the
original input and add `answers`, mapping each question's text to the answer.

## Transport and recovery

The existing metering proxy authenticates CONNECT with a place-scoped token
carrying `rc: 1`. Direct connections and the WebSocket tunnel use that same
credential; the hook token and machine OAuth ticket serve different purposes.
RC `/v1/code/…` requests go to the backend that already serves `/llm/admission`.
Worker bootstrap returns a session-scoped, one-hour JWT and the backend's
`connector_public_base`. Renewing the bridge replaces the worker epoch.

Redis stores sessions, pending questions, command results and event journals for
seven days of inactivity. Worker mutations check their epoch in the same Redis
transaction that updates state. Old workers cannot write into a renewed session.
A backend restart retains outstanding questions while Redis remains available.
Redis durability still depends on the deployment's persistence configuration.

Permission answers can be replayed while their request remains pending. The UI
removes a question after the CLI acknowledges processing its answer. Ordinary
controls are offered once because the CLI can execute a repeated control again.
An interrupted delivery remains `uncertain`; the controller must inspect current
state before intentionally issuing a new control. The server does not turn a
lost connection into a second interrupt or stop command.

## Provider visibility

This is a Cheese implementation of the native RC transport, not a session
registered on claude.ai. It does not patch the binary or forge the account's
profile or organization. The proxy enables the three RC feature flags in the
provider's feature response and preserves its other flags.

For RC-enabled connections, the proxy consumes `/api/event_logging/…` and the
configured Statsig hosts locally. It strips provider authentication and cookies
before forwarding RC bootstrap to Cheese. Launch settings set
`attribution.sessionUrl` to `false` before the first turn. This prevents new RC
URL attribution; it does not erase URLs already present in resumed history.

These boundaries do not establish that automation is undetectable. Inference,
account checks, network timing and any traffic outside the intercepted paths
remain observable to their destinations. No claim of invisibility to the model
provider follows from hosting RC locally.

## Rollout and checks

Deploy the backend routes and proxy addon together before enabling the new
launcher. Keep `CHEESE_ADMISSION_URL` pointed at the backend's `/llm/admission`
and `connector_public_base` reachable from devices. The existing proxy CA,
shared secret used to sign scoped tokens and Redis connection are reused. If RC has
no configured backend, the addon refuses the request locally.

The unit tests cover Redis recovery, duplicate controls, stale epochs and proxy
routing. HTTP tests use the real identity and room policy. Frontend tests cover
native errors, rejected backgrounding, pending answers and delayed results.
Native acceptance uses Claude Code 2.1.261 with isolated inference/account
fixtures; production account behavior remains a separate deployment check.

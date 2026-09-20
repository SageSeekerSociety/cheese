# Agent-owned configuration

## Accepted design

Each project agent owns its role instructions and an explicit model. Built-in roles initialize new agents; later changes to a built-in role do not update existing agents. Projects select a default agent, not a default model. Editing one agent affects its next turn in every room using it, without changing its identity, memory, or another agent.

Model availability remains a property of the project's connected supply. An explicit unavailable model is rejected rather than replaced. Both device and Cloud execution consume the agent's model. Changing model or instructions retires an idle reused runtime before the next turn so that a running process cannot retain the old configuration.

## Implementation

1. Add agent-owned configuration columns and backfill existing agents, including each project's implicit default agent. Snapshot custom role data before retiring its editable catalog. Preserve agent handles and memory keys. Validate the migration against an isolated test database and keep one Alembic head.
2. Update `agent_instance` services and schemas, project agent routes, and project creation. Expose model choices scoped to project supply. Make built-in role listings read-only creation inputs. Remove project model controls and mutable shared-role endpoints.
3. Update model resolution in `chat.py`, gateway launch settings, and runtime reuse. Resolve the same explicit model on every execution path; refresh only between turns when configuration changes.
4. Replace the shared-type editor with direct agent editing. Offer built-in starting configurations when creating an agent. Show the agent's saved model in the project agent list; remove role/model editors from project settings.
5. Test independent edits, preset initialization, default-agent creation, model validation, cross-room resolution, both supply paths, and runtime reuse after configuration changes. Run backend checks, frontend tests/typecheck/lint, and migration guards. Update the existing PR to the final scope; do not deploy production data changes in this task.

## Boundaries

No new team role library, bulk editing, model provider, or tool integration. Existing unsupported tool settings remain unavailable in the editor; their saved values are preserved during migration. Provider-reported model telemetry is separate work; this change guarantees which model Cheese requests.

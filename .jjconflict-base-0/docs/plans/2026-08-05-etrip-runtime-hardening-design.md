# Etrip Runtime Hardening Design

**Date:** 2026-08-05

## Problem

The Etrip CheeseX deployment can look completely empty after one failed project-list request, while a fifteen-minute AI scheduler continuously walks every project and can spend hours per sweep. More importantly, the project quality-gate configuration is writable without human project authorization and its command is executed by the backend process on the host. On the current bare-metal deployment that process is root.

## Constraints

- Repair the running host directly; do not use GitHub Actions as an operations transport.
- Preserve active workspaces and long-lived agent sessions.
- Make every production mutation recoverable from a timestamped backup.
- Avoid a full deployment-topology migration during an incident repair.
- Keep manual heartbeats and quality gates available, but make them safe by default.

## Design

### Human-only quality-gate administration

Reading quality-gate settings remains a normal project read. Updating them requires a verified human session whose handle is the project owner or a project member with the `lead` role. Agent-scoped sandbox tokens are intentionally insufficient. Commands are bounded in length and NUL bytes are rejected.

### Container-only gate execution

The backend never passes a gate command to a host shell. It launches a short-lived Docker container with:

- no Docker socket and no inherited backend secrets;
- no network;
- all Linux capabilities dropped and `no-new-privileges`;
- explicit CPU, memory, PID, and wall-clock limits;
- only the selected topic worktree mounted;
- bounded output returned to the application and full output written to the gate log.

If Docker or the configured image is unavailable, the gate fails closed. There is no host-execution fallback.

### Independent deterministic maintenance

Automatic AI heartbeats default to off in production. Idle sandbox cleanup is moved to a separate daily runner, so disabling model-consuming heartbeats does not disable container lifecycle maintenance.

### Honest health and self-healing

The backend exposes a cheap process health endpoint and a dependency-aware readiness endpoint. A systemd timer checks process health locally and restarts the service only after three consecutive failures; dependency outages do not create restart loops.

### Frontend stale-while-revalidate project rail

GET requests retry short-lived network and gateway failures. The project rail initializes from a session-scoped, user-scoped cache, replaces it on a successful refresh, and retains the last known list when refresh fails. A transient API error therefore cannot render “all projects deleted.”

## Rollback

Before applying the live patch, back up every changed source file, the built frontend, environment configuration, and systemd additions under a timestamped `/opt/cheesex-backups` directory. Rollback restores those files, disables the new timer, rebuilds the prior frontend if needed, and restarts `cheesex.service`.

## Verification

- Unit tests prove gate commands are expressed only as constrained Docker argv and timeouts forcibly remove the exact gate container.
- Integration tests prove anonymous users, agents, ordinary members, and outsiders cannot update quality-gate settings while owners/leads can.
- Frontend tests cover retry classification and project-cache isolation.
- Production probes cover backend health/readiness, project listing through Uvicorn/Caddy/Funnel, unauthorized gate mutation, scheduler state, timer state, and repeated-request latency/error rate.

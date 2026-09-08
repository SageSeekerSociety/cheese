# Team Cloud Compute Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Put MicroCloud provisioning in the existing team compute-registration page and make provisioned machines usable by every project in that team, while preserving topic affinity.

**Architecture:** The team owns resource permissions and quotas. A MicroCloud machine records one project as its billing/audit owner, while enrollment shares its device with that project's team. Projects store explicit defaults and favorites; room choices never rewrite them. See [the current project configuration plan](2026-09-07-project-compute-configs.md).

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, Vue 3, Vuetify, TypeScript, pytest.

---

## Task 1: Project defaults supersede team defaults

**Files:**
- Modify: `backend/app/domain/team/models.py`
- Create: `backend/alembic/versions/<revision>_team_compute_profile.py`
- Modify: `backend/app/api/routes/teams.py`
- Test: `backend/tests/integration/test_team_devices.py`

1. Migrate existing team and project profile defaults into explicit project configurations.
2. Remove the team default field and endpoints; project owners and leads manage project defaults.
3. Team membership governs resource use; adding a device to a team shares it with its projects.
4. Verify project management permissions separately from room resource use.

## Task 2: Resolve topic compute through the documented hierarchy

**Files:**
- Modify: `backend/app/api/routes/topics.py`
- Modify: `backend/app/domain/agent/chat.py`
- Test: `backend/tests/integration/test_topic_compute_profile.py`
- Test: `backend/tests/unit/test_compute_pool.py`

1. Resolve `room choice -> project default -> deployment default` in both the read API and turn execution.
2. Room selection changes only that room.
3. Preserve the first-turn freeze (a topic with an `agent_sessions` row is pinned) and the offline-device no-drift rule.

## Task 3: Bind provisioned cloud machines to the team

**Files:**
- Modify: `backend/app/domain/machine/services.py`
- Modify: `backend/app/api/routes/machines.py`
- Test: `backend/tests/integration/test_project_machines.py`
- Test: relevant machine enrollment unit tests

1. During enrollment, bind the generated device to `project.team_id`; do not create a project-only compute island.
2. Allow team members to read the inventory. Restrict create/delete, which allocate persistent resources, to team owner/admin; retain project lead/owner authorization only for legacy teamless rows.
3. Verify outsiders and agents cannot inspect or mutate machines.

## Task 4: Put provisioning on the team compute page

**Files:**
- Modify: `frontend/src/views/teams/detail/Compute.vue`
- Modify: `frontend/src/views/ProjectSettingsView.vue`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/cx_types.ts`
- Modify: `frontend/src/types/teams.ts`

1. Put defaults and favorites first in project settings.
2. Keep permissions, quotas, and device registration on the team page.
3. List MicroCloud machines across the team's projects, showing provisioning, AI setup, enrollment, online state, billing project, and resource size.
4. Let team admins provision via a dialog. Select a billing project when the team has several; use 4 cores, 8 GiB RAM, and 64 GiB disk as the explicit defaults.
5. Poll only while a machine lifecycle is transitional and stop polling on unmount.
6. Keep self-hosted registration on the same page and avoid duplicate cards once a cloud machine has enrolled as a device.
7. Require an explicit destructive confirmation before deleting a machine.

## Task 5: Verify without spending or deploying

1. Run focused backend tests for team compute, topic selection, machine authorization, and enrollment.
2. Run backend type/lint checks for touched modules.
3. Run frontend type checking and linting for touched files.
4. Exercise the authenticated list/default APIs locally. Do not create a real MicroCloud machine in automated verification because that allocates billable persistent infrastructure.
5. Review the final diff against execution-architecture v4. Do not stage, commit, push, or deploy from this shared repository without a separately scoped approval.

## Task 6: Direct dev deployment after approval

1. Preserve registry-backed deployment as the default and add an explicit local-image mode for operator-built backend and frontend images.
2. Refuse local deployment unless both requested images already exist on the box.
3. Capture and restore the exact previous backend and frontend image references on a failed health check.
4. Build from the committed feature revision on dev with durable logs, back up the external database, run migrations, and deploy through the same health gate.
5. Verify the live containers, migration head, backend health, frontend health, and published image revision. Do not allocate a paid MicroCloud machine during the smoke test.

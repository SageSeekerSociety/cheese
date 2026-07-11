# Fusion Unification Plan — "naturally cheese + our AI, one system"

Goal (user): 完成所有迁移 — dissolve the seams so the merged app is genuinely ONE
system (知是 product + our AI layer), not two glued halves with fallbacks / cx_
namespacing / route collisions.

## What the domain maps established (2026-07-11)

1. **Main int `project` table is DEAD** — SQL-only (migration `a95752502bb0`), no
   Python domain/service/route; frontend `/projects/:id` hits the old
   `VITE_NEW_API_BASE_URL`, unserved here. Only `knowledge.project_id` (int) +
   `project_membership` + the orphan frontend reference it. → **retire, don't
   migrate onto it.**
2. **知是's real product = Space(机构, int) → Task(赛题, int) → Team → Submission.**
   Live, rich, the actual product.
3. **cheesex = the AI layer** (Project/Topic/Block/Agent/Memory, uuid). Live.
4. **The `cx_` rename was a botched half-merge:**
   - `cx_space` (3-col uuid stub) duplicates main `space` (10 cols + 6 satellite
     tables). Redundant. Empty in DB. **CONFIRMED BUG:** cx_spaces.py + spaces.py
     both register `/api/spaces`; the empty cx_ stub wins → real 知是 spaces
     (space table has 3 rows) are hidden (`GET /api/spaces` → total=0).
   - `cx_task` duplicates main `task`, BUT adds a net-new `TaskTemplate` +
     `TaskApplication` market (spec §4.2). Keep the market; drop the duplicate
     `Task` stub. cx_task already imports main `space`.
   - `cx_notification` is a genuinely DIFFERENT feature (AI/topic alerts:
     level/kind/project-scoped) vs main `notification` (social feed). Not a
     duplicate — collided on the word only. Its event pipeline
     (config/events/handlers/publisher) has BROKEN imports (`NotificationType`
     absent). Fix + clarify name; do NOT merge into main notification.
   - `cx_tasks.py` `GET /api/tasks/{id}` collides with main `tasks.py`.

## Target model

知是 product shell stays int (space/task/team). cheesex Project (uuid) is the AI
workspace that **attaches natively to a 知是 Task/Team** (not a separate
`/cxproject` universe). Delete duplicate/dead/broken cx_ stubs; one notification
story; one frontend token/API; no fallbacks.

## Phases (each: edit → verify import + fresh-DB upgrade + serve → commit)

- [x] **P1 backend de-dup**
  - [x] P1a cx_notification: deleted the dead broken event pipeline (6 files).
  - [x] P1b: removed the duplicate cheesex Space CRUD stub (no real collision —
        main space is `/spaces`, cheesex stub was `/api/spaces`; stub was dead).
  - [x] P1c cx_space → main space: repointed `TaskTemplate.space_id` +
        `custom_roles.space_id` uuid→int (→`space.id`), rewired dashboard, deleted
        `app/domain/cx_space/`, migration b8e1c0a5f7d2. Finished a half-done merge
        (market was silently broken). Verified: 3 real spaces.
  - [x] P1d cx_task: deleted 3 dead broken stub files (deadline_scheduler,
        task_ai_advice_service, visibility_service) + orphans; kept the market
        (TaskTemplate/Application/Task — net-new, NOT a duplicate of main task).
- [x] **P2 retire dead int project**: dropped `project` + `project_membership`
      (migration c9f2a3b40e15). cheesex uuid `projects` is the only project.
- [x] **P3 unify** (mostly): one auth token (main JWT carries a `handle` claim;
      cheesex reads it — verified one token authenticates both `/spaces` and
      `/api/projects`); one backend (frontend `.env.local` → :8799, avatars load,
      no fallback); dropped the `/cx` URL prefix (`/project/:id`). The workspace
      is reachable natively from the shell via the rail project tiles.
- [ ] **P4 native Task/Team → Project** (needs a product decision — see below).
- [x] **P5 kill fallbacks**: avatars load from the backend `/avatars` (fixed the
      dead `:7777` base URL, not a fallback); AI pool is the Claude subscription
      profile via `AGENT_DEFAULT_PROFILE` (GLM pool is out of balance).

## P4 open decision

"A 知是 Task/Team page opens its cheesex Project natively" needs an association
that doesn't exist yet: a cheesex Project (uuid, owner_handle + member handles)
has no link to a 知是 Team/Task (int). Options: (a) the rail already gives native
per-user project access (matches the original "each project = a rail icon"
vision) — treat P4 as satisfied; (b) add a nullable `project.team_id` (→ 知是
team) + a "AI 工作台" button on the team page; (c) link via the existing
TaskTemplate/Application market (space publishes template → team applies with a
project). Needs the user to pick the relationship model.

## Verification harness

- Fresh DB: drop+create `fusion_test`, `alembic upgrade head` (clean env), restart
  `:8799`.
- Import health: `python -c "import app.main"` + per-route-module import loop.
- Product surface: `GET /api/spaces` must return the 3 real spaces.
- Agent: smoke one turn (Claude subscription pool).

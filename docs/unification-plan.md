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

- [ ] **P1 backend de-dup**
  - [ ] P1a cx_notification: fix broken event-pipeline imports (or delete if dead).
  - [ ] P1b route collisions: remove cx_spaces stub CRUD (`/api/spaces`, `/{id}`)
        + cx_tasks `GET /api/tasks/{id}`; keep template/market endpoints on
        non-colliding paths. main space/task own `/api/spaces` `/api/tasks`.
  - [ ] P1c cx_space → main space: repoint `TaskTemplate.space_id` uuid→int
        (→`space.id`), rewire cx_space importers (dashboard, cx_spaces, cx_task)
        to main space, delete `app/domain/cx_space/`, migration.
  - [ ] P1d cx_task: drop duplicate `Task` stub (uuid), keep TaskTemplate +
        TaskApplication; rewire ProjectTaskLink if needed; migration.
- [ ] **P2 retire dead int project**: drop `project` + `project_membership`
      tables + orphan `/projects` frontend + `knowledge.project_id`.
- [ ] **P3 frontend unify**: one auth token; fold `/cxproject` workspace into the
      知是 shell as a native surface; drop the `/cx` prefix seam.
- [ ] **P4 native integration**: a 知是 Task/Team → "AI 工作台" opens its cheesex
      Project natively.
- [ ] **P5 kill fallbacks**: run the avatar service (real avatars); AI pool is one
      configured native pool (no fallback chain).

## Verification harness

- Fresh DB: drop+create `fusion_test`, `alembic upgrade head` (clean env), restart
  `:8799`.
- Import health: `python -c "import app.main"` + per-route-module import loop.
- Product surface: `GET /api/spaces` must return the 3 real spaces.
- Agent: smoke one turn (Claude subscription pool).

## 状态摘要

操作卡落地顺序的**第 3 步**。第 1 步（人类授权动作前移）、第 2 步（`accept_via_pr`）已在 main。

**本卡只做「只登记不执行」**：建 operation registry + 清单（manifest）格式，跑通
「写一个操作请求 → 它变成一个只碰 `ops/requests/` 的 PR → 卡面把七问显示出来」。

## 目标（验收标准）

1. **operation registry** —— 有哪些 operation、每个的 `blast_radius`、参数 schema、可逆性。
   registry 是**权威**：卡面七问尽量从 registry + args 推出来，不让芝士自由发挥填文本。
2. **清单格式 + schema 校验** —— `ops/requests/<topic8>-<operation_id>.yaml`，字段即卡面七问
   （`operation_id` / `args` / 执行什么 / 打到哪 / 可逆吗 / 炸了会怎样 / `interruptible`）。
   **提交时就校验**，格式不对当场失败，不等到执行。
3. **硬约束：一个 ops PR 只能碰 `ops/requests/` 下的文件** —— 由必需检查强制。
   即使还不执行也必须先立，否则格式一旦被用起来就晚了。
4. **禁止隐含的「当前」** —— `commit_id` 只锁得住「PR 的 head 没变」，锁不住「main 变了」。
   一切目标写成具体 sha / 具体版本，**schema 层面拒绝**「当前 main」这类隐含参数。
5. **卡面** —— 把七问显示出来即可，**不接执行结果回写**。

## 范围红线（绝对不要越）

- **不接执行链**。`pull_request_review` 触发执行是第 4 步，且必须从 device-smoke（只读）起步；
  prod 是第 5 步且永远两次人工授权。
- **拍板 6（2026-08-11）：每次执行都要一次新的 approve。** 长期信封 PR 只允许
  `blast_radius: none`；有副作用的一律一次性 PR、执行完即关。信封省的是解释成本，不是人点那一下。
  → `blast_radius` 就是这条拍板的落点，**不设计成「授权一次可反复执行」**。
- **开 PR 只能用人的授权**，绝不用平台 App 的 `write_token()`。
  **不需要给 App 加任何新权限**——若方案需要加权限，说明走错了，停下报告。

## 约束

- **并发**：`backend/app/domain/review/` 有四条线在跑，`backend/app/domain/topic/` 有一条。
  本卡优势是**几乎全是新建文件**（新 `ops` 域、`ops/` 目录、新 workflow）——
  diff 尽量限制在新文件里，改既有文件要说明为什么。基于最新 main 开工。
- 分层 Route → Service → Repository → Model；Pydantic v2；全类型标注，不用 `Any`。
- **本步不落库 → 不写 alembic 迁移**（顺带躲开 main 上曾出现的 2 heads）。
- 测试至少覆盖：合法清单通过、隐含「当前 main」被拒、ops PR 夹带非 `ops/requests/` 文件被拒。

## 进展

- [x] 接手、范围确认、活文档改写
- [x] `backend/app/domain/ops/` registry + 清单 schema + 校验器 + 七问推导
      （`registry.py` / `manifest.py` / `guard.py` / `schemas.py` / `services.py`）
- [x] `ops/requests/` 目录约定 + 格式说明（`ops/README.md`）
- [x] CI 必需检查 `ops-guard.yml`：ops PR 路径白名单 + 全量清单校验
- [x] 卡面七问展示：`GET /api/projects/{id}/operation-requests`（只读，不接执行回写）
- [x] 单测 472 行（合法清单、隐含「当前」被拒、夹带非 ops 文件被拒）
- [x] 检查跑绿：ruff 通过、alembic 单 head、ops 单测 40 passed、
      pyright 对 `app/domain/ops` + `routes/ops.py` + `scripts/ops_manifest.py` 0 errors
      （沙箱没有 Postgres，check.sh 跳过全量 pytest；CI 上会跑）

## 落地形态（与最初设想的两处差异）

- **不提交示例清单文件。** 规矩 3 要求「ops PR 只能碰 `ops/requests/`」，而本 PR 同时带着
  backend 代码——真放一份 `.yaml` 进去，`ops-guard` 会当场把自己这个 PR 判红。
  所以示例以完整 YAML 的形式写在 <&ops/README.md> 里，`ops/requests/` 本体只留 `.gitkeep`。
  第一份真清单会是它自己那一个 PR，这正是格式想要的形状。
- **卡面停在 API 层。** 后端把七问按 registry 推导好后整份吐出来，前端渲染留给接执行链的那一步——
  本卡范围内没有必要动 `frontend/`，也就不去和并发的几条线抢文件。

## 改动范围

15 个文件、全部新建（另加 `pyproject.toml` / `uv.lock` 各一行：pyyaml）。
没有 alembic 迁移，没有触碰 `backend/app/domain/review/` 与 `topic/`。

## 采纳时的合并冲突（已解）

主分支上另一条线同时在改 CI，5 个 workflow 撞车。逐个语义化合并，不是二选一：

- <&.github/workflows/test.yml> / <&.github/workflows/e2e.yml> —— 并发键统一成
  `ci-*-${{ github.ref }}-${{ ref == main && sha || '' }}`（build.yml 早就是这个形状）。
  按 `event_name` 分支的写法保护 main 的效果一样，但会让**非 main 分支的每次 push 各自成组、永不取消**，
  白占那三个 cheese-ci 槽——而这段注释本身讲的就是省这几个槽。e2e 另外保留了本卡加的
  顶层 `permissions: contents: read`（主分支那边没有）。
- <&.github/workflows/build-tmux.yml> —— 取 30m。build-sandbox 的 60m 是**从零构建 sandbox 基底**的上限，
  而这个 job 是 FROM 已完成的基底再叠 tmux+ttyd，实测 4.8m；照抄 60m 等于按错误的 job 定界，
  还让那个单槽多被扣一倍时间。
- <&.github/workflows/deploy-dev.yml> —— `ubuntu-latest`。上面那段（已合并的）注释写着这个 job
  "只打印再退出，所以跑 hosted"，还记了它 2026-08-07 为送一行报错排了 1h45m 的队；
  留 self-hosted 会跟注释自相矛盾。timeout 5 两边一致，保留。
- <&.github/workflows/frontend.yml> —— 两边各写了一份，取并集：
  本卡的 SHA 钉版 + 工具链存在性校验 + ratchet typecheck + ratchet 自测，
  主分支的 `persist-credentials: false` + vue-tsc 堆内存上调 + **vitest 全量套件**（本卡漏了）。
  pnpm 走 corepack 认 `packageManager`（9.15.3），不用 `pnpm/action-setup` 硬钉 11——
  那会盖掉仓库钉的版本，正是 `--frozen-lockfile` 要防的漂移。

合并暴露了两个真问题，一并修了：

1. <&frontend/vite.config.ts> —— 两份意图凑到一起才会炸：`scripts/*.test.mjs` 是 node:test 文件，
   却落在 vitest 默认 include 里，vitest 以 "No test suite found" 把整轮判红。
   给 `test.exclude` 加了 `scripts/**`（保留 `configDefaults.exclude`）。
2. <&.github/workflows/ops-guard.yml> —— 主分支新加了「Action 必须钉 commit SHA」这条仓库守卫，
   本卡的 workflow 早于该规则，`@v4` 被判红。已钉成仓库里既有的那两个 SHA。

## 下一步

第 4 步（`pull_request_review` 触发执行，从 device-smoke 只读起步）不在本卡范围内。

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

## 采纳时的合并冲突（两轮，已解）

同一批 CI 改动在主分支上有另一条并行线，采纳时撞了两轮。

**第二轮的关键发现：真正的破坏不在冲突标记里，而在标记之外自动合并出来的行。**
`test.yml` / `e2e.yml` 的并发块两边都改过，jj 逐行合出了一个**两边都没有**的杂交体：

```yaml
group: ci-test-${{ github.ref }}      # ref-only 键
cancel-in-progress: true              # 且开着取消
```

这正是两边长注释都在防的组合——往 main 合一次就会掐掉上一个 commit 还没跑完的套件，
而被取消的 run 不是红的，main 就带着没验过的代码、且 commit 上什么都看不出来。
它落在 `>>>>>>>` 之后，只解冲突标记根本不会看到。已按主分支的形状修回：

```yaml
group: ci-test-${{ github.event_name == 'pull_request' && github.ref || github.sha }}
cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

**同一类问题还咬了一份源码。** <&frontend/src/views/spaces/detail/AuditTask.vue> 的
`} catch (error) {` 在工作区里被合成了 `} catch {`，而 `error` 在块内被用了两次——
**两个 parent 都是修好的版本，只有合并结果是坏的**。这就是 frontend.yml 头注释里点名的那个
ReferenceError，刚被修好又被合回来。ESLint 抓到（`no-undef`），已改回。

为了确认没有第三处，写了个对拍：凡是两个 parent **内容一致**的文件，工作区必须与之逐字节相同。
1680 个文件里除上述外无一例外（5 个 lark skills 目录是 symlink，非真实差异）。

其余四处按语义合，多数是取主分支那份（它已经是两边意图的并集，我这边没有新东西可加）：

- <&.github/workflows/build-tmux.yml> —— 30m。主分支那版把「另一个候选是 60m、
  为什么选有实测支撑的 30m」写全了，比我的更完整；只补了一句两者不可比的原因
  （build-sandbox 从零构建基底，本 job 是 FROM 成品基底再叠 tmux+ttyd，实测 4.8m）。
- <&.github/workflows/deploy-dev.yml> / <&.github/workflows/e2e.yml> —— 直接取主分支那份，
  与主分支逐字节一致。e2e 我上一轮加的顶层 `permissions: contents: read` 主分支已经有了。
- <&.github/workflows/frontend.yml> —— 取主分支那份（它比我上一轮的多了 `--dir src`
  和更细的注释），折回三处：pnpm 为什么走 corepack 而不是 `pnpm/action-setup` 硬钉版本、
  为什么显式 cache 而不用 setup-node 的 `cache: pnpm`、以及把 vitest 数量改成实测的 29 文件/299 用例。
  主分支那句「真正的修法是在 vitest 配置里 exclude，本次合并范围外」已经过期——
  那个修法就在本卡里（见下），注释改成两道防线并存。

上一轮已修、本轮保留的两处：

1. <&frontend/vite.config.ts> —— `scripts/*.test.mjs` 是 node:test 文件却落在 vitest 默认
   include 里，`pnpm exec vitest run` 会以 "No test suite found" 整轮判红。
   `test.exclude` 加了 `scripts/**`（保留 `configDefaults.exclude`）。
   现在 `--dir src`（workflow 里）和配置里的 exclude 各挡一道，任一单独都够。
2. <&.github/workflows/ops-guard.yml> —— 主分支新加了「Action 必须钉 commit SHA」的仓库守卫，
   本卡的 workflow 早于该规则，`@v4` 被判红。已钉成仓库里既有的那两个 SHA。

**上一轮报告里有一句判断是错的，更正：** 我说按 `event_name` 分组会让「非 main 分支的每次 push
各自成组、永不取消，白占 cheese-ci 槽」。这两个 workflow 的 `on:` 只有
`push: branches: [main]` 和 `pull_request:`，根本不存在非 main 的 push 事件——
两种写法在这里行为完全等价。所以本轮直接取主分支的形状，不再折腾。

## 下一步

第 4 步（`pull_request_review` 触发执行，从 device-smoke 只读起步）不在本卡范围内。

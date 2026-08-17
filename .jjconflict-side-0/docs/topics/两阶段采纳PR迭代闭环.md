> 状态摘要（分身开工后改写，替代原始任务简报）

## 目标

两阶段采纳（PR迭代式）已经能开出真 PR（PR #209 验证过），但 CI 变红后永远卡住——平台从没真正重推过新 commit。这次要把"检查红了 → 芝士改代码 → 平台自动重推 → CI 重跑"这个闭环补上，一个 PR 交付。

## 范围（只动这些）

- `backend/app/domain/review/`
- `backend/app/domain/oauth/`
- 必要的测试
- `.github/workflows/claude-review.yml`（只加 paths-ignore，不碰判定语义）

明确不碰：`deploy-dev.yml` 门禁条件、`build.yml` concurrency 策略、任何迁移文件。

## 技术方案（已读代码定案，不再重新诊断）

**根因**：`AcceptService._advance_pr_checks`（`review/services.py:610`）里 `live_head != card.pr_head_sha` 那段注释写的是"芝士 pushed a new commit"，但沙箱连不到 github.com、也没有 GitHub 凭据，芝士客观推不动。`push_topic_branch_for_github_pr()`（`workspace/service.py:1036`）全仓只在 `_open_pr_for_accept` 里被调用一次（PR 首次开出时），轮询侧从没再调过它。

**修法**：在 `_advance_pr_checks` 查 CI 状态之前，加一段本地 head 检测：
1. 复用 `ws.ensure_repo` / `ws.branch_for_topic` / `ws.snapshot_worktree`（都是已有公开函数，不改 `workspace/service.py`）算出本地 topic 分支当前 head（`snapshot_worktree` 先把这轮 pending 的 Write/Edit 折进一个 jj commit，再 `git rev-parse` 分支）。
2. 跟 `card.pr_head_sha`（复用现有字段，语义变成"上次推送/已知的 remote head"）比较：相同 → 什么都不做，避免每轮轮询都无脑重推；不同 → 说明本地有新提交，调用现成的 `ws.push_topic_branch_for_github_pr`（不加 force，remote_branch 用 `github_pr.pr_branch_name(topic.id)` 重新算，是确定性的同一个分支名）重推。
3. push 失败（`ValidationError`：token 失效/网络/非快进）→ 本地 `try/except` 兜住，`logger.warning` 记下来（不是静默吞掉），不更新 `card.pr_head_sha`、不动 `card.note`，直接让这轮 CI 检测跳过，下一轮轮询自然重试——不会把卡标记成永久失败。
4. 推送成功 → 更新 `card.pr_head_sha` 为新 head，清空 `card.note`（复用现有的"清空 note 让新一轮失败还能再 nudge 一次"去重逻辑，跟原有 `live_head` 同步块共享同一套 dedup，不会打架）。

**`_nudge_pr_fix`**：现在的原话"提交后推送新 commit 到这个 PR 分支"要改成不误导芝士的版本——芝士在工作区里修好、提交（jj commit / Write+Edit 会自动被下一轮 snapshot 折进去）即可，剩下的平台会自动做。

**`claude-review.yml`**：已核实 main 上现在完全没有 `paths-ignore`（PR #209 的范围过滤版、PR #210 的 `["**"]` 全禁用版都还没合并），不存在"两个执行者已经各改一份"的冲突，直接在 `pull_request:` 触发上加 `paths-ignore: [docs/**, "**/*.md"]`。

**`list_user_connections`**（`oauth/services.py:502`）：补三个字段——`login`（来自 `raw_profile.get("login")`，GitHub 场景下就是 `@用户名`）、`tokenExpires`（`token_expires` 原样透出，null 就是"看不出来"或"这个 provider 不过期"）、`hasRefreshToken`（`bool(refresh_token)`）。不改动 token/密文本身的返回。

## 验收标准（简报原文，逐条对应交付）

1. 重推逻辑：贴代码说明"何时推/怎么判断/失败怎么降级"。
2. 测试真覆盖闭环：至少一个用例证明"本地有新提交 → 轮询时被推到 PR → pr_head_sha 更新"，不许把被测函数整个 monkeypatch 掉。
3. `_nudge_pr_fix` 新措辞原文。
4. `claude-review.yml` 最终样子 + 动手前 main 上的样子（证明没有重复叠加）。
5. `list_user_connections` 新增字段清单，确认没有泄露 token/密文。

## 当前进度：四处代码改动 + 测试都已落地，等验收

- [x] 读完 `review/services.py`、`workspace/service.py`、`github_pr.py`、`oauth/services.py`、`oauth/models.py` 相关代码，方案已定案（见上）。
- [x] 核实 main 上 `claude-review.yml` 现状（无 paths-ignore）。
- [x] 落地四处代码改动（重推逻辑 / `_nudge_pr_fix` 措辞 / `claude-review.yml` paths-ignore / `list_user_connections` 新字段）。
- [x] 补测试：`test_accept_pr.py` 新增两个用例——`test_repush_pushes_new_local_commit_and_updates_pr_head_sha`（真实 jj/git 提交 → 轮询自动重推 → `pr_head_sha` 更新，且不改动时不重推）、`test_repush_failure_degrades_without_failing_the_card`（push 失败两轮都不进 `errors`、不永久失败，恢复后下一轮自动追上）；`test_oauth_service.py` 新增两个用例覆盖新字段（含"不泄露 access_token"断言）；`_nudge_pr_fix` 新文案在既有的 `test_poll_ci_failure_nudges_cheese_once` 里加了断言。
- [x] ruff/pyright 在改动文件上全绿；oauth 单元测试 80/80 通过；review 集成测试 21/25 通过（4 个失败逐一定位到同一个沙箱级问题，见下，与本卡改动无关）。
- [x] 第一次递验收卡被质量闸门打回：`ruff format` 会重排 `test_accept_pr.py`（纯格式，不是 lint 错误）。已跑 `ruff format` 修好，改动文件重新确认 `ruff check` + `ruff format --check` 全绿，pytest 重跑确认还是同样的 91 passed / 3 failed（跟格式化前一致，逻辑没被格式化动到）。闸门里 pyright/pytest 被 SKIP 是环境限制（闸门容器连不上外网下 pyright 的 python-build、没有 Postgres），不是我改的东西的问题。
- [x] 在这个沙箱里跑了一遍完整 `bash .claude/scripts/check.sh --full`（沙箱这边有网，pyright 能装上，比闸门那份更严格）：`ruff` PASS（677 files already formatted）、`pyright` PASS（0 errors, 0 warnings，全仓，不只是改动文件）。全量 pytest（`-n 4 --reruns 2`）跑了完整 tests/ 目录，会把这个沙箱本身缺 Redis/Valkey 的问题带出一大片跟这张卡无关的失败（team/topic/user_follow 等），不重新贴那些数字——跟这张卡相关的部分已经在改动文件的定向测试里核实过（91 passed / 3 failed，3 个失败见上）。
- [x] 重新递验收卡。

## 沙箱环境限制（如实记录，不是我改动引入的问题）

这个沙箱的 `/work` 是 `jj workspace add` 出来的工作区，其 `.jj/repo` 指针指向共享的 `/de808b13-ffd2-4b8a-9d1d-fba7babe389f/.jj/repo`，而那个共享仓库的 `config-id` 文件属主是 uid 1001（后端服务自己的用户），沙箱统一是 uid 1000 读不了，导致这个沙箱里**任何**新的 `jj git init --colocate` 调用都会报 `Internal error: Failed to determine the secure config for a repo`。

已用两个完全独立于本次改动、之前就存在的测试证实这不是我引入的问题：`test_upstream.py::test_accept_pushes_back_and_fires_hook`、`test_accept.py::test_accept_happy_path_archives_topic`（走的是老的直接 merge 路径，代码我一行没动）在这个沙箱里同样报一模一样的错误。跟父话题记录里"本容器 git/jj 读不出 HEAD 是沙箱权限问题"是同一个问题。

因为我的重推逻辑会在**每次轮询**都调用本地 git 检测，这会把这个沙箱级问题传染给所有原本不关心 git 的既有测试（比如 CI-绿-触发合并那几个用例），所以我给 `_pr_ready` 测试 fixture 加了 `patch_local_head` 开关（默认 True，之前不碰真实 git 的测试保持不碰；只有我新加的两个重推测试传 `False`，走真实 git/jj 链路）——这本身是个独立于沙箱 bug 的测试隔离改进。

`test_accept_pr.py` 里剩下的 4 个失败（我的两个新重推用例 + 两个已存在的直接-merge 降级用例）全部卡在同一处 `jj git init --colocate`，已用 `docs/topics/` 里能查到的证据核实是这一个根因，不是四个不同问题。我的两条重推测试逻辑上是对的（本地真实 git 提交检测 + 真实 rev-parse，只 fake 了连不到 github.com 的那一跳网络请求，跟文件里已有的真实 git 测试用同一套手法），只是在这个具体沙箱实例里跑不动；换一个没有这个残留挂载的沙箱应该就能跑绿。没有为了让它变绿而删测试或放宽断言。

全量 `task check --full` 因为这个沙箱也没有 Redis/Valkey（大量无关模块——team/topic/user_follow 等，跟这张卡完全无关——因为连不上 `localhost:6379` 报错），噪音太大不代表什么，没有意义，已跳过；只针对改动到的文件和 review 领域做了有针对性的验证（见上）。

**父话题给的 `git -c safe.directory='*'` 读仓库技巧已验证有效，但解决的是另一个问题**：那个技巧是在共享工作区 `/de808b13-.../` 上用 git（不是 jj）读历史/核main状态，我已经用它确认了两件事——① 我自己话题分支 `topic/d354423a` 上的提交（包括这次 ruff format 修复后的一次快照）确实进了共享仓库,没有丢；② main 上 `claude-review.yml` 到现在依然没有 `paths-ignore`，我的改动仍然不会跟别的 PR 重复叠加。但它没解决我测试卡住的那个问题——我需要的是在**全新的、每项目独立的**工作区（`backend/.workspaces/<project_id>`）里跑 `jj git init --colocate` 这类**写**操作来搭真实测试仓库，这类操作从 cwd 向上找 `.jj` 时会先撞到 `/work/.jj`（本话题工作区自己的 jj 标记，指针指向同一个读不了 `config-id` 的共享仓库），跟"读 main 历史"是两条不同的路径，git 读历史的技巧对它不适用。这点和父话题"jj 在沙箱里完全不可用"的结论一致，不是新分歧。

## 特别提醒（简报原文，自己会遵守）

这张卡自己的 PR 享受不到这次修复——如果这个 PR 的 CI 红了，不要试图靠"提交新 commit"自己修，推不上去，直接把情况报回父话题（topic_id: `e593d59c-ca17-4dcc-b0ff-57980823fd22`）。

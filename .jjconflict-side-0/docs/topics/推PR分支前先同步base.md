## 结论

已修。推 PR 分支被 GitHub 以 `workflows` 权限拒绝时，先把**推送目标仓库的默认分支**合进话题分支，然后**只重推一次**。改动全部收在 `push_topic_branch_for_github_pr` 内部，`_open_pr_for_accept()` 和 `_repush_if_local_head_moved()` 两个入口一起覆盖。

改的文件：<&backend/app/domain/workspace/service.py>、<&backend/tests/unit/test_workspace_pr_branch_sync.py>（新增）。没有改 `review/services.py`，没有改任何 `.github/workflows/` 文件，没有改前端。

## 1. 选了哪种实现，为什么

**方案 2：推失败后再补救。**

- **方案 1（每次推之前都同步）**：60 秒一次的重推轮询每次都要 fetch 一遍远端，还会不断产生 merge commit——直接违反验收标准 4。
- **方案 3（推之前先比对 workflow 树）**：比对本身就要拿到 upstream 默认分支的树，等于每次推之前都得 fetch 一次（`ls-remote` 只给 sha，比树还得 fetch）。省不掉网络开销，却比方案 2 多一条常态路径。
- **方案 2**：happy path 一个字节都没变——不 fetch、不 merge、不多一次 rev-parse。同步只在**真的被拒**之后发生，而且只重试一次。爆炸半径最小。

### 同步的是「推送目标仓库的默认分支」，不是本地 base

任务简报提醒的坑确实存在，但落点比简报说的更靠前一层：GitHub 比的是**这次 push 的目标仓库**（`owner/repo`，即 #192 连接的那个仓库）的默认分支，而 `upstream` remote 不保证是同一个仓库（`push_topic_branch_for_github_pr` 的 docstring 自己就写了这一点）。所以同步源不是 `sync_upstream()`，而是**用同一个 token、从同一个 push URL** `ls-remote --symref HEAD` 拿默认分支名，再 fetch 它。这样"比的是什么"和"同步的是什么"物理上不可能错开。

## 2. 落后场景能推上去了

新增 <&backend/tests/unit/test_workspace_pr_branch_sync.py>，用**真的 git 仓库**跑：一个 bare 仓库扮演 GitHub，它的 `pre-receive` 钩子复刻 GitHub 的判定——拿进来的分支的 `.github/workflows` 树和 `main` 的比，不一致就用 GitHub 的原话拒绝。测试不读实现，只断言"GitHub 那边最后收到了什么"。

`test_branch_with_lagging_workflows_still_reaches_github`：本地 base 带 e2e.yml v1 → GitHub main 前进到 v2 → 话题分支只改了 `src/app.py`（完全没碰 workflow）。改动前这一推必被拒；改动后推送落地，且 GitHub 上那个 commit 里**卡自己的改动和 v2 workflow 都在**。

反向验证（探针，未入库）：把 `_is_workflow_permission_rejection` 临时打成恒 False，同一个用例立刻回到 `refusing to allow ...` 失败——证明这套 harness 真复现了线上的病，绿是修出来的不是构造出来的。

## 3. 冲突不会让采纳失败，原因进 note

`_sync_remote_base_into_topic_branch` 对普通失败（冲突 / 拉不到 / 分支被并发改动）**不抛异常**，一律返回带 `reason` 的字典；`push_topic_branch_for_github_pr` 把它包成 `ValidationError` 抛出。这条异常走的是既有的降级通道：`_open_pr_for_accept` 的调用方 catch 住 → `two_phase_degrade_reason = f"GitHub 侧调用失败：{exc}"` → `_with_pr_degrade_note()` 前缀进 `card.note`。降级语义一个字没动。

`test_conflicting_sync_reports_the_conflict_and_leaves_the_branch_alone`：话题和 GitHub main 同时改 README.md → 报错里点名"冲突"和 `README.md`，token 不出现在错误里，**话题分支 head 原地不动**（合并跑在一次性 worktree 里，落地靠 `update-ref` 的 compare-and-swap，不存在半合并状态）。

端到端那一半（卡最终 `accepted`、note 里有"未走 PR 采纳"）已经由 `tests/integration/test_accept_pr.py::test_accept_pr_open_failure_degrades_with_github_call_failed_reason` 覆盖，我这里抛的就是同一类异常、走同一条路，没有重复造一份。

## 4. 60 秒轮询没有新增开销，也没有死循环

三条证据，都是测试断言：

- `test_push_that_github_accepts_costs_nothing_extra`：没被拒时，返回的 head 就是推之前的 head（**没有多出 merge commit**），而且仓库里**没有留下 fetch 来的 ref**——同步压根没跑。
- `test_repeated_pushes_after_a_sync_do_not_keep_moving_the_branch`：同步过一次之后连推三次，三次 head_sha 完全相同，本地分支和远端分支都不再动。所以 `card.pr_head_sha` 和 `_local_topic_branch_head()` 之后一直相等，轮询不会误判"head 变了"。
- `test_a_later_agent_commit_builds_on_the_synced_branch`：芝士后来又提交一版修复，新 head **是同步 merge 的后代**，下一次重推是干净的 fast-forward。

第三条是我实现里唯一一处"多做的事"，值得单独说：同步是直接移 git ref 的，而话题的 jj workspace 不知道。如果就这么放着，下一次 `snapshot_worktree()` 会用 `--allow-backwards` 把 bookmark 移到**同步前那个 commit 的子节点**上——merge 被丢掉，之后每一次重推都变成被拒的 non-fast-forward，卡在那里再也推不动。所以移完 ref 之后调一次既有的 `_catch_up_with_branch()`（它本来就只做 fast-forward，且工作区有未提交改动时会自己让开，不会吞掉人手改的文件）。这一步是 best-effort：jj 出问题只记日志，不影响推送，因为要推的是 git ref。

## 5. 仓库约定 / 检查

- 测试是功能测试：全部对着真实 git 仓库和真实 jj workspace 断言外部可观察的结果，不读源码、不断言内部调用。
- `ruff check` 全绿、`ruff format` 已跑、`pyright app` **0 errors**。
- **环境限制（如实标注）**：本沙箱容器里没有 Docker、也没有 PostgreSQL 服务端（只有 client），`tests/conftest.py` 的 session 级 `_pg_schema` fixture 连 `localhost:5433` 失败，因此 `task be:test` 在这里跑不起来——**这不是我的改动导致的，同一个容器里既有的 `tests/unit/test_workspace_git_timeout.py` 也是同样的 DB 连接错误**。为了不把"没验证"说成"验证过了"，我把新测试原样复制到 `backend/tmp/`（gitignored）绕开那个 fixture 单独跑了一遍：**8 passed**，跑完即删。完整 `task check` 请以闸门/CI 的结果为准。

## 6. 哪些情况仍然会被拒（正常，不是 bug）

**卡自己改了 workflow 文件的**，同步之后照样被拒——同步只能消掉"分支落后"造成的差异，消不掉卡自己的改动。两条路径都覆盖了：

- 分支不落后、卡改了 workflow：同步发现"已经包含 GitHub 默认分支的最新提交"，**不做无意义的合并、不做无意义的重推**，直接带着"被拒的 workflow 改动来自这张卡本身"降级（`test_card_that_edits_a_workflow_itself_is_still_rejected`）。
- 分支既落后、卡又改了 workflow：同步照常做，重推**仍然**被拒，GitHub 的原话进 note（`test_card_editing_a_workflow_on_a_lagging_branch_is_rejected_after_syncing`）。

另外，**不是 workflow 权限的推送失败一律不重试**（token 失效、分支保护等）——立即降级，不 fetch 不合并（`test_other_push_failures_are_not_retried`）。

还有两种仍会降级的，本来就在既有语义里、这次没碰：拿不到 token / 项目没连仓库；以及同步遇到真冲突。

## 需要知会的两个副作用

1. 走了同步路径的 PR 里会多一个 `同步 GitHub main → topic/xxxx（推 PR 分支前）` 的 merge commit，作者是芝士。这是让 GitHub 放行的必要代价，PR 的 diff 本身不受影响（对 base 是干净的）。
2. 同步会把 GitHub 默认分支的提交带进话题分支，后续 `merge_topic` 把话题分支并回本地 base 时，这些提交会顺带进来——方向和 `sync_upstream` 一致，不会倒退。

## 这张卡自己

简报提前说过：它的话题分支同样是从落后的本地 main 切出来的，所以**这次采纳大概率还是会降级直推 main**。这是它要修的问题的最后一次发作，不用当故障处理，也不用为此改实现。

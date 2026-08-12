## 现在的状态

**主体修复（`checkout --force`）在我接手前就已经进 main 了**，同期另一条话题先落的地。
我核过基线：工作区 `@` 就在 `main@upstream` 上，`jj diff` 为空，
<&backend/app/domain/workspace/service.py> 里 `_sync_shared_checkout` 已经是
`_git(repo, "checkout", "-q", "--force", base)`，配套功能测试
`test_dirty_shared_tree_does_not_block_the_sync` 也在
<&backend/tests/unit/test_workspace_git_timeout.py> 里。

所以本话题的实际范围收敛成简报里**还没做的那两件**。

## 目标

1. **补上「丢弃前先说清丢了什么」**（要改代码）。`--force` 之前跑一次
   `git status --porcelain -uno`，把将要被丢弃的已跟踪文件写进 `logger.warning`。
   不做这一步，等于把「永久失败」换成了「静默数据丢失」——后者更难查，净亏。
2. **只查不修：脏文件是哪来的**（写进结论，不动代码）。

## 约束（沿用简报的硬边界）

- 只碰 `_sync_shared_checkout` 及其日志。**不重构外层重试循环**（`index.lock` 逻辑
  是 2026-08-09 事故的产物，有效）。
- 不碰 `_merge_ref_into_base`。
- 不加 `git clean` —— 未跟踪文件 `checkout --force` 和 `reset --hard` 都不动它，
  删它属于超范围。
- 不碰 `backend/app/domain/review/`（同期有别的话题在改）。

## 调查结论：脏文件是哪来的

共享目录不是只读的，而且**有两条写入路径，且两条都没有任何提交路径**——写进去的
改动不会变成提交，只会一直脏着，直到某次 `_sync_shared_checkout` 把它扔掉。这正好
解释了「永远失败」而不是「偶发失败」。

| 写入路径 | 怎么落到共享目录 | 为什么脏会一直留着 |
|---|---|---|
| `PUT /projects/{id}/file` **不带 `?topic=`** | <&backend/app/api/routes/workspace.py> → `ws.write_file(..., topic_id=None)`，`_tree(project_id, None)` 就是主仓目录 | `snapshot_worktree(project_id, topic_id)` 的 `topic_id` 是**必填**的，它只在话题的 jj worktree 里提交。项目级写入没有对应的快照调用 |
| `exec_in_sandbox(topic_id=None)` | <&backend/app/domain/workspace/service.py> 把 `_tree(project_id, None)` 读写挂到容器 `/work` | 同上，沙箱里的任何写入之后没有人快照 |

前端也够得着第一条：<&frontend/src/components/DocPanel.vue> 的 `writeOpenFile` 传
`props.topic?.id`，没有话题上下文时就是 `undefined`，`api.ts` 的 `writeFile` 于是
不拼 `?topic=`，直接落到共享工作树。

**这是一个比本话题更值得修的洞**（按简报要求只报告不修）。可选的收口方向，留给后续
话题拍板：项目级写入要么禁掉，要么给它一条真正的提交路径。

## 下一步

- [x] 核基线：主体修复已在 main
- [x] 查清脏文件来源
- [x] 先写红测试（断言 warning 里点名被丢弃的文件、未跟踪文件不误报），确认改代码前是红的
- [x] 加 `git status --porcelain -uno` 日志
- [x] `task check` 全绿
- [x] 递验收卡给 <@wangchangxin>

## 验收依据

**基线**：本话题原先的基线落后 main 14 个提交。已把本话题的改动
`jj rebase -s` 到 `main@upstream`（`fe949e17`，#284），无冲突。那 14 个提交
没有一个碰过 `backend/app/domain/workspace/`，所以不存在静默覆盖别人改动的风险。

**红→绿是实测的，不是推断的**：临时摘掉 `_sync_shared_checkout` 里那一行调用后，
`test_discarded_local_modifications_are_named_in_the_log` 失败于
`assert 'kept.txt' in ''`；恢复后 18/18 通过。
另一条 `test_a_clean_shared_tree_logs_no_discard_warning` 摘掉调用也照样绿——
这是**预期**的：它防的是「日志变噪音」，不是用来验证修复本身的。

**静态检查**：`check.sh --no-tests` 6/6 全绿（ruff / pyright / alembic heads /
repo guards / actions pinned / migration fork）。这正是质量闸门跑的那一份。

**全量后端测试**：4034 passed、23 failed、31 skipped。23 条失败**没有一条**沾本话题：

| 失败 | 条数 | 定性 |
|---|---|---|
| `test_machine_service.py` + `test_tmux_control.py` | 22 | CLAUDE.md 已记录的 procps 缺失（沙箱没有 `ps`/`kill` 二进制），非代码缺陷 |
| `test_cheese_cli.py::test_await_log_lives_outside_the_worktree` | 1 | **不在已记录的名单里**，见下 |

`test_market_api` 那条已知失败这次是绿的——按 CLAUDE.md 的说法带
`ANTHROPIC_AUTH_TOKEN=dummy-for-test` 跑即可。

## 顺带发现（不在本话题范围，交给人定夺）

`test_cheese_cli.py::test_await_log_lives_outside_the_worktree` 在本沙箱稳定失败，
且**单独跑也失败**（不是测试间串扰）：它 `monkeypatch.setenv("HOME", tmp_path)` 之后
期望 `_await_log_path("run-1")` 落在 tmp 下，实际拿到
`/home/node/.claude/cheese-await/run-1.log`——即真实 HOME。也就是说这条路径没有在调用时
读 `HOME`。

它和本话题的改动无关（本话题只碰 `workspace/service.py` 与其测试，两者都不导入
`cheese` CLI）。但它**不在 CLAUDE.md 记录的沙箱缺口名单里**，所以我不敢断言它是
「已知环境噪音」：也可能是 `cheese await` 的日志真的写到了固定 HOME 下。这一条按
只报告不修处理。

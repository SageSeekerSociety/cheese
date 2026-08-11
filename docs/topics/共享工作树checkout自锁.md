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
- [ ] 先写红测试（断言 warning 里点名被丢弃的文件、未跟踪文件不误报），确认改代码前是红的
- [ ] 加 `git status --porcelain -uno` 日志
- [ ] `task check` 全绿
- [ ] 递验收卡给 <@wangchangxin>

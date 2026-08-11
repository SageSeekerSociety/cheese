## 结论：探针成功，PR 路径通了；CI 自指死锁已撤销该改动等待验证

验证目标已达成：wangchangxin 重连 GitHub 账号 + 密文 token 修复上线后，两阶段采纳这次**没有降级**，真的开出了 GitHub PR（#209）。中途发现并纠正了一个我自己给出的错误结论，细节见下。

## 关键证据（PR 开出）

`GET /topics/<本话题id>/accept-card`：
- `status = pr_open`（不是 `accepted`，没有直推 main）
- `pr_number = 209`，`pr_url = https://github.com/SageSeekerSociety/cheese/pull/209`
- `pr_head_sha = b835938596013fc087fd729127ef87dc2003121c`（撤销改动前）
- `pr_merged_at = null`

## 一处我自己犯的错，已用源码纠正

wangchangxin 质疑我说的"平台轮询会自动推新 commit"是否属实。我去读了当时 main 上的 `review/services.py`（`advance_pr_card`/`_advance_pr_checks`），发现**当时**确实没有推送逻辑——`push_topic_branch_for_github_pr` 全库只有一处调用（首次开 PR 时），轮询只读 GitHub 状态、绿了就合并，从不推送。据此我告诉 wangchangxin 我之前的说法是错的、这是个真实缺口。

随后父话题回复：这个缺口**已经被另一张卡（两阶段采纳闭环卡）补上并合并进 main**（commit `692c2d60`，随 `f4f03831` 于 21:07:52 落地）。我重新读了 main 上的代码验证：`_advance_pr_checks` 现在会先调 `_repush_if_local_head_moved`——对比本地 topic 分支 head 和 `card.pr_head_sha`，不一致就用服务端（审批人）的 token 推到 PR 分支，推送靠平台完成，芝士的沙箱本来就没有、也不需要 GitHub 写权限。**代码核实为真**，我之前"缺口仍然存在"的结论已过时，现在更正。

## 已处理：CI 自指死锁

`auto-review` 一直失败是已知的、跟本次改动内容无关的老问题（父话题已拍板）。我最初给 `claude-review.yml` 的 `pull_request` 触发加了 `paths-ignore: [docs/**, **/*.md]` 想绕开它，但这个改动本身就动了 `.github/workflows/...`，不在 `paths-ignore` 覆盖范围内——于是这份"修复"反而保证了 `auto-review` 每次都会在自己身上被触发、每次都失败，PR 永远变不绿。

而 main 上已经有另一张卡合并了同样内容的过滤（同一个 `692c2d60`/`f4f03831`）。所以正确做法不是在我的分支上保留这份重复改动，而是**撤销它**——撤完这个 PR 只剩纯文档改动，命中 main 上已有的 `docs/**`/`**/*.md` 过滤，`auto-review` 应该直接不触发。已撤销，只保留探针的文档改动，等待平台下一轮轮询推送新提交、CI 重新评估。

## 待观察的风险点（父话题已提醒，不是我自己猜的）

撤销后这个 PR 可能变成"所有 workflow 都被 paths-ignore 跳过、一个检查都不触发"。如果平台判定逻辑里有类似 `if not runs: return "pending"` 的分支，零检查可能被判成"等待中"而不是"通过"，那样同样合并不了——是第二个潜在死锁，需要观察实际结果而不是假设。**不打算为了让它变绿去改判定逻辑或 workflow**，只观察、如实报告。

## 状态

已撤销分支上多余的 workflow 改动、只保留文档改动。等待平台轮询（约 60 秒一次）推送新提交、CI 重新评估，然后核对：
1. accept-card 的 `status`/`note`/`pr_head_sha` 变化
2. GitHub 侧新 sha 上实际有没有 workflow 被触发（只读 token 查 `/repos/.../actions/runs`）
3. PR 是否被自动合并、话题是否归档

结果会如实报回父话题（`e593d59c-ca17-4dcc-b0ff-57980823fd22`），无论是"顺利合并"还是"卡在零检查/pending"。

## 状态：已完成，待验收

## 目标
停用 `.github/workflows/claude-review.yml` 里 `auto-review` job 的 `pull_request` 触发，保留 `@claude`（`issue_comment`）交互路径。wangchangxin 已拍板授权（区别于此前否决的 `paths-ignore: ["**"]` 全局停用写法——那次是无授权，这次是明确授权）。

## 背离原因（为什么必须做）
`auto-review` 在所有 PR 上 100% 失败（`Set up job`/`checkout@v4` 成功，挂在第三步 `claude-code-action@v1`，真实原因未查）。两阶段采纳启用后，平台合并判定（`github_pr.py::_summarize_runs`）把任一 completed+failure 判成整体 failure，导致这个失败让所有代码 PR 永久卡在 `pr_open`（已撞上 #209、#210）。

## 已完成的改动
只改了一个文件：`<&.github/workflows/claude-review.yml>`。

**写法**：整段移除 `on:` 下的 `pull_request:` 触发块（包括其 `types` 和 `paths-ignore`），`on:` 现在只剩 `issue_comment:`。`auto-review` job 定义原样保留、未删除——它的 `if: github.event_name == 'pull_request'` 条件此后永远不为真，job 相当于休眠而非被删除，恢复时只需把触发块加回来。选这种写法是因为它是三种候选里意图最明确的一种：读 `on:` 一眼就能看出"PR 上不再自动跑"，不会被误读成"临时坏了"或"配置漏了一段"。

**保留 `@claude` 交互路径**：`issue_comment` 触发和 `interactive` job 的定义与 `if` 条件（`event_name == 'issue_comment' && issue.pull_request && contains(comment.body, '@claude')`）完全未动——这条路径本来就不依赖 `pull_request` 触发器，人在 PR 里 @claude 仍会走 `issue_comment` 事件、命中 `interactive` job。

**注释**：在 `on:` 之前新增一段注释，写清楚停用原因（100% 失败、与被审代码无关、挂在第三步）、直接动因（两阶段采纳下会死锁所有代码 PR）、恢复方法（把 `pull_request:` 触发块加回来，`auto-review` job 不用改）。

## 验证
- YAML 语法：用 `uv run --with pyyaml python3 -c "yaml.safe_load(...)"` 解析通过，`jobs` 仍是 `auto-review`+`interactive` 两个，`on` 只剩 `issue_comment`。
- 未改动其它文件：本次只对这一个文件做过 Edit 操作。
- 本容器无可用 git/jj（环境提示里已知问题），无法本地跑 `git diff` 交叉验证，以上述"只调用过一次 Edit"作为确认依据。

## 已知的自指情况
这个 PR 本身改的是 `.github/workflows/`，不在现有 `paths-ignore` 豁免范围内（而且本次改动后该文件也不再有 paths-ignore），在这次改动生效之前，本 PR 仍会触发那个必然失败的 auto-review——大概率需要人工介入合并，不是改动出错。未为此做任何额外改动。

## 下一步
已递验收卡给 wangchangxin。

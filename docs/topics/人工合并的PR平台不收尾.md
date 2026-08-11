## 目标

PR 被**人工在 GitHub 上合并**之后，验收卡永远停在 `pr_open`、话题永远不归档。让轮询路径
（`AcceptService._advance_pr_checks`）能识别「PR 已被外部合并」并按正常方式收尾：记
`pr_merged_at`、把 `pr_head_sha` 换成**合并提交**的 sha、转入 stage 2 等部署。

样本：`1c7016e3`(#210) 与 `ceb1b9b9`(#211) 两张卡（**只作验证样本，不动它们**）。

## 关键核实结论（推翻/修正了简报里的两条前提）

**1. 简报说「client 里已经有 `pr_view`，不用新增能力」——不成立。**
仓库里有**两个同名不同壳**的 GitHub client：

- `GitHubPRClient`（大写 PR，`github_pr.py:477`）：`_accept_via_pr` 用的，**有** `pr_view`。
- `GitHubPrClient` / `HttpxGitHubPrClient`（小写 r，`github_pr.py:88/168`）：**轮询器用的就是这个**，
  只有 `open_pull_request / check_state / pull_request_head_sha / merge_pull_request /
  workflow_run_state`，**没有** `pr_view`。

所以这条路径必须给轮询侧的 Protocol 新增一个读 PR 状态的方法。

**2. 简报倾向「拿到 405 之后再判」——评估后不采纳，必须前置判断。**
`_advance_pr_checks` 的顺序是 重推 → 取 live head → `check_state` → 全绿才 merge。
`check_state` 返回 `failure` 时会 `_nudge_pr_fix` 后 **return**，**根本走不到 merge**，
也就永远拿不到那个 405。#210 恰恰是「带着一条红的 auto-review 被人工放行合掉的」——
只在 405 分支上做判断，对这两张样本卡**完全无效**。

## 设计点（含理由）

**① 在哪一步判：前置，放在 `_advance_pr_checks` 最开头**（重推之前、`check_state` 之前）。
理由见上：红 CI 会在到达 merge 前就 return。而且**不多花 API 调用**——
`_repush_if_local_head_moved` 改为返回「这轮是否真的推了」，没推就复用前置那次 GET 拿到的
head_sha，只有真推了才再取一次。常态下仍是每轮 1 次 `GET /pulls/{n}`，与改动前持平。

**② 合并提交的 sha 取 `merge_commit_sha`，且只在 `merged: true` 时可信**（这条有实测证据）：

| 实测对象 | `merged` | `head.sha` | `merge_commit_sha` | 该 sha 与 base 的关系 |
|---|---|---|---|---|
| astral-sh/ruff #20000（已 squash 合并） | `true` | `6c0badc4…` | `f4be05a8…` | `compare f4be05a…main` → `status=ahead, behind_by=0`，即**在 main 上**；该 commit 单 parent、message 是 `…(#20000)` |
| astral-sh/ruff #27626（open 未合并） | `false` | `8e763106…` | `cb623502…`（**非空！**） | `compare cb6235…main` → `status=behind, ahead_by=0, behind_by=2`，即**不在 main 上**，是 GitHub 的 test-merge 弃用提交 |

结论：`merge_commit_sha` 在**未合并**时也是非空的临时提交——**必须以 `merged` 为门**，否则
stage 2 会拿一个任何分支上都不存在的 sha 去找 deploy run，卡永远等部署（正是简报警告的坑）。
额外保险：`merged: true` 但 `merge_commit_sha` 为空时**不收尾**，写 note 下轮重试。

**③ PR 被关闭但没合并（`state: closed, merged: false`）：本卡处理，但只写 note + 回房间一次，不自动收尾、不回落本地合并。**
理由：`_accept_via_pr` 里那条「回落本地合并」的前提是采纳动作还没落地；而 `pr_open` 卡的采纳
已经拍板、分支已推上去，人主动关掉 PR 表达的是「这个不要」，此时替他本地合进 main 是错的。
不处理的话现状是每轮走到 merge 拿 405、写一句误导性的「检查全绿但 GitHub 拒绝合并」。

## 红鲱鱼（已写进代码注释）

两张卡 note 上的 `⚠️ 平台自动重推失败（refusing to allow … without workflows permission）`
**与交付无关**：合并之后平台还在继续重推该分支，而推之前会先把 main 合进分支，于是 main 上
#212/#214 那批 `build.yml` 改动成了本次推送要携带的 workflow 文件，被 GitHub 拒。
**看到这个 note 不代表这张卡的东西没落地。**

顺带评估「合并后还要不要继续重推」：**不需要单独改**——前置的 merged 判断 return 在重推之前，
观察到合并的那一轮起重推就自然停了，噪声一并消失。

## 改了什么

- `backend/app/domain/review/github_pr.py`：新增 `PullRequestStatus` 数据类和轮询侧 Protocol 的
  `pull_request_status()`；`merge_commit_sha` / `merged_at` **只在 `merged: true` 时才透出**，
  未合并时一律置 None。时间戳统一解析成 aware UTC。
- `backend/app/domain/review/services.py`：`_advance_pr_checks` 开头前置读 PR 状态 →
  已合并走新的 `_settle_external_merge`（记 `pr_merged_at`、`pr_head_sha` 换成合并提交、进 stage 2、回房间一条消息）；
  closed-unmerged 走 `_note_pr_closed_unmerged`（只写 note + 回房间一次，不自动合、不归档）。
  `_repush_if_local_head_moved` 改为返回「这轮是否真推了」，没推就复用前置那次读到的 head。
- 测试：`tests/integration/test_accept_pr.py` +6、`tests/unit/test_github_pr_httpx_client.py` +5。

## 验证结果（沙箱真跑，非 SKIP）

- `ruff check app tests`：All checks passed
- `pyright app`：`0 errors, 0 warnings, 0 informations`
- `pytest tests/unit/test_github_pr_httpx_client.py tests/integration/test_accept_pr.py`：**42 passed**
- 全量 `pytest tests/ -n 4`：**3639 passed, 31 skipped, 23 failed**。23 条全部与本改动无关：
  22 条是 CLAUDE.md 记录的沙箱无 procps（`test_machine_service.py` 21 + `test_tmux_control.py` 1）；
  第 23 条 `test_market_api.py::test_market_lists_ai_and_compute_pools` 是沙箱没有 LLM 凭证
  （`market.py:ai_listings` 的 `available = name in registry.selectable(...)`），同样与 review 域无关。

## 状态

- [x] 核实两个 client / 调用链、`merge_commit_sha` 语义（实测）
- [x] 改 `github_pr.py`（新增 `PullRequestStatus` + `pull_request_status`）与 `services.py`
- [x] 补功能测试（外部已合并 → 收尾；真被挡 → 只写 note 不收尾；closed unmerged；缺 sha 不收尾）
- [x] 真跑 ruff / pyright / pytest（沙箱起了 pgserver+redislite）
- [x] 递验收卡给 @王长鑫

## 结论

GitHub 返回 422「A pull request already exists」时不再降级——去把那个已经开着的 PR **认领**下来，写进卡片、转 `pr_open`，走正常的轮询→CI→合并→部署→归档。孤儿 PR 的产生路径就此关掉。

改动集中在 <&backend/app/domain/review/github_pr.py>（认领本体）与 <&backend/app/domain/review/services.py>（措辞），加三处测试。

## 判定条件：只认这一种 422（验收 1）

`github_pr.py::_is_pr_already_exists(resp)` 同时要求三条：

1. `status_code == 422`；
2. 响应体能解析成 JSON dict、且 `errors` 是个 list；
3. `errors[]` 里**存在一条** `resource == "PullRequest"` 且 message 含 `already exist`（小写比对）。

```json
{"message": "Validation Failed",
 "errors": [{"resource": "PullRequest", "code": "custom",
             "message": "A pull request already exists for SageSeekerSociety:cheesex/0bbc3403."}]}
```

**为什么不会误伤真正的校验失败**：422 是 `POST /pulls` 的万能 Validation Failed，base 分支不存在、head == base、两个分支之间没有 commit，全都是 422，它们的 `errors[]` 条目 `resource` 是 `Issue`/`PullRequest` 但 message 完全不同（如 `No commits between ...`），拿不到 `already exist` 这个词，因此继续走原来的 `GitHubPrError` → 降级。反过来，判定读的是**结构化的 `errors[]` 条目**而不是整个响应体的子串，所以一个碰巧叫「already exists」的分支名或 PR 标题也不会被误读成本情形。

认死这一条还有个语义前提：head 分支名由话题 id 推导（`pr_branch_name(topic.id)`），所以"这个分支上已经开着的 PR"**就是本话题的 PR**，认领它不存在认错对象的可能。

## 认领怎么做

`HttpxGitHubPrClient.open_pull_request`：201 → 照旧返回；命中上面的 422 → 调 `_find_open_pull_request()`，`GET /repos/{owner}/{repo}/pulls?head={owner}:{head}&state=open`，在结果里优先挑 `base.ref` 等于本次要开的 base 的那条（一个 head 分支可以对多个 base 开 PR），拿到 number/url/head_sha，返回 `PullRequest(..., already_existed=True)`。

两个刻意的保守选择：

- `_find_open_pull_request` **从不抛异常**，查不到、非 200、JSON 形状不对，一律返回 `None` → 落回原来的 `raise GitHubPrError` → 正常降级。「凭没核实的猜测转 `pr_open`」比「按 GitHub 真实返回的错误降级」更糟。
- 只改了 `GitHubPrClient` / `HttpxGitHubPrClient`（小写 r，两阶段采纳这条路用的这个）。`GitHubPRClient`（大写 PR，#188 §5.1 App token 同步合并那条路）**没动**——简报特意提醒过别加错壳，`_open_pr_for_accept` 走的确实是 `github_pr.default_client()`，即小写那个。

## 顺带解释了现场那对矛盾消息

简报里的现场（话题 `0bbc3403` 10:40 被采纳两次、卡片同时留下 `pr_number=234` 和 `status=accepted`）用"两次采纳撞车"能完整解释：两次都在 `pr_number` 还是 NULL 时进了 `accept()`，先到的那次把 PR #234 开出来、发了「已开 PR…话题保持 active」；后到的那次拿到 422 已存在 → 走降级 → 本地 merge + 直推 main → 覆写成 `accepted` 并发「已采纳并归档」。所以卡片上的 `pr_number` 是赢家写的、`status` 是输家写的，两条消息各说各的。

认领分支把输家的结局也变成 `pr_open`，最终状态一致、只剩一条消息，撞车不再产生孤儿。

## 卡片字段与状态（验收 2）

认领后走的是和新开 PR 完全相同的落库分支（`services.py::_open_pr_for_accept`）：`status = pr_open`、`pr_number`/`pr_url` 取自认领到的 PR、`pr_repo = owner/repo`、`pr_merged_at = None`。

`pr_head_sha` 取 `pushed["head_sha"]`（本次推送的 sha）而不是列表接口返回的 `head.sha`——推分支发生在开 PR 之前，推完 PR 的 head 就是这个 sha，用本地推上去的值可以避开"列表响应比推送晚一拍"的竞态。轮询侧本来也会每轮重新拉 PR 的当前 head。

## 通知不再自相矛盾（验收 3）

原来的矛盾是这么来的：PR 开出去了（GitHub 侧 422 之前已经真的建过 PR），但异常被 `except Exception` 接住 → 降级 → 本地 merge + 直推 main → 发第二条「已采纳并归档」。现在这一路根本不抛异常，`_open_pr_for_accept` 直接 `return card`，`accept()` 在第 446 行原地返回，降级分支下面那一整段（本地 merge / `push_back()` / 归档通知）一行都不会执行。

措辞也跟着分了岔：`already_existed` 为真时卡片 note 和通知都说「已认领该分支上已存在的 PR #N」，不说「已开 PR #N」——时间线记录的就是这件事，说反了等于记假账。

**怎么确认的**：`tests/unit/test_review_pr_claim_existing.py::test_claim_emits_only_the_pr_message_never_the_archive_one` 收集本次采纳发出的全部通知，断言只有一条、且是 PR 那条；`test_claimed_pr_is_opened_exactly_once_per_accept` 另外钉住"认领路径不会顺带再调一次开 PR"。

## 怎么找出已经产生的孤儿 PR（验收 4）

一条 SQL 列出全部嫌疑卡片（卡片已归档，却还挂着 PR）：

```sql
SELECT topic_id, pr_repo, pr_number, pr_url, decided_at
FROM accept_cards
WHERE status = 'accepted' AND pr_number IS NOT NULL
ORDER BY decided_at DESC;
```

命中的每一条再问 GitHub 一句 `gh pr view <pr_number> --repo <pr_repo> --json state`：`state == "OPEN"` 的就是孤儿——代码已从降级路径进了 main，PR 却还开着、CI 白烧、永远不会被合并。`MERGED`/`CLOSED` 的是正常历史（#188 §5.1 那条路本来就是"合并已存在的 PR"，会留下 `accepted` + `pr_number`）。

本卡按简报要求**没有去动 #234**（人工善后）。这个修复落地后此类孤儿不会再产生：唯一制造它的路径（422 已存在 → 降级 → 本地 merge + 直推）已经被认领分支取代。

## 测试（验收 5）

- <&backend/tests/unit/test_github_pr_open_pull_request.py>（8 例，用 `httpx.MockTransport` 驱动真实的 `HttpxGitHubPrClient.open_pull_request`，不 mock 被测函数）：201 正常开且 `already_existed=False`；422 已存在 → 认领；多个 base 时挑对 base；**其它 422 仍然抛错降级**；「already exists」出现在非 `PullRequest` resource 上仍然抛错；422 已存在但列表查不到 / 列表调用失败 → 抛回原错误；非 422 失败行为不变。
- <&backend/tests/unit/test_review_pr_claim_existing.py>（4 例，服务层）：认领后卡片进 `pr_open` 且字段正确；只发 PR 那一条通知；其它 GitHub 失败仍然降级并把原因写进 note；认领路径只开一次 PR。
- <&backend/tests/integration/test_accept_pr.py> 新增 2 例：`test_accept_claims_the_pr_that_already_exists_on_the_branch`、`test_accept_still_degrades_on_a_422_that_is_not_already_exists`（走真实路由 + DB）。

## 验证结果（验收 6）

沙箱内用 `.claude/scripts/dev-db.sh start` 起了真实 PG + Redis 后跑的，**没有走 `check.sh`**（简报点名它会假绿：探不到可用的 venv 控制台脚本就把 pyright/pytest 记成 SKIP 而退出码仍是 0）。三项都是真跑的：

- `ruff check .` → All checks passed；`ruff format --check .` → 691 files already formatted。
- `pyright` → **0 errors, 0 warnings, 0 informations**。
- 本卡相关测试 `test_github_pr_open_pull_request.py` + `test_review_pr_claim_existing.py` + `test_accept_pr.py` → **29 passed**。
- 全量 `pytest tests/ -n 4` → **23 failed, 3642 passed, 31 skipped**（349s）。

**SKIP 项：0**——ruff / ruff format / pyright / pytest 四项全部真跑，没有一项被跳过。31 个 skipped 是测试自身的 skip 标记，不是检查项被跳过。

23 个失败逐条核对，全是沙箱缺工具、无一与本卡相关：

- **22 个是 CLAUDE.md 已记载的「无 procps」缺口**：`test_machine_service.py` 21 个 + `test_tmux_control.py` 1 个，`FileNotFoundError: 'kill'`。
- **1 个是清单上没有的新缺口**：`test_market_api.py::test_market_lists_ai_and_compute_pools`，断言 `ai_default["available"]` 失败。原因是 `agent/market.py:69` 的 `available = name in selectable`，而 `selectable` 由 AI pool 凭据决定——沙箱没配凭据，默认 pool 就不可选。属于环境缺凭据，`app/domain/agent/` 与本卡改的 `app/domain/review/` 之间没有任何引用关系。
- CLAUDE.md 提到的另一类「无 git identity → 43 个 worktree 测试失败」这次**没有出现**，`test_workspace.py`/`test_upstream.py`/`test_accept*.py` 全绿——本卡的集成测试 `test_accept_pr.py` 正是这批里的，等于是在真实 worktree 上跑通的。

## 明确没做

不动 `push_back()`/本地 merge 那条降级路径本身（只是让它不再被这一种 422 触发）、不动归档门禁、不动重推逻辑、不去关闭或合并 #234、不改前端、不动 `.github/workflows/`。

## 目标

让"采纳为什么没走 PR"变成一眼可见，并修掉 token 续期的脆弱性（案底 `0556ac50`：GitHub App 的
`refresh_token` 一次性轮换；续期成功后若采纳后续步骤失败回滚，库里会留下已被 GitHub 作废的旧
refresh_token，账号连接持续失效直到用户手动重连；并发采纳同理会丢一个 refresh_token）。

## 实现已完成

### 1. 降级原因可见（`review/services.py`）

- `_TOKEN_UNAVAILABLE_MESSAGES`：把 `oauth/services.py` 的 `TOKEN_UNAVAILABLE_*` 机器可读原因
  （`not_connected`/`undecryptable`/`expired_no_refresh`/`refresh_failed`/`provider_not_configured`）
  翻成人能看懂的中文，绝不包含 token/密文本身（只是"哪个前提没满足"的分类标签）。
- `_with_pr_degrade_note()`：把降级原因前缀拼进验收卡的 `note` 字段——`AcceptService.accept()`
  在 `_resolve_pr_prerequisites()` 判定 PR 路径不可用、或开 PR 过程中任何一步失败时，都会把
  `pr_degrade_reason` 一并写进 `card.note`，而不是像原来那样只留一条 `logger.warning`（用户和
  agent 都看不到）。

**真实样例文本**（`token 续期失败` 场景，即当初 `d354423a` 那次的根因）：

```
⚠️ 未走 PR 采纳（批准人的 GitHub token 已过期，续期失败（GitHub 拒绝、refresh token 本身不可
用，或网络错误））；已合并并推送到上游 main
```

（`未连接账号` 场景对照：`⚠️ 未走 PR 采纳（批准人未连接 GitHub 账号）；已合并并推送到上游 main`）

`cheese api GET /topics/{id}/accept-card` 现在就能直接读到这条 note，不需要翻日志。

### 2. refresh_token 轮换脆弱性修复（`oauth/services.py` + `oauth/repositories.py`）

**方案**：`OAuthService._refresh_and_persist_token()` 把 GitHub 侧的 token 续期改成在**自己独立
的 session/事务**里提交，不再跟着调用方（典型是 `AcceptService.accept()`）当时所在的事务走；
`OAuthConnectionRepository.get_for_update()`（新增，`SELECT ... FOR UPDATE`）在这个独立事务里
对该行加锁。

**为什么这么选**：
- **回滚场景**：续期在独立事务里 `commit()`，跟调用方事务是否最终 commit/rollback 完全解耦——
  调用方后面随便哪一步失败回滚，续期已经落库的新 token 不受影响。这是唯一能同时满足"续期立刻
  生效"和"调用方失败不影响已完成的续期"的方案；不这么做的替代方案（比如把续期挪到调用方事务
  最后再做）没法根本解决"GitHub 那边旧 refresh_token 已经失效，本地事务却可能回滚"这个时序错位。
- **并发场景**：`FOR UPDATE` 行锁让第二个并发采纳的续期请求阻塞在锁上，等第一个提交后，靠"二次
  检查"（重新读一遍 `token_expires`）发现token已经不需要再续期，直接复用赢家写的新 token，不再
  调 GitHub 第二次——避免浪费一个 GitHub 已经作废的 refresh_token，也避免两次写互相覆盖。
- 新 session 绑定 `self._repo.session.bind`（调用方 session 的 bind），而不是硬编码模块级工厂：
  测试沙箱里 app 默认 session 工厂和某个具体请求的 session 可能绑定不同的库，用调用方自己的 bind
  能在两种情况下都指向正确的库。
- 代价：续期这个"单行短事务"里，GitHub 的 HTTP 调用本身是在行锁下做的（唯一慢的部分）——这是
  有意接受的权衡，最多阻塞同一个 connection 的另一个并发续期者（不是更大范围的表锁），且受 HTTP
  客户端自身超时约束。
- 顺带把 provider 是否配置的检查挪到了会话打开**之前**（原来在拿到行锁之后才检查）——这个检查
  只依赖静态的 `provider_id`，不依赖任何行数据，提前检查能在这条续期机制在本部署上根本用不了的
  情况下，省掉一次没必要的行锁往返。

### 3. 回归测试（`tests/integration/test_oauth_token_refresh.py`，新文件）

原有 `tests/unit/test_oauth_service.py` 里覆盖续期路径的几个测试全部靠 mock 掉的 repo 断言
"DB 被写了什么"——这套 mock 现在完全看不见真实发生的事：`_refresh_and_persist_token` 内部会
重新开一个**真实**的、绑定到真实引擎的 session，注入的 mock repo 根本拦不住它。继续保留这些
断言就是这张卡开头点名的那种"整条链路被 mock 掉、bug 活下来"的测试，所以把它们迁到了走真实
Postgres 的集成测试（复用 `tests/conftest.py::client` 提供的真实引擎 `client.test_factory`，只
mock 外部 GitHub HTTP 调用这一个真正的外部边界）：

- **`test_refresh_survives_outer_transaction_rollback`**（本卡的关键断言）：续期成功后，模拟
  调用方后续步骤失败、显式 `rollback()` 外层 session；用一个全新的第三个 session/连接重新读库，
  确认库里仍是续期后的新 access_token/refresh_token，而不是被回滚掉的旧值。
- `test_concurrent_refresh_serializes_and_calls_github_once`：两个并发调用同一个 connection 的
  续期，用真实的两条数据库连接和 `asyncio.gather` 制造真实竞争，断言 GitHub 只被真正调用一次、
  两边拿到同一个最终 token。
- `test_refresh_http_failure_leaves_stale_token_untouched`：GitHub 调用失败时，库里的旧 token
  原样不动。
- `test_undecryptable_stored_refresh_token_degrades_without_writing`：`refresh_token` 列不是合
  法 Fernet 密文时，安全降级为 None、不触碰这一行、也不再去调 GitHub。
- `test_within_margin_token_is_refreshed_early`：进入续期缓冲期（差 5 分钟过期）也会提前续期。

`tests/unit/test_oauth_service.py` 里对应位置留了两个轻量单元测试（`test_expired_token_delegates
_to_refresh_and_persist`、`test_refresh_failure_is_forwarded_as_none_with_reason`），只测试"过期
时是否真的调用了 `_refresh_and_persist_token`、结果是否被正确转发"这层纯分支逻辑（mock 的是这个
私有方法本身这个明确的边界，不是底下的 session/repo），不重复断言真实持久化——那部分完全交给上
面的集成测试。

## 验证结果

- `ruff check .` / `ruff format --check .`：全绿（678 文件已格式化）。
- `pyright`（项目网关只检查 `app/`，见 `pyproject.toml` 的 `[tool.pyright] include = ["app"]`）：
  0 errors。
- `pytest`：本沙箱容器连不上 Postgres（`localhost:5433` connection refused，`docker`/`jj` 均不可
  用，跟本卡改动无关的既有环境限制，其他子话题此前已核实过同一限制），`bash .claude/scripts/
  check.sh --full` 如实报告 `pytest FAIL: 无 DB`，没有为了绿而跳过或删掉这一步。已用不依赖真实
  Postgres 的方式手工验证了核心逻辑（直接跑通了 `_refresh_and_persist_token` 重排后的分支、两个
  轻量单元测试的断言全部通过）；新增的 5 个集成测试和既有集成测试一样需要真实 Postgres 才能跑，
  逻辑已经过人工逐行核对（session 绑定关系、`FOR UPDATE` 行锁语义、`client.test_factory` 复用
  既有 `test_accept_pr.py` 验证过的真实引擎路径），建议在有 Postgres 的环境（如另一个沙箱/CI）
  跑一次做最终确认。
- 顺带发现并修了一处纯格式问题：`oauth/repositories.py::get_for_update` 的签名换行不符合
  `ruff format`（历史遗留，与本卡逻辑无关）。

## 验收标准逐条回答

1. **降级原因新增在哪**：`AcceptCard.note`（`review/services.py::_with_pr_degrade_note`），真实
   样例见上文"实现已完成 §1"，不含 token/密文。
2. **token 续期事务方案**：独立 session + `SELECT ... FOR UPDATE` 行锁，理由见上文"实现已完成
   §2"；回滚场景下续期已提交、不受影响；并发场景下靠行锁+二次检查复用赢家结果、不重复调 GitHub。
3. **回归测试关键断言**：`test_refresh_survives_outer_transaction_rollback`——外层事务 rollback
   后，新开的第三个 session 读到的仍是续期后的新 token（`tests/integration/test_oauth_token_
   refresh.py`）。
4. **降级语义未变**：`None` 仍然只代表"走降级"，`_refresh_and_persist_token`/`get_github_user_
   token_with_reason` 所有失败分支（HTTP 失败、解密失败、provider 未配置、连接不存在）都是
   `return None`，没有新增任何会向外抛出的异常路径。

## 约束遵守情况

只动了 `backend/app/domain/oauth/`（`services.py`、`repositories.py`）、
`backend/app/domain/review/services.py` 和必要的测试（`tests/unit/test_oauth_service.py` 改写、
新增 `tests/integration/test_oauth_token_refresh.py`）。未触碰 `deploy-dev.yml`、`build.yml`
concurrency、任何迁移文件、`claude-review.yml`。

## 传话方式

拿不准的事实性问题：`cheese api POST /topics/e593d59c-ca17-4dcc-b0ff-57980823fd22/comments
--data '{"author": "cheese", "content": "..."}'` 问回父话题。

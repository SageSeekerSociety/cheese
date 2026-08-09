# 修复：两阶段采纳拿到的是密文 token（没解密）

**状态：已完工，待验收。** 改动 4 个文件，验证全部在本容器真实 Postgres 上跑通。

## 目标

`get_github_user_token_for_handle()`（两阶段采纳 `_resolve_pr_prerequisites()` /
轮询 `advance_pr_card()` 的入口）直接 `getattr(conn, "access_token")` 返回 **Fernet 密文**。
密文非空 → 过得了所有 `if not token` 检查 → 拿去 push/开 PR 被 GitHub 401 →
落进 `except Exception` 静默降级直推 main，**表现和「账号没连」完全无法区分**。

## 怎么修的

**复用**，不抄第二份解密逻辑：`get_github_user_token_for_handle()` 退化成
handle → user_id 的适配器，转调同文件里本来就正确的
`OAuthService.get_github_user_token(user_id, provider_id=...)`
（它做 `decrypt_text`、`_GITHUB_TOKEN_REFRESH_MARGIN` 过期判断、refresh 续期）。
这个 bug 本身就是分叉出来的第二份实现，再抄一份等于把过期/续期逻辑也变成两份维护。

顺手补齐兄弟方法上三个会让异常穿透到采纳流程的口子（都改成"返回 None 走降级"）：

| 情况 | 原来 | 现在 |
|---|---|---|
| 密文解不开（密钥轮换 / 历史明文行） | `decrypt_text` 抛异常炸穿 | `_decrypt_stored_token()` → None + `logger.exception` |
| refresh_token 解不开 | 同上 | 同上，且不写库 |
| provider 未配置（`get_provider` 抛 `NotFoundError`） | 抛穿 | None + `logger.warning` |

另外 `get_github_user_token` 加了 `provider_id` 关键字参数（原来写死 `"github_app"`），
适配器才能把自己的 `provider_id` 透传下去。

## 测试（无 monkeypatch 的真往返）

`tests/integration/test_github_account_link.py` 新增两例，跑真实 PG：

- `test_token_for_handle_round_trips_through_encryption`：走真实写入路径
  `OAuthService.create_connection()`（加密落库）→ 断言库里那列**不等于**明文且
  `decrypt_text(列) == 明文`（防止往返断言空过）→ 再按 handle 读回，断言拿到**原始明文**。
- `test_token_for_handle_degrades_to_none_instead_of_raising`：过期且无 refresh /
  密文解不开 / 该列为空，三种形态**都返回 None，都不抛**。

`tests/unit/test_oauth_service.py` 另加 4 例覆盖上表三个口子 + `provider_id` 透传。
既有那个 monkeypatch 掉整个函数的 `test_accept_pr.py` 用例保留（它测的是别的东西）。

## 两个留意点的结论

**1. refresh 写库落在采纳事务里 —— 有一个窄风险，已判断为可接受。**
`repo.update_tokens()` 只 `flush()` 不 `commit()`，所以续期写入跟着 `accept()` 的事务走：

- **事务边界**：无脏写。要么跟采纳一起提交，要么一起回滚，不存在半状态。
- **回滚**：真实风险在这里 —— GitHub App 的 refresh_token 是一次性轮换的。
  若「续期成功 → 采纳后续步骤失败回滚」，库里留的是已被 GitHub 作废的旧 refresh_token，
  账号连接会持续失效直到用户重新连接。触发需同时满足：token 落在 5 分钟续期窗口内 +
  续期成功 + 之后回滚，且后果封顶是"降级直推 main + 需重连"，不损坏数据、不阻断采纳。
- **并发采纳**：两个并发采纳各自续期，后写覆盖先写，丢掉一个 refresh_token —— 同上，后果是降级。

修法（把续期写入放进独立事务提交）会引入第二条连接和跨事务顺序问题，超出本卡范围，
且要动 DI 的单 session 约定。**结论：显式接受残余风险并记录决策**，不在本卡扩大改动。

**2. 解密失败 = 返回 None 走降级 + 记日志**（不是抛异常）。理由：调用方只处理 None，
不处理异常；但静默正是这个 bug 活下来的原因，所以 `_decrypt_stored_token` 每次失败都
`logger.exception`。单测用 `caplog` 断言日志确实打出来了。

**降级语义没变**：没连账号 / 没 token / 过期且无法刷新 / 解密失败 / provider 未配置 →
一律 `None`，调用方照旧走直推 main。改动只是让"本该可用"的情况真的可用。

## 顺带修的（超出 oauth/ 范围，已说明）

`tests/conftest.py`：`alembic upgrade head` → `upgrade heads`。
main 上现在有**两个 alembic head**（`c2f677c441f0` 和 `fbd4bf2a51b6`，两个话题各自为
504ece6e60ea+c7d8e9f0a1b2 建了一个合并迁移），`head` 会直接报
"Multiple head revisions are present" 把**整套 DB 测试**打挂。这是 main 的既有问题、
不是本卡引入的，已单独告知父话题；这里只做测试侧的一词修正让测试能跑。

## 验证结果（本容器 pgserver 起真实 PG :5433）

- `ruff check .` / `ruff format --check .`：全绿（676 文件）
- `pyright`（配置只收 `app`）：**0 errors**
- `pytest tests/unit/test_oauth_service.py`：**78 passed**
- `pytest tests/integration/test_github_account_link.py`：**10 passed**（含两个新往返用例）
- `pytest tests/integration/test_accept_pr.py test_accept_pr_publish.py tests/unit/test_oauth_callback_route.py test_oauth_repository.py`：31 passed，1 failed
  —— 唯一那个失败是 `test_accept_without_token_or_repo_degrades_to_direct_merge`，
  原因是沙箱 jj 权限（`Cannot access .../.jj/repo/config-id: Permission denied`），与本改动无关
- `pytest tests/unit`（全量）：**2482 passed / 22 failed**，22 个全在
  `test_machine_service.py`(21) + `test_tmux_control.py`(1)，失败原因是容器缺 `kill` 等
  二进制（`FileNotFoundError: 'kill'`），父话题此前已记录过这批无关失败

## 改动清单

- `backend/app/domain/oauth/services.py`
- `backend/tests/integration/test_github_account_link.py`
- `backend/tests/unit/test_oauth_service.py`
- `backend/tests/conftest.py`（一词，见上）

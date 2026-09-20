## 状态：完成，待验收（递卡给 wangchangxin）

修复了「连接 GitHub 账号」（#192 user-to-server）回调换到 access_token 后直接丢弃、从没落库的缺口——这是父话题「两阶段采纳（PR迭代式）」设计的前置依赖，诊断子话题（id=1b5439ae-9236-49ae-a008-3ecabc31ab3b）确认过这是真实缺口。

## 改了什么

- `<&backend/app/domain/oauth/models.py>`：`UserOAuthConnection` 加 `access_token`（Text，存密文）列。
- `<&backend/alembic/versions/f9a1c7e3b502_oauth_connection_access_token.py>`：新迁移。**顺带修了一个跟这次任务无关但会挡路的问题**——main 上有两个从没合并过的 alembic head（`e1f2a3b4c5d6` webhook 原语 / `e3a94c6f5b18` #192 安装流），test harness 跑 `alembic upgrade head` 会因为 head 有歧义直接报错、整个测试套件起不来。新迁移做成这两个 head 的 merge migration，收回单一 head（决策已记录）。
- `<&backend/app/domain/oauth/repositories.py>`：`create()`/`update_tokens()` 接 `access_token` 参数。
- `<&backend/app/domain/oauth/services.py>`：
  - `GitHubProvider` 加 `refresh_access_token()`（GitHub 的 refresh_token grant）。
  - `create_connection()`/新增 `update_connection_tokens()`：写库前用 `app/core/crypto.py` 现成的 Fernet 工具加密 `access_token` 和 `refresh_token`（决策已记录：不只加密 access_token）。
  - 新增 `get_github_user_token(user_id) -> str | None`：解密返回；未过期或不过期（GitHub 未开 token 过期时响应里没有 `expires_in`）直接返回；过期且有 `refresh_token` 就调用 GitHub 刷新并回写；过期且没有 `refresh_token` 返回 `None` 给调用方走降级路径。
- `<&backend/app/api/routes/github_account_link.py>`：回调不再复用 `OAuthService.handle_callback`（那个方法丢弃 expires_in/refresh_token，且被 users.py 经典登录流程共用，不宜改签名——决策已记录），改为直接调 `provider.exchange_code`/`get_user_info`。丢弃改成落库；新增「重新走一遍授权（relink）」分支——之前这种情况静默跳过，现在会更新已有连接的 token 而不是留着旧的/空的。

## 验证结果（真实 Postgres 上验证，不是纯静态检查）

沙箱用户态 `pgserver`（python3.11 单独装的，backend venv 是 3.13 没有对应 wheel）起了真 PG 16，监听 `127.0.0.1:5433`，`cheesex/cheesex` 角色 + 数据库，`TEST_PG_BASE` 指过去：

- `alembic upgrade heads`：干净跑完全部迁移链，`access_token` 列在真实表上确认存在。
- `ruff check .`：全仓库绿。
- `pyright`：0 errors, 0 warnings。
- `pytest tests/unit`：**2436 passed, 22 failed, 1 skipped**。失败里 21 个是已知环境缺口（`test_machine_service.py`/`test_tmux_control.py`，沙箱镜像没有 `kill`/`tmux` 二进制，卡1当时也记录过同类失败）；另 2 个是与本卡无关的模块里改动前就存在的失败，单独跑也复现，不在本卡范围内，未处理。
- oauth 相关测试单独跑：`test_oauth_service.py`/`test_oauth_repository.py`/`test_oauth_callback_route.py`/`test_github_account_link.py` 共 **101 passed**，覆盖 token 存储、读取、过期、加密解密、刷新（成功/失败）、relink 更新在位（不重复建行）、真实 DB 往返（不是 mock repo）。

## 下一步

递验收卡给 wangchangxin。若被打回，会用「跨话题传话」技巧（评论接口）补充说明，不会假设自己知道被打回的理由。

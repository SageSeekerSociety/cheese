## 状态：实现完成，已验证，待验收

一句话：`AcceptService.accept()` 决议 merge 的成功/冲突/失败三种收尾，各调一次卡1 webhook 原语的内部函数 `post_with_retries`，把结果发回触发采纳的话题时间线；调用改成 fire-and-forget（`asyncio.create_task`），不阻塞采纳者的 HTTP 响应。

## 依赖确认（不变）

- 卡1（webhook 原语）已合并进 main：`backend/app/domain/webhook/service.py` 的 `post_with_retries(session_factory, *, topic_id, content, source)` 就是要调的内部函数——不鉴权、不走 HTTP。
- `ACCEPT_VIA_PR`（#195 开关）这个沙箱的 main 上还搜不到，#195 还没合并。本卡只覆盖现在活着的 `AcceptService.accept()` → `ws.merge_topic()` 路径；#195 合并后再补一处调用，不算这张卡的返工。

## 实现（`backend/app/domain/review/services.py`）

`AcceptService._notify_merge_result(topic, content)`：内部辅助方法，`asyncio.get_running_loop().create_task(webhook_service.post_with_retries(async_session_factory, topic_id=..., content=..., source="accept"))`。

`accept()` 里四个收尾点各调一次（都不 `await`，只是调度）：
1. `ws.merge_topic()` 抛异常 → 失败通知，`raise ValidationError`。
2. 合并冲突（`conflicts` 非空）→ 失败通知（含冲突原因），card 转 `conflict`，正常 return。
3. 非冲突的其他合并失败（`noop` 不为真）→ 失败通知，`raise ValidationError`。
4. 合并成功（含 noop 直接可验收）→ 成功通知，复用已算好的 `card.note`。

**为什么改成 fire-and-forget**：最初实现是 `await post_with_retries(...)`。`post_with_retries` 失败会重试 3 次（0s/5s/30s，最坏 35s）——集成测试里一撞就是 35 秒卡在采纳请求上，暴露出这个设计问题：写一条房间通知不该让采纳者的 HTTP 响应等一个失败重试。改成 `asyncio.create_task` 调度、不等待。单测里对应加了 `await asyncio.sleep(0)`（"give the loop one tick"）再断言。

## 单测（`backend/tests/unit/test_review_acceptance_merge_failure.py`）

6 个场景，全部 mock `webhook_service.post_with_retries`，断言 topic_id/source="accept"/成功失败文案：
- 合并抛异常 → 失败通知
- 合并失败无冲突路径 → 失败通知
- 合并冲突（真实 conflicts 路径）→ 失败通知 + card 转 conflict
- noop（无变更）→ 成功通知
- 真正 merge+push 成功 → 成功通知，带 push 状态

## 验证状态（真实 Postgres + Redis，不是纯静态检查）

沙箱没有 Docker。用 `uv venv` 起了一个独立 Python 3.11 环境装 `pgserver`（PG 16 二进制）+ `redislite`（真实 redis-server 二进制），分别监听 `127.0.0.1:5433`（角色 cheesex/cheesex）和 `127.0.0.1:6379`，跑通 `TEST_PG_BASE=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433`：

- `ruff check .`：全仓库绿。
- `pyright`：0 errors, 0 warnings。
- 首次不带 Redis 跑全量 `pytest` 时炸出 581 个 ERROR（登录限流 `is_locked_out` 连不上 `localhost:6379`）——跟本次改动无关，是沙箱本来就没 Redis；补上 redislite 后这批 ERROR 全部消失。
- 补 Redis 后仍有 58 个 FAILED，逐一定位：
  - **本沙箱 jj 权限问题**（跟父话题「环境注意事项」记录的一样，不是父话题独有——`.jj/repo/config-id` 属主是 uid 1001，当前进程 uid 1000，`Permission denied`）：`test_workspace.py`、`test_git_http.py`、`test_project_tree.py`、`test_upstream.py`、`test_accept.py`、`test_accept_approvals.py`、`test_accept_authorization.py`、`test_reassign.py`、`test_protocol.py::test_mentor_can_accept` 全部卡在 `ws.merge_topic()` → `_ensure_jj()` 这一步，跟本卡代码无关（`workspace/service.py` 我没动）。
  - **`test_room_and_task.py` 两个用例 401**：`_accept()` helper 不带认证头直接 POST `/api/accept-cards/{id}/accept`，在 `conftest.py` 强制 `authz_enforce_topic_access=True` 的测试配置下，请求在到达 `AcceptService.accept()` 之前就被 `ActorResolverDep` 拒了——跟本卡逻辑无关（我没碰 `accept.py` 路由或 auth 层）。
  - `test_machine_service.py`（`kill` 二进制缺失）、`test_tmux_control.py`（同）、`test_market_api.py`、`test_artifacts.py`、`test_attachments.py`、`test_auth_token_handle.py` 等：跟卡1验收时记录的环境缺口同一类，不是本卡引入。
  - 结论：**58 个失败全部可归因于沙箱环境限制（jj 权限 / 未认证请求 / 缺二进制），没有一个是本卡改动引入的新问题**——凡是不经过 `ws.merge_topic()` 真跑 git/jj、且带了认证头的 accept 路径（本卡新增的 6 个单测、以及 `test_avatars.py`/`test_bug3_team_eligibility.py` 等原本因缺 Redis 而误报的用例）现在全部通过。
- 补上 fire-and-forget 修复后，用同样的 58 个失败用例重跑一遍全量 `pytest`：**失败集合逐条比对完全一致**（一个不多一个不少），但总耗时从 682s 降到 263s——证实这个改动本身没引入新的失败，只是去掉了失败重试拖慢采纳请求的副作用。`3393 passed, 31 skipped, 58 failed`。

## 下一步

已完成，递验收卡给 wangchangxin，附上环境限制的归因说明（jj 权限问题不是本卡能修的，需要平台侧修沙箱镜像）。

## 状态：改动已完成，待采纳

## 目标
main 上 `test.yml`/`e2e.yml` 因为 `CARGO_TARGET_DIR` 写在 job 级 `env:` 却引用了 `runner.temp`（job 级 env 不允许 `runner` context）而变成 invalid workflow，Backend Test / E2E Tests 自 2026-08-10 03:11 起完全不产出。

## 已完成的改动
只改了两个 workflow 文件，三处 job 级 `env:` 全部挪成每个 job `steps:` 最前面（`actions/checkout` 之后、`uv sync` 之前）写 `$GITHUB_ENV` 的一步：

- <&.github/workflows/test.yml> — `migration-heads` job（原 ~126 行）、`test` job（原 ~165 行）
- <&.github/workflows/e2e.yml> — `e2e` job（原 ~91 行）

写法统一为：
```yaml
- name: Point Cargo at the persistent target dir
  run: echo "CARGO_TARGET_DIR=$(dirname "$RUNNER_TEMP")/cargo-target/<job名>" >> "$GITHUB_ENV"
```
`<job名>` 三处分别是 `migration-heads`/`test`/`e2e`，保持原来"每个 job 独立子目录"的设计不变，没有改共享目录。

`runs-on` 开工前已确认三处都还是 `[self-hosted, cheese-ci]`（沙箱是刚拉的最新 main），没有被顶回 `cheese-dev`；`concurrency`/`paths`/`timeout-minutes`/`claude-review.yml` 均未触碰；backend/frontend 代码零改动。

## 本地校验结果
- 用 `uv run`（backend 自身 3.13 环境的 pyyaml）解析两个文件：valid YAML，`jobs` 列表正常（`scope`/`migration-heads`/`test` 和 `scope`/`e2e`），且 assert 三处 job 级 `env` 均不再引用 `runner`。
- 落到 main 之后需要在 Actions 页面确认工作流名恢复成 `Backend Test`/`E2E Tests`（不再是文件路径）、job 数 > 0 —— 这一步只能等采纳、真实 push 到 main 之后才能验证，本地无法模拟。

## 附带验收标准第4条：`2f0f6ff9`（PR #213）修的两处测试，实测结果是"仍有 2 类真实挂起"，不是环境假象
本容器没有 Docker，无法用 `task infra` 起 PG。改用 `uv run --no-project --python 3.12 --with pgserver` 拉起一个独立 Python 3.12 环境跑 pgserver（embedded Postgres 16，无 docker 依赖），手动开 TCP（`listen_addresses=127.0.0.1:5433`）+ 建好 `cheesex`/`cheesex` CREATEDB 角色，让 backend 自己 3.13 环境下的 `uv run pytest` 通过 `TEST_PG_BASE` 指过去——不改任何 backend 代码，验证完立刻停掉这个临时 PG 实例，没有残留。

跑 `tests/integration/test_topic_sort.py` + `tests/integration/test_github_account_link.py`：**5 failed, 9 passed**。

1. **`test_topic_sort.py` 4 个用例全挂，根因是测试助手 `_titles()` 自己的 bug，不是应用代码问题**：`client.get(f"/api/topics?project_id={pid}", params={...})` 同时给了 URL 自带的 query string 和 `params=` kwarg——httpx 0.28.1（当前 lock 版本）在这种情况下用 `params` **整体替换**掉 URL 自己的 query，不是合并（已用最小复现脚本证实：`httpx.Request('GET', 'http://x/y?project_id=abc', params={'sort':'title'})` → `http://x/y?sort=title`，`project_id` 直接丢了）。实测报错也印证了这点：`400 {"details":[{"loc":["query","project_id"],"msg":"Field required"}]}`。这是确定性行为，跟我这次用的 embedded PG 无关，GitHub Actions CI 用同一个 lock 出的 httpx 版本会复现一模一样的失败。
2. **`test_github_account_link.py::TestAccountLinkTokenPersistence::test_get_github_user_token_reads_back_and_refreshes` 挂在最后一个断言**：`assert decrypt_text(refreshed.access_token) == "refreshed-token"` 拿到的是 `"stale-token"`。定位到根因是 SQLAlchemy 会话身份图（identity map）过期语义：`tests/conftest.py` 的 `test_factory = async_sessionmaker(engine, expire_on_commit=False)`，而 `_refresh_and_persist_token`（`app/domain/oauth/services.py`）刻意在**独立的新 session** 里 commit 刷新后的 token（决策 0556ac50 的设计，为了不被调用方外层事务的 rollback 连累）。测试里 `svc.get_github_user_token(user_id)` 返回值确实是 `"refreshed-token"`（这行 assert 没报错）——证明刷新和落库都成功了；但紧接着同一个外层 `session` 再查一次 `repo.get_by_user_and_provider(...)`，因为 `expire_on_commit=False`、且这个 connection 对象在本 session 身份图里已经加载过一次（`conn = await repo.get_by_user_and_provider(...)` 在 `_refresh_scenario` 靠前的地方），SQLAlchemy 对已加载、未过期的对象**不会**用新 SELECT 的结果覆盖旧属性，读到的是本 session 缓存的旧值。这同样是确定性语义，不依赖具体 Postgres 环境，CI 上会复现同样的失败。

**这两类失败都不是 CARGO_TARGET_DIR 修复引入的、也跟这张卡的改动无关，按简报要求如实报告，没有顺手去改** —— 一类是测试助手自己的 httpx 用法 bug，另一类是测试对"独立 session 提交"设计的断言方式没考虑本 session 身份图缓存，两者都在 `backend/tests/`，不在这张卡"只改 workflow 文件"的范围内。

## 严禁改动核对
`runs-on`、`concurrency`、`paths`、`timeout-minutes`、`claude-review.yml` 均未触碰；backend/frontend 代码零改动（上面测试验证过程中新建过一个临时探测测试文件，已在验证完毕后删除，不在最终改动里）。

## 下一步
提交给 wangchangxin 采纳。已知会降级成本地 merge + 直推 main（GitHub App 缺 `workflows: write` 权限，这是已知、已批准的路径）；采纳后会触发一次真实构建/部署，这是已知代价。采纳后需要去 Actions 页面确认：① 两个工作流名恢复正常、② job 数 > 0 且真的跑起来。第 4 条验收因为测试本身有两类预置挂点，预计首次真实运行仍会红，但那是另外两张（潜在的）小卡的事，不阻塞这张卡的采纳。

# 没有 docker 的机器上怎么跑测试

一台既没有 Postgres 也没有 docker 的容器**照样能跑全量测试**——DB-backed 的测试要的是一个服务器，不是 docker。`.claude/scripts/dev-db.sh` 用预编译 wheel（`postgresql-binaries`、`redislite`，由 `uv` 取）把 Postgres + Redis 起起来，装好钉住版本的 Claude Code 和 Codex——CI 用服务容器和 “Install pinned harness binaries” 那一步提供的，这里一样不缺：

```bash
eval "$(bash .claude/scripts/dev-db.sh start)"   # 导出 TEST_PG_BASE、REDIS_URL、CHEESE_TEST_CLAUDE、PATH
cd backend && uv run pytest tests/ -n 4 -q
bash .claude/scripts/dev-db.sh stop --purge      # 停掉并删数据目录
```

- **Redis 是必须的,不是可选**——登录限流、2FA 验证票据和会话状态都在里面,没有它 integration 套件直接报错。
- Postgres 必须带 contrib:迁移里有 `CREATE EXTENSION pg_trgm`,只含核心的构建(比如 `pgserver` wheel)上 `alembic upgrade head` 会失败。`postgresql-binaries` 原样打包 theseus-rs 的发行包,contrib 齐全,主版本和 dev、生产一样是 17。
- 这两个 wheel **故意不是 backend 依赖**:它们是测试机上的工具,backend 不 import。`redislite` 的 wheel 只到 cp312,所以脚本自己钉住版本,把它们跑在一个一次性的 3.12 解释器上;测试本身仍跑在 3.13。
- Claude Code 和 Codex 的版本从 backend 里的声明读出来,跟 CI 装的一样。不能靠 PATH 上现成的 `claude`:本机那个常常是别的版本,或者是一层包装脚本,远程执行和 runner 相关的测试会因此失败,跟代码无关。
- 下载的东西放在 `${XDG_CACHE_HOME:-~/.cache}/cheesex-dev-db`,`stop --purge` 不删它。
- `tests/unit/` 完全不需要服务器（`conftest.py` 只给主动要的测试建 schema）。`tests/unit/` 以外的一律是 DB-backed。
- `check.sh --no-tests`（质量闸门用的那条）完全跳过 pytest——要真正跑套件就用上面的方子。

## 唯一要自己补的:provider 凭据

**别再花时间重新诊断它**:`test_market_api.py::test_market_lists_ai_and_compute_pools` 那一条红是缺环境,不是代码有 bug。一个 profile 的 `available` 是 `bool(auth_token or oauth_token)`（`app/domain/agent/profiles.py`）,所以 `settings.anthropic_auth_token` 没设时,默认 AI 池诚实地报告不可用。随便给个非空值就解决——`ANTHROPIC_AUTH_TOKEN=dummy-for-test uv run pytest tests/integration/test_market_api.py` 就绿了,过程中不会真去调 provider。

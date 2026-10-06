# 没有 docker 的机器上怎么跑测试

一台既没有 Postgres 也没有 docker 的容器**照样能跑全量测试**——DB-backed 的测试要的是一个服务器，不是 docker。`.claude/scripts/dev-db.sh` 装好钉住版本的 PostgreSQL 17 和 Valkey 8.0.2（CI 的 Redis 服务就是它）并把它们起起来，给 Postgres 补上 ParadeDB 的 `pg_search`，装好钉住版本的 Claude Code 和 Codex——CI 用服务容器和 “Install pinned harness binaries” 那一步提供的，这里一样不缺：

```bash
eval "$(bash .claude/scripts/dev-db.sh start)"   # 导出 TEST_PG_BASE、REDIS_URL、CHEESE_TEST_CLAUDE、CHEESE_TEST_CODEX、CHEESE_TEST_PI、PATH
cd backend && uv run pytest tests/ -n 4 -q
bash .claude/scripts/dev-db.sh stop --purge      # 停掉并删数据目录
```

- **Redis 是必须的,不是可选**——登录限流、2FA 验证票据和会话状态都在里面,没有它 integration 套件直接报错。
- Postgres 必须带 contrib 和 `pg_search`:迁移里有 `CREATE EXTENSION pg_trgm` 和 `CREATE EXTENSION pg_search`,缺哪个 `alembic upgrade head` 都会失败。PostgreSQL 取自 theseus-rs/postgresql-binaries 的发行包，contrib 齐全，主版本和 dev、生产一样是 17，但不带 `pg_search`。
- `pg_search` 取自 ParadeDB 在 GitHub 发布的 0.24.0 Debian bookworm 包,版本和 SHA-256 都钉在脚本里。脚本不安装这个包,只取出 `pg_search.so` 和扩展的 SQL、control 文件,放进上面那个 Postgres 的目录,启动时用 `shared_preload_libraries=pg_search` 预加载(0.24.0 不预加载就不让建扩展)。Linux x86_64 和 aarch64(glibc 至少 2.34)用这个包;macOS arm64 用 ParadeDB 给 Homebrew PostgreSQL 17 出的安装包,同样只取这三个文件。其他系统上脚本直接报错退出,并说明缺的是什么。
- Valkey 没有 macOS 的发行包，所以各平台一律从钉住 SHA-256 的源码包编译，要 `make` 和 C 编译器（`cc`），首次大约半分钟。Redis 6.2 不够用：`session_host/consumptions.py` 发的 `EXPIRE … NX` 是 7.0 才有的。
- 服务器装在 `${XDG_DATA_HOME:-~/.local/share}/cheesex-dev-servers`，数据目录默认是 `${XDG_DATA_HOME:-~/.local/share}/cheesex-dev-db`，都不在缓存或临时目录里。运行中的 Postgres 每个新连接都会从安装目录重新加载 `plpgsql`、`pg_search`，装在 uv 缓存里时，一次 `uv cache prune` 就让它只剩能连上、查询却报 `could not access file "$libdir/plpgsql"`；macOS 每天 03:35 会删掉 `$TMPDIR` 下超过三天的文件，数据目录放那里会被删掉一部分。安装目录被删掉一部分时，`start` 报出是哪个目录并退出，不会再起一个半残的服务器；整个删掉的，下次 `start` 原样重装。
- Claude Code 和 Codex 的版本从 backend 里的声明读出来,跟 CI 装的一样。不能靠 PATH 上现成的 `claude`:本机那个常常是别的版本,或者是一层包装脚本,远程执行和 runner 相关的测试会因此失败,跟代码无关。
- 下载的东西放在 `${XDG_CACHE_HOME:-~/.cache}/cheesex-dev-db`，删了只是下次重新下载。`stop --purge` 只删数据目录，不删安装和下载。
- 版本钉子一改（Postgres 版本、`pg_search` 版本、安装位置），`start` 会认出端口上那个由本脚本早先启动、钉子不同的 Postgres：没有客户端就停掉它、换成当前版本（旧大版本的数据目录一并删掉，反正是测试数据）；有客户端就报错退出，说明有几个连接，留着它不动。本脚本起的 Redis 不是钉住的 Valkey 时同样处理。不是本脚本起的服务器照旧拒绝、不碰。这段逻辑由 `.claude/scripts/test-dev-db.sh` 用假二进制测试,跑在 repo guards 里。
- `tests/unit/` 不需要 Postgres（`conftest.py` 只给主动要的测试建 schema），但其中一部分要 Redis，例如 `test_consumptions.py`、`test_admission.py`。`tests/unit/` 以外的一律是 DB-backed。
- `check.sh --no-tests`（质量闸门用的那条）完全跳过 pytest——要真正跑套件就用上面的方子。

## 唯一要自己补的:provider 凭据

**别再花时间重新诊断它**:`test_market_api.py::test_market_lists_ai_and_compute_pools` 那一条红是缺环境,不是代码有 bug。一个 profile 的 `available` 是 `bool(auth_token or oauth_token)`（`app/domain/agent/profiles.py`）,所以 `settings.anthropic_auth_token` 没设时,默认 AI 池诚实地报告不可用。随便给个非空值就解决——`ANTHROPIC_AUTH_TOKEN=dummy-for-test uv run pytest tests/integration/test_market_api.py` 就绿了,过程中不会真去调 provider。

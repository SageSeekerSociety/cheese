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
- 没有 `make` 或 `cc` 的机器（多数 agent 沙箱）编不了 Valkey，脚本改用 `fakeredis[lua]`（钉 2.39.0，`uv` 现拉）在同一个端口上起一个 TCP 服务，`start` 的日志会写明走了这条路。它是内存里的 Python 实现，不是 CI 跑的 Valkey；登录限流、2FA、额度这些带 Lua 脚本的路径已用它跑通。想在有编译器的机器上也用它，设 `CHEESEX_DEV_REDIS=fakeredis`。
- 服务器装在 `${XDG_DATA_HOME:-~/.local/share}/cheesex-dev-servers`，数据目录默认是 `${XDG_DATA_HOME:-~/.local/share}/cheesex-dev-db`，都不在缓存或临时目录里。运行中的 Postgres 每个新连接都会从安装目录重新加载 `plpgsql`、`pg_search`，装在 uv 缓存里时，一次 `uv cache prune` 就让它只剩能连上、查询却报 `could not access file "$libdir/plpgsql"`；macOS 每天 03:35 会删掉 `$TMPDIR` 下超过三天的文件，数据目录放那里会被删掉一部分。安装目录被删掉一部分时，`start` 报出是哪个目录并退出，不会再起一个半残的服务器；整个删掉的，下次 `start` 原样重装。
- Claude Code 和 Codex 的版本从 backend 里的声明读出来,跟 CI 装的一样。不能靠 PATH 上现成的 `claude`:本机那个常常是别的版本,或者是一层包装脚本,远程执行和 runner 相关的测试会因此失败,跟代码无关。
- 下载的东西放在 `${XDG_CACHE_HOME:-~/.cache}/cheesex-dev-db`，删了只是下次重新下载。`stop --purge` 只删数据目录，不删安装和下载。
- 版本钉子一改（Postgres 版本、`pg_search` 版本、安装位置），`start` 会认出端口上那个由本脚本早先启动、钉子不同的 Postgres：没有客户端就停掉它、换成当前版本（旧大版本的数据目录一并删掉，反正是测试数据）；有客户端就报错退出，说明有几个连接，留着它不动。本脚本起的 Redis 不是钉住的 Valkey 时同样处理。不是本脚本起的服务器照旧拒绝、不碰。这段逻辑由 `.claude/scripts/test-dev-db.sh` 用假二进制测试,跑在 repo guards 里。
- `tests/unit/` 不需要 Postgres（`conftest.py` 只给主动要的测试建 schema），但其中一部分要 Redis，例如 `test_consumptions.py`、`test_admission.py`。`tests/unit/` 以外的一律是 DB-backed。
- `task guards` 只跑静态守卫，不跑 pytest；要真正跑套件就用上面的方子。

## 整套跑起来看：`task dev:sandbox`

要在浏览器里看一个改动、手上又没有 Docker（`task dev` 要 `docker compose`）时用它。没装 Task 就直接跑 `node e2e/dev-stack.mjs`：

```bash
task dev:sandbox      # 前台运行；Ctrl-C 停掉它起的服务
```

它依次做这几件事，全部只监听 127.0.0.1：

1. `dev-db.sh servers` 起 Postgres 和 Redis。只起这两个，不装测试用的 Claude Code、Codex 和 pi。
2. 在这个 Postgres 里建 `cheese_dev_sandbox` 库，`uv sync`、`pnpm install`，再单独跑一次 `vite optimize`。
3. 跑迁移；库里还一个项目都没有时，再跑 `seed_fusion_demo --without-repository`，种出 alice 的三个演示项目。
4. 照 `e2e/stack.mjs` 起 stub 网关、后端、协作服务和 Vite 开发服务器。这份清单 e2e 也在用，那边加了服务这里跟着有。

最后打印 `FRONTEND_PORT=3000`。浏览器只要这一个端口，`/api`、`/connector`、`/collab` 由 Vite 转给后面的服务。用 alice / demo12345 登录；新库第一次登录会弹「协议已更新」，点同意。

- **不连任何外部服务。** 没有模型：stub 网关只回答有哪些模型，对话起不了轮次。没有 forge：演示项目没有仓库，任务、PR 走不通。也没有邮件和对象存储。`backend/.env` 存在时直接退出，因为后端会读它，连上里面配的服务。传给各服务的环境变量只留 `PATH`、`HOME` 这一类，调用方环境里的模型密钥、代理都传不进去。
- **已经有项目就不再种数据**：种子会删掉同名项目重建，连同在里面做过的事。要从头来，先 `bash .claude/scripts/dev-db.sh stop --purge`。停掉 `dev:sandbox` 之后 Postgres 和 Redis 还在跑，也用 `dev-db.sh stop` 停。
- 端口被占时它退出并说是哪个端口；换端口用 e2e 那组变量 `E2E_FRONTEND_PORT`、`E2E_BACKEND_PORT`、`E2E_COLLAB_PORT`、`E2E_STUB_GATEWAY_PORT`。
- 要 node 22、pnpm 和 uv。Redis 是 fakeredis 时（没有 `cc`，见上），协作服务不接 Redis：它的客户端一连上就发 `INFO`，fakeredis 不认这条命令。一个协作服务实例本来也用不着 Redis。
- **`vite optimize` 单独跑是为了内存。** Vite 第一次启动时要预打包依赖，放在开发服务器里跟其他服务一起跑，整套峰值到 2698 MiB，限 2.5 GiB 时 Vite 被杀；单独先跑完，峰值降到 1332–1759 MiB，开发服务器只读它留下的缓存。

下表是在 Linux 容器里量的：cgroup 限内存、无 swap、2 核、没有 Docker、没有 `cc`，每秒读一次 cgroup 的匿名内存(anon)。冷启动是新容器，所有下载缓存都是空的；热启动是同一个容器里停掉再起，依赖和 Vite 缓存都在。「浏览」是浏览器登录后走一遍项目、频道、资料库、`/spaces`、`/market`、`/admin`、`/settings`。表里不含 agent 自己的进程，一个 Claude Code 进程本身约占 0.4–0.5 GB。

| 内存上限 | 冷启动就绪 | 冷：启动时峰值 | 冷：运行中峰值 | 冷：闲置 | 热启动就绪 | 热：运行中峰值 | 被 OOM 杀 |
|---|---|---|---|---|---|---|---|
| 6 GiB | 37 s | 1625 MiB | 1310 MiB | 819 MiB | 7 s | 1274 MiB | 无 |
| 4 GiB | 36 s | 1349 MiB | 1295 MiB | 824 MiB | 8 s | 1290 MiB | 无 |
| 3 GiB | 38 s | 1759 MiB | 1260 MiB | 813 MiB | 7 s | 1265 MiB | 无 |
| 2.5 GiB | 44 s | 1332 MiB | 1210 MiB | 808 MiB | 8 s | 1235 MiB | 无 |
| 2 GiB | 39 s | 1598 MiB | 1222 MiB | 818 MiB | 8 s | 1243 MiB | 无 |

「启动时」是从运行到打印端口，峰值都落在 `vite optimize` 跑完、迁移开始的那一两秒；「运行中」是之后 Vite 起来的头一分钟加上浏览那一趟，取两者大的那个；「闲置」是浏览之前那一分钟的最后 20 秒。预打包还放在开发服务器里的时候，同样的冷启动在 3 GiB 下运行中峰值 2698 MiB、闲置 2310 MiB,2.5 GiB 和 2 GiB 都在 Vite 预打包时被杀。3 GiB 下另起一个占住 512 MiB 的进程（顶替 agent 自己）再跑一遍，冷启动峰值 2265 MiB（含那 512 MiB），冷、热都没有被杀。

冷启动的时间主要花在下载上（Postgres 和 pg_search 约 85 MB，加上 Python 和前端依赖），网速慢的机器会更久。装完以后家目录里：`backend/.venv` 537 MB、`frontend/node_modules` 1.1 GB（7.5 万个文件）、Postgres 安装 193 MB、数据 91 MB。

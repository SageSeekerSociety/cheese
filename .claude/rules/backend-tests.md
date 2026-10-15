---
paths:
  - "backend/tests/**"
---

# Backend tests — pitfalls, by the symptom you will see

## 症状：路由返回 `401 {"message": "Login required"}`，同一个 Authorization 头在别的路由上却能用

**容易误判成**：token 过期、签名密钥不对、fixture 没带 header。

**真因**：token 分两种，外表一样。`create_access_token(user_id, handle=...)` 签出的
token 带数字用户 id，所有路由都认；`create_access_token(None, handle=...)` 签出的只带
handle，只有按 handle 认人的路由（accept / split / connector / project，走
`caller_handle()`）认它，`Depends(require_auth_user)` 的路由把它当访客，于是 401。
`create_access_token` 的 docstring 讲了两层各认什么。

**做法**：先看目标路由的依赖。要数字 id 的，用 `seed_user()` 或
`authenticated_user` / `auth_headers` fixture；只按 handle 认人的，用
`session_auth_headers()` / `session_token()`。

## 症状：contract 测试一登录就报 Redis 连不上，或者一片 401

**容易误判成**：contract harness 的鉴权坏了，照 integration 的写法补一个登录。

**真因**：contract harness 不跑 Redis，而登录路由依赖 Redis（限流、2FA 会话），所以
POST `/users/auth/login` 必然失败。

**做法**：用 `authed_client` fixture，它直接签 token，不走登录。

## 症状：`tests/unit/test_no_adhoc_auth_helpers.py` 红了，点名你的文件

**容易误判成**：命名洁癖，改个名字或者塞进 fixture 就能绕过。

**真因**：这条守卫不看名字，只看「不带用户 id 的 `create_access_token` 调用」。测试里
只带 handle 的 token 只能从 `tests.integration.conftest` 的 `session_token()` /
`session_auth_headers()` 来；历史原因见守卫文件的 docstring。

**做法**：改用共享 helper。helper 不够用就扩展 helper；实在不行，才在守卫的 `ALLOWED`
里加一条，并写明理由。

## 症状：一批你没碰过的测试变红，报 `AttributeError`，或者错误出在离改动很远的地方

**容易误判成**：环境脏了、缓存问题，于是清 `.pytest_cache`、重装依赖。

**真因**：测试替身没跟着生产代码改。`monkeypatch.setattr` 一个已改名的函数会抛
`AttributeError`；更隐蔽的是假实现还返回旧形状的值，下游用到时才出错。

**做法**：改函数签名、返回值或模块级名字时，在同一次改动里 `grep backend/tests/`，
把它的每个 fake、mock、monkeypatch 一起改掉。

## 症状：一批看不出关联的测试红了，单独跑每一个都是绿的

**容易误判成**：上一条（替身过期），或者当成 flaky 加重试。区别在于单独跑：替身过期
单独跑照样红，这一条不会。

**真因**：两个独立的 pytest 进程同时在同一个测试库上跑，比如手动跑的同时 pre-commit
钩子也在跑。xdist 的各个 worker 各有自己的库，互不干扰；两次独立调用则都落在
`cheesex_test` 上，互相踩数据。

**做法**：串行跑，或者给第二个进程换一个库。

## 症状：改了某条路由的鉴权，只有一两个测试文件红

**容易误判成**：红的都修完就结束了。

**真因**：调用这条路由的测试文件往往不止你打开的那几个。

**做法**：按路由路径 grep，而不是按测试名，把命中的每个文件都跑一遍。

## 症状：测试报 `took N s with setup, over the <layer> layer's N s ceiling`

**容易误判成**：CI 机器慢，重跑一次就绿。

**真因**：上限（`tests/conftest.py` 的 `_LAYER_CEILING_S`）大约是 runner 上未列入名单的最慢测试的两倍，
撞上它的测试几乎总是在等一个真实的超时、退避、轮询间隔或进程。

**做法**：找到它在等什么，把那个值做成测试能传小值的参数。不要把它加进 `tests/slow_tests.txt`，
也不要提高上限：这两条只许变紧。

## 选择鉴权方式

| 目录 | 用什么 | 定义在 |
|---|---|---|
| `tests/integration/` | `auth_headers` fixture（真用户、真登录）；要裸 token 用 `seed_user` | `integration/conftest.py` |
| `tests/integration/`，只按 handle 认人的路由 | `session_auth_headers()` / `session_token()` | `integration/conftest.py` |
| `tests/contract/` | `authed_client` fixture | `contract/conftest.py` |

不要自己手写这三种机制，原因见上面第一、三条。

## one-test-slot-per-runner

同一台机器上并发的每个 pytest 进程必须各占一个 `CHEESE_CI_SLOT` + `CHEESE_CI_REDIS_BASE_DB`。库名按 slot 派生且测试会 CREATE/DROP DATABASE，共用 slot 的两个进程会互相删库。

症状：`InvalidCatalogNameError: database "cheesex_test_N_c" does not exist` 或 `connection was closed in the middle of operation`，失败点在文件间乱跳，单独跑永远绿。

`CHEESE_CI_REDIS_BASE_DB` 必须 ≤ 15：valkey 只开 DB 0–15，写 16 必报 `DB index is out of range`。slot 号可以大，redis 基号不能。

**Why:** 2026-09-28 主 agent 和一支分身被指到同一个 slot，拿到假的测试失败，差点当代码缺陷去查。2026-10-12 又犯一次：自己在后台挂着一轮 76 个文件的套件，随手在前台单跑一个文件，两边都没设 slot，一轮从 820 passed/5 failed 变成 70 failed，失败点全是 `cheesex_test_c` 不存在、连接被关、库不干净。

**How to apply:** 派分身时在简报里写死它专用的 slot 号和 redis 基号，写明别和别人共用；自己后台挂着一轮时也别在前台跑第二个测试，先 `ps -ww | grep pytest` 数一遍进程。见到上面的报错先查有没有第二个 pytest 在跑同一 slot——两个互删库的进程里，干净那轮才是真的。

## test-unit-needs-pinned-harness

本地测试的这些红先按环境分流，别当代码回归：

- `tests/unit` 若 setup 报 `must point to the pinned Claude Code build`，先 `eval "$(bash .claude/scripts/dev-db.sh env)"` 补齐钉死构建和 `CHEESE_TEST_*`；标准沙箱跑真实沙箱用例会 `PermissionError: unshare: Operation not permitted`，换 `vm`。`test_every_harness_checkpoints_each_turn.py` 的 pi/codex 两条本机 4 秒内等不到推送会 `CancelledError`，干净 `origin/main` 也复现而 CI 绿。
- `backend/tests/contract/test_api_addressing_contract.py` 两条路由枚举测试报 15 秒墙钟超时，先比干净基线：本机 `import app` 约 18 秒。别调大 `_LAYER_CEILING_S`、别加 `slow_tests.txt`。
- 本机跑 e2e 的 seed 前先 `bash backend/tests/forgejo/start.sh`，否则 `seed_fusion_demo` 建不出演示账号和项目。分片红在 MinIO 30 秒健康检查（不是 Playwright 断言）先重跑。
- `test_legacy_migration_respects_old_lock_and_recovers_commit_before_ack` 报 `redis.exceptions.ConnectionError: Connection closed by server.`：本机没 C 编译器，`dev-db.sh` 建不出 Valkey 退成 fakeredis，而它的 `BRPOP` 空闲断连 fakeredis 复现不出；CI 的真 Valkey 上过。

**Why:** 这些红由工作机缺构建、权限、导入速度或测试栈启动时序决定，CI 或干净基线正常。

**How to apply:** 按错误原文和失败步骤分类，补环境或重跑；交付里分开说本机环境红和 CI 结果，不把局部绿写成整仓绿。

## tests-that-read-a-conversation-break-on-announcements

改「平台往对话里说不说哪一句」时（新增/去掉一条系统事件、换公告文案），会红的测试往往不在用到 `open_task` / `teammate_task` / `read_task` 这些辅助函数的文件里，而在**直接按 `conversation_id` 取块、再把结果当空表或当全集**的文件里：`_events(...) == []`、`_history(...)["data"] == [事先种下的 id...]`、`len(events) == 1`。多一行平台事件就全红，而且报的是 `assert [...] == []`，看不出跟你的改动有关。

**Why:** 2026-10-12 改「任务建出来即已开始」时，让建任务那一下也往任务对话里写「任务已开始」。我先按辅助函数筛出的 76 个文件全绿，CI 却红了三轮：`test_task_naming.py` 两条（断言任务对话里一条事件都没有）和 `test_chat_history.py` 一条（断言按关键词搜出来的就是种下的那两条）。

**How to apply:** 动了平台说的话，跑之前先 grep `Block.kind == BlockKind.event`、`/history`、`= []` 落在任务或房间对话上的断言，按「读这段对话」而不是「用了哪个辅助函数」定范围；拿不准就整套跑 `tests/integration`。测试本身也别写成空表——钉「这次动作没多说一句」（跑前跑后的块 id 相同）和「该回来的按序回来」（加 `kind` 收窄），下次改文案不会再红。

## merge-group-accept-pr-test-is-intermittent

`backend / integration (1/5)` 在 merge group 里红、并且点名
`tests/integration/test_accept_pr.py::test_the_merge_that_closed_a_task_does_not_close_it_again`
时，先当成已知的间歇红。它是那个分片上唯一失败的用例（`1 failed, 1112 passed, 1 rerun`），
报 `AssertionError: assert 'open' == 'closed'`——接受卡合并后任务没被关掉。

2026-10-13 全天 63 次 merge group 运行里红 11 次，其中 4 次就是它（另 3 次是
`test_indexed_rows.py` 那条 planner 断言）。它跨不同 merge commit 反复出现，
不是某次提交改坏的；当时本地连跑 20 多次也没复现。

分片编号会变（4 分片矩阵时在 `integration (0..3/4)` 里，5 分片后是 `(1/5)`），但切的规则
是按用例 nodeid 的 sha256 取模，同一条用例永远落同一个分片——不同分片号是矩阵变了，
不是好几个分片各有各的病。

merge group 的 run 不能重跑：每个 merge commit 只有一次 Required CI
（失败时 GitHub 也不会给第二次 attempt），`.github/scripts/rerun-failed.py:29` 又只接
`event == pull_request` 的 run。队列表自己会再排一次，那是新的 merge commit。

**Why:** 每张后端 PR 合并都因此多花一轮队列时间；把它当成本次改动造成的，会白查半天、
甚至去改无关的代码。反过来，本地复现不出来也不能当成「没漏测」的证据。

**How to apply:** 见到这盏红灯先看测试名和 run 的 event：是它就别改代码、也别提重跑，
等队列重排；真要修，先按提交时间把历次红点点出来，确认是不是同一处 flake 再动手。
竞态机制至今没证实，也可能是真 bug（accept→merge→close 之间），要先按这一步确认。

## indexed-rows-planner-assertion-is-flaky

`tests/integration/test_indexed_rows.py::test_the_speaking_again_reads_do_not_reach_past_the_question[asyncio]`
在 `backend / integration (4/5)` 红，断言查询计划里不该出现 `GroupAggregate`。2026-10-13 在
三个不同的 merge commit 上各红一次，报出的成本值每次不同（832.09 对 993.09）——那是
Postgres planner 按统计信息做的选择，不是代码变了。同一天还有 52 次 merge group 是绿的。

**Why:** 「钉查询计划形状」的断言天生不稳，看到它红会误以为是谁把查询写坏了。

**How to apply:** 见到它红了先和业务改动解耦。要动就动测试本身（改成钉索引是否存在、
或者钉更粗的判据），别为了它去调 SQL——调 SQL 只是把这个 flake 挪到别处。

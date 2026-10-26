---
paths:
  - "backend/tests/**"
---

# Backend tests — pitfalls, by the symptom you will see

## 症状：contract 测试一登录就报 Redis 连不上，或者一片 401

**容易误判成**：contract harness 的鉴权坏了，照 integration 的写法补一个登录。

**真因**：contract harness 不跑 Redis，而登录路由依赖 Redis（限流、2FA 会话），所以
POST `/users/auth/login` 必然失败。

**做法**：用 `authed_client` fixture，它直接签 token，不走登录。

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
| `tests/integration/`，只要一个 handle | `session_auth_headers()` / `session_token()`（只带 handle 的 token：按 handle 认人的路由都认它；要求登录用户的路由还要库里真有这个用户） | `integration/conftest.py` |
| `tests/contract/` | `authed_client` fixture | `contract/conftest.py` |

不要自己手写这三种机制。

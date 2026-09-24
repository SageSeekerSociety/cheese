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

## 选择鉴权方式

| 目录 | 用什么 | 定义在 |
|---|---|---|
| `tests/integration/` | `auth_headers` fixture（真用户、真登录）；要裸 token 用 `seed_user` | `integration/conftest.py` |
| `tests/integration/`，只按 handle 认人的路由 | `session_auth_headers()` / `session_token()` | `integration/conftest.py` |
| `tests/contract/` | `authed_client` fixture | `contract/conftest.py` |

不要自己手写这三种机制，原因见上面第一、三条。

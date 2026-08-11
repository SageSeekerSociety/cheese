---
paths:
  - "backend/tests/**"
---

# Backend tests — pitfalls, written symptom-first

Every entry below starts from **what you will actually see** (an error string, a
status code, a failure pattern), then names **the wrong diagnosis it invites**,
then the real cause. You meet these as symptoms, not as rules — so they are
indexed by symptom. The one section that has no symptom is marked as such at the
bottom.

---

## 症状：路由返回 `401 {"message": "Login required"}`，可同一个 Authorization 头在别的路由上是好使的

**最容易被误判成**：token 过期了 / 签名密钥不对 / fixture 忘了带 header。于是
去查 `SECRET_KEY`、去打印 token、去数 TTL —— 全是白费，token 本身完全有效。

**真因**：本仓有**两族 token**，它们长得一模一样，但只有一族能过数字身份层
（`app/auth/caller.py` 的模块 docstring 把这件事讲得最清楚）：

- **主干签发的 access token**：`sub` 是 int 用户 id。`get_auth_user` 能把它变成
  `AuthUserInfo`，所以 `Depends(require_auth_user)` 的路由认它。
- **cheesex session token**（`session_token()` / `mint_session_token`）：**只有
  handle，没有 int id**（`user_id=None`）。数字身份层看不到 id，直接当访客处理，
  于是 `require_auth_user` 抛 `AuthenticationRequiredError` → 401。

它在 accept / split / connector / project 这些路由上好使，只是因为那些路由走的是
`caller_handle()`——按 **handle** 认人，不查 int id。**同一个 header 在两类路由上
行为不同，这才是它这么难查的原因。**

**正确做法**：先看目标路由的依赖是 `require_auth_user` 还是 handle 解析。要 int
id 的，用 `seed_user()` 或 `authenticated_user` / `auth_headers` fixture 拿真用户
的 access token；只要 handle 的，用 `session_auth_headers()`。

---

## 症状：contract 测试里一片 401，或者一 login 就报 Redis 连不上

**最容易被误判成**：contract 这套 harness 的鉴权坏了，想着"照 integration 的写法
补个登录就行"，于是 POST `/users/auth/login`。

**真因**：contract harness **不跑 Redis**。而登录路由依赖 Redis（限流器 + 2FA
session store），所以那一步必然炸；这也正是 `tests/contract/conftest.py` 的
docstring 里写明"我们不 POST `/users/auth/login`"的原因。

**正确做法**：用 `authed_client` fixture。它给 `python_client` 挂一个
`create_access_token()` 直接签出来的 token，身份是 `python_client` 自己已经
seed 好的平台 agent 用户（id=1，`cheese`）——不走登录，也就不需要 Redis。

---

## 症状：`tests/unit/test_no_adhoc_auth_helpers.py` 红了，列出你的文件

**最容易被误判成**：一条洁癖式的风格检查，改个名字（`_auth` → `_login`）或者把
那行塞进 fixture 里就绕过去了。

**真因**：这条守卫**不认名字，只认 `mint_session_token`**——因为改名正是它要拦的
东西。原来那条规矩是散文写的（「已经有八份复制粘贴的 `_auth()`，别加第九个」），
结果第九份照样出现了，另外还长出三份叫 `_login` 的、两份直接把 mint 调用写进
`client.headers` 的——一共十四个文件。按名字 grep `_auth` 只能抓到其中九个；反过来
按名字数还会数错人：`test_connector_device_flow.py` 的 `_login` 和
`test_connector_viewer.py` 的 `_login_real` 根本不是这东西（它们 seed 真用户拿
数字 id token），`_authenticated_project_owner` 顶着 `_auth` 前缀也是另一回事。
**名字不携带「它是不是在铸 token」这个信息**，所以它现在是一个测试，不是一句话。

**正确做法**：

```python
from tests.integration.conftest import session_auth_headers, session_token

client.get(url, headers=session_auth_headers("alice"))   # 要 header
token = session_token("alice")                           # 要裸 token
```

真有共享 helper 覆盖不了的用法（比如需要带 int `user_id` 的 token），**扩这个
helper**；实在不行才往那个测试的 `ALLOWED` 里加一条，**并写清楚理由**。

---

## 症状：一批你根本没碰过的测试变红，`AttributeError` 说某模块没有那个属性

**最容易被误判成**：环境脏了 / 缓存问题 / 别人的分支带红的。于是清 `.pytest_cache`、
重装依赖 —— 一样红。

**真因**：**test double 没跟着生产代码一起改**。`monkeypatch.setattr` 打一个已经
被改名的函数，抛的就是 `AttributeError`；更阴的一种是假的 `add()` 还在返回
`None`，而真实实现的返回值早已变成下游要用的东西，于是错误在**离改动很远的地方**
才炸出来。有一天三次 CI 红全是这个原因。

**正确做法**：改动任何函数的**签名、返回值、或模块级名字**时，同一次改动里
`grep backend/tests/` 把它的每一个 fake / mock / monkeypatch 都改掉。

---

## 症状：一批看不出关联的测试红了，但单独跑每一个都是绿的

**最容易被误判成**：上一条（stale fake），或者认定是 flaky 测试，加 retry 了事。
两者症状高度重合——区别在**单独跑绿不绿**：stale fake 单独跑照样红，这一条不会。

**真因**：**同时有两个 pytest 进程在同一个测试库上跑**。xdist 的 worker 之间是
安全的（`tests/conftest.py` 给每个 worker 分了**自己的**库），但两次**独立的**
pytest 调用不是——比如你手跑一遍的同时 pre-commit 钩子也在跑，两边都是 serial
模式，落在同一个 `cheesex_test` 上，互相踩数据。踩出过 10 个凭空的失败。

**正确做法**：串行跑，或者给第二个进程换库。

---

## 症状：你改了某条路由的鉴权，改完只有一两个测试文件红

**最容易被误判成**："红的都修完了，齐活"。

**真因**：调这条路由的测试文件往往**不止你打开的那几个**。accept 鉴权那次改动就
漏了一个文件，把 main 弄红了。

**正确做法**：按**路由路径**去 grep（不是按测试名），把命中的每个文件都过一遍。

---

## 纯约定（没有对应症状，故保持规范式写法）

下面这条不是坑，是**选型速查**——它不会以某个报错的形式找上你，你是在**动手写
第一行之前**需要它。硬套「症状 → 误判 → 真因」只会把一张查得动的表变成一段
读不动的散文，所以这里保持原样：

| 目录 | 用什么 | 来源 |
|---|---|---|
| `tests/integration/` | `auth_headers` fixture（真用户 + 真登录）；要裸 token 用 `seed_user` | `integration/conftest.py` |
| `tests/integration/`（只按 handle 认人的路由） | `session_auth_headers()` / `session_token()` | `integration/conftest.py` |
| `tests/contract/` | `authed_client` fixture | `contract/conftest.py` |

三种机制都别手搓——第一条和第三条的坑就写在上面。

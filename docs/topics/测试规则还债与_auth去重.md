## 状态

两件事都做完了，代码在工作区里（未采纳）。质量检查见下方「验证」一节。

## 任务一：`_auth()` 去重 + 守卫

**做法**：在 <&backend/tests/integration/conftest.py> 里加了两个共享 helper——`session_token(handle)` 拿裸 token、`session_auth_headers(handle)` 拿 header；15 个集成测试文件改成调它们。守卫做成一个测试 <&backend/tests/unit/test_no_adhoc_auth_helpers.py>（按简报要求，没碰 <&.claude/scripts/check.sh>）。

**动手前先数了一遍，实际比简报里写的多**。规则说的是「已经有八份 `_auth()`，别加第九个」，而真实情况是 `tests/integration` 下**十四个**文件在自己铸 session token：

| 形态 | 文件数 |
|---|---|
| `def _auth(handle) -> dict` | 9（第九份就是规则明令不许加的那个） |
| 同一份复制粘贴、改名叫 `def _login(...)` | 3 |
| 直接把 `mint_session_token(...)` 内联进 `client.headers` | 2 |

**所以守卫不能按名字做。** 简报里设想的「禁止在 `tests/integration` 下新增本地 `_auth` 定义」这个形态，按上表只能抓到 14 份里的 9 份——躲过散文规则的那 5 份，恰恰是**已经改了名或者根本没起名**的。改名是这条规则要拦的东西本身，拿名字当特征等于拿被规避的那一面当锚点。

守卫因此盯的是**稀缺资源**而不是名字：`app.core.tokens.mint_session_token`。用 `ast` 走一遍 `backend/tests/` 的每个文件，`from ... import mint_session_token`（含 `as` 别名）和 `tokens.mint_session_token` 属性访问都算命中。三个文件在白名单里，每个都写了理由（conftest 自己、`test_tokens.py` 测的就是它、`test_actor_numeric_handle.py` 需要带数字 id 的 token）；另有一个测试专门检查白名单里没有已被删除/改名的死条目——每条烂掉的白名单都是守卫上的一个洞。

## 任务二：`backend-tests.md` 改成症状式

<&.claude/rules/backend-tests.md> 已按 <&.claude/rules/e2e.md> 的范式重写：**症状（你会看到什么）→ 最容易被误判成什么 → 真因 → 正确做法**。六条症状：

1. 401 但同一个 header 在别的路由上好使（两族 token：数字 id vs 只有 handle）
2. contract 测试一片 401 / 一 login 就报 Redis 连不上（contract harness 不跑 Redis）
3. `test_no_adhoc_auth_helpers.py` 红了（不认名字只认 `mint_session_token`，改名绕不过）
4. 一批没碰过的测试 `AttributeError`（test double 没跟生产代码一起改）
5. 一批测试红了但单独跑都绿（两个 pytest 进程打同一个测试库）
6. 改了某条路由鉴权，只有一两个文件红（要按路由路径 grep，不是按测试名）

最后一节「纯约定」按简报要求**保持规范式写法**，并在文里说明了为什么不硬套格式：那是一张开工前查的选型表，不以报错的形式找上门，硬改成症状式只会把一张查得动的表变成一段读不动的散文。

## 这轮最值钱的一条证据

`.claude/rules/backend-tests.md` 里「别加第九个 `_auth()`」这句话，是**以纯文字形式存在期间**被突破的：第九份照样出现了，另外还长出 3 份改名的、2 份内联的。**同一条规则、同一个仓库、同一批 agent**——规则不是没写清楚，是没有执行面。这是「规则要么能被机器检查、要么会衰减」的一手实证，不是推演。

今天合进 main 的 `.claude/scripts/check-repo-rules.sh` 走的是同一条思路（三条 linter 覆盖不到的 CLAUDE.md 规则变成 grep 守卫 + `--self-test`）。两者的分工也清楚了：**能用诚实的 grep 表达的规则进 check-repo-rules.sh；需要理解语法结构的（比如「谁在铸 token」，要看 import 别名和属性访问）做成 pytest 守卫**。那个脚本自己的注释也是这么划的边界——「a guard that misfires is worse than no guard」。

## 验证

- `uv run ruff check tests/`：干净。
- `uv run pyright`：0 errors / 0 warnings（<@wangchangxin> 沙箱实测）。
- 全量 `uv run pytest tests/ -n 4`：**3924 passed / 30 skipped / 0 failed**（138s，<@wangchangxin> 沙箱实测）。
- 本轮另外单跑了守卫测试本身：2 passed。

## 待处理（交接）

本分支基于**今天早些时候的 main**，`check-repo-rules.sh` 那次合并之后 main 又动过。两件事要在采纳前后处理：

1. 需要 rebase 到新 main。
2. `check-repo-rules.sh` 的头部注释把「`_auth()` 现在有九份」当作**现行**证据引用。本分支落地后这句话就不再成立（守卫会保证是零份）。那句话该改成指向 `test_no_adhoc_auth_helpers.py`——**证据从「现在有九份」变成「曾经涨到十四份，现在有测试拦着」**，论点更强而不是更弱。该文件属于另一个 agent 今天的工作范围，等确认后再动。

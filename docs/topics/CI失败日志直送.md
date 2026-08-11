## 目标

CI 红了之后，芝士**不需要人转述、不需要自己再跑一趟 GitHub**，从话题里收到的那条消息就能知道：哪个 job 挂了、错在哪一行、去哪看全文。

权限从来不是瓶颈——`cheese gh-token` 铸的只读 token（`actions:read`/`checks:read`/`metadata:read`）实测就能拉到失败 job 的完整日志。断的是"送到眼前"这一段。

## 改了什么

### 1. 失败详情塞进消息（主菜）

`<&backend/app/domain/review/github_pr.py>`：`check_state` 判定为 `failure` 时，追加一段 `_failure_detail`——每个失败 job 给出 **名字 + conclusion + Actions 页面链接 + 日志里错误行附近的片段**。

设计上几个必须记下来的点（都是实测踩出来的，不是推测）：

- **`output.title`/`output.summary` 是空的。** GitHub 文档说失败摘要在这两个字段，Actions 自己把两个都留成 `null`（2026-08-11 实测），细节走 annotations。所以真正扛事的是日志片段；`output` 只在非 Actions 的 check-run（codecov 之类）上有值，有就带上，没有就跳过。
- **只 grep `##[error]` 会一无所获。** 失败 shell step 吐的是 `##[error]Process completed with exit code 1.`，说明问题的是它**上面那一行**（`FAIL: the secret file was not handed over`）。所以取的是**最后一个 error 标记 + 它前面 15 行**；整份日志没有标记（cancelled/timed_out）就退回取尾巴。
- **job_id 不是 check-run id。** 从 check-run 的 `html_url` 里 `/actions/runs/<run>/job/<job>` 解析。两者今天恰好相等，但只有 URL 形状**说明**这是个 Actions job——把 codecov 的 check-run id 丢给 jobs API 只会 404。测试里专门锁死了"非 Actions 的 check-run 不会被送去 jobs API"。
- **日志 302 到第三方存储**（`*.blob.core.windows.net` 预签名 URL）。重定向是**手动跟**的、且**不带头**——否则 httpx 会把 `Authorization: Bearer <installation token>` 原样重放给存储服务商。预签名 URL 本来就不需要我们的凭证。有测试守着这条。

体积与安全：单次最多展开 3 个失败 job（一个根因红十个 job 是一次调查，不是十次；每多一个就是每轮轮询多一次 GitHub 往返），日志按**滑动窗口只留尾部 256KB**（错误在末尾，`text[:cap]` 会忠实地送来二十分钟前的准备步骤），片段截 1200 字，进话题前统一过一遍 `ghp_/ghs_/github_pat_` 形状的脱敏——Actions 只屏蔽它自己知道的密钥，日志里 curl 我们自己 API 回显出来的那种它管不着。

**任何一步取不到日志都退化成 ""**，headline 照发。轮询拿不到日志，绝不能连"CI 挂了"这件事都丢掉。

### 2. 消息里说清楚"全文去哪看"

`<&backend/app/domain/review/services.py>`：`_nudge_pr_fix` 发出的消息在片段下面附一段可直接复制的命令（`cheese gh-token` + `gh api repos/<owner>/<repo>/actions/jobs/<job_id>/logs`），仓库名由调用处现成的 `owner/repo` 填进去。

卡片 `note` 只取 **headline 第一行**——note 在 UI 上是一行字段，把多行详情灌进 2000 字的列里，等于用可扫读性换一份没人在那儿读的副本。详情是消息该干的事。

### 3. 把这条路径写成芝士读得到的文档

新增 `<&.claude/rules/ci-logs.md>`，`paths` 绑 `backend/app/domain/review/**` 和 `.github/workflows/**`——碰到这些文件时自动加载，不用谁记得去翻。此前 `cheese gh-token` 能看 CI 日志这件事在 `.claude/`、`docs/`、`CLAUDE.md` 里**零处提及**，唯一写明用法的是一句给读代码的人看的代码注释。

### 4. `gh-token` 自己把仓库名说出来

`<&backend/app/api/routes/github_token.py>` 的响应加 `repo` 字段（`"owner/repo"`），取自**解析出 minter 的同一行 installation**，所以 token 和它能操作的仓库不可能对不上。`<&backend/sandbox/cheese>` 把它连同具体命令打在 **stderr**——`GH_TOKEN=$(cheese gh-token)` 这个既有契约必须保住，token 仍然独占 stdout。

以前少了这一步：工作区不是那个仓库的 git checkout，`gh api repos/:owner/:repo/...` 的占位符直接报错，得绕到 `GET /projects/{id}/github/connection` 才知道自己在哪个仓库上干活。

### 简报第 2 条：已被别人做掉，未重复

`pr_open` 进开场状态行这条，main 上 `agent/chat.py:323-331` 已含 `AcceptStatus.pr_open`，且 `_OPEN_CARD_HINTS` 里**也有对应文案**（简报提醒要单独确认这点，已核实，不是半拉子）。本次未动这个文件。

## 约束遵守情况

- **没有新增任何 GitHub 权限**。全程只用现有只读 token，`write_token()` 一寸都没往沙箱方向挪。
- 日志不原样灌入话题：只取错误行附近、有长度上限、进话题前脱敏。
- 绿/pending 的稳态**一次 GitHub 请求都没多**——详情抓取只在 failure 分支触发，有测试锁死。

## 测试

新增 `backend/tests/unit/test_github_pr_failure_detail.py`（8 个）：用 `httpx.MockTransport` 驱动**真实的** `HttpxGitHubPrClient.check_state`，payload 按 2026-08-11 从本仓库拉到的真实 check-runs/日志形状构造。覆盖：错误行提取、302 不重放 token、无 error 标记退回取尾、日志取不到仍发 headline、非 Actions check-run 不进 jobs API、只展开前 3 个失败 job、token 形状脱敏、绿/pending 不碰 logs API。

新增 `backend/tests/integration/test_sandbox_github_token.py`（3 个）：`/sandbox/github-token` 返回 `repo`、未连仓库的项目拿不到 token、无 scoped token 401。

`backend/tests/integration/test_accept_pr.py` 补断言：CI 失败的推进消息里必须同时含 `cheese gh-token` 和 `repos/acme/widgets/actions/jobs/`——两半都要有，缺仓库名的命令是跑不通的。

## 验证状态

- `ruff check .`：全绿。`ruff format --check .`：全绿。
- `pyright app`：0 errors。
- 直接相关的 34 个测试（新增两个文件 + `test_accept_pr.py`）：全过。
- 全量后端套件（`pytest tests/ -n 4`，用户态 Postgres + Redis）：**3777 passed / 23 failed / 31 skipped**。23 个失败全部与本次改动无关，逐一核过：
  - 22 个在 `test_machine_service.py` / `test_tmux_control.py`——沙箱没有 procps（`ps`/`kill` 二进制不存在），`CLAUDE.md` 已把它记为环境缺口。
  - 1 个 `test_market_api.py::test_market_lists_ai_and_compute_pools`——默认 AI 池的 `available` 由**凭证是否存在**决定（`agent/market.py:69` 的 `selectable`），沙箱里没有 AI 凭证所以是 `False`。同属环境缺口，本次改动一行都没碰 `agent/market.py`、`profiles.py` 或凭证配置。
  - `CLAUDE.md` 里记的另一类失败（43 个 git identity 相关）这次**没有出现**，环境比那条笔记写的时候好了。

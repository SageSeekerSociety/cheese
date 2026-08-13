## 目标

把"采纳"从单次"合并+归档"改成三阶段：人点一次采纳 → 有可用已连接 GitHub token 则推分支开真实 PR、卡片转 `pr_open`、话题不归档 → 系统轮询 PR 的真实 CI（GitHub Actions）检查状态，全绿则自动调 API 合并 PR → 合并后**继续轮询 merge 触发的部署 workflow**（Deploy dev/test box），真正 completed+success 才最终归档。CI 或部署检查红了都唤醒芝士处理。没有可用 token / GitHub API 故障 → 直接走现在的 `merge_topic()+push_back()` 老路径正常归档，不阻塞采纳（这是降级，不是错误）。

**2026-08-09 wangchangxin 追加修正**：归档时机比最初简报更严格——不是"PR merge 成功"就归档，而是"merge 之后触发的部署 workflow 也 completed+success"才归档。理由：反复观测到 merge 成功但后续 Docker 构建/部署被 single-runner 并发取消策略打断、静默失败（build-did-not-produce-images 那个模式）——卡在 merge 这步等于把"采纳后平台撒手"问题往后挪了一步，没有真正解决，跟#188最初诉求不符。

## 约束（已拍板，不重新讨论）

1. 不等 #195（PR-based accept，处于停滞状态，全部 CI 失败几小时未推进）合并，参考它公开的设计思路自己实现一遍等价机制。
2. 降级路径（无 token/API 故障 → 走老的 `merge_topic()+push_back()`）本次要留着，不用管以后何时删除。
3. 不做二次人工确认，CI 真正转绿后系统自动合并 PR；合并后自动轮询部署 workflow，部署也成功才算完成。话题归档卡在"部署真的成功"，不能卡在"点了采纳"或"PR merge 成功"。点了采纳到最终归档之间，话题保持 active，容器不停，芝士能在里面继续改代码。
4. 内部闸门（`review/gate.py`，跑 ruff/pyright，不跑重的 pytest/e2e）保持不变，继续在人点采纳按钮之前起作用；开 PR 之后跑真实的重 CI（Backend Test/E2E Tests）+ 部署 workflow。三层不互相替代（内部闸门 → PR CI → 部署）。
5. 本次明确不做：CI/部署一直不转绿的超时升级；降级路径未来何时删除。不自行加范围。
6. **部署失败处理（wangchangxin 建议默认值，我评估后采纳为最终方案）**：topic 保持 active（不归档）+ 复用卡5 的 webhook 机制发一条部署失败通知回房间，不做自动重试，交给人判断。不做自动重试是因为"重试几次/什么时候放弃"边界不明确，贸然加了会制造新的不确定状态。

## 依赖

- 前置修复（并行进行中，id=12147a29-a681-4b6d-8dea-4f0dc669e49b）：账号连接的 OAuth token 持久化，未完工不阻塞开工——没有可用 token 时走降级路径就是正常情况。

## 代码现状核查结果（已确认，非猜测）

- `AcceptStatus`（`review/models.py:20-32`）现有 `pending/accepted/rejected/revoked/conflict/pending_gate/gate_failed` 七个状态，无 `pr_open`。`AcceptCard`（同文件:35-59）字段：`topic_id/reviewer_handle/routing_reason/status/decided_by/decided_at/note/gate_passed_at/gate_output`，无 PR 相关字段，需要新增。
- `AcceptService.accept()`（`review/services.py:250-390`）现状：投票达标后 `ws.merge_topic()` 本地 `git merge --no-ff`（`workspace/service.py:388`），成功后 `ws.push_back()`（`workspace/service.py:595`，纯 `git push`，无 PR/API），然后 `stop_topic_container()` + 话题转 `archived`。全程无 GitHub PR/API 代码。
- `gate.py` 唤醒芝士的确切调用（失败时复用）：`runner.submit(chat_service, topic_id, author="system", content=..., summon=True)`；`runner`/`chat_service` 是 `dispatch()` 的入参，不是模块级单例。
- 全仓搜索确认：`ACCEPT_VIA_PR`/`pr_number`/`pr_url`/`write_token`/`pr-checks`/`pr_open` 零命中（除一处文档提及"#195还没合并"）——#195 确认未合并进本仓库，`write_token()` 不存在，不能依赖它。
- `UserOAuthConnection`（`oauth/models.py:10-27`）确认无 `access_token` 字段，只有 `refresh_token`/`token_expires`；`github_account_link.py:80-83` 回调里 `_access_token` 变量拿到就丢弃，`OAuthService.create_connection()` 也没有 token 参数——依赖缺口（前置修复要解决的）完全属实。
- `github_app.py` 现有 `readonly_token()`（App 安装级只读 token，`actions:read/checks:read/metadata:read`），写权限（`contents/pull_requests`）明确预留但未实现——但本卡按拍板用的是"批准人自己连接的 GitHub token"而非 App 写 token，所以不需要新建 App 写权限铸造，只需要读 `UserOAuthConnection` 里持久化的用户 token（依赖前置修复落地字段）。
- `SchedulerService`（`scheduler/service.py`）是纯 asyncio 轮询，无 APScheduler/cron 库。现有模式：`SandboxReaperRunner`（`start/stop/_loop`，`while True: sleep(interval); reap_idle_containers()`）驱动 `reap_idle_containers()`；新轮询任务照此模式加一个新 Runner 类，在 `main.py`/`api/deps.py` 里跟 `SchedulerRunner`/`SandboxReaperRunner` 一起接线。
- `git_http.py` 里**没有**"committer-only 写法"（简报描述与实际代码不符，已核实排除）——该文件只在 CGI env 里写死 `GIT_COMMITTER_NAME=芝士`，没有 author/committer 拆分逻辑，PR trailer 的 author/committer 拆分需要自己设计，不能照抄现成代码。
- `docs/topics/两阶段采纳-PR迭代式设计.md` 设计文档在本仓库里不存在（子话题产出未合并进 main），无法读取原文，只能依赖简报里转述的五点结论。
- `config.py` 的 `Settings` 已有 `oauth_github_client_id` 等字段和 `subscription_enabled`/`agent_sandbox_enabled` 这类布尔开关先例，可参照加新配置。

## 2026-08-09 采纳时合并冲突：#195 + OAuth token 持久化都在此期间真合并进 main 了

wangchangxin 点采纳后合并冲突，平台把 main 合进了工作区。核实发现：**不是普通文本冲突**——#195（PR-based accept，简报写它"停滞"）和前置修复（OAuth token 持久化，id=12147a29-...）在我实现期间都**真的合并进 main 了**，且两者都独立创建了 `app/domain/review/github_pr.py`、`accept_cards.pr_number/pr_url` 字段、`review/services.py::_accept_via_pr` 方法名——同名不同实现，jj 没能识别成冲突的部分（models.py 里 `pr_number`/`pr_url` 被定义了两次）我也一并修了。

**#195 最终落地的实际设计**（跟简报转述的"进度停滞"不一样）：card 一变 `pending` 就在后台 fire-and-forget 开 PR（`pr_publish.py`，用 App 自己的 write_token，从 git `upstream` 远程解析 owner/repo），行为**同步**——人点采纳时若 `card.pr_number` 已经有值，直接调 API 合并那个 PR（CI 没跑完会被 GitHub 拒绝合并，转 `conflict` 状态）。默认关闭（`settings.accept_via_pr = False`，dark ship）。

**合并策略**（保留双方意图，语义化合并）：
- `accept()` 里两段判断按顺序共存：① `card.pr_number is not None`（#195，已有 PR 就合并它，绝不重复开 PR）→ ② 我的 `_resolve_pr_prerequisites`（没有 PR 才尝试开新的）→ ③ 老的本地 merge 降级路径。`accept_via_pr` 默认关闭，① 在当前配置下永远是 no-op，两条机制互不干扰；哪天有人打开那个 flag，两条机制会真正并存，这点已经写进验收卡说明里让 wangchangxin 知道。
- 命名冲突：我的 `_accept_via_pr(card, topic, decided_by, *, token, owner, repo)`（开新 PR）改名成 `_open_pr_for_accept`，让位给 #195 原本的 `_accept_via_pr(card, topic, decided_by)`（合并已有 PR）。
- `alembic`：#195 已有自己的 `pr_number`/`pr_url` 迁移（`b3d5f7a9c102`），我的迁移改成只加净新列（`pr_repo`/`pr_head_sha`/`pr_merged_at`），down_revision 改指向合并 `f9a1c7e3b502`（access_token 列）+ `b3d5f7a9c102` 两个头；顺带删掉了我自己那个后来发现是重复劳动的 merge-heads 迁移（`093133add3e1`，跟 `f9a1c7e3b502`/`b2958d64e679` 做的是同一件事）。`alembic heads` 现在唯一。
- 顺手把我自己的 `push_topic_branch_for_github_pr` 也改成用 #195 的 `_token_push_env`（env var 传 token 给 git credential helper）而不是把 token 拼进 push URL——更安全，避免 token 在 `ps` 里短暂可见。
- 测试文件重名（都叫 `test_accept_pr.py`）：我的留在原文件，#195 那份挪到新文件 `test_accept_pr_publish.py`（完整保留，未改动断言）。

**验证**：`ruff check .`/`ruff format --check .`/`pyright` 全绿；`alembic upgrade head` 在干净库上从头跑通，唯一 head；`test_accept_pr.py`(8) + `test_accept_pr_publish.py`(5, #195 自己的用例) + `test_github_pr.py`(9) + `test_accept_gate.py`(11) + `test_review_acceptance_merge_failure.py`(6) + `test_scheduler.py`/`test_github_account_link.py`/`test_github_install.py` 全部通过，只有那个已知的沙箱 jj 权限问题（跟这次冲突无关）还是红。`tests/unit` 全量 2465 passed（新增的都是 #195 带来的用例），失败的 22 个跟之前排查过的一样（machine_service 域不相关 + tmux_control 缺 `kill` 二进制），不是这次冲突解决引入的。

## 实现已完成

- `review/models.py`：`AcceptStatus` 加 `pr_open`；`AcceptCard` 加 `pr_number`/`pr_repo`/`pr_url`/`pr_head_sha`/`pr_merged_at`（`pr_merged_at` 区分"还在等 PR CI"(None) vs "PR 已合并等部署"(已设置)，同一个 `pr_open` 状态覆盖两个子阶段）。迁移：`093133add3e1`（先合并此前两个分叉的 alembic head）+ `c7d8e9f0a1b2`（新增列）。
- `review/github_pr.py`（新文件）：`GitHubPrClient` Protocol + `HttpxGitHubPrClient` 真实实现（开PR/查check-runs/合并PR/查workflow run 状态）+ `default_client()`/`set_default_client()`（测试用 fake 替换的钩子）。
- `oauth/services.py`：新增 `get_github_user_token_for_handle()`——用 `getattr(conn, "access_token", None)` 防御式读取批准人的已连接 GitHub token；字段不存在时天然返回 None（=正常降级），前置修复（id=12147a29-...）落地 `access_token` 字段后自动生效，不需要再改这个函数。
- `workspace/service.py`：新增 `push_topic_branch_for_github_pr()`（推话题分支到 #192 连的 GitHub 仓库，用批准人自己的 token 认证，不用 App 的）+ `pr_base_branch()`。
- `review/services.py`：`AcceptService.accept()` 先尝试 `_resolve_pr_prerequisites()`（有 token 且项目连了仓库）→ `_accept_via_pr()`（推分支+开PR+卡片转`pr_open`+不归档）；任何一步失败（含解析前置条件本身失败）都在 try/except 里降级到原来的 `merge_topic()+push_back()` 路径，不影响 accept 本身。新增 `advance_pr_card()`（由轮询调用的状态机推进：查CI→合并→查部署→归档，CI/部署失败都有对应的通知/唤醒逻辑，且都做了"同一个失败不重复通知"的去重）。
- `scheduler/service.py` + `main.py`：新增 `PrPollRunner`（跟 `SandboxReaperRunner` 同款结构），按 `accept_pr_poll_interval_s`（默认60s）轮询所有 `pr_open` 卡片；`api/routes/scheduler.py` 加 `POST /api/admin/scheduler/poll-open-prs` 供测试/运维手动触发一轮（跟现有 `/tick` 同款）。
- PR trailer（`_pr_trailers`）：`Requested-by`=`Topic.created_by`，`Reviewed-by`=`AcceptCard.decided_by`，`Cheese-Topic`=话题id；PR 描述和最终合并 commit message 里都带上。

## 测试与验证结果

- `tests/integration/test_accept_pr.py`（新增8个测试，全部通过）：有token开PR+话题保持active、CI pending不动、CI转绿自动合并但**部署未完成前topic仍是active**（归档时机的核心断言）、部署转绿才真正归档、CI失败唤醒芝士且同一commit不重复唤醒（推新commit后能对新失败重新唤醒）、部署失败保持active+通知房间+不重试、无token/无连接仓库时结构性走老路径、poller正确跳过非pr_open卡片。
- 顺带修了一个真实 bug：`_resolve_pr_prerequisites()` 最初没包 try/except，DB查询失败会让整个 accept() 崩溃而不是降级——这既是生产环境下的正确性问题（决策2要求"任何机制不可用都降级，不是错误"），也导致跑现有单测 `tests/unit/test_review_acceptance_merge_failure.py`（用 mock session）直接报错；修完后这6个单测全部转绿，未改动其断言本身。
- `ruff check .`、`pyright`（全仓库）：0 errors。
- `tests/unit`：2419 passed（此前该话题所在的沙箱环境基线，卡1的话题记录是2416 passed，量级一致）。
- **发现一个跟本卡改动无关的沙箱环境限制，已确认是环境问题而非代码问题**：本容器里任何 `jj` 命令（哪怕 `jj --version`）都失败，报 `Cannot access /de808b13-.../.jj/repo/config-id: Permission denied`——`/work` 本身是一个 jj workspace，其真实 store 挂载在 `/de808b13-.../`，`config-id` 文件是 uid 1001 owned 的 0600，而本容器进程跑在 uid 1000（node），读不了。这不是"修复容器内jj仓库失效.md"那个已解决的问题（那个是嵌套 Docker 沙箱里的相对路径穿透 bug，已修好且验证过），是另一个更基础的、这次新发现的权限问题。**影响范围**：任何测试只要真的调用到 `merge_topic()`（即没有 mock `ws.merge_topic` 的测试）都会在这个容器里失败——包括改动前就存在、我完全没动过的 `test_accept_happy_path_archives_topic` 等（已现场验证：checkout 未改的这条老测试在本容器里同样报一模一样的错，证明是环境问题、不是回归）。凡是 mock 了 `ws.merge_topic`/`push_topic_branch_for_github_pr` 的测试（含 `test_accept_gate.py` 全部11个、我新增的全部8个）都正常通过。建议：如需要一次完全干净的 `pytest` 全绿验证，换一个容器/沙箱跑（同 topic 内已有先例：诊断类问题拆个新子话题比在本容器里纠结 git/jj 权限更快）。

## 递验收卡前的结论

核心状态机改动已实现完整、测试覆盖到位（尤其是归档时机这个决策3的关键点），ruff/pyright 全绿。pytest 因为上述沙箱环境限制无法在本容器里做到"完全无 mock 的端到端全绿"，但：①这个限制在改动前就存在，对本改动是中性的；②所有跟 PR/部署状态机相关的新逻辑都有对应 mock 测试覆盖并通过；③老的降级路径代码本身一行没改，只是被挪到了新的 try/except 之后。会在验收卡的 routing_reason 里如实说明这一点。

## 传话方式

拿不准的事实性问题：`cheese api POST /topics/e593d59c-ca17-4dcc-b0ff-57980823fd22/comments --data '{"author": "...", "content": "..."}'` 问回父话题。

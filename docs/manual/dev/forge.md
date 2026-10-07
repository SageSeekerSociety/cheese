---
title: 代码托管
kind: 参考
summary: Forgejo 与 GitHub 两个实现、能力位、令牌、事件中继，以及没有 webhook 时的轮询。
covers:
  - backend/app/domain/review/forge.py
  - backend/app/domain/project/forge.py
  - backend/app/domain/repository/
  - backend/app/domain/agent/github_app.py
  - backend/app/core/forge_quota.py
  - backend/app/core/forge_etags.py
  - backend/app/domain/review/events.py
  - backend/app/domain/agent/forge_cli.py
  - backend/app/forge_events_app.py
  - backend/app/domain/review/pr_poll.py
  - backend/app/domain/review/pr_publish.py
  - deploy/forge-events/
---

# 代码托管 {#forge}

每个项目选一个代码托管：新项目默认用部署自带的 Forgejo，也可以绑定 GitHub；绑了 GitHub 的项目以 GitHub 为准。

> 讲：两个实现怎么被选中、能力位说了什么、令牌从哪来、webhook 怎么进到部署里、没有 webhook 时靠什么补。不讲：采纳本身怎么走（见[验收与采纳](/dev/accept)），交付链路每一步的顺序（见[任务 → 分支 → PR → 验收合并](/dev/delivery)），房间里工作区的检出目录（见[本地文件](/dev/local-fs)）。

## 两个实现，一份形状 {#kinds}

`review/forge.py` 里只有两个 `ForgeKind`：`github_app` 和 `forgejo`。`ForgejoForge` 直接继承 `GitHubForge`——两者的 API 形状一样，差别在**谁去调**和**署谁的名**，而不在流程。`resolve()` 拿项目的绑定（`project/forge.py` 的 `binding_for_project`）挑实现，读不到绑定就抛错，**绝不退回本地**：读不到的时候就选一条路，等于盲选。

新项目不需要选：`provision_repository` 在部署自带的 Forgejo 上建一个项目账号（`cheese-<project hex>`，密码由 `jwt_secret` + project id 派生，所以一次被回滚的创建重试能拿回同一个账号），账号下建一个**私有**仓库 `project`。已经绑了 GitHub 的项目不走这条路（`settings["forge_kind"] == "github_app"` 时直接要求先连 GitHub）。

`resolve()` 还把「项目在哪」这一件事封成一个对象：`proposal_url` 的 host 与绑定仓库的 host 不一致就拒（「评审所属的托管服务与项目仓库不一致」）。

## 能力位 {#capabilities}

产品判断只许读 `ForgeCapabilities`，**不许读「项目有没有绑外部仓库」那个布尔**（不变量 I21②）——那个事实进得来，但要以能力位的形式进来，由实现去读，不由产品判断分叉。

| 位 | 意思 |
|---|---|
| `reports_checks` | 这个托管方跑不跑检查、平台能不能读到结论 |
| `hosts_proposals` | 改动在外部有没有一个可以被人打开的提案页 |
| `can_write_remote` | 平台有没有写那个远端的凭据 |
| `has_external_remote` | 项目有没有一个外部 git 远端 |
| `pushes_to_external_remote` | 有远端 ∧ 写得动 |
| `identity` | 提案与合并署谁的名（`user` / `platform`） |

`has_external_remote` 和 `can_write_remote` 分开，因为「没填地址」和「填了地址我们推不动」是两句不同的话，而 `pushes_to_external_remote` 在这两种情况下都是否——压成一位就说不出后一句。今天两个实现都回同一组值（前四位都是真），`identity` 按 kind 分：GitHub 是 `user`（以人的身份出现），Forgejo 是 `platform`。

## 令牌 {#tokens}

**GitHub 侧只有一把长期凭据：App 私钥**，它留在主 API 上，**绝不交给沙箱**（`domain/agent/github_app.py`）。机器要写仓库时，带着自己的 scoped cheese token 调 `/sandbox/forge-token`，后端先确认这枚 token 所属的 agent 现在还在它的房间或项目里（`api/auth.py` 的 `require_seated_agent`；被移出的 agent 拿着没过期的 token 也只得到 403，经平台中转的 git 流量和隧道同样如此），再用 App 的授权在**那个绑定仓库**上签一枚安装令牌：

- 一小时有效，缓存到剩不到 20 分钟才重签（一次密集调用只花一次上游签发）。
- App JWT 的 TTL 是 540 秒（GitHub 的上限是 10 分钟，留出时钟偏差）。
- 权限集是 `contents: write` + `metadata: read` + `pull_requests: write` + **`workflows: write`**。最后一项是承重的：没有它，GitHub 会拒绝任何碰 `.github/workflows/*` 的分支的**推送**（"refusing to allow a GitHub App to create or update workflow … without `workflows` permission"），在采纳时表现为一个说不清的 422（2026-08-16，话题 ee17b136）。App 一直有这个授权，只是签发请求没要——GitHub 会收窄到你开口要的那些。
- 从哪个安装签发是按项目解析的：App id 和私钥是平台级一份，每个连上的仓库有自己的 `installation_id`（`project_git_installations`）。

**Forgejo 侧**是项目账号本身：密码存在 `ProjectForge.account_password` 里，读的时候过 `forge_password` / 写的时候过 `seal_forge_password`。另外 `ensure_author_email` 会把 agent 的署名邮箱登记到那个账号上（并发登记同一个邮箱会拿 422，所以那里先读一遍再补——登记完还要求它是 verified）。

改动留不下的会话（支线、还没开始的任务，`Place.keeps_work`）从同一个接口拿到的是**只读令牌**，响应里 `read_only` 为真：

- GitHub 侧是只要读权限的安装令牌（`GitHubAppTokens.read_token`）：安装的每项授权都按读级别要，没有读级别的 `workflows` 不要，否则整次签发会被拒。
- Forgejo 侧给内置 OAuth 客户端的令牌加不了范围，所以是项目账号建的带 `read:repository`、`read:issue`、`read:user` 范围的访问令牌（`ForgejoTokens.read_token`）。它在 Forgejo 上永不过期，平台在名字里写上过期时刻，签发新的和定时清理时把过期的删掉（`revoke_expired_read_tokens`），缓存行带 `read_only`。

机器上那两条命令（`gh` / `fj`）由 `domain/agent/forge_cli.py` 包一层：凭据是为**这一次调用**现签的，只对绑定的 host 和 path 有效；`fj` 还会因为 0.6 版把绝对 API 路径拼在一起而起一个本地转发。

## 事件中继 {#events}

webhook 打不到部署上（部署常常在客户网络里），所以有一个公网入口替它接：`backend/app/forge_events_app.py`，跑在 `deploy/forge-events/compose.yml` 起的那一个容器里，每个部署一条**出站** WebSocket 连上来。

- 中继只把「**哪个仓库变了**」推给订阅它的部署（`{"kind","repo"}`，多一个 `project_id` 而已）——不转发 webhook 内容，不转发令牌。
- 请求体上限 5 MiB；签名校验按来源分：Forgejo 看 `x-forgejo-signature`，GitHub 看 `x-hub-signature-256`（前缀必须是 `sha256=`），比对用 `hmac.compare_digest`。
- GitHub 那条（`/forge/events/github-app`）投给谁由两件事决定：安装 id → 部署的静态授权表（`forge_event_github_installations`），以及各部署**动态订阅**的 `(installation_id, repo)`。动态订阅靠 App 签名的一份 assertion（每分钟重报一次，见 `review/events.py` 的 `register_subscriptions`）。
- Forgejo 那条按部署分别校验（`/forge/events/{deployment}/{project_id}`，密钥是部署密钥与 project id 派生的 `project_secret`）。
- 一个部署断线或忙，中继回 503（**不静默丢弃**）：出问题比丢事件好。部署侧 `events.listen` 收到断线就 10 秒后重连，**重连后立刻**排一次全量的 `open_draft_prs` + `poll_open_prs`，把断开期间丢的事件补回来。
- 部署侧收到的事件不在读 socket 的循环里处理，而是交给旁边一个刷新队列（`events._Refreshes`）：同一个仓库最多排一次，正在刷新时又来的事件只让它刷完后再刷一次。一个忙的仓库一分钟能来十几个事件（推送、检查、评审），每个都刷一遍就是背靠背地扫，安装的额度很快见底；而读循环卡在刷新上，中继的心跳就会超时断线，每次重连又是一次全量。
- Forgejo 的 webhook 由平台自己维护（`ensure_repository_webhook`）：要 `push`、`pull_request`、`action_run_failure`、`action_run_recover`、`action_run_success` 五个事件，重建时**先建新的再删旧的**（Forgejo 15 的 PATCH 会忽略 Actions 事件字段），留着旧的会让投递断掉。这条维护还会定期对账（`reconcile_repository_webhooks`）。

## 没有 webhook 时靠轮询 {#poll}

「托管方没有 webhook 时，PR / 上游轮询是这个实现内部的物理事实」——不是调度，是 review 领域自己补的一条读路径。产出落各自的卡和房间，和 webhook 送来的事件走同一条路（`forge_repository_changed`）。

- 一跳一条遍历在飞的卡（每张卡一个事务，一张卡的失败不回滚别人）。
- 网络抖动**不算新闻**：同一张卡连续丢掉 `TRANSIENT_MISSES_BEFORE_ERROR`（3）跳才算错误——下一跳在一分钟后，通常自己就好了。轮询器本身的 bug 不属于这一档，第一次就报。
- GitHub App 一个安装一小时只有一份 REST 额度，轮询、draft 扫和人递卡、合并都从里面扣。所以轮询和 draft 扫在动手前先问一句（`background_may_use_forge`）：剩下的不到 `KEPT_FOR_PEOPLE`（20%）就这一跳不碰这个项目，留给人的请求，下一跳再看。
  - 先看 GitHub 已经说过的话。每个经 `forge_client` 发出、带着平台签的安装令牌的请求，回来时都会把响应头里的额度（`x-ratelimit-*`）记到 `core/forge_quota.py`；被拒成「额度用完」（403/429 带额度头）就记下 GitHub 给的恢复时间。恢复之前，或者上一次报的余量已经不到 20% 而这一小时还没重置，后台一律不发请求，连 `GET /rate_limit` 都不问。
  - 没有这样的记录才问 `GET /rate_limit`（这一问不扣额度）。这一问本身被拒成额度用完，也算不能用；问不到别的原因（非 200 时会记一条日志），照常跑，跑出来的拒绝会记下来挡住下一个。
  - 读不变的东西不扣额度。`forge_client` 发出的普通 GET（不是流式下载）只要带着平台签的安装令牌，就记下 GitHub 回的 ETag 和回答（`core/forge_etags.py`，按安装 + URL + `Accept` 记，最多 1024 条，单条超过 256 KiB 不记）。下次同样的读带上 `If-None-Match`，GitHub 回 304 就不扣额度，调用方拿到的是记下的那份完整回答。键里不放令牌：令牌每小时重签，而 304 本身就是 GitHub 确认那份回答没变。Forgejo 不走这条。
  - draft 扫在一个项目上碰到额度用完（`ForgeRateLimitedError`），这一趟就不再碰这个项目剩下的任务，只记一行日志。
- 归档话题上的卡**不在名单里**（`open_pr_card_ids` 就把它们排除了）：继续跟等于拿批准人的 GitHub 凭据去动一件没人再跟的工作。
- 还有一条单独的扫：还没递卡的任务，如果分支已经有提交，就替它开一个 **draft** PR（`sweep_draft_prs` → `_draft_pr_for_one_task`）。每跳最多看 `UNCARDED_TASKS_PER_TICK`（10）个的是另一条：已经有 PR、但没递卡的任务，看它是不是在 GitHub 上被合了（`poll_uncarded_task_prs`）。
- 一条卡在某个时刻是「已经有人递了卡」的——这个问题的答案比行锁稳定：合并是**先合再写库**的，中间那一瞬 `status` 还是 `open`，而分支已经进了主干；照 `status` 判会开出一个永远合不上的 PR。递了卡的活从那一刻起 PR 就归它（`open_pr_for_card`），所以那条判据（有没有卡）永远不会漂。

## 平台不为读写源码维护本地仓库 {#no-local-repo}

读文件、看 diff、合并、代推都走托管平台的 API：读提交是 `repository/forge_files.py`（内容经 `project.forge.repository_data`），合并是 `AcceptService._merge_pr_for_accept` 调托管平台的合并接口，推送是**干活的那台机器**自己 `git push`（凭据现取现用）。

平台侧那份检出（`repository/service.py` 管的房间工作区）只在**检出**这一级做事：搭目录、挂 CLI、清退役检出。它不写任何人的提交——提交是在执行机上做的，推分支也是那台机器推的。

## 边界与坑 {#traps}

- 仓库创建、账号、webhook 都是**幂等重试**的：一次被回滚的创建必须能拿回同一个账号（密码是派生的），否则第二次会在 Forgejo 上撞上一个不属于任何人的同名账号。
- 中继的 `Connection` 每部署只有一条，滚动发布时新后端会一直重试到旧后端退出（后连上来的收到 1013）。
- GitHub 的分支保护查询是**只展示、永不失败**（`github_repo_snapshot`）：403 / 404 / 网络问题 / 凭据不对，回来都是一个有效答案，绝不让设置页因为 GitHub 的心情 500。免费计划私有仓库对每个保护端点都回 403，所以 `unknown` 是日常答案，不是错误。
- 一台机器能不能直连托管平台是**当场探出来**的（探 `/api/v1/version` 或 `/` 的 5 秒超时），探不通才把 git 的 URL 改写走平台中转（`_configure_git_transport` / `forge_cli.py` 的 `/sandbox/forge/{project_id}`），并打一句「仓库无法直连，本次通过平台访问」。已结束的任务只往平台备份、不推送，所以不探。

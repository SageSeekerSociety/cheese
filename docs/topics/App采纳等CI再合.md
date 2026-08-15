# App 采纳等 CI 再合

接了平台 GitHub App 的项目（`ForgeKind.github_app`），采纳不再是「合并」，而是
「授权」：卡进 `pr_open`，已有的轮询器等 CI 全绿、过完三道安全阀，再自动合并。

## 之前是什么样

`AcceptService._accept_via_pr` 在合并前读一次 check-runs，把状态写进卡片 note，
**然后直接调合并 API**。那个方法的 docstring 原话就写着 check-runs 的状态
"never used to block"。卡片上那句「合并时 CI 检查未全绿」是如实留痕，不是拦截。

实测（PR #414，UTC）：

| 时刻 | 事情 |
|---|---|
| 14:48:54 | PR 开出 |
| 14:49:19 | cheesex-app[bot] 合并进 main（开 PR 之后 **25 秒**） |
| 14:49:44 → 15:05:22 | 9 个检查陆续跑完，最后一个比合并晚 **16 分钟** |

那次碰巧全绿，但**绿是运气，门禁根本没等**。

两种情况下这甚至算不上「人的自由裁量」：递卡时 fire-and-forget 开 PR 没成功、
PR 是在采纳那一刻才现场补开的（人点采纳时界面上根本没有检查可看，PR #414 就是
这个形状）；以及人点得比 CI 快（本仓库 CI 约 16 分钟，卡片一转 pending 就能点）。

## 现在是什么样

`github_user` 那条路早就在走两阶段（`pr_open` + `SchedulerService.poll_open_prs`
→ `advance_pr_card`）。这次把 App 这条路也接上去，而不是新造一道闸门——
`review/gate.py` 第一行至今写着 NO LONGER DISPATCHED，那道前置闸门是被 andy 退役
掉的，原则是 **mirror, don't gate**：平台不自己重算可合并性，只读 forge 的判断。
等 CI 全绿再合，读的正是 forge 自己的检查结论；「读一次、留痕、照合」才是把判断
丢掉了。

```
人点采纳 = 授权
  → 推工作区最后的改动到 PR 分支，冻结 pr_authorized_sha
  → 卡进 pr_open，话题保持 active（容器不停）
  → 轮询每 60 秒：重推芝士的修复 → 读 check_state
        pending  → 卡面写「在等什么、等了多久」
        failure  → 叫芝士回来修，不合并
        success  → 过三道安全阀 → 合并 → 归档
```

三道安全阀是现成的（`_authorization_exception`）：①授权之后的新 diff 越界
②`no_checks`（根本没有 workflow 会对这次改动跑）③目标分支是 prod。任一命中就不
合并、回来找人；判断不了也算「找人」（fail closed）。

## 四个必须知道的接线点

1. **两条路的远端分支名不一样。** App 的 PR 开在 `ws.branch_for_topic` →
   `topic/<hex8>`；轮询器原来按话题 id 推算，推的是 `github_pr.pr_branch_name`
   → `cheesex/<hex8>`。分支名现在从 PR 自己身上读（`PullRequestStatus.head_ref`），
   不再推算——推算的后果是提交落了地、PR 一动不动。
2. **轮询器要四个字段齐。** `advance_pr_card` 缺 `pr_repo` / `pr_number` /
   `pr_head_sha` 任何一个，就只 log 一行 error 然后 return——卡永远停在
   `pr_open`，不报错、不提醒、界面看不出来。App 那条路此前只写 `pr_number` +
   `pr_url`，所以 `_authorize_pr_for_accept` 一次补齐四个（含 `pr_authorized_sha`）。
3. **轮询 App 卡用 App 的 write token，不是验收人的。** 验收人未必连过 GitHub，
   也未必有 main 的写权限；把一张已经授权过的卡的推进权绑在别人的账号状态上，
   等于让它随时可能停住而没人知道为什么。归属靠合并提交的 `Reviewed-by` trailer
   留，不靠借谁的钥匙。选凭据的地方是 `AcceptService._pr_poll_token`。
4. **卡片 `note` 是状态机的一部分，不是自由文本。** 各写入方靠前缀互相识别：
   `⚠️`（CI 未通过 / 重推失败 / 轮询暂停）、`🌿`（分支分叉）、`🚫`（GitHub 拒绝
   合并）、`✋`（安全阀扣住）、`🚪`（PR 被关）、`❌`（部署失败）。新加的
   `⏳ 等 CI` 是这个家族里**优先级最低**的一条：只写进空 note 或盖自己，绝不盖掉
   上面任何一条。

## 两条硬约束怎么落的

### 等待期间卡面自己说话

`pr_open` 期间「什么都没发生」和「还在等」在卡面上长得一模一样，而采纳从此不再
是秒回——一张 16 分钟不动的卡读起来像死了。所以 `state == "pending"` 时卡上写
「在等哪几项 + 已经等了多久」。「多久」按 5 分钟分档（`_WAIT_BUCKET_MINUTES`），
文本没变就不写，所以一次等待最多几次更新，而不是每 60 秒一次。

`no_checks`（根本没有 CI 会跑这次改动）那道阀原样保留，只是卡面上补了出口指引：
人工放行、自己去 GitHub 合、或者作废这张卡。

### 「明知红也要合」必须有人签字

红着合有时候是对的：CI 基础设施抽风、与本次改动无关的既有失败。真正不能接受的
不是红着合，而是**没有人做过这个决定**——那正是旧行为（默认放行、事后留痕）的
毛病。

出口是 `POST /accept-cards/{id}/merge-anyway`，**默认拒绝、显式放行**：

- 必须是登录的人（路由的 `ActorResolverDep` + `AuthenticationRequiredError`）；
- 芝士被 `AcceptService._forbid_ai` 挡住，跟 accept / approve / void 同一条线；
- 只有这张卡的验收人、当初的授权人、项目 owner / lead 能点；
- 卡面上留下**谁、什么时候、合并那一刻检查到底是什么状态、理由**。

路由**故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`——照 `void` 的先例：那张
表是「要被 per-turn token 把关的路径」白名单，不是拦截器，没列进去的写路由压根
不过那个中间件（症状是静默放行，不是 401）。真正拦住芝士的是 `_forbid_ai` 加
路由上的登录校验。

## 行为变化（用的人要知道）

- **采纳不再是秒回。** 话题会保持 active 等 CI（本仓库约 16 分钟），容器不停。
- **等 CI 期间不要写工作区。** 轮询每 60 秒 snapshot 工作区并重推，写一次就把
  PR 打回起点、CI 重排。合并之后（`pr_merged_at` 非空）不再重推，写才安全。

## 没做、故意留着的

- 没有删 `_accept_via_pr`。它在 `github_user` 那条路上（卡上已有 PR 时）仍然可
  达，是否该删是另一张独立的卡。
- 没有动 GitHub 仓库设置 / 分支保护 / `.github/workflows/` / `deploy/`。分支保护
  是另一条候选方案，不在这次范围里。
- 平台 App 的 write token 请求的是 `contents:write` + `pull_requests:write` +
  `metadata:read`（`github_app._WRITE_PERMISSIONS`），**不含 `workflows`**。所以
  一次改到 `.github/workflows/` 的重推仍可能被 GitHub 拒（落在
  `⚠️ 平台自动重推失败` 上，卡面看得见）。`push_topic_branch_for_github_pr` 里
  那次「同步默认分支后重试一次」是为这个准备的。要根治得改
  `_WRITE_PERMISSIONS`，而那个集合是**不做 narrow 直接送给 GitHub** 的——加一项
  没被授予的权限会让整次铸 token 以 422 失败，属于另一件事。

测试：`backend/tests/integration/test_accept_app_waits_for_ci.py`。

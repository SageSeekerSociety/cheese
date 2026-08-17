# 提交与 PR 规范化

## 目标

让平台产出的 git 历史「可读、可追溯、归属正确」：提交信息、PR 标题/正文统一走 Conventional Commits（英文），并且提交和 PR 都能关联到人的 GitHub 账号。由 @彭文博 提出。

## 四个问题的根因（都已定位到代码，都有实测数据）

1. **「芝士 edits」「同步上游 → main」「PR 快照」不是 AI 写的**，是后端硬编码的中文模板。散在 `<&backend/app/domain/workspace/service.py>`、`<&backend/app/domain/agent/awaited_tasks.py>`、`<&backend/app/domain/agent/device_launch.py>`。
2. **提交关联不上 GitHub 账号**：所有提交的作者都写死成 `芝士 <cheese@zhishi.local>`，这个邮箱不属于任何 GitHub 账号，所以显示成灰色无头像的名字。
3. **PR 关联不上账号**：当前主路径（`accept_via_pr`）用平台 GitHub App 的 token 开 PR，GitHub 就把 PR 算在 bot 名下。PR 标题直接拿话题标题（中文房间名），正文写的是「验收人/路由理由」这类平台内部信息。
4. **一个 PR 里几十条提交，不是每轮快照，是同步上游**（@彭文博 发现）。实测：PR #488 的 40 条里 39 条是 `同步上游 upstream/main → main`；#483 是 39/42，#454 是 33/34，#422 是 27/37。本仓自己的 local `main` 比 `upstream/main` 多 **41 条**这种提交，而两边的文件树**一个字节都不差**。

   根因：`ensure_repo` 给 local main 造了一个合成的 `init` 提交，于是 local main 和 GitHub 的 main 是**两段无关历史**，`sync_upstream` 只能 `merge --no-ff`——每次 tick 造一个只存在于本地的合并提交，没有任何东西清理它们。话题分支是从 local main 切出来的，于是把这一整摞都带进了自己的 PR。

   第一性原理：**绑了 GitHub 的项目，local `main` 是上游默认分支的镜像，不是一个自己的分支**。采纳即合并（#296）之后平台根本不往 main 上提交，所以它永远可以快进。

## 已定的做法

- **提交信息**：平台自动提交一律改成英文 Conventional Commits（`chore: snapshot workspace after agent turn` 等）；「后台任务运行中，可能是中间态」这类警告移到正文，不再挂在标题上。
- **谁的提交**：话题发起人连了 GitHub 就用他的 `<id>+<login>@users.noreply.github.com` 作为 author（jj 0.43 无 `--author`，改用 `jj metaedit --update-author` 事后改写）。没连账号就保持 芝士，不编邮箱。
- **谁的 PR**：开 PR 时优先用发起人自己的 user-to-server token，失败（离职/取消授权/权限不足）自动回落到 App token，绝不因此开不出 PR。
- **落到 main 的那一个提交**：本仓是 squash 合并，整个话题压成一个提交。它的标题/正文由芝士递卡时写：`cheese accept-request <handle> "<理由>" --subject '<Conventional Commits 标题>' --body '<为什么>'`。格式不对后端当场打回并给出正确写法。不给 `--subject` 就回落成 `chore: <话题标题>`——故意难看，提醒补上。
- **归属兜底**：squash 提交正文带 `Co-authored-by:`，GitHub 据此计入贡献。
- **同步上游**：改成「能快进就快进，绝不造合并提交」。判据不是「落后几个提交」而是**本地 base 相对两边共同祖先有没有内容改动**——因为存量项目已经比上游多几十个空合并，用 `merge --ff-only` 只会被拒绝然后再造第 42 个。有真实本地内容（绑定前就有活的项目）时才走真合并。

## 规范本身（写进 <&CLAUDE.md> 的「Commits and PRs」一节）

- 标题：`type(scope): description`，英文祈使句，≤72 字符，不加句号，说「改了什么」不说「动了哪些文件」。
- 正文：只在标题不自明时写，讲**为什么**（根因、为什么这么修）。不写做了什么的清单、不写「测试全绿」、不写构建方法、不写自己补丁各版本之间的差异。
- 一次提交只做一件事；不打补丁式提交（jj 里直接改 + `jj describe`，不叠一个「修上一个」）。
- 默认读者是熟悉本项目的合格开发者，别解释他们已知的东西。

## 改了什么（backend）

- 新增 `<&backend/app/domain/workspace/identity.py>`：把人解析成 git 身份并落盘（同步的快照路径没有 DB session，只能读文件）。
- 新增 `<&backend/app/domain/review/commit_message.py>`：Conventional Commits 校验 + squash 标题拼接。
- 新增 `<&backend/app/domain/review/pr_text.py>`：PR/提交文案只此一份——之前四个地方各写各的。
- `<&backend/app/domain/workspace/service.py>`：`sync_upstream` 改为快进优先；平台自动提交信息全部英文化。
- `<&backend/app/domain/review/pr_publish.py>` / `<&backend/app/domain/review/github_pr.py>`：`open_pr` 支持以人的 token 开，失败回落 App。
- 迁移 `c1a5f70b3d24`：`accept_cards` 加 `change_subject` / `change_body`。
- `<&backend/sandbox/cheese>`：`accept-request` 加 `--subject` / `--body`。
- 前端验收卡上显示「合并后的提交标题」。

## 状态

已完成，等验收。后端 ruff + pyright 全绿，全量 pytest **4848 passed / 0 failed**；前端 lint 0 error、typecheck 0 error（ratchet 基线未变）。

## 未决：每轮一个快照提交，这个切分本身是错的

@彭文博 追问后确认的：把「芝士 edits」改成 `chore: snapshot workspace after agent turn` 只是把**看不懂的黑话**换成了**读得懂的噪音**——内容恒定的 message 等于没有 message。真正的问题是**提交的单位应该是一次改动，而「轮」是 AI 调度的单位**，它对读仓库的人没有意义，甚至不稳定（被人插一句话、被后台任务 hold 一次，边界就变了）。

而且快照真正要满足的是「容器挂了别丢改动」和「给采纳一个 ref 指」，这两件事 jj 在 git 之下的一层已经给了（工作副本本身就是提交，每次快照进 operation log）。现在能忍只是因为 squash 合并把这些全删了——这是个**承重的巧合**，仓库哪天改成 merge/rebase 合并就会落到 main 上。

方向：**一个话题一个提交，每轮 amend 进去**，逐轮粒度留给 `jj op log` 和平台时间线。PR 分支现在就已经是 `--force-with-lease` 强推，改写历史不额外增加成本。

**动手前必须先确认的一件事**：`pr_authorized_sha`（人类授权动作前移）冻的是点采纳那一刻的 commit SHA，之后拿当前 head 跟它对 diff 来卡超范围的改动。改成 amend 后那个 SHA 在强推后远端不可达，GitHub compare 可能解析不了。**那个安全阀比干净的历史值钱**，要么先验证它还能工作，要么把锚点换成 tree hash。未拍板，未动手。

## 边界（先说清楚）

- **存量话题的历史提交不会被追溯改写**，只影响之后的提交；本话题自己的 PR 里那 41 条同步提交也还在。
- 同步上游的修复对**新切出来的话题分支**才干净——已经开着的分支已经把那一摞带进去了。
- 归属要求本人在设置里连过 GitHub；没连就还是芝士署名，不会瞎编邮箱。

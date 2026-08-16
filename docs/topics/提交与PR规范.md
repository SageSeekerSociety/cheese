# 提交与 PR 规范化

## 目标

让平台产出的 git 历史「可读、可追溯、归属正确」：提交信息、PR 标题/正文统一走 Conventional Commits（英文），并且提交和 PR 都能关联到人的 GitHub 账号。由 @彭文博 提出。

## 三个问题的根因（都已定位到代码）

1. **「芝士 edits」「同步上游 → main」「PR 快照」不是 AI 写的**，是后端硬编码的中文模板。散在 `<&backend/app/domain/workspace/service.py>`、`<&backend/app/domain/agent/awaited_tasks.py>`、`<&backend/app/domain/agent/device_launch.py>`。
2. **提交关联不上 GitHub 账号**：所有提交的作者都写死成 `芝士 <cheese@zhishi.local>`，这个邮箱不属于任何 GitHub 账号，所以显示成灰色无头像的名字。
3. **PR 关联不上账号**：当前主路径（`accept_via_pr`）用平台 GitHub App 的 token 开 PR，GitHub 就把 PR 算在 bot 名下。PR 标题直接拿话题标题（中文房间名），正文写的是「验收人/路由理由」这类平台内部信息。

## 已定的做法

- **提交信息**：平台自动提交一律改成英文 Conventional Commits（`chore: snapshot workspace after agent turn` 等）；「后台任务运行中，可能是中间态」这类警告移到正文，不再挂在标题上。
- **谁的提交**：话题发起人连了 GitHub 就用他的 `<id>+<login>@users.noreply.github.com` 作为 author（jj 0.43 无 `--author`，改用 `jj metaedit --update-author` 事后改写）。没连账号就保持 芝士，不编邮箱。
- **谁的 PR**：开 PR 时优先用发起人自己的 user-to-server token，失败（离职/取消授权/权限不足）自动回落到 App token，绝不因此开不出 PR。
- **落到 main 的那一个提交**：本仓是 squash 合并，整个话题压成一个提交。它的标题/正文由芝士递卡时写：`cheese accept-request <handle> "<理由>" --subject '<Conventional Commits 标题>' --body '<为什么>'`。格式不对后端当场打回并给出正确写法。不给 `--subject` 就回落成 `chore: <话题标题>`——故意难看，提醒补上。
- **归属兜底**：squash 提交正文带 `Co-authored-by:`，GitHub 据此计入贡献。

## 规范本身（写进 <&CLAUDE.md> 的「Commits and PRs」一节）

- 标题：`type(scope): description`，英文祈使句，≤72 字符，不加句号，说「改了什么」不说「动了哪些文件」。
- 正文：只在标题不自明时写，讲**为什么**（根因、为什么这么修）。不写做了什么的清单、不写「测试全绿」、不写构建方法、不写自己补丁各版本之间的差异。
- 一次提交只做一件事；不打补丁式提交（jj 里直接改 + `jj describe`，不叠一个「修上一个」）。
- 默认读者是熟悉本项目的合格开发者，别解释他们已知的东西。

## 改了什么（backend）

- 新增 `<&backend/app/domain/workspace/identity.py>`：把人解析成 git 身份并落盘（同步的快照路径没有 DB session，只能读文件）。
- 新增 `<&backend/app/domain/review/commit_message.py>`：Conventional Commits 校验 + squash 标题拼接。
- `<&backend/app/domain/review/services.py>`：三条合并路径的 commit_title/message 统一走同一套构造器（之前 `_accept_via_pr` 还有一份自己的中文内联文案）。
- `<&backend/app/domain/review/pr_publish.py>` / `<&backend/app/domain/review/github_pr.py>`：PR 文案统一；`open_pr` 支持以人的 token 开。
- 迁移 `c1a5f70b3d24`：`accept_cards` 加 `change_subject` / `change_body`。
- `<&backend/sandbox/cheese>`：`accept-request` 加 `--subject` / `--body`，不给会在 stderr 提醒。
- 前端验收卡上显示「合并后的提交标题」——采纳前是最后一次能反对它的机会。

## 状态

代码、测试、文档都已完成，后端 ruff + pyright 全绿，新增/改动的单测与集成测试通过。全量 pytest 与前端 lint/typecheck 在跑。

## 待办

- 全量测试结果确认 → 递验收卡。
- 已有的老话题第一次跑轮次时才会写入身份文件，所以**存量话题的历史提交不会被追溯改写**——只影响之后的提交。

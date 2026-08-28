# 退掉 jj：让芝士自己提交、推送、开 PR

**状态：设计已定（Zhifei，2026-08-27）。已落地：芝士手上那张 GitHub 票不再是只读的（见第四节第一条），以及主体的前一半——平台不再替任何人提交（见第二节）。剩下的是机械替换：jj workspace 换成 git worktree、镜像去掉 jj、存量项目迁回纯 git。**

这份文档面向零前置知识的读者。前半解释平台为什么曾经用 jj、那个理由为什么没了；后半是还要改什么，以及为什么。涉及的术语第一次出现时都会解释。

---

## 一、为什么当初选 jj，以及那个理由现在没了

平台的版本控制用的是 **jj（Jujutsu）**——一个建在 git 之上的前端，它管「分支」叫**书签（bookmark）**，两边要靠 `jj git export` / `import` 来回同步。

选它的理由曾经写在 `workspace/service.py` 的模块开头，**只有一条**：

> 每个话题 = 一个 jj workspace（取代 git worktree）。**这样沙箱容器里的原生 Bash/Write/Edit 改动会被 jj 自动快照（无需手动 commit）**，而 git 侧照常工作。

其余全部——colocate、bookmark、导出成 git branch——都是为了让 git 在选了 jj 之后**还能照常用**而搭的补偿机制。

**所以退掉 jj 不是「换个版本控制工具」，是把 jj 唯一的理由删掉。** 一旦芝士自己提交，jj 不再买到任何东西，只剩下面这些已经实打实付过的账：

| 代价 | 出处 |
|---|---|
| 后端进程和沙箱里的芝士**必须是同一个 uid**（写死 1000，后端镜像专门造得匹配，一个测试把三处钉在一起） | `AGENT_UID` 注释 |
| `.jj/repo/config-id` 硬编码 0600，谁先写谁占有，另一个 uid 之后所有 jj 命令全死 | #242「全项目停摆」 |
| 沙箱里 `git status` 报「不是仓库」，看着像仓库坏了 | `sandbox/skills/cheese/SKILL.md` 要专门写一段解释 |
| 沙箱里 `jj git push` 用不了（镜像 git 版本低于 jj 要求） | 同上 |
| 芝士**看不见自己的推送状态**，只能「断言，不是观察」 | 同上——已出过一次假报告：报「改动已进 PR 分支」，而分支根本没动 |
| 工作区过期（stale）→ 那一轮的活进不了分支 | 全仓搜不到 `jj workspace update-stale` |
| bookmark 和 git ref 会双向不一致，要一套调和逻辑 | `_catch_up_with_branch` / `_catch_up_topic_workspace` |

最后一行值得单独说：**这些代价没有一条是 jj 本身的错，全都是「平台替模型写树」这个决定的下游。** 换成 git 也一样——只要平台在写别人的工作树，就还得有工作区隔离、uid 对齐，和一套「别人推上来的东西不能被我盖掉」的调和。

## 二、已经落地：平台不再替任何人提交

**芝士自己提交、自己推送、自己开 PR，全部通过 prompt 交代。平台不再写任何人的工作树。**

这不是新架构——设备路本来就是这么跑的：机器自己 `git clone`、自己 `git commit`、把分支 push 回来（`api/routes/git_http.py`），没提交的东西另外存到 `refs/cheese/snapshots/<branch>`，**永远不动分支**。平台那套代劳是并排的第二套机制。

拿掉的是：每轮末的快照、采纳前快照、开 PR 前快照、两阶段采纳前快照、手动补推前快照，以及 `snapshot_worktree`、`commit_pending_work`、`_put_the_branch_under_the_working_copy`、`cheese await` 的快照暂缓、「平台自己的快照失败了」那条房间通知。

两条明确决定（Zhifei，2026-08-27 / 08-28），别自作主张加回来：

**采纳不看工作树脏不脏。** 采纳 = 合并那个 PR，PR 里有什么合什么。人在工作区改了但没提交的东西没进 PR，那它本来就不该被合进去——这比「快照一下悄悄塞进去」诚实。这也让采纳回到 `docs/agent-principles.md` 第五条本来的样子：平台不在合并前往那条分支上追加任何东西。

**平台也不要「看一眼」树脏不脏然后提醒。** 三条理由，每条单独就够：

- **芝士自己拿得到。** `git status` 就在它手边，一条命令。平台去查了再告诉它，不是反馈，是噪音。真正拿不到的是推送结果、卡被驳回、平台判它这轮失败——那些在它的机器之外，`git status` 不是。
- **提不提交是它的选择。** 在 room/task 下，一件活是芝士的交付。它决定「这半截先不提交」是正当判断，平台去查等于替它做判断。
- **后果已经是结构性的。** 采纳 = 合并那个 PR。没提交就不在 PR 里，就合不进去。**系统本来就不可能合并没提交的东西**，不需要谁额外看一眼。

那么原来担心的「活悄悄丢」去哪了？它随代劳一起消失：**丢活的从来不是模型忘了提交，是平台替它提交、失败了、而谁都不知道。** 代劳没了，这个失败模式就没有了，不需要一个检测去对准一个不再存在的东西。

### 还剩的替换

| 今天 | 之后 |
|---|---|
| jj workspace（`jj workspace add`） | git worktree（`git worktree add`） |
| bookmark → git branch 导出 | 不需要，本来就是 git branch |
| 主仓 jj colocate（`.git` + `.jj`） | 只有 `.git` |
| 镜像里装 jj | 不装 |

一起消失的还有 `AGENT_UID` 必须对齐这条约束的 jj 部分——`config-id` 0600 那个陷阱随 `.jj` 一起走。

---

## 三、改动面

- `_jj(` 全后端**只出现在一个文件**：`backend/app/domain/workspace/service.py`。剩下的调用点是检出与合并前置：`_ensure_worktree`、`_catch_up_with_branch`、`prepare_conflict_resolution`、`prepare_upstream_conflict_resolution`、`_catch_up_topic_workspace`
- `sandbox_vcs_mounts` 整套是围着 `.jj/repo` 那个相对指针建的，换成 git worktree 之后它没有对位物（worktree 的 `.git` 存的是**绝对**路径）——而 #630 退掉平台自己那台机器之后，全后端已经没有调用它的地方了，跟着删
- 沙箱镜像：不再需要 jj
- 存量项目要从 jj colocate 迁回纯 git（`.git` 本来就在，colocate 的意思就是两者并存），并把已有的 jj workspace 目录换成 git worktree——**目录名必须一个字不变**

## 四、开工前要确认的（已核实）

**1. 芝士推送用什么凭据 —— 已经解决。**

`/sandbox/github-token` 铸的是平台 GitHub App 在这个仓库上被授予的**全部**权限，一项不减：`contents` / `pull_requests` / `workflows` 的写权限都在里面。原来那组收窄成只读的权限、以及把它钉住的那条测试，都已经删掉（Zhifei，2026-08-27：全给，不做隔离——这也正是 `docs/agent-principles.md` 第二条「凭证不携带任何缩水」）。于是两条路都通：

- **自己提交 + 自己推回项目仓**：`api/routes/git_http.py` 用话题手上那个 scoped cheese token 认证 `git-receive-pack`，设备路今天就是这么把分支推回来的，零新凭据。
- **自己推到 GitHub、自己开 PR**：`export GH_TOKEN=$(cheese gh-token)`，然后 `git push https://x-access-token:$GH_TOKEN@github.com/<owner>/<repo> HEAD:<分支>`、`gh api repos/<owner>/<repo>/pulls`。沙箱镜像里 git 2.39.5 和 gh 2.62.0 都在，两条命令都不需要新工具。

两条路今天都通：干活的机器拿到的是一份普通 `git clone`。平台自己那份检出还是 jj workspace（里面没有 `.git`），但没有人在那里面干活——它只被文件面板和合并读。

**2. 多棵树共用一个仓库 —— 对位替换成立。**

#615 已合并。路径声明和锁挂的是**树**，不是 jj workspace：`_worktree_path` 走 `tree_for_place`，`_tree_dirname` 从 topic id 派生并且**刻意不跟分支名走**。要保住的只有目录名不变——`.jj/repo` 的相对指针、`sandbox_vcs_mounts` 复制的深度、容器 workdir、以及由 workdir 派生的 tmux 会话名，全都吃它。

**3. 沙箱镜像的 git —— 不用升。**

`Git does not recognize required option: porcelain` 说的是 **jj 要的那个 `--porcelain`**，不是 git 自己推不动。镜像基底 `node:22-bookworm-slim` 里实测 git 2.39.5：`git fetch --porcelain` 报 `unknown option`，而 `git push --porcelain` 认得（只抱怨没配远端）。普通 `git push` 在它上面完全够用，jj 一走这条限制跟着走。

**4. 归属 —— 作者是人类需求方，用 git 自己的两个身份位。**

`workspace/identity.py` 已经把「话题属于谁」解析成一个 GitHub 认得的 `<id>+<login>@users.noreply.github.com`，并落盘在工作区旁边（`git-identity.json`），开屏路径没有 DB session 也读得到。芝士自己提交时把它读成 `GIT_AUTHOR_*`。

顺带**变好一件事**：平台不再自己提交之后，「一个旋钮，不是两个」那条 jj 限制就不成立了——author 是人类需求方（`GIT_AUTHOR_*`），committer 是芝士（`GIT_COMMITTER_*`），`Co-authored-by:` 照 #546 的口径进 trailer。

## 相关

- `backend/app/domain/workspace/service.py` — 剩下的 jj 调用点都在这里
- `docs/agent-principles.md` — 第五条「采纳 = 合并那个 PR」
- `docs/device-self-hosting.md` — 设备路为什么不碰用户机器上的东西
- #242 — `config-id` 属主导致全项目停摆
- #516 — 沙箱镜像缺 jj 等工具，每个被托管仓库都要为此付说明文字

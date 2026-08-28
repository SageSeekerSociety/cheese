# 退掉 jj：让芝士自己提交、推送、开 PR

**状态：设计已定（Zhifei，2026-08-27）。已落地两块——平台不再把「提交没做成」当成「本来就没活」（见第三节），以及芝士手上那张 GitHub 票不再是只读的（见第六节第一条）；主体（芝士自己提交、平台停止写树）还没开工。**

这份文档面向零前置知识的读者。前半解释平台今天在做什么、为什么这么做；后半是要改成什么，以及为什么。涉及的术语第一次出现时都会解释。

---

## 一、今天：平台替芝士敲那次提交

### 缺的是哪一步

芝士干活的方式和人一样：读文件、写文件、跑命令。干完之后，改动**躺在磁盘上**。

但平台后面每件事都不看磁盘，只看**提交（commit）**——git 里记录下来的一次改动快照：

- 房间里那句「这轮改了 3 个文件、+120/-15」，是数提交数出来的
- 验收卡上的 diff，是提交之间的差
- 推到 GitHub 开 PR，推的是提交
- 点「采纳」，合并的是提交

**磁盘上的改动和提交之间隔着一步：得有人敲一次 `git commit`。**

```
芝士改文件  ──▶  ???  ──▶  改动摘要 / 验收卡 diff / PR / 采纳
（在磁盘上）              （全部只认提交）
```

### 今天由谁敲，取决于芝士跑在哪

| 芝士跑在哪 | 谁敲这次提交 |
|---|---|
| **沙箱**——平台自己开的容器 | **平台替它敲。**代码里叫「快照」（snapshot） |
| **设备**——用户自己接进来的机器 | **芝士自己推。**平台不碰它的文件 |

设备路的分工是显式写死的：`checkpoint` 在通用通道上是空实现，注释写着 "the device owns its own tree"，只有沙箱通道覆盖它去做快照。

**这份文档要改的是沙箱路——把它并到设备路已经在用的模型上。**

### 平台一共在五个时机做快照

都在 `workspace/service.py`，各有自己的提交信息：

| 时机 | 所在函数 | 提交信息 |
|---|---|---|
| 每轮结束 | `checkpoint_worktree` | `chore: snapshot workspace after agent turn` |
| 本地合并采纳之前 | `merge_topic` | `chore: snapshot workspace before accept` |
| 平台推分支之前 | `push_topic_branch` | `chore: snapshot workspace for pull request` |
| 平台为 PR 推分支之前 | `push_topic_branch_for_github_pr` | `chore: snapshot workspace before two-phase accept` |
| 手动补推之前 | `review/services.py` | `chore: snapshot workspace for push-fix` |

---

## 二、为什么当初选 jj，以及那个理由现在没了

平台的版本控制用的是 **jj（Jujutsu）**——一个建在 git 之上的前端，它管「分支」叫**书签（bookmark）**，两边要靠 `jj git export` / `import` 来回同步。

选它的理由写在 `workspace/service.py` 的模块开头，**只有一条**：

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
| 工作区过期（stale）→ 这一轮的活进不了分支（下一节） | 全仓无恢复代码 |
| bookmark 和 git ref 会双向不一致，要一整套调和逻辑 | `_put_the_branch_under_the_working_copy` |

最后一行值得单独说：**这些代价没有一条是 jj 本身的错，全都是「平台替模型写树」这个决定的下游。** 换成 git 也一样——只要平台在写别人的工作树，就还得有工作区隔离、uid 对齐，和一套「别人推上来的东西不能被我盖掉」的调和。

## 三、这个决定制造出来的那个 bug

### 一次没做成的提交，长得和「本来就没活」一模一样

平台替芝士提交，于是「这一轮的改动」和「分支上的提交」之间隔着一次**会失败的操作**。它失败时磁盘上一个字节都没少，而下面每一处一起看不到这一轮——它们读的全是提交：

| 应该发生 | 实际发生 |
|---|---|
| 改动变成提交 | 没有提交 |
| 房间里显示「这轮改了什么」 | **什么都不显示**——摘要是数「新出现的提交」算的 |
| 分支往前走 | 分支不动 |
| 验收卡 diff 含这轮改动 | 不含——`topic_diff` 比的是**分支**和它的起点 |
| PR 更新 | 没变化，或 diff 是空的 |

在采纳和推 PR 两条路上还要再糟一层：**它们不是漏掉这一轮，是带着一条缺了这一轮的分支继续走完。**

### 「没做成」曾经被当成「没有」——这一半已经修掉

五个快照点原先全都把失败吞掉：每轮末那次是 `except Exception` 加一行服务器日志，另外四处是 `except ValidationError: pass`，注释写着「还没有工作区」。那句注释描述的情况确实无害，但 **`ValidationError` 是 jj 任何一次执行失败都会抛的东西**（`_jj` 里除权限问题外一律抛它），工作区过期抛的就是它。于是「活丢了」被当成「本来就没活」，人看到的 diff 里没有它，点了采纳，合进主干的也没有它，全程零报错。

现在「有没有工作区」由 `workspace.commit_pending_work` 一处读盘回答：**没有工作区**是唯一还会安静跳过的情况；**提交没做成**会让采纳、开 PR、push-fix 停下来并说出原因，每轮末那次失败会在房间里落一条系统事件（带上 jj 自己那句话，里面就有恢复命令）。

失败本身没有消失。它是「平台替别人写树」的下游，只有第四节那个改法才拿得掉。

### 为什么会过期

一个项目底下很多棵工作树，每棵是一个 jj workspace，**共用同一个版本库存储**。同时对它下命令的至少三方：后端（多线程、无排队）、沙箱容器里的芝士（**另一个系统用户**）、从 git 代理推进来的代码。任一方改了存储，其他工作区就过期，jj 拒绝执行并提示：

```
Error: The working copy is stale (not updated since operation ...).
Hint: Run `jj workspace update-stale` to update it.
```

**这句恢复命令在整个代码库里搜不到**——41 个远程分支，零命中。而快照第一步就是问 jj「这轮有没有改动」，所以过期时它第一个动作就失败，后面全不执行。

---

## 四、改成什么

**芝士自己提交、自己推送、自己开 PR，全部通过 prompt 交代。平台不再写任何人的工作树。**

这不是新架构——**设备路今天就是这么跑的**，而且设备路没有上面任何一条毛病。这次是把沙箱路并过去。

### 具体替换

| 今天 | 之后 |
|---|---|
| jj workspace（`jj workspace add`） | git worktree（`git worktree add`） |
| 平台每轮末自动 `jj commit` | 芝士自己 `git commit` |
| 平台 `push_topic_branch` 代推 | 芝士自己 `git push` |
| 平台开 PR | 芝士自己开 PR |
| bookmark → git branch 导出 | 不需要，本来就是 git branch |
| 采纳前快照 | **取消**——采纳 = 合并那个 PR，PR 里有什么就合什么 |

最后一行是本次的一条明确决定（Zhifei，2026-08-27）：**采纳不看工作树脏不脏。** 人在工作区改了但没提交的东西没进 PR，那它本来就不该被合进去——这比「快照一下悄悄塞进去」诚实。想提示「你还有没提交的改动」是可以另外做的事，但它是附加功能，不是核心机制，不进这一轮。

这也让采纳回到 `docs/agent-principles.md` 第五条本来的样子：**采纳 = 合并那个 PR**，平台不在合并前往那条分支上追加任何东西。

### 会一并消失的东西

- 五个快照点和 `snapshot_worktree` 本身
- `_put_the_branch_under_the_working_copy`（bookmark/git ref 调和）
- `cheese await` 的快照暂缓逻辑——芝士自己提交，自然是命令跑完才提交
- `AGENT_UID` 必须对齐这条约束的 jj 部分（`config-id` 0600 陷阱随 `.jj` 一起走）
- SKILL.md 里那整段 jj 免责声明

### 也不要留一个「看一眼」

这一节原本写的是：平台停止写树，但每轮结束查一句「树还脏着吗 / 分支动了吗」，脏了就说一句。**那也不做**（Zhifei，2026-08-28）。

三条理由，每条单独就够：

**芝士自己拿得到。** `git status` 就在它手边，一条命令。平台去查了再告诉它，不是反馈，是噪音。真正拿不到的是推送结果、卡被驳回、平台判它这轮失败——那些在它的沙箱之外，`git status` 不是。

**提不提交是它的选择。** 在 room/task 下，一件活是芝士的交付。它决定「这半截先不提交」是正当判断，平台去查等于替它做判断。

**后果已经是结构性的。** 采纳 = 合并那个 PR。没提交就不在 PR 里，就合不进去。**系统本来就不可能合并没提交的东西**，不需要谁额外看一眼。

那么原来担心的「活悄悄丢」去哪了？它随代劳一起消失：**丢活的从来不是模型忘了提交，是平台替它提交、失败了、而谁都不知道。** 代劳没了，这个失败模式就没有了，不需要一个检测去对准一个不再存在的东西。

---

## 五、改动面

- `_jj(` 全后端**只出现在一个文件**：`backend/app/domain/workspace/service.py`（约 2900 行，27 处调用）
- 连带要改：`agent/awaited_tasks.py`（快照暂缓）、`review/services.py`（补推）、`agent/tmux_provider.py` 与 `harness/claude_code/hooks_substrate.py`（`checkpoint` 覆盖）
- 沙箱镜像：不再需要 jj；需要一个能 push 的 git（今天镜像里的 git 版本连 jj 的 push 都带不动）
- `sandbox/skills/cheese/SKILL.md`：删掉 jj 免责段，改成芝士自己提交推送的说明
- 存量项目要从 jj colocate 迁回纯 git（`.git` 本来就在，colocate 的意思就是两者并存）

## 六、开工前要确认的（已核实）

**1. 芝士推送用什么凭据 —— 已经解决。**

`/sandbox/github-token` 铸的是平台 GitHub App 在这个仓库上被授予的**全部**权限，一项不减：`contents` / `pull_requests` / `workflows` 的写权限都在里面。原来那组收窄成只读的权限、以及把它钉住的那条测试，都已经删掉（Zhifei，2026-08-27：全给，不做隔离——这也正是 `docs/agent-principles.md` 第二条「凭证不携带任何缩水」）。于是两条路都通：

- **自己提交 + 自己推回项目仓**：`api/routes/git_http.py` 用话题手上那个 scoped cheese token 认证 `git-receive-pack`，设备路今天就是这么把分支推回来的，零新凭据。
- **自己推到 GitHub、自己开 PR**：`export GH_TOKEN=$(cheese gh-token)`，然后 `git push https://x-access-token:$GH_TOKEN@github.com/<owner>/<repo> HEAD:<分支>`、`gh api repos/<owner>/<repo>/pulls`。沙箱镜像里 git 2.39.5 和 gh 2.62.0 都在，两条命令都不需要新工具。

**所以剩下的缺口不是凭据，是一个 `.git`。** jj 工作区里没有它（`jj workspace add` 出来的次级工作区不 colocate），`jj git push` 又用不了，拿着这张票照样推不动——要等第四节把 jj workspace 换成 git worktree。跑在托管机器上的那条路（普通 `git clone`）今天就已经能一路走到 PR。

**2. 多棵树共用一个仓库 —— 对位替换成立。**

#615 已合并。路径声明和锁挂的是**树**，不是 jj workspace：`_worktree_path` 走 `tree_for_place`，`_tree_dirname` 从 topic id 派生并且**刻意不跟分支名走**。要保住的只有目录名不变——`.jj/repo` 的相对指针、`sandbox_vcs_mounts` 复制的深度、容器 workdir、以及由 workdir 派生的 tmux 会话名，全都吃它。

**3. 沙箱镜像的 git —— 不用升。**

`Git does not recognize required option: porcelain` 说的是 **jj 要的那个 `--porcelain`**，不是 git 自己推不动。镜像基底 `node:22-bookworm-slim` 里实测 git 2.39.5：`git fetch --porcelain` 报 `unknown option`，而 `git push --porcelain` 认得（只抱怨没配远端）。普通 `git push` 在它上面完全够用，jj 一走这条限制跟着走。

**4. 归属 —— 作者是人类需求方，用 git 自己的两个身份位。**

`workspace/identity.py` 已经把「话题属于谁」解析成一个 GitHub 认得的 `<id>+<login>@users.noreply.github.com`，并落盘在工作区旁边（`git-identity.json`），同步快照路径没有 DB session 也读得到。芝士自己提交时，把它读成 `GIT_AUTHOR_*` 即可。

顺带**变好一件事**：identity.py 现在写着「一个旋钮，不是两个——jj 0.43 从 JJ_USER/JJ_EMAIL 同时设 author 和 committer，没有 `--author`，所以记不下『芝士提交的』」。git 有 `GIT_COMMITTER_*`，所以退掉 jj 之后这条限制消失：author 是人类需求方，committer 是芝士，`Co-authored-by:` 照 #546 的口径进 trailer。那段注释要在同一个 PR 里删掉。

## 相关

- `backend/app/domain/workspace/service.py` — 模块开头就是 jj 的原始理由
- `docs/agent-principles.md` — 第五条「采纳 = 合并那个 PR」
- `docs/device-self-hosting.md` — 设备路为什么不碰用户机器上的东西
- #242 — `config-id` 属主导致全项目停摆
- #516 — 沙箱镜像缺 jj 等工具，每个被托管仓库都要为此付说明文字

# 退掉 jj：让芝士自己提交、推送、开 PR

**状态：设计已定（Zhifei，2026-08-27），尚未开工。**

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
| 工作区过期（stale）→ **静默丢活**（下一节） | 全仓无恢复代码 |
| bookmark 和 git ref 会双向不一致，要一整套调和逻辑 | `_put_the_branch_under_the_working_copy` |

最后一行值得单独说：**这些代价没有一条是 jj 本身的错，全都是「平台替模型写树」这个决定的下游。** 换成 git 也一样——只要平台在写别人的工作树，就还得有工作区隔离、uid 对齐，和一套「别人推上来的东西不能被我盖掉」的调和。

## 三、今天最贵的一个 bug，正好是这个决定的产物

### 五个快照点，五个全都把失败吞掉

```python
# agent/awaited_tasks.py —— 每轮结束的自动快照
except Exception:
    logger.warning("snapshot failed for topic %s", topic_id, exc_info=True)
    return "failed"
```

```python
# workspace/service.py 三处 + review/services.py 一处
except ValidationError:
    pass  # no workspace/jj state yet — nothing pending to fold
```

那句注释——「还没有工作区 / jj 状态，没有待折叠的东西」——描述的是一种确实无害的情况。

**但 `ValidationError` 是 jj 任何一次执行失败都会抛的东西**（`_jj` 里除权限问题外一律抛它）。工作区过期抛的就是它，一模一样。

**所以这五处的实际行为是：把「活丢了」当成「本来就没活」，然后继续往下走。**

### 后果

| 应该发生 | 实际发生 |
|---|---|
| 改动变成提交 | 没有提交 |
| 房间里显示「这轮改了什么」 | **什么都不显示**——摘要是数「新出现的提交」算的 |
| 分支往前走 | 分支不动 |
| 验收卡 diff 含这轮改动 | 不含——`topic_diff` 比的是**分支**和它的起点 |
| PR 更新 | 没变化，或 diff 是空的 |
| 有人被告知 | **没有** |

在采纳和推 PR 两条路上还要再糟一层：**它们不是漏掉这一轮，是带着一条缺了这一轮的分支继续走完。** 人看到的 diff 里没有它，点了采纳，合进主干的也没有它，全程零报错。

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

### 必须保留的：检测，不是代劳

平台**停止写树，但不要停止看树**。每轮结束检查一句「树还脏着吗 / 分支动了吗」，脏了就在房间里说一句。

这是那套机制真正在保护的东西——**活不会悄悄丢**——而检测比代劳便宜两个数量级，且不带上面任何一条代价。

要说清楚的是：今天的问题**从来不是模型忘了提交**，是平台替它提交、失败了、然后谁都不知道。检测直接对准真实的失败模式。

---

## 五、改动面

- `_jj(` 全后端**只出现在一个文件**：`backend/app/domain/workspace/service.py`（2914 行，27 处调用）
- 连带要改：`agent/awaited_tasks.py`（快照暂缓）、`review/services.py`（补推）、`agent/tmux_provider.py` 与 `harness/claude_code/hooks_substrate.py`（`checkpoint` 覆盖）
- 沙箱镜像：不再需要 jj；需要一个能 push 的 git（今天镜像里的 git 版本连 jj 的 push 都带不动）
- `sandbox/skills/cheese/SKILL.md`：删掉 jj 免责段，改成芝士自己提交推送的说明
- 存量项目要从 jj colocate 迁回纯 git（`.git` 本来就在，colocate 的意思就是两者并存）

## 六、开工前要确认的

1. **芝士推送用什么凭据。**`cheese gh-token` 已经存在，需要确认它在沙箱里够开 PR 用。
2. **多棵树共用一个仓库**：#615 的「一棵树一个 PR、多件活共用一棵树」建在 jj workspace 上，换 git worktree 是对位替换，但路径声明和锁那套要重新对一遍。
3. **归属**：今天平台会把提交作者改写成话题主人；改成芝士自己提交后，作者和 trailer 由 prompt 交代（#189 / #546 已经定过口径：作者是人类需求方，其余进 trailer）。

## 相关

- `backend/app/domain/workspace/service.py` — 模块开头就是 jj 的原始理由
- `docs/agent-principles.md` — 第五条「采纳 = 合并那个 PR」
- `docs/device-self-hosting.md` — 设备路为什么不碰用户机器上的东西
- #242 — `config-id` 属主导致全项目停摆
- #516 — 沙箱镜像缺 jj 等工具，每个被托管仓库都要为此付说明文字

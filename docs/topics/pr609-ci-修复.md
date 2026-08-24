# PR #609 的 CI 修复

## 目标

把 @task 设计现状 的 PR #609（`feat(tasks): re-deliver the foundation #608 merged as an empty PR`）修到能合。起点是 CI 的 `test` job 报 31 个失败。

@wangchangxin 中途定了两件事：切换真做完了再一起交；以及既然芝士推不到 GitHub，就把 609 的内容挪过来、从本话题开一份新 PR。

## 现在的状态

代码全部做完，**本地全量 5282 passed / 0 failed**，ruff 全过。改动在平台 git 的 `topic/155c6916` 上（`f642d3aa4`，比 main 多 **118 files / +4676**）。

**卡在最后一步：递不出卡了。** 见下面「死锁」。

## 出过一次事故：PR #611 空合并

第一次递卡时 PR #611 被采纳合并，但它的 diff 是 **0 files**——什么都没交付，main 只多了一个空提交 `97b53d67`。和 #608 是同一类事故。

机制：递卡那一刻平台把话题分支重置成「base + 一个空提交」并拿它开 PR；而平台的自动快照只 commit 工作区改动、**从不 push**。所以本地有全部内容、远端是空的、PR 自然也是空的。

**教训（已写进项目记忆）**：递卡前必须自己 `git push origin HEAD:refs/heads/topic/<自己的话题id>`；推被拒时用 **merge** 把远端那个空提交并进来（**绝不能 rebase**，平台用的是普通 push）；递卡后立刻查 PR 的 `changed_files` 非 0，再叫人采纳。

## 死锁：这个话题现在永久递不出卡

PR #611 合并后 GitHub 自动删掉了 `topic/155c6916` 分支。而：

- 平台推分支用 `git push --force-with-lease`，lease 取自它自己的 remote-tracking ref，那个 ref 还停在 `d8cceda99`；
- 远端实际已无此分支 → lease 判定 stale → 推送被拒（`! [rejected] (stale info)`）；
- 平台的定时同步跑的是 `git fetch upstream`，**不带 `--prune`**（<&backend/app/domain/workspace/service.py> 的 1830 与 1909 行），所以那个过期 ref 永远不会自己消失。

**这是平台的真缺陷**：任何话题只要 PR 合并过一次、GitHub 删过分支，就会永久卡在这里。

**解锁需要一个有写权限的人跑一条命令**（芝士的 token 是 `push: false`）：

```bash
gh api -X POST repos/SageSeekerSociety/cheese/git/refs \
  -f ref=refs/heads/topic/155c6916 \
  -f sha=d8cceda99de9cfe83dfb851532a590ee068c6755
```

跑完再点一次卡片上的采纳（那张 pending 的卡会重试）。`d8cceda99` 这个 commit 在 GitHub 上仍然存在，已核实。

## 绕开死锁：拆一条支线去交付

房间分支既然永久推不上去，交付就换一条从没在 GitHub 上出现过的分支走。

已拆出支线 @交付支线地基（`c67b321e`）。它的分支是 `topic/c67b321e`——GitHub 上没有同名 ref，所以没有过期 lease 可判；而它 fork 自房间分支（<&backend/app/domain/workspace/service.py> 的 `_fork_point`），带着完整的 118 个文件。

注意：`cheese split --help` 里写的「工作区是从 main 新建的」**已经过时**，代码实际是从所在房间的分支长出来的。这一条如果信错了，整个方案就不成立，所以是核对过代码才动手的。

简报里写死的硬边界：不改任何代码、不重跑测试（结果直接给它了）、**先 push 再递卡**、**绝不 rebase**、递完必须用命令确认 `changed_files` 非 0。

## 做完的三件事

### 一、CI 报的 31 个失败

修掉 30 个，其中挖出**两个真 bug**：

1. **支线里说的话会落到房间主线上**。<&backend/app/domain/agent/chat.py> 的 `post_user_message` 解析出了 place，却把房间 id 传给 `blocks.add`；`add` 是自己拆 place id 的，拿到房间 id 就静默当成"房间主线"。后果：支线里每一句话整个房间都能读到，且每句都点亮房间未读角标——正是角标文档写明不该做的事。
2. **`comments`（读+写）、`GET /agent`、项目级 memory 的两条路由对支线 id 返回 404**。13 条路由已换成 `place_or_404`，这几条漏了。

剩下 30 个是测试没跟上重构（假对象缺 `Place`/`task_id`、房间之下不能再套房间、房间叫 `archived` 而支线叫 `closed`）。

### 二、补完芝士 CLI 在支线里的整套能力

这是"切换有没有做完"的真正验收面。线程容器里的 `CHEESE_TOPIC` 就是**线程 id**（<&backend/app/domain/agent/tmux_provider.py> 就这么设的），而 CLI 把它直接拼进 `/topics/{id}/…`。**7 条路由只认房间**，等于分身失去这些命令：

`ask`、`decision`、`PUT doc`（`cheese doc set`）、`artifact`、`status`、`background-task`（`cheese await`）、`webhook-token`，外加 `preview`。

其中 **`title` 最危险**：它不报 404。一条支线给自己起名字，改的是**整个房间的名字**，两边都没有任何提示——而 `cheese title` 是分身被要求开工第一个跑的命令。

改的时候有三个地方必须分开，合并任何一个都是新 bug：

- **身份查 place，权限查房间**。per-turn token 按 place 签；roster 只有房间有。搞反了，支线自己的 token 会被判越权——而结果不是拒绝，是把作者抹成匿名。
- **`resolve_agent_handle` 读房间的 roster，但回落必须是支线自己的 handle**——那是它沙箱 token 铸出来的名字。合并的话，一个分身会有两个名字，记忆存进两个池子。
- **房间的盒子是房间的**。预览去敲哪个端口、算力档位允不允许预览，即使是支线在问，答案也是房间的。

顺带修了 `stall_signal`：它从整个房间读"最后一条 block"，于是一条死掉的支线，只要房间里还有别的支线在说话，就会被报成活着——正好是这个判定存在的意义。

### 三、删掉哨兵测试

<&backend/tests/unit/test_switch_is_still_in_progress.py>（整个文件就是一句 `pytest.fail()`）已删。依据是它自己写的条件：**创建 work 的代码必须指向 `tasks`**，否则就是"数据库历史搬走了、应用还在旁边写旧形状"。这个条件现在成立——`POST /topics` 只建房间、`split` / 讨论升级 / upstream-conflict 三条路都开支线——而且分身实际要跑的命令都能到达支线，有 11 条测试钉着。

## 知道但没做的（如实记账）

`GET /{topic_id}/usage` 和 `GET /{topic_id}/transcript` 仍然只认房间。

**没顺手改的原因**：两者的数据层**已经**按 (房间, 支线) 两列存了，所以不存在"还在写旧形状"；缺的是读路径。而补它要先定一件产品上的事——**一个房间的用量，该不该含它派出去的支线花的钱**。这个分支不该悄悄替人定，所以留着。

其余只认房间的路由（`members`、`children`、`tasks`、`read`、`archive`、`unarchive`、`PUT agent`、`compute-profile`）是**设计如此**：这些问题本来就只有房间才答得上来。

## 环境备注

跑 integration 用的临时库是我起的独立容器 `cheese-pr609-testpg`（绑 `127.0.0.1:15433`），没碰机器上任何现有容器。合并后可以删。

# PR #609 的 CI 修复

## 目标

把 @task 设计现状 的 PR #609（`feat(tasks): re-deliver the foundation #608 merged as an empty PR`）修到能合。起点是 CI 的 `test` job 报 31 个失败。

@wangchangxin 中途定了两件事：切换真做完了再一起交；以及既然芝士推不到 GitHub，就把 609 的内容挪过来、从本话题开一份新 PR。

## 现在的状态

**本地全量绿**，`ruff format` + `ruff check` 全过。改动全部落在本话题分支 `topic/155c6916` 上：609 的 48 个提交已合并进来，加上 7 个我的提交。

**下一步是递验收卡开新 PR**——这条路能走通，因为采纳流程会用批准人的 GitHub 身份推分支，绕开芝士自己 `push: false` 的限制。

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

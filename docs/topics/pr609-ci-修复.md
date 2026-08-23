# PR #609 的 CI 修复

## 目标

把 @task 设计现状 的 PR #609（`feat(tasks): re-deliver the foundation #608 merged as an empty PR`）的红色 CI 修绿。CI 的 `test` job 报 31 个失败。

## 现在的状态

**31 个失败已修掉 30 个**，本地全量 **5271 passed / 1 failed / 0 errors**，ruff format + check 全过。剩的那 1 个是分支自己设的红闸门（见下）。

4 个提交已推到平台 git 的 `topic/d5b859e4`（头 `ec91bb01d`）。**GitHub 上的 PR #609 还没收到**——见「卡在哪」。

## 修了什么

### 两个真 bug（改了生产代码）

1. **支线里说的话会落到房间主线上**。<&backend/app/domain/agent/chat.py> 的 `post_user_message` 解析出了 place，却把**房间的 id** 传给 `blocks.add`；`add` 会自己把 place id 拆成 (房间, 支线)，拿到房间 id 就静默地当成"房间主线"。后果：支线里的每一句话整个房间都能读到，而且每说一句就点亮房间的未读角标——恰恰是角标文档里写明不该做的事。

2. **三条路由对支线 id 返回 404**。13 条路由已经换成 `place_or_404`，`comments`（读+写）、`GET /agent`、以及项目级 memory 的两条没换到。memory 那两条最要命：`cheese remember` 就是干活的人在跑，而干活的人现在是一条支线。

   顺带修正了这里的一个陷阱：**identity 和 access 要用两个不同的 id**。per-turn token 是按 place 签的，所以身份校验要拿 place id（拿房间 id 会把支线自己的 token 判成越权，结果不是拒绝而是把作者抹成匿名）；而权限查的是**房间**的 roster，因为支线没有自己的 roster。

3. 附带补了一个缺口：房间归档时里面的支线被静默关掉，**支线里不留任何记录**（房间自己有一条）。人打开那条支线只会看到活干到一半断了，哪儿都查不到原因。

### 测试跟上重构（30 个失败里的绝大多数）

- `wake_target` 现在收 `Place`、`AcceptCard` 多了 `task_id`、`_topic_or_404` 改走 `PlaceResolver`——假对象一个都没跟上。
- 房间之下不能再套房间了（422），拆活出来的是 task 不是子话题，所以 `children` 是错的列表、`kind` 不是 task 有的字段。
- 房间收尾叫 `archived`、支线收尾叫 `closed`，是故意分开的两个词。
- `test_room_activity_vs_unread` 从来没跑通过，两半都是错的：它用评论说话（评论压根不算未读），又从话题列表里读角标（列表不带这个字段）。
- 删掉 `review.pr_publish → topic.repositories` 这条已经还清的豁免。

## 剩下的那 1 个红：不是 bug，是闸门

<&backend/tests/unit/test_switch_is_still_in_progress.py> 整个文件就是一句 `pytest.fail()`。它写明：这个分支的迁移把所有 work 的 `topics` 行删了、对话搬到房间上，在「创建 work 的代码也指向 tasks」之前合并它，等于**数据库历史搬走了、应用还在旁边写旧形状**。

**删掉这个文件 = 宣布切换完成。** 而实测说明切换**没**完成：

- 创建 work 的四条路径确实都指向 tasks 了（`POST /topics` 只建房间、`split` → task、消息升级 → task、`upstream_conflict` → `dispatch_task`）。
- **但还有 25 条 `/topics/{id}/*` 路由只认房间**，支线 id 打过去就是 404。其中这几条是芝士 CLI 每轮都在调的：`/ask`、`/decision`、`/title`、`PUT /doc`、`/artifact`、`/webhook-token`、`/background-task`。
- 而线程容器里的 `CHEESE_TOPIC` 就是**线程 id**（<&backend/app/domain/agent/tmux_provider.py> 写死 `"CHEESE_TOPIC": str(topic_id)`），CLI 打的正是 `/topics/$CHEESE_TOPIC/ask`。

**结论：现在一条支线里的分身用不了 `cheese ask`、`cheese decision`、`cheese title`、`cheese doc set`、`cheese artifact`。** 那个闸门是对的，现在不该拆。

（顺带一提，那个文件里写着「它是唯一失败的东西」——提交时实际有 31 个失败，这句话当时就不准。）

## 卡在哪：需要人推一把

改动在平台 git 上，**推不到 GitHub**：芝士的 `cheese gh-token` 权限是 `{"push": false}`（只有 actions/checks/metadata 只读）。平台代推只发生在验收卡处于 `pr_open` 的轮询循环里，而 @task 设计现状 这个话题**没有验收卡**，所以没有任何轮询器会去推它。

需要 @wangchangxin 拍板走哪条路，见对话里的选项。

## 环境备注

跑 integration 用的临时库是我起的独立容器 `cheese-pr609-testpg`（端口 127.0.0.1:15433），没碰任何现有容器。用完请提醒我删，或直接 `docker rm -f cheese-pr609-testpg`。

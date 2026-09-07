# T6 任务彻底不是地点

## 目标

「任务=分身」的最后一棒：把**一件活还能被当成一个地点**这件事拆干净。此前一条活虽然已经是 `tasks` 表的一行，但它的 id 仍然是一个地址——`/blocks`、`/doc`、`/usage`、`/terminal` 全都认它，界面上点开它就跳到一个新页面。它不该是：做这条活的是房间会话里的一个分身，它没有名册、没有归档、没有自己的一轮，也没有为它签的 token。

## 现在的状态：代码全部写完，检查全绿，**等最后一次全量复跑**

分支 `topic/80027df3`，平台仓与 GitHub 双侧同步。

## 做了什么

**后端：地址空间只剩房间**
- `PlaceResolver` 不再看 `tasks` 表，`Place` 只剩「房间 + 它当前写的那棵树」。`room_and_task()` 整个删除——需要成对存的表（blocks/agent_turns/agent_sessions/usage/cards/progress）现在从**分身事件的归属**拿到卡的那一半，那是唯一真知道它的地方。
- `tasks.agent_instance_id` 列删除 + 迁移 `c8f21d4a7e93`（HEAD 已跟上）。一条活没有第二个身份：会话是房间的，agent 也就是房间的，卡上记的是**哪个分身**在做（`subagent_id`）。
- 补了三条房间寻址的路，替代原来只能用活的 id 走的口子：`GET /topics/{room}/tasks/{card}`（卡 + 它的时间线 + 看板那一格）、`POST .../messages`（在卡下面说话：落在这条活的时间线上，唤醒房间转达）、`POST .../claim`（替一个分身声明路径，这条会被记下来；房间自己那条只查不记）。
- 顺带删掉了随地址一起失效的东西：递卡的「支线不能递」那道判断（现在由地址空间保证）、`thread_at`/`_run_summoned` 的转达分支、一条支线 prompt 里的「房间背景」整套（只有支线轮次会用到，而支线轮次不存在了）。

**前端：卡按卡渲染**
- `lib/place.ts`（把 task 伪装成 topic 的适配层）整个删除。点开一张卡不再离开房间：地址停在房间上、带 `?card=`，总览那一格往下钻一层显示这张卡（标题/状态词/负责人/简报/结论/它自己的对话/输入框），左上角「看板」退回去。项目级看板和话题时间线上的跳转都改成「房间 + card query」。
- 侧栏只列房间。一件活在界面上的位置是它房间的看板和项目级那块板——**状态区（简报里当作待办的那条）此前已经存在**（`PanelOverview` 顶部的 `TaskProgress`，读的就是 presentation），核实后没有重做。
- 清了死标签与过期文案：`create_subtopic`/`return_conclusion` 两个不存在的工具标签（后端 chat.py + 前端 toolLabels/PanelSite）、`cx_types` 的 `residency?`/`queued_at?`、`accept-request` 帮助里「递卡后平台自动跑检查、红了打回」那段（机器闸门早退役）。

**两个真 bug（不是形状问题，是删掉拆分之后暴露出来的）**
1. 归档级联把**活的 id 当 topic_id** 写块，撞外键 → 整个归档 500。已修；同时把「房间归档时收掉挂在它卡上的旧验收卡」并进房间那一次收，否则一张 legacy `pr_open` 会活过归档、轮询器继续推它。
2. 解上游冲突的派活返回 `{topic_id: 卡的 id}`——那不是地址。改成 `{room_id, task_id}`。

## 检查结果

- 后端 unit：**3585 passed / 1 skipped / 0 failed**
- 后端 integration：**1787 passed / 23 skipped**，2 条失败——`test_market_api`（既有环境项，无模型凭据）和 `test_turn_exit_paths::test_session_pointer_survives_a_real_sigkill`（跑到它时平台把工作区删了，子进程 exec 拿到 255）。**后者正在重跑确认**。
- ruff / ruff format / pyright：0
- 前端：vue-tsc 0；vitest **89 文件 780 passed**；eslint 0 error；stylelint ratchet 65/65（基线）；tsc ratchet 0/0
- 守卫：repo-rules PASS、action-pins PASS、migration-fork PASS（单头 + HEAD 名字对得上）

第一轮 integration 全量 70 条红，逐条查下来只有上面那 1 条是真 bug；其余 68 条全是测试在断言旧形状，按新设计改写了 20 个文件的断言（并说明每一条改的是什么）。

## 环境上的事（值得记）

**平台在这一棒里把工作区删了三次**（整个系列第四、五、六次）。每次都是 tracked 文件连同 `.git` 一起消失、gitignored 的东西留着 = worktree 被拆除。**代码零丢失**，因为每完成一小块就 commit + 双侧 push；丢的只有依赖（backend `.venv` 每次十几分钟、frontend `node_modules` 每次二十分钟，还遇上 npm registry 超时要重试）。

## 待办

- [ ] 重装依赖后重跑 `test_session_pointer_survives_a_real_sigkill`，再跑一次全量 integration 确认
- [ ] `cheese conclude` 逐条对验收标准给证据

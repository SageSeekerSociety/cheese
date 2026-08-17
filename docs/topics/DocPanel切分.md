# DocPanel 切分

## 目标
把 4559 行的 <&frontend/src/components/DocPanel.vue> 切成 **Tab 容器 + 四个 tab 组件**（文档 / 现场 / 改动 / 预览），纯搬迁零功能改动，同时把 <&frontend/src/views/workspace/TopicView.vue> 的三套头部并成一条、采纳卡抽成独立组件。这张卡的产出物是**一条画对了的接缝**——后面四张并行卡各自独占一个 tab 文件，seam 画错代价 ×4。

## 基线（已核实 2026-08-17）
`main@upstream` = `a9e6e385`（#524），含 #520 / #521，**不含 #526**（简报要求的那个）。
查过 #526 的改动面：只碰 `TopicSidebar.vue` / `lib/topicTree.ts` / `cx_types.ts` / `stores/workspace.ts`。前两个是本卡禁区；`cx_types.ts` 是纯新增类型；`stores/workspace.ts` 只改了一段讲排序的注释（+7-5），离 `chatPct` 很远。**与本卡零重叠**，所以不停工、不 ask，按 #524 基线做；三方合并不会吃掉 #526。

## 简报的一处更正
简报说「DocPanel 零组件测试」——实际有 <&frontend/src/components/__tests__/DocPanelFiles.test.ts>（6 条，测文件抽屉的跨话题串写/只读/冲突）。它叫 `.test.ts` 不叫 `.spec.ts`，所以没被 grep 到。这是现成的安全网：搬迁后它必须仍然绿。

## 我画的接缝
```
TopicView.vue
├─ TopicHeader.vue        新：一条话题头（标题·#id·状态标·连接点·成员堆·用量 popover·专注）
├─ ChatPanel.vue          新增 hideHeader prop（DmView 不受影响）
├─ TopicAcceptCard.vue    新：采纳/闸门/交付/已采纳四张卡
└─ WorkPanel.vue          Tab 容器（DocPanel.vue 改名）
   ├─ panels/PanelDoc.vue      props: topic, activityTick, topicList, active
   │                           emits: open-topic, mention-click, comment-intent, open-file
   │                           expose: pulse, highlightTurn, refreshComments
   ├─ panels/PanelSite.vue     props: topic, worklog, working, workingSince, active
   ├─ panels/PanelChanges.vue  props: topic, active, refreshTick ｜ expose: openFile
   └─ panels/PanelPreview.vue  props: topic, active, refreshTick ｜ emits: loaded(artifactId)
```
- **共享状态只剩两样，都住在容器**：`activeTab`（切 tab、`open-file` 落到改动 tab）和预览的「有新内容」指针（`latest`/`seen`）——因为它是**信号**，属于后面「信号规则 + URL」那张卡的地盘。
- **一轮活干完的刷新信号**收敛成一个 `refreshTick`：容器 watch `working` 下降沿 +1，改动/预览各自据此静默重刷。`working` / `worklog` / `workingSince` 仍然只有现场用。
- **每个 tab 自带自己的工具条**（文档的保存状态/只读/源码、预览的新标签页/全屏/刷新、改动的刷新），所以四张卡各改各的文件，互不碰。

## 状态
开工中。

## 下一步
1. 写四个 tab 组件 + 容器 + TopicHeader + TopicAcceptCard
2. 补表征测试（tab 能切、`<&path>` chip 落到改动 tab、预览已看过、现场跟随滚动）
3. 门禁：lint / lint:style / vitest 全绿，typecheck + build 试一次

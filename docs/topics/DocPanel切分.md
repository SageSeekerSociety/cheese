# DocPanel 切分

## 目标
把 4559 行的 `DocPanel.vue` 切成 **Tab 容器 + 四个 tab 组件**（文档 / 现场 / 改动 / 预览），纯搬迁零功能改动；同时把 <&frontend/src/views/workspace/TopicView.vue> 的三套头部并成一条、采纳卡抽成独立组件。这张卡的产出物是**一条画对了的接缝**——后面四张并行卡各自独占一个 tab 文件。

## 状态：改完了，门禁全绿，等递卡

## 落成的接缝
```
TopicView.vue                    1473 → 799
├─ TopicHeader.vue          新   235   标题·#id·状态标·连接点·成员堆·用量 popover·专注
├─ ChatPanel.vue            +1 prop (hideHeader)；DmView 不受影响
├─ TopicAcceptCard.vue      新   692   闸门/待采纳/交付中/已采纳四张卡 + 两个轮询
└─ WorkPanel.vue            新   295   Tab 容器（DocPanel.vue 改名 + 掏空）
   ├─ panels/PanelDoc.vue        2755  编辑器/源码模式/评论区/slash 菜单
   ├─ panels/PanelSite.vue        520
   ├─ panels/PanelChanges.vue     823  Git + 文件（内部分段，下一张卡合并）
   └─ panels/PanelPreview.vue     452
lib/topicState.ts           新    25   状态标文案，头部和 ChatPanel 共用
```
- **容器只管两样**：`activeTab`，和「预览有新内容」这个**信号**（必须在 tab 关着时也算得对，所以不能住在 PanelPreview 里）。其余全在各自 tab 的 SFC 里。
- **跨 tab 只剩一根线**：`<&path>` chip → PanelDoc 的 `open-file` → 容器 → PanelChanges.openFile。
- **一轮活干完**收敛成一个 `refreshTick`（容器 watch `working` 下降沿），改动/预览各自静默重刷；`worklog`/`working`/`workingSince` 仍然只有现场用。

## 顺带清掉的
钉住/浮动双形态、`cheesex.toolPinned`、`cheesex.toolWidth`（第二个宽度滑块）、抽屉遮罩、`z-index: 2400` 的手搓全屏层（改用 `v-dialog fullscreen`，Esc 归 Vuetify）、成员栏那两个魔法数（`height:45px` / `right: calc(…+92px)`）、资源抽屉的 20 秒轮询（改成头部 popover 打开时拉一次）、`topics-changed` 这个从未被 emit 过的事件。全树 grep 无残留。

## 简报的两处更正
1. **基线**：`main@upstream` = `a9e6e385`（#524），**不含 #526**。查过 #526 的改动面（TopicSidebar / topicTree / cx_types / stores/workspace 的一段注释），与本卡零重叠，故未停工。
2. **「DocPanel 零组件测试」不成立**：`components/__tests__/` 下有三个（Files 6 条、Preview 5 条、Site 3 条），叫 `.test.ts` 不叫 `.spec.ts` 所以没被 grep 到。全部原样搬到 WorkPanel 上，断言一条没改——这是本卡最硬的等价证据。

## 验证（都在沙箱里真跑过）
- `vitest run --dir src`：**57 文件 / 494 用例全绿**
- `pnpm run lint`：0 error（286 warning 是既有基线）
- `pnpm run lint:style`：绿，基线 **69 → 65**（只降不升）
- `pnpm run typecheck`（vue-tsc）：0 error
- `pnpm run build`：成功（这台机器 62G/16 核，跑得动）
- **没做**：两个主题的真机目视。见下。

## 留给后面的卡
- `PanelDoc` 的 `.doc-comments__avatar` 还是 `#fff` + `#8a94a3` 两个字面色，同一处缺陷在 TopicView 的 `.mention-avatar` 上已按 `--surface`/`--muted` 修过。本轮纯搬迁没动，加了带理由的 stylelint 豁免注释。
- 滚动层：文档 1 层、现场 1 层、预览 0 层（iframe 自己滚）；改动 = 1 层外层 + 文件树/编辑器各自内部滚动（两栏浏览器的固有结构）。

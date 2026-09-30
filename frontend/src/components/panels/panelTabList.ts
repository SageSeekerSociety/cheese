// 工作面板那几格固定页签是**哪几格、叫什么、挂哪个图标**。它和页签条本身
// （`PanelTabs.vue`）分开放，是因为问这句话的有两处：产品的话题页（`WorkPanel`）
// 和文档里的动态演示（`views/demo/DemoRoom.vue`）。演示照着同一份表画，页签上写
// 哪几个字就只有一个出处——改名字不会漏掉演示，也不用在演示里抄一遍。
// 产品比演示多一格（`ROUTINES_TAB`，见下面 `workPanelTabs`）：演示画不出来的那一格
// 不放进共用表，才不会变成演示里的一格假的。
//
// 页签条的**画法**在 `PanelTabs.vue`，它只管画和量；这一份只管「有哪几格」。

export type TabKey = 'chat' | 'overview' | 'site' | 'changes' | 'preview' | 'routines'

export interface TabDef {
  key: TabKey
  label: string
  icon: string
}

export const ALL_TABS: TabDef[] = [
  { key: 'chat', label: '对话', icon: 'mdi-message-outline' },
  // 文档 和 任务 合成了一格。它们回答的是同一个问题的两半——「这个房间在干什么」
  // ——分成两格意味着看完一半得先想起来还有另一半，于是大多数人只看文档，房间里
  // 有几条活在跑就没人知道。
  { key: 'overview', label: '总览', icon: 'mdi-file-document-outline' },
  { key: 'site', label: '现场', icon: 'mdi-hammer-wrench' },
  { key: 'changes', label: '改动', icon: 'mdi-source-branch' },
  { key: 'preview', label: '预览', icon: 'mdi-eye-outline' },
]

/** 这一屏上有哪几格。手机上对话自己是一格，桌面上对话在左边那一栏里，不在面板上。 */
export function panelTabs(withChat: boolean): TabDef[] {
  return ALL_TABS.filter((t) => t.key !== 'chat' || withChat)
}

// 「定时与触发」只有产品有，所以它不在上面那张共用表里：演示讲的是房间里的四件事
// （文档、芝士怎么干活、改动、结果），规则那一格要连后端才画得出来，塞进演示只会
// 多一格假的。于是它接在共用表后面，由 `workPanelTabs` 拼出话题页真正的那几格 ——
// 演示照旧读 `panelTabs`，两边的四格仍然只有一个出处。
export const ROUTINES_TAB: TabDef = { key: 'routines', label: '定时与触发', icon: 'mdi-clock-outline' }

/** 话题页右侧有哪几格：共用的那几格 + 只有产品有的「定时与触发」。 */
export function workPanelTabs(withChat: boolean): TabDef[] {
  return [...panelTabs(withChat), ROUTINES_TAB]
}

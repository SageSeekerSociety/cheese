// 工作面板那几格固定页签是**哪几格、叫什么、挂哪个图标**。它和页签条本身
// （`PanelTabs.vue`）分开放，是因为问这句话的有两处：产品的话题页（`WorkPanel`）
// 和文档里的动态演示（`views/demo/DemoRoom.vue`）。演示照着同一份表画，页签上写
// 哪几个字就只有一个出处——改名字不会漏掉演示，也不用在演示里抄一遍。
// 产品比演示多一格（`ROUTINES_TAB`，见下面 `workPanelTabs`）：演示画不出来的那一格
// 不放进共用表，才不会变成演示里的一格假的。
//
// 页签条的**画法**在 `PanelTabs.vue`，它只管画和量；这一份只管「有哪几格」。

import { t } from '@/i18n'

export type TabKey =
  | 'chat'
  | 'overview'
  | 'threads'
  | 'files'
  | 'site'
  | 'changes'
  | 'preview'
  | 'routines'
  | 'code'
  | 'terminal'

export interface TabDef {
  key: TabKey
  label: string
  icon: string
}

// 名字是 getter：每次读都按当前语言取，切换语言后页签跟着变。
function tab(key: TabKey, icon: string): TabDef {
  return {
    key,
    icon,
    get label() {
      return t(`work.room.tabs.${key}`)
    },
  }
}

export const ALL_TABS: TabDef[] = [
  tab('chat', 'mdi-message-outline'),
  // 文档 和 任务 合成了一格。它们回答的是同一个问题的两半——「这个房间在干什么」
  // ——分成两格意味着看完一半得先想起来还有另一半，于是大多数人只看文档，房间里
  // 有几条活在跑就没人知道。
  tab('overview', 'mdi-file-document-outline'),
  tab('site', 'mdi-hammer-wrench'),
  tab('changes', 'mdi-source-branch'),
  tab('preview', 'mdi-eye-outline'),
]

/** 这一屏上有哪几格。手机上对话自己是一格，桌面上对话在左边那一栏里，不在面板上。 */
export function panelTabs(withChat: boolean): TabDef[] {
  return ALL_TABS.filter((tabDef) => tabDef.key !== 'chat' || withChat)
}

// 「例行事务」只有产品有，所以它不在上面那张共用表里：演示讲的是房间里的四件事
// （文档、芝士怎么干活、改动、结果），规则那一格要连后端才画得出来，塞进演示只会
// 多一格假的。于是它接在共用表后面，由 `workPanelTabs` 拼出话题页真正的那几格 ——
// 演示照旧读 `panelTabs`，两边的四格仍然只有一个出处。
export const ROUTINES_TAB: TabDef = tab('routines', 'mdi-clock-outline')

// 频道里主线消息下面的支线，一条一行。同样只有产品有：演示里的房间没有支线。
export const THREADS_TAB: TabDef = tab('threads', 'mdi-forum-outline')

// 这段对话里的附件和文档（#3828）：默认只属于这段对话，存进资料库才进项目。
export const FILES_TAB: TabDef = tab('files', 'mdi-paperclip')

// 任务的工作副本（芝士在里面干活的那一份）：人在这里浏览、编辑文件。和频道的「文件」同名，
// 两格不会同时出现（任务没有频道那一格）。
export const CODE_TAB: TabDef = {
  key: 'code',
  icon: 'mdi-folder-outline',
  get label() {
    return t('work.room.tabs.files')
  },
}

// 任务里人自己的终端：每一格是一个 shell，读者从「+」里加，加几格就有几个。只有桌面上的任务页有，
// 所以它不在上面那几张表里，由工作面板按需摆出来（`WorkPanel` 的 `terminalMeta`）。
export const TERMINAL_TAB: TabDef = tab('terminal', 'mdi-console')

/** 话题页右侧有哪几格：共用的那几格，总览后面接「支线」「文件」，最后是「例行事务」和任务的文件。 */
export function workPanelTabs(withChat: boolean): TabDef[] {
  const shared = panelTabs(withChat)
  const at = shared.findIndex((tabDef) => tabDef.key === 'overview') + 1
  return [...shared.slice(0, at), THREADS_TAB, FILES_TAB, ...shared.slice(at), ROUTINES_TAB, CODE_TAB]
}

// 频道是人说话的地方，没有电脑在它名下干活：只有概览、支线、文件、例行事务。任务没有
// 支线、频道的文件和例行事务，有它自己的文件（工作副本），其余几格都有。在频道里打开的
// 一件任务：它的对话、改动、预览。
const CHANNEL_TABS: ReadonlySet<string> = new Set(['chat', 'overview', 'threads', 'files', 'routines'])
const TASK_HIDDEN_TABS: ReadonlySet<string> = new Set(['routines', 'threads', 'files'])
const REVIEW_TABS: ReadonlySet<string> = new Set(['chat', 'changes', 'preview'])

/**
 * 一个房间的工作面板有哪几格：频道、任务页、在频道里打开的一件任务（`review`）各不同。
 * `withChat`：对话是这一排里的一格（手机上，和打开的任务里）。
 */
export function roomTabs(opts: { withChat: boolean; review: boolean; task: boolean }): TabDef[] {
  return workPanelTabs(opts.withChat || opts.review).filter((tab) =>
    opts.review ? REVIEW_TABS.has(tab.key) : opts.task ? !TASK_HIDDEN_TABS.has(tab.key) : CHANNEL_TABS.has(tab.key)
  )
}

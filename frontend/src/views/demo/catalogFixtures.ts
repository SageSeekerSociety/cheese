/**
 * 预览站喂给组件的数据。
 *
 * 全部是产品里真会出现的形状，没有「随便来一条」：
 *
 *   - 房间那几条（消息、通知、验收卡）从演示剧本算出来，和 `DemoRoom` 走同一条
 *     管线（`frameAt` + `collapseNotices`）—— 预览站上演的正是文档里的那些话；
 *   - 看板那几张卡抄自看板页真正的调用方（`AdminDashboardPage`），行的形状、
 *     口径句、时间戳的写法都一样；
 *   - 剩下几条是产品里会遇到、剧本里没演到的那几格（长正文、没送出去的消息、
 *     空列表、首次加载），照各组件的 props 造。
 *
 * 这里只出数据（外加两个把行翻成 props 的小函数）。哪一份配哪个组件、每个组件
 * 看哪几格，在 `catalog.ts`。
 *
 * **这一份现在只是个薄入口**：夹具按数据域分在 `./catalogFixtures/` 下面 ——
 * `common`（剧本那一刻与折行的那三条管线）、`room`（房间那几格）、`accept`
 * （验收卡）、`dashboard`（看板）、`navigation`（外壳那几条清单）、`feedback`
 * （反馈一行与学习摘录）、`rail`（项目侧栏）、`panels`（工作面板三格）、`chat`
 * （对话栏）。从这里出去的名字和拆分之前一模一样，调用方一行都不用改。
 */
export {
  ACCEPT_APPROVALS,
  ACCEPT_BLOCKED,
  ACCEPT_CARD,
  ACCEPT_CHECKS,
  ACCEPT_CONFLICTED,
  ACCEPT_DELIVERING_ONE,
  ACCEPT_GATE_BLOCKED,
  ACCEPT_GATE_FAILED,
  ACCEPT_NOTE_ERROR,
  ACCEPT_NOTE_INFO,
  ACCEPT_ONE,
  ACCEPT_REVIEWERS,
  acceptChecks,
  acceptHeadProps,
  installCatalogAnswers,
} from './catalogFixtures/accept'
export { CHAT_ROWS, CHAT_TOPIC, chatTimelineProps } from './catalogFixtures/chat'
export type { RoomRow } from './catalogFixtures/common'
export { AGENT_NAME, ROOM_REFS } from './catalogFixtures/common'
export { ACTION_ROWS, ADMIN_QUEUE, BAR_ROWS, BAR_ROWS_LONG, KPI_STATES, NUMBER_ROWS } from './catalogFixtures/dashboard'
export { EXCERPTS, FEEDBACK_ROWS } from './catalogFixtures/feedback'
export { COMPUTE_DEVICES, NAV_ITEMS, OPEN_FILES, SHEET_ACTIONS } from './catalogFixtures/navigation'
export {
  CHANGES_EMPTY,
  changesPanelProps,
  DOC_TOPIC,
  docPanelProps,
  docSession,
  NO_REPO,
  NOTHING_CHANGED,
  PREVIEW_EMPTY,
  previewPanelProps,
} from './catalogFixtures/panels'
export { RAIL_ACTIONS, RAIL_MARKS, RAIL_PAGES, RAIL_ROOT_TOPIC, RAIL_ROWS, RAIL_TERMS } from './catalogFixtures/rail'
export {
  ACCEPT_DONE,
  ASK_ANSWERED,
  ASK_OPEN,
  CARD_FILED,
  CHAT_ROWS_FULL,
  CHEESE_LINES,
  isMineWang,
  LONG_ROW,
  ROOM_MENTION_POOL,
  roomMessageProps,
  roomNoticeProps,
  TURN_SUMMARY,
  WANG_CLOSING,
  WANG_EDITING_ROW,
  WANG_LINES,
} from './catalogFixtures/room'

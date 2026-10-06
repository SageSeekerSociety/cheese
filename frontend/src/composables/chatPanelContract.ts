// The contract between the room panel and whoever renders it: the events it
// raises, and the handful of accessors it reads off their props.
//
// Both sides have to agree on these, so they live in one place of their own —
// the panel itself is the socket, the window of history and the timers, and a
// list of events is neither.
import type { AgentControlState, Block, ProjectMemberRow, Topic } from '../cx_types'
import type { DocReviewRequest } from '../lib/docReview'
import type { OpenedDocument } from '../lib/docReview'
import type { MemberActivityLine } from '../lib/memberActivity'

/** The events this panel surfaces to whoever owns the address it is rendered at. */
// Surface AI activity so the parent can refresh the living doc / topic list
// without a manual reload (spec §7.1 实时联动). `turn-done` fires when a turn
// completes.
export interface ChatPanelEmit {
  // 标题左边那颗 ← 被按了。去哪儿由拥有这个地址的人决定，不是这里。
  (e: 'back'): void
  // A cheese command changed a platform resource (doc/topics/...) —
  // the parent refreshes that panel live, mid-turn.
  (e: 'state-changed', resource: string): void
  // 芝士摆出来一份东西（`cheese show` / `cheese serve`）：房间里多了一块 kind=artifact
  // 的卡，当前预览跟着它换。对话栏是这条 socket 的家，面板自己听不到，所以往上报一
  // 声，面板据此立刻去问一次指针——而不是等下一次轮询（那要十几秒）。
  (e: 'preview-shown'): void
  (e: 'turn-done'): void
  // 芝士 是不是正在这个话题里干活。跟着轮次生命周期走（summon / turn_started /
  // turn_active 开，turn_finished / done / error 关），不是跟着它第一次动手
  // 走：干出来的东西是干活的**证据**，不是干活的**开始**，而右边那格「现场」得
  // 在开工那一刻就在那儿——它就是用来看它在干什么的。
  (e: 'working', working: boolean): void
  // 此刻谁在这个房间里忙（打字的人、干活的队友），已经配好名字和此刻那一步。
  // 现场那一格画同一份（`MemberActivity`）。
  (e: 'activity', lines: MemberActivityLine[]): void
  // 会话状态（任务、模型）动了：socket 上的这一帧转给现场那格的会话详情。
  (e: 'agent-control', state: AgentControlState): void
  // 现场那格的时间线上多了一行，或者已有的一行变了（挂了、重试次数涨了）。socket
  // 在这一栏，现场自己听不到。
  (e: 'site-block', block: Block): void
  // 正在跑的轮次，各自从什么时候开始（毫秒）。现场靠它分出哪一组还在进行。
  (e: 'site-turns', turns: Record<string, number>): void
  // 一位 AI 队友在某条支线里开始或停下回答（主线上那一行写「正在回复」）。
  (e: 'thread-activity', threadId: string, member: string, active: boolean): void
  /** 支线里的一条运行记录：那条消息下面那一行写队友在等什么。 */
  (e: 'thread-status', threadId: string, record: Block): void
  // 主线上一条消息的支线：「在支线中回复」，或者点了它下面那一行。
  (e: 'open-thread', block: Block): void
  /** 支线里一条 AI 队友的回复：看它那一轮的过程。 */
  (e: 'open-process', turnId: string): void
  // 转为任务：the parent turns this message into a task of the channel.
  (e: 'upgrade-message', messageId: string): void
  // Open the topic an upgraded block points to (the 活引用 back-link).
  (e: 'open-topic', topicId: string): void
  // A task in this room (dispatched marker, upgraded message, <#task> chip): opens its card here.
  (e: 'open-card', taskId: string): void
  // A clicked @mention chip (resolved by the parent: person → member page,
  // topic/doc → open that topic).
  (e: 'mention-click', name: string): void
  // A <&path> file chip was clicked — the parent opens it in the 文件 drawer.
  (e: 'open-file', path: string): void
  // An action card's button (doc → highlight the turn, or review the changes
  // someone asked the agent for; changes → diff tab…).
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument): void
}

/** Everything the panel reads off its props, as accessors: a composable is not
 *  re-run when a prop changes, it reads the current value when it needs it. */
export interface ChatPanelOptions {
  topic: () => Topic | null
  /** 读的是这个房间里的一段别的对话（一个任务）：消息、连接、发送都走它；名册、
   *  附件仍是房间的。没有就是房间自己。 */
  conversationId?: () => string | null
  alwaysSummon: () => boolean
  showComposer: () => boolean
  members: () => ProjectMemberRow[]
  topicList: () => Topic[]
  unreadOnOpen: () => number
  focusBlock: () => string | null
  emit: ChatPanelEmit
}

// 视图拿到的那一个扁平对象：面板对外长什么样，就是这一份。
//
// 为什么单独一篇：这一包八十来个字段是按「视图怎么用它」排的（头部、时间线、发件箱、
// 输入框、往上发的事件），和上面每一块按「谁负责什么」分家的方式正好是两回事。把它留在
// 门面里，两套顺序就挤在一处；挪出来之后，门面只回答「怎么拼起来」，这里只回答「拼出
// 来的是什么」——字段名、顺序、类型和拆之前一模一样，调用方一个字节都不用动。
//
// 底下那几块各自返回自己的一包（`people`、`rows`、`outgoing`……），这里按名字取用；只
// 有跨块的粘合（`hiddenRows`、`agentFaces`、`typingRows` 这类换了名字的）才在下面写。
import type { ComputedRef, Ref } from 'vue'
import type { useChatRowActions } from '../../components/chat/composables/useChatRowActions'
import type { useTimelineMotion } from '../../components/chat/composables/useTimelineMotion'
import type { useChatScroll } from '../../components/room/composables/useChatScroll'
import type { useRoomTurns } from '../../components/room/composables/useRoomTurns'
import type { useRowBatch } from '../../components/room/composables/useRowBatch'
import type { useTimeline } from '../../components/room/composables/useTimeline'
import type { useTypingPreview } from '../../components/room/composables/useTypingPreview'
import type { Topic } from '../../cx_types'
import type { useChatComposer } from '../useChatComposer'
import type { useChatMessageClicks } from '../useChatMessageClicks'
import type { useMessageReactions } from '../useMessageReactions'
import type { useOwnChecklist } from '../useOwnChecklist'
import type { usePanelActivity } from './activity'
import type { usePanelAuthorship } from './authorship'
import type { usePanelOutgoing } from './outgoing'
import type { usePanelPaging } from './paging'
import type { usePanelPeople } from './people'
import type { usePanelRows } from './rows'
import type { usePanelSocket } from './socket'

import { computed } from 'vue'

import { isAgentBlock } from '../../lib/authorship'
import { topicShortId, topicStateBadge } from '../../lib/topicState'

/** 面板对外的全部：视图读的东西，和它往上发的事件所需的那些。 */
export function usePanelSurface(parts: {
  /** 这一栏读的那段对话（房间自己，或房间里的一个任务）。 */
  topic: () => Topic | null
  /** 我自己的 handle。 */
  author: string
  people: ReturnType<typeof usePanelPeople>
  timeline: ReturnType<typeof useTimeline>
  turns: ReturnType<typeof useRoomTurns>
  rows: ReturnType<typeof usePanelRows>
  authorship: ReturnType<typeof usePanelAuthorship>
  outgoing: ReturnType<typeof usePanelOutgoing>
  socket: ReturnType<typeof usePanelSocket>
  activity: ReturnType<typeof usePanelActivity>
  paging: ReturnType<typeof usePanelPaging>
  motion: ReturnType<typeof useTimelineMotion>
  typing: ReturnType<typeof useTypingPreview>
  composer: ReturnType<typeof useChatComposer>
  rowActions: ReturnType<typeof useChatRowActions>
  clicks: ReturnType<typeof useChatMessageClicks>
  reactions: ReturnType<typeof useMessageReactions>
  scroll: ReturnType<typeof useChatScroll>
  rowBatch: ReturnType<typeof useRowBatch>
  checklist: ReturnType<typeof useOwnChecklist>
  viewer: ComputedRef<string>
  errorMsg: Ref<string | null>
  /** 往上翻着的时候来了几条。 */
  unseen: Ref<string[]>
  /** 读最新一页时骨架用：这一条由面板自己拿着，翻页也读它。 */
  loadingHistory: Ref<boolean>
}) {
  // ---- Topic header state. The labels live in lib/topicState.ts because the
  // 工作台's own topic header renders the same badge — one table, so the two can
  // never disagree about what `archived` is called.
  const prShortId = computed(() => topicShortId(parts.topic()?.id))
  const prState = computed(() => topicStateBadge(parts.topic()?.status))

  // Everything the view draws. One flat object, named after what it is: the
  // component below is a thin shell around these.
  return {
    // header
    topic: computed(() => parts.topic()),
    agentName: parts.people.naming.agentName,
    agentSeat: parts.people.naming.agentSeat,
    prShortId,
    prState,
    composerHint: parts.people.naming.composerHint,
    // timeline
    rows: parts.rows.rows,
    askGroups: parts.rows.askGroups,
    hiddenRows: parts.rowBatch.hidden,
    refMaps: parts.people.refMaps,
    awaitingReply: parts.turns.awaitingReply,
    agentFaces: parts.turns.faces,
    activityLines: parts.activity.activityLines,
    timeline: parts.timeline,
    hasMore: parts.timeline.hasMore,
    hasNewer: parts.timeline.hasNewer,
    loadingHistory: parts.loadingHistory,
    loadingOlder: parts.paging.paging.loadingOlder,
    loadingNewer: parts.paging.paging.loadingNewer,
    openAt: parts.paging.paging.openAt,
    onTimelineScroll: parts.paging.paging.onTimelineScroll,
    togglePicker: parts.reactions.togglePicker,
    scrollRef: parts.scroll.scrollRef,
    contentRef: parts.scroll.contentRef,
    atBottom: parts.scroll.atBottom,
    scrollToBottom: parts.scroll.scrollToBottom,
    dayLabels: parts.rows.dayLabels,
    unreadAnchorId: parts.rows.unreadAnchorId,
    runEdges: parts.rows.runEdges,
    outboxEdge: parts.rows.outboxEdge,
    pendingBlock: parts.authorship.pendingBlock,
    outgoingState: parts.authorship.outgoingState,
    retrySend: parts.outgoing.retrySend,
    outbox: parts.outgoing.outbox,
    typingRows: parts.typing.rows,
    noticeAgent: parts.authorship.noticeAgent,
    parentOf: parts.outgoing.parentOf,
    showReplyCue: parts.outgoing.showReplyCue,
    fmtTime: parts.authorship.fmtTime,
    isMine: parts.authorship.isMine,
    displayName: parts.people.roster.displayName,
    authorTitle: parts.people.roster.authorTitle,
    nameOfHandle: parts.people.roster.nameOfHandle,
    isExternal: parts.people.roster.isExternal,
    avatarSrc: parts.people.roster.avatarSrc,
    myName: parts.people.roster.myName,
    mentionPool: parts.people.roster.mentionPool,
    onMessagesClick: parts.clicks,
    onTimelinePointer: parts.rowActions.onTimelinePointer,
    hideBar: parts.rowActions.hideBar,
    bar: parts.rowActions.bar,
    barBlock: parts.rowActions.barBlock,
    kbdNav: parts.rowActions.kbdNav,
    reactionPickerFor: parts.reactions.reactionPickerFor,
    touchOnly: parts.rowActions.touchOnly,
    timeShownId: parts.rowActions.timeShownId,
    arrived: parts.motion.arrived,
    sentNow: parts.motion.sentNow,
    delivered: parts.motion.delivered,
    flashId: parts.motion.flashId,
    flash: parts.motion.flash,
    settleArrival: parts.motion.settleArrival,
    settleSent: parts.motion.settleSent,
    outboxLeave: parts.motion.outboxLeave,
    jumpToUnseen: parts.motion.jumpToUnseen,
    unseen: parts.unseen,
    // sheet
    sheet: parts.rowActions.sheet,
    sheetBlock: parts.rowActions.sheetBlock,
    // composer (its whole surface; the view destructures what it draws)
    ...parts.composer,
    // what the page above listens for
    errorMsg: parts.errorMsg,
    connected: parts.socket.connected,
    linkDown: parts.socket.linkDown,
    send: parts.outgoing.send,
    replyToQuestion: parts.outgoing.replyToQuestion,
    viewer: parts.viewer,
    postChecklist: parts.checklist.postChecklist,
    changeChecklist: parts.checklist.changeChecklist,
    onReact: parts.reactions.onReact,
    downloadAttachment: parts.outgoing.downloadAttachment,
    roomTasks: parts.rows.roomTasks,
    isAgentBlock,
    AUTHOR: parts.author,
    // what the host may pull out of the panel without an event
    flushComposer: parts.composer.flushComposer,
  }
}

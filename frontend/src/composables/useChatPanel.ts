// 对话栏的取数与连接：那条 socket、它守着的那扇历史窗口，和窗口底下每一个计时器与
// 缓存。
//
// 组件那一半（`components/ChatPanel.vue`）曾经是一整个 1987 行的东西：这一层取了它里
// 面取数、重连、缓存、测量的那半边，留下的组件只认 props、只管画、只往上发事件。
//
// 这一份是组合根：读 props，按顺序把各块建出来（顺序是契约的一部分——下面每一块的
// 依赖都在它上面几行，晚建的用取值函数别过去），再把 `chatPanel/surface` 拼出来的那
// 一包还回去。每块各自负责的那件事写在 `chatPanel/` 下，找它就往那儿翻：
//
//   名册与称呼            chatPanel/people.ts
//   一行谁署名、什么时间  chatPanel/authorship.ts
//   画得出来的行与分组    chatPanel/rows.ts
//   发件箱与发送          chatPanel/outgoing.ts
//   每一帧的含义          chatPanel/socket.ts
//   房间里此刻的动静      chatPanel/activity.ts
//   翻页与断线对账        chatPanel/paging.ts
//   打开一段对话          chatPanel/topicWindow.ts
//   返回的那一包是什么    chatPanel/surface.ts
//
// 完全不在这里的：输入框的判据（useChatComposer，下面只建它）、行上的指针手势
// （useChatRowActions）、逐行的进场动画（useTimelineMotion，下面只建它）、任何标记，
// 以及属于「面板被画在哪个页面」的决定。
import type { Block, Topic } from '../cx_types'
import type { ChatPanelOptions } from './chatPanelContract'

import { computed, ref, watch } from 'vue'

import { useChatRowActions } from '../components/chat/composables/useChatRowActions'
import { useTimelineMotion } from '../components/chat/composables/useTimelineMotion'
import { useChatScroll } from '../components/room/composables/useChatScroll'
import { useHistoryReads } from '../components/room/composables/useHistoryReads'
import { useRoomTail } from '../components/room/composables/useRoomTail'
import { useRoomTurns } from '../components/room/composables/useRoomTurns'
import { useRowBatch } from '../components/room/composables/useRowBatch'
import { useRunRecords } from '../components/room/composables/useRunRecords'
import { useTimeline } from '../components/room/composables/useTimeline'
import { useTypingPreview } from '../components/room/composables/useTypingPreview'
import { isPersonBlock } from '../lib/authorship'
import { rendersInRoom } from '../lib/platformNotice'
import { myHandle } from '../me'
import { currentUserName } from '../services/account'

import { usePanelActivity } from './chatPanel/activity'
import { usePanelAuthorship } from './chatPanel/authorship'
import { usePanelOutgoing } from './chatPanel/outgoing'
import { usePanelPaging } from './chatPanel/paging'
import { usePanelPeople } from './chatPanel/people'
import { usePanelRows } from './chatPanel/rows'
import { usePanelSocket } from './chatPanel/socket'
import { usePanelSurface } from './chatPanel/surface'
import { usePanelTopicWindow } from './chatPanel/topicWindow'
import { useChatComposer } from './useChatComposer'
import { useChatMessageClicks } from './useChatMessageClicks'
import { useMessageReactions } from './useMessageReactions'
import { useOwnChecklist } from './useOwnChecklist'

import { t } from '@/i18n'

// The panel and its host have to agree on the event list, so it lives on its own
// (see chatPanelContract) and is re-exported here: the view keeps importing
// everything room-shaped from one module.
export type { ChatPanelEmit, ChatPanelOptions } from './chatPanelContract'

export function useChatPanel(opts: ChatPanelOptions) {
  const { topic, alwaysSummon, showComposer, members, topicList, unreadOnOpen, focusBlock, emit } = opts
  const answersInThread = () => opts.answersInThread?.() ?? false
  // 这一栏读的那段对话：房间自己，或房间里的一个任务（`conversationId`）。消息、连接、
  // 发送、草稿走它；名册、附件、标题还是房间的（`topic()`）。
  function place(): Topic | null {
    const room = topic()
    const id = opts.conversationId?.()
    return room && id && id !== room.id ? { ...room, id } : room
  }

  const AUTHOR = myHandle()

  // 这一条出错就写给用户看，所以它得在名册（下面紧接着建）之前。
  const errorMsg = ref<string | null>(null)
  // 卸载之后还在飞的那几个请求回来时，不该再往一个已经没了的面板上写东西。
  const disposed = ref(false)
  // 往上翻着的时候别人又说了话：底部浮出一颗提示，写着来了几条。它和下面这些由面板自
  // 己拿着，因为读它的人分属好几块（连接、翻页、动画、换话题）。
  const unseen = ref<string[]>([])
  // 点了「编辑」的那几条：它们离开时先收拢自己的高度，原文回到输入框。别的离开（送达
  // 后换成落库的那一条）必须是瞬时的，否则同一句话会在屏幕上出现两遍。
  const editing = new Set<string>()
  const loadingHistory = ref(false)

  // 名册、称呼，和 chips 读的那两张表 —— 见 chatPanel/people。
  const people = usePanelPeople({
    topic,
    members,
    author: AUTHOR,
    onError: (message) => {
      errorMsg.value = message
    },
    agentHandle: opts.agentHandle,
    alwaysSummon,
  })

  // 此刻显示时间线的哪一段 —— 见 room/composables/useTimeline；rendersInRoom 只放画得
  // 出来的块进窗口，不露面的块不占额度。尾读那块（`tail`）反过来要翻页那边对账，晚建。
  const timeline = useTimeline({ renders: rendersInRoom })
  const history = useHistoryReads({ roomId: () => place()?.id, disposed: () => disposed.value })
  const tail = useRoomTail({
    roomId: () => place()?.id,
    reading: () => history.reading(),
    catchUp: (roomId, after) => void timelinePaging.resync.catchUp(roomId, after),
  })
  // 名册里没有的作者，问一次是不是注销了：注销了的署「已注销用户」—— 见 useClosedAuthors。
  people.closedAuthors.follow(() =>
    timeline.messages.value
      .filter((b) => isPersonBlock(b) && !people.roster.memberByHandle.value.has(b.author))
      .map((b) => b.author)
  )

  // 哪几轮在跑、谁在干、要不要显示「在处理」—— 见 room/composables/useRoomTurns。
  // 往上报（working / site-turns）是这里的事。
  const runRecords = useRunRecords()
  const turns = useRoomTurns({ messages: timeline.messages, records: runRecords.records })
  watch(turns.awaitingReply, (v) => emit('working', v))
  watch(turns.turnStarts, (v) => emit('site-turns', v))

  // 看着这间房的人：表情里哪几个是自己点的，靠它认。
  const viewer = computed(() => currentUserName.value ?? myHandle())
  /** 把这一条换进时间线（在的话）。清单点了记号之后要的是它：换了还得告诉尾读一遍。 */
  function replaceShown(block: Block) {
    if (!timeline.find(block.id)) return
    timeline.replace(block)
    history.note(block.id, block)
  }

  // 自己的清单：发一张、点记号改一步 —— 见 useOwnChecklist。
  const checklist = useOwnChecklist({
    topicId: () => place()?.id,
    show: replaceShown,
    fail: (e) => (errorMsg.value = e instanceof Error ? e.message : t('work.room.checklist.saveFailed')),
  })

  // ---- Emoji reactions —— 见 composables/useMessageReactions ----
  const reactions = useMessageReactions({
    find: (id) => timeline.find(id),
    errorMsg,
    pendingHistory: history.pendingReactions,
    me: AUTHOR,
  })

  // 滚动位置、跟不跟新消息、重放期间不抖 —— 见 room/composables/useChatScroll。
  const scroll = useChatScroll({
    showingNewest: () => !timeline.hasNewer.value,
    topicId: () => place()?.id,
    column: opts.column,
  })

  // 打开话题时分批挂行 —— 见 room/composables/useRowBatch（`rows`、`paging` 在下面，晚读）。
  const rowBatch = useRowBatch({
    scrollRef: scroll.scrollRef,
    atBottom: scroll.atBottom,
    restoresToBottom: scroll.restoresToBottom,
    rowIds: () => panelRows.rows.value.map((r) => r.block.id),
    onDone: () => void timelinePaging.paging.fillViewportIfNeeded(),
  })

  // 这条 socket，和每一帧对窗口意味着什么 —— 见 chatPanel/socket。它建的时候下面好几块
  // 还没出来（它们反过来靠这条连接活着），所以那几个依赖传取值函数，晚一步拿。
  const socket = usePanelSocket({
    place,
    topic,
    author: AUTHOR,
    emit,
    timeline,
    history,
    tail,
    runRecords,
    turns,
    reactions,
    errorMsg,
    awaitingReply: turns.awaitingReply,
    atBottom: scroll.atBottom,
    autoScroll: scroll.autoScroll,
    beginCatchUp: scroll.beginCatchUp,
    noteFrame: scroll.noteFrame,
    unseen,
    settleOutbox: (block) => outgoing.settleOutbox(block),
    flushOutbox: () => outgoing.flushOutbox(),
    delivered: () => timelineMotion.delivered,
    arrived: () => timelineMotion.arrived,
    activity: () => roomActivity.activity,
    liveSteps: () => roomActivity.liveSteps,
    typing: () => typing,
    roomTasks: () => panelRows.roomTasks,
    resync: () => timelinePaging.resync,
    loadTopic: (room, entering) => void topicWindow.loadTopic(room, entering),
  })

  // 发件箱、发送、回复、附件下载 —— 见 chatPanel/outgoing。
  const outgoing = usePanelOutgoing({
    place,
    topic,
    pushBlock: socket.pushBlock,
    autoScroll: scroll.autoScroll,
    scrollToBottom: scroll.scrollToBottom,
    errorMsg,
    awaitingReply: turns.awaitingReply,
    answersInThread,
    onPosted: opts.onPosted,
    timeline,
    sentNow: () => timelineMotion.sentNow,
    delivered: () => timelineMotion.delivered,
    composer: () => composer,
    paging: () => timelinePaging.paging,
  })

  // 一行署谁的名、什么时间、是不是我说的 —— 见 chatPanel/authorship。放在这里因为下面
  // 的行分组、输入框都读它（isMine 决定自己发的那条靠哪边）。
  const authorship = usePanelAuthorship({
    author: AUTHOR,
    roster: people.roster,
    naming: people.naming,
    turnAgentHandle: turns.turnAgentHandle,
    connected: socket.connected,
  })

  // 画得出来的那几行、日期线、未读线，和跟着各块一起来的任务 —— 见 chatPanel/rows。
  const panelRows = usePanelRows({
    messages: timeline.messages,
    unreadOnOpen,
    author: AUTHOR,
    isMine: authorship.isMine,
    topic,
    topicList,
    replace: (block) => timeline.replace(block),
    titles: people.topicTitles,
  })

  // 房间里此刻的动静：谁在打字、哪位队友在干活、干到哪一步 —— 见 chatPanel/activity。
  const roomActivity = usePanelActivity({
    author: AUTHOR,
    emit,
    socket,
    roster: people.roster,
    turns,
    runRecords,
  })

  // 翻页、断线对账、跳到某一条 —— 见 chatPanel/paging。`failed` 和 `flash` 分属下面两
  // 块，用取值函数晚一步拿；它们只在读失败或用户动作时才被问到，那时两块都建好了。
  const timelinePaging = usePanelPaging({
    place,
    focusBlock,
    timeline,
    history,
    tail,
    runRecords,
    rowBatch,
    scrollRef: scroll.scrollRef,
    atBottom: scroll.atBottom,
    autoScroll: scroll.autoScroll,
    scrollToBottom: scroll.scrollToBottom,
    rememberScroll: scroll.rememberScroll,
    forgetScroll: scroll.forgetScroll,
    beginOwnScroll: scroll.beginOwnScroll,
    loadingHistory,
    unseen,
    errorMsg,
    settleOutbox: outgoing.settleOutbox,
    connectSocket: socket.connectSocket,
    closeSocket: socket.closeSocket,
    connectRefused: socket.connectRefused,
    failed: (roomId, e) => topicWindow.historyReadFailed(roomId, e),
    flash: (id) => timelineMotion.flash(id),
  })

  // 每一条进场/退场怎么动、哪几条要闪 —— 见 components/chat/composables/useTimelineMotion。
  const timelineMotion = useTimelineMotion({
    atBottom: scroll.atBottom,
    hasNewer: timeline.hasNewer,
    unseen,
    editing,
    scrollToBottom: scroll.scrollToBottom,
    backToNewest: () => timelinePaging.paging.backToNewest(),
  })

  // 队友正在写的那条 —— 见 room/composables/useTypingPreview。
  const typing = useTypingPreview({
    topic,
    hasNewer: timeline.hasNewer,
    outbox: outgoing.outbox,
    visible: panelRows.visible,
    arrived: timelineMotion.arrived,
    delivered: timelineMotion.delivered,
  })

  const composer = useChatComposer({
    topic: place,
    ownLine: () => place()?.id === topic()?.id,
    alwaysSummon,
    showComposer,
    rows: panelRows.rows,
    blocks: panelRows.visible,
    hasMore: timeline.hasMore,
    loadingHistory,
    awaitingReply: turns.awaitingReply,
    outbox: outgoing.outbox,
    errorMsg,
    agentSeat: () => people.naming.agentSeat.value ?? undefined,
    refs: people.refMaps,
    timeline,
    editing,
    isMine: authorship.isMine,
    displayName: people.roster.displayName,
    send: outgoing.send,
    dropSend: outgoing.dropSend,
  })
  // The composer hands back its whole surface (see chatPanel/surface): the panel
  // reads a few of those refs itself, the view destructures the rest.
  // 我在输入框里打字，房间里的人看得见（useRoomActivity）。
  watch(composer.draft, (text) => roomActivity.activity.composing(text))

  // 行上的指针手势：长按出的那张卡片、时间、选中条 —— 见 useChatRowActions。
  const rowActions = useChatRowActions({
    timeline,
    scrollRef: scroll.scrollRef,
    contentRef: scroll.contentRef,
    rows: panelRows.rows,
    reactionPickerFor: reactions.reactionPickerFor,
    editingId: composer.editingId,
    // 首屏的行还在分批挂（useRowBatch）时，顶上那几条还没挂上，不是真的到了顶。
    loadOlder: () => void (rowBatch.pending.value || timelinePaging.paging.loadOlder()),
  })

  // 消息区里点到的 chip / 头像落到哪个事件 —— 见 composables/useChatMessageClicks。
  const clicks = useChatMessageClicks({
    reactionPickerFor: reactions.reactionPickerFor,
    touchOnly: rowActions.touchOnly,
    toggleTime: rowActions.toggleTime,
    rows: () => panelRows.rows.value,
    isTask: (id: string) => panelRows.roomTasks.known.value.has(id),
    emit,
  })

  // 换一段对话时：清干净、摆缓存、读最新一页，并看着它换 —— 见 chatPanel/topicWindow。
  // 它一建出来就立刻读一次（watch 的 immediate），所以摆在最后：那时上面每一块都在了。
  const topicWindow = usePanelTopicWindow({
    place,
    focusBlock,
    disposed,
    timeline,
    history,
    tail,
    runRecords,
    turns,
    rowBatch,
    reactions,
    errorMsg,
    loadingHistory,
    unseen,
    editing,
    unreadAnchorId: panelRows.unreadAnchorId,
    placeUnreadAnchor: panelRows.placeUnreadAnchor,
    atBottom: scroll.atBottom,
    autoScroll: scroll.autoScroll,
    restoreScroll: scroll.restoreScroll,
    rememberScroll: scroll.rememberScroll,
    connectSocket: socket.connectSocket,
    closeSocket: socket.closeSocket,
    connectRefused: socket.connectRefused,
    retryLater: socket.retryLater,
    outbox: outgoing.outbox,
    settleOutbox: outgoing.settleOutbox,
    flushOutbox: outgoing.flushOutbox,
    pauseOutbox: outgoing.pauseOutbox,
    activity: () => roomActivity.activity,
    liveSteps: () => roomActivity.liveSteps,
    typing: () => typing,
    rowActions: () => rowActions,
    motion: () => timelineMotion,
    composer: () => composer,
    paging: () => timelinePaging.paging,
  })

  return usePanelSurface({
    topic,
    author: AUTHOR,
    people,
    timeline,
    turns,
    rows: panelRows,
    authorship,
    outgoing,
    socket,
    activity: roomActivity,
    paging: timelinePaging,
    motion: timelineMotion,
    typing,
    composer,
    rowActions,
    clicks,
    reactions,
    scroll,
    rowBatch,
    checklist,
    viewer,
    errorMsg,
    unseen,
    loadingHistory,
  })
}

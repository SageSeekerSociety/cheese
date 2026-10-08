// The room half of ChatPanel: the socket, the window of history it holds, and
// every timer and cache that window is built from.
//
// ChatPanel used to be one 1987-line component: this module is the part of it
// that fetches, reconnects, caches and measures; the component that is left
// takes props, draws, and emits. The seam is the same one RoomMessage draws
// around a row — the composable knows what the room is doing, the view knows
// what it looks like.
//
// What lives here: the roster and the turns, the timeline window and its paging
// (listBlocks is called from useChatPaging, loadTopic and roomResync),
// the socket frames and what each one means for the window, the scroll position
// policy, the unread/received animation sets, and the error banner. What
// does not: the composer (useChatComposer), the pointer affordances on a row
// (useChatRowActions), the per-row entrance animations (useTimelineMotion), any
// markup, and the decisions that belong to the page a panel is rendered from.
import type { Block, ChatAttachment, Topic, WsServerFrame } from '../cx_types'
import type { Outgoing } from '../lib/composerDrafts'
import type { NoticeAgent } from '../lib/platformNotice'
import type { QuotedContext } from '../lib/quotedContext'
import type { ChatPanelOptions } from './chatPanelContract'

import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { scrollBehavior } from '@/utils/motion'

import { ApiError, attachmentRawUrl, downloadFile, ensureFreshToken, isRetryableGetFailure, listBlocks } from '../api'
import { postChatMessage } from '../api/messages'
import { useChatRowActions } from '../components/chat/composables/useChatRowActions'
import { useTimelineMotion } from '../components/chat/composables/useTimelineMotion'
import { useActivityLines } from '../components/room/composables/useActivityLines'
import { useChatScroll } from '../components/room/composables/useChatScroll'
import { useErrorFade } from '../components/room/composables/useErrorFade'
import { useHistoryReads } from '../components/room/composables/useHistoryReads'
import { useLiveSteps } from '../components/room/composables/useLiveSteps'
import { SendRefused, useOutbox } from '../components/room/composables/useOutbox'
import { useRoomActivity } from '../components/room/composables/useRoomActivity'
import { useRoomRoster } from '../components/room/composables/useRoomRoster'
import { useRoomSocket } from '../components/room/composables/useRoomSocket'
import { useRoomTail } from '../components/room/composables/useRoomTail'
import { useRoomTurns } from '../components/room/composables/useRoomTurns'
import { useRowBatch } from '../components/room/composables/useRowBatch'
import { runRecordOf, threadStatusOf, useRunRecords } from '../components/room/composables/useRunRecords'
import { useTimeline } from '../components/room/composables/useTimeline'
import { useTypingPreview } from '../components/room/composables/useTypingPreview'
import { isAgentBlock, isAgentHandle, isPersonBlock } from '../lib/authorship'
import { cachedWindow, pendingBlockRefresh, setCachedWindow } from '../lib/blockCache'
import { applyLiveChanges, mergeRefreshedTail, PAGE_SIZE } from '../lib/blockPaging'
import { dayLabelsFor, outboxEdgeAfter, type RunEdge, runEdgeBetween, unreadAnchorBlock } from '../lib/chatGrouping'
import { renderNoticeMessage } from '../lib/noticeText'
import { outgoingMessageBody, pendingMessageBlock } from '../lib/outgoingMessage'
import { AGENT_STATUS_EVENTS, collapseNotices, type PlatformNotice, rendersInRoom } from '../lib/platformNotice'
import { coalesceSplitFencedCodeBlocks } from '../lib/renderMessage'
import { placeSplitMarkers } from '../lib/splitMarkers'
import { taskTitle, topicShortId, topicStateBadge, topicTitle } from '../lib/topicState'
import { myHandle } from '../me'
import { currentUserName } from '../services/account'

import { ANNOUNCED } from './chatPanelContract'
import { useAgentNaming } from './useAgentNaming'
import { useChatComposer } from './useChatComposer'
import { useChatMessageClicks } from './useChatMessageClicks'
import { useChatPaging } from './useChatPaging'
import { useMessageReactions } from './useMessageReactions'
import { useOwnChecklist } from './useOwnChecklist'
import { applyRoomState, useRoomResync } from './useRoomResync'
import { useRoomTasks } from './useRoomTasks'

import i18n, { t } from '@/i18n'

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

  // Message rendering (markdown / plain / reference chips) lives in
  // ../lib/renderMessage and happens in the row components; here we only fill the
  // handle→name and id→title maps they render with, from the roster / topics props.
  const mentionNames = reactive<Record<string, string>>({})
  const topicTitles = reactive<Record<string, string>>({})
  const refMaps = { mentionNames, topicTitles }

  const AUTHOR = myHandle()

  // 这一条出错就写给用户看，所以它得在下面那段（房间名册）之前。
  const errorMsg = ref<string | null>(null)

  // 名册、座位、显示名、头像 —— 见 room/composables/useRoomRoster。
  // 房间名册和项目名册是两份，因为 AI 队友的座位只在前者上。
  const {
    agentSeat: rosterSeat,
    mentionPool,
    memberByHandle,
    seatByHandle,
    agentNameOf,
    agentDisplayName,
    displayName,
    isExternal,
    avatarSrc,
    myName,
  } = useRoomRoster({
    topic: () => topic(),
    members: () => members(),
    author: AUTHOR,
    onError: (message) => {
      errorMsg.value = message
    },
  })

  // 谁在这间房说话、界面上怎么称呼它、@ 渲染成谁的名字 —— 见 useAgentNaming。
  const { agentSeat, agentName, composerHint } = useAgentNaming({
    roomSeat: rosterSeat,
    pool: mentionPool,
    nameOf: agentNameOf,
    names: mentionNames,
    taskAgentHandle: opts.taskAgentHandle,
    alwaysSummon,
  })

  // 此刻显示时间线的哪一段 —— 见 room/composables/useTimeline；rendersInRoom 只放画得出来的块进窗口，不露面的块不占额度。
  const timeline = useTimeline({ renders: rendersInRoom })
  const history = useHistoryReads({ roomId: () => place()?.id, disposed: () => disposed })
  const tail = useRoomTail({
    roomId: () => place()?.id,
    reading: () => history.reading(),
    catchUp: (roomId) => void resync.catchUp(roomId),
  })
  const { messages, hasMore, hasNewer } = timeline
  const loadingHistory = ref(false)

  // 哪几轮在跑、谁在干、要不要显示「在处理」—— 见 room/composables/useRoomTurns。
  // 往上报（working / site-turns）是这里的事。
  const runRecords = useRunRecords()
  const turns = useRoomTurns({ messages, records: runRecords.records })
  const { awaitingReply, turnAgentHandle } = turns
  watch(awaitingReply, (v) => emit('working', v))
  watch(turns.turnStarts, (v) => emit('site-turns', v))
  // 现场那一格只收房间自己的事件行：分身的记在它那张卡上，消息在对话栏。
  function toSite(b: Block) {
    if (b.kind === 'event') emit('site-block', b)
  }

  // 看着这间房的人：表情里哪几个是自己点的，靠它认。
  const viewer = computed(() => currentUserName.value ?? myHandle())
  /** 把这一条换进时间线（在的话）。 */
  function replaceShown(block: Block) {
    if (!timeline.find(block.id)) return
    timeline.replace(block)
    history.note(block.id, block)
  }

  // 自己的清单：发一张、点记号改一步 —— 见 useOwnChecklist。
  const { postChecklist, changeChecklist } = useOwnChecklist({
    topicId: () => place()?.id,
    show: replaceShown,
    fail: (e) => (errorMsg.value = e instanceof Error ? e.message : t('work.room.checklist.saveFailed')),
  })

  // ---- Emoji reactions —— 见 composables/useMessageReactions ----
  const { reactionPickerFor, applyReactions, onReact, togglePicker } = useMessageReactions({
    find: (id) => timeline.find(id),
    errorMsg,
    pendingHistory: history.pendingReactions,
    me: AUTHOR,
  })

  // 滚动位置、跟不跟新消息、重放期间不抖 —— 见 room/composables/useChatScroll。
  // 往回翻历史留在这里：它碰 messages / 缓存 / 错误横幅，不是滚动的事。
  const {
    scrollRef,
    contentRef,
    atBottom,
    scrollToBottom,
    autoScroll,
    beginCatchUp,
    noteFrame,
    rememberScroll,
    restoreScroll,
    restoresToBottom,
  } = useChatScroll({ showingNewest: () => !hasNewer.value })

  // 打开话题时分批挂行 —— 见 room/composables/useRowBatch（`rows`、`paging` 在下面，晚读）。
  const rowBatch = useRowBatch({
    scrollRef,
    atBottom,
    restoresToBottom,
    rowIds: () => rows.value.map((r) => r.block.id),
    onDone: () => void paging.fillViewportIfNeeded(),
  })

  // 这条房间 socket 的连接、重连退避、心跳、换掉假活的那条 —— 见
  // room/composables/useRoomSocket。它不认识帧的含义：帧交给下面的 handleFrame。
  const {
    connected,
    linkDown,
    connectRefused,
    open: openSocket,
    close: closeSocket,
    send: sendOnSocket,
    isConnectRefusal,
    retryLater,
  } = useRoomSocket({
    topicId: () => place()?.id,
    onFrame: (frame) => {
      handleFrame(frame)
      noteFrame()
    },
    onOpen: (reconnect, newest) => {
      // State frames are transient: what the room announced while we were away is
      // gone, so a re-connect (not the first open) treats each as changed once.
      if (reconnect) {
        for (const resource of ANNOUNCED) emit('state-changed', resource)
        emit('state-changed', 'topics', place()?.id)
        void reloadRoomTasks()
      }
      tail.subscribed(newest)
      // 要一份此刻的现场来核对屏幕上留着的那份（handleFrame 的 room_state）。
      sendOnSocket({ type: 'sync' })
      void flushOutbox() // 断线期间没送出去的，连上就自己走
    },
    reconnect: (topicId) => {
      const current = place()
      if (current?.id === topicId) void (history.entered(topicId) ? resync.resync(topicId) : loadTopic(current))
    },
    errorMsg,
  })

  // 此刻谁在这个房间里忙（打字的人、干活的队友）—— 见 room/composables/useRoomActivity；
  // 拼成输入框下面那一行的数据见 useActivityLines（阶段、当前一步、多久没新帧）。
  const activity = useRoomActivity({ me: AUTHOR, send: sendOnSocket })
  const liveSteps = useLiveSteps()
  const activityLines = useActivityLines(
    activity.others,
    liveSteps,
    (handle) =>
      agentNameOf(handle) ??
      (isAgentHandle(handle) ? agentDisplayName(handle) : memberByHandle.value.get(handle)?.name || handle),
    (handle) => turns.faces.value[handle]?.status
  )
  watch(
    () => {
      const busy = new Set(activityLines.value.map((l) => l.handle))
      const queued = runRecords.waitingLines((h) => agentNameOf(h) ?? agentDisplayName(h))
      return [...activityLines.value, ...queued.filter((l) => !busy.has(l.handle))]
    },
    (v) => emit('activity', v)
  )

  // 每次连上，broker 都会把一轮进行中的帧一次性重放出来——先进追赶模式，这一阵里
  // 不逐帧滚动。
  function connectSocket(topicId: string) {
    beginCatchUp()
    openSocket(topicId)
  }

  useErrorFade(errorMsg, connected, connectRefused)

  // 点了「编辑」的那几条：它们离开时先收拢自己的高度，原文回到输入框。别的离开（送达
  // 后换成落库的那一条）必须是瞬间的，否则同一句话会在屏幕上出现两遍。
  const editing = new Set<string>()

  // Append a block unless it's already in the timeline: after a switch-away /
  // return, history (DB) and the broker's in-progress-turn replay overlap, and
  // a block must never show up twice (现场不能错).
  function pushBlock(b: Block) {
    history.note(b.id, b)
    tail.hold(b.id)
    const landing = timeline.append(b)
    if (landing === 'known' || landing === 'above' || history.reading() || b.author === AUTHOR) return
    if (b.kind === 'artifact') emit('preview-shown') // 新摆出一份东西：面板立刻去问预览指针，不等轮询
    if (landing === 'shown') arrived.add(b.id)
    if ((landing === 'held' || !atBottom.value) && b.kind !== 'event') unseen.value.push(b.id)
  }

  // 往上翻着的时候别人又说了话：底部浮出一颗提示，写着来了几条。点它回到最新，并让
  // 来的第一条闪一下——人要找的是「新的从哪开始」，不只是「到底了」。回到底部（不管
  // 是点它还是自己滚下去）它就收起。停在历史中间时它一直在，写「回到最新」：那时底部
  // 只是这一段的底部，不是最新。
  const unseen = ref<string[]>([])

  function handleFrame(frame: WsServerFrame) {
    // 平台运行中记下的一件事：不进对话，现场和状态行读它。
    const recorded = runRecordOf(frame)
    if (recorded) {
      runRecords.receive(recorded)
      toSite(recorded)
    }
    const told = threadStatusOf(frame)
    if (told) emit('thread-status', told.threadId, told.record)
    switch (frame.type) {
      case 'user_block':
        if (settleOutbox(frame.block)) delivered.add(frame.block.id)
        pushBlock(frame.block)
        autoScroll()
        break
      case 'reaction':
        // Someone toggled an emoji / 芝士's 👀 receipt landed — update the chip
        // row in place (the frame carries the block's full fresh aggregate).
        applyReactions(frame.block_id, frame.reactions)
        break
      case 'state':
        // A platform resource changed → parent refreshes that panel live.
        // The clickable record of the action is a persisted event_block (below).
        if (frame.resource === 'topics' || frame.resource === 'tasks') void reloadRoomTasks()
        emit('state-changed', frame.resource, frame.id)
        break
      case 'event_block':
        // A persisted, clickable action card (doc/topics/...) for this turn.
        pushBlock(frame.block)
        toSite(frame.block)
        autoScroll()
        break
      case 'block_updated': {
        // 已经在时间线上的一行变了：原地换掉，不追加第二行。
        timeline.replace(frame.block)
        history.note(frame.block.id, frame.block)
        tail.hold(frame.block.id)
        toSite(frame.block)
        break
      }
      case 'assistant_block':
        // One complete 芝士 message (Slack-style) — a turn may land several.
        pushBlock(frame.block)
        // Compatibility with an older backend that has no lifecycle markers.
        turns.settleIfIdle()
        autoScroll()
        break
      case 'error':
        // The socket was refused at connect — the backend closes right after this
        // frame, so latch the reason and stop the reconnect loop from burying it.
        if (isConnectRefusal(frame.code)) {
          connectRefused.value = true
          errorMsg.value = renderNoticeMessage(frame.i18n, frame.message)
          awaitingReply.value = false
          return
        }
        // A persisted turn failure is already in the timeline as an event block
        // (现场即事实记录); only un-persisted errors need the floating banner.
        if (!frame.persisted) errorMsg.value = renderNoticeMessage(frame.i18n, frame.message)
        turns.settleIfIdle()
        break
      case 'done':
        // Mid-session messages fold into the existing Claude run and emit no
        // separate completion frame. Lifecycle markers own the running indicator.
        if (turns.settleIfIdle()) emit('turn-done')
        autoScroll()
        break
      case 'retract_block':
        history.note(frame.block_id, null)
        timeline.remove(frame.block_id)
        break
      case 'agent_control':
        emit('agent-control', frame.state)
        break
      case 'turn_active':
        turns.active(frame.turn_ids ?? [], frame.since, frame.agents)
        break
      case 'activity':
        activity.apply(frame)
        break
      case 'activity_snapshot':
        activity.snapshot(frame.members)
        break
      case 'room_state':
        applyRoomState(frame, { turns, runRecords, activity, liveSteps, typing })
        break
      case 'thread_activity':
        emit('thread-activity', frame.thread_id, frame.member, frame.active)
        break
      case 'turn_started':
        turns.started(frame.turn_id, frame.agent)
        runRecords.turnBegan(frame.turn_id)
        break
      case 'turn_finished': {
        turns.finished(frame.turn_id)
        runRecords.turnBegan(frame.turn_id)
        emit('turn-done')
        autoScroll()
        break
      }
    }
    liveSteps.follow(frame)
    typing.follow(frame, !awaitingReply.value)
  }

  // 卸载之后还在飞的那几个请求回来时，不该再往一个已经没了的面板上写东西。
  let disposed = false

  function historyReadFailed(roomId: string, e: unknown) {
    if (e instanceof ApiError && [401, 403, 404].includes(e.status)) closeSocket()
    errorMsg.value = e instanceof Error ? e.message : t('work.room.loadFailed')
    // A failed history fetch must not terminate socket recovery during an outage.
    if (isRetryableGetFailure('GET', e instanceof ApiError ? e.status : undefined, e)) retryLater(roomId)
  }

  async function loadTopic(room: Topic, entering = false) {
    const { changes, reactions, stillHere, done, end } = history.begin(room.id, true)
    // 地址点名了一条消息：落到它上面，而不是上次停的地方。
    const focus = focusBlock() ?? null
    errorMsg.value = null
    connectRefused.value = false // a fresh topic gets a fresh attempt at connecting
    turns.reset()
    typing.clear()
    activity.reset()
    liveSteps.reset()
    runRecords.reset()
    reactionPickerFor.value = null
    rowActions.resetBar()
    unreadAnchorId.value = null
    arrived.clear()
    sentNow.clear()
    delivered.clear()
    editing.clear()
    composer.editingId.value = null
    unseen.value = []
    tail.reset()
    composer.clearPendingAtts() // pending images belong to the topic they were typed in
    closeSocket()
    paging.loadingOlder.value = false
    rowBatch.revealAll()
    const cached = cachedWindow(room.id)
    if (cached) {
      timeline.show(cached)
      rowBatch.startFor(room.id, focus)
      if (!focus) restoreScroll(room.id)
      // 缓存里只有一页，而最新那一段几乎全是 `in_room:false` 的回合事件——一页常常画
      // 不满一屏（一条消息飘在半空、下面空一片）。不等上面那条请求回来，现在就按真实
      // 行往回补：补的时候滚动补偿把最新那条钉在底部，历史往上长。见 useChatPaging。
      void paging.fillViewportIfNeeded()
    } else {
      timeline.show({ blocks: [], hasMore: false })
      loadingHistory.value = true
    }
    try {
      // Allow composer restoration to finish, then authenticate both transports.
      // Recovery keeps its history-first reconciliation for lost message echoes.
      await ensureFreshToken()
      if (!stillHere()) return
      const parallelSocket = entering && outbox.value.length === 0
      if (parallelSocket) connectSocket(room.id)
      // 打开话题的那次导航已经替它起了头（router/index.ts），它往往比下面这一条先
      // 回来：先回来就先画出来。不等它——那条走的是后台预取的队列，可能排在别的话题
      // 后面；下面这一条照常直接去取，谁先到用谁。
      // 先画出来的那一页之后就当缓存看待：下面合并、判断「长了没有」、要不要复位滚动，
      // 都和一开始就有缓存时一样。
      let shownEarly: typeof cached = null
      const warming = cached ? undefined : pendingBlockRefresh(room.id)
      void warming?.then(async () => {
        if (!stillHere() || !loadingHistory.value) return
        const warmed = cachedWindow(room.id)
        if (!warmed) return
        shownEarly = warmed
        timeline.show(warmed)
        rowBatch.startFor(room.id, focus)
        if (!focus) restoreScroll(room.id)
        // 预热回来的是同样的一页，而最新那一段几乎全是 `in_room:false` 的回合事件，
        // 一页只画得出一两行——这时候撤骨架露出来还是「一条消息飘在半空」。先按真实行
        // 补满一屏再撤，和下面正式那一页补法一致。见 useChatPaging.fillViewportIfNeeded。
        await paging.fillViewportIfNeeded()
        if (!stillHere()) return
        loadingHistory.value = false
      })
      // One screenful, not the whole timeline — older blocks arrive when the
      // user scrolls up to them (loadOlder).
      const payload = await listBlocks(room.id, { limit: PAGE_SIZE })
      // Only apply if still the active topic (avoid race on fast switching).
      if (!stillHere()) return
      for (const block of payload.data) tail.hold(block.id)
      // Blocks that landed while we were away append at the tail; if the user
      // was parked at the bottom, follow them so the newest message is visible
      // without a manual scroll. Compared on the LAST id, not on length: the
      // cached window and this page can be different sizes (the user may have
      // paged back), so a length comparison says nothing about the tail.
      const shown = cached ?? shownEarly
      // 开场补窗（上面那次 fillViewportIfNeeded）可能已经把更早的几页读进来了。游标是
      // 「窗口读到哪了」，比缓存窗口的更老就说明窗口被往上撑开过——这时候下面这一份
      // 「最新一页」只是它的尾巴，`timeline.show` 整段换会把刚读回来的历史丢掉、屏幕跳
      // 回最新那条。改成把它当缓存窗口，按它去合并。
      const extended = shown !== null && timeline.oldestLoaded() !== (shown.blocks[0]?.id ?? null)
      const grew = shown !== null && shown.blocks.at(-1)?.id !== payload.data.at(-1)?.id
      // Merge rather than replace, so scrollback the user already loaded (and
      // that restoreScroll's saved offset refers to) does not vanish under them.
      // `has_more` is a boolean on the wire; a response that leaves it out is
      // "nothing older", not "unknown" — the timeline's flag is never undefined.
      const more = !!payload.has_more
      const merged = shown
        ? mergeRefreshedTail(shown, { blocks: payload.data, hasMore: more })
        : { blocks: payload.data, hasMore: more }
      // Live frames can arrive while the HTTP snapshot is pending. Apply them
      // last, including retractions, so that snapshot cannot erase newer events.
      merged.blocks = applyLiveChanges(merged, changes, reactions)
      if (extended) {
        // 窗口已经被补开了：不整段换，逐条接——变了的原地替换，新来的接到末尾。更早
        // 那段历史原样留着，滚动位置也就不动。
        for (const block of merged.blocks) {
          if (timeline.find(block.id)) timeline.replace(block)
          else timeline.append(block)
        }
      } else {
        timeline.show(merged)
      }
      // A reconnect starts with durable history. Settle sends that landed while
      // their echo was lost before opening the new socket; only absent client ids
      // remain queued for an idempotent resend.
      for (const block of merged.blocks) settleOutbox(block)
      // 补过窗的话缓存那一页由翻页自己维护（pullOlderPage 会写），这里别用这一页盖回去。
      if (!extended) setCachedWindow(room.id, merged)
      placeUnreadAnchor() // 冻在这一刻：之后来的新消息不再移动这条线
      if (!shown) rowBatch.startFor(room.id, focus)
      if (focus) void paging.openAt(focus)
      else if (!shown) restoreScroll(room.id)
      else if (grew && atBottom.value) autoScroll()
      if (!parallelSocket && !connectRefused.value) connectSocket(room.id)
      // 开场这一窗先按真实行补满一屏，再让骨架撤（下面 finally 才把 loadingHistory 落下）：
      // 最新那一段几乎全是 `in_room:false` 的回合事件，一页 50 块常常只画得出一两行，补完
      // 之前露出来就是「一条消息飘在半空」，补完再露才是首屏一屏历史。见 useChatPaging。
      await paging.fillViewportIfNeeded()
      done()
    } catch (e) {
      if (stillHere()) historyReadFailed(room.id, e)
    } finally {
      if (end()) {
        loadingHistory.value = false
        tail.check()
      }
    }
  }

  // Send a message. `summon` (= 这条消息 @ 了芝士) asks 芝士 to reply; when false
  // the message is just posted (spec §7.1 默认不 @). The composer lives in TopicView and
  // drives this via the exposed ref, so the input bar can span chat + doc.
  // B3: reply target — the message this next send threads under (reply_to).
  function parentOf(m: Block): Block | undefined {
    return m.reply_to ? timeline.find(m.reply_to) : undefined
  }
  function showReplyCue(m: Block): boolean {
    // Only a person's replies are explicit threads. An AI message's reply_to is
    // the implicit link to the message that triggered it — not a thread cue.
    return isPersonBlock(m) && !!parentOf(m)
  }

  // An image attachment block (图片输入) — drawn in place by AttachmentImage.
  // ---- 芝士摆出来的一份东西 (`cheese show` → kind=artifact) ----

  // 只给下载用：downloadFile 自己会带上 Authorization。显示图片不走这里。
  function imageUrl(m: Block): string {
    const room = topic()
    return room ? attachmentRawUrl(room.id, m.content) : ''
  }
  async function downloadAttachment(m: Block) {
    try {
      await downloadFile(imageUrl(m), m.content.split('/').pop() || 'file')
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.chat.downloadFailed')
    }
  }
  function scrollToMessage(id: string, behavior: ScrollBehavior = scrollBehavior()) {
    if (rowBatch.deferUntilRevealed(() => scrollToMessage(id, behavior))) return
    const el = scrollRef.value?.querySelector(`[data-mid="${id}"]`)
    if (!el) return
    el.scrollIntoView({ behavior, block: 'center' })
    flash(id)
  }

  // 发件箱：已经打出去、库里还没有的那几条 —— 见 room/composables/useOutbox。
  // 「发出去之后房间该有什么反应」留在这里（下面的 `send`）。
  const {
    outbox,
    enqueue,
    flush: flushOutbox,
    settle: settleOutbox,
    retry: retrySend,
    drop: dropSend,
    pause: pauseOutbox,
  } = useOutbox({
    send: (item, signal) => {
      const room = place()
      if (!room) return Promise.reject(new DOMException('no room', 'AbortError'))
      const body = outgoingMessageBody(item)
      return postChatMessage(room.id, body, signal).catch((error: unknown) => {
        // 4xx 是后端说了「不」（没权限、房间已归档、内容不合法）；408/429 和别的失败
        // 都是这一刻的事，发件箱会带同一个 id 再试。
        if (
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500 &&
          ![408, 429].includes(error.status)
        ) {
          throw new SendRefused(error.message)
        }
        throw error
      })
    },
    onDelivered: (block) => {
      // 切走之后才回来的那一条属于上一个房间：它在那边的历史里，不画在这里。
      if (block.conversation_id !== place()?.id) return
      delivered.add(block.id)
      pushBlock(block)
      autoScroll()
    },
  })

  function send(
    content: string,
    summon: boolean,
    attachments?: ChatAttachment[],
    quotedContext?: QuotedContext
  ): boolean {
    const trimmed = content.trim()
    const atts = attachments?.length ? attachments : undefined
    // An image-only send (no text) is a valid message (图片输入).
    if (!trimmed && !atts) return false
    errorMsg.value = null
    paging.backToNewest()
    sentNow.add(
      enqueue({ content: trimmed, replyTo: composer.replyTarget.value?.id ?? undefined, atts, quotedContext })
    )
    composer.clearReply()
    // Local acceptance starts the waiting indicator, before delivery — where the
    // answer comes. A summon in a channel's main line is answered in its 支线,
    // whose turn frames this line never hears, so lit here nothing would put it
    // out: the room would say it is working long after the answer landed.
    if (summon && !answersInThread()) awaitingReply.value = true
    scrollToBottom()
    return true
  }

  // 点一道题的快捷回复：把那几个字作为对这道题的回复发出去 —— 和在输入框里打字是
  // 同一条消息，提问的那位队友因此开下一轮（后端认它是回这道题的）。输入框里写到
  // 一半的话不动。
  function replyToQuestion(question: Block, text: string) {
    errorMsg.value = null
    paging.backToNewest()
    sentNow.add(enqueue({ content: text, replyTo: question.id }))
    if (!answersInThread()) awaitingReply.value = true
    scrollToBottom()
  }

  // The conversation stream shows messages + lightweight system lines only.
  // doc blocks are document state (they live in the doc panel), and AI tool
  // events belong in 现场 — neither belongs in the group chat (spec §7.1).
  // Historical SDK turns can contain one fenced Markdown block split across
  // consecutive message rows. Repair those rows before collapseNotices hides
  // event blocks, because an event is a hard boundary and must prevent an
  // accidental merge.
  const rows = computed(() => collapseNotices(coalesceSplitFencedCodeBlocks(messages.value)))
  const visible = computed<Block[]>(() => rows.value.map((r) => r.block))

  // ---- the pieces this panel is made of --------------------------------
  // Each block below owns one job, so no single file has to hold the whole
  // room; the panel keeps the wiring and what is shared between them.
  const paging = useChatPaging({
    topic: place,
    focusBlock,
    timeline,
    scrollRef,
    atBottom,
    loadingHistory,
    unseen,
    errorMsg,
    rememberScroll,
    scrollToMessage,
    scrollToBottom,
    rowsPending: () => rowBatch.pending.value,
  })
  const resync = useRoomResync({
    history,
    timeline,
    runRecords,
    settle: (b: Block) => settleOutbox(b),
    noteRead: (blocks: Block[]) => blocks.forEach((b) => tail.hold(b.id)),
    readEnded: () => tail.check(),
    scroll: { atBottom, follow: autoScroll, fill: () => paging.fillViewportIfNeeded() },
    socket: { connect: (id: string) => connectRefused.value || connectSocket(id), close: closeSocket },
    failed: historyReadFailed,
  })

  const { loadingOlder, loadingNewer, openAt, onTimelineScroll } = paging

  const motion = useTimelineMotion({
    atBottom,
    hasNewer,
    unseen,
    editing,
    scrollRef,
    backToNewest: () => paging.backToNewest(),
  })
  const { arrived, sentNow, delivered, flashId, flash, settleArrival, settleSent, outboxLeave, jumpToUnseen } = motion

  const composer = useChatComposer({
    topic: place,
    alwaysSummon,
    showComposer,
    rows,
    blocks: visible,
    hasMore,
    loadingHistory,
    awaitingReply,
    outbox,
    errorMsg,
    agentSeat: () => agentSeat.value ?? undefined,
    refs: refMaps,
    timeline,
    editing,
    isMine,
    displayName,
    send,
    dropSend,
  })
  // The composer hands back its whole surface (see the return below): the
  // panel reads a few of those refs itself, the view destructures the rest.
  // 我在输入框里打字，房间里的人看得见（`useRoomActivity`）。
  watch(composer.draft, (text) => activity.composing(text))

  const rowActions = useChatRowActions({
    timeline,
    scrollRef,
    contentRef,
    rows,
    reactionPickerFor,
    editingId: composer.editingId,
  })
  const { sheet, sheetBlock, touchOnly, timeShownId, bar, barBlock, onTimelinePointer, hideBar } = rowActions

  // 消息区里点到的 chip / 头像落到哪个事件 —— 见 composables/useChatMessageClicks。
  const onMessagesClick = useChatMessageClicks({
    reactionPickerFor,
    touchOnly,
    toggleTime: rowActions.toggleTime,
    rows: () => rows.value,
    roomTasks: () => roomTasks.value,
    emit,
  })

  // ---- 时间刻度 ----
  // 哪一条属于哪一天、日期线画在它上面：规则和判据在 lib/chatGrouping.ts（纯函数，
  // 那份测试就在旁边），这里只是把它接到此刻的这几行上。
  const dayLabels = computed(() => dayLabelsFor(rows.value))

  // 新消息线锚在哪条块上。开话题时按当时的未读数往回数一次就冻住 —— 它是「我上次
  // 看到哪儿」的记号，不是一个会跟着新消息跑的游标。
  const unreadAnchorId = ref<string | null>(null)
  function placeUnreadAnchor() {
    unreadAnchorId.value = unreadAnchorBlock(rows.value, unreadOnOpen(), AUTHOR)
  }

  // 「已派出」标记 (issue #314): 本房间派出去的活，在时间线上它被派出去的那个时刻
  // 标一行，点进去就是那条支线。库里没有这行 —— split 不往房间主线写任何 block，所
  // 以位置只能由支线的 created_at 现算（lib/splitMarkers.ts 说明了它能标什么、标不
  // 了什么）。
  //
  // 单独拉一次而不是从 topicList 里挑：一件活不再是话题树上的一个节点，话题列表里
  // 根本没有它了。`limit: 0` 是因为标记只要支线本身，不要它们的对话。
  const { tasks: roomTasks, reload: reloadRoomTasks } = useRoomTasks(() => topic()?.id)

  // <#id> 可以指一个话题，也可以指这个房间里的一件活：两边的标题都得认得，否则活的
  // chip 只会写「#话题」。
  watch(
    [() => topicList(), roomTasks],
    ([ts, tasks]) => {
      for (const k of Object.keys(topicTitles)) delete topicTitles[k]
      for (const t of ts) topicTitles[t.id] = topicTitle(t)
      for (const t of tasks) topicTitles[t.id] = taskTitle(t)
    },
    { immediate: true, deep: true }
  )

  // 「从这里拆出了一个任务」只画在房间的对话里：任务自己的对话里没有拆出去这回事。
  const splitMarkers = computed(() =>
    placeSplitMarkers(place()?.id === topic()?.id ? roomTasks.value : [], {
      blocks: visible.value,
      hasMore: hasMore.value,
      hasNewer: hasNewer.value,
    })
  )

  // 某一轮那位队友。认不出是谁的轮次，房间里只坐着一位时只能是它；坐着几位时不猜：
  // 拿排在最前的那位顶上，发给 B 的那一轮出了错，提示就署成了 A。
  const severalAgents = computed(() => [...seatByHandle.value.values()].filter((row) => row.agent).length > 1)
  function turnAgent(turnId: string | null | undefined): NoticeAgent | null {
    const handle = turnAgentHandle(turnId)
    if (handle) return { name: agentDisplayName(handle), handle }
    return severalAgents.value ? null : { name: agentName.value, handle: agentSeat.value?.handle ?? null }
  }

  function noticeAgent(block: Block, notice: PlatformNotice): NoticeAgent | null {
    // This event contains the worker's actual result, rather than a status notice.
    if (block.meta?.event_type === 'subagent_stop') return null
    if (isPersonBlock(block)) return null
    // 关于某位 AI 队友那件事的通知，以那位队友的身份出现（头像和名字），不另署「平
    // 台」。是哪位：署名是队友就是它，否则是这一轮的那位（turnAgent）。不属于任
    // 何一位队友那一轮的平台通知（人编辑了文档之类）照旧不署队友。
    if (isAgentHandle(block.author) || seatByHandle.value.get(block.author)?.agent) {
      return { name: agentDisplayName(block.author), handle: block.author }
    }
    if (seatByHandle.value.has(block.author) || memberByHandle.value.has(block.author)) return null
    if (AGENT_STATUS_EVENTS.has(String(block.meta?.event_type ?? ''))) return turnAgent(block.turn_id)
    if (block.author === 'system' && (notice.mode === 'action' || notice.mode === 'turn-summary')) {
      return turnAgent(block.turn_id)
    }
    if (block.turn_id && (block.author === 'system' || notice.mode === 'action' || notice.mode === 'turn-summary')) {
      return turnAgent(block.turn_id)
    }
    return null
  }
  const pendingBlock = (item: Outgoing) => pendingMessageBlock(item, AUTHOR)

  // 时间那一格说的是送达状态。失败了就不说：失败那一行自己会说清楚是什么失败了。
  function outgoingState(item: Outgoing): string {
    if (item.state === 'failed') return ''
    return connected.value ? t('work.room.chat.sending') : t('work.room.chat.waitingConnection')
  }

  function fmtTime(iso: string): string {
    // Local HH:mm next to the name on the first of a run (not raw UTC). In the
    // app's language, not the browser's: a Chinese screen on an en-US system
    // otherwise reads 「07:20 PM」, which also no longer fits the gutter that a
    // continued message shows its time in.
    return new Date(iso).toLocaleTimeString(i18n.global.locale.value, {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  }
  // 分栏 (2026-09-09, <@符露夀> 定): 我说的话靠右，别人和芝士靠左。
  //
  // 侧只回答一件事——**这条是不是我说的**。「谁在说」仍然由头像和名字承担，两侧
  // 都保留它们：房间里是「多个人 + 一个芝士」，左边同时坐着好几个人，光靠「在左边」
  // 分不出谁是谁。芝士也在左边，它是队友里的一个，不是对话的另一极。
  //
  // 按 handle 判，不按 author_type：author_type 只说「参与者还是平台」，而这一列
  // 里有好几个人。
  function isMine(m: Block): boolean {
    return isPersonBlock(m) && m.author === AUTHOR
  }

  // 一段话从哪一行开始、哪一行是同一段的续话、发件箱那几条接在谁后面：规则在
  // lib/chatGrouping.ts，这里只回答「上面有没有插进别的行」——「已派出」标记和新
  // 消息线都会把一段话切断。
  const runEdges = computed(() =>
    visible.value.map((cur, i) =>
      runEdgeBetween(visible.value[i - 1], cur, {
        broken: splitMarkers.value.before.has(cur.id) || cur.id === unreadAnchorId.value,
      })
    )
  )
  const typing = useTypingPreview({ topic, hasNewer, outbox, visible, splitMarkers, arrived, delivered }) // 队友正在写的那条
  function outboxEdge(index: number): RunEdge {
    if (index > 0) return 'cont'
    const last = visible.value.at(-1)
    return outboxEdgeAfter(last, {
      mine: !!last && isMine(last),
      brokenAbove: splitMarkers.value.tail.length > 0,
    })
  }

  // ---- Topic header state. The labels live in lib/topicState.ts because the
  // 工作台's own topic header renders the same badge — one table, so the two can
  // never disagree about what `archived` is called.
  const prShortId = computed(() => topicShortId(topic()?.id))
  const prState = computed(() => topicStateBadge(topic()?.status))

  // IME (输入法) guard — see TopicView.vue for the full story: Safari fires
  // compositionend BEFORE the commit-Enter keydown, which then looks like a
  // plain Enter. Track composition ourselves and swallow the trailing Enter.

  watch(
    () => place()?.id,
    (id, oldId) => {
      // Save where we were in the topic we're leaving, so coming back restores it.
      if (oldId) rememberScroll(oldId)
      if (oldId) {
        composer.rememberComposer(oldId)
        pauseOutbox()
      }
      const room = place()
      if (room) {
        // loadTopic clears the pending attachments synchronously before its first
        // await, so this topic's own draft has to be restored AFTER the call.
        void loadTopic(room, true)
        composer.restoreComposer(id)
        void flushOutbox() // 上次在这个房间里没送完的，接着送
      } else {
        timeline.show({ blocks: [], hasMore: false })
        closeSocket()
      }
    },
    { immediate: true }
  )

  onBeforeUnmount(() => {
    disposed = true
    // persist position across an unmount (e.g. leaving the view)
    rememberScroll(place()?.id)
    const room = place()
    if (room) composer.rememberComposer(room.id)
    // 链路和回声计时器由各自的 composable 在 scope 停掉时收，这里不重复一遍。
  })
  // Everything the view draws. One flat object, named after what it is: the
  // component below is a thin shell around these.
  return {
    // header
    topic: computed(() => topic()),
    agentName,
    agentSeat,
    prShortId,
    prState,
    composerHint,
    // timeline
    rows,
    hiddenRows: rowBatch.hidden,
    refMaps,
    awaitingReply,
    agentFaces: turns.faces,
    activityLines,
    timeline,
    hasMore,
    hasNewer,
    loadingHistory,
    loadingOlder,
    loadingNewer,
    openAt,
    onTimelineScroll,
    togglePicker,
    scrollRef,
    contentRef,
    atBottom,
    scrollToBottom,
    dayLabels,
    unreadAnchorId,
    splitMarkers,
    runEdges,
    outboxEdge,
    pendingBlock,
    outgoingState,
    retrySend,
    outbox,
    typingRows: typing.rows,
    noticeAgent,
    parentOf,
    showReplyCue,
    fmtTime,
    isMine,
    displayName,
    isExternal,
    avatarSrc,
    myName,
    mentionPool,
    onMessagesClick,
    onTimelinePointer,
    hideBar,
    bar,
    barBlock,
    reactionPickerFor,
    touchOnly,
    timeShownId,
    arrived,
    sentNow,
    delivered,
    flashId,
    flash,
    settleArrival,
    settleSent,
    outboxLeave,
    jumpToUnseen,
    unseen,
    // sheet
    sheet,
    sheetBlock,
    // composer (its whole surface; the view destructures what it draws)
    ...composer,
    // what the page above listens for
    errorMsg,
    connected,
    linkDown,
    send,
    replyToQuestion,
    viewer,
    postChecklist,
    changeChecklist,
    onReact,
    downloadAttachment,
    roomTasks,
    isAgentBlock,
    AUTHOR,
    // what the host may pull out of the panel without an event
    flushComposer: composer.flushComposer,
  }
}

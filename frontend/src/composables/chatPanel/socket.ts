// 这一栏的那条 socket：连接、重连退避、心跳，和每一帧对窗口意味着什么。
//
// 连不上的那几件事（退避、假活、换掉死掉的那条）见 room/composables/useRoomSocket——
// 它不认识帧的含义，帧交给这里。这里也不认识「一条消息长什么样」：块进窗口走
// `pushBlock`（去重、未读、闪一下都在那儿），再往下是行组件的事。
import type { Ref } from 'vue'
import type { useHistoryReads } from '../../components/room/composables/useHistoryReads'
import type { useLiveSteps } from '../../components/room/composables/useLiveSteps'
import type { useRoomActivity } from '../../components/room/composables/useRoomActivity'
import type { useRoomTail } from '../../components/room/composables/useRoomTail'
import type { useRoomTurns } from '../../components/room/composables/useRoomTurns'
import type { useRunRecords } from '../../components/room/composables/useRunRecords'
import type { useTimeline } from '../../components/room/composables/useTimeline'
import type { useTypingPreview } from '../../components/room/composables/useTypingPreview'
import type { Block, Topic, WsServerFrame } from '../../cx_types'
import type { ChatPanelEmit } from '../chatPanelContract'
import type { useMessageReactions } from '../useMessageReactions'
import type { useRoomResync } from '../useRoomResync'
import type { useTimelineTasks } from '../useTimelineTasks'

import { onScopeDispose, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { useErrorFade } from '../../components/room/composables/useErrorFade'
import { useRoomSocket } from '../../components/room/composables/useRoomSocket'
import { runRecordOf, threadStatusOf } from '../../components/room/composables/useRunRecords'
import { renderNoticeMessage } from '../../lib/noticeText'
import { ANNOUNCED, SNAPSHOT_RESOURCES } from '../chatPanelContract'
import { applyRoomState } from '../useRoomResync'

import { roomChanged } from '@/query/changes'
import { refreshTopicRow } from '@/query/project'
import { checklistMissed, checklistPushed } from '@/query/room'
import { settleRoom } from '@/query/snapshot'

/** 这条 socket，和每一帧对窗口意味着什么。 */
export function usePanelSocket(deps: {
  /** 这一栏此刻读的那段对话（房间自己，或房间里的一个任务）。 */
  place: () => Topic | null
  /** 这间房。 */
  topic: () => Topic | null
  /** 我自己的 handle。 */
  author: string
  emit: ChatPanelEmit
  timeline: ReturnType<typeof useTimeline>
  history: ReturnType<typeof useHistoryReads>
  tail: ReturnType<typeof useRoomTail>
  runRecords: ReturnType<typeof useRunRecords>
  turns: ReturnType<typeof useRoomTurns>
  reactions: ReturnType<typeof useMessageReactions>
  errorMsg: Ref<string | null>
  awaitingReply: Ref<boolean>
  atBottom: Ref<boolean>
  autoScroll: () => void
  /** 每次连上都先算在追赶（这一段里不逐帧滚动）。 */
  beginCatchUp: () => void
  /** 收到一帧（滚动那边按下暂停）。 */
  noteFrame: () => void
  /** 往上翻着的时候来了几条：底部那颗提示读它。 */
  unseen: Ref<string[]>
  /** 落库的那条回声到了/重连读回来了：发件箱据此收尾。 */
  settleOutbox: (block: Block) => 'dimmed' | 'fast' | false
  /** 断线期间没送出去的那几条，连上就自己走。 */
  flushOutbox: () => void
  /** 已经送达的那几条（useTimelineMotion）。 */
  delivered: () => Set<string>
  /** 刚到场、要闪一下的那几条（useTimelineMotion）。 */
  arrived: () => Set<string>
  activity: () => ReturnType<typeof useRoomActivity>
  liveSteps: () => ReturnType<typeof useLiveSteps>
  typing: () => ReturnType<typeof useTypingPreview>
  roomTasks: () => ReturnType<typeof useTimelineTasks>
  resync: () => ReturnType<typeof useRoomResync>
  /** 重新读一窗历史（usePanelTopicWindow）。 */
  loadTopic: (room: Topic, entering?: boolean) => void
}) {
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
    topicId: () => deps.place()?.id,
    onFrame: (frame) => {
      handleFrame(frame)
      deps.noteFrame()
    },
    onOpen: (reconnect, newest, room) => {
      // 订阅带来的房间快照放进缓存（query/snapshot）；进房间时等着它的那几处读就此拿到。
      const here = deps.place()
      const channel = deps.topic()
      if (here && channel) settleRoom(here.id, channel.id, room)
      // State frames are transient: what the room announced while we were away is
      // gone, so a re-connect (not the first open) treats each as changed once —
      // except what the snapshot just brought, which is as of now.
      if (reconnect) {
        const brought: ReadonlySet<string> = room ? SNAPSHOT_RESOURCES : new Set()
        for (const resource of ANNOUNCED) if (!brought.has(resource)) announce(resource)
        if (here) void checklistMissed(here.id)
        if (!room) announce('topics', channel?.id)
        else {
          // 房间这一行不在快照里；任务在，时间线上各块带着的那几件照旧重读。
          if (channel) void refreshTopicRow(channel.project_id, channel.id)
          void deps.roomTasks().refresh()
        }
      }
      deps.tail.subscribed(newest)
      // 要一份此刻的现场来核对屏幕上留着的那份（handleFrame 的 room_state）。
      sendOnSocket({ type: 'sync', asked: deps.runRecords.asking() })
      deps.flushOutbox() // 断线期间没送出去的，连上就自己走
    },
    reconnect: (topicId) => {
      const current = deps.place()
      if (current?.id === topicId)
        void (deps.history.entered(topicId) ? deps.resync().resync(topicId) : deps.loadTopic(current))
    },
    errorMsg: deps.errorMsg,
  })

  // 「有一轮在跑」是这条连接自己记的账：turn_started 让它亮，turn_finished 让它灭。
  // 现场页签上那个绿色的呼吸点、对话里的「芝士正在处理」读的都是它。一轮可能跑上
  // 几分钟到几小时，而这期间只要结束的那一帧没送到（掉线重连的边界上、一条丢掉的
  // 帧），页面就会一直显示有活在跑，直到下一次重连——可连接本身是好的，不会再重连。
  // 所以只要还认为有活在跑，就隔一阵用 sync 对一次：服务端答的 room_state 和重连时
  // 核对的是同一份事实，它说没有轮次了，就把亮着的那些撤掉。
  const WORKING_VERIFY_MS = 30_000
  let verifier: ReturnType<typeof setInterval> | null = null
  function stopVerifier() {
    if (verifier) clearInterval(verifier)
    verifier = null
  }
  function verifyWorking() {
    // 没连上时 `send` 自己就不发；这条只决定「还有没有必要问」。
    if (deps.awaitingReply.value) sendOnSocket({ type: 'sync', asked: deps.runRecords.asking() })
  }
  watch(deps.awaitingReply, (working) => {
    stopVerifier()
    if (working) verifier = setInterval(verifyWorking, WORKING_VERIFY_MS)
  })
  onScopeDispose(stopVerifier)
  // 后台标签页里的定时器会被浏览器降频甚至冻住。回到这个页面时马上对一次，不等
  // 下一个间隔。
  useEventListener(document, 'visibilitychange', () => {
    if (!document.hidden) verifyWorking()
  })

  // 每次连上，broker 都会把一轮进行中的帧一次性重放出来——先进追赶模式，这一阵里
  // 不逐帧滚动。
  function connectSocket(topicId: string) {
    deps.beginCatchUp()
    openSocket(topicId)
  }

  useErrorFade(deps.errorMsg, connected, connectRefused)

  // Append a block unless it's already in the timeline: after a switch-away /
  // return, history (DB) and the broker's in-progress-turn replay overlap, and
  // a block must never show up twice (现场不能错).
  function pushBlock(b: Block) {
    deps.history.note(b.id, b)
    deps.tail.hold(b)
    const landing = deps.timeline.append(b)
    if (landing === 'known' || landing === 'above' || deps.history.reading() || b.author === deps.author) return
    if (b.kind === 'artifact') deps.emit('preview-shown') // 新摆出一份东西：面板立刻去问预览指针，不等轮询
    if (landing === 'shown') deps.arrived().add(b.id)
    if ((landing === 'held' || !deps.atBottom.value) && b.kind !== 'event') deps.unseen.value.push(b.id)
  }

  // 房间说它的某样东西变了：缓存里那几份标过期，时间线上各块带着的任务重读，页面
  // 再接它自己画的那些。
  function announce(resource: string, id?: string) {
    const room = deps.topic()
    if (room) void roomChanged({ room: room.id, project: room.project_id, resource, id })
    if (resource === 'topics' || resource === 'tasks') void deps.roomTasks().refresh()
    deps.emit('state-changed', resource, id)
  }

  // 现场那一格只收房间自己的事件行：分身的记在它那张卡上，消息在对话栏。
  function toSite(b: Block) {
    if (b.kind === 'event') deps.emit('site-block', b)
  }

  // `platform_error` 是这一轮以平台故障收场的标志事件：后端发完它紧接着发 `error`
  // 和 `done`（turn/steps/execute.py）。会话没起来的那一类不会有 `turn_finished`
  // —— 它由「这一轮已经宣布过自己开始」那条路才发（turn/intake/messages.py）——
  // 于是这一轮在页面上「还在跑」：重试要等后端每 15 秒一次的对账
  // （turn_adoption.watch_books 读到库里那一行已结束，替它发 turn_finished）才对上。
  // 收到这条就当场结掉，其余几类提示（投递降级、机器不在…）都可能在轮次还活着时
  // 出现，一律不在这里替它做主。
  function endsTurn(b: Block): boolean {
    const meta = b.meta as Record<string, unknown> | null | undefined
    return meta?.event_type === 'platform_error'
  }

  function handleFrame(frame: WsServerFrame) {
    // 平台运行中记下的一件事：不进对话，现场和状态行读它。
    const recorded = runRecordOf(frame)
    if (recorded) {
      deps.runRecords.receive(recorded)
      toSite(recorded)
    }
    const told = threadStatusOf(frame)
    if (told) deps.emit('thread-status', told.threadId, told.record)
    switch (frame.type) {
      case 'user_block':
        if (deps.settleOutbox(frame.block) === 'dimmed') deps.delivered().add(frame.block.id)
        pushBlock(frame.block)
        deps.autoScroll()
        break
      case 'reaction':
        // Someone toggled an emoji / 芝士's 👀 receipt landed — update the chip
        // row in place (the frame carries the block's full fresh aggregate).
        deps.reactions.applyReactions(frame.block_id, frame.reactions)
        break
      case 'todo':
        if (deps.place()) checklistPushed(deps.place()!.id, frame.items)
        break
      case 'state':
        // A platform resource changed: what the cache holds of it goes stale
        // (`query/changes`), and the parent refreshes whatever else it draws.
        // The clickable record of the action is a persisted event_block (below).
        announce(frame.resource, frame.id)
        break
      case 'event_block':
        // A persisted, clickable action card (doc/topics/...) for this turn.
        pushBlock(frame.block)
        deps.roomTasks().landed(frame.block)
        toSite(frame.block)
        // 这一轮已经在这里结束了。按 turn_id 结掉，别的轮次照跑；没有 id 的那种
        // （关于整个频道的一句话）不替任何一轮做主，`error`/`done` 帧会收尾。
        if (endsTurn(frame.block) && frame.block.turn_id) {
          deps.turns.finished(frame.block.turn_id)
        }
        deps.autoScroll()
        break
      case 'block_updated': {
        // 已经在时间线上的一行变了：原地换掉，不追加第二行。
        deps.timeline.replace(frame.block)
        deps.history.note(frame.block.id, frame.block)
        deps.tail.hold(frame.block)
        toSite(frame.block)
        break
      }
      case 'assistant_block':
        // One complete 芝士 message (Slack-style) — a turn may land several.
        pushBlock(frame.block)
        // Compatibility with an older backend that has no lifecycle markers.
        deps.turns.settleIfIdle()
        deps.autoScroll()
        break
      case 'error':
        // The socket was refused at connect — the backend closes right after this
        // frame, so latch the reason and stop the reconnect loop from burying it.
        if (isConnectRefusal(frame.code)) {
          connectRefused.value = true
          deps.errorMsg.value = renderNoticeMessage(frame.i18n, frame.message)
          deps.awaitingReply.value = false
          return
        }
        // A persisted turn failure is already in the timeline as an event block
        // (现场即事实记录); only un-persisted errors need the floating banner.
        if (!frame.persisted) deps.errorMsg.value = renderNoticeMessage(frame.i18n, frame.message)
        deps.turns.settleIfIdle()
        break
      case 'done':
        // Mid-session messages fold into the existing Claude run and emit no
        // separate completion frame. Lifecycle markers own the running indicator.
        if (deps.turns.settleIfIdle()) deps.emit('turn-done')
        deps.autoScroll()
        break
      case 'retract_block':
        deps.history.note(frame.block_id, null)
        deps.timeline.remove(frame.block_id)
        break
      case 'agent_control':
        deps.emit('agent-control', frame.state)
        break
      case 'turn_active':
        deps.turns.active(frame.turn_ids ?? [], frame.since, frame.agents)
        break
      case 'activity':
        deps.activity().apply(frame)
        break
      case 'activity_snapshot':
        deps.activity().snapshot(frame.members)
        break
      case 'room_state':
        applyRoomState(frame, {
          turns: deps.turns,
          runRecords: deps.runRecords,
          activity: deps.activity(),
          liveSteps: deps.liveSteps(),
          typing: deps.typing(),
        })
        break
      case 'thread_activity':
        deps.emit('thread-activity', frame.thread_id, frame.member, frame.active)
        break
      case 'turn_started':
        deps.turns.started(frame.turn_id, frame.agent)
        deps.runRecords.turnBegan(frame.turn_id)
        break
      case 'turn_finished': {
        deps.turns.finished(frame.turn_id)
        deps.runRecords.turnBegan(frame.turn_id)
        deps.emit('turn-done')
        deps.autoScroll()
        break
      }
    }
    deps.liveSteps().follow(frame)
    deps.typing().follow(frame, !deps.awaitingReply.value)
  }

  return {
    connected,
    linkDown,
    connectRefused,
    connectSocket,
    closeSocket,
    sendOnSocket,
    retryLater,
    isConnectRefusal,
    pushBlock,
    announce,
  }
}

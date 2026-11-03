// 时间线的翻页和对账：往上读更早的、往下读更新的、断线回来跟服务端对一遍，以及跳到
// 某一条消息上。
//
// 一页怎么算、游标怎么走见 useChatPaging；断线重连那一套对账（谁比谁新、哪些回声丢
// 了）见 useRoomResync。两块读的是同一份窗口、同一个滚动位置，所以一起建。「跳到某
// 一条」也在这儿：它得先让行挂出来，再滚，再闪一下——三件事分属三个块，粘合是这里的
// 事。
import type { Ref } from 'vue'
import type { useHistoryReads } from '../../components/room/composables/useHistoryReads'
import type { useRoomTail } from '../../components/room/composables/useRoomTail'
import type { useRowBatch } from '../../components/room/composables/useRowBatch'
import type { useRunRecords } from '../../components/room/composables/useRunRecords'
import type { useTimeline } from '../../components/room/composables/useTimeline'
import type { Block, Topic } from '../../cx_types'

import { scrollBehavior } from '@/utils/motion'

import { useChatPaging } from '../useChatPaging'
import { useRoomResync } from '../useRoomResync'

/** 时间线的翻页、对账，和跳到某一条。 */
export function usePanelPaging(deps: {
  /** 这一栏此刻读的那段对话（房间自己，或房间里的一个任务）。 */
  place: () => Topic | null
  /** 地址点名的那条消息。 */
  focusBlock: () => string | null
  timeline: ReturnType<typeof useTimeline>
  history: ReturnType<typeof useHistoryReads>
  tail: ReturnType<typeof useRoomTail>
  runRecords: ReturnType<typeof useRunRecords>
  rowBatch: ReturnType<typeof useRowBatch>
  scrollRef: Ref<HTMLElement | null>
  atBottom: Ref<boolean>
  autoScroll: () => void
  scrollToBottom: () => void
  rememberScroll: (topicId: string | undefined) => void
  forgetScroll: (topicId: string) => void
  /** 这一下滚动是我们自己发的（跳到某一条）：别当成读者翻走了，也别当成浏览器夹的。 */
  beginOwnScroll: () => void
  loadingHistory: Ref<boolean>
  /** 往上翻着的时候来了几条：底部那颗提示读它。 */
  unseen: Ref<string[]>
  errorMsg: Ref<string | null>
  /** 落库的那条读回来了：发件箱据此收尾。 */
  settleOutbox: (block: Block) => unknown
  /** 连线那边（usePanelSocket）：对账要把连接摆回正轨。 */
  connectSocket: (topicId: string) => void
  closeSocket: () => void
  connectRefused: Ref<boolean>
  /** 读历史失败（usePanelTopicWindow）：401/403/404 收连接，可重试的挂回去重试。 */
  failed: (roomId: string, e: unknown) => void
  /** 跳到某一条时闪一下（useTimelineMotion）。 */
  flash: (id: string) => void
}) {
  function scrollToMessage(id: string, behavior: ScrollBehavior = scrollBehavior()) {
    if (deps.rowBatch.deferUntilRevealed(() => scrollToMessage(id, behavior))) return
    const el = deps.scrollRef.value?.querySelector(`[data-mid="${id}"]`)
    if (!el) return
    // 连着 `flash` 那一行：这一下是读的人点名要去的，随后的滚动事件不能反过来把他
    // 钉回底部（见 useChatScroll 的 beginOwnScroll）。
    deps.beginOwnScroll()
    el.scrollIntoView({ behavior, block: 'center' })
    deps.flash(id)
  }

  const paging = useChatPaging({
    topic: deps.place,
    focusBlock: deps.focusBlock,
    timeline: deps.timeline,
    scrollRef: deps.scrollRef,
    atBottom: deps.atBottom,
    loadingHistory: deps.loadingHistory,
    unseen: deps.unseen,
    errorMsg: deps.errorMsg,
    rememberScroll: deps.rememberScroll,
    scrollToMessage,
    scrollToBottom: deps.scrollToBottom,
    rowsPending: () => deps.rowBatch.pending.value,
  })
  const resync = useRoomResync({
    history: deps.history,
    timeline: deps.timeline,
    runRecords: deps.runRecords,
    settle: deps.settleOutbox,
    noteRead: (blocks: Block[]) => blocks.forEach((b) => deps.tail.hold(b)),
    readEnded: () => deps.tail.check(),
    scroll: {
      atBottom: deps.atBottom,
      follow: deps.autoScroll,
      scrollToBottom: deps.scrollToBottom,
      forgetScroll: deps.forgetScroll,
      fill: () => paging.fillViewportIfNeeded(),
    },
    socket: {
      connect: (id: string) => deps.connectRefused.value || deps.connectSocket(id),
      close: deps.closeSocket,
    },
    failed: deps.failed,
  })

  return { paging, resync }
}

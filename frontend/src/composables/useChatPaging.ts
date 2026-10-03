// Backing further into a topic's history: the chat holds a WINDOW of the
// timeline (newest PAGE_SIZE blocks), not the whole thing — a long topic was
// 2.1 MB / 2226 rows in one response, and the browser choked on all three of
// transfer, JSON parse, and 2226 live DOM nodes.
//
// Lifted verbatim out of ChatPanel: the four listBlocks calls that walk the
// window, the scroll compensation that keeps the reader where they were, and
// the watch that obeys a `focusBlock` jump from the address bar.
import type { Ref } from 'vue'
import type { useTimeline } from '../components/room/composables/useTimeline'
import type { Topic } from '../cx_types'

import { nextTick, ref, watch } from 'vue'

import { ApiError, listBlocks } from '../api'
import { setCachedWindow } from '../lib/blockCache'
import { PAGE_SIZE, scrollTopAfterPrepend, shouldLoadNewer, shouldLoadOlder } from '../lib/blockPaging'

import { t } from '@/i18n'

type Timeline = ReturnType<typeof useTimeline>

export interface ChatPagingDeps {
  topic: () => Topic | null
  focusBlock: () => string | null
  timeline: Timeline
  scrollRef: Ref<HTMLElement | null>
  atBottom: Ref<boolean>
  /** The opening history load is in flight: a jump would race with it. */
  loadingHistory: Ref<boolean>
  unseen: Ref<string[]>
  errorMsg: Ref<string | null>
  rememberScroll: (topicId: string | undefined) => void
  scrollToMessage: (id: string, behavior?: 'smooth' | 'auto') => void
  scrollToBottom: () => void
}

export function useChatPaging(deps: ChatPagingDeps) {
  const {
    topic,
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
  } = deps
  const { messages, hasMore, hasNewer } = timeline

  // --- paging back through history --------------------------------------------
  // The panel holds a WINDOW of the timeline (newest PAGE_SIZE blocks), not the
  // whole thing: a long topic was 2.1 MB / 2226 rows in one response, and the
  // browser choked on all three of transfer, JSON parse, and 2226 live DOM nodes.
  const loadingOlder = ref(false)

  // A page of very short messages can be shorter than the pane. Then there is
  // nothing to scroll, no scroll event fires, and the remaining history would be
  // unreachable — so top up until the pane actually scrolls.
  async function fillViewportIfNeeded() {
    await nextTick()
    const el = scrollRef.value
    if (!el || !hasMore.value || loadingOlder.value) return
    if (el.scrollHeight > el.clientHeight) return
    await loadOlder()
  }

  async function loadOlder() {
    const el = scrollRef.value
    const tid = topic()?.id
    if (!el || !tid || loadingOlder.value || !hasMore.value) return
    const oldest = messages.value[0]
    if (!oldest) return
    loadingOlder.value = true
    let failed = false
    try {
      const payload = await listBlocks(tid, { limit: PAGE_SIZE, before: oldest.id })
      // The user may have switched topics while this was in flight.
      if (topic()?.id !== tid) return
      // Measure right before the rows go in: prepending grows the content above
      // the viewport, so scrollTop has to be pushed down by exactly that much or
      // the timeline jumps out from under the reader (and re-triggers this
      // loader). Not when the request left: a flick keeps scrolling while it is
      // in flight, and the position from back then would pull the reader back.
      const before = { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight }
      timeline.prepend(payload.data, payload.has_more)
      await nextTick()
      const sc = scrollRef.value
      if (sc) sc.scrollTop = scrollTopAfterPrepend(before, sc.scrollHeight)
      // Trim AFTER the compensation, never before: the rows this drops are below
      // the viewport, so removing them moves nothing on screen — but they shrink
      // scrollHeight, and compensating with a scrollHeight that already excludes
      // them pulls the reader up by their height on every page. See capWindow.
      timeline.capNewest()
      if (!hasNewer.value) setCachedWindow(tid, timeline.newest())
    } catch (e) {
      failed = true
      errorMsg.value = e instanceof Error ? e.message : t('work.room.loadFailed')
    } finally {
      // Unconditional: a topic switch mid-flight must not leave the flag stuck,
      // or the new topic could never page back.
      loadingOlder.value = false
    }
    // Only now that the flag is clear can another page be pulled, if the pane
    // still isn't tall enough to scroll. Not after a failure — that would retry
    // a broken request in a tight loop.
    if (!failed) await fillViewportIfNeeded()
  }

  // --- a window opened in the middle of the history ---------------------------
  // 从一条旧消息打开对话时，显示的是它前后的一段，下面还有更新的（hasNewer）。往下
  // 翻时一页页接上，接到最新的那一段就合成一段（见 room/composables/useTimeline）。
  const loadingNewer = ref(false)

  async function loadNewer() {
    const tid = topic()?.id
    const last = messages.value.at(-1)
    if (!tid || !last || loadingNewer.value || !hasNewer.value) return
    loadingNewer.value = true
    try {
      const payload = await listBlocks(tid, { limit: PAGE_SIZE, after: last.id })
      if (topic()?.id !== tid) return
      timeline.appendNewer(payload.data, !payload.has_newer)
      if (!hasNewer.value) setCachedWindow(tid, timeline.newest())
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : t('work.room.loadFailed')
    } finally {
      loadingNewer.value = false
    }
  }

  /**
   * 停到这一条上，并让它闪一下。已经在显示的这一段里就直接滚过去；不在就取它前后
   * 的一段换上来——离最新不远的话，这一段会和最新的接上，那就还是平常的样子。
   */
  async function openAt(id: string) {
    // 落到一条上之后，这一栏不该再被「新消息来了就钉到底部」拽走。
    atBottom.value = false
    await nextTick()
    if (timeline.find(id)) {
      scrollToMessage(id)
      return
    }
    const tid = topic()?.id
    if (!tid) return
    try {
      const payload = await listBlocks(tid, { limit: PAGE_SIZE, around: id })
      if (topic()?.id !== tid) return
      unseen.value = []
      timeline.showMiddle({ blocks: payload.data, hasMore: !!payload.has_more }, !payload.has_newer)
      if (!hasNewer.value) setCachedWindow(tid, timeline.newest())
      await nextTick()
      // 整段换过，没有「从哪滑过去」可言：直接落到那一行。
      scrollToMessage(id, 'auto')
      void fillViewportIfNeeded()
    } catch (e) {
      errorMsg.value =
        e instanceof ApiError && e.status === 404 ? t('work.room.messageGone') : t('work.room.loadFailed')
    }
  }

  /** 回到最新：背后一直在收新消息的那一段直接换上来，不用再取。 */
  function backToNewest() {
    if (!hasNewer.value) return
    timeline.backToNewest()
    scrollToBottom()
  }

  // 同一个房间里，地址换了点名的那一条（又从面板跳了一次）就落过去；点名去掉了（浏览
  // 器后退到跳之前）就回到最新。换房间不归这里：loadTopic 打开新房间时自己看点名。
  watch([() => topic()?.id, () => focusBlock() ?? null], ([topicId, id], [wasTopic, wasId]) => {
    if (topicId !== wasTopic || id === wasId || loadingHistory.value) return
    if (id) void openAt(id)
    else backToNewest()
  })

  // 滚动事件：记下位置，顺带判断是不是滚到了要上一页的地方。
  function onTimelineScroll() {
    rememberScroll(topic()?.id)
    const el = scrollRef.value
    if (el && shouldLoadOlder(el.scrollTop, { hasMore: hasMore.value, loading: loadingOlder.value })) void loadOlder()
    const fromBottom = el ? el.scrollHeight - el.scrollTop - el.clientHeight : Infinity
    if (shouldLoadNewer(fromBottom, { hasNewer: hasNewer.value, loading: loadingNewer.value })) void loadNewer()
  }

  return {
    loadingOlder,
    loadingNewer,
    fillViewportIfNeeded,
    loadOlder,
    loadNewer,
    openAt,
    onTimelineScroll,
    backToNewest,
  }
}

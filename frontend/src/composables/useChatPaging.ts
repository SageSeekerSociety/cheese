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
import { beginMeasuredLayout, endMeasuredLayout } from '../lib/contentVisibility'

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
  /** 首屏分批挂行还没挂完（useRowBatch）：顶上还有没挂的行，滚到那儿不是真的到顶。 */
  rowsPending?: () => boolean
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
  const rowsPending = deps.rowsPending ?? (() => false)
  const { messages, hasMore, hasNewer } = timeline

  // --- paging back through history --------------------------------------------
  // The panel holds a WINDOW of the timeline (newest PAGE_SIZE blocks), not the
  // whole thing: a long topic was 2.1 MB / 2226 rows in one response, and the
  // browser choked on all three of transfer, JSON parse, and 2226 live DOM nodes.
  const loadingOlder = ref(false)

  // A page of very short messages, or a stretch of blocks that do not surface in
  // the room at all (`in_room:false` turn events — 295 of the newest 300 blocks
  // in a real topic), can leave the pane with less on it than fills the
  // viewport. Then there is nothing to scroll, no scroll event fires, and the
  // rest of the history is unreachable — so top up until the pane actually
  // scrolls.
  //
  // Bound on how many pages one fill may read: opening a room sweeps back past
  // the hidden tail until the pane is full (usually ~5-6 pages in a live topic),
  // and this stops it dead if a room really has nothing to show for pages on end.
  const MAX_OPENING_PULLS = 24

  // Bound on pulling consecutive pages that render NOTHING (a whole page of
  // hidden events). Every "load older" keeps going until a page adds a visible
  // row, so a page that draws nothing cannot strand the reader at the top of a
  // pane that will not move — but not forever, either.
  const MAX_EMPTY_PULLS = 12

  /**
   * The pane holds more than a screenful of history. While the opening skeleton
   * is up it stands in for the pane, so subtract it: called on `scrollHeight`
   * alone we would call the pane "full" on the skeleton's own height and reveal
   * a window that is short the moment the skeleton leaves — the newest message
   * left floating above empty space.
   */
  function paneFilled(el: HTMLElement): boolean {
    const skel = loadingHistory.value
      ? [...el.querySelectorAll<HTMLElement>('.skel')].reduce((h, node) => h + node.offsetHeight, 0)
      : 0
    return el.scrollHeight - skel > el.clientHeight
  }

  // `budget` is the pages this fill chain may still read; a fill that recurses
  // passes what is left down, so one opening can never read without end.
  async function fillViewportIfNeeded(budget = MAX_OPENING_PULLS) {
    await nextTick()
    const el = scrollRef.value
    if (!el || !hasMore.value || loadingOlder.value || rowsPending() || budget <= 0) return
    if (paneFilled(el)) return
    await loadOlder(budget)
  }

  /**
   * One `before` page, prepended to the window. Returns how many ROWS the page
   * added (`0` if it drew nothing), or null if the topic switched or there is no
   * cursor any more.
   */
  async function pullOlderPage(tid: string): Promise<{ added: number } | null> {
    // 游标是「窗口读到哪了」，不是「窗口里画得出来的最老那条」：最新那一页整页不露面
    //（事件远多于消息的房间里很常见）时窗口里一条都没有，拿 `messages[0]` 当游标就
    // 一步都翻不动——房间开出来是空的。见 useTimeline.oldestLoaded。
    const cursor = timeline.oldestLoaded()
    if (!cursor) return null
    const el = scrollRef.value
    const payload = await listBlocks(tid, { limit: PAGE_SIZE, before: cursor })
    // The user may have switched topics while this was in flight.
    if (topic()?.id !== tid) return null
    // 或者这一段被整段换过（开场那条请求回来了、点了 `?block=` 跳过去）：游标已经不作
    // 数，这一页接上去会在中间留一道缝。丢掉这次结果，让新的一段自己重新翻。
    if (timeline.oldestLoaded() !== cursor) return null
    // Measure right before the rows go in: prepending grows the content above
    // the viewport, so scrollTop has to be pushed down by exactly that much or
    // the timeline jumps out from under the reader (and re-triggers this
    // loader). Not when the request left: a flick keeps scrolling while it is
    // in flight, and the position from back then would pull the reader back.
    //
    // The rows carry content-visibility (room-row.css), so an off-screen row
    // reports its ESTIMATED height, not its real one — measuring in that state
    // would compensate by the wrong amount and the timeline would jump. The
    // measure frame lays the window out at real heights (including the page we
    // are about to add) for exactly this read-and-compensate; see
    // lib/contentVisibility.
    beginMeasuredLayout(el)
    const before = el ? { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight } : null
    const added = timeline.prepend(payload.data, payload.has_more)
    await nextTick()
    const sc = scrollRef.value
    if (sc && before) sc.scrollTop = scrollTopAfterPrepend(before, sc.scrollHeight)
    endMeasuredLayout(el)
    // Trim AFTER the compensation, never before: the rows this drops are below
    // the viewport, so removing them moves nothing on screen — but they shrink
    // scrollHeight, and compensating with a scrollHeight that already excludes
    // them pulls the reader up by their height on every page. See capWindow.
    timeline.capNewest()
    if (!hasNewer.value) setCachedWindow(tid, timeline.newest())
    return { added }
  }

  async function loadOlder(budget = MAX_OPENING_PULLS) {
    const tid = topic()?.id
    if (!scrollRef.value || !tid || loadingOlder.value || !hasMore.value) return
    loadingOlder.value = true
    let failed = false
    let pulls = 0
    try {
      // 一页画不出行就再翻一页，直到这一页真的接上了看得见的历史（画出来的行 > 0）或者
      // 历史到头。整页不露面的那一带（最新那一段多是 in_room:false 的回合事件）翻一页
      // 长一分，没有滚动事件，只翻一页就停会把上面那些消息永远卡住。
      let added = 0
      do {
        const page = await pullOlderPage(tid)
        if (page === null) return
        added = page.added
        pulls++
      } while (added === 0 && hasMore.value && pulls < MAX_EMPTY_PULLS && pulls < budget)
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
    if (!failed) await fillViewportIfNeeded(budget - pulls)
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
      // scrollIntoView 落点也算在真实高度上：这一行和它中间那些行带着
      // content-visibility，报的是估计高度，不铺开的话落点会差（见 lib/contentVisibility）。
      beginMeasuredLayout(scrollRef.value)
      scrollToMessage(id)
      endMeasuredLayout(scrollRef.value)
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
      // 整段换过，没有「从哪滑过去」可言：直接落到那一行。落点同样按真实高度量。
      beginMeasuredLayout(scrollRef.value)
      scrollToMessage(id, 'auto')
      endMeasuredLayout(scrollRef.value)
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
    if (el && !rowsPending() && shouldLoadOlder(el.scrollTop, { hasMore: hasMore.value, loading: loadingOlder.value }))
      void loadOlder()
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

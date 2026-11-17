// 已读：这一栏的每条消息谁真读到了，和我读到了哪几条。
//
// 两件事。一是读：这一页拿着的那几条编号（`Block.seq`）之间谁读过哪几段，进房间、翻
// 出更早的一页时问一次，之后靠 `reads` 帧一段段并进来。二是报：一条消息在这一栏里
// 露出大半、页面在前台（这个标签页看得见、窗口有焦点）停够一会儿，就算我读到了；
// 攒几秒报一次，切走前把攒着的先报掉。
//
// 只开着标签页、人走开了不算读（页面不在前台）；一条长得比屏幕还高的消息，露出屏幕
// 高度的大半也算露了面。人和 AI 队友读到的都在这里；AI 队友那一半后端自己记。
import type { Ref } from 'vue'
import type { useRoomRoster } from '../../components/room/composables/useRoomRoster'
import type { Block, Topic } from '../../cx_types'
import type { Receipt, ReceiptPerson, Stretch } from '../../lib/readReceipts'

import { computed, onScopeDispose, shallowReactive, watch } from 'vue'
import { useDebounceFn, useEventListener } from '@vueuse/core'

import { getMessageReads, reportMessageReads } from '../../api'
import { isAgentHandle } from '../../lib/authorship'
import { covers, mergeStretches, receiptOf, stretchesOf } from '../../lib/readReceipts'

/** 露出多少算露了面：消息自身（或比屏幕高时屏幕）高度的这一比例。 */
export const VISIBLE_SHARE = 0.6
/** 露了面、页面在前台，要连续停这么久才算读到。 */
export const DWELL_MS = 1000
/** 读到的攒这么久报一次。 */
export const REPORT_EVERY_MS = 2000

export function usePanelReceipts(deps: {
  /** 这一栏此刻读的那段对话。 */
  place: () => Topic | null
  /** 我自己的 handle。 */
  author: string
  messages: Ref<Block[]>
  /** 这条是不是我说的（`chatPanel/authorship`）。 */
  isMine: (block: Block) => boolean
  roster: ReturnType<typeof useRoomRoster>
  scrollRef: Ref<HTMLElement | null>
  contentRef: Ref<HTMLElement | null>
}) {
  /** 读的人 → 读过的那几段编号；只装这段对话的。 */
  const reads = shallowReactive(new Map<string, Stretch[]>())
  let conversation: string | null = null
  /** 读到了、还没报上去的编号。 */
  const pending = new Set<number>()
  let reportTimer: ReturnType<typeof setTimeout> | null = null

  function absorb(reader: string, ranges: Stretch[]) {
    if (!ranges.length) return
    reads.set(reader, mergeStretches([...(reads.get(reader) ?? []), ...ranges]))
  }

  // ---- 读：这一页拿着的编号之间谁读过 ----
  const span = computed<[number, number] | null>(() => {
    let first = Infinity
    let last = -Infinity
    for (const b of deps.messages.value) {
      if (b.seq == null) continue
      first = Math.min(first, b.seq)
      last = Math.max(last, b.seq)
    }
    return Number.isFinite(first) ? [first, last] : null
  })
  const fetchReads = useDebounceFn(async () => {
    const id = deps.place()?.id
    const range = span.value
    if (!id || !range) return
    try {
      const { readers } = await getMessageReads(id, range[0], range[1])
      if (deps.place()?.id !== id) return
      for (const row of readers) absorb(row.reader, row.ranges)
    } catch {
      // 读不到就不画那一圈的变化：这不是这一栏要说的错，下一次翻页或帧会再补上。
    }
  }, 300)
  watch(
    () => deps.place()?.id ?? null,
    (id) => {
      if (id === conversation) return
      void flush()
      conversation = id
      reads.clear()
      pending.clear()
    },
    { immediate: true }
  )
  // 换了一段对话，或者这一页往上、往下多拿了几条：问一次。
  watch(
    [() => deps.place()?.id ?? null, span] as const,
    ([id, next], before) => {
      const [beforeId, last] = before ?? [null, null]
      if (!id || !next) return
      if (id !== beforeId || !last || next[0] < last[0] || next[1] > last[1]) void fetchReads()
    },
    { immediate: true }
  )

  /** 一帧 `reads`：这位读的人又读到了几段。 */
  function applyFrame(frame: { conversation_id: string; reader: string; ranges: Stretch[] }) {
    if (frame.conversation_id !== conversation) return
    absorb(frame.reader, frame.ranges)
  }

  // ---- 谁能读到：这间房名册上的人和 AI 队友，按名册的顺序 ----
  const people = computed<ReceiptPerson[]>(() => {
    const roster = deps.roster
    const handles = roster.roomMembers.value.map((row) => row.member_handle)
    // 老房间没有自己的 AI 席位，答话的是项目默认那位：它也读得到。
    const seat = roster.agentSeat.value?.handle
    if (seat && !handles.includes(seat)) handles.push(seat)
    return handles.map((handle) => {
      const isAgent = isAgentHandle(handle) || !!roster.seatByHandle.value.get(handle)?.agent
      return {
        handle,
        isAgent,
        name: isAgent ? roster.agentDisplayName(handle) : roster.nameOfHandle(handle),
        avatar: isAgent ? null : roster.avatarSrc(handle),
      }
    })
  })

  /** 自己那条消息的已读情况；别人说的、还没落库的、没有别人能读的，都是 null。 */
  function receiptFor(block: Block): Receipt | null {
    if (block.seq == null || !deps.isMine(block)) return null
    return receiptOf(block.seq, block.author, people.value, reads)
  }

  // ---- 报：屏幕上露了面、停够了的那几条 ----
  async function flush() {
    if (reportTimer) clearTimeout(reportTimer)
    reportTimer = null
    const id = conversation
    if (!id || !pending.size) return
    const seqs = [...pending]
    pending.clear()
    try {
      const { ranges } = await reportMessageReads(id, stretchesOf(seqs))
      if (id === conversation) absorb(deps.author, ranges)
    } catch {
      // 没报上去的下次露面再报；读没读到不值得打断人。
    }
  }

  function noteRead(seq: number) {
    if (covers(reads.get(deps.author), seq)) return
    pending.add(seq)
    reportTimer ??= setTimeout(() => void flush(), REPORT_EVERY_MS)
  }

  const foreground = () =>
    typeof document !== 'undefined' && document.visibilityState === 'visible' && document.hasFocus()
  /** 此刻露着面的那几行（block id）和各自停够之后的那一下。 */
  const showing = new Set<string>()
  const dwell = new Map<string, ReturnType<typeof setTimeout>>()

  function seqOf(id: string): number | undefined {
    return deps.messages.value.find((b) => b.id === id)?.seq
  }
  function startDwell(id: string) {
    if (dwell.has(id) || !foreground()) return
    dwell.set(
      id,
      setTimeout(() => {
        dwell.delete(id)
        const seq = seqOf(id)
        if (seq != null && showing.has(id) && foreground()) noteRead(seq)
      }, DWELL_MS)
    )
  }
  function stopDwell(id: string) {
    const timer = dwell.get(id)
    if (timer) clearTimeout(timer)
    dwell.delete(id)
  }

  let observer: IntersectionObserver | null = null
  const observed = new WeakSet<Element>()
  function visibleEnough(entry: IntersectionObserverEntry): boolean {
    if (!entry.isIntersecting) return false
    const viewport = entry.rootBounds?.height ?? entry.boundingClientRect.height
    const tall = Math.min(entry.boundingClientRect.height, viewport)
    return tall > 0 && entry.intersectionRect.height >= VISIBLE_SHARE * tall
  }
  function ensureObserver(): IntersectionObserver | null {
    if (observer || typeof IntersectionObserver === 'undefined' || !deps.scrollRef.value) return observer
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).dataset.rowId
          if (!id) continue
          if (visibleEnough(entry)) {
            showing.add(id)
            startDwell(id)
          } else {
            showing.delete(id)
            stopDwell(id)
          }
        }
      },
      { root: deps.scrollRef.value, threshold: [0, 0.25, 0.5, 0.6, 0.75, 1] }
    )
    return observer
  }
  function observeRows() {
    const io = ensureObserver()
    const content = deps.contentRef.value
    if (!io || !content) return
    for (const el of content.querySelectorAll<HTMLElement>('[data-row-id]')) {
      if (observed.has(el)) continue
      observed.add(el)
      io.observe(el)
    }
  }
  watch(
    [() => deps.messages.value.length, () => deps.messages.value.at(-1)?.id, deps.contentRef, deps.scrollRef],
    () => observeRows(),
    { flush: 'post', immediate: true }
  )

  // 页面回到前台：露着面的那几行重新开始算；离开前台：都停下，攒着的先报掉。
  function onForeground() {
    if (foreground()) for (const id of showing) startDwell(id)
    else {
      for (const id of [...dwell.keys()]) stopDwell(id)
      void flush()
    }
  }
  useEventListener(document, 'visibilitychange', onForeground)
  useEventListener(window, 'focus', onForeground)
  useEventListener(window, 'blur', onForeground)
  useEventListener(window, 'pagehide', () => void flush())

  onScopeDispose(() => {
    void flush()
    observer?.disconnect()
    for (const id of [...dwell.keys()]) stopDwell(id)
  })

  return { receiptFor, applyFrame }
}

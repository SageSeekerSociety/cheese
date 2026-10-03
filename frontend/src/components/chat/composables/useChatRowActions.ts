// Row-level pointer affordances for the chat timeline: the touch long-press
// sheet, tap-to-reveal time, and the single hover bar that slides between rows
// (room/RoomHoverBar). Presentation only — no fetching, no route, no store.
//
// Lifted verbatim out of ChatPanel so the container could fit under the size
// cap: what stayed behind is what a row *means*, not what it looks like when
// someone points at it.
import type { ComputedRef, Ref } from 'vue'
import type { useTimeline } from '../../room/composables/useTimeline'

import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useEventListener, useResizeObserver } from '@vueuse/core'

import { useLongPress } from '../../../composables/useLongPress'

type Timeline = ReturnType<typeof useTimeline>

export interface ChatRowActionsDeps {
  timeline: Timeline
  scrollRef: Ref<HTMLElement | null>
  contentRef: Ref<HTMLElement | null>
  /** The rows on screen; the hover bar re-measures when they change. */
  rows: ComputedRef<readonly unknown[]>
  reactionPickerFor: Ref<string | null>
  /** The row being edited inline: its own long-press sheet is suppressed. */
  editingId: Ref<string | null>
}

/** 悬停条比它那一行的顶边高多少（RoomHoverBar 的 `top`），和行的上内边距。 */
const BAR_LIFT = 24
const ROW_PAD = 4

export function useChatRowActions(deps: ChatRowActionsDeps) {
  const { timeline, scrollRef, contentRef, rows, reactionPickerFor, editingId } = deps

  // ---- 触屏：长按一条消息打开它的操作面板（见 room/RoomMessageSheet）。 ----
  // 按输入方式判断，不按视口宽度：带触摸屏的笔记本两样都对，有鼠标就有悬停条。
  const touchQuery = typeof window !== 'undefined' ? window.matchMedia?.('(hover: none)') : undefined
  const touchOnly = ref(!!touchQuery?.matches)
  useEventListener(touchQuery, 'change', (e: MediaQueryListEvent) => (touchOnly.value = e.matches))

  const sheet = reactive({ id: null as string | null, open: false })
  const sheetBlock = computed(() => (sheet.id ? timeline.find(sheet.id) ?? null : null))
  // 长按是在整列上听的，按在哪一条上由落点算：一条一条挂监听，几百条消息就是几百组。
  // 只认带操作的那几行（还没送出去的那条自己带着重试和编辑），正在改的那条不算。
  useLongPress(scrollRef, (e) => {
    const row = (e.target as HTMLElement | null)?.closest<HTMLElement>('[data-mid][data-actions]')
    const id = row?.dataset.mid
    if (!id || id === editingId.value) return
    sheet.id = id
    sheet.open = true
  })

  // 续话的时间平时藏着，桌面上悬停才出现；触屏上点一下这一条把它亮出来，再点收起。
  const timeShownId = ref<string | null>(null)
  function toggleTime(target: HTMLElement) {
    if (target.closest('a, button, input, textarea, img, .mention, .md-pre')) return
    const id = target.closest<HTMLElement>('.im-row--cont[data-mid]')?.dataset.mid ?? null
    timeShownId.value = id && id !== timeShownId.value ? id : null
  }

  // ---- 悬停条：整列一个，跟着指针在消息之间滑（见 room/RoomHoverBar）。 ----
  // 指针落在一条消息上就移过去；落在行与行之间的空隙里就留在原地（从一行滑到下一行
  // 的路上不该让它一闪一闪）；落在别的行上（事件、标记）或移出整列
  // 就收起。表情选择条开着的时候钉在那一行上，不跟指针走。
  const bar = reactive({ id: null as string | null, shown: false, top: 0, jump: false })
  const barBlock = computed(() => (bar.id ? timeline.find(bar.id) ?? null : null))

  function rowTop(row: HTMLElement): number | null {
    const content = contentRef.value
    return content ? row.getBoundingClientRect().top - content.getBoundingClientRect().top : null
  }

  function showBarAt(row: HTMLElement) {
    const id = row.dataset.mid
    const top = rowTop(row)
    if (!id || top === null) return
    if (reactionPickerFor.value && reactionPickerFor.value !== id) return
    if (!bar.shown) {
      // 从收起状态出现：直接落在这一行上，只淡入，不从上一次停的地方滑过来。
      bar.jump = true
      requestAnimationFrame(() => requestAnimationFrame(() => (bar.jump = false)))
    }
    bar.id = id
    bar.top = top
    bar.shown = true
  }

  /** 指针是不是正往条上去：在条那一横带里（这一行的顶边往上 BAR_LIFT，到这一行
   *  字的上沿），在条的左边，而且这一下是往右、不往下走。往上去点上一行的东西时
   *  不算，条照常跟过去，不压着那一行。 */
  let last: { x: number; y: number } | null = null
  function headingForBar(e: MouseEvent, from: { x: number; y: number } | null): boolean {
    const content = contentRef.value
    const el = content?.querySelector<HTMLElement>('.hover-bar')
    if (!content || !el || !from) return false
    const y = e.clientY - content.getBoundingClientRect().top
    if (y < bar.top - BAR_LIFT || y > bar.top + ROW_PAD) return false
    return e.clientX < el.getBoundingClientRect().left && e.clientX > from.x && e.clientY <= from.y
  }

  function hideBar() {
    if (reactionPickerFor.value) return
    bar.shown = false
  }

  function onTimelinePointer(e: MouseEvent) {
    // 触屏上没有悬停：浏览器照样补发 mouseover，悬停条出来了就再也收不回去。这些操作
    // 在触屏上是长按一条消息打开的面板（见下面 touchOnly）。
    if (touchOnly.value) return
    const from = last
    last = { x: e.clientX, y: e.clientY }
    const target = e.target as HTMLElement | null
    if (!target || target.closest('.hover-bar')) return
    const row = target.closest<HTMLElement>('[data-mid], .notice-row, .room-happening, .dispatched, .tl-mark, .im-row')
    if (!row) return
    // 条浮在它那一行的字上面，压着上一行的末尾。指针从这一行斜着往条上走，会先经过
    // 上一行：正朝着条去的那几下不换行，不然条在指针到之前就跳走了。
    if (bar.shown && row.dataset.mid !== bar.id && headingForBar(e, from)) return
    if (row.matches('[data-actions]')) showBarAt(row)
    else hideBar()
  }

  // 它停着的那一行上面的东西变了（往上翻拼进来一页、上面一条长高了），行的位置跟着
  // 变：重新量一次，直接落过去，不演滑动——这一下不是指针在动。
  function repositionBar() {
    // 指针已移出时，焦点或菜单仍会保留这条动作栏；位置也继续属于原消息。
    if (!bar.id) return
    const row = scrollRef.value?.querySelector<HTMLElement>(`[data-mid="${bar.id}"]`)
    const top = row ? rowTop(row) : null
    if (top === null) return (bar.shown = false)
    if (top === bar.top) return
    bar.jump = true
    bar.top = top
    requestAnimationFrame(() => requestAnimationFrame(() => (bar.jump = false)))
  }
  watch(rows, () => void nextTick(repositionBar))
  // 实际聊天列改宽度、正文换行或图片长高都不会改rows数组。
  // 观察内容层的宽/高，在浏览器完成布局后重新量同一条消息。
  useResizeObserver(contentRef, repositionBar)
  watch(reactionPickerFor, (open) => {
    if (!open && !scrollRef.value?.matches(':hover')) bar.shown = false
  })

  // 从一个话题切到另一个：收起悬停条。它是绝对定位的，收起只是透明，仍停在上一个话题那一行的 translateY 上，
  // 仍算进这一栏的可滚动高度。`jump` 让它直接落回原点而不是滑回去。
  function resetBar() {
    bar.id = null
    bar.shown = false
    bar.jump = true
    bar.top = 0
    requestAnimationFrame(() => requestAnimationFrame(() => (bar.jump = false)))
  }

  return {
    sheet,
    sheetBlock,
    touchOnly,
    timeShownId,
    toggleTime,
    bar,
    barBlock,
    showBarAt,
    hideBar,
    onTimelinePointer,
    resetBar,
  }
}

// 对话时间线的键盘：整列消息是一个 Tab 停靠点（WAI-ARIA 的 feed，roving tabindex）。
//
// - Tab 进来停在上次停过的那一条，第一次进来停在最新一条；Tab / Shift+Tab 一下就出去。
// - 停在一条上：↑↓ 换一条（比可见区还高的那条先在它里面滚），Home / End 到已经载入的
//   第一条 / 最后一条，PgUp / PgDn 一屏一屏走；在最早一条上再按 ↑ 就往上拼一页。
// - Enter 进到这一条自己的按钮里（动作条、@ 和文件、表情、支线那一行、任务卡），按
//   眼睛看到的顺序：先动作条（它浮在这一行上面），再这一行里的，再挂在它下面的。
//   在里面 Tab 一个个走，走完最后一个就出了这一列；Shift+Tab 退到头、或者 Esc，回到
//   这一条上。
//
// 选 Enter 进去、而不是 Tab 从行里直接往里走：每条消息都有一条动作条，Tab 往里走的
// 话，离开这一列又得按过一整条动作条——「一下就出去」做不到。
//
// 一条消息不在当前这一条上时，它里面的按钮全都拿出 Tab 序列（tabindex=-1，roving.ts
// 的 park）；进到这一条里时只放回这一条的。按钮是很多组件、外加 v-html 画出来的
// （正文里的 @ chip 自己写着 tabindex="0"），所以在 DOM 上做，并且盯着这一块的变化：
// 新到的消息、重画过的正文，一出现就收进去。指针点在哪里照旧，只是焦点跟着记下停在
// 哪一条。
import type { Ref } from 'vue'

import { nextTick, watch } from 'vue'
import { useEventListener, useMutationObserver } from '@vueuse/core'

import {
  isTabbable,
  parkTabStop,
  restoreTabStop,
  rovingStep,
  setRoving,
  TAB_STOP,
  tabStopsIn,
} from '../../../lib/roving'

export interface TimelineKeysDeps {
  scrollRef: Ref<HTMLElement | null>
  contentRef: Ref<HTMLElement | null>
  /** 全列唯一那条动作条停在哪条消息上、亮着没有（useChatRowActions）。 */
  bar: { id: string | null; shown: boolean }
  showBarAt: (row: HTMLElement) => void
  /** 只有触屏：没有动作条，操作在长按出来的面板里。 */
  touchOnly: Ref<boolean>
  /** 表情选择条开在哪一条上：开着时 Esc 是收起它的。 */
  reactionPickerFor: Ref<string | null>
  /** 在已经载入的最早一条上再往上：拼一页更早的进来，和滚到顶一样。 */
  loadOlder: () => void
}

/** 一条（消息、事件行、「新建了任务」那一条、还没送出去的那条）。值是它的 id。 */
const ITEM = '[data-feed-item]'
/** 挂在一条消息下面、却不在那一行里面的东西（支线那一行、从它出来的任务）：值是它属于的那一条。 */
const OWNED = '[data-feed-of]'
/** 一条比可见区还高的消息里，↑↓ 一下滚多少：和浏览器自己按方向键滚的一档差不多。 */
const LINE_PX = 40

/** 这一列自己认的键：Tab 走进来、↑↓ 换一条、Home/End/PgUp/PgDn、Enter 进到这一条里、
 *  Esc 出来。只有按这些键才算人是在用键盘走这一列，别的一概不算——包括 F1–F12 和
 *  音量/媒体键：有的系统会把它们也递进网页，而浏览器的 :focus-visible 见到任何一次
 *  按键都会给焦点中的元素画框，于是点过一条消息再按一下音量键，整行就亮起一圈。 */
const TIMELINE_KEYS: ReadonlySet<string> = new Set([
  'Tab',
  'Enter',
  'Escape',
  'ArrowUp',
  'ArrowDown',
  'Home',
  'End',
  'PageUp',
  'PageDown',
])

/** 这一下按键是不是「在用键盘走这一列」（见 TIMELINE_KEYS）。 */
export function isTimelineKey(event: KeyboardEvent): boolean {
  return TIMELINE_KEYS.has(event.key)
}

export function useTimelineKeys(deps: TimelineKeysDeps) {
  const { scrollRef, contentRef, bar, showBarAt, touchOnly, reactionPickerFor, loadOlder } = deps

  /** 上次停过的那一条；null 是还没进来过，那就是最新一条。 */
  let current: string | null = null
  /** 焦点在 current 那一条自己的按钮里（Enter 进去的，或者指针点进去的）。 */
  let inside = false

  const feedEl = () => contentRef.value?.querySelector<HTMLElement>('[data-chat-feed]') ?? null
  const barEl = () => contentRef.value?.querySelector<HTMLElement>('.hover-bar') ?? null
  const itemsOf = (feed: HTMLElement) => [...feed.querySelectorAll<HTMLElement>(ITEM)]
  const idOf = (item: HTMLElement) => item.dataset.feedItem ?? ''

  /** 这个元素属于哪一条：在那一条里、挂在它下面，或者是停在它上面的动作条。 */
  function ownerOf(el: Element): string | null {
    const host = el.closest<HTMLElement>(`${ITEM}, ${OWNED}`)
    if (host) return host.dataset.feedItem ?? host.dataset.feedOf ?? null
    return el.closest('.hover-bar') ? bar.id : null
  }

  /** 按 current / inside 重排一遍谁在 Tab 序列里。只在有变化时写，重复调用没有代价。 */
  function sync() {
    const feed = feedEl()
    if (!feed) return
    const items = itemsOf(feed)
    let cur = items.find((item) => idOf(item) === current)
    if (!cur) {
      // 那一条不在了（换了房间、窗口裁掉了）：回到最新一条，焦点也不在任何一条里面。
      current = null
      inside = false
      cur = items.at(-1)
    }
    setRoving(items, cur)
    const open = inside && cur ? idOf(cur) : null
    for (const el of tabStopsIn(feed)) {
      if (el.matches(ITEM)) continue
      if (open !== null && ownerOf(el) === open) restoreTabStop(el)
      else parkTabStop(el)
    }
    const barNode = barEl()
    if (!barNode) return
    for (const el of tabStopsIn(barNode)) {
      if (open !== null && bar.id === open) restoreTabStop(el)
      else parkTabStop(el)
    }
  }

  // 行是 Vue 画的，按钮是各个组件和 v-html 画的：哪一处变了都重排。只听 tabindex：
  // sync 自己写的也会再叫醒它一次，那一次什么都不写，就停了。
  useMutationObserver(contentRef, sync, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['tabindex'],
  })
  watch(contentRef, (el) => el && sync(), { flush: 'post', immediate: true })

  /** 这一条按眼睛看到的顺序能按的东西：动作条（亮在它上面时），再这一行里的、挂在它下面的。 */
  function controlsOf(id: string): HTMLElement[] {
    const out: HTMLElement[] = []
    const barNode = barEl()
    if (barNode && bar.shown && bar.id === id) out.push(...tabStopsIn(barNode).filter(isTabbable))
    const feed = feedEl()
    if (feed) out.push(...tabStopsIn(feed).filter((el) => !el.matches(ITEM) && ownerOf(el) === id && isTabbable(el)))
    return out
  }

  function focusItem(item: HTMLElement) {
    current = idOf(item)
    inside = false
    sync()
    item.focus({ preventScroll: true })
    item.scrollIntoView?.({ block: 'nearest' })
  }

  /** 焦点进到一条里的一个按钮上，并且滚到看得见它。动作条浮在这一行顶边的上面：贴着
   *  可见区顶边的那一行，它的条在可见区外面，焦点落上去看不到。 */
  function focusControl(el: HTMLElement) {
    el.focus({ preventScroll: true })
    el.scrollIntoView?.({ block: 'nearest' })
  }

  async function enter(item: HTMLElement) {
    const id = idOf(item)
    current = id
    // 动作条被 Esc 收起过、或者指针把它带到了别的行上：叫回这一条上来。
    if (item.matches('[data-actions]') && !touchOnly.value) showBarAt(item)
    await nextTick()
    inside = true
    sync()
    const first = controlsOf(id)[0]
    if (first) return focusControl(first)
    inside = false
    sync()
  }

  /** 停着的那一条比可见区高、还没看完：先在它里面滚一档，不换到下一条。 */
  function scrollWithin(item: HTMLElement, key: string): boolean {
    const scroller = scrollRef.value
    if (!scroller) return false
    const box = item.getBoundingClientRect()
    const view = scroller.getBoundingClientRect()
    if (box.bottom <= view.top || box.top >= view.bottom) return false
    if (key === 'ArrowDown' && box.bottom > view.bottom + 1) {
      scroller.scrollTop += Math.min(LINE_PX, box.bottom - view.bottom)
      return true
    }
    if (key === 'ArrowUp' && box.top < view.top - 1) {
      scroller.scrollTop -= Math.min(LINE_PX, view.top - box.top)
      return true
    }
    return false
  }

  /** PgUp / PgDn：离这一条一屏之内最远的那一条，至少走一条。 */
  function pageStep(items: HTMLElement[], from: number, down: boolean): number {
    const height = scrollRef.value?.clientHeight ?? 0
    const origin = items[from].getBoundingClientRect().top
    let to = from
    for (let i = from + (down ? 1 : -1); i >= 0 && i < items.length; i += down ? 1 : -1) {
      if (Math.abs(items[i].getBoundingClientRect().top - origin) > height) break
      to = i
    }
    return to !== from ? to : Math.max(0, Math.min(items.length - 1, from + (down ? 1 : -1)))
  }

  function move(item: HTMLElement, key: string, feed: HTMLElement) {
    if ((key === 'ArrowUp' || key === 'ArrowDown') && scrollWithin(item, key)) return
    const items = itemsOf(feed)
    const from = items.indexOf(item)
    const to =
      key === 'PageUp' || key === 'PageDown'
        ? pageStep(items, from, key === 'PageDown')
        : rovingStep(key, from, items.length, 'vertical', false)
    if (to === null) return
    if (to !== from) return focusItem(items[to])
    if (from === 0 && (key === 'ArrowUp' || key === 'PageUp')) loadOlder()
  }

  /** 这一列之后第一个 Tab 停得住的地方（输入框，或者宿主接在对话后面的卡片）。 */
  function tabbableAfter(node: Element): HTMLElement | null {
    for (const el of document.querySelectorAll<HTMLElement>(TAB_STOP)) {
      if (node.contains(el) || !(node.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue
      if (isTabbable(el)) return el
    }
    return null
  }

  // 在一条的按钮里按 Tab。行里、挂在下面的那几个按 DOM 顺序就是看到的顺序，交给浏览器；
  // 动作条挂在时间线顶上，要接的只有它的两头。
  function onControlTab(e: KeyboardEvent, target: HTMLElement, id: string, feed: HTMLElement) {
    const list = controlsOf(id)
    const at = list.indexOf(target)
    if (at < 0) return
    const inBar = (el: HTMLElement) => !!el.closest('.hover-bar')
    const firstOwn = list.findIndex((el) => !inBar(el))
    if (e.shiftKey) {
      if (at === 0) {
        e.preventDefault()
        const item = itemsOf(feed).find((el) => idOf(el) === id)
        if (item) focusItem(item)
      } else if (at === firstOwn) {
        e.preventDefault()
        focusControl(list[at - 1])
      }
      return
    }
    if (at < list.length - 1) {
      if (inBar(target) && !inBar(list[at + 1])) {
        e.preventDefault()
        focusControl(list[at + 1])
      }
      return
    }
    // 最后一个。在行里的话，后面的都已收起，浏览器的下一个就在这一列外面；在动作条上
    // 的话，浏览器的下一个是这一条本身（条排在所有行前面），要自己送出去。
    if (!inBar(target)) return
    e.preventDefault()
    inside = false
    sync()
    const next = tabbableAfter(feed)
    if (next) next.focus()
    else target.blur()
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return
    const target = e.target as HTMLElement
    const feed = feedEl()
    if (!feed) return
    if (target.matches(ITEM) && feed.contains(target)) {
      if (e.key === 'Enter') {
        e.preventDefault()
        void enter(target)
      } else if (['ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(e.key)) {
        e.preventDefault()
        move(target, e.key, feed)
      }
      return
    }
    const id = ownerOf(target)
    if (!inside || id === null || id !== current) return
    if (e.key === 'Tab') return onControlTab(e, target, id, feed)
    if (e.key !== 'Escape') return
    // 这一下 Esc 是收起别的东西的：焦点所在那颗开着的菜单（⋯，文件的 ⋯）、表情选择条。
    // 菜单收起时自己把焦点还给那颗按钮，这里再把焦点拉回这一条就抢走了它。
    if (target.closest('[aria-expanded="true"]') || reactionPickerFor.value) return
    const item = itemsOf(feed).find((el) => idOf(el) === id)
    if (!item) return
    e.preventDefault()
    focusItem(item)
  }

  // 焦点停到哪一条上（键盘也好、指针点的也好），就记下那一条；在它的按钮上就算「在里面」。
  function onFocusin(e: FocusEvent) {
    const target = e.target as HTMLElement
    const id = ownerOf(target)
    const wasCurrent = current
    const wasInside = inside
    if (id === null) inside = false
    else {
      current = id
      inside = !target.matches(ITEM)
    }
    if (current !== wasCurrent || inside !== wasInside) sync()
  }
  function onFocusout(e: FocusEvent) {
    const to = e.relatedTarget as Node | null
    if (to && scrollRef.value?.contains(to)) return
    if (!inside) return
    inside = false
    sync()
  }

  useEventListener(scrollRef, 'keydown', onKeydown)
  useEventListener(scrollRef, 'focusin', onFocusin)
  useEventListener(scrollRef, 'focusout', onFocusout)
}

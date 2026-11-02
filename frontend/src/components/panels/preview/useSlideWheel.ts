// 幻灯片上的滚轮和触控板：翻页，按住 Ctrl / ⌘（或触控板双指捏合）是缩放。
//
// 规则照 PowerPoint 的阅读视图：
// - 这一页整页都在屏上：上下滚（或左右划）一次翻一页。
// - 这一页比屏高（适合宽度、放大了）：先滚这一页；滚到底了，再来一次新的滚动才翻到下一页，
//   到顶同理。同一次滚动（含触控板松手后的惯性）滚到底不会顺势翻走——读者是想看完这一页
//   的底部，不是想离开它。
// - 指针在备注区、缩略图轨上：那一块自己滚，不翻页（备注区标着 `data-wheel-own`，缩略图轨
//   不在监听的范围里）。
//
// 「一次」怎么认：触控板一次划动会连发几十个事件，松手后还有一段惯性。把位移攒够一个门槛
// 才算一次；翻过一页之后，要等事件停够一小段时间（惯性结束）才认下一次。鼠标滚轮一格一个
// 事件、位移一下就过门槛，所以一格翻一页；连着快速拨的几格算一次。

import { onBeforeUnmount, watch, type Ref } from 'vue'

/** 攒够这么多像素算一次翻页。鼠标一格是 100 左右，触控板轻划一下几十。 */
export const TURN_THRESHOLD = 50
/** 事件停了这么久，下一个事件就算新的一次滚动。比触控板惯性事件的间隔（约 16ms）长得多，
 *  比人两次拨动之间的停顿短。 */
export const GESTURE_GAP_MS = 180

export type WheelZoomAt = { x: number; y: number }

export type SlideWheelOptions = {
  /** 监听的那一块（舞台和页码条）。 */
  area: Ref<HTMLElement | null>
  /** 舞台：放大或适合宽度时它自己会滚。 */
  scroller: () => HTMLElement | null
  enabled: () => boolean
  turn: (direction: 1 | -1) => void
  /** 按比例缩放；`at` 是缩放中心在舞台里的位置（相对舞台可视区左上角）。没有就不缩放。 */
  zoom?: (factor: number, at: WheelZoomAt) => void
  now?: () => number
}

type Axis = 'x' | 'y'

function pixels(event: WheelEvent, page: number): { dx: number; dy: number } {
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? page : 1
  return { dx: event.deltaX * unit, dy: event.deltaY * unit }
}

/** 舞台在这个方向上还能不能往前滚（`direction` 1 是往下 / 往右）。 */
export function canScroll(el: HTMLElement | null, axis: Axis, direction: 1 | -1): boolean {
  if (!el) return false
  const [pos, size, view] =
    axis === 'y' ? [el.scrollTop, el.scrollHeight, el.clientHeight] : [el.scrollLeft, el.scrollWidth, el.clientWidth]
  if (size - view <= 1) return false
  return direction > 0 ? pos + view < size - 1 : pos > 1
}

export function useSlideWheel(options: SlideWheelOptions) {
  const now = options.now ?? (() => performance.now())
  let last = -Infinity
  let total = 0
  /** 这一次滚动里已经翻过一页，或已经在滚这一页：剩下的事件（惯性）都不再翻。 */
  let spent = false
  /** Safari 的触控板捏合不发带 ctrlKey 的 wheel，发自己的 gesture 事件；记上一下的比例。 */
  let gestureScale = 1

  function stagePoint(event: { clientX: number; clientY: number }): WheelZoomAt {
    const box = options.scroller()?.getBoundingClientRect()
    return box ? { x: event.clientX - box.left, y: event.clientY - box.top } : { x: 0, y: 0 }
  }

  function wheel(event: WheelEvent) {
    if (!options.enabled()) return
    // 自己会滚的那几块（备注区）：滚轮归它们，这里不翻页也不缩放。
    if (event.target instanceof Element && event.target.closest('[data-wheel-own]')) return
    const scroller = options.scroller()
    const { dx, dy } = pixels(event, scroller?.clientHeight ?? 800)
    if (event.ctrlKey || event.metaKey) {
      if (!options.zoom) return
      // 浏览器自己的整页缩放要拦下：读者是要放大这一页，不是放大整个界面。
      event.preventDefault()
      // 触控板捏合一下几个像素，鼠标一格一百：同一个指数，各自顺手；一个事件最多放大 / 缩小一档。
      const factor = Math.min(1.25, Math.max(0.8, Math.exp(-dy * 0.01)))
      if (factor !== 1) options.zoom(factor, stagePoint(event))
      return
    }
    const t = now()
    if (t - last > GESTURE_GAP_MS) {
      total = 0
      spent = false
    }
    last = t
    const axis: Axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y'
    const delta = axis === 'x' ? dx : dy
    if (!delta) return
    const direction: 1 | -1 = delta > 0 ? 1 : -1
    if (canScroll(scroller, axis, direction)) {
      // 这一页还没滚到头：交给浏览器滚，这一次滚动不再翻页。
      spent = true
      total = 0
      return
    }
    // 到头了（或本来就不滚）：这一下归翻页，浏览器不要再拿它去滚外面的面板。
    event.preventDefault()
    if (spent) return
    if (Math.sign(total) !== direction) total = 0
    total += delta
    if (Math.abs(total) < TURN_THRESHOLD) return
    spent = true
    total = 0
    options.turn(direction)
  }

  function gestureStart(event: Event) {
    if (!options.enabled() || !options.zoom) return
    event.preventDefault()
    gestureScale = 1
  }
  function gestureChange(event: Event) {
    if (!options.enabled() || !options.zoom) return
    event.preventDefault()
    const scale = (event as Event & { scale?: number }).scale ?? 1
    const factor = scale / gestureScale
    gestureScale = scale
    const point = event as Event & { clientX?: number; clientY?: number }
    if (factor && factor !== 1) options.zoom(factor, stagePoint({ clientX: point.clientX ?? 0, clientY: point.clientY ?? 0 }))
  }

  let bound: HTMLElement | null = null
  function unbind() {
    if (!bound) return
    bound.removeEventListener('wheel', wheel)
    bound.removeEventListener('gesturestart', gestureStart)
    bound.removeEventListener('gesturechange', gestureChange)
    bound = null
  }
  watch(
    options.area,
    (el) => {
      unbind()
      if (!el) return
      // 不是 passive：到头了要 preventDefault，免得外面的面板跟着滚。
      el.addEventListener('wheel', wheel, { passive: false })
      el.addEventListener('gesturestart', gestureStart)
      el.addEventListener('gesturechange', gestureChange)
      bound = el
    },
    { immediate: true, flush: 'post' }
  )
  onBeforeUnmount(unbind)
  return { wheel }
}

// 芝士面板取消固定之后停在哪：按住面板顶部拖到哪，就停到哪，记在这台浏览器里。
//
// **记的是比例，不是像素。** 同一个人换一块屏幕、把窗口拉宽，浮层还在原来那个相对角落：
// 记像素的话，小屏上拖到中间的位置换到大屏会缩在左上角，反过来直接出屏。
//
// 没拖过就是 `null`，那时 `style` 给 `undefined`，浮层按自己的 CSS 停在右下角——默认那一位
// 不写进存储，以后改样式的人不用替所有人清一遍。
//
// 挪过 3px 才算拖：按住顶部只是按住，不会把默认的角钉成他「选的位置」。松手才写一次存储。
//
// 窗口改小之后，拖到边上的那一版可能已经出了屏幕：按比例算出来的位置再加上面板此刻的尺寸，
// 四边至少留 `PANEL_EDGE`，窗口 resize 时重新收一次。
import type { Ref } from 'vue'

import { computed, ref } from 'vue'
import { tryOnScopeDispose, useEventListener } from '@vueuse/core'

import { storageGetJson, storageRemove, storageSetJson } from '@/lib/browserStorage'

/** 浮层离窗口边缘至少留这么多。 */
export const PANEL_EDGE = 12

/** 存下来的位置：左上角占窗口宽 / 高的比例，0–1。 */
export interface PanelSpot {
  x: number
  y: number
}

function isSpot(value: unknown): value is PanelSpot {
  if (typeof value !== 'object' || value === null) return false
  const spot = value as Partial<PanelSpot>
  return Number.isFinite(spot.x) && Number.isFinite(spot.y) && (spot.x as number) >= 0 && (spot.y as number) >= 0
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

export function useFloatingPanelSpot(key: string, el: Ref<HTMLElement | null>) {
  const saved = storageGetJson<unknown>(key)
  const spot = ref<PanelSpot | null>(isSpot(saved) ? { x: Math.min(saved.x, 1), y: Math.min(saved.y, 1) } : null)
  /** 拖起来之后才置位；按一下不算。 */
  const dragging = ref(false)

  /** 一块左上角像素位置：往里收到看得见的地方。窗口比面板还小时上下限会碰头，取小的那个。 */
  function clampPx(left: number, top: number, size?: { width: number; height: number }): { left: number; top: number } {
    // 拖动时把手里的尺寸一路传进来：每帧现量一次是「写完样式紧接着读排版」，拖起来会抖。
    const width = size?.width ?? el.value?.offsetWidth ?? 0
    const height = size?.height ?? el.value?.offsetHeight ?? 0
    const maxLeft = Math.max(PANEL_EDGE, window.innerWidth - width - PANEL_EDGE)
    const maxTop = Math.max(PANEL_EDGE, window.innerHeight - height - PANEL_EDGE)
    return { left: clamp(left, PANEL_EDGE, maxLeft), top: clamp(top, PANEL_EDGE, maxTop) }
  }

  /** 这一块左上角现在该在哪（像素）。没记过是 `null`——那时由 CSS 停在右下角。 */
  function where(): { left: number; top: number } | null {
    const now = spot.value
    if (!now) return null
    return clampPx(now.x * window.innerWidth, now.y * window.innerHeight)
  }

  const style = computed(() => {
    const at = where()
    if (!at) return undefined
    return { left: `${Math.round(at.left)}px`, top: `${Math.round(at.top)}px`, right: 'auto', bottom: 'auto' }
  })

  function save(next: PanelSpot | null) {
    if (next) storageSetJson(key, next)
    else storageRemove(key)
  }

  let from: {
    x: number
    y: number
    left: number
    top: number
    width: number
    height: number
    pointerId: number
    /** 捕获指针的那一个（头部那一行）。松手时要把捕获还给它。 */
    captor: HTMLElement
  } | null = null

  /** 只认开始那一拖的那根指头：别的指头（多指触屏）动了、抬了，都不算这一拖结束了。 */
  function ended(event: PointerEvent) {
    if (from && event.pointerId !== from.pointerId) return
    stop()
  }

  function move(event: PointerEvent) {
    const start = from
    if (!start) return
    // 另一个指头（多指触屏）的移动不算这一拖。
    if (event.pointerId !== start.pointerId) return
    // 松手发生在窗口外、或者浏览器把这一串事件丢了：`pointerup` 收不到，`from` 还留着。
    // 没有按键还跟着**开始那一拖的那根**指头走，就当作已经松手，把它收掉，别让面板漂到别处
    // 再记住，也别让下一次按下被 `from` 挡住。
    if (event.buttons === 0) {
      ended(event)
      return
    }
    const dx = event.clientX - start.x
    const dy = event.clientY - start.y
    if (!dragging.value && Math.abs(dx) + Math.abs(dy) < 3) return
    dragging.value = true
    const at = clampPx(start.left + dx, start.top + dy, start)
    spot.value = { x: at.left / window.innerWidth, y: at.top / window.innerHeight }
  }

  function stop() {
    const start = from
    if (!start) return
    from = null
    try {
      start.captor.releasePointerCapture(start.pointerId)
    } catch {
      // 浏览器已经自己收回了：不用管。
    }
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', ended)
    window.removeEventListener('pointercancel', ended)
    if (dragging.value) save(spot.value)
    dragging.value = false
  }
  // 拖到一半浮层被关掉、被换成另一种摆法：照样摘掉监听，别让它挂着。
  tryOnScopeDispose(stop)

  /**
   * 从一次按下开始拖：按下之后挪了多少，加上浮层当初的左上角。从默认的右下角开始时也有
   * 一个确定的起点。
   *
   * **不 `preventDefault`**：那会连带走掉这一下之后的 click，双击回到角落就收不到了。选中
   * 文字由顶部那一条的 `user-select: none` 挡住，触屏的滚动由 `touch-action: none` 挡住。
   */
  function startDrag(event: PointerEvent) {
    // 一只手指拖到一半，另一只又按下来：这一下不算新的一拖，否则松手时拿走的是第二根手指的
    // 捕获，面板跟着先按下的那只手停住，而且看不出为什么。
    if (event.button !== 0 || from) return
    const node = el.value
    if (!node) return
    const box = node.getBoundingClientRect()
    // 捕获指针：松手发生在窗口外、或者被系统手势接走时，`pointerup` 仍然回到这里，
    // 这样 `stop()` 一定跑得到（`GuideBubble`、`DocCommentPanel` 的拖动也是这么做）。
    //
    // 捕获挂在**按下去的那一个**（头部那一行）上，不是整块浮层上：捕获期间浏览器会把
    // click / dblclick 的目标也改成捕获元素，挂在浮层上时，双击头部就落到浮层自己头上，
    // 头部的「双击回角落」永远收不到。
    const captor = event.currentTarget instanceof HTMLElement ? event.currentTarget : node
    try {
      captor.setPointerCapture(event.pointerId)
    } catch {
      // 合成事件、或者指针已经不在了：捕获不上不影响拖。`move` 里还有一道 `buttons` 兜底。
    }
    from = {
      x: event.clientX,
      y: event.clientY,
      left: box.left,
      top: box.top,
      width: box.width,
      height: box.height,
      pointerId: event.pointerId,
      captor,
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', ended)
    // 触摸被打断、窗口失焦时不会有 pointerup：取消也算松手，否则监听一直挂着。
    window.addEventListener('pointercancel', ended)
  }

  /**
   * 窗口改了大小：按比例重新摆一遍。只是重新算，不写存储——比例没变，写进去的还是一样的数。
   */
  function settle() {
    if (spot.value) spot.value = { ...spot.value }
  }
  useEventListener(window, 'resize', settle, { passive: true })

  /** 回到默认的右下角。 */
  function reset() {
    spot.value = null
    save(null)
  }

  return { spot, style, dragging, startDrag, settle, reset }
}

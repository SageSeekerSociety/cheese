// 看图浮层里那张图的缩放和拖动：滚轮或双指缩放、按住拖动、双击放大/还原。
//
// 一倍是「整张图看得见」，往上放大到八倍。图转了 90° 时，屏幕上的宽高和原图对调，
// 「装得下」和「拖不出界」都按转过之后的样子算。
import type { Ref } from 'vue'

import { ref } from 'vue'

/** 往上放大到原文的八倍。 */
export const MAX_SCALE = 8
/** 按下和抬起之间挪过这么多像素，就算拖动，不再当成点了一下空白。 */
export const CLICK_SLOP = 4

/** 浮层四边留给工具条、左右切换、缩略图条的那一圈（像素）：图一倍时摆在这圈里面。 */
export interface Margins {
  top: number
  bottom: number
  side: number
}

export function useZoomPan(
  stage: Ref<HTMLElement | null>,
  figure: Ref<HTMLImageElement | null>,
  sideways: () => boolean,
  margins: () => Margins
) {
  const scale = ref(1)
  const offset = ref({ x: 0, y: 0 })
  /** 一倍下这张图（没转的那个元素）摆多大。字节到之前是 0。 */
  const fitted = ref({ w: 0, h: 0 })
  const moving = ref(false)

  /** 此刻按着的每一根手指/鼠标。一根是拖，两根是捏。 */
  const pointers = new Map<number, { x: number; y: number }>()
  type Drag = { from: { x: number; y: number }; offset: { x: number; y: number } }
  type Pinch = { distance: number; mid: { x: number; y: number }; scale: number; offset: { x: number; y: number } }
  let drag: Drag | null = null
  let pinch: Pinch | null = null
  let travelled = 0

  /** 屏幕上看到的宽高（转过之后的）。 */
  function shown() {
    return sideways() ? { w: fitted.value.h, h: fitted.value.w } : fitted.value
  }

  /** 装得下就按原尺寸，装不下就等比缩到工具条和缩略图条之间。 */
  function fit() {
    const el = figure.value
    const box = stage.value
    if (!el || !box || !el.naturalWidth || !el.naturalHeight) return
    const m = margins()
    const room = { w: Math.max(box.clientWidth - 2 * m.side, 1), h: Math.max(box.clientHeight - m.top - m.bottom, 1) }
    const [w, h] = sideways() ? [el.naturalHeight, el.naturalWidth] : [el.naturalWidth, el.naturalHeight]
    const ratio = Math.min(room.w / w, room.h / h, 1)
    fitted.value = { w: Math.round(el.naturalWidth * ratio), h: Math.round(el.naturalHeight * ratio) }
  }

  /** 拖不出界：放大出来的那一圈之外，图至少还有一边贴着窗口。 */
  function clamp() {
    const box = stage.value
    if (!box) return
    if (scale.value === 1) {
      offset.value = { x: 0, y: 0 }
      return
    }
    const size = shown()
    const maxX = Math.max(0, (size.w * scale.value - box.clientWidth) / 2)
    const maxY = Math.max(0, (size.h * scale.value - box.clientHeight) / 2)
    offset.value = {
      x: Math.min(maxX, Math.max(-maxX, offset.value.x)),
      y: Math.min(maxY, Math.max(-maxY, offset.value.y)),
    }
  }

  /** 缩放，并且让 (ox, oy) 这一点（相对窗口中心的像素）在缩放前后停在原地。 */
  function zoomTo(next: number, ox = 0, oy = 0) {
    const from = scale.value
    const to = Math.min(MAX_SCALE, Math.max(1, next))
    if (to === from) return
    offset.value = {
      x: ox - ((ox - offset.value.x) / from) * to,
      y: oy - ((oy - offset.value.y) / from) * to,
    }
    scale.value = to
    clamp()
  }

  function centre() {
    const box = stage.value
    if (!box) return { x: 0, y: 0 }
    // 图摆在上下留白之间的正中，缩放围着的也是那一点。
    const rect = box.getBoundingClientRect()
    const m = margins()
    return { x: rect.left + rect.width / 2, y: rect.top + m.top + (rect.height - m.top - m.bottom) / 2 }
  }

  function reset() {
    zoomTo(1)
    offset.value = { x: 0, y: 0 }
  }

  /** 按钮上的「放大一档 / 缩小一档」：以窗口中心为准。 */
  function step(factor: number) {
    zoomTo(scale.value * factor)
  }

  function onWheel(event: WheelEvent) {
    event.preventDefault()
    const middle = centre()
    zoomTo(scale.value * Math.exp(-event.deltaY * 0.0015), event.clientX - middle.x, event.clientY - middle.y)
  }

  function onPointerDown(event: PointerEvent) {
    if (event.pointerType === 'mouse' && event.button !== 0) return
    travelled = 0
    moving.value = true
    ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
    if (pointers.size === 1) {
      pinch = null
      drag = { from: { x: event.clientX, y: event.clientY }, offset: { ...offset.value } }
    } else if (pointers.size === 2) {
      drag = null
      const [a, b] = [...pointers.values()]
      pinch = {
        distance: Math.hypot(a.x - b.x, a.y - b.y),
        mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
        scale: scale.value,
        offset: { ...offset.value },
      }
    }
  }

  function onPointerMove(event: PointerEvent) {
    const previous = pointers.get(event.pointerId)
    if (!previous) return
    travelled += Math.hypot(event.clientX - previous.x, event.clientY - previous.y)
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })

    if (pointers.size >= 2 && pinch) {
      const [a, b] = [...pointers.values()]
      const distance = Math.hypot(a.x - b.x, a.y - b.y)
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      const middle = centre()
      // 两指中点挪了多少就跟着挪多少（这是拖动），两指之间张开了几倍就放大几倍。
      offset.value = {
        x: pinch.offset.x + (mid.x - pinch.mid.x),
        y: pinch.offset.y + (mid.y - pinch.mid.y),
      }
      zoomTo(pinch.scale * (pinch.distance ? distance / pinch.distance : 1), mid.x - middle.x, mid.y - middle.y)
      return
    }

    if (pointers.size === 1 && drag) {
      offset.value = {
        x: drag.offset.x + (event.clientX - drag.from.x),
        y: drag.offset.y + (event.clientY - drag.from.y),
      }
      clamp()
    }
  }

  function onPointerUp(event: PointerEvent) {
    pointers.delete(event.pointerId)
    if (pointers.size === 1) {
      // 从捏变回一根手指：接着拖，别跳。
      const [only] = [...pointers.values()]
      pinch = null
      drag = { from: { x: only.x, y: only.y }, offset: { ...offset.value } }
    } else if (pointers.size === 0) {
      pinch = null
      drag = null
      moving.value = false
    }
  }

  function onDoubleClick(event: MouseEvent) {
    const middle = centre()
    if (scale.value > 1) reset()
    else zoomTo(2.5, event.clientX - middle.x, event.clientY - middle.y)
  }

  /** 刚才那一下是不是拖过：拖过的不算「点了一下」。 */
  const dragged = () => travelled > CLICK_SLOP

  return {
    scale,
    offset,
    fitted,
    moving,
    fit,
    reset,
    step,
    dragged,
    onWheel,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onDoubleClick,
  }
}

// 预览里「两根手指缩放、点两下放大」这一套手势：PDF 的页和图片共用这一份。
//
// 双指交给 @use-gesture（触摸、Safari 的 gesture 事件、触控板的 ctrl+滚轮它都认），这里只
// 补它不管的两件：
//
//   - 双指按下去以后挡掉浏览器自己的平移。滚动容器上写的是 `touch-action: pan-x pan-y`
//     （单指照常滚、浏览器不再整页缩放），可两根手指一挪浏览器也会顺手滚这一格，和我们按
//     手指中点校正的滚动打架，画面一路往一边漂。
//   - 点两下。手势库没有这一项；只认手指（鼠标双击在桌面上是选中一个词，不能拿走）。
//
// 只认触摸、不认指针：指针那一档要在按下时 setPointerCapture，触屏笔记本上拿鼠标拖选文字
// 会被它截走。
import type { Ref } from 'vue'

import { onScopeDispose, watch } from 'vue'
import { Gesture } from '@use-gesture/vanilla'

export type PinchPhase = 'start' | 'move' | 'end'

export interface PinchZoomOptions {
  /** 现在的缩放：每一次捏都从它起算。 */
  zoom: () => number
  min: number
  max: number
  /** 关着时两种手势都不接（比如「指位置」开着，点一下是指一处，不是放大）。 */
  enabled?: () => boolean
  /** 捏的过程中每一帧报一次：缩放到多少、手指中点在屏幕哪儿（client 坐标）。 */
  onZoom: (zoom: number, origin: { x: number; y: number }, phase: PinchPhase) => void
  /** 手指点了两下，落点在屏幕哪儿。 */
  onDoubleTap?: (point: { x: number; y: number }) => void
}

/** 两次点之间最多隔多久、挪多远还算「点两下」。和 iOS 自己的双击放大差不多。 */
const DOUBLE_TAP_MS = 300
const DOUBLE_TAP_PX = 24
/** 按下到抬起挪过这么远就是在滑，不是点。 */
const TAP_SLOP_PX = 10

export function usePinchZoom(target: Ref<HTMLElement | null>, options: PinchZoomOptions) {
  const enabled = () => options.enabled?.() ?? true
  let gesture: Gesture | null = null
  let detach: (() => void) | null = null

  function bind(el: HTMLElement) {
    gesture = new Gesture(
      el,
      {
        onPinch: ({ offset: [scale], origin: [x, y], first, last, canceled }) => {
          if (!enabled() || canceled) return
          options.onZoom(scale, { x, y }, first ? 'start' : last ? 'end' : 'move')
        },
      },
      {
        pinch: {
          pointer: { touch: true },
          scaleBounds: { min: options.min, max: options.max },
          rubberband: false,
          from: () => [options.zoom(), 0],
        },
      }
    )

    const holdPan = (event: TouchEvent) => {
      if (event.touches.length >= 2 && event.cancelable && enabled()) event.preventDefault()
    }
    let down: { x: number; y: number } | null = null
    let lastTap: { x: number; y: number; at: number } | null = null
    const start = (event: TouchEvent) => {
      down = event.touches.length === 1 ? { x: event.touches[0].clientX, y: event.touches[0].clientY } : null
      if (event.touches.length > 1) lastTap = null
    }
    const end = (event: TouchEvent) => {
      const touch = event.changedTouches[0]
      if (!down || !touch || event.touches.length > 0) return
      const moved = Math.hypot(touch.clientX - down.x, touch.clientY - down.y)
      down = null
      if (moved > TAP_SLOP_PX) {
        lastTap = null
        return
      }
      const now = event.timeStamp
      const tap = { x: touch.clientX, y: touch.clientY, at: now }
      if (
        lastTap &&
        now - lastTap.at < DOUBLE_TAP_MS &&
        Math.hypot(tap.x - lastTap.x, tap.y - lastTap.y) < DOUBLE_TAP_PX &&
        enabled() &&
        options.onDoubleTap
      ) {
        lastTap = null
        // 第二下的 click 不再往下传：页上的评论标记、表格的格子都接 click。
        if (event.cancelable) event.preventDefault()
        options.onDoubleTap({ x: tap.x, y: tap.y })
        return
      }
      lastTap = tap
    }
    el.addEventListener('touchmove', holdPan, { passive: false })
    el.addEventListener('touchstart', start, { passive: true })
    el.addEventListener('touchend', end, { passive: false })
    detach = () => {
      el.removeEventListener('touchmove', holdPan)
      el.removeEventListener('touchstart', start)
      el.removeEventListener('touchend', end)
    }
  }

  function unbind() {
    gesture?.destroy()
    gesture = null
    detach?.()
    detach = null
  }

  watch(
    target,
    (el) => {
      unbind()
      if (el) bind(el)
    },
    { immediate: true, flush: 'post' }
  )
  onScopeDispose(unbind)
}

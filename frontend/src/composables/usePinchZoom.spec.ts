/**
 * 两根手指、点两下：预览里 PDF 和图片共用的那两种手势。
 *
 * 双指缩放本身交给 @use-gesture，这里不重测它；守的是自己补的两件——点两下怎么认、两根手指
 * 按下去以后浏览器的平移挡没挡住——以及「关着就都不接」。
 */
import { createApp, defineComponent, ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'

import { usePinchZoom } from './usePinchZoom'

const apps: ReturnType<typeof createApp>[] = []
afterEach(() => apps.splice(0).forEach((app) => app.unmount()))

function mount(enabled = () => true) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const onDoubleTap = vi.fn()
  const app = createApp(
    defineComponent({
      setup() {
        usePinchZoom(ref(el), { zoom: () => 1, min: 1, max: 4, enabled, onZoom: vi.fn(), onDoubleTap })
        return () => null
      },
    })
  )
  app.mount(document.createElement('div'))
  apps.push(app)
  return { el, onDoubleTap }
}

/** 一次触摸事件：happy-dom 没有 TouchEvent 的构造参数，`touches` 自己挂上去。 */
function touch(el: HTMLElement, type: string, points: [number, number][], changed: [number, number][], at: number) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  const list = (ps: [number, number][]) =>
    ps.map(([clientX, clientY], identifier) => ({ clientX, clientY, identifier }))
  Object.defineProperties(event, {
    touches: { value: list(points) },
    changedTouches: { value: list(changed) },
    timeStamp: { value: at },
  })
  el.dispatchEvent(event)
  return event
}

function tap(el: HTMLElement, x: number, y: number, at: number) {
  touch(el, 'touchstart', [[x, y]], [[x, y]], at)
  return touch(el, 'touchend', [], [[x, y]], at + 40)
}

it('同一处很快点两下才算，第二下的 click 不往下传', async () => {
  const { el, onDoubleTap } = mount()
  await Promise.resolve()
  tap(el, 100, 200, 1000)
  const second = tap(el, 104, 203, 1200)
  expect(onDoubleTap).toHaveBeenCalledOnce()
  expect(onDoubleTap).toHaveBeenCalledWith({ x: 104, y: 203 })
  expect(second.defaultPrevented).toBe(true)
})

it('隔得久、隔得远、或者其实是滑了一下，都不算点两下', async () => {
  const { el, onDoubleTap } = mount()
  await Promise.resolve()
  tap(el, 100, 200, 1000)
  tap(el, 100, 200, 1500)
  tap(el, 100, 200, 3000)
  tap(el, 180, 200, 3100)
  // 滑：按下和抬起隔了几十像素。
  touch(el, 'touchstart', [[100, 200]], [[100, 200]], 5000)
  touch(el, 'touchend', [], [[100, 260]], 5040)
  tap(el, 100, 260, 5100)
  expect(onDoubleTap).not.toHaveBeenCalled()
})

it('两根手指在动时挡掉浏览器的平移，一根手指不挡', async () => {
  const { el } = mount()
  await Promise.resolve()
  expect(touch(el, 'touchmove', [[10, 10]], [[10, 10]], 1).defaultPrevented).toBe(false)
  expect(
    touch(
      el,
      'touchmove',
      [
        [10, 10],
        [60, 60],
      ],
      [[60, 60]],
      2
    ).defaultPrevented
  ).toBe(true)
})

it('关着时两样都不接：「指位置」开着，点一下是在指一处', async () => {
  const { el, onDoubleTap } = mount(() => false)
  await Promise.resolve()
  tap(el, 100, 200, 1000)
  tap(el, 100, 200, 1150)
  expect(onDoubleTap).not.toHaveBeenCalled()
  expect(
    touch(
      el,
      'touchmove',
      [
        [10, 10],
        [60, 60],
      ],
      [[60, 60]],
      2
    ).defaultPrevented
  ).toBe(false)
})

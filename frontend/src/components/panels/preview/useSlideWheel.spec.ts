import { defineComponent, h, nextTick, ref } from 'vue'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import { useSlideWheel } from './useSlideWheel'

/** 一个能滚的舞台：内容 1000 高、可视 400 高，`scrollTop` 由用例摆。 */
function scroller(top: number) {
  const el = document.createElement('div')
  Object.defineProperties(el, {
    scrollHeight: { value: 1000 },
    clientHeight: { value: 400 },
    scrollWidth: { value: 400 },
    clientWidth: { value: 400 },
    // happy-dom 没有排版，原生的 scrollTop 会被夹回 0：换成一个普通的可写字段。
    scrollTop: { value: top, writable: true },
  })
  return el
}

async function mount(stage: HTMLElement) {
  const turns: number[] = []
  let clock = 0
  const area = ref<HTMLElement | null>(null)
  render(
    defineComponent({
      setup() {
        useSlideWheel({
          area,
          scroller: () => stage,
          enabled: () => true,
          turn: (d) => turns.push(d),
          now: () => clock,
        })
        return () => h('div', { ref: area })
      },
    })
  )
  // 监听在挂载后的 post 阶段才接上。
  await nextTick()
  const wheel = (deltaY: number, after = 16) => {
    clock += after
    const event = new WheelEvent('wheel', { deltaY, bubbles: true, cancelable: true })
    area.value!.dispatchEvent(event)
    return event
  }
  return { turns, wheel }
}

describe('useSlideWheel on a page taller than the stage', () => {
  it('scrolls the page first and leaves the event to the browser', async () => {
    const { turns, wheel } = await mount(scroller(100))
    expect(wheel(80).defaultPrevented).toBe(false)
    expect(turns).toEqual([])
  })

  it('does not turn at the bottom within the same scroll, only on a fresh one', async () => {
    const stage = scroller(100)
    const { turns, wheel } = await mount(stage)
    wheel(80)
    stage.scrollTop = 600 // reached the bottom during this scroll
    expect(wheel(80).defaultPrevented).toBe(true)
    expect(turns).toEqual([])
    wheel(80, 400)
    expect(turns).toEqual([1])
  })

  it('turns back from the top of the page', async () => {
    const { turns, wheel } = await mount(scroller(0))
    wheel(-120, 400)
    expect(turns).toEqual([-1])
  })
})

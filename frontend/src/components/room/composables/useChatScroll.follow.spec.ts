// 停在底部的人：时间线一变高，同一帧就跟到底——不管长了多少。
import { defineComponent, h, nextTick } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useChatScroll } from './useChatScroll'

let fire: () => void = () => {}

class FakeResizeObserver {
  constructor(callback: () => void) {
    fire = callback
  }
  observe() {}
  disconnect() {}
}

/** 一个高 400、内容高 `content` 的滚动框，scrollTop 照真的一样能读写。 */
function pane(content: number) {
  const el = document.createElement('div')
  let top = 0
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 400 })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => content })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (v: number) => (top = Math.max(0, Math.min(v, content - 400))),
  })
  return {
    el,
    grow(by: number) {
      content += by
    },
  }
}

afterEach(() => vi.unstubAllGlobals())

async function mounted(content: number) {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  const box = pane(content)
  let scroll!: ReturnType<typeof useChatScroll>
  render(
    defineComponent({
      setup() {
        scroll = useChatScroll()
        return () => h('div')
      },
    })
  )
  scroll.scrollRef.value = box.el
  scroll.contentRef.value = document.createElement('div')
  await nextTick()
  box.el.scrollTop = content
  return { box, scroll }
}

describe('停在底部时跟着内容走', () => {
  it('内容只长了几像素，也在这一帧跟到底', async () => {
    const { box } = await mounted(1000)
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(610)
  })

  it('人已经往上翻走了，内容变高不拽他回来', async () => {
    const { box, scroll } = await mounted(1000)
    box.el.scrollTop = 100
    scroll.atBottom.value = false
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(100)
  })
})

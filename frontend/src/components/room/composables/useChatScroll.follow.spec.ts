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
  scroll.rememberScroll('room') // 那一下滚动的 scroll 事件
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

// 刚打开的话题停在底部：底下那几行第一次画出来，比占位的估计高度高出一大截。浏览器
// 先用滚动锚定把 scrollTop 挪一点、发一个 scroll 事件，ResizeObserver 晚一步才看到
// 变高——线上实测每次整页打开都停在离最新一条 1152px 的地方。
describe('打开话题，底下的行画出来变高了', () => {
  it('浏览器自己挪的那一下不算人往上翻：最后仍停在最新一条', async () => {
    const { box, scroll } = await mounted(1000)
    expect(box.el.scrollTop).toBe(600)
    box.grow(1800)
    box.el.scrollTop = 600 + 900 // 锚定只补回了一半，离底部还差 900px
    scroll.rememberScroll('t-open') // 那个 scroll 事件
    fire() // 然后才是 ResizeObserver
    expect(box.el.scrollTop).toBe(2400)
    expect(scroll.atBottom.value).toBe(true)
    expect(scroll.restoresToBottom('t-open')).toBe(true)
  })

  it('人真的往上翻了：不跟、不拽回去，下次打开回到他停的地方', async () => {
    const { box, scroll } = await mounted(1000)
    box.el.scrollTop = 300
    scroll.rememberScroll('t-read')
    box.grow(500)
    fire()
    expect(box.el.scrollTop).toBe(300)
    expect(scroll.atBottom.value).toBe(false)
    expect(scroll.restoresToBottom('t-read')).toBe(false)
  })
})

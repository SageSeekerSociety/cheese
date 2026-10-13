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
  // 和 ChatPanel 里一样，滚动框和输入框那一块是同一层里的兄弟。
  document.createElement('div').append(box.el, document.createElement('div'))
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

  // 反方向：上面那几行收短（离屏的消息行从估计高度换成真实高度、一条长回复被折叠成
  // 阅读高度），下面同时长出来。浏览器照样自己把 scrollTop 往上挪、发一个 scroll 事件，
  // ResizeObserver 还是晚一步——和上面那一组是同一件事的两个方向。
  it('浏览器自己往上挪的那一下也不算人往上翻：最后仍停在最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    expect(box.el.scrollTop).toBe(1600)
    box.grow(-400) // 上面收短了 400
    box.grow(300) // 下面同时长出来 300，正文还在变长
    box.el.scrollTop = 1600 - 400 // 锚定把位置往上挪 400，离底部更远了
    scroll.rememberScroll('t-shrink') // 那个 scroll 事件
    fire() // 然后才是 ResizeObserver
    expect(box.el.scrollTop).toBe(1500)
    expect(scroll.atBottom.value).toBe(true)
    expect(scroll.restoresToBottom('t-shrink')).toBe(true)
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

// 正文被就地改写（话题标题刷新时每条消息都重画）：新插进来的块先按估计高度排，内容
// 短了一下，浏览器把 scrollTop 夹到那个更短的底部；真实高度随即回来，ResizeObserver
// 什么都没看到，scroll 事件里高度也和上次一样。桌面端每当项目里有件任务一动就这样
// 被甩上去几百像素，「回到最新」冒出来。
describe('正文被就地改写', () => {
  async function rewrite(box: ReturnType<typeof pane>, content: HTMLElement, to: number) {
    content.append(document.createElement('p'))
    await new Promise((r) => setTimeout(r)) // MutationObserver 的回调
    box.el.scrollTop = to // 浏览器夹下来的位置，高度已经回到原样
  }

  it('没人动，位置却往上挪了：仍算停在底部，回到最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    await rewrite(box, scroll.contentRef.value!, 1600 - 620)
    scroll.rememberScroll('t-rewrite') // 那个 scroll 事件
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
    expect(scroll.restoresToBottom('t-rewrite')).toBe(true)
  })

  it('人正好在这时用滚轮往上翻：算他翻走了，不拽回去', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.dispatchEvent(new WheelEvent('wheel', { deltaY: -300 }))
    await rewrite(box, scroll.contentRef.value!, 1300)
    scroll.rememberScroll('t-wheel')
    expect(scroll.atBottom.value).toBe(false)
    expect(box.el.scrollTop).toBe(1300)
  })
})

// 滚动框底下那一块（输入框、「谁在忙」那一行）在同一个任务里没了又回来：这一下滚动框
// 变高，停在底部的位置被夹上去；它回来时滚动框高度复原，位置却留在了上面。
// ResizeObserver 看不到，时间线里也一个节点都没变。
describe('滚动框底下那一块闪了一下', () => {
  it('没人动，位置却往上挪了：仍算停在底部，回到最新一条', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.parentElement!.lastElementChild!.append(document.createElement('div'))
    await new Promise((r) => setTimeout(r)) // MutationObserver 的回调
    box.el.scrollTop = 1600 - 196
    scroll.rememberScroll('t-composer')
    expect(scroll.atBottom.value).toBe(true)
    expect(box.el.scrollTop).toBe(1600)
  })
})

// 跳到某一条时那一段「这一下是我们自己发的滚动」窗口：它把位置和「在不在底部」
// 都推后到滚停下来的那一刻再定。窗口期内这一栏被切走、被藏起来、或者读的人动了一下
// 却没挪位置，都得各有出路——不然窗口过了，`atBottom` 就撂在开始那一刻设的 false 上，
// 「回到最新」一直亮着、新消息也不再跟（独立 review 在 e2936a36 上报出这三条）。
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

/** 一个高 `height`、内容高 `content` 的滚动框，scrollTop 照真的一样能读写。 */
function pane(content: number, height = 400) {
  const el = document.createElement('div')
  let top = 0
  let ch = height
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => ch })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => content })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (v: number) => (top = Math.max(0, Math.min(v, content - ch))),
  })
  return { el, grow: (by: number) => (content += by), setHeight: (h: number) => (ch = h) }
}

afterEach(() => vi.unstubAllGlobals())

/** 和 ChatPanel 里一样把话题 id 接上：窗口到点时要按「当时那一段」落账。 */
async function mounted(content: number) {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  const box = pane(content)
  let topicNow = 'room'
  let scroll!: ReturnType<typeof useChatScroll>
  render(
    defineComponent({
      setup() {
        scroll = useChatScroll({ topicId: () => topicNow })
        return () => h('div')
      },
    })
  )
  scroll.scrollRef.value = box.el
  scroll.contentRef.value = document.createElement('div')
  await nextTick()
  box.el.scrollTop = content
  scroll.rememberScroll(topicNow)
  return { box, scroll, switchTo: (t: string) => (topicNow = t) }
}

const settle = () => new Promise((r) => setTimeout(r, 200))

describe('窗口期内切走了话题', () => {
  it('离开的那一段照常记下它停在哪，不把新话题按旧落点写掉', async () => {
    const { box, scroll, switchTo } = await mounted(2000)
    scroll.beginOwnScroll()
    box.el.scrollTop = 2000 - 900 // 跳到上面某一条，还在滚
    scroll.rememberScroll('room')
    switchTo('next-room')
    // 切走时 topicWindow 会替刚离开的那段存位置：这一下不能被窗口吞掉。
    scroll.rememberScroll('room')
    expect(scroll.restoresToBottom('room')).toBe(false)
    await settle() // 窗口到点
    expect(scroll.atBottom.value).toBe(false)
    expect(scroll.restoresToBottom('next-room')).toBe(true)
  })
})

describe('窗口期内读的人自己动了一下，却一个像素都没挪', () => {
  it('仍算停在底部，继续跟新消息', async () => {
    const { box, scroll } = await mounted(2000)
    scroll.beginOwnScroll() // 跳到恰好是最后一条的书签
    box.el.scrollTop = 1600 // 就在底部
    box.el.dispatchEvent(new WheelEvent('wheel', { deltaY: 300 }))
    await settle()
    // 这一下没有滚动事件跟着来（位置没动），所以撤窗口时就得按位置判回来。
    expect(scroll.atBottom.value).toBe(true)
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(1610)
  })
})

describe('跳的时候这一栏还没有布局', () => {
  it('露出来之后按落点定回来，继续跟新消息', async () => {
    const { box, scroll } = await mounted(2000)
    box.setHeight(0) // v-show 藏着，或另一个页签在前
    scroll.beginOwnScroll()
    await settle() // 窗口在没有布局的情况下到点
    box.setHeight(400)
    box.el.scrollTop = 1600
    fire() // 布局回来了，观察者报一次
    expect(scroll.atBottom.value).toBe(true)
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(1610)
  })
})

describe('上一次跳完还在等布局时又跳了一次', () => {
  it('新的这一跳不会被观察者提前结掉', async () => {
    const { box, scroll } = await mounted(2000)
    box.setHeight(0)
    scroll.beginOwnScroll()
    await settle() // 第一跳在没有布局的情况下到点，等布局再落账
    box.setHeight(400)
    scroll.beginOwnScroll() // 第二跳，此刻还在底部
    fire() // 布局回来了
    expect(scroll.atBottom.value).toBe(false) // 还在滚，落点由这一跳自己停下那一刻说了算
  })
})

describe('当时没有布局、又切走了话题', () => {
  it('有布局之后按此刻的位置定回来，不卡在 false', async () => {
    const { box, scroll, switchTo } = await mounted(2000)
    box.setHeight(0)
    scroll.beginOwnScroll()
    await settle() // 等布局
    switchTo('next-room')
    box.setHeight(400)
    box.el.scrollTop = 1600 // 新话题落到了底部
    fire()
    expect(scroll.atBottom.value).toBe(true)
  })
})

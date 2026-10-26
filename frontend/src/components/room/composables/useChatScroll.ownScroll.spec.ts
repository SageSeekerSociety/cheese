// 跳到某一条时那一段「这一下是我们自己发的滚动」窗口：这一阵里不动「在不在底部」，
// 跟随由窗口自己挡（观察者与 `autoScroll` 各一处判据），位置和「在不在底部」都推后到
// 滚停下来那一刻再定一次。
//
// 曾经它靠把 `atBottom` 强设成 false 来挡跟随，于是每个出口（切话题、没有布局、读的人
// 接手、又跳一次）都得记得把它改回来，漏一个就永远卡在 false 上——三轮独立 review 各
// 抓到一个。这里钉的就是那个根不在了之后还成立的几条：切话题时离开那段照常记位置、
// 读的人动了却没挪位置、这一栏当时没有布局。
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
    // 窗口从没改过它，位置也没动，所以还是「停在底部」。
    expect(scroll.atBottom.value).toBe(true)
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(1610)
  })
})

describe('跳的时候这一栏还没有布局', () => {
  // 这一栏藏着时 `scrollIntoView` 本来就没挪动它，所以窗口本身不该改「在不在底部」。
  // （真的从一次跳转过来时，`useChatPaging.openAt` 会先把它设成 false——那是跳转自己
  // 的事，不是这个窗口该管的。）
  it('窗口本身不动「在不在底部」', async () => {
    const { box, scroll } = await mounted(2000)
    box.setHeight(0) // v-show 藏着，或另一个页签在前
    scroll.beginOwnScroll()
    await settle() // 窗口在没有布局的情况下到点
    box.setHeight(400)
    fire() // 布局回来了，观察者报一次
    expect(scroll.atBottom.value).toBe(true) // 没被这一跳改过
    box.grow(10)
    fire()
    expect(box.el.scrollTop).toBe(1610)
  })
})

describe('没有布局的时候又跳了一次', () => {
  it('还在滚的时候观察者不跟随，停下再按落点定', async () => {
    const { box, scroll } = await mounted(2000)
    box.setHeight(0)
    scroll.beginOwnScroll()
    await settle()
    box.setHeight(400)
    scroll.beginOwnScroll() // 第二跳
    box.grow(400) // 还在滚，长出来的这一截不该把它拽下去
    fire()
    expect(box.el.scrollTop).toBe(1600)
    await settle()
  })
})

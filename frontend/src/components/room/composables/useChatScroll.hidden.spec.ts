// 一栏藏起来再露出来，位置还在不在自己手上。
//
// 手机上离开「对话」去别的页签，那一格是 `v-show` 藏起来的：元素没了布局，三个数
// （clientHeight / scrollTop / scrollHeight）全报 0。Chromium 会在重新布局时把
// scrollTop 写回去，Safari 不会——真机上（2026-10-12）点开一份预览再关掉，人正读在
// 中间的那段话回到了顶上。所以位置不能交给浏览器记：藏起来的那一刻自己记下，露出来
// 的那一刻自己放回去。
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

/**
 * 一个滚动框。`hide()` 之后三个数都报 0，和真元素藏起来时一样——注意 `scrollTop`
 * 在藏起来之后**读不到**原来的值，只有 composable 自己记着。
 */
function pane(content: number) {
  const el = document.createElement('div')
  let top = 0
  let height = 400
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => height })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => (height ? content : 0) })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => (height ? top : 0),
    set: (v: number) => {
      top = Math.max(0, Math.min(v, content - 400))
    },
  })
  return {
    el,
    grow(by: number) {
      content += by
    },
    hide() {
      height = 0
    },
    show() {
      height = 400
    },
  }
}

afterEach(() => vi.unstubAllGlobals())

async function mounted(content: number, topicId?: string) {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  const box = pane(content)
  let scroll!: ReturnType<typeof useChatScroll>
  render(
    defineComponent({
      setup() {
        scroll = useChatScroll({ topicId: () => topicId })
        return () => h('div')
      },
    })
  )
  scroll.scrollRef.value = box.el
  scroll.contentRef.value = document.createElement('div')
  await nextTick()
  box.el.scrollTop = content
  scroll.rememberScroll('room')
  return { box, scroll }
}

describe('藏在别的页签后面', () => {
  it('人读在中间那段：回来还停在那条上，不在顶上', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.scrollTop = 700
    scroll.rememberScroll('t-mid')

    box.hide()
    fire() // 藏起来的那一刻：0×0 的回调
    box.grow(300) // 离开期间到了新消息
    box.show()
    fire() // 露出来，有了尺寸

    expect(box.el.scrollTop).toBe(700)
  })

  it('人停在底部：回来停在最新的那条，而不是离开时那条的位置', async () => {
    const { box } = await mounted(2000) // rememberScroll 已把它记成停在底部
    box.hide()
    fire()
    box.grow(300)
    box.show()
    fire()

    expect(box.el.scrollTop).toBe(box.el.scrollHeight - box.el.clientHeight)
  })

  it('没藏起来时长的个儿不算「离开过」：位置照旧不动', async () => {
    const { box, scroll } = await mounted(2000)
    box.el.scrollTop = 700
    scroll.rememberScroll('t-mid')
    box.grow(300)
    fire()

    expect(box.el.scrollTop).toBe(700)
  })

  it('露出来之后跟着到底仍然管用', async () => {
    const { box, scroll } = await mounted(2000)
    box.hide()
    fire()
    box.show()
    fire()

    scroll.scrollToBottom()
    await nextTick()
    await nextTick()
    box.grow(200)
    fire()

    expect(box.el.scrollTop).toBe(box.el.scrollHeight - box.el.clientHeight)
  })
})

// 藏着的时候整段换了内容（断线回来接不上，换成最新一页）：他读的那段已经不在了，
// 记着的偏移指的是新内容里随便哪个高度。要的是最新那条，露出来、下次进来都该在那儿。
describe('藏着的时候被带到最新那条', () => {
  it('露出来停在底部，不回到藏起来之前那个偏移', async () => {
    const { box, scroll } = await mounted(2000, 't-swapped')
    box.el.scrollTop = 700
    scroll.rememberScroll('t-swapped')

    box.hide()
    fire()
    scroll.scrollToBottom()
    await nextTick()
    box.grow(-500) // 换上来的那一页比原来那段短
    box.show()
    fire()

    expect(box.el.scrollTop).toBe(box.el.scrollHeight - box.el.clientHeight)
    expect(scroll.restoresToBottom('t-swapped')).toBe(true)
  })
})

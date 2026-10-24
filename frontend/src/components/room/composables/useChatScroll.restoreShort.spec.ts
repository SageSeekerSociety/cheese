// 回到一段对话、位置却落到了最底下——真机上的「退出支线被甩到频道最底」（2026-10-24）。
//
// 手机上支线是整页，退回频道时这一栏是重建的：位置按记下的偏移放回去，但那一帧时间线
// 只有**估计高度**那一份（行是分批挂上来的，`content-visibility` 先按 contain-intrinsic
// -size 占位），内容比记下位置时短，偏移被浏览器卡到当时的末端。修复前，这一「卡」被
// 当成「他本来就停在底部」，随后每长高一点就跟着往下钉，最后落到真正的最底。
//
// 这里钉住的是判据本身：没能放到目标位置时，别把它读成「在底部」，也别把那次卡住的
// 位置记下来；等内容长够了要落到目标上。
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

/** 一个滚动框：`content` 是内容高度，`height` 是视口高。 */
function pane(content: number, height = 400) {
  const el = document.createElement('div')
  let top = 0
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => height })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => content })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (v: number) => {
      top = Math.max(0, Math.min(v, content - height))
    },
  })
  return {
    el,
    grow(to: number) {
      content = to
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
        scroll = useChatScroll({ topicId: () => 'room' })
        return () => h('div')
      },
    })
  )
  scroll.scrollRef.value = box.el
  scroll.contentRef.value = document.createElement('div')
  await nextTick()
  return { box, scroll }
}

/** 读的人往上滚了一段，停在这一栏中间：这是「记下的位置不是底部」的来路。
 *  先像真的打开一段对话那样停在底部，再往上翻——从顶上往下挪不算「离开底部」。 */
function readerScrolledUp(box: ReturnType<typeof pane>, scroll: ReturnType<typeof useChatScroll>, top: number) {
  box.el.scrollTop = box.el.scrollHeight - box.el.clientHeight
  scroll.rememberScroll('room') // 停在底部
  box.el.scrollTop = top
  box.el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 })) // 他自己动的（见 useChatScroll 的输入判定）
  scroll.rememberScroll('room')
}

describe('回到一段对话时内容还没长够', () => {
  it('记下的位置放不进去：留在那儿等，不去跟着最底', async () => {
    const { box, scroll } = await mounted(2000)
    readerScrolledUp(box, scroll, 700) // 记下 700，不是底部

    // 又回到这一段，但这一刻时间线只有 900 高（离屏的行还按估计高度占着）：
    // 700 这个偏移放不进去，会被卡到 500。
    box.grow(900)
    scroll.restoreScroll('room')
    await nextTick()

    expect(box.el.scrollTop).toBe(500) // 放不进去，卡在当前末端
    expect(scroll.atBottom.value).toBe(false) // 但这不是「他停在底部」

    // 行挂上来了：内容长回真实高度，位置要落到记下的 700 上，不跟到底部。
    box.grow(2000)
    fire()
    expect(box.el.scrollTop).toBe(700)
  })

  it('长够之后仍然跟着新消息（回填没有把这一栏钉死）', async () => {
    const { box, scroll } = await mounted(2000)
    readerScrolledUp(box, scroll, 700)

    box.grow(900)
    scroll.restoreScroll('room')
    await nextTick()
    box.grow(2000)
    fire()
    expect(box.el.scrollTop).toBe(700)

    // 读的人自己滚到底：从那以后就是个正常停在底部的人，新消息要跟。
    box.el.scrollTop = 1600
    scroll.rememberScroll('room')
    box.grow(2400)
    fire()
    expect(box.el.scrollTop).toBe(box.el.scrollHeight - box.el.clientHeight)
  })

  it('人自己动过手，就听他的，不再回填', async () => {
    const { box, scroll } = await mounted(2000)
    readerScrolledUp(box, scroll, 700)

    box.grow(900)
    scroll.restoreScroll('room')
    await nextTick()
    expect(box.el.scrollTop).toBe(500)

    // 回填还没落到位，读的人自己往下滚了：他选的位置作数。
    box.el.scrollTop = 200
    box.el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }))
    box.grow(2000)
    fire()
    expect(box.el.scrollTop).toBe(200)
  })
})

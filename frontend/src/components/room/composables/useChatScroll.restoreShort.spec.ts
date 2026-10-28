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

async function mountOn(el: HTMLElement) {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  let scroll!: ReturnType<typeof useChatScroll>
  render(
    defineComponent({
      setup() {
        scroll = useChatScroll({ topicId: () => 'room' })
        return () => h('div')
      },
    })
  )
  scroll.scrollRef.value = el
  scroll.contentRef.value = document.createElement('div')
  await nextTick()
  return scroll
}

async function mounted(content: number) {
  const box = pane(content)
  return { box, scroll: await mountOn(box.el) }
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

  // 只按像素摆是不够的：行的高度一变，同一个偏移就是另一条消息了。真机上剩下的那半屏
  // 偏移就是这么来的——回来时上面的行还在估计高度上，记下的像素指到了别处。所以还记
  // 顶边那一行，按它摆回去。
  it('按顶边那一行摆回去：上面的行还在估计高度上也不指错', async () => {
    const paneHeight = 400
    const el = document.createElement('div')
    el.style.height = `${paneHeight}px`
    const heights = [100, 100, 100, 100, 100, 100, 100, 100]
    let scrollTop = 0
    const rows = heights.map((_, i) => {
      const row = document.createElement('div')
      row.dataset.rowId = `r${i}`
      row.getBoundingClientRect = () => {
        const above = heights.slice(0, i).reduce((a, b) => a + b, 0)
        const top = -scrollTop + above
        return {
          top,
          bottom: top + heights[i],
          height: heights[i],
          left: 0,
          right: 300,
          width: 300,
          x: 0,
          y: top,
          toJSON: () => ({}),
        }
      }
      el.appendChild(row)
      return row
    })
    el.getBoundingClientRect = () => ({
      top: 0,
      bottom: paneHeight,
      height: paneHeight,
      left: 0,
      right: 300,
      width: 300,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => paneHeight })
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => heights.reduce((a, b) => a + b, 0) })
    Object.defineProperty(el, 'scrollTop', {
      configurable: true,
      get: () => scrollTop,
      set: (v: number) => {
        scrollTop = Math.max(0, Math.min(v, heights.reduce((a, b) => a + b, 0) - paneHeight))
      },
    })

    const scroll = await mountOn(el)

    // 停在「顶边被 r2 压住 30px」的那一处
    el.scrollTop = el.scrollHeight - paneHeight
    scroll.rememberScroll('room') // 停在底部
    el.scrollTop = 230
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }))
    scroll.rememberScroll('room')

    // 回来时每行只报 50（估计高度），整段因此比这一屏还矮：
    // 像素 230 放不进去，会被卡到 0——那正是「屏幕上换成另一条消息」的来路。
    heights.fill(50)
    scroll.restoreScroll('room')
    await nextTick()
    expect(scroll.atBottom.value).toBe(false)

    // 真实高度回来：顶边又是 r2，还压进去 30px。
    heights.fill(100)
    fire()

    const rect = rows[2].getBoundingClientRect()
    expect(Math.round(rect.top)).toBe(-30)
    expect(scroll.atBottom.value).toBe(false)
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

  // 回填在等下一帧的时候，人常常就在这一栏里点来点去（点开一条消息、点一支线）。
  // 「按一下」不是「他自己挑了位置」——真把回填取消掉的只有**位置真的动了**的手势
  // （主干 #4780 定的那条，2026-10-24）。这两条钉住这个接缝。
  it('回填期间点一下：不算他自己挑位置，照旧回原来那条', async () => {
    const { box, scroll } = await mounted(2000)
    readerScrolledUp(box, scroll, 700)

    box.grow(900)
    scroll.restoreScroll('room')
    await nextTick()

    box.el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })) // 按住
    box.el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })) // 又松手，位置没动
    box.grow(2000)
    fire()
    expect(box.el.scrollTop).toBe(700)
  })

  it('回填期间真的拖动位置：听他的', async () => {
    const { box, scroll } = await mounted(2000)
    readerScrolledUp(box, scroll, 700)

    box.grow(900)
    scroll.restoreScroll('room')
    await nextTick()

    box.el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    box.el.scrollTop = 300 // 按住期间位置动了
    window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }))
    box.el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    box.grow(2000)
    fire()
    expect(box.el.scrollTop).toBe(300)
  })
})

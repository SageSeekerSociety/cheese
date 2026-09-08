// 软键盘盖住多少：这一份守的是「基准是 CSS 此刻认的那个 100dvh」。
//
// 手机浏览器分两派：一派把布局视口跟着键盘一起缩（`100dvh` 自己就变小了），一派
// 只缩「看得见的那块」而让页面毫不知情。同一段减法要在两派上都对，唯一的办法是
// 拿 CSS 真正在减的那个数当基准——量一根 100dvh 高的尺子——而不是任何一个可能
// 跟着缩、也可能不跟着缩的 window 属性。下面第二条用例钉的就是这个。
import { afterEach, describe, expect, it } from 'vitest'

import { hiddenByKeyboard, trackKeyboardInset } from './keyboardInset'

describe('hiddenByKeyboard', () => {
  it('键盘弹起来：布局视口没变，看得见的那块矮了 300', () => {
    expect(hiddenByKeyboard(780, 480, 0)).toBe(300)
  })

  it('浏览器自己把布局视口缩掉了（Chrome 的 resizes-content）：尺子跟着变短，不能再减一次', () => {
    expect(hiddenByKeyboard(480, 480, 0)).toBe(0)
  })

  it('页面被顶上去的那一截（iOS 滚动布局视口）也算被挡住', () => {
    expect(hiddenByKeyboard(780, 480, 100)).toBe(200)
  })

  it('没有键盘时是 0，不会算出负数', () => {
    expect(hiddenByKeyboard(780, 780, 0)).toBe(0)
    expect(hiddenByKeyboard(780, 800, 0)).toBe(0)
  })
})

type FakeVv = {
  height: number
  offsetTop: number
  addEventListener: (t: string, fn: () => void) => void
  removeEventListener: () => void
  fire: () => void
}

function fakeVisualViewport(height: number, offsetTop = 0): FakeVv {
  const listeners: (() => void)[] = []
  return {
    height,
    offsetTop,
    addEventListener: (_t: string, fn: () => void) => listeners.push(fn),
    removeEventListener: () => {},
    fire: () => listeners.forEach((fn) => fn()),
  }
}

// 那根 100dvh 的尺子：happy-dom 不做布局，getBoundingClientRect 恒为 0，所以这里
// 按尺子被插进 body 的那一刻把它的高度钉住。
function withCssViewportHeight(px: number) {
  const realAppend = document.body.appendChild.bind(document.body)
  document.body.appendChild = ((el: HTMLElement) => {
    const node = realAppend(el)
    if (el.hasAttribute?.('data-keyboard-probe')) {
      el.getBoundingClientRect = () => ({ height: px }) as DOMRect
    }
    return node
  }) as typeof document.body.appendChild
}

// 量高度合并到下一帧，所以断言前要等一帧。
const raf = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)))

afterEach(() => {
  document.documentElement.style.removeProperty('--keyboard-inset')
})

describe('trackKeyboardInset', () => {
  it('把遮挡高度写成 <html> 上的 --keyboard-inset，键盘一动就跟着改', async () => {
    const vv = fakeVisualViewport(780)
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = vv
    withCssViewportHeight(780)

    trackKeyboardInset()
    await raf()
    expect(document.documentElement.style.getPropertyValue('--keyboard-inset')).toBe('0px')

    vv.height = 480 // 键盘弹起来
    vv.fire()
    await raf()
    expect(document.documentElement.style.getPropertyValue('--keyboard-inset')).toBe('300px')
  })
})

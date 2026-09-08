// 软键盘盖住多少：这一份守的是「基准取的是布局视口」。
//
// 老写法拿 window.innerHeight 当基准。在一部分手机浏览器上它会跟着键盘一起缩，
// 于是它和 visualViewport 一样高，算出来的遮挡永远是 0，输入框一动不动——偏偏
// 就是那些浏览器最需要这段代码。下面第二条用例钉的就是这个。
import { afterEach, describe, expect, it } from 'vitest'

import { hiddenByKeyboard, trackKeyboardInset } from './keyboardInset'

describe('hiddenByKeyboard', () => {
  it('键盘弹起来：布局视口没变，看得见的那块矮了 300', () => {
    expect(hiddenByKeyboard(780, 480, 0)).toBe(300)
  })

  it('浏览器自己把布局视口缩掉了（Chrome 的 resizes-content）：不能再减一次', () => {
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

function withLayoutHeight(px: number) {
  Object.defineProperty(document.documentElement, 'clientHeight', { value: px, configurable: true })
}

afterEach(() => {
  document.documentElement.style.removeProperty('--keyboard-inset')
})

describe('trackKeyboardInset', () => {
  it('把遮挡高度写成 <html> 上的 --keyboard-inset，键盘一动就跟着改', () => {
    const vv = fakeVisualViewport(780)
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = vv
    withLayoutHeight(780)

    trackKeyboardInset()
    expect(document.documentElement.style.getPropertyValue('--keyboard-inset')).toBe('0px')

    vv.height = 480 // 键盘弹起来
    vv.fire()
    expect(document.documentElement.style.getPropertyValue('--keyboard-inset')).toBe('300px')
  })
})

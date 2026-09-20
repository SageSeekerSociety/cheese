// 软键盘盖住多少：这一份守的是「基准是布局视口本身」。
//
// 手机浏览器分两派：一派把布局视口跟着键盘一起缩（那时页面自己就矮了，不该再减
// 一次），一派只缩「看得见的那块」（那时要减掉整块）。同一段减法要在两派上都对，
// 基准就只能取布局视口 `document.documentElement.clientHeight`——它要么跟着键盘
// 一起缩、要么纹丝不动，两种都不需要分支。
//
// 注意：`trackKeyboardInset` 往 window 上挂的是全局监听，用例之间不会自己摘掉。
// 下面几条会依次触发它，靠的是「后注册的监听后触发」——每个用例都在最前面调它，
// 于是它写进去的那个值总是最后一个落的。
import { afterEach, describe, expect, it, vi } from 'vitest'

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

function setVisualViewport(vv: unknown) {
  ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = vv
}

/** happy-dom 不做布局，clientHeight 恒为 0：布局视口多高由用例钉住。 */
function fakeLayoutHeight(px: number) {
  const box = { px }
  Object.defineProperty(document.documentElement, 'clientHeight', {
    configurable: true,
    get: () => box.px,
  })
  return box
}

const appHeight = () => document.documentElement.style.getPropertyValue('--app-height')
const inset = () => document.documentElement.style.getPropertyValue('--keyboard-inset')

// 量高度合并到下一帧，所以断言前要等一帧。
const raf = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)))

afterEach(async () => {
  vi.useRealTimers() // 先还回真时钟，否则下面那一帧永远等不到
  // 再放一帧：上一条用例可能还排着「下一帧再量一次」（rAF 按注册顺序执行），让它
  // 在这一帧里跑完，别落进下一条用例的帧里把断言写花。
  await raf()
  document.documentElement.style.removeProperty('--app-height')
  document.documentElement.style.removeProperty('--keyboard-inset')
})

describe('trackKeyboardInset', () => {
  it('把布局视口高度和遮挡高度一起写在 <html> 上，键盘一动就跟着改', async () => {
    const vv = fakeVisualViewport(780)
    setVisualViewport(vv)
    fakeLayoutHeight(780)

    trackKeyboardInset()
    await raf()
    // CSS 减的就是这两个数：视口多高、被盖住多少。没有键盘时遮挡是 0。
    expect(appHeight()).toBe('780px')
    expect(inset()).toBe('0px')

    vv.height = 480 // 键盘弹起来，布局视口没动
    vv.fire()
    await raf()
    expect(inset()).toBe('300px')
  })

  it('浏览器把布局视口一起缩了：两个数都矮下去，遮挡是 0', async () => {
    const vv = fakeVisualViewport(480)
    setVisualViewport(vv)
    const box = fakeLayoutHeight(480)

    trackKeyboardInset()
    await raf()
    expect(appHeight()).toBe('480px')
    expect(inset()).toBe('0px')

    box.px = 780 // 键盘收起来
    vv.height = 780
    window.dispatchEvent(new Event('resize'))
    await raf()
    expect(appHeight()).toBe('780px')
    expect(inset()).toBe('0px')
  })

  it('只发 window.resize 的浏览器也要醒', async () => {
    // 有的浏览器键盘弹出时 visualViewport 一个事件都不发。挂在 vv 上的那两条
    // 监听这时全是哑的，只剩 window.resize 能把我们叫起来。
    const vv = fakeVisualViewport(900)
    setVisualViewport(vv)
    fakeLayoutHeight(900)

    trackKeyboardInset()
    await raf()

    vv.height = 600
    window.dispatchEvent(new Event('resize'))
    await raf()
    expect(inset()).toBe('300px')
  })

  it('焦点变化后过一拍、再过一拍各量一次（有的浏览器收键盘时什么都不发）', async () => {
    const vv = fakeVisualViewport(800)
    setVisualViewport(vv)
    fakeLayoutHeight(800)

    trackKeyboardInset()
    await raf()

    vi.useFakeTimers()
    vv.height = 500
    window.dispatchEvent(new Event('focusin'))
    vi.advanceTimersByTime(600)
    expect(inset()).toBe('300px')
  })

  it('没有 visualViewport 的浏览器：不遮挡，照报视口高度', async () => {
    setVisualViewport(undefined)
    fakeLayoutHeight(700)

    trackKeyboardInset()
    await raf()
    expect(appHeight()).toBe('700px')
    expect(inset()).toBe('0px')
  })
})

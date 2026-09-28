// 长按打开一项的操作。人能在读代码之前说出来的几条：按住够久才算；轻点、滚动、
// 提前松手都不算；真的长按了，松手时那一下点击不该再把这一项「点开」；没长按的
// 时候点击照常。
import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLongPress } from '../useLongPress'

let el: HTMLElement
let scope: ReturnType<typeof effectScope>

function pointer(type: string, init: Partial<PointerEventInit> = {}) {
  el.dispatchEvent(
    new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'touch', ...init })
  )
}

function setup(options: Parameters<typeof useLongPress>[2] = {}) {
  const onLongPress = vi.fn()
  scope = effectScope()
  scope.run(() => useLongPress(el, onLongPress, options))
  return onLongPress
}

beforeEach(async () => {
  vi.useFakeTimers()
  el = document.createElement('div')
  document.body.appendChild(el)
})

afterEach(() => {
  scope?.stop()
  el.remove()
  vi.useRealTimers()
})

// watch(flush: 'post') 在下一轮微任务里挂监听。
async function ready() {
  await Promise.resolve()
  await Promise.resolve()
}

describe('useLongPress', () => {
  it('按住半秒触发一次', async () => {
    const onLongPress = setup()
    await ready()
    pointer('pointerdown', { clientX: 10, clientY: 10 })
    vi.advanceTimersByTime(499)
    expect(onLongPress).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(onLongPress).toHaveBeenCalledTimes(1)
  })

  it('轻点一下不触发，点击照常到达', async () => {
    const onLongPress = setup()
    await ready()
    const onClick = vi.fn()
    el.addEventListener('click', onClick)
    pointer('pointerdown')
    vi.advanceTimersByTime(120)
    pointer('pointerup')
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    vi.advanceTimersByTime(1000)
    expect(onLongPress).not.toHaveBeenCalled()
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('提前松手就取消', async () => {
    const onLongPress = setup()
    await ready()
    pointer('pointerdown')
    vi.advanceTimersByTime(300)
    pointer('pointerup')
    vi.advanceTimersByTime(1000)
    expect(onLongPress).not.toHaveBeenCalled()
  })

  it('手指挪开超过 8px（在滚动）就取消，挪一点点不算', async () => {
    const onLongPress = setup()
    await ready()
    pointer('pointerdown', { clientX: 100, clientY: 100 })
    pointer('pointermove', { clientX: 103, clientY: 104 })
    vi.advanceTimersByTime(500)
    expect(onLongPress).toHaveBeenCalledTimes(1)

    pointer('pointerup')
    vi.advanceTimersByTime(10)
    pointer('pointerdown', { clientX: 100, clientY: 100 })
    pointer('pointermove', { clientX: 100, clientY: 112 })
    vi.advanceTimersByTime(1000)
    expect(onLongPress).toHaveBeenCalledTimes(1)
  })

  it('浏览器接管去滚动（pointercancel）就取消', async () => {
    const onLongPress = setup()
    await ready()
    pointer('pointerdown')
    pointer('pointercancel')
    vi.advanceTimersByTime(1000)
    expect(onLongPress).not.toHaveBeenCalled()
  })

  it('长按之后松手的那一下点击被吞掉', async () => {
    setup()
    await ready()
    // 听在外层：happy-dom 在目标自身上不遵守 stopImmediatePropagation，浏览器遵守。
    const onClick = vi.fn()
    document.body.addEventListener('click', onClick)
    pointer('pointerdown')
    vi.advanceTimersByTime(600)
    pointer('pointerup')
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    expect(onClick).not.toHaveBeenCalled()

    // 下一次轻点不受影响。
    vi.advanceTimersByTime(10)
    pointer('pointerdown')
    pointer('pointerup')
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    expect(onClick).toHaveBeenCalledTimes(1)
    document.body.removeEventListener('click', onClick)
  })

  it('按住期间拦下系统的右键菜单，平时不拦', async () => {
    setup()
    await ready()
    const idle = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    el.dispatchEvent(idle)
    expect(idle.defaultPrevented).toBe(false)

    pointer('pointerdown')
    const pressing = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    el.dispatchEvent(pressing)
    expect(pressing.defaultPrevented).toBe(true)
  })

  it('默认不认鼠标：桌面上按住鼠标不触发', async () => {
    const onLongPress = setup()
    await ready()
    pointer('pointerdown', { pointerType: 'mouse', button: 0 })
    vi.advanceTimersByTime(1000)
    expect(onLongPress).not.toHaveBeenCalled()
  })

  it('关掉时不触发', async () => {
    const onLongPress = setup({ disabled: true })
    await ready()
    pointer('pointerdown')
    vi.advanceTimersByTime(1000)
    expect(onLongPress).not.toHaveBeenCalled()
  })
})

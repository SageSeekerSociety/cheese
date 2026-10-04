/** 序列键（`g 1`）：先 G、一秒内再按数字；输入框里不认；超时不认。 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { installShortcuts } from './shortcuts'
import { defineCommands } from '.'

function press(code: string, init: KeyboardEventInit & { target?: EventTarget } = {}) {
  const event = new KeyboardEvent('keydown', { code, bubbles: true, cancelable: true, ...init })
  ;(init.target ?? window).dispatchEvent(event)
  return event
}

describe('序列键', () => {
  const undo: (() => void)[] = []
  afterEach(() => {
    undo.splice(0).forEach((f) => f())
    vi.useRealTimers()
  })

  function setup() {
    const run = vi.fn()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: {} }] })
    undo.push(defineCommands(() => [{ id: 'rail.1', title: '一', shortcut: 'g 1', run }]))
    undo.push(installShortcuts(router))
    return run
  }

  it('先 G 再 1 才跑', () => {
    const run = setup()
    press('Digit1')
    expect(run).not.toHaveBeenCalled()
    press('KeyG')
    const second = press('Digit1')
    expect(run).toHaveBeenCalledTimes(1)
    expect(second.defaultPrevented).toBe(true)
  })

  it('焦点在输入框里时不认', () => {
    const run = setup()
    const input = document.createElement('input')
    document.body.append(input)
    press('KeyG', { target: input })
    press('Digit1', { target: input })
    expect(run).not.toHaveBeenCalled()
    input.remove()
  })

  it('带修饰键的第二下不算', () => {
    const run = setup()
    press('KeyG')
    press('Digit1', { metaKey: true })
    expect(run).not.toHaveBeenCalled()
  })

  it('按 Shift+G（表上写的大写 G）也算第一下', () => {
    const run = setup()
    press('KeyG', { shiftKey: true })
    press('Digit1')
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('中间那一下被别处处理掉了，序列就断了', () => {
    const run = setup()
    press('KeyG')
    const other = new KeyboardEvent('keydown', { code: 'Slash', bubbles: true, cancelable: true })
    other.preventDefault()
    window.dispatchEvent(other)
    press('Digit1')
    expect(run).not.toHaveBeenCalled()
  })
})

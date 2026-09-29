// 快捷键大多是从浏览器手里抢来的（⌘1–9 本来是切标签页）：只有真的对上一条此刻能做
// 的命令才拦下来，对不上的照旧交回给浏览器。
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { installShortcuts } from './shortcuts'
import { defineCommands } from '.'

const cleanups: (() => void)[] = []
afterEach(() => cleanups.splice(0).forEach((undo) => undo()))

function press(init: KeyboardEventInit) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  window.dispatchEvent(event)
  return event
}

async function setup() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: {} }] })
  await router.push('/')
  cleanups.push(installShortcuts(router))
  return router
}

describe('快捷键', () => {
  it('⌘/Ctrl + 数字对上一条命令就去它说的地方', async () => {
    const router = await setup()
    cleanups.push(defineCommands(() => [{ id: 'rail.2', title: '知是', shortcut: 'mod+2', to: '/projects/p0' }]))

    const event = press({ code: 'Digit2', key: '2', ctrlKey: true })
    expect(event.defaultPrevented).toBe(true)
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p0'))
  })

  it('认的是物理键：键盘布局让 key 不是数字时照样对得上', async () => {
    const run = vi.fn()
    await setup()
    cleanups.push(defineCommands(() => [{ id: 'rail.1', title: '首页', shortcut: 'mod+1', run }]))

    press({ code: 'Digit1', key: '&', metaKey: true })
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('没有命令的数字交回给浏览器', async () => {
    await setup()
    cleanups.push(defineCommands(() => [{ id: 'rail.1', title: '首页', shortcut: 'mod+1', run: vi.fn() }]))

    expect(press({ code: 'Digit7', key: '7', ctrlKey: true }).defaultPrevented).toBe(false)
  })

  it('修饰键要对得上：少了 ⌘/Ctrl 或多按了 Shift 都不算', async () => {
    const run = vi.fn()
    await setup()
    cleanups.push(defineCommands(() => [{ id: 'rail.1', title: '首页', shortcut: 'mod+1', run }]))

    press({ code: 'Digit1', key: '1' })
    press({ code: 'Digit1', key: '!', ctrlKey: true, shiftKey: true })
    expect(run).not.toHaveBeenCalled()
  })

  it('命令撤掉以后，它的快捷键也不再拦', async () => {
    const run = vi.fn()
    await setup()
    const undo = defineCommands(() => [{ id: 'rail.1', title: '首页', shortcut: 'mod+1', run }])
    undo()

    expect(press({ code: 'Digit1', key: '1', ctrlKey: true }).defaultPrevented).toBe(false)
    expect(run).not.toHaveBeenCalled()
  })
})

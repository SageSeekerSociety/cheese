// 侧栏宽度：四处侧栏共用一个数，拖不出上下限，下次打开还是那个宽度。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

async function freshModule() {
  vi.resetModules()
  return await import('./useSidebarWidth')
}

describe('侧栏宽度', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => localStorage.clear())

  it('在一处拖过，另一处拿到的是同一个宽度', async () => {
    const { useSidebarWidth } = await freshModule()
    const project = useSidebarWidth()
    const space = useSidebarWidth()

    project.setWidth(333)

    expect(space.width.value).toBe(333)
  })

  it('拖不出上下限', async () => {
    const { useSidebarWidth, SIDEBAR_MIN, SIDEBAR_MAX } = await freshModule()
    const { width, setWidth } = useSidebarWidth()

    setWidth(20)
    expect(width.value).toBe(SIDEBAR_MIN)
    setWidth(5000)
    expect(width.value).toBe(SIDEBAR_MAX)
  })

  it('重新打开页面还是上次拖出来的宽度', async () => {
    const first = await freshModule()
    first.useSidebarWidth().setWidth(321)

    const reopened = await freshModule()

    expect(reopened.useSidebarWidth().width.value).toBe(321)
  })

  it('存的值坏了就用默认宽度', async () => {
    localStorage.setItem('cheesex.sidebarWidth', 'wide')
    const { useSidebarWidth, SIDEBAR_DEFAULT } = await freshModule()

    expect(useSidebarWidth().width.value).toBe(SIDEBAR_DEFAULT)
  })
})

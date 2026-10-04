// 平板横放（960–1180）里二级侧栏浮层和话题的工作面板浮层可以同时开着。从前是两个组件
// 各装一个 window keydown：一下 Esc 两层一起关，人分不清刚收起来的是哪一层。现在它们
// 共用一个栈——Esc 只打发最上面那层（最后打开的那层），第二下才轮到压在下面那层。
//
// 这里用一个两层都在栈里的替身来钉这条：谁后打开，Esc 就先关谁，与挂在哪个组件、
// 哪个按钮无关。module 级的栈会跨用例留着，所以每个用例都 resetModules 拿一份干净的。
import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let mod: typeof import('../useEscapeStack')

beforeEach(async () => {
  vi.resetModules()
  mod = await import('../useEscapeStack')
})
afterEach(cleanup)

function mountLayers() {
  const sidebarOpen = ref(false)
  const panelOpen = ref(false)
  const closed: string[] = []
  const Harness = defineComponent({
    setup() {
      // 关掉一层就是把它收起来（active 变假），这样它才退栈——真代码里 close 做的也
      // 正是这件事。
      mod.useEscapeLayer(sidebarOpen, () => {
        closed.push('sidebar')
        sidebarOpen.value = false
      })
      mod.useEscapeLayer(panelOpen, () => {
        closed.push('panel')
        panelOpen.value = false
      })
      return () =>
        h('div', [
          h('button', { 'data-open-sidebar': '', onClick: () => (sidebarOpen.value = true) }),
          h('button', { 'data-open-panel': '', onClick: () => (panelOpen.value = true) }),
        ])
    },
  })
  const { container } = render(Harness)
  return {
    sidebarOpen,
    panelOpen,
    closed,
    openSidebar: () => fireEvent.click(container.querySelector('[data-open-sidebar]') as HTMLElement),
    openPanel: () => fireEvent.click(container.querySelector('[data-open-panel]') as HTMLElement),
    escape: () => fireEvent.keyDown(window, { key: 'Escape' }),
  }
}

describe('useEscapeLayer', () => {
  it('两层都开着：一下 Esc 只关最后打开的那层，第二下才关另一层', async () => {
    const { closed, openSidebar, openPanel, escape } = mountLayers()
    await openSidebar()
    await openPanel()

    await escape()
    expect(closed).toEqual(['panel'])

    await escape()
    expect(closed).toEqual(['panel', 'sidebar'])
  })

  it('谁后打开谁先关，与挂在哪个组件、哪个按钮无关', async () => {
    const { closed, openSidebar, openPanel, escape } = mountLayers()
    await openPanel()
    await openSidebar()

    await escape()
    expect(closed).toEqual(['sidebar'])

    await escape()
    expect(closed).toEqual(['sidebar', 'panel'])
  })

  it('上面那层被别的方式收起（退出栈）后，Esc 落到下面那层', async () => {
    const { closed, openSidebar, openPanel, panelOpen, escape } = mountLayers()
    await openSidebar()
    await openPanel()

    // 面板被遮罩点击之类的方式收起：它退栈，剩下的顶层换成了侧栏。
    panelOpen.value = false
    await nextTick()

    await escape()
    expect(closed).toEqual(['sidebar'])
  })

  it('一层都没开着时 Esc 什么也不关', async () => {
    const { closed, escape } = mountLayers()
    await escape()
    expect(closed).toEqual([])
  })
})

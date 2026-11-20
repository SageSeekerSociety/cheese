// 平板横放那一档（960–1180）里二级侧栏默认收起，顶栏最左那颗开关开合它，人开过
// 一次就记住。宽档里它常驻，同一颗开关把它整个收起，也记住。
//
// 状态住在 useSidebarCollapse 里，readonly 之后四处共享；compact 从 Vuetify 的宽度
// 读出来（`useDisplay`），所以每个用例都要有真的 Vuetify（也才有 DisplaySymbol 可
// inject）。module 级的 `expanded` 会跨用例留着，这里每个用例都 resetModules 拿一份
// 干净模块，行为才只由那一场的 localStorage 和宽度决定。
import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const KEY = 'cheesex.sidebarExpanded'

function setWidth(px: number) {
  Object.defineProperty(window, 'innerWidth', { value: px, writable: true, configurable: true })
}

/** 挂一个只把状态写进 data-* 的壳，开关是壳里的按钮，行为经它跑一遍真代码。 */
async function mountHarness() {
  vi.resetModules()
  const mod = await import('../useSidebarCollapse')
  const Harness = defineComponent({
    setup() {
      const { compact, open, toggle, close, setExpanded } = mod.useSidebarCollapse()
      return () =>
        h('div', { 'data-compact': String(compact.value), 'data-open': String(open.value) }, [
          h('button', { 'data-toggle': '', onClick: () => toggle() }),
          h('button', { 'data-expand': '', onClick: () => setExpanded(true) }),
          h('button', { 'data-close': '', onClick: () => close() }),
        ])
    },
  })
  const view = render(Harness, { global: { plugins: [createVuetify({ components, directives })] } })
  return view
}

function openOf(container: Element): boolean {
  return (container.querySelector('[data-open]') as HTMLElement).getAttribute('data-open') === 'true'
}
function compactOf(container: Element): boolean {
  return (container.querySelector('[data-compact]') as HTMLElement).getAttribute('data-compact') === 'true'
}
const click = (container: Element, sel: string) => fireEvent.click(container.querySelector(sel) as HTMLElement)

beforeEach(() => localStorage.clear())
afterEach(cleanup)

describe('useSidebarCollapse', () => {
  it('平板横放里默认收起', async () => {
    setWidth(1024)
    const { container } = await mountHarness()

    await waitFor(() => expect(compactOf(container)).toBe(true))
    expect(openOf(container)).toBe(false)
  })

  it('开一次就记住：写进 localStorage，收起写的是关', async () => {
    setWidth(1024)
    const { container } = await mountHarness()
    await waitFor(() => expect(compactOf(container)).toBe(true))

    await click(container, '[data-toggle]')
    expect(openOf(container)).toBe(true)
    expect(localStorage.getItem(KEY)).toBe('1')

    await click(container, '[data-toggle]')
    expect(openOf(container)).toBe(false)
    expect(localStorage.getItem(KEY)).toBe('0')
  })

  it('刷新后记得上次的选择', async () => {
    localStorage.setItem(KEY, '1')
    setWidth(1024)
    const { container } = await mountHarness()

    await waitFor(() => expect(compactOf(container)).toBe(true))
    expect(openOf(container)).toBe(true)
  })

  it('close() 只把这次收起来，不动记住的选择', async () => {
    setWidth(1024)
    const { container } = await mountHarness()
    await waitFor(() => expect(compactOf(container)).toBe(true))

    await click(container, '[data-expand]')
    expect(localStorage.getItem(KEY)).toBe('1')

    await click(container, '[data-close]')
    expect(openOf(container)).toBe(false)
    // 记住的仍然是「开」：close 是这一次，不是人的选择
    expect(localStorage.getItem(KEY)).toBe('1')
  })
})

/** 顶栏那颗开关（useSidebarToggle）：两档一颗开关，各记各的。 */
async function mountToggle() {
  vi.resetModules()
  const mod = await import('../useSidebarCollapse')
  const Harness = defineComponent({
    setup() {
      const { shown, toggle } = mod.useSidebarToggle()
      return () => h('button', { 'data-shown': String(shown.value), onClick: () => toggle() })
    },
  })
  return render(Harness, { global: { plugins: [createVuetify({ components, directives })] } })
}
const shownOf = (container: Element) => container.querySelector('[data-shown]')!.getAttribute('data-shown') === 'true'

describe('useSidebarToggle', () => {
  it('宽档里侧栏默认看得见，按一下整个收起，刷新后还是收着', async () => {
    setWidth(1400)
    const first = await mountToggle()
    await waitFor(() => expect(shownOf(first.container)).toBe(true))

    await fireEvent.click(first.container.querySelector('[data-shown]') as HTMLElement)
    expect(shownOf(first.container)).toBe(false)
    // 宽档的选择不碰平板那一档记的那个。
    expect(localStorage.getItem(KEY)).toBeNull()
    cleanup()

    const again = await mountToggle()
    await waitFor(() => expect(shownOf(again.container)).toBe(false))
  })

  it('平板横放里同一颗开关开合浮层', async () => {
    setWidth(1024)
    const { container } = await mountToggle()
    await waitFor(() => expect(shownOf(container)).toBe(false))

    await fireEvent.click(container.querySelector('[data-shown]') as HTMLElement)
    expect(shownOf(container)).toBe(true)
    expect(localStorage.getItem(KEY)).toBe('1')
  })
})

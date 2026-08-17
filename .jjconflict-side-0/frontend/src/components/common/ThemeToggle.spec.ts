// 主题切换控件的端到端行为（组件层）。
//
// theme.spec.ts 测的是纯函数；这里测的是「装到真实组件里还能不能跑」——
// useAppTheme() 要拿 Vuetify 的注入上下文，要挂 watcher，要往 <html> 上写属性，
// 这几件事任何一件写错，纯函数测试都照样全绿，但界面上的开关是死的。
//
// 特别盯住一条：点「深色」之后，localStorage 里存的必须是偏好 'dark'，而点回
// 「跟随系统」必须存 'system' —— 存成解析后的结果会让「跟随系统」永久失效。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

/** 与 theme.ts 的 THEME_STORAGE_KEY 一致；这里写字面量以免固定住模块实例。 */
const THEME_STORAGE_KEY = 'cheesex.theme'

beforeAll(() => {
  // Vuetify 的 overlay（v-tooltip）会摸这两个浏览器 API，happy-dom 没有。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

/**
 * theme.ts 的偏好状态是**模块级单例**——一个 app 只有一份，多处入口（顶栏、
 * 设置页）才会显示同一个值，也才不会每挂一个组件就多注册一个系统主题监听。
 *
 * 代价是测试之间会串状态：上一个用例把偏好设成 dark 之后，下一个用例点「深色」
 * 因为值没变而根本不触发 update 事件。所以每个用例都重置模块图、重新 import，
 * 拿一份干净的单例。
 */
async function mount() {
  vi.resetModules()
  const { default: ThemeToggle } = await import('./ThemeToggle.vue')
  const vuetify = createVuetify({
    components,
    directives,
    theme: { defaultTheme: 'light', themes: { light: {}, dark: { dark: true } } },
  })
  return render(ThemeToggle, { global: { plugins: [vuetify] } })
}

/** 按可访问名称找到三个选项之一。testing-library 的 container 类型是 Element。 */
function optionButton(container: Element, label: string): HTMLElement {
  const button = container.querySelector(`[aria-label="${label}"]`)
  expect(button, `找不到「${label}」按钮`).not.toBeNull()
  return button as HTMLElement
}

describe('ThemeToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  it('渲染出三个选项，跟随系统是其中之一', async () => {
    const { container } = await mount()
    for (const label of ['跟随系统', '浅色', '深色']) {
      expect(optionButton(container, label)).toBeTruthy()
    }
  })

  it('挂载时就把当前主题写到 <html data-theme> 上', async () => {
    await mount()
    // 没存过偏好 → 跟随系统 → happy-dom 的 matchMedia 报 false → 浅色。
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('点「深色」会切换 data-theme 并把偏好持久化', async () => {
    const { container } = await mount()

    await fireEvent.click(optionButton(container, '深色'))

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
  })

  it('点回「跟随系统」存的是 system 而不是解析后的结果', async () => {
    const { container } = await mount()

    await fireEvent.click(optionButton(container, '深色'))
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')

    await fireEvent.click(optionButton(container, '跟随系统'))
    // 存 'light' 就说明存的是解析结果——那样用户的系统入夜切深色再也不会生效。
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('system')
  })

  it('重新挂载时读回上次的选择', async () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    await mount()
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})

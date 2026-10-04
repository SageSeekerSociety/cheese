/** 页签的键盘语义：只有选中那一格在 Tab 序列里；←/→/Home/End 移动并选中。 */
import { fireEvent, render } from '@testing-library/vue'
import { defineComponent, h, ref, withDirectives } from 'vue'
import { describe, expect, it } from 'vitest'

import { vRovingTabs } from './rovingTabs'

function mount() {
  const selected = ref('a')
  const Host = defineComponent(
    () => () =>
      withDirectives(
        h(
          'div',
          { role: 'tablist' },
          ['a', 'b', 'c'].map((k) =>
            h(
              'button',
              { role: 'tab', 'aria-selected': String(selected.value === k), onClick: () => (selected.value = k) },
              k
            )
          )
        ),
        [[vRovingTabs]]
      )
  )
  const view = render(Host)
  const tabs = () => Array.from(view.container.querySelectorAll<HTMLElement>('[role="tab"]'))
  return { selected, tabs }
}

describe('页签键盘', () => {
  it('只有选中那一格在 Tab 序列里', async () => {
    const { tabs, selected } = mount()
    expect(tabs().map((t) => t.tabIndex)).toEqual([0, -1, -1])
    selected.value = 'c'
    await Promise.resolve()
    await new Promise((r) => setTimeout(r, 0))
    expect(tabs().map((t) => t.tabIndex)).toEqual([-1, -1, 0])
  })

  it('方向键只移焦点、不选中；Enter（按钮的点击）才选中', async () => {
    const { tabs, selected } = mount()
    tabs()[0].focus()
    await fireEvent.keyDown(tabs()[0], { key: 'ArrowRight' })
    expect(document.activeElement).toBe(tabs()[1])
    expect(selected.value).toBe('a')
    expect(tabs().map((t) => t.tabIndex)).toEqual([-1, 0, -1])
    await fireEvent.keyDown(tabs()[1], { key: 'End' })
    expect(document.activeElement).toBe(tabs()[2])
    await fireEvent.keyDown(tabs()[2], { key: 'ArrowRight' })
    expect(document.activeElement).toBe(tabs()[0])
    await fireEvent.keyDown(tabs()[0], { key: 'ArrowLeft' })
    expect(document.activeElement).toBe(tabs()[2])
    await fireEvent.keyDown(tabs()[2], { key: 'Home' })
    expect(document.activeElement).toBe(tabs()[0])
    await fireEvent.click(tabs()[0])
    expect(selected.value).toBe('a')
  })

  it('带修饰键的方向键不管（留给浏览器和全局快捷键）', async () => {
    const { tabs, selected } = mount()
    tabs()[0].focus()
    await fireEvent.keyDown(tabs()[0], { key: 'ArrowRight', altKey: true })
    expect(selected.value).toBe('a')
  })
})

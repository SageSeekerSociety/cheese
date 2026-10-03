// 工具栏的两件事：按宽度三档自适配（窄了先收文字标签、再收颜色轮），以及禁用时
// 说清为什么按不动（title / aria-label）。都照参考物（Claude 桌面版那套标注器）。
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { SKETCH_COLORS } from './designSketch'
import DesignSketchToolbar from './DesignSketchToolbar.vue'

import { setLocale } from '@/i18n'

let observed: Map<Element, ResizeObserverCallback>
beforeEach(() => {
  setLocale('zh-CN')
  observed = new Map()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(element: Element) {
        observed.set(element, this.callback)
      }
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(overrides: Partial<Record<string, unknown>> = {}) {
  const ui = render(DesignSketchToolbar, {
    props: {
      tool: 'select',
      color: SKETCH_COLORS[0],
      canUndo: false,
      canRedo: false,
      hasStrokes: false,
      canSend: false,
      ...overrides,
    },
  })
  const root = ui.container.querySelector('.sketch-toolbar') as HTMLElement
  return { ui, root }
}

/** 量出来的宽度：通知工具栏切档。 */
async function resize(root: HTMLElement, width: number) {
  observed.get(root)!([{ contentRect: { width } }] as unknown as ResizeObserverEntry[], {} as ResizeObserver)
  await waitFor(() => expect(root.getAttribute('data-tier')).not.toBeNull())
}

const labels = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-toolbar__label').length
const colors = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-toolbar__color').length

it('宽档：工具名和颜色轮都摆着', async () => {
  const { ui, root } = mount()
  await resize(root, 700)
  expect(root.getAttribute('data-tier')).toBe('full')
  expect(labels(ui)).toBe(8)
  expect(colors(ui)).toBe(5)
})

it('中档：收掉文字标签，颜色轮留着', async () => {
  const { ui, root } = mount()
  await resize(root, 300)
  expect(root.getAttribute('data-tier')).toBe('compact')
  expect(labels(ui)).toBe(0)
  expect(colors(ui)).toBe(5)
  // 名字没丢，退到 aria-label。
  expect(ui.getByRole('button', { name: '矩形' })).toBeTruthy()
})

it('窄档：颜色轮也收掉，只剩图标', async () => {
  const { ui, root } = mount()
  await resize(root, 180)
  expect(root.getAttribute('data-tier')).toBe('minimal')
  expect(labels(ui)).toBe(0)
  expect(colors(ui)).toBe(0)
})

it('再窄整条收起：加 inert、标上 concealed，收起来的按钮 Tab 不到也点不到', async () => {
  const { ui, root } = mount({ hasStrokes: true })
  await resize(root, 100)
  expect(root.getAttribute('data-tier')).toBe('concealed')
  expect(root.hasAttribute('data-concealed')).toBe(true)
  expect(root.hasAttribute('inert')).toBe(true)
  expect(labels(ui)).toBe(0)
  expect(colors(ui)).toBe(0)
})

it('没量到宽度时不收起（保持 full），别因为一次量不到就把工具藏了', async () => {
  const { ui, root } = mount()
  expect(root.getAttribute('data-tier')).toBe('full')
  expect(labels(ui)).toBe(8)
})

it('禁用时说清为什么：撤销/重做/清空各有各的说法', async () => {
  const { root } = mount()
  const title = (label: string) => (root.querySelector(`[title="${label}"]`) as HTMLElement | null) ?? null
  expect(title('还没有可撤销的操作')).toBeTruthy()
  expect(title('还没有可重做的操作')).toBeTruthy()
  expect(title('还没有画任何标注')).toBeTruthy()
})

it('撤销/重做能按的时候 title 换成快捷键提示', async () => {
  const { root } = mount({ canUndo: true, canRedo: true })
  // 能按了就不再挂「为什么不能按」的理由。
  expect(root.querySelector('[title="还没有可撤销的操作"]')).toBeNull()
  expect(root.querySelector('[title="还没有可重做的操作"]')).toBeNull()
})

it('「加入对话」禁用时把原因挂在 title 上：先画东西、再写一句话', async () => {
  const noStrokes = mount()
  let send = noStrokes.root.querySelector('button.is-primary') as HTMLButtonElement
  expect(send.title).toBe('还没有画任何标注')

  cleanup()
  const noNote = mount({ hasStrokes: true })
  send = noNote.root.querySelector('button.is-primary') as HTMLButtonElement
  expect(send.title).toBe('先写一句要改什么')
})

it('正在生成标注图时理由换成「正在生成」', async () => {
  const { root } = mount({ hasStrokes: true, canSend: true, busy: true })
  const send = root.querySelector('button.is-primary') as HTMLButtonElement
  expect(send.disabled).toBe(true)
  expect(send.title).toBe('正在生成标注图')
})

it('图上正在编辑文字时，发送禁用并说明原因', async () => {
  const { root } = mount({ hasStrokes: true, canSend: false, textEditing: true })
  const send = root.querySelector('button.is-primary') as HTMLButtonElement
  expect(send.title).toBe('先把图上的文字确认或取消')
})

it('默认选中的颜色是红（第一颗）', async () => {
  const { ui } = mount()
  const active = ui.container.querySelector('.sketch-toolbar__color.is-active') as HTMLElement
  expect(active.getAttribute('aria-label')).toBe('标注颜色 #E03131')
})

const styles = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-toolbar__style')
const activeStyle = (ui: ReturnType<typeof render>) =>
  ui.container.querySelector('.sketch-toolbar__style.is-active') as HTMLElement | null

it('涂黑时：颜色轮换成样式行（三颗），颜色轮一颗不留', async () => {
  const { ui } = mount({ tool: 'redact', redact: true, redactStyle: 'mosaic' })
  expect(colors(ui)).toBe(0)
  expect(styles(ui)).toHaveLength(3)
  expect(activeStyle(ui)?.getAttribute('aria-label')).toBe('马赛克')
})

it('没在涂黑时：样式行不出现，颜色轮照旧', async () => {
  const { ui } = mount()
  expect(colors(ui)).toBe(5)
  expect(styles(ui)).toHaveLength(0)
})

it('点样式发 restyle，带上那颗的样式名', async () => {
  const { ui } = mount({ tool: 'redact', redact: true, redactStyle: 'solid' })
  await fireEvent.click(styles(ui)[2])
  expect(ui.emitted('restyle')?.[0]).toEqual(['noise'])
})

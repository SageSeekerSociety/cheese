// 代码查看器怎么把一个选区读成行号：起止两头各落在哪一行；终点落在下一行最开头（三击、
// 拖过行尾）时那一行一个字也没选，不算进去。
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import PreviewCode from './PreviewCode.vue'

const SOURCE = 'a = 1\nb = 2\nc = 3\nd = 4\n'

afterEach(() => {
  cleanup()
  window.getSelection()?.removeAllRanges()
})

function select(start: [Node, number], end: [Node, number]) {
  const range = document.createRange()
  range.setStart(...start)
  range.setEnd(...end)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
}

function setup() {
  const ui = render(PreviewCode, { props: { source: SOURCE, language: 'python' } })
  const code = (line: number) => ui.container.querySelector(`[data-line="${line}"] [data-code]`)!
  const lines = () => ui.emitted<[{ start: number; end: number; text: string }]>().lines ?? []
  return { ui, code, lines }
}

it('终点落在下一行最开头时，那一行不算', async () => {
  const { code, lines } = setup()
  select([code(2), 0], [code(4), 0])
  await fireEvent.mouseUp(code(4))
  expect(lines().at(-1)![0]).toEqual({ start: 2, end: 3, text: 'b = 2\nc = 3' })
})

it('一行里选几个字，就是那一行', async () => {
  const { code, lines } = setup()
  const text = code(3).firstChild!
  select([text, 0], [text, 1])
  await fireEvent.mouseUp(code(3))
  expect(lines().at(-1)![0]).toMatchObject({ start: 3, end: 3 })
})

// 三击、或者拖到行尾右边那一片空白：终点不落在正文的文字节点上，而报在**行元素**上，
// 整行是选完了的。这两种形状原来被判成「一个字都没选到」，末行因此少算一行。
it('终点报在行元素上（整行选完）时，那一行算在内', async () => {
  const { ui, code, lines } = setup()
  const line = ui.container.querySelector('[data-line="4"]')!
  select([code(2), 0], [line, line.childNodes.length])
  await fireEvent.mouseUp(line)
  expect(lines().at(-1)![0]).toEqual({ start: 2, end: 4, text: 'b = 2\nc = 3\nd = 4' })
})

it('终点报在行元素的最开头（行号那一列、行首）时，那一行不算', async () => {
  const { ui, code, lines } = setup()
  const line = ui.container.querySelector('[data-line="4"]')!
  select([code(2), 0], [line, 0])
  await fireEvent.mouseUp(line)
  expect(lines().at(-1)![0]).toEqual({ start: 2, end: 3, text: 'b = 2\nc = 3' })
})

it('终点报在装着所有行的那一层、指着末行之后时，那一行算在内', async () => {
  const { ui, code, lines } = setup()
  const all = ui.container.querySelector('.code-view__lines')!
  select([code(2), 0], [all, [...all.childNodes].indexOf(ui.container.querySelector('[data-line="4"]')!) + 1])
  await fireEvent.mouseUp(all)
  expect(lines().at(-1)![0]).toEqual({ start: 2, end: 4, text: 'b = 2\nc = 3\nd = 4' })
})

// 键盘上 Shift+方向键扩出来的选区，落点是 `keyup`——它只报给收得住焦点的那一层。
// 容器不可聚焦时（`tabindex` 不在），焦点在别处，这一条永远等不到，等于键盘读者指不了。
it('容器收得住焦点，键盘扩出来的选区报得出来', async () => {
  const { ui, code, lines } = setup()
  const scroll = ui.container.querySelector<HTMLElement>('.code-view__scroll')!
  expect(scroll.tabIndex).toBe(0)
  scroll.focus()
  expect(document.activeElement).toBe(scroll)
  select([code(2), 0], [code(3), 1])
  await fireEvent.keyUp(scroll, { key: 'ArrowDown', shiftKey: true })
  expect(lines().at(-1)![0]).toMatchObject({ start: 2, end: 3 })
})

it('没选中东西就不报', async () => {
  const { code, lines } = setup()
  await fireEvent.mouseUp(code(1))
  expect(lines()).toHaveLength(0)
})

it('指过的那几行留一层底色，下一次在代码里按下时撤掉', async () => {
  const { ui, code } = setup()
  await fireEvent.click(ui.container.querySelector('[data-line="2"] [data-gutter]')!)
  expect(ui.container.querySelector('[data-line="2"]')!.className).toContain('code-view__line--picked')
  await fireEvent.mouseDown(code(1))
  expect(ui.container.querySelector('.code-view__line--picked')).toBeNull()
})

// 这一档的开销在行上（每行两个元素，高亮过的还多几十个）。量过：一整份 700 KB 的网页
// 源码 14 866 行，滚动一帧 134 ms（546 px 面板、CPU 降速到 1/4）；先摆 1 000 行是
// 261 ms 出画面、帧 16.7 ms。所以一次只摆一段，底下说清总共多少行。
describe('长文件按段摆', () => {
  const LONG = Array.from({ length: 2600 }, (_, i) => `第 ${i + 1} 行`).join('\n')

  // 底下那颗「再看一段」是 `BaseButton`，它包着 Vuetify 的 `<v-btn>`；这一档验的是
  // 摆几行，不是那颗按钮长什么样，所以只留一个真 button。
  const stubs = { VBtn: { template: '<button v-bind="$attrs"><slot /></button>' } }

  function long() {
    const ui = render(PreviewCode, { props: { source: LONG, language: null }, global: { stubs } })
    const rows = () => ui.container.querySelectorAll('.code-view__line').length
    return { ui, rows }
  }

  it('先只摆前 1000 行，行号从第一行数', () => {
    const { ui, rows } = long()
    expect(rows()).toBe(1000)
    expect(ui.container.querySelector('[data-line="1000"]')).not.toBeNull()
    expect(ui.container.querySelector('[data-line="1001"]')).toBeNull()
    // 用例环境的语言不定（这里跑的是英文包），所以只认数字本身。
    expect(ui.container.querySelector('[data-testid="code-more"]')!.textContent).toContain('2,600')
  })

  it('点「再看一段」多摆 1000 行，摆到头那一句自己消失', async () => {
    const { ui, rows } = long()
    const more = () => ui.container.querySelector<HTMLElement>('[data-testid="code-more-button"]')
    await fireEvent.click(more()!)
    expect(rows()).toBe(2000)
    await fireEvent.click(more()!)
    expect(rows()).toBe(2600)
    expect(ui.container.querySelector('[data-testid="code-more"]')).toBeNull()
  })

  it('短于一段的文件不摆那一句', () => {
    const ui = render(PreviewCode, { props: { source: SOURCE, language: 'python' }, global: { stubs } })
    expect(ui.container.querySelector('[data-testid="code-more"]')).toBeNull()
  })

  it('点出来的那几行报的还是整份的行号', async () => {
    const { ui } = long()
    await fireEvent.click(ui.container.querySelector('[data-testid="code-more-button"]')!)
    await fireEvent.click(ui.container.querySelector('[data-line="1500"] [data-gutter]')!)
    const lines = ui.emitted<[{ start: number; end: number; text: string }]>().lines ?? []
    expect(lines.at(-1)![0]).toMatchObject({ start: 1500, end: 1500, text: '第 1500 行' })
  })
})

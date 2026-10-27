// 代码查看器怎么把一个选区读成行号：起止两头各落在哪一行；终点落在下一行最开头（三击、
// 拖过行尾）时那一行一个字也没选，不算进去。
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

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

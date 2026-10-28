/**
 * 地址栏与键盘：读者此刻指着哪一格，这一格里写的是什么。
 *
 * 表格里说不清「哪一格」就等于说不清要改哪一处。这一份盯的是那三件：点一格/拖一片
 * 之后地址栏说的是不是那一处，方向键走不走得动，回车能不能把这一格交出去；以及
 * 「定位」——输地址跳过去、输几个字找到内容。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewSheet from './PreviewSheet.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const vuetify = createVuetify({ components, directives })

const CSV = new TextEncoder().encode('姓名,学号,备注\n张伟,2026001,编程\n王芳,2026002,演示超时\n').buffer as ArrayBuffer

function mount() {
  return render(PreviewSheet, {
    props: { data: CSV, kind: 'csv' as const },
    global: { plugins: [vuetify] },
  })
}

function address(container: Element): string {
  return container.querySelector('[data-testid=sheet-address]')?.textContent?.trim() ?? ''
}
function value(container: Element): string {
  return container.querySelector('.ps__value')?.textContent?.trim() ?? ''
}
function cell(container: Element, row: number, col: number): Element {
  return container.querySelectorAll('tbody tr')[row]!.querySelectorAll('td')[col]!
}

it('还没指任何一处时地址栏是一横，不是空着', async () => {
  const { container } = mount()
  await waitFor(() => expect(container.querySelector('.ps__addr')).not.toBeNull())
  expect(address(container)).toBe('—')
})

it('点一格：地址栏说这一格，右边写这一格里是什么', async () => {
  const { container } = mount()
  await waitFor(() => expect(cell(container, 2, 1)).not.toBeNull())
  await fireEvent.click(cell(container, 2, 1))

  expect(address(container)).toBe('B3')
  expect(value(container)).toBe('2026002')
})

it('拖过一片：地址栏说的是两个角，不是松手的那一格', async () => {
  const { container } = mount()
  await waitFor(() => expect(cell(container, 2, 1)).not.toBeNull())
  await fireEvent.mouseDown(cell(container, 1, 0))
  await fireEvent.mouseEnter(cell(container, 2, 1))
  await fireEvent.mouseUp(cell(container, 2, 1))

  expect(address(container)).toBe('A2:B3')
})

it('方向键走格子，回车把这一格交出去', async () => {
  const { container, emitted } = mount()
  await waitFor(() => expect(cell(container, 1, 0)).not.toBeNull())
  const grid = container.querySelector('table[role=grid]') as HTMLElement
  await fireEvent.click(cell(container, 1, 0))
  expect(address(container)).toBe('A2')

  await fireEvent.keyDown(grid, { key: 'ArrowDown' })
  await fireEvent.keyDown(grid, { key: 'ArrowRight' })
  expect(address(container)).toBe('B3')

  await fireEvent.keyDown(grid, { key: 'Enter' })
  expect(emitted('cell')?.at(-1)).toEqual([{ address: 'B3', value: '2026002', sheet: '' }])
})

it('Shift+方向键扩出一片', async () => {
  const { container } = mount()
  await waitFor(() => expect(cell(container, 1, 0)).not.toBeNull())
  const grid = container.querySelector('table[role=grid]') as HTMLElement
  await fireEvent.click(cell(container, 1, 0))
  await fireEvent.keyDown(grid, { key: 'ArrowDown', shiftKey: true })
  await fireEvent.keyDown(grid, { key: 'ArrowRight', shiftKey: true })

  expect(address(container)).toBe('A2:B3')
})

it('定位：输地址跳到那一格', async () => {
  const { container } = mount()
  await waitFor(() => expect(container.querySelector('[data-testid=sheet-locate]')).not.toBeNull())
  const input = container.querySelector('[data-testid=sheet-locate]') as HTMLInputElement
  await fireEvent.update(input, 'C3')
  await fireEvent.keyDown(input, { key: 'Enter' })

  expect(address(container)).toBe('C3')
})

it('定位：输几个字找到写着它的那一格', async () => {
  const { container } = mount()
  await waitFor(() => expect(container.querySelector('[data-testid=sheet-locate]')).not.toBeNull())
  const input = container.querySelector('[data-testid=sheet-locate]') as HTMLInputElement
  await fireEvent.update(input, '演示超时')
  await fireEvent.keyDown(input, { key: 'Enter' })

  expect(address(container)).toBe('C3')
})

it('找不到时说没找到，不把地址栏挪到别处去', async () => {
  const { container } = mount()
  await waitFor(() => expect(container.querySelector('[data-testid=sheet-locate]')).not.toBeNull())
  const input = container.querySelector('[data-testid=sheet-locate]') as HTMLInputElement
  await fireEvent.update(input, '没有这几个字')
  await fireEvent.keyDown(input, { key: 'Enter' })

  expect(address(container)).toBe('—')
  expect(container.querySelector('.ps__summary')?.textContent).toContain('没找到')
})

it('回车在还没指过任何一处时交出去的是 A1，不是什么都不做', async () => {
  const { container, emitted } = mount()
  await waitFor(() => expect(container.querySelector('table[role=grid]')).not.toBeNull())
  await fireEvent.keyDown(container.querySelector('table[role=grid]') as HTMLElement, { key: 'Enter' })
  expect(emitted('cell')?.at(-1)).toEqual([{ address: 'A1', value: '姓名', sheet: '' }])
})

it('定位：地址栏写的一片（`A2:B3`）也认，抄回来落到同一片', async () => {
  const { container } = mount()
  await waitFor(() => expect(container.querySelector('[data-testid=sheet-locate]')).not.toBeNull())
  const input = container.querySelector('[data-testid=sheet-locate]') as HTMLInputElement
  await fireEvent.update(input, 'A2:B3')
  await fireEvent.keyDown(input, { key: 'Enter' })
  expect(address(container)).toBe('A2:B3')
})

it('一片的下角越界算没找到，不会给出表里没有的坐标', async () => {
  const { container, emitted } = mount()
  await waitFor(() => expect(container.querySelector('[data-testid=sheet-locate]')).not.toBeNull())
  const input = container.querySelector('[data-testid=sheet-locate]') as HTMLInputElement
  await fireEvent.update(input, 'A1:Z999')
  await fireEvent.keyDown(input, { key: 'Enter' })

  // 地址栏不写 `A1:Z999` 这种这张表没有的坐标，也不交出一格不存在的格子。
  expect(address(container)).toBe('—')
  expect(container.querySelector('.ps__summary')?.textContent).toContain('没找到')
  expect(emitted('cell')).toBeUndefined()
})

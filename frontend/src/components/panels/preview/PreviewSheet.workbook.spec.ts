/**
 * 图表不是这张视图要显示的东西，但它的存在不该让整份工作簿读不出来。
 *
 * 仓库自带的 analysis.xlsx 是 openpyxl 写的（芝士 自己产出表格用的就是它），里面有一张
 * 柱状图。openpyxl 把 drawing 部件写成默认命名空间（`<wsDr xmlns=...>`），Excel 写的是
 * `xdr:wsDr`，而 exceljs 4.4.0 只认后者 —— 绘图解析成空，整份工作簿在 reconcile 时就
 * 抛 `Cannot read properties of undefined (reading 'anchors')`。这几条盯的是读者能看见
 * 表里的格子，不是解析过程。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewSheet from './PreviewSheet.vue'

import { setLocale } from '@/i18n'
import { analysisWorkbookBytes } from '@/test/analysisWorkbook'

beforeEach(() => setLocale('zh-CN'))

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

function mountWorkbook(marks: { id: string; sheet: string; address: string }[] = []) {
  return render(PreviewSheet, {
    props: { data: analysisWorkbookBytes(), kind: 'workbook' as const, marks },
    global: { plugins: [vuetify] },
  })
}

/** 屏幕上那张表的内容，行号和列头不算在内。 */
function grid(container: Element): string[][] {
  return Array.from(container.querySelectorAll('tbody tr')).map((tr) =>
    Array.from(tr.querySelectorAll('td')).map((td) => td.textContent?.trim() ?? '')
  )
}

/** 按从左到右、从上到下的顺序排好的格子，点哪一格就取第几个。 */
function cellBoxes(container: Element): Element[] {
  return Array.from(container.querySelectorAll('tbody tr')).flatMap((tr) => Array.from(tr.querySelectorAll('td')))
}

it('带图表的工作簿也读得出格子，而不是整份显示不出来', async () => {
  const { container } = mountWorkbook()

  await waitFor(() => expect(grid(container).length).toBeGreaterThan(1))
  expect(grid(container)[0]).toEqual(['类别', '指标一', '指标二', '备注'])
  expect(grid(container)[1][0]).toBe('【类别1】')
})

it('数字按文件自己的格式排，靠右；文件加粗的表头照旧加粗', async () => {
  const { container } = mountWorkbook()

  await waitFor(() => expect(grid(container).length).toBeGreaterThan(1))
  // 这份模板里 B2、C2 的格式是 `#,##0.00`：存的是 0，屏幕上该是 `0.00`。
  expect(grid(container)[1][1]).toBe('0.00')
  const head = cellBoxes(container)[0]!
  expect(head.classList.contains('ps__cell--bold')).toBe(true)
  const number = cellBoxes(container)[5]!
  expect(number.classList.contains('ps__cell--right')).toBe(true)
})

it('攒着的那几条待发批注在格子上挂带序号的 pin', async () => {
  const { container } = render(PreviewSheet, {
    props: {
      data: analysisWorkbookBytes(),
      kind: 'workbook' as const,
      pins: [{ id: 'p1', n: 2, sheet: '数据', address: 'B2' }],
    },
    global: { plugins: [vuetify] },
  })

  await waitFor(() => expect(grid(container).length).toBeGreaterThan(1))
  const pin = container.querySelector('[data-pending-pin="p1"]') as HTMLElement
  expect(pin).not.toBeNull()
  expect(pin.textContent?.trim()).toBe('2')
})

it('工作簿里的每一张表都在，切得过去', async () => {
  const { container } = mountWorkbook()

  // 图表挂在第二张表上：它也在，说明这份工作簿确实是带图的那一份。
  for (const name of ['数据', '汇总', '说明']) {
    expect(await screen.findByRole('tab', { name })).toBeTruthy()
  }
  await fireEvent.click(await screen.findByRole('tab', { name: '说明' }))
  await waitFor(() => expect(grid(container)[0]).toEqual(['数据来源', '【文件名 / 表名 / 链接】']))
})

it('标记按工作表名归属：只有它自己那张表上才画', async () => {
  // `B2` 在每一张表里都是一格，所以要靠工作表名分清说的是哪一张。第一张表是「数据」，
  // 这里把标记记在「汇总」上——切过去才看得见。
  const { container } = mountWorkbook([{ id: 'c1', sheet: '汇总', address: 'A1' }])

  await waitFor(() => expect(grid(container).length).toBeGreaterThan(1))
  expect(container.querySelectorAll('.ps__cmt')).toHaveLength(0)

  await fireEvent.click(await screen.findByRole('tab', { name: '汇总' }))
  await waitFor(() => expect(container.querySelectorAll('.ps__cmt')).toHaveLength(1))
})

it('切到另一张表：上一张表上圈的那一片不带过去', async () => {
  // 地址只在它自己那张表里有意义：`B2` 在这一张和那一张里是两格。留着高亮，屏幕上
  // 那句「引用了 A1:B2」就落在了它没说的那张表上。
  const { container } = mountWorkbook()

  await waitFor(() => expect(grid(container).length).toBeGreaterThan(1))
  const cells = cellBoxes(container)
  await fireEvent.mouseDown(cells[0]!)
  await fireEvent.mouseEnter(cells[1]!)
  await fireEvent.mouseUp(cells[1]!)
  await waitFor(() => expect(container.querySelectorAll('.ps__cell--in')).toHaveLength(2))

  await fireEvent.click(await screen.findByRole('tab', { name: '汇总' }))

  await waitFor(() => expect(container.querySelectorAll('.ps__cell--in')).toHaveLength(0))
})

it('切到另一张表：上一张表上点的那一格也不带过去', async () => {
  const { container } = mountWorkbook()

  await waitFor(() => expect(grid(container).length).toBeGreaterThan(1))
  await fireEvent.click(cellBoxes(container)[0]!)
  await waitFor(() => expect(container.querySelectorAll('.ps__cell--on')).toHaveLength(1))

  await fireEvent.click(await screen.findByRole('tab', { name: '汇总' }))

  await waitFor(() => expect(container.querySelectorAll('.ps__cell--on')).toHaveLength(0))
})

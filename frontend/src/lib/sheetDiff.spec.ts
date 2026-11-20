/** 表格两版逐格比：显示出来的字不同才算改过；整张新加、删掉的表单列，不按格数。 */
import type { Sheet } from './sheetRead'

import { expect, it } from 'vitest'

import { diffSheets } from './sheetDiff'

const sheet = (name: string, rows: string[][]): Sheet => ({
  name,
  rows,
  width: Math.max(0, ...rows.map((r) => r.length)),
  totalRows: rows.length,
  totalCols: Math.max(0, ...rows.map((r) => r.length)),
  shortened: false,
})

it('同名表逐格比，地址和原值都对；加长的行列也算（原来是空的）', () => {
  const before = [
    sheet('汇总', [
      ['月份', '订单'],
      ['10月', '150'],
    ]),
  ]
  const after = [sheet('汇总', [['月份', '订单'], ['10月', '160', '备注'], ['11月']])]
  expect(diffSheets(before, after).cells).toEqual([
    { sheet: '汇总', address: 'B2', before: '150' },
    { sheet: '汇总', address: 'C2', before: '' },
    { sheet: '汇总', address: 'A3', before: '' },
  ])
})

it('新加的表、删掉的表只报表名，不把它的格子数进去', () => {
  const diff = diffSheets(
    [sheet('旧表', [['a']]), sheet('汇总', [['x']])],
    [sheet('汇总', [['x']]), sheet('新表', [['b', 'c']])]
  )
  expect(diff).toEqual({ cells: [], addedSheets: ['新表'], removedSheets: ['旧表'] })
})

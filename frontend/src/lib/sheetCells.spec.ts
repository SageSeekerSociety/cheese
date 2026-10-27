// 表格卡片上「改了 N 格」数的是两版之间显示出来不一样的格子：改了值、新填了、清空了
// 都算，别的表上的改动也算；一个字没动的表是 0。
import ExcelJS from 'exceljs'
import { expect, it } from 'vitest'

import { changedCells, peekSheet } from './sheetCells'

async function book(fill: (sheet: ExcelJS.Worksheet, other: ExcelJS.Worksheet) => void): Promise<ArrayBuffer> {
  const wb = new ExcelJS.Workbook()
  const sheet = wb.addWorksheet('汇总')
  const other = wb.addWorksheet('明细')
  sheet.addRow(['月份', '订单数'])
  sheet.addRow(['9 月', 1204])
  sheet.addRow(['10 月', 968])
  other.addRow(['订单号', '金额'])
  fill(sheet, other)
  const out = await wb.xlsx.writeBuffer()
  return (out as Uint8Array).buffer.slice(0) as ArrayBuffer
}

it('counts every cell whose shown value differs, on any sheet', async () => {
  const before = await book(() => {})
  const after = await book((sheet, other) => {
    sheet.getCell('B2').value = 990 // 改了值
    sheet.getCell('A3').value = null // 清空
    sheet.getCell('C1').value = '备注' // 新填
    other.getCell('B1').value = '金额（元）' // 别的表
  })
  const changed = await changedCells(before, after)
  expect([...changed].sort()).toEqual(['明细!B1', '汇总!A3', '汇总!B2', '汇总!C1'])
})

it('an untouched workbook has no changed cells', async () => {
  const bytes = await book(() => {})
  expect((await changedCells(bytes, await book(() => {}))).size).toBe(0)
})

it('peeks the first sheet’s top-left block with its addresses', async () => {
  const peek = await peekSheet(await book(() => {}), { rows: 2, cols: 2 })
  expect(peek.rows).toEqual([
    ['月份', '订单数'],
    ['9 月', '1204'],
  ])
  expect(peek.addresses[1][1]).toBe('B2')
  expect(peek.rowCount).toBe(3)
  expect(peek.sheetNames).toEqual(['汇总', '明细'])
})

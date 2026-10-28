// 表格里的格子：读成屏幕上显示的那几个字，以及「两版之间哪些格子变了」。
//
// 对话里的表格卡片和预览面板的表格读同一份 .xlsx，格子显示成什么样只能有一种说法，
// 否则卡片上说改了的那一格，点开看又是另一副样子。
import type { Workbook } from 'exceljs'

/** 单元格里可能是数字、日期、公式结果或富文本，屏幕上要的都是它显示出来的样子。 */
export function cellDisplay(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (value instanceof Date) return value.toISOString().slice(0, 10)
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>
    if (typeof v.text === 'string') return v.text
    if (Array.isArray(v.richText)) {
      return (v.richText as { text?: string }[]).map((r) => r.text ?? '').join('')
    }
    if ('result' in v) return cellDisplay(v.result)
    return ''
  }
  return String(value)
}

async function load(bytes: ArrayBuffer): Promise<Workbook> {
  const ExcelJS = await import('exceljs')
  const book = new ExcelJS.Workbook()
  await book.xlsx.load(bytes.slice(0))
  return book
}

/** 一本表里每一格显示的字，键是「表名!A1」。空格子不记。 */
function cellMap(book: Workbook): Map<string, string> {
  const out = new Map<string, string>()
  book.eachSheet((sheet) => {
    sheet.eachRow((row) => {
      row.eachCell((cell) => {
        const text = cellDisplay(cell.value)
        if (text !== '') out.set(`${sheet.name}!${cell.address}`, text)
      })
    })
  })
  return out
}

export interface SheetPeek {
  /** 第一张表左上角那一块，按行。 */
  rows: string[][]
  /** 这块里每一格的地址（A1 写法），和 rows 一一对应。 */
  addresses: string[][]
  /** 第一张表有内容的行数。 */
  rowCount: number
  /** 第一张表叫什么。 */
  firstSheet: string
  sheetNames: string[]
}

/** 第一张表左上角 rows×cols 那一块，和整本表的几个数。 */
export async function peekSheet(bytes: ArrayBuffer, size = { rows: 5, cols: 5 }): Promise<SheetPeek> {
  const book = await load(bytes)
  const sheet = book.worksheets[0]
  const rows: string[][] = []
  const addresses: string[][] = []
  if (sheet) {
    const last = Math.min(sheet.rowCount, size.rows)
    const width = Math.min(sheet.columnCount, size.cols)
    for (let r = 1; r <= last; r += 1) {
      const row = sheet.getRow(r)
      rows.push(Array.from({ length: width }, (_, c) => cellDisplay(row.getCell(c + 1).value)))
      addresses.push(Array.from({ length: width }, (_, c) => row.getCell(c + 1).address))
    }
  }
  return {
    rows,
    addresses,
    rowCount: sheet?.actualRowCount ?? 0,
    firstSheet: sheet?.name ?? '',
    sheetNames: book.worksheets.map((s) => s.name),
  }
}

/** 两版之间显示出来不一样的格子（「表名!A1」）。多出来、清空了的格子也算。 */
export async function changedCells(before: ArrayBuffer, after: ArrayBuffer): Promise<Set<string>> {
  const [a, b] = await Promise.all([load(before), load(after)])
  const old = cellMap(a)
  const now = cellMap(b)
  const changed = new Set<string>()
  for (const [key, text] of now) if (old.get(key) !== text) changed.add(key)
  for (const key of old.keys()) if (!now.has(key)) changed.add(key)
  return changed
}

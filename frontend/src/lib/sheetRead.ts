// 表格文件读成一张张表：工作簿（.xlsx）、CSV、TSV 各有各的读法，读出来是同一种形状。
//
// 从阅读视图（`components/panels/preview/PreviewSheet.vue`）里拆出来，是因为版本对比也要
// 读同一份表：「改了哪几格」只能拿同一套读法读出来的两版来比，两处各读各的，同一格在两边
// 显示成不同的字，就会被数成改过。

import { cellDisplay } from './sheetCells'

import { t } from '@/i18n'

export type Sheet = {
  name: string
  /** 画出来的这些行（最多 `MAX_ROWS` 行、每行最多 `MAX_COLS` 格）。 */
  rows: string[][]
  /** 画出来的列数。 */
  width: number
  /** 整张表本来有多少行 / 多少列 —— 只画了一部分时要靠它说清。 */
  totalRows: number
  totalCols: number
  /** 这一张表里有格子被截短了。按表记，不按文件记：多表工作簿里切到没有长格的那张
   *  表，不该还挂着「较长的单元格被截短了」。 */
  shortened: boolean
}

/** 读一份表格的结果。`failure` 非空时 `sheets` 是空的，那句话就是给读者看的原因。 */
export interface SheetRead {
  sheets: Sheet[]
  /** 分隔文本的原文（工作簿没有原文，是空串）。 */
  sourceText: string
  /** 字节在解析之前就被切了，表尾可能缺。 */
  endClipped: boolean
  failure: string
}

/** 画出来的上限。一张几十万行的 CSV 整份铺进 DOM 会把面板拖死，而人要找的那几格
 *  总在开头。超出的部分不是丢了，是没画出来 —— 所以下面那条提示必须说清「只显示了
 *  前 N 行 M 列」，并且文件条上照旧有「下载原文件」这条路。 */
export const MAX_ROWS = 500
export const MAX_COLS = 64
export const MAX_CELL = 300
/** 解析之前先切字节的上限：一份 200 MB 的 CSV 光解码成字符串就先占掉几百 MB。 */
const MAX_BYTES = 1 << 20

/** 0→A、25→Z、26→AA：电子表格的列名，和 openpyxl 用的是同一套。 */
export function columnName(index: number): string {
  let name = ''
  let n = index
  while (n >= 0) {
    name = String.fromCharCode((n % 26) + 65) + name
    n = Math.floor(n / 26) - 1
  }
  return name
}

/** CSV 的编码取决于谁写的。芝士 写 UTF-8；而人从 Excel 导出的 CSV 在中文 Windows
 *  上是 GBK，按 UTF-8 解出来是一整片乱码，而且不抛错 —— 所以先严格按 UTF-8 解，
 *  它失败了才说明这不是 UTF-8，退到 GBK。 */
function decodeText(bytes: Uint8Array): string {
  let text: string
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    try {
      text = new TextDecoder('gbk').decode(bytes)
    } catch {
      text = new TextDecoder('utf-8').decode(bytes)
    }
  }
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
}

/** 分隔符不总是逗号：Excel 按系统的列表分隔符导出，某些区域设置下是分号；从数据库
 *  导出的常是制表符。数第一行里哪个多就用哪个。引号里的分隔符也被数进去了，但一份
 *  真表里真正的分隔符仍然占多数，而猜错的表现是整张表挤成一列 —— 一眼就看得出。 */
function sniffSeparator(text: string): string {
  const end = text.search(/\r?\n/)
  const line = end === -1 ? text : text.slice(0, end)
  let best = ','
  let most = 0
  for (const sep of [',', ';', '\t']) {
    const n = line.split(sep).length - 1
    if (n > most) {
      best = sep
      most = n
    }
  }
  return best
}

/** 按 RFC 4180 读：引号里的分隔符、换行和 `""` 转义的引号都不是边界。自己写而不是
 *  按行 split，因为一个带地址或备注的单元格里就有逗号和换行，split 会把一行拆散，
 *  而拆散之后地址全错 —— 而地址是这个视图存在的理由。 */
function parseCsv(text: string, sep: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (quoted) {
      if (ch !== '"') field += ch
      else if (text[i + 1] === '"') {
        field += '"'
        i += 1
      } else quoted = false
      continue
    }
    if (ch === '"') quoted = true
    else if (ch === sep) {
      row.push(field)
      field = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i += 1
      row.push(field)
      field = ''
      rows.push(row)
      row = []
    } else field += ch
  }
  if (field !== '' || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

/** 老版 `.xls`（Excel 97-2003）是 OLE2 复合文档，头八个字节固定是这几个；OOXML 是
 *  zip（`PK`）。exceljs 只读 OOXML，拿 OLE2 进去只会抛一句「找不到中央目录」——
 *  按魔数先认出来，才能把话说成人话。 */
const OLE2_MAGIC = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]

function isOle2(bytes: Uint8Array): boolean {
  return bytes.length >= OLE2_MAGIC.length && OLE2_MAGIC.every((b, i) => bytes[i] === b)
}

/** 切到 `limit` 处可能正好把一个多字节字符切成两半。残字节会让严格 UTF-8 解码抛错，
 *  于是一份合法的 UTF-8 文件被判成 GBK、整份乱码 —— 先把切点退到字符边界上（UTF-8
 *  的续字节长这样 `10xxxxxx`），切出来的前缀才是这份文件的一个真前缀。 */
function sliceAtCharBoundary(bytes: Uint8Array, limit: number): Uint8Array {
  if (bytes.length <= limit) return bytes
  let end = limit
  while (end > 0 && (bytes[end] & 0xc0) === 0x80) end -= 1
  return bytes.subarray(0, end)
}

/** CSV / TSV 只有一张表，而且它没有名字 —— 没有名字就不该编一个：地址栏里写 `B7`，
 *  和一个真有工作表名的 `Sheet1!B7` 是两种不同的坐标，编出来的名字会让读者以为
 *  这份文件里还有别的表。 */
function readText(data: ArrayBuffer, kind: 'csv' | 'tsv'): SheetRead {
  const bytes = new Uint8Array(data)
  // 先切字节再解码：整份读进来再切开，切之前那几百 MB 已经占住了。
  const endClipped = bytes.length > MAX_BYTES
  const text = decodeText(sliceAtCharBoundary(bytes, MAX_BYTES))
  // TSV 的分隔符是格式的一部分，不用猜；CSV 才要数第一行里哪个符号多。
  const rows = parseCsv(text, kind === 'tsv' ? '\t' : sniffSeparator(text))
  if (!rows.length) return { sheets: [], sourceText: text, endClipped, failure: t('work.room.preview.fileEmpty') }
  // 不用 Math.max(...)：一份只由换行组成的 1 MB 文件有几十万行，摊开成实参会把栈压爆。
  let totalCols = 0
  for (const r of rows) if (r.length > totalCols) totalCols = r.length
  const kept = rows.slice(0, MAX_ROWS).map((r) => r.slice(0, MAX_COLS))
  const sheets: Sheet[] = [
    {
      name: '',
      rows: kept,
      width: Math.min(totalCols, MAX_COLS),
      totalRows: rows.length,
      totalCols,
      shortened: kept.some((r) => r.some((c) => c.length > MAX_CELL)),
    },
  ]
  return { sheets, sourceText: text, endClipped, failure: '' }
}

async function readWorkbook(data: ArrayBuffer): Promise<SheetRead> {
  const ExcelJS = await import('exceljs')
  const book = new ExcelJS.Workbook()
  await book.xlsx.load(data.slice(0))
  const read: Sheet[] = []
  book.eachSheet((worksheet) => {
    const rows: string[][] = []
    let width = 0
    // 一张表里有没有超长的格子，是这张表自己的事：一本表里既有小台账又有长备注，
    // 用同一个标记说「被截短了」，切到没有长格子的那页也会顶着这句提示。
    let shortened = false
    worksheet.eachRow({ includeEmpty: true }, (row, rowNumber) => {
      if (rowNumber > MAX_ROWS) return
      const cells: string[] = []
      row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
        if (colNumber > MAX_COLS) return
        const text = cellDisplay(cell.value)
        if (text.length > MAX_CELL) shortened = true
        cells[colNumber - 1] = text
      })
      width = Math.max(width, cells.length)
      rows[rowNumber - 1] = cells
    })
    for (let i = 0; i < rows.length; i += 1) if (!rows[i]) rows[i] = []
    read.push({
      name: worksheet.name,
      rows,
      width,
      totalRows: worksheet.rowCount,
      totalCols: worksheet.columnCount,
      shortened,
    })
  })
  if (!read.length) return { sheets: [], sourceText: '', endClipped: false, failure: t('work.room.preview.noSheets') }
  return { sheets: read, sourceText: '', endClipped: false, failure: '' }
}

/** 把一份表格的字节读成表。`kind` 只有调用方知道（`lib/fileKind.sheetKindOf`）：字节本身
 *  看不出是 CSV 还是工作簿。读不了时不抛，`failure` 里是给读者看的那句话。 */
export async function readSheets(data: ArrayBuffer, kind: 'workbook' | 'csv' | 'tsv' | 'ods'): Promise<SheetRead> {
  const failed = (failure: string): SheetRead => ({ sheets: [], sourceText: '', endClipped: false, failure })
  // OpenDocument 表格和 OOXML 不是一个格式，exceljs 读不了它。与其让它抛一句
  // 读不懂的错，不如说清这条路走不通、以及可以怎么拿到内容。
  if (kind === 'ods') return failed(t('work.room.preview.sheetFormatUnsupported'))
  if (kind === 'csv' || kind === 'tsv') return readText(data, kind)
  if (isOle2(new Uint8Array(data))) return failed(t('work.room.preview.sheetLegacyUnsupported'))
  try {
    return await readWorkbook(data)
  } catch (e) {
    return failed(e instanceof Error ? e.message : t('work.room.preview.sheetReadFailed'))
  }
}

// 表格文件读成一张张表：工作簿（.xlsx）、CSV、TSV 各有各的读法，读出来是同一种形状。
//
// 从阅读视图（`components/panels/preview/PreviewSheet.vue`）里拆出来，是因为版本对比也要
// 读同一份表：「改了哪几格」只能拿同一套读法读出来的两版来比，两处各读各的，同一格在两边
// 显示成不同的字，就会被数成改过。

import type { RawChart } from './sheetChart'

import { cellDisplay, cellFormula } from './sheetCells'
import { readChartParts } from './sheetChart'
import { alignmentOf } from './sheetNumberFormat'

import { t } from '@/i18n'

/** 一格怎么摆：文件自己说了就照它，没说时数靠右、别的靠左（`alignmentOf`）。 */
export type CellAlign = 'left' | 'center' | 'right'

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
  /** 每一格怎么摆，和 `rows` 一一对应；这一格没读到对齐就是 `undefined`。分隔文本按
   *  内容判（像数就靠右），工作簿按文件里那一格自己写的来。 */
  aligns?: (CellAlign | undefined)[][]
  /** 文件自己加粗的那几格（`A1` 形式）：表头、合计行常常是靠这一层认出来的，
   *  不靠「第几行想必是表头」去猜。 */
  bold?: Set<string>
  /** 写了公式的格子（`A1` → `=SUM(数据!B2:B6)`）：地址栏照着写出来。 */
  formulas?: Map<string, string>
  /** 工作簿里带的图表引用的那几格里，原始的数（`A1` → 数）。图上画的是数，不是
   *  「4.1%」这几个字——带上格式的字符串再解析回来会变成 4.1。只留图引用到的那几格。 */
  numbers?: Map<string, number>
}

/** 读一份表格的结果。`failure` 非空时 `sheets` 是空的，那句话就是给读者看的原因。 */
export interface SheetRead {
  sheets: Sheet[]
  /** 分隔文本的原文（工作簿没有原文，是空串）。 */
  sourceText: string
  /** 字节在解析之前就被切了，表尾可能缺。 */
  endClipped: boolean
  failure: string
  /** 工作簿里带的图表（还没取数，取数靠 `resolveCharts`）。分隔文本没有图。 */
  charts?: RawChart[]
  /** 读到了、我们画不出来的图有几张，按工作表名记。 */
  chartsSkipped?: Map<string, number>
}

/** 读进来的上限。一张几十万行的 CSV 整份读进内存会把面板拖死，而人要找的那几格
 *  总在开头；几千行（这个数）是读者真会翻到底的那一档，所以读到那里。超出的部分不是
 *  丢了，是没读出来 —— 所以下面那条提示必须说清「只显示了前 N 行 M 列」，并且文件条
 *  上照旧有「下载原文件」这条路。
 *
 *  画出来的行数比这个少：几千行铺进 DOM 一样会卡，视图只画看得见的那一屏
 *  （`PreviewSheet` 的窗口）。读进来多少和画出来多少是两件事。 */
export const MAX_ROWS = 5000
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

/** 这一格的文字看着是不是一个数：一串数字，可以带正负号、千分位、小数点和百分号。
 *  分隔文本没有样式，只能这么判；判错了最多是这一列没靠右，不会把字改掉。 */
function looksNumeric(text: string): boolean {
  const trimmed = text.trim()
  if (!trimmed || trimmed.length > 32) return false
  return /^[-+]?[\d,]*\.?\d+%?$/.test(trimmed) || /^[-+]?[\d,]+$/.test(trimmed)
}

/** 这一格在文件里存的是不是数（公式的结果也算）：`alignmentOf` 靠它决定默认靠哪边。 */
function isNumericValue(value: unknown): boolean {
  return numberValue(value) !== undefined
}

/** 这一格里的那个数：直接存的数，或者公式算出来的数。别的（日期、文本、富文本）没有。 */
function numberValue(value: unknown): number | undefined {
  if (typeof value === 'number') return value
  if (!value || typeof value !== 'object') return undefined
  const result = (value as Record<string, unknown>).result
  return typeof result === 'number' ? result : undefined
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
      aligns: kept.map((row) => row.map((text) => (looksNumeric(text) ? 'right' : undefined))),
    },
  ]
  return { sheets, sourceText: text, endClipped, failure: '' }
}

async function readWorkbook(data: ArrayBuffer): Promise<SheetRead> {
  const ExcelJS = await import('exceljs')
  const book = new ExcelJS.Workbook()
  await book.xlsx.load(data.slice(0))
  // 图只引用到几格，先问出来是哪些 —— 底下读格子的那一段顺手留下它们的原始数。
  const chartParts = await readChartParts(data)
  const wanted = (name: string) => chartParts.needed.get(name)
  const read: Sheet[] = []
  book.eachSheet((worksheet) => {
    const rows: string[][] = []
    const aligns: (CellAlign | undefined)[][] = []
    const bold = new Set<string>()
    const formulas = new Map<string, string>()
    const numbers = new Map<string, number>()
    let width = 0
    // 一张表里有没有超长的格子，是这张表自己的事：一本表里既有小台账又有长备注，
    // 用同一个标记说「被截短了」，切到没有长格子的那页也会顶着这句提示。
    let shortened = false
    worksheet.eachRow({ includeEmpty: true }, (row, rowNumber) => {
      if (rowNumber > MAX_ROWS) return
      const cells: string[] = []
      const line: (CellAlign | undefined)[] = []
      const charted = wanted(worksheet.name)
      row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
        if (colNumber > MAX_COLS) return
        const text = cellDisplay(cell.value, cell.numFmt)
        if (text.length > MAX_CELL) shortened = true
        cells[colNumber - 1] = text
        line[colNumber - 1] = alignmentOf(cell.alignment?.horizontal, isNumericValue(cell.value))
        const address = `${columnName(colNumber - 1)}${rowNumber}`
        if (cell.font?.bold) bold.add(address)
        const formula = cellFormula(cell.value)
        if (formula) formulas.set(address, formula)
        const raw = numberValue(cell.value)
        if (charted?.has(address) && raw !== undefined) numbers.set(address, raw)
      })
      width = Math.max(width, cells.length)
      rows[rowNumber - 1] = cells
      aligns[rowNumber - 1] = line
    })
    for (let i = 0; i < rows.length; i += 1) {
      if (!rows[i]) rows[i] = []
      if (!aligns[i]) aligns[i] = []
    }
    read.push({
      name: worksheet.name,
      rows,
      width,
      totalRows: worksheet.rowCount,
      totalCols: worksheet.columnCount,
      shortened,
      aligns,
      bold,
      formulas,
      numbers: numbers.size ? numbers : undefined,
    })
  })
  if (!read.length) return { sheets: [], sourceText: '', endClipped: false, failure: t('work.room.preview.noSheets') }
  return {
    sheets: read,
    sourceText: '',
    endClipped: false,
    failure: '',
    charts: chartParts.raw,
    chartsSkipped: chartParts.skipped,
  }
}

/** 刚读过的表：同一份字节再打开时不重读一遍。
 *
 *  这是「热启动」那一半：一份几千行的工作簿 exceljs 要读几百毫秒到几秒，而读者做的
 *  常常是关掉、又点开同一份文件（换一版看、退出去比一下）。字节相同，读出来的就是同一
 *  张表，没有理由再排一遍。键是内容的摘要，不是路径——路径相同而字节换了（文件出了新
 *  版），读出来的必须是新的那一份。
 *
 *  上限按份数记：一份几千行的表读出来有几十万个字符串，留着太多份会把面板的内存吃满。
 *  存的是整份 `SheetRead`，谁都不会去改它（视图只读）。 */
const READ_CACHE_LIMIT = 4
const readCache = new Map<string, Promise<SheetRead>>()

/** 内容的摘要。优先用 `crypto.subtle`（同一个文件每次算出来都一样）；拿不到它时退回
 *  一个不看内容的键——那等于不缓存，比按错的键返回别的文件强。 */
async function contentKey(data: ArrayBuffer, kind: string): Promise<string | null> {
  try {
    const digest = await crypto.subtle.digest('SHA-256', data)
    const bytes = new Uint8Array(digest)
    let hex = ''
    for (const byte of bytes) hex += byte.toString(16).padStart(2, '0')
    return `${kind}:${hex}`
  } catch {
    return null
  }
}

/** 把一份表格的字节读成表。`kind` 只有调用方知道（`lib/fileKind.sheetKindOf`）：字节本身
 *  看不出是 CSV 还是工作簿。读不了时不抛，`failure` 里是给读者看的那句话。 */
export async function readSheets(data: ArrayBuffer, kind: 'workbook' | 'csv' | 'tsv' | 'ods'): Promise<SheetRead> {
  const failed = (failure: string): SheetRead => ({ sheets: [], sourceText: '', endClipped: false, failure })
  // OpenDocument 表格和 OOXML 不是一个格式，exceljs 读不了它。与其让它抛一句
  // 读不懂的错，不如说清这条路走不通、以及可以怎么拿到内容。
  if (kind === 'ods') return failed(t('work.room.preview.sheetFormatUnsupported'))
  const key = await contentKey(data, kind)
  const hit = key === null ? undefined : readCache.get(key)
  if (hit) {
    // 命中就把它挪到队尾：这一份刚用过，最不该被挤掉。
    readCache.delete(key!)
    readCache.set(key!, hit)
    return hit
  }
  const read = (async (): Promise<SheetRead> => {
    try {
      let result: SheetRead
      if (kind === 'csv' || kind === 'tsv') result = readText(data, kind)
      else if (isOle2(new Uint8Array(data))) result = failed(t('work.room.preview.sheetLegacyUnsupported'))
      else result = await readWorkbook(data)
      // 读失败不进缓存：换一份字节重试是读者会做的事，而这条路的结果不该被记成「这份文件
      // 就是读不出来」。`.ods`、老版 `.xls` 那两句也是失败，走同一条。
      if (result.failure && key !== null) readCache.delete(key)
      return result
    } catch (e) {
      // 抛出来的任何一条也当失败：分隔文本那一路是同步的，`isOle2` 也会抛，抛出去就是
      // 一条被缓存住、又没人接的拒绝承诺，下一次打开同一份字节只会再撞一次。
      if (key !== null) readCache.delete(key)
      return failed(e instanceof Error ? e.message : t('work.room.preview.sheetReadFailed'))
    }
  })()
  if (key !== null) {
    readCache.set(key, read)
    while (readCache.size > READ_CACHE_LIMIT) {
      const oldest = readCache.keys().next().value
      if (oldest === undefined) break
      readCache.delete(oldest)
    }
  }
  return read
}

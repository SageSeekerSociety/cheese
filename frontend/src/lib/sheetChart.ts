// 工作簿里带的图表：从 zip 里把图的定义读出来，再照它引用的那几片格子取数。
//
// 表格转成 PDF 会把图变成一张图，按字节画表格又会把图整个丢掉 —— 一份「有图的表」
// 在预览里看不见图，读者会以为文件里没有。所以这里两头都读：`xl/charts/chart1.xml`
// 说这张图是柱状还是折线、引用了哪一片、每一条叫什么；数从已经读出来的那张表里取。
//
// 只认最常见的几种（柱、折线、面积、饼、散点）和它们按格引用那一半。图里写死的数、
// 组合图、透视图、走数据模型的图一律跳过：画不出来的图不画，也不拿一张猜出来的图
// 冒名顶替。
import type { ChartKind } from '@/lib/docSchema/chart'

import { columnName } from './sheetRead'
import { listZipEntries, readZipText } from './zipEntry'

import { t } from '@/i18n'

/** 从 zip 里读出来的一张图，还没取数：引用还是 `'数据'!$A$2:$A$6` 这种字符串。 */
export interface RawChart {
  /** 这张图画在哪张表上（工作表名）。 */
  sheet: string
  title: string
  kind: ChartKind
  horizontal: boolean
  series: { name: string; categories: string; values: string }[]
}

/** 取完数的一张图，可以直接交给 `chartData` 摆。 */
export interface SheetChart {
  sheet: string
  title: string
  kind: ChartKind
  horizontal: boolean
  categories: string[]
  series: { name: string; texts: string[] }[]
}

/** 解析出来的一批图，和它们向表格要的那几片格子。`needed` 是「工作表 → 地址」：
 *  读表那一步只把这些格子的原始数字留下来（一张几十万格的表全留数字太重，而图只
 *  看它引用到的那几格）。 */
export interface ChartParts {
  raw: RawChart[]
  needed: Map<string, Set<string>>
}

const EMPTY: ChartParts = { raw: [], needed: new Map() }

/** 一张图最多取多少格。读表那一步按它记原始数、取数那一步按它展开，两处必须是同一个数：
 *  只改一处，另一处就会把「记了数的格子」读成空档。 */
const MAX_REF_CELLS = 2000

/** 一份工作簿里的一批图，按 XML 里的定义原样。读不出图的（不是 OOXML、没有图）给空。 */
export async function readChartParts(bytes: ArrayBuffer): Promise<ChartParts> {
  try {
    return await parseCharts(bytes)
  } catch {
    // 图表是加分项：读不出来不该让整份表读不出来。
    return EMPTY
  }
}

async function parseCharts(bytes: ArrayBuffer): Promise<ChartParts> {
  const names = listZipEntries(bytes)
  if (!names.length) return EMPTY
  const workbook = await readZipText(bytes, 'xl/workbook.xml')
  const workbookRels = await readZipText(bytes, 'xl/_rels/workbook.xml.rels')
  if (!workbook || !workbookRels) return EMPTY
  const sheetTargets = relationships(workbookRels)
  const needed = new Map<string, Set<string>>()
  const raw: RawChart[] = []

  // 工作表名 → 它的 xml（`xl/worksheets/sheet1.xml`）。
  const doc = parseXml(workbook)
  if (!doc) return EMPTY
  for (const sheet of all(doc, 'sheet')) {
    const name = sheet.getAttribute('name') ?? ''
    const rid = sheet.getAttribute('r:id') ?? attrEndingWith(sheet, 'id')
    const target = rid ? sheetTargets.get(rid) : undefined
    if (!name || !target) continue
    const sheetPath = resolvePath('xl/workbook.xml', target)
    const sheetRels = await readZipText(bytes, relsPath(sheetPath))
    if (!sheetRels) continue
    const sheetRelMap = relationships(sheetRels)
    const drawing = firstByType(sheetRelMap, 'drawing')
    if (!drawing) continue
    const drawingPath = resolvePath(sheetPath, drawing)
    const drawingRels = await readZipText(bytes, relsPath(drawingPath))
    if (!drawingRels) continue
    const drawingRelMap = relationships(drawingRels)
    for (const [id, target] of drawingRelMap) {
      if (!/\/chart$/.test(drawingRelMap.typeOf(id) ?? '')) continue
      const chartPath = resolvePath(drawingPath, target)
      const xml = await readZipText(bytes, chartPath)
      if (!xml) continue
      const chart = parseChart(xml, name)
      if (!chart) continue
      raw.push(chart)
      // 图上的数来自它引用的那张表，不一定是挂着它的那张（这张图挂在「汇总」上、数在
      // 「数据」里）。所以要留原始数的也是被引用的那张表。
      for (const series of chart.series) {
        for (const ref of [series.categories, series.values]) {
          const owner = sheetOf(ref) || chart.sheet
          let set = needed.get(owner)
          if (!set) needed.set(owner, (set = new Set()))
          for (const address of expandRef(ref, MAX_REF_CELLS)) set.add(address)
        }
      }
    }
  }
  return { raw, needed }
}

/** OOXML 的图形元素 → 我们画得出来的那几种。表里没有的（组合图里的自定义图形、
 *  曲面图、雷达图等）一律不画。 */
const PLOT_KINDS: Record<string, ChartKind | undefined> = {
  barChart: 'bar',
  lineChart: 'line',
  areaChart: 'area',
  pieChart: 'pie',
  doughnutChart: 'pie',
  scatterChart: 'scatter',
}

/** 一张图里我们要的那几件。认不出类型、或者一条数据都没有时给 null。 */
function parseChart(xml: string, sheet: string): RawChart | null {
  const doc = parseXml(xml)
  if (!doc) return null
  // 一张图里可能有不止一种图形（柱 + 折线那种组合图）。我们只画一种，所以组合图整个
  // 跳过——画一半等于给读者一张错的图。
  const plots = all(doc, 'plotArea').flatMap((area) =>
    Array.from(area.children).filter((child) => PLOT_KINDS[child.localName] && all(child, 'ser').length)
  )
  if (plots.length !== 1) return null
  const plot = plots[0]!
  const kind = PLOT_KINDS[plot.localName]!
  const barDir = first(plot, 'barDir')?.getAttribute('val')
  const horizontal = kind === 'bar' && barDir === 'bar'
  const title = chartTitle(doc)
  const series = all(plot, 'ser').map((ser) => ({
    name: refOf(ser, 'tx'),
    categories: refOf(ser, 'cat'),
    values: refOf(ser, 'val'),
  }))
  const usable = series.filter((s) => s.values)
  if (!usable.length) return null
  return { sheet, title, kind, horizontal, series: usable }
}

function chartTitle(doc: Document): string {
  // 只认 `<c:chart>` 直接挂着的那个标题。坐标轴也有自己的 `<c:title>`（在 `plotArea`
  // 里面），拿全文档第一个会在一张没有标题的图上显示成「销量」这种轴名。
  const chart = first(doc, 'chart')
  const titleNode = chart ? Array.from(chart.children).find((child) => child.localName === 'title') ?? null : null
  if (!titleNode) return ''
  // 图表标题有两种写法：直接的 `<c:title><c:tx><c:rich>…<a:t>文字</a:t>`，或者引用一格。
  const parts: string[] = []
  for (const t of all(titleNode, 't')) parts.push(t.textContent ?? '')
  if (parts.length) return parts.join('').trim()
  const ref = first(titleNode, 'f')?.textContent ?? ''
  return ref.trim()
}

/** `<ser>` 里某一项的格子引用：`<tx>`/`<cat>`/`<val>` 下面的 `<strRef><f>` 或 `<numRef><f>`。 */
function refOf(ser: Element, name: string): string {
  const node = first(ser, name)
  if (!node) return ''
  return first(node, 'f')?.textContent?.trim() ?? ''
}

/** `'数据'!$A$2:$A$6` → 那几个地址。整列引用（`A:A`）、整行引用、跨工作簿的引用都不管：
 *  一张图引用一整列在我们这里和引用一万格一样重，而读者要看的还是开头那几行；而
 *  `[1]'数据'!A1` 指的是另一个工作簿里的一张表，不在这份 zip 里。 */
export function expandRef(ref: string, limit: number): string[] {
  const at = ref.lastIndexOf('!')
  // `[1]` 这种外部工作簿前缀：`!` 前面那一截里带方括号，就不是这一份里的引用。
  if (at >= 0 && ref.slice(0, at).includes('[')) return []
  const range = at >= 0 ? ref.slice(at + 1) : ref
  const match = /^\$?([A-Za-z]{1,3})\$?(\d{1,7})(?::\$?([A-Za-z]{1,3})\$?(\d{1,7}))?$/.exec(range.trim())
  if (!match) return []
  const col1 = columnIndex(match[1])
  const row1 = Number(match[2]) - 1
  const col2 = match[3] ? columnIndex(match[3]) : col1
  const row2 = match[4] ? Number(match[4]) - 1 : row1
  const out: string[] = []
  for (let row = Math.min(row1, row2); row <= Math.max(row1, row2) && out.length < limit; row += 1) {
    for (let col = Math.min(col1, col2); col <= Math.max(col1, col2) && out.length < limit; col += 1) {
      out.push(`${columnName(col)}${row + 1}`)
    }
  }
  return out
}

/** `'数据'!$A$2:$A$6` 里的工作表名（去掉引号和 `''` 转义）；没有工作表名时是空串。 */
function sheetOf(ref: string): string {
  const at = ref.lastIndexOf('!')
  if (at < 0) return ''
  let name = ref.slice(0, at)
  if (name.startsWith("'") && name.endsWith("'")) name = name.slice(1, -1).replace(/''/g, "'")
  return name
}

/** 取数：把 `RawChart` 和读出来的表对上。取不到的格子在图上是一个空档，不是 0。 */
export function resolveCharts(
  raw: readonly RawChart[],
  sheets: readonly { name: string; rows: string[][]; numbers?: Map<string, number> }[]
): Map<string, SheetChart[]> {
  const byName = new Map(sheets.map((sheet) => [sheet.name, sheet]))
  const out = new Map<string, SheetChart[]>()
  for (const chart of raw) {
    const host = byName.get(chart.sheet)
    if (!host) continue
    const series: { name: string; texts: string[] }[] = []
    let categories: string[] = []
    for (const line of chart.series) {
      const source = byName.get(sheetOf(line.values) || chart.sheet) ?? host
      const values = expandRef(line.values, MAX_REF_CELLS)
      const cats = line.categories ? expandRef(line.categories, MAX_REF_CELLS) : []
      const catSheet = byName.get(sheetOf(line.categories) || chart.sheet) ?? source
      const texts = values.map((address) => textAt(source, address) ?? '')
      const catTexts = cats.length
        ? cats.map((address) => textAt(catSheet, address) ?? '')
        : values.map((_, i) => String(i + 1))
      if (!categories.length) categories = catTexts
      // 这一条叫什么：先按它引用的那一格取（Excel 里系列的标题常常就是一格），取不到
      // 才用「系列 N」——那是一个给读者看的名字，所以走词条。
      const fallback = t('work.room.preview.sheetChartSeries', { n: series.length + 1 })
      const name = line.name
        ? textAt(byName.get(sheetOf(line.name)) ?? source, expandRef(line.name, 1)[0] ?? '') ?? line.name
        : fallback
      series.push({ name: name.trim() || fallback, texts })
    }
    if (!series.length) continue
    const list = out.get(chart.sheet) ?? []
    list.push({
      sheet: chart.sheet,
      title: chart.title,
      kind: chart.kind,
      horizontal: chart.horizontal,
      categories,
      series,
    })
    out.set(chart.sheet, list)
  }
  return out
}

/** 一格显示出来的字。数字优先用原始值（带格式的 `4.1%` 再解析回来会变成 4.1）。 */
function textAt(sheet: { rows: string[][]; numbers?: Map<string, number> }, address: string): string | null {
  const number = sheet.numbers?.get(address)
  if (number !== undefined) return String(number)
  const match = /^([A-Z]+)(\d+)$/.exec(address)
  if (!match) return null
  const col = columnIndex(match[1])
  const row = Number(match[2]) - 1
  const text = sheet.rows[row]?.[col]
  return text === undefined ? null : text
}

function columnIndex(letters: string): number {
  let index = 0
  for (const ch of letters.toUpperCase()) index = index * 26 + (ch.charCodeAt(0) - 64)
  return index - 1
}

// ---- 一份 OOXML 里那几条 XPath 式的活 ----

function parseXml(text: string): Document | null {
  const doc = new DOMParser().parseFromString(text, 'application/xml')
  return doc.querySelector('parsererror') ? null : doc
}

/** 按本地名找第一个元素（Excel 写 `c:ser`，openpyxl 写 `ser`：前缀不能写死）。 */
function first(root: Document | Element, name: string): Element | null {
  const found = Array.from(root.getElementsByTagNameNS('*', name))
  return found[0] ?? null
}

function all(root: Document | Element, name: string): Element[] {
  return Array.from(root.getElementsByTagNameNS('*', name))
}

function attrEndingWith(node: Element, suffix: string): string | null {
  for (const attr of Array.from(node.attributes)) {
    if (attr.localName === suffix) return attr.value
  }
  return null
}

/** `[Content_Types].xml` 那种相对路径拼法：`../drawings/drawing1.xml` 从 part 所在目录往上走。 */
function resolvePath(from: string, target: string): string {
  if (target.startsWith('/')) return target.slice(1)
  const base = from.split('/').slice(0, -1)
  for (const piece of target.split('/')) {
    if (piece === '..') base.pop()
    else if (piece !== '.') base.push(piece)
  }
  return base.join('/')
}

/** `xl/worksheets/sheet1.xml` → `xl/worksheets/_rels/sheet1.xml.rels`。 */
function relsPath(part: string): string {
  const parts = part.split('/')
  const name = parts.pop() ?? ''
  return `${parts.join('/')}/_rels/${name}.rels`
}

/** 关系表：Id → 目标。另外记住每条的类型，调用方按 `Type` 的结尾筛。 */
class Relationships extends Map<string, string> {
  types = new Map<string, string>()
  typeOf(id: string): string | undefined {
    return this.types.get(id)
  }
}

function relationships(xml: string): Relationships {
  const out = new Relationships()
  const doc = parseXml(xml)
  if (!doc) return out
  for (const rel of all(doc, 'Relationship')) {
    const id = rel.getAttribute('Id') ?? ''
    const target = rel.getAttribute('Target') ?? ''
    if (!id || !target) continue
    out.set(id, target)
    out.types.set(id, rel.getAttribute('Type') ?? '')
  }
  return out
}

/** 关系表里第一条类型以 `/drawing` 结尾的；没有就 null。 */
function firstByType(rels: Relationships, suffix: string): string | null {
  for (const [id, target] of rels) {
    if ((rels.typeOf(id) ?? '').endsWith(`/${suffix}`)) return target
  }
  return null
}

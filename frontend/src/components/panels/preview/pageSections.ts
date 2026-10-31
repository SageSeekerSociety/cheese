// 页式文档里「这一段属于哪一节」。
//
// PDF（以及被转成 PDF 的 docx）的文字层里没有「标题」这个结构：pdf.js 给回来的是一段段
// 绝对定位的 span，谁大谁小只是画出来的字号。markdown 那边一句 `querySelectorAll('h1,h2')`
// 就有的东西，这里只能靠字号认。
//
// 为什么值得认：卡上写「第 3 页」只说出这是哪一页，说不出一页里的哪一处；同一份文档里
// 「报名截止 11 月 8 日」这句话出现在好几节是常事，受话人拿到的又是一句可能被改掉的原文，
// 章节才是能分辨「说的是哪一节」的那个东西。原型也是这么写的（`第 1 页 · 二、时间安排`）。
//
// 判据只认三样都成立的行，宁可认不出来也不认错：
//
//   1. 这一行的字号明显大过正文（正文取这一份文档里出现最多的字号）；
//   2. 这一行自己站成一整行——同一视觉高度上没有别的字（表格里加粗的格、行内放大的字
//      都会和同行别的字并排，于是被排除）；
//   3. 它在正文之上（不是页脚、页码、水印）。
//
// 认错比认不出来坏得多：说错一节，读者会照着去找一个不存在的标题，而「只有页码」至少
// 永远是准的。所以阈值取得宽、条件取得窄，认不出来就不写。
//
// 作用域是**整份文档**而不是单页：节可以跨页，第 5 页的正文常常属于第 4 页开头起的节。

/** 一行字在文字层里的样子：这一行的字、上下沿、它自己的字号，以及这一行里字号齐不齐。 */
export type MeasureRow = {
  top: number
  bottom: number
  text: string
  size: number
  /** 这一行上最小的那个字号（不传就是和 `size` 一样，也就是齐的）。
   *
   *  标题整行是同一个字号；表格的一行里常常是一个大字加几个小字（「指标卡」+ 说明），
   *  行内放大的字（正文里加粗放大的一句）也一样。那种行不是标题——写进卡里会把
   *  「指标卡说明文字」整句当成一节的名字。 */
  minSize?: number
}

/** 标题判定的一行。`page` 是第几页（从 1 数）。 */
export type HeadingRow = { page: number; top: number; text: string; size: number }

/** 这一行至少要比正文大这么多倍才算标题。文档里「节标题」常见的比例是 1.15–1.6 倍；
 *  正文与小一号的表格字（0.9 倍）之间的差只有 0.1 倍上下，卡在 1.25 上，两边都不碰。 */
const HEADING_RATIO = 1.25

/** 判断两个相邻档位「像不像一套层级」时用的比例，比 `HEADING_RATIO` 宽。
 *
 *  正文 12 与表格 9 是 1.33 倍、正文 14 与小一号 12.7 是 1.10 倍，而标题 18 对正文 12
 *  是 1.5 倍。正文与表格之间本来就近，硬套标题那条线会把正文判成标题。 */
const LOOSE_RATIO = 1.45

/** 一行里两个字的上下沿差这么多以上，就不算同一行——表头、上下标都这样。 */
const SAME_ROW = 0.5

/** 标题最多这么长。再长的多半是加大的正文段落（引言、「结论：……」那种整段），
 *  写进卡里也读不下去。 */
const MAX_HEADING_CHARS = 60

/** 正文的字号。
 *
 *  取「行数够多的那些字号里**最大**的一个」，不是取行数最多的那一个：一份报告里表格
 *  常常比正文密得多，按行数最多的取，表格那号字（比正文小一档）会当上「正文」，于是
 *  真正的正文反而「比正文大」、被当成标题——整页正文全变成标题。表头、图注、脚注也都
 *  在正文以下，往大取不会碰到它们。
 *
 *  门槛是「至少占一成的行」：再稀少的字号多半是标题自己、封面大字或者某个孤零零的标注，
 *  不能拿它当基准。
 *
 *  按行而不是按字：一段正文会拆成几十个 span，按字统计等于按字母出现频率投票。 */
function bodySize(rows: readonly MeasureRow[]): number {
  const tally = new Map<number, number>()
  let total = 0
  for (const row of rows) {
    if (!row.text.trim()) continue
    // 同一个字号按 0.1 取整再数：同一字号在不同页、不同字体下会差出 0.01。
    const key = Math.round(row.size * 10) / 10
    tally.set(key, (tally.get(key) ?? 0) + 1)
    total += 1
  }
  // 降序过一遍：第一个「行数够多、而且和下面够多的那一档差得不远」的才是正文。
  //
  // 为什么要比「和下一档差多远」：短文档里往往只有两个标题、三行正文，标题的行数也可能
  // 够多。一步取最大的那个够多字号，就会把标题当正文，于是真正的正文全被当成标题。
  // 正文的特征是它下面还压着一层（表格、图注）而且和它差得不远；标题则是孤零零地高出去。
  const sizes = [...tally.keys()].sort((a, b) => b - a)
  // 「够多」= 至少占全部行的一成，且不少于 2 行：靠这个把标题自己（往往一两行）排除在
  // 候选之外，省得它当上「正文」。一份 31 页的报告里正文能到几百行，一成的门槛够粗。
  const floor = Math.max(2, total * 0.1)
  for (let i = 0; i < sizes.length; i += 1) {
    const size = sizes[i]!
    if ((tally.get(size) ?? 0) < floor) continue
    // 往下找**第一个够多**的字号：中间那些零星的大字（封面、二级标题、孤零零的图注）
    // 不参与「差多远」的比较，否则正文和它的表格之间隔着一层二级标题时，会比错对象。
    const below = sizes.slice(i + 1).filter((other) => (tally.get(other) ?? 0) >= floor)
    const next = below[0]
    // 底下没有别的字号了：它是最大的那一档，就是正文（只有一种字号的文档也走这里）。
    if (next === undefined) return size
    // 和下一档差得不算远：这一档是正文，下一档是它的表格/图注。正文和表格之间差一档
    // （12 对 9，1.33 倍）是常态，比标题对正文（18 对 12，1.5 倍）更难分；所以这里
    // 用比判定标题更宽的一档，宁可把「正文」认成正文，也不让整页正文都变成标题。
    if (size < next * LOOSE_RATIO) return size
    // 差得远：这一档更像标题，跳过它继续往下找。
  }
  return sizes.at(-1) ?? 0
}

const measured = new WeakMap<HTMLElement, { signature: string; rows: MeasureRow[] }>()

/** 一页文字层里按视觉行收好的字与字号。
 *
 *  字号从 span 的 `--font-height` 读：pdf.js 6 只把几何写成变量，`--font-height` 是
 *  **这一行字的原始高矮**（不含缩放），缩放全在 `--total-scale-factor` 里。用它而不是
 *  量出来的像素高度：面板宽度变了，同一份文档量出来的像素跟着变，而「哪些行是标题」
 *  不该跟着变。变量读不出来（不是 pdf.js 画的、或者换了实现）就退回量出来的高度。
 *
 *  `layer` 的坐标以这一页左上角为准（`getBoundingClientRect` 已经减掉它），所以不同页
 *  的行可以直接放进同一个列表里比。 */
export function rowsInLayer(layer: HTMLElement, box: DOMRect): MeasureRow[] {
  // 同一层、同一个页框量过就用上次的：选一次字要把已经画出来的每一页都过一遍，长文档里
  // 每页上千个 span 逐个量位置，选区一动就卡。页框尺寸变了（缩放、重排）或者层里的字换了才重新量。
  const signature = `${box.width}x${box.height}:${layer.querySelectorAll('span').length}`
  const hit = measured.get(layer)
  if (hit && hit.signature === signature) return hit.rows
  const rows: MeasureRow[] = []
  for (const span of Array.from(layer.querySelectorAll<HTMLElement>('span'))) {
    // 只数最里层的字：pdf.js 把带结构标记的一段包在外层 span 里，外层的 textContent
    // 就是里面几段的合并，一起数进去同一行字会算两遍，字号统计也跟着偏。
    if (span.querySelector('span')) continue
    const text = span.textContent ?? ''
    if (!text.trim()) continue
    const rect = span.getBoundingClientRect()
    if (rect.width < 0.5 || rect.height < 0.5) continue
    const top = rect.top - box.top
    const bottom = rect.bottom - box.top
    const declared = Number.parseFloat(
      span.style.getPropertyValue('--font-height') || getComputedStyle(span).getPropertyValue('--font-height')
    )
    const size = declared > 0 ? declared : rect.height
    const row = rows.find(
      (one) =>
        Math.min(one.bottom, bottom) - Math.max(one.top, top) > SAME_ROW * Math.min(one.bottom - one.top, bottom - top)
    )
    if (!row) rows.push({ top, bottom, text, size, minSize: size })
    else {
      // 同一行里几段字字号不同时取大的：这一行看起来有多高，取决于最大的那几个字。
      row.top = Math.min(row.top, top)
      row.bottom = Math.max(row.bottom, bottom)
      row.text += text
      row.size = Math.max(row.size, size)
      row.minSize = Math.min(row.minSize ?? size, size)
    }
  }
  measured.set(layer, { signature, rows })
  return rows
}

/** 整份文档里认得出的标题，按文档顺序。 */
export function headingsOf(pages: readonly (readonly MeasureRow[])[]): HeadingRow[] {
  const all = pages.flat()
  const body = bodySize(all)
  if (body <= 0) return []
  const floor = body * HEADING_RATIO
  const out: HeadingRow[] = []
  pages.forEach((rows, index) => {
    const pageHeadings: HeadingRow[] = []
    for (const row of rows) {
      const text = row.text.replace(/\s+/g, ' ').trim()
      if (!text || text.length > MAX_HEADING_CHARS) continue
      if (row.size < floor) continue
      // 一行里字号不齐：这是表格的一行或者正文里一句放大的字，不是标题。
      if ((row.minSize ?? row.size) < floor) continue
      if (!standsAlone(row, rows)) continue
      pageHeadings.push({ page: index + 1, top: row.top, text, size: row.size })
    }
    pageHeadings.sort((a, b) => a.top - b.top)
    out.push(...pageHeadings)
  })
  return out
}

/** 这一行上下有没有和它并排的别的字。有就不是标题，是一行里的一个大字。 */
function standsAlone(row: MeasureRow, rows: readonly MeasureRow[]): boolean {
  return !rows.some((other) => {
    if (other === row) return false
    const shared = Math.min(row.bottom, other.bottom) - Math.max(row.top, other.top)
    return shared > SAME_ROW * Math.min(row.bottom - row.top, other.bottom - other.top)
  })
}

/** `spot`（这一处在这一页上的位置）属于哪一节：它之前最近的那个标题。
 *
 *  先按页、再按竖直位置比：标题在第 2 页、这一处在第 4 页时，第 2 页的标题就是它的节。
 *  这一处之前一个标题都没有时返回空串——文件开头不算一节。 */
export function sectionAt(headings: readonly HeadingRow[], page: number, top: number): string {
  let found = ''
  for (const heading of headings) {
    if (heading.page > page) break
    if (heading.page === page && heading.top > top) break
    found = heading.text
  }
  return found
}

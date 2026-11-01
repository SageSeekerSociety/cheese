// 选中一段原文的底色，由我们自己画。
//
// 为什么不用浏览器自带的那一层：它是按「一段文字」各画各的底色，而 pdf.js 的文字层是
// 一段段绝对定位摆上去的 span。一份文档里相邻两段的盒子经常是搭着的——每段的宽度按
// PDF 里自己那一小段算，接缝处宁可重叠也不留缝，中西文混排、字体切换的地方尤其明显。
// 搭着的那几个像素上，半透明的橙被画了两遍，比别处深；读者看到的就是「有的字和符号
// 旁边多出来一个框，和上面重叠」。
//
// pdf.js 自己的阅读器走的是另一条路：它把文字层的 `::selection` 底色关掉，自己把选区
// 画出来（pdf.mjs 里 `DrawLayer`），而且是**把选区的每一块收成一条 path、填一次**——
// 重叠的地方也只落一层。这里照做。
//
// 但光「一条 path」不够：文字层里同一行相邻两段的盒子高矮不一（字号不同、字体不同，
// 浏览器给回来的块也就跟着 9.78、11、11.56、14 各不相同），一块块按原样画出来是一排
// 参差的方块，不是一道荧光笔。所以这里再把**同一行**上的块并成一条：上下取这一行最
// 高那一块、横向把挨得近的接上，再上下各放一点边——读者看到的是划过去的一道，而不是
// 一串拼起来的小格子。相邻两行不会被这两条边连成一片（同一行才并）。
//
// 只管画。选区的范围、`getSelection()` 里的文字、`mouseup` 上那一跳全都没碰，所以
// 「选中一段话再提意见」那条路和以前一模一样。

import { onBeforeUnmount } from 'vue'

/** 浏览器给的选区块。`DOMRect` 够用，这里只这么用，图的是测试里能拿普通对象喂进来。 */
export type SelectionRect = {
  left: number
  top: number
  width: number
  height: number
}

/** 比这个还小的一块不画：浏览器在行首行尾会给出零尺寸的块，pdf.js 自己也跳过它们。 */
const MIN_SIDE = 0.5

/** 同一行上，竖直方向搭到较矮那一块的一半以上才算同一行。 */
const SAME_LINE = 0.5

/** 底色画成这一行字盒的多少倍高。荧光笔划出来的一道比字本身宽：上下各放约三成，
 *  字压在底色上还看得清。这个比例照着一行字和它下面的行距来定，不是随便取的：
 *  原型的正文行高 1.85、底色铺满整行（16px 的字盒画出 26.8px 的带），也就是 1.69
 *  倍；这里的 1.65 贴着那个观感，同时留一点余量给下面 `roomAround` 的收边——
 *  PDF 的行距比网页排版紧得多，放不下时按隔壁行收窄，不会糊成一片。 */
const BAND = 1.65

/** 同一行上两块横向隔得超过「字高 × 这个数」，就不并起来：那多半是两栏或者两个格子，
 *  中间那一截本来就不该涂。挨着的两段（隔一个空格、或者字层自己的接缝）都在这之内。 */
const JOIN_GAP = 0.6

type Band = { top: number; bottom: number; left: number; right: number }

/** 把一块块选区收成「一行一条」的带子，坐标是相对画布的（`box` 是画布左上角在视口里的
 *  位置，选区的坐标要减掉它才落在画布上）。
 *
 *  一块选区可能横跨两页，`rects` 里就混着另一页的块，按块心落在不在这一页上分开。
 */
export function selectionPath(
  rects: ArrayLike<SelectionRect>,
  box: SelectionRect,
  /** 这张纸上**所有**文字行的上下沿（同一坐标系，相对画布）。给了它就按相邻行的实际
   *  间距收着放：行距紧的文档（PDF 里常见 1.1 倍行高）上，1.4 倍会盖到隔壁行的字上。 */
  rows?: readonly [number, number][]
): string {
  const kept: Band[] = []
  for (const rect of Array.from(rects)) {
    if (rect.width < MIN_SIDE || rect.height < MIN_SIDE) continue
    const x = rect.left - box.left
    const y = rect.top - box.top
    if (x + rect.width / 2 < -1 || x + rect.width / 2 > box.width + 1) continue
    if (y + rect.height / 2 < -1 || y + rect.height / 2 > box.height + 1) continue
    kept.push({ top: y, bottom: y + rect.height, left: x, right: x + rect.width })
  }
  const bands = lineBands(kept)
  // 每行上下各放一点：底色是「划过去的一道」，不是贴着字画的一个框。放多少先看这一行
  // 上下离隔壁行有多近（紧行距的 PDF 里宁可窄一点），再看两条带子之间还剩多少。
  const pads = bands.map((band) => {
    const h = band.bottom - band.top
    return Math.max(0, Math.min((h * (BAND - 1)) / 2, roomAround(band, rows)))
  })
  // 相邻两行都在选区里时，两条带子各自放出去的那一截是加起来的。只按「离隔壁行多远」
  // 算还是会叠上（量到过 1.5px 的重叠，看上去连成一块），所以再按带子之间剩下的空隙
  // 收一次，保证中间至少留 1px。
  for (let i = 1; i < bands.length; i++) {
    const above = bands[i - 1]
    const below = bands[i]
    // 同一行上分左右两团的（两栏、两个格子）上下沿是一样的，`gap` 会是负的：那不是
    // 上下相邻，跳过。
    const gap = below.top - above.bottom
    const shared = pads[i - 1] + pads[i]
    if (gap > 0 && shared > gap - 1) {
      const scale = Math.max(0, (gap - 1) / shared)
      pads[i - 1] *= scale
      pads[i] *= scale
    }
  }
  return bands
    .map((band, i) => {
      const h = band.bottom - band.top
      const pad = pads[i]
      return `M${round(band.left)} ${round(band.top - pad)}h${round(band.right - band.left)}v${round(h + 2 * pad)}h${round(band.left - band.right)}Z`
    })
    .join(' ')
}

/** 这一行上下各能放出去多少还不碰着隔壁行。没有别的行（或者没给行表）时不限。
 *
 *  **取一半**：紧挨着的两行常常都在选区里，两边的带子各往上/下放一截，加起来才是这条
 *  行距。只看自己这一侧的话，两条带子会在中间叠上——量到过相邻两行的带子上下叠 1.5px，
 *  看上去就连成了一块。各放一半、两头各留半像素，才是一行一条。 */
function roomAround(band: Band, rows: readonly [number, number][] | undefined): number {
  if (!rows?.length) return Infinity
  let above = Infinity
  let below = Infinity
  for (const [top, bottom] of rows) {
    if (bottom <= band.top + 0.5) above = Math.min(above, band.top - bottom)
    else if (top >= band.bottom - 0.5) below = Math.min(below, top - band.bottom)
  }
  const room = Math.min(above, below)
  return room === Infinity ? Infinity : Math.max(0, (room - 1) / 2)
}

/** 按视觉行归并，再把同一行上挨着的块接成一条。 */
function lineBands(rects: Band[]): Band[] {
  const lines: Band[][] = []
  for (const rect of [...rects].sort((a, b) => a.top - b.top)) {
    // 只有和这一行**已经收进来的某一块**竖直搭上，才算同一行：拖过好几行时，一个块
    // 可能和上一行的尾巴也搭一点，拿整行去比会把它并到上一行里。
    const line = lines.find((members) => members.some((member) => overlaps(member, rect)))
    if (line) line.push(rect)
    else lines.push([rect])
  }
  return lines.flatMap(mergeRuns)
}

function overlaps(a: Band, b: Band): boolean {
  const shared = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
  return shared > SAME_LINE * Math.min(a.bottom - a.top, b.bottom - b.top)
}

/** 一行里横向挨着的几段接成一条；隔得远的（两栏、两个格子）各留各的。 */
function mergeRuns(line: Band[]): Band[] {
  const top = Math.min(...line.map((rect) => rect.top))
  const bottom = Math.max(...line.map((rect) => rect.bottom))
  const gap = (bottom - top) * JOIN_GAP
  const runs: Band[] = []
  for (const rect of [...line].sort((a, b) => a.left - b.left)) {
    const last = runs[runs.length - 1]
    if (last && rect.left - last.right <= gap) last.right = Math.max(last.right, rect.right)
    else runs.push({ top, bottom, left: rect.left, right: rect.right })
  }
  return runs
}

function round(value: number): number {
  return Math.round(value * 100) / 100
}

/** 画出来的那一层的类名。分页文档和幻灯片**用同一个**：样式只有一份
 *  （`preview-selection-paint.css`），两处就不可能漂成两个颜色。 */
export const SELECTION_PAINT_CLASS = 'preview-selection-paint'

export type SelectionHighlightOptions = {
  /** 能选的纸：每一页、每一张幻灯片。底色画在纸里，跟着纸的坐标走。 */
  sheets: () => HTMLElement[]
  /** 纸上文字层的类名。文字层还没画出来的纸不画。 */
  layerClass: string
  /** 画出来的那一层用的类名。只有测试里要单独认这一层时才给，平时不用。 */
  paintClass?: string
  /** 浏览器已经把选区收了、但读者还在写这一处批注时，按这一份（打开批注时留下的
   *  副本）继续画。点开批注卡会把光标交给输入框，浏览器随即把页面上的选区收起来——
   *  底色跟着灭掉的话，读者看不见自己在说哪一段。 */
  kept?: () => Range | null
}

type Paint = { box: HTMLDivElement; path: SVGPathElement }

/** 这一处选区和这一张纸的文字层有没有搭上。 */
function intersects(range: Range, layer: HTMLElement): boolean {
  return layer.contains(range.startContainer) || layer.contains(range.endContainer) || range.intersectsNode(layer)
}

/** 取选区落在这一层文字上的那些块。
 *
 *  直接问 `range.getClientRects()` 会带上浏览器为「跨页的那一大段」补的大块：一路从第
 *  1 页拖到第 2 页时，它会给出两个 528×747 的整页块（选区在某一片元素边界上越过去时，
 *  浏览器按最近的可选容器补一块）。那些不是字，画出来就是整页一片橙。这里改成走到
 *  文字层里那几个真文字节点上量：一个节点一段，块就是那一行字自己的框。 */
export function rectsIn(range: Range, layer: HTMLElement): SelectionRect[] {
  const out: SelectionRect[] = []
  const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT)
  let node = walker.nextNode()
  while (node) {
    const text = node.nodeValue ?? ''
    if (text.trim()) {
      const sub = document.createRange()
      try {
        // 这一段落在选区里的哪一截：两头各自和「选区的那一头」比位置。
        //
        // 不能用 `compareDocumentPosition` 比：选区的那一头常常是**元素**而不是文字节点
        // ——拖选时越过一段的边界，浏览器给的终点是包着那几段文字的那个容器，偏移量是
        // 「第几个孩子」。那时「这个文字节点排在终点容器之后吗」永远答「是」，每一段都
        // 被判成整段进选区（起点那侧用同一个比法又答「不进」），结果一条都收不到，橙色
        // 整个灭掉——读者拖过两行就看到「高亮没了」。`comparePoint` 比的是「这个点相对
        // 选区在哪儿」：负数是起点之前、正数是终点之后、0 是在里面，两头都用它。
        const head = range.comparePoint(node, 0)
        const tail = range.comparePoint(node, text.length)
        if (head <= 0 && tail >= 0) {
          // 起点落在这段里才用它自己的偏移，否则这一整段都在选区里。
          const from = head < 0 ? range.startOffset : 0
          const to = tail > 0 ? range.endOffset : text.length
          if (to > from) {
            sub.setStart(node, from)
            sub.setEnd(node, to)
            for (const rect of sub.getClientRects()) out.push(rect)
          }
        }
      } catch {
        // 这一段的边界算不出来（选区已经不在文档里）：跳过它，别的段照画。
      }
    }
    node = walker.nextNode()
  }
  return out
}

/** 这一页上文字层里每一段文字的上下沿，按画布坐标。底色要靠着这些行收边：
 *  一行一行量出来的框是「这一行字自己的」，比按段落猜的行高准。
 *
 *  同一视觉行上的几段（并排的两栏、中西文混排）竖直方向搭着，但上下沿各差一点
 *  （量到过 633 与 634.1），所以按**竖直搭上**归并，不按坐标取整。 */
function pageRows(layer: HTMLElement, box: DOMRect): [number, number][] {
  const rows: [number, number][] = []
  for (const span of layer.querySelectorAll('span')) {
    if (!(span.textContent ?? '').trim()) continue
    const rect = span.getBoundingClientRect()
    if (rect.width < MIN_SIDE || rect.height < MIN_SIDE) continue
    const top = rect.top - box.top
    const bottom = rect.bottom - box.top
    const row = rows.find(
      ([t, b]) => Math.min(b, bottom) - Math.max(t, top) > SAME_LINE * Math.min(b - t, bottom - top)
    )
    if (!row) rows.push([top, bottom])
    else {
      row[0] = Math.min(row[0], top)
      row[1] = Math.max(row[1], bottom)
    }
  }
  return rows
}

const SVG_NS = 'http://www.w3.org/2000/svg'

export function useSelectionHighlight(options: SelectionHighlightOptions) {
  const paints = new Map<HTMLElement, Paint>()

  /** 这一页上画底色用的那一层，没有就建一个。建在文字层旁边：画布是别人的，文字层
   *  重画时会被整块清掉，底色不能跟着一起没。 */
  function paintFor(sheet: HTMLElement): Paint | null {
    const layer = sheet.querySelector<HTMLElement>(`.${options.layerClass}`)
    const existing = paints.get(sheet)
    if (existing?.box.isConnected) return existing
    if (!layer) return null
    const box = document.createElement('div')
    box.className = options.paintClass ?? SELECTION_PAINT_CLASS
    const svg = document.createElementNS(SVG_NS, 'svg')
    svg.setAttribute('aria-hidden', 'true')
    svg.setAttribute('width', '100%')
    svg.setAttribute('height', '100%')
    const path = document.createElementNS(SVG_NS, 'path')
    svg.append(path)
    box.append(svg)
    if (layer.parentElement && layer.parentElement !== sheet) layer.parentElement.after(box)
    else sheet.append(box)
    const paint = { box, path }
    paints.set(sheet, paint)
    return paint
  }

  /** 选区分成几块、每块落在哪，只有浏览器知道；每次选区动了就问它一次，重画一遍。
   *  只认落在这些纸上的选区——外面的输入框里选字不该在纸上留一块橙。 */
  function refresh() {
    const selection = window.getSelection()
    const live = selection && !selection.isCollapsed && selection.rangeCount > 0 ? selection.getRangeAt(0) : null
    // 卡一打开光标就进了输入框，页面上的选区没了：这时按打开时留下的那份副本画，
    // 读者写批注的整段时间里，他在说哪一段一直亮着。
    const kept = live ? null : options.kept?.() ?? null
    const range = live ?? (kept && !kept.collapsed ? kept : null)
    for (const sheet of options.sheets()) {
      const layer = sheet.querySelector<HTMLElement>(`.${options.layerClass}`)
      const paint = paints.get(sheet)
      // 这张纸没连着这一处选区、或者文字层刚被重画掉（改宽度、缩放都会）：
      // 旧底色的坐标已经不指着那些字了，先抹掉。
      if (!range || !layer || !intersects(range, layer)) {
        if (paint?.box.isConnected) paint.path.setAttribute('d', '')
        continue
      }
      // 没选中的纸不建这一层：一页一个空 div 不划算，等真有选区再说。
      const at = paint?.box.isConnected ? paint : paintFor(sheet)
      if (!at) continue
      const box = at.box.getBoundingClientRect()
      at.path.setAttribute('d', selectionPath(rectsIn(range, layer), box, pageRows(layer, box)))
    }
  }

  document.addEventListener('selectionchange', refresh)
  onBeforeUnmount(() => {
    document.removeEventListener('selectionchange', refresh)
    for (const paint of paints.values()) paint.box.remove()
    paints.clear()
  })

  /** 重排、缩放之后外面要叫一次：文字层换了一批节点，选区可能已经指不到任何东西，
   *  底色的坐标也得按新的纸重算。 */
  return { refresh }
}

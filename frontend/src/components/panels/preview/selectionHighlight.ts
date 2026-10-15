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

/** 一条 path，每个矩形一句 `M x y h w v h h -w Z`（`h`/`v` 是相对坐标）。
 *
 * 一条 path 只填一次，所以哪怕两块搭着，那个地方的颜色也和别处一样深——这正是这一层
 * 存在的理由。`box` 是画布左上角在视口里的位置，选区的坐标要减掉它才落在画布上。
 *
 * 一块选区可能横跨两页，`rects` 里就混着另一页的块，按块心落在不在这一页上分开。
 */
export function selectionPath(rects: ArrayLike<SelectionRect>, box: SelectionRect): string {
  const parts: string[] = []
  for (const rect of Array.from(rects)) {
    if (rect.width < MIN_SIDE || rect.height < MIN_SIDE) continue
    const x = rect.left - box.left
    const y = rect.top - box.top
    if (x + rect.width / 2 < -1 || x + rect.width / 2 > box.width + 1) continue
    if (y + rect.height / 2 < -1 || y + rect.height / 2 > box.height + 1) continue
    parts.push(`M${round(x)} ${round(y)}h${round(rect.width)}v${round(rect.height)}h${round(-rect.width)}Z`)
  }
  return parts.join(' ')
}

function round(value: number): number {
  return Math.round(value * 100) / 100
}

export type SelectionHighlightOptions = {
  /** 能选的纸：每一页、每一张幻灯片。底色画在纸里，跟着纸的坐标走。 */
  sheets: () => HTMLElement[]
  /** 纸上文字层的类名。文字层还没画出来的纸不画。 */
  layerClass: string
  /** 画出来的那一层用的类名，位置和颜色由各自的样式给。 */
  paintClass: string
}

type Paint = { box: HTMLDivElement; path: SVGPathElement }

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
    box.className = options.paintClass
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
    const sheets = options.sheets().filter((sheet) => sheet.querySelector(`.${options.layerClass}`))
    const selection = window.getSelection()
    const range = selection && !selection.isCollapsed && selection.rangeCount > 0 ? selection.getRangeAt(0) : null
    // 一路拖过好几页时，一个 range 的块落在好几张纸上，所以每一张都拿整个 range 的块来
    // 试，落不到这一张上的那些由 `selectionPath` 按块心丢掉。
    const ours =
      !!range &&
      sheets.some((sheet) => sheet.contains(range.startContainer) || sheet.contains(range.endContainer))
    const rects = ours ? range.getClientRects() : null
    for (const sheet of sheets) {
      // 没选中的纸不建这一层：一页一个空 div 不划算，等真有选区再说。
      if (!rects && !paints.has(sheet)) continue
      const paint = paintFor(sheet)
      if (!paint) continue
      paint.path.setAttribute(
        'd',
        rects ? selectionPath(rects, paint.box.getBoundingClientRect()) : ''
      )
    }
  }

  document.addEventListener('selectionchange', refresh)
  onBeforeUnmount(() => {
    document.removeEventListener('selectionchange', refresh)
    for (const paint of paints.values()) paint.box.remove()
    paints.clear()
  })

  return { refresh }
}

// 代码查看器里「选中的这一段落在哪几行」。
//
// 每一行是一个带 `data-line` 的元素（`PreviewCode`），选区两头往上找最近的那一行就是
// 起止行号。两个边角要单独处理：
//
// - 三击、或者从一行拖到下一行的行首，选区的终点落在下一行的最开头（甚至落在那一行的
//   行号上）。那一行一个字也没选到，不算进去，否则「选了第 3 行」会变成「第 3–4 行」。
// - 浏览器有时把一头报在装着所有行的那一层上（offset 是第几个子元素），那时按 offset
//   去找对应的那一行。
//
// 批注递出去的是行号加这几行的原文。原文按行号从源码里取，不读 `selection.toString()`：
// 行号那一列虽然不让选，各家浏览器对「不让选的字算不算进选区文字」说法不一。

export interface LineRange {
  start: number
  end: number
}

function lineElement(node: Node | null): HTMLElement | null {
  const element = node instanceof Element ? node : node?.parentElement ?? null
  return element?.closest<HTMLElement>('[data-line]') ?? null
}

/** 选区的一头落在哪一行。`edge` 说是起点还是终点：落在容器上时，起点取 offset 指着的
 *  那个子元素，终点取它前面那一个。 */
function lineAt(node: Node, offset: number, edge: 'start' | 'end'): HTMLElement | null {
  const direct = lineElement(node)
  if (direct) return direct
  if (!(node instanceof Element)) return null
  const child = node.childNodes[edge === 'start' ? offset : offset - 1] ?? null
  if (!child) return null
  if (child instanceof Element) {
    if (child.matches('[data-line]')) return child as HTMLElement
    const inside = child.querySelectorAll<HTMLElement>('[data-line]')
    return (edge === 'start' ? inside[0] : inside[inside.length - 1]) ?? null
  }
  return lineElement(child)
}

/** 终点那一行是不是一个字都没选到：终点在行号上，或者在正文的最开头。 */
function touchesNothing(line: HTMLElement, node: Node, offset: number): boolean {
  const code = line.querySelector('[data-code]')
  if (!code || !code.contains(node)) return true
  const before = document.createRange()
  before.setStart(code, 0)
  before.setEnd(node, offset)
  return before.toString().length === 0
}

/** 这个选区在 `root` 里选中了第几行到第几行；没选、选区跑出了这一份代码就是 null。 */
export function lineRangeIn(root: HTMLElement | null, selection: Selection | null): LineRange | null {
  if (!root || !selection || selection.rangeCount === 0 || selection.isCollapsed) return null
  const range = selection.getRangeAt(0)
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null
  const first = lineAt(range.startContainer, range.startOffset, 'start')
  const last = lineAt(range.endContainer, range.endOffset, 'end')
  if (!first || !last) return null
  const start = Number(first.dataset.line)
  let end = Number(last.dataset.line)
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  if (end > start && touchesNothing(last, range.endContainer, range.endOffset)) end -= 1
  return { start: Math.min(start, end), end: Math.max(start, end) }
}

/** 把第 `start` 到第 `end` 行整行选中（点行号时用），选不到就什么也不做。 */
export function selectLines(root: HTMLElement, start: number, end: number): boolean {
  const first = root.querySelector(`[data-line="${start}"] [data-code]`)
  const last = root.querySelector(`[data-line="${end}"] [data-code]`)
  const selection = window.getSelection()
  if (!first || !last || !selection) return false
  const range = document.createRange()
  range.setStart(first, 0)
  range.setEnd(last, last.childNodes.length)
  selection.removeAllRanges()
  selection.addRange(range)
  return true
}
